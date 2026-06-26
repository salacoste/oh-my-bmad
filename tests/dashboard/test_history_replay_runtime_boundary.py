from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
HISTORY_REPLAY_RUNTIME = Path("dashboard/static/history-replay.js")
APPROVED_HEALTH_SCRIPT = "health-readiness.js"
APPROVED_TASK_DETAIL_SCRIPT = "task-detail.js"
APPROVED_EVENT_SCRIPT = "event-timeline.js"
APPROVED_AGGREGATE_SCRIPT = "aggregate-task-list.js"
APPROVED_TRACE_SCRIPT = "trace-correlation.js"
APPROVED_HISTORY_REPLAY_SCRIPT = "history-replay.js"
APPROVED_LIFECYCLE_SCRIPT = "lifecycle-snapshot.js"
APPROVED_DIGEST_SCRIPT = "task-log-digest.js"
APPROVED_SCRIPTS = [
    APPROVED_HEALTH_SCRIPT,
    APPROVED_TASK_DETAIL_SCRIPT,
    APPROVED_AGGREGATE_SCRIPT,
    APPROVED_EVENT_SCRIPT,
    APPROVED_TRACE_SCRIPT,
    APPROVED_HISTORY_REPLAY_SCRIPT,
    APPROVED_LIFECYCLE_SCRIPT,
    APPROVED_DIGEST_SCRIPT,
]
VISIBLE_TASK_ID = "fixture-task-id"
VISIBLE_TARGET_KIND = "to_sequence"
VISIBLE_TARGET_VALUE = "42"
HISTORY_ROUTE_PREFIX = "/v1/tasks/"
HISTORY_ROUTE_SUFFIX = "/history"
REPLAY_ROUTE = "/v1/events/replay"
VALIDATE_ROUTE = "/v1/events/replay/validate"
APPROVED_HISTORY_ROUTE = f"{HISTORY_ROUTE_PREFIX}{VISIBLE_TASK_ID}{HISTORY_ROUTE_SUFFIX}"
APPROVED_REPLAY_ROUTE = f"{REPLAY_ROUTE}?{VISIBLE_TARGET_KIND}={VISIBLE_TARGET_VALUE}"
HISTORY_PATTERN = "GET /v1/tasks/{task_id}/history"
REPLAY_PATTERN = "GET /v1/events/replay"
VALIDATE_PATTERN = "GET /v1/events/replay/validate"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/events/replay/snapshots",
    "/v1/lifecycle",
    "/v1/tasks?",
    "/v1/tasks/search",
    "/v1/sessions",
    "/v1/tasks/{task_id}/logs/digest",
    "/v1/logs/digest",
    "/v1/trace/",
    "/v1/traces",
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
    "data-replay",
    "location.search",
    "location.hash",
    "URLSearchParams(location",
    "Date.now",
    "new Date",
    "toISOString",
    "snapshot",
    "lifecycle",
    "aggregate",
    "digest",
    "search",
    "discover",
    "poll",
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
FETCH_CALL_RE = re.compile(r"fetch\(\s*route(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class HistoryRow(TypedDict, total=False):
    task_id: str
    event_id: str
    trace_id: str
    emitted_at: str
    payload: dict[str, object]


class HistoryBody(TypedDict, total=False):
    task_id: str
    display_state: str
    retrieved_at: str
    freshness_state: str
    events: list[HistoryRow]


class ReplayBody(TypedDict, total=False):
    display_state: str
    retrieved_at: str
    freshness_state: str
    replayed_event_count: int
    replay_id: str
    event_id: str
    trace_id: str
    state: dict[str, object]
    tasks: list[dict[str, object]]
    sessions: list[dict[str, object]]


class ValidateBody(TypedDict, total=False):
    display_state: str
    retrieved_at: str
    freshness_state: str
    validation_status: str
    replay_id: str
    field_diffs: dict[str, object]


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: HistoryBody | ReplayBody | ValidateBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    historyResponse: NotRequired[RuntimeResponse]
    replayResponse: NotRequired[RuntimeResponse]
    validateResponse: NotRequired[RuntimeResponse]
    historyReject: NotRequired[str]
    replayReject: NotRequired[str]
    validateReject: NotRequired[str]
    taskIdText: NotRequired[str]
    targetKindText: NotRequired[str]
    targetValueText: NotRequired[str]
    hiddenTaskId: NotRequired[str]
    hiddenReplayTarget: NotRequired[str]


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
        self.visible_target_kind_text = ""
        self.visible_target_value_text = ""
        self.task_source_attrs: dict[str, str] = {}
        self.target_kind_attrs: dict[str, str] = {}
        self.target_value_attrs: dict[str, str] = {}
        self.controls: list[str] = []
        self._capture: str | None = None

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
        element_id = attrs_dict.get("id")
        if element_id == "history-replay-task-id-source":
            self._capture = "task"
            self.task_source_attrs = attrs_dict
        if element_id == "history-replay-target-kind-source":
            self._capture = "kind"
            self.target_kind_attrs = attrs_dict
        if element_id == "history-replay-target-value-source":
            self._capture = "value"
            self.target_value_attrs = attrs_dict

    def handle_endtag(self, _tag: str) -> None:
        if self.inline_script_depth:
            self.inline_script_depth -= 1
        self._capture = None

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)
        if self._capture == "task":
            self.visible_task_id_text += data
        elif self._capture == "kind":
            self.visible_target_kind_text += data
        elif self._capture == "value":
            self.visible_target_value_text += data


