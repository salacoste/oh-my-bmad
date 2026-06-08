"""Story 11.3.1 — 10-event approval-inbox replay integration test.

Closes Story 11.3 AC5 (deferred 2026-05-20 per D1): an in-process
mock-harness exercise of the full clawhip-daemon Telegram sink stack
asserts that 10 distinct ``task.approval_requested`` envelopes all
route to the operator's pinned Forum-Topic inbox with distinct
link-backs to each originating task thread.

Architecture (in-process):

* :class:`EventLogWriter` writes envelopes to a ``tmp_path`` events_dir.
* :func:`handle_approval_inbox_opened` seeds the ``approval_inbox`` row
  in an on-disk SQLite (matches ``test_capability_denied_emission``
  convention via ``_db_helpers``).
* :class:`EventLogReader` tails the events_dir.
* :class:`TelegramSink` is constructed with a mocked
  :class:`RegistryAPIReadClient` (so ``get_task_binding`` /
  ``get_pinned_inbox`` are pure in-process) and a mocked
  :class:`TelegramOutbound` (so ``send_to_thread`` is observable).
* The test drives ``sink._handle(env)`` for each envelope (private
  per-envelope entry point — mirrors
  ``services/clawhip-daemon/.../test_telegram_sink.py`` test pattern).

Variants:

* **Variant 1 (load-bearing, AC1+AC2+AC3):** seed inbox FIRST, then
  emit 10 ``task.approval_requested`` → all 10 route to pinned thread
  with distinct link-backs.
* **Variant 2 (race-tolerant, AC4):** documented in module docstring +
  the parent spec; the in-process flow does not construct a true race
  window (``_handle`` is a single coroutine that awaits both lookups
  synchronously). The race tolerance is covered by Story 11.3 AC4's
  unit tests (``test_telegram_sink_routes_to_task_thread_when_no_inbox``)
  which exercise the no-pinned-inbox fallback path that the race would
  collapse into. See Dev Agent Record on the parent spec.
* **AC5 idempotency-on-replay:** call ``sink._handle`` twice with the
  same 10 envelopes — assert 20 ``send_to_thread`` calls total (sink
  does NOT dedupe — pins current behavior).
"""

from __future__ import annotations

from pathlib import Path
from random import Random
from unittest.mock import AsyncMock, MagicMock

import pytest
from clawhip_daemon.adapters.sinks.telegram_sink import (  # noqa: IMP001 — tests/* may cross services
    EventLogReader,
    TelegramSink,
)
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TaskApprovalRequestedPayload,
    new_event_id,
    new_uuid7,
)
from events.event_log_writer import EventLogWriter
from events.schema_registry import register as _reg
from registry_state.domain.event_types import ApprovalInboxOpenedPayload
from registry_state.domain.handlers import handle_approval_inbox_opened
from registry_state.schema import ApprovalInbox

from tests.integration._db_helpers import integration_db_url, integration_seed_tables

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Supergroup chat_id (<= -1_000_000_000_000) so _render_task_thread_link
# produces the expected ``https://t.me/c/<short>/<msg>`` deep link
# (basic-group chat_ids would skip the footer per Story 11.3 PP2).
_OPERATOR_CHAT_ID: int = -1_001_234_567_890
_PINNED_THREAD_ID: int = 777
_ACTOR_ID: str = "operator-test"
_ACTOR = Actor(kind="operator", id=_ACTOR_ID)
_TRACE_ID: str = "01917e5c-a7d1-7000-8abc-000000000000"

# Story 11.3 schema versions (confirmed in domain/event_types.py).
_APPROVAL_INBOX_OPENED_SCHEMA: str = "1.1.0"
_TASK_APPROVAL_REQUESTED_SCHEMA: str = "1.1.0"


