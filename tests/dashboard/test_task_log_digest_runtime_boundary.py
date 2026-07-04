from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
DIGEST_RUNTIME = Path("dashboard/static/task-log-digest.js")
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
VISIBLE_TASK_ID = "fixture-task-id"
ROUTE_PREFIX = "/v1/tasks/"
ROUTE_SUFFIX = "/logs/digest"
APPROVED_ROUTE = f"{ROUTE_PREFIX}{VISIBLE_TASK_ID}{ROUTE_SUFFIX}"
DIGEST_PATTERN = "GET /v1/tasks/{task_id}/logs/digest"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/tasks/{task_id}/logs/digest/stream",
    "/v1/tasks?",
    "/v1/tasks/search",
    "/v1/sessions",
    "/v1/trace/",
    "/v1/events/replay",
    "/v1/health",
    "/v1/logs/digest",
    "stream",
    "aggregate",
    "search",
    "discover",
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
    "data-task-id",
    "location.search",
    "location.hash",
    "URLSearchParams(location",
    "Date.now",
    "new Date",
    "toISOString",
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
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class DigestBody(TypedDict, total=False):
    task_id: str
    digest: str
    summary: str
    display_state: str
    freshness_state: str
    retrieved_at: str
    completed_at: str
    provenance: str
    authority_state: str
    request_id: str
    trace_id: str
    correlation_id: str
    degraded_reason: str


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: DigestBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    response: NotRequired[RuntimeResponse]
    reject: NotRequired[str]
    taskIdText: NotRequired[str]
    hiddenTaskId: NotRequired[str]


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
        self.visible_task_id_text = ""
        self.task_source_attrs: dict[str, str] = {}
        self.controls: list[str] = []
        self._in_task_source = False

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
        if attrs_dict.get("id") == "task-log-digest-task-id-source":
            self._in_task_source = True
            self.task_source_attrs = attrs_dict

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.inline_script_depth:
            self.inline_script_depth -= 1
        if self._in_task_source:
            self._in_task_source = False

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)
        if self._in_task_source:
            self.visible_task_id_text += data


def parse_scripts() -> ScriptParser:
    parser = ScriptParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_source() -> str:
    return DIGEST_RUNTIME.read_text(encoding="utf-8")


def test_story_108_2_runtime_script_allowlist_is_exact() -> None:
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
        "select",
        "select",
        "input",
        "button",
        "input",
        "select",
        "button",
        "button",
        "input",
        "button",
    ]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert DIGEST_RUNTIME.exists()


def test_story_108_2_visible_task_id_source_is_not_hidden_data() -> None:
    parser = parse_scripts()
    assert parser.visible_task_id_text.strip() == VISIBLE_TASK_ID
    assert "data-task-id" not in parser.task_source_attrs
    raw = DASHBOARD.read_text(encoding="utf-8").lower()
    assert "visible task_id source" in raw
    assert "digest stream is a separate story 112.2 route-local panel" in raw
    assert "no browser-side generation" in raw


def test_story_108_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_108_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {ROUTE_PREFIX}
    assert ROUTE_SUFFIX in source

    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_108_2_panel_exposes_bounded_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "task-log-digest-task-id-source",
        "task-log-digest-status",
        "task-log-digest-source",
        "task-log-digest-task-id",
        "task-log-digest-freshness",
        "task-log-digest-authority",
        "task-log-digest-provenance",
        "task-log-digest-correlation",
        "task-log-digest-degraded",
        "task-log-digest-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert DIGEST_PATTERN in raw


def test_story_108_2_missing_task_id_does_not_fetch() -> None:
    output = run_digest_runtime_case({"name": "missing", "taskIdText": "", "expected": []})
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == []
    assert "missing task_id" in rendered
    assert "non-authoritative" in rendered


def test_story_108_2_hidden_task_id_decoy_is_ignored() -> None:
    output = run_digest_runtime_case(
        {
            "name": "hidden-decoy",
            "taskIdText": VISIBLE_TASK_ID,
            "hiddenTaskId": "hidden-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Bounded backend digest text.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "freshness_state": "fresh",
                    "provenance": "backend-digest-provider",
                    "correlation_id": "corr-1",
                },
            },
            "expected": ["healthy"],
        }
    )
    assert output["fetchCalls"] == [{"route": APPROVED_ROUTE, "method": "GET", "hasBody": False}]


