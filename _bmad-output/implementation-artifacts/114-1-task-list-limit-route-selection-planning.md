# Story 114.1 — Task List Limit Route Selection Planning

## Status

Done — docs/status-only Phase 35 / Epic 114 opening after Autopilot deep-interview handoff, Architect APPROVE/CLEAR, and subsequent Critic APPROVE/CLEAR RALPLAN consensus. Runtime/API/browser/test implementation remains unauthorized until a later tests-first Story 114.2 consumes this approved planning handoff.

## Selected route family and exact future candidate

- Selected family: read-only task-list sizing / bounded-list control.
- Exact future candidate: `GET /v1/tasks?limit={task_list_limit}`.
- Allowed selector domain: an integer task_list_limit from 1 through 50 inclusive.
- Current brownfield state: `GET /v1/tasks` is implemented as a selector-free bounded first page with fixed server limit 50; `GET /v1/tasks?status={task_status}` is implemented with one lifecycle status selector only; offset/cursor/page traversal, sorting, free-text search, arbitrary discovery, and selector composition remain deferred.

## Non-authorization statement

Story 114.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, status+limit combinations, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 114.2 test obligations

A later tests-first implementation story must prove:

1. exact route `GET /v1/tasks?limit={task_list_limit}` only;
2. GET-only behavior with exactly one `limit` query key and no request body;
3. accepted limit values limited to integers from 1 through 50 inclusive;
4. repeated limit keys, unknown/empty keys, zero/negative/fractional/non-integer/out-of-range values, extra query keys, encoded nested parameters, hidden selectors, URL hashes, cookies, storage, generated selectors, status+limit combinations, and row-derived route inputs fail closed;
5. bounded aggregate task-list row shape and order remain intact and output adds only selected-limit metadata required for source/freshness/authority;
6. no offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, automatic retry, workers, side channels, storage writes, mutation/control calls, or production operations;
7. fail-closed non-authoritative rendering for missing/invalid selector, empty result, unauthorized/configuration failure, backend unavailable, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload;
8. visible source route, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata;
9. existing aggregate task list, status-filter task list, and adjacent dashboard/API boundary tests remain green;
10. code-review APPROVE, architect CLEAR, UltraQA PASS or proportional QA, push, and remote CI green before completion.

## Verification plan for Story 114.1

- Confirm Phase 35 planning artifacts exist and select exactly `GET /v1/tasks?limit={task_list_limit}`.
- Confirm sprint status opens Epic 114 and leaves Story 114.2/114.3 backlog.
- Confirm feature status records Phase 35 as planning-only and does not claim task-list-limit runtime/API implementation.
- Confirm changed files are docs/status/planning/OMX evidence only before any implementation handoff.
- Run `git diff --check` and parse `sprint-status.yaml`.

## RALPLAN evidence

- Context snapshot: `.omx/context/1-create-phase-35-route-selection-planning-artif-20260627T185148Z.md`.
- Deep interview: `.omx/interviews/phase-35-task-list-limit-route-selection-deep-interview.md`.
- Plan: `.omx/plans/phase-35-task-list-limit-route-selection-plan.md`.
- Test spec: `.omx/specs/phase-35-task-list-limit-route-selection-test-spec.md`.
- Architect review: `.omx/specs/phase-35-task-list-limit-route-selection-architect-review.md` — native architect agent `019f0a72-4ede-7b40-bd0a-8862431ca4c4`, `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/phase-35-task-list-limit-route-selection-critic-review.md` — native critic agent `019f0a74-783a-78f0-98bf-7bbc22c61683`, `APPROVE` / `CLEAR` after fixing stale feature-status sprint evidence.

## Completion note

Story 114.1 is complete only as a planning/status opening after sequential Architect and Critic approval. Story 114.2 is the first story allowed to modify runtime/API/tests, and only after this RALPLAN consensus is consumed as the implementation handoff.

Generated: 2026-06-27T18:56:38Z
