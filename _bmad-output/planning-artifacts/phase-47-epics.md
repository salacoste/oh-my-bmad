# Phase 47 Epics — Browser Full Selector Composition

Generated: 2026-07-01T22:32:55Z

## Phase 47 theme

Expose the already implemented API-local full selector composition route in the dashboard/browser aggregate-task-list panel using visible controls only.

## Epic 126 — Dashboard/browser full selector composition

### Objective

Implement and verify exact browser consumption of `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}` from visible aggregate-task-list controls while keeping search/discovery and broad dashboard rewiring closed.

### Story 126.1 — Browser full selector composition planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Select the exact browser runtime boundary, test obligations, and non-goals for Phase 47.

**Scope:** planning/status artifacts only.

### Story 126.2 — Browser full selector composition runtime boundary

**Status:** ready for implementation after Story 126.1 consensus.

**Intent:** Tests-first dashboard aggregate-task-list runtime update for the exact canonical full selector route. Reuse visible status, limit, offset, and two-value sort controls; validate response selected metadata; preserve explicit manual navigation; do not add search/discovery, hidden selectors, backend/API changes, broad rewiring, dependencies, credentials, or production operations.

**Scope:** `dashboard/static/aggregate-task-list.js`, `dashboard/static/index.html`, `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`, docs/status/story evidence.

## Dependency and sequencing notes

1. Story 126.2 depends on Story 125.2 API-local support and Story 125.1 browser-visible sort vocabulary.
2. Manual previous/next controls remain explicit, not automatic traversal.
3. Final Phase 47 closure/remote CI evidence can be recorded in a later closure story if requested after push.
