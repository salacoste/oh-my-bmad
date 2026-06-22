# Feature Status Matrix

> **Derivative status summary.** The canonical implementation status is
> [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml).
> This page is a read-only, human-navigable summary last verified from the files
> named below on 2026-06-22. If this page conflicts with sprint-status, trust
> sprint-status and update this derivative matrix.

## Current BMad status

- **Current phase:** Phase 23 — Task Detail Live-Read Runtime Boundary is closure-in-progress.
- **Current epic:** Epic 102 is closure-pending until final review, QA/skip, push, and remote CI evidence exists.
- **Done in Phase 23 so far:**
  - Story 102.1 selected exactly `GET /v1/tasks/{task_id}` for the next dashboard live-read route family and opened the Phase 23 PRD/architecture/epics/status artifacts.
  - Story 102.2 implemented the narrow browser/runtime task-detail boundary with a single dashboard runtime module.
- **Pending in Phase 23:**
  - Story 102.3 final closure/status hygiene. It must keep Epic 102 pending until final code-review, QA/skip, commit/push, and remote CI evidence are recorded.
- **Not closed by this page:** Any later dashboard live-read route family beyond Health/readiness and Task detail.

Primary evidence:

- `../_bmad-output/implementation-artifacts/sprint-status.yaml` — `current_phase: 23`, `epic-102` in progress, `102-1` done, `102-2` done, and `102-3` in progress/pending final gates.
- `../_bmad-output/planning-artifacts/phase-23-epics.md` — Phase 23 route-selection and closure guardrails.
- `../_bmad-output/implementation-artifacts/102-2-task-detail-runtime-boundary.md` — Story 102.2 local implementation/review/QA evidence.
- `../_bmad-output/implementation-artifacts/102-3-phase-23-epic-102-final-closure.md` — Story 102.3 closure-prep artifact; final closure remains pending until gates pass.
- `../dashboard/static/task-detail.js` — browser runtime calls only `GET /v1/tasks/{task_id}`.
- `../services/registry-api/src/registry_api/routes/tasks.py` — backend `GET /v1/tasks/{task_id}` route exists from the registry API.

## Status categories

| Status | Meaning |
|---|---|
| Implemented | Present in code/docs/tests and marked done in BMad status. |
| Implemented locally / closure-pending | Present in the working tree and locally verified, but enclosing story/epic final closure is pending review/QA/push/CI. |
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
| Task detail dashboard runtime | Implemented locally / closure-pending | Phase 23 / Epic 102: Story 102.2 `dashboard/static/task-detail.js` uses exactly `GET /v1/tasks/{task_id}` with visible task-id provenance and fail-closed states. Story 102.3 final closure remains pending review/QA/push/CI. |

## Contract/static-only or intentionally narrow areas

| Area | Status | Boundary |
|---|---|---|
| Dashboard event timeline, transitions, trace, history, replay, lifecycle readiness | Contract/static-only | Existing panels/tests define approved read-route metadata and unavailable/freshness states, but browser runtime wiring remains intentionally narrow unless a later PRD/story approves one route family at a time. |
| Task/session overview and sessions visibility | Contract/static-only | Static dashboard copy and metadata distinguish resource-native fields from derived/unavailable semantics; no broad session HTTP/list/history/search runtime is authorized. |
| Fixture snapshot/static rendering | Contract/static-only | Fixture/view-model/static rendering tests prove read-only presentation behavior and fail-closed unavailable states; they do not authorize new runtime polling or mutation controls. |

## Deferred, unavailable, or not implemented

| Area | Status | Reason / guardrail |
|---|---|---|
| Later dashboard route families after task detail | Deferred / not implemented | Event timeline, transitions, trace, history, replay, lifecycle, aggregate/session/digest, and task-list/search/discovery require future separate stories. |
| Destructive lifecycle apply/prune/delete/truncate/move/rewrite/chmod | Deferred / not implemented | Phase 17 is readiness-only. Future mutation requires exact dry-run plan-hash binding, replay validation, rollback/restore evidence, and explicit operator gate. |
| Object-storage lifecycle jobs and scheduled retention | Deferred / not implemented | `docs/architecture.md` keeps automatic S3/B2/R2 lifecycle management and time-based lifecycle automation as future work. |
| Broad dashboard live-read runtime wiring | Deferred / not implemented | Current browser runtime is narrow: health/readiness and task detail only. Later route families require separate PRD/architecture/test-spec approval. |
| Aggregate/session/digest dashboard live contracts | Deferred / fail-closed | Aggregate/session/digest reads are intentionally unavailable or excluded until safe contracts are approved. |
| Task-list/search/discovery hidden calls | Deferred / fail-closed | Phase 23 guardrails forbid hidden discovery, task-list/search calls, broad dashboard wiring, and backend/API expansion. |
| Mutation/control affordances in dashboard | Deferred / not implemented | Dashboard scope remains read-only by effect; no control buttons, lifecycle actions, writer imports, background jobs, cache warming, or snapshot creation are authorized. |
| Production GitHub write activation | Credential/operator-gated | GitHub write behavior remains simulated/gated by operator-provisioned credentials and explicit enablement; not a default shipped operator action. |
| GLM/fourth runtime adapter | Deferred / not implemented | Future adapter candidate after the ADR-0015 pattern. |
| Split deployment / remote Postgres horizontal scaling | Deferred / not implemented | Future deployment topology; current docs retain single-backend platform framing. |
| Postgres connection mTLS | Deferred / not implemented | Internal Docker-network mTLS exists; database connection mTLS remains future work. |

## Maintenance rules

1. Update canonical sprint status first; update this page only as a derivative summary.
2. Use explicit status labels rather than “done” prose when a phase is local-only or evidence-pending.
3. Do not move deferred destructive, credentialed, or mutation-capable work to “implemented” without a BMad story, tests, review, and CI evidence.
4. For dashboard runtime work, record the exact approved route family and keep aggregate/session/digest/task-list/search/discovery fail-closed unless a later phase changes that contract.
