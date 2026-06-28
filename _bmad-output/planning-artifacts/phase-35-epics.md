# Phase 35 Epics — Task List Limit Route Selection Planning

## Phase 35 theme

Phase 35 continues the dashboard/API route-family sequence after Phase 34 closed the exact task-status-filter read boundary. It selects one future task-list sizing surface for separate runtime/API proof:

- Family: read-only task-list sizing / bounded-list control
- Exact future candidate: `GET /v1/tasks?limit={task_list_limit}`
- Allowed selector domain: an integer task_list_limit from 1 through 50 inclusive

Non-selected surfaces remain future-only and fail-closed:

- offset/cursor/page traversal, next-page tokens, infinite scroll, and pagination state;
- sorting controls;
- free-text task search, arbitrary discovery, multi-field filtering, arbitrary query language, and saved searches;
- status+limit combinations or any other selector composition;
- automatic task/detail/digest/history/trace/replay/session drill-down from returned rows;
- replay execution target selection;
- lifecycle apply/prune/rollback, snapshot deletion/restore, archive/manifest mutation, and other mutation/control behavior;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 114.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 114 — Task list limit dashboard/API route boundary

### Objective

Plan, prove, and close a bounded dashboard/API route boundary for one limit-selected first-page task-summary list through `GET /v1/tasks?limit={task_list_limit}` without pagination traversal, sorting, free-text search, arbitrary discovery, hidden selectors, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM behavior, background retry/refresh behavior, selector composition, or mutation/control side effects.

### Story 114.1 — Task list limit route selection planning

**Status:** done after sequential Architect APPROVE/CLEAR and Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 35 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the read-only task-list sizing family and exactly `GET /v1/tasks?limit={task_list_limit}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 35 PRD amendment exists and selects read-only task-list sizing and exactly `GET /v1/tasks?limit={task_list_limit}` as the future candidate surface.
2. Phase 35 architecture amendment defines exact route, finite bounded integer limit selector vocabulary, single-query-key boundary, bounded row-shape/order expectations, freshness/provenance requirements, fail-closed states, no pagination traversal/sorting/search/hidden discovery/traversal, no replay/lifecycle mutation, and deferred-surface boundaries.
3. Phase 35 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 114.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status initially opens Phase 35/Epic 114, marks Story 114.1 done after Architect/Critic consensus, and sequences Story 114.2/114.3 after planning consensus.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim task-list-limit runtime/API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 114.2 — Task list limit runtime/API contract boundary

**Status:** done after tests-first implementation, code-review APPROVE/CLEAR, UltraQA PASS, push, and green remote CI run `28306586314`.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/tasks?limit={task_list_limit}` with narrow additive API/runtime tests and no broader dashboard/API expansion.

**Acceptance criteria:**

1. Tests prove only `GET /v1/tasks?limit={task_list_limit}` is newly reachable for this slice.
2. Dashboard/API calls are GET-only with exactly one `limit` query key and no request body.
3. Accepted limits are integers from 1 through 50 inclusive.
4. Extra query keys, repeated limit keys, empty/zero/negative/fractional/non-integer/out-of-range values, encoded nested parameters, hidden selectors, storage, cookies, hashes, status+limit combinations, and generated selector sources fail closed.
5. Output preserves bounded aggregate task-list row shape and current order and adds only selected-limit metadata needed for authority/freshness.
6. Offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, and mutation/control calls are not introduced.
7. Missing/invalid selector, empty result, backend unavailable, unauthorized/configuration failure, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload render non-authoritative fail-closed copy.
8. Limited list output exposes source route, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata.
9. Existing dashboard/API runtime-boundary suites remain green.
10. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS, push, and green remote CI run `28306586314` are recorded.

### Story 114.3 — Phase 35 / Epic 114 final validation closure

**Status:** done after Story 114.2 implementation, review, UltraQA, push, and green remote CI evidence.

**Intent:** Complete docs/status final closure only after Story 114.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact names exact implemented limit-selected route, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, selector composition, or production operations.
3. Sprint status marks Epic 114 done only after all Epic 114 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 114.1 must complete with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before task-list-limit runtime/API work is authorized.
2. Story 114.2 must remain route-local to `GET /v1/tasks?limit={task_list_limit}` and must not add offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, replay execution target selection, lifecycle mutation behavior, selector composition, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 114.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 114.3 may run only after implementation, final review, proportional QA decision, push, and remote CI evidence exist; it records closure evidence and keeps deferred surfaces fail-closed.
4. Replay execution target selection, lifecycle apply/prune/rollback, free-text search, arbitrary discovery, pagination traversal, and sorting remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-27T18:56:38Z
Updated: 2026-06-28T00:47:00Z
