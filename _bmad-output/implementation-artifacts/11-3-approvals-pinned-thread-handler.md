# Story 11.3 — `/approvals` Telegram command opens pinned-thread inbox

Status: **ready-for-dev**

## Story

**As** the platform operator
**I want** to open a persistent pinned Telegram thread via the `/approvals` command, and have subsequent `task.approval_requested` events deliver into that thread (with a link back to the originating task thread for context)
**so that** approval requests don't scatter across N task threads — they consolidate into a single audit-friendly inbox where I can triage, approve, or reject from one consistent place (FR63).

Story 11.3 wires the operator-facing approval-inbox UX:
1. New `/approvals` Telegram handler in `services/telegram-gateway/.../handlers/approvals_command.py` opens the pinned thread + emits a NEW `approval.inbox_opened` event type
2. New `approval_inbox` SQLite table in registry-state (one row per operator chat) materialized by the event subscriber
3. `clawhip-daemon/.../sinks/telegram_sink.py` routing modified to check pinned-inbox state before delivering `task.approval_requested` events

## Acceptance criteria

### AC1 — `/approvals` command handler in telegram-gateway

New module: `services/telegram-gateway/src/telegram_gateway/handlers/approvals_command.py`

Mirror the existing handler pattern (look at `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` for canonical structure):
- `async def handle_approvals(message: aiogram.types.Message, ...) -> None` signature
- Allowlist middleware (existing Story 3.2 pattern) gates access
- On invocation:
  1. Check if operator already has a pinned inbox (via registry-state read — see AC2 schema)
  2. If yes: reply "You already have an approval inbox pinned at <thread-link>"
  3. If no:
     - Create new thread in the operator's chat via aiogram `bot.create_forum_topic(...)` (or equivalent — verify aiogram API)
     - Pin the thread via `bot.pin_chat_message(...)` (or thread-specific pinning)
     - Emit `approval.inbox_opened` event via clawhip-bridge MCP (DO NOT write directly to registry-state per FR26 single-writer rule)
     - Reply with confirmation + thread link
- Register handler in `services/telegram-gateway/.../app.py` lifespan (mirror existing handler registration pattern)

Self-verification:
- `grep -F "approvals_command" services/telegram-gateway/src/telegram_gateway/handlers/` returns the new file
- Test `test_approvals_command_creates_pinned_thread_when_none_exists` — mock aiogram bot, mock registry-state read returning None, assert `create_forum_topic` called + `approval.inbox_opened` emitted
- Test `test_approvals_command_returns_existing_thread_link_when_inbox_already_open` — mock registry-state read returning existing row, assert NO new thread created + reply contains existing thread link
- Test `test_approvals_command_rejects_non_allowlisted_caller` — non-allowlisted user → 403/silent-drop per existing middleware behavior

### AC2 — `approval_inbox` SQLite table in registry-state

New table schema in `services/registry-state/src/registry_state/schema.py` (or wherever existing tables are defined — verify):

```python
class ApprovalInbox(Base):
    """One row per operator chat with an open approval inbox.

    Materialized by the event subscriber from `approval.inbox_opened` events.
    Read by `clawhip-daemon` (outbound delivery) to determine routing for
    `task.approval_requested` events. Read by `/approvals` handler (telegram-gateway)
    to check whether an operator already has an inbox open.
    """
    __tablename__ = "approval_inbox"

    operator_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbox_thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_by_actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
```

Constraints:
- **Primary key = `operator_chat_id`** (one inbox per operator chat — UPSERT semantics on duplicate `approval.inbox_opened` events).
- **`inbox_thread_id` is `BigInteger`** — Telegram thread IDs fit in int64.
- **`opened_at` is timezone-aware** — matches Phase 2 datetime convention (Story 11.2 D2 fingerprint pattern parallel).
- **`opened_by_actor_id` capped at 128 chars** — matches Story 11.2 P1-H1 codebase-wide invariant.

