from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path
from typing import NotRequired, TypedDict

DASHBOARD = Path("dashboard/static/index.html")
RUNTIME = Path("dashboard/static/session-list.js")
APPROVED_ROUTE = "/v1/sessions"
ROUTE_PATTERN = "GET /v1/sessions"
FORBIDDEN_RUNTIME_MARKERS = (
    "import ",
    "import(",
    "new Worker",
    "SharedWorker",
    "serviceWorker.register",
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
    "/v1/sessions/",
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
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*ROUTE(?P<options>[^)]*)\)", re.DOTALL)
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


def test_story_110_2_session_runtime_module_exists_and_is_closed() -> None:
    assert RUNTIME.exists()
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_110_2_session_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {APPROVED_ROUTE}
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert "headers" in fetches[0].group("options").lower()
    assert "accept" in fetches[0].group("options").lower()
    assert "application/json" in fetches[0].group("options").lower()
    assert "credentials" not in fetches[0].group("options").lower()
    assert "include" not in fetches[0].group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_110_2_session_panel_exposes_bounded_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "session-list-status",
        "session-list-source",
        "session-list-freshness",
        "session-list-authority",
        "session-list-provenance",
        "session-list-correlation",
        "session-list-pagination",
        "session-list-degraded",
        "session-list-count",
        "session-list-rows",
    ):
        assert f'id="{element_id}"' in raw
    lowered = raw.lower()
    assert ROUTE_PATTERN.lower() in lowered
    assert "no query selectors" in lowered
    assert "no request body" in lowered
    assert "inert display text" in lowered
    assert (
        "session detail is available only through the separate visible-source section below"
        in lowered
    )


def test_story_110_2_session_runtime_behavior_maps_success_empty_and_failures() -> None:
    row = {
        "session_id": "s-1",
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
        "retrieved_at": "2026-06-26T00:00:20Z",
        "freshness_state": "fresh",
        "display_state": "healthy",
        "authority_state": "authoritative",
        "provenance": "registry-state session summary list",
        "request_id": "req-1",
        "trace_id": "trace-root",
        "correlation_id": "req-1",
        "limit": 50,
        "returned_count": 1,
        "has_more": False,
        "next_offset": None,
        "sort": "last_heartbeat_at_desc_nulls_last_started_at_desc_id_asc",
        "items": [row],
    }
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {"ok": True, "status": 200, "body": base_body},
            "expected": ["healthy", "authoritative", "get /v1/sessions", "s-1", "t-1", "observed"],
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
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unknown-field",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "items": [{**row, "worktree_path": "/private/leak"}]},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "path-leak",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "items": [{**row, "status": "/private/leak"}]},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "invalid-timestamp",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "retrieved_at": "not-a-timestamp"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "semantic-invalid-timestamp",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    **base_body,
                    "retrieved_at": "2026-99-99T99:99:99Z",
                    "items": [{**row, "started_at": "2026-99-99T99:99:99Z"}],
                },
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "malformed-metadata",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "trace_id": "/private/leak"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "correlation-mismatch",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "correlation_id": "corr-1"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "healthy-empty-incoherent",
            "response": {
                "ok": True,
                "status": 200,
                "body": {**base_body, "returned_count": 0, "items": []},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "empty-with-items-incoherent",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    **base_body,
                    "display_state": "empty-list",
                    "authority_state": "non-authoritative",
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
            "name": "non-null-offset",
            "response": {"ok": True, "status": 200, "body": {**base_body, "next_offset": 50}},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "response": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "wrong-content-type",
            "response": {"ok": True, "status": 200, "contentType": "text/plain", "body": base_body},
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
            {
                "route": APPROVED_ROUTE,
                "method": "GET",
                "hasBody": False,
                "credentials": None,
                "accept": "application/json",
                "hasSignal": True,
            }
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        for forbidden in ("worktree", "/private", "href", "data-session-id", "data-task-id"):
            assert forbidden not in rendered, (case["name"], forbidden, rendered)


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
          AbortSignal: {{
            timeout: (_milliseconds) => "abort-signal"
          }},
          window: {{}},
          document: {{
            readyState: {json.dumps(ready_state)},
            getElementById: element,
            addEventListener: (_name, callback) => callbacks.push(callback)
          }},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{
              route,
              method: options.method || 'GET',
              hasBody: Object.prototype.hasOwnProperty.call(options, 'body'),
              credentials: options.credentials || null,
              accept: options.headers && options.headers.Accept ? options.headers.Accept : null,
              hasSignal: Object.prototype.hasOwnProperty.call(options, 'signal')
            }});
            if (testCase.reject) throw new Error(testCase.reject);
            const response = testCase.response || {{ ok: true, status: 200, body: {{}} }};
            return {{
              ok: response.ok,
              status: response.status,
              headers: {{
                get: (name) => name.toLowerCase() === 'content-type'
                  ? (response.contentType || 'application/json')
                  : null
              }},
              json: async () => {{
                if (response.jsonError) throw new Error(response.jsonError);
                return response.body;
              }}
            }};
          }}
        }};
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, {{ filename: 'session-list.js' }});
        for (const callback of callbacks) {{ await callback(); }}
        if (sandbox.window.__sessionListReady) {{ await sandbox.window.__sessionListReady; }}
        await Promise.resolve();
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


def test_story_128_5_session_list_cleanup_helpers_remain_module_local() -> None:
    source = runtime_source()
    assert "function readFailureState(" in source
    assert "dashboard-shared" not in source