def test_story_108_2_runtime_behavior_maps_success_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Backend digest text for the selected task.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "completed_at": "2026-06-25T23:59:00.000Z",
                    "freshness_state": "fresh",
                    "provenance": "backend-digest-provider",
                    "request_id": "req-1",
                    "trace_id": "trace-1",
                    "correlation_id": "corr-1",
                },
            },
            "expected": [
                "healthy",
                "authoritative",
                f"get {APPROVED_ROUTE}",
                "backend digest text",
                "backend-digest-provider",
                "corr-1",
            ],
        },
        {
            "name": "summary-fallback",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "summary": "Backend summary field.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "freshness_state": "fresh",
                    "provenance": "backend-digest-provider",
                },
            },
            "expected": ["healthy", "authoritative", "backend summary field"],
        },
        {
            "name": "missing-server-freshness",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Digest without server freshness.",
                },
            },
            "expected": ["invalid", "non-authoritative", "missing server freshness"],
        },
        {
            "name": "stale",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Stale digest text.",
                    "display_state": "stale",
                    "freshness_state": "stale",
                    "retrieved_at": "2026-06-25T00:00:00.000Z",
                    "degraded_reason": "stale digest",
                },
            },
            "expected": ["stale", "non-authoritative", "stale digest"],
        },
        {
            "name": "provider-unavailable",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "display_state": "provider-unavailable",
                    "degraded_reason": "no configured digest provider",
                },
            },
            "expected": [
                "provider-unavailable",
                "non-authoritative",
                "no configured digest provider",
            ],
        },
        {
            "name": "invalid-json",
            "response": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-display-state",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Digest with drifting state.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "freshness_state": "fresh",
                    "display_state": "authoritative-success",
                },
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-freshness-state",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Digest with drifting freshness.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "freshness_state": "current",
                },
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-empty-object",
            "response": {"ok": True, "status": 200, "body": {}},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "empty-digest",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "digest": ""},
            },
            "expected": ["empty-digest", "non-authoritative"],
        },
        {
            "name": "missing-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"digest": "Digest without task_id."},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "mismatched-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"task_id": "other-task-id", "digest": "Digest text."},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "response": {"ok": False, "status": 403, "body": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "timeout",
            "response": {"ok": False, "status": 504, "body": {}},
            "expected": ["backend unavailable", "non-authoritative"],
        },
        {
            "name": "network",
            "reject": "network down",
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_digest_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {"route": APPROVED_ROUTE, "method": "GET", "hasBody": False}
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] in {
            "invalid-json",
            "unexpected-display-state",
            "unexpected-freshness-state",
        }:
            assert (
                output["texts"]["task-log-digest-status"].lower()
                == "task log digest state: invalid."
            )
        if case["name"] != "healthy" and case["name"] != "summary-fallback":
            assert "authoritative digest" not in rendered
            assert "authoritative success" not in rendered


def test_story_108_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_digest_runtime_case(
        {
            "name": "already-loaded",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "digest": "Loaded digest.",
                    "retrieved_at": "2026-06-26T00:00:00.000Z",
                    "freshness_state": "fresh",
                },
            },
            "expected": ["healthy"],
        },
        ready_state="interactive",
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == [{"route": APPROVED_ROUTE, "method": "GET", "hasBody": False}]
    assert "healthy" in rendered
    assert "authoritative" in rendered


def run_digest_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(DIGEST_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: id === 'task-log-digest-task-id-source' ? (testCase.taskIdText ?? {json.dumps(VISIBLE_TASK_ID)}) : '',
              dataset: id === 'task-log-digest-task-id-source' ? {{ taskId: testCase.hiddenTaskId || '' }} : {{}},
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
          Date: class extends Date {{
            constructor(...args) {{ super(...(args.length ? args : ['2026-06-26T00:00:00.000Z'])); }}
            static now() {{ return new Date('2026-06-26T00:00:00.000Z').getTime(); }}
          }},
          document: {{
            readyState: {json.dumps(ready_state)},
            addEventListener: (_name, callback) => callbacks.push(callback),
            getElementById: element,
          }},
          window: {{}},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{ route, method: (options.method || 'GET').toUpperCase(), hasBody: Object.prototype.hasOwnProperty.call(options, 'body') }});
            if (testCase.reject) throw new Error(testCase.reject);
            const response = testCase.response;
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
        vm.runInContext(source, sandbox, {{ filename: 'task-log-digest.js' }});
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
        ["node", "-e", node_code],
        check=True,
        text=True,
        capture_output=True,
    )
    loaded = json.loads(completed.stdout)
    assert isinstance(loaded, dict)
    return cast(RuntimeOutput, loaded)


def test_story_128_5_task_log_digest_cleanup_helpers_remain_module_local() -> None:
    source = runtime_source()
    assert "function readFailureState(" in source
    assert "dashboard-shared" not in source
