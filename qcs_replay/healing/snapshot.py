"""
qcs_replay.healing.snapshot — Tier-1 Snapshot Healer.

On locator failure:
  1. Capture the accessibility snapshot of the relevant window/page.
  2. Scrub secrets.
  3. Send to Azure OpenAI with a constrained JSON tool schema.
  4. Parse and validate the returned LocatorDescriptor.
  5. Resolve it against the live UI to confirm it works before returning.
"""
from __future__ import annotations

import json
import time
from typing import Any

import config
from qcs_java_agent import java_elements_to_ai_snapshot, java_nodes_to_repo_elements
from qcs_repo.snapshot import playwright_a11y_to_text, scrub_secrets


# ── Tool schema returned by the LLM (constrained JSON) ───────────────────────

LOCATOR_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "return_locator",
        "description": "Return the corrected locator descriptor for the missing element.",
        "parameters": {
            "type": "object",
            "properties": {
                "role":           {"type": "string"},
                "name":           {"type": "string"},
                "ancestors":      {"type": "array", "items": {"type": "string"}},
                "label_neighbor": {"type": "string"},
                "xpath":          {"type": "string"},
                "css":            {"type": "string"},
                "element_id":     {"type": "string"},
            },
            "required": [],
        },
    },
}

SYSTEM_PROMPT = (
    "You are an Oracle EBS test automation expert. "
    "A UI element could not be found using its recorded locator. "
    "Analyze the provided accessibility snapshot of the current screen and "
    "the original locator descriptor, then call return_locator with the best "
    "corrected locator. Only use information visible in the snapshot. "
    "Never guess credentials or invent element names."
)


class SnapshotHealer:
    def __init__(self, timeout_s: int = config.HEALING_TIMEOUT_S):
        self.timeout_s = timeout_s
        self.last_tokens: int = 0

    def heal(
        self,
        intent:          str,
        surface:         str,  # "java" | "html"
        descriptor:      dict,
        driver_or_page:  Any,
    ) -> dict | None:
        """
        Returns a healed locator descriptor dict, or None if the LLM cannot fix it.
        """
        snapshot_text = self._capture_snapshot(surface, driver_or_page)
        snapshot_text = scrub_secrets(snapshot_text)

        user_message = (
            f"Intent: {intent}\n\n"
            f"Original locator (failed):\n{json.dumps(descriptor, indent=2)}\n\n"
            f"Current UI snapshot:\n{snapshot_text}"
        )

        response_desc = self._call_llm(user_message)
        if response_desc is None:
            return None

        # Anti-loop: if LLM returned exactly the same descriptor, do not retry
        if _descriptors_equal(descriptor, response_desc):
            return None

        # Validate: try to resolve the healed descriptor
        if self._validate(surface, response_desc, driver_or_page):
            return response_desc

        return None

    # ── Snapshot capture ──────────────────────────────────────────────────────

    def _capture_snapshot(self, surface: str, driver_or_page: Any) -> str:
        if surface == "java":
            return self._java_snapshot(driver_or_page)
        return self._html_snapshot(driver_or_page)

    def _java_snapshot(self, driver: Any) -> str:
        elements = java_nodes_to_repo_elements(driver.scan())
        return java_elements_to_ai_snapshot(elements)

    def _html_snapshot(self, page: Any) -> str:
        import asyncio  # noqa: PLC0415
        try:
            loop = asyncio.get_event_loop()
            snap = loop.run_until_complete(page.accessibility.snapshot())
        except Exception:
            snap = None
        return playwright_a11y_to_text(snap)

    # ── LLM call ──────────────────────────────────────────────────────────────

    def _call_llm(self, user_message: str) -> dict | None:
        import aiohttp, asyncio  # noqa: PLC0415, E401

        payload = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            "tools":       [LOCATOR_TOOL_SCHEMA],
            "tool_choice": {"type": "function", "function": {"name": "return_locator"}},
            "max_tokens":  512,
        }
        headers = {
            "api-key":      config.AZURE_OPENAI_API_KEY,
            "Content-Type": "application/json",
        }
        url = (
            f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
            f"{config.AZURE_OPENAI_DEPLOYMENT}/chat/completions"
            f"?api-version={config.AZURE_OPENAI_API_VERSION}"
        )

        async def _post():
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as r:
                    return await r.json()

        try:
            loop = asyncio.get_event_loop()
            resp = loop.run_until_complete(_post())
        except Exception:
            return None

        self.last_tokens = resp.get("usage", {}).get("total_tokens", 0)

        choice = (resp.get("choices") or [{}])[0]
        tool_calls = choice.get("message", {}).get("tool_calls", [])
        if not tool_calls:
            return None

        try:
            args = json.loads(tool_calls[0]["function"]["arguments"])
            return args
        except (KeyError, json.JSONDecodeError):
            return None

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, surface: str, descriptor: dict, driver_or_page: Any) -> bool:
        try:
            if surface == "java":
                from qcs_replay.locator import JavaAgentResolver, LocatorDescriptor  # noqa: PLC0415
                JavaAgentResolver(driver_or_page).resolve(LocatorDescriptor(descriptor))
            else:
                from qcs_replay.locator import PlaywrightResolver, LocatorDescriptor  # noqa: PLC0415
                PlaywrightResolver(driver_or_page, timeout_ms=5000).resolve(LocatorDescriptor(descriptor))
            return True
        except Exception:
            return False


def _descriptors_equal(a: dict, b: dict) -> bool:
    keys = {"role", "name", "ancestors", "label_neighbor", "xpath", "css", "element_id"}
    return all(a.get(k) == b.get(k) for k in keys)
