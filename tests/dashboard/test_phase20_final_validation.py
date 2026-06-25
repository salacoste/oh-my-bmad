from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from tests.dashboard import test_live_read_contracts as live_contracts
from tests.dashboard import test_read_only_boundary as boundary


def load_adapter() -> ModuleType:
    source = Path("dashboard/live_read_adapter.py")
    spec = importlib.util.spec_from_file_location("dashboard_live_read_adapter_phase20", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()

APPROVED_DASHBOARD_ROUTES = frozenset(
    {
        "/v1/tasks/{task_id}",
        "/v1/tasks/{task_id}/events",
        "/v1/tasks/{task_id}/transitions",
        "/v1/trace/{trace_id}",
        "/v1/tasks/{task_id}/history",
        "/v1/events/replay",
        "/v1/events/replay/validate",
        "/v1/health",
        "/v1/tasks/{task_id}/logs/digest",
    }
)
NEEDS_CONTRACT_ROUTES = frozenset({"/v1/tasks", "/v1/sessions"})
DIGEST_STREAM_ROUTES = frozenset({"/v1/tasks/{task_id}/logs/digest/stream"})


def _panel_route_patterns() -> tuple[str, ...]:
    return tuple(
        route.route_pattern
        for panel in (
            *adapter.story_96_1_panel_contracts(),
            *adapter.story_96_2_panel_contracts(),
            *adapter.story_108_2_panel_contracts(),
        )
        for route in panel.routes
    )


def test_dashboard_approved_route_inventory_is_exact_and_get_only() -> None:
    approved_contract_routes = {
        contract.route_pattern for contract in adapter.approved_read_contracts()
    }
    boundary_routes = {route for method, route in boundary.CORE_APPROVED_READ_ROUTES}

    assert approved_contract_routes == APPROVED_DASHBOARD_ROUTES
    assert boundary_routes == APPROVED_DASHBOARD_ROUTES
    assert len(boundary.CORE_APPROVED_READ_ROUTES) == len(APPROVED_DASHBOARD_ROUTES)
    for route_pattern in APPROVED_DASHBOARD_ROUTES:
        request = adapter.read_request(route_pattern)
        assert request.method == "GET"
        assert live_contracts.is_allowlisted_dashboard_read("GET", route_pattern)


def test_dashboard_aggregate_and_session_decision_remains_needs_contract() -> None:
    unavailable = {
        contract.source_category: contract for contract in adapter.unavailable_read_contracts()
    }

    assert set(unavailable) == {"aggregate", "session"}
    assert unavailable["aggregate"].route_pattern == "/v1/tasks"
    assert unavailable["session"].route_pattern == "/v1/sessions"

    for contract in unavailable.values():
        assert contract.route_status == "needs-separate-contract"
        assert contract.route_pattern in NEEDS_CONTRACT_ROUTES
        assert contract.allowed_states <= {"unavailable", "needs-contract"}
        assert contract.timestamp_policy == "not-available-until-contract"
        assert contract.freshness_policy == "not-authoritative-until-contract"
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            assert not live_contracts.is_allowlisted_dashboard_read(
                method,
                contract.route_pattern,
            )


def test_dashboard_panel_selectors_cover_approved_reads_and_exclude_unapproved_reads() -> None:
    panel_routes = _panel_route_patterns()

    assert len(panel_routes) == len(set(panel_routes))
    assert frozenset(panel_routes) == APPROVED_DASHBOARD_ROUTES
    assert frozenset(panel_routes).isdisjoint(NEEDS_CONTRACT_ROUTES)
    assert frozenset(panel_routes).isdisjoint(DIGEST_STREAM_ROUTES)
    assert frozenset(panel_routes).isdisjoint(adapter.EXCLUDED_ROUTE_PATTERNS)


def test_dashboard_static_shell_remains_inert_accessible_and_read_only() -> None:
    for html_file in boundary.dashboard_files():
        raw = html_file.read_text(encoding="utf-8")
        parser = boundary.parse_html(raw)
        page_text = raw.lower()
        runtime_text = boundary.context_text(boundary.runtime_contexts(parser)).lower()

        assert "/v1/" not in runtime_text, html_file
        assert "fetch(" not in runtime_text, html_file
        assert "xmlhttprequest" not in runtime_text, html_file
        assert "websocket" not in runtime_text, html_file
        assert "eventsource" not in runtime_text, html_file
        boundary.assert_no_control_affordance_mechanics(raw)
        assert not parser.form_contexts, html_file
        assert "read-only" in page_text, html_file
        assert "unavailable" in page_text, html_file


def test_dashboard_adapter_has_no_runtime_or_mutation_markers() -> None:
    source = Path(adapter.__file__).read_text(encoding="utf-8").lower()
    forbidden_markers = (
        "fetch(",
        "xmlhttprequest",
        "websocket",
        "eventsource",
        "httpx",
        "requests.",
        "urllib.request",
        "create_snapshot",
        "delete_snapshot",
        "cache_warm",
        "localstorage.setitem",
        "sessionstorage.setitem",
    )
    for marker in forbidden_markers:
        assert marker not in source
    live_contracts.assert_no_forbidden_effect_markers(
        source,
        source=str(Path(adapter.__file__).relative_to(Path.cwd())),
    )
