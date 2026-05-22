"""Shared pytest fixtures for the qcs test suite."""
from __future__ import annotations

from typing import Any

import pytest

from qcs_replay.dsl import (
    ReplayBackend,
    ReplayLogger,
    RepositoryResolver,
    ResolvedTarget,
)
from qcs_replay.script import OracleReplay


class _SpyBackend(ReplayBackend):
    """Simple spy backend — records every call for assertion in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def click(self, ref: str, descriptor: dict) -> None:
        self.calls.append(("click", ref))

    def double_click(self, ref: str, descriptor: dict) -> None:
        self.calls.append(("double_click", ref))

    def set_text(self, ref: str, descriptor: dict, value: str) -> None:
        self.calls.append(("set_text", ref, value))

    def select_value(self, ref: str, descriptor: dict, value: str) -> None:
        self.calls.append(("select_value", ref, value))

    def press_key(self, key: str) -> None:
        self.calls.append(("press_key", key))

    def wait_for(self, ref: str, descriptor: dict, timeout_ms: int = 10_000) -> None:
        self.calls.append(("wait_for", ref))

    def assert_visible(self, ref: str, descriptor: dict) -> None:
        self.calls.append(("assert_visible", ref))

    def get_text(self, ref: str, descriptor: dict) -> str:
        self.calls.append(("get_text", ref))
        return ""

    def get_value(self, ref: str, descriptor: dict) -> str:
        self.calls.append(("get_value", ref))
        return ""


class OracleReplayFixture:
    """Test wrapper around OracleReplay that exposes the spy backends."""

    def __init__(self) -> None:
        self.browser_backend = _SpyBackend()
        self.java_backend = _SpyBackend()
        self.resolver = RepositoryResolver()
        self._replay = OracleReplay(
            page=None,
            resolver=self.resolver,
            browser_backend=self.browser_backend,
            java_backend=self.java_backend,
        )
        # Expose logger for assertion helpers
        self.logger: ReplayLogger = self._replay._logger

    # Delegate step/press_key to the inner OracleReplay.
    def step(self, description: str) -> None:
        self._replay.step(description)

    def press_key(self, key: str) -> None:
        self._replay.press_key(key)

    def form(self, form_ref: str):
        return self._replay.form(form_ref)

    def register(self, form_ref: str, element_ref: str, surface: str, descriptor: dict | None = None) -> None:
        """Register a known (form_ref, element_ref) pair on the resolver for this fixture."""
        self.resolver.register(
            form_ref,
            element_ref,
            ResolvedTarget(ref=element_ref, surface=surface, form_id=form_ref, descriptor=descriptor or {}),
        )


@pytest.fixture
def oracle_replay() -> OracleReplayFixture:
    """Pre-wired OracleReplay fixture with spy browser and Java Forms backends."""
    return OracleReplayFixture()
