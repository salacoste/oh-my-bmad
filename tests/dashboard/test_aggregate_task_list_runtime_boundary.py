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
ROUTE_PATTERN = "GET /v1/tasks?status={task_status}&limit={task_list_limit}"
ALLOWED_STATUSES = (
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
    select_match = re.search(
        r'<select id="aggregate-task-list-status-control"[^>]*>(?P<options>.*?)</select>',
        raw,
        re.DOTALL,
    )
    assert select_match is not None
    selected_status_match = re.search(
        r'<option value="(?P<status>[^"]+)" selected>',
        select_match.group("options"),
    )
    assert selected_status_match is not None
    limit_match = re.search(
        r'<input id="aggregate-task-list-limit-control"(?P<attrs>[^>]*)>',
        raw,
    )
    assert limit_match is not None
    selected_limit_match = re.search(r'\bvalue="(?P<limit>[^"]+)"', limit_match.group("attrs"))
    assert selected_limit_match is not None
    return selected_status_match.group("status"), selected_limit_match.group("limit")


DEFAULT_STATUS, DEFAULT_LIMIT = dashboard_default_selectors()
DEFAULT_ROUTE = f"/v1/tasks?status={DEFAULT_STATUS}&limit={DEFAULT_LIMIT}"
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
    "offset=",
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


def test_story_116_2_runtime_script_allowlist_and_visible_controls_are_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["select", "input", "button", "input", "button"]
    controls_by_id = {control.get("id"): control for control in parser.control_attrs}
    assert controls_by_id["aggregate-task-list-status-control"]["tag"] == "select"
    assert DEFAULT_STATUS == "pending"
    assert DEFAULT_STATUS in ALLOWED_STATUSES
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


def test_story_116_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_116_2_runtime_route_and_method_allowlist_is_exact() -> None:
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


def test_story_116_2_panel_exposes_status_limit_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "aggregate-task-list-status-control",
        "aggregate-task-list-limit-control",
        "aggregate-task-list-load",
        "aggregate-task-list-status",
        "aggregate-task-list-source",
        "aggregate-task-list-selected-status",
        "aggregate-task-list-selected-limit",
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
    assert "GET /v1/tasks?status={task_status}&amp;limit={task_list_limit}" in raw
    lowered = raw.lower()
    assert "visible status and limit controls" in lowered
    assert "no selector-free aggregate browser fetch" in lowered
    assert "no status-only browser fetch" in lowered
    assert "no limit-only browser fetch" in lowered
    assert "no request body" in lowered
    assert "no hidden selectors" in lowered


def task_row() -> dict[str, object]:
    return {
        "task_id": "t-1",
        "status": DEFAULT_STATUS,
        "title": "First task",
        "created_at": "2026-06-26T00:00:00Z",
        "updated_at": "2026-06-26T00:00:01Z",
        "state_since": "2026-06-26T00:00:01Z",
        "actor": {"kind": "operator", "id": "http-api"},
        "last_event": {
            "id": "e-1",
            "type": "task.created",
            "emitted_at": "2026-06-26T00:00:00Z",
            "trace_id": "trace-1",
        },
    }


def response_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "route": ROUTE_PATTERN,
        "selected_status": DEFAULT_STATUS,
        "selected_limit": int(DEFAULT_LIMIT),
        "retrieved_at": "2026-06-26T00:00:02Z",
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


def assert_default_status_limit_fetch(output: RuntimeOutput) -> None:
    assert output["fetchCalls"] == [
        {"route": DEFAULT_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
    ]


def test_story_116_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": response_body()},
            "expected": [
                "healthy",
                "authoritative",
                "get /v1/tasks?status={task_status}&limit={task_list_limit}",
                "runtime route: /v1/tasks?status=pending&limit=50",
                "selected status: pending",
                "selected limit: 50",
                "first task",
                "corr-1",
                "has_more false",
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
            "name": "selected-status-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(selected_status="blocked"),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "selected-limit-mismatch",
            "response": {"ok": True, "status": 200, "body": response_body(selected_limit=3)},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "ambiguous-freshness",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(freshness_state="ambiguous"),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-top-level-key",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**response_body(), "debug": "leak"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "last-event-summary-leak",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(
                    items=[
                        {
                            **task_row(),
                            "last_event": {**task_row()["last_event"], "summary": "leak"},
                        }
                    ]
                ),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "row-status-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "body": response_body(items=[{**task_row(), "status": "failed"}]),
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
        assert_default_status_limit_fetch(output)
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative aggregate" not in rendered


def test_story_116_2_runtime_rejects_invalid_or_missing_visible_controls_before_fetch() -> None:
    invalid_cases: list[RuntimeCase] = [
        {"name": "missing-status", "missingElements": ["aggregate-task-list-status-control"], "expected": ["invalid", "unavailable"]},
        {"name": "missing-limit", "missingElements": ["aggregate-task-list-limit-control"], "expected": ["invalid", "unavailable"]},
        {"name": "hidden-status", "controlTypes": {"aggregate-task-list-status-control": "hidden"}, "expected": ["invalid"]},
        {"name": "empty-status", "controlValues": {"aggregate-task-list-status-control": ""}, "expected": ["invalid"]},
        {"name": "unknown-status", "controlValues": {"aggregate-task-list-status-control": "ready"}, "expected": ["invalid"]},
        {"name": "uppercase-status", "controlValues": {"aggregate-task-list-status-control": "PLAN_READY"}, "expected": ["invalid"]},
        {"name": "empty-limit", "controlValues": {"aggregate-task-list-limit-control": ""}, "expected": ["invalid"]},
        {"name": "zero-limit", "controlValues": {"aggregate-task-list-limit-control": "0"}, "expected": ["invalid"]},
        {"name": "negative-limit", "controlValues": {"aggregate-task-list-limit-control": "-1"}, "expected": ["invalid"]},
        {"name": "fractional-limit", "controlValues": {"aggregate-task-list-limit-control": "1.5"}, "expected": ["invalid"]},
        {"name": "noninteger-limit", "controlValues": {"aggregate-task-list-limit-control": "two"}, "expected": ["invalid"]},
        {"name": "out-of-range-limit", "controlValues": {"aggregate-task-list-limit-control": "51"}, "expected": ["invalid"]},
        {"name": "unicode-digit-limit", "controlValues": {"aggregate-task-list-limit-control": "２"}, "expected": ["invalid"]},
        {"name": "encoded-digit-limit", "controlValues": {"aggregate-task-list-limit-control": "%32"}, "expected": ["invalid"]},
    ]
    for case in invalid_cases:
        output = run_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [], case["name"]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)


def test_story_116_2_runtime_supports_only_allowed_visible_statuses_and_limits() -> None:
    for status in ALLOWED_STATUSES:
        output = run_runtime_case(
            {
                "name": f"status-{status}",
                "controlValues": {
                    "aggregate-task-list-status-control": status,
                    "aggregate-task-list-limit-control": "1",
                },
                "response": {
                    "ok": True,
                    "status": 200,
                    "body": response_body(
                        selected_status=status,
                        selected_limit=1,
                        limit=1,
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
            {"route": f"/v1/tasks?status={status}&limit=1", "method": "GET", "hasBody": False, "credentials": "omit"}
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
            {"route": f"/v1/tasks?status={DEFAULT_STATUS}&limit={limit}", "method": "GET", "hasBody": False, "credentials": "omit"}
        ]


def test_story_116_2_runtime_behavior_runs_when_document_already_loaded() -> None:
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
    assert_default_status_limit_fetch(output)
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
          'aggregate-task-list-status-control': {json.dumps(DEFAULT_STATUS)},
          'aggregate-task-list-limit-control': {json.dumps(DEFAULT_LIMIT)},
        }}, testCase.controlValues || {{}});
        const controlTypes = Object.assign({{
          'aggregate-task-list-status-control': 'select-one',
          'aggregate-task-list-limit-control': 'number',
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
