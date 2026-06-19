# Phase 21 PRD Amendment — Dashboard Live-Read Rendering Readiness

## Summary

Phase 21 opens the **dashboard live-read rendering readiness** branch after Phase 20 closed the read-only live-read contract groundwork. Phase 20 approved per-task/readiness route and panel metadata, kept aggregate and session-list reads unavailable/needs-contract, preserved the inert static shell, and completed final no-mutation/provenance/accessibility/review/CI validation.

This Phase 21 PRD is a product planning artifact. It does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, test code, dependencies, CI/deployment changes, or operator mutation/control surfaces. It defines the product scope for turning approved Phase 20 read metadata into auditable presentation contracts and fixture-backed rendering expectations before any live browser/API wiring is allowed.

## Problem

The project now has safe read-contract metadata but does not yet have a product-level rendering contract that explains how dashboard panels should display approved read-only state without implying false authority. If rendering work begins directly with live HTTP calls, the dashboard could accidentally blur fixture/static/readiness data with live backend state, hide stale/partial/unavailable states, or reopen aggregate/session pressure before a safe contract exists.

Phase 21 therefore starts with rendering-readiness planning and test-first presentation semantics. The first story is docs/status-only: it records the PRD, architecture, epics, Story 98.1 lifecycle artifact, and sprint status opening.

## Goals

- Define product requirements for dashboard live-read presentation readiness before runtime wiring.
- Preserve read-only-by-effect and no-mutation/no-control semantics from Phases 19 and 20.
- Convert approved Phase 20 route/panel metadata into future display contracts with provenance, freshness, authority, and degraded-state semantics.
- Require fixture/snapshot and presentation-model tests before any browser `fetch`, XHR, WebSocket, EventSource, polling, or live API wiring.
- Keep aggregate overview and session-list reads unavailable/needs-contract until a later separately approved contract exists.
- Keep digest integration optional/non-core and outside the first rendering-readiness stories.
- Keep Phase 21 stories small, reviewable, reversible, and CI-gated.

## Scope

IN:

- Product requirements for Phase 21 dashboard rendering readiness.
- Presentation-model requirements for approved Phase 20 per-task/readiness panel families.
- Fixture/snapshot semantics for future static rendering tests.
- Requirements for provenance, freshness, authority, stale/error/partial/unavailable/backend-unavailable states, and explicit needs-contract copy.
- A docs/status-only first story that opens Phase 21 BMad artifacts and sprint status.

OUT:

- Runtime dashboard live wiring, browser `fetch`, XHR, WebSocket, EventSource, polling, frontend scripts, backend/API route expansion, HTTP clients, generated live data, or static shell behavior changes in Story 98.1.
- Aggregate overview/session-list live contracts or wiring.
- Digest integration.
- Mutation/control/destructive lifecycle affordances including approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth.
- Dependencies, lockfiles, deployment, CI, package, service, MCP, runtime, or test-code changes in Story 98.1.

## Functional requirements

- **FR183 — Phase 21 rendering-readiness scope.** The repository records Phase 21 as the product-scope gate for dashboard live-read rendering readiness. PRD creation alone does not authorize runtime implementation.
- **FR184 — Presentation contracts before runtime wiring.** Future dashboard rendering stories must define and test presentation-model semantics before adding live browser/API calls.
- **FR185 — Approved read metadata only.** Future renderable state must derive from approved Phase 20 per-task/readiness route and panel metadata unless a later story explicitly approves a new contract.
- **FR186 — Provenance and freshness visibility.** Future displayed rows or states must expose source route/category, source identifiers, timestamp/freshness where available, authority/non-authority status, and stale/partial/error semantics.
- **FR187 — Fixture/snapshot distinction.** Fixture-backed or static readiness displays must not be represented as live backend state; copy and metadata must preserve that distinction.
- **FR188 — Aggregate/session needs-contract.** Aggregate overview and session-list panels remain unavailable/needs-contract; this aggregate/session needs-contract boundary remains in force until a separately approved safe read contract exists.
- **FR189 — No mutation/control vocabulary.** Presentation contracts and fixture rendering must exclude mutation/control/destructive lifecycle affordances by route, method, vocabulary, and behavior.
- **FR190 — No behavior change in Story 98.1.** Story 98.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S31 — Rendering fail-closed safety.** Missing, stale, partial, invalid, unauthorized, backend-unavailable, or needs-contract states render bounded uncertainty and never healthy/authoritative success.
- **NFR-S32 — Read-only presentation boundary.** Dashboard presentation work remains visibility-only and cannot introduce controls, forms, scripts, hidden network clients, or state transitions.
- **NFR-O26 — Display auditability.** Every future rendered value is traceable to a source category/route, identifier, freshness/authority state, and approved contract.
- **NFR-M22 — Test-first rendering maintainability.** Presentation-model and fixture/snapshot tests must make drift easier to catch than manual UI inspection.
- **NFR-R22 — Safe degradation.** Backend unavailability, invalid fixture data, unauthorized responses, or absent contracts must degrade to explicit unavailable/stale/error copy.

## Acceptance criteria

1. Phase 21 PRD amendment defines rendering-readiness product scope, non-goals, FRs/NFRs, and follow-on gates.
2. Story 98.1 tracked diff is limited to Phase 21 PRD, architecture, epics, Story 98.1 artifact, and sprint-status only.
3. Sprint status sets `current_phase: 21`, opens Epic 98 / Story 98.1, and records a newest-first audit event without claiming runtime implementation.
4. The PRD explicitly excludes runtime live wiring, aggregate/session contracts, digest integration, mutation/control/destructive lifecycle affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes in Story 98.1.
5. Follow-on Phase 21 epics sequence docs/status first, presentation-contract tests second, fixture/snapshot rendering third, and live wiring only after a separate explicit story and review gate.

## Required follow-on gates before live runtime wiring

Before any dashboard live runtime wiring can start, BMAD must produce and approve:

1. Presentation-model contract tests for approved read categories, source identifiers, authority, freshness, and degraded states.
2. Fixture/snapshot rendering tests that prove static/fixture displays do not call live APIs and do not masquerade as live backend state.
3. Guard tests proving no `fetch`, XHR, WebSocket, EventSource, polling, frontend script, HTTP client, backend route expansion, aggregate/session live contract, digest integration, or mutation/control behavior is introduced prematurely.
4. Independent code-reviewer APPROVE and architect CLEAR before each story reaches done.
5. Push and GitHub Actions CI green before final Story 98.1 and later Phase 21 checkpoints.
