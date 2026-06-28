# Phase 37 PRD Amendment — Task Status + Limit Browser Consumption Planning

Generated: 2026-06-28T17:26:00Z

## Scope statement

Phase 37 opens the next narrow dashboard branch after Phase 36 / Epic 115 closed the backend/API boundary for exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.

Story 116.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, new selector vocabularies, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list browser consumption for the already-approved status+limit composition route.
- **Selected exact future candidate surface:** dashboard aggregate task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- **Allowed status selector domain:** `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- **Allowed limit selector domain:** one integer task-list limit from 1 through 50 inclusive.
- **Selector source:** visible aggregate-task-list panel controls only. No URL hash/query-state persistence, cookies, local/session storage, generated selectors, hidden inputs, row-derived selectors, background jobs, or inferred selector sources.
- **Canonical query order:** status first, then limit. Reversed order or additional query keys remain unauthorized.

## Product goals

- Select the smallest browser/runtime consumption step for the completed backend status+limit route without entering pagination traversal, free-text search, arbitrary discovery, replay execution, lifecycle mutation, or broad dashboard wiring.
- Preserve the existing selector-free aggregate task list route and the route-local API contracts from Phases 30, 34, 35, and 36.
- Require a later tests-first Story 116.2 before any dashboard/browser/runtime contract work.
- Keep all non-selected surfaces fail-closed.

## Non-goals

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, pagination state, sorting controls, new filter keys, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating browser/dashboard status+limit consumption as already implemented.
- Inferring status+limit+anything, URL hash/query-state persistence, local/session storage, saved searches, background refresh, cache warming, or automatic dashboard-wide mode switching from this route-selection artifact.

## Functional requirements

- **FR309 — Selected family.** Story 116.1 selects only read-only aggregate task-list browser consumption for the already-approved status+limit composition route.
- **FR310 — Exact future candidate.** Story 116.1 selects dashboard aggregate-task-list panel consumption/rendering of exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the only future runtime candidate in this phase.
- **FR311 — Visible selector source.** Future Story 116.2 may use only visible aggregate-task-list panel controls as selector input; no hidden selector source or generated selector source is authorized.
- **FR312 — Selector domains.** Future status values are limited to the existing finite lifecycle vocabulary; future limit values are limited to one integer from 1 through 50 inclusive.
- **FR313 — Canonical request shape.** Future browser fetches must be GET-only, bodyless, credentials-omitted, canonical status-then-limit query order, and must not include any extra/repeated query key.
- **FR314 — Response authority.** Future rendering must validate and expose source route, selected status, selected limit, retrieved_at, freshness_state, authority_state, provenance, correlation/request/trace id where available, returned_count, has_more, and degraded-state metadata before marking any row state authoritative.
- **FR315 — Adjacent surfaces remain deferred.** Offset/cursor/page traversal, sort controls, free-text search, arbitrary filters beyond the exact two selectors, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.

## Non-functional requirements

- **NFR-S59 — Fail-closed selector consumption.** Missing/invalid controls, malformed selector values, reversed query construction, rejected/failed responses, stale/ambiguous freshness, unexpected response keys, over-limit rows, and mismatched selected-status/selected-limit metadata must render non-authoritative fail-closed copy.
- **NFR-O41 — Provenance continuity.** Status+limit browser consumption must preserve the Phase 36 response metadata contract and show enough route/selector/provenance evidence to audit exactly which bounded read produced the displayed rows.
- **NFR-M32 — No broad dashboard mode switch.** Future work may not silently replace every aggregate task-list read mode, add global search/discovery, add dashboard-wide route switching, or use returned rows as automatic traversal triggers.

## Acceptance criteria for Story 116.1

1. Phase 37 PRD, architecture, and epics artifacts exist and define task status+limit browser-consumption planning scope.
2. Story 116.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 37 / Epic 116, marks Story 116.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 116.2/116.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim browser/dashboard status+limit consumption implementation.
5. Story 116.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, pagination traversal, sorting controls, free-text search, arbitrary query language, hidden selectors, row-driven traversal, replay execution target selection, lifecycle mutation planning, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

## Follow-on story sequence

- Story 116.1: docs/status-only route-selection and planning gate.
- Story 116.2: future tests-first browser/runtime boundary for exact dashboard aggregate-task-list status+limit consumption only, after Story 116.1 consensus.
- Story 116.3: final validation closure after Story 116.2 review, QA, push, and remote CI evidence.
