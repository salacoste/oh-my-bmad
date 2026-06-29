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
ROUTE_PATTERN = "GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}"
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


def dashboard_default_selectors() -> tuple[str, str]:
    raw = DASHBOARD.read_text(encoding="utf-8")
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
    return selected_limit_match.group("limit"), selected_offset_match.group("offset")


DEFAULT_LIMIT, DEFAULT_OFFSET = dashboard_default_selectors()
DEFAULT_ROUTE = f"/v1/tasks?limit={DEFAULT_LIMIT}&offset={DEFAULT_OFFSET}"
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
    "status=",
    "cursor=",
    "page=",
    "sort=",
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
    reject: NotRequired[str]
    controlValues: NotRequired[dict[str, str]]
    controlTypes: NotRequired[dict[str, str]]
    missingElements: NotRequired[list[str]]


class FetchCall(TypedDict):
    route: str
    method: str
    hasBody: bool
    credentials: str | None


class RuntimeOutput(TypedDict):
    texts: dict[str, str]
    fetchCalls: list[FetchCall]


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


def test_story_118_2_runtime_script_allowlist_and_visible_controls_are_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "input", "button", "input", "button"]
    controls_by_id = {control.get("id"): control for control in parser.control_attrs}
    assert "aggregate-task-list-status-control" not in controls_by_id
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
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert "credentials" in fetches[0].group("options").lower()
    assert "omit" in fetches[0].group("options").lower()
    assert "include" not in fetches[0].group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_118_2_panel_exposes_limit_offset_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "aggregate-task-list-limit-control",
        "aggregate-task-list-offset-control",
        "aggregate-task-list-load",
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
    ):
        assert f'id="{element_id}"' in raw
    assert 'id="aggregate-task-list-selected-status"' not in raw
    assert 'id="aggregate-task-list-status-control"' not in raw
    assert "GET /v1/tasks?limit={task_list_limit}&amp;offset={task_list_offset}" in raw
    lowered = raw.lower()
    assert "visible limit and offset controls" in lowered
    assert "no status composition" in lowered
    assert "no automatic traversal" in lowered
    assert "no infinite scroll" in lowered
    assert "no search" in lowered
    assert "no sort" in lowered
    assert "no request body" in lowered
    assert "no hidden selectors" in lowered


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
        "selected_limit": int(DEFAULT_LIMIT),
        "selected_offset": int(DEFAULT_OFFSET),
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


def assert_default_limit_offset_fetch(output: RuntimeOutput) -> None:
    assert output["fetchCalls"] == [
        {"route": DEFAULT_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
    ]


def test_story_118_2_runtime_behavior_maps_success_empty_pagination_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": response_body()},
            "expected": [
                "healthy",
                "authoritative",
                "get /v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
                "runtime route: /v1/tasks?limit=50&offset=0",
                "selected limit: 50",
                "selected offset: 0",
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
            "expected": ["selected limit: 2", "selected offset: 1", "has_more true", "next_offset 3"],
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
            "response": {"ok": True, "status": 200, "body": response_body(authority_state="non-authoritative")},
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
            "name": "unexpected-selected-status",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**response_body(), "selected_status": "pending"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "bad-next-offset",
            "response": {"ok": True, "status": 200, "body": response_body(has_more=True, next_offset=1)},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "row-status-not-finite",
            "response": {"ok": True, "status": 200, "body": response_body(items=[task_row(status="ready")])},
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
                {"route": "/v1/tasks?limit=2&offset=1", "method": "GET", "hasBody": False, "credentials": "omit"}
            ]
        else:
            assert_default_limit_offset_fetch(output)
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] not in {"healthy", "has-more"}:
            assert "authoritative aggregate" not in rendered


