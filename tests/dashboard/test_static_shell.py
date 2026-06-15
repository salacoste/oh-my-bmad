from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

DASHBOARD = Path("dashboard/static/index.html")

REQUIRED_PANELS = {
    "overview": "Overview",
    "tasks": "Tasks",
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
    "/v1/",
    "http://",
    "https://",
)
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


class StaticDashboardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.data: list[str] = []
        self.banner_data: list[str] = []
        self.sections: dict[str, list[str]] = {}
        self.nav_hrefs: list[str] = []
        self.section_hrefs: dict[str, list[str]] = {}
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
