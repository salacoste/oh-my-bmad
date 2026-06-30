# Story 122.1 — Task List Sort API-local Planning

Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list API-local finite sort selection.
- Exact future candidate: canonical `GET /v1/tasks?sort={task_sort}` only.
- Sort selector source: exactly one raw ASCII `sort` query key.
- Approved sort vocabulary: singleton value `updated_at_desc_id_asc` only.
- Sort semantics: `updated_at` descending, then `id` ascending deterministic tie-breaker, matching the current implicit task-list ordering.
- Canonical shape: exactly `sort=updated_at_desc_id_asc`; all encoded, repeated, aliased, nested, JSON, field/direction, empty, Unicode, or extra-key spellings fail closed.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser task-list routes are implemented and closed. Sort query selectors are currently explicitly rejected. Exact API-local finite sort remains unimplemented until Story 122.2.

## Non-authorization statement

Story 122.1 is docs/status-only. It does not add runtime implementation, backend/API behavior changes, test-code changes, browser network calls, dashboard JavaScript/HTML behavior changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, browser/dashboard sort controls, free-text search, arbitrary query language, automatic traversal, infinite scroll, hidden selectors, row-derived selectors, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 122.2 test obligations

A future tests-first Story 122.2 must prove at minimum:

1. `GET /v1/tasks?sort=updated_at_desc_id_asc` succeeds and returns selected sort metadata with deterministic `updated_at DESC, id ASC` rows.
2. Only `updated_at_desc_id_asc` is accepted; aliases such as `updated_at`, `updated_desc`, `created_at_desc`, `status_asc`, `title_asc`, field/direction pairs, JSON, nested params, encoded values, Unicode lookalikes, empty values, repeated keys, and extra keys fail closed.
3. GET request bodies are rejected for the sort route.
4. Existing selector-free, status-only, limit-only, status+limit, limit+offset, status+limit+offset, and dashboard/manual-navigation tests remain green and continue rejecting sort composition.
5. The sort route emits bounded task summary rows only, fixed first-page limit behavior, `has_more` semantics, `next_offset: null`, freshness, authority, provenance, request/trace/correlation metadata, and no adjacent traversal hints beyond the existing row shape.
6. No browser/dashboard controls, automatic traversal, search/discovery, hidden selectors, storage/URL/cookie state, replay/lifecycle mutation, services/MCP/dependencies/CI/deployment changes, credentials, or production operations are added.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-43-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-43-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-43-epics.md`
- `_bmad-output/implementation-artifacts/122-1-task-list-sort-api-local-planning.md`

## Consensus evidence

- Architect review: native agent `019f1539-d5e4-78f3-99b2-613df6e5508b` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-122-1-architect-review.md`.
- Critic review: native agent `019f153b-1595-7ee0-82ac-7a5ffc5a03c3` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after the Architect gate; persisted at `.omx/artifacts/ralplan/story-122-1-critic-review.md`.

## Completion evidence

Story 122.1 completes Phase 43 / Epic 122 docs/status-only finite task-list sort API-local planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/API/test/browser/dashboard/dependency/CI/deployment/service/MCP/generated-data implementation remains unstarted and authorized only for the exact future Story 122.2 boundary.

## Verification plan

- Verify Phase 43 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 43 / Epic 122 without marking implementation complete.
- Verify `docs/feature-status.md` states sort is selected/opened as a planning candidate, not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change for Story 122.1.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

2026-06-29T21:16:06Z
