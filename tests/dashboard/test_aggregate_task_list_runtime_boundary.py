from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict

DASHBOARD = Path("dashboard/static/index.html")
RUNTIME = Path("dashboard/static/aggregate-task-list.js")
APPROVED_ROUTE_BASE = "/v1/tasks"
ROUTE_PATTERN = "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}"
SORT_VALUE = "updated_at_desc_id_asc"
CREATED_SORT_VALUE = "created_at_desc_id_asc"
SORT_VALUES = (SORT_VALUE, CREATED_SORT_VALUE)
MAX_OFFSET = 2_147_483_647
ALLOWED_ROW_STATUSES = (
    "pending",
    "planning",
    "plan_ready",
    "executing",
    "blocked",
    "completed",
    "stopped",
    "failed",
)


def dashboard_default_selectors() -> tuple[str, str, str]:
    raw = DASHBOARD.read_text(encoding="utf-8")
    status_match = re.search(
        r'<select id="aggregate-task-list-status-control"(?P<attrs>[^>]*)>(?P<body>.*?)</select>',
        raw,
        re.DOTALL,
    )
    assert status_match is not None
    selected_status_match = re.search(
        r'<option value="(?P<status>[^"]+)" selected>',
        status_match.group("body"),
    )
    assert selected_status_match is not None
    limit_match = re.search(
        r'<input id="aggregate-task-list-limit-control"(?P<attrs>[^>]*)>',
        raw,
    )
    assert limit_match is not None
    selected_limit_match = re.search(r'\bvalue="(?P<limit>[^"]+)"', limit_match.group("attrs"))
    assert selected_limit_match is not None
    offset_match = re.search(
        r'<input id="aggregate-task-list-offset-control"(?P<attrs>[^>]*)>',
        raw,
    )
    assert offset_match is not None
    selected_offset_match = re.search(r'\bvalue="(?P<offset>[^"]+)"', offset_match.group("attrs"))
    assert selected_offset_match is not None
    return (
        selected_status_match.group("status"),
        selected_limit_match.group("limit"),
        selected_offset_match.group("offset"),
    )


DEFAULT_STATUS, DEFAULT_LIMIT, DEFAULT_OFFSET = dashboard_default_selectors()
DEFAULT_ROUTE = f"/v1/tasks?status={DEFAULT_STATUS}&limit={DEFAULT_LIMIT}&offset={DEFAULT_OFFSET}&sort={SORT_VALUE}"
APPROVED_SCRIPTS = [
    "health-readiness.js",
    "task-detail.js",
    "aggregate-task-list.js",
    "session-list.js",
    "session-detail.js",
    "event-timeline.js",
    "trace-correlation.js",
    "history-replay.js",
    "lifecycle-snapshot.js",
    "task-log-digest.js",
    "digest-stream.js",
]
FORBIDDEN_RUNTIME_MARKERS = (
    "import ",
    "import(",
    "new Worker",
    "SharedWorker",
    "serviceWorker.register",
    "modulepreload",
    "preload",
    "setInterval",
    "setTimeout",
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "caches.open",
    "sendBeacon",
    "WebSocket",
    "EventSource",
    "XMLHttpRequest",
    "document.cookie",
    "credentials: 'include'",
    'credentials: "include"',
    "data-task-id",
    "location.search",
    "location.hash",
    "URLSearchParams(location",
    "Date.now",
    "new Date",
    "prompt",
    "completion",
    "openai",
    "anthropic",
    "llm",
    "summarize",
    "generate",
    "cacheWarm",
    "cache_warm",
    "background",
    "retry(",
)
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/tasks/search",
    "/v1/tasks/",
    "/v1/sessions",
    "/v1/trace/",
    "/v1/events/replay",
    "/v1/health",
    "/logs/digest",
    "stream",
    "search",
    "discover",
    "cursor=",
    "page=",
    "q=",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: dict[str, object]
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    response: NotRequired[RuntimeResponse]
    responses: NotRequired[list[RuntimeResponse]]
    reject: NotRequired[str]
    controlValues: NotRequired[dict[str, str]]
    controlTypes: NotRequired[dict[str, str]]
    missingElements: NotRequired[list[str]]
    clickTargets: NotRequired[list[str]]
    concurrentClickTargets: NotRequired[list[str]]
    mutateBeforeClicks: NotRequired[dict[str, str]]
    mutateBeforeClicksSilently: NotRequired[dict[str, str]]


class FetchCall(TypedDict):
    route: str
    method: str
    hasBody: bool
    credentials: str | None


class RuntimeOutput(TypedDict):
    texts: dict[str, str]
    fetchCalls: list[FetchCall]
    controlValues: dict[str, str]
    disabled: dict[str, bool]


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.inline_script_depth = 0
        self.inline_script_text: list[str] = []
        self.controls: list[str] = []
        self.control_attrs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag == "script":
            self.scripts.append(attrs_dict)
            if not attrs_dict.get("src"):
                self.inline_script_depth += 1
        if tag == "link":
            self.links.append(attrs_dict)
        if tag in {"form", "button", "input", "select", "textarea", "dialog"}:
            self.controls.append(tag)
            self.control_attrs.append({"tag": tag, **attrs_dict})

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline_script_depth:
            self.inline_script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)


def parse_scripts() -> ScriptParser:
    parser = ScriptParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_source() -> str:
    return RUNTIME.read_text(encoding="utf-8")


