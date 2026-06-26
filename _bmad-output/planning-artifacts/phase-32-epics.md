# Phase 32 Epics — Session Detail Route Selection Planning

## Phase 32 theme

Phase 32 continues the dashboard route-family sequence after Phase 31 closed the exact session-list read boundary. It selects one future session-detail surface for separate runtime/API proof:

- Family: session visibility continuation
- Exact future candidate: `GET /v1/sessions/{session_id}`

Non-selected surfaces remain future-only and fail-closed:

- session mutation/search/discovery and historical session search;
- `/v1/tasks/{task_id}/logs/digest/stream`;
- task-list/search/discovery beyond already approved exact reads;
- automatic task/session/detail/digest/history/trace/replay drill-down from list/detail rows;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, and automatic refresh;
- mutation/control behavior;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 111.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 111 — Session detail dashboard route boundary

### Objective

Plan, prove, and close a bounded dashboard route boundary for one session detail read through `GET /v1/sessions/{session_id}` without automatic session-list drill-down, task traversal, digest streaming, search/discovery, hidden selectors, broad dashboard wiring, generated live data, browser-side LLM behavior, path/log/event leakage, or mutation/control side effects.

### Story 111.1 — Session detail route selection planning

**Status:** done after sequential Architect APPROVE/CLEAR and Critic APPROVE consensus.

**Intent:** Create Phase 32 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the session visibility continuation family and exactly `GET /v1/sessions/{session_id}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 32 PRD amendment exists and selects session visibility continuation and exactly `GET /v1/sessions/{session_id}` as the future candidate surface.
2. Phase 32 architecture amendment defines exact route, visible path-parameter source, bounded Session-table detail output, no automatic session-list row drill-down, freshness/provenance requirements, fail-closed degraded states, no digest/task/search/discovery traversal, and deferred-surface boundaries.
3. Phase 32 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 111.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 32`, keeps Epic 110 done, opens Epic 111, marks Story 111.1 done with sequential Architect/Critic consensus evidence, and leaves Story 111.2/111.3 backlog.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim session detail runtime or API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 111.2 — Session detail runtime/API contract boundary

**Status:** done — tests-first implementation, local validation, code-review APPROVE/CLEAR, UltraQA PASS, push, and remote CI run `28259115072` complete.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/sessions/{session_id}` if and only if the route contract is proven or implemented with narrow additive API tests.

**Acceptance criteria planned:**

1. Tests prove only `GET /v1/sessions/{session_id}` is reachable for this slice.
2. Dashboard/API calls are GET-only, query-free, and body-free.
3. The selected `session_id` comes only from a visible operator-provided field and is percent-encoded for the path segment.
4. Session-list rows remain inert display text and do not become hidden selectors, links, click targets, prefetch sources, storage keys, generated prompts, or automatic drill-down inputs.
5. Returned session detail is bounded Session-table metadata only and omits raw `worktree_path`, filesystem/resource paths, event payloads, log content, summaries, hrefs/URLs, generated text, joined task/event data, and operation/control hints.
6. Digest stream, task-list/search/discovery, task detail, digest/history/trace/replay traversal, broad dashboard wiring, generated live data, browser-side generation/summarization, cache warming, polling/timers, workers, side channels, storage writes, automatic refresh, and automatic retry are not introduced.
7. Missing/invalid visible session_id, not found, backend unavailable, unauthorized, non-2xx, route failure/read error, invalid response, malformed row, unexpected keys, path-like values, stale/ambiguous freshness, and over-broad payload render non-authoritative fail-closed copy.
8. Session detail output exposes source route, selected visible session_id, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata.
9. Existing dashboard runtime-boundary suites remain green.
10. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS, push, and remote CI green are recorded before runtime completion is claimed.

### Story 111.3 — Phase 32 / Epic 111 final validation closure

**Status:** done after Story 111.2 implementation, final review, QA decision, push, and remote CI run `28259115072`.

**Intent:** Complete docs/status final closure only after Story 111.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact `_bmad-output/implementation-artifacts/111-3-phase-32-epic-111-final-closure.md` names exact implemented session-detail route, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply session mutation/search/discovery, digest streaming, task-list/search/discovery expansion, task/detail/digest/history/trace/replay traversal, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 111 done only after all Epic 111 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 111.1 must complete before session-detail runtime/API work is authorized.
2. Story 111.2 must remain route-local to `GET /v1/sessions/{session_id}` and must not add session mutation/search/discovery, digest streaming, generated live data, browser-side LLM behavior, broad dashboard wiring, mutation/control behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 111.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 111.3 must run after implementation, final review, QA decision, push, and remote CI evidence exist.
4. Digest stream, broader task-list/search/discovery, generated live data, broad dashboard wiring, and production operations remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-26T18:14:59Z
