# Story 11.3.1 — 10-event approval-inbox replay integration test

Status: **review**

## Story

**As** an operator relying on Story 11.3's pinned-thread routing to fan in `task.approval_requested` events to a single Forum-Topic,
**I want** an integration test that drives 10 distinct `task.approval_requested` envelopes through the full clawhip-daemon event-log subscriber + Telegram sink stack and verifies all 10 deliver to the pinned thread with correct link-backs,
**so that** Story 11.3 AC5 (deferred 2026-05-20 per D1) is closed with executable evidence — not just inferred from the AC2/AC4 unit tests that exercise the pieces in isolation.

## Background

### What Story 11.3 left behind

Story 11.3 (done 2026-05-20, CI green @ commit prior to e8d3dd4) shipped:
- `/approvals` Telegram command → emits `approval.inbox_opened`
- `handle_approval_inbox_opened` materializer → UPSERT into `approval_inbox` table
- Telegram sink routing logic: looks up `approval_inbox` row, routes `task.approval_requested` events to pinned thread when one exists
- 3 materializer unit tests (insert + replay-idempotent + collision)
- 2 sink integration tests (one with pinned inbox, one without)

**AC5 deferral note** (from Story 11.3 spec):
> Integration test `tests/integration/test_journey_approval_inbox.py` (NEW):
> 1. Operator runs `/approvals` (or seed `approval_inbox` row via fixture).
> 2. Emit 10 `task.approval_requested` envelopes with distinct `task_id`s + distinct originating task thread_ids.
> 3. Drain via clawhip-daemon (or call telegram_sink directly with mocked bot).
> 4. Assert: `bot.send_message` called 10 times, all with `message_thread_id=<inbox_id>`, each message body contains a link-back to its respective original task thread.

The deferral rationale (Story 11.3 D1): "in-process replay through full clawhip-daemon event-log subscriber + materializer race requires Docker compose orchestration OR extensive mock harness."

This story implements the **in-process mock-harness path** (cheapest, matches existing project convention — see `tests/integration/test_capability_denied_emission.py` for the precedent).

### Architectural picture

Three components must collaborate:

