from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from tests.dashboard.test_static_shell import (
    DASHBOARD,
    LIVE_API_MARKERS,
    assert_only_story_107_2_controls,
    parse_dashboard,
)

if TYPE_CHECKING:
    from dashboard.live_read_adapter import PanelFixtureSnapshot, RouteFixtureRow

_ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "dashboard_live_read_adapter_story_100_1",
    Path("dashboard/live_read_adapter.py"),
)
assert _ADAPTER_SPEC and _ADAPTER_SPEC.loader
live_read_adapter = importlib.util.module_from_spec(_ADAPTER_SPEC)
sys.modules[_ADAPTER_SPEC.name] = live_read_adapter
_ADAPTER_SPEC.loader.exec_module(live_read_adapter)

FIXTURE_SECTION_ID = "fixture-readiness"
FIXTURE_ROW_LIST_LABEL = "story 99.2 fixture-backed approved route rows"
DEGRADED_SUMMARY_LIST_LABEL = "bounded degraded fixture states"
FORBIDDEN_STATIC_ROUTE_PATTERNS = frozenset(
    {
        "/v1/tasks/{task_id}/logs/digest/stream",
    }
)
REQUIRED_NON_AUTHORITATIVE_STATES = frozenset(
    {
        "backend-unavailable",
        "invalid",
        "partial",
        "stale",
        "unauthorized",
        "unavailable",
        "empty-list",
    }
)


def fixture_snapshots() -> tuple[PanelFixtureSnapshot, ...]:
    snapshots = live_read_adapter.story_99_2_fixture_snapshots()
    assert snapshots
    return snapshots


def fixture_rows() -> tuple[RouteFixtureRow, ...]:
    rows = tuple(row for snapshot in fixture_snapshots() for row in snapshot.rows)
    assert rows
    return rows


def fixture_section_text() -> str:
    parser = parse_dashboard()
    assert FIXTURE_SECTION_ID in parser.sections
    return " ".join(parser.sections[FIXTURE_SECTION_ID])


def test_story_100_1_fixture_readiness_section_is_present_and_explicitly_static() -> None:
    parser = parse_dashboard()
    assert FIXTURE_SECTION_ID in parser.sections
    assert f"#{FIXTURE_SECTION_ID}" in parser.nav_hrefs

    text = fixture_section_text().lower()
    for term in (
        "static fixture readiness",
        "story 99.2",
        "runtime data remains disconnected",
        "not runtime dashboard wiring",
        "committed html",
    ):
        assert term in text


def test_story_100_1_static_shell_renders_every_story_99_2_fixture_row() -> None:
    parser = parse_dashboard()
    fixture_list = parser.section_lists[FIXTURE_SECTION_ID][FIXTURE_ROW_LIST_LABEL]
    rows = fixture_rows()

    assert len(fixture_list) == len(rows)
    text = fixture_section_text()
    for snapshot in fixture_snapshots():
        assert snapshot.title in text
        assert snapshot.panel_family in text
    for list_item, row in zip(fixture_list, rows, strict=True):
        assert row.panel_family in list_item
        assert row.source_route_pattern in list_item
        assert row.source_category in list_item
        assert row.fixture_provenance in list_item
        assert row.timestamp_policy in list_item
        assert row.freshness_policy in list_item
        assert row.fixture_timestamp_label in list_item
        assert row.fixture_freshness_label in list_item
        assert row.display_state in list_item
        assert row.degraded_state_category in list_item
        assert row.authority_state in list_item
        assert row.display_severity in list_item
        assert row.display_copy in list_item
        for identifier in row.route_input_identifiers + row.row_display_identifiers:
            assert identifier in list_item
        for source_identifier in row.source_identifiers:
            assert source_identifier.name in list_item
            assert source_identifier.fixture_value in list_item


def test_story_100_1_degraded_unavailable_rendering_choice_is_bounded_summary() -> None:
    parser = parse_dashboard()
    summary = parser.section_lists[FIXTURE_SECTION_ID][DEGRADED_SUMMARY_LIST_LABEL]
    text = " ".join(summary).lower()

    assert summary
    assert "bounded non-authoritative summary" in text
    assert "needs contract fixture review" in text
    assert "runtime data remains disconnected" in text
    assert "authoritative success" not in text
    for state in REQUIRED_NON_AUTHORITATIVE_STATES:
        assert state in text


def test_story_100_1_forbidden_digest_stream_route_fails_closed_in_static_copy() -> None:
    text = fixture_section_text()
    lowered = text.lower()

    for route_pattern in FORBIDDEN_STATIC_ROUTE_PATTERNS:
        probe = live_read_adapter.story_99_2_route_fixture_probe(route_pattern)
        assert probe.renderable is False
        assert probe.display_copy in text
        assert route_pattern in text
    assert "digest stream fails closed" in lowered
    assert "needs-contract" in lowered


def test_story_100_1_fixture_section_keeps_routes_inert_and_avoids_runtime_controls() -> None:
    parser = parse_dashboard()
    raw = DASHBOARD.read_text(encoding="utf-8")
    fixture_attrs = " ".join(parser.section_attrs.get(FIXTURE_SECTION_ID, [])).lower()
    fixture_hrefs = parser.section_hrefs.get(FIXTURE_SECTION_ID, [])

    for marker in LIVE_API_MARKERS:
        assert marker not in raw
    assert_only_story_107_2_controls(parser)
    assert not fixture_hrefs
    assert "/v1/" not in fixture_attrs
    assert "data-route" not in fixture_attrs
    assert "hx-get" not in fixture_attrs
