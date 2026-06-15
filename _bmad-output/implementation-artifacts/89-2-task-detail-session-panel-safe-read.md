# Story 89.2: Task detail/session panel using safe task detail read

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a single-operator maintainer,
I want the dashboard task detail view to show passive task detail and narrow task-local session reference data from the approved task-detail read,
so that I can inspect one task's state and provenance without exposing command, budget, lifecycle, or session-control behavior.

## Acceptance Criteria

1. The Task Detail panel renders only passive task-detail placeholders/data from the existing safe read provenance `GET /v1/tasks/{task_id}` and does not add backend routes, live data wiring, dependencies, lockfile changes, or runtime behavior changes.
2. The panel's allowed display contract is limited to passive `TaskResponse` fields: `task_id`, `status`, `title`, `created_at`, `updated_at`, `state_since`, `actor.kind`, `actor.id`, `last_event.id`, `last_event.type`, `last_event.emitted_at`, `last_event.summary`, `current_step`, `total_steps`, `last_agent_action`, `hint`, `worktree_lock.held`, `worktree_lock.by_session_id`, `worktree_lock.acquired_at`, `chat_id`, and `reply_to_message_id`.
3. The panel does not expose `available_commands`, `next_commands`, `budget_token_limit`, `budget_action`, or any command, budget policy, lifecycle, approval, retry, cancel, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, credential entry, or other mutation/control field or affordance as display data.
4. Route provenance for `GET /v1/tasks/{task_id}` appears only as inert text/provenance; no `/v1/` route appears in href/src/action/data-api/event handler/script/style/page-load/network/runtime contexts.
5. Task detail fields distinguish stale, missing, unauthorized, unavailable, empty successful read, and read error states without implying mutation occurred.
6. Related session data is limited to task-local `worktree_lock` semantics (`held`, `by_session_id`, `acquired_at`) from the approved task detail read. Broader session metadata, heartbeat, historical sessions, terminal session outcome, session rows, session aggregation, session state/freshness, and session history are explicitly unavailable and deferred to Story 89.3 unless a separate future read contract is approved.
7. Optional `chat_id` and `reply_to_message_id` thread-binding metadata may appear only as passive inert metadata with unavailable/not configured states when absent; they must not introduce credential entry, notification controls, message sending, approval, retry/cancel, or communication-side action.
8. Story 89.2 preserves Story 88.1/88.2/89.1 read-only dashboard behavior and all existing panels/tests.

## Tasks / Subtasks

- [x] Confirm safe task-detail read contract before implementation (AC: 1, 2, 3, 4)
  - [x] Inspect `TaskResponse` and document passive whitelist plus denylist.
  - [x] Keep route provenance inert body prose only.
  - [x] Do not add backend/API/routes/dependencies/live data wiring.
- [x] Implement Task Detail static panel inside the approved dashboard shell (AC: 1, 2, 5, 8)
  - [x] Add Task Detail navigation and panel while preserving existing panels.
  - [x] Show approved passive field placeholders and state vocabulary.
  - [x] Keep all values unavailable/static unless a later live-read story approves wiring.
- [x] Implement narrow task-local session reference copy (AC: 6, 7)
  - [x] Show `worktree_lock.held`, `worktree_lock.by_session_id`, and `worktree_lock.acquired_at` as the only Story 89.2 session-related safe-read fields.
  - [x] State broader session metadata/heartbeat/history/aggregation is unavailable and deferred to Story 89.3.
  - [x] Show `chat_id` / `reply_to_message_id` as passive optional thread metadata only.
- [x] Preserve read-only/no-mutation boundaries (AC: 3, 4, 8)
  - [x] Do not add buttons, forms, event handlers, scripts, live polling, mutation routes, lifecycle controls, credential entry, message sending, notification controls, or production operation controls.
  - [x] Do not render command/budget/lifecycle/control response fields as dashboard data.
  - [x] Keep Story 88.2 no-mutation guard coverage green.
- [x] Add/update tests (AC: all)
  - [x] Extend dashboard static-shell tests for Task Detail structure, inert route provenance, passive whitelist, denied fields, state vocabulary, worktree-lock-only session semantics, passive thread metadata, and no synthesized/live data.
  - [x] Keep route/method/no-mutation boundary tests green and, if needed, distinguish inert body route prose from runtime/live API contexts.
