# Phase 19 PRD Amendment — Read-Only Web Dashboard Planning (P19-ROWD)

## Summary

Phase 19 starts the non-destructive **read-only web dashboard** planning branch selected after Phase 18 PRD-scope work. The dashboard is an operator visibility surface over existing task, session, event, replay, and lifecycle-planning state. It must not introduce approval, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, credentialed lifecycle operation, or new write controls.

This artifact is product planning only. It does not add frontend code, backend routes, runtime behavior, deployment changes, dependencies, or operator mutation surfaces.

## Problem

The platform already exposes rich state through the event spine, registry API, task history, replay validation, lifecycle dry-run planning, metrics, Telegram, and console surfaces. Operators can inspect this state, but visibility is fragmented across command-line calls, Telegram summaries, API curls, logs, and BMAD artifacts.

A browser-based dashboard can improve operator situational awareness without changing control authority if the first slice is strictly read-only.

## Goals

- Define a read-only browser dashboard product scope for operator visibility.
- Reuse existing read surfaces wherever possible before adding new API contracts.
- Make current task/session/event/replay/lifecycle status easier to inspect.
- Preserve single-writer, event-spine, Tier-3 approval, capability-tier, token-scoping, and lifecycle safety invariants.
- Keep all mutation, approval, and destructive lifecycle controls out of the initial dashboard scope.

## Scope

IN:

- PRD planning for a read-only web dashboard.
- Operator visibility requirements for task list/detail, session status, event timeline, trace correlation, replay/task-history status, and lifecycle dry-run/readiness evidence when already available through existing read contracts.
- Product requirements for safe empty/error states, read-only affordances, provenance, and auditability of displayed data.
- UX follow-on recommendation for information architecture and screens.
- Documentation/status proof that this phase is planning-only.

OUT:

- Any frontend implementation, backend route implementation, API schema change, deployment change, dependency addition, or runtime behavior change in this PRD slice.
- Approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, or scheduled job controls.
- Public replay export, share links, multi-user access, OAuth, external dashboard hosting, or credentialed production operation.
- Bypassing Telegram/console approval gates, Tier-3 decisions, capability tiers, or existing registry write ownership.
- Any weakening of Phase 18 destructive lifecycle apply gates.

## Functional requirements

- **FR169 — Read-only dashboard scope.** The initial dashboard product scope is read-only operator visibility. It must not provide mutation or approval controls.
- **FR170 — Task and session visibility.** The dashboard should make current and historical task/session state inspectable, including task status, active session, terminal state, failure/recovery signals, and stale/heartbeat signals where available.
- **FR171 — Event and trace visibility.** The dashboard should expose a task/event timeline with trace correlation and event provenance so operators can understand what happened without parsing raw logs.
- **FR172 — Replay and lifecycle visibility.** The dashboard should surface existing replay/task-history/lifecycle-readiness evidence as read-only information, including archive configuration errors and dry-run/readiness status when available through existing safe read surfaces.
- **FR173 — Safe error and empty states.** The dashboard must distinguish no data, loading, invalid archive configuration, permission/configuration failure, stale data, and internal errors without implying that mutation occurred.
- **FR174 — No behavior change in this PRD slice.** Phase 19 PRD creation must not change runtime/package/API/MCP/service/script/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S27 — Read-only by construction.** The first dashboard scope must not include mutating HTTP methods, approval forms, lifecycle apply controls, credential entry, or privileged operator actions.
- **NFR-O24 — Provenance-first display.** Displayed state should identify its source: registry projection, event log, replay/task-history response, metrics projection, or BMAD artifact.
- **NFR-M20 — Existing contract reuse.** Dashboard planning should prefer existing registry API/read surfaces and avoid new backend contracts unless UX/architecture proves they are necessary.
- **NFR-R20 — Fail-safe visibility.** If a read dependency fails, the dashboard should show a bounded, explicit error state rather than partial data that appears authoritative.
- **NFR-S28 — Lifecycle gate preservation.** Dashboard visibility must not weaken Phase 18 destructive apply requirements or create an indirect mutation path.

## Acceptance criteria

1. Phase 19 PRD amendment defines read-only dashboard product scope, non-goals, functional requirements, NFRs, and follow-on gates.
2. Sprint status records Phase 19 as a planning-only read-only dashboard branch.
3. Verification proves no runtime/package/API/MCP/service/script/deployment/dependency/lockfile/CI path changed.
4. The PRD explicitly excludes approval, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, and credentialed lifecycle operation controls.
5. Follow-on UX and architecture work are identified before any dashboard implementation story can be created.

## Required follow-on gates before implementation

Before any web dashboard implementation can start, BMAD must produce and approve:

1. UX design artifact for read-only dashboard information architecture and screen inventory.
2. Architecture amendment defining frontend/backend boundaries, auth assumptions, data freshness, error handling, and read-only enforcement.
3. Epics/stories decomposed into testable read-only slices.
4. Test design for no-mutation guarantees, route/method allowlists, error states, empty states, and source-provenance display.
5. Independent review confirming no approval/destructive lifecycle/control-plane mutation path is introduced in the initial dashboard scope.
