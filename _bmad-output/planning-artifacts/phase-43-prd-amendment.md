# Phase 43 PRD Amendment — Task List Sort API-local Route Planning

Generated: 2026-06-29T20:58:06Z

## Scope statement

Phase 43 opens the next narrow API-local task-list branch after Phase 42 / Epic 121 closed dashboard/browser consumption of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`.

Story 122.1 is docs/status-only. It selects and constrains one future API-local runtime candidate: exact canonical `GET /v1/tasks?sort={task_sort}` with a finite approved sort vocabulary. It does not add runtime implementation, backend/API behavior changes, test-code changes, dashboard JavaScript/HTML behavior changes, browser network calls, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, automatic traversal, infinite scroll, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list API-local finite sort selection.
- **Selected exact future candidate surface:** canonical `GET /v1/tasks?sort={task_sort}` only.
- **Sort selector:** exactly one `sort` query key with one approved value: `updated_at_desc_id_asc`.
- **Sort semantics:** `updated_at` descending, then `id` ascending as a deterministic tie-breaker; this mirrors the current implicit task-list ordering and only makes that ordering explicit after a later implementation story.
- **Vocabulary policy:** singleton finite vocabulary for this phase; additional sort values such as created-time, status, title, priority, last-event, heartbeat, session, direction toggles, or arbitrary field/direction syntax remain unauthorized until separately planned.
- **Canonical query shape:** one raw ASCII query segment `sort=updated_at_desc_id_asc`. Repeated keys, percent-encoded keys/values, Unicode lookalikes, empty segments, aliases, field/direction subkeys, JSON values, additional query keys, or composition with status/limit/offset remain unauthorized.
- **Runtime boundary:** API-route-local only; no dashboard/browser sort control, no selector composition with existing task-list status/limit/offset routes, no automatic traversal, no URL/storage state, no row-derived selection, and no adjacent route wiring.

## Product goals

- Select the smallest safe sort continuation while keeping completed task-list route contracts stable.
- Make the existing task-list order explicitly requestable through one finite API-local sort token before any browser/UI exposure.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only, limit-only, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser.
- Require Architect approval followed by Critic approval before any tests-first runtime implementation story.
- Keep all non-selected sort/search/composition/traversal surfaces fail-closed.

## Non-goals

Browser/dashboard sort controls, dashboard rendering changes, search/discovery, arbitrary query language, multi-column user-defined sorting, direction toggles, status+sort, limit+sort, offset+sort, status+limit+offset+sort, cursor/page tokens, automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, URL/hash state, local/session storage, cookies, generated selectors, hidden selectors, row-derived selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, and production operations.

## Functional requirements

- **FR352 — Selected family.** Story 122.1 selects only read-only aggregate task-list API-local finite sort planning.
- **FR353 — Exact future candidate.** The only future runtime candidate selected by this phase is canonical `GET /v1/tasks?sort={task_sort}`.
- **FR354 — Finite sort vocabulary.** The only approved `task_sort` value is `updated_at_desc_id_asc`; all aliases, arbitrary field/direction strings, and additional values remain unauthorized.
- **FR355 — Deterministic order semantics.** The approved sort value means `updated_at` descending and `id` ascending as the deterministic tie-breaker, matching the current implicit task-list order.
- **FR356 — API-local request contract.** Future implementation must accept a bodyless GET with exactly one raw ASCII `sort` query key/value segment and reject request bodies, repeated/encoded/malformed keys or values, and additional query keys.
- **FR357 — Strict response metadata.** Future implementation must return selected sort metadata, route, freshness, authority, provenance, request/trace/correlation id, fixed bounded limit, row count, `has_more`, `next_offset: null`, and bounded task summary rows.
- **FR358 — Existing contract preservation.** Existing task-list API and dashboard/browser/manual-navigation routes must remain unchanged and must continue rejecting sort composition unless separately approved.
- **FR359 — Adjacent surfaces remain deferred.** Browser wiring, search/discovery, hidden selectors, auto traversal, sort composition with status/limit/offset, services/MCP/dependency/CI/deployment changes, credentials, and production operations remain unauthorized.

## Non-functional requirements

- **NFR-S64 — Fail-closed sort selector.** Missing, malformed, encoded, repeated, aliased, or out-of-vocabulary sort selectors must fail closed.
- **NFR-O46 — Deterministic stable ordering.** The selected sort must be deterministic for equal timestamps through `id` ascending tie-break behavior.
- **NFR-M37 — Regression isolation.** Future tests must prove the new API-local sort route without weakening existing read-only task-list API/dashboard/manual-navigation contracts.

## Acceptance criteria for Story 122.1

1. Phase 43 PRD, architecture, and epics artifacts exist and define exact API-local finite sort planning scope.
2. Story 122.1 artifact records selected family, exact future candidate, singleton sort vocabulary, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 43 / Epic 122, marks Story 122.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 122.2/122.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim task-list sort runtime implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 122.1.

## Follow-on story sequence

- Story 122.1: docs/status-only exact API-local finite task-list sort planning and consensus.
- Story 122.2: tests-first API-route-local implementation only if Story 122.1 consensus approves the exact boundary.
- Story 122.3: final validation closure with implementation commit and CI evidence after Story 122.2, if implemented.
