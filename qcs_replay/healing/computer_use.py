"""
qcs_replay.healing.computer_use — Tier-2 Computer-Use Fallback.

Provider interface + OpenAI default adapter (gpt-5.4-mini computer use).

Flow
----
1. Capture a Java Forms or Playwright page screenshot.
2. Send to the CU provider with a constrained intent string.
3. Provider executes a single click + keystroke cycle.
4. Reverse-map the click coordinates to the nearest Java-agent element / PW locator.
5. Return a LocatorDescriptor for the healed element so the engine writes it
   to repo_patch.yaml.
"""
from __future__ import annotations

import abc
import base64
import io
import time
from typing import Any

import config


# ── Provider interface ────────────────────────────────────────────────────────

class ComputerUseProvider(abc.ABC):
    """Abstract base class for computer-use backends."""

    @abc.abstractmethod
    def perform_step(
        self,
        screenshot_b64: str,
        intent: str,
        *,
        timeout_s: int,
    ) -> dict | None:
        """
        Given a base64-encoded PNG screenshot and a plain-English intent,
        perform the minimal action (click + type) needed and return a dict
        with any recovered coordinate information:
            {"x": int, "y": int, "keys": str, "action": "click|fill|..."}
        Returns None on failure.
        """


# ── OpenAI default adapter ────────────────────────────────────────────────────

class OpenAIComputerUseProvider(ComputerUseProvider):
    """
    Uses the OpenAI gpt-5.4-mini computer-use endpoint.
    Falls back gracefully if the model/endpoint is unavailable.
    """

    def __init__(self, model: str = config.CU_MODEL):
        self.model = model
        self.last_tokens:   int   = 0
        self.last_cost_usd: float = 0.0

    def perform_step(
        self,
        screenshot_b64: str,
        intent: str,
        *,
        timeout_s: int = config.HEALING_TIMEOUT_S,
    ) -> dict | None:
        import aiohttp, asyncio, json  # noqa: PLC0415, E401

        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{screenshot_b64}",
                        },
                        {
                            "type": "input_text",
                            "text": (
                                f"Perform exactly ONE action on the Oracle EBS form in the screenshot: "
                                f"{intent}. "
                                "Click the correct field and type the required text. "
                                "Output a JSON object with keys: x, y, keys (text to type), action."
                            ),
                        },
                    ],
                }
            ],
            "tools": [{"type": "computer_use_preview", "display_width": 1920, "display_height": 1080}],
            "max_output_tokens": 1024,
        }
        headers = {
            "api-key":      config.AZURE_OPENAI_API_KEY,
            "Content-Type": "application/json",
        }
        url = (
            f"{config.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
            f"{self.model}/responses?api-version={config.AZURE_OPENAI_API_VERSION}"
        )

        async def _post():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as r:
                    return await r.json()

        try:
            loop = asyncio.get_event_loop()
            resp = loop.run_until_complete(_post())
        except Exception:
            return None

        self.last_tokens = resp.get("usage", {}).get("total_tokens", 0)

        # Parse action from response
        for item in resp.get("output", []):
            if item.get("type") == "computer_call":
                call = item.get("action", {})
                return {
                    "x":      call.get("x", 0),
                    "y":      call.get("y", 0),
                    "keys":   call.get("text", ""),
                    "action": call.get("type", "click"),
                }
            if item.get("type") == "text":
                # Model returned a JSON string as text
                try:
                    return json.loads(item["text"])
                except Exception:
                    pass
        return None


# ── Stub providers (reserve for future adapters) ──────────────────────────────

class AnthropicComputerUseProvider(ComputerUseProvider):
    def perform_step(self, screenshot_b64, intent, *, timeout_s=60):
        raise NotImplementedError("Anthropic CU provider not yet implemented.")


class OmniParserProvider(ComputerUseProvider):
    def perform_step(self, screenshot_b64, intent, *, timeout_s=60):
        raise NotImplementedError("OmniParser provider not yet implemented.")


