# Phase 45 PRD Amendment — Task List Sort Vocabulary API-local Planning

Generated: 2026-06-30T19:35:36Z

## Scope statement

Phase 45 opens the next narrow task-list sort branch after Phase 44 / Epic 123 closed visible dashboard controls for the existing singleton sort route `GET /v1/tasks?sort=updated_at_desc_id_asc`.

Story 124.1 is docs/status-only. It selects and constrains one future API-local runtime candidate: finite task-list sort vocabulary expansion for the already existing parameterized route `GET /v1/tasks?sort={task_sort}`, preserving the existing `updated_at_desc_id_asc` token and adding exactly one new token, `created_at_desc_id_asc`. It does not add runtime implementation, backend/API behavior changes, test-code changes, dashboard JavaScript/HTML behavior changes, browser network calls, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sort composition, browser sort vocabulary changes, automatic traversal, infinite scroll, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

Canonical contract source: `phase-45-architecture-amendment.md`; this PRD section is a product summary of that boundary.

- **Selected family:** read-only aggregate task-list API-local finite sort vocabulary expansion.
- **Selected exact future candidate surface:** canonical `GET /v1/tasks?sort={task_sort}` with a finite two-token vocabulary.
- **Existing approved token:** `updated_at_desc_id_asc`, preserving deterministic `updated_at DESC, id ASC` ordering.
- **New selected token:** `created_at_desc_id_asc`, meaning deterministic `created_at DESC, id ASC` ordering.
- **Selector source:** exactly one raw ASCII `sort` query key with exactly one approved value.
- **Vocabulary policy for this phase:** only `updated_at_desc_id_asc` and `created_at_desc_id_asc` are approved. Status, title, priority, last-event, heartbeat, session, direction toggles, aliases, and arbitrary field/direction syntax remain unauthorized.
- **Canonical query shape:** one raw ASCII query segment, either `sort=updated_at_desc_id_asc` or `sort=created_at_desc_id_asc`. Repeated keys, percent-encoded keys/values, Unicode lookalikes, empty segments, aliases, field/direction subkeys, JSON values, additional query keys, or composition with status/limit/offset remain unauthorized.
- **Runtime boundary:** API-route-local only; no dashboard/browser vocabulary change, no selector composition with existing task-list status/limit/offset routes, no automatic traversal, no URL/storage state, no row-derived selection, and no adjacent route wiring.

## Product goals

- Continue the product/PRD operator-dashboard task-list read surface in the smallest safe increment after singleton sort exposure.
- Add one low-risk, visible-row-field sort option (`created_at`) before any sort composition or browser vocabulary expansion.
- Preserve all completed task-list contracts: selector-free, status-only, limit-only, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser, API-local singleton sort, and browser singleton sort controls.
- Require Architect approval followed by Critic approval before any tests-first runtime implementation story.
- Keep all non-selected sort/search/composition/traversal surfaces fail-closed.

## Non-goals

Runtime implementation, backend/API source changes, test-code changes, browser/dashboard control changes, sort composition with status/limit/offset, browser vocabulary expansion, additional sort values beyond `created_at_desc_id_asc`, arbitrary sort grammar, aliases, direction toggles, title/status/priority/last-event/session/heartbeat sorts, free-text search/discovery, cursor/page traversal, automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, URL/hash state, local/session storage, cookies, generated selectors, hidden selectors, row-derived selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations.

## Functional requirements

- **FR370 — Selected family.** Story 124.1 selects only read-only aggregate task-list API-local finite sort vocabulary planning.
- **FR371 — Exact future route.** The only future runtime route selected by this phase is canonical `GET /v1/tasks?sort={task_sort}`.
- **FR372 — Finite vocabulary.** Future implementation may accept only `updated_at_desc_id_asc` and `created_at_desc_id_asc`; all aliases, arbitrary field/direction strings, and additional values remain unauthorized.
- **FR373 — Created-time semantics.** `created_at_desc_id_asc` means `created_at` descending and `id` ascending as the deterministic tie-breaker.
- **FR374 — Existing sort preservation.** `updated_at_desc_id_asc` must retain its current `updated_at DESC, id ASC` behavior and response metadata.
- **FR375 — API-local request contract.** Future implementation must accept a bodyless GET with exactly one raw ASCII `sort` query key/value segment and reject request bodies, repeated/encoded/malformed keys or values, and additional query keys.
- **FR376 — Strict response metadata.** Future implementation must return selected sort metadata, route, freshness, authority, provenance, request/trace/correlation id, fixed bounded limit, row count, `has_more`, `next_offset: null`, and bounded task summary rows.
- **FR377 — Existing contract preservation.** Existing task-list API and dashboard/browser/manual-navigation/singleton-sort-control routes must remain unchanged and must continue rejecting sort composition unless separately approved.
- **FR378 — Adjacent surfaces remain deferred.** Browser vocabulary changes, search/discovery, hidden selectors, auto traversal, sort composition with status/limit/offset, services/MCP/dependency/CI/deployment changes, credentials, and production operations remain unauthorized.

## Non-functional requirements

- **NFR-S66 — Fail-closed finite vocabulary.** Missing, malformed, encoded, repeated, aliased, or out-of-vocabulary sort selectors must fail closed.
- **NFR-O48 — Deterministic stable ordering.** Both approved sort tokens must be deterministic for equal primary sort values through `id ASC` tie-break behavior.
- **NFR-M39 — Regression isolation.** Future tests must prove the finite vocabulary expansion without weakening existing read-only task-list API/dashboard/manual-navigation/singleton-sort-control contracts.

## Acceptance criteria for Story 124.1

1. Phase 45 PRD, architecture, and epics artifacts exist and define exact API-local finite sort vocabulary planning scope.
2. Story 124.1 artifact records selected family, exact future route, two-token vocabulary, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opens Phase 45 / Epic 124, marks Story 124.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and keeps Story 124.2/124.3 as future work.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim created-time sort runtime implementation.
5. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 124.1.

## Follow-on story sequence

- Story 124.1: docs/status-only exact API-local finite task-list sort vocabulary planning and consensus.
- Story 124.2: tests-first API-route-local implementation only if Story 124.1 consensus approves the exact boundary.
- Story 124.3: final validation closure with implementation commit and CI evidence after Story 124.2, if implemented.
