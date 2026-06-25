# Phase 28 PRD Amendment — Snapshot Creation Authorization Planning

## Summary

Phase 28 opens the next narrow post-Lifecycle/Snapshot planning branch after Phase 27 closed `GET /v1/events/replay/snapshots` and passive lifecycle-readiness display. Phase 28 selects exactly the **Snapshot Creation authorization** surface for future runtime consideration:

- `POST /v1/events/replay/snapshots`

Story 107.1 is docs/status-only. It does not add runtime behavior, dashboard JavaScript/HTML behavior, browser network calls, backend/API route changes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, destructive lifecycle operations, discovery/search, aggregate/session/digest, archive/manifest mutation, replay execution controls, or production credential-gated writes.

## Problem

The repository already contains a backend snapshot creation route from earlier replay work, while recent dashboard phases intentionally authorized only read-only route families. After Phase 27, the dashboard can list replay snapshots and display passive lifecycle-readiness evidence, but it still cannot create snapshots from the operator surface. Snapshot creation is a write by effect, so it must be planned separately with explicit authorization, visible operator intent, bounded metadata, concurrency/idempotency discipline, and fail-closed degraded states.

## Goals

- Formally open Phase 28 / Epic 107 as planning-only snapshot creation authorization work.
- Select exactly `POST /v1/events/replay/snapshots` as the future candidate surface.
- Define authorization and safety boundaries before any implementation.
- Preserve Phase 27 read-only listing/lifecycle evidence behavior.
- Require a later tests-first runtime story before any dashboard/operator invocation of `POST`.
- Require future runtime work to prove visible operator initiation, explicit authorization, no hidden/background writes, bounded returned metadata, concurrency/idempotency discipline, and fail-closed error handling.

## Out of scope for Story 107.1

- Runtime implementation, dashboard JavaScript/HTML behavior changes, browser network calls, or backend/API changes.
- New tests or test-code changes.
- Destructive lifecycle apply/prune/rollback, destructive lifecycle authorization execution, retention jobs, object-storage lifecycle jobs, archive/manifest mutation, snapshot deletion, snapshot restore, replay execution target selection, background validation jobs, cache-warming writes, generated live data, task-list/search/discovery, aggregate/session/digest, broad dashboard wiring, mutation/control surfaces beyond the single planned snapshot-create authorization affordance, dependencies, lockfiles, deployment, CI, service, MCP, or production credential changes.

## Functional requirements

- **FR236 — Phase 28 snapshot-creation scope.** The repository records Phase 28 as the product-scope gate for future authorized snapshot creation through `POST /v1/events/replay/snapshots`.
- **FR237 — Exact candidate surface.** Story 107.1 selects exactly `POST /v1/events/replay/snapshots`; it does not select lifecycle apply/prune/rollback, archive/manifest mutation, discovery/search, aggregate/session/digest, or broad dashboard wiring.
- **FR238 — Separate implementation story.** Any runtime/dashboard/operator use of `POST /v1/events/replay/snapshots` requires a later separately approved tests-first story.
- **FR239 — Visible operator authorization.** Future runtime work must require an explicit visible operator action and authorization context before invoking snapshot creation; no automatic, polling, background, load-time, cache-warming, or hidden invocation may create snapshots. Story 107.2 must reuse the existing operator/API authorization surface available at that time and must not invent new credential systems, new backend auth middleware, new service tokens, or new capability tiers; if no existing authorization source is sufficient, Story 107.2 is blocked rather than allowed to expand scope.
- **FR240 — Bounded snapshot-create result.** Future UI/output may display only bounded snapshot creation metadata such as snapshot id, sequence number, timestamp, size, request/correlation id, authority, provenance, and freshness. Snapshot internals must not become generated live data or route selectors. Story 107.2 must preserve the existing backend success contract unless a later planning gate explicitly approves API redesign: body-free `POST /v1/events/replay/snapshots` returns `201` with snapshot metadata.
- **FR241 — No lifecycle or discovery drift.** Future work must not add destructive lifecycle implementation, lifecycle apply/prune/rollback, archive/manifest mutation, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, services/MCP/dependencies/CI changes, or broad dashboard wiring.
- **FR242 — No behavior change in Story 107.1.** Story 107.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S43 — Snapshot creation fail-closed authorization.** Missing, invalid, stale, ambiguous, unauthorized, or unverifiable authorization states must not invoke snapshot creation and must render explicit unavailable/denied copy in future runtime work.
- **NFR-S44 — No hidden writes.** Future runtime work must fail tests if `POST /v1/events/replay/snapshots` can be invoked by page load, polling, timers, storage/hash/query changes, background workers, websocket/xhr side channels, cache warming, retry loops without operator action, or unrelated dashboard controls.
- **NFR-R28 — Snapshot-create concurrency discipline.** Future runtime work must define and test duplicate-submit, in-flight, retry, timeout, and concurrent-creation behavior before enabling the affordance. Minimum client contract: one visible operator action may create at most one in-flight request; duplicate submits while in-flight are blocked locally; failed/timeout/unknown outcomes render non-authoritative state and may refresh the snapshot list but must not auto-retry `POST`; any second creation after completion requires a fresh visible operator action.
- **NFR-O32 — Snapshot-create auditability.** Future displayed values must be traceable to source route, request/correlation id where available, retrieved-at/completed-at, actor/authority, provenance, and returned metadata.
- **NFR-M28 — Tests-first maintainability.** Future implementation must add boundary tests before or with runtime wiring.

## Acceptance criteria

1. Phase 28 PRD, architecture, and epics artifacts exist and define snapshot creation authorization planning scope.
2. Story 107.1 artifact records exact route selection, non-authorization statement, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 28`, opens Epic 107 / Story 107.1, preserves Epic 106 done, and records newest-first audit evidence.
4. Story 107.1 explicitly excludes implementation, destructive lifecycle, broad dashboard, discovery/search, aggregate/session/digest, archive/manifest mutation, snapshot deletion/restore, replay execution, generated live data, dependencies/lockfiles/CI/deployment/services/MCP, and production credential changes.
5. Follow-on Phase 28 epics sequence docs/status opening first, snapshot creation authorization runtime-boundary tests/implementation second, final closure third.