def raw_sort_options() -> list[tuple[str, bool]]:
    raw = DASHBOARD.read_text(encoding="utf-8")
    match = re.search(
        r'<select id="aggregate-task-list-sort-control"[^>]*>(?P<body>.*?)</select>',
        raw,
        re.DOTALL,
    )
    assert match is not None
    return [
        (
            option_match.group("value"),
            "selected" in (option_match.group("attrs") + option_match.group("attrs_after")),
        )
        for option_match in re.finditer(
            r'<option(?P<attrs>[^>]*)value="(?P<value>[^"]+)"(?P<attrs_after>[^>]*)>',
            match.group("body"),
        )
    ]


def test_story_118_2_runtime_script_allowlist_and_visible_controls_are_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == [
        "select",
        "input",
        "input",
        "button",
        "button",
        "button",
        "select",
        "input",
        "button",
    ]
    controls_by_id = {control.get("id"): control for control in parser.control_attrs}
    assert controls_by_id["aggregate-task-list-status-control"] == {
        "tag": "select",
        "id": "aggregate-task-list-status-control",
        "name": "aggregate-task-list-status-control",
    }
    assert controls_by_id["aggregate-task-list-limit-control"] == {
        "tag": "input",
        "id": "aggregate-task-list-limit-control",
        "name": "aggregate-task-list-limit-control",
        "type": "number",
        "min": "1",
        "max": "50",
        "step": "1",
        "value": "50",
    }
    assert controls_by_id["aggregate-task-list-limit-control"]["value"] == DEFAULT_LIMIT
    assert controls_by_id["aggregate-task-list-offset-control"] == {
        "tag": "input",
        "id": "aggregate-task-list-offset-control",
        "name": "aggregate-task-list-offset-control",
        "type": "number",
        "min": "0",
        "max": str(MAX_OFFSET),
        "step": "1",
        "value": "0",
    }
    assert controls_by_id["aggregate-task-list-offset-control"]["value"] == DEFAULT_OFFSET
    assert controls_by_id["aggregate-task-list-load"] == {
        "tag": "button",
        "id": "aggregate-task-list-load",
        "type": "button",
    }
    assert controls_by_id["aggregate-task-list-previous-offset"] == {
        "tag": "button",
        "id": "aggregate-task-list-previous-offset",
        "type": "button",
    }
    assert controls_by_id["aggregate-task-list-next-offset"] == {
        "tag": "button",
        "id": "aggregate-task-list-next-offset",
        "type": "button",
    }
    assert controls_by_id["aggregate-task-list-sort-control"] == {
        "tag": "select",
        "id": "aggregate-task-list-sort-control",
        "name": "aggregate-task-list-sort-control",
    }
    assert raw_sort_options() == [(SORT_VALUE, True), (CREATED_SORT_VALUE, False)]
    assert controls_by_id["lifecycle-snapshot-create-token"]["tag"] == "input"
    assert controls_by_id["lifecycle-snapshot-create-button"]["tag"] == "button"
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert RUNTIME.exists()


def test_story_118_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_118_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {APPROVED_ROUTE_BASE}
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    for fetch in fetches:
        method_match = METHOD_RE.search(fetch.group("options"))
        assert method_match is None or method_match.group("method").upper() == "GET"
        assert "body" not in fetch.group("options").lower()
        assert "credentials" in fetch.group("options").lower()
        assert "omit" in fetch.group("options").lower()
        assert "include" not in fetch.group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_118_2_panel_exposes_limit_offset_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "aggregate-task-list-status-control",
        "aggregate-task-list-limit-control",
        "aggregate-task-list-offset-control",
        "aggregate-task-list-load",
        "aggregate-task-list-previous-offset",
        "aggregate-task-list-next-offset",
        "aggregate-task-list-sort-control",
        "aggregate-task-list-status",
        "aggregate-task-list-source",
        "aggregate-task-list-selected-limit",
        "aggregate-task-list-selected-offset",
        "aggregate-task-list-freshness",
        "aggregate-task-list-authority",
        "aggregate-task-list-provenance",
        "aggregate-task-list-correlation",
        "aggregate-task-list-pagination",
        "aggregate-task-list-degraded",
        "aggregate-task-list-count",
        "aggregate-task-list-rows",
        "aggregate-task-list-selected-sort",
    ):
        assert f'id="{element_id}"' in raw
    assert 'id="aggregate-task-list-selected-status"' in raw
    assert 'id="aggregate-task-list-status-control"' in raw
    assert (
        "GET /v1/tasks?status={task_status}&amp;limit={task_list_limit}&amp;offset={task_list_offset}&amp;sort={task_sort}"
        in raw
    )
    lowered = raw.lower()
    assert "visible status, limit, offset, and sort controls" in lowered
    assert "no automatic traversal" in lowered
    assert "no infinite scroll" in lowered
    assert "no search" in lowered
    assert "full-composition read" in lowered
    assert "singleton" not in lowered
    assert "created_at_desc_id_asc" in lowered
    assert "no broader sort vocabulary" in lowered
    assert "preserving the selected sort" in lowered
    assert "no request body" in lowered
    assert "no hidden selectors" in lowered
    assert "manual previous" in lowered
    assert "manual next" in lowered