def get_provider() -> ComputerUseProvider:
    name = config.CU_PROVIDER.lower()
    if name == "anthropic":
        return AnthropicComputerUseProvider()
    if name == "omniparser":
        return OmniParserProvider()
    return OpenAIComputerUseProvider(model=config.CU_MODEL)


# ── Main healer ───────────────────────────────────────────────────────────────

class ComputerUseHealer:
    def __init__(self, timeout_s: int = config.HEALING_TIMEOUT_S):
        self.timeout_s    = timeout_s
        self.last_tokens  = 0
        self.last_cost_usd = 0.0

    def heal(
        self,
        intent:          str,
        surface:         str,
        descriptor:      dict,
        driver_or_page:  Any,
    ) -> dict | None:
        """
        Capture screenshot → CU provider performs the action → reverse-map coords.
        Returns healed LocatorDescriptor dict, or None on failure.
        """
        screenshot_b64 = self._screenshot(surface, driver_or_page)
        if screenshot_b64 is None:
            return None

        provider = get_provider()
        action = provider.perform_step(screenshot_b64, intent, timeout_s=self.timeout_s)
        if isinstance(provider, OpenAIComputerUseProvider):
            self.last_tokens = provider.last_tokens

        if action is None:
            return None

        x, y = int(action.get("x", 0)), int(action.get("y", 0))
        keys = action.get("keys", "")

        # Execute the action on the real window
        self._execute_action(surface, driver_or_page, x, y, keys, action.get("action", "click"))

        # Reverse-map coords to a locator descriptor
        healed = self._coords_to_descriptor(surface, driver_or_page, x, y)
        return healed

    # ── Screenshot capture ────────────────────────────────────────────────────

    def _screenshot(self, surface: str, driver_or_page: Any) -> str | None:
        try:
            if surface == "java":
                return self._java_screenshot(driver_or_page)
            return self._html_screenshot(driver_or_page)
        except Exception:
            return None

    def _java_screenshot(self, driver: Any) -> str:
        import win32gui  # noqa: PLC0415
        from PIL import ImageGrab  # noqa: PLC0415

        hwnd = getattr(driver, "_hwnd", None) or getattr(driver, "hwnd", None)
        if hwnd:
            rect = win32gui.GetWindowRect(hwnd)
            img = ImageGrab.grab(bbox=rect)
        else:
            img = ImageGrab.grab()  # fallback: full desktop

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _html_screenshot(self, page: Any) -> str:
        import asyncio  # noqa: PLC0415
        loop = asyncio.get_event_loop()
        png_bytes = loop.run_until_complete(page.screenshot())
        return base64.b64encode(png_bytes).decode()

    # ── Execute action ────────────────────────────────────────────────────────

    def _execute_action(
        self,
        surface: str,
        driver_or_page: Any,
        x: int,
        y: int,
        keys: str,
        action_type: str,
    ) -> None:
        if surface == "java":
            import pyautogui  # noqa: PLC0415
            pyautogui.click(x, y)
            if keys:
                time.sleep(0.2)
                pyautogui.typewrite(keys, interval=0.05)
        else:
            import asyncio  # noqa: PLC0415
            loop = asyncio.get_event_loop()

            async def _act():
                await driver_or_page.mouse.click(x, y)
                if keys:
                    await asyncio.sleep(0.2)
                    await driver_or_page.keyboard.type(keys)

            loop.run_until_complete(_act())

    # ── Reverse-map coords → locator ──────────────────────────────────────────

    def _coords_to_descriptor(
        self,
        surface: str,
        driver_or_page: Any,
        x: int,
        y: int,
    ) -> dict | None:
        from qcs_replay.healing.coord_to_locator import (  # noqa: PLC0415
            java_coord_to_descriptor,
            html_coord_to_descriptor,
        )
        try:
            if surface == "java":
                return java_coord_to_descriptor(driver_or_page, x, y)
            return html_coord_to_descriptor(driver_or_page, x, y)
        except Exception:
            return None