def test_story_118_2_runtime_rejects_invalid_or_missing_visible_controls_before_fetch() -> None:
    invalid_cases: list[RuntimeCase] = [
        {"name": "missing-limit", "missingElements": ["aggregate-task-list-limit-control"], "expected": ["invalid", "unavailable"]},
        {"name": "missing-offset", "missingElements": ["aggregate-task-list-offset-control"], "expected": ["invalid", "unavailable"]},
        {"name": "hidden-limit", "controlTypes": {"aggregate-task-list-limit-control": "hidden"}, "expected": ["invalid"]},
        {"name": "hidden-offset", "controlTypes": {"aggregate-task-list-offset-control": "hidden"}, "expected": ["invalid"]},
        {"name": "empty-limit", "controlValues": {"aggregate-task-list-limit-control": ""}, "expected": ["invalid"]},
        {"name": "zero-limit", "controlValues": {"aggregate-task-list-limit-control": "0"}, "expected": ["invalid"]},
        {"name": "negative-limit", "controlValues": {"aggregate-task-list-limit-control": "-1"}, "expected": ["invalid"]},
        {"name": "fractional-limit", "controlValues": {"aggregate-task-list-limit-control": "1.5"}, "expected": ["invalid"]},
        {"name": "noninteger-limit", "controlValues": {"aggregate-task-list-limit-control": "two"}, "expected": ["invalid"]},
        {"name": "out-of-range-limit", "controlValues": {"aggregate-task-list-limit-control": "51"}, "expected": ["invalid"]},
        {"name": "unicode-digit-limit", "controlValues": {"aggregate-task-list-limit-control": "２"}, "expected": ["invalid"]},
        {"name": "encoded-digit-limit", "controlValues": {"aggregate-task-list-limit-control": "%32"}, "expected": ["invalid"]},
        {"name": "empty-offset", "controlValues": {"aggregate-task-list-offset-control": ""}, "expected": ["invalid"]},
        {"name": "negative-offset", "controlValues": {"aggregate-task-list-offset-control": "-1"}, "expected": ["invalid"]},
        {"name": "fractional-offset", "controlValues": {"aggregate-task-list-offset-control": "1.5"}, "expected": ["invalid"]},
        {"name": "noninteger-offset", "controlValues": {"aggregate-task-list-offset-control": "two"}, "expected": ["invalid"]},
        {"name": "unicode-digit-offset", "controlValues": {"aggregate-task-list-offset-control": "２"}, "expected": ["invalid"]},
        {"name": "encoded-digit-offset", "controlValues": {"aggregate-task-list-offset-control": "%31"}, "expected": ["invalid"]},
        {"name": "leading-zero-offset", "controlValues": {"aggregate-task-list-offset-control": "01"}, "expected": ["invalid"]},
        {"name": "out-of-range-offset", "controlValues": {"aggregate-task-list-offset-control": "2147483648"}, "expected": ["invalid"]},
        {"name": "oversized-offset-spelling", "controlValues": {"aggregate-task-list-offset-control": "99999999999"}, "expected": ["invalid"]},
    ]
    for case in invalid_cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [], case["name"]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)


def test_story_118_2_runtime_supports_only_allowed_visible_limits_and_offsets() -> None:
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
            {"route": f"/v1/tasks?limit={limit}&offset={DEFAULT_OFFSET}", "method": "GET", "hasBody": False, "credentials": "omit"}
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
            {"route": f"/v1/tasks?limit={DEFAULT_LIMIT}&offset={offset}", "method": "GET", "hasBody": False, "credentials": "omit"}
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
    assert_default_limit_offset_fetch(output)
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
        const missing = new Set(testCase.missingElements || []);
        const controlValues = Object.assign({{
          'aggregate-task-list-limit-control': {json.dumps(DEFAULT_LIMIT)},
          'aggregate-task-list-offset-control': {json.dumps(DEFAULT_OFFSET)},
        }}, testCase.controlValues || {{}});
        const controlTypes = Object.assign({{
          'aggregate-task-list-limit-control': 'number',
          'aggregate-task-list-offset-control': 'number',
        }}, testCase.controlTypes || {{}});
        function element(id) {{
          if (missing.has(id)) return null;
          if (!elements.has(id)) {{
            const node = {{
              value: Object.prototype.hasOwnProperty.call(controlValues, id) ? controlValues[id] : '',
              type: Object.prototype.hasOwnProperty.call(controlTypes, id) ? controlTypes[id] : undefined,
              _text: '',
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }},
              addEventListener: () => undefined,
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
            const response = testCase.response || {{ ok: true, status: 200, body: {{}} }};
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
        await new Promise((resolve) => setImmediate(resolve));
        process.stdout.write(JSON.stringify({{ texts, fetchCalls }}));
        }})();
        """
    )
    result = subprocess.run(["node", "-e", node_code], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)