def task_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "task_id": "t-1",
        "status": "pending",
        "title": "First task",
        "created_at": "2026-06-29T00:00:00Z",
        "updated_at": "2026-06-29T00:00:01Z",
        "state_since": "2026-06-29T00:00:01Z",
        "actor": {"kind": "operator", "id": "http-api"},
        "last_event": {
            "id": "e-1",
            "type": "task.created",
            "emitted_at": "2026-06-29T00:00:00Z",
            "trace_id": "trace-1",
        },
    }
    row.update(overrides)
    return row


def response_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "route": ROUTE_PATTERN,
        "selected_status": DEFAULT_STATUS,
        "selected_limit": int(DEFAULT_LIMIT),
        "selected_offset": int(DEFAULT_OFFSET),
        "selected_sort": SORT_VALUE,
        "retrieved_at": "2026-06-29T00:00:02Z",
        "freshness_state": "fresh",
        "display_state": "healthy",
        "authority_state": "authoritative",
        "provenance": "registry-state task summary list",
        "request_id": "req-1",
        "trace_id": "trace-root",
        "correlation_id": "corr-1",
        "limit": int(DEFAULT_LIMIT),
        "returned_count": 1,
        "has_more": False,
        "next_offset": None,
        "items": [task_row()],
    }
    body.update(overrides)
    return body


