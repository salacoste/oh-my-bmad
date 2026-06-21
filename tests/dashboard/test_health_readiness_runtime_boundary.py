from __future__ import annotations

import json
import re
import subprocess
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import NotRequired, TypedDict, cast

DASHBOARD = Path("dashboard/static/index.html")
HEALTH_RUNTIME = Path("dashboard/static/health-readiness.js")
APPROVED_SCRIPT = "health-readiness.js"
APPROVED_ROUTE = "/v1/health"
FORBIDDEN_ROUTE_MARKERS = (
    "/v1/tasks",
    "/v1/sessions",
    "/v1/trace",
    "/v1/events/replay",
    "/v1/tasks/{task_id}/history",
    "/v1/tasks/{task_id}/logs/digest",
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
)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
ROUTE_LITERAL_RE = re.compile(r"['\"](?P<route>/v1/[^'\"]+)['\"]")
FETCH_CALL_RE = re.compile(
    r"fetch\(\s*['\"](?P<route>/v1/[^'\"]+)['\"](?P<options>[^)]*)\)", re.DOTALL
)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


class HealthBody(TypedDict, total=False):
    registry_status: str
    worker_status: str
    clawhip_queue_depth: int
    version: str


class RuntimeResponse(TypedDict, total=False):
    ok: bool
    status: int
    body: HealthBody
    jsonError: str


class RuntimeCase(TypedDict):
    name: str
    expected: list[str]
    response: NotRequired[RuntimeResponse]
    reject: NotRequired[str]


class FetchCall(TypedDict):
    route: str
    method: str


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
    return HEALTH_RUNTIME.read_text(encoding="utf-8")


def test_story_101_2_runtime_script_allowlist_is_exact() -> None:
    parser = parse_scripts()

    assert parser.scripts == [{"src": APPROVED_SCRIPT, "defer": ""}]
    assert not "".join(parser.inline_script_text).strip()
    assert not parser.controls
    assert all(
        link.get("rel", "").lower() not in {"preload", "modulepreload"} for link in parser.links
    )
    assert HEALTH_RUNTIME.exists()


def test_story_101_2_only_index_mounts_approved_runtime_script() -> None:
    html_files = sorted(Path("dashboard/static").rglob("*.html"))
    assert html_files == [DASHBOARD]
    for html_file in html_files:
        parser = ScriptParser()
        parser.feed(html_file.read_text(encoding="utf-8"))
        if html_file == DASHBOARD:
            assert parser.scripts == [{"src": APPROVED_SCRIPT, "defer": ""}]
        else:
            assert not parser.scripts


def test_story_101_2_guard_rejects_second_html_entrypoint_reusing_health_script(
    tmp_path: Path,
) -> None:
    second = tmp_path / "secondary.html"
    second.write_text(f'<script src="{APPROVED_SCRIPT}" defer></script>', encoding="utf-8")
    parser = ScriptParser()
    parser.feed(second.read_text(encoding="utf-8"))
    assert parser.scripts == [{"src": APPROVED_SCRIPT, "defer": ""}]
    assert second != DASHBOARD


def test_story_101_2_runtime_module_graph_is_closed() -> None:
    runtime_files = sorted(path.name for path in Path("dashboard/static").glob("*.js"))
    assert runtime_files == [APPROVED_SCRIPT]

    source = runtime_source()
    for marker in FORBIDDEN_RUNTIME_MARKERS:
        assert marker not in source, marker


def test_story_101_2_runtime_route_and_method_allowlist_is_exact() -> None:
    source = runtime_source()
    route_literals = {match.group("route") for match in ROUTE_LITERAL_RE.finditer(source)}
    assert route_literals == {APPROVED_ROUTE}

    fetches = list(FETCH_CALL_RE.finditer(source))
    assert len(fetches) == 1
    fetch = fetches[0]
    assert fetch.group("route") == APPROVED_ROUTE
    method_match = METHOD_RE.search(fetch.group("options"))
    assert method_match is None or method_match.group("method").upper() == "GET"
    assert not FORBIDDEN_METHOD_RE.search(source)
    for marker in FORBIDDEN_ROUTE_MARKERS:
        assert marker not in source, marker


def test_story_101_2_health_panel_exposes_runtime_metadata_targets() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for element_id in (
        "health-readiness-status",
        "health-readiness-source",
        "health-readiness-freshness",
        "health-readiness-authority",
        "health-readiness-detail",
    ):
        assert f'id="{element_id}"' in raw
    assert "single approved live-read boundary" in raw.lower()
    assert "GET /v1/health" in raw


def test_story_101_2_runtime_behavior_maps_success_and_failures() -> None:
    cases: list[RuntimeCase] = [
        {
            "name": "healthy",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "registry_status": "ok",
                    "worker_status": "ok",
                    "clawhip_queue_depth": 0,
                    "version": "1.2.3",
                },
            },
            "expected": ["healthy", "authoritative", "get /v1/health"],
        },
        {
            "name": "stale",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "registry_status": "ok",
                    "worker_status": "idle",
                    "clawhip_queue_depth": 0,
                    "version": "1.2.3",
                },
            },
            "expected": ["stale", "non-authoritative"],
        },
        {
            "name": "backend-unavailable",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "registry_status": "degraded",
                    "worker_status": "unknown",
                    "clawhip_queue_depth": 0,
                    "version": "1.2.3",
                },
            },
            "expected": ["backend unavailable", "non-authoritative"],
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
            "expected": ["backend unavailable", "non-authoritative"],
        },
    ]
    for case in cases:
        output = run_health_runtime_case(case)
        rendered = " ".join(output["texts"].values()).lower()
        assert output["fetchCalls"] == [{"route": APPROVED_ROUTE, "method": "GET"}]
        for expected in case["expected"]:
            assert expected in rendered, (case["name"], expected, rendered)
        if case["name"] != "healthy":
            assert "authoritative success" not in rendered


def test_story_101_2_runtime_behavior_runs_when_document_already_loaded() -> None:
    output = run_health_runtime_case(
        {
            "name": "already-loaded",
            "response": {
                "ok": True,
                "status": 200,
                "body": {
                    "registry_status": "ok",
                    "worker_status": "ok",
                    "clawhip_queue_depth": 0,
                    "version": "1.2.3",
                },
            },
            "expected": ["healthy"],
        },
        ready_state="interactive",
    )
    rendered = " ".join(output["texts"].values()).lower()
    assert output["fetchCalls"] == [{"route": APPROVED_ROUTE, "method": "GET"}]
    assert "healthy" in rendered
    assert "authoritative" in rendered


def run_health_runtime_case(case: RuntimeCase, *, ready_state: str = "loading") -> RuntimeOutput:
    node_code = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({str(HEALTH_RUNTIME)!r}, 'utf8');
        const testCase = {json.dumps(case)};
        const texts = {{}};
        const elements = new Map();
        const callbacks = [];
        function element(id) {{
          if (!elements.has(id)) {{
            elements.set(id, {{
              set textContent(value) {{ texts[id] = String(value); }},
              get textContent() {{ return texts[id] || ''; }}
            }});
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
            fetchCalls.push({{ route, method: (options.method || 'GET').toUpperCase() }});
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
        vm.runInContext(source, sandbox, {{ filename: 'health-readiness.js' }});
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