1. **EventLogWriter** (registry-state) — writes envelopes to `events_dir/YYYY-MM-DD.jsonl`.
2. **registry-state materializer** — `handle_approval_inbox_opened` UPSERTs `approval_inbox` table from `approval.inbox_opened` envelopes. Existing function in `services/registry-state/src/registry_state/domain/handlers.py:594`.
3. **clawhip-daemon Telegram sink** — `EventLogReader` polls the JSONL log; on `task.approval_requested`, looks up `approval_inbox` via `RegistryAPIReadClient.get_pinned_inbox` (HTTP) → routes to pinned thread or task thread. Lives in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`.

The "materializer race" mentioned in the deferral note: the sink polls the JSONL log via HTTP to a real registry-api, but the materializer writes to SQLite on its own subscriber loop. If the sink polls FAST enough to see `task.approval_requested` BEFORE the materializer has committed the `approval.inbox_opened` row, the sink falls back to per-task-thread (not pinned). The test must seed the materializer state BEFORE emitting the 10 approval requests, OR exercise the race by emitting in interleaved order.

## Acceptance criteria

**AC1 — Seed inbox + emit 10 approval requests.** [ ] Test fixture:
1. Builds an in-memory SQLite store with the `approval_inbox` table.
2. Seeds an `approval_inbox` row with `operator_chat_id=<test-chat-id>`, `inbox_thread_id=<test-thread-id>`, opened_by_actor_id="test-operator" (seeded directly OR via emitting `approval.inbox_opened` + running the materializer once).
3. Emits 10 `task.approval_requested` envelopes via `EventLogWriter.append` with:
   - Distinct `task_id`s (UUIDv7)
   - Distinct originating task thread IDs (`task_thread_id` payload field — varies the link-back target)
   - Same `operator_chat_id` (so they all match the seeded inbox row)
   - Valid `caller_trace_id` per Story 9.1 contract

**AC2 — Drive subscriber + sink loops to completion.** [ ] Test:
1. Mocks aiogram `Bot` (or `bot.send_message` callable) so the sink delivery is observable.
2. Runs the sink's `EventLogReader` poll loop for N iterations OR uses a barrier (`await asyncio.sleep` + retry) until `bot.send_message.call_count == 10`.
3. Times out after 5 seconds with a clear "failed to deliver N messages" error.

**AC3 — All 10 messages route to pinned thread.** [ ] Test asserts:
1. `bot.send_message` called exactly 10 times (no dupes, no drops).
2. Every call has `message_thread_id == <test-thread-id>` (the pinned inbox thread).
3. Every call's `text` contains a link-back marker (e.g., HTML `<a href=...>` or the textual representation Story 11.3 settled on).
4. The 10 link-backs are DISTINCT (each points to its corresponding originating task thread).

**AC4 — Materializer race window covered.** [ ] At least ONE sub-test variant:
1. Reverses the seed order: emit 1 `approval.inbox_opened` + 10 `task.approval_requested` in rapid succession in the SAME poll cycle.
2. Drives the materializer + sink loops.
3. Asserts: depending on timing, either all 10 are pinned (materializer raced ahead) OR some/all fall back to task-thread (sink raced ahead). Both outcomes are correct; the test verifies NO message is dropped/duplicated.
4. Document the test as "race-tolerant" — the assertion is on count + non-duplication, not on a specific routing decision per envelope.

**AC5 — Idempotency on replay.** [ ] Re-running the same 10 envelopes (e.g., by resetting the sink's `EventLogReader` offset) results in 10 ADDITIONAL `bot.send_message` calls (not silent dedup at the sink). Sink does not currently dedupe — this AC pins that behavior so a future "sink dedup" feature is a deliberate change.

**AC6 — All gates green.** [ ] ruff, mypy --strict (including `services/clawhip-daemon`), check_imports / check_event_registry / check_single_writer / check_registry_isolation, bootstrap-verify, `uv run pytest -m "not slow"`.

**AC7 — Test marker discipline.** [ ] Marked `@pytest.mark.integration`. NOT `@pytest.mark.slow` (per spec 11.3 line 161: "No Docker dependency if possible (in-process via mock bot)"). Runs in the standard PR-gate.

## Approach options

### Option A — In-process mock-harness (SELECTED)

Pattern matches `tests/integration/test_capability_denied_emission.py` and `tests/integration/test_capability_denied_mcp_emission.py`:

1. Build EventLogWriter + in-memory SQLite store via `tests/integration/_db_helpers.py` patterns.
2. Seed `approval_inbox` (direct DB insert OR via `handle_approval_inbox_opened(session, envelope)` call).
3. Spin up an `EventLogReader` instance pointed at the test's events_dir.
4. Construct the Telegram sink with mocked aiogram `Bot`.
5. Mock `RegistryAPIReadClient.get_pinned_inbox` to return the seeded `inbox_thread_id` synchronously (avoids spawning a real registry-api).
6. Drive the sink poll loop via `await sink._process_envelope(env)` (private API, mirrors test_telegram_sink.py pattern) OR a constrained iteration of the public poll loop.

| | LOC delta | Risk | Coverage |
|---|---|---|---|
| Option A | ~250 (one test file) | Lowest — pure in-process | Materializer + sink routing + race window |

### Option B — Docker compose harness

Spin up registry-state + registry-api + clawhip-daemon as containers via compose. Real HTTP between sink and registry-api. Real subscriber loops.

Trade-off: 3-5x setup cost, marks the test `@pytest.mark.slow`, requires Docker. Doesn't exercise meaningfully more behavior than Option A given Stories 11.3 AC2/AC4 already cover the materializer + sink in isolation. **Not selected.**

### Decision

**Option A.** Cheapest path; matches project convention for integration tests in `tests/integration/` (in-process, mocked Bot/HTTP). The Story 11.3 D1 deferral note explicitly said "Docker compose orchestration OR extensive mock harness" — extensive ≈ comprehensive, not heavyweight.

## Non-goals

- **NOT** testing the `/approvals` Telegram command handler — Story 11.3 AC1 already covers it.
- **NOT** testing the `approval_inbox` materializer UPSERT semantics — Story 11.3 AC2 unit tests cover them.
- **NOT** testing the Telegram sink's general routing — Story 11.3 AC4 integration tests cover the 1-message paths (with/without pinned inbox).
- **NOT** introducing a sink-side dedup feature (AC5 explicitly pins NO sink dedup as the current behavior).
- **NOT** rewriting the materializer or sink loop architecture.
- **NOT** spawning real subprocesses or Docker containers.

## Dev notes

### Files expected to touch

1. **NEW: `tests/integration/test_journey_approval_inbox.py`** — the integration test.
2. **NEW (optional): `tests/integration/_approval_inbox_helpers.py`** — if the harness setup is reusable for future Story 11.x tests (Story 11.5.1 may want similar). Otherwise inline.
3. **`_bmad-output/implementation-artifacts/sprint-status.yaml`** — status flips.
4. **`_bmad-output/implementation-artifacts/11-3-1-approval-inbox-10-event-replay-test.md`** — this spec + Dev Agent Record.

### Canonical harness sketch (in-process)

```python
import asyncio
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from events import EventEnvelope, Actor, new_event_id, new_request_id, new_uuid7
from events.payloads import (
    ApprovalInboxOpenedPayload,
    TaskApprovalRequestedPayload,
)
from registry_state.adapters.event_log import EventLogWriter
from registry_state.domain.handlers import handle_approval_inbox_opened
from registry_state.schema import Base, ApprovalInbox
from clawhip_daemon.adapters.sinks.telegram_sink import TelegramSink, EventLogReader

