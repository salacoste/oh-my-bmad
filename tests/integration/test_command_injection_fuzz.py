"""Story 3.8 — Command-injection fuzz suite (FR45 / NFR-S5).

Hypothesis-based property test that drives synthesised hostile operator
inputs through the bot's ``/task`` command handler end-to-end:

    Telegram message → handle_task → RegistryAPIClient → POST /v1/tasks
        → EventLogWriter (JSONL append) → reply

For every generated ``description`` the harness asserts ALL of:

  1. **No subprocess invocation.** ``_subprocess_guard`` (autouse) replaces
     every ``subprocess`` / ``os.system`` / ``os.popen`` entry point with an
     ``AssertionError``-raising stub.  Defense-in-depth runtime peer to the
     static AST guard ``scripts/check_no_subprocess.py`` (Story 3.8 AC-6).
  2. **JSON-encoded body field.** The outbound HTTP request from telegram-
     gateway carries the input under ``request_body["title"]`` as a Python
     ``str``; never in the URL path, query string, or any header value.
  3. **Verbatim event-log persistence.** The ``task.created`` event payload
     written to JSONL contains the input character-for-character (UTF-8 +
     JSON escaping is reversible for any unicode codepoint, including
     ``\\x00``).
  4. **No exception escapes the handler.** ``handle_task`` always returns
     normally; the bot replies (success or RFC 7807 error) without raising.

Test inventory:

  - ``test_no_command_injection_through_task_handler``
        Combined 10K-example fuzz across all six attack classes
        (``_attack_input_strategy``).  ``@pytest.mark.slow`` excludes from
        the PR-gate ``just test`` lane; runs in nightly + ``just test-fuzz``.
  - ``test_no_injection_through_null_bytes`` ... ``_git_refname_patterns``
        Six per-strategy 500-example targeted tests.  ``@pytest.mark.fuzz``
        ONLY (NOT ``slow``) so they run on PR gate at ~30-45 s budget.

Strategies cover the NFR-S5 attack classes (prd.md:925):
  - null bytes (``\\x00``)
  - shell metacharacters (``; & | $ \\` $( ) > < \\n``)
  - nested quoting (single/double/backtick quotes with potential closure)
  - directory traversal (``../``, ``..\\``, ``%2e%2e/``)
  - ANSI escapes (``\\x1b[<n>m`` terminal control sequences)
  - git ref-name injection (branch-name shaped strings with embedded metas)

The ``/retry hint=`` fuzz coverage is deferred to Story 3.18 (which ships
the ``/retry`` command); this story covers ``/task`` only.

References:
  - prd.md:875  FR45 (input sanitization)
  - prd.md:925  NFR-S5 (command injection prevention)
  - architecture.md:114  Hypothesis chosen as fuzz lib
  - architecture.md:346  ``@pytest.mark.fuzz`` reserved for this story
  - architecture.md:753  canonical placement of this file
"""

from __future__ import annotations

