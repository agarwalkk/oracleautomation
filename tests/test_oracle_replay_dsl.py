# Tests for the FormReplay DSL layer (Phase 4 form-scoped reference model).
# All tests use spy backends; no Playwright, Java agent, or AI/LLM is invoked.
from __future__ import annotations

import pytest

from qcs_replay.dsl import (
    FormReplay,
    ReplayRefNotFoundError,
    ReplayRoutingError,
    ResolvedTarget,
)
from qcs_replay.script import OracleReplay
from tests.conftest import OracleReplayFixture


# === OracleReplay.form() ======================================================


def test_oracle_replay_form_method_returns_form_replay(oracle_replay: OracleReplayFixture) -> None:
    result = oracle_replay._replay.form("java_demo_form")
    assert isinstance(result, FormReplay)
    assert result.form_ref == "java_demo_form"


def test_oracle_replay_form_shares_resolver(oracle_replay: OracleReplayFixture) -> None:
    result = oracle_replay._replay.form("java_demo_form")
    assert result.resolver is oracle_replay._replay._resolver


# === FormReplay routing: browser backend ======================================


def test_form_click_routes_to_browser_backend(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("html_page", "submit_btn", surface="browser")
    oracle_replay.form("html_page").click("submit_btn")
    assert ("click", "submit_btn") in oracle_replay.browser_backend.calls
    assert not any(c[0] == "click" for c in oracle_replay.java_backend.calls)


def test_form_set_text_routes_to_browser_backend(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("html_page", "search_fld", surface="browser")
    oracle_replay.form("html_page").set_text("search_fld", "FIND_ME")
    assert ("set_text", "search_fld", "FIND_ME") in oracle_replay.browser_backend.calls


def test_form_double_click_routes_to_browser_backend(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("html_page", "logo", surface="browser")
    oracle_replay.form("html_page").double_click("logo")
    assert ("double_click", "logo") in oracle_replay.browser_backend.calls


# === FormReplay routing: java_forms backend ===================================


def test_form_set_text_routes_to_java_forms_backend(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("java_find_orders", "order_type", surface="java_forms")
    oracle_replay.form("java_find_orders").set_text("order_type", "STANDARD")
    assert ("set_text", "order_type", "STANDARD") in oracle_replay.java_backend.calls
    assert not any(c[0] == "set_text" for c in oracle_replay.browser_backend.calls)


def test_form_click_routes_to_java_forms_backend(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("java_find_orders", "find_btn", surface="java_forms")
    oracle_replay.form("java_find_orders").click("find_btn")
    assert ("click", "find_btn") in oracle_replay.java_backend.calls


# === FormReplay error cases ===================================================


def test_form_unknown_element_ref_raises(oracle_replay: OracleReplayFixture) -> None:
    with pytest.raises(ReplayRefNotFoundError):
        oracle_replay.form("java_find_orders").click("nonexistent_ref")


def test_form_browser_action_without_backend_raises_routing_error(oracle_replay: OracleReplayFixture) -> None:
    resolver = oracle_replay._replay._resolver
    resolver.register(
        "html_page", "btn",
        ResolvedTarget(ref="btn", surface="browser", form_id="html_page", descriptor={}),
    )
    replay_no_browser = OracleReplay(
        page=None, resolver=resolver,
        browser_backend=None, java_backend=oracle_replay.java_backend,
    )
    with pytest.raises(ReplayRoutingError):
        replay_no_browser.form("html_page").click("btn")


def test_form_java_action_without_backend_raises_routing_error(oracle_replay: OracleReplayFixture) -> None:
    resolver = oracle_replay._replay._resolver
    resolver.register(
        "java_form", "fld",
        ResolvedTarget(ref="fld", surface="java_forms", form_id="java_form", descriptor={}),
    )
    replay_no_java = OracleReplay(
        page=None, resolver=resolver,
        browser_backend=oracle_replay.browser_backend, java_backend=None,
    )
    with pytest.raises(ReplayRoutingError):
        replay_no_java.form("java_form").set_text("fld", "x")


def test_form_unsupported_surface_raises_routing_error(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay._replay._resolver.register(
        "some_form", "elem",
        ResolvedTarget(ref="elem", surface="unknown_surface", form_id="some_form", descriptor={}),
    )
    with pytest.raises(ReplayRoutingError):
        oracle_replay.form("some_form").click("elem")


# === RepositoryResolver with (form_ref, element_ref) keys ====================


def test_resolver_resolves_by_form_ref_and_element_ref(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("java_orders", "po_number", surface="java_forms")
    result = oracle_replay.resolver.resolve("java_orders", "po_number")
    assert result is not None
    assert result.surface == "java_forms"


def test_resolver_different_form_same_element_resolves_independently(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("form_a", "fld", surface="browser")
    oracle_replay.register("form_b", "fld", surface="java_forms")
    assert oracle_replay.resolver.resolve("form_a", "fld").surface == "browser"
    assert oracle_replay.resolver.resolve("form_b", "fld").surface == "java_forms"


def test_resolver_unknown_ref_raises(oracle_replay: OracleReplayFixture) -> None:
    with pytest.raises(ReplayRefNotFoundError):
        oracle_replay.resolver.resolve("no_form", "no_elem")


# === press_key routing ========================================================


def test_press_key_routes_to_java_backend_when_available(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay._replay.press_key("TAB")
    assert ("press_key", "TAB") in oracle_replay.java_backend.calls
    assert ("press_key", "TAB") not in oracle_replay.browser_backend.calls


def test_press_key_raises_when_no_backend_configured() -> None:
    from qcs_replay.dsl import RepositoryResolver  # noqa: PLC0415
    replay = OracleReplay(page=None, resolver=RepositoryResolver())
    with pytest.raises(ReplayRoutingError):
        replay.press_key("F4")


# === Action log entries =======================================================


def test_form_action_log_records_form_ref_element_ref_surface_status(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay.register("java_demo", "order_type", surface="java_forms")
    oracle_replay.form("java_demo").set_text("order_type", "STANDARD")
    log = oracle_replay.logger.actions
    assert log, "logger must have at least one entry"
    last = log[-1]
    assert last.action == "set_text"
    assert "order_type" in last.target_ref
    assert last.surface == "java_forms"
    assert last.status == "ok"


def test_step_logs_narrative_entry(oracle_replay: OracleReplayFixture) -> None:
    oracle_replay._replay.step("Navigate to PO entry screen")
    entry = oracle_replay.logger.actions[-1]
    assert entry.action == "step"
    assert "PO entry screen" in entry.target_ref
    assert entry.status == "ok"

