# Story 5.17a: Resume-after-approval state machine (FSM + unit tests)

Status: done

## Story

As the worker,
I want `services/worker-wrapper/domain/lifecycle.py` defining a deterministic FSM with states `running` -> `awaiting_approval` -> `paused` -> `resumed` -> `completed`/`failed` and transitions driven exclusively by input events,
So that the state machine has an isolated, unit-tested core before it's coupled to cross-restart + idempotency concerns (pair-reviewed HIGH-RISK file).

## Acceptance Criteria

1. **AC-1 (Determinism):** Given the FSM receives input event sequence `[running, task.awaiting_approval, approval.granted]`, when transitions are applied, then the final state is `resumed` and the transition log is deterministic (same input = same state + same transition trace every time).

2. **AC-2 (Invalid transitions):** Given invalid transitions (e.g., `approval.granted` from `completed`), when they are fed into the FSM, then the FSM rejects with a typed `InvalidTransition` exception and the rejection is audited.

3. **AC-3 (Full coverage):** Given the unit test suite, when CI runs, then every state x input-event combination has explicit coverage and transition-table coverage is 100%.

4. **AC-4 (Zero IO):** The FSM module imports NO IO libraries (`fastapi`, `aiogram`, `sqlalchemy`, `aiohttp`, `mcp`, `httpx`, `asyncio`). Pure domain logic only. `scripts/check_imports.py` exits 0.

5. **AC-5 (Atomic commit):** title: `feat(worker): add resume-after-approval state machine with unit tests · E5`

6. **AC-6: `just lint` green, `just test` no regressions.**

## Tasks / Subtasks

