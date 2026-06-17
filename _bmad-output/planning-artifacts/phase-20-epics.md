# Phase 20 Epics — Read-Only Dashboard Live-Read Contracts

Phase 20 remains **planning-first**. It defines and sequences the safe path from Phase 19 static dashboard visibility toward future live read-only integration. This artifact does not implement live dashboard wiring, frontend live wiring, backend routes, runtime behavior, dependencies, deployment changes, CI changes, or mutation/control surfaces.

## Requirements traceability

- **FR175 — Phase 20 live-read contract scope:** Phase 20 is the product-scope gate for future dashboard live-read contracts.
- **FR176 — Existing safe reads first:** Future live dashboard stories use existing safe reads before proposing backend expansion.
- **FR177 — Read-only by effect:** Reads must prove no hidden writes, background dispatch, cache-warming writes, mutation reachability, or control-plane operation.
- **FR178 — Unavailable/needs-contract for missing aggregate reads:** Missing aggregate/session safe reads render explicit unavailable states.
- **FR179 — Provenance and freshness:** Live values expose source, timestamp/freshness, and relevant identifiers.
- **FR180 — Optional digest exclusion:** Digest/external-service-dependent reads are non-core and excluded from first implementation unless separately approved.
- **FR181 — Contract tests before live wiring:** Route/method/effect tests land before live UI wiring.
- **FR182 — No behavior change in this PRD slice:** Planning artifacts do not alter runtime behavior.
- **NFR-S29 — Live-read fail-closed safety**
- **NFR-S30 — Control-surface exclusion**
- **NFR-O25 — Auditability of displayed state**
- **NFR-M21 — Contract-first maintainability**
- **NFR-R21 — Safe degradation**

## Standard Phase 20 guardrail

Every Phase 20 story must preserve this exact rule: dashboard live-read work is read-only by effect; no mutation routes; no hidden writes; no background-job dispatch; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth. The first story remains docs/status-only; later implementation requires separate approved stories, contract tests first before live wiring, and review gates.

## Epic 93 — Phase 20 planning and status gate

### Story 93.1: Phase 20 PRD, architecture, epics, and sprint-status opening

- Status: current docs/status planning story; no runtime/dashboard/test implementation.
- Scope: create Phase 20 PRD, architecture, epics, story/status evidence, and reconcile stale Phase 19 `current_phase` metadata.
- Governing FR/NFR: FR175, FR176, FR177, FR178, FR179, FR180, FR181, FR182, NFR-S29, NFR-S30, NFR-O25, NFR-M21, NFR-R21.
- Acceptance criteria:
  - Phase 20 PRD, architecture, and epics artifacts exist and define live-read contract scope.
  - Sprint status opens Phase 20 and adds Story 93.1 without claiming runtime implementation.
  - Tracked changes are limited to Phase 20 planning/status/story artifacts.
  - Runtime code, dashboard HTML, dashboard tests, API/backend code, CI, dependency manifests, lockfiles, scripts, and deployment files are not changed.
  - Future implementation remains blocked until contract tests and separate stories are approved.
- Safety guardrails: docs/status-only; no live wiring; no digest integration; no aggregate/session-list read contract; no destructive lifecycle or mutation/control surface.

## Epic 94 — Contract tests and read-effect proof

### Story 94.1: Route/method/effect allowlist contract tests

- Status: future implementation story; not implemented in this planning story.
- Scope: add or extend tests proving dashboard live-read code can reach only approved read routes and cannot trigger mutation/control behavior by method, route, import, effect, or vocabulary.
- Governing FR/NFR: FR176, FR177, FR181, NFR-S29, NFR-S30, NFR-M21.
- Acceptance criteria:
  - Tests fail on POST, PUT, PATCH, DELETE, unapproved GET routes, mutation/control vocabulary, writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming writes, or hidden write paths.
  - Tests preserve existing Phase 19 read-only boundary coverage.
  - No live UI wiring is required unless explicitly scoped by the story.
- Safety guardrails: contract tests before live wiring; no new dashboard data source is approved by this story alone.

### Story 94.2: Provenance, freshness, stale/error-state contract tests

- Status: future implementation story; not implemented in this planning story.
- Scope: define and test required source, timestamp/freshness, identifier, unavailable, partial, stale, invalid, unauthorized, and backend-unavailable states for future live values.
- Governing FR/NFR: FR179, NFR-S29, NFR-O25, NFR-R21.
- Acceptance criteria:
  - Every future displayed live value has source category/route and freshness semantics.
  - Invalid or partial replay/lifecycle data cannot render as healthy.
  - Missing aggregate/session reads render unavailable/needs-contract copy.