def assert_default_status_limit_offset_fetch(output: RuntimeOutput) -> None:
    assert output["fetchCalls"] == [
        {"route": DEFAULT_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
    ]


def test_story_126_2_full_composition_default_and_primary_button_fetch_exact_route() -> None:
    output = run_runtime_case(
        {
            "name": "full-composition-default-sort-click",
            "controlValues": {
                "aggregate-task-list-status-control": "failed",
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "7",
            },
            "responses": [
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_status="failed",
                        selected_limit=2,
                        selected_offset=7,
                        selected_sort=SORT_VALUE,
                        limit=2,
                        returned_count=2,
                        has_more=True,
                        next_offset=9,
                        items=[
                            task_row(task_id="failed-1", status="failed"),
                            task_row(task_id="failed-2", status="failed"),
                        ],
                    ),
                },
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_status="failed",
                        selected_limit=2,
                        selected_offset=7,
                        selected_sort=SORT_VALUE,
                        limit=2,
                        returned_count=2,
                        has_more=True,
                        next_offset=9,
                        items=[
                            task_row(task_id="failed-3", status="failed"),
                            task_row(task_id="failed-4", status="failed"),
                        ],
                    ),
                },
            ],
            "clickTargets": ["aggregate-task-list-load"],
            "expected": ["updated_at_desc_id_asc", "runtime route"],
        }
    )
    expected_route = "/v1/tasks?status=failed&limit=2&offset=7&sort=updated_at_desc_id_asc"
    assert output["fetchCalls"] == [
        {"route": expected_route, "method": "GET", "hasBody": False, "credentials": "omit"},
        {"route": expected_route, "method": "GET", "hasBody": False, "credentials": "omit"},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert (
        "source: get /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}"
        in rendered
    )
    assert f"runtime route: {expected_route}" in rendered
    assert "selected status: failed" in rendered
    assert "selected limit: 2" in rendered
    assert "selected offset: 7" in rendered
    assert "selected sort: updated_at_desc_id_asc" in rendered
    assert "failed-3 failed" in rendered
    assert output["disabled"]["aggregate-task-list-previous-offset"] is False
    assert output["disabled"]["aggregate-task-list-next-offset"] is False


def test_story_126_2_created_at_sort_composes_with_visible_status_limit_offset() -> None:
    output = run_runtime_case(
        {
            "name": "created-at-full-composition",
            "controlValues": {
                "aggregate-task-list-status-control": "failed",
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "7",
                "aggregate-task-list-sort-control": CREATED_SORT_VALUE,
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_status="failed",
                    selected_limit=2,
                    selected_offset=7,
                    selected_sort=CREATED_SORT_VALUE,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=9,
                    items=[
                        task_row(
                            task_id="created-new",
                            status="failed",
                            created_at="2026-06-29T00:00:03Z",
                        ),
                        task_row(
                            task_id="created-old",
                            status="failed",
                            created_at="2026-06-29T00:00:01Z",
                        ),
                    ],
                ),
            },
            "expected": ["created_at_desc_id_asc", "runtime route"],
        }
    )
    expected_route = "/v1/tasks?status=failed&limit=2&offset=7&sort=created_at_desc_id_asc"
    assert output["fetchCalls"] == [
        {"route": expected_route, "method": "GET", "hasBody": False, "credentials": "omit"},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert f"runtime route: {expected_route}" in rendered
    assert "selected sort: created_at_desc_id_asc" in rendered
    assert "created-new failed" in rendered


def test_story_126_2_full_composition_rejects_invalid_visible_sort_before_fetch() -> None:
    invalid_cases: list[RuntimeCase] = [
        {
            "name": "missing-sort",
            "missingElements": ["aggregate-task-list-sort-control"],
            "expected": ["invalid"],
        },
        {
            "name": "hidden-sort",
            "controlTypes": {"aggregate-task-list-sort-control": "hidden"},
            "expected": ["invalid"],
        },
        {
            "name": "empty-sort",
            "controlValues": {"aggregate-task-list-sort-control": ""},
            "expected": ["invalid"],
        },
        {
            "name": "alias-sort",
            "controlValues": {"aggregate-task-list-sort-control": "updated_at"},
            "expected": ["invalid"],
        },
        {
            "name": "encoded-sort",
            "controlValues": {"aggregate-task-list-sort-control": "updated_at_desc_id%5Fasc"},
            "expected": ["invalid"],
        },
    ]
    for case in invalid_cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [], case["name"]
        assert (
            "invalid visible aggregate task-list status, limit, offset, or sort selector"
            in rendered
        )
        assert "authority: authoritative" not in rendered


def test_story_126_2_full_composition_response_validation_fails_closed() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "route-mismatch",
            "response": {"ok": True, "status": 200, "body": response_body(route="GET /v1/tasks")},
            "expected": ["invalid"],
        },
        {
            "name": "selected-sort-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(selected_sort=CREATED_SORT_VALUE),
            },
            "expected": ["invalid"],
        },
        {
            "name": "invalid-json",
            "response": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid"],
        },
        {
            "name": "unauthorized",
            "response": {"ok": False, "status": 403, "body": {}},
            "expected": ["unauthorized"],
        },
        {
            "name": "network",
            "reject": "network down",
            "expected": ["backend-unavailable"],
        },
    ]
    for case in cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {"route": DEFAULT_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        assert "authority: authoritative" not in rendered


def test_story_119_2_manual_navigation_controls_are_explicit_and_bounded() -> None:
    no_click = run_runtime_case(
        {
            "name": "next-metadata-without-click-does-not-traverse",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "1",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=1,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=3,
                    items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                ),
            },
            "expected": ["next_offset 3"],
        }
    )
    assert no_click["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    assert no_click["disabled"]["aggregate-task-list-next-offset"] is False
    assert no_click["disabled"]["aggregate-task-list-previous-offset"] is False

    next_click = run_runtime_case(
        {
            "name": "manual-next-click",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "1",
            },
            "responses": [
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=1,
                        limit=2,
                        returned_count=2,
                        has_more=True,
                        next_offset=3,
                        items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                    ),
                },
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=3,
                        limit=2,
                        returned_count=0,
                        has_more=False,
                        next_offset=None,
                        items=[],
                        display_state="empty-list",
                        authority_state="non-authoritative",
                    ),
                },
            ],
            "clickTargets": ["aggregate-task-list-next-offset"],
            "expected": ["selected offset: 3"],
        }
    )
    assert next_click["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=3&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
    ]
    assert next_click["controlValues"]["aggregate-task-list-offset-control"] == "3"

    previous_click = run_runtime_case(
        {
            "name": "manual-previous-click",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "3",
            },
            "responses": [
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=3,
                        limit=2,
                        returned_count=1,
                        has_more=False,
                        next_offset=None,
                        items=[task_row(task_id="t-3")],
                    ),
                },
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=1,
                        limit=2,
                        returned_count=1,
                        has_more=False,
                        next_offset=None,
                        items=[task_row(task_id="t-1")],
                    ),
                },
            ],
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["selected offset: 1"],
        }
    )
    assert previous_click["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=3&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
    ]
    assert previous_click["controlValues"]["aggregate-task-list-offset-control"] == "1"

    concurrent_previous_click = run_runtime_case(
        {
            "name": "manual-previous-concurrent-clicks-fail-closed",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "responses": [
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=10,
                        limit=2,
                        returned_count=1,
                        has_more=False,
                        next_offset=None,
                        items=[task_row(task_id="t-10")],
                    ),
                },
                {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=2,
                        selected_offset=8,
                        limit=2,
                        returned_count=0,
                        has_more=False,
                        next_offset=None,
                        items=[],
                        display_state="empty-list",
                        authority_state="non-authoritative",
                    ),
                },
            ],
            "concurrentClickTargets": [
                "aggregate-task-list-previous-offset",
                "aggregate-task-list-previous-offset",
            ],
            "expected": ["selected offset: 8"],
        }
    )
    assert concurrent_previous_click["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=10&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=8&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        },
    ]
    assert concurrent_previous_click["controlValues"]["aggregate-task-list-offset-control"] == "8"

    mutated_previous = run_runtime_case(
        {
            "name": "manual-previous-fails-closed-after-visible-offset-edit",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "3",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=3,
                    limit=2,
                    returned_count=1,
                    has_more=False,
                    next_offset=None,
                    items=[task_row(task_id="t-3")],
                ),
            },
            "mutateBeforeClicks": {"aggregate-task-list-offset-control": "10"},
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["invalid"],
        }
    )
    assert mutated_previous["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=3&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(mutated_previous["texts"].values()).lower()
    assert "non-authoritative" in rendered
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered
    assert mutated_previous["controlValues"]["aggregate-task-list-offset-control"] == "10"
    assert mutated_previous["disabled"]["aggregate-task-list-previous-offset"] is True

    mutated_previous_from_disabled = run_runtime_case(
        {
            "name": "manual-previous-stays-closed-after-visible-offset-edit-from-zero",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "0",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=0,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=2,
                    items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                ),
            },
            "mutateBeforeClicks": {"aggregate-task-list-offset-control": "10"},
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["invalid"],
        }
    )
    assert mutated_previous_from_disabled["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=0&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(mutated_previous_from_disabled["texts"].values()).lower()
    assert "non-authoritative" in rendered
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered
    assert mutated_previous_from_disabled["disabled"]["aggregate-task-list-previous-offset"] is True

    mutated_next = run_runtime_case(
        {
            "name": "manual-next-fails-closed-after-visible-selector-edit",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "1",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=1,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=3,
                    items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                ),
            },
            "mutateBeforeClicks": {"aggregate-task-list-limit-control": "3"},
            "clickTargets": ["aggregate-task-list-next-offset"],
            "expected": ["invalid"],
        }
    )
    assert mutated_next["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(mutated_next["texts"].values()).lower()
    assert "non-authoritative" in rendered
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered
    assert mutated_next["disabled"]["aggregate-task-list-next-offset"] is True
    assert mutated_next["disabled"]["aggregate-task-list-previous-offset"] is True

    invalid_next_selector_without_edit_event = run_runtime_case(
        {
            "name": "manual-next-renders-invalid-after-silent-invalid-selector",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "1",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=1,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=3,
                    items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                ),
            },
            "mutateBeforeClicksSilently": {"aggregate-task-list-sort-control": "updated_at"},
            "clickTargets": ["aggregate-task-list-next-offset"],
            "expected": ["invalid"],
        }
    )
    assert invalid_next_selector_without_edit_event["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(invalid_next_selector_without_edit_event["texts"].values()).lower()
    assert "invalid visible aggregate task-list status, limit, offset, or sort selector" in rendered
    assert "authority: authoritative" not in rendered
    assert (
        invalid_next_selector_without_edit_event["disabled"]["aggregate-task-list-next-offset"]
        is True
    )
    assert (
        invalid_next_selector_without_edit_event["disabled"]["aggregate-task-list-previous-offset"]
        is True
    )

    valid_previous_selector_without_edit_event = run_runtime_case(
        {
            "name": "manual-previous-rejects-silent-valid-selector-mutation",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=10,
                    limit=2,
                    returned_count=1,
                    has_more=False,
                    next_offset=None,
                    items=[task_row(task_id="t-10")],
                ),
            },
            "mutateBeforeClicksSilently": {"aggregate-task-list-sort-control": CREATED_SORT_VALUE},
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["invalid"],
        }
    )
    assert valid_previous_selector_without_edit_event["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=10&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(valid_previous_selector_without_edit_event["texts"].values()).lower()
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered
    assert (
        valid_previous_selector_without_edit_event["disabled"]["aggregate-task-list-next-offset"]
        is True
    )
    assert (
        valid_previous_selector_without_edit_event["disabled"][
            "aggregate-task-list-previous-offset"
        ]
        is True
    )

    invalid_status_edit = run_runtime_case(
        {
            "name": "visible-invalid-status-edit-clears-authoritative-state",
            "response": {"ok": True, "status": 200, "body": response_body()},
            "mutateBeforeClicks": {"aggregate-task-list-status-control": "ready"},
            "expected": ["invalid"],
        }
    )
    rendered = " ".join(invalid_status_edit["texts"].values()).lower()
    assert_default_status_limit_offset_fetch(invalid_status_edit)
    assert "non-authoritative" in rendered
    assert "invalid visible aggregate task-list status, limit, offset, or sort selector" in rendered
    assert "authority: authoritative" not in rendered

    valid_status_edit = run_runtime_case(
        {
            "name": "visible-status-edit-clears-authoritative-state",
            "response": {"ok": True, "status": 200, "body": response_body()},
            "mutateBeforeClicks": {"aggregate-task-list-status-control": "completed"},
            "expected": ["invalid"],
        }
    )
    rendered = " ".join(valid_status_edit["texts"].values()).lower()
    assert_default_status_limit_offset_fetch(valid_status_edit)
    assert "selected status: completed" in rendered
    assert "non-authoritative" in rendered
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered

    invalid_click = run_runtime_case(
        {
            "name": "invalid-response-next-click-fails-closed",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(has_more=True, next_offset=1),
            },
            "clickTargets": ["aggregate-task-list-next-offset"],
            "expected": ["invalid"],
        }
    )
    assert_default_status_limit_offset_fetch(invalid_click)
    assert invalid_click["disabled"]["aggregate-task-list-next-offset"] is True

    non_authoritative_previous_click_cases: list[RuntimeCase] = [
        {
            "name": "invalid-response-at-offset-keeps-previous-closed",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    route="GET /v1/tasks",
                    selected_limit=2,
                    selected_offset=10,
                    limit=2,
                    returned_count=1,
                    has_more=False,
                    next_offset=None,
                    items=[task_row(task_id="t-10")],
                ),
            },
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["invalid"],
        },
        {
            "name": "stale-response-at-offset-keeps-previous-closed",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=10,
                    freshness_state="stale",
                    display_state="stale",
                    authority_state="non-authoritative",
                    limit=2,
                    returned_count=1,
                    has_more=False,
                    next_offset=None,
                    items=[task_row(task_id="t-10")],
                ),
            },
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["stale"],
        },
        {
            "name": "backend-unavailable-at-offset-keeps-previous-closed",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "response": {"ok": False, "status": 503, "body": {}},
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["backend-unavailable"],
        },
    ]
    for case in non_authoritative_previous_click_cases:
        output = run_runtime_case(case)
        assert output["fetchCalls"] == [
            {
                "route": "/v1/tasks?status=pending&limit=2&offset=10&sort=updated_at_desc_id_asc",
                "method": "GET",
                "hasBody": False,
                "credentials": "omit",
            }
        ], case["name"]
        rendered = " ".join(output["texts"].values()).lower()
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        assert "authority: authoritative" not in rendered
        assert output["disabled"]["aggregate-task-list-previous-offset"] is True
        assert output["disabled"]["aggregate-task-list-next-offset"] is True

    stale_edit_after_non_authoritative = run_runtime_case(
        {
            "name": "stale-response-stays-closed-after-valid-selector-edit",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "10",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=10,
                    freshness_state="stale",
                    display_state="stale",
                    authority_state="non-authoritative",
                    limit=2,
                    returned_count=1,
                    has_more=False,
                    next_offset=None,
                    items=[task_row(task_id="t-10")],
                ),
            },
            "mutateBeforeClicks": {"aggregate-task-list-sort-control": CREATED_SORT_VALUE},
            "clickTargets": ["aggregate-task-list-previous-offset"],
            "expected": ["stale"],
        }
    )
    assert stale_edit_after_non_authoritative["fetchCalls"] == [
        {
            "route": "/v1/tasks?status=pending&limit=2&offset=10&sort=updated_at_desc_id_asc",
            "method": "GET",
            "hasBody": False,
            "credentials": "omit",
        }
    ]
    rendered = " ".join(stale_edit_after_non_authoritative["texts"].values()).lower()
    assert "reload required before manual pagination" in rendered
    assert "authority: authoritative" not in rendered
    assert (
        stale_edit_after_non_authoritative["disabled"]["aggregate-task-list-previous-offset"]
        is True
    )
    assert stale_edit_after_non_authoritative["disabled"]["aggregate-task-list-next-offset"] is True


