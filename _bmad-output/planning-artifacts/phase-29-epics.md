# Phase 29 Epics — Aggregate/Session/Digest Route Selection Planning

## Phase 29 theme

Phase 29 continues the dashboard route-family sequence. It opens the **Aggregate/Session/Digest** branch as planning first, while narrowing the future runtime candidate to one exact task-scoped digest read surface. Runtime work is deferred to a separate story and final closure is deferred until review, QA, push, and CI evidence exist.

Selected family and future surface:

- Family: aggregate/session/digest
- Exact future candidate: `GET /v1/tasks/{task_id}/logs/digest`

Non-selected surfaces remain future-only and fail-closed:

- `/v1/tasks` aggregate/list/read contracts;
- `/v1/sessions` and `/v1/sessions/{session_id}` session list/detail contracts;
- `/v1/tasks/{task_id}/logs/digest/stream`;
- task-list/search/discovery;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, generated live data, cache warming, polling, timers, background workers, and automatic refresh;
- mutation/control behavior;
- services/MCP/dependencies/CI/deployment changes;
- backend/API expansion and production operations.

## Epic 108 — Aggregate/session/digest dashboard route boundary

### Objective

Plan and later prove a bounded dashboard route boundary for a task-scoped digest read through `GET /v1/tasks/{task_id}/logs/digest` without aggregate/session list contracts, digest streaming, task-list/search/discovery, broad dashboard wiring, hidden generated data, browser-side LLM behavior, or mutation/control side effects.

### Story 108.1 — Aggregate/session/digest route selection planning

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 29 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the aggregate/session/digest family and exactly `GET /v1/tasks/{task_id}/logs/digest` as the future runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 29 PRD amendment exists and selects the aggregate/session/digest family and exactly `GET /v1/tasks/{task_id}/logs/digest` as the future candidate surface.
2. Phase 29 architecture amendment defines exact route, visible task_id selector, digest provenance/freshness, fail-closed degraded states, no-hidden-generation, no-streaming, no-aggregate/session, no-discovery, and deferred-surface boundaries.
3. Phase 29 epics file exists and sequences planning before runtime-boundary implementation and final closure.
4. Story 108.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 29`, keeps Epic 107 done, opens Epic 108, marks Story 108.1 done, and leaves Story 108.2/108.3 backlog.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim digest runtime implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 108.2 — Task log digest runtime boundary

**Status:** backlog.

**Intent:** Implement a separately approved, tests-first dashboard runtime boundary for exactly `GET /v1/tasks/{task_id}/logs/digest`.

**Future acceptance criteria:**

1. Tests prove only `GET /v1/tasks/{task_id}/logs/digest` is reachable for this slice.
2. Dashboard calls are GET-only and body-free.
3. The only selector is a visible task_id; no query/hash/storage/session/discovery/list/aggregate-derived selector is accepted.
4. `/v1/tasks/{task_id}/logs/digest/stream`, `/v1/tasks`, `/v1/sessions`, `/v1/sessions/{session_id}`, task-list/search/discovery, aggregate/session traversal, and broad dashboard wiring remain unreachable.
5. No browser-side LLM generation, prompt construction, hidden summarization, external provider calls, generated live data, cache warming, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, automatic refresh, or automatic retry loops are introduced.
6. Missing task_id, unavailable/missing digest, no configured digest provider, provider unavailable, timeout, non-2xx, invalid response, empty digest, stale, unauthorized, and backend-unavailable states render non-authoritative fail-closed copy without auto-retry.
7. Digest output is bounded display content with source route, visible task_id, retrieved-at/completed-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata.
8. Digest text cannot become a route selector, control input, replay target, aggregate/session source, generated live-data substrate, or discovery/search input.
9. Existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, and snapshot-create runtime-boundary tests remain green.
10. Independent code-reviewer APPROVE and architect CLEAR are recorded.
11. Remote CI is green before runtime completion is claimed.

### Story 108.3 — Phase 29 / Epic 108 final validation closure

**Status:** backlog.

**Intent:** Complete docs/status final closure only after Story 108.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented digest route, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply aggregate/session contracts, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, backend/API expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 108 done only after all Epic 108 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 108.1 must complete before any dashboard digest runtime work is authorized.
2. Story 108.2 must remain route-local to `GET /v1/tasks/{task_id}/logs/digest` and cannot add aggregate/session contracts, digest streaming, task-list/search/discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, backend/API expansion, mutation/control behavior, or production operations.
3. Story 108.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. Task-list/search/discovery and aggregate/session list/detail remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.
