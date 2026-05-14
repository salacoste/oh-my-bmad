# Story 7.5.2: Session bulk close + compound index

Status: done

## Story

As the system,
I want session cleanup to close ALL active sessions for a task in a single bulk UPDATE,
So that stale sessions cannot leak if multiple active sessions exist, and the query is efficient with a proper compound index.

During code review of Story 7.7 (worktree-lock blocker persistence), the Blind Hunter and Edge Case Hunter identified three related issues in `_close_active_session_for_task`: (D1) it uses `.limit(1)` so only one session is closed even if multiple active sessions exist, (D2) the query filters on both `task_id` and `status` but only `ix_sessions_task_id` exists — no compound index, and (D3) the ORM loads a row, mutates attributes, and flushes when a bulk `UPDATE ... WHERE` would be more efficient and simultaneously solve D1. The system enforces single-session-per-task via `handle_task_execution_started`, but a defensive bulk close is more robust.

## Acceptance Criteria

1. **AC-1: Bulk UPDATE** — `_close_active_session_for_task` is refactored to use a bulk `UPDATE ... WHERE task_id=... AND status IN (...)` statement that closes all matching sessions in one operation, not just the first.
2. **AC-2: Compound index** — A compound index on `(sessions.task_id, sessions.status)` is added to the session schema, covering the query used by `_close_active_session_for_task`.
3. **AC-3: Existing tests pass** — All existing handler tests, registry-state tests, and registry-api tests continue to pass after the refactor.

## Tasks / Subtasks