# ---------------------------------------------------------------------------
# Registry priming (Story 8.7.5 PP3 discipline — re-register types per test).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register the two event types this test relies on.

    The cross-suite ``_clean_registry`` teardown (in
    ``test_event_log.py``) can wipe the schema registry between test
    files. Re-registering here is idempotent (Story 2.1 register()
    contract) and gives this test a known clean state regardless of
    suite ordering.
    """
    _reg(
        "approval.inbox_opened",
        _APPROVAL_INBOX_OPENED_SCHEMA,
        ApprovalInboxOpenedPayload,
    )
    _reg(
        "task.approval_requested",
        _TASK_APPROVAL_REQUESTED_SCHEMA,
        TaskApprovalRequestedPayload,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_inbox_opened_envelope() -> EventEnvelope:
    """Build an ``approval.inbox_opened`` envelope (Story 11.3 AC2 shape)."""
    rng = Random(11)
    clk = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        type="approval.inbox_opened",
        schema_version=_APPROVAL_INBOX_OPENED_SCHEMA,
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ApprovalInboxOpenedPayload(
            operator_chat_id=_OPERATOR_CHAT_ID,
            inbox_thread_id=_PINNED_THREAD_ID,
            opened_at=FROZEN_EPOCH,
            opened_by_actor_id=_ACTOR_ID,
        ),
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _make_approval_request_envelope(*, index: int) -> EventEnvelope:
    """Build a ``task.approval_requested`` envelope with a distinct task_id.

    The ``task_id`` is derived from *index* so the binding-lookup mock
    can return distinct ``reply_to_message_id`` values per envelope and
    we can assert all 10 link-backs are distinct.
    """
    rng = Random(99 + index)
    clk = FrozenClock(mono_ns=10_000_000 + index, now=FROZEN_EPOCH)
    # Match the TaskApprovalRequestedPayload task_id pattern
    # (``t-<uuid7>``); the helper in test_handlers.py constructs them
    # the same way.
    task_id = f"t-00000000-0000-7000-8000-{index:012d}"
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        type="task.approval_requested",
        schema_version=_TASK_APPROVAL_REQUESTED_SCHEMA,
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskApprovalRequestedPayload(
            task_id=task_id,
            action=f"merge PR #{index}",
            justification=f"approval request #{index}",
        ),
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _make_sink_with_mocked_registry(
    *,
    events_dir: Path,
    pinned_thread_id: int | None,
    binding_by_task_id: dict[str, tuple[int, int]],
) -> tuple[TelegramSink, MagicMock]:
    """Construct a TelegramSink with mocked outbound + registry-client.

    The mocked ``RegistryAPIReadClient`` returns:

    * ``get_task_binding(task_id)`` → ``binding_by_task_id[task_id]``
      (a ``(chat_id, reply_to_message_id)`` pair). Unknown task_ids
      yield ``(None, None)``.
    * ``get_pinned_inbox(chat_id)`` → *pinned_thread_id* (a fixed
      value, or ``None`` to simulate no-inbox-open).

    Returns ``(sink, outbound_mock)`` so callers can drive
    ``sink._handle(env)`` and inspect ``outbound_mock.send_to_thread``.
    """
    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()

    async def _get_task_binding(
        task_id: str, *, request_id: str | None = None
    ) -> tuple[int | None, int | None]:
        return binding_by_task_id.get(task_id, (None, None))

    async def _get_pinned_inbox(
        operator_chat_id: int, *, request_id: str | None = None
    ) -> int | None:
        # Pass-1 review PP1 (Blind #1): branch on operator_chat_id rather
        # than returning a constant for any chat — pre-PP1 the mock made
        # the AC3 "routing to pinned" assertion tautological. Now the
        # routing decision IS exercised: only operator_chat_id == the
        # SEEDED chat (where the materializer wrote a row) returns the
        # pinned thread; other chats fall through to per-task binding.
        if operator_chat_id == _OPERATOR_CHAT_ID:
            return pinned_thread_id
        return None

    registry_client_mock = MagicMock()
    registry_client_mock.get_task_binding = AsyncMock(side_effect=_get_task_binding)
    registry_client_mock.get_pinned_inbox = AsyncMock(side_effect=_get_pinned_inbox)

    reader = EventLogReader(events_dir)
    sink = TelegramSink(
        outbound=outbound_mock,  # type: ignore[arg-type]
        log_reader=reader,
        registry_client=registry_client_mock,
    )
    return sink, outbound_mock


# ---------------------------------------------------------------------------
# Variant 1 (AC1+AC2+AC3): seed inbox FIRST, 10 approval requests, all pinned
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="function")  # PP-Edge#9 — Story 8.7.6 PP2 discipline
async def test_journey_approval_inbox_10_event_replay_all_routed_to_pinned_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 11.3.1 AC1+AC2+AC3: 10 approval requests → pinned thread.

    Workflow:

    1. Build EventLogWriter pointed at ``tmp_path / events``.
    2. Seed ``approval_inbox`` via :func:`handle_approval_inbox_opened`
       on an on-disk SQLite (matches existing integration-test
       convention via ``_db_helpers``).
    3. Emit 10 distinct ``task.approval_requested`` envelopes (distinct
       task_ids → distinct binding lookups → distinct link-backs).
    4. Drive ``sink._handle(env)`` for each envelope read back from
       the JSONL log.
    5. Assert:

       * ``outbound.send_to_thread`` called exactly 10 times.
       * Every call has ``reply_to_message_id == _PINNED_THREAD_ID``.
       * Every call has the link-back footer
         ``"↩ Original task thread:"``.
       * All 10 link-backs are DISTINCT (each contains the originating
         task's ``reply_to_message_id`` in the
         ``https://t.me/c/<short>/<msg>`` deep link).
    """
    # PP-Edge#4: defensively unset the deliver-event-types env var so
    # ambient CI config can't silently drop task.approval_requested
    # envelopes at the sink's filter and fail the test for an unrelated
    # reason.
    monkeypatch.delenv("CLAWHIP_DAEMON_DELIVER_EVENT_TYPES", raising=False)

    # --- 1. Build EventLogWriter + on-disk SQLite ---
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    writer = EventLogWriter(base_dir=events_dir, clock=clock)

    db_path = tmp_path / "state.sqlite3"
    db_url = integration_db_url(db_path)
    await integration_seed_tables(db_url)

    # --- 2. Seed approval_inbox via the materializer ---
    from registry_state.adapters.sqlite_store import create_engine, get_session

    engine = create_engine(db_url)
    sm = get_session(engine)
    inbox_env = _make_inbox_opened_envelope()
    async with sm() as session, session.begin():
        await handle_approval_inbox_opened(session, inbox_env)
    # Verify the row exists — defensive assertion that pins the
    # materializer contract (test fails loud if the seed silently no-ops).
    async with sm() as session:
        row = await session.get(ApprovalInbox, _OPERATOR_CHAT_ID)
        assert row is not None, "approval_inbox row must exist after seed"
        assert row.inbox_thread_id == _PINNED_THREAD_ID
    await engine.dispose()

    # --- 3. Emit 10 task.approval_requested envelopes ---
    approval_envelopes = [_make_approval_request_envelope(index=i) for i in range(10)]
    for env in approval_envelopes:
        await writer.append(env)
    await writer.close()

    # Build distinct binding rows: task_id_i → (operator_chat_id, 100+i).
    # The 100+i is the per-task originating thread_id; the assertions
    # below verify each link-back encodes its own task's value.
    binding_by_task_id: dict[str, tuple[int, int]] = {
        env.payload.task_id: (_OPERATOR_CHAT_ID, 100 + i)
        for i, env in enumerate(approval_envelopes)
    }

    # --- 4. Construct sink + drive per-envelope dispatch ---
    sink, outbound_mock = _make_sink_with_mocked_registry(
        events_dir=events_dir,
        pinned_thread_id=_PINNED_THREAD_ID,
        binding_by_task_id=binding_by_task_id,
    )
    reader = EventLogReader(events_dir)
    replayed = await reader.read_new_envelopes()
    assert len(replayed) == 10, (
        f"expected 10 task.approval_requested envelopes in the log; got {len(replayed)}"
    )
    for env in replayed:
        await sink._handle(env)

    # --- 5. Assertions ---
    assert outbound_mock.send_to_thread.call_count == 10, (
        f"expected 10 send_to_thread calls; got {outbound_mock.send_to_thread.call_count}"
    )

    link_back_fragments: set[str] = set()
    for call in outbound_mock.send_to_thread.call_args_list:
        kwargs = call.kwargs
        assert kwargs["chat_id"] == _OPERATOR_CHAT_ID
        # AC3: every envelope routed to the pinned thread.
        assert kwargs["reply_to_message_id"] == _PINNED_THREAD_ID, (
            f"envelope routed to {kwargs['reply_to_message_id']} "
            f"instead of pinned thread {_PINNED_THREAD_ID}"
        )
        # AC3: link-back footer present.
        text: str = kwargs["text"]
        assert "↩ Original task thread:" in text, f"missing link-back footer; got text={text!r}"
        # Supergroup deep-link shape (Story 11.3 PP2).
        assert "https://t.me/c/" in text
        # Record the URL fragment for distinct-link-back assertion.
        # The URL embeds the originating msg id as ``/<msg>`` — extract
        # everything after the last ``/`` of the t.me/c/ URL up to the
        # closing quote.
        marker = "https://t.me/c/"
        idx = text.index(marker)
        end = text.index('"', idx)
        link_back_fragments.add(text[idx:end])

    # AC3: all 10 link-backs are DISTINCT (each points to its own
    # originating task thread, encoded in the URL).
    assert len(link_back_fragments) == 10, (
        f"expected 10 distinct link-back URLs; got "
        f"{len(link_back_fragments)}: {sorted(link_back_fragments)}"
    )


