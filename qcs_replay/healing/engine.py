"""
qcs_replay.healing.engine — Orchestrates Tier-1 and Tier-2 healing.

The HealingEngine is created once per test (by conftest) and injected into
every step wrapper.  It tracks per-step and per-test heal counts, enforces
budgets, and records events for the pytest-html report.

Typical usage in a generated test step
---------------------------------------
    def fill_order_type(value, *, ctx: StepContext):
        def _action():
            el = java_resolver.resolve(OrderTypePage.order_type)
            el.send_text(value, simulate=True)
            assert_java_text(el, value)
        ctx.healer.run_with_healing(
            action=_action,
            intent=f"fill OrderType with {value!r}",
            surface="java",
            descriptor=OrderTypePage.order_type,
            driver_or_page=java_driver,
        )
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import config


Surface = Literal["java", "html"]


@dataclass
class HealingEvent:
    step_intent:    str
    surface:        Surface
    tier:           Literal["tier1", "tier2"]
    success:        bool
    original_desc:  dict
    healed_desc:    dict | None = None
    error:          str = ""
    duration_s:     float = 0.0
    tokens_used:    int = 0
    cost_usd:       float = 0.0
    ts:             str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


class HealingBudgetExceeded(RuntimeError):
    pass


class HealingEngine:
    """
    Manages the two-tier self-healing loop for a single test run.

    Parameters
    ----------
    patch_path   : Path to the repo_patch.yaml file for this recording run.
    mode         : "on" | "off" | "tier1-only"
    max_per_step : Maximum heal attempts per individual step.
    max_per_test : Maximum heal attempts across the entire test.
    timeout_s    : Wall-clock timeout for each individual heal attempt.
    """

    def __init__(
        self,
        patch_path: Path,
        mode:         str = config.HEALING_MODE,
        max_per_step: int = config.HEALING_MAX_PER_STEP,
        max_per_test: int = config.HEALING_MAX_PER_TEST,
        timeout_s:    int = config.HEALING_TIMEOUT_S,
    ):
        self.patch_path   = patch_path
        self.mode         = mode
        self.max_per_step = max_per_step
        self.max_per_test = max_per_test
        self.timeout_s    = timeout_s
        self.events:      list[HealingEvent] = []
        self._test_count  = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def run_with_healing(
        self,
        action:          Callable[[], None],
        intent:          str,
        surface:         Surface,
        descriptor:      dict,
        driver_or_page:  Any,
    ) -> None:
        """
        Execute ``action()``. On LocatorNotFound or post-action verify failure,
        attempt Tier-1 then Tier-2 healing up to the configured budgets.
        """
        if self.mode == "off":
            action()
            return

        step_count = 0
        last_desc = descriptor.copy()
        last_exc: Exception | None = None

        while step_count < self.max_per_step:
            try:
                action()
                return  # success
            except Exception as exc:
                last_exc = exc

            if self._test_count >= self.max_per_test:
                raise HealingBudgetExceeded(
                    f"Healing budget exhausted (max_per_test={self.max_per_test}). "
                    f"Last step: {intent!r}"
                ) from last_exc

            # ── Tier 1: snapshot healer ──────────────────────────────────────
            t1_result = self._tier1(intent, surface, last_desc, driver_or_page)
            if t1_result is not None and t1_result != last_desc:
                last_desc = t1_result
                step_count += 1
                self._test_count += 1
                continue  # retry with healed descriptor

            # ── Tier 2: computer-use fallback ────────────────────────────────
            if self.mode != "tier1-only":
                t2_result = self._tier2(intent, surface, last_desc, driver_or_page)
                if t2_result is not None:
                    last_desc = t2_result
                    step_count += 1
                    self._test_count += 1
                    try:
                        action()
                        return
                    except Exception as exc:
                        last_exc = exc

            step_count += 1

        raise RuntimeError(
            f"All healing attempts exhausted for step {intent!r}"
        ) from last_exc

    # ── Tier 1 ────────────────────────────────────────────────────────────────

    def _tier1(
        self,
        intent:         str,
        surface:        Surface,
        descriptor:     dict,
        driver_or_page: Any,
    ) -> dict | None:
        from qcs_replay.healing.snapshot import SnapshotHealer  # noqa: PLC0415

        t0 = time.monotonic()
        healer = SnapshotHealer(timeout_s=self.timeout_s)
        try:
            healed = healer.heal(intent, surface, descriptor, driver_or_page)
            duration = time.monotonic() - t0
            success = healed is not None
            self._record_event(HealingEvent(
                step_intent=intent, surface=surface, tier="tier1",
                success=success, original_desc=descriptor,
                healed_desc=healed, duration_s=duration,
                tokens_used=healer.last_tokens,
            ))
            if success:
                self._write_patch(descriptor, healed, intent)
            return healed
        except Exception as exc:
            duration = time.monotonic() - t0
            self._record_event(HealingEvent(
                step_intent=intent, surface=surface, tier="tier1",
                success=False, original_desc=descriptor,
                error=str(exc), duration_s=duration,
            ))
            return None

    # ── Tier 2 ────────────────────────────────────────────────────────────────

    def _tier2(
        self,
        intent:         str,
        surface:        Surface,
        descriptor:     dict,
        driver_or_page: Any,
    ) -> dict | None:
        from qcs_replay.healing.computer_use import ComputerUseHealer  # noqa: PLC0415

        t0 = time.monotonic()
        healer = ComputerUseHealer(timeout_s=self.timeout_s)
        try:
            healed = healer.heal(intent, surface, descriptor, driver_or_page)
            duration = time.monotonic() - t0
            success = healed is not None
            self._record_event(HealingEvent(
                step_intent=intent, surface=surface, tier="tier2",
                success=success, original_desc=descriptor,
                healed_desc=healed, duration_s=duration,
                tokens_used=healer.last_tokens,
                cost_usd=healer.last_cost_usd,
            ))
            if success:
                self._write_patch(descriptor, healed, intent)
            return healed
        except Exception as exc:
            duration = time.monotonic() - t0
            self._record_event(HealingEvent(
                step_intent=intent, surface=surface, tier="tier2",
                success=False, original_desc=descriptor,
                error=traceback.format_exc(), duration_s=duration,
            ))
            return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _record_event(self, event: HealingEvent) -> None:
        self.events.append(event)

    def _write_patch(self, original: dict, healed: dict, intent: str) -> None:
        from qcs_repo.store import append_repo_patch  # noqa: PLC0415

        self.patch_path.parent.mkdir(parents=True, exist_ok=True)
        append_repo_patch(self.patch_path, {
            "intent":   intent,
            "original": original,
            "healed":   healed,
        })

    def cost_summary(self) -> dict:
        return {
            "total_heal_attempts": len(self.events),
            "tier1_attempts":      sum(1 for e in self.events if e.tier == "tier1"),
            "tier2_attempts":      sum(1 for e in self.events if e.tier == "tier2"),
            "tier1_successes":     sum(1 for e in self.events if e.tier == "tier1" and e.success),
            "tier2_successes":     sum(1 for e in self.events if e.tier == "tier2" and e.success),
            "total_tokens":        sum(e.tokens_used for e in self.events),
            "total_cost_usd":      round(sum(e.cost_usd for e in self.events), 6),
            "total_duration_s":    round(sum(e.duration_s for e in self.events), 2),
        }
