from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
TRACE_RUNTIME = Path("dashboard/static/trace-correlation.js")
APPROVED_HEALTH_SCRIPT = "health-readiness.js"
APPROVED_TASK_DETAIL_SCRIPT = "task-detail.js"
APPROVED_EVENT_SCRIPT = "event-timeline.js"
APPROVED_AGGREGATE_SCRIPT = "aggregate-task-list.js"
APPROVED_SESSION_SCRIPT = "session-list.js"
APPROVED_SESSION_DETAIL_SCRIPT = "session-detail.js"
APPROVED_TRACE_SCRIPT = "trace-correlation.js"
APPROVED_HISTORY_REPLAY_SCRIPT = "history-replay.js"
APPROVED_LIFECYCLE_SCRIPT = "lifecycle-snapshot.js"
APPROVED_DIGEST_SCRIPT = "task-log-digest.js"
APPROVED_DIGEST_STREAM_SCRIPT = "digest-stream.js"
APPROVED_SCRIPTS = [
    APPROVED_HEALTH_SCRIPT,
    APPROVED_TASK_DETAIL_SCRIPT,
    APPROVED_AGGREGATE_SCRIPT,
    APPROVED_SESSION_SCRIPT,
    APPROVED_SESSION_DETAIL_SCRIPT,
    APPROVED_EVENT_SCRIPT,
    APPROVED_TRACE_SCRIPT,
    APPROVED_HISTORY_REPLAY_SCRIPT,
    APPROVED_LIFECYCLE_SCRIPT,
    APPROVED_DIGEST_SCRIPT,
    APPROVED_DIGEST_STREAM_SCRIPT,
]
VISIBLE_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000001"
ENCODED_TRACE_ID = VISIBLE_TRACE_ID
TRACE_ROUTE_PREFIX = "/v1/trace/"
APPROVED_TRACE_ROUTE = f"{TRACE_ROUTE_PREFIX}{ENCODED_TRACE_ID}"
TRACE_PATTERN = "GET /v1/trace/{trace_id}"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/traces",
    "/v1/trace?",
    "/v1/trace/search",
    "/v1/tasks?",
    "/v1/tasks/",
    "/v1/tasks/{task_id}/history",
    "/v1/tasks/{task_id}/logs/digest",
    "/v1/sessions",
    "/v1/events/replay",
    "/v1/health",
)
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
    "data-trace-id",
    "data-event-id",
    "data-task-id",
    "data-session-id",
    "location.search",
    "location.hash",
    "history",
    "replay",
    "lifecycle",
    "aggregate",
    "digest",
    "search",
    "list",
    "discover",
    "sessionId",
    "taskId",
    "eventId",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class TraceRow(TypedDict, total=False):
    trace_id: str
    event_id: str
    task_id: str
    session_id: str
    emitted_at: str
    summary: str


class TraceBody(TypedDict, total=False):
    trace_id: str
    display_state: str
    retrieved_at: str
    freshness_state: str
    authority: str
    events: list[TraceRow]
    linked_event_id: str
    linked_task_id: str
    linked_session_id: str


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: TraceBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    traceResponse: NotRequired[RuntimeResponse]
    traceReject: NotRequired[str]
    traceIdText: NotRequired[str]
    hiddenTraceId: NotRequired[str]
    hiddenEventId: NotRequired[str]
    hiddenTaskId: NotRequired[str]
    hiddenSessionId: NotRequired[str]


class FetchCall(TypedDict):
    route: str
    method: str
    hasBody: bool


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
        self.visible_trace_id_text = ""
        self.trace_source_attrs: dict[str, str] = {}
        self._in_trace_source = False
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
        if attrs_dict.get("id") == "trace-correlation-trace-id-source":
            self._in_trace_source = True
            self.trace_source_attrs = attrs_dict

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline_script_depth:
            self.inline_script_depth -= 1
        if self._in_trace_source:
            self._in_trace_source = False

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)
        if self._in_trace_source:
            self.visible_trace_id_text += data


def parse_scripts() -> ScriptParser:
    parser = ScriptParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_source() -> str:
    return TRACE_RUNTIME.read_text(encoding="utf-8")


def test_story_104_2_trace_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "input", "button", "button", "button", "input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert TRACE_RUNTIME.exists()


