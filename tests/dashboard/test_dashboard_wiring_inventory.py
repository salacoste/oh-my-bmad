from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

DASHBOARD = Path("dashboard/static/index.html")
STATIC_DIR = Path("dashboard/static")
INVENTORY = Path(
    "_bmad-output/implementation-artifacts/125-4-dashboard-wiring-inventory-test-guard.md"
)
SEARCH_GATE = Path(
    "_bmad-output/implementation-artifacts/125-3-task-list-search-discovery-implementation-planning.md"
)
AGGREGATE_RUNTIME = Path("dashboard/static/aggregate-task-list.js")


class ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []
        self.inline_script_depth = 0
        self.inline_script_text: list[str] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag == "script":
            self.scripts.append(attrs_dict)
            if not attrs_dict.get("src"):
                self.inline_script_depth += 1
        if tag == "link":
            self.links.append(attrs_dict)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline_script_depth:
            self.inline_script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)


def json_contract(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    matches = re.findall(r"```json\n(?P<payload>.*?)\n```", raw, re.DOTALL)
    assert len(matches) == 1, f"{path} must contain exactly one parseable JSON block"
    parsed = json.loads(matches[0])
    assert isinstance(parsed, dict)
    return parsed


def inventory() -> dict[str, object]:
    return json_contract(INVENTORY)


def search_gate() -> dict[str, object]:
    return json_contract(SEARCH_GATE)


def shell_parser() -> ShellParser:
    parser = ShellParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def test_story_125_4_inventory_matches_static_shell_script_allowlist() -> None:
    data = inventory()
    shell = shell_parser()
    approved_scripts = data["static_shell"]["approved_scripts"]

    assert data["schema_version"] == 1
    assert data["runtime_rewiring_authorized"] is False
    assert data["contract_source"] == "derived_guard"
    assert data["shared_dashboard_guard"] is True
    assert "same narrow story" in data["shared_guard_update_policy"]
    assert data["static_shell"]["path"] == str(DASHBOARD)
    assert shell.scripts == [{"src": script, "defer": ""} for script in approved_scripts]
    assert not "".join(shell.inline_script_text).strip()
    assert data["static_shell"]["inline_scripts_authorized"] is False
    assert data["static_shell"]["modulepreload_authorized"] is False
    assert sorted(path.name for path in STATIC_DIR.glob("*.js")) == sorted(approved_scripts)


def test_story_125_4_inventory_matches_aggregate_task_list_contract_targets() -> None:
    data = inventory()
    aggregate = data["aggregate_task_list"]
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    runtime = AGGREGATE_RUNTIME.read_text(encoding="utf-8")

    assert aggregate["runtime_path"] == str(AGGREGATE_RUNTIME)
    assert aggregate["approved_fetch_base"] == "/v1/tasks"
    assert aggregate["approved_route_patterns"] == [
        "GET /v1/tasks?status={task_status}&limit={task_list_limit}"
        "&offset={task_list_offset}&sort={task_sort}",
    ]
    assert (
        "GET /v1/tasks?status={task_status}&limit={task_list_limit}"
        "&offset={task_list_offset}&sort={task_sort}" in runtime
    )

    for element_id in aggregate["visible_control_ids"] + aggregate["metadata_target_ids"]:
        assert f'id="{element_id}"' in dashboard

    assert aggregate["authorized_sort_values"] == [
        "updated_at_desc_id_asc",
        "created_at_desc_id_asc",
    ]
    assert aggregate["status"] == "live_guarded"


def test_story_125_4_inventory_keeps_search_discovery_and_broad_rewiring_closed() -> None:
    data = inventory()
    gate = search_gate()

    assert gate["runtime_authorized"] is False
    assert gate["runtime_selection"] == "unselected"
    assert gate["future_contract_required"] is True
    assert gate["next_allowed_surface"] == (
        "Story 125.4 inventory and behavior-preserving test guards only"
    )
    assert data["search_discovery"]["runtime_authorized"] is False
    assert data["broad_cleanup"]["runtime_rewiring_authorized"] is False

    assert set(gate["missing_runtime_contract_inputs"]) == {
        "exact searchable fields",
        "exact query grammar and encoding policy",
        "minimum and maximum query lengths",
        "authority freshness provenance and privacy redaction semantics",
        "status limit offset sort selector interactions",
        "malformed hidden adjacent encoded repeated and body selector failure modes",
        "adversarial side-channel traversal storage cookie and generated-data tests",
        "explicit API and browser authorization boundaries",
    }
    assert set(gate["non_authorized_surfaces"]) == {
        "search/discovery runtime",
        "backend/API behavior",
        "browser/dashboard behavior",
        "arbitrary query grammar",
        "generated data",
        "hidden selectors",
        "automatic traversal",
        "row-derived selectors",
        "dependencies",
        "services/MCP",
        "CI/deployment",
        "credentials",
        "production operations",
    }
    assert set(data["search_discovery"]["forbidden_markers"]) == {
        "/v1/tasks/search",
        "task-search",
        "discover",
        "q=",
        "cursor=",
        "page=",
        "hidden selectors",
        "automatic traversal",
        "URL/hash/storage selectors",
        "generated live data",
    }

    assert set(data["broad_cleanup"]["forbidden_current_story_changes"]) == {
        "dashboard runtime rewiring",
        "dashboard runtime cleanup",
        "dashboard JavaScript behavior changes",
        "dashboard HTML wiring changes",
        "backend/API behavior changes",
        "browser behavior changes",
        "generated data",
        "hidden selectors",
        "automatic traversal",
        "dependencies or lockfiles",
        "services/MCP changes",
        "CI/deployment changes",
        "credentials or production operations",
    }


def test_story_125_4_runtime_module_inventory_is_live_guarded_and_complete() -> None:
    data = inventory()
    approved_scripts = data["static_shell"]["approved_scripts"]
    modules = data["runtime_modules"]

    assert [module["script"] for module in modules] == approved_scripts
    assert all(module["status"] == "live_guarded" for module in modules)
    assert all(module["boundary"] for module in modules)
