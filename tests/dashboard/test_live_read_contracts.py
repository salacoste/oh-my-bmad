from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from tests.dashboard import test_read_only_boundary as boundary

DASHBOARD_ROOT = Path("dashboard")
STATIC_ROOT = DASHBOARD_ROOT / "static"

# Intentional alias, not a copied inventory: Story 94.1 keeps the existing
# read-only boundary route constants as the single source of truth until a
# future story extracts a shared helper.
APPROVED_READ_ROUTES = boundary.CORE_APPROVED_READ_ROUTES
OPTIONAL_NON_CORE_READ_ROUTES = boundary.OPTIONAL_NON_CORE_READ_ROUTES
FORBIDDEN_METHODS = boundary.FORBIDDEN_METHODS
APPROVED_STORY_107_2_CREATE_SOURCE = str(STATIC_ROOT / "lifecycle-snapshot.js")
APPROVED_STORY_107_2_CREATE_ROUTE = "/v1/events/replay/snapshots"
APPROVED_STORY_112_2_STREAM_SOURCE = str(STATIC_ROOT / "digest-stream.js")

NEEDS_SEPARATE_CONTRACT_GET_ROUTES = frozenset(
    {
        "/v1/dashboard",
        "/v1/tasks/{task_id}/actions",
        "/v1/tasks/{task_id}/retry",
        "/v1/events/replay/snapshots/{snapshot_id}",
    }
)

WRITER_OR_MUTATION_IMPORT_MARKERS = (
    "event_writer",
    "registry_writer",
    "write_event",
    "append_event",
    "emit_event",
    "create_snapshot",
    "delete_snapshot",
    "snapshot creation",
    "snapshot delete",
)

LIFECYCLE_OR_BACKGROUND_EFFECT_MARKERS = (
    "lifecycle job",
    "retention job",
    "archive mutation",
    "manifest mutation",
    "background job",
    "cache warm",
    "cache_warm",
    "caches.open",
    "localstorage.setitem",
    "sessionstorage.setitem",
    "indexeddb",
    "navigator.sendbeacon",
    "sendbeacon",
    "serviceworker.register",
    "websocket",
    "eventsource",
    "xmlhttprequest",
    "setinterval",
    "settimeout",
    "document.cookie",
    "credentials: 'include'",
    'credentials: "include"',
)

ACTIONABLE_MUTATION_WORD_RE = re.compile(
    r"\b(?:approval|retry|cancel|apply|prune|delete|truncate|move|rewrite|chmod|"
    r"dispatch|enqueue|schedule|cron|idempotency)\b",
    re.IGNORECASE,
)

