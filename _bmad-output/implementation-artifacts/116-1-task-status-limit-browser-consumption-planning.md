# Story 116.1 — Task Status + Limit Browser Consumption Route-Selection Planning

Date: 2026-06-28T17:26:00Z
Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only

## Selected route family and exact future candidate

- Selected family: read-only aggregate task-list status+limit browser consumption.
- Exact future candidate: dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Selector source: visible aggregate-task-list panel controls only.
- Allowed statuses: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`.
- Allowed limits: integer values 1 through 50 inclusive.
- Current brownfield state: backend/API routes are implemented for selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, and exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`. The dashboard aggregate-task-list browser runtime currently consumes selector-free `GET /v1/tasks` only; browser/dashboard status+limit consumption, pagination traversal, sorting, free-text search, arbitrary discovery, and broader selector composition remain deferred.

## Non-authorization statement

Story 116.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, new selector vocabularies, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 116.2 test obligations

A future tests-first implementation story must prove:

1. only dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` is newly reachable;
2. browser requests are GET-only, bodyless, credentials-omitted, and canonical status-then-limit query order;
3. status values are limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, and `failed`;
4. limit values are ASCII integer values from 1 through 50 inclusive;
5. selector values come from visible aggregate-task-list panel controls only;
6. missing/invalid controls, extra/repeated keys, empty/unknown statuses, empty/zero/negative/fractional/non-integer/out-of-range limits, encoded/nested parameters, reversed query order, hidden selectors, URL hash/query-state persistence, local/session storage, cookies, generated selectors, row-derived selectors, background jobs, polling/timers, automatic refresh, automatic retry, workers, side channels, and status+limit+anything fail closed;
7. displayed output exposes route, selected status, selected limit, retrieved_at, freshness, authority, provenance, request/trace/correlation id where available, returned_count, has_more, degraded state, and bounded rows before any authoritative state;
8. selector-free, status-only, limit-only, and backend status+limit task-list route contracts remain independently green;
9. no pagination traversal, sorting, free-text search, arbitrary discovery, replay execution, lifecycle mutation, broad dashboard wiring, generated live data, browser-side LLM behavior, services/MCP/dependencies/CI/deployment expansion, production credentials, or production operations are introduced.

## Verification plan for Story 116.1

- Verify Phase 37 PRD, architecture, and epics artifacts exist and contain the exact future candidate.
- Verify sprint status opens Phase 37 / Epic 116 without marking runtime/browser implementation complete.
- Verify `docs/feature-status.md` states Phase 37 is planning-only and browser status+limit consumption is selected but not implemented.
- Verify only docs/status artifacts changed; no runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files changed.
- Run YAML parse on `sprint-status.yaml` and `git diff --check`.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-37-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-37-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-37-epics.md`
- `_bmad-output/implementation-artifacts/116-1-task-status-limit-browser-consumption-planning.md`

## Consensus evidence

- Architect review: native agent `019f0f42-bfe6-7fb0-85f1-ef3f361f69ec` returned `verdict: approve`, `architectural_status: CLEAR`, `findings: none` on 2026-06-28.
- Critic review: native agent `019f0f4b-d94a-7172-a1c8-22c93150a0d5` returned `verdict: approve`, `architectural_status: CLEAR`, `findings: []` after the Architect gate on 2026-06-28.

## Completion evidence

- Story 116.1 opened Phase 37 / Epic 116 as docs/status-only planning.
- Exact future candidate recorded: dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` from visible controls only.
- Runtime/browser/API/test implementation remains deferred to Story 116.2.
- Sprint status and derivative feature status were updated on 2026-06-28T17:34:00Z.

## Completion timestamp

Completed: 2026-06-28T17:34:00Z
