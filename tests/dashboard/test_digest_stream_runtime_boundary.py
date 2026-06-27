from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
RUNTIME = Path("dashboard/static/digest-stream.js")
VISIBLE_TASK_ID = "fixture-task-id"
ROUTE_PREFIX = "/v1/tasks/"
ROUTE_SUFFIX = "/logs/digest/stream"
APPROVED_ROUTE = f"{ROUTE_PREFIX}{VISIBLE_TASK_ID}{ROUTE_SUFFIX}"
ROUTE_PATTERN = "GET /v1/tasks/{task_id}/logs/digest/stream"
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
    "summarize",
    "generate",
    "cacheWarm",
    "cache_warm",
    "retry(",
)
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/tasks?",
    "/v1/tasks/search",
    "/v1/sessions",
    "/v1/trace/",
    "/v1/events/replay",
    "/v1/health",
    "/v1/logs/digest",
    '/logs/digest"',
    "/logs/digest'",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class Frame(TypedDict, total=False):
    type: str
    task_id: str
    route: str
    sequence: int
    chunk: str
    retrieved_at: str
    freshness_state: str
    display_state: str
    authority_state: str
    provenance: str
    request_id: str
    trace_id: str
    correlation_id: str
    chunk_count: int
    line_count: int


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    headers: dict[str, str]
    frames: list[Frame]
    brokenText: str
    reject: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    response: NotRequired[RuntimeResponse]
    taskIdText: NotRequired[str]
    hiddenTaskId: NotRequired[str]


class FetchCall(TypedDict):
    route: str
    method: str
    hasBody: bool
    hasSignal: bool


class RuntimeOutput(TypedDict):
    texts: dict[str, str]
    fetchCalls: list[FetchCall]


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str]] = []
        self.inline_script_text: list[str] = []
        self.inline_script_depth = 0
        self.visible_task_id_text = ""
        self.task_source_attrs: dict[str, str] = {}
        self._in_task_source = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag == "script":
            self.scripts.append(attrs_dict)
            if not attrs_dict.get("src"):
                self.inline_script_depth += 1
        if attrs_dict.get("id") == "digest-stream-task-id-source":
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
    return RUNTIME.read_text(encoding="utf-8")


def test_story_112_2_runtime_script_allowlist_is_additive_and_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert sorted(path.name for path in Path("dashboard/static").glob("*.js")) == sorted(
        APPROVED_SCRIPTS
    )


def test_story_112_2_visible_task_id_source_is_not_hidden_data() -> None:
    parser = parse_scripts()
    assert parser.visible_task_id_text.strip() == VISIBLE_TASK_ID
    assert "data-task-id" not in parser.task_source_attrs
    raw = DASHBOARD.read_text(encoding="utf-8").lower()
    assert "fetch plus readablestream" in raw
    assert "application/x-ndjson" in raw
    assert "other stream transports" in raw


def test_story_112_2_runtime_route_transport_and_method_contract_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {ROUTE_PREFIX}
    assert ROUTE_SUFFIX in source
    assert "getReader" in source
    assert "TextDecoder" in source
    assert "application/x-ndjson" in source
    assert "AbortController" in source
    assert "setTimeout" in source
    assert "setInterval" not in source
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match and method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_112_2_panel_exposes_required_stream_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "digest-stream-task-id-source",
        "digest-stream-status",
        "digest-stream-source",
        "digest-stream-task-id",
        "digest-stream-freshness",
        "digest-stream-authority",
        "digest-stream-provenance",
        "digest-stream-correlation",
        "digest-stream-degraded",
        "digest-stream-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert ROUTE_PATTERN in raw


def test_story_112_2_missing_task_id_does_not_fetch() -> None:
    output = run_stream_runtime_case({"name": "missing", "taskIdText": "", "expected": []})
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == []
    assert "missing task_id" in rendered
    assert "non-authoritative" in rendered