def parse_scripts() -> ScriptParser:
    parser = ScriptParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_source() -> str:
    return HISTORY_REPLAY_RUNTIME.read_text(encoding="utf-8")


def test_story_105_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert HISTORY_REPLAY_RUNTIME.exists()


def test_story_105_2_visible_sources_are_only_selectors_and_targets() -> None:
    parser = parse_scripts()
    assert parser.visible_task_id_text.strip() == VISIBLE_TASK_ID
    assert parser.visible_target_kind_text.strip() == VISIBLE_TARGET_KIND
    assert parser.visible_target_value_text.strip() == VISIBLE_TARGET_VALUE
    for attrs in (parser.task_source_attrs, parser.target_kind_attrs, parser.target_value_attrs):
        serialized = " ".join(f"{key}={value}" for key, value in attrs.items()).lower()
        assert "data-task-id" not in serialized
        assert "data-replay" not in serialized
    raw = DASHBOARD.read_text(encoding="utf-8").lower()
    assert "exactly one visible replay target" in raw
    assert "to_sequence" in raw
    assert "to_timestamp" in raw
    assert "no snapshots" in raw


def test_story_105_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_105_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    assert HISTORY_ROUTE_PREFIX in source
    assert HISTORY_ROUTE_SUFFIX in source
    assert REPLAY_ROUTE in source
    assert VALIDATE_ROUTE in source
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    for fetch in fetches:
        method_match = METHOD_RE.search(fetch.group("options"))
        assert method_match is None or method_match.group("method").upper() == "GET"
        assert "body" not in fetch.group("options").lower()
    assert 'read(route, task, "history")' in source
    assert 'read(replay, task, "replay")' in source
    assert 'read(VALIDATE_ROUTE, task, "validation")' in source
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_105_2_panel_exposes_bounded_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "history-replay-task-id-source",
        "history-replay-target-kind-source",
        "history-replay-target-value-source",
        "history-replay-status",
        "history-replay-source",
        "history-replay-task-id",
        "history-replay-target",
        "history-replay-freshness",
        "history-replay-authority",
        "history-replay-history-count",
        "history-replay-replay-count",
        "history-replay-validation-status",
        "history-replay-linked-identifiers",
        "history-replay-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert HISTORY_PATTERN in raw
    assert REPLAY_PATTERN in raw
    assert VALIDATE_PATTERN in raw
    assert "event_id, trace_id, replay_id, task_id, and session_id metadata only" in raw.lower()


def test_story_105_2_missing_or_invalid_visible_target_does_not_fetch() -> None:
    cases: list[RuntimeCase] = [
        {"name": "missing-task", "taskIdText": "", "expected": ["missing task_id"]},
        {"name": "missing-kind", "targetKindText": "", "expected": ["missing replay target"]},
        {"name": "missing-value", "targetValueText": "", "expected": ["missing replay target"]},
        {
            "name": "unsupported-kind",
            "targetKindText": "replay_id",
            "expected": ["invalid replay target"],
        },
    ]
    for case in cases:
        output = run_history_replay_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == []
        assert "non-authoritative" in rendered
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)


def test_story_105_2_visible_replay_target_xor_builds_exact_routes() -> None:
    cases: list[tuple[str, str, str]] = [
        ("to_sequence", "42", "/v1/events/replay?to_sequence=42"),
        (
            "to_timestamp",
            "2026-06-24T00:00:00Z",
            "/v1/events/replay?to_timestamp=2026-06-24T00%3A00%3A00Z",
        ),
    ]
    for kind, value, expected_replay_route in cases:
        output = run_history_replay_runtime_case(
            healthy_case({"targetKindText": kind, "targetValueText": value})
        )
        assert output["fetchCalls"] == [
            {"route": APPROVED_HISTORY_ROUTE, "method": "GET", "hasBody": False},
            {"route": expected_replay_route, "method": "GET", "hasBody": False},
            {"route": VALIDATE_ROUTE, "method": "GET", "hasBody": False},
        ]
        rendered = " ".join(output["texts"].values()).lower()
        assert f"{kind}={value}".lower() in rendered


