# Deep-Interview Handoff — Story 127.1 Search/Discovery Product and Architecture Contract

Generated: 2026-07-02T12:52:50Z
Context snapshot: `.omx/context/story-127-1-search-discovery-contract-20260702T125153Z.md`

## Gate status

Status: COMPLETE by repo-evidence closure audit; no user question required because the activation prompt, Phase 48 story acceptance criteria, and current docs already define the intent, non-goals, decision boundaries, and stop condition.

## Clarified intent

Create a planning/contract-only Story 127.1 artifact set that decides the safe future search/discovery contract before runtime work. The contract must define exact searchable fields, query grammar/operators, length/encoding rules, sort/pagination composition, response metadata, privacy/redaction, fail-closed states, selector provenance, and traversal boundaries.

## Scope

In scope:
- Amend phase-scoped PRD/architecture/story planning artifacts and derivative status/API-contract docs if needed.
- Convert prior deferred search/discovery risk language into a concrete future implementation contract.
- Preserve the current Phase 47 browser/API selector-family baseline.
- Record sequential Architect APPROVE/CLEAR then Critic APPROVE/CLEAR for this planning story.

Out of scope:
- Runtime API route implementation, dashboard behavior changes, test-code changes for runtime behavior, dependencies, lockfiles, services/MCP changes, CI/deployment changes, credentials, production operations, mutation/control behavior, automatic traversal, background prefetch, hidden selectors, URL/hash/storage/cookie selectors, and row-derived selectors.

## Brownfield facts

- `docs/api-contracts.md` currently permits only exact `/v1/tasks` aggregate-list query shapes through Story 125.2/126.2 and explicitly rejects free-text search, cursor/page traversal beyond exact limit+offset, hidden selectors, traversal, and broad dashboard wiring.
- `docs/feature-status.md` records Phase 47 as shipped/green and search/discovery runtime, hidden selectors, automatic traversal, row-driven traversal, broad rewiring, backend/API changes, dependencies, credentials, deployment, and production operations as deferred/fail-closed.
- `_bmad-output/planning-artifacts/phase-46-prd-amendment.md` identifies search/discovery as high risk and requiring a separate product/architecture contract before runtime.
- `_bmad-output/planning-artifacts/phase-47-prd-amendment.md` and architecture amendment prohibit search/discovery, arbitrary query building, URL/hash/storage/cookie state, hidden selectors, automatic traversal/infinite scroll/background refresh, row-driven traversal, and broad cleanup.
- `dashboard/static/aggregate-task-list.js` reads visible status/limit/offset/sort controls only, rejects hidden controls, builds `/v1/tasks?status=...&limit=...&offset=...&sort=...`, uses `credentials: "omit"`, validates selected metadata/freshness/authority/provenance/correlation/pagination, and keeps manual previous/next explicit.
- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py` contains forbidden-marker coverage for imports/workers/storage/cookies/URL state/timers/background/retry/search/discovery/cursor/page/routes and visible-control allowlist coverage.

## Planning directives for Ralplan

- Define a bodyless GET search/discovery candidate for later Story 127.2; Story 127.1 itself remains docs/status-only.
- Searchable fields should be allowlisted from non-secret aggregate task summary fields already visible or contract-safe; exclude generated text, event/log payloads, worktree/resource paths, credentials, hidden/internal ids beyond task identity fields, and row-derived selector behavior.
- Grammar must be intentionally small: finite field key, operator vocabulary, bounded raw query text/bytes, canonical query order, no repeated/encoded keys, no arbitrary boolean DSL, no GET body.
- Browser provenance must remain visible-control-only and explicit user action only.
- Traversal must remain disabled until Story 127.4 and, if enabled later, must be explicit, bounded, cancellable, observable, rate-limited, and stale-state-invalidating.

## Interview-complete rationale

The request names the story, order, acceptance criteria, and production-readiness boundaries; repo artifacts provide the required non-goals and current code facts. Remaining choices are contract design choices that can be resolved in Ralplan with Architect/Critic consensus rather than by user preference.
