# Story 124.1 — Task List Sort Vocabulary Planning

Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only
Context snapshot: `.omx/context/open-phase-45-epic-124-planning-from-the-product-20260630T192954Z.md`
Deep-interview handoff: `.omx/interviews/phase-45-epic-124-task-list-sort-vocabulary-planning-deep-interview-complete.md`
Ralplan plan: `.omx/plans/story-124-1-task-list-sort-vocabulary-planning-plan.md`
Test spec: `.omx/specs/story-124-1-task-list-sort-vocabulary-planning-test-spec.md`

## Selected family and exact future candidate

Canonical contract source: `../planning-artifacts/phase-45-architecture-amendment.md`; this story records planning, consensus, and status completion evidence against that source.

- Selected family: read-only aggregate task-list API-local finite sort vocabulary expansion.
- Exact future candidate: canonical `GET /v1/tasks?sort={task_sort}` only.
- Existing approved sort token: `updated_at_desc_id_asc`, preserving `updated_at DESC, id ASC`.
- New selected sort token: `created_at_desc_id_asc`, meaning `created_at DESC, id ASC`.
- Sort selector source: exactly one raw ASCII `sort` query key.
- Approved vocabulary for the future implementation story: exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc`.
- Canonical shape: exactly one raw query segment, either `sort=updated_at_desc_id_asc` or `sort=created_at_desc_id_asc`; all encoded, repeated, aliased, nested, JSON, field/direction, empty, Unicode, or extra-key spellings fail closed.
- Current brownfield state: selector-free, status-only, limit-only, status+limit API/browser, limit+offset API/browser/manual-navigation, status+limit+offset API/browser, API-local singleton sort, and browser singleton sort controls are implemented and closed. Broader sort vocabulary remains unimplemented until Story 124.2.

## Non-authorization statement

Story 124.1 is docs/status-only. It does not add runtime implementation, backend/API behavior changes, test-code changes, browser network calls, dashboard JavaScript/HTML behavior changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, browser/dashboard sort vocabulary changes, sort composition, free-text search, arbitrary query language, automatic traversal, infinite scroll, hidden selectors, row-derived selectors, URL/hash state, local/session storage, cookies, timers/workers/retry/polling side channels, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 124.2 test obligations

A future tests-first Story 124.2 must prove at minimum:

1. `GET /v1/tasks?sort=updated_at_desc_id_asc` still succeeds and returns selected sort metadata with deterministic `updated_at DESC, id ASC` rows.
2. `GET /v1/tasks?sort=created_at_desc_id_asc` succeeds and returns selected sort metadata with deterministic `created_at DESC, id ASC` rows.
3. Only the two approved values are accepted; aliases such as `updated_at`, `created_at_desc`, `created_at`, `status_asc`, `title_asc`, `priority_desc`, field/direction pairs, JSON, nested params, encoded values, Unicode lookalikes, empty values, repeated keys, and extra keys fail closed.
4. GET request bodies are rejected for both sort values.
5. Sort remains mutually exclusive with status, limit, offset, cursor, page, search, and arbitrary query selectors.
6. Existing selector-free, status-only, limit-only, status+limit, limit+offset, manual-navigation, status+limit+offset, API-local singleton sort, and dashboard singleton sort-control tests remain green.
7. The sort route emits bounded task summary rows only, fixed first-page limit behavior, `has_more` semantics, `next_offset: null`, freshness, authority, provenance, request/trace/correlation metadata, and no adjacent traversal hints beyond the existing row shape.
8. No browser/dashboard controls, automatic traversal, search/discovery, hidden selectors, storage/URL/cookie state, replay/lifecycle mutation, services/MCP/dependencies/CI/deployment changes, credentials, or production operations are added.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-45-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-45-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-45-epics.md`
- `_bmad-output/implementation-artifacts/124-1-task-list-sort-vocabulary-planning.md`
- `.omx/plans/story-124-1-task-list-sort-vocabulary-planning-plan.md`
- `.omx/specs/story-124-1-task-list-sort-vocabulary-planning-test-spec.md`

## Consensus evidence

- Architect review: native agent `019f1a08-9bcc-7961-9c36-c60daacf175f` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []`; persisted at `.omx/artifacts/ralplan/story-124-1-architect-review.md`.
- Critic initial review: native agent `019f1a0a-10e7-7d13-b985-b96420e3f6d0` returned `verdict: request_changes`, `architectural_status: WATCH`, requiring repair to a stale `docs/feature-status.md` sprint-status evidence bullet; persisted at `.omx/artifacts/ralplan/story-124-1-critic-review-initial.md`.
- Critic final review: native agent `019f1a0a-10e7-7d13-b985-b96420e3f6d0` returned `verdict: approve`, `architectural_status: CLEAR`, `required_changes: []` after the derivative status repair; persisted at `.omx/artifacts/ralplan/story-124-1-critic-review.md`.

## Completion evidence

Story 124.1 completes Phase 45 / Epic 124 docs/status-only finite task-list sort vocabulary planning after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/API/test/browser/dashboard/dependency/CI/deployment/service/MCP/generated-data implementation remains unstarted and authorized only for the exact future Story 124.2 boundary.

## Review and QA evidence

- Final code-review: APPROVE after the LOW derivative-doc drift risk was repaired by declaring `phase-45-architecture-amendment.md` the canonical Phase 45 contract source and pointing derivative docs/status entries back to it; persisted at `.omx/artifacts/code-review/story-124-1-code-review-final.md`.
- UltraQA disposition: skipped as not applicable because Story 124.1 changed only docs/status/planning artifacts and did not change runtime/API/browser/test behavior; persisted at `.omx/artifacts/ultraqa/story-124-1-ultraqa-skip-report.md`.
- Verification rerun: `.omx/artifacts/ultragoal/story-124-1/verification-after-review-repair.log` records YAML/status assertions, canonical-source assertions, changed-file scope guard, trailing-whitespace check, and `git diff --check` passing.

## Verification plan

- Verify Phase 45 artifacts exist and state docs/status-only planning scope.
- Verify sprint status opens Phase 45 / Epic 124, marks Story 124.1 done only after consensus, and keeps Story 124.2/124.3 backlog.
- Verify `docs/feature-status.md` states broader sort vocabulary is planned but not implemented.
- Verify no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change for Story 124.1.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Completion timestamp

2026-06-30T19:51:04Z
