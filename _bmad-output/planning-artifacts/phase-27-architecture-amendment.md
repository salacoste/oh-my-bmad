# Phase 27 Architecture Amendment — Lifecycle / Snapshot Live-Read Route Selection

## Decision summary

Phase 27 may proceed from the completed History / Replay runtime-boundary proof into **Lifecycle / Snapshot live-read route-family planning**. This amendment selects exactly this future read surface:

- `GET /v1/events/replay/snapshots`
- passive lifecycle-readiness evidence fields from `dashboard/static/replay-lifecycle-contract.json`

Story 106.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, backend/API expansion, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, snapshot creation, replay execution, lifecycle apply/prune/rollback, archive/manifest mutation, or mutation/control surfaces.

## Inputs

- `_bmad-output/implementation-artifacts/105-3-phase-26-epic-105-final-validation-closure.md`
- `_bmad-output/planning-artifacts/phase-26-epics.md`
- `docs/api-contracts.md`
- `dashboard/static/replay-lifecycle-contract.json`
- `.omx/interviews/phase-27-lifecycle-snapshot-planning-deep-interview.md`
- `.omx/specs/phase-27-lifecycle-snapshot-planning-ralplan.md`
- `.omx/specs/phase-27-lifecycle-snapshot-planning-test-spec.md`

## Route selection rationale

Lifecycle/snapshot visibility is the next safest branch because Phase 26 explicitly deferred it first after history/replay, and because the API contract already distinguishes snapshot listing from snapshot creation. It is risky enough to require a planning-first split: a snapshot/lifecycle surface can accidentally become snapshot creation, replay execution, archive traversal, lifecycle apply/prune/rollback, or control affordances.

## Architectural boundaries

### Boundary 1 — Story 106.1 is docs/status-only

Story 106.1 may create or update only Phase 27 planning artifacts, the Story 106.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact read surface only

Future Phase 27 runtime work may target only `GET /v1/events/replay/snapshots` for snapshot list visibility and passive lifecycle-readiness evidence display from the static lifecycle contract. It may not silently include `POST /v1/events/replay/snapshots`, snapshot creation, snapshot deletion, lifecycle apply/prune/rollback, task-list/search/discovery, aggregate overview, session list, digest, stream, generated live data, replay execution, trace/history expansion, or control routes.

### Boundary 3 — Snapshot list semantics

Future dashboard calls must be GET-only and body-free. Snapshot identifiers, sequence numbers, timestamps, and paths returned by the list response are display/provenance metadata only. They must not become hidden route selectors, download paths, replay targets, archive traversal keys, mutation targets, or controls unless a later explicit product and architecture gate selects that surface.

### Boundary 4 — Passive lifecycle-readiness evidence

Future lifecycle-readiness display may render bounded passive evidence labels such as plan hash, dry-run artifact reference, safety policy version, retention input digest, affected segment count/identity summary, replay validation reference, rollback evidence reference, operator authorization reference, archive manifest reference, and archive validation status. Evidence fields are not buttons, links, route selectors, background-job inputs, production operations, or mutation authorizations.

### Boundary 5 — Destructive lifecycle remains separate

Lifecycle apply/prune/rollback, archive/manifest mutation, destructive authorization gates, scheduled retention, object-storage lifecycle jobs, and production operations remain separate future-only surfaces. Existing Phase 17 readiness contracts remain guardrails, not implementation approval.

### Boundary 6 — No hidden writes or side effects

Future implementation tests must fail on writer imports/calls, snapshot creation helpers, lifecycle helper mutation paths, archive/manifest mutation, replay execution jobs, background validation jobs, idempotency writes, cache-warming write paths, side-effectful reads, mutation/control vocabulary, POST/PUT/PATCH/DELETE dashboard calls, or hidden discovery/aggregation.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. exact route allowlist for `GET /v1/events/replay/snapshots` only;
2. GET-only and body-free dashboard calls;
3. no `POST /v1/events/replay/snapshots`, snapshot creation, or snapshot mutation reachability;
4. passive lifecycle evidence is display/provenance metadata only;
5. snapshot and lifecycle identifiers are not hidden selectors, controls, replay targets, archive traversal keys, or generated-data sources;
6. no task-list/search/discovery, aggregate/session/digest, replay execution, archive/manifest mutation, lifecycle apply/prune/rollback, generated live data, or control reachability;
7. empty/unavailable/stale/invalid/unauthorized/non-2xx/backend-unavailable/network-failure/unverifiable-evidence rendering semantics;
8. visible source route, evidence source, freshness/retrieved-at or emitted-at, authority, provenance identifiers, and degraded-state metadata;
9. static no-hidden-write/import grep guards;
10. existing health, task-detail, event/transition, trace, and history/replay runtime-boundary tests remain green.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 106.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.
