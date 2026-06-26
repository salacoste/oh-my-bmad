# Phase 31 Epics — Session List Route Selection Planning

## Phase 31 theme

Phase 31 continues the dashboard route-family sequence after Phase 30 closed the exact aggregate task list read boundary. It selects one future session-visibility surface for separate runtime/API proof:

- Family: session visibility
- Exact future candidate: `GET /v1/sessions`

Non-selected surfaces remain future-only and fail-closed:

- `/v1/sessions/{session_id}` session detail;
- `/v1/tasks/{task_id}/logs/digest/stream`;
- task-list/search/discovery beyond the exact aggregate read already shipped in Phase 30;
- automatic task/session/detail/digest/history/trace/replay drill-down from rows;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, generated live data, cache warming, polling, timers, background workers, and automatic refresh;
- mutation/control behavior;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 110.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 110 — Session list dashboard route boundary

### Objective

Plan, prove, and close a bounded dashboard route boundary for session summaries through `GET /v1/sessions` without session-detail traversal, digest streaming, search/discovery, hidden row-driven selectors, broad dashboard wiring, generated live data, browser-side LLM behavior, or mutation/control side effects.

### Story 110.1 — Session list route selection planning

**Status:** done after repaired sequential Architect APPROVE/CLEAR and Critic APPROVE consensus.

**Intent:** Create Phase 31 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the session visibility family and exactly `GET /v1/sessions` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 31 PRD amendment exists and selects session visibility and exactly `GET /v1/sessions` as the future candidate surface.
2. Phase 31 architecture amendment defines exact route, bounded summary output, no hidden selector propagation, freshness/provenance requirements, fail-closed degraded states, no session-detail, no-digest-stream, no-search/discovery, and deferred-surface boundaries.
3. Phase 31 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 110.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 31`, keeps Epic 109 done, opens Epic 110, marks Story 110.1 done with sequential Architect/Critic consensus evidence, and leaves Story 110.2/110.3 backlog.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim session list runtime or API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 110.2 — Session list runtime/API contract boundary

**Status:** done after tests-first implementation, code-review APPROVE, architect recheck CLEAR, UltraQA PASS, push, and remote CI run `28248851773` success.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/sessions` if and only if the route contract is proven or implemented with narrow additive API tests.

**Acceptance criteria planned:**

1. Tests prove only `GET /v1/sessions` is reachable for this slice.
2. Dashboard calls are GET-only and body-free.
3. Session detail `/v1/sessions/{session_id}` and all mutation/control methods remain unreachable.
4. Returned session rows are bounded summaries and do not automatically drive session detail, task detail, digest, history, trace, replay, search/discovery, mutation controls, or hidden selectors.
5. Digest stream, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation/summarization, cache warming, polling/timers, workers, side channels, storage writes, automatic refresh, and automatic retry are not introduced.
6. Missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, stale heartbeat metadata, and ambiguous freshness render non-authoritative fail-closed copy.
7. Session output is bounded display content with source route, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, limit/page metadata, and degraded-state metadata.
8. Existing dashboard runtime-boundary suites remain green.
9. Independent code-reviewer APPROVE and architect CLEAR are recorded.
10. Remote CI is green before runtime completion is claimed.

### Story 110.3 — Phase 31 / Epic 110 final validation closure

**Status:** done after Story 110.2 implementation, final local validation, push, remote CI run `28248851773` success, and docs/status closure evidence.

**Intent:** Complete docs/status final closure only after Story 110.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact `_bmad-output/implementation-artifacts/110-3-phase-31-epic-110-final-closure.md` names exact implemented session-list route, changed files, review lanes, QA decision, commit `a2a066f52b647f5e10cfddeb0454590da93497bd`, and CI run `28248851773`.
2. Closure wording does not imply session-detail contracts, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 110 done only after all Epic 110 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 110.1 must complete before session-list runtime/API work is authorized.
2. Story 110.2 must remain route-local to `GET /v1/sessions` and must not add session-detail contracts, digest streaming, task-list/search/discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, mutation/control behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 110.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 110.3 must run after implementation, final review, QA decision, push, and remote CI evidence exists.
4. Session detail, digest stream, broader task-list/search/discovery, generated live data, and production operations remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-26T08:50:53Z
