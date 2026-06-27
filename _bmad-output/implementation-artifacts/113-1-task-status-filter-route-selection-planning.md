# Story 113.1 — Task Status Filter Route Selection Planning

## Status

Done — docs/status-only Phase 34 / Epic 113 opening after Autopilot deep-interview handoff, Architect APPROVE/CLEAR, and subsequent Critic APPROVE/CLEAR RALPLAN consensus.

## Selected route family and exact future candidate

- Selected family: read-only task-list/search/discovery.
- Exact future candidate: `GET /v1/tasks?status={task_status}`.
- Allowed selector domain: one explicit task lifecycle status from `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`.
- Current brownfield state: `GET /v1/tasks` is implemented as a selector-free bounded first page and currently rejects query strings and GET bodies; broader task-list/search/discovery remains deferred.

## Non-authorization statement

Story 113.1 does not implement or authorize runtime behavior. It does not add browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, pagination/sort controls, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, cache warming, polling/timers/background jobs, storage writes, browser-side generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 113.2 test obligations

A later tests-first implementation story must prove:

1. exact route `GET /v1/tasks?status={task_status}` only;
2. GET-only behavior with exactly one `status` query key and no request body;
3. accepted status values limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`;
4. repeated status keys, unknown/empty values, extra query keys, encoded nested parameters, hidden selectors, URL hashes, cookies, storage, generated selectors, and row-derived route inputs fail closed;
5. bounded aggregate task-list row shape remains intact and output adds only selected-status/filter metadata required for source/freshness/authority;
6. no free-text search, arbitrary filters, pagination/cursor/offset/limit/sort controls, saved searches, hidden discovery, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, automatic retry, workers, side channels, storage writes, mutation/control calls, or production operations;
7. fail-closed non-authoritative rendering for missing/invalid selector, empty result, unauthorized/configuration failure, backend unavailable, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload;
8. visible source route, selected status, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, count/has_more, and degraded-state metadata;
9. existing aggregate task list and adjacent dashboard/API boundary tests remain green;
10. code-review APPROVE, architect CLEAR, UltraQA PASS or proportional QA, push, and remote CI green before completion.

## Verification plan for Story 113.1

- Confirm Phase 34 planning artifacts exist and select exactly `GET /v1/tasks?status={task_status}`.
- Confirm sprint status opens Epic 113 and leaves Story 113.2/113.3 backlog.
- Confirm feature status records Phase 34 as planning-only and does not claim status-filter runtime/API implementation.
- Confirm changed files are docs/status/planning/state only before any implementation handoff.
- Run `git diff --check` and parse `sprint-status.yaml`.

## RALPLAN evidence

- Context snapshot: `.omx/context/phase-34-select-the-next-exact-dashboard-api-rou-20260627T162736Z.md`.
- Deep interview: `.omx/interviews/phase-34-task-status-filter-route-selection-deep-interview.md`.
- Plan: `.omx/plans/phase-34-task-status-filter-route-selection-plan.md`.
- Test spec: `.omx/specs/phase-34-task-status-filter-route-selection-test-spec.md`.
- Architect review: `.omx/specs/phase-34-task-status-filter-route-selection-architect-review.md` — native architect agent `019f09ec-f500-7c53-8395-f2ed643368e6`, `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/phase-34-task-status-filter-route-selection-critic-review.md` — native critic agent `019f09ee-53c5-79f2-9ad3-f1ff5a391cbf`, `APPROVE` / `CLEAR`.

## Completion note

Story 113.1 is complete only as a planning/status opening after sequential Architect and Critic approval. Story 113.2 is the first story allowed to modify runtime/API/tests, and only after this RALPLAN consensus is consumed as the implementation handoff.

Generated: 2026-06-27T16:32:18Z