# ---------------------------------------------------------------------------
# AC5 — idempotency on replay (sink does NOT dedupe)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="function")  # PP-Edge#9 — Story 8.7.6 PP2 discipline
async def test_journey_approval_inbox_replay_does_not_dedupe_at_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 11.3.1 AC5: re-reading the JSONL log produces 20 calls.

    Pins the current behavior: the Telegram sink does NOT dedupe
    duplicate envelopes (idempotency is the materializer's
    responsibility, not the delivery sink's). A future "sink dedup"
    feature would intentionally break this assertion, signalling a
    deliberate behavior change.

    Pass-1 review PP2 (Blind #2 + Edge #1): the previous version called
    ``sink._handle`` twice on the in-memory envelope list — a "called
    twice" test that did NOT exercise the reader-replay path. The
    spec's AC5 wording says "resetting the sink's EventLogReader
    offset" — fresh EventLogReader instances start at offset 0 so
    constructing two readers and feeding both batches through
    ``_handle`` simulates the offset-reset semantics genuinely.
    """
    # PP-Edge#4: defensively unset the deliver-event-types env var.
    monkeypatch.delenv("CLAWHIP_DAEMON_DELIVER_EVENT_TYPES", raising=False)

    from datetime import UTC, datetime

    from events.event_log_writer import EventLogWriter

    events_dir = tmp_path / "events"
    events_dir.mkdir()
    clock = FrozenClock(now=datetime.now(UTC), mono_ns=1_000_000)

    # Persist 10 approval envelopes to disk via the real EventLogWriter.
    writer = EventLogWriter(base_dir=events_dir, clock=clock)
    in_memory_envelopes = [_make_approval_request_envelope(index=i) for i in range(10)]
    for env in in_memory_envelopes:
        await writer.append(env)
    await writer.close()

    binding_by_task_id: dict[str, tuple[int, int]] = {
        env.payload.task_id: (_OPERATOR_CHAT_ID, 200 + i)
        for i, env in enumerate(in_memory_envelopes)
    }
    sink, outbound_mock = _make_sink_with_mocked_registry(
        events_dir=events_dir,
        pinned_thread_id=_PINNED_THREAD_ID,
        binding_by_task_id=binding_by_task_id,
    )

    # First pass — fresh reader (offset 0) reads all 10 from disk.
    reader_pass_1 = EventLogReader(events_dir)
    replayed_1 = await reader_pass_1.read_new_envelopes()
    assert len(replayed_1) == 10, f"first reader pass yielded {len(replayed_1)} envelopes, want 10"
    for env in replayed_1:
        await sink._handle(env)
    assert outbound_mock.send_to_thread.call_count == 10

    # Second pass — NEW reader instance (offset reset to 0). Re-reads
    # the same on-disk envelopes; sink processes them again.
    reader_pass_2 = EventLogReader(events_dir)
    replayed_2 = await reader_pass_2.read_new_envelopes()
    assert len(replayed_2) == 10, (
        f"second reader pass yielded {len(replayed_2)} envelopes, want 10 (offset should reset)"
    )
    for env in replayed_2:
        await sink._handle(env)
    assert outbound_mock.send_to_thread.call_count == 20, (
        f"sink unexpectedly deduplicated replays; expected 20 calls, "
        f"got {outbound_mock.send_to_thread.call_count}"
    )
