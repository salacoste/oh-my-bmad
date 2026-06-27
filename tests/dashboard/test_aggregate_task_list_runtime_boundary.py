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
APPROVED_ROUTE = "/v1/tasks"
ROUTE_PATTERN = "GET /v1/tasks"
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
    "/v1/tasks?",
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
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*ROUTE(?P<options>[^)]*)\)", re.DOTALL)
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


def test_story_109_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert RUNTIME.exists()


def test_story_109_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_109_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {APPROVED_ROUTE}
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


def test_story_109_2_panel_exposes_bounded_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "aggregate-task-list-status",
        "aggregate-task-list-source",
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
    assert ROUTE_PATTERN in raw
    lowered = raw.lower()
    assert "no query selectors" in lowered
    assert "no request body" in lowered
    assert "no hidden selectors" in lowered


def test_story_109_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    row = {
        "task_id": "t-1",
        "status": "plan_ready",
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
    base_body: dict[str, object] = {
        "route": ROUTE_PATTERN,
        "retrieved_at": "2026-06-26T00:00:02Z",
        "freshness_state": "fresh",
        "display_state": "healthy",
        "authority_state": "authoritative",
        "provenance": "registry-state task summary list",
        "request_id": "req-1",
        "trace_id": "trace-root",
        "correlation_id": "corr-1",
        "limit": 50,
        "returned_count": 1,
        "has_more": False,
        "next_offset": None,
        "items": [row],
    }
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": base_body},
            "expected": [
                "healthy",
                "authoritative",
                "get /v1/tasks",
                "first task",
                "corr-1",
                "limit 50",
            ],
        },
        {
            "name": "empty",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    **base_body,
                    "display_state": "empty-list",
                    "authority_state": "non-authoritative",
                    "returned_count": 0,
                    "items": [],
                },
            },
            "expected": ["empty-list", "non-authoritative", "empty successful read"],
        },
        {
            "name": "stale",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    **base_body,
                    "display_state": "stale",
                    "authority_state": "non-authoritative",
                    "freshness_state": "stale",
                },
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "last-event-summary-leak",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    **base_body,
                    "items": [{**row, "last_event": {**row["last_event"], "summary": "leak"}}],
                },
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "over-limit",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "limit": 1, "returned_count": 2, "items": [row, row]},
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
        assert output["fetchCalls"] == [
            {"route": APPROVED_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative aggregate" not in rendered


def test_story_109_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_runtime_case(
        {
            "name": "already-loaded",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "route": ROUTE_PATTERN,
                    "retrieved_at": "2026-06-26T00:00:02Z",
                    "freshness_state": "fresh",
                    "display_state": "empty-list",
                    "authority_state": "non-authoritative",
                    "provenance": "registry-state task summary list",
                    "request_id": "req-1",
                    "trace_id": "trace-root",
                    "correlation_id": "corr-1",
                    "limit": 50,
                    "returned_count": 0,
                    "has_more": False,
                    "next_offset": None,
                    "items": [],
                },
            },
            "expected": ["empty-list"],
        },
        ready_state="interactive",
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == [
        {"route": APPROVED_ROUTE, "method": "GET", "hasBody": False, "credentials": "omit"}
    ]
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
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: '',
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
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
