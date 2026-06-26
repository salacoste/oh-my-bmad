from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.dashboard import test_live_read_contracts as live_contracts

_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "dashboard_live_read_adapter_story_99_2",
    Path("dashboard/live_read_adapter.py"),
)
assert _ADAPTER_SPEC is not None
assert _ADAPTER_SPEC.loader is not None
live_read_adapter = importlib.util.module_from_spec(_ADAPTER_SPEC)
sys.modules[_ADAPTER_SPEC.name] = live_read_adapter
_ADAPTER_SPEC.loader.exec_module(live_read_adapter)

if TYPE_CHECKING:
    from dashboard.live_read_adapter import RouteFixtureRow, SourceIdentifier

EXPECTED_FIXTURE_ROUTE_PATTERNS = frozenset(
    route.route_pattern
    for panel in (
        live_read_adapter.story_96_1_panel_contracts()
        + live_read_adapter.story_96_2_panel_contracts()
        + live_read_adapter.story_108_2_panel_contracts()
        + live_read_adapter.story_109_2_panel_contracts()
    )
    for route in panel.routes
)
EXPECTED_APPROVED_ROUTE_PATTERNS = frozenset(
    route for method, route in live_contracts.APPROVED_READ_ROUTES if method == "GET"
)
EXPLICIT_FORBIDDEN_FIXTURE_ROUTES = frozenset(
    {
        "/v1/sessions",
        "/v1/sessions/{session_id}",
        "/v1/tasks/{task_id}/logs/digest/stream",
    }
)
STATIC_FIXTURE_TERMS = ("static", "fixture", "readiness")
RUNTIME_DISCONNECTED_TERMS = ("runtime data remains disconnected", "contract fixture")
FORBIDDEN_SUCCESS_CLAIMS = ("live", "current", "fetched", "backend success")
FORBIDDEN_RENDERED_TERMS = (
    "<script",
    "fetch",
    "xhr",
    "websocket",
    "eventsource",
    "polling",
    "http://",
    "https://",
    "post",
    "put",
    "patch",
    "delete",
    "form",
    "button",
    "input",
    "control",
    "mutation",
    "destructive",
    "start",
    "stop",
    "retry",
    "approve",
    "reject",
)
UNSAFE_CONTEXT_PROBES = (
    ("href", "/v1/tasks/{task_id}"),
    ("src", "/asset.js"),
    ("action", "/v1/tasks/{task_id}"),
    ("data-endpoint", "/v1/health"),
    ("data-route", "/v1/health"),
    ("hx-get", "/v1/health"),
    ("onclick", "openPanel()"),
    ("metadata", "javascript:alert(1)"),
    ("metadata", "data:text/html,unsafe"),
)
UNSAFE_TEXT_PROBES = (
    "<script>alert(1)</script>",
    "fetch('/v1/health')",
    "XHR request",
    "WebSocket channel",
    "EventSource stream",
    "polling refresh",
    "http://example.test",
    "https://example.test",
    "POST /v1/tasks",
    "PUT /v1/tasks",
    "PATCH /v1/tasks",
    "DELETE /v1/tasks",
    "form button input control",
    "mutation destructive lifecycle",
    "start stop retry approve reject",
    "backend success from current fetched live data",
)
NON_AUTHORITATIVE_STATES = frozenset(
    {
        "unavailable",
        "needs-contract",
        "partial",
        "stale",
        "invalid",
        "unauthorized",
        "backend-unavailable",
        "provider-unavailable",
        "empty-digest",
        "empty-list",
    }
)


