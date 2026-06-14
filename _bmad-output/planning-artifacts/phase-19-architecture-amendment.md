# Phase 19 Architecture Amendment — Read-Only Web Dashboard

## Decision Summary

Phase 19 may proceed from product/UX planning into architecture planning for a **read-only web dashboard**. The dashboard is an operator visibility surface over existing oh-my-bmad state; it is not a control plane. This architecture amendment authorizes only design decisions and future implementation constraints. It is a **docs/status-only** planning artifact and does **not authorize frontend code**, backend route implementation, API schema changes, dependency changes, deployment changes, runtime behavior changes, or mutation controls.

The architectural default is **existing safe read surfaces first**. Any new read contract requires a separate future story and review proving it is read-only by effect, not merely by HTTP method.

## Inputs

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md`
- Existing registry-api, replay, health, trace, and metrics read surfaces in source.
- Phase 16 and Phase 17 architecture constraints for archive-aware task history and destructive lifecycle readiness.

## Existing Safe Read Surfaces

The first dashboard implementation slice should consume only these existing surfaces unless a later architecture amendment approves a new read contract:

| Dashboard need | Existing source | Notes |
|---|---|---|
| Task detail | `GET /v1/tasks/{task_id}` | Reads the materialized registry state through registry-api's read-only state engine. |
| Task events | `GET /v1/tasks/{task_id}/events` | Ordered typed event stream with pagination/filtering. |
| Task transitions | `GET /v1/tasks/{task_id}/transitions` | State-transition-only projection for timeline summaries. |
| Trace correlation | `GET /v1/trace/{trace_id}` | Presentation view over event rows; not canonical JSONL replay. |
| Logs digest | `GET /v1/tasks/{task_id}/logs/digest` | Existing read route, but it may call an LLM adapter and should be treated as optional/non-core for dashboard MVP because it can create external-service dependency and latency. |
| Task history / archive-aware history | `GET /v1/tasks/{task_id}/history` | Existing replay route; may include archive manifest validation paths. |
| Replay state | `GET /v1/events/replay` | Existing read route for point-in-time replay state. |
| Replay validation | `GET /v1/events/replay/validate` | Existing read route that compares replayed vs live state. |
| Snapshot listing | `GET /v1/events/replay/snapshots` | Read-only listing only. `POST /v1/events/replay/snapshots` is not dashboard-safe for Phase 19. |
| Health / stale signals | `GET /v1/health` | Existing degraded/unknown/queue-depth status surface. |
| Metrics | Existing metrics-subscriber exposition | Dashboard may link or display derived read-only metrics only if accessed through an approved read path in a later story. |
| BMAD artifact context | `_bmad-output/**` planning/status artifacts | Static documentation/status links only; no file mutation from dashboard. |

## Architecture Decisions

### Decision 1 — Dashboard is a separate read-only client

A future implementation may add a browser UI, but the UI must be a client of read-only contracts. It must not become a registry writer, event-log writer, lifecycle executor, approval command surface, or credential entry surface.

Consequences:

- No dashboard component may import or call registry/event-log write helpers.
- No dashboard route, action, or background refresh may dispatch a job, mutate cache/state, write audit rows, or enqueue lifecycle work.
- Dashboard state is ephemeral client/render state only unless a later architecture amendment explicitly approves a read-only server-side projection cache with no writes caused by reads.

### Decision 2 — Existing contracts first; new read contracts are exceptional

The initial implementation stories must compose existing registry-api and replay read endpoints before proposing backend expansion. If the UX requires a task list or aggregate overview not currently available as a safe read, the implementation story must choose one of these options:

1. Defer that panel and show an explicit unavailable/needs-contract state.
2. Use a narrowly scoped new `GET` contract approved by a later architecture amendment.
3. Use a static BMAD/status artifact when the data is planning metadata rather than runtime state.

Any new `GET` route must prove no hidden writes, no cache-warming writes/read-side effects, no background-job dispatch, and no mutation route reachability.

### Decision 3 — Effect-based read-only enforcement

Read-only means **no side effects**, not only no mutating HTTP methods. Future implementation must prove:

- dashboard data comes from existing safe read surfaces or separately approved future read contract;
- only allowlisted read routes/read methods are reachable;
- no mutation routes are reachable from the dashboard;
- no background-job dispatch can be triggered by dashboard reads;
- no hidden writes behind read endpoints are present;
- no cache-warming writes/read-side effects occur;
- approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, credential entry, scheduled job, and production operation controls are absent.

### Decision 4 — Lifecycle visibility stays informational

Replay/lifecycle panels may display existing archive manifest, replay validation, task-history, and dry-run/readiness evidence only when those values are available through safe reads. The dashboard must not expose lifecycle apply/prune controls and must not convert a dry-run/readiness concept into an executable operation.

Phase 18 and Phase 17 destructive-lifecycle gates remain authoritative. This amendment does not approve destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, object storage lifecycle jobs, scheduled retention workers, or credentialed production operations.

### Decision 5 — Authentication and authorization reuse existing registry-api controls

Future dashboard implementation must reuse existing registry-api auth/tier enforcement for API access. It must not introduce OAuth, multi-user auth, public sharing, external hosting, credential storage, or token minting in Phase 19 implementation stories unless a separate PRD/architecture amendment explicitly opens that scope.

The dashboard MVP is for the single operator/developer deployment profile.

### Decision 6 — Error, freshness, and provenance are first-class contracts

Every dashboard panel must identify source category and confidence:

- registry projection;
- event log / event row presentation view;
- replay/task-history response;
- health/metrics projection;
- BMAD artifact;
- unavailable / stale / partial / invalid.

Errors must be fail-safe: stale, partial, invalid archive config, backend unavailable, permission/configuration failure, and parse/read errors must not render as healthy state and must not imply mutation happened.

## Forbidden Surfaces

Future Phase 19 implementation stories must not add or expose:

- approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credential entry, or production operation controls;
- frontend forms or buttons that trigger state transitions;
- `POST`, `PUT`, `PATCH`, or `DELETE` dashboard calls, except static asset delivery outside API control semantics;
- calls to registry/event-log writers, idempotency-cache writes, lifecycle apply/prune helpers, snapshot creation, archive mutation, or job dispatch;
- cache-warming writes/read-side effects hidden behind a read;
- new dependencies, deployment surfaces, external hosting, OAuth, public share/export features, or multi-user access without a later amendment.

## Allowed Future Implementation Write Set

This amendment is planning-only. A later implementation story may propose a narrow write set, but it must be reviewed separately. Expected future candidates are:

- dashboard frontend files for static/read-only UI;
- tests for route/method allowlists and no-mutation behavior;
- optional registry-api read-only `GET` route(s) only if existing surfaces cannot satisfy the approved UX;
- documentation updates for operator use.

No implementation write set is approved by this architecture amendment alone.

## Verification Strategy for Future Stories

Future dashboard implementation stories must include:

1. Route/method allowlist tests proving only approved `GET` reads are reachable.
2. Static grep/import checks proving no registry/event-log writer, lifecycle apply/prune, snapshot creation, background job dispatch, or cache-warming write path is imported or called by dashboard code.
3. Tests for empty/loading/error/stale states and source-provenance labels.
4. Tests proving invalid archive/replay/health responses render bounded uncertainty, not healthy state.
5. Security checks proving no credential entry, public sharing, OAuth, multi-user auth, or token minting appears in Phase 19 implementation.
6. Regression checks that existing registry-api/replay behavior remains unchanged.
7. Independent code-reviewer and architect review with final recommendation `APPROVE` and architectural status `CLEAR` before completion.

## Handoff to Epics and Stories

The next BMAD artifact should be `_bmad-output/planning-artifacts/phase-19-epics.md`. It should decompose implementation into small read-only slices, for example:

1. Dashboard shell and read-only boundary tests.
2. Task detail/timeline using existing task/event/trace reads.
3. Replay/lifecycle visibility using existing replay/task-history/validation reads.
4. Health/provenance/stale-state panels.
5. Final no-mutation and accessibility/responsiveness validation.

Stories must keep implementation behind this architecture amendment and the Phase 19 UX spec, and must return to architecture planning before adding any non-existing read contract.
