from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
LIFECYCLE_RUNTIME = Path("dashboard/static/lifecycle-snapshot.js")
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
SNAPSHOT_ROUTE = "/v1/events/replay/snapshots"
SNAPSHOT_PATTERN = "GET /v1/events/replay/snapshots"
FETCH_CALL_RE = re.compile(r"fetch\(\s*ROUTE(?P<options>[^)]*)\)", re.DOTALL)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:PUT|PATCH|DELETE)\b", re.IGNORECASE)
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
    "location.search",
    "location.hash",
    "URLSearchParams(location",
    "Date.now",
    "new Date",
    "toISOString",
    "create_snapshot",
    "delete_snapshot",
    "post_replay_snapshot",
    "applyLifecycle",
    "pruneLifecycle",
    "rollbackLifecycle",
    "archiveMutation",
    "manifestMutation",
    "/v1/tasks?",
    "/v1/tasks/search",
    "/v1/sessions",
    "/v1/tasks/{task_id}/logs/digest",
    "/v1/logs/digest",
    "/v1/events/replay?",
    "/v1/events/replay/validate",
    "/v1/trace/",
    "/v1/health",
)


class SnapshotEntry(TypedDict, total=False):
    snapshot_id: str
    sequence_number: int
    timestamp: str
    size_bytes: int
    state: dict[str, object]
    tasks: list[dict[str, object]]
    sessions: list[dict[str, object]]
    archive_path: str
    replay_target: str


class SnapshotBody(TypedDict, total=False):
    snapshots: list[SnapshotEntry]
    total: int
    retrieved_at: str
    freshness_state: str
    display_state: str


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: SnapshotBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    snapshotResponse: NotRequired[RuntimeResponse]
    snapshotReject: NotRequired[str]
    hiddenSnapshotId: NotRequired[str]
    hiddenLifecycleAction: NotRequired[str]
    lifecycleEvidence: NotRequired[dict[str, object]]


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

    def handle_endtag(self, _tag: str) -> None:
        if self.inline_script_depth:
            self.inline_script_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.inline_script_depth:
            self.inline_script_text.append(data)


def parse_scripts() -> ScriptParser:
    parser = ScriptParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def runtime_source() -> str:
    return LIFECYCLE_RUNTIME.read_text(encoding="utf-8")


def test_story_106_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()
    assert parser.scripts == [{"src": script, "defer": ""} for script in APPROVED_SCRIPTS]
    assert not "".join(parser.inline_script_text).strip()
    assert parser.controls == ["input", "button"]
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert LIFECYCLE_RUNTIME.exists()


def test_story_106_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == sorted(APPROVED_SCRIPTS)
    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_106_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    assert SNAPSHOT_ROUTE in source
    assert source.count(SNAPSHOT_ROUTE) == 2
    assert "const ROUTE = " in source
    assert "const CREATE_ROUTE = " in source
    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    for fetch in fetches:
        method_match = METHOD_RE.search(fetch.group("options"))
        assert method_match is None or method_match.group("method").upper() == "GET"
        assert "body" not in fetch.group("options").lower()
    assert not FORBIDDEN_METHOD_RE.search(source)
    assert source.count('method: "POST"') == 1


def test_story_106_2_panel_exposes_bounded_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "lifecycle-snapshot-status",
        "lifecycle-snapshot-source",
        "lifecycle-snapshot-count",
        "lifecycle-snapshot-freshness",
        "lifecycle-snapshot-authority",
        "lifecycle-snapshot-items",
        "lifecycle-snapshot-evidence",
        "lifecycle-snapshot-degraded",
        "lifecycle-snapshot-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert SNAPSHOT_PATTERN in raw
    panel_text = raw[raw.index('id="lifecycle-snapshot"') : raw.index('id="health"')]
    assert "metadata only" in panel_text.lower()
    assert "not controls" in panel_text.lower()
    assert "window.LIFECYCLE_SNAPSHOT_EVIDENCE" in panel_text
    assert "missing lifecycle evidence as non-authoritative" in panel_text