def test_story_118_2_runtime_behavior_maps_success_empty_pagination_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": response_body()},
            "expected": [
                "healthy",
                "authoritative",
                "get /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}",
                "runtime route: /v1/tasks?status=pending&limit=50&offset=0&sort=updated_at_desc_id_asc",
                "selected status: pending",
                "selected limit: 50",
                "selected offset: 0",
                "selected sort: updated_at_desc_id_asc",
                "first task",
                "corr-1",
                "has_more false",
                "next_offset none",
            ],
        },
        {
            "name": "has-more",
            "controlValues": {
                "aggregate-task-list-limit-control": "2",
                "aggregate-task-list-offset-control": "1",
            },
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    selected_limit=2,
                    selected_offset=1,
                    limit=2,
                    returned_count=2,
                    has_more=True,
                    next_offset=3,
                    items=[task_row(task_id="t-2"), task_row(task_id="t-1")],
                ),
            },
            "expected": [
                "selected status: pending",
                "selected limit: 2",
                "selected offset: 1",
                "has_more true",
                "next_offset 3",
            ],
        },
        {
            "name": "empty",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    display_state="empty-list",
                    authority_state="non-authoritative",
                    returned_count=0,
                    items=[],
                ),
            },
            "expected": ["empty-list", "non-authoritative", "empty successful read"],
        },
        {
            "name": "stale-fail-closed",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    display_state="stale",
                    authority_state="non-authoritative",
                    freshness_state="stale",
                ),
            },
            "expected": ["stale", "non-authoritative", "not authoritative"],
        },
        {
            "name": "healthy-non-authoritative",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(authority_state="non-authoritative"),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "route-mismatch",
            "response": {"ok": True, "status": 200, "body": response_body(route="GET /v1/tasks")},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "selected-offset-mismatch",
            "response": {"ok": True, "status": 200, "body": response_body(selected_offset=1)},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "selected-status-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(selected_status="completed"),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "bad-next-offset",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(has_more=True, next_offset=1),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "row-status-not-finite",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(items=[task_row(status="ready")]),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "over-limit",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    returned_count=int(DEFAULT_LIMIT) + 1,
                    items=[task_row() for _ in range(int(DEFAULT_LIMIT) + 1)],
                ),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "response": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "response": {"ok": False, "status": 403, "body": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "network",
            "reject": "network down",
            "expected": ["backend-unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        if case["name"] == "has-more":
            assert output["fetchCalls"] == [
                {
                    "route": "/v1/tasks?status=pending&limit=2&offset=1&sort=updated_at_desc_id_asc",
                    "method": "GET",
                    "hasBody": False,
                    "credentials": "omit",
                }
            ]
        else:
            assert_default_status_limit_offset_fetch(output)
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] not in {"healthy", "has-more"}:
            assert "authoritative aggregate" not in rendered


def test_story_118_2_runtime_rejects_invalid_or_missing_visible_controls_before_fetch() -> None:
    invalid_cases: list[RuntimeCase] = [
        {
            "name": "missing-status",
            "missingElements": ["aggregate-task-list-status-control"],
            "expected": ["invalid", "unavailable"],
        },
        {
            "name": "hidden-status",
            "controlTypes": {"aggregate-task-list-status-control": "hidden"},
            "expected": ["invalid"],
        },
        {
            "name": "empty-status",
            "controlValues": {"aggregate-task-list-status-control": ""},
            "expected": ["invalid"],
        },
        {
            "name": "unknown-status",
            "controlValues": {"aggregate-task-list-status-control": "ready"},
            "expected": ["invalid"],
        },
        {
            "name": "encoded-status",
            "controlValues": {"aggregate-task-list-status-control": "plan%5Fready"},
            "expected": ["invalid"],
        },
        {
            "name": "missing-limit",
            "missingElements": ["aggregate-task-list-limit-control"],
            "expected": ["invalid", "unavailable"],
        },
        {
            "name": "missing-offset",
            "missingElements": ["aggregate-task-list-offset-control"],
            "expected": ["invalid", "unavailable"],
        },
        {
            "name": "hidden-limit",
            "controlTypes": {"aggregate-task-list-limit-control": "hidden"},
            "expected": ["invalid"],
        },
        {
            "name": "hidden-offset",
            "controlTypes": {"aggregate-task-list-offset-control": "hidden"},
            "expected": ["invalid"],
        },
        {
            "name": "empty-limit",
            "controlValues": {"aggregate-task-list-limit-control": ""},
            "expected": ["invalid"],
        },
        {
            "name": "zero-limit",
            "controlValues": {"aggregate-task-list-limit-control": "0"},
            "expected": ["invalid"],
        },
        {
            "name": "negative-limit",
            "controlValues": {"aggregate-task-list-limit-control": "-1"},
            "expected": ["invalid"],
        },
        {
            "name": "fractional-limit",
            "controlValues": {"aggregate-task-list-limit-control": "1.5"},
            "expected": ["invalid"],
        },
        {
            "name": "noninteger-limit",
            "controlValues": {"aggregate-task-list-limit-control": "two"},
            "expected": ["invalid"],
        },
        {
            "name": "out-of-range-limit",
            "controlValues": {"aggregate-task-list-limit-control": "51"},
            "expected": ["invalid"],
        },
        {
            "name": "unicode-digit-limit",
            "controlValues": {"aggregate-task-list-limit-control": "２"},
            "expected": ["invalid"],
        },
        {
            "name": "encoded-digit-limit",
            "controlValues": {"aggregate-task-list-limit-control": "%32"},
            "expected": ["invalid"],
        },
        {
            "name": "empty-offset",
            "controlValues": {"aggregate-task-list-offset-control": ""},
            "expected": ["invalid"],
        },
        {
            "name": "negative-offset",
            "controlValues": {"aggregate-task-list-offset-control": "-1"},
            "expected": ["invalid"],
        },
        {
            "name": "fractional-offset",
            "controlValues": {"aggregate-task-list-offset-control": "1.5"},
            "expected": ["invalid"],
        },
        {
            "name": "noninteger-offset",
            "controlValues": {"aggregate-task-list-offset-control": "two"},
            "expected": ["invalid"],
        },
        {
            "name": "unicode-digit-offset",
            "controlValues": {"aggregate-task-list-offset-control": "２"},
            "expected": ["invalid"],
        },
        {
            "name": "encoded-digit-offset",
            "controlValues": {"aggregate-task-list-offset-control": "%31"},
            "expected": ["invalid"],
        },
        {
            "name": "leading-zero-offset",
            "controlValues": {"aggregate-task-list-offset-control": "01"},
            "expected": ["invalid"],
        },
        {
            "name": "out-of-range-offset",
            "controlValues": {"aggregate-task-list-offset-control": "2147483648"},
            "expected": ["invalid"],
        },
        {
            "name": "oversized-offset-spelling",
            "controlValues": {"aggregate-task-list-offset-control": "99999999999"},
            "expected": ["invalid"],
        },
    ]
    for case in invalid_cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [], case["name"]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)


