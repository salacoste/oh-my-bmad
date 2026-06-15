from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

DASHBOARD = Path("dashboard/static/index.html")

REQUIRED_PANELS = {
    "overview": "Overview",
    "tasks": "Tasks",
    "task-detail": "Task Detail",
    "sessions": "Sessions",
    "events": "Events",
    "traces": "Traces",
    "replay-lifecycle-readiness": "Replay / lifecycle readiness",
    "health": "Health",
    "audit": "Audit",
    "help": "Help",
}

FORBIDDEN_TAGS = {"form", "button", "script", "input", "select", "textarea"}
LIVE_API_MARKERS = (
    "fetch(",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "http://",
    "https://",
)
APPROVED_TASK_DETAIL_ROUTE = "GET /v1/tasks/{task_id}"
CONTROL_TERMS = (
    "approval",
    "retry",
    "cancel",
    "budget override",
    "apply",
    "prune",
    "delete",
    "truncate",
    "move",
    "rewrite",
    "chmod",
    "archive mutation",
    "manifest mutation",
    "scheduled job",
    "credentialed lifecycle",
    "production operation",
)
STORY_89_1_STATE_TERMS = (
    "unavailable read",
    "loading",
    "empty successful read",
    "stale/partial data",
    "permission/configuration failure",
    "read error",
)
TASK_ROW_CONTRACT_TERMS = (
    "provenance",
    "source",
    "timestamp",
    "freshness",
    "state",
    "route/reference",
)
TASK_DETAIL_PASSIVE_FIELDS = (
    "task_id",
    "status",
    "title",
    "created_at",
    "updated_at",
    "state_since",
    "actor.kind",
    "actor.id",
    "last_event.id",
    "last_event.type",
    "last_event.emitted_at",
    "last_event.summary",
    "current_step",
    "total_steps",
    "last_agent_action",
    "hint",
    "worktree_lock.held",
    "worktree_lock.by_session_id",
    "worktree_lock.acquired_at",
    "chat_id",
    "reply_to_message_id",
)
TASK_DETAIL_DENIED_FIELDS = (
    "available_commands",
    "next_commands",
    "budget_token_limit",
    "budget_action",
)
TASK_DETAIL_STATE_TERMS = (
    "stale",
    "missing",
    "unauthorized",
    "unavailable",
    "empty successful read",
    "read error",
)
SESSION_RESOURCE_PROVENANCE = (
    "session://active",
    "session://detail/{session_id}",
    "session://heartbeats",
)
SESSION_RESOURCE_NATIVE_FIELDS = (
    "id",
    "task_id",
    "worker_kind",
    "worktree_path",
    "status",
    "started_at",
    "ended_at",
    "last_heartbeat_at",
)
SESSION_DERIVED_UNAVAILABLE_FIELDS = (
    "freshness_state",
    "source",
    "trace_id",
)
SESSION_STATE_TERMS = (
    "no active sessions",
    "active session",
    "historical session",
    "terminal session outcome",
    "heartbeat/stale warning",
    "loading",
    "unavailable pending dashboard read contract",
    "empty successful read",
    "read error",
    "unauthorized/configuration failure",
    "stale data",
)


class StaticDashboardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.data: list[str] = []
        self.banner_data: list[str] = []
        self.sections: dict[str, list[str]] = {}
        self.nav_hrefs: list[str] = []
        self.section_hrefs: dict[str, list[str]] = {}
        self.section_attrs: dict[str, list[str]] = {}
        self._section_stack: list[str] = []
        self._banner_depth = 0
        self._nav_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        attrs_dict = dict(attrs)
        if self._nav_depth:
            self._nav_depth += 1
        elif tag == "nav":
            self._nav_depth = 1
        if self._banner_depth:
            self._banner_depth += 1
        elif tag == "aside" and attrs_dict.get("aria-label") == "Read-only dashboard boundary":
            self._banner_depth = 1
        if tag == "section" and attrs_dict.get("id"):
            self._section_stack.append(attrs_dict["id"] or "")
        if self._section_stack:
            self.section_attrs.setdefault(self._section_stack[-1], []).extend(
                f"{tag}[{name}]={value or ''}" for name, value in attrs
            )
        if tag == "a" and attrs_dict.get("href"):
            href = attrs_dict["href"] or ""
            if self._nav_depth:
                self.nav_hrefs.append(href)
            if self._section_stack:
                self.section_hrefs.setdefault(self._section_stack[-1], []).append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self._section_stack:
            self._section_stack.pop()
        if self._banner_depth:
            self._banner_depth -= 1
        if self._nav_depth:
            self._nav_depth -= 1

    def handle_data(self, data: str) -> None:
        stripped = " ".join(data.split())
        if not stripped:
            return
        self.data.append(stripped)
        if self._banner_depth:
            self.banner_data.append(stripped)
        if self._section_stack:
            self.sections.setdefault(self._section_stack[-1], []).append(stripped)