def test_story_106_2_fetches_exact_snapshot_route_once() -> None:
    output = run_lifecycle_snapshot_runtime_case(healthy_case({}))
    assert output["fetchCalls"] == [{"route": SNAPSHOT_ROUTE, "method": "GET", "hasBody": False}]
    rendered = " ".join(output["texts"].values()).lower()
    for expected in ("healthy", "authoritative", "snapshots: 1", "snapshot_id=snap-1"):
        assert expected in rendered


def test_story_106_2_hidden_selector_decoys_are_ignored() -> None:
    output = run_lifecycle_snapshot_runtime_case(
        healthy_case(
            {
                "hiddenSnapshotId": "snap-hidden",
                "hiddenLifecycleAction": "run-hidden-lifecycle",
            }
        )
    )
    assert output["fetchCalls"] == [{"route": SNAPSHOT_ROUTE, "method": "GET", "hasBody": False}]
    rendered = " ".join(output["texts"].values()).lower()
    assert "snap-hidden" not in rendered
    assert "run-hidden-lifecycle" not in rendered


def test_story_106_2_runtime_behavior_maps_success_empty_and_failures() -> None:
    cases: list[RuntimeCase] = [
        healthy_case({}),
        {
            "name": "empty",
            "snapshotResponse": {"ok": True, "status": 200, "body": {"snapshots": [], "total": 0}},
            "expected": ["empty", "non-authoritative", "snapshots: 0"],
        },
        {
            "name": "stale",
            "snapshotResponse": {
                "ok": True,
                "status": 200,
                "body": {"snapshots": [], "total": 0, "freshness_state": "stale"},
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "invalid-json",
            "snapshotResponse": {"ok": True, "status": 200, "jsonError": "bad json"},
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "malformed-row",
            "snapshotResponse": {
                "ok": True,
                "status": 200,
                "body": {"snapshots": [{"snapshot_id": "snap"}], "total": 1},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "inconsistent-total",
            "snapshotResponse": {
                "ok": True,
                "status": 200,
                "body": {"snapshots": [], "total": 2},
            },
            "expected": ["invalid", "non-authoritative"],
        },
        {
            "name": "unauthorized",
            "snapshotResponse": {"ok": False, "status": 403, "body": {}},
            "expected": ["unauthorized", "non-authoritative"],
        },
        {
            "name": "backend",
            "snapshotResponse": {"ok": False, "status": 500, "body": {}},
            "expected": ["backend unavailable", "non-authoritative"],
        },
        {"name": "network", "snapshotReject": "network down", "expected": ["backend unavailable"]},
    ]
    for case in cases:
        output = run_lifecycle_snapshot_runtime_case(case)
        assert output["fetchCalls"] == [
            {"route": SNAPSHOT_ROUTE, "method": "GET", "hasBody": False}
        ]
        rendered = " ".join(output["texts"].values()).lower()
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_106_2_lifecycle_evidence_degraded_states_are_non_authoritative() -> None:
    degraded_evidence_cases: list[tuple[str, dict[str, object], str]] = [
        ("missing-evidence", {}, "missing lifecycle evidence"),
        (
            "failed-replay",
            {"replay_validation_ref": "failed replay validation"},
            "failed replay validation",
        ),
        (
            "stale-replay",
            {"replay_validation_ref": "stale replay evidence"},
            "stale replay evidence",
        ),
        ("missing-rollback", {"rollback_evidence_ref": ""}, "missing rollback evidence"),
        (
            "invalid-archive",
            {"archive_manifest_validation": "invalid archive configuration"},
            "invalid archive configuration",
        ),
        (
            "unverifiable",
            {"archive_error_boundary": "unverifiable lifecycle evidence"},
            "unverifiable lifecycle evidence",
        ),
    ]
    for name, evidence, expected in degraded_evidence_cases:
        output = run_lifecycle_snapshot_runtime_case(
            healthy_case({"name": name, "lifecycleEvidence": evidence})
        )
        rendered = " ".join(output["texts"].values()).lower()
        assert expected in rendered
        assert "non-authoritative" in rendered


def test_story_106_2_unbounded_display_state_is_not_rendered() -> None:
    output = run_lifecycle_snapshot_runtime_case(
        healthy_case(
            {
                "snapshotResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "retrieved_at": "2026-06-24T00:00:00Z",
                        "display_state": "archive_path=/private raw-task-secret raw-session-secret",
                        "snapshots": [
                            {
                                "snapshot_id": "snap-1",
                                "sequence_number": 1,
                                "timestamp": "2026-06-24T00:00:00Z",
                                "size_bytes": 12,
                            }
                        ],
                        "total": 1,
                    },
                }
            }
        )
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert "invalid" in rendered
    assert "non-authoritative" in rendered
    for forbidden in ("archive_path", "/private", "raw-task-secret", "raw-session-secret"):
        assert forbidden not in rendered


def test_story_106_2_archive_manifest_validation_requires_exact_valid_evidence() -> None:
    for manifest_value in ("invalid", "not valid", "unvalidated"):
        evidence = dict(healthy_case({})["lifecycleEvidence"])
        evidence["archive_manifest_validation"] = manifest_value
        output = run_lifecycle_snapshot_runtime_case(
            healthy_case({"name": f"manifest-{manifest_value}", "lifecycleEvidence": evidence})
        )
        rendered = " ".join(output["texts"].values()).lower()
        assert "invalid archive configuration" in rendered
        assert "non-authoritative" in rendered
        assert "lifecycle evidence: ready" not in rendered


def test_story_106_2_runtime_does_not_render_raw_snapshot_state_or_control_values() -> None:
    output = run_lifecycle_snapshot_runtime_case(
        healthy_case(
            {
                "snapshotResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "retrieved_at": "2026-06-24T00:00:00Z",
                        "snapshots": [
                            {
                                "snapshot_id": "snap-visible",
                                "sequence_number": 7,
                                "timestamp": "2026-06-24T00:00:00Z",
                                "size_bytes": 123,
                                "state": {"raw_state_secret": "do-not-render"},
                                "tasks": [{"task_id": "raw-task-secret"}],
                                "sessions": [{"session_id": "raw-session-secret"}],
                                "archive_path": "../../archive-secret",
                                "replay_target": "to_sequence=999",
                            }
                        ],
                        "total": 1,
                    },
                }
            }
        )
    )
    rendered = " ".join(output["texts"].values()).lower()
    for forbidden in (
        "raw_state_secret",
        "raw-task-secret",
        "raw-session-secret",
        "archive-secret",
        "to_sequence=999",
        "do-not-render",
    ):
        assert forbidden not in rendered
    assert "snap-visible" in rendered
    assert "metadata only" in rendered


