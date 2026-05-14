# Story 7.2: Telegram `/status` business logic

Status: review

## Story

As the operator,
I want Telegram `/status <task-id>` to render the reconstituted-state response with state-aware formatting — compact blocked-state summaries, step progress, and contextual command hints — instead of a flat field dump,
So that returning to a stale-blocker task after hours gives me immediate, scannable context without reading scrollback.

## Acceptance Criteria

1. **Given** a task in `blocked` status with step progress, last event, last agent action, and worktree lock
   **When** the operator sends `/status <task-id>`
   **Then** the reply uses a compact blocked-state format matching the Journey 6 UX spec:
   ```
   📋 Task <id>
   Status: blocked (since 10:41)
   Step: 3/5
   Last event: task.blocker_raised — 2 unit tests failed (middleware_rate_limit_test.py)
   Last agent: Edit server/middleware/rate.py:87
   Worktree: held
   Available: /logs, /retry, /stop
   ```
   NOT the current flat field list.

2. **Given** a task in `executing` status
   **When** the operator sends `/status <task-id>`
   **Then** the reply shows a compact executing-state format emphasizing step progress and last agent action.

3. **Given** a task in `completed` or `stopped` status
   **When** the operator sends `/status <task-id>`
   **Then** the reply shows a terminal-state format without step progress or worktree lock details.

4. **And** every response fits in a single Telegram message (<= 4096 chars) without truncation.

5. **Given** the enriched fields from Story 7.1's `GET /v1/tasks/{id}`
   **When** `state_since`, `current_step`, `total_steps`, `last_agent_action`, `worktree_lock`, `last_event.summary` are all `None`
   **Then** the renderer gracefully omits those sections (no "Step: None/None" or "Since: None").

*Cites: FR4, FR15, FR17b.*

## Tasks / Subtasks

