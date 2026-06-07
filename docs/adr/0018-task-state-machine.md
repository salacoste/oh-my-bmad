---
id: ADR-0018
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0018: Task state machine — formal lifecycle for task state transitions

## Status

**Accepted** — 2026-06-07. Resolves **GATED-ARCH D4** (deferred state-machine debt since Phase 1, tracked from Story 3.10 review). Gates the multi-task parallelism feature in Phase 6. Extends the event spine as the single source of truth for task lifecycle.

## Context

Phase 1 introduced task status tracking in Epic 5. The `Task` ORM carries a `status` field updated directly by various handlers — the task driver, the materializer, the cancel endpoint — without a formal state machine. Transitions are unguarded: any handler can set any status at any time.

GATED-ARCH D4 has tracked this as deferred architectural debt since the Story 3.10 review. The implicit states in use are `created`, `queued`, `running`, `completed`, `failed`, and `cancelled`, but nothing prevents an invalid transition (e.g. `completed` → `running`, or `cancelled` → `queued`).

Phase 6 multi-task parallelism makes this debt acute. Multiple workers claiming tasks concurrently need unambiguous state transitions to prevent race conditions — two workers must not both claim the same task. A formal finite state machine is the standard solution.

The event spine already carries enough information to derive state transitions: `task.created`, `task.started`, `task.completed`, `task.failed`, `task.cancelled`. The FSM consumes these events; it does not replace them.

## Decision

### D1: Formal FSM

A `TaskStateMachine` class in `domain/task_fsm.py` defines explicit states, transitions, and guard conditions. States: `CREATED`, `QUEUED`, `ASSIGNED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`.

Permitted transitions:

| From | To | Trigger |
|---|---|---|
| `CREATED` | `QUEUED` | `task.created` event |
| `QUEUED` | `ASSIGNED` | Worker claim via `task.assigned` event |
| `ASSIGNED` | `RUNNING` | `task.started` event |
| `RUNNING` | `COMPLETED` | `task.completed` event |
| `RUNNING` | `FAILED` | `task.failed` event |
| `RUNNING` | `CANCELLED` | `task.cancelled` event |
| `QUEUED` | `CANCELLED` | Operator cancel before assignment |
| `ASSIGNED` | `QUEUED` | Worker timeout / reclaim |

Terminal states (`COMPLETED`, `FAILED`, `CANCELLED`) admit no further transitions. All transitions not listed above are invalid.

### D2: Invalid transitions raise `InvalidStateTransition`

The FSM is the sole authority on state changes. Calling `TaskStateMachine.transition(current, trigger)` with an invalid combination raises `InvalidStateTransition` (a domain exception). Direct `Task.status` mutations outside the FSM are forbidden — the materializer calls the FSM to validate and execute every transition. This makes invalid-state bugs fail loudly at the domain layer rather than silently corrupting downstream projections.

### D3: Event-driven transitions

State transitions are triggered by events on the spine. The materializer calls the FSM to validate and execute transitions in response to `task.*` events. The FSM is therefore testable in isolation (pure function of current state + trigger event → next state) and the event spine remains the single source of truth. No out-of-band status mutations bypass the event → FSM → materializer path.

## Consequences

- **Positive:** Resolves GATED-ARCH D4 — state-machine debt tracked since Phase 1 is closed.
- **Positive:** Unambiguous task assignment for multi-worker parallelism. Two workers claiming the same task is a state-violation caught by the FSM guard on `QUEUED → ASSIGNED`.
- **Positive:** Invalid state transitions caught at the domain layer (`InvalidStateTransition`) rather than surfacing as silent data corruption in downstream projections.
- **Positive:** The FSM is a pure function, testable with a table-driven test covering every valid and invalid transition.
- **Negative:** All existing `Task.status` update sites must be refactored to go through the FSM — a non-trivial refactoring surface across the task driver, materializer, and cancel endpoint.
- **Negative:** State transition events add to the event spine cardinality (the `task.assigned` event is new; existing events gain FSM-validation side effects).

## Alternatives considered

- **Database-level state constraints (CHECK / trigger).** Rejected — couples state logic to the storage engine; the FSM belongs in the domain layer where it is testable and runtime-portable.
- **Optimistic concurrency via version column only (no FSM).** Rejected — prevents double-claim at the storage level but does not prevent logically invalid transitions (e.g. `completed` → `running`). The FSM catches both classes of error.
- **Status enum without transition guards.** Rejected — this is the current situation (implicit states, unguarded transitions). It is the problem being solved, not a viable alternative.

— *R2d2, 2026-06-07 (accepted; via the BMad Phase-6 planning chain).*
