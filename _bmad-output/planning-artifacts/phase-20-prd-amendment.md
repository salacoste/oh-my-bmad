# Phase 20 PRD Amendment — Read-Only Dashboard Live-Read Contracts (P20-RODLC)

## Summary

Phase 20 starts the **read-only dashboard live-read contracts** planning branch after the Phase 19 static dashboard slice. Phase 19 delivered the static shell, read-only boundary tests, static visibility panels, no-mutation copy, accessibility/responsiveness coverage, and final quality gate evidence. Phase 20 defines the product scope for a future transition from static dashboard visibility to live read-only data integration.

This artifact is product planning only. It does not add frontend wiring, backend routes, runtime behavior, JavaScript behavior, dependencies, deployment changes, CI changes, or operator mutation/control surfaces.

## Problem

The Phase 19 dashboard is intentionally static and safe. It establishes the operator-facing information architecture and proves the read-only/no-mutation boundary, but it does not yet consume live read surfaces. Operators need a future path to inspect current task, event, trace, replay, and health state from the dashboard without weakening the established safety model.

The next risk is not visual layout; it is **read-contract drift**: a dashboard can accidentally become a control surface or trigger side effects if live reads are introduced without exact route, method, provenance, freshness, and failure contracts.

## Goals

- Define product requirements for live read-only dashboard contracts before implementation.
- Preserve read-only-by-effect semantics from Phase 19.
- Prefer existing safe read surfaces before any new backend contract is considered.
- Require explicit unavailable/needs-contract states when aggregate or session-list reads are not safely available.
- Keep destructive lifecycle apply and all mutation/control surfaces outside Phase 20 scope.
- Make contract tests first: contract tests are the first future implementation artifact before live UI wiring.
- Require separate approved stories and review gates before any implementation beyond this docs/status-only planning story.

## Scope

IN:

- Product requirements for future live read-only dashboard integration.
- Requirements for allowed read-route inventory, route/method allowlists, no-hidden-write proofs, provenance, freshness, stale/error states, and unavailable states.
- Requirements for a docs/status-only first Phase 20 story that reconciles BMad planning artifacts and `current_phase` metadata before runtime work.
- Requirements that aggregate overview/session-list data remain unavailable/needs-contract unless a later approved GET contract proves read-only-by-effect behavior.
- Requirements that optional logs digest integration remains excluded from the first implementation story unless separately approved because it can involve LLM/external-service dependency and latency.

OUT:

- Runtime implementation, frontend live wiring, JavaScript behavior, backend route implementation, API schema changes, dependencies, deployment changes, CI changes, or test-code implementation in this PRD slice.
- POST, PUT, PATCH, DELETE dashboard calls or any dashboard call that mutates state by effect.
- Approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, public share/export, OAuth, external hosting, multi-user auth, credential entry, or token minting.
- Destructive lifecycle apply implementation or any weakening of Phase 18/Phase 17 destructive lifecycle gates.
- New aggregate task/session GET contracts without a later architecture gate and test-first story proving no hidden writes, no background dispatch, and no cache-warming side effects.

## Functional requirements

- **FR175 — Phase 20 live-read contract scope.** The repository records Phase 20 as the product-scope gate for future dashboard live-read contracts. Phase 20 PRD creation alone does not authorize runtime implementation.
- **FR176 — Existing safe reads first.** Future live dashboard stories must use existing approved safe read surfaces before proposing any new read contract.
- **FR177 — Read-only by effect.** Future dashboard reads must prove no hidden writes, no cache-warming writes/read-side effects, no background-job dispatch, no mutation route reachability, and no control-plane operation.
- **FR178 — Unavailable/needs-contract for missing aggregate reads.** If no approved safe aggregate overview or session-list read exists, the dashboard must show an explicit unavailable/needs-contract state instead of synthesizing authoritative data.
- **FR179 — Provenance and freshness.** Every future live value must expose source route/category, retrieved-at or emitted-at timestamp where available, freshness/staleness status, and task/session/event/trace reference where applicable.
- **FR180 — Optional digest exclusion.** Logs digest or other external-service-dependent reads must remain non-core and excluded from first implementation unless separately approved by architecture and tests.
- **FR181 — Contract tests before live wiring.** Future implementation must land route/method/effect allowlist and provenance/freshness tests before live UI wiring.
- **FR182 — No behavior change in this PRD slice.** This PRD slice must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S29 — Live-read fail-closed safety.** Missing, stale, unauthorized, partial, invalid, or unsafe reads render explicit bounded states and never imply healthy or authoritative data.
- **NFR-S30 — Control-surface exclusion.** Dashboard live-read contracts must exclude mutation/control affordances and privileged operator actions by vocabulary, route, method, and effect.
- **NFR-O25 — Auditability of displayed state.** Live dashboard state must be explainable from source route/category and freshness metadata.
- **NFR-M21 — Contract-first maintainability.** Read contracts, allowlists, and tests must be easier to audit than the UI wiring that consumes them.
- **NFR-R21 — Safe degradation.** Backend unavailability, route failure, invalid archive/replay state, or missing approved read contracts must degrade to visible uncertainty, not synthetic success.

## Acceptance criteria

1. Phase 20 PRD amendment defines live-read contract product scope, non-goals, FRs, NFRs, and implementation gates.
2. Sprint status records Phase 20 as opened by a docs/status-only planning story and reconciles stale `current_phase: 19` metadata before runtime work.
3. Verification proves no runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI path changed in this PRD/story slice.
4. The PRD explicitly excludes digest integration, aggregate/session-list contracts, live wiring, destructive lifecycle apply, and all mutation/control surfaces from the first story.
5. Follow-on architecture and epics define contract-first stories before any live dashboard wiring can start.

## Required follow-on gates before implementation

Before any live-read dashboard implementation can start, BMAD must produce and approve:

1. Phase 20 architecture amendment enumerating allowed existing safe reads, excluded reads, route/method allowlists, no-hidden-write requirements, provenance/freshness, and unavailable states.
2. Phase 20 epics/stories decomposed into docs/status first, contract tests second, and only then narrow live-read wiring stories.
3. Test design for mutation-route rejection, hidden-write rejection, background-dispatch rejection, cache-warming side-effect rejection, missing aggregate-read unavailable states, digest exclusion, provenance/freshness, and degraded/error states.
4. Independent architecture and code-review gates before any runtime dashboard live wiring is completed.
5. Final CI and UltraQA evidence before any live-read implementation story reaches done.
