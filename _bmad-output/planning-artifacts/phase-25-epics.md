# Phase 25 Epics — Trace Correlation Live-Read Route Selection

## Phase 25 theme

Phase 25 continues the narrow dashboard live-read route-family sequence. It opens the **Trace correlation** branch as planning first, with runtime work deferred to a separate story and final closure deferred until review and CI evidence exist.

Selected future route family:

- `GET /v1/trace/{trace_id}`

Non-selected route families remain future-only and fail-closed:

- history / replay;
- lifecycle readiness;
- task-list/search/discovery;
- aggregate/session/digest;
- generated live data;
- mutation/control surfaces.

## Epic 104 — Trace correlation live-read runtime boundary

### Objective

Plan and later prove a trace-scoped dashboard live-read boundary for trace correlation visibility without broad dashboard live wiring, trace search/list/discovery, history/replay traversal, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, or mutation/control behavior.

### Story 104.1 — Trace correlation live-read route selection

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 25 PRD, architecture, epics, story artifact, and sprint-status opening that select exactly the Trace correlation route family.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 25 PRD amendment exists and selects exactly `GET /v1/trace/{trace_id}`.
2. Phase 25 architecture amendment exists and defines exact route, method, trace_id, module, no-hidden-write, no-discovery, and deferred-surface boundaries.
3. Phase 25 epics file exists and sequences route selection before runtime boundary implementation and final closure.
4. Story 104.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 25`, keeps Epic 103 done, records Epic 103 retrospective done, opens Epic 104, and marks Story 104.1 done.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 104.2 — Trace correlation runtime boundary

**Status:** done by commit `13bbc37` plus formatting CI fix `e0c624c`; final code-review APPROVE/CLEAR, UltraQA complete, and remote CI run `28025459660` green.

**Intent:** Implement a separately approved, tests-first browser/runtime boundary for exactly the selected route family.

**Future acceptance criteria:**

1. Tests prove only `/v1/trace/{trace_id}` is reachable for this slice.
2. Calls are GET-only and body-free.
3. Visible trace_id is the sole route selector source.
4. Event/task/session identifiers are display metadata only, not hidden trace route selectors.
5. Empty/unavailable, partial, stale, invalid, unauthorized, non-2xx, backend-unavailable, and network-failure states render bounded copy.
6. Source route, trace_id, freshness, authority, linked identifiers, and degraded-state metadata are visible.
7. Existing health, task-detail, and event/transition runtime-boundary tests remain green.
8. No history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, trace search/list/discovery, or control behavior is introduced.
9. Independent code-reviewer APPROVE and architect CLEAR are recorded.
10. Remote CI is green before runtime completion is claimed.

### Story 104.3 — Phase 25 / Epic 104 final validation closure

**Status:** done by `_bmad-output/implementation-artifacts/104-3-phase-25-epic-104-final-closure.md`.

**Intent:** Complete docs/status final closure only after Story 104.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented route family, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply broad dashboard live wiring or trace search/list/discovery.
3. Sprint status marks Epic 104 done only after all Epic 104 stories are done.
4. Deferred surfaces remain explicit and fail-closed.
5. Final docs/status verification and `git diff --check` pass.
6. If `gh run watch` times out, direct `gh run view` evidence against the expected run/head SHA is recorded before treating CI as successful.

## Dependency and sequencing notes

1. Story 104.1 must complete before any trace runtime code is authorized.
2. Story 104.2 must remain trace-scoped and cannot add discovery/search/listing.
3. Story 104.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. History/replay and lifecycle readiness remain separate future candidates and stay fail-closed unless explicitly selected later.
5. Task-list/search/discovery and aggregate/session/digest remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.