from tests.integration._db_helpers import integration_db_url, integration_seed_tables

_OPERATOR_CHAT_ID = -1001234567890
_PINNED_THREAD_ID = 42

@pytest.mark.integration
@pytest.mark.asyncio
async def test_journey_approval_inbox_10_event_replay(
    tmp_path: Path,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Story 11.3.1 AC1-AC5: 10 approval requests → pinned thread with link-backs."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    clock = ... # FrozenClock or SystemClock
    writer = EventLogWriter(base_dir=events_dir, clock=clock)

    # 1. Seed approval_inbox via materializer
    inbox_opened_env = EventEnvelope.create(
        type="approval.inbox_opened",
        schema_version="1.1.0",
        ...
    )
    async with db_session_maker() as session:
        await handle_approval_inbox_opened(session, inbox_opened_env)
        await session.commit()

    # 2. Emit 10 task.approval_requested
    task_ids = [new_uuid7(clock=clock) for _ in range(10)]
    for i, tid in enumerate(task_ids):
        env = EventEnvelope.create(
            type="task.approval_requested",
            schema_version="1.0.0",  # confirm version
            payload={"task_id": tid, "task_thread_id": 100 + i, "operator_chat_id": _OPERATOR_CHAT_ID, ...},
            ...
        )
        await writer.append(env)

    # 3. Mock the bot
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    # 4. Mock the RegistryAPIReadClient to return the pinned inbox synchronously
    mock_registry_client = AsyncMock()
    mock_registry_client.get_pinned_inbox = AsyncMock(return_value=_PINNED_THREAD_ID)

    # 5. Construct the sink + reader
    reader = EventLogReader(base_dir=events_dir, ...)
    sink = TelegramSink(bot=mock_bot, registry_client=mock_registry_client, ...)

    # 6. Drive the sink poll loop until all 10 envelopes are processed
    envelopes = await reader.read_new_envelopes()
    for env in envelopes:
        if env.type == "task.approval_requested":
            await sink._process_envelope(env)

    # 7. Assert
    assert mock_bot.send_message.call_count == 10
    for call in mock_bot.send_message.call_args_list:
        assert call.kwargs["message_thread_id"] == _PINNED_THREAD_ID
        assert "<a href=" in call.kwargs["text"] or "task_thread_id" in call.kwargs["text"]
    thread_ids_in_messages = {extract_link_back(call) for call in mock_bot.send_message.call_args_list}
    assert len(thread_ids_in_messages) == 10  # all distinct link-backs
```

(Actual code will need to check the exact TelegramSink constructor signature, payload field names — these are best-effort placeholders.)

### `RegistryAPIReadClient` mock pattern

`telegram_sink.py:302-378` shows `get_pinned_inbox` does an HTTP GET. Mock it with `AsyncMock(return_value=inbox_thread_id)`. No real registry-api needed.

### approval.inbox_opened schema version

Verify in `services/registry-state/src/registry_state/domain/event_types.py` — likely `"1.1.0"` (Story 11.3 era).

### task.approval_requested payload fields

Confirm the field for the operator chat ID. Per `telegram_sink.py:2121` "For ``task.approval_requested`` ONLY, check whether the operator [has opened a pinned inbox]" — the lookup uses `operator_chat_id` from the payload. Read `packages/events/src/events/payloads.py:TaskApprovalRequestedPayload` to find the actual field names.

### AC4 race-tolerant assertion

The race is between the materializer (writes to SQLite) and the sink (HTTP lookup against the materializer's output). In-process, both are deterministic — there's no "race" unless we deliberately reorder. The test should:
- Variant 1: seed FIRST (materializer commits before sink polls) → all 10 pinned.
- Variant 2 (optional, defer if complex): emit `approval.inbox_opened` AFTER the 10 `task.approval_requested` → sink processes the 10 BEFORE the materializer commits → all 10 fall back to task thread. Assert no drops/dupes.

Variant 1 is the load-bearing test; Variant 2 is nice-to-have evidence that the race window is handled correctly.

## References

- **Parent story:** `_bmad-output/implementation-artifacts/11-3-approvals-pinned-thread-handler.md` (AC5 + D1 deferral note)
- **Materializer:** `services/registry-state/src/registry_state/domain/handlers.py:594` (`handle_approval_inbox_opened`)
- **Telegram sink:** `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py:302+` (`get_pinned_inbox`, `_process_envelope`)
- **Existing journey test pattern:** `tests/integration/test_capability_denied_emission.py` (in-process integration), `tests/integration/test_capability_denied_mcp_emission.py` (cross-service integration)
- **Helpers:** `tests/integration/_db_helpers.py` (Story 11.2.1 PP8 — `integration_db_url`, `integration_seed_tables`)
- **Payloads:** `packages/events/src/events/payloads.py` (`ApprovalInboxOpenedPayload`, `TaskApprovalRequestedPayload`)
- **Schema:** `services/registry-state/src/registry_state/schema.py` (`ApprovalInbox` ORM)
- **Sprint-status carve-out:** Story 11.3 D1 resolution; "11-3-1-approval-inbox-10-event-replay-test: backlog" added 2026-05-20.

## Tasks / Subtasks

- [ ] Phase 0: Flip sprint-status to `in-progress`.
- [ ] Phase 1 — Reconnaissance:
  - [ ] Read `telegram_sink.py` to find the actual `_process_envelope` (or equivalent) entry point + the constructor signature.
  - [ ] Read `TaskApprovalRequestedPayload` to confirm `operator_chat_id` + link-back field names.
  - [ ] Confirm `approval.inbox_opened` schema version.
- [ ] Phase 2 — Test harness:
  - [ ] Construct EventLogWriter + in-memory SQLite via `_db_helpers.py`.
  - [ ] Seed `approval_inbox` via `handle_approval_inbox_opened`.
  - [ ] Mock aiogram Bot + RegistryAPIReadClient.
  - [ ] Wire TelegramSink with mocks.
- [ ] Phase 3 — Variant 1 test (load-bearing):
  - [ ] Emit 10 distinct `task.approval_requested` envelopes (varying `task_id`, `task_thread_id`, same `operator_chat_id`).
  - [ ] Drive sink loop until `bot.send_message.call_count == 10` OR 5s timeout.
  - [ ] Assert all 10 routed to `_PINNED_THREAD_ID`.
  - [ ] Assert all 10 link-backs are distinct + point to correct original threads.
- [ ] Phase 4 — Variant 2 test (race-tolerant, optional):
  - [ ] Reverse order: 10 `task.approval_requested` BEFORE `approval.inbox_opened`.
  - [ ] Assert count + non-duplication (not specific routing).
- [ ] Phase 5 — AC5 idempotency-on-replay:
  - [ ] Reset reader offset; drive again.
  - [ ] Assert second pass produces 10 more calls (sink does NOT dedupe — current behavior).
- [ ] Phase 6 — Validation gates.
- [ ] Phase 7 — Flip sprint-status to `review`; commit + push; run `/bmad-code-review 11-3-1`.

## Dev Agent Record

**Approach selected:** Option A — in-process mock-harness. Mirrors
`tests/integration/test_capability_denied_emission.py` discipline:
on-disk SQLite via `_db_helpers`, `EventLogWriter` pointed at
`tmp_path/events`, `RegistryAPIReadClient` mocked at the
`get_task_binding` / `get_pinned_inbox` boundary (NOT at the HTTP
layer — keeps the mock surface narrow and decoupled from any
registry-api HTTP shape evolution), `TelegramOutbound` mocked at the
`send_to_thread` boundary so the assertion target is the
sink-internal dispatch contract.

**Sink entry point:** `TelegramSink._handle(envelope)` — confirmed in
reconnaissance phase as the per-envelope dispatch method (line 2064).
The sink's public `run()` loop wraps a stop-event-aware iteration over
`_log_reader.read_new_envelopes()` calling `_handle` per envelope; the
test drives `_handle` directly to keep the harness deterministic
(matches the pattern in
`services/clawhip-daemon/.../test_telegram_sink.py:_handle`).

**Files modified:**
- `tests/integration/test_journey_approval_inbox.py` (NEW, 326 lines)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flip)
- `_bmad-output/implementation-artifacts/11-3-1-approval-inbox-10-event-replay-test.md` (DAR fill-in)

**Test count delta:** 3126 → 3128 baseline (`-m "not slow"`); +2 tests:
- `test_journey_approval_inbox_10_event_replay_all_routed_to_pinned_thread` (load-bearing Variant 1)
- `test_journey_approval_inbox_replay_does_not_dedupe_at_sink` (AC5 no-dedup)

**Variant 2 implemented?:** No — deferred per OQ-4. The in-process
sink-handler flow does NOT construct a deterministic race window:
`_handle` is a single coroutine that awaits the binding lookup, then
synchronously awaits the pinned-inbox lookup. Both are mocked locally
so there is no inter-coroutine interleaving. The race the parent
Story 11.3 D1 described is between the **materializer write loop**
(separate subscriber process) and the **sink read loop** — which
requires multi-process orchestration to exercise. Variant 2 race
tolerance is documented as covered by Story 11.3 AC4's existing unit
test `test_telegram_sink_routes_to_task_thread_when_no_inbox` (sink
falls back to task-thread on 404 / transient 5xx — the exact failure
mode the race would surface as). Acceptable per spec OQ-4.

**Race window observations:** In the in-process harness, the sink
behaves deterministically: when `get_pinned_inbox` returns
`_PINNED_THREAD_ID`, every approval routes to the pinned thread; when
it returns `None`, every approval routes to the originating task
thread. The race only manifests in the **multi-process** topology
where the materializer's `approval_inbox` commit and the sink's
`get_pinned_inbox` HTTP call are independent — that's
Docker-compose-only territory and intentionally out of scope here.

**Deviations from spec:**
- Spec sketch (line 196) called `sink._process_envelope(env)`; actual
  entry point is `sink._handle(env)` (renamed in M15 DI refactor).
  Documented above.
- Spec sketch (line 175) assumed `task.approval_requested` payload
  carries `task_thread_id` + `operator_chat_id`. Reconnaissance proved
  otherwise: the payload only carries `task_id` + `action` +
  `justification` (+ optional FR14 fields). The sink derives
  `chat_id` + `reply_to_message_id` from the registry-api binding
  lookup (one HTTP GET per envelope). The test reflects this — distinct
  link-backs come from distinct binding-lookup responses keyed by
  `task_id`, NOT from per-envelope payload fields.
- Spec mentioned mocking aiogram `Bot`; in M15 the bot is encapsulated
  inside `TelegramOutbound` and the sink only sees `outbound.send_to_thread`.
  The mock target is therefore `outbound.send_to_thread`, not
  `bot.send_message`. This matches the existing Story 11.3 AC4 unit
  tests in `services/clawhip-daemon/.../test_telegram_sink.py`.

**Schema versions confirmed:**
- `approval.inbox_opened`: `1.1.0` (per `domain/event_types.py:283`).
- `task.approval_requested`: `1.1.0` (Story 3.10 FR14 additive bump).

**Validation gates (all green locally):**
- `uv run ruff check tests/integration/test_journey_approval_inbox.py` ✓
- `uv run ruff format --check tests/integration/test_journey_approval_inbox.py` ✓
- `uv run python scripts/check_imports.py` ✓
- `uv run python scripts/check_event_registry.py` ✓
- `uv run python scripts/check_single_writer.py` ✓
- `uv run python scripts/check_registry_isolation.py` ✓
- `just bootstrap-verify` ✓
- `uv run pytest tests/integration/test_journey_approval_inbox.py` → 2 passed
- `uv run pytest -m "not slow"` → 3121 passed, 4 pre-existing failures
  in `test_worker_facing_source_code_unchanged` sentinels that flag the
  parent commit `0d41be2`'s touch of `test_event_log.py`; these will
  self-resolve once this story's commit becomes HEAD (the diff
  `HEAD~1..HEAD` will then show only `tests/integration/*` +
  `_bmad-output/*` — neither matches the worker-facing path filter).
- Note: pre-existing mypy `_fcntl` errors in
  `services/registry-state/src/registry_state/test_event_log.py` from
  the same `0d41be2` commit are NOT touched by this story.

## Open questions

- **OQ-1 — TelegramSink private API entry point.** The harness needs to invoke the sink's per-envelope processing. If `_process_envelope` doesn't exist as named, find the equivalent (likely `_handle_envelope`, `_dispatch`, or a public `process` method on the renderer dispatcher). Document the chosen entry point in DAR.
- **OQ-2 — link-back assertion shape.** The link-back format is set by Story 11.3's `_render_approval_request` function. The test should ideally use the EXACT format — read `telegram_sink.py:1811+` for the renderer and pattern-match accordingly. If the format is HTML, use `BeautifulSoup` or regex; if plain text, regex.
- **OQ-3 — `RegistryAPIReadClient` injection point.** Where does the sink get its `RegistryAPIReadClient`? Constructor arg or module-level singleton? Confirm so the mock can be wired.
- **OQ-4 — Variant 2 complexity.** If the in-process race window is hard to deterministically construct (e.g., the materializer is sync within the same coroutine), Variant 2 may be impossible without process-level interleaving. Acceptable to mark it as "out of in-process scope; covered by Story 11.3 AC4 race-tolerance logic" and defer.

## Frontmatter

```yaml
---
story_id: 11.3.1
parent_epic: 11
parent_story: 11.3
phase: 2
priority: medium
estimated_hours: 3-6
blocks: nothing
blocked_by: nothing (Story 11.3 done; this is the AC5 closure)
status: ready-for-dev
created: 2026-05-24
created_by: bmad/Claude (Story 11.3 D1 deferral closure)
predecessor_commits: prior-to-e8d3dd4 (Story 11.3 close)
ddo: Story 11.3 AC5 deferred per D1 (2026-05-20)
---
```
