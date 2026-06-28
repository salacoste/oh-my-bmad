# Phase 36 Epics — Task Status + Limit Composition Route Selection Planning

## Phase 36 theme

Phase 36 continues the dashboard/API route-family sequence after Phase 35 closed the exact task-list-limit read boundary. It selects one future bounded selector-composition surface for separate runtime/API proof:

- Family: read-only task-list bounded selector composition
- Exact future candidate: `GET /v1/tasks?status={task_status}&limit={task_list_limit}`
- Allowed status selector domain: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`
- Allowed limit selector domain: an integer task_list_limit from 1 through 50 inclusive

Non-selected surfaces remain future-only and fail-closed:

- offset/cursor/page traversal, next-page tokens, infinite scroll, and pagination state;
- sorting controls;
- free-text task search, arbitrary discovery, multi-field filtering beyond status+limit, arbitrary query language, and saved searches;
- status+limit+anything combinations or any other selector composition;
- automatic task/detail/digest/history/trace/replay/session drill-down from returned rows;
- replay execution target selection;
- lifecycle apply/prune/rollback, snapshot deletion/restore, archive/manifest mutation, and other mutation/control behavior;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 115.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 115 — Task status + limit dashboard/API route boundary

### Objective

Plan, prove, and close a bounded dashboard/API route boundary for one status-filtered, limit-selected first-page task-summary list through `GET /v1/tasks?status={task_status}&limit={task_list_limit}` without pagination traversal, sorting, free-text search, arbitrary discovery, hidden selectors, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM behavior, background retry/refresh behavior, broader selector composition, or mutation/control side effects.

### Story 115.1 — Task status + limit route selection planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 36 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the read-only task-list bounded selector-composition family and exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 36 PRD amendment exists and selects read-only task-list bounded selector composition and exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the future candidate surface.
2. Phase 36 architecture amendment defines exact route, finite status vocabulary, bounded integer limit vocabulary, two-query-key boundary, query spelling decision requirements, bounded row-shape/order expectations, freshness/provenance requirements, fail-closed states, no pagination traversal/sorting/search/hidden discovery/traversal, no replay/lifecycle mutation, and deferred-surface boundaries.
3. Phase 36 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 115.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status opens Phase 36/Epic 115, marks Story 115.1 done after Architect/Critic consensus, and sequences Story 115.2/115.3 after planning consensus.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim status+limit runtime/API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 115.2 — Task status + limit runtime/API contract boundary

**Status:** done after Story 115.1 consensus, tests-first implementation, code-review APPROVE/CLEAR, UltraQA PASS, push, and green remote CI run `28329475903`.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` with narrow additive API/runtime tests and no broader dashboard/API expansion.

**Acceptance criteria:**

1. Tests prove only `GET /v1/tasks?status={task_status}&limit={task_list_limit}` is newly reachable for this slice.
2. Dashboard/API calls are GET-only with exactly one `status` query key, exactly one `limit` query key, and no request body.
3. Accepted statuses are the existing finite lifecycle status set only.
4. Accepted limits are integers from 1 through 50 inclusive.
5. Extra query keys, repeated keys, empty/unknown statuses, empty/zero/negative/fractional/non-integer/out-of-range limits, encoded nested parameters, hidden selectors, storage, cookies, hashes, generated selector sources, and status+limit+anything combinations fail closed.
6. Output preserves bounded aggregate task-list row shape and current order and adds only selected-status and selected-limit metadata needed for authority/freshness.
7. Selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, and limit-only `GET /v1/tasks?limit={task_list_limit}` remain independently green.
8. Offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, and mutation/control calls are not introduced.
9. Missing/invalid selector, empty result, backend unavailable, unauthorized/configuration failure, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload render non-authoritative fail-closed copy.
10. Status+limit list output exposes source route, selected status, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata.
11. Existing dashboard/API runtime-boundary suites remain green.
12. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS or proportional QA, push, and green remote CI are recorded.

### Story 115.3 — Phase 36 / Epic 115 final validation closure

**Status:** done after Story 115.2 implementation, review, QA, push, and green remote CI evidence.

**Intent:** Complete docs/status final closure only after Story 115.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact names exact implemented status+limit route, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior beyond exact status+limit composition, or production operations.
3. Sprint status marks Epic 115 done only after all Epic 115 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 115.1 must complete with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before task status+limit runtime/API work is authorized.
2. Story 115.2 must remain route-local to `GET /v1/tasks?status={task_status}&limit={task_list_limit}` and must not add offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, replay execution target selection, lifecycle mutation behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 115.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 115.3 may run only after implementation, final review, proportional QA decision, push, and remote CI evidence exist; it records closure evidence and keeps deferred surfaces fail-closed.
4. Replay execution target selection, lifecycle apply/prune/rollback, free-text search, arbitrary discovery, pagination traversal, and sorting remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-28T01:35:04Z

Updated: 2026-06-28T17:06:00Z
