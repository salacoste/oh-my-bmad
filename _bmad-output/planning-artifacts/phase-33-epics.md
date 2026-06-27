# Phase 33 Epics — Digest Stream Route Selection Planning

## Phase 33 theme

Phase 33 continues the dashboard route-family sequence after Phase 32 closed the exact session-detail read boundary. It selects one future task log digest stream surface for separate runtime/API proof:

- Family: task log digest continuation
- Exact future candidate: `GET /v1/tasks/{task_id}/logs/digest/stream`

Non-selected surfaces remain future-only and fail-closed:

- broader task-list/search/discovery beyond approved exact reads;
- automatic task/detail/digest/history/trace/replay/session drill-down from list/detail rows;
- broad dashboard wiring;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- mutation/control behavior;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope; Story 112.2 may collect CI evidence but must not modify those surfaces;
- production credentials and production operations.

## Epic 112 — Digest stream dashboard route boundary

### Objective

Plan, prove, and close a bounded dashboard route boundary for one task-scoped digest stream through `GET /v1/tasks/{task_id}/logs/digest/stream` without task-list/search/discovery, hidden selectors, automatic adjacent-route traversal, broad dashboard wiring, generated live data, browser-side LLM behavior, raw log/prompt/provider/path leakage, background retry/refresh behavior, or mutation/control side effects.

### Story 112.1 — Digest stream route selection planning

**Status:** done after repaired sequential Architect APPROVE/CLEAR and Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 33 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and OMX evidence that select the task log digest continuation family and exactly `GET /v1/tasks/{task_id}/logs/digest/stream` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 33 PRD amendment exists and selects task log digest continuation and exactly `GET /v1/tasks/{task_id}/logs/digest/stream` as the future candidate surface.
2. Phase 33 architecture amendment defines exact route, visible `task_id` source, transport-not-pre-authorized boundary, bounded stream payload expectations, freshness/provenance requirements, lifecycle/error/fail-closed states, no task-list/search/discovery traversal, and deferred-surface boundaries.
3. Phase 33 epics file exists and sequences planning before runtime/API-boundary implementation and final closure.
4. Story 112.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status originally opened Phase 33/Epic 112 after repaired Architect/Critic consensus; final closure status now records Story 112.1, 112.2, and 112.3 done with remote CI evidence.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim digest stream runtime or API implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 112.2 — Digest stream runtime/API contract boundary

**Status:** done — local implementation completed with Autopilot code-review cycle 6 APPROVE/CLEAR, verifier cycle 3 PASS/CLEAR, push, and remote CI run `28291210521` success.

**Intent:** Implement a separately approved, tests-first boundary for exactly `GET /v1/tasks/{task_id}/logs/digest/stream` with narrow additive API/runtime tests and no broader dashboard/API expansion.

**Acceptance criteria planned:**

1. Tests prove only `GET /v1/tasks/{task_id}/logs/digest/stream` is reachable for this slice.
2. Dashboard/API calls are GET-only, query-free, and body-free.
3. The selected `task_id` comes only from a visible operator-provided field and is percent-encoded for the path segment.
4. Exactly one stream transport and framing contract is selected and tested; unselected EventSource/WebSocket/XMLHttpRequest/workers/polling/retry mechanisms remain forbidden.
5. Stream lifecycle covers open, partial, final, error, timeout, close/cancel, stale, backend/provider unavailable, unauthorized, and malformed states.
6. Output is bounded digest-stream metadata/chunks only and omits raw logs, event payloads, prompts, model/provider internals, filesystem/resource paths, hrefs/URLs, generated browser summaries, joined task/session/event data, and operation/control hints.
7. Task-list/search/discovery, task detail, digest fallback, history, trace, replay, session traversal, broad dashboard wiring, generated live data, browser-side generation/summarization, cache warming, polling/timers beyond the chosen transport contract, workers, side channels, storage writes, automatic refresh, automatic retry, and mutation/control calls are not introduced.
8. Missing/invalid visible `task_id`, backend/provider unavailable, unauthorized, non-2xx, route failure/read error, invalid chunk framing, malformed payload, unexpected keys, path-like values, stale/ambiguous freshness, excessive chunk volume, and interrupted streams render non-authoritative fail-closed copy.
9. Digest stream output exposes source route, selected visible `task_id`, connection/opened-at, last-event/retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata.
10. Existing dashboard runtime-boundary suites remain green.
11. Independent code-reviewer APPROVE/CLEAR, UltraQA PASS or explicit proportional QA, push, and remote CI green are recorded before runtime completion is claimed.

### Story 112.3 — Phase 33 / Epic 112 final validation closure

**Status:** done — final closure records Story 112.2 implementation, review, verifier, push, and remote CI run `28291210521` success.

**Intent:** Complete docs/status final closure only after Story 112.2 runtime/API evidence, final review, QA decision, push, and remote CI evidence exist.

**Acceptance criteria:**

1. Closure artifact names exact implemented digest stream route, changed files, review lanes, QA decision, commit, and CI run.
2. Closure wording does not imply broader task-list/search/discovery, automatic adjacent-route traversal, broad dashboard wiring, generated live data, browser-side LLM generation, services/MCP/dependencies/CI expansion, mutation/control behavior, or production operations.
3. Sprint status marks Epic 112 done only after all Epic 112 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 112.1 must complete with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before digest-stream runtime/API work is authorized.
2. Story 112.2 must remain route-local to `GET /v1/tasks/{task_id}/logs/digest/stream` and must not add task-list/search/discovery, generated live data, browser-side LLM behavior, broad dashboard wiring, mutation/control behavior, production operations, or any services/MCP/dependencies/CI/deployment modifications. Story 112.2 may run and record CI evidence, but modifying CI/deployment/dependencies/services/MCP requires a separate explicit planning gate and story.
3. Story 112.3 ran after implementation, final review, verifier decision, push, and remote CI evidence existed; future closure stories should preserve that ordering.
4. Broader task-list/search/discovery, generated live data, broad dashboard wiring, and production operations remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.

Generated: 2026-06-26T22:03:46Z