- Safety guardrails: no data synthesis that appears authoritative without approved source/provenance.

## Epic 95 — Existing safe-read adapter readiness

### Story 95.1: Minimal read adapter boundary for existing per-task safe reads

- Status: future implementation story; not implemented in this planning story.
- Scope: introduce a minimal adapter boundary for approved existing per-task reads only after contract tests exist.
- Governing FR/NFR: FR176, FR177, FR179, FR181, NFR-S29, NFR-O25, NFR-M21.
- Candidate/provisional reads: task detail, task events, task transitions, trace correlation, task history, replay state, replay validation, snapshot listing, and health. These names are planning labels, not route or adapter lock-in; contract tests may narrow, rename, or reject them.
- Acceptance criteria:
  - Adapter exposes route/source/freshness metadata and error categories.
  - Adapter cannot call mutation/control routes or hidden write paths.
  - Digest route remains excluded unless separately approved.
  - Aggregate/session-list panels remain unavailable/needs-contract.
- Safety guardrails: adapter boundary only; no broad aggregate contract; no mutation/control behavior.

## Epic 96 — Narrow live-read panel wiring

### Story 96.1: Task detail, event timeline, and trace live-read panels

- Status: future implementation story; not implemented in this planning story.
- Scope: wire candidate/provisional task detail, task events/transitions, and trace correlation panel families to approved existing safe reads, one narrow panel family at a time; panel names may change if contract tests or architecture evidence require it.
- Governing FR/NFR: FR176, FR177, FR179, NFR-S29, NFR-S30, NFR-O25, NFR-R21.
- Acceptance criteria:
  - Panels show source route/category, freshness, and task/event/trace identifiers.
  - Partial route failure renders visible partial/unavailable states.
  - No approval/retry/cancel/budget/lifecycle controls appear.
- Safety guardrails: no aggregate overview/session-list contract; no digest integration; no mutation/control surface.

### Story 96.2: Replay, history, health, and lifecycle-readiness live-read panels

- Status: future implementation story; not implemented in this planning story.
- Scope: wire approved replay/history/health reads to candidate/provisional static lifecycle/readiness panel families while preserving fail-safe ProblemDetails and unavailable states; panel names may change if contract tests or architecture evidence require it.
- Governing FR/NFR: FR176, FR177, FR179, FR181, NFR-S29, NFR-S30, NFR-O25, NFR-R21.
- Acceptance criteria:
  - Replay/history/health panels distinguish healthy, stale, invalid, partial, unauthorized, unavailable, and backend failure states.
  - Archive/replay/lifecycle errors never render as healthy.
  - No apply/prune/delete/truncate/move/rewrite/chmod/archive mutation/manifest mutation control appears.
- Safety guardrails: lifecycle visibility remains informational only.

## Epic 97 — Aggregate/session contract decision and final validation

### Story 97.1: Aggregate overview/session-list contract decision

- Status: future decision story; not implemented in this planning story.
- Scope: decide whether to keep aggregate overview/session-list panels unavailable or propose a separately approved read-only GET contract.
- Governing FR/NFR: FR176, FR177, FR178, FR179, NFR-S29, NFR-S30, NFR-O25, NFR-M21.
- Acceptance criteria:
  - If no safe aggregate/session-list contract is approved, dashboard copy remains unavailable/needs-contract.
  - If a contract is proposed, it includes pagination/freshness/provenance semantics and no-hidden-write/background-dispatch/cache-warming proof before implementation.
  - Decision is reviewed independently before any wiring.
- Safety guardrails: no silent aggregate synthesis; no side-effectful discovery.

### Story 97.2: Phase 20 final no-mutation, provenance, accessibility, review, and CI gate

- Status: future final validation story; not implemented in this planning story.
- Scope: final Phase 20 validation after approved stories complete.
- Governing FR/NFR: all Phase 20 FR/NFR.
- Acceptance criteria:
  - Route/method/effect allowlists pass.
  - Provenance/freshness/stale/error-state tests pass.
  - Dashboard accessibility/responsiveness/read-only boundary tests pass.
  - Independent code-review APPROVE, architecture CLEAR, UltraQA pass or justified skip, push, and CI green are recorded.
  - Sprint status closes completed Phase 20 stories/epics accurately.
- Safety guardrails: no mutation/control surfaces and no destructive lifecycle behavior.
