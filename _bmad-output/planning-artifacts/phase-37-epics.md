# Phase 37 Epics — Task Status + Limit Browser Consumption Planning

## Phase 37 theme

Phase 37 continues the dashboard/API route-family sequence after Phase 36 closed the exact backend/API status+limit route. It selects one future browser/runtime consumption surface for separate tests-first proof:

- Family: read-only aggregate task-list status+limit browser consumption
- Exact future candidate: dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`
- Allowed status selector domain: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`
- Allowed limit selector domain: one integer task-list limit from 1 through 50 inclusive
- Selector source: visible aggregate-task-list panel controls only

Non-selected surfaces remain future-only and fail-closed:

- offset/cursor/page traversal, next-page tokens, infinite scroll, and pagination state;
- sorting controls;
- free-text task search, arbitrary discovery, multi-field filtering beyond status+limit, arbitrary query language, and saved searches;
- status+limit+anything combinations or any other selector composition;
- hidden selectors, URL hash/query-state persistence, cookies, local/session storage, generated selector values, and row-derived selector inputs;
- automatic task/detail/digest/history/trace/replay/session drill-down from returned rows;
- replay execution target selection;
- lifecycle apply/prune/rollback, snapshot deletion/restore, archive/manifest mutation, and other mutation/control behavior;
- broad dashboard wiring or dashboard-wide mode switching;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope;
- production credentials and production operations.

## Epic 116 — Task status + limit dashboard/browser consumption boundary

### Objective

Plan, prove, and close a bounded dashboard/browser consumption boundary for one visible-control-driven aggregate task-list status+limit read through canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` without pagination traversal, sorting, free-text search, arbitrary discovery, hidden selectors, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM behavior, background retry/refresh behavior, broader selector composition, or mutation/control side effects.

### Story 116.1 — Task status + limit browser consumption route-selection planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 37 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the read-only aggregate task-list status+limit browser-consumption family and exactly dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 37 PRD amendment exists and selects read-only aggregate task-list status+limit browser consumption and exactly dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the future candidate surface.
2. Phase 37 architecture amendment defines exact route, finite status vocabulary, bounded integer limit vocabulary, visible-control selector source, canonical query spelling/order, response metadata validation, fail-closed states, no pagination traversal/sorting/search/hidden discovery/traversal, no replay/lifecycle mutation, and deferred-surface boundaries.
3. Phase 37 epics file exists and sequences planning before browser/runtime-boundary implementation and final closure.
4. Story 116.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status opens Phase 37/Epic 116, marks Story 116.1 done after Architect/Critic consensus, and sequences Story 116.2/116.3 after planning consensus.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim browser/dashboard status+limit consumption implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 116.2 — Task status + limit browser consumption runtime boundary

**Status:** planned after Story 116.1 consensus.

**Intent:** Implement a separately approved, tests-first browser/runtime boundary for exactly dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` with visible selector controls only and no broader dashboard/API expansion.

**Acceptance criteria:**

1. Tests prove only the selected dashboard aggregate-task-list status+limit consumption path is newly reachable for this slice.
2. Browser requests are GET-only, bodyless, credentials-omitted, and constructed as canonical `/v1/tasks?status={task_status}&limit={task_list_limit}` with status before limit.
3. Accepted statuses are the existing finite lifecycle status set only.
4. Accepted limits are integers from 1 through 50 inclusive.
5. Selector values originate from visible aggregate-task-list panel controls only; hidden selectors, URL hash/query-state persistence, local/session storage, cookies, generated selectors, row-derived selectors, and background selector sources fail closed.
6. Extra query keys, repeated keys, empty/unknown statuses, empty/zero/negative/fractional/non-integer/out-of-range limits, encoded nested parameters, status+limit+anything combinations, reversed query order, and malformed responses fail closed.
7. Output preserves bounded aggregate task-list row shape and exposes selected-status and selected-limit metadata needed for authority/freshness.
8. Selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, and backend canonical status+limit contracts remain independently green.
9. Offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, and mutation/control calls are not introduced.
10. Missing/invalid selector control, empty result, backend unavailable, unauthorized/configuration failure, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, selected-status mismatch, selected-limit mismatch, and over-broad payload render non-authoritative fail-closed copy.
11. Existing dashboard/API runtime-boundary suites remain green.
12. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS or proportional QA, push, and green remote CI are recorded.

### Story 116.3 — Phase 37 / Epic 116 final validation closure

**Status:** planned after Story 116.2 review, QA, push, and green remote CI evidence.

**Intent:** Complete docs/status final closure only after Story 116.2 runtime/browser evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact names exact implemented dashboard status+limit consumption path, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior beyond exact status+limit browser consumption, or production operations.
3. Sprint status marks Epic 116 done only after all Epic 116 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 116.1 must complete with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before browser/runtime status+limit consumption work is authorized.
2. Story 116.2 must remain browser-local to dashboard aggregate-task-list consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` and must not add offset/cursor/page traversal, sorting controls, free-text search, arbitrary discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, replay execution target selection, lifecycle mutation behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 116.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 116.3 may run only after implementation, final review, proportional QA decision, push, and remote CI evidence exist; it records closure evidence and keeps deferred surfaces fail-closed.
4. Replay execution target selection, lifecycle apply/prune/rollback, free-text search, arbitrary discovery, pagination traversal, and sorting remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-28T17:26:00Z

Updated: 2026-06-28T17:34:00Z
