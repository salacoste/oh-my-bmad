---
id: ADR-0019
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0019: Worker pool assignment — pull-based task claiming for multi-worker parallelism

## Status

**Accepted** — 2026-06-07. Gates the Phase 6 multi-task parallelism plane. Must be `accepted` before the first worker-pool story merges to `main`.

## Context

Phase 5 is complete (ADR-0016 accepted; all Phase 5 ship-blockers green; Epics 26–29 done). Each worker-wrapper instance currently processes one task at a time — the single-worker model is sufficient for Phase 5's runtime-abstraction work but leaves the fleet's capacity underutilized.

Phase 6 introduces multi-task parallelism via Docker Compose `docker compose up --scale worker-wrapper=N`. The key design question: how do multiple workers claim tasks without collision?

Two candidate models:

- **Push**: the orchestrator assigns tasks to specific workers (requires routing logic, worker registries, health-aware dispatch).
- **Pull**: workers claim available tasks from the registry (workers are interchangeable; the registry is the coordination point).

The existing architecture is already pull-based: worker-wrapper polls the task registry for pending tasks via MCP. The orchestrator-adapter signals task availability but never assigns to a specific worker.

## Decision

### D1: Pull-based claiming

Workers poll the task registry for tasks in `QUEUED` state and atomically claim them. Claiming = setting `task.worker_id` and transitioning `QUEUED -> ASSIGNED` in a single transaction.

- **Postgres**: `SELECT ... FOR UPDATE SKIP LOCKED` (standard worker-queue pattern; mature, well-understood, no custom locking).
- **SQLite**: `BEGIN EXCLUSIVE` transaction (only one writer at a time; assignment is fast enough that contention is negligible for single-operator scale).

### D2: Worker identity

Each worker instance has a unique `worker_id` = `<hostname>-<pid>` (or configurable via `WORKER_ID` env var). The `worker_id` is:

- Stamped on claimed tasks (`task.worker_id` column).
- Carried in events (`task.assigned` event payload) and metrics labels for observability.
- Used in logging to trace which worker processed which task.

## Consequences

- **Positive: Pull-based is simpler than push.** No orchestrator routing logic, no worker registry, no health-aware dispatch. The registry is the single coordination point.
- **Positive: Workers are interchangeable.** No sticky assignment needed; any worker can claim any QUEUED task. Scaling up is `docker compose up --scale worker-wrapper=N`.
- **Positive: Natural load balancing.** Fast workers claim more tasks; slow workers claim fewer. No central scheduler required.
- **Negative: Polling interval creates assignment latency.** Default 5-second poll interval means up to 5s delay between task creation and claim. Configurable via `WORKER_POLL_INTERVAL_SECONDS`.
- **Negative: New event type required.** The `task.assigned` event must be registered in `domain/event_types.py` at the next additive schema version.

## Alternatives considered

- **Push-based assignment (orchestrator dispatches).** Rejected — adds routing logic, worker health tracking, and retry/rebalance complexity. Pull-based achieves the same result with the registry as the single coordination point.
- **Randomized backoff to reduce contention.** Unnecessary at single-operator scale (single-writer SQLite or `SKIP LOCKED` on Postgres). Can be added later if fleet size warrants it.
- **Work-stealing (idle workers steal from busy workers).** Rejected — orders of magnitude more complex for no measurable benefit at the planned fleet size (2–5 workers).

## Linked artifacts

- ADR-0016 — Phase 5 gate (prerequisite: Phase 5 complete before Phase 6 opens).
- ADR-0015 — Multi-runtime adapter protocol (runtime dispatch that the worker pool builds on).

— *R2d2, 2026-06-07 (accepted; via the BMad Phase-6 planning chain).*
