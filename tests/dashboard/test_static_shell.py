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
APPROVED_EVENT_ROUTE = "GET /v1/tasks/{task_id}/events"
APPROVED_TRANSITION_ROUTE = "GET /v1/tasks/{task_id}/transitions"
EVENT_TIMELINE_FIELDS = (
    "event_id",
    "schema_version",
    "type",
    "emitted_at",
    "emitted_at_monotonic_ns",
    "actor.kind",
    "actor.id",
    "session_id",
    "payload",
    "parent_event_id",
    "trace_id",
    "request_id",
)
TRANSITION_TIMELINE_FIELDS = (
    "event_id",
    "from_state",
    "to_state",
    "trigger_event",
    "worker_id",
    "timestamp",
    "emitted_at",
    "emitted_at_monotonic_ns",
    "trace_id",
)
EVENT_TIMELINE_STATE_TERMS = (
    "empty history",
    "missing task",
    "unauthorized access",
    "stale data",
    "route failure/read error",
    "unavailable unsupported timeline segment",
    "loading",
    "empty successful read",
)
EVENT_SECTION_LIVE_MARKERS = (
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
    "poll",
    "live stream",
)

APPROVED_TRACE_ROUTE = "GET /v1/trace/{trace_id}"
TRACE_METADATA_SLOTS = (
    "trace_source",
    "retrieved_at",
    "linked_event_id",
    "linked_task_id",
    "linked_session_id",
    "parent_event_id",
    "request_id",
    "freshness",
)
TRACE_PRESENTATION_FIELDS = (
    "event_id",
    "schema_version",
    "type",
    "emitted_at",
    "emitted_at_monotonic_ns",
    "actor.kind",
    "actor.id",
    "task_id",
    "session_id",
    "payload",
    "extensions",
    "parent_event_id",
    "trace_id",
    "trace_id_synthetic_source",
    "request_id",
)
TRACE_QUERY_AND_HEADER_FIELDS = (
    "trace_id",
    "limit",
    "after_event_id",
    "X-Trace-Truncated",
)
TRACE_STATE_TERMS = (
    "unavailable read",
    "loading",
    "empty successful read",
    "absent trace",
    "unknown trace",
    "invalid trace id shape",
    "unauthorized/configuration failure",
    "stale data",
    "truncated/paginated result",
    "payload/extension parse fallback",
    "route failure/read error",
)
TRACE_SECTION_LIVE_MARKERS = (
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
    "poll",
    "live stream",
    "data-endpoint",
    "data-route",
    "hx-get",
)
TRACE_TRUNCATE_ACTION_PHRASES = (
    "truncate trace",
    "truncate events",
    "truncate button",
    "truncate action",
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
        self.section_lists: dict[str, dict[str, list[str]]] = {}
        self._section_stack: list[str] = []
        self._list_stack: list[tuple[str, str]] = []
        self._li_buffer: list[str] | None = None
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
        if tag in {"ul", "ol"} and self._section_stack and attrs_dict.get("aria-label"):
            section_id = self._section_stack[-1]
            list_label = (attrs_dict["aria-label"] or "").lower()
            self._list_stack.append((section_id, list_label))
            self.section_lists.setdefault(section_id, {}).setdefault(list_label, [])
        if tag == "li" and self._list_stack:
            self._li_buffer = []
        if tag == "a" and attrs_dict.get("href"):
            href = attrs_dict["href"] or ""
            if self._nav_depth:
                self.nav_hrefs.append(href)
            if self._section_stack:
                self.section_hrefs.setdefault(self._section_stack[-1], []).append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._list_stack and self._li_buffer is not None:
            section_id, list_label = self._list_stack[-1]
            item = " ".join(self._li_buffer).strip()
            if item:
                self.section_lists[section_id][list_label].append(item)
            self._li_buffer = None
        if tag in {"ul", "ol"} and self._list_stack:
            self._list_stack.pop()
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
        if self._li_buffer is not None:
            self._li_buffer.append(stripped)
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
    return {item.strip(" ,.") for item in normalized.split(",") if item.strip(" ,.")}


def passive_trace_truncate_remainder(text: str) -> str:
    remainder_parts: list[str] = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        remainder = lowered
        for phrase in ("x-trace-truncated", "truncated/paginated result"):
            remainder = remainder.replace(phrase, "")
        remainder_parts.append(remainder)
    return " ".join(remainder_parts)


def test_passive_trace_truncate_helper_keeps_mixed_actionable_remainder() -> None:
    mixed = "X-Trace-Truncated is passive protocol text, but truncate action is offered."
    assert "truncate action" in passive_trace_truncate_remainder(mixed.lower())


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


def test_story_90_1_events_panel_declares_safe_route_provenance() -> None:
    parser = parse_dashboard()
    events_text = " ".join(parser.sections["events"]).lower()
    event_attrs = " ".join(parser.section_attrs.get("events", [])).lower()
    event_hrefs = " ".join(parser.section_hrefs.get("events", [])).lower()
    for route in (APPROVED_EVENT_ROUTE, APPROVED_TRANSITION_ROUTE):
        route_text = route.lower()
        assert route_text in events_text, route
        assert route_text not in event_attrs, route
        assert route_text not in event_hrefs, route
    assert "inert visible provenance" in events_text
    assert "no live dashboard wiring" in events_text
    provenance_sentence = next(
        sentence for sentence in sentences(events_text) if "inert visible provenance" in sentence
    )
    assert (
        "not links, attributes, automatic-refresh sources, or client calls" in provenance_sentence
    )
    assert "fetch" not in provenance_sentence
    assert "poll" not in provenance_sentence


def test_story_90_1_events_panel_lists_passive_timeline_contracts() -> None:
    parser = parse_dashboard()
    events_text = " ".join(parser.sections["events"]).lower()
    event_lists = parser.section_lists["events"]
    assert set(event_lists["raw event envelope fields"]) == set(EVENT_TIMELINE_FIELDS)
    assert set(event_lists["transition fields"]) == set(TRANSITION_TIMELINE_FIELDS)
    assert "payload.summary is a derived visible summary from payload" in events_text
    assert "no event rows or transition rows are synthesized" in events_text


def test_story_90_1_events_panel_states_and_unavailable_contract_are_explicit() -> None:
    parser = parse_dashboard()
    events_text = " ".join(parser.sections["events"]).lower()
    for term in EVENT_TIMELINE_STATE_TERMS:
        assert term in events_text, term
    assert "timeline aggregation" in events_text
    assert "separate read contract" in events_text
    assert "problemdetails" in events_text
    assert "planned unavailable/deferred summary" in events_text
    assert "loading is not active in this static, not-wired slice" in events_text
    assert "unavailable read" in events_text
    assert "empty successful read" in events_text


def test_story_90_1_events_panel_has_no_section_local_control_or_live_source_affordances() -> None:
    parser = parse_dashboard()
    events_text = " ".join(parser.sections["events"]).lower()
    for term in CONTROL_TERMS:
        assert term not in events_text, term
    for marker in EVENT_SECTION_LIVE_MARKERS:
        assert marker not in events_text, marker
    assert "story 90.2" not in events_text
    assert "trace correlation panel" not in events_text


def test_story_90_1_events_panel_denies_timeline_refresh_side_effects() -> None:
    parser = parse_dashboard()
    events_text = " ".join(parser.sections["events"]).lower()
    event_attrs = " ".join(parser.section_attrs.get("events", [])).lower()
    assert "cannot append events" in events_text
    assert "trigger replay" in events_text
    assert "create snapshots" in events_text
    assert "dispatch background jobs" in events_text
    assert "plain explanatory text" in events_text
    for phrase in ("append events", "trigger replay", "create snapshots", "background jobs"):
        assert phrase not in event_attrs, phrase


def test_story_90_2_traces_panel_declares_safe_route_provenance() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    trace_attrs = " ".join(parser.section_attrs.get("traces", [])).lower()
    trace_hrefs = " ".join(parser.section_hrefs.get("traces", [])).lower()
    assert APPROVED_TRACE_ROUTE.lower() in traces_text
    assert APPROVED_TRACE_ROUTE.lower() not in trace_attrs
    assert APPROVED_TRACE_ROUTE.lower() not in trace_hrefs
    assert "inert visible provenance" in traces_text
    assert "no live dashboard wiring" in traces_text
    provenance_sentence = next(
        sentence for sentence in sentences(traces_text) if "inert visible provenance" in sentence
    )
    assert (
        "not links, attributes, automatic-refresh sources, or client calls" in provenance_sentence
    )


def test_story_90_2_traces_panel_shows_required_trace_metadata_slots() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    trace_lists = parser.section_lists["traces"]
    assert tuple(trace_lists["trace metadata slots"]) == TRACE_METADATA_SLOTS
    assert "trace_source is the existing route provenance" in traces_text
    assert (
        "retrieved_at and freshness are visible slots with unavailable static-shell values"
        in traces_text
    )
    assert "event_id maps to linked_event_id" in traces_text
    assert "task_id maps to linked_task_id" in traces_text
    assert "session_id maps to linked_session_id" in traces_text


def test_story_90_2_traces_panel_lists_passive_trace_contracts() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    trace_lists = parser.section_lists["traces"]
    assert tuple(trace_lists["trace event presentation fields"]) == TRACE_PRESENTATION_FIELDS
    assert tuple(trace_lists["trace query parameters and headers"]) == TRACE_QUERY_AND_HEADER_FIELDS
    assert "presentation view over event rows" in traces_text
    assert "not canonical jsonl replay" in traces_text
    assert "x-trace-truncated is a passive response header" in traces_text
    assert "not a dashboard action" in traces_text


def test_story_90_2_traces_panel_states_and_deferred_contract_are_explicit() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    for term in TRACE_STATE_TERMS:
        assert term in traces_text, term
    assert "truncated/paginated result is a passive read state" in traces_text
    assert "broader trace search/list requires a separate approved read contract" in traces_text
    assert (
        "canonical jsonl replay/byte-equivalent envelope display is a separate contract"
        in traces_text
    )


def test_story_90_2_traces_panel_explains_causality_without_generating_graphs() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    assert "ordered trace rows explain related events and causal links" in traces_text
    assert "parent_event_id" in traces_text
    assert "linked_event_id" in traces_text
    assert "linked_task_id" in traces_text
    assert "route provenance" in traces_text
    assert "does not generate a new causal graph" in traces_text


def test_story_90_2_traces_panel_has_no_section_local_control_or_live_source_affordances() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    trace_attrs = " ".join(parser.section_attrs.get("traces", [])).lower()
    trace_hrefs = " ".join(parser.section_hrefs.get("traces", [])).lower()
    sanitized_text = passive_trace_truncate_remainder(traces_text)
    for marker in TRACE_SECTION_LIVE_MARKERS:
        assert marker not in traces_text, marker
        assert marker not in trace_attrs, marker
        assert marker not in trace_hrefs, marker
    for term in CONTROL_TERMS:
        if term == "truncate":
            assert term not in sanitized_text, term
        else:
            assert term not in traces_text, term
        assert term not in trace_attrs, term
        assert term not in trace_hrefs, term
    for phrase in TRACE_TRUNCATE_ACTION_PHRASES:
        assert phrase not in traces_text, phrase


def test_story_90_2_traces_panel_denies_trace_mutation_and_scope_bleed() -> None:
    parser = parse_dashboard()
    traces_text = " ".join(parser.sections["traces"]).lower()
    assert "denies mutation routes" in traces_text
    assert "background replay actions" in traces_text
    assert "snapshot actions" in traces_text
    assert "hidden writes" in traces_text
    assert "cache-warming/read-side effects" in traces_text
    assert "story 90.3" not in traces_text
    assert "replay/lifecycle implementation" not in traces_text


def test_control_terms_are_negative_safety_copy_only() -> None:
    parser = parse_dashboard()
    page_text = dashboard_text(parser).lower()
    boundary_text = banner_text(parser).lower()
    boundary_sentences = sentences(boundary_text)
    assert "control operations are not available" in boundary_text
    assert "control affordances are absent" in boundary_text
    for term in CONTROL_TERMS:
        assert term in boundary_text, term
        comparable_page_text = page_text
        if term == "truncate":
            comparable_page_text = passive_trace_truncate_remainder(comparable_page_text)
        assert comparable_page_text.count(term) == boundary_text.count(term), term
        assert any(
            term in sentence and "affordances are absent" in sentence
            for sentence in boundary_sentences
        ), term
