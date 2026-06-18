from __future__ import annotations

import importlib.util
import sys
from collections.abc import MutableMapping
from pathlib import Path
from types import ModuleType

import pytest

from tests.dashboard import test_live_read_contracts as live_contracts
from tests.dashboard import test_live_read_state_contracts as state_contracts
from tests.dashboard import test_read_only_boundary as boundary


def load_adapter() -> ModuleType:
    source = Path("dashboard/live_read_adapter.py")
    spec = importlib.util.spec_from_file_location("dashboard_live_read_adapter", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()


def test_adapter_contracts_cover_exact_approved_route_inventory() -> None:
    approved = adapter.approved_read_contracts()
    adapter_routes = {
        (adapter.read_request(contract.route_pattern).method, contract.route_pattern)
        for contract in approved
    }

    assert len(approved) == 9
    assert adapter_routes == live_contracts.APPROVED_READ_ROUTES
    assert {contract.route_status for contract in approved} == {"approved"}
    assert {
        contract.route_pattern for contract in approved
    } == state_contracts.APPROVED_ROUTE_PATTERNS


def test_adapter_request_descriptions_are_get_only_allowlisted_and_inert() -> None:
    for contract in adapter.approved_read_contracts():
        request = adapter.read_request(contract.route_pattern)
        assert request.method == "GET"
        assert live_contracts.is_allowlisted_dashboard_read(request.method, request.route_pattern)
        assert request.source_category == contract.source_category
        assert request.required_identifiers == contract.required_identifiers

    for unavailable in adapter.unavailable_read_contracts():
        with pytest.raises(ValueError):
            adapter.read_request(unavailable.route_pattern)


def test_adapter_metadata_matches_state_contracts() -> None:
    expected_by_route = {
        contract.route_pattern: contract
        for contract in state_contracts.LIVE_VALUE_CONTRACTS
        if contract.route_contract == "approved"
    }

    for contract in adapter.approved_read_contracts():
        assert contract.route_pattern is not None
        expected = expected_by_route[contract.route_pattern]
        assert contract.source_category == expected.source_category
        assert contract.timestamp_policy == expected.timestamp_policy
        assert contract.freshness_policy == expected.freshness_policy
        assert contract.required_identifiers == expected.required_identifiers
        assert contract.allowed_states == expected.allowed_states

        meta = adapter.result_meta(
            contract.route_pattern,
            "healthy",
            {identifier: f"example-{identifier}" for identifier in contract.required_identifiers},
        )
        assert meta.authoritative is True
        assert meta.route_pattern == contract.route_pattern
        assert meta.identifiers.keys() == set(contract.required_identifiers)
        assert not isinstance(meta.identifiers, MutableMapping)


def test_digest_aggregate_and_session_routes_are_not_adapter_reads() -> None:
    approved_routes = {contract.route_pattern for contract in adapter.approved_read_contracts()}
    assert not (adapter.EXCLUDED_ROUTE_PATTERNS & approved_routes)
    assert "/v1/tasks/{task_id}/logs/digest" in adapter.EXCLUDED_ROUTE_PATTERNS

    unavailable = {
        contract.source_category: contract for contract in adapter.unavailable_read_contracts()
    }
    assert set(unavailable) == {"aggregate", "session"}
    assert unavailable["aggregate"].route_pattern == "/v1/tasks"
    assert unavailable["session"].route_pattern == "/v1/sessions"
    for contract in unavailable.values():
        assert contract.route_status == "needs-separate-contract"
        assert contract.route_pattern in live_contracts.NEEDS_SEPARATE_CONTRACT_GET_ROUTES
        assert contract.allowed_states <= {"unavailable", "needs-contract"}


def test_error_categories_render_fail_closed_metadata() -> None:
    for contract in adapter.all_read_contracts():
        for state in contract.allowed_states - {"healthy"}:
            meta = adapter.result_meta(contract.route_pattern, state)
            assert meta.authoritative is False
            assert meta.state in adapter.NON_AUTHORITATIVE_STATES
            assert meta.timestamp_policy == contract.timestamp_policy
            assert meta.freshness_policy == contract.freshness_policy

    with pytest.raises(ValueError):
        adapter.result_meta("/v1/tasks", "healthy")
    with pytest.raises(ValueError):
        adapter.result_meta("/v1/tasks/{task_id}", "needs-contract")


def test_adapter_module_has_no_forbidden_effect_markers() -> None:
    source_file = Path(adapter.__file__).relative_to(Path.cwd())
    assert source_file in live_contracts.dashboard_executable_surfaces()
    live_contracts.assert_no_forbidden_effect_markers(
        source_file.read_text(encoding="utf-8"),
        source=str(source_file),
    )


def test_static_dashboard_still_has_no_live_wiring_after_adapter_boundary() -> None:
    for html_file in boundary.dashboard_files():
        raw = html_file.read_text(encoding="utf-8")
        parser = boundary.parse_html(raw)
        runtime_text = boundary.context_text(boundary.runtime_contexts(parser)).lower()
        assert "/v1/" not in runtime_text, html_file
        assert not boundary.FORBIDDEN_METHOD_RE.search(runtime_text), html_file