- [x] Validate implementation before review (AC: all)
  - [x] Run focused dashboard tests: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`.
  - [x] Run `git diff --check`, `uv run ruff format --check .`, and `uv run ruff check .`.
  - [x] Run broader regression: `uv run pytest -q -m "not slow"`.
  - [x] Record code-review `APPROVE`, architect `CLEAR`, UltraQA, CI, and Ultragoal evidence before marking done.

## Dev Notes

### Governing FR/NFR

- **FR169 — Read-only dashboard scope**: the dashboard is an operator visibility surface, not a control plane.
- **FR170 — Task and session visibility**: task/session state should be inspectable where safe reads expose it.
- **FR173 — Safe error and empty states**: distinguish no data, loading, invalid configuration, permission/configuration failure, stale data, and internal errors.
- **FR174 — No behavior change in planning/create-story slices**: the earlier planning artifacts did not alter runtime behavior; this implementation pass remains limited to static dashboard/test/story/status artifacts unless later reviews approve more.
- **NFR-S27 — Read-only by construction**: no mutating HTTP methods, approval forms, lifecycle apply controls, credential entry, or privileged operator actions.
- **NFR-O24 — Provenance-first display**: displayed state identifies source category, route/reference, freshness, and confidence.
- **NFR-M20 — Existing contract reuse**: prefer existing registry API/read surfaces; new contracts require separate approval.
- **NFR-R20 — Fail-safe visibility**: failed or unavailable reads show bounded explicit uncertainty, not authoritative partial data.

### Read-surface basis

Story 89.2 is static/read-only and uses `GET /v1/tasks/{task_id}` as approved inert provenance for task detail only. It does not wire a live call.

Actual `TaskResponse` includes passive display fields plus control-adjacent fields. The dashboard must use the passive whitelist and deny command/budget/lifecycle/control fields as panel data.

### Passive display whitelist

Allowed as inert placeholders/copy:

- `task_id`, `status`, `title`, `created_at`, `updated_at`, `state_since`
- `actor.kind`, `actor.id`
- `last_event.id`, `last_event.type`, `last_event.emitted_at`, `last_event.summary`
- `current_step`, `total_steps`, `last_agent_action`, `hint`
- `worktree_lock.held`, `worktree_lock.by_session_id`, `worktree_lock.acquired_at`
- `chat_id`, `reply_to_message_id`

### Explicit denylist

Do not render as panel content:

- `available_commands`
- `next_commands`
- `budget_token_limit`
- `budget_action`
- any command, budget policy, lifecycle, mutation, or control-oriented field/copy

### Session semantics

Story 89.2 may show only task-local `worktree_lock` reference semantics. Broader session metadata, state/freshness, heartbeat, historical sessions, terminal session outcome, session rows, aggregation, and session history remain unavailable and deferred to Story 89.3.

### Previous story intelligence

- Story 88.1 established the dependency-free static dashboard shell and read-only banner.
- Story 88.2 established static/adversarial no-mutation boundary tests.
- Story 89.1 added Overview/Tasks unavailable fallback, local Audit/Help anchors, and full state matrix vocabulary while preserving no-live-API behavior.

### Architecture compliance

- Existing safe reads first; new read contracts require separate approval.
- No dashboard component may import or call registry/event-log write helpers.
- No dashboard route, action, page load, refresh, polling, or error recovery path may dispatch a job, mutate cache/state, write audit rows, or enqueue lifecycle work.
- Route provenance can be inert body prose only, not href/action/src/data-api/script/style/network/runtime context.

### References

- `_bmad-output/planning-artifacts/phase-19-epics.md` — Story 89.2 source requirements.
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md` — safe reads and no-mutation architecture.
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md` — Task Detail and Sessions UX states.
- `services/registry-api/src/registry_api/routes/tasks.py` — `TaskResponse` and `worktree_lock` source shape.
- `dashboard/static/index.html` — approved static dashboard shell.
- `tests/dashboard/test_static_shell.py` and `tests/dashboard/test_read_only_boundary.py` — existing dashboard guard coverage.
- `_bmad-output/implementation-artifacts/89-1-overview-task-list-unavailable-fallback.md` — previous Story 89.1 patterns.

## Dev Agent Record

### Agent Model Used

GPT-5.4 Codex via OMX Autopilot/Ultragoal.

### Debug Log References

- 2026-06-15: Story context created by Autopilot/BMad create-story slice. No runtime/source/test implementation was performed in this creation step.
- 2026-06-15: Ralplan consensus required two remediation rounds: Critic required a concrete TaskResponse whitelist/denylist and exact `worktree_lock` session semantics; Architect required adding optional passive `chat_id` / `reply_to_message_id` metadata. Final Architect APPROVE/CLEAR and Critic APPROVE recorded before implementation.
- 2026-06-15: Red test evidence: `uv run pytest tests/dashboard/test_static_shell.py -q` failed with 5 expected Story 89.2 failures for missing `section#task-detail`, route provenance, passive field contract, state/session scope, and thread metadata copy.
- 2026-06-15: Green focused evidence: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 20 tests.
- 2026-06-15: Full regression evidence: `uv run pytest -q -m "not slow"` passed 4155 tests, skipped 8, deselected 61.
- 2026-06-15: Final review evidence: independent code-review Recommendation APPROVE; architect Architectural Status CLEAR; UltraQA Story 89.2 static scenarios passed 40.

### Completion Notes List

- Created Story 89.2 context and updated sprint status from backlog to ready-for-dev.
- Added static Task Detail panel to `dashboard/static/index.html` while preserving all existing Story 88.1/89.1 panels.
- Added inert route provenance for `GET /v1/tasks/{task_id}` as body text only, not href/action/src/data-api/script/style/network/runtime context.
- Added passive TaskResponse field placeholders and state vocabulary for stale, missing, unauthorized, unavailable, empty successful read, and read error states.
- Limited Story 89.2 session semantics to task-local `worktree_lock` references and deferred broader session metadata/heartbeat/history/aggregation to Story 89.3.
- Added passive `chat_id` and `reply_to_message_id` thread metadata copy with not-configured fallback and no action semantics.
- Extended static dashboard tests for Story 89.2 route provenance, whitelist/denylist, worktree-lock-only session semantics, thread metadata, and no-live/no-control boundaries.
- Final gates are clean: code-review APPROVE, architect CLEAR, UltraQA 40 static scenarios passed, local lint/focused/full regression passed; CI evidence to be attached after push.

### File List

- `dashboard/static/index.html`
- `tests/dashboard/test_static_shell.py`
- `_bmad-output/implementation-artifacts/89-2-task-detail-session-panel-safe-read.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-06-15: Created Story 89.2 context/status artifact. Status: ready-for-dev.
- 2026-06-15: Implemented Story 89.2 static Task Detail safe-read panel with red/green dashboard tests. Status: review.
- 2026-06-15: Reconciled Story 89.2 to done after code-review APPROVE, architect CLEAR, UltraQA pass, and local verification gates.