def test_story_121_2_runtime_supports_only_allowed_visible_statuses_limits_and_offsets() -> None:
    for status in ALLOWED_ROW_STATUSES:
        output = run_runtime_case(
            {
                "name": f"status-{status}",
                "controlValues": {"aggregate-task-list-status-control": status},
                "response": {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_status=status,
                        returned_count=0,
                        items=[],
                        display_state="empty-list",
                        authority_state="non-authoritative",
                    ),
                },
                "expected": [status],
            }
        )
        assert output["fetchCalls"] == [
            {
                "route": f"/v1/tasks?status={status}&limit={DEFAULT_LIMIT}&offset={DEFAULT_OFFSET}&sort={SORT_VALUE}",
                "method": "GET",
                "hasBody": False,
                "credentials": "omit",
            }
        ]

    for limit in ("1", "2", "50"):
        output = run_runtime_case(
            {
                "name": f"limit-{limit}",
                "controlValues": {"aggregate-task-list-limit-control": limit},
                "response": {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_limit=int(limit),
                        limit=int(limit),
                        returned_count=0,
                        items=[],
                        display_state="empty-list",
                        authority_state="non-authoritative",
                    ),
                },
                "expected": [limit],
            }
        )
        assert output["fetchCalls"] == [
            {
                "route": f"/v1/tasks?status={DEFAULT_STATUS}&limit={limit}&offset={DEFAULT_OFFSET}&sort={SORT_VALUE}",
                "method": "GET",
                "hasBody": False,
                "credentials": "omit",
            }
        ]

    for offset in ("0", "1", str(MAX_OFFSET)):
        output = run_runtime_case(
            {
                "name": f"offset-{offset}",
                "controlValues": {"aggregate-task-list-offset-control": offset},
                "response": {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_offset=int(offset),
                        returned_count=0,
                        items=[],
                        display_state="empty-list",
                        authority_state="non-authoritative",
                    ),
                },
                "expected": [offset],
            }
        )
        assert output["fetchCalls"] == [
            {
                "route": f"/v1/tasks?status={DEFAULT_STATUS}&limit={DEFAULT_LIMIT}&offset={offset}&sort={SORT_VALUE}",
                "method": "GET",
                "hasBody": False,
                "credentials": "omit",
            }
        ]


