from __future__ import annotations

import importlib.util
import sys
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType

import pytest

from tests.dashboard import test_live_read_contracts as live_contracts
from tests.dashboard import test_read_only_boundary as boundary


def load_adapter() -> ModuleType:
    source = Path("dashboard/live_read_adapter.py")
    spec = importlib.util.spec_from_file_location("dashboard_live_read_adapter_lifecycle", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()


STORY_96_2_ROUTES = (
    "/v1/tasks/{task_id}/history",
    "/v1/events/replay",
    "/v1/events/replay/validate",
    "/v1/health",
)


def story_96_2_routes_by_pattern() -> dict[str, object]:
    return {
        route.route_pattern: route
        for panel in adapter.story_96_2_panel_contracts()
        for route in panel.routes
    }


def test_story_96_2_panel_contracts_have_exact_replay_history_health_subset() -> None:
    panels = adapter.story_96_2_panel_contracts()
    assert tuple(panel.panel_family for panel in panels) == (
        "task-history",
        "replay-readiness",
        "health-readiness",
    )

    routes = tuple(route.route_pattern for panel in panels for route in panel.routes)
    assert routes == STORY_96_2_ROUTES
    assert adapter.story_96_2_route_patterns() == STORY_96_2_ROUTES

    approved_routes = {contract.route_pattern for contract in adapter.approved_read_contracts()}
    assert set(routes) <= approved_routes
    assert set(routes).isdisjoint(adapter.EXCLUDED_ROUTE_PATTERNS)
    assert {contract.route_pattern for contract in adapter.unavailable_read_contracts()}.isdisjoint(
        routes
    )

    blocked_story_96_2_routes = {
        "/v1/tasks/{task_id}",
        "/v1/tasks/{task_id}/events",
        "/v1/tasks/{task_id}/transitions",
        "/v1/trace/{trace_id}",
        "/v1/tasks",
        "/v1/sessions",
        "/v1/tasks/{task_id}/logs/digest",
    }
    assert set(routes).isdisjoint(blocked_story_96_2_routes)


def test_story_96_2_panel_contract_routes_are_get_only_and_contract_derived() -> None:
    for route_pattern in adapter.story_96_2_route_patterns():
        request = adapter.read_request(route_pattern)
        assert request.method == "GET"
        assert live_contracts.is_allowlisted_dashboard_read(request.method, request.route_pattern)

    contracts_by_route = {
        contract.route_pattern: contract for contract in adapter.approved_read_contracts()
    }
    for panel in adapter.story_96_2_panel_contracts():
        for route in panel.routes:
            contract = contracts_by_route[route.route_pattern]
            assert route.source_category == contract.source_category
            assert route.timestamp_policy == contract.timestamp_policy
            assert route.freshness_policy == contract.freshness_policy
            assert route.allowed_states == contract.allowed_states
            assert route.non_authoritative_states == contract.allowed_states & (
                adapter.NON_AUTHORITATIVE_STATES
            )


def test_story_96_2_route_input_identifiers_are_exact_and_not_display_metadata() -> None:
    routes_by_pattern = story_96_2_routes_by_pattern()

    assert routes_by_pattern["/v1/tasks/{task_id}/history"].route_input_identifiers == ("task_id",)
    assert routes_by_pattern["/v1/events/replay"].route_input_identifiers == ()
    assert routes_by_pattern["/v1/events/replay/validate"].route_input_identifiers == ()
    assert routes_by_pattern["/v1/health"].route_input_identifiers == ()

    assert routes_by_pattern["/v1/tasks/{task_id}/history"].row_display_identifiers == (
        "task_id",
        "event_id",
        "trace_id",
    )
    assert routes_by_pattern["/v1/events/replay"].row_display_identifiers == (
        "replay_id",
        "event_id",
        "trace_id",
    )
    assert routes_by_pattern["/v1/events/replay/validate"].row_display_identifiers == ("replay_id",)
    assert routes_by_pattern["/v1/health"].row_display_identifiers == ()

    for route in routes_by_pattern.values():
        assert "replay_id" not in route.route_input_identifiers
        assert "snapshot_id" not in route.route_input_identifiers
        assert "lifecycle_manifest_id" not in route.route_input_identifiers


def test_story_96_2_replay_lifecycle_errors_are_non_authoritative() -> None:
    replay_lifecycle_routes = (
        "/v1/tasks/{task_id}/history",
        "/v1/events/replay",
        "/v1/events/replay/validate",
    )
    for route_pattern in replay_lifecycle_routes:
        contract_route = story_96_2_routes_by_pattern()[route_pattern]
        assert "healthy" in contract_route.allowed_states
        for state in contract_route.non_authoritative_states:
            meta = adapter.result_meta(route_pattern, state)
            assert meta.authoritative is False
        healthy = adapter.result_meta(route_pattern, "healthy")
        assert healthy.authoritative is True

    health_route = story_96_2_routes_by_pattern()["/v1/health"]
    assert health_route.non_authoritative_states == frozenset(
        {"stale", "unavailable", "backend-unavailable"}
    )


def test_story_96_2_panel_metadata_is_immutable_and_fail_closed() -> None:
    panels = adapter.story_96_2_panel_contracts()
    with pytest.raises(FrozenInstanceError):
        panels[0].routes[0].route_input_identifiers += ("event_id",)

    for panel in panels:
        for route in panel.routes:
            healthy = adapter.result_meta(
                route.route_pattern,
                "healthy",
                {
                    identifier: f"example-{identifier}"
                    for identifier in route.row_display_identifiers
                },
            )
            assert healthy.authoritative is True
            assert not isinstance(healthy.identifiers, MutableMapping)


def test_story_96_2_adapter_boundary_has_no_runtime_live_call_markers() -> None:
    source = Path(adapter.__file__).read_text(encoding="utf-8").lower()
    forbidden_runtime_markers = (
        "fetch(",
        "xmlhttprequest",
        "websocket",
        "eventsource",
        "setinterval",
        "settimeout",
        "httpx",
        "requests.",
        "urllib.request",
    )
    for marker in forbidden_runtime_markers:
        assert marker not in source

    source_file = Path(adapter.__file__).relative_to(Path.cwd())
    live_contracts.assert_no_forbidden_effect_markers(
        source,
        source=str(source_file),
    )


def test_story_96_2_static_shell_remains_inert_without_live_calls() -> None:
    for html_file in boundary.dashboard_files():
        raw = html_file.read_text(encoding="utf-8")
        parser = boundary.parse_html(raw)
        runtime_text = boundary.context_text(boundary.runtime_contexts(parser)).lower()
        assert "/v1/" not in runtime_text
        assert "fetch(" not in runtime_text
        assert "xmlhttprequest" not in runtime_text
        assert "websocket" not in runtime_text
        assert "eventsource" not in runtime_text
