from __future__ import annotations

import os

import pytest

from qcs_replay.script import OracleReplay


class _LoginLocator:
    def __init__(self, *, wait_for_raises: Exception | None = None) -> None:
        self.fills: list[str] = []
        self.click_count = 0
        self.wait_for_calls: list[tuple[str | None, int | None]] = []
        self._wait_for_raises = wait_for_raises

    def fill(self, value: str) -> None:
        self.fills.append(value)

    def click(self) -> None:
        self.click_count += 1

    def wait_for(self, *, state: str | None = None, timeout: int | None = None) -> None:
        self.wait_for_calls.append((state, timeout))
        if self._wait_for_raises is not None:
            raise self._wait_for_raises


class _LoginPage:
    def __init__(self, *, login_button: _LoginLocator | None = None) -> None:
        self.user_name = _LoginLocator()
        self.password = _LoginLocator()
        self.login_button = login_button or _LoginLocator()
        self.goto_calls: list[str] = []
        self.load_state_calls: list[str] = []

    def goto(self, url: str) -> None:
        self.goto_calls.append(url)

    def wait_for_load_state(self, state: str) -> None:
        self.load_state_calls.append(state)

    def get_by_role(self, role: str, *, name: str | None = None) -> _LoginLocator:
        if role == "textbox" and name == "User Name":
            return self.user_name
        if role == "textbox" and name == "Password":
            return self.password
        if role == "button" and name == "Log In":
            return self.login_button
        raise AssertionError(f"Unexpected role lookup: role={role!r} name={name!r}")


def test_login_succeeds_when_login_button_disappears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EBS_USER", "demo_user")
    monkeypatch.setenv("EBS_PASSWORD", "demo_password")
    page = _LoginPage()
    replay = OracleReplay(page=page)

    replay.login(url="https://example.test/OA_HTML/AppsLocalLogin.jsp")

    assert page.goto_calls == ["https://example.test/OA_HTML/AppsLocalLogin.jsp"]
    assert page.load_state_calls == ["networkidle", "networkidle"]
    assert page.user_name.fills == ["demo_user"]
    assert page.password.fills == ["demo_password"]
    assert page.login_button.click_count == 1
    assert page.login_button.wait_for_calls == [("hidden", 15_000)]


def test_login_raises_when_login_page_stays_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EBS_USER", "bad_user")
    monkeypatch.setenv("EBS_PASSWORD", "bad_password")
    page = _LoginPage(login_button=_LoginLocator(wait_for_raises=RuntimeError("still visible")))
    replay = OracleReplay(page=page)

    with pytest.raises(RuntimeError) as exc_info:
        replay.login(url="https://example.test/OA_HTML/AppsLocalLogin.jsp")

    assert "login did not complete successfully" in str(exc_info.value)
    assert "EBS_USER/EBS_PASSWORD" in str(exc_info.value)
    assert page.login_button.click_count == 1