FETCH_CALL_RE = re.compile(
    r"fetch\(\s*['\"](?P<route>/v1/[^'\"]+)['\"](?P<options>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
METHOD_RE = re.compile(r"method\s*:\s*['\"](?P<method>[A-Z]+)['\"]", re.IGNORECASE)


def test_route_inventory_is_imported_from_static_boundary_contract() -> None:
    assert APPROVED_READ_ROUTES is boundary.CORE_APPROVED_READ_ROUTES
    assert OPTIONAL_NON_CORE_READ_ROUTES is boundary.OPTIONAL_NON_CORE_READ_ROUTES
    assert FORBIDDEN_METHODS is boundary.FORBIDDEN_METHODS
    assert len(APPROVED_READ_ROUTES) == 16


def test_candidate_core_read_routes_are_unique_normalized_and_get_only() -> None:
    routes = [route for method, route in APPROVED_READ_ROUTES]
    assert len(routes) == len(set(routes))
    for method, route in APPROVED_READ_ROUTES:
        assert method == "GET"
        assert method not in FORBIDDEN_METHODS
        assert route.startswith("/v1/")
        assert "//" not in route
        assert route == route.rstrip("/")


def test_forbidden_methods_are_rejected_for_every_candidate_dashboard_route() -> None:
    for _, route in APPROVED_READ_ROUTES | OPTIONAL_NON_CORE_READ_ROUTES:
        for method in FORBIDDEN_METHODS:
            assert not is_allowlisted_dashboard_read(method, route), (method, route)


def test_digest_aggregate_and_session_reads_are_promoted_and_adjacent_routes_need_contracts() -> (
    None
):
    assert ("GET", "/v1/tasks/{task_id}/logs/digest") in APPROVED_READ_ROUTES
    assert ("GET", "/v1/tasks/{task_id}/logs/digest/stream") in APPROVED_READ_ROUTES
    assert ("GET", "/v1/tasks") in APPROVED_READ_ROUTES
    assert (
        "GET",
        "/v1/tasks?status={task_status}&limit={task_list_limit}",
    ) in APPROVED_READ_ROUTES
    assert (
        "GET",
        "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
    ) in APPROVED_READ_ROUTES
    assert (
        "GET",
        "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
    ) in APPROVED_READ_ROUTES
    assert ("GET", "/v1/sessions") in APPROVED_READ_ROUTES
    assert ("GET", "/v1/sessions/{session_id}") in APPROVED_READ_ROUTES
    for route in NEEDS_SEPARATE_CONTRACT_GET_ROUTES:
        assert ("GET", route) not in APPROVED_READ_ROUTES
        assert not is_allowlisted_dashboard_read("GET", route), route


def test_static_dashboard_keeps_routes_as_inert_text_not_executable_contexts() -> None:
    for html_file in boundary.dashboard_files():
        raw = html_file.read_text(encoding="utf-8")
        parser = boundary.parse_html(raw)
        runtime_text = boundary.context_text(boundary.runtime_contexts(parser)).lower()
        assert "/v1/" not in runtime_text, html_file
        assert not boundary.FORBIDDEN_METHOD_RE.search(runtime_text), html_file


def test_dashboard_executable_surfaces_have_no_writer_or_mutating_effect_markers() -> None:
    for source_file in dashboard_executable_surfaces():
        raw = source_file.read_text(encoding="utf-8")
        executable_text = executable_context_text(source_file, raw)
        assert_no_forbidden_effect_markers(executable_text, source=str(source_file))


def test_guard_sensitivity_rejects_unapproved_live_read_calls_and_methods() -> None:
    bad_snippets = (
        "fetch('/v1/tasks', {method: 'GET'})",
        "fetch('/v1/tasks?status=open', {method: 'GET'})",
        "fetch('/v1/tasks?limit=2&status=plan_ready', {method: 'GET'})",
        "fetch('/v1/tasks?status=plan_ready&limit=2&sort=updated_at', {method: 'GET'})",
        "fetch('/v1/tasks/search', {method: 'GET'})",
        "fetch('/v1/tasks', {method: 'GET', body: '{}'})",
        "fetch('/v1/tasks', {method: 'GET', credentials: 'include'})",
        "fetch('/v1/sessions', {method: 'GET', body: '{}'})",
        "fetch('/v1/sessions/abc', {method: 'GET', body: '{}'})",
        "fetch('/v1/tasks/abc', {method: 'POST'})",
        "fetch('/v1/tasks/abc/events', {method: 'PATCH'})",
    )
    for snippet in bad_snippets:
        assert_live_call_contract_fails(snippet)


def test_guard_sensitivity_rejects_writer_lifecycle_background_and_cache_effects() -> None:
    bad_snippets = (
        "from registry_writer import append_event",
        "event_writer.write_event(payload)",
        "create_snapshot(task_id)",
        "dispatch(background_job)",
        "enqueue(lifecycle_job)",
        "cache_warm('/v1/tasks/abc')",
        "localStorage.setItem('dashboard', 'warm')",
        "sessionStorage.setItem('dashboard', 'warm')",
        "indexedDB.open('dashboard')",
        "navigator.sendBeacon('/v1/health')",
        "serviceWorker.register('/worker.js')",
        "caches.open('dashboard')",
        "setInterval(refreshDashboard, 1000)",
    )
    for snippet in bad_snippets:
        try:
            assert_no_forbidden_effect_markers(snippet, source="synthetic")
        except AssertionError:
            continue
        raise AssertionError(f"forbidden effect probe unexpectedly passed: {snippet}")


def test_guard_sensitivity_rejects_actionable_mutation_vocabulary() -> None:
    bad_snippets = (
        "button.onclick = () => retry(taskId)",
        "schedule credentialed lifecycle production operation",
        "idempotency cache mutation write",
        "apply archive mutation and manifest mutation",
        "delete snapshot and prune history",
        "chmod rewrite move truncate",
    )
    for snippet in bad_snippets:
        try:
            assert_no_forbidden_effect_markers(snippet, source="synthetic")
        except AssertionError:
            continue
        raise AssertionError(f"mutation vocabulary probe unexpectedly passed: {snippet}")


def is_allowlisted_dashboard_read(method: str, route: str) -> bool:
    normalized_method = method.upper()
    if normalized_method in FORBIDDEN_METHODS:
        return False
    return (normalized_method, route.rstrip("/")) in APPROVED_READ_ROUTES


def dashboard_executable_surfaces() -> tuple[Path, ...]:
    files: list[Path] = []
    for pattern in ("static/**/*.html", "**/*.js", "**/*.ts", "**/*.py"):
        files.extend(DASHBOARD_ROOT.glob(pattern))
    return tuple(sorted(path for path in files if path.is_file()))


def executable_context_text(path: Path, raw: str) -> str:
    if path.suffix == ".html":
        parser = boundary.parse_html(raw)
        return boundary.context_text(boundary.runtime_contexts(parser))
    return unquote(raw)


def assert_no_forbidden_effect_markers(text: str, *, source: str) -> None:
    lowered = text.lower()
    for marker in WRITER_OR_MUTATION_IMPORT_MARKERS + LIFECYCLE_OR_BACKGROUND_EFFECT_MARKERS:
        if source == APPROVED_STORY_107_2_CREATE_SOURCE and marker == "snapshot creation":
            continue
        if source == APPROVED_STORY_112_2_STREAM_SOURCE and marker == "settimeout":
            continue
        assert marker not in lowered, (source, marker)
    assert not ACTIONABLE_MUTATION_WORD_RE.search(text), source
    for match in FETCH_CALL_RE.finditer(text):
        route = match.group("route").rstrip("/")
        options = match.group("options").lower()
        method_match = METHOD_RE.search(match.group("options"))
        method = method_match.group("method").upper() if method_match else "GET"
        if method == "GET" and (
            route
            in {
                "/v1/tasks",
                "/v1/tasks?status={task_status}&limit={task_list_limit}",
                "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
                "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
                "/v1/sessions",
            }
            or route.startswith("/v1/sessions/")
        ):
            assert "body" not in options, (source, method, route, "body")
            assert 'credentials: "include"' not in options, (
                source,
                method,
                route,
                "credentials",
            )
            assert "credentials: 'include'" not in options, (
                source,
                method,
                route,
                "credentials",
            )
            if route in {
                "/v1/tasks",
                "/v1/tasks?status={task_status}&limit={task_list_limit}",
                "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
                "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
            }:
                credentials_is_omit = (
                    'credentials: "omit"' in options or "credentials: 'omit'" in options
                )
                assert credentials_is_omit, (source, method, route, "credentials")
            if route == "/v1/sessions" or route.startswith("/v1/sessions/"):
                assert "credentials" not in options, (source, method, route, "credentials")
                assert "accept" in options and "application/json" in options, (
                    source,
                    method,
                    route,
                    "accept",
                )
        assert is_allowlisted_dashboard_call(source, method, route), (source, method, route)


def is_allowlisted_dashboard_call(source: str, method: str, route: str) -> bool:
    if (
        source == APPROVED_STORY_107_2_CREATE_SOURCE
        and method.upper() == "POST"
        and route == APPROVED_STORY_107_2_CREATE_ROUTE
    ):
        return True
    return is_allowlisted_dashboard_read(method, route)


def assert_live_call_contract_fails(snippet: str) -> None:
    try:
        assert_no_forbidden_effect_markers(snippet, source="synthetic")
    except AssertionError:
        return
    raise AssertionError(f"live call probe unexpectedly passed: {snippet}")
