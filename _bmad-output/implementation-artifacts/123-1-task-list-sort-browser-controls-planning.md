# Story 123.1 — Task List Sort Browser Controls Planning

Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only
Context snapshot: `.omx/context/story-123-1-task-list-sort-browser-controls-planning-20260630T022023Z.md`
Deep-interview handoff: `.omx/autopilot/deep-interview-story-123-1-task-list-sort-browser-controls-planning.md`
Ralplan plan: `.omx/plans/story-123-1-task-list-sort-browser-controls-planning-plan.md`
Test spec: `.omx/specs/story-123-1-task-list-sort-browser-controls-planning-test-spec.md`

## Selected family and exact future candidate

- Selected family: read-only aggregate-task-list browser/dashboard singleton sort consumption.
- Exact future candidate: visible aggregate-task-list sort controls that issue exactly `GET /v1/tasks?sort=updated_at_desc_id_asc`.
- Sort selector source: visible browser control state only.
- Approved sort vocabulary: singleton value `updated_at_desc_id_asc` only, matching Story 122.2 API-local support and deterministic `updated_at DESC, id ASC` ordering.
- Route composition policy: standalone sort route only. Status+sort, limit+sort, offset+sort, status+limit+sort, limit+offset+sort, and status+limit+offset+sort remain deferred/fail-closed.
- User-action policy: one explicit visible action per sorted read; no automatic traversal, infinite scroll, prefetch, background refresh, retry loops, timers, workers, storage, URL/hash state, cookies, hidden selectors, or row-derived selectors.

## Current brownfield state

- Story 122.2 implements the exact API-local singleton sort route with `selected_sort: "updated_at_desc_id_asc"` and rejects malformed/composed/bodyful requests.
- The aggregate-task-list dashboard currently exposes visible status, limit, offset, load, previous-offset, and next-offset controls for the exact status+limit+offset route.
- Browser sort controls are not implemented yet and remain future work for Story 123.2.

## Non-authorization statement

Story 123.1 is docs/status-only. It does not add runtime implementation, dashboard JavaScript/HTML behavior changes, browser network calls, backend/API source changes, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, broader sort vocabulary, sort composition, search/discovery, hidden selectors, automatic traversal, row traversal, URL/hash/storage/cookie behavior, replay/lifecycle mutation, production credentials, production operations, or mutation/control behavior.

## Future Story 123.2 test obligations

A future tests-first Story 123.2 must prove at minimum:

1. Visible aggregate-task-list sort control(s) and one explicit action construct exactly `/v1/tasks?sort=updated_at_desc_id_asc`.
2. The browser request is GET, bodyless, JSON-accepting, and `credentials: "omit"`.
3. Missing/malformed/hidden/duplicated/mutated/out-of-vocabulary sort control state fails closed as non-authoritative and does not issue adjacent routes.
4. Authoritative rendering requires `selected_sort: "updated_at_desc_id_asc"`, exact route metadata, freshness, authority, provenance, request/trace/correlation evidence, bounded row shape, returned_count, `has_more`, and `next_offset: null`.
5. Existing manual previous/next offset controls do not become sort-pagination controls; sorted reads render in a separate singleton-sort subtree and leave manual navigation state unchanged because sort+offset composition is not authorized.
6. Existing selector-free/status/limit/status+limit/limit+offset/manual-navigation/status+limit+offset API and dashboard tests remain green.
7. Broader sort values, status/limit/offset composition with sort, search/discovery, hidden selectors, URL/storage/cookies, timers/workers/polling/retry, automatic traversal, row traversal, services/MCP/dependencies/CI/deployment changes, credentials, and production operations remain absent.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-44-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-44-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-44-epics.md`
- `_bmad-output/implementation-artifacts/123-1-task-list-sort-browser-controls-planning.md`
- `.omx/plans/story-123-1-task-list-sort-browser-controls-planning-plan.md`
- `.omx/specs/story-123-1-task-list-sort-browser-controls-planning-test-spec.md`

## Consensus evidence

- Architect review: native agent `019f1654-2f66-7b03-9688-bc8a6b0d3df4` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-123-1-architect-review.md`.
- Critic review: native agent `019f1659-fb37-7f91-82c4-2bedec876eef` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after a scoped status-artifact repair; persisted at `.omx/artifacts/ralplan/story-123-1-critic-review.md`.

## Completion evidence

Story 123.1 completes Phase 44 / Epic 123 docs/status-only browser singleton sort-controls planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/dashboard/browser/test/backend/API/dependency/CI/deployment/service/MCP/generated-data implementation remains unstarted and authorized only for the exact future Story 123.2 boundary.

## Verification plan

- Verify Phase 44 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 44 / Epic 123, marks Story 123.1 done, and keeps Story 123.2/123.3 backlog.
- Verify `docs/feature-status.md` states browser sort controls are planned but not implemented.
- Verify `docs/api-contracts.md` reflects existing Story 122.2 singleton API-local sort support while keeping broader browser/composition surfaces deferred.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change for Story 123.1.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

2026-06-30T02:20:23Z
