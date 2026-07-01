# Phase 46 Architecture Amendment — Dashboard Task-list Expansion Planning Boundaries

Generated: 2026-06-30T21:44:33Z

## Canonical decision

This architecture amendment is the canonical Phase 46 contract source. PRD, epics, story, sprint-status, feature-status, and API-contract entries are derivative summaries and should point back here if they conflict.

Phase 46 is planning/status-only. It covers four requested aggregate-task-list/dashboard follow-up points one by one:

1. **Browser sort vocabulary expansion — selected future candidate.** Future dashboard implementation may expose exactly the already implemented API-local finite sort vocabulary values `updated_at_desc_id_asc` and `created_at_desc_id_asc` through visible aggregate-task-list sort controls.
2. **Sort composition — API-local future candidate only.** A later API-local planning/implementation sequence may consider one canonical query shape that composes approved `status`, `limit`, `offset`, and `sort` selectors. Browser composition remains separate future work.
3. **Task-list search/discovery — high-risk deferred.** No runtime search/discovery surface is selected in Phase 46.
4. **Broad dashboard wiring cleanup — high-risk deferred.** No broad runtime cleanup is selected in Phase 46.

## Brownfield context

Closed task-list surfaces include selector-free reads, status-only, limit-only, status+limit API/browser, limit+offset API/browser, manual previous/next controls, status+limit+offset API/browser, API-local sort, browser singleton sort controls, and API-local finite sort vocabulary. Phase 45 implemented standalone `GET /v1/tasks?sort={task_sort}` for exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc`; Phase 44 dashboard controls still expose only the singleton `updated_at_desc_id_asc` value.

## Story 125.1 future browser constraints

A later implementation story for browser sort vocabulary expansion must:

1. Use a visible aggregate-task-list sort control with exactly two values: `updated_at_desc_id_asc` and `created_at_desc_id_asc`.
2. Use one explicit operator action per sorted read.
3. Issue only standalone sort reads, either `/v1/tasks?sort=updated_at_desc_id_asc` or `/v1/tasks?sort=created_at_desc_id_asc`.
4. Validate `route: "GET /v1/tasks?sort={task_sort}"`, `selected_sort`, bounded row/count/freshness/authority/provenance/request/trace/correlation metadata, and `next_offset: null`.
5. Render sorted results in a sort-specific aggregate-task-list subtree without mutating status/limit/offset/manual-navigation state.
6. Fail closed on unknown sort values, response/selector mismatch, stale authoritative sort state, network errors, malformed rows, missing metadata, or non-JSON responses.
7. Avoid backend/API source changes, hidden selectors, URL/hash/storage/cookie state, row-derived traversal, search/discovery, automatic traversal, services/MCP/dependencies/CI/deployment changes, credentials, and production operations.

## Story 125.2 future API-local composition constraints

A later API-local composition sequence may be considered only if it remains separate from Story 125.1 browser vocabulary work and defines:

- one canonical raw ASCII query order;
- finite lifecycle status domain;
- bounded integer limit domain;
- bounded non-negative offset domain;
- finite sort vocabulary values `updated_at_desc_id_asc` and `created_at_desc_id_asc` only;
- deterministic ordering after filtering and before offset windowing;
- fail-closed rejection of reversed order, repeated/encoded/malformed/extra keys, GET bodies, search/cursor/page/hidden selectors, and arbitrary grammar.

Phase 46 does not choose implementation details beyond those planning constraints.

## Story 125.3 search/discovery risk constraints

Before any future search/discovery runtime may be selected, a separate phase must define exact searchable fields, exact query grammar, minimum/maximum lengths, encoding policy, authority/freshness/provenance semantics, privacy redaction, sort/pagination interaction, failure modes, and adversarial tests. Until then, search/discovery remains unavailable.

## Story 125.4 broad dashboard cleanup constraints

Before any future broad dashboard cleanup implementation, a separate phase must inventory existing dashboard modules/contracts, classify dead vs live wiring, define behavior-preserving tests, and split cleanup into narrow file-level changes. Phase 46 does not authorize broad rewiring.

## Deferred surfaces

Backend/API changes, dashboard JavaScript/HTML behavior changes, test-code changes, sort-composition runtime, browser composition, search/discovery runtime, arbitrary query grammar, hidden selectors, row-derived traversal, automatic traversal, infinite scroll, URL/hash/local-storage/session-storage/cookie state, cache warming, polling/timers/workers/retry loops, broad dashboard rewiring, generated live data, replay/lifecycle mutation, mutation/control behavior, services/MCP/dependencies/lockfiles/CI/deployment changes, credentials, and production operations.

## Architecture acceptance criteria

1. Phase 46 artifacts cover all four requested planning points and explicitly separate low-risk browser vocabulary from higher-risk composition/search/wiring work.
2. Story 125.1 is the only future runtime candidate selected in Phase 46.
3. Stories 125.2, 125.3, and 125.4 remain planning/risk-classification artifacts and do not authorize implementation.
4. Existing closed task-list API/browser contracts remain unchanged.
5. Docs/status verification proves no runtime/source/test/dependency/CI files changed.