def test_story_104_2_visible_trace_id_source_is_only_selector() -> None:
    parser = parse_scripts()
    assert parser.visible_trace_id_text.strip() == VISIBLE_TRACE_ID
    assert "data-trace-id" not in parser.trace_source_attrs
    raw = DASHBOARD.read_text(encoding="utf-8").lower()
    assert "trace_id selector" in raw
    assert "no trace search/list/discovery" in raw
    assert "event_id, task_id, and session_id metadata only" in raw


def test_story_104_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_104_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {TRACE_ROUTE_PREFIX}
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert "readRoute(route" in source
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_104_2_runtime_does_not_fabricate_freshness() -> None:
    source = runtime_source()
    assert "new Date" not in source
    assert "Date.now" not in source
    assert "toISOString" not in source


def test_story_104_2_missing_returned_freshness_is_not_fabricated() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy-missing-retrieved-at",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "events": [{"trace_id": VISIBLE_TRACE_ID, "event_id": "evt-1"}],
                },
            },
            "expected": ["not returned"],
        },
        {
            "name": "invalid-json",
            "traceResponse": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["not returned"],
        },
        {
            "name": "unauthorized",
            "traceResponse": {"ok": False, "status": 403, "body": {}},
            "expected": ["not returned"],
        },
        {"name": "network", "traceReject": "network down", "expected": ["not returned"]},
    ]
    for case in cases:
        output = run_trace_runtime_case(case)
        freshness = output["texts"].get("trace-correlation-freshness", "").lower()
        assert "freshness: not returned" in freshness, (case["name"], freshness)
        assert "2026-" not in freshness, (case["name"], freshness)


def test_story_104_2_trace_panel_exposes_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "trace-correlation-trace-id-source",
        "trace-correlation-status",
        "trace-correlation-source",
        "trace-correlation-trace-id",
        "trace-correlation-freshness",
        "trace-correlation-authority",
        "trace-correlation-detail",
        "trace-correlation-row-count",
        "trace-correlation-linked-identifiers",
    ):
        assert f'id="{element_id}"' in raw
    assert TRACE_PATTERN in raw
    assert "no trace search/list/discovery" in raw.lower()


def test_story_104_2_missing_trace_id_does_not_fetch() -> None:
    output = run_trace_runtime_case({"name": "missing", "traceIdText": "", "expected": []})
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == []
    assert "missing trace_id" in rendered
    assert "non-authoritative" in rendered


def test_story_104_2_hidden_identifier_decoys_are_ignored() -> None:
    output = run_trace_runtime_case(
        {
            "name": "hidden-decoys",
            "traceIdText": VISIBLE_TRACE_ID,
            "hiddenTraceId": "hidden-trace-id",
            "hiddenEventId": "hidden-event-id",
            "hiddenTaskId": "hidden-task-id",
            "hiddenSessionId": "hidden-session-id",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "retrieved_at": "2026-06-23T00:00:00.000Z",
                    "events": [
                        {
                            "trace_id": VISIBLE_TRACE_ID,
                            "event_id": "evt-visible",
                            "task_id": "task-visible",
                            "session_id": "session-visible",
                        }
                    ],
                },
            },
            "expected": ["healthy", "authoritative", "1 rows", "metadata only"],
        }
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_TRACE_ROUTE, "method": "GET", "hasBody": False}
    ]
    rendered = " ".join(output["texts"].values()).lower()
    for hidden in ("hidden-trace-id", "hidden-event-id", "hidden-task-id", "hidden-session-id"):
        assert hidden not in rendered
    assert "evt-visible" in rendered
    assert "task-visible" in rendered
    assert "session-visible" in rendered