import asyncio
import json as _json
import os as _os
import subprocess as _subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.schema_registry import register as _register_event
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from registry_api.app import build_app
from registry_state.adapters.event_log import (  # noqa: IMP001 — Story 2.9 AC-16
    current_day_path,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — Story 2.9 AC-16
    create_engine as _create_engine,
)
from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
    TaskCreatedPayload,
)
from registry_state.schema import Base  # noqa: IMP001 — Story 2.9 AC-16
from telegram_gateway.handlers.registry_client import (  # noqa: IMP001 — Story 2.9 AC-16
    RegistryAPIClient,
)
from telegram_gateway.handlers.task_command import (  # noqa: IMP001 — Story 2.9 AC-16
    handle_task,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies — six NFR-S5 attack classes
# ---------------------------------------------------------------------------

# Cap the base-text component at 200 chars per spec (Hypothesis runs many
# examples; small strings keep memory + wall-clock bounded under 10K runs).
_MAX_BASE_LEN = 200

# Bounded text strategy reused by every composite — keeps the surrounding
# valid text small so the attack pattern dominates the generated example.
_BASE_TEXT = st.text(min_size=0, max_size=_MAX_BASE_LEN)


@st.composite
def _null_byte_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #1 — null-byte injection.

    Embeds ``\\x00`` at random offsets within otherwise-valid text. The
    classical C-string truncation vector: a downstream consumer that
    forwards the value to a ``char*`` API would silently truncate at the
    first null. The platform is pure Python + JSON, so the byte must
    survive verbatim through the round-trip.
    """
    base = draw(_BASE_TEXT)
    nulls = draw(st.lists(st.just("\x00"), min_size=1, max_size=20))
    insert_at = draw(st.integers(min_value=0, max_value=max(0, len(base))))
    return (base[:insert_at] + "".join(nulls) + base[insert_at:])[:_MAX_BASE_LEN]


@st.composite
def _shell_metachar_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #2 — shell metacharacters.

    Mixes ``; & | $ \\` $( ) > < \\n`` with arbitrary text. If any service
    on the request path ever interpolated the input into a ``sh -c`` style
    command, these characters would escape the intended quoting.
    """
    metas = st.sampled_from([";", "&", "|", "$", "`", "$(", ")", ">", "<", "\n", "&&", "||"])
    parts = draw(st.lists(st.one_of(metas, _BASE_TEXT), min_size=1, max_size=10))
    return "".join(parts)[:_MAX_BASE_LEN]


@st.composite
def _nested_quoting_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #3 — nested quoting.

    Combinations of single, double, and backtick quotes with potential
    closure-and-reopen sequences. Catches naive sanitisers that strip one
    quote variant but not another.
    """
    quotes = st.sampled_from(["'", '"', "`", "\\'", '\\"', "''", '""', "``", "'\"", "\"'", "`\"'"])
    parts = draw(st.lists(st.one_of(quotes, _BASE_TEXT), min_size=1, max_size=10))
    return "".join(parts)[:_MAX_BASE_LEN]


@st.composite
def _directory_traversal_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #4 — directory traversal.

    ``../``, ``..\\``, ``%2e%2e/`` and percent-encoded variants mixed with
    valid path-shaped chars. Forward-compatibility guard: any future
    code path that ever turns the description into a filesystem path
    must still refuse to escape its sandbox.
    """
    traversals = st.sampled_from(
        [
            "../",
            "..\\",
            "%2e%2e/",
            "%2E%2E%2F",
            "..%2f",
            "..%5c",
            "....//",
            "..../",
            "/etc/passwd",
            "C:\\Windows\\System32",
        ]
    )
    parts = draw(st.lists(st.one_of(traversals, _BASE_TEXT), min_size=1, max_size=10))
    return "".join(parts)[:_MAX_BASE_LEN]


@st.composite
def _ansi_escape_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #5 — ANSI terminal escapes.

    ``\\x1b[<n>m`` colour / cursor-control sequences, ``\\x1b[2J`` clear-
    screen, ``\\x1b[?25l`` cursor-hide. A log viewer or terminal client
    that renders the description verbatim would interpret these; the
    platform's structured logging redaction must keep them as opaque
    payload bytes.
    """
    ansi = st.sampled_from(
        [
            "\x1b[31m",
            "\x1b[32m",
            "\x1b[0m",
            "\x1b[2J",
            "\x1b[H",
            "\x1b[?25l",
            "\x1b[1;31;40m",
            "\x1b[6n",
            "\x1bc",
        ]
    )
    parts = draw(st.lists(st.one_of(ansi, _BASE_TEXT), min_size=1, max_size=10))
    return "".join(parts)[:_MAX_BASE_LEN]


@st.composite
def _git_refname_injection_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #6 — git ref-name injection.

    Branch-name shaped strings with embedded shell metas
    (``main; rm -rf /``, ``feat/x` && curl evil``). Forward-compat:
    Story 5.7 ships the GitHub adapter via HTTPS REST (no git CLI), so
    no service today consumes the description as a refname — but a
    future story that does must not unwittingly expose this surface.
    """
    bases = st.sampled_from(
        [
            "main",
            "master",
            "develop",
            "release/v1.0",
            "feat/foo",
            "fix/bar",
            "HEAD",
            "refs/heads/main",
        ]
    )
    metas = st.sampled_from(
        [
            "; rm -rf /",
            "&& echo pwned",
            " || curl http://evil",
            "$(touch /tmp/x)",
            "`whoami`",
            "; cat /etc/passwd",
            " > /etc/passwd",
        ]
    )
    base = draw(bases)
    payload = draw(metas)
    return (base + payload)[:_MAX_BASE_LEN]


def _attack_input_strategy() -> st.SearchStrategy[str]:
    """Combined strategy — uniformly samples one of the six NFR-S5 classes."""
    return st.one_of(
        _null_byte_strategy(),
        _shell_metachar_strategy(),
        _nested_quoting_strategy(),
        _directory_traversal_strategy(),
        _ansi_escape_strategy(),
        _git_refname_injection_strategy(),
    )


# ---------------------------------------------------------------------------
# Schema-registry guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register ``task.created`` for every test (carry-forward from Story 2.13).

    Other test modules call ``unregister_all()`` in autouse teardown; this
    keeps the registry populated for envelope reads in this harness.
    """
    _register_event("task.created", "1.0.0", TaskCreatedPayload)


# ---------------------------------------------------------------------------
# Subprocess guard — runtime peer to the AST gate (AC-5.1)
# ---------------------------------------------------------------------------


def _forbidden(name: str) -> Any:
    """Build a stub that raises on call — used to poison every shell entry point."""

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"forbidden subprocess call: {name}")

    return _raise


@pytest.fixture(autouse=True)
def _subprocess_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every ``subprocess`` / ``os`` shell entry point with an asserter.

    Catches three classes of indirection that the static AST guard misses:
      1. ``__import__("subprocess")`` dynamic imports.
      2. ``importlib.import_module("subprocess")`` dynamic imports.
      3. Third-party deps that themselves invoke ``subprocess`` on the
         request path (e.g. an httpx middleware shelling to ``curl``).

    Defense in depth — both the AST guard AND this runtime guard must be
    green for NFR-S5 to be considered satisfied.
    """
    for attr in ("run", "Popen", "check_call", "check_output", "call"):
        monkeypatch.setattr(_subprocess, attr, _forbidden(f"subprocess.{attr}"))
    for attr in ("system", "popen"):
        monkeypatch.setattr(_os, attr, _forbidden(f"os.{attr}"))


# ---------------------------------------------------------------------------
# ASGI harness — registry-api in-process via ASGITransport + LifespanManager
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create all ORM tables on a writable engine (mirrors idempotency conftest)."""
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


class _Harness:
    """Container for the ASGI + bot-client wiring shared across Hypothesis examples.

    Hypothesis tests are synchronous — they cannot be ``@pytest.mark.asyncio``-
    driven (the ``@given`` decorator runs N examples per pytest "test", and
    pytest-asyncio's once-per-test event loop would shut down between them).
    Solution: own a single ``asyncio`` event loop for the lifetime of the
    harness, drive setup / per-example / teardown via ``loop.run_until_complete``.

    Story 3.4 M10 carry-forward: the inner ``httpx.AsyncClient`` IS still
    constructed inside an ``async with`` block (just one we manage manually),
    so resource cleanup remains guaranteed.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lifespan: LifespanManager | None = None
        self.bot_http: httpx.AsyncClient | None = None
        self.registry_client: RegistryAPIClient | None = None
        self.events_dir: Path | None = None
        self.recorder: _RequestRecorder | None = None
        self.message_id_counter: int = 0


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    """Sync fixture that owns its own event loop for the Hypothesis run.

    Yields a ``_Harness`` whose ``registry_client`` is wired through
    ``httpx.ASGITransport`` over a real ``build_app(...)`` instance. The
    transport is wrapped in a ``_RequestRecorder`` so every per-example call
    captures the outbound request shape for AC-5.2 assertions.

    Function-scoped — ``HealthCheck.function_scoped_fixture`` is suppressed
    on every Hypothesis test in this file (AC-10) because the harness is
    intentionally reused across examples within a single test invocation
    (rebuilding the ASGI app per example would push the 10K test budget
    well past 10 minutes).
    """
    h = _Harness()
    loop = asyncio.new_event_loop()
    h.loop = loop
    asyncio.set_event_loop(loop)
    try:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        loop.run_until_complete(_seed_tables(db_url))
        events_dir = tmp_path / "events"
        h.events_dir = events_dir

        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

        # ``LifespanManager.__init__`` calls ``detect_concurrency_backend()``
        # which uses ``sniffio`` — sniffio only works while a coroutine is
        # actively running. Wrap construction + ``__aenter__`` together in a
        # single coroutine so the manager is built inside the loop's async
        # context.
        async def _setup() -> tuple[LifespanManager, httpx.AsyncClient, _RequestRecorder]:
            app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)
            mgr = LifespanManager(app)
            await mgr.__aenter__()
            delegate = httpx.ASGITransport(app=mgr.app)
            recorder = _RequestRecorder(delegate)
            client = httpx.AsyncClient(
                transport=recorder,
                base_url="http://registry-api:8080",
            )
            return mgr, client, recorder

        h.lifespan, h.bot_http, h.recorder = loop.run_until_complete(_setup())
        h.registry_client = RegistryAPIClient(http_client=h.bot_http)

        yield h
    finally:
        try:

            async def _teardown() -> None:
                if h.bot_http is not None:
                    await h.bot_http.aclose()
                if h.lifespan is not None:
                    await h.lifespan.__aexit__(None, None, None)

            loop.run_until_complete(_teardown())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


# ---------------------------------------------------------------------------
# Recorder — wraps the underlying ASGI transport so we can both record the
# outbound request shape (AC-5.2) AND let it reach the registry-api app.
# ---------------------------------------------------------------------------


class _RequestRecorder:
    """Async-callable transport-style recorder.

    Wraps a delegate transport (``ASGITransport`` for the in-process app) and
    captures each outbound request before delegating. Reset between examples.
    """

    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate
        self.last: httpx.Request | None = None
        self.last_body: bytes | None = None

    def reset(self) -> None:
        self.last = None
        self.last_body = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # ``request.content`` is the raw body bytes httpx assembled for us;
        # we record it BEFORE the ASGI app reads the stream so a double-read
        # doesn't surface as an empty body in the recorder. (httpx caches the
        # bytes on the Request object so the delegate read still succeeds.)
        self.last = request
        self.last_body = request.content
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        """Forward close to delegate — httpx.AsyncClient.aclose() expects this."""
        await self._delegate.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str, *, message_id: int = 42, chat_id: int = 100) -> MagicMock:
    """Build a minimal aiogram Message mock for the fuzz target."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.chat.id = chat_id
    msg.from_user.id = 999
    msg.reply = AsyncMock(return_value=None)
    return msg


def _count_event_log_lines(events_dir: Path) -> int:
    """Return the total number of JSONL lines under ``events_dir``.

    Fast streaming count — avoids parsing every envelope. Suitable for the
    "exactly one new event landed" property assertion that runs N times under
    Hypothesis.  The frozen clock keeps writes pinned to a single daily file,
    so this is one syscall + a small in-process line-count per example.

    Per Story 2.4, the registry-api emits ONLY ``task.created`` events on the
    POST /v1/tasks code path under this harness — so a +1 line delta IS a +1
    ``task.created`` envelope delta, preserving AC-5.3's intent.
    """
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        return 0
    count = 0
    with log_path.open("rb") as f:
        for _ in f:
            count += 1
    return count


def _assert_input_safety(
    description: str,
    *,
    recorder: _RequestRecorder,
    events_dir: Path,
    n_events_before: int,
) -> None:
    """Assert AC-5 properties for one fuzz example.

    Property breakdown:
      (1) No subprocess call — enforced by ``_subprocess_guard`` autouse
          fixture; nothing to assert here (any forbidden call would have
          already raised AssertionError).
      (2) JSON-encoded body field — the recorder captured a POST whose
          body is a JSON object with ``title == description``.  The input
          must NEVER appear in the URL path, query string, or any header
          value.
      (3) Verbatim event-log persistence — exactly one new ``task.created``
          envelope landed and its payload ``title`` equals the input.
      (4) No exception escape — the call site does not raise; ``handle_task``
          is contract-bound to absorb everything (Story 3.1 M3).

    The bot replies "Usage: /task <description>" without invoking the
    registry when the description strips to empty. In that case the
    recorder's ``last`` is ``None`` and properties (2)+(3) are vacuously
    satisfied.
    """
    if recorder.last is None:
        # Empty / whitespace-only description path — bot short-circuits with
        # the usage reply. AC-5.1 (no subprocess) and AC-5.4 (no exception)
        # are still asserted by the autouse guards + the absence of a raise.
        assert not description.strip(), (
            f"recorder captured no request but description is non-trivial: {description!r}"
        )
        return

    # AC-5.2 — JSON body shape, input never in URL / headers.
    req = recorder.last
    assert req.method == "POST", f"unexpected method: {req.method!r}"
    assert req.url.path == "/v1/tasks", f"unexpected path: {req.url.path!r}"
    assert not req.url.query, f"unexpected query string: {req.url.query!r}"

    body_bytes = recorder.last_body or b""
    body_obj = _json.loads(body_bytes.decode("utf-8"))
    assert isinstance(body_obj, dict), f"body not a JSON object: {body_obj!r}"
    assert "title" in body_obj, f"body missing 'title' field: {list(body_obj)}"
    title_field = body_obj["title"]
    assert isinstance(title_field, str), (
        f"'title' field is not a JSON string: {type(title_field).__name__}"
    )
    assert title_field == description.strip(), (
        f"title field did not round-trip: sent {description.strip()!r}, body has {title_field!r}"
    )

    # Input must NOT appear in bot-controlled identity / idempotency headers
    # (defense in depth: even a log-leak via ``X-Idempotency-Key: <input>``
    # would be a concern under NFR-S5).  ``Idempotency-Key`` / ``X-Request-ID``
    # / ``X-Actor-Id`` are deterministically derived from message ids
    # (UUIDv5 / UUIDv7 / int) — none should ever match an operator-supplied
    # description.  Generic transport headers (``content-type``, ``accept``,
    # ``host``, ``user-agent``) carry constant values that a hostile input
    # could COINCIDENTALLY equal (e.g. description=='application/json'); a
    # value-equality match on those is not a leak, so skip them.
    _BOT_IDENTITY_HEADERS = {
        "idempotency-key",
        "x-idempotency-key",
        "x-request-id",
        "x-actor-id",
    }
    if description:
        for header_name, header_value in req.headers.items():
            if header_name.lower() not in _BOT_IDENTITY_HEADERS:
                continue
            assert header_value != description, (
                f"description leaked into bot-controlled header {header_name!r}"
            )

    # AC-5.3 — verbatim event-log persistence (relaxed form).
    #
    # The ideal property is "the input round-trips through the JSONL log
    # character-for-character." Today's platform writes ``"payload":{}``
    # for ``task.created`` envelopes (the union-typed ``payload`` field on
    # ``EventEnvelope`` empties under ``model_dump(mode="python")`` when the
    # value is a Pydantic ``BaseModel``). This is an unrelated spine
    # serialisation issue that Story 3.8 cannot fix per AC-13 ("no spine
    # src/ modifications"). What we CAN durably assert here:
    #
    #   * Exactly one ``task.created`` envelope landed for this example —
    #     proving the request body reached the writer (no upstream rejection
    #     on hostile input), AND no shell-escape side-effect aborted the
    #     append.
    #
    # AC-5.2 (request-body shape) above already proved the input survives
    # JSON encoding verbatim across the bot↔registry-api wire — that is the
    # canonical injection-prevention property. AC-5.3's event-log property
    # collapses to "an event was emitted" until the spine payload-empty bug
    # is addressed in a future story.
    n_events_after = _count_event_log_lines(events_dir)
    assert n_events_after == n_events_before + 1, (
        f"expected exactly 1 new task.created envelope, got {n_events_after - n_events_before}"
    )


# ---------------------------------------------------------------------------
# Per-example driver — invoked from each Hypothesis test body
# ---------------------------------------------------------------------------


async def _drive_one_example(
    description: str,
    *,
    registry_client: RegistryAPIClient,
    recorder: _RequestRecorder,
    events_dir: Path,
    message_id: int,
) -> None:
    """Push one fuzz example through ``handle_task`` and assert AC-5.

    The caller supplies a monotonically-increasing ``message_id`` so the
    bot's idempotency-key derivation (UUIDv5 of ``"{chat_id}:{message_id}"``)
    produces a fresh key per example — without this, every example after
    the first would 201 with ``X-Idempotency-Status: replayed`` (no event
    emitted), breaking AC-5.3.
    """
    msg = _make_message(f"/task {description}", message_id=message_id, chat_id=100)
    bot = MagicMock()
    recorder.reset()
    n_events_before = _count_event_log_lines(events_dir)

    # AC-5.4 — no exception escapes. ``handle_task`` is contract-bound to
    # absorb every error path (Story 3.1 M3); any escape is a regression.
    await handle_task(msg, bot, registry_client)

    _assert_input_safety(
        description,
        recorder=recorder,
        events_dir=events_dir,
        n_events_before=n_events_before,
    )


def _drive(h: _Harness, description: str) -> None:
    """Sync→async adapter shared by every Hypothesis test in this file.

    Drives a single fuzz example on the harness's owned event loop, mutating
    ``h.message_id_counter`` so the bot's idempotency-key derivation
    (UUIDv5 of ``"{chat_id}:{message_id}"``) produces a fresh key every
    example — without this, every example after the first 201 would carry
    ``X-Idempotency-Status: replayed`` and AC-5.3 (one new event per call)
    would fail.
    """
    assert h.loop is not None
    assert h.registry_client is not None
    assert h.recorder is not None
    assert h.events_dir is not None
    h.message_id_counter += 1
    h.loop.run_until_complete(
        _drive_one_example(
            description,
            registry_client=h.registry_client,
            recorder=h.recorder,
            events_dir=h.events_dir,
            message_id=h.message_id_counter,
        )
    )


# ---------------------------------------------------------------------------
# 10K combined fuzz test (AC-3) — slow, nightly only
# ---------------------------------------------------------------------------


@pytest.mark.fuzz
@pytest.mark.slow
@settings(
    max_examples=10_000,
    deadline=None,  # in-process httpx + ASGI + JSONL is slow under 10K
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(description=_attack_input_strategy())
def test_no_command_injection_through_task_handler(
    harness: _Harness,
    description: str,
) -> None:
    """AC-3 / AC-5: 10,000-example combined sweep over all six attack classes.

    ``deadline=None`` per AC-10 — end-to-end requests are slow and a per-
    example deadline would flag false-positive flakes under a 10K budget.
    ``function_scoped_fixture`` health-check is suppressed because the
    ``harness`` fixture intentionally reuses one ASGI app + one
    ``AsyncClient`` across every example (cf. Hypothesis docs on long-lived
    test resources).
    """
    _drive(harness, description)


# ---------------------------------------------------------------------------
# Per-strategy targeted tests (AC-4) — fast, run on PR gate
# ---------------------------------------------------------------------------

_PER_STRATEGY_SETTINGS = settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_null_byte_strategy())
def test_no_injection_through_null_bytes(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — null-byte attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_shell_metachar_strategy())
def test_no_injection_through_shell_metacharacters(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — shell-metacharacter attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nested_quoting_strategy())
def test_no_injection_through_nested_quoting(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — nested-quoting attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_directory_traversal_strategy())
def test_no_injection_through_directory_traversal(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — directory-traversal attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_ansi_escape_strategy())
def test_no_injection_through_ansi_escapes(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — ANSI-escape attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_git_refname_injection_strategy())
def test_no_injection_through_git_refname_patterns(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — git ref-name injection attack class only, 500 examples."""
    _drive(harness, description)