def test_story_105_2_hidden_selector_and_target_decoys_are_ignored() -> None:
    output = run_history_replay_runtime_case(
        healthy_case(
            {
                "hiddenTaskId": "hidden-task-id",
                "hiddenReplayTarget": "to_timestamp=1999-01-01T00:00:00Z",
            }
        )
    )
    assert output["fetchCalls"] == [
        {"route": APPROVED_HISTORY_ROUTE, "method": "GET", "hasBody": False},
        {"route": APPROVED_REPLAY_ROUTE, "method": "GET", "hasBody": False},
        {"route": VALIDATE_ROUTE, "method": "GET", "hasBody": False},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "hidden-task-id" not in rendered
    assert "1999-01-01" not in rendered


def test_story_105_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    cases: list[RuntimeCase] = [
        healthy_case({}),
        {
            "name": "empty",
            "historyResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "events": []},
            },
            "replayResponse": {"ok": True, "status": 200, "body": {"replayed_event_count": 0}},
            "validateResponse": {"ok": True, "status": 200, "body": {"validation_status": "empty"}},
            "expected": ["empty", "non-authoritative", "history events: 0", "replayed events: 0"],
        },
        {
            "name": "partial",
            "historyResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "display_state": "partial", "events": []},
            },
            "replayResponse": {
                "ok": True,
                "status": 200,
                "body": {"display_state": "partial", "replayed_event_count": 0},
            },
            "validateResponse": {
                "ok": True,
                "status": 200,
                "body": {"display_state": "partial", "validation_status": "partial"},
            },
            "expected": ["partial", "non-authoritative"],
        },
        {
            "name": "stale",
            "historyResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "freshness_state": "stale", "events": []},
            },
            "replayResponse": {
                "ok": True,
                "status": 200,
                "body": {"freshness_state": "stale", "replayed_event_count": 0},
            },
            "validateResponse": {
                "ok": True,
                "status": 200,
                "body": {"freshness_state": "stale", "validation_status": "stale"},
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "historyResponse": {"ok": True, "status": 200, "jsonError": "bad json"},
            "replayResponse": {"ok": True, "status": 200, "body": {}},
            "validateResponse": {"ok": True, "status": 200, "body": {}},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "historyResponse": {"ok": False, "status": 403, "body": {}},
            "replayResponse": {"ok": True, "status": 200, "body": {}},
            "validateResponse": {"ok": True, "status": 200, "body": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "validation-mismatch",
            "historyResponse": {
                "ok": True,
                "status": 200,
                "body": {"task_id": VISIBLE_TASK_ID, "events": [{"task_id": VISIBLE_TASK_ID}]},
            },
            "replayResponse": {"ok": True, "status": 200, "body": {"replayed_event_count": 1}},
            "validateResponse": {
                "ok": True,
                "status": 200,
                "body": {"validation_status": "mismatch"},
            },
            "expected": ["replay validation mismatch", "validation: mismatch", "non-authoritative"],
        },
        {
            "name": "network",
            "historyReject": "network down",
            "replayResponse": {"ok": True, "status": 200, "body": {}},
            "validateResponse": {"ok": True, "status": 200, "body": {}},
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_history_replay_runtime_case(case)
        assert output["fetchCalls"] == [
            {"route": APPROVED_HISTORY_ROUTE, "method": "GET", "hasBody": False},
            {"route": APPROVED_REPLAY_ROUTE, "method": "GET", "hasBody": False},
            {"route": VALIDATE_ROUTE, "method": "GET", "hasBody": False},
        ]
        rendered = " ".join(output["texts"].values()).lower()
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_105_2_runtime_does_not_render_raw_state_rows_or_diff_values() -> None:
    output = run_history_replay_runtime_case(
        healthy_case(
            {
                "historyResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "task_id": VISIBLE_TASK_ID,
                        "retrieved_at": "2026-06-24T00:00:00Z",
                        "events": [
                            {
                                "task_id": VISIBLE_TASK_ID,
                                "event_id": "evt-visible",
                                "trace_id": "trace-visible",
                                "payload": {"summary": "raw-payload-secret"},
                            }
                        ],
                    },
                },
                "replayResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "replayed_event_count": 7,
                        "replay_id": "replay-visible",
                        "event_id": "evt-replay-visible",
                        "trace_id": "trace-replay-visible",
                        "state": {"raw_state_secret": "do-not-render"},
                        "tasks": [{"task_id": "raw-task-secret"}],
                        "sessions": [{"session_id": "raw-session-secret"}],
                    },
                },
                "validateResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "validation_status": "match",
                        "replay_id": "replay-visible",
                        "field_diffs": {"raw_diff_secret": "do-not-render"},
                    },
                },
            }
        )
    )
    rendered = " ".join(output["texts"].values()).lower()
    for forbidden in (
        "raw-payload-secret",
        "raw_state_secret",
        "raw-task-secret",
        "raw-session-secret",
        "raw_diff_secret",
        "do-not-render",
    ):
        assert forbidden not in rendered
    assert "evt-visible" in rendered
    assert "trace-visible" in rendered
    assert "replay-visible" in rendered
    assert "metadata only" in rendered


