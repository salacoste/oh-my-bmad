# Phase 23 Epics — Task Detail Live-Read Runtime Boundary

Phase 23 is **task-detail-boundary-first**. It opens the next live-read phase after Health/readiness and selects exactly `GET /v1/tasks/{task_id}` as the next future route family. Story 102.1 does not implement runtime live dashboard wiring, frontend scripts, backend routes, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

## Requirements traceability

- **FR191 — Phase 23 task-detail scope**
- **FR192 — Exact route selection**
- **FR193 — Separate implementation story**
- **FR194 — Task identifier boundary**
- **FR195 — Provenance and freshness visibility**
- **FR196 — Task-detail state semantics**
- **FR197 — No hidden writes/effects**
- **FR198 — No behavior change in Story 102.1**
- **NFR-S33 — Task-detail fail-closed safety**
- **NFR-S34 — Read-only-by-effect enforcement**
- **NFR-O27 — Task-detail auditability**
- **NFR-M23 — Test-first runtime maintainability**
- **NFR-R23 — Safe task-detail degradation**

## Standard Phase 23 guardrail

Every Phase 23 story must preserve this rule: dashboard task-detail live-read work is read-only by effect; no mutation routes; no hidden writes; no writer imports; no lifecycle helper imports; no snapshot creation; no background-job dispatch; no idempotency writes; no cache-warming writes/read-side effects; no archive or manifest mutation; no aggregate/session/digest/task-list/search/discovery contract; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth. Story 102.1 is docs/status-only.

## Epic 102 — Task detail live-read runtime boundary

### Story 102.1: Phase 23 PRD, architecture, epics, and task-detail route selection

- Status: current docs/status planning story.
- Scope: create Phase 23 PRD, architecture, epics, Story 102.1 lifecycle artifact, and update sprint status to open Phase 23 / Epic 102.
- Selected route family: `GET /v1/tasks/{task_id}`.
- Governing FR/NFR: FR191, FR192, FR193, FR194, FR195, FR196, FR197, FR198, NFR-S33, NFR-S34, NFR-O27, NFR-M23, NFR-R23.
- Acceptance criteria:
  - Phase 23 PRD, architecture, and epics artifacts exist and define task-detail route-selection scope.
  - Story 102.1 artifact records lifecycle evidence, exact route selection, non-goals, verification plan, and future implementation obligations.
  - Sprint status sets `current_phase: 23`, opens Epic 102 / Story 102.1, preserves Epic 101 done, and records newest-first audit evidence.
  - Tracked diff is limited exactly to the five Story 102.1 docs/status files.
  - Runtime implementation, backend/API expansion, test-code changes, aggregate/session/digest/task-list/search/discovery/event/trace/history/replay/lifecycle contracts, and mutation/control surfaces remain explicitly unauthorized.
- Safety guardrails: docs/status-only; no runtime wiring; no backend/API route expansion; no source/test/dependency/CI change.

### Story 102.2: Task detail runtime-boundary implementation

- Status: future implementation story; not implemented by Story 102.1.
- Scope: implement the browser/runtime boundary for exactly `GET /v1/tasks/{task_id}` if fresh planning/review gates approve it.
- Governing FR/NFR: FR192, FR193, FR194, FR195, FR196, FR197, NFR-S33, NFR-S34, NFR-O27, NFR-M23, NFR-R23.
- Acceptance criteria:
  - Runtime-boundary tests prove only `/v1/tasks/{task_id}` is reachable for this slice.
  - Browser calls are GET-only; POST/PUT/PATCH/DELETE calls fail tests.
  - `task_id` is required, visible, and not obtained through hidden task-list/search/discovery behavior.
  - Source route, `task_id`, retrieved-at/freshness, authority, and degraded-state metadata are visible.
  - Healthy, unavailable, stale, unauthorized, and backend-unavailable states render bounded copy and do not falsely appear authoritative.
  - Static import/grep guards reject writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
  - Aggregate/session/digest/event/trace/history/replay/lifecycle/task-list/search/discovery routes remain excluded.
  - Existing health runtime-boundary and dashboard static/read-only regressions stay green.
  - Independent code-reviewer APPROVE, architect CLEAR, push, and CI green are recorded.
- Safety guardrails: one route family only; no broad live dashboard wiring; no mutation/control behavior.

### Story 102.3: Phase 23 final validation and closure

- Status: future closure story; not implemented by Story 102.1.
- Scope: close Phase 23 / Epic 102 after Story 102.2 or equivalent task-detail implementation is complete, reviewed, pushed, and CI-green.
- Governing FR/NFR: all Phase 23 FR/NFR.
- Acceptance criteria:
  - Phase 23 completion evidence cites Story 102.1 and Story 102.2 outcomes.
  - Sprint status marks Epic 102 done only after implementation and CI evidence are complete.
  - Closure explicitly states only the task-detail `GET /v1/tasks/{task_id}` boundary is complete; other live-read route families require separate stories.
- Safety guardrails: closure only; no new runtime behavior.

## Phase 23 completion criteria

Phase 23 can be considered complete only after docs/status opening, task-detail runtime-boundary implementation, independent review, UltraQA/pass-or-justified equivalent, push, CI green, and final closure are all recorded. Aggregate/session, digest, event timeline, transitions, trace, history, replay, lifecycle, task-list/search/discovery, and mutation/control surfaces remain deferred unless later stories explicitly approve them.
