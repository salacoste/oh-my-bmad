# Phase 34 Epics — Task Status Filter Route Selection Planning

## Phase 34 theme

Phase 34 continues the dashboard/API route-family sequence after Phase 33 closed the exact digest-stream read boundary. It selects one future task-list/search/discovery surface for separate runtime/API proof:

- Family: read-only task-list/search/discovery
- Exact future candidate: `GET /v1/tasks?status={task_status}`
- Allowed selector domain: one explicit task lifecycle status from `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`

Non-selected surfaces remain future-only and fail-closed:

- free-text task search and arbitrary discovery;
- multi-field filtering, arbitrary query language, pagination/cursor/offset/limit controls, and sort controls;
- automatic task/detail/digest/history/trace/replay/session drill-down from returned rows;
- replay execution target selection;
- lifecycle apply/prune/rollback, snapshot deletion/restore, archive/manifest mutation, and other mutation/control behavior;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 113.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 113 — Task status filter dashboard/API route boundary

### Objective

Plan, prove, and close a bounded dashboard/API route boundary for one status-filtered task-summary list through `GET /v1/tasks?status={task_status}` without free-text search, arbitrary discovery, hidden selectors, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM behavior, background retry/refresh behavior, or mutation/control side effects.

### Story 113.1 — Task status filter route selection planning

**Status:** done after sequential Architect APPROVE/CLEAR and Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 34 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the read-only task-list/search/discovery family and exactly `GET /v1/tasks?status={task_status}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 34 PRD amendment exists and selects read-only task-list/search/discovery and exactly `GET /v1/tasks?status={task_status}` as the future candidate surface.
2. Phase 34 architecture amendment defines exact route, finite status selector vocabulary, single-query-key boundary, bounded row-shape expectations, freshness/provenance requirements, fail-closed states, no hidden discovery/traversal, no replay/lifecycle mutation, and deferred-surface boundaries.
3. Phase 34 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 113.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status opens Phase 34/Epic 113, marks Story 113.1 done after Architect/Critic consensus, and leaves Story 113.2/113.3 backlog.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim status-filter runtime/API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 113.2 — Task status filter runtime/API contract boundary

**Status:** backlog — not authorized until Story 113.1 has sequential Architect then Critic approval.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/tasks?status={task_status}` with narrow additive API/runtime tests and no broader dashboard/API expansion.

**Acceptance criteria planned:**

1. Tests prove only `GET /v1/tasks?status={task_status}` is newly reachable for this slice.
2. Dashboard/API calls are GET-only with exactly one `status` query key and no request body.
3. Accepted statuses are limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`.
4. Extra query keys, repeated status keys, empty/unknown values, encoded nested parameters, hidden selectors, storage, cookies, hashes, and generated selector sources fail closed.
5. Output preserves bounded aggregate task-list row shape and adds only selected-status/filter metadata needed for authority/freshness.
6. Free-text search, arbitrary filters, pagination/offset/cursor/limit/sort controls, saved searches, hidden discovery, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, and mutation/control calls are not introduced.
7. Missing/invalid selector, empty result, backend unavailable, unauthorized/configuration failure, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload render non-authoritative fail-closed copy.
8. Filtered list output exposes source route, selected status, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, count/has_more, and degraded-state metadata.
9. Existing dashboard/API runtime-boundary suites remain green.
10. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS or explicit proportional QA, push, and remote CI green are recorded before runtime completion is claimed.

### Story 113.3 — Phase 34 / Epic 113 final validation closure

**Status:** backlog — not authorized until Story 113.2 implementation, review, QA, push, and remote CI evidence exist.

**Intent:** Complete docs/status final closure only after Story 113.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact names exact implemented status-filter route, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply free-text search, arbitrary discovery, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 113 done only after all Epic 113 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 113.1 must complete with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before status-filter runtime/API work is authorized.
2. Story 113.2 must remain route-local to `GET /v1/tasks?status={task_status}` and must not add free-text search, arbitrary discovery, pagination/sort controls, generated live data, browser-side LLM behavior, broad dashboard wiring, replay execution target selection, lifecycle mutation behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 113.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 113.3 may run only after implementation, final review, verifier/QA decision, push, and remote CI evidence exist.
4. Replay execution target selection and lifecycle apply/prune/rollback remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-27T16:32:18Z
