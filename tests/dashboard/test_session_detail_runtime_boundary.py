from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path
from typing import NotRequired, TypedDict

DASHBOARD = Path("dashboard/static/index.html")
RUNTIME = Path("dashboard/static/session-detail.js")
APPROVED_ROUTE_PREFIX = "/v1/sessions/"
ROUTE_PATTERN = "GET /v1/sessions/{session_id}"
FORBIDDEN_RUNTIME_MARKERS = (
    "import ",
    "import(",
    "new Worker",
    "SharedWorker",
    "serviceWorker.register",
    "setInterval",
    "setTimeout",
    "AbortSignal.timeout",
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
    "data-session-id",
    "data-task-id",
    "href",
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
    "innerHTML",
)
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/sessions?",
    "/v1/tasks/",
    "/v1/tasks?",
    "/v1/tasks/search",
    "/logs/digest",
    "/v1/trace/",
    "/v1/events/replay",
    "stream",
    "search",
    "discover",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: dict[str, object]
    jsonError: str
    contentType: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    response: NotRequired[RuntimeResponse]
    sourceText: NotRequired[str]
    reject: NotRequired[str]


class FetchCall(TypedDict):
    route: str
    method: str
    hasBody: bool
    credentials: str | None
    accept: str | None
    hasSignal: bool


class RuntimeOutput(TypedDict):
    texts: dict[str, str]
    fetchCalls: list[FetchCall]


def runtime_source() -> str:
    return RUNTIME.read_text(encoding="utf-8")


def test_story_111_2_session_detail_runtime_module_exists_and_is_closed() -> None:
    assert RUNTIME.exists()
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_111_2_session_detail_route_selector_and_method_are_exact() -> None:
    source = runtime_source()
    assert ROUTE_PATTERN in source
    assert APPROVED_ROUTE_PREFIX in source
    assert "encodeURIComponent" in source
    assert "session-detail-session-id-source" in source
    assert "session-list-rows" not in source
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    options = fetches[0].group("options")
    method_match = METHOD_RE.search(options)
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in options.lower()
    assert "headers" in options.lower()
    assert "accept" in options.lower()
    assert "application/json" in options.lower()
    assert "credentials" not in options.lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_111_2_session_detail_panel_exposes_visible_source_and_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "session-detail-session-id-source",
        "session-detail-status",
        "session-detail-source",
        "session-detail-freshness",
        "session-detail-authority",
        "session-detail-provenance",
        "session-detail-correlation",
        "session-detail-degraded",
        "session-detail-row",
    ):
        assert f'id="{element_id}"' in raw
    lowered = raw.lower()
    assert ROUTE_PATTERN.lower() in lowered
    assert "visible session_id source" in lowered
    assert "no query selectors" in lowered
    assert "no request body" in lowered
    assert "session-list rows remain inert" in lowered
    assert "raw worktree_path" in lowered


def test_story_111_2_session_detail_runtime_behavior_maps_success_and_failures() -> None:
    row = {
        "session_id": "s-visible",
        "task_id": "t-1",
        "worker_kind": "worker",
        "status": "active",
        "started_at": "2026-06-26T00:00:00Z",
        "ended_at": None,
        "last_heartbeat_at": "2026-06-26T00:00:10Z",
        "heartbeat_state": "observed",
    }
    base_body: dict[str, object] = {
        "route": ROUTE_PATTERN,
        "selected_session_id": "s-visible",
        "retrieved_at": "2026-06-26T00:00:20Z",
        "freshness_state": "fresh",
        "display_state": "healthy",
        "authority_state": "authoritative",
        "provenance": "registry-state session detail",
        "request_id": "req-1",
        "trace_id": "trace-root",
        "correlation_id": "req-1",
        "item": row,
    }
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": base_body},
            "expected": [
                "healthy",
                "authoritative",
                "get /v1/sessions/{session_id}",
                "s-visible",
                "t-1",
                "observed",
            ],
        },
        {
            "name": "missing-source",
            "sourceText": "   ",
            "expected": ["unavailable", "non-authoritative", "missing visible session_id"],
        },
        {
            "name": "not-found",
            "response": {"ok": False, "status": 404, "body": {"detail": "not found"}},
            "expected": ["not-found", "non-authoritative"],
        },
        {
            "name": "unknown-field",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "item": {**row, "worktree_path": "/private/leak"}},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "path-leak",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "item": {**row, "status": "/private/leak"}},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "mismatched-selected-session",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "selected_session_id": "s-other"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "network-error",
            "reject": "network down",
            "expected": ["backend-unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_runtime_case(case)
        combined = "\n".join(output["texts"].values()).lower()
        for expected in case["expected"]:
            assert expected.lower() in combined, (case["name"], expected, combined)
        if case["name"] == "healthy":
            assert output["fetchCalls"] == [
                {
                    "route": "/v1/sessions/s-visible",
                    "method": "GET",
                    "hasBody": False,
                    "credentials": None,
                    "accept": "application/json",
                    "hasSignal": False,
                }
            ]


def run_runtime_case(case: RuntimeCase) -> RuntimeOutput:
    response = case.get("response")
    source_text = case.get("sourceText", "s-visible")
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(RUNTIME))}, 'utf8');
        const ids = [
          'session-detail-session-id-source','session-detail-status','session-detail-source',
          'session-detail-freshness','session-detail-authority','session-detail-provenance',
          'session-detail-correlation','session-detail-degraded','session-detail-row'
        ];
        const texts = {{}};
        const elements = {{}};
        for (const id of ids) {{
          elements[id] = {{
            get textContent() {{ return texts[id] || ''; }},
            set textContent(value) {{ texts[id] = String(value); }}
          }};
        }}
        texts['session-detail-session-id-source'] = {json.dumps(source_text)};
        const fetchCalls = [];
        const response = {json.dumps(response)};
        const reject = {json.dumps(case.get("reject"))};
        const sandbox = {{
          document: {{
            readyState: 'complete',
            getElementById: (id) => elements[id] || null,
            addEventListener: () => {{}}
          }},
          window: {{}},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{
              route,
              method: options.method || 'GET',
              hasBody: Object.prototype.hasOwnProperty.call(options, 'body'),
              credentials: options.credentials || null,
              accept: options.headers ? options.headers.Accept || options.headers.accept || null : null,
              hasSignal: Object.prototype.hasOwnProperty.call(options, 'signal')
            }});
            if (reject) throw new Error(reject);
            return {{
              ok: response ? response.ok !== false : true,
              status: response && response.status ? response.status : 200,
              headers: {{ get: (name) => (response && response.contentType) || 'application/json' }},
              json: async () => {{
                if (response && response.jsonError) throw new Error(response.jsonError);
                return response ? response.body : {{}};
              }}
            }};
          }}
        }};
        vm.createContext(sandbox);
        (async () => {{
          vm.runInContext(source, sandbox, {{ filename: 'session-detail.js' }});
          if (sandbox.window.__sessionDetailReady) {{ await sandbox.window.__sessionDetailReady; }}
          await Promise.resolve();
          await new Promise((resolve) => setImmediate(resolve));
          process.stdout.write(JSON.stringify({{texts, fetchCalls}}));
        }})().catch((error) => {{
          console.error(error);
          process.exit(1);
        }});
        """
    )
    result = subprocess.run(["node", "-e", node_code], check=True, capture_output=True, text=True)
    loaded = json.loads(result.stdout)
    return RuntimeOutput(texts=loaded["texts"], fetchCalls=loaded["fetchCalls"])
