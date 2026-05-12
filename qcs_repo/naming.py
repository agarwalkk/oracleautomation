"""
qcs_repo.naming — AI-propose + human-confirm friendly name assignment.

On first encounter of an unknown element:
  1. LLM proposes a friendly_name from (name, role, label_neighbor, position).
  2. User is prompted once via stdin (or a side-channel queue) to confirm/rename.
  3. Result is stored with confirmed_by="human" | "ai".

This module is intentionally synchronous so it can be called from both
the async MCP server (via asyncio.to_thread) and the sync CLI.
"""
from __future__ import annotations

import re
import sys
import threading
from typing import Callable

_confirm_lock = threading.Lock()


# ── AI proposal (thin wrapper — caller provides the LLM callable) ─────────────

def propose_friendly_name(
    element: dict,
    llm_call: Callable[[str], str],
) -> str:
    """
    Ask the LLM to propose a CamelCase friendly name for the element.
    Returns the proposed name (unconfirmed).

    ``llm_call`` should be a simple synchronous function:
        def my_llm(prompt: str) -> str: ...
    """
    prompt = (
        "You are naming UI elements for an Oracle EBS test automation repository.\n"
        "Given this element info, propose a short, unique, CamelCase friendly_name "
        "(no spaces, no special chars, max 30 chars). Reply with ONLY the name.\n\n"
        f"role        : {element.get('role', '')}\n"
        f"name        : {element.get('name', '')}\n"
        f"label_neighbor: {element.get('label_neighbor', '')}\n"
        f"ancestors   : {element.get('ancestors', [])}\n"
        f"description : {element.get('description', '')}\n"
    )
    raw = llm_call(prompt).strip()
    # Sanitise — keep only alphanumeric
    clean = re.sub(r"[^A-Za-z0-9]", "", raw)
    return clean or _fallback_name(element)


def _fallback_name(element: dict) -> str:
    """Deterministic fallback if LLM returns garbage."""
    base = element.get("name") or element.get("role") or "Element"
    slug = re.sub(r"[^A-Za-z0-9]", " ", base).title().replace(" ", "")
    return slug[:30] or "Element"


# ── Human confirmation ──────────────────────────────────────────────────────

def confirm_name(
    proposed: str,
    element: dict,
    form_id: str,
    *,
    interactive: bool = True,
    auto_confirm: bool = False,
) -> tuple[str, str]:
    """
    Present the proposed name to the user and get confirmation.

    Returns
    -------
    (confirmed_name, confirmed_by)  where confirmed_by = "human" | "ai"
    """
    if auto_confirm:
        return proposed, "ai"

    if not interactive or not sys.stdin.isatty():
        # Non-interactive (CI / piped): accept the AI proposal silently.
        return proposed, "ai"

    with _confirm_lock:
        print(
            f"\n[QCS] New element found in form '{form_id}':\n"
            f"  role  : {element.get('role')}\n"
            f"  name  : {element.get('name')}\n"
            f"  label : {element.get('label_neighbor')}\n"
            f"  AI proposes: '{proposed}'\n"
            "  Press <Enter> to accept, or type a new name: ",
            end="",
            flush=True,
        )
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""

    if not answer:
        return proposed, "human"

    clean = re.sub(r"[^A-Za-z0-9]", "", answer)
    return (clean or proposed), "human"


# ── Batch review helper (for reviewing ai-confirmed names later) ──────────────

def list_unconfirmed(elements: list[dict]) -> list[dict]:
    """Return elements that were auto-named by AI and not yet reviewed."""
    return [e for e in elements if e.get("confirmed_by") == "ai"]