def test_story_106_2_missing_returned_freshness_is_not_fabricated() -> None:
    output = run_lifecycle_snapshot_runtime_case(
        healthy_case(
            {
                "snapshotResponse": {
                    "ok": True,
                    "status": 200,
                    "body": {
                        "snapshots": [
                            {
                                "snapshot_id": "snap-1",
                                "sequence_number": 1,
                                "timestamp": "2026-06-24T00:00:00Z",
                                "size_bytes": 12,
                            }
                        ],
                        "total": 1,
                    },
                }
            }
        )
    )
    freshness = output["texts"].get("lifecycle-snapshot-freshness", "").lower()
    assert "freshness: not returned" in freshness
    assert "2026-" not in freshness


def test_story_106_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_lifecycle_snapshot_runtime_case(healthy_case({}), ready_state="interactive")
    assert output["fetchCalls"] == [{"route": SNAPSHOT_ROUTE, "method": "GET", "hasBody": False}]
    rendered = " ".join(output["texts"].values()).lower()
    assert "healthy" in rendered
    assert "authoritative" in rendered


def healthy_case(overrides: dict[str, object]) -> RuntimeCase:
    base: RuntimeCase = {
        "name": "healthy",
        "snapshotResponse": {
            "ok": True,
            "status": 200,
            "body": {
                "retrieved_at": "2026-06-24T00:00:00Z",
                "snapshots": [
                    {
                        "snapshot_id": "snap-1",
                        "sequence_number": 42,
                        "timestamp": "2026-06-24T00:00:00Z",
                        "size_bytes": 2048,
                    }
                ],
                "total": 1,
            },
        },
        "lifecycleEvidence": {
            "plan_hash": "plan-hash-1",
            "dry_run_artifact_ref": "dry-run-ref",
            "safety_policy_version": "policy-v1",
            "retention_input_digest": "digest-1",
            "affected_segments": "segments-1",
            "replay_validation_ref": "replay-validation-ok",
            "rollback_evidence_ref": "rollback-ok",
            "operator_identity": "operator-1",
            "authorized_at": "2026-06-24T00:00:00Z",
            "authorization_event_ref": "auth-event-1",
            "archive_manifest_ref": "manifest-1",
            "archive_manifest_validation": "valid archive manifest",
            "archive_error_boundary": "none",
        },
        "expected": ["healthy", "authoritative", "snapshots: 1", "metadata only"],
    }
    base.update(cast(RuntimeCase, overrides))
    return base