def test_story_118_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_runtime_case(
        {
            "name": "already-loaded",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    display_state="empty-list",
                    authority_state="non-authoritative",
                    returned_count=0,
                    items=[],
                ),
            },
            "expected": ["empty-list"],
        },
        ready_state="interactive",
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert_default_status_limit_offset_fetch(output)
    assert "empty-list" in rendered


def run_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        (async () => {{
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        const listeners = new Map();
        const missing = new Set(testCase.missingElements || []);
        const controlValues = Object.assign({{
          'aggregate-task-list-status-control': {json.dumps(DEFAULT_STATUS)},
          'aggregate-task-list-limit-control': {json.dumps(DEFAULT_LIMIT)},
          'aggregate-task-list-offset-control': {json.dumps(DEFAULT_OFFSET)},
          'aggregate-task-list-sort-control': {json.dumps(SORT_VALUE)},
        }}, testCase.controlValues || {{}});
        const controlTypes = Object.assign({{
          'aggregate-task-list-status-control': 'select-one',
          'aggregate-task-list-limit-control': 'number',
          'aggregate-task-list-offset-control': 'number',
          'aggregate-task-list-sort-control': 'select-one',
        }}, testCase.controlTypes || {{}});
        function element(id) {{
          if (missing.has(id)) return null;
          if (!elements.has(id)) {{
            const node = {{
              value: Object.prototype.hasOwnProperty.call(controlValues, id) ? controlValues[id] : '',
              type: Object.prototype.hasOwnProperty.call(controlTypes, id) ? controlTypes[id] : undefined,
              disabled: false,
              _text: '',
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }},
              addEventListener: (name, callback) => {{
                if (!listeners.has(id)) listeners.set(id, {{}});
                listeners.get(id)[name] = callback;
              }},
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        const sandbox = {{
          console,
          Set,
          Object,
          Array,
          String,
          Boolean,
          Number,
          RegExp,
          window: {{}},
          document: {{
            readyState: {json.dumps(ready_state)},
            getElementById: element,
            addEventListener: (_name, callback) => callbacks.push(callback)
          }},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{ route, method: options.method || 'GET', hasBody: Object.prototype.hasOwnProperty.call(options, 'body'), credentials: options.credentials || null }});
            if (testCase.reject) throw new Error(testCase.reject);
            const responses = testCase.responses || (testCase.response ? [testCase.response] : [{{ ok: true, status: 200, body: {{}} }}]);
            const response = responses[Math.min(fetchCalls.length - 1, responses.length - 1)];
            return {{
              ok: response.ok,
              status: response.status,
              json: async () => {{
                if (response.jsonError) throw new Error(response.jsonError);
                return response.body;
              }}
            }};
          }}
        }};
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, {{ filename: 'aggregate-task-list.js' }});
        for (const callback of callbacks) {{ await callback(); }}
        if (sandbox.window.__aggregateTaskListReady) {{ await sandbox.window.__aggregateTaskListReady; }}
        await Promise.resolve();
        await Promise.resolve();
        for (const [id, value] of Object.entries(testCase.mutateBeforeClicksSilently || {{}})) {{
          const node = element(id);
          if (node) node.value = String(value);
        }}
        for (const [id, value] of Object.entries(testCase.mutateBeforeClicks || {{}})) {{
          const node = element(id);
          if (node) {{
            node.value = String(value);
            const inputHandler = listeners.get(id)?.input;
            const changeHandler = listeners.get(id)?.change;
            if (inputHandler) inputHandler();
            if (changeHandler) changeHandler();
          }}
        }}
        const concurrentClicks = [];
        for (const clickId of testCase.concurrentClickTargets || []) {{
          const node = elements.get(clickId);
          const handler = listeners.get(clickId)?.click;
          if (node && !node.disabled && handler) {{
            const clicked = handler();
            if (clicked && typeof clicked.then === 'function') concurrentClicks.push(clicked);
          }}
        }}
        for (const clicked of concurrentClicks) {{ await clicked; }}
        if (concurrentClicks.length && sandbox.window.__aggregateTaskListReady) {{ await sandbox.window.__aggregateTaskListReady; }}
        for (const clickId of testCase.clickTargets || []) {{
          const node = elements.get(clickId);
          const handler = listeners.get(clickId)?.click;
          if (node && !node.disabled && handler) {{
            const clicked = handler();
            if (clicked && typeof clicked.then === 'function') await clicked;
            if (sandbox.window.__aggregateTaskListReady) {{ await sandbox.window.__aggregateTaskListReady; }}
            await Promise.resolve();
            await Promise.resolve();
          }}
        }}
        await new Promise((resolve) => setImmediate(resolve));
        const controlSnapshot = {{}};
        const disabled = {{}};
        for (const [id, node] of elements.entries()) {{
          controlSnapshot[id] = String(node.value ?? '');
          disabled[id] = Boolean(node.disabled);
        }}
        process.stdout.write(JSON.stringify({{ texts, fetchCalls, controlValues: controlSnapshot, disabled }}));
        }})();
        """
    )
    result = subprocess.run(["node", "-e", node_code], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)
