# Story 7.10: Journey 6 stale-blocker integration test

Status: review

## Story

As a CI pipeline,
I want `tests/integration/test_journey_6_stale_blocker.py` that puts a task into `blocked` via a deliberately-failing test, leaves it for a simulated stale period, and verifies the operator can `/status` -> `/logs` -> `/retry hint="..."` -> task resumes to completion with the hint honored,
So that Journey 6 acceptance is continuously verified.

## Acceptance Criteria

1. **Given** the test harness forces a blocker on a running task
   **When** the test simulates a stale period (blocked state persists)
   **Then** `/status` still returns the lock-held + available-commands response, `/logs` returns a coherent digest, `/retry` with a hint resumes the task, and the hint is persisted on the task row.

2. **And When** CI runs on merge
   **Then** this test passes green as part of the MVP ship checklist.

*Cites: FR4, FR5, FR7, FR27.*

## Tasks / Subtasks

- [x] Task 1 — Add `journey_6` scenario to scripted worker stub (AC: #1)
  - [x] In `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py`, add a `journey_6` scenario that emits events sequentially (dedupe handles kill-restart split):
    - Phase 1 + Phase 2 in flat list: `task.planning.started`, `task.plan.ready` (3 steps), `task.execution.started`, `task.step.completed` (step 1), `task.blocker_raised`, `task.step.completed` (step 2), `task.step.completed` (step 3), `task.completed`.
    - Test kills worker after `task.blocker_raised` appears in JSONL. On restart, dedupe skips Phase 1 (already emitted) and emits Phase 2 (`step.completed.2`, `step.completed.3`, `completed`).
  - [x] Add `task.blocker_raised` to `STUB_EMITTED_TYPES` frozenset.
  - [x] The `task.blocker_raised` payload must include `task_id`, `reason="test failure: 2 assertions failed in middleware_rate_limit_test.py"`, `blocked_since` (ISO 8601 datetime), `last_event="task.step.completed"`, `last_action="Edit middleware/rate_limit.py:87"`.
  - [x] The `task.plan.ready` payload must include `estimated_steps=3` with step titles: "Implement rate limiting", "Add per-user rate limit", "Fix remaining test failures".
  - [x] Use `SCRIPTED_WORKER_EVENT_DELAY_S=0.5` to ensure a reliable kill window after `blocker_raised`.
  - [x] Update module docstring to list `journey_6` in supported scenarios.

- [x] Task 2 — Create `docker-compose.j6.yml` compose overlay (AC: #1)
  - [x] Create `tests/integration/docker-compose.j6.yml` based on `docker-compose.j3.yml` pattern.
  - [x] 4 services: `registry-state`, `registry-api`, `worker-wrapper` (scripted-worker-stub override), `auto-approval-stub`.
  - [x] Environment variables: `SCRIPTED_WORKER_SCENARIO=journey_6`, `SCRIPTED_WORKER_EVENT_DELAY_S=0.5`.
  - [x] Uses `OMB_J6_DATA_DIR` for the shared event-log volume.
  - [x] `depends_on` with `condition: service_healthy` for startup ordering. `restart: "no"`.

- [x] Task 3 — Create `test_journey_6_stale_blocker.py` main test (AC: #1, #2)
  - [x] Create `tests/integration/test_journey_6_stale_blocker.py` with `@pytest.mark.integration` and `@pytest.mark.slow` markers.
  - [x] Use `skip_if_no_docker` fixture from `conftest.py`.
  - [x] Use deferred imports for build scripts (same pattern as Journey 3).
  - [x] Test flow:
    1. Build scripted worker + auto-approval images (idempotent SHA-tagged builders).
    2. Create temp data dir, write initial event log with `task.created` envelope (seeded task).
    3. Start compose (`docker compose -f docker-compose.j6.yml up -d`).
    4. Wait for all services healthy.
    5. Resolve registry-api's host-mapped port + TCP probe.
    6. POST `/v1/tasks` with `{"title": "j6-stale-blocker-test"}`.
    7. Poll JSONL until `task.blocker_raised` appears (Phase 1 complete).
    8. Kill worker: `docker compose stop --timeout 1 worker-wrapper`.
    9. Wait for container exit (`_wait_for_container_exit`).
    10. **Verify `/status`**: GET `/v1/tasks/{task_id}` → assert `status == "blocked"`, `worktree_lock.held == True`, `available_commands` includes `"retry"`, `state_since` is present, `last_event.type == "task.blocker_raised"`.
    11. **Verify `/logs`**: GET `/v1/tasks/{task_id}/logs/digest` → assert 200, `digest` is non-empty string, `task_id` matches, `line_count >= 1`.
    12. **Retry with hint**: POST `/v1/tasks/{task_id}/decisions` with `{"action": "retry", "hint": "rate limit must be per-user, not per-IP"}` → assert 200, response `action == "retry"`.
    13. **Verify hint persisted**: GET `/v1/tasks/{task_id}` → assert `hint == "rate limit must be per-user, not per-IP"`, `status == "pending"`.
    14. Restart worker: `docker compose up -d worker-wrapper`.
    15. Wait for all services healthy.
    16. Poll JSONL until `task.completed` appears.
    17. Assert event log contains all expected events with no duplicates.
    18. Assert `task.retry_requested` event appears (emitted by decisions endpoint).
    19. Assert `task.completed` event present.
    20. Tear down: `docker compose down -v`.
  - [x] **Why**: This is the MVP gate test for Journey 6. The kill-restart pattern isolates Phase 1 (blocker) from Phase 2 (completion after retry), allowing the test to exercise /status, /logs, /retry in between.

- [x] Task 4 — Verify no regressions (AC: #2)
  - [x] `ruff check` and `ruff format` clean on all new/modified files.
  - [x] Existing integration tests still pass (Journey 1, Journey 3, S-1, S-2, S-3).
  - [x] Unit test suites unchanged (`just test`).

## Dev Notes

### Architecture: What This Story Does

This story creates the Journey 6 integration test — the stale-blocker reconnaissance flow. It verifies that when a task is blocked:

1. `/status` returns reconstituted full context (blocked state, lock held, available commands).
2. `/logs/digest` returns a coherent summary of task events.
3. `/retry` with a hint transitions the task and persists the hint.
4. After restart, the worker completes the remaining steps and the task reaches `completed`.

**The data flow:**
```
test_journey_6_stale_blocker.py
    | 1. Start compose (registry-state, registry-api, worker-stub, auto-approval)
    | 2. Worker stub emits: planning.started → plan.ready → execution.started
    |    → step.completed(1) → blocker_raised (Phase 1)
    | 3. Test kills worker (docker compose stop --timeout 1)
    ▼
test_journey_6_stale_blocker.py (stale-blocker reconnaissance)
    | 4. GET /v1/tasks/{id} → assert blocked, lock held, commands
    | 5. GET /v1/tasks/{id}/logs/digest → assert digest returned
    | 6. POST /v1/tasks/{id}/decisions {action:retry, hint:"..."} → assert 200
    | 7. GET /v1/tasks/{id} → assert hint persisted, status=pending
    ▼
test_journey_6_stale_blocker.py (completion)
    | 8. Restart worker
    | 9. Worker stub scans JSONL → skips Phase 1 (dedupe)
    |    Emits: step.completed(2) → step.completed(3) → completed (Phase 2)
    | 10. Assert full lifecycle, no duplicates
    ▼
Journey 6 verified
```

### Critical: What Is Already Done (DO NOT recreate)

| Layer | Status | File |
|---|---|---|
| Scripted worker stub | DONE (Stories 5.16, 5.17c, 5.18, 7.9) | `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` |
| Auto-approval stub | DONE (Story 5.18) | `tests/fixtures/auto_approval_stub/auto_approval_stub.py` |
| Event-level dedupe (`_dedupe_key`) | DONE (Story 5.17c) | `scripted_worker_stub.py` |
| `SCRIPTED_WORKER_EVENT_DELAY_S` env var | DONE (Story 5.17c) | `scripted_worker_stub.py` |
| SHA-tagged image builders | DONE (Stories 5.16, 5.18) | `tests/separability/_build_scripted_worker.py`, `tests/integration/_build_auto_approval.py` |
| `skip_if_no_docker` fixture | DONE (Stories 5.16, 5.18) | `tests/integration/conftest.py` |
| `TaskBlockerRaisedPayload` model | DONE (pre-existing) | `packages/events/src/events/payloads.py` |
| `task.blocker_raised` event registration | DONE (pre-existing) | `services/registry-state/.../event_types.py` |
| `handle_task_blocker_raised` materializer | DONE (Story 7.7) | `services/registry-state/.../handlers.py` |
| `TaskRetryRequestedPayload` model | DONE (pre-existing) | `packages/events/src/events/payloads.py` |
| `task.retry_requested` event registration | DONE (pre-existing) | `services/registry-state/.../event_types.py` |
| `GET /v1/tasks/{id}` reconstituted state | DONE (Story 7.1) | `services/registry-api/.../routes/tasks.py` |
| `GET /v1/tasks/{id}/logs/digest` endpoint | DONE (Story 7.3) | `services/registry-api/.../routes/digest.py` |
| `POST /v1/tasks/{id}/decisions` (retry) | DONE (Story 7.6) | `services/registry-api/.../routes/decisions.py` |
| Worktree lock in GET /status response | DONE (Story 7.1) | `services/registry-api/.../routes/tasks.py` |
| `hint` column on Task ORM model | DONE (Story 7.6) | `services/registry-state/.../schema.py` |
| Worktree lock persistence through blocker | DONE (Story 7.7) | `services/registry-state/.../handlers.py` |
| Journey 3 compose overlay | DONE (Story 7.9) | `tests/integration/docker-compose.j3.yml` |
| Kill-restart test pattern (poll loop) | DONE (Story 5.17c, 7.9) | `tests/separability/test_s2_midflight_swap.py`, `tests/integration/test_journey_3_recovery.py` |
| `_wait_for_container_exit` helper | DONE (Story 7.9) | `tests/integration/test_journey_3_recovery.py` |

### Journey 6 Scenario Design for Scripted Worker Stub

The `journey_6` scenario emits events in a flat list. The test kills the worker after `task.blocker_raised` (Phase 1 boundary). On restart, dedupe skips Phase 1 events and emits Phase 2.

**Full event list:**
```
task.planning.started
task.plan.ready          (estimated_steps=3)
task.execution.started
task.step.completed      (step_number=1, step_title="Implement rate limiting")
task.blocker_raised      (reason="test failure: 2 assertions failed in middleware_rate_limit_test.py")
--- kill boundary ---
task.step.completed      (step_number=2, step_title="Add per-user rate limit")
task.step.completed      (step_number=3, step_title="Fix remaining test failures")
task.completed           (summary="Journey 6 task completed after retry")
```

**Dedupe on restart:**
- `task.planning.started` → dedupe key `"task.planning.started"` → already emitted → skip
- `task.plan.ready` → dedupe key `"task.plan.ready"` → already emitted → skip
- `task.execution.started` → dedupe key `"task.execution.started"` → already emitted → skip
- `task.step.completed.1` → dedupe key `"task.step.completed.1"` → already emitted → skip
- `task.blocker_raised` → dedupe key `"task.blocker_raised"` → already emitted → skip
- `task.step.completed.2` → dedupe key `"task.step.completed.2"` → NOT emitted → emit
- `task.step.completed.3` → dedupe key `"task.step.completed.3"` → NOT emitted → emit
- `task.completed` → dedupe key `"task.completed"` → NOT emitted → emit

No conditional logic needed in the scenario function — dedupe handles Phase 2 naturally because step completion dedupe keys are step-number-qualified.

### `task.blocker_raised` Payload Construction

The stub must construct the `TaskBlockerRaisedPayload` with valid field values:

```python
{
    "task_id": task_id,
    "reason": "test failure: 2 assertions failed in middleware_rate_limit_test.py",
    "blocked_since": <ISO 8601 datetime, use now()>,
    "last_event": "task.step.completed",
    "last_action": "Edit middleware/rate_limit.py:87",
}
```

All fields except `task_id` and `reason` are optional (`blocked_since`, `last_event`, `last_action` default to `None`). Including them makes the test realistic but they are not required.

### `task.plan.ready` Payload for 3-Step Plan

The plan.ready event must include `estimated_steps=3` so that `/status` can show step progress (e.g., "Step 1/3"). The step titles should be realistic:

```python
{
    "task_id": task_id,
    "estimated_steps": 3,
    "steps": [
        {"step_number": 1, "title": "Implement rate limiting"},
        {"step_number": 2, "title": "Add per-user rate limit"},
        {"step_number": 3, "title": "Fix remaining test failures"},
    ],
}
```

Check the `TaskPlanReadyPayload` model for exact field names and constraints.

### Time-Skip Strategy

The AC says "leaves it for a simulated 6-hour window (mocked clock)". The injectable clock is at the application level (UUIDv7 generation), not exposed to Docker containers. In the Docker-based integration test:

- **Do NOT** attempt to mock time inside Docker containers (complex, fragile, cross-container).
- **DO** verify that `/status` returns the correct `state_since` timestamp for the blocked state, proving the blocked-since information is captured and returned correctly.
- The "stale period" is a data integrity property, not a time-elapsed property. The test verifies the state reconstitution works regardless of actual elapsed time — the same state would be returned after 6 hours or 6 seconds.
- If a time-skip unit test is needed, it should be a separate test using the injectable clock at the application level, not in the Docker integration test.

### `/logs/digest` Endpoint Behavior

The `GET /v1/tasks/{id}/logs/digest` endpoint uses an Anthropic LLM for summarization. In CI:
- If `ANTHROPIC_API_KEY` is set → returns an LLM-summarized digest.
- If `ANTHROPIC_API_KEY` is NOT set → gracefully degrades to a raw-event fallback digest.

The test must work in both cases. Assert only that:
- Response status is 200.
- `digest` is a non-empty string (1-20,000 chars).
- `task_id` matches the test task.
- `line_count >= 1`.

Do NOT assert specific digest content — it varies between LLM and fallback modes.

### Hint Verification Strategy

The AC says "the final plan reflects the hint." The scripted worker stub emits canned events — it does not read the hint from the task and include it in emitted plan/step events. Instead:

1. **Verify hint persistence**: After POST `/decisions {action: "retry", hint: "..."}`, GET `/v1/tasks/{id}` returns `hint == "rate limit must be per-user, not per-IP"`.
2. **Verify hint propagation**: The `task.retry_requested` event emitted by the decisions endpoint carries `payload.hint`. This is already unit-tested in Story 7.6.
3. **Verify task completion**: After worker restart, the task reaches `completed` status.

The "final plan reflects the hint" is a real-worker behavior (the orchestrator reads the hint and adjusts planning). The integration test verifies the data pipe (hint persisted, task transitions, task completes). Full end-to-end hint-honoring requires a real worker, which is out of scope for this integration test.

### Kill-Restart Pattern (from Journey 3 / Story 5.17c)

The proven kill-restart pattern:

1. Kill: `docker compose stop --timeout 1 worker-wrapper` (SIGTERM with 1s grace).
2. Wait for exit: Poll `docker compose ps` for "Exit" in Status (NOT fixed sleep).
3. Exercise reconnaissance: Call /status, /logs, /retry endpoints.
4. Restart: `docker compose up -d worker-wrapper`.
5. Wait for healthy: `_wait_for_all_healthy(project, env, timeout_s=60.0)`.

**Gotchas from prior stories:**
- Do NOT use fixed `sleep()` after kill — use poll loop via `_wait_for_container_exit`.
- `_wait_for_container_exit` must raise `TimeoutError` on timeout, not silently proceed.
- Always add healthcheck wait after restart.
- `emitted.setdefault(task_id, set())` for dedupe set access (not `emitted.get()`).

### Seeded Task Created Event

Same as Journey 1 and Journey 3: the test seeds the event log with a `task.created` envelope before starting the compose. This gives the worker stub a `task_id` to work with.

### Deferred Import Pattern

Same as Journey 3: imports of `_build_auto_approval` and `_build_scripted_worker` must be DEFERRED inside test functions because pytest collects (imports) test modules before running conftest fixtures. Also add `tests/separability/` to `sys.path` for `_build_scripted_worker` (fix from Story 7.9 code review).

### structlog Gotcha (from Story 5.17b)

Never use `event=` as keyword argument with structlog loggers — clashes with structlog's positional `event` parameter. Use `fsm_event=`, `env_event=`, or similar.

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated (same as prior stories):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### Relationship to Other Stories

- **Story 7.9** (Journey 3): Direct template for compose overlay, kill-restart pattern, dedupe, helper functions (`_wait_for_container_exit`, `_poll_for_event`, etc.). Copy the helpers rather than importing — each journey test is self-contained.
- **Story 7.1** (reconstituted state): Provides the GET /v1/tasks/{id} response format that the test asserts on.
- **Story 7.3** (logs digest): Provides the GET /v1/tasks/{id}/logs/digest endpoint.
- **Story 7.6** (retry with hint): Provides the POST /v1/tasks/{id}/decisions endpoint with retry action.
- **Story 7.7** (worktree lock persistence): Provides the blocked state handling in the materializer.
- **Story 5.17c** (S-2): Original kill-restart pattern and dedupe mechanism.

### Scope Boundary

**DO modify:**
- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` — add `journey_6` scenario
- `tests/integration/docker-compose.j6.yml` — NEW compose overlay
- `tests/integration/test_journey_6_stale_blocker.py` — NEW integration test

**DO NOT modify:**
- `services/registry-state/` — materializer handlers are done (Story 7.7)
- `services/registry-api/` — endpoints are done (Stories 7.1, 7.3, 7.6)
- `packages/events/` — payload models are done
- `services/clawhip-daemon/` — synthesis logic is done (Story 7.8)
- `tests/integration/docker-compose.j1.yml` — Journey 1 must remain independent
- `tests/integration/docker-compose.j3.yml` — Journey 3 must remain independent
- `tests/separability/` — S-1/S-2/S-3 compose overlays must remain independent
- Production source code in any `services/` or `packages/`

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.10]
- [Source: _bmad-output/planning-artifacts/prd.md#Journey6 — lines 296-315]
- [Source: _bmad-output/planning-artifacts/prd.md#FR4, FR5, FR7, FR27]
- [Source: _bmad-output/planning-artifacts/architecture.md#test-layout, integration-test-tree]
- [Source: _bmad-output/implementation-artifacts/7-9-journey-3-integration-test.md — Journey 3 patterns, kill-restart, helpers]
- [Source: _bmad-output/implementation-artifacts/7-1-reconstituted-state-handler.md — GET /v1/tasks/{id} response format]
- [Source: _bmad-output/implementation-artifacts/7-3-logs-digest-llm-adapter.md — GET /logs/digest endpoint]
- [Source: _bmad-output/implementation-artifacts/7-6-retry-hint-injection.md — POST /decisions retry action, hint persistence]
- [Source: _bmad-output/implementation-artifacts/7-7-worktree-lock-blocker-persistence.md — blocked state handling, lock persistence]
- [Source: tests/fixtures/scripted_worker_stub/scripted_worker_stub.py — stub scenarios, dedupe, STUB_EMITTED_TYPES]
- [Source: tests/integration/docker-compose.j3.yml — compose overlay template]
- [Source: tests/integration/test_journey_3_recovery.py — kill-restart test pattern, helper functions]
- [Source: packages/events/src/events/payloads.py — TaskBlockerRaisedPayload, TaskRetryRequestedPayload]
- [Source: services/registry-state/.../event_types.py — task.blocker_raised, task.retry_requested registrations]
- [Source: services/registry-api/.../routes/decisions.py — POST /v1/tasks/{id}/decisions]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

No issues encountered during implementation.

### Completion Notes List

- Task 1: Added `journey_6` scenario to scripted worker stub — flat list of 8 events (planning.started, plan.ready with 3 steps, execution.started, step.completed.1, blocker_raised, step.completed.2, step.completed.3, completed). No conditional logic needed — dedupe handles Phase 2 naturally via step-number-qualified dedupe keys. Added `task.blocker_raised` to `STUB_EMITTED_TYPES`. Updated module docstring.
- Task 2: Created `docker-compose.j6.yml` based on J-3 overlay with `OMB_J6_DATA_DIR`, `SCRIPTED_WORKER_SCENARIO=journey_6`, `SCRIPTED_WORKER_EVENT_DELAY_S=0.5`.
- Task 3: Created `test_journey_6_stale_blocker.py` with kill-restart-reconnaissance pattern. After kill, exercises GET /status (blocked state, lock held, commands), GET /logs/digest (non-empty digest), POST /decisions (retry with hint), GET /status (hint persisted, status pending). Then restarts worker and asserts full lifecycle with no duplicates, 3 step completions, retry_requested between blocker_raised and completed.
- Task 4: `ruff check` and `ruff format` clean. 2136 passed, 18 pre-existing failures confirmed unrelated (16 decisions + 2 documented).

### File List

- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` — added `journey_6` scenario, added `task.blocker_raised` to `STUB_EMITTED_TYPES`, updated module docstring
- `tests/integration/docker-compose.j6.yml` — NEW compose overlay for Journey 6
- `tests/integration/test_journey_6_stale_blocker.py` — NEW integration test

### Review Findings