def test_story_112_2_hidden_task_id_decoy_is_ignored() -> None:
    output = run_stream_runtime_case(
        {
            "name": "hidden-decoy",
            "taskIdText": VISIBLE_TASK_ID,
            "hiddenTaskId": "hidden-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(),
            },
            "expected": ["healthy"],
        }
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_ROUTE, "method": "GET", "hasBody": False, "hasSignal": True}
    ]


def test_story_112_2_runtime_behavior_maps_success_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(),
            },
            "expected": [
                "healthy",
                "authoritative",
                f"get {APPROVED_ROUTE}",
                "bounded stream chunk",
                "corr-1",
            ],
        },
        {
            "name": "stale",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(
                    final={
                        "freshness_state": "stale",
                        "display_state": "stale",
                        "authority_state": "non-authoritative",
                    }
                ),
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "provider-unavailable",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(
                    chunk={"chunk": "LLM unavailable — bounded digest stream summary unavailable."},
                    final={
                        "display_state": "provider-unavailable",
                        "authority_state": "non-authoritative",
                    },
                ),
            },
            "expected": ["provider-unavailable", "non-authoritative", "llm unavailable"],
        },
        {
            "name": "invalid-content-type",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/json"},
                "frames": healthy_frames(),
            },
            "expected": ["invalid", "non-authoritative", "content type"],
        },
        {
            "name": "malformed-frame",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "brokenText": "{bad-json}\n",
            },
            "expected": ["invalid", "non-authoritative", "read error"],
        },
        {
            "name": "unexpected-key",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(chunk={"href": "https://example.test"}),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "overbroad-chunk",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(chunk={"chunk": "see https://example.test/raw"}),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "excessive-chunks",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": excessive_frames(),
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "interrupted",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames()[:-1],
            },
            "expected": ["invalid", "non-authoritative", "interrupted"],
        },
        {
            "name": "final-not-last",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": [healthy_frames()[0], healthy_frames()[2], healthy_frames()[1]],
            },
            "expected": ["invalid", "non-authoritative", "final frame position"],
        },
        {
            "name": "non-contiguous-sequence",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(chunk={"sequence": 2}, final={"sequence": 3}),
            },
            "expected": ["invalid", "non-authoritative", "non-contiguous chunk sequence"],
        },
        {
            "name": "chunk-count-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "headers": {"content-type": "application/x-ndjson"},
                "frames": healthy_frames(final={"chunk_count": 2}),
            },
            "expected": ["invalid", "non-authoritative", "mismatched chunk count"],
        },
        {
            "name": "unauthorized",
            "response": {"ok": False, "status": 403, "headers": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "backend",
            "response": {"ok": False, "status": 503, "headers": {}},
            "expected": ["backend unavailable", "non-authoritative"],
        },
        {
            "name": "network",
            "response": {"ok": True, "status": 200, "headers": {}, "reject": "network down"},
            "expected": ["invalid", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_stream_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {"route": APPROVED_ROUTE, "method": "GET", "hasBody": False, "hasSignal": True}
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative digest stream chunks" not in rendered


def test_story_112_2_runtime_rejects_expanded_overbroad_markers() -> None:
    markers = (
        "Payload_JSON",
        "Provider Internal",
        "Provider Internals",
        "provider_internal",
        "HTTPS://example.test/raw",
        "file://local/path",
        "Prompt",
        "Prompts",
        "OpenAI",
        "Anthropic",
        "Retry",
        "Control",
        "control hints",
        "raw logs",
        "raw events",
        "event payloads",
        "hrefs",
        "URLs",
        "source token",
        '"/tmp/operator/work"',
        "'/tmp/operator/work'",
        "`/tmp/operator/work`",
        '"/Users/operator/work"',
        '"C:\\tmp\\x"',
        "(/tmp/operator/work)",
        "/home/operator/work path",
        "~/secret",
        "C:\\tmp\\x",
    )
    for marker in markers:
        output = run_stream_runtime_case(
            {
                "name": f"overbroad-{marker}",
                "response": {
                    "ok": True,
                    "status": 200,
                    "headers": {"content-type": "application/x-ndjson"},
                    "frames": healthy_frames(
                        chunk={"chunk": f"bounded prefix {marker} bounded suffix"}
                    ),
                },
                "expected": ["invalid", "non-authoritative"],
            }
        )
        rendered = " ".join(output["texts"].values()).lower()
        assert "invalid" in rendered, marker
        assert "non-authoritative" in rendered, marker
        assert "authoritative digest stream chunks" not in rendered, marker


def healthy_frames(
    *, chunk: dict[str, object] | None = None, final: dict[str, object] | None = None
) -> list[Frame]:
    base: Frame = {
        "task_id": VISIBLE_TASK_ID,
        "route": ROUTE_PATTERN,
        "retrieved_at": "2026-06-26T00:00:00.000Z",
        "freshness_state": "fresh",
        "provenance": "registry-state digest stream",
        "request_id": "req-1",
        "trace_id": "trace-1",
        "correlation_id": "corr-1",
    }
    return [
        {
            **base,
            "type": "open",
            "sequence": 0,
            "display_state": "partial",
            "authority_state": "non-authoritative",
        },
        {
            **base,
            "type": "chunk",
            "sequence": 1,
            "display_state": "partial",
            "authority_state": "non-authoritative",
            "chunk": "Bounded stream chunk.",
        }
        | cast(Frame, chunk or {}),
        {
            **base,
            "type": "final",
            "sequence": 2,
            "display_state": "healthy",
            "authority_state": "authoritative",
            "chunk_count": 1,
            "line_count": 1,
            "truncated": False,
        }
        | cast(Frame, final or {}),
    ]


def excessive_frames() -> list[Frame]:
    frames = healthy_frames()
    base = frames[1].copy()
    chunks = [{**base, "sequence": index, "chunk": f"chunk {index}"} for index in range(1, 12)]
    return [frames[0], *chunks, frames[-1]]


def run_stream_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        class AbortController {{ constructor() {{ this.signal = {{ aborted: false }}; }} abort() {{ this.signal.aborted = true; }} }}
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: id === 'digest-stream-task-id-source' ? (testCase.taskIdText ?? {json.dumps(VISIBLE_TASK_ID)}) : '',
              dataset: id === 'digest-stream-task-id-source' ? {{ taskId: testCase.hiddenTaskId || '' }} : {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const encoder = new TextEncoder();
        function streamFromText(text) {{
          return {{ getReader() {{
            let done = false;
            return {{ read: async () => {{
              if (done) return {{ done: true }};
              done = true;
              return {{ done: false, value: encoder.encode(text) }};
            }} }};
          }} }};
        }}
        const fetchCalls = [];
        const sandbox = {{
          console,
          AbortController,
          TextDecoder,
          setTimeout: (fn, _ms) => 1,
          clearTimeout: (_id) => undefined,
          document: {{
            readyState: {json.dumps(ready_state)},
            addEventListener: (_name, callback) => callbacks.push(callback),
            getElementById: element,
          }},
          window: {{}},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{ route, method: (options.method || 'GET').toUpperCase(), hasBody: Object.prototype.hasOwnProperty.call(options, 'body'), hasSignal: Object.prototype.hasOwnProperty.call(options, 'signal') }});
            const response = testCase.response;
            if (response && response.reject) throw new Error(response.reject);
            const text = response.brokenText ?? (response.frames || []).map((frame) => JSON.stringify(frame)).join('\\n') + '\\n';
            return {{
              ok: response.ok,
              status: response.status,
              headers: {{ get: (name) => (response.headers || {{}})[name.toLowerCase()] || '' }},
              body: streamFromText(text),
            }};
          }},
        }};
        sandbox.window = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, {{ filename: 'digest-stream.js' }});
        Promise.resolve(callbacks[0] ? callbacks[0]() : undefined)
          .then(() => new Promise((resolve) => setImmediate(resolve)))
          .then(() => {{ process.stdout.write(JSON.stringify({{ texts, fetchCalls }})); }})
          .catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )
    completed = subprocess.run(
        ["node", "-e", node_code], check=True, text=True, capture_output=True
    )
    loaded = json.loads(completed.stdout)
    assert isinstance(loaded, dict)
    return cast(RuntimeOutput, loaded)
