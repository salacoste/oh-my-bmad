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

**Status:** done and shipped/green after Story 126.1 consensus, Story 126.2 implementation/review/QA, implementation commit `8d6cfc6`, green remote `ci` run `28555502488`, and green remote `nightly` run `28565399310`.

**Intent:** Tests-first dashboard aggregate-task-list runtime update for the exact canonical full selector route. Reuse visible status, limit, offset, and two-value sort controls; validate response selected metadata; preserve explicit manual navigation; do not add search/discovery, hidden selectors, backend/API changes, broad rewiring, dependencies, credentials, or production operations.

**Scope:** `dashboard/static/aggregate-task-list.js`, `dashboard/static/index.html`, `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`, docs/status/story evidence.

## Dependency and sequencing notes

1. Story 126.2 depends on Story 125.2 API-local support and Story 125.1 browser-visible sort vocabulary.
2. Manual previous/next controls remain explicit, not automatic traversal.
3. Final Phase 47 closure/remote CI evidence is recorded by Story 126.3 after post-push reconciliation: commit `8d6cfc6`, green `ci` run `28555502488`, and green `nightly` run `28565399310`.


### Story 126.3 — Phase 47 / Epic 126 final closure

**Status:** done.

**Intent:** Docs/status-only post-push reconciliation for Phase 47 / Epic 126 browser full selector composition.

**Closure evidence:** implementation commit `8d6cfc664b9e85caf42ad5f0fe633ed10913584c` (`8d6cfc6`), green GitHub Actions `ci` run `28555502488`, and green GitHub Actions `nightly` run `28565399310`.

**Scope:** status/artifact docs only; no runtime, test, dependency, service, CI/deployment, credential, or production-operation changes.