def test_story_105_2_missing_returned_freshness_is_not_fabricated() -> None:
    output = run_history_replay_runtime_case(
        healthy_case(
            {
                "historyResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {"task_id": VISIBLE_TASK_ID, "events": []},
                },
                "replayResponse": {"ok": True, "status": 200, "body": {"replayed_event_count": 0}},
                "validateResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {"validation_status": "match"},
                },
            }
        )
    )
    freshness = output["texts"].get("history-replay-freshness", "").lower()
    assert "freshness: not returned" in freshness
    assert "2026-" not in freshness


def test_story_105_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_history_replay_runtime_case(healthy_case({}), ready_state="interactive")
    assert output["fetchCalls"] == [
        {"route": APPROVED_HISTORY_ROUTE, "method": "GET", "hasBody": False},
        {"route": APPROVED_REPLAY_ROUTE, "method": "GET", "hasBody": False},
        {"route": VALIDATE_ROUTE, "method": "GET", "hasBody": False},
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "healthy" in rendered
    assert "authoritative" in rendered


def test_story_105_2_omx_planning_review_wait_guardrail_is_recorded() -> None:
    policy = Path("docs/omx-guardrails.md").read_text(encoding="utf-8").lower()
    for required in (
        "planning/review waits are capped at 5 minutes",
        "on timeout, attempt one replacement lane spawn",
        "if replacement spawn is unavailable",
        "record a stale/capacity incident",
        "stop that lane cleanly",
        "do not use multi_agent_v1.close_agent",
        "no unbounded waits",
    ):
        assert required in policy


def healthy_case(overrides: dict[str, object]) -> RuntimeCase:
    base: RuntimeCase = {
        "name": "healthy",
        "historyResponse": {
            "ok": True,
            "status": 200,
            "body": {
                "task_id": VISIBLE_TASK_ID,
                "retrieved_at": "2026-06-24T00:00:00Z",
                "events": [
                    {"task_id": VISIBLE_TASK_ID, "event_id": "evt-1", "trace_id": "trace-1"}
                ],
            },
        },
        "replayResponse": {
            "ok": True,
            "status": 200,
            "body": {
                "retrieved_at": "2026-06-24T00:00:01Z",
                "replayed_event_count": 3,
                "replay_id": "replay-1",
                "event_id": "evt-r1",
                "trace_id": "trace-r1",
            },
        },
        "validateResponse": {
            "ok": True,
            "status": 200,
            "body": {
                "retrieved_at": "2026-06-24T00:00:02Z",
                "validation_status": "match",
                "replay_id": "replay-1",
            },
        },
        "expected": [
            "healthy",
            "authoritative",
            "history events: 1",
            "replayed events: 3",
            "validation: match",
            "metadata only",
        ],
    }
    base.update(cast(RuntimeCase, overrides))
    return base


def run_history_replay_runtime_case(
    case: RuntimeCase, *, ready_state: str = "loading"
) -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(HISTORY_REPLAY_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            let defaultText = '';
            if (id === 'history-replay-task-id-source') defaultText = testCase.taskIdText ?? {json.dumps(VISIBLE_TASK_ID)};
            if (id === 'history-replay-target-kind-source') defaultText = testCase.targetKindText ?? {json.dumps(VISIBLE_TARGET_KIND)};
            if (id === 'history-replay-target-value-source') defaultText = testCase.targetValueText ?? {json.dumps(VISIBLE_TARGET_VALUE)};
            const node = {{
              _text: defaultText,
              dataset: id === 'history-replay-task-id-source' ? {{ taskId: testCase.hiddenTaskId || '', replayTarget: testCase.hiddenReplayTarget || '' }} : {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        async function responseFor(route) {{
          if (route.startsWith('/v1/tasks/') && route.endsWith('/history')) {{
            if (testCase.historyReject) throw new Error(testCase.historyReject);
            return testCase.historyResponse;
          }}
          if (route.startsWith('/v1/events/replay?')) {{
            if (testCase.replayReject) throw new Error(testCase.replayReject);
            return testCase.replayResponse;
          }}
          if (route === '/v1/events/replay/validate') {{
            if (testCase.validateReject) throw new Error(testCase.validateReject);
            return testCase.validateResponse;
          }}
          throw new Error('unexpected route ' + route);
        }}
        const sandbox = {{
          console,
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
        vm.runInContext(source, sandbox, {{ filename: 'history-replay.js' }});
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
