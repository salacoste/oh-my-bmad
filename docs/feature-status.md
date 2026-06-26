# Feature Status Matrix

> **Derivative status summary.** The canonical implementation status is
> [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml).
> This page is a read-only, human-navigable summary last verified from the files
> named below on 2026-06-26. If this page conflicts with sprint-status, trust
> sprint-status and update this derivative matrix.

## Current BMad status

- **Current phase:** Phase 31 — closed for the exact session-list runtime/API boundary. Story 110.2 implements exactly `GET /v1/sessions` with code-review APPROVE, architect recheck CLEAR, UltraQA PASS, local backend/dashboard/static gates green, push commit `a2a066f52b647f5e10cfddeb0454590da93497bd`, and remote CI run `28248851773` passed; Story 110.3 records final closure.
- **Current epic:** Epic 110 is done after Story 110.3 final closure and remote CI run `28248851773`; Epic 109 remains done after remote CI run `28213044828`.
- **Recently closed:** Phase 31 / Epic 110 — Session list runtime/API boundary and final validation closure are done.
- **Done in Phase 27:**
  - Story 106.1 selected exactly `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display from `dashboard/static/replay-lifecycle-contract.json`.
  - Story 106.2 implemented the narrow browser/runtime lifecycle/snapshot listing boundary with bounded metadata-only rendering and review/UltraQA evidence.
  - Story 106.3 records Phase 27 / Epic 106 closure after Story 106.2 review, QA, push, and remote CI run `28139358221` passed.
- **Done in Phase 28:**
  - Story 107.1 selected exactly `POST /v1/events/replay/snapshots` as the snapshot creation authorization surface.
  - Story 107.2 implemented the visible operator-initiated, JWT-authenticated snapshot-create runtime boundary and recorded code-review/UltraQA/local CI evidence.
  - Story 107.3 records Phase 28 / Epic 107 closure after Story 107.2 review, QA, push, and remote CI run `28195545005` passed.
- **Done in Phase 29:**
  - Story 108.1 selected the aggregate/session/digest family and narrowed the candidate surface to exactly `GET /v1/tasks/{task_id}/logs/digest`.
  - Story 108.2 implements the tests-first dashboard runtime boundary for exactly `GET /v1/tasks/{task_id}/logs/digest`, using a visible `task_id` source only, server-supplied freshness only, fail-closed digest states, final code-review `APPROVE`, UltraQA `PASS`, local non-slow PR-gate evidence, and remote CI run `28205787033` passed.
  - Story 108.3 records Phase 29 / Epic 108 closure after Story 108.2 review, QA, push, and remote CI run `28205787033` passed.
- **Done in Phase 30:**
  - Story 109.1 selected the aggregate task list read family and exactly `GET /v1/tasks` as the future candidate surface; Architect APPROVE/CLEAR, Critic APPROVE, code-review APPROVE/CLEAR, and docs-only UltraQA skip are recorded.
  - Story 109.2 implements the exact `GET /v1/tasks` runtime/API boundary: fixed bounded first page, no query/body, bounded task summary row shape, browser fetch with `credentials: "omit"`, fail-closed states, code-review APPROVE, architect recheck CLEAR, UltraQA PASS, and local non-slow gate evidence.
  - Story 109.3 records Phase 30 / Epic 109 closure after Story 109.2 review, QA, push, and remote CI run `28213044828` passed.
  - Session list/detail, digest streaming, task-list/search/discovery beyond exact `GET /v1/tasks`, broad dashboard wiring, generated live data, browser-side LLM generation, browser-side summarization, cache warming/background refresh, mutation/control behavior, backend/API expansion beyond exact approved dashboard runtime routes, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain deferred/fail-closed.
- **Done in Phase 31:**
  - Story 110.1 selected the session visibility family and exactly `GET /v1/sessions` as the future candidate surface after repaired Architect APPROVE/CLEAR and Critic APPROVE consensus.
  - Story 110.2 implements exactly `GET /v1/sessions`: Session-table-only bounded API rows, query/body rejection, strict dashboard contract promotion for `/v1/sessions`, one text-only `session-list.js` fetch/render path with `Accept: application/json`, no credentials override, strict metadata/timestamp/content-type validation, code-review APPROVE, architect recheck CLEAR, UltraQA PASS, local non-slow gate evidence, and remote CI run `28248851773` passed.
  - Story 110.3 records Phase 31 / Epic 110 closure after Story 110.2 review, QA, push, and remote CI run `28248851773` passed.
  - Session detail `/v1/sessions/{session_id}`, digest streaming, task-list/search/discovery beyond exact approved reads, broad dashboard wiring, generated live data, browser-side LLM generation/summarization, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment modifications, production credentials, and production operations remain deferred/fail-closed.
- **Not closed by this page:** lifecycle apply/prune/rollback, destructive lifecycle authorization execution, archive/manifest mutation, snapshot deletion/restore, session detail, digest streaming, task-list/search/discovery beyond exact `GET /v1/tasks`, generated live data, replay execution target selection, broad dashboard live wiring, backend/API expansion beyond exact approved dashboard runtime routes, additional controls, services, MCP changes, deployment changes, dependencies, lockfiles, and production operations.

Primary evidence:

- `../_bmad-output/implementation-artifacts/sprint-status.yaml` — `current_phase: 31`, `epic-109` done, `epic-110` done, `110-1` done after repaired consensus, `110-2` done with review/UltraQA/CI evidence, and `110-3` done for final closure/push/remote-CI evidence.
- `../_bmad-output/planning-artifacts/phase-27-prd-amendment.md` / `phase-27-architecture-amendment.md` / `phase-27-epics.md` — Phase 27 lifecycle/snapshot listing scope and deferred snapshot-create boundary.
- `../_bmad-output/implementation-artifacts/106-3-phase-27-epic-106-final-closure.md` — Story 106.3 final closure evidence, including remote CI run `28139358221`.
- `../_bmad-output/planning-artifacts/phase-28-prd-amendment.md` — Phase 28 snapshot creation authorization PRD scope.
- `../_bmad-output/planning-artifacts/phase-28-architecture-amendment.md` — exact `POST /v1/events/replay/snapshots`, visible operator initiation, one existing authorization source, no-hidden-write, no-lifecycle-mutation boundaries.
- `../_bmad-output/planning-artifacts/phase-28-epics.md` — Epic 107 story sequence.
- `../_bmad-output/implementation-artifacts/107-1-snapshot-creation-authorization-planning.md` — Story 107.1 route-selection and non-authorization evidence.
- `../_bmad-output/implementation-artifacts/107-2-snapshot-creation-authorization-runtime-boundary.md` — Story 107.2 runtime implementation, review, UltraQA, and local CI evidence.
- `../_bmad-output/implementation-artifacts/107-3-phase-28-epic-107-final-closure.md` — Story 107.3 final closure evidence, including remote CI run `28195545005`.
- `../_bmad-output/planning-artifacts/phase-29-prd-amendment.md` — Phase 29 aggregate/session/digest route-selection PRD scope.
- `../_bmad-output/planning-artifacts/phase-29-architecture-amendment.md` — exact future `GET /v1/tasks/{task_id}/logs/digest`, visible task_id, no-streaming/no-hidden-generation/no-aggregate-session/no-discovery boundaries.
- `../_bmad-output/planning-artifacts/phase-29-epics.md` — Epic 108 story sequence.
- `../_bmad-output/implementation-artifacts/108-1-aggregate-session-digest-route-selection-planning.md` — Story 108.1 route-family selection and non-authorization evidence.
- `../_bmad-output/implementation-artifacts/108-2-task-log-digest-runtime-boundary.md` — Story 108.2 runtime implementation, review, UltraQA, local CI, push, and remote CI evidence.
- `../_bmad-output/implementation-artifacts/108-3-phase-29-epic-108-final-closure.md` — Story 108.3 final closure evidence, including remote CI run `28205787033`.
- `../_bmad-output/planning-artifacts/phase-30-prd-amendment.md` — Phase 30 aggregate task list route-selection PRD scope.
- `../_bmad-output/planning-artifacts/phase-30-architecture-amendment.md` — exact future `GET /v1/tasks`, route-contract caveat, bounded rows, no-hidden-selector/no-session/no-stream/no-search boundaries.
- `../_bmad-output/planning-artifacts/phase-30-epics.md` — Epic 109 story sequence.
- `../_bmad-output/implementation-artifacts/109-1-aggregate-task-list-route-selection-planning.md` — Story 109.1 route-family selection and non-authorization evidence.
- `../_bmad-output/implementation-artifacts/109-2-aggregate-task-list-runtime-boundary.md` — Story 109.2 exact `GET /v1/tasks` runtime/API implementation, code-review, UltraQA, and local validation evidence.
- `../_bmad-output/implementation-artifacts/109-3-phase-30-epic-109-final-closure.md` — Story 109.3 final closure evidence, including commit `3ebc2c0909e3d4dec586cb6d5f7f73c52d71010f`, remote CI run `28213044828`, and CI URL https://github.com/salacoste/oh-my-bmad/actions/runs/28213044828.
- `../_bmad-output/planning-artifacts/phase-31-prd-amendment.md` / `phase-31-architecture-amendment.md` / `phase-31-epics.md` — Phase 31 session-list route-selection scope and exact future `GET /v1/sessions` candidate.
- `../_bmad-output/implementation-artifacts/110-1-session-list-route-selection-planning.md` — Story 110.1 planning-only route selection and repaired consensus evidence.
- `../_bmad-output/implementation-artifacts/110-2-session-list-runtime-boundary.md` — Story 110.2 exact `GET /v1/sessions` runtime/API implementation, code-review, architect recheck, UltraQA, and local validation evidence.
- `../_bmad-output/implementation-artifacts/110-3-phase-31-epic-110-final-closure.md` — Story 110.3 final closure evidence, including commit `a2a066f52b647f5e10cfddeb0454590da93497bd`, remote CI run `28248851773`, and CI URL https://github.com/salacoste/oh-my-bmad/actions/runs/28248851773.
- `../docs/api-contracts.md` — distinguishes `GET /v1/events/replay/snapshots` snapshot listing from `POST /v1/events/replay/snapshots` snapshot creation.
- `../dashboard/static/replay-lifecycle-contract.json` — passive lifecycle-readiness evidence fields and fail-safe states.

## Status categories

| Status | Meaning |
|---|---|
| Implemented | Present in code/docs/tests and marked done in BMad status. |
| Implemented with recorded closure | Present in code/docs/tests and closed by the enclosing BMad story/epic evidence. |
| Contract/static-only | Documented, panelled, or contract-tested without browser runtime/API expansion. |
| Deferred / not implemented | Explicitly future, unavailable, fail-closed, or credential/operator-gated. |

## Implemented capabilities

| Area | Status | Evidence / notes |
|---|---|---|
| Core event-sourced platform | Implemented | Phase 1 / Epics 1–7.5: append-only JSONL event spine, registry API/state materializer, Telegram gateway, console CLI, worker wrapper, approval/policy/recovery UX. See sprint-status Phase 1 section and `docs/architecture.md`. |
| Supply-chain, trace, metrics, HMAC approvals, budget, DR | Implemented | Phase 2 / Epics 8–13 are marked done in sprint-status: SBOM/cosign/SLSA, `trace_id`, metrics subscriber, HMAC approval signing, budget enforcement, Litestream/backup. |
| MCP tooling fleet | Implemented | Workspace has 9 MCP servers: `task-registry`, `session-registry`, `clawhip-bridge`, `git`, `github`, `verification`, `memory`, `artifact`, `browser`; see `docs/component-inventory.md`. |
| Browser automation plane | Implemented | Phase 4 / Epics 20–22: Playwright/browser MCP server, container isolation, validation and finalization. |
| Multi-runtime worker plane | Implemented | Phase 5 / Epics 26–29: Claude Code, Codex, and Gemini adapters behind runtime selection/handoff/budget boundaries. |
| Postgres/task state machine/multi-task execution | Implemented | Phase 6 / Epics 30–34: Postgres migration, task FSM, multi-worker pool, Gemini adapter, CI hardening. |
| Reliability/operator tooling | Implemented | Phase 7 / Epics 35–40: audit trail, dead-session detection, stale alerts, recovery loops, priority queue, gate/retro. |
| Remote MCP transport and mTLS | Implemented | Phases 10–11 / Epics 50–59: Streamable HTTP/bearer auth dual transport plus mTLS internal Docker-network profile and validation. |
| Replay/archive read paths | Implemented | Phases 12–13 / Epics 60–68: replay engine/endpoints, validation/snapshots, archive manifest lifecycle, hot+archive equivalence, streaming replay. |
| Lifecycle operations readiness, product scope, and archive-aware history | Implemented read-only/readiness/docs scope | Phases 14–18 / Epics 69–86: ADR-0025, non-destructive dry-run planner, archive-aware task-history boundary, destructive-apply readiness contracts, and Phase 18 product-scope/next-candidate planning. No destructive apply was added. |
| Read-only dashboard shell and contracts | Implemented static/contract scope | Phases 19–21 / Epics 88–100: static dashboard shell, read-only panels, accessibility/help, live-read contracts, fixture/view-model/static rendering readiness, fail-closed unavailable routes. |
| Health/readiness dashboard runtime | Implemented | Phase 22 / Epic 101: narrow browser runtime boundary for `GET /v1/health`; see `dashboard/static/health-readiness.js` and related tests. |
| Task detail dashboard runtime | Implemented with recorded closure | Phase 23 / Epic 102: Story 102.2 `dashboard/static/task-detail.js` uses exactly `GET /v1/tasks/{task_id}` with visible task-id provenance and fail-closed states; Story 102.3 records final closure. |

## Contract/static-only or intentionally narrow areas

| Area | Status | Boundary |
|---|---|---|
| Dashboard lifecycle/snapshot runtime | Implemented with recorded closure | Phase 27 / Epic 106: Story 106.2 adds `dashboard/static/lifecycle-snapshot.js` and the lifecycle/snapshot panel for exactly `GET /v1/events/replay/snapshots`; output is bounded snapshot metadata only, passive lifecycle evidence fail-closes as non-authoritative when missing/degraded, architect re-check is CLEAR after shell copy declares optional evidence injection and fail-closed behavior, and Story 106.3 records final closure with remote CI run `28139358221`. |
| Snapshot creation authorization boundary | Implemented with recorded closure | Phase 28 / Epic 107: Story 107.2 adds the visible bearer-token operator affordance and route-local JWT authorization for exactly body-free `POST /v1/events/replay/snapshots`; HTTP 201 is required before authoritative metadata rendering, failed/unknown states do not auto-retry, duplicate in-flight clicks are blocked, tokens are not echoed or stored, and Story 107.3 records final closure with remote CI run `28195545005`. |
| Task log digest dashboard runtime | Implemented with recorded closure | Phase 29 / Epic 108: Story 108.2 adds `dashboard/static/task-log-digest.js` and a bounded digest panel for exactly `GET /v1/tasks/{task_id}/logs/digest`; the selector is visible task_id text only, healthy authoritative rendering requires backend digest/summary plus server freshness, unknown/malformed states fail closed, and Story 108.3 records final closure with remote CI run `28205787033`. Session list/detail, digest streaming, task-list/search/discovery, generated live data, browser-side LLM behavior, and broad dashboard wiring remain fail-closed. |
| Aggregate task list dashboard runtime | Implemented with recorded closure | Phase 30 / Epic 109: Story 109.2 adds exact `GET /v1/tasks` API/runtime behavior and `dashboard/static/aggregate-task-list.js`; the backend rejects query strings and GET bodies, returns a fixed bounded first page of task summaries, and the browser fetch uses `credentials: "omit"` with fail-closed display states. Code-review APPROVE, architect recheck CLEAR, UltraQA PASS, local non-slow validation, and Story 109.3 remote CI run `28213044828` are recorded. |
| Session list dashboard runtime | Implemented with recorded closure | Phase 31 / Story 110.2 adds exact `GET /v1/sessions` API/runtime behavior and `dashboard/static/session-list.js`; the backend queries `Session` only, rejects query/body selectors, returns fixed bounded summary rows, and omits raw `worktree_path`. The dashboard promotes only `/v1/sessions`, sends `Accept: application/json` with no credentials override, validates content type, metadata, UTC timestamps, fixed limit/sort, and row shape strictly, and renders inert text only. Code-review APPROVE, architect recheck CLEAR, UltraQA PASS, backend 54 passed, dashboard 199 passed, full non-slow 4352 passed, Ruff, `node --check`, `git diff --check`, and Story 110.3 remote CI run `28248851773` are recorded. |
| Fixture snapshot/static rendering | Contract/static-only | Fixture/view-model/static rendering tests prove read-only presentation behavior and fail-closed unavailable states; they do not authorize new runtime polling or mutation controls. |

## Deferred, unavailable, or not implemented

| Area | Status | Reason / guardrail |
|---|---|---|
| Task-list/search/discovery dashboard route family | Deferred / not implemented beyond exact aggregate read | Phase 30 implements only exact aggregate task list read `GET /v1/tasks`; broader task-list/search/discovery remains a higher-risk future family requiring separate planning. |
| Aggregate/session dashboard live contracts | Aggregate task list and session list implemented with recorded closure | Phase 30 / Story 109.2 implements exactly `GET /v1/tasks` for bounded aggregate task summaries and Story 109.3 records green remote CI run `28213044828`. Phase 31 / Story 110.2 implements exactly `GET /v1/sessions` after code-review/architect/UltraQA gates and Story 110.3 records green remote CI run `28248851773`. `/v1/sessions/{session_id}`, digest streaming, search/discovery, hidden selectors, automatic drill-down, and broad dashboard wiring remain unavailable/needs-contract until separately approved. |
| Destructive lifecycle apply/prune/delete/truncate/move/rewrite/chmod | Deferred / not implemented | Phase 17 is readiness-only. Future mutation requires exact dry-run plan-hash binding, replay validation, rollback/restore evidence, and explicit operator gate. |
| Object-storage lifecycle jobs and scheduled retention | Deferred / not implemented | `docs/architecture.md` keeps automatic S3/B2/R2 lifecycle management and time-based lifecycle automation as future work. |
| Broad dashboard live-read runtime wiring | Deferred / not implemented | Current browser runtime remains route-family-scoped. Health/readiness, task detail, event/transition, trace, history/replay, and lifecycle/snapshot have recorded narrow boundaries only; broad dashboard live wiring remains unavailable. |
| Digest stream dashboard runtime | Deferred / fail-closed | `/v1/tasks/{task_id}/logs/digest/stream` remains excluded; Story 108.2 implements only non-streaming `GET /v1/tasks/{task_id}/logs/digest`. |
| Task-list/search/discovery hidden calls | Deferred / fail-closed | Phase 23 guardrails forbid hidden discovery, task-list/search calls, broad dashboard wiring, and backend/API expansion. |
| Additional mutation/control affordances in dashboard | Deferred / not implemented | Only the Phase 28 visible JWT-authenticated snapshot-create affordance is authorized. Lifecycle actions, writer imports, background jobs, cache warming, snapshot deletion/restore, archive/manifest mutation, destructive lifecycle controls, and unrelated control buttons remain unauthorized/fail-closed. |
| Production GitHub write activation | Credential/operator-gated | GitHub write behavior remains simulated/gated by operator-provisioned credentials and explicit enablement; not a default shipped operator action. |
| GLM/fourth runtime adapter | Deferred / not implemented | Future adapter candidate after the ADR-0015 pattern. |
| Split deployment / remote Postgres horizontal scaling | Deferred / not implemented | Future deployment topology; current docs retain single-backend platform framing. |
| Postgres connection mTLS | Deferred / not implemented | Internal Docker-network mTLS exists; database connection mTLS remains future work. |

## Maintenance rules

1. Update canonical sprint status first; update this page only as a derivative summary.
2. Use explicit status labels rather than “done” prose when a phase is local-only or evidence-pending.
3. Do not move deferred destructive, credentialed, or mutation-capable work to “implemented” without a BMad story, tests, review, and CI evidence.
4. For dashboard runtime work, record the exact approved route family/action and keep aggregate/session/digest/task-list/search/discovery plus destructive/additional mutation controls fail-closed unless a later phase changes that contract.
