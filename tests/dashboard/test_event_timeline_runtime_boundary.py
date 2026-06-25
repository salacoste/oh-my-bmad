from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
EVENT_RUNTIME = Path("dashboard/static/event-timeline.js")
APPROVED_HEALTH_SCRIPT = "health-readiness.js"
APPROVED_TASK_DETAIL_SCRIPT = "task-detail.js"
APPROVED_EVENT_SCRIPT = "event-timeline.js"
APPROVED_TRACE_SCRIPT = "trace-correlation.js"
APPROVED_HISTORY_REPLAY_SCRIPT = "history-replay.js"
APPROVED_LIFECYCLE_SCRIPT = "lifecycle-snapshot.js"
APPROVED_SCRIPTS = [
    APPROVED_HEALTH_SCRIPT,
    APPROVED_TASK_DETAIL_SCRIPT,
    APPROVED_EVENT_SCRIPT,
    APPROVED_TRACE_SCRIPT,
    APPROVED_HISTORY_REPLAY_SCRIPT,
    APPROVED_LIFECYCLE_SCRIPT,
]
VISIBLE_TASK_ID = "fixture-task-id"
EVENTS_ROUTE_PREFIX = "/v1/tasks/"
EVENTS_ROUTE_SUFFIX = "/events"
TRANSITIONS_ROUTE_SUFFIX = "/transitions"
APPROVED_EVENTS_ROUTE = f"{EVENTS_ROUTE_PREFIX}{VISIBLE_TASK_ID}{EVENTS_ROUTE_SUFFIX}"
APPROVED_TRANSITIONS_ROUTE = f"{EVENTS_ROUTE_PREFIX}{VISIBLE_TASK_ID}{TRANSITIONS_ROUTE_SUFFIX}"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/trace",
    "/v1/tasks/{task_id}/history",
    "/v1/tasks/{task_id}/logs/digest",
    "/v1/tasks?",
    "/v1/tasks/",  # exact literals are checked separately; runtime must construct from task_id only
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
    "data-event-id",
    "data-task-id",
    "location.search",
    "location.hash",
    "trace_id)",
    "traceId",
    "history",
    "replay",
    "lifecycle",
    "session_id)",
    "aggregate",
    "digest",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class EventRow(TypedDict, total=False):
    task_id: str
    event_id: str
    event_type: str
    transition: str
    emitted_at: str
    summary: str
    trace_id: str
    session_id: str


class EventBody(TypedDict, total=False):
    task_id: str
    display_state: str
    retrieved_at: str
    freshness_state: str
    events: list[EventRow]
    transitions: list[EventRow]


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: EventBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    eventsResponse: NotRequired[RuntimeResponse]
    transitionsResponse: NotRequired[RuntimeResponse]
    eventsReject: NotRequired[str]
    transitionsReject: NotRequired[str]
    taskIdText: NotRequired[str]
    hiddenTaskId: NotRequired[str]
    hiddenEventId: NotRequired[str]


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
        self.event_source_attrs: dict[str, str] = {}
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
        if attrs_dict.get("id") == "event-timeline-task-id-source":
            self._in_task_source = True
            self.task_source_attrs = attrs_dict
        if attrs_dict.get("id") == "event-timeline-event-id-source":
            self.event_source_attrs = attrs_dict

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
    return EVENT_RUNTIME.read_text(encoding="utf-8")


def test_story_103_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()

    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert EVENT_RUNTIME.exists()


def test_story_103_2_visible_task_id_source_is_only_selector() -> None:
    parser = parse_scripts()
    assert parser.visible_task_id_text.strip() == VISIBLE_TASK_ID
    assert "data-task-id" not in parser.task_source_attrs
    assert "data-event-id" not in parser.event_source_attrs
    raw = DASHBOARD.read_text(encoding="utf-8")
    assert "event_id metadata only" in raw.lower()
    assert "selector-drift" in raw.lower()


def test_story_103_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)

    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_103_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {EVENTS_ROUTE_PREFIX}
    assert f'"{EVENTS_ROUTE_SUFFIX}"' in source
    assert f'"{TRANSITIONS_ROUTE_SUFFIX}"' in source

    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    method_match = METHOD_RE.search(fetches[0].group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert "body" not in fetches[0].group("options").lower()
    assert "readRoute(eventsRoute" in source
    assert "readRoute(transitionsRoute" in source
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        if marker != "/v1/tasks/":
            assert marker not in source, marker


def test_story_103_2_event_panel_exposes_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "event-timeline-task-id-source",
        "event-timeline-status",
        "event-timeline-source",
        "event-timeline-task-id",
        "event-timeline-freshness",
        "event-timeline-authority",
        "event-timeline-detail",
        "event-timeline-event-count",
        "event-timeline-transition-count",
    ):
        assert f'id="{element_id}"' in raw
    assert "GET /v1/tasks/{task_id}/events" in raw
    assert "GET /v1/tasks/{task_id}/transitions" in raw
    assert "event_id metadata only" in raw.lower()


