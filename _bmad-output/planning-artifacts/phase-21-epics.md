# Phase 21 Epics — Dashboard Live-Read Rendering Readiness

Phase 21 is **rendering-readiness-first**. It converts approved Phase 20 route/panel metadata into auditable presentation contracts and fixture-backed rendering expectations before any live browser/API wiring is allowed. This artifact does not implement runtime live dashboard wiring, frontend scripts, backend routes, dependencies, CI/deployment changes, or mutation/control surfaces.

## Requirements traceability

- **FR183 — Phase 21 rendering-readiness scope:** Phase 21 is the product-scope gate for dashboard live-read rendering readiness.
- **FR184 — Presentation contracts before runtime wiring:** Presentation semantics and tests precede live browser/API calls.
- **FR185 — Approved read metadata only:** Renderable state derives from approved Phase 20 per-task/readiness metadata unless later approved.
- **FR186 — Provenance and freshness visibility:** Displayed values carry source, identifiers, authority, freshness, and degraded-state semantics.
- **FR187 — Fixture/snapshot distinction:** Fixture/static readiness displays must not masquerade as live backend state.
- **FR188 — Aggregate/session needs-contract:** Aggregate overview and session-list remain unavailable/needs-contract.
- **FR189 — No mutation/control vocabulary:** Presentation contracts exclude mutation/control/destructive affordances.
- **FR190 — No behavior change in Story 98.1:** Story 98.1 changes only BMad planning/status artifacts.
- **NFR-S31 — Rendering fail-closed safety**
- **NFR-S32 — Read-only presentation boundary**
- **NFR-O26 — Display auditability**
- **NFR-M22 — Test-first rendering maintainability**
- **NFR-R22 — Safe degradation**

## Standard Phase 21 guardrail

Every Phase 21 story must preserve this rule: dashboard rendering readiness remains read-only by effect; no runtime live API calls, no browser `fetch`, no XHR, no WebSocket, no EventSource, no polling, no hidden HTTP clients, no backend/API route expansion, no aggregate/session live contract, no digest integration unless separately approved, no mutation/control/destructive lifecycle affordance, no dependency/lockfile/CI/deployment change unless explicitly scoped by a later story. Story 98.1 is docs/status-only.

## Epic 98 — Phase 21 planning and status gate

### Story 98.1: Phase 21 PRD, architecture, epics, and sprint-status opening

- Status: current docs/status planning story.
- Scope: create Phase 21 PRD, architecture, epics, Story 98.1 lifecycle artifact, and update sprint status to open Phase 21.
- Governing FR/NFR: FR183, FR184, FR185, FR186, FR187, FR188, FR189, FR190, NFR-S31, NFR-S32, NFR-O26, NFR-M22, NFR-R22.
- Acceptance criteria:
  - Phase 21 PRD, architecture, and epics artifacts exist and define rendering-readiness scope.
  - Story 98.1 artifact records lifecycle evidence, scope, non-goals, verification plan, and completion criteria.
  - Sprint status sets `current_phase: 21`, opens Epic 98 / Story 98.1, and records newest-first audit evidence.
  - Tracked diff is limited exactly to the five Story 98.1 BMad planning/status files.
  - No runtime/dashboard/test/API/backend/CI/dependency/lockfile/script/deployment file changes occur.
  - Runtime live wiring, aggregate/session contracts, digest integration, and mutation/control surfaces remain explicitly unauthorized.
- Safety guardrails: docs/status-only; no live wiring; no aggregate/session live contract; no digest; no mutation/control/destructive lifecycle affordance.

## Epic 99 — Presentation model and fixture contract tests

### Story 99.1: Dashboard live-read view-model contract tests

- Status: future test-first story; not implemented by Story 98.1.
- Scope: define expected view-model semantics for approved Phase 20 panel routes: source route/category, identifiers, freshness, authority, display severity, empty/unavailable copy, and row grouping.
- Acceptance criteria:
  - Tests fail if an approved Phase 20 route lacks display metadata.
  - Tests fail if non-authoritative, stale, partial, invalid, unauthorized, or backend-unavailable states render as healthy/authoritative.
  - Tests fail if aggregate/session/digest routes appear in renderable view models without a separate approved contract.
  - Tests fail if mutation/control vocabulary or forbidden methods become reachable.
- Safety guardrails: no UI/browser runtime wiring; no backend route expansion; no mutation/control surface.

### Story 99.2: Fixture/snapshot rendering contract tests

- Status: future fixture/test story; not implemented by Story 98.1.
- Scope: define a safe fixture/snapshot format for dashboard rendering tests without live network calls.
- Acceptance criteria:
  - Fixture schema carries provenance, freshness, source identifiers, and authority/degraded-state fields.
  - Invalid, stale, partial, unauthorized, backend-unavailable, and needs-contract states render bounded copy.
  - Fixture tests prove no script, `fetch`, XHR, WebSocket, EventSource, polling, HTTP client, or live API URL is introduced.
  - Aggregate/session panels remain unavailable/needs-contract.
- Safety guardrails: fixture/static readiness is not live backend state.

## Epic 100 — Static shell rendering readiness

### Story 100.1: Static HTML/presentation rendering for fixture-backed read-only states

- Status: future rendering story; not implemented by Story 98.1.
- Scope: after Epic 99 tests exist, add minimal static rendering or generated markup for approved panel families, still without runtime network calls.
- Acceptance criteria:
  - Static shell displays fixture-backed panel rows/states with source and freshness labels.
  - Existing static read-only boundary tests remain green.
  - No form/button/input/control scripts are introduced.
  - No live API URL appears in runtime contexts.
- Safety guardrails: no live browser/API wiring; no aggregate/session contract; no mutation/control affordance.

### Story 100.2: Live-read wiring decision gate

- Status: future decision story; not implemented by Story 98.1.
- Scope: decide whether Phase 21 is ready for a later narrow live HTTP read story, or whether more fixture/static validation is required.
- Acceptance criteria:
  - Independent code review and architecture review evaluate readiness.
  - If live wiring is proposed, it is scoped to approved GET routes only and must add e2e/browser/runtime-boundary tests before implementation.
  - If not ready, sprint status records deferred live wiring and keeps dashboard fixture/static only.
- Safety guardrails: no live wiring implementation occurs inside the decision gate unless a later story separately scopes and verifies it.

## Phase 21 completion criteria

Phase 21 can be considered complete only after docs/status opening, presentation-model tests, fixture/snapshot rendering tests, static rendering readiness, independent review, UltraQA pass/skip evidence, push, and CI green are all recorded. Aggregate/session and digest remain deferred unless a later story explicitly approves them.