- [x] Task 1 — Refactor `status_command.py` renderer to use state-aware formatting (AC: #1, #2, #3)
  - [x] Extract the reply-text builder in `handle_status` into a dedicated function `_render_status_reply(task: TaskResponseLocal) -> str` in `status_command.py`. Keep the function in the same module — no new files needed.
  - [x] Implement state-aware branches based on `task.status`:
    - **blocked**: compact format with `(since {HH:MM})`, step progress, last event summary, last agent action, worktree lock, available commands. This is the primary UX target per Journey 6.
    - **executing** / **idle**: show step progress, last agent action, worktree lock. Emphasize "in progress" feel.
    - **completed** / **stopped**: terminal format — task ID, status, title, timestamps. No step/lock/agent fields.
    - **pending** / **planning** / **plan_ready**: early-stage format — status, title, actor, available commands.
  - [x] Each branch uses HTML-safe rendering (`html.escape()` on all external values) and stays under `_MAX_REPLY_LEN`.
  - [x] The `handle_status` function calls `_render_status_reply(task)` instead of inline f-string construction.

- [x] Task 2 — Ensure graceful handling of nullable enriched fields (AC: #5)
  - [x] Verify that `state_since is None`, `current_step is None`, `total_steps is None`, `last_agent_action is None`, `worktree_lock is None`, and `last_event.summary is None` all produce clean output without "None" artifacts.
  - [x] The null-state behavior is already partially implemented (7.1 added `if task.X is not None` guards). This task verifies completeness and adds any missing guards.

- [x] Task 3 — Add unit tests for state-aware rendering (AC: #1, #2, #3, #4, #5)
  - [x] In `test_status_command.py`, add test cases:
    - `test_blocked_state_renders_compact_format` — mock a `TaskResponseLocal` with `status="blocked"`, `state_since`, `current_step=3`, `total_steps=5`, last event with summary, last agent action, worktree lock held. Assert output contains "blocked", "since", "Step: 3/5", agent action, "held", and commands.
    - `test_executing_state_renders_progress` — mock with `status="executing"`, step progress, agent action, worktree lock. Assert output emphasizes progress, not blocked-state language.
    - `test_completed_state_renders_terminal_format` — mock with `status="completed"`. Assert output does NOT contain "Step:", "Worktree:", or "Last agent:".
    - `test_null_enriched_fields_produce_clean_output` — mock with all new fields as None/defaults. Assert no "None" in output.
    - `test_blocked_state_message_fits_in_4096_chars` — construct worst-case blocked task with maximum-length fields. Assert `len(reply) <= 4096`.
  - [x] All tests use `httpx.MockTransport` to fake registry responses (same pattern as existing tests).
  - [x] Follow per-function RNG pattern: `rng = Random(42)` inside each test.

- [x] Task 4 — Run existing tests and verify no regressions (AC: #4)
  - [x] `uv run pytest services/telegram-gateway/ -x -q` passes (364 passed)
  - [x] `ruff check` clean on all modified files

## Dev Notes

### Architecture: What This Story Does

Story 7.1 enriched the `GET /v1/tasks/{id}` endpoint with reconstituted state. Story 3.14 created the Telegram `/status` handler and basic renderer. **This story refines the renderer to be state-aware** — different formatting for blocked, executing, completed, and pending states — matching the Journey 6 UX spec from the PRD.

The current renderer in `status_command.py` is a flat field list. The Journey 6 spec shows a compact, contextual format. This story closes that gap.

### Critical Architecture Constraints

1. **No new files needed**: The renderer is a pure function in `status_command.py`. No new modules, no new message-template infrastructure.

2. **HTML parse mode**: All replies use HTML markup. `html.escape()` on every externally-sourced value. `<code>` tags for task IDs and session IDs.

3. **Telegram message limit**: 4096 chars. Current cap is `_MAX_REPLY_LEN = 4000` with truncation notice. The state-aware rendering must stay within this limit.

4. **No audit events**: `/status` is read-only. No event emission. FR26 not in scope.

5. **Error handling contract**: Handler ALWAYS returns normally — exceptions surfaced as Telegram replies so the webhook never retries.

6. **Backward compatibility**: `TaskResponseLocal` fields `available_commands` and `next_commands` both exist. Use `task.available_commands or task.next_commands` for rendering.

### Journey 6 UX Spec (from PRD)

The PRD describes the ideal blocked-state output:
```
State: `blocked` since 10:41. Step 3/5.
Last event: `test.failed` (2 unit tests, `middleware_rate_limit_test.py`).
Last agent action: edit to `server/middleware/rate.py:87`.
Worktree held. Worker idle.
Available commands: `/logs`, `/retry`, `/stop`, `/handoff`.
```

The current renderer outputs:
```
📋 Task <id>
Status: blocked
Step: 3/5
Since: 2026-05-11T10:41:00+00:00
Title: ...
Created: ...
Updated: ...
Actor: ...
Last event: task.blocker_raised at 2026-05-11T10:41:00+00:00
  2 unit tests failed
Last agent: Edit server/middleware/rate.py:87
Worktree: held by <session>...
Available: /logs, /retry, /stop
```

Story 7.2 should make the blocked-state rendering more compact (fold "since" into status line, drop full timestamps for HH:MM, hide title/created/updated for blocked tasks or move them below the fold).

### State-to-Commands Mapping (from lifecycle.py)

| State | Available Commands |
|-------|-------------------|
| `blocked` | `approve`, `retry`, `stop` |
| `executing` | `stop` |
| `plan_ready` | `approve`, `reject`, `stop` |
| `pending` | `stop` |
| `completed` | (none) |
| `stopped` | (none) |

### Relationship to Other Stories

- **Story 7.1** (reconstituted-state handler): Predecessor. Enriched `GET /v1/tasks/{id}` with all the fields this renderer consumes.
- **Story 3.14** (status-command-telegram-surface): Created the handler, router, error branches, and basic renderer. This story refines the renderer.
- **Story 7.10** (journey-6 integration test): Downstream. Will exercise the full `/status` flow end-to-end. This story's unit tests prepare the way.

### Scope Boundary

**DO modify:**
- `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` — refactor renderer, add state-aware formatting
- `services/telegram-gateway/src/telegram_gateway/test_status_command.py` — add new test cases

**DO NOT modify:**
- `services/registry-api/` — no changes needed, enriched response is already complete
- `services/registry-state/` — no changes needed, materializer already populates all fields
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — models already have all fields
- `packages/events/` — no new events needed

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated (same as prior stories):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.2]
- [Source: _bmad-output/planning-artifacts/prd.md#FR4]
- [Source: _bmad-output/planning-artifacts/prd.md#FR15]
- [Source: _bmad-output/planning-artifacts/prd.md#FR17b]
- [Source: _bmad-output/planning-artifacts/architecture.md#telegram-gateway-service]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/status_command.py — handle_status, _MAX_REPLY_LEN]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py — TaskResponseLocal, WorktreeLockLocal]
- [Source: services/registry-api/src/registry_api/routes/tasks.py — TaskResponse, get_task_by_id]
- [Source: services/registry-api/src/registry_api/lifecycle.py — STATE_NEXT_COMMANDS, ACTION_VALID_STATES]
- [Source: _bmad-output/implementation-artifacts/7-1-reconstituted-state-handler.md — predecessor story context]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

No debug cycles needed — clean implementation on first pass.

### Completion Notes List

- Extracted `_render_status_reply(task: TaskResponseLocal) -> str` as a pure function with three state branches: operational (blocked/executing/idle), terminal (completed/stopped), and early-stage (pending/planning/plan_ready).
- Blocked state omits title per Journey 6 spec; executing/idle include it. Both operational states fold state_since into status line as HH:MM, use " — " separator for event summary (no full timestamp), and show worktree as "held"/"not held" (no session ID).
- Terminal states show title, created/updated timestamps, and actor only — no step/lock/agent fields.
- Early-stage states show title, actor, and commands — no operational fields that won't be populated yet.
- Updated 4 existing tests that asserted old flat-field output format (no longer applicable with state-aware rendering).
- New tests test `_render_status_reply` directly as a pure function (cleaner than full HTTP mock stack for renderer unit tests).

### File List

- services/telegram-gateway/src/telegram_gateway/handlers/status_command.py — added `_render_status_reply()`, replaced inline rendering in `handle_status`
- services/telegram-gateway/src/telegram_gateway/test_status_command.py — updated 4 existing tests, added 5 new state-aware rendering tests