def test_story_99_2_fixture_snapshot_route_coverage_matches_story_99_1_view_models() -> None:
    snapshots = live_read_adapter.story_99_2_fixture_snapshots()
    fixture_rows = [row for snapshot in snapshots for row in snapshot.rows]
    story_99_1_routes = {
        view_model.route_pattern for view_model in live_read_adapter.story_99_1_route_view_models()
    }

    assert EXPECTED_FIXTURE_ROUTE_PATTERNS == EXPECTED_APPROVED_ROUTE_PATTERNS
    assert story_99_1_routes == EXPECTED_FIXTURE_ROUTE_PATTERNS
    assert {row.source_route_pattern for row in fixture_rows} == EXPECTED_FIXTURE_ROUTE_PATTERNS
    assert (
        not {row.source_route_pattern for row in fixture_rows} & EXPLICIT_FORBIDDEN_FIXTURE_ROUTES
    )

    panel_routes = {
        route.route_pattern: (panel.panel_family, route)
        for panel in (
            live_read_adapter.story_96_1_panel_contracts()
            + live_read_adapter.story_96_2_panel_contracts()
            + live_read_adapter.story_108_2_panel_contracts()
            + live_read_adapter.story_109_2_panel_contracts()
        )
        for route in panel.routes
    }
    for row in fixture_rows:
        panel_family, panel_route = panel_routes[row.source_route_pattern]
        assert row.panel_family == panel_family
        assert row.source_category == panel_route.source_category
        assert row.route_input_identifiers == panel_route.route_input_identifiers
        assert row.row_display_identifiers == panel_route.row_display_identifiers
        assert row.timestamp_policy == panel_route.timestamp_policy
        assert row.freshness_policy == panel_route.freshness_policy


def test_story_99_2_fixture_schema_is_complete_static_and_renderer_safe() -> None:
    for snapshot in live_read_adapter.story_99_2_fixture_snapshots():
        assert snapshot.panel_family
        assert snapshot.title
        assert snapshot.rows
        for row in snapshot.rows:
            assert row.panel_family == snapshot.panel_family
            assert row.source_route_pattern.startswith("/v1/")
            assert "route_input_identifiers" in row.__dataclass_fields__
            assert "row_display_identifiers" in row.__dataclass_fields__
            expected_source_identifiers = expected_fixture_source_identifiers(row)
            assert row.source_identifiers == expected_source_identifiers
            assert {identifier.name for identifier in row.source_identifiers}.isdisjoint(
                set(row.row_display_identifiers) - set(row.route_input_identifiers)
            )
            assert row.fixture_provenance in {
                "static-fixture",
                "snapshot-fixture",
                "contract-fixture",
            }
            assert row.fixture_timestamp_label
            assert row.fixture_freshness_label
            assert row.read_only_contract is True
            assert not row.renderer_context_fields
            assert_static_fixture_copy(row.display_copy)
            assert_static_fixture_copy(row.fixture_freshness_label)
            assert_renderer_metadata_is_safe(row)


def test_story_99_2_degraded_fixture_states_are_bounded_non_authoritative() -> None:
    approved_contracts = {
        contract.route_pattern: contract for contract in live_read_adapter.approved_read_contracts()
    }

    for route_pattern, contract in approved_contracts.items():
        for display_state in contract.allowed_states:
            row = live_read_adapter.story_99_2_route_fixture_row(
                route_pattern,
                display_state=display_state,
            )
            assert row.display_state == display_state
            assert row.degraded_state_category == (
                "none" if display_state == "healthy" else display_state
            )
            assert_static_fixture_copy(row.display_copy)
            assert_renderer_metadata_is_safe(row)
            if display_state == "healthy":
                assert row.authority_state == "authoritative"
                assert row.display_severity == "normal"
                assert "contract fixture authority" in row.display_copy.lower()
            else:
                assert row.display_state in NON_AUTHORITATIVE_STATES
                assert row.authority_state in {"non-authoritative", "needs-contract"}
                assert row.display_severity in {"warning", "error", "blocked"}
                assert row.display_severity != "normal"
                assert display_state.replace("-", " ") in row.display_copy.lower()
                assert "authoritative success" not in row.display_copy.lower()


def test_story_99_2_forbidden_session_and_digest_routes_fail_closed() -> None:
    assert live_read_adapter.story_99_2_forbidden_renderable_route_patterns() == (
        EXPLICIT_FORBIDDEN_FIXTURE_ROUTES
    )
    fixture_routes = {
        row.source_route_pattern
        for snapshot in live_read_adapter.story_99_2_fixture_snapshots()
        for row in snapshot.rows
    }
    assert not fixture_routes & EXPLICIT_FORBIDDEN_FIXTURE_ROUTES

    for route_pattern in EXPLICIT_FORBIDDEN_FIXTURE_ROUTES | {"/v1/unknown"}:
        assert_fixture_build_fails(route_pattern)
        probe = live_read_adapter.story_99_2_route_fixture_probe(route_pattern)
        assert probe.renderable is False
        assert probe.display_state in {"unavailable", "needs-contract"}
        assert probe.authority_state == "needs-contract"
        assert probe.display_severity == "blocked"
        assert_static_fixture_copy(probe.display_copy)