def test_story_104_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "retrieved_at": "2026-06-23T00:00:00.000Z",
                    "events": [{"trace_id": VISIBLE_TRACE_ID, "event_id": "evt-1"}],
                },
            },
            "expected": ["healthy", "authoritative", "1 rows", "get /v1/trace/01917e5c"],
        },
        {
            "name": "empty",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"trace_id": VISIBLE_TRACE_ID, "events": []},
            },
            "expected": ["empty", "non-authoritative", "0 rows"],
        },
        {
            "name": "partial",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"trace_id": VISIBLE_TRACE_ID, "display_state": "partial", "events": []},
            },
            "expected": ["partial", "non-authoritative"],
        },
        {
            "name": "stale",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"trace_id": VISIBLE_TRACE_ID, "freshness_state": "stale", "events": []},
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "traceResponse": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-shape",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"events": [{"event_id": "evt-1"}]},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "mismatched-trace-id",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"trace_id": "other", "events": []},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "traceResponse": {"ok": False, "status": 403, "body": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "network",
            "traceReject": "network down",
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_trace_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {"route": APPROVED_TRACE_ROUTE, "method": "GET", "hasBody": False}
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_104_2_mismatched_trace_rows_do_not_render_linked_identifiers() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "partial-mismatched-row",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "display_state": "partial",
                    "events": [
                        {
                            "trace_id": "other-trace",
                            "event_id": "evt-other",
                            "task_id": "task-other",
                            "session_id": "session-other",
                        }
                    ],
                },
            },
            "expected": ["invalid", "0 rows", "none returned"],
        },
        {
            "name": "stale-mismatched-row",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "freshness_state": "stale",
                    "events": [
                        {
                            "trace_id": "other-trace",
                            "event_id": "evt-stale-other",
                            "task_id": "task-stale-other",
                            "session_id": "session-stale-other",
                        }
                    ],
                },
            },
            "expected": ["invalid", "0 rows", "none returned"],
        },
        {
            "name": "healthy-mismatched-row",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "trace_id": VISIBLE_TRACE_ID,
                    "events": [
                        {
                            "trace_id": "other-trace",
                            "event_id": "evt-healthy-other",
                            "task_id": "task-healthy-other",
                            "session_id": "session-healthy-other",
                        }
                    ],
                },
            },
            "expected": ["invalid", "0 rows", "none returned"],
        },
    ]
    for case in cases:
        output = run_trace_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        assert "other-trace" not in rendered, (case["name"], rendered)
        assert "evt-" not in rendered, (case["name"], rendered)
        assert "task-" not in rendered, (case["name"], rendered)
        assert "session-" not in rendered, (case["name"], rendered)


def test_story_104_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_trace_runtime_case(
        {
            "name": "already-loaded",
            "traceResponse": {
                "ok": True,
                "status": 200,
                "body": {"trace_id": VISIBLE_TRACE_ID, "events": []},
            },
            "expected": ["empty"],
        },
        ready_state="interactive",
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_TRACE_ROUTE, "method": "GET", "hasBody": False}
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "empty" in rendered
    assert "non-authoritative" in rendered


def run_trace_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(TRACE_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: id === 'trace-correlation-trace-id-source' ? (testCase.traceIdText ?? {json.dumps(VISIBLE_TRACE_ID)}) : '',
              dataset: id === 'trace-correlation-trace-id-source' ? {{ traceId: testCase.hiddenTraceId || '', eventId: testCase.hiddenEventId || '', taskId: testCase.hiddenTaskId || '', sessionId: testCase.hiddenSessionId || '' }} : {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        async function responseFor(route) {{
          if (route.startsWith('/v1/trace/')) {{
            if (testCase.traceReject) throw new Error(testCase.traceReject);
            return testCase.traceResponse;
          }}
          throw new Error('unexpected route ' + route);
        }}
        const sandbox = {{
          console,
          Date: class extends Date {{
            constructor(...args) {{ super(...(args.length ? args : ['2026-06-23T00:00:00.000Z'])); }}
            static now() {{ return new Date('2026-06-23T00:00:00.000Z').getTime(); }}
          }},
          document: {{
            readyState: {json.dumps(ready_state)},
            addEventListener: (_name, callback) => callbacks.push(callback),
            getElementById: element,
          }},
          window: {{}},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{ route, method: (options.method || 'GET').toUpperCase(), hasBody: Object.prototype.hasOwnProperty.call(options, 'body') }});
            const response = await responseFor(route);
            return {{
              ok: response.ok,
              status: response.status,
              json: async () => {{
                if (response.jsonError) throw new Error(response.jsonError);
                return response.body;
              }},
            }};
          }},
        }};
        sandbox.window = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, {{ filename: 'trace-correlation.js' }});
        Promise.resolve(callbacks[0] ? callbacks[0]() : undefined)
          .then(() => new Promise((resolve) => setImmediate(resolve)))
          .then(() => {{
            process.stdout.write(JSON.stringify({{ texts, fetchCalls }}));
          }}).catch((error) => {{
            console.error(error);
            process.exit(1);
          }});
        """
    )
    completed = subprocess.run(
        ["node", "-e", node_code], check=True, text=True, capture_output=True
    )
    loaded = json.loads(completed.stdout)
    assert isinstance(loaded, dict)
    return cast(RuntimeOutput, loaded)
