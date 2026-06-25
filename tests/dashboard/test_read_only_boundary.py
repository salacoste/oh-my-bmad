from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from posixpath import normpath
from urllib.parse import unquote

DASHBOARD_ROOT = Path("dashboard/static")

CORE_APPROVED_READ_ROUTES = frozenset(
    {
        ("GET", "/v1/tasks/{task_id}"),
        ("GET", "/v1/tasks/{task_id}/events"),
        ("GET", "/v1/tasks/{task_id}/transitions"),
        ("GET", "/v1/trace/{trace_id}"),
        ("GET", "/v1/tasks/{task_id}/history"),
        ("GET", "/v1/events/replay"),
        ("GET", "/v1/events/replay/validate"),
        ("GET", "/v1/health"),
    }
)
OPTIONAL_NON_CORE_READ_ROUTES = frozenset(
    {
        # Non-core for the static dashboard MVP: the architecture notes this digest may
        # call an LLM adapter and can add latency or external-service dependency risk.
        ("GET", "/v1/tasks/{task_id}/logs/digest"),
    }
)
FORBIDDEN_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CONTROL_TAGS = frozenset(
    {"form", "button", "input", "select", "textarea", "menu", "menuitem", "dialog"}
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
APPROVED_HEALTH_RUNTIME_SCRIPT = "health-readiness.js"
APPROVED_TASK_DETAIL_RUNTIME_SCRIPT = "task-detail.js"
APPROVED_EVENT_RUNTIME_SCRIPT = "event-timeline.js"
APPROVED_TRACE_RUNTIME_SCRIPT = "trace-correlation.js"
APPROVED_HISTORY_REPLAY_RUNTIME_SCRIPT = "history-replay.js"
APPROVED_LIFECYCLE_RUNTIME_SCRIPT = "lifecycle-snapshot.js"
APPROVED_RUNTIME_SCRIPTS = {
    APPROVED_HEALTH_RUNTIME_SCRIPT,
    APPROVED_TASK_DETAIL_RUNTIME_SCRIPT,
    APPROVED_EVENT_RUNTIME_SCRIPT,
    APPROVED_TRACE_RUNTIME_SCRIPT,
    APPROVED_HISTORY_REPLAY_RUNTIME_SCRIPT,
    APPROVED_LIFECYCLE_RUNTIME_SCRIPT,
}

RUNTIME_CALL_MARKERS = (
    "fetch(",
    "xmlhttprequest",
    "websocket",
    "eventsource",
)
HIDDEN_WRITE_OR_BACKGROUND_MARKERS = (
    "localstorage.setitem",
    "sessionstorage.setitem",
    "indexeddb",
    "caches.open",
    "serviceworker.register",
    "sendbeacon",
    "setinterval",
    "settimeout",
    "requestidlecallback",
    "queuemicrotask",
    "navigator.locks",
    "broadcastchannel",
)
WRITE_OR_CONTROL_ROUTE_MARKERS = (
    "/approve",
    "/retry",
    "/cancel",
    "/apply",
    "/prune",
    "/delete",
    "/snapshot",
    "/snapshots/delete",
)
APPROVED_LOCAL_REFERENCE_ROOTS = (
    "_bmad-output/",
    "dashboard/static/",
    "docs/",
    "status/",
)
NETWORK_FETCH_ATTRS = frozenset(
    {
        "action",
        "data",
        "href",
        "poster",
        "src",
        "srcset",
    }
)
NETWORK_FETCH_TAGS = frozenset(
    {"audio", "embed", "iframe", "img", "object", "script", "source", "track", "video"}
)
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(?P<url>.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
FORBIDDEN_METHOD_RE = re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")
NEGATIVE_BOUNDARY_MARKERS = (
    "absent",
    "not available",
    "not a ",
    "unavailable",
    "read-only",
    "without ",
    "no ",
)
POSITIVE_BOUNDARY_RE = re.compile(
    r"\b(?:available\s+soon|click|now|run|soon|start|trigger)\b", re.IGNORECASE
)

PASSIVE_TRACE_TRUNCATE_PHRASES = (
    "x-trace-truncated",
    "truncated/paginated result",
)


@dataclass(frozen=True)
class Context:
    source: str
    text: str


@dataclass
class BoundaryParser(HTMLParser):
    tags: list[str] = field(default_factory=list)
    controls: list[Context] = field(default_factory=list)
    event_handlers: list[Context] = field(default_factory=list)
    form_contexts: list[Context] = field(default_factory=list)
    hrefs: list[Context] = field(default_factory=list)
    data_api_contexts: list[Context] = field(default_factory=list)
    page_load_network_contexts: list[Context] = field(default_factory=list)
    script_contexts: list[Context] = field(default_factory=list)
    style_url_contexts: list[Context] = field(default_factory=list)
    text_contexts: list[Context] = field(default_factory=list)
    boundary_texts: list[str] = field(default_factory=list)
    _script_depth: int = 0
    _style_depth: int = 0
    _script_parts: list[str] = field(default_factory=list)
    _style_parts: list[str] = field(default_factory=list)
    _open_script_tail: str = ""
    _open_style_tail: str = ""
    _boundary_depth: int = 0

    def __post_init__(self) -> None:
        HTMLParser.__init__(self)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in CONTROL_TAGS:
            self.controls.append(Context(f"<{tag}>", render_attrs(attrs_dict)))
        if tag == "script":
            self._script_depth += 1
            self._open_script_tail = "" if attrs_dict.get("src") else self._tail_after_starttag()
        if tag == "style":
            self._style_depth += 1
            self._open_style_tail = self._tail_after_starttag()
        if self._boundary_depth:
            self._boundary_depth += 1
        elif is_read_only_boundary(attrs_dict):
            self._boundary_depth = 1
        for name, value in attrs_dict.items():
            if name.startswith("on"):
                self.event_handlers.append(Context(f"{tag}[{name}]", value))
            if tag == "form" and name in {"action", "method"}:
                self.form_contexts.append(Context(f"form[{name}]", value))
            if name.startswith("data-") and "/v1/" in value:
                self.data_api_contexts.append(Context(f"{tag}[{name}]", value))
            if name == "style":
                self.style_url_contexts.extend(css_url_contexts(f"{tag}[style]", value))
        self._collect_href_context(tag, attrs_dict)
        self._collect_page_load_contexts(tag, attrs_dict)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_depth:
            self._script_depth -= 1
            if self._script_depth == 0:
                self._flush_script_context()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            if self._style_depth == 0:
                self._flush_style_context()
        if self._boundary_depth:
            self._boundary_depth -= 1

    def close(self) -> None:
        super().close()
        self._flush_script_context()
        self._flush_style_context()

    def _tail_after_starttag(self) -> str:
        starttag = self.get_starttag_text() or ""
        if not starttag:
            return ""
        _, separator, tail = self.rawdata.partition(starttag)
        return tail if separator else ""

    def _flush_script_context(self) -> None:
        if not self._script_parts and self._open_script_tail:
            self._script_parts.append(trim_document_tail(self._open_script_tail))
        script = "".join(self._script_parts).strip()
        if script:
            self.script_contexts.append(Context("<script>", script))
        self._script_parts.clear()
        self._open_script_tail = ""
        self._script_depth = 0

    def _flush_style_context(self) -> None:
        if not self._style_parts and self._open_style_tail:
            self._style_parts.append(trim_document_tail(self._open_style_tail))
        style = "".join(self._style_parts)
        self.style_url_contexts.extend(css_url_contexts("<style>", style))
        self._style_parts.clear()
        self._open_style_tail = ""
        self._style_depth = 0

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self._script_parts.append(data)
            return
        if self._style_depth:
            self._style_parts.append(data)
            return
        normalized = " ".join(data.split())
        if not normalized:
            return
        source = "read-only-boundary" if self._boundary_depth else "body"
        self.text_contexts.append(Context(source, normalized))
        if self._boundary_depth:
            self.boundary_texts.append(normalized)

    def _collect_href_context(self, tag: str, attrs: dict[str, str]) -> None:
        href = attrs.get("href")
        if tag == "a" and href is not None:
            self.hrefs.append(Context("a[href]", href))

    def _collect_page_load_contexts(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            self.page_load_network_contexts.append(
                Context("meta[refresh]", attrs.get("content", ""))
            )
        if tag == "link" and attrs.get("href") is not None:
            self.page_load_network_contexts.append(Context("link[href]", attrs.get("href", "")))
        for name, value in attrs.items():
            if name in NETWORK_FETCH_ATTRS and tag in NETWORK_FETCH_TAGS:
                self.page_load_network_contexts.append(Context(f"{tag}[{name}]", value))


def trim_document_tail(text: str) -> str:
    lower = text.lower()
    cut_points = [index for marker in ("</body>", "</html>") if (index := lower.find(marker)) != -1]
    if not cut_points:
        return text
    return text[: min(cut_points)]


def render_attrs(attrs: dict[str, str]) -> str:
    return " ".join(f"{name}={value!r}" for name, value in sorted(attrs.items()))


def is_read_only_boundary(attrs: dict[str, str]) -> bool:
    return attrs.get("aria-label") == "Read-only dashboard boundary"


def css_url_contexts(source: str, css: str) -> list[Context]:
    contexts: list[Context] = []
    for match in CSS_URL_RE.finditer(css):
        contexts.append(Context(f"{source} url(...)", match.group("url").strip()))
    return contexts


def parse_html(raw: str) -> BoundaryParser:
    parser = BoundaryParser()
    parser.feed(raw)
    parser.close()
    return parser


def dashboard_files() -> tuple[Path, ...]:
    return html_files_under(DASHBOARD_ROOT)


def actionable_contexts(parser: BoundaryParser) -> list[Context]:
    non_fragment_hrefs = [context for context in parser.hrefs if not context.text.startswith("#")]
    return [
        *parser.script_contexts,
        *parser.event_handlers,
        *parser.form_contexts,
        *non_fragment_hrefs,
        *parser.data_api_contexts,
    ]


def network_contexts(parser: BoundaryParser) -> list[Context]:
    return [*parser.page_load_network_contexts, *parser.style_url_contexts]


def unexpected_network_contexts(parser: BoundaryParser) -> list[Context]:
    return [
        context
        for context in network_contexts(parser)
        if context.text.strip() not in {"", *APPROVED_RUNTIME_SCRIPTS}
    ]


def runtime_contexts(parser: BoundaryParser) -> list[Context]:
    return [*actionable_contexts(parser), *network_contexts(parser)]


def context_text(contexts: list[Context]) -> str:
    return "\n".join(unquote(context.text) for context in contexts)


def assert_no_api_or_mutating_method_calls(raw: str) -> None:
    parser = parse_html(raw)
    contexts = runtime_contexts(parser)
    text = context_text(contexts).lower()
    for marker in RUNTIME_CALL_MARKERS:
        assert marker not in text, contexts
    assert not FORBIDDEN_METHOD_RE.search(text), contexts
    assert "/v1/" not in text, contexts
    for context in network_contexts(parser):
        assert context.text.strip() in {"", *APPROVED_RUNTIME_SCRIPTS}, context


def assert_no_hidden_write_or_background_markers(raw: str) -> None:
    contexts = runtime_contexts(parse_html(raw))
    text = context_text(contexts).lower()
    for marker in HIDDEN_WRITE_OR_BACKGROUND_MARKERS:
        assert marker not in text, marker
    for marker in WRITE_OR_CONTROL_ROUTE_MARKERS:
        assert marker not in text, marker


def assert_no_control_affordance_mechanics(raw: str) -> None:
    parser = parse_html(raw)
    assert not parser.controls, parser.controls
    assert not parser.event_handlers, parser.event_handlers
    for context in parser.hrefs:
        href = context.text.strip().lower()
        assert not href.startswith(("http://", "https://", "//")), context
        assert ":" not in href, context
        assert "/v1/" not in href, context
        if not (href.startswith("#") or is_safe_local_static_reference(href)):
            assert not any(marker in href for marker in WRITE_OR_CONTROL_ROUTE_MARKERS), context
        assert href.startswith("#") or is_safe_local_static_reference(href), context


def is_safe_local_static_reference(href: str) -> bool:
    allowed_extensions = "css|html|jpeg|jpg|json|md|png|svg|txt|webp|yaml|yml"
    if not re.fullmatch(rf"(?:\./|\.\./)?[a-z0-9_./-]+\.(?:{allowed_extensions})", href):
        return False
    normalized = normalize_local_reference(href)
    return normalized is not None and normalized.startswith(APPROVED_LOCAL_REFERENCE_ROOTS)


def normalize_local_reference(href: str) -> str | None:
    trimmed = href.removeprefix("./")
    while trimmed.startswith("../"):
        trimmed = trimmed[3:]
    normalized = normpath(trimmed)
    if normalized in {".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def assert_control_vocabulary_is_negative_non_actionable(raw: str) -> None:
    parser = parse_html(raw)
    actionable = context_text([*runtime_contexts(parser), *parser.controls]).lower()
    for term in CONTROL_TERMS:
        assert term not in actionable, term
        for context in parser.text_contexts:
            text = context.text.lower()
            if term not in text:
                continue
            if term == "truncate":
                suspicious_sentences = passive_trace_truncate_suspicious_sentences(text)
                if not suspicious_sentences:
                    continue
                assert context.source == "read-only-boundary", (term, context, suspicious_sentences)
                for sentence in suspicious_sentences:
                    assert has_negative_boundary_marker(sentence), (term, sentence, context)
                continue
            assert context.source == "read-only-boundary", (term, context)
            for sentence in sentences_containing(text, term):
                assert has_negative_boundary_marker(sentence), (term, sentence, context)


def passive_trace_truncate_suspicious_sentences(text: str) -> list[str]:
    suspicious: list[str] = []
    for sentence in sentences_containing(text, "truncate"):
        lowered = sentence.lower()
        remainder = lowered
        for phrase in PASSIVE_TRACE_TRUNCATE_PHRASES:
            remainder = remainder.replace(phrase, "")
        remainder = remainder.strip()
        exact_passive = lowered.strip(" .") in PASSIVE_TRACE_TRUNCATE_PHRASES
        if not exact_passive and (
            "truncate" in remainder or not has_negative_boundary_marker(lowered)
        ):
            suspicious.append(remainder or lowered)
    return suspicious


def sentences_containing(text: str, term: str) -> list[str]:
    return [
        match.group(0).strip()
        for match in SENTENCE_RE.finditer(text)
        if term in match.group(0).lower()
    ]


def has_negative_boundary_marker(sentence: str) -> bool:
    lowered = sentence.lower()
    if POSITIVE_BOUNDARY_RE.search(lowered):
        return False
    return any(marker in lowered for marker in NEGATIVE_BOUNDARY_MARKERS)


def html_files_under(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.html")))


def assert_fails(raw: str, assertion_name: str) -> None:
    checks = {
        "api": assert_no_api_or_mutating_method_calls,
        "hidden": assert_no_hidden_write_or_background_markers,
        "controls": assert_no_control_affordance_mechanics,
        "vocabulary": assert_control_vocabulary_is_negative_non_actionable,
    }
    try:
        checks[assertion_name](raw)
    except AssertionError:
        return
    raise AssertionError(f"mutation probe unexpectedly passed: {assertion_name}")


def index_html() -> str:
    return (DASHBOARD_ROOT / "index.html").read_text(encoding="utf-8")


def insert_before_body_end(raw: str, fragment: str) -> str:
    return raw.replace("</body>", f"{fragment}\n</body>")


def test_passive_trace_truncate_allowance_rejects_mixed_actionable_sentence() -> None:
    raw = insert_before_body_end(
        index_html(),
        "<p>X-Trace-Truncated is passive protocol text, but truncate action is offered.</p>",
    )
    assert_fails(raw, "vocabulary")


def test_passive_trace_truncate_allowance_rejects_actionable_header_sentence() -> None:
    raw = insert_before_body_end(index_html(), "<p>Click X-Trace-Truncated now.</p>")
    assert_fails(raw, "vocabulary")


def test_approved_read_route_contract_contains_only_get_methods() -> None:
    assert OPTIONAL_NON_CORE_READ_ROUTES.isdisjoint(CORE_APPROVED_READ_ROUTES)
    for method, route in CORE_APPROVED_READ_ROUTES | OPTIONAL_NON_CORE_READ_ROUTES:
        assert method == "GET", (method, route)
        assert method not in FORBIDDEN_METHODS, (method, route)
        assert route.startswith("/v1/"), route
    assert ("GET", "/v1/tasks/{task_id}/logs/digest") not in CORE_APPROVED_READ_ROUTES


def test_dashboard_static_assets_make_no_api_or_mutating_method_calls() -> None:
    files = dashboard_files()
    assert files
    for html_file in files:
        raw = html_file.read_text(encoding="utf-8")
        parser = parse_html(raw)
        assert not parser.script_contexts, html_file
        assert not actionable_contexts(parser), html_file
        assert not unexpected_network_contexts(parser), html_file
        assert_no_api_or_mutating_method_calls(raw)


def test_dashboard_static_assets_have_no_hidden_write_or_background_job_markers() -> None:
    for html_file in dashboard_files():
        assert_no_hidden_write_or_background_markers(html_file.read_text(encoding="utf-8"))


def test_dashboard_markup_exposes_no_control_affordance_mechanics() -> None:
    for html_file in dashboard_files():
        assert_no_control_affordance_mechanics(html_file.read_text(encoding="utf-8"))


def test_forbidden_control_vocabulary_stays_negative_and_non_actionable() -> None:
    for html_file in dashboard_files():
        assert_control_vocabulary_is_negative_non_actionable(html_file.read_text(encoding="utf-8"))


def test_guard_sensitivity_mutation_probes() -> None:
    raw = index_html()
    assert_fails(
        insert_before_body_end(raw, "<script>fetch('/v1/tasks', {method: 'POST'})</script>"),
        "api",
    )
    malformed_script = insert_before_body_end(raw, "<script>fetch('/v1/tasks',{method:'POST'})")
    assert_fails(malformed_script, "api")
    malformed_script_contexts = parse_html(malformed_script).script_contexts
    assert len(malformed_script_contexts) == 1
    assert malformed_script_contexts[0].text.count("fetch('/v1/tasks'") == 1
    assert "</body>" not in malformed_script_contexts[0].text.lower()
    assert "</html>" not in malformed_script_contexts[0].text.lower()
    assert_fails(
        insert_before_body_end(
            raw, "<script>setInterval(() => fetch('/v1/health'), 1000)</script>"
        ),
        "api",
    )
    assert_fails(
        insert_before_body_end(
            raw, "<script>setInterval(() => fetch('/v1/health'), 1000)</script>"
        ),
        "hidden",
    )
    assert_fails(
        insert_before_body_end(
            raw, "<script>localStorage.setItem('dashboard-cache', 'warm')</script>"
        ),
        "hidden",
    )
    assert_fails(
        insert_before_body_end(
            raw,
            "<script>fetch('/v1/tasks/abc/logs/digest', {method: 'GET'})</script>",
        ),
        "api",
    )
    assert_fails(insert_before_body_end(raw, '<img src="/v1/tasks/abc">'), "api")
    assert_fails(
        insert_before_body_end(
            raw,
            '<meta http-equiv="refresh" content="0;url=/v1/health">',
        ),
        "api",
    )
    assert_fails(
        insert_before_body_end(raw, '<link rel="prefetch" href="/v1/health">'),
        "api",
    )
    assert_fails(
        insert_before_body_end(raw, '<link rel="stylesheet" href="https://example.test/x.css">'),
        "api",
    )
    assert_fails(
        insert_before_body_end(raw, '<link rel="icon" href="https://example.test/favicon.ico">'),
        "api",
    )
    closed_style = raw.replace("</style>", ".probe { background: url('/v1/health'); }\n  </style>")
    assert_fails(closed_style, "api")
    malformed_style = raw.replace("</style>", ".probe { background: url('/v1/health'); }")
    assert_fails(malformed_style, "api")
    malformed_style_contexts = parse_html(malformed_style).style_url_contexts
    api_style_contexts = [
        context for context in malformed_style_contexts if context.text == "/v1/health"
    ]
    assert len(api_style_contexts) == 1
    assert all("</body>" not in context.text.lower() for context in malformed_style_contexts)
    assert all("</html>" not in context.text.lower() for context in malformed_style_contexts)
    assert_fails(insert_before_body_end(raw, "<button>Retry</button>"), "controls")
    assert_fails(insert_before_body_end(raw, '<p onclick="retry()">Retry</p>'), "controls")
    assert_fails(insert_before_body_end(raw, "<p>retry</p>"), "vocabulary")
    assert_fails(raw.replace("retry, cancel", "retry available soon. Retry, cancel"), "vocabulary")
    assert_fails(
        raw.replace("Approval, retry", "No approvals, retry now. Approval, retry"), "vocabulary"
    )
    assert_fails(
        insert_before_body_end(raw, "<p>retry is unavailable in this panel</p>"), "vocabulary"
    )
    assert_fails(insert_before_body_end(raw, '<div class="banner">retry</div>'), "vocabulary")
    assert_no_control_affordance_mechanics(
        insert_before_body_end(
            raw, '<a href="../_bmad-output/planning-artifacts/phase-19-epics.md">Epics</a>'
        )
    )
    assert_fails(
        insert_before_body_end(raw, '<a href="https://example.test/status.md">External</a>'),
        "controls",
    )
    assert_fails(insert_before_body_end(raw, '<a href="/v1/health">API</a>'), "controls")
    assert_no_control_affordance_mechanics(
        insert_before_body_end(raw, '<a href="../status/retry.json">Retry status</a>')
    )
    assert_fails(insert_before_body_end(raw, '<a href="/retry">Retry runtime</a>'), "controls")
    assert_fails(
        insert_before_body_end(raw, '<a href="../tmp/read-only.md">Read-only notes</a>'),
        "controls",
    )
    assert_fails(
        insert_before_body_end(raw, '<a href="../status/../../tmp/read-only.md">Escape</a>'),
        "controls",
    )
    assert_fails(
        insert_before_body_end(raw, '<a href="../../docs/../tmp/retry.json">Escape</a>'),
        "controls",
    )
    assert_fails(
        insert_before_body_end(raw, '<a href="./dashboard/static/../../tmp/x.json">Escape</a>'),
        "controls",
    )

    harmless_route_prose = insert_before_body_end(
        raw, "<p>Documented safe read: GET /v1/health.</p>"
    )
    assert_no_api_or_mutating_method_calls(harmless_route_prose)


def test_html_file_discovery_is_recursive(tmp_path: Path) -> None:
    top_level = tmp_path / "top.html"
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested = nested_dir / "child.html"
    top_level.write_text("<p>top</p>", encoding="utf-8")
    nested.write_text("<p>child</p>", encoding="utf-8")

    assert set(html_files_under(tmp_path)) == {top_level, nested}