def test_story_103_2_missing_task_id_does_not_fetch() -> None:
    output = run_event_runtime_case({"name": "missing", "taskIdText": "", "expected": []})
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == []
    assert "missing task_id" in rendered
    assert "non-authoritative" in rendered


def test_story_103_2_hidden_task_and_event_id_decoys_are_ignored() -> None:
    output = run_event_runtime_case(
        {
            "name": "hidden-decoys",
            "taskIdText": VISIBLE_TASK_ID,
            "hiddenTaskId": "hidden-task-id",
            "hiddenEventId": "hidden-event-id",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "retrieved_at": "2026-06-22T00:00:00.000Z",
                    "events": [
                        {
                            "task_id": VISIBLE_TASK_ID,
                            "event_id": "evt-1",
                            "event_type": "task.started",
                            "emitted_at": "2026-06-22T00:00:00.000Z",
                            "summary": "started",
                            "trace_id": "trace-display-only",
                        }
                    ],
                },
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "transitions": [
                        {
                            "task_id": VISIBLE_TASK_ID,
                            "event_id": "evt-2",
                            "transition": "queued -> running",
                            "emitted_at": "2026-06-22T00:01:00.000Z",
                        }
                    ],
                },
            },
            "expected": ["healthy", "authoritative", "event_id metadata only", "2 rows"],
        }
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_EVENTS_ROUTE, "method": "GET", "hasBody": False},
        {"route": APPROVED_TRANSITIONS_ROUTE, "method": "GET", "hasBody": False},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "hidden-task-id" not in rendered
    assert "hidden-event-id" not in rendered
    assert "trace-display-only" not in rendered


def test_story_103_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "retrieved_at": "2026-06-22T00:00:00.000Z",
                    "events": [
                        {
                            "task_id": VISIBLE_TASK_ID,
                            "event_id": "evt-1",
                            "event_type": "task.started",
                        }
                    ],
                },
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "task_id": VISIBLE_TASK_ID,
                    "transitions": [
                        {
                            "task_id": VISIBLE_TASK_ID,
                            "event_id": "evt-2",
                            "transition": "queued -> running",
                        }
                    ],
                },
            },
            "expected": [
                "healthy",
                "authoritative",
                "2 rows",
                "get /v1/tasks/fixture-task-id/events",
            ],
        },
        {
            "name": "empty",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "events": []},
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["empty", "non-authoritative", "0 rows"],
        },
        {
            "name": "stale",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "display_state": "stale", "events": []},
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "eventsResponse": {"ok": True, "status": 200, "jsonError": "bad json"},
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unexpected-shape",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {"events": [{"event_id": "evt-1"}]},
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "mismatched-task-id",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": "other", "events": []},
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "eventsResponse": {"ok": False, "status": 403, "body": {}},
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "network",
            "eventsReject": "network down",
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_event_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [
            {"route": APPROVED_EVENTS_ROUTE, "method": "GET", "hasBody": False},
            {"route": APPROVED_TRANSITIONS_ROUTE, "method": "GET", "hasBody": False},
        ]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_103_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_event_runtime_case(
        {
            "name": "already-loaded",
            "eventsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "events": []},
            },
            "transitionsResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "transitions": []},
            },
            "expected": ["empty"],
        },
        ready_state="interactive",
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_EVENTS_ROUTE, "method": "GET", "hasBody": False},
        {"route": APPROVED_TRANSITIONS_ROUTE, "method": "GET", "hasBody": False},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "empty" in rendered
    assert "non-authoritative" in rendered


def run_event_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(EVENT_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: id === 'event-timeline-task-id-source' ? (testCase.taskIdText ?? {json.dumps(VISIBLE_TASK_ID)}) : '',
              dataset: id === 'event-timeline-task-id-source' ? {{ taskId: testCase.hiddenTaskId || '', eventId: testCase.hiddenEventId || '' }} : {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        async function responseFor(route) {{
          if (route.endsWith('/events')) {{
            if (testCase.eventsReject) throw new Error(testCase.eventsReject);
            return testCase.eventsResponse;
          }}
          if (route.endsWith('/transitions')) {{
            if (testCase.transitionsReject) throw new Error(testCase.transitionsReject);
            return testCase.transitionsResponse;
          }}
          throw new Error('unexpected route ' + route);
        }}
        const sandbox = {{
          console,
          Date: class extends Date {{
            constructor(...args) {{ super(...(args.length ? args : ['2026-06-22T00:00:00.000Z'])); }}
            static now() {{ return new Date('2026-06-22T00:00:00.000Z').getTime(); }}
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
        vm.runInContext(source, sandbox, {{ filename: 'event-timeline.js' }});
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