def parse_dashboard() -> StaticDashboardParser:
    parser = StaticDashboardParser()
    parser.feed(DASHBOARD.read_text(encoding="utf-8"))
    return parser


def dashboard_text(parser: StaticDashboardParser) -> str:
    return " ".join(parser.data)


def banner_text(parser: StaticDashboardParser) -> str:
    return " ".join(parser.banner_data)


def sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in text.split(".") if sentence.strip()]


def clause_after(text: str, prefix: str) -> str:
    start = text.index(prefix) + len(prefix)
    end = text.index(".", start)
    return text[start:end].strip()


def comma_list_clause(text: str, prefix: str) -> set[str]:
    clause = clause_after(text, prefix)
    normalized = clause.replace(" and ", ", ")
    return {item.strip(" ,") for item in normalized.split(",") if item.strip(" ,")}


def test_static_dashboard_file_exists_and_uses_safe_tags() -> None:
    assert DASHBOARD.exists()
    parser = parse_dashboard()
    assert not (set(parser.tags) & FORBIDDEN_TAGS)
    assert "main" in parser.tags
    assert "nav" in parser.tags


def test_banner_contains_full_read_only_semantics() -> None:
    text = banner_text(parse_dashboard())
    assert "Read-only visibility surface" in text
    assert "unsafe or unavailable reads render unavailable states" in text
    assert "mutation/control operations are not available in this dashboard" in text


def test_no_live_api_or_browser_runtime_wiring() -> None:
    raw = DASHBOARD.read_text(encoding="utf-8")
    for marker in LIVE_API_MARKERS:
        assert marker not in raw


def test_required_panels_have_local_unavailable_and_provenance_placeholders() -> None:
    parser = parse_dashboard()
    assert set(REQUIRED_PANELS).issubset(parser.sections)
    for panel_id in REQUIRED_PANELS:
        assert f"#{panel_id}" in parser.nav_hrefs
    for panel_id, heading in REQUIRED_PANELS.items():
        panel_text = " ".join(parser.sections[panel_id]).lower()
        assert heading.lower() in panel_text
        assert any(
            term in panel_text for term in ("unavailable", "not yet approved", "not wired")
        ), panel_id
        assert "source" in panel_text, panel_id
        assert "freshness" in panel_text or "timestamp" in panel_text, panel_id
        assert (
            "reference" in panel_text
            or "trace" in panel_text
            or "event" in panel_text
            or "session" in panel_text
        ), panel_id
        assert "confidence" in panel_text, panel_id


def test_data_panels_distinguish_unavailable_from_empty_success() -> None:
    parser = parse_dashboard()
    data_panels = ["tasks", "sessions", "events", "traces", "replay-lifecycle-readiness", "health"]
    for panel_id in data_panels:
        panel_text = " ".join(parser.sections[panel_id]).lower()
        assert "unavailable read" in panel_text, panel_id
        assert "empty successful read" in panel_text, panel_id


def test_story_89_1_overview_and_tasks_use_explicit_aggregate_unavailable_fallback() -> None:
    parser = parse_dashboard()
    for panel_id in ("overview", "tasks"):
        panel_text = " ".join(parser.sections[panel_id]).lower()
        assert "aggregate task" in panel_text, panel_id
        assert "safe aggregate task read" in panel_text, panel_id
        assert "unavailable" in panel_text, panel_id
        assert "no safe aggregate task read is approved or wired" in panel_text, panel_id
        assert "empty successful read" in panel_text, panel_id
        assert "audit" in panel_text, panel_id
        assert "help" in panel_text, panel_id
        assert "#audit" in parser.section_hrefs.get(panel_id, []), panel_id
        assert "#help" in parser.section_hrefs.get(panel_id, []), panel_id


def test_story_89_1_overview_and_tasks_name_full_state_matrix() -> None:
    parser = parse_dashboard()
    for panel_id in ("overview", "tasks"):
        panel_text = " ".join(parser.sections[panel_id]).lower()
        for term in STORY_89_1_STATE_TERMS:
            assert term in panel_text, (panel_id, term)


def test_story_89_1_task_list_keeps_future_row_contract_without_synthesized_rows() -> None:
    parser = parse_dashboard()
    tasks_text = " ".join(parser.sections["tasks"]).lower()
    for term in TASK_ROW_CONTRACT_TERMS:
        assert term in tasks_text, term
    assert "no task rows are synthesized" in tasks_text
    assert "literal live route" not in tasks_text


