# Phase 21 Architecture Amendment — Dashboard Live-Read Rendering Readiness

## Decision Summary

Phase 21 may proceed from Phase 20 contract metadata into **dashboard live-read rendering readiness**. The architecture remains contract-first and read-only by effect. This amendment authorizes presentation-model and fixture/snapshot design constraints only. It does not authorize runtime live dashboard wiring, browser network calls, frontend scripts, backend/API route expansion, aggregate/session live contracts, digest integration, dependency changes, CI/deployment changes, or mutation/control surfaces.

The architectural rule is **presentation contracts before runtime wiring**. A value may be displayed only when its source, authority, freshness, and degraded-state semantics are explicit enough to avoid false authority.

## Inputs

- `_bmad-output/planning-artifacts/phase-20-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-epics.md`
- `_bmad-output/implementation-artifacts/97-1-aggregate-session-contract-decision.md`
- `_bmad-output/implementation-artifacts/97-2-phase-20-final-validation.md`
- `.omx/plans/phase-21-dashboard-live-read-rendering-readiness-plan.md`
- `.omx/specs/autopilot-story-98-1-ralplan-architect-review.md`
- `.omx/specs/autopilot-story-98-1-ralplan-critic-review.md`

## Architectural Boundaries

### Boundary 1 — Story 98.1 is docs/status-only

Story 98.1 may create or update only:

- `_bmad-output/planning-artifacts/phase-21-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-21-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-21-epics.md`
- `_bmad-output/implementation-artifacts/98-1-phase-21-prd-architecture-epics-status-opening.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

It must not edit runtime code, dashboard HTML, dashboard tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Presentation model before runtime network

Future Phase 21 stories must first define and test a pure presentation model that can be evaluated from approved metadata/fixtures. Runtime browser/API wiring remains forbidden until a later explicit live-wiring story passes architecture and review gates.

Required future presentation fields include:

- panel family and display state;
- source category and approved route identity;
- route input identifiers distinct from display row identifiers;
- authority state (`authoritative`, `non_authoritative`, `needs_contract`, or equivalent bounded vocabulary);
- freshness/staleness timestamp or explicit unavailable marker;
- degraded state category for stale, partial, invalid, unauthorized, backend-unavailable, or needs-contract data;
- copy that distinguishes fixture/static readiness from live backend state.

### Boundary 3 — Fixture/snapshot semantics

Fixture-backed rendering tests must use bounded static inputs. Fixtures may represent approved source metadata and degraded states, but they must not create an implicit live backend contract or authorize live API calls.

Fixture/snapshot artifacts must prove:

- provenance and freshness metadata travel with displayed values;
- stale/partial/invalid/unauthorized/backend-unavailable states do not render healthy;
- aggregate/session panels remain unavailable/needs-contract;
- no `fetch`, XHR, WebSocket, EventSource, polling, frontend script, HTTP client, backend route expansion, digest integration, or mutation/control behavior is introduced by fixture rendering.

### Boundary 4 — Aggregate/session remain unavailable

Phase 21 does not approve aggregate overview or session-list live reads. These surfaces remain unavailable/needs-contract until a separate contract proves no hidden writes, no background dispatch, no cache-warming side effects, clear pagination/freshness/provenance semantics, and no mutation route reachability.

### Boundary 5 — Digest remains non-core

Digest integration remains optional/non-core and excluded from Story 98.1 and the initial rendering-readiness test stories because it may involve external service/LLM latency and degraded behavior outside the core read-only dashboard rendering path.

## Allowed Data Categories for Phase 21 Rendering Readiness

Allowed future rendering-readiness work may reference only approved Phase 20 per-task/readiness metadata categories unless a later story narrows or rejects them:

| Category | Phase 21 stance |
|---|---|
| Task detail | Approved metadata source for future presentation-model tests. |
| Task event/timeline | Approved metadata source for future presentation-model tests. |
| Task transitions | Approved metadata source for future presentation-model tests. |
| Trace correlation | Approved metadata source for future presentation-model tests. |
| Task history | Approved metadata source for future presentation-model tests. |
| Replay/readiness | Approved metadata source for future presentation-model tests. |
| Snapshot listing visibility | Approved listing/readiness metadata only; snapshot creation remains forbidden. |
| Health/readiness | Approved metadata source for future presentation-model tests. |
| Aggregate overview | Unavailable/needs-contract. |
| Session list | Unavailable/needs-contract. |
| Digest | Excluded until separately approved. |

## Forbidden Surfaces

Phase 21 stories must not add or expose:

- approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credential entry, token minting, production operation, public share/export, OAuth, external hosting, or multi-user auth controls;
- frontend forms/buttons/links/scripts that trigger state transitions;
- POST, PUT, PATCH, DELETE dashboard calls;
- browser `fetch`, XHR, WebSocket, EventSource, polling, or HTTP clients before a separate approved live-wiring story;
- backend/API route expansion in Story 98.1;
- registry/event-log writers, idempotency-cache writes, lifecycle apply/prune helpers, snapshot creation, archive mutation, job dispatch, or cache-warming writes;
- dependency, lockfile, deployment, or CI changes in Story 98.1.

## Required Future Verification

Future Phase 21 implementation stories must include:

1. Exact changed-file allowlist checks matching story scope.
2. Presentation-model tests for source category, route identity, identifiers, authority, freshness, and degraded states.
3. Fixture/snapshot tests proving fixture-backed displays are not live backend state.
4. Static guard scans for forbidden browser/network/runtime/backend/mutation/control surfaces.
5. Aggregate/session unavailable/needs-contract assertions.
6. Digest exclusion assertions until separately approved.
7. Independent code-reviewer APPROVE and architect CLEAR.
8. GitHub Actions CI green after push.

## Handoff to Epics and Stories

The next BMAD artifact is `_bmad-output/planning-artifacts/phase-21-epics.md`. It decomposes Phase 21 into docs/status opening, presentation-model contract tests, fixture/snapshot rendering tests, static rendering readiness, and a later live-wiring decision gate.
