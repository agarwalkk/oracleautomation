"""Forms-server-aware settle primitive.

Blocks until Oracle Forms finishes its current server round-trip by polling
the Java agent DOM for the absence of a busy cursor on ANY node.

The busy signal is ``cursorName == "Wait Cursor"`` (Java ``Cursor.getName()``
for ``WAIT_CURSOR``).  During a round-trip the wait cursor sits on the WINDOW
and outer ancestor nodes, not on inner leaf elements — so the scan is treated
as busy if ANY node carries a busy cursor.

Usage
-----
    from qcs_java_agent.settle import settle_forms, SettleResult

    result = settle_forms(driver, log_prefix="[Record:B]")
    if not result.settled:
        print(f"Warning: Forms did not settle in {result.waited_s:.1f}s")

Deterministic and AI-free — safe to call from both the Approach B recorder
and deterministic replay.  Never raises; all errors are captured in
``SettleResult.reason``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)

# Matched case-insensitively against cursorName (stripped).
# "Wait Cursor" is the value the Java agent emits for WAIT_CURSOR.
_BUSY_CURSORS: frozenset[str] = frozenset({"wait cursor", "wait_cursor", "wait", "busy"})


@dataclass
class SettleResult:
    settled: bool
    waited_s: float
    polls: int
    reason: str  # "idle_stable" | "timeout" | "scan_error"


def _cursor_is_busy(scan: dict) -> bool:
    """Return True if ANY node in the scan carries a busy cursor.

    Walks the full DOM depth-first (windows + all children) so that a wait
    cursor on an ancestor — the common Forms round-trip pattern — is detected
    even when inner leaf elements report a non-busy cursor.
    """
    from qcs_java_agent.snapshot import flatten_nodes  # local import avoids circular dep

    for node in flatten_nodes(scan):
        raw = node.get("cursorName")
        if raw is None:
            continue
        if str(raw).strip().lower() in _BUSY_CURSORS:
            return True
    return False


def settle_forms(
    driver: Any,
    *,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.15,
    stable_polls: int = 2,
    log_prefix: str = "[Settle]",
) -> SettleResult:
    """Poll the Java agent until Oracle Forms is idle.

    Returns only when BOTH conditions hold for *stable_polls* CONSECUTIVE scans:

    * ``not _cursor_is_busy(scan)``  — no wait cursor on any node
    * ``analyze_forms_readiness(scan).ready``  — Forms is structurally ready

    The consecutive counter is reset whenever a poll is busy or not-ready.
    On scan exceptions the poll is treated as "not settled" (counter reset) so
    a transient network/JVM hiccup does not cause a premature idle signal.

    Parameters
    ----------
    driver:
        Attached ``JavaAgentDriver`` instance.
    timeout_s:
        Hard upper bound; returns ``SettleResult(settled=False, reason="timeout")``
        if elapsed.
    poll_interval_s:
        Sleep duration between polls.
    stable_polls:
        Number of consecutive idle+ready polls required before declaring settled.
    log_prefix:
        Short string prepended to console log lines (e.g. "[Record:B]").

    Returns
    -------
    SettleResult
        Never raises.  Caller should log / handle ``settled=False`` as a
        warning; replay may still succeed if the form is structurally correct.
    """
    from qcs_java_agent.readiness import analyze_forms_readiness  # local import

    start = time.monotonic()
    deadline = start + timeout_s
    polls = 0
    consecutive = 0
    last_was_scan_error = False

    while time.monotonic() < deadline:
        polls += 1
        try:
            scan = driver.scan()
            last_was_scan_error = False
        except Exception as exc:  # noqa: BLE001
            last_was_scan_error = True
            consecutive = 0
            _log.debug("%s scan error on poll %d: %s", log_prefix, polls, exc)
            time.sleep(poll_interval_s)
            continue

        busy = _cursor_is_busy(scan)
        ready = analyze_forms_readiness(scan).ready

        if busy or not ready:
            consecutive = 0
            reason_hint = "busy-cursor" if busy else "not-ready"
            _log.debug("%s poll %d: %s", log_prefix, polls, reason_hint)
        else:
            consecutive += 1
            _log.debug("%s poll %d: idle (%d/%d)", log_prefix, polls, consecutive, stable_polls)
            if consecutive >= stable_polls:
                waited = time.monotonic() - start
                _log.debug("%s settled after %d poll(s), %.2fs", log_prefix, polls, waited)
                return SettleResult(
                    settled=True,
                    waited_s=waited,
                    polls=polls,
                    reason="idle_stable",
                )

        time.sleep(poll_interval_s)

    waited = time.monotonic() - start
    reason = "scan_error" if last_was_scan_error else "timeout"
    _log.debug("%s did not settle within %.1fs (%d polls): %s", log_prefix, timeout_s, polls, reason)
    return SettleResult(settled=False, waited_s=waited, polls=polls, reason=reason)