Materialization logic in `services/registry-state/src/registry_state/domain/event_subscribers.py` (or wherever existing materializers live):
- On `approval.inbox_opened` event: UPSERT row in `approval_inbox` table (insert if absent, update `inbox_thread_id` + `opened_at` + `opened_by_actor_id` if present — idempotent replay).

Self-verification:
- `uv run python scripts/check_single_writer.py` — exit 0 (registry-state remains sole writer of state).
- Test `test_approval_inbox_materializer_inserts_on_first_event` — emit `approval.inbox_opened` → assert row exists.
- Test `test_approval_inbox_materializer_upserts_on_replay` — emit same event twice → assert exactly one row.
- Test `test_approval_inbox_materializer_handles_chat_id_collision_with_new_thread` — operator opens NEW inbox (new event with same `operator_chat_id` but different `inbox_thread_id`) → row updated, NOT duplicated.

### AC3 — `approval.inbox_opened` event type + `ApprovalInboxOpenedPayload`

NEW event type. Add to `packages/events/src/events/payloads.py`:

```python
class ApprovalInboxOpenedPayload(BaseModel):
    """Operator opened a pinned approval inbox via /approvals command.

    Drives the registry-state ``approval_inbox`` table (Story 11.3 AC2).
    Read by clawhip-daemon to route ``task.approval_requested`` events
    to the pinned inbox instead of the originating task thread (FR63).

    Idempotent: re-emitting for the same operator_chat_id replaces the
    previous inbox_thread_id (operator opened a new inbox; old one
    deprecated).
    """
    model_config = ConfigDict(frozen=True, strict=True)

    operator_chat_id: int = Field(ge=-2**63, le=2**63 - 1)  # Telegram chat_id is int64
    inbox_thread_id: int = Field(ge=1)  # thread_id is positive int64
    opened_at: AwareDatetime
    opened_by_actor_id: str = Field(min_length=1, max_length=128)
```

Register at schema_version `1.1.0` in `services/registry-state/src/registry_state/domain/event_types.py`:
```python
register("approval.inbox_opened", "1.1.0", ApprovalInboxOpenedPayload)
```

Re-export from `packages/events/src/events/__init__.py` via `_payloads_all`.

Constraints:
- **`operator_chat_id` allows negative values** — Telegram group/supergroup chat IDs are negative int64.
- **`inbox_thread_id >= 1`** — Telegram thread IDs are positive.
- **`actor_id` `max_length=128`** — Story 11.2 P1-H1 codebase-wide invariant.

Self-verification:
- `uv run python scripts/check_event_registry.py` — exit 0 (`approval.inbox_opened` recognized).
- Test `test_approval_inbox_opened_rejects_negative_thread_id`.
- Test `test_approval_inbox_opened_rejects_zero_thread_id` (Telegram-spec: thread_id >= 1).
- Contract fixture `tests/contract/fixtures/approval.inbox_opened.v1.1.0.json` + round-trip test.

### AC4 — `clawhip-daemon` outbound routing modified to check pinned-inbox state

Modify `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` around line 102 (where `task.approval_requested` is currently routed):

```python
# Schematic — actual via implementation
if envelope.type == "task.approval_requested":
    operator_chat_id = _derive_operator_chat_id(envelope)  # existing pattern
    pinned_inbox = await _read_pinned_inbox(operator_chat_id)  # NEW
    if pinned_inbox is not None:
        target_thread_id = pinned_inbox.inbox_thread_id
        message = _render_approval_request_with_task_link(payload, original_task_thread_id)
    else:
        target_thread_id = original_task_thread_id  # existing behavior preserved
        message = _render_approval_request(payload)
    await bot.send_message(chat_id=operator_chat_id, message_thread_id=target_thread_id, ...)
```

