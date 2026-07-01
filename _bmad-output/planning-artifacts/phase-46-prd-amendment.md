# Phase 46 PRD Amendment — Dashboard Task-list Expansion Planning Matrix

Generated: 2026-06-30T21:44:33Z

## Scope statement

Phase 46 opens Epic 125 as docs/status-only planning for the next four aggregate-task-list/dashboard follow-up points requested by the operator. It follows Phase 45 / Epic 124, which closed API-local finite sort vocabulary runtime support for exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc`.

Phase 46 does not add runtime implementation, backend/API route behavior, test-code behavior, dashboard JavaScript/HTML behavior, browser network calls, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, hidden selectors, automatic traversal, row-derived traversal, URL/hash/local-storage/session-storage/cookie state, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, production credentials, or production operations.

## Product decisions by story

1. **Story 125.1 — Browser sort vocabulary expansion planning: selected as the next future runtime candidate.** A later implementation story may update the visible aggregate-task-list sort control to expose exactly the API-local finite vocabulary values `updated_at_desc_id_asc` and `created_at_desc_id_asc`. It must not add sort composition, search/discovery, hidden selectors, automatic traversal, backend/API changes, or non-visible selector sources.
2. **Story 125.2 — Sort composition planning: recorded as a future API-local candidate, not implemented here.** A later planning/implementation sequence may consider one canonical bodyless GET shape that composes finite `status`, bounded `limit`, bounded `offset`, and finite `sort` values. It remains API-local first; browser composition remains separate future work.
3. **Story 125.3 — Task-list search/discovery planning: high-risk, not selected for runtime in Phase 46.** Search/discovery needs a separate product/architecture contract before any API/browser work because it risks arbitrary query grammar, hidden selectors, generated live data, privacy/authority ambiguity, and automatic traversal drift.
4. **Story 125.4 — Broad dashboard wiring cleanup planning: high-risk, not selected for broad runtime cleanup in Phase 46.** Cleanup must start with inventory and test guards only; broad dashboard behavior rewiring remains unauthorized.

## Product goals

- Convert the four requested follow-up candidates into explicit planning/status artifacts.
- Preserve the narrow task-list contract progression already closed across Phases 30-45.
- Select only the smallest low-risk browser follow-up for later implementation: visible finite sort vocabulary expansion backed by already implemented API vocabulary.
- Keep higher-risk composition/search/wiring work explicitly separated and fail-closed.

## Non-goals

Runtime implementation, backend/API changes, dashboard/browser behavior changes, test-code changes, dependencies, lockfiles, services, MCP changes, CI/deployment changes, generated live data, automatic traversal, infinite scroll, polling/timers/workers/retry loops, hidden selectors, row-driven route selection, URL/hash/storage/cookie-derived selectors, free-text search runtime, arbitrary discovery runtime, broad dashboard rewiring, mutation/control behavior, credentials, and production operations.

## Functional requirements

- **FR379 — Planning-only phase.** Phase 46/Epic 125 may change only planning/status/contract documentation and Autopilot artifacts.
- **FR380 — Story order.** Phase 46 covers the four requested points in order: browser sort vocabulary, sort composition, search/discovery, broad dashboard cleanup.
- **FR381 — Browser vocabulary candidate.** Story 125.1 selects a future visible dashboard candidate that may expose exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc` as finite sort choices.
- **FR382 — Browser selector source.** Future Story 125.1 implementation must use visible control state and an explicit user action only; query/hash/storage/cookie/row-derived/hidden selector sources remain unauthorized.
- **FR383 — No browser composition.** Future browser vocabulary implementation must issue standalone sort reads only; it must not compose sort with status, limit, offset, search, cursor, page, or arbitrary query selectors.
- **FR384 — API-local composition candidate.** Story 125.2 records only a future API-local candidate for `status` + `limit` + `offset` + `sort` composition with finite/bounded domains and a single canonical query order.
- **FR385 — Search/discovery deferral.** Story 125.3 must not authorize runtime search/discovery and must record required future decision inputs.
- **FR386 — Broad wiring deferral.** Story 125.4 must not authorize broad dashboard runtime cleanup and must require future inventory/test-guard work before any rewiring.
- **FR387 — Contract preservation.** Existing selector-free/status/limit/status+limit/limit+offset/manual navigation/status+limit+offset/sort API/browser contracts remain unchanged.

## Acceptance criteria

1. Phase 46 PRD, architecture, and epics artifacts exist and cover all four requested planning stories.
2. Story artifacts 125.1 through 125.4 exist, are docs/status-only, and record non-authorization boundaries.
3. Sprint status opens Phase 46 / Epic 125, marks all four planning stories done only after consensus evidence, and does not claim runtime implementation.
4. `docs/feature-status.md` is refreshed as derivative status and points to Phase 46 planning artifacts.
5. `docs/api-contracts.md` reflects the already implemented Phase 45 two-token API-local sort vocabulary while keeping browser vocabulary expansion, sort composition, search/discovery, and broad dashboard wiring deferred unless separately implemented.
6. No runtime/source/test/backend/API/dashboard JS/HTML/dependency/lockfile/CI/deployment/service/MCP/generated-data files change.