- [x] **Task 1: Define FSM state enum and transition table** (AC: #1, #2)
  - [x] Create `services/worker-wrapper/src/worker_wrapper/domain/lifecycle.py`
  - [x] Define `WorkerState` enum: `RUNNING`, `AWAITING_APPROVAL`, `PAUSED`, `RESUMED`, `COMPLETED`, `FAILED`
  - [x] Define `LifecycleEvent` enum or string literal type for input events: `task.awaiting_approval`, `approval.granted`, `approval.rejected`, `task.completed`, `task.failed`, `task.paused`, `task.resumed`
  - [x] Define transition table as `dict[tuple[WorkerState, LifecycleEvent], WorkerState]`
  - [x] Implement `InvalidTransitionError` exception (domain-local, in lifecycle.py)
  - [x] Implement `TransitionLogEntry` dataclass: from_state, event, to_state
  - [x] Implement `LifecycleFSM` class with `current_state`, `transition(event) -> WorkerState`, `transition_log` property
  - [x] FSM must be deterministic: same initial state + same event sequence = same final state + same log

- [x] **Task 2: Write unit tests with 100% transition-table coverage** (AC: #3)
  - [x] Create `services/worker-wrapper/src/worker_wrapper/test_lifecycle.py` (co-located with source per existing worker-wrapper pattern)
  - [x] Test every valid (state, event) pair produces expected new state (13 parametrized)
  - [x] Test every invalid (state, event) pair raises `InvalidTransitionError` with audit info (29 parametrized + 1 message test)
  - [x] Test determinism: run same event sequence N times, assert identical final state + log
  - [x] Test the canonical approval flow: `RUNNING -> AWAITING_APPROVAL -> RESUMED -> COMPLETED`
  - [x] Test rejection flow: `RUNNING -> AWAITING_APPROVAL -> FAILED`
  - [x] Test pause/resume flow: `RUNNING -> PAUSED -> RESUMED -> COMPLETED`
  - [x] Verify transition-table coverage = 100% (parametrize over the full table)

- [x] **Task 3: Update domain __init__.py exports** (AC: #4)
  - [x] Export `WorkerState`, `LifecycleFSM`, `InvalidTransitionError`, `LifecycleEvent`, `TransitionLogEntry` from `services/worker-wrapper/src/worker_wrapper/domain/__init__.py`

- [x] **Task 4: Verification + commit** (AC: #5, #6)
  - [x] `ruff check` and `ruff format` clean
  - [x] `scripts/check_imports.py` exits 0 (1 pre-existing violation in test_reasoning.py — unrelated)
  - [x] `just test` no regressions (605 passed, 1 pre-existing failure in test_reasoning.py — unrelated)
  - [x] Atomic commit

## Dev Notes

### What already exists

**`services/worker-wrapper/src/worker_wrapper/domain/__init__.py`** — Currently exports `atomic_edit` and `worktree_lock`. Line 7 has placeholder comment: `Future inhabitants: state machine, lifecycle, resume-after-approval.` This is exactly where `lifecycle.py` goes.

**`services/worker-wrapper/src/worker_wrapper/domain/worktree_lock.py`** — Lock acquired at session start, released at session finish. FSM must keep lock held through `awaiting_approval` -> `paused` states (but 5.17a is pure FSM, no lock integration — that's 5.17b).

**`services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py`** — Atomic file-edit with secret scanning. Used during `running` state; must be idempotent on resume.

**`packages/events/src/events/errors.py`** — Contains `BudgetExceeded` with note `for future internal signaling (e.g. Story 5.17a resume-after-approval)`. Does NOT yet contain `InvalidTransition` — add it domain-locally in `lifecycle.py` to keep the FSM self-contained.

**`packages/events/src/events/payloads.py`** — Already has `TaskApprovalRequestedPayload` (line 269) and `TaskSelfRecoveredPayload` (line 492) which references `session.reconnecting` + `task.execution.resumed`. The FSM's event names should align with these existing payload types.

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| File location | `services/worker-wrapper/domain/lifecycle.py` | architecture.md ~line 833 |
| Zero IO imports | Domain layer cannot import `fastapi`, `aiogram`, `sqlalchemy`, `aiohttp`, `mcp` | check_imports.py enforcement |
| Event naming | `domain.action` past-tense with dots (e.g., `task.awaiting_approval`, `approval.granted`) | architecture.md |
| Exception naming | PascalCase, suffixed (e.g., `InvalidTransition`) | architecture.md |
| State enum naming | PascalCase (`Running`, `AwaitingApproval`) | architecture.md |
| Test location | `services/worker-wrapper/tests/test_lifecycle.py` | existing test file pattern |
| HIGH-RISK designation | Pair review required before merge | epics.md Story 5.17a |

### Key design decisions

1. **`InvalidTransition` is domain-local** — Not in `packages/events/errors.py`. The FSM is self-contained; the exception is only raised by the FSM and consumed by tests. If 5.17b needs it outside domain, it can import from here.

2. **`paused` state is first-class** — The epics mention it in the state list. Include it in the transition table even though no FR explicitly requires the pause flow yet. Downstream stories (5.17b, 6.7) will wire it.

3. **Scope is pure FSM only** — NO IO wiring, NO MCP calls, NO integration tests, NO session management. Those belong in Story 5.17b. The FSM is a pure function of (current_state, input_event) -> (new_state, audit_log).

4. **Transition table is the source of truth** — Define transitions as a data structure (dict), not scattered if/elif chains. This makes 100% coverage trivially verifiable by iterating the table.

5. **Determinism via immutable state** — The FSM should not use random, time, or external state. Given the same initial state and event sequence, the result must be identical.

### Scope boundary — what NOT to do

- Do NOT add any MCP client calls or event emission
- Do NOT add cross-restart recovery logic (that's 5.17b)
- Do NOT add idempotency cache integration (that's 5.17b)
- Do NOT add integration tests (that's 5.17b's `test_resume_after_approval.py`)
- Do NOT modify `packages/events/schema_registry.py` — the FSM doesn't emit events, it models state transitions
- Do NOT import from `adapters/` or `app/` layers

### Downstream consumers

- **Story 5.17b** — Plugs this FSM into idempotency cache (FR28) + reattach (FR29) + atomic-edit (FR30) + GitHub idempotency passthrough
- **Story 5.17c** — S-2 mid-flight swap separability test
- **Story 5.18** — Journey 1 integration test (MVP gate)
- **Story 6.7** — Worker approval-wait state (FR36 coupling with lifecycle)

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1688-1708 — Story 5.17a definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` FR36 — Worker approval-gated flows]
- [Source: `_bmad-output/planning-artifacts/prd.md` FR28 — Idempotency]
- [Source: `_bmad-output/planning-artifacts/prd.md` FR29 — Worker reattach after restart]
- [Source: `_bmad-output/planning-artifacts/architecture.md` ~line 833 — HIGH-RISK file designation + FSM states]
- [Source: `services/worker-wrapper/src/worker_wrapper/domain/__init__.py` — Placeholder for lifecycle.py]
- [Source: `packages/events/src/events/payloads.py` line 269 — TaskApprovalRequestedPayload]
- [Source: `packages/events/src/events/payloads.py` line 492 — TaskSelfRecoveredPayload]
- [Source: `packages/events/src/events/errors.py` — BudgetExceeded (InvalidTransition not yet defined)]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- FSM has 6 states, 7 events, 13 valid transitions, 2 terminal states (COMPLETED, FAILED)
- Transition table is the single source of truth — no if/elif chains
- 61 unit tests: 13 valid parametrized, 29 invalid parametrized + 1 message test, 18 behavioral tests
- Zero IO imports verified by check_imports.py (lifecycle.py is pure domain logic)
- test_lifecycle.py co-located with source in worker_wrapper package (matches existing test placement pattern)
- Named `InvalidTransitionError` (not `InvalidTransition`) per ruff N818 convention

### File List

- `services/worker-wrapper/src/worker_wrapper/domain/lifecycle.py` — NEW — FSM core (158 lines)
- `services/worker-wrapper/src/worker_wrapper/test_lifecycle.py` — NEW — Unit tests (258 lines, 61 tests)
- `services/worker-wrapper/src/worker_wrapper/domain/__init__.py` — MODIFIED — Added lifecycle exports

### Review Findings

- [x] [Review][Patch] Stale docstring: `Raises: InvalidTransition` should be `InvalidTransitionError` [lifecycle.py:140] — FIXED
- [x] [Review][Patch] Stale class docstring: "raises InvalidTransition" should be "raises InvalidTransitionError" [test_lifecycle.py:54] — FIXED
- [x] [Review][Defer] Transition log grows unboundedly [lifecycle.py:136] — deferred, by-design for finite worker lifecycle
- [x] [Review][Defer] AC-2 "rejection is audited" — exception carries audit data, not logged internally — deferred, acceptable for pure domain module
- [x] [Review][Dismiss] 8 findings dismissed: O(n) property negligible, private imports acceptable, thread safety not needed, naming choices justified