Constraints:
- **Backwards-compat preserved**: operators who haven't run `/approvals` still get approval requests in the originating task thread (no behavior change for them).
- **Link-back format**: rerouted messages MUST include "↩ Original task thread: <link>" footer with Telegram t.me link (or task-thread-id reference depending on chat type — verify aiogram).
- **Read-only access to registry-state**: clawhip-daemon already uses `EventLogReader` (read-only per P2-I1). Add a SQLite read query for `approval_inbox` via existing `services/registry-state` adapter (or refactor into `packages/events` shared package if direct registry-state import not allowed — check Story 10.2 P2-I1 read-only-subscriber rule).
- **Caching**: pinned-inbox lookup hits the DB on every `task.approval_requested` event. With current low approval-volume this is fine, but document as out-of-scope risk for future caching layer (e.g., 30-second TTL cache in clawhip-daemon).

Self-verification:
- Integration test `test_telegram_sink_routes_to_pinned_inbox_when_open` — seed `approval_inbox` row, emit `task.approval_requested`, assert `bot.send_message` called with `message_thread_id=<inbox_id>` + link-back to original.
- Integration test `test_telegram_sink_routes_to_task_thread_when_no_inbox` — empty `approval_inbox`, emit event, assert backwards-compat (original task thread).

### AC5 — Replay test: 10 approval requests → all in pinned thread with link-back

Per epics.md AC: "Replay test: 10 approval requests for 10 different tasks arrive in pinned thread; each has a working link back to original."

Integration test `tests/integration/test_journey_approval_inbox.py` (NEW):
1. Operator runs `/approvals` (or seed `approval_inbox` row via fixture).
2. Emit 10 `task.approval_requested` envelopes with distinct `task_id`s + distinct originating task thread_ids.
3. Drain via clawhip-daemon (or call telegram_sink directly with mocked bot).
4. Assert: `bot.send_message` called 10 times, all with `message_thread_id=<inbox_id>`, each message body contains a link-back to its respective original task thread.
5. Cleanup: clear `approval_inbox`.

Constraints:
- **Test marker**: `@pytest.mark.integration` (existing convention).
- **No Docker dependency** if possible (in-process via mock bot). If Docker required for full stack, mark `@pytest.mark.slow`.
- **Use `datetime.now(UTC).date()` for fixture dates** — Story 10.5 hotfix lesson.

Self-verification:
- Test exists at `tests/integration/test_journey_approval_inbox.py`.
- 10 messages all routed to pinned thread; each contains link-back.

### AC6 — Allowlist + tier discipline

The `/approvals` command MUST:
- Pass through existing allowlist middleware (Story 3.2 pattern — operator-only).
- Be categorized at Tier 2 (operator-action, MCP write surface) per Epic 6's capability tiers.
- If a non-allowlisted user tries, fail silently or emit `capability.denied` (DD5 from Epic 10 retro → Story 11.2.1 will wire emission; for now just silent-drop per existing middleware).

Self-verification:
- Test `test_approvals_command_silent_drops_non_allowlisted` — non-allowlisted user invocation does NOT create thread, does NOT emit `approval.inbox_opened`, does NOT reply.

### AC7 — Mypy --strict baseline extension

Approximate growth: new handler module (~150 lines) + new payload class + new SQLite table + materializer change + clawhip-daemon routing change. Expected baseline shift: **130 → ~133** source files.

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber services/telegram-gateway services/clawhip-daemon 2>&1 | tail -2` reports new count + exit 0.

### AC8 — Validation gates

- `uv run ruff check . && ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber services/telegram-gateway services/clawhip-daemon` — exit 0
- `uv run python scripts/check_imports.py` — exit 0 (telegram-gateway → packages/events allowed; telegram-gateway → registry-state NOT allowed — must go via event emission)
- `uv run python scripts/check_event_registry.py` — exit 0 (`approval.inbox_opened` recognized)
- `uv run python scripts/check_single_writer.py` — exit 0 (registry-state remains sole state writer)
- `uv run pytest -x -q services/telegram-gateway services/registry-state services/clawhip-daemon packages/events tests/contract tests/integration/test_journey_approval_inbox.py` — all green
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green

---

## Developer context

### Existing state (post Story 11.2)

- **Telegram-gateway handlers**: `services/telegram-gateway/src/telegram_gateway/handlers/` contains 11 existing handler files (`approve_command.py`, `reject_command.py`, `task_command.py`, etc.) — established pattern with allowlist middleware + aiogram message handling. Tests live at `services/telegram-gateway/src/telegram_gateway/test_*_command.py`.
- **`task.approval_requested` event**: registered + `TaskApprovalRequestedPayload` exists in `packages/events/src/events/payloads.py:277`.
- **Outbound delivery (clawhip-daemon)**: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py:102` currently routes `task.approval_requested` to the originating task thread. Lines 511/549/567/582/648/715 contain rendering helpers.
- **Registry-state schema**: `services/registry-state/src/registry_state/schema.py` (single file — not a directory). Materializers in `services/registry-state/src/registry_state/domain/`.
- **Single-writer rule (FR26)**: registry-state is sole writer. Telegram-gateway MUST emit events via clawhip-bridge MCP — NEVER write directly to SQLite.
- **Story 11.2 just closed**: `task.approval_signed` + `key.rotated` + `capability.denied` registered; `actor_id max_length=128` codebase-wide invariant established.
- **Mypy `--strict` baseline:** 130 source files.

