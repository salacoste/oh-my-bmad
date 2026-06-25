from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
TASK_RUNTIME = Path("dashboard/static/task-detail.js")
APPROVED_SCRIPT = "task-detail.js"
APPROVED_HEALTH_SCRIPT = "health-readiness.js"
APPROVED_EVENT_SCRIPT = "event-timeline.js"
APPROVED_TRACE_SCRIPT = "trace-correlation.js"
APPROVED_HISTORY_REPLAY_SCRIPT = "history-replay.js"
APPROVED_LIFECYCLE_SCRIPT = "lifecycle-snapshot.js"
APPROVED_ROUTE_PREFIX = "/v1/tasks/"
VISIBLE_TASK_ID = "fixture-task-id"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/tasks/{task_id}/events",
    "/v1/tasks/{task_id}/transitions",
    "/v1/tasks/{task_id}/history",
    "/v1/tasks/{task_id}/logs/digest",
    "/v1/tasks?",
    "/v1/sessions",
    "/v1/trace",
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
    "data-task-id",
    "location.search",
    "location.hash",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class TaskBody(TypedDict, total=False):
    task_id: str
    status: str
    title: str
    updated_at: str
    retrieved_at: str
    freshness_state: str
    display_state: str
    authority_state: str


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: TaskBody
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
        self._in_task_source = False
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
        if attrs_dict.get("id") == "task-detail-task-id-source":
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
    return TASK_RUNTIME.read_text(encoding="utf-8")


def test_story_102_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()

    assert parser.scripts == [
        {"src": APPROVED_HEALTH_SCRIPT, "defer": ""},
        {"src": APPROVED_SCRIPT, "defer": ""},
        {"src": APPROVED_EVENT_SCRIPT, "defer": ""},
        {"src": APPROVED_TRACE_SCRIPT, "defer": ""},
        {"src": APPROVED_HISTORY_REPLAY_SCRIPT, "defer": ""},
        {"src": APPROVED_LIFECYCLE_SCRIPT, "defer": ""},
    ]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert TASK_RUNTIME.exists()


def test_story_102_2_visible_task_id_source_is_not_hidden_data() -> None:
    parser = parse_scripts()
    assert parser.visible_task_id_text.strip() == VISIBLE_TASK_ID
    assert "data-task-id" not in parser.task_source_attrs
    assert "fixture-task-id" in DASHBOARD.read_text(encoding="utf-8")


def test_story_102_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(
        [
            APPROVED_HEALTH_SCRIPT,
            APPROVED_SCRIPT,
            APPROVED_EVENT_SCRIPT,
            APPROVED_TRACE_SCRIPT,
            APPROVED_HISTORY_REPLAY_SCRIPT,
            APPROVED_LIFECYCLE_SCRIPT,
        ]
    )

    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_102_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {APPROVED_ROUTE_PREFIX}

    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_102_2_task_detail_panel_exposes_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "task-detail-task-id-source",
        "task-detail-status",
        "task-detail-source",
        "task-detail-task-id",
        "task-detail-freshness",
        "task-detail-authority",
        "task-detail-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert "GET /v1/tasks/{task_id}" in raw
    assert "visible task_id source" in raw.lower()


def test_story_102_2_missing_task_id_does_not_fetch() -> None:
    output = run_task_runtime_case({"name": "missing", "taskIdText": "", "expected": []})
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == []
    assert "missing task_id" in rendered
    assert "non-authoritative" in rendered


def test_story_102_2_hidden_task_id_decoy_is_ignored() -> None:
    output = run_task_runtime_case(
        {
            "name": "hidden-decoy",
            "taskIdText": VISIBLE_TASK_ID,
            "hiddenTaskId": "hidden-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "status": "running",
                    "title": "Fixture task",
                    "retrieved_at": "2026-06-21T00:00:00.000Z",
                    "freshness_state": "fresh",
                },
            },
            "expected": ["healthy"],
        }
    )
    assert output["fetchCalls"] == [
        {"route": f"{APPROVED_ROUTE_PREFIX}{VISIBLE_TASK_ID}", "method": "GET", "hasBody": False}
    ]


def test_story_102_2_runtime_behavior_maps_success_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "status": "running",
                    "title": "Fixture task",
                    "retrieved_at": "2026-06-21T00:00:00.000Z",
                    "freshness_state": "fresh",
                },
            },
            "expected": ["healthy", "authoritative", f"get /v1/tasks/{VISIBLE_TASK_ID}"],
        },
        {
            "name": "stale",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "status": "running",
                    "title": "Fixture task",
                    "display_state": "stale",
                    "freshness_state": "stale",
                    "retrieved_at": "2026-06-20T00:00:00.000Z",
                },
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "backend-unavailable",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "display_state": "backend-unavailable"},
            },
            "expected": ["backend unavailable", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "response": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-empty-object",
            "response": {"ok": True, "status": 200, "body": {}},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "missing-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"status": "running", "title": "Fixture task"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "mismatched-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"task_id": "other-task-id", "status": "running", "title": "Fixture task"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "healthy-display-state-missing-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {"display_state": "healthy", "status": "running", "title": "Fixture task"},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "healthy-display-state-mismatched-task-id",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "display_state": "healthy",
                    "task_id": "other-task-id",
                    "status": "running",
                    "title": "Fixture task",
                },
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "healthy-display-state-blank-status",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "display_state": "healthy",
                    "task_id": VISIBLE_TASK_ID,
                    "status": "",
                    "title": "Fixture task",
                },
            },
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
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_task_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {
                "route": f"{APPROVED_ROUTE_PREFIX}{VISIBLE_TASK_ID}",
                "method": "GET",
                "hasBody": False,
            }
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_102_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_task_runtime_case(
        {
            "name": "already-loaded",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "status": "running",
                    "title": "Fixture task",
                    "retrieved_at": "2026-06-21T00:00:00.000Z",
                },
            },
            "expected": ["healthy"],
        },
        ready_state="interactive",
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == [
        {"route": f"{APPROVED_ROUTE_PREFIX}{VISIBLE_TASK_ID}", "method": "GET", "hasBody": False}
    ]
    assert "healthy" in rendered
    assert "authoritative" in rendered


def run_task_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(TASK_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: id === 'task-detail-task-id-source' ? (testCase.taskIdText ?? {json.dumps(VISIBLE_TASK_ID)}) : '',
              dataset: id === 'task-detail-task-id-source' ? {{ taskId: testCase.hiddenTaskId || '' }} : {{}},
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
            constructor(...args) {{ super(...(args.length ? args : ['2026-06-21T00:00:00.000Z'])); }}
            static now() {{ return new Date('2026-06-21T00:00:00.000Z').getTime(); }}
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
        vm.runInContext(source, sandbox, {{ filename: 'task-detail.js' }});
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