- [x] **Task 1: Refactor `_close_active_session_for_task` to bulk UPDATE** (AC: #1)
  - [x] In `services/registry-state/src/registry_state/domain/handlers.py` (lines 121-142), replace the ORM load-mutate-flush pattern with a bulk `update(SessionRow).where(SessionRow.task_id == task_id, SessionRow.status.in_(["active", "idle"])).values(status="closed", ended_at=ended_at, worktree_path=None)` statement.
  - [x] Remove the `.limit(1)` and `.order_by` clauses that are no longer needed.
  - [x] Keep the `select` import (used by `_task_id_for_session` at line 116).
  - [x] Update the helper's docstring to reflect the bulk behavior (closes ALL active/idle sessions for the task, not just the latest).

- [x] **Task 2: Add compound index to session schema** (AC: #2)
  - [x] In `services/registry-state/src/registry_state/schema.py`, add `Index("ix_sessions_task_id_status", Session.task_id, Session.status)` in the index declarations section (after line 231).
  - [x] Replace the existing `ix_sessions_task_id` index with the compound `ix_sessions_task_id_status` — the compound index's left-prefix covers task_id-only queries, eliminating the redundant single-column index.

- [x] **Task 3: Update tests** (AC: #3)
  - [x] In `services/registry-state/src/registry_state/domain/test_handlers.py`, add a new test:
    - `test_close_active_session_for_task_closes_all_sessions` — seed a task with 3 active sessions, call the helper directly, assert ALL 3 are closed.
  - [x] Verify existing `test_task_stop_requested_closes_active_session` and `test_task_completed_closes_active_session` still pass (they exercise the helper via full handler flow).

- [x] **Task 4: Run full regression suite** (AC: #3)
  - [x] `uv run pytest services/registry-state/ -x -q` passes (290 passed).
  - [x] `uv run pytest services/registry-api/ -x -q` passes (114 passed).
  - [x] `uv run ruff check` clean on all modified files.

## Dev Notes

### Origin and Context

Three deferred items from Story 7.7 code review converge on a single refactor:

- **D1** (Blind Hunter + Edge Case Hunter) — `_close_active_session_for_task` uses `.limit(1)`, so if multiple active sessions exist for a task, only one is closed. The system enforces single-session-per-task via `handle_task_execution_started`, but a defensive bulk close is more robust.
- **D2** (Blind Hunter) — The query `WHERE task_id = ? AND status IN (...)` filters on both columns but only has an index on `task_id`. SQLite must post-filter by `status`. A compound index covers the query directly.
- **D3** (Blind Hunter + Edge Case Hunter) — The ORM loads a `SessionRow` object, mutates 3 attributes (`status`, `ended_at`, `worktree_path`), and flushes. A bulk `update()` statement is more efficient and eliminates the `.limit(1)` issue from D1.

All three are resolved by a single refactor: replace the ORM load-mutate-flush with a bulk `UPDATE` and add the compound index.

### Key Files (exact paths + line numbers)

| File | Lines | What changes |
|------|-------|-------------|
| `services/registry-state/src/registry_state/domain/handlers.py` | 121-142 | Refactor `_close_active_session_for_task` to bulk UPDATE |
| `services/registry-state/src/registry_state/schema.py` | After 231 | Add `Index("ix_sessions_task_id_status", Session.task_id, Session.status)` |
| `services/registry-state/src/registry_state/domain/test_handlers.py` | TBD | Add bulk-close test + verify existing tests |

### Architecture Compliance

- **Single-writer discipline**: `registry-state` is the ONLY service that writes to the sessions table. This refactor stays within the existing materializer handler flow (no new writers).
- **Idempotency**: The bulk UPDATE is idempotent — re-running with the same `task_id` + `ended_at` produces the same state (rows already closed stay closed).
- **Replay safety**: The helper must remain a no-op when no active sessions exist (materializer replays events that may already have been applied).
- **SQLAlchemy 2.x async**: Use `update()` statement (already imported at line 30), NOT raw SQL. Execute via `await session.execute(stmt)`.
- **UTCDateTime convention**: The `ended_at` parameter is already a UTC-aware `datetime` from the event envelope — no conversion needed.

### Code Pattern to Follow

The existing `_touch_task` helper (handlers.py lines 80-107) demonstrates the bulk UPDATE pattern used throughout this file:

```python
stmt = update(Task).where(Task.id == task_id).values(**values)
result = cast(CursorResult[tuple[()]], await session.execute(stmt))
```

Follow this same pattern for `_close_active_session_for_task` — use `update(SessionRow).where(...).values(...)` instead of the ORM load-mutate-flush. Do NOT use `cast(CursorResult[...])` or check `rowcount` since the bulk close is intentionally a no-op when zero rows match.

### Previous Story Intelligence (7.5.1)

- **Testing pattern**: Test at the appropriate layer. For session handlers, test directly via `AsyncSession` + handler calls (not through HTTP or MCP layers).
- **Regression**: After changes, run both `registry-state/` and `registry-api/` test suites — the API service has read-only queries against the sessions table that must remain compatible.
- **Commit style**: Use conventional commits with scope, e.g. `fix(registry-state): refactor session close to bulk UPDATE (Story 7.5.2)`.

### References

- [Source: deferred-work.md — D1, D2, D3 (story 7.7 code review)]
- [Source: epic-7-retro-2026-05-13.md — items 2 (HIGH) and 4 (MEDIUM)]
- [Source: services/registry-state/src/registry_state/domain/handlers.py — lines 121-142]
- [Source: services/registry-state/src/registry_state/schema.py — Session model lines 130-145, index section lines 216-237]

## Dev Agent Record

### Implementation Plan

1. Refactor `_close_active_session_for_task` to use bulk `update()` statement
2. Add compound index `ix_sessions_task_id_status` to schema.py
3. Write 4 new tests: bulk close (3 sessions), idle sessions, no-op, already-closed
4. Run full regression on registry-state + registry-api

### Debug Log References

### Completion Notes

- Replaced ORM load-mutate-flush with single `update(SessionRow).where(...).values(...)` statement
- Removed `.limit(1)`, `.order_by()`, `select()` usage from the helper (kept `select` import for `_task_id_for_session`)
- Replaced standalone `ix_sessions_task_id` with compound `ix_sessions_task_id_status` index
- Added Alembic migration `0003` to drop old index and create compound index
- Bulk close now targets `["active", "idle", "reconnecting"]` for future-proofing (Story 7.8)
- 8 new tests: bulk close (3 sessions), idle close, no-op, already-closed, cross-task isolation, mixed active+idle, non-existent task, index metadata
- Existing session-close tests continue to pass
- Full regression: 293 registry-state passed, 114 registry-api passed, ruff clean

## File List

- `services/registry-state/src/registry_state/domain/handlers.py` — refactored `_close_active_session_for_task` to bulk UPDATE (active, idle, reconnecting)
- `services/registry-state/src/registry_state/schema.py` — replaced `ix_sessions_task_id` with compound `ix_sessions_task_id_status`
- `services/registry-state/src/registry_state/domain/test_handlers.py` — added 8 bulk-close tests + helper
- `services/registry-state/src/registry_state/migrations/versions/2026-05-13_0003_session_compound_index.py` — Alembic migration for index swap

## Review Findings

### Re-review (2026-05-13, after code-review fixes applied)

- [x] [Review][Patch] `"reconnecting"` status added to bulk close IN list [handlers.py:134] — resolved: user chose to add `"reconnecting"` now for future-proofing.
- [x] [Review][Patch] Missing Alembic migration for compound index [schema.py:232, migrations/versions/2026-04-24_0001_initial_schema.py:64] — resolved: added migration `0003_session_compound_index.py`.
- [x] [Review][Patch] Story Task 2 says "keep ix_sessions_task_id" but implementation removes it — resolved: updated Task 2 to reflect the actual decision.
- [x] [Review][Patch] Completion Notes undercount tests — resolved: updated to reflect 8 tests and all patches applied.
- [x] [Review][Defer] `assert isinstance` stripped in Python -O mode — pre-existing pattern used by all handlers. Deferred: not introduced by this story.
- Dismissed (5): transaction atomicity (materializer framework), missing flush (SQLAlchemy autoflush), test imports private function (standard practice), no concurrency test (single-writer), `worktree_path=None` in docstring (adequate), test 8 metadata vs query plan (adequate), silent on non-existent task (by design).

## Change Log

- 2026-05-13: Story implemented — session close refactored to bulk UPDATE, compound index added, 8 tests (4 original + 4 from review fixes). All ACs satisfied. 293 + 114 tests pass, ruff clean.
- 2026-05-13: Code review fixes applied — H1 cross-task isolation test, H2/H3 already-closed false-positive fix, M1 redundant index removal, M2 idle-session assertions, M3 mixed active+idle test, M4 non-existent task test, M5 plural docstrings, L1 module-level imports, L2 index metadata test.