### Architecture compliance

- **FR63** — Operator-facing approval inbox UX with pinned thread routing.
- **FR26** — Single-writer rule: registry-state is sole state writer; telegram-gateway emits events, clawhip-daemon reads via existing materialized state.
- **P2-I1** — Read-only-subscriber rule: clawhip-daemon reads `approval_inbox` via existing registry-state read adapter (not direct SQLite).
- **NFR-S10** — Allowlist middleware enforced; non-allowlisted users silent-dropped.
- **Trace-id (Epic 9)** — `/approvals` command + `approval.inbox_opened` event + downstream `task.approval_requested` rerouting all share `trace_id` for operator correlation.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `aiogram` | already pinned | Telegram bot API. `create_forum_topic`, `pin_chat_message`, `send_message(message_thread_id=...)`. |
| `pydantic` | already pinned | `AwareDatetime`, `Field`, `ConfigDict`. |
| `sqlalchemy` | already pinned | New `ApprovalInbox` ORM model + migration. |
| `events` workspace | already wired | New `ApprovalInboxOpenedPayload`. |
| no new deps | — | Zero new third-party dependencies. |

### File-structure requirements

```
services/telegram-gateway/src/telegram_gateway/
├── handlers/
│   └── approvals_command.py            # NEW — /approvals handler
├── test_approvals_command.py           # NEW — unit tests

packages/events/src/events/
├── payloads.py                          # MODIFY — add ApprovalInboxOpenedPayload
└── __init__.py                          # MODIFY — re-export

services/registry-state/src/registry_state/
├── schema.py                            # MODIFY — add ApprovalInbox ORM model
├── domain/
│   ├── event_types.py                   # MODIFY — register("approval.inbox_opened", "1.1.0", ...)
│   └── event_subscribers.py (or similar) # MODIFY — materializer for approval.inbox_opened
├── adapters/
│   └── approval_inbox.py (or similar)   # NEW or MODIFY — read query for clawhip-daemon
└── migrations/
    └── <next>_approval_inbox.py         # NEW — alembic-style migration adding approval_inbox table

services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/
└── telegram_sink.py                     # MODIFY — routing check for pinned inbox

tests/integration/
└── test_journey_approval_inbox.py       # NEW — 10-message replay test

tests/contract/fixtures/
└── approval.inbox_opened.v1.1.0.json    # NEW
```

### Testing requirements

- **Pyramid**: unit tests for `/approvals` handler (mocked aiogram) + materializer unit tests (mocked session) + clawhip-daemon routing unit tests + 1 integration test for full replay journey.
- **Test isolation**: each test constructs its own SQLite in-memory or `tmp_path`-based DB.
- **No hardcoded dates** in test fixtures (Story 10.5 hotfix lesson — use `datetime.now(UTC)` or fixture-parameter).
- **Allowlist middleware**: tests must NOT bypass the middleware — exercise the full chain.

### Previous-story intelligence

#### From Story 11.2 (just closed)

