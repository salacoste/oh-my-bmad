from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

DASHBOARD = Path("dashboard/static/index.html")
STATIC_DIR = Path("dashboard/static")
INVENTORY = Path(
    "_bmad-output/implementation-artifacts/128-1-dashboard-wiring-inventory-cleanup-contract-refresh.md"
)
AGGREGATE_RUNTIME = Path("dashboard/static/aggregate-task-list.js")
FEATURE_STATUS = Path("docs/feature-status.md")
SPRINT_STATUS = Path("_bmad-output/implementation-artifacts/sprint-status.yaml")

FORBIDDEN_SIDE_CHANNEL_MARKERS = {
    "EventSource",
    "WebSocket",
    "XMLHttpRequest",
    "localStorage",
    "sessionStorage",
    "document.cookie",
    "location.hash",
    "Worker",
    "new Worker",
    "SharedWorker",
    "serviceWorker",
}


class ShellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []
        self.inline_script_depth = 0
        self.inline_script_text: list[str] = []
        self.links: list[dict[str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag == "script":
            self.scripts.append(attrs_dict)
            if not attrs_dict.get("src"):
                self.inline_script_depth += 1
        if tag == "link":
            self.links.append(attrs_dict)
        if "id" in attrs_dict:
            self.ids.add(attrs_dict["id"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline_script_depth:
            self.inline_script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)


def json_contract(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    matches = re.findall(r"```json\n(?P<payload>.*?)\n```", raw, re.DOTALL)
    assert len(matches) == 1, f"{path} must contain exactly one parseable JSON block"
    parsed = json.loads(matches[0])
    assert isinstance(parsed, dict)
    return parsed


def inventory() -> dict[str, Any]:
    return json_contract(INVENTORY)


def shell_parser() -> ShellParser:
    parser = ShellParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_text(script: str) -> str:
    return (STATIC_DIR / script).read_text(encoding="utf-8")


def uncommented_runtime_text(script: str) -> str:
    text = runtime_text(script)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def fetch_calls(script: str) -> list[dict[str, object]]:
    text = runtime_text(script)
    calls: list[dict[str, object]] = []
    pattern = re.compile(r"fetch\((?P<arg>[^,\n]+),\s*\{(?P<opts>.*?)\}\s*\)", re.DOTALL)
    for match in pattern.finditer(text):
        arg = " ".join(match.group("arg").strip().split())
        opts = match.group("opts")
        method_match = re.search(r"method:\s*\"(?P<method>[A-Z]+)\"", opts)
        headers: list[str] = []
        if "Accept" in opts:
            headers.append("Accept: application/json")
        if "Authorization" in opts:
            headers.append("Authorization: visible bearer token")
        credentials = "omit" if 'credentials: "omit"' in opts else "default"
        calls.append(
            {
                "fetch_argument": arg,
                "method": method_match.group("method") if method_match else "GET",
                "headers": sorted(headers),
                "credentials": credentials,
                "uses_signal": "signal" in opts,
                "body_argument_present": re.search(r"\bbody\s*:", opts) is not None,
            }
        )
    return sorted(calls, key=lambda item: (str(item["fetch_argument"]), str(item["method"])))


def expected_fetch_calls(module: dict[str, Any]) -> list[dict[str, object]]:
    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for route in module["routes"]:
        signature = {
            "fetch_argument": route["fetch_argument"],
            "method": route["method"],
            "headers": sorted(route["headers"]),
            "credentials": route["credentials"],
            "uses_signal": route["uses_signal"],
            "body_argument_present": route["body_argument_present"],
        }
        key = tuple(
            (name, json.dumps(value, sort_keys=True)) for name, value in sorted(signature.items())
        )
        unique[key] = signature
    return sorted(
        unique.values(), key=lambda item: (str(item["fetch_argument"]), str(item["method"]))
    )


def assert_owner_and_classification(item: dict[str, Any], *, label: str) -> None:
    assert item.get("classification"), (
        f"{label} must declare live/deferred/forbidden classification"
    )
    assert item.get("owner_story"), f"{label} must declare owner_story"
    assert item.get("owner_phase"), f"{label} must declare owner_phase"


def test_story_128_1_inventory_matches_static_shell_script_allowlist() -> None:
    data = inventory()
    shell = shell_parser()
    approved_scripts = data["static_shell"]["approved_scripts"]

    assert data["schema_version"] == 2
    assert data["story"] == "128.1"
    assert data["runtime_rewiring_authorized"] is False
    assert data["contract_source"] == "post_epic_127_fact_inventory"
    assert data["shared_dashboard_guard"] is True
    assert "bidirectional guard tests" in data["shared_guard_update_policy"]
    assert data["static_shell"]["path"] == str(DASHBOARD)
    assert_owner_and_classification(data["static_shell"], label="static_shell")
    assert shell.scripts == [{"src": script, "defer": ""} for script in approved_scripts]
    assert not "".join(shell.inline_script_text).strip()
    assert data["static_shell"]["inline_scripts_authorized"] is False
    assert data["static_shell"]["modulepreload_authorized"] is False
    assert not [link for link in shell.links if link.get("rel") == "modulepreload"]
    assert sorted(path.name for path in STATIC_DIR.glob("*.js")) == sorted(approved_scripts)


def test_story_128_1_inventory_has_owner_classification_for_all_surfaces() -> None:
    data = inventory()
    for module in data["runtime_modules"]:
        assert_owner_and_classification(module, label=module["script"])
        for route in module["routes"]:
            assert_owner_and_classification(
                route, label=f"{module['script']} route {route['name']}"
            )
            assert route["method"] in {"GET", "POST"}
            assert route["request_body"] == "none"
            assert route["selector_source"]
            assert route["source_markers"], (
                f"{module['script']} route {route['name']} must declare exact source markers"
            )
            assert all(isinstance(marker, str) and marker for marker in route["source_markers"])
        assert set(FORBIDDEN_SIDE_CHANNEL_MARKERS) <= set(
            module["side_channel_policy"]["forbidden"]
        )
        surfaces = module["dom_surfaces"]
        assert {item["id"] for item in surfaces if item["role"] == "visible_control"} == set(
            module["visible_control_ids"]
        )
        assert {item["id"] for item in surfaces if item["role"] == "metadata_target"} == set(
            module["metadata_target_ids"]
        )
        assert {item["id"] for item in surfaces if item["role"] == "visible_source"} == set(
            module["visible_source_ids"]
        )
        for surface in surfaces:
            assert_owner_and_classification(
                surface, label=f"{module['script']} DOM {surface['id']}"
            )
            assert surface["role"] in {"visible_control", "metadata_target", "visible_source"}
        for source in module["passive_global_sources"]:
            assert_owner_and_classification(
                source, label=f"{module['script']} passive source {source['name']}"
            )
    for surface in data["deferred_or_forbidden_surfaces"]:
        assert_owner_and_classification(surface, label=surface["name"])
    assert_owner_and_classification(data["broad_cleanup"], label="broad_cleanup")
    assert data["broad_cleanup"]["runtime_rewiring_authorized"] is False


def test_story_128_1_inventory_matches_dom_ids_and_runtime_modules() -> None:
    data = inventory()
    shell = shell_parser()
    scripts = data["static_shell"]["approved_scripts"]
    modules = data["runtime_modules"]

    assert [module["script"] for module in modules] == scripts
    for module in modules:
        path = Path(module["runtime_path"])
        assert path == STATIC_DIR / module["script"]
        assert path.exists()
        dom_ids = set(
            module["visible_control_ids"]
            + module["metadata_target_ids"]
            + module["visible_source_ids"]
        )
        assert dom_ids <= shell.ids, (
            f"missing DOM ids for {module['script']}: {sorted(dom_ids - shell.ids)}"
        )
        text = path.read_text(encoding="utf-8")
        for dom_id in dom_ids:
            if (
                dom_id in module["metadata_target_ids"]
                or dom_id in module["visible_source_ids"]
                or dom_id in module["visible_control_ids"]
            ):
                assert dom_id in text or module["script"] == "aggregate-task-list.js"


def test_story_128_1_inventory_bidirectionally_matches_runtime_fetches_and_routes() -> None:
    data = inventory()
    for module in data["runtime_modules"]:
        script = module["script"]
        assert fetch_calls(script) == expected_fetch_calls(module), script
        text = uncommented_runtime_text(script)
        for route in module["routes"]:
            for marker in route["source_markers"]:
                assert marker in text, (
                    f"{script} missing exact source marker for inventory route "
                    f"{route['name']}: {marker}"
                )


def test_story_128_1_aggregate_inventory_reflects_epic_127_search_and_traversal() -> None:
    data = inventory()
    aggregate = data["aggregate_task_list"]
    module = next(
        item for item in data["runtime_modules"] if item["script"] == "aggregate-task-list.js"
    )
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    runtime = AGGREGATE_RUNTIME.read_text(encoding="utf-8")

    assert aggregate["owner_story"] == "127.4"
    assert aggregate["owner_phase"] == "Phase 48"
    assert aggregate["classification"] == "live_guarded"
    assert aggregate["approved_fetch_base"] == "/v1/tasks"
    assert aggregate["authorized_sort_values"] == [
        "updated_at_desc_id_asc",
        "created_at_desc_id_asc",
    ]
    assert aggregate["authorized_search_fields"] == [
        "task_id",
        "title",
        "actor_id",
        "last_event_type",
        "updated_at",
        "created_at",
    ]
    assert "Story 127.4 only" in aggregate["traversal_policy"]
    assert "MAX_TRAVERSAL_BUDGET = 5" in runtime
    assert 'credentials: "omit"' in runtime
    assert "field={task_search_field}&op={task_search_operator}&q={task_search_query}" in runtime
    for element_id in module["visible_control_ids"] + module["metadata_target_ids"]:
        assert f'id="{element_id}"' in dashboard


def test_story_128_1_lifecycle_create_is_visible_bearer_token_not_jwt_claim() -> None:
    data = inventory()
    module = next(
        item for item in data["runtime_modules"] if item["script"] == "lifecycle-snapshot.js"
    )
    create = next(route for route in module["routes"] if route["method"] == "POST")
    text = runtime_text("lifecycle-snapshot.js")

    assert create["classification"] == "live_visible_bearer_token_create_affordance"
    assert create["headers"] == ["Authorization: visible bearer token"]
    assert "browser checks only Bearer prefix" in create["notes"]
    assert 'token.startsWith("Bearer ")' in text
    assert "JWT" not in json.dumps(create)
    assert "JWT" not in text
    assert "lifecycle-snapshot-create-token" in module["visible_control_ids"]
    assert "lifecycle-snapshot-create-button" in module["visible_control_ids"]


def test_story_128_1_forbidden_side_channels_and_broad_cleanup_remain_closed() -> None:
    data = inventory()
    all_runtime = "\n".join(path.read_text(encoding="utf-8") for path in STATIC_DIR.glob("*.js"))
    for marker in FORBIDDEN_SIDE_CHANNEL_MARKERS:
        assert marker not in all_runtime

    # Timers are allowed only for existing bounded digest stream timeout; aggregate
    # traversal must not use timer/worker side channels.
    aggregate_runtime = runtime_text("aggregate-task-list.js")
    assert "setTimeout" not in aggregate_runtime
    assert "setInterval" not in aggregate_runtime
    assert "Worker" not in aggregate_runtime
    digest_runtime = runtime_text("digest-stream.js")
    assert "setTimeout" in digest_runtime
    assert "clearTimeout" in digest_runtime

    forbidden = {surface["name"]: surface for surface in data["deferred_or_forbidden_surfaces"]}
    assert forbidden["broad-dashboard-runtime-rewiring"]["classification"] == "deferred"
    assert forbidden["hidden-selectors"]["classification"] == "forbidden"
    assert forbidden["epic-128-closure-or-shipped-status"]["classification"] == "deferred"
    assert data["broad_cleanup"]["runtime_rewiring_authorized"] is False


def test_story_128_1_lifecycle_passive_global_evidence_source_is_inventoried() -> None:
    data = inventory()
    module = next(
        item for item in data["runtime_modules"] if item["script"] == "lifecycle-snapshot.js"
    )
    sources = module["passive_global_sources"]
    assert sources == [
        {
            "name": "window.LIFECYCLE_SNAPSHOT_EVIDENCE",
            "alternate_name": "LIFECYCLE_SNAPSHOT_EVIDENCE",
            "classification": "live_passive_global_evidence_source",
            "owner_story": "107.2",
            "owner_phase": "Phase 28",
            "policy": "optional passive lifecycle evidence object; absence renders non-authoritative degraded state and does not trigger adjacent fetches",
        }
    ]
    text = runtime_text("lifecycle-snapshot.js")
    assert "window.LIFECYCLE_SNAPSHOT_EVIDENCE" in text
    assert "typeof LIFECYCLE_SNAPSHOT_EVIDENCE" in text


def test_story_128_1_status_docs_remain_local_only_and_epic_128_in_progress() -> None:
    data = inventory()
    feature_status = FEATURE_STATUS.read_text(encoding="utf-8")
    sprint_status_text = SPRINT_STATUS.read_text(encoding="utf-8")
    sprint_status = yaml.safe_load(sprint_status_text)

    assert data["story"] == "128.1"
    assert "Story 128.1 is docs/tests/status-only" in feature_status
    assert "Epic 128 is now in progress" in feature_status
    assert "remote CI/shipped evidence remains pending until push" in feature_status
    assert "Epic 128 is shipped" not in feature_status
    assert "Epic 128 is complete" not in feature_status
    assert sprint_status["development_status"]["epic-128"] == "in-progress"
    assert (
        sprint_status["development_status"][
            "128-1-dashboard-wiring-inventory-cleanup-contract-refresh"
        ]
        == "done"
    )
    assert "remote CI/shipped evidence remains pending until push" in sprint_status_text
