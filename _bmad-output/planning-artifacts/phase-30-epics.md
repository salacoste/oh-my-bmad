# Phase 30 Epics — Aggregate Task List Route Selection Planning

## Phase 30 theme

Phase 30 continues the dashboard route-family sequence. It opens the **Aggregate Task List** branch as planning first, while narrowing the future runtime/API candidate to one exact read surface. Runtime and API work are deferred to a separate story and final closure is deferred until review, QA, push, and CI evidence exist.

Selected family and future surface:

- Family: aggregate task list read
- Exact future candidate: `GET /v1/tasks`

Non-selected surfaces remain future-only and fail-closed:

- `/v1/sessions` and `/v1/sessions/{session_id}` session list/detail contracts;
- `/v1/tasks/{task_id}/logs/digest/stream`;
- task-list/search/discovery beyond the exact aggregate read candidate;
- automatic task detail/digest/history/trace/replay drill-down from list rows;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, generated live data, cache warming, polling, timers, background workers, and automatic refresh;
- mutation/control behavior including `POST /v1/tasks` task creation;
- services/MCP/dependencies/CI/deployment changes except if separately authorized by a future implementation story;
- production credentials and production operations.

## Epic 109 — Aggregate task list dashboard route boundary

### Objective

Plan and later prove a bounded dashboard route boundary for aggregate task summaries through `GET /v1/tasks` without session traversal, digest streaming, search/discovery, hidden row-driven selectors, broad dashboard wiring, generated live data, browser-side LLM behavior, or mutation/control side effects.

### Story 109.1 — Aggregate task list route selection planning

**Status:** done after sequential Architect APPROVE/CLEAR and Critic APPROVE consensus.

**Intent:** Create Phase 30 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the aggregate task list read family and exactly `GET /v1/tasks` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 30 PRD amendment exists and selects aggregate task list read and exactly `GET /v1/tasks` as the future candidate surface.
2. Phase 30 architecture amendment defines exact route, route-contract caveat, bounded summary output, no hidden selector propagation, pagination/freshness/provenance requirements, fail-closed degraded states, no-session, no-digest-stream, no-search/discovery, and deferred-surface boundaries.
3. Phase 30 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 109.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 30`, keeps Epic 108 done, opens Epic 109, marks Story 109.1 done with sequential Architect/Critic consensus evidence, and leaves Story 109.2/109.3 backlog.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim aggregate task list runtime or API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 109.2 — Aggregate task list runtime/API contract boundary

**Status:** backlog.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/tasks` if and only if the route contract is proven or implemented with narrow additive API tests.

**Future acceptance criteria:**

1. Tests prove only `GET /v1/tasks` is reachable for this slice.
2. Dashboard calls are GET-only and body-free.
3. `POST /v1/tasks` and all mutation/control methods remain unreachable.
4. Returned task rows are bounded summaries and do not automatically drive task detail, digest, history, trace, replay, session traversal, mutation controls, or hidden selectors.
5. `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, digest/session traversal, and broad dashboard wiring remain unreachable.
6. No browser-side LLM generation, prompt construction, hidden summarization, external provider calls, generated live data, cache warming, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, automatic refresh, or automatic retry loops are introduced.
7. Missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, and ambiguous freshness render non-authoritative fail-closed copy without auto-retry.
8. Aggregate output is bounded display content with source route, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, pagination/limit metadata, and degraded-state metadata.
9. Existing dashboard runtime-boundary suites remain green.
10. Independent code-reviewer APPROVE and architect CLEAR are recorded.
11. Remote CI is green before runtime completion is claimed.

### Story 109.3 — Phase 30 / Epic 109 final validation closure

**Status:** backlog.

**Intent:** Complete docs/status final closure only after Story 109.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented aggregate route, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply session contracts, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 109 done only after all Epic 109 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 109.1 must complete before any aggregate task list runtime/API work is authorized.
2. Story 109.2 must remain route-local to `GET /v1/tasks` and cannot add session contracts, digest streaming, task-list/search/discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes beyond the exact additive route contract, mutation/control behavior, or production operations.
3. Story 109.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. Session list/detail, digest stream, and task-list/search/discovery remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-25T23:30:38Z