def test_story_99_2_validation_allows_v1_only_in_inert_route_identity_fields() -> None:
    row = live_read_adapter.story_99_2_route_fixture_row("/v1/tasks/{task_id}")
    rendered = live_read_adapter.story_99_2_fixture_rendered_metadata(row)
    assert rendered["source_route_pattern"] == "/v1/tasks/{task_id}"

    for key, value in rendered.items():
        if key == "source_route_pattern":
            assert "/v1/" in str(value)
            continue
        assert "/v1/" not in stringify(value), (key, value)

    for key, value in UNSAFE_CONTEXT_PROBES:
        bad_row = replace(row, renderer_context_fields={key: value})
        assert_fixture_validation_fails(bad_row)

    mutable_empty_context = replace(row, renderer_context_fields={})
    assert_fixture_validation_fails(mutable_empty_context)


def test_story_99_2_guard_sensitivity_rejects_unsafe_fixture_strings() -> None:
    row = live_read_adapter.story_99_2_route_fixture_row("/v1/health")
    text_fields = (
        "display_copy",
        "fixture_timestamp_label",
        "fixture_freshness_label",
    )
    for unsafe in UNSAFE_TEXT_PROBES:
        for field_name in text_fields:
            bad_row = replace(row, **{field_name: unsafe})
            assert_fixture_validation_fails(bad_row)


def test_story_99_2_guard_sensitivity_rejects_masquerading_degraded_rows() -> None:
    row = live_read_adapter.story_99_2_route_fixture_row(
        "/v1/tasks/{task_id}/events",
        display_state="partial",
    )
    blurred_source_identifiers = row.source_identifiers + (
        live_read_adapter.SourceIdentifier(
            name="event_id",
            fixture_value="fixture-event-id",
        ),
        live_read_adapter.SourceIdentifier(
            name="trace_id",
            fixture_value="fixture-trace-id",
        ),
    )
    bad_rows = (
        replace(row, authority_state="authoritative"),
        replace(row, display_severity="normal"),
        replace(row, degraded_state_category="none"),
        replace(row, display_copy="Static fixture readiness authoritative success."),
        replace(row, source_identifiers=blurred_source_identifiers),
    )
    for bad_row in bad_rows:
        assert_fixture_validation_fails(bad_row)


def expected_fixture_source_identifiers(
    row: RouteFixtureRow,
) -> tuple[SourceIdentifier, ...]:
    if not row.route_input_identifiers:
        return (
            live_read_adapter.SourceIdentifier(
                name="source_category",
                fixture_value=f"fixture-{row.source_category}-source",
            ),
        )
    return tuple(
        live_read_adapter.SourceIdentifier(
            name=identifier,
            fixture_value=f"fixture-{identifier.replace('_', '-')}",
        )
        for identifier in row.route_input_identifiers
    )


def assert_static_fixture_copy(copy: str) -> None:
    lowered = copy.lower()
    for term in STATIC_FIXTURE_TERMS:
        assert term in lowered, copy
    assert any(term in lowered for term in RUNTIME_DISCONNECTED_TERMS), copy
    for claim in FORBIDDEN_SUCCESS_CLAIMS:
        assert claim not in lowered, copy


def assert_renderer_metadata_is_safe(row: RouteFixtureRow) -> None:
    live_read_adapter.validate_story_99_2_fixture_row(row)
    rendered = live_read_adapter.story_99_2_fixture_rendered_metadata(row)
    rendered_text = stringify(rendered).lower()
    for term in FORBIDDEN_RENDERED_TERMS:
        assert term not in rendered_text, (term, rendered)
    assert "read_only_contract" not in rendered
    assert row.read_only_contract is True


def stringify(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {stringify(item)}" for key, item in value.items())
    if isinstance(value, (tuple, list, frozenset, set)):
        return " ".join(stringify(item) for item in value)
    return str(value)


def assert_fixture_build_fails(route_pattern: str) -> None:
    try:
        live_read_adapter.story_99_2_route_fixture_row(route_pattern)
    except ValueError:
        return
    raise AssertionError(f"fixture build unexpectedly passed: {route_pattern}")


def assert_fixture_validation_fails(row: RouteFixtureRow) -> None:
    try:
        live_read_adapter.validate_story_99_2_fixture_row(row)
    except ValueError:
        return
    raise AssertionError(f"fixture validation unexpectedly passed: {row}")