def run_lifecycle_snapshot_runtime_case(
    case: RuntimeCase, *, ready_state: str = "loading"
) -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(LIFECYCLE_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              _text: '',
              dataset: id === 'lifecycle-snapshot-status' ? {{ snapshotId: testCase.hiddenSnapshotId || '', lifecycleAction: testCase.hiddenLifecycleAction || '' }} : {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }}
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        async function responseFor(route) {{
          if (route === {json.dumps(SNAPSHOT_ROUTE)}) {{
            if (testCase.snapshotReject) throw new Error(testCase.snapshotReject);
            return testCase.snapshotResponse;
          }}
          throw new Error('unexpected route ' + route);
        }}
        const sandbox = {{
          console,
          LIFECYCLE_SNAPSHOT_EVIDENCE: testCase.lifecycleEvidence,
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
        vm.runInContext(source, sandbox, {{ filename: 'lifecycle-snapshot.js' }});
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


CREATE_ROUTE = "/v1/events/replay/snapshots"
CREATE_PATTERN = "POST /v1/events/replay/snapshots"
CREATE_TOKEN = "Bearer story-107-2-token-secret"


class CreateRuntimeOutput(TypedDict):
    texts: dict[str, str]
    fetchCalls: list[dict[str, object]]
    listeners: dict[str, int]
    storedWrites: list[str]


def test_story_107_2_create_affordance_is_visible_and_narrowly_allowlisted() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "lifecycle-snapshot-create-token",
        "lifecycle-snapshot-create-button",
        "lifecycle-snapshot-create-status",
        "lifecycle-snapshot-create-result",
    ):
        assert f'id="{element_id}"' in raw
    assert CREATE_PATTERN in raw
    parser = parse_scripts()
    controls = [control for control in parser.controls if control in {"input", "button"}]
    assert controls == ["input", "button"]


def test_story_107_2_create_posts_only_after_visible_click_with_exact_body_free_shape() -> None:
    output = run_lifecycle_snapshot_create_case(
        {
            "tokenValue": CREATE_TOKEN,
            "createResponse": {
                "ok": True,
                "status": 201,
                "body": {
                    "snapshot_id": "snap-created",
                    "sequence_number": 99,
                    "timestamp": "2026-06-25T18:00:00Z",
                    "size_bytes": 4096,
                    "raw_state_secret": "do-not-render",
                },
            },
            "clicks": 1,
        }
    )
    post_calls = [call for call in output["fetchCalls"] if call["method"] == "POST"]
    assert post_calls == [
        {
            "route": CREATE_ROUTE,
            "method": "POST",
            "hasBody": False,
            "authorization": CREATE_TOKEN,
        }
    ]
    get_calls = [call for call in output["fetchCalls"] if call["method"] == "GET"]
    assert get_calls == [
        {"route": SNAPSHOT_ROUTE, "method": "GET", "hasBody": False, "authorization": None}
    ]
    rendered = " ".join(output["texts"].values()).lower()
    assert "snap-created" in rendered
    assert "sequence_number=99" in rendered
    assert "size_bytes=4096" in rendered
    assert "raw_state_secret" not in rendered
    assert "story-107-2-token-secret" not in rendered
    assert output["storedWrites"] == []


def test_story_107_2_create_missing_or_malformed_token_fails_closed_before_fetch() -> None:
    for token in ("", "not-bearer token"):
        output = run_lifecycle_snapshot_create_case({"tokenValue": token, "clicks": 1})
        post_calls = [call for call in output["fetchCalls"] if call["method"] == "POST"]
        assert post_calls == []
        rendered = " ".join(output["texts"].values()).lower()
        assert "authorization required" in rendered
        assert "non-authoritative" in rendered


def test_story_107_2_create_failures_and_timeout_do_not_auto_retry_or_echo_token() -> None:
    cases = [
        {"name": "unauthorized", "createResponse": {"ok": False, "status": 403, "body": {}}},
        {"name": "backend", "createResponse": {"ok": False, "status": 500, "body": {}}},
        {
            "name": "non-201-success",
            "createResponse": {
                "ok": True,
                "status": 200,
                "body": {
                    "snapshot_id": "snap-wrong-status",
                    "sequence_number": 99,
                    "timestamp": "2026-06-25T18:00:00Z",
                    "size_bytes": 4096,
                },
            },
        },
        {"name": "invalid-json", "createResponse": {"ok": True, "status": 201, "jsonError": "bad"}},
        {
            "name": "malformed",
            "createResponse": {"ok": True, "status": 201, "body": {"snapshot_id": "snap"}},
        },
        {"name": "network", "createReject": "network down"},
        {"name": "timeout", "createReject": "timeout unknown outcome"},
    ]
    for case in cases:
        output = run_lifecycle_snapshot_create_case(
            {"tokenValue": CREATE_TOKEN, "clicks": 1, **case}
        )
        post_calls = [call for call in output["fetchCalls"] if call["method"] == "POST"]
        assert len(post_calls) == 1, case["name"]
        rendered = " ".join(output["texts"].values()).lower()
        assert "non-authoritative" in rendered, case["name"]
        assert "snap-wrong-status" not in rendered, case["name"]
        assert "story-107-2-token-secret" not in rendered


def test_story_107_2_duplicate_in_flight_clicks_create_one_post_and_later_success_needs_fresh_click() -> (
    None
):
    output = run_lifecycle_snapshot_create_case(
        {
            "tokenValue": CREATE_TOKEN,
            "clicks": 2,
            "resolveCreateAfterClicks": True,
            "createResponse": {
                "ok": True,
                "status": 201,
                "body": {
                    "snapshot_id": "snap-first",
                    "sequence_number": 1,
                    "timestamp": "2026-06-25T18:00:00Z",
                    "size_bytes": 10,
                },
            },
        }
    )
    assert [call for call in output["fetchCalls"] if call["method"] == "POST"] == [
        {"route": CREATE_ROUTE, "method": "POST", "hasBody": False, "authorization": CREATE_TOKEN}
    ]

    output = run_lifecycle_snapshot_create_case(
        {
            "tokenValue": CREATE_TOKEN,
            "clicks": 2,
            "createResponse": {
                "ok": True,
                "status": 201,
                "body": {
                    "snapshot_id": "snap-next",
                    "sequence_number": 2,
                    "timestamp": "2026-06-25T18:01:00Z",
                    "size_bytes": 20,
                },
            },
        }
    )
    assert len([call for call in output["fetchCalls"] if call["method"] == "POST"]) == 2


def run_lifecycle_snapshot_create_case(case: dict[str, object]) -> CreateRuntimeOutput:
    healthy_get = healthy_case({})["snapshotResponse"]
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(LIFECYCLE_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const snapshotResponse = {json.dumps(healthy_get)};
        const texts = {{}};
        const elements = new Map();
        const listeners = {{}};
        const callbacks = [];
        const storedWrites = [];
        function element(id) {{
          if (!elements.has(id)) {{
            const node = {{
              id,
              value: id === 'lifecycle-snapshot-create-token' ? (testCase.tokenValue || '') : '',
              disabled: false,
              dataset: testCase.dataset || {{}},
              _text: '',
              _listeners: {{}},
              set textContent(value) {{ this._text = String(value); texts[id] = String(value); }},
              get textContent() {{ return this._text || ''; }},
              addEventListener(name, callback) {{ this._listeners[name] = callback; listeners[id + ':' + name] = (listeners[id + ':' + name] || 0) + 1; }},
            }};
            elements.set(id, node);
          }}
          return elements.get(id);
        }}
        const fetchCalls = [];
        let pendingCreate;
        async function responseFor(route, options) {{
          const method = (options.method || 'GET').toUpperCase();
          if (method === 'GET' && route === {json.dumps(SNAPSHOT_ROUTE)}) return snapshotResponse;
          if (method === 'POST' && route === {json.dumps(CREATE_ROUTE)}) {{
            if (testCase.createReject) throw new Error(testCase.createReject);
            if (testCase.resolveCreateAfterClicks && !pendingCreate) {{
              pendingCreate = {{}};
              pendingCreate.promise = new Promise((resolve) => {{ pendingCreate.resolve = resolve; }});
              return pendingCreate.promise;
            }}
            return testCase.createResponse;
          }}
          throw new Error('unexpected route ' + method + ' ' + route);
        }}
        const sandbox = {{
          console: {{ log() {{}}, warn() {{}}, error() {{}} }},
          LIFECYCLE_SNAPSHOT_EVIDENCE: {json.dumps(healthy_case({})["lifecycleEvidence"])},
          localStorage: {{ setItem: (...args) => storedWrites.push('localStorage:' + args.join('=')), getItem: () => null }},
          sessionStorage: {{ setItem: (...args) => storedWrites.push('sessionStorage:' + args.join('=')), getItem: () => null }},
          indexedDB: {{ open: () => storedWrites.push('indexedDB.open') }},
          caches: {{ open: () => storedWrites.push('caches.open') }},
          document: {{
            readyState: 'loading',
            addEventListener: (_name, callback) => callbacks.push(callback),
            getElementById: element,
          }},
          window: {{}},
          fetch: async (route, options = {{}}) => {{
            fetchCalls.push({{ route, method: (options.method || 'GET').toUpperCase(), hasBody: Object.prototype.hasOwnProperty.call(options, 'body'), authorization: options.headers && (options.headers.Authorization || options.headers.authorization) || null }});
            const response = await responseFor(route, options);
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
        vm.runInContext(source, sandbox, {{ filename: 'lifecycle-snapshot.js' }});
        Promise.resolve(callbacks[0] ? callbacks[0]() : undefined)
          .then(async () => {{
            await new Promise((resolve) => setImmediate(resolve));
            const button = element('lifecycle-snapshot-create-button');
            const click = button._listeners.click;
            for (let i = 0; i < (testCase.clicks || 0); i += 1) {{
              if (click) {{
                const result = click({{ preventDefault() {{}} }});
                if (!testCase.resolveCreateAfterClicks && result && typeof result.then === 'function') await result;
              }}
            }}
            if (pendingCreate) {{
              pendingCreate.resolve(testCase.createResponse);
              await new Promise((resolve) => setImmediate(resolve));
            }}
            await new Promise((resolve) => setImmediate(resolve));
          }})
          .then(() => {{ process.stdout.write(JSON.stringify({{ texts, fetchCalls, listeners, storedWrites }})); }})
          .catch((error) => {{ console.error(error); process.exit(1); }});
        """
    )
    completed = subprocess.run(
        ["node", "-e", node_code], check=True, text=True, capture_output=True
    )
    loaded = json.loads(completed.stdout)
    assert isinstance(loaded, dict)
    return cast(CreateRuntimeOutput, loaded)