- **`actor_id` codebase-wide `Field(min_length=1, max_length=128)` invariant established** (P1-H1). New `ApprovalInboxOpenedPayload.opened_by_actor_id` follows this.
- **`AwareDatetime` for timezone-aware fields** (Story 11.2 KeyRotatedPayload pattern).
- **Contract-fixture forward-compat pair convention** (Story 11.2 AC4 pattern).
- **`_EVENT_FAMILIES` discipline** (P1-H2) — `approval` family already in `_EVENT_FAMILIES` (Story 11.2 didn't add it; it was registered in Story 10.4 already). NO change needed for `approval.inbox_opened` family routing.

#### From Story 11.1

- **`SecretStr` discipline** — n/a (no key handling in this story).
- **Pipe-injection guard** — n/a (no canonical-string signing).
- **`structlog` for log calls** (P1-H5) — telegram-gateway already uses structlog; new handler follows.

#### From Epic 10 retro (AI-2 — canonical source citation)

- `clawhip-daemon` reads `approval_inbox` via the registry-state read adapter at `services/registry-state/src/registry_state/adapters/...`. **Cite the exact adapter path in the implementation** (currently unknown — discover during implementation).

### Trade-off notes

- **`approval.inbox_opened` event vs direct registry-state write**: chose event. Reason: FR26 single-writer rule. Telegram-gateway → clawhip-bridge MCP → event-log → registry-state materializer. Adds one event type but preserves the architectural invariant.
- **`approval_inbox` table key by `operator_chat_id` (NOT `inbox_thread_id`)**: chose chat-id. Reason: one inbox per operator chat; thread_id can change if operator re-runs `/approvals` (e.g., archived first thread). UPSERT-on-chat-id is the simpler invariant.
- **Pin via thread (forum topic) vs pin a chat message**: chose forum topic. Reason: Telegram's modern Forum-Topic UX is the canonical way to create a persistent labeled thread. Requires the bot to have permission to create topics in the operator's chat (verify upstream — if not, fall back to "starred message" UX).
- **Caching in clawhip-daemon**: NOT caching. Approval-request volume is operationally low; DB hit per event is fine. Document as out-of-scope risk for future optimization.
- **No per-task inbox config (e.g., separate inbox for "high-priority" tasks)**: out of scope. Single inbox per operator chat. Phase 3 work if operator demand surfaces.
- **No `/approvals close` command**: out of scope. Operator can ignore the inbox or manually unpin/archive. Phase 3 work.

### Lessons from prior reviews to apply

- **AI-2 canonical-source citation**: spec cites `services/telegram-gateway/.../handlers/approve_command.py` as reference handler pattern; cites `clawhip-daemon/.../telegram_sink.py:102` as integration point.
- **AI-3 Decisions block pre-impl**: D1-D5 below.
- **AI-4 no hardcoded dates**: tests use `datetime.now(UTC)` or fixture parameters.
- **AG-2 empirical "no X anywhere"**: AC8 self-verification includes `check_single_writer.py` to enforce that telegram-gateway doesn't accidentally write to SQLite directly.
- **Story 11.1 P1-H5 structlog**: new handler uses structlog keyword-arg form.
- **Story 11.2 P1-H1 `actor_id max_length=128`**: applied in AC3.

### Non-goals (do NOT do in 11.3)

- **`/approvals close` command** → Phase 3.
- **Multiple inboxes per operator** (e.g., one per task category) → Phase 3.
- **Inbox-thread message templates customization** → Phase 3 ops docs.
- **`capability.denied` emission for non-allowlisted `/approvals` calls** → Story 11.2.1 (DD5 follow-up) will wire emission; Story 11.3 just silent-drops per existing middleware.
- **`/approvals` UX in console-cli** → out of scope (this is operator-Telegram-only per FR63).
- **Migrating existing scattered approval threads** → operator manually archives if desired.

## Out-of-scope risk flags

- **aiogram `create_forum_topic` permission**: bot must have `can_manage_topics` permission in the operator's chat. If absent: handler must fail gracefully with an actionable error message ("Bot lacks can_manage_topics permission — grant via Telegram chat settings"). Document in handler docstring + add test `test_approvals_command_handles_missing_can_manage_topics_permission`.

- **`approval_inbox` table on existing deployments**: requires a migration. New `migrations/` entry must be backward-compatible (no DROP of existing data; pure additive). Verify migration test suite covers this.

- **Race condition: simultaneous `/approvals` from same operator chat**: if operator sends `/approvals` twice in quick succession, both could create new threads before the first event materializes. Mitigation: UPSERT on `operator_chat_id` (last-write-wins). Telegram itself rate-limits commands; document as accepted edge case.

- **clawhip-daemon → registry-state read path**: if no existing pattern, may need new adapter. Check `services/clawhip-daemon/src/clawhip_daemon/adapters/` for existing registry-state-read adapter. If absent: D5 below resolves.

- **Existing pinned thread but Telegram archived it**: operator may have manually archived the thread. clawhip-daemon would still try to send to a non-existent/archived thread. Mitigation: catch aiogram's "chat not found" / "thread closed" error and emit a structured warning; do NOT fall back to task thread silently (operator chose to archive; they should reopen via `/approvals`). Add test for archived-thread error handling.

- **Trace-id propagation across `/approvals` → `approval.inbox_opened` event**: ensure the command handler creates a new `trace_id` (or reuses operator-session trace_id if such concept exists). Cite Story 9.6's trace_id propagation kernel.

## Decisions (resolved before implementation)

- **D1 — Event-driven materialization (NOT direct registry-state write).** FR26 single-writer rule. Telegram-gateway emits `approval.inbox_opened` via clawhip-bridge MCP; registry-state materializer creates the row.
- **D2 — `approval_inbox` table primary key = `operator_chat_id`.** One inbox per operator chat. UPSERT semantics on duplicate events (operator re-ran `/approvals`).
- **D3 — Pin via Telegram Forum Topic (modern UX), NOT pinned-chat-message (legacy).** Bot needs `can_manage_topics` permission. Fallback handling for missing permission documented as graceful-fail.
- **D4 — No caching in clawhip-daemon's inbox lookup.** DB hit per `task.approval_requested` event is fine at current volume. Future optimization out of scope.
- **D5 — clawhip-daemon reads `approval_inbox` via existing registry-state read adapter** (or new one if absent). NO direct SQLite import — goes through the established adapter pattern. Check `services/clawhip-daemon/src/clawhip_daemon/adapters/` for the existing pattern during implementation; if no read-adapter for registry-state exists, create one in `services/registry-state/src/registry_state/adapters/` (e.g., `approval_inbox_reader.py`) per the existing `event_log.py` precedent.

## Definition of done

- All 8 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `11-3-approvals-pinned-thread-handler: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- 1 new event type + 1 new SQLite table + 1 new Telegram handler + 1 routing change in clawhip-daemon.
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, surprises/deviations, D5 outcome — which adapter pattern was used).
- No regressions in: existing handler tests, materializer tests, separability tests, full pytest suite.
- 10-message replay integration test passes.

---

## Frontmatter

```yaml
---
story_id: 11.3
story_key: 11-3-approvals-pinned-thread-handler
parent_epic: 11
phase: 2
fr_refs: [FR63]
nfr_refs: [FR26]  # single-writer rule cited
arch_refs:
  - "Trace-id propagation kernel (Epic 9) — command + event + downstream routing share trace_id"
  - "Read-only-subscriber rule (P2-I1) — clawhip-daemon reads approval_inbox via registry-state adapter"
  - "Single-writer rule (FR26) — telegram-gateway emits event; registry-state materializes"
estimated_hours: 4-6
priority: high (Epic 11 operator-facing UX — most-visible Phase 2 win per epics.md)
blocks:
  - 11.5 (key rotation can also surface notifications in the approval inbox if desired)
  - epic-11-retrospective
blocked_by:
  - 11.1 (HMAC signing infrastructure — done)
  - 11.2 (event-type registration pattern established — done)
status: ready-for-dev
created: 2026-05-20
created_by: bmad-create-story skill
---
```