def test_story_89_2_task_detail_uses_inert_approved_route_provenance() -> None:
    parser = parse_dashboard()
    task_detail = " ".join(parser.sections["task-detail"]).lower()
    assert APPROVED_TASK_DETAIL_ROUTE.lower() in task_detail
    assert "inert provenance" in task_detail
    assert "not live wiring" in task_detail
    assert "no backend route" in task_detail
    assert "no dependency" in task_detail


def test_story_89_2_task_detail_lists_only_passive_field_contract() -> None:
    parser = parse_dashboard()
    task_detail = " ".join(parser.sections["task-detail"]).lower()
    for field in TASK_DETAIL_PASSIVE_FIELDS:
        assert field in task_detail, field
    for field in TASK_DETAIL_DENIED_FIELDS:
        assert field not in task_detail, field
    assert "command field" not in task_detail
    assert "budget policy" not in task_detail
    assert "lifecycle field" not in task_detail


def test_story_89_2_task_detail_state_and_session_scope_are_explicit() -> None:
    parser = parse_dashboard()
    task_detail = " ".join(parser.sections["task-detail"]).lower()
    for term in TASK_DETAIL_STATE_TERMS:
        assert term in task_detail, term
    assert "worktree_lock" in task_detail
    assert "task-local session reference" in task_detail
    assert "broader session metadata" in task_detail
    assert "heartbeat" in task_detail
    assert "history" in task_detail
    assert "aggregation" in task_detail
    assert "deferred to story 89.3" in task_detail


def test_story_89_2_thread_metadata_is_passive_and_unavailable_when_absent() -> None:
    parser = parse_dashboard()
    task_detail = " ".join(parser.sections["task-detail"]).lower()
    assert "chat_id" in task_detail
    assert "reply_to_message_id" in task_detail
    assert "passive thread metadata" in task_detail
    assert "not configured" in task_detail
    assert "message sending" not in task_detail
    assert "notification control" not in task_detail


def test_story_89_3_sessions_panel_declares_safe_mcp_resource_provenance() -> None:
    parser = parse_dashboard()
    sessions_text = " ".join(parser.sections["sessions"]).lower()
    session_attrs = " ".join(parser.section_attrs.get("sessions", [])).lower()
    for resource in SESSION_RESOURCE_PROVENANCE:
        assert resource in sessions_text, resource
        assert resource not in session_attrs, resource
    assert "existing mcp read resources" in sessions_text
    assert "inert visible provenance" in sessions_text
    assert "no live dashboard wiring" in sessions_text


def test_story_89_3_sessions_panel_lists_passive_row_contract() -> None:
    parser = parse_dashboard()
    sessions_text = " ".join(parser.sections["sessions"]).lower()
    native_fields = comma_list_clause(sessions_text, "resource-native session fields are:")
    assert native_fields == set(SESSION_RESOURCE_NATIVE_FIELDS)
    assert "session_id is a display label for resource-native id" in sessions_text
    assert "not a separate resource field" in sessions_text
    uri_template_sentence = next(
        sentence
        for sentence in sentences(sessions_text)
        if "session://detail/{session_id}" in sentence
    )
    assert "display label for resource-native id" not in uri_template_sentence
    derived_fields = comma_list_clause(
        sessions_text, "derived/provenance/unavailable-only semantics are"
    )
    assert derived_fields == set(SESSION_DERIVED_UNAVAILABLE_FIELDS)
    assert "derived/provenance/unavailable-only semantics" in sessions_text
    assert "visibility placeholders only" in sessions_text
    assert "no links or session actions appear here" in sessions_text
    for term in CONTROL_TERMS:
        assert term not in sessions_text, term


def test_story_89_3_sessions_panel_states_and_unavailable_contract_are_explicit() -> None:
    parser = parse_dashboard()
    sessions_text = " ".join(parser.sections["sessions"]).lower()
    for term in SESSION_STATE_TERMS:
        assert term in sessions_text, term
    assert "loading is not active in this static, not-wired slice" in sessions_text
    assert "dashboard-consumable session http route" in sessions_text
    assert "aggregate session list" in sessions_text
    assert "aggregate historical-session list/search/read route" in sessions_text
    assert "live polling" in sessions_text
    assert (
        "historical session and terminal session outcome wording is explanatory only"
        in sessions_text
    )
    assert "does not authorize session history enumeration" in sessions_text


def test_control_terms_are_negative_safety_copy_only() -> None:
    parser = parse_dashboard()
    page_text = dashboard_text(parser).lower()
    boundary_text = banner_text(parser).lower()
    boundary_sentences = sentences(boundary_text)
    assert "control operations are not available" in boundary_text
    assert "control affordances are absent" in boundary_text
    for term in CONTROL_TERMS:
        assert term in boundary_text, term
        assert page_text.count(term) == boundary_text.count(term), term
        assert any(
            term in sentence and "affordances are absent" in sentence
            for sentence in boundary_sentences
        ), term
