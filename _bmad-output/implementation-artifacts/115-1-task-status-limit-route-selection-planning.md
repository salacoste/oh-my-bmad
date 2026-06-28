# Story 115.1 — Task Status + Limit Route Selection Planning

## Status

Done — docs/status-only Phase 36 / Epic 115 opening after Autopilot deep-interview handoff, Architect APPROVE/CLEAR, and subsequent Critic APPROVE/CLEAR RALPLAN consensus. Runtime/API/browser/test implementation remains unstarted in this planning lane; a later tests-first Story 115.2 may consume this approved planning handoff.

## Selected route family and exact future candidate

- Selected family: read-only task-list bounded selector composition.
- Exact future candidate: `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Allowed status selector domain: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- Allowed limit selector domain: an integer task_list_limit from 1 through 50 inclusive.
- Current brownfield state: `GET /v1/tasks` is implemented as a selector-free bounded first page with fixed server limit 50; `GET /v1/tasks?status={task_status}` is implemented with one lifecycle status selector only; `GET /v1/tasks?limit={task_list_limit}` is implemented with one bounded integer limit selector only; `status+limit` composition, offset/cursor/page traversal, sorting, free-text search, arbitrary discovery, and broader selector composition remain deferred.

## Non-authorization statement

Story 115.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, new selector vocabularies, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 115.2 test obligations

A later tests-first implementation story must prove:

1. exact route `GET /v1/tasks?status={task_status}&limit={task_list_limit}` only;
2. GET-only behavior with exactly one `status` query key, exactly one `limit` query key, and no request body;
3. accepted status values limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, and `failed`;
4. accepted limit values limited to integers from 1 through 50 inclusive;
5. repeated keys, unknown/empty keys, unknown statuses, zero/negative/fractional/non-integer/out-of-range limits, extra query keys, encoded nested parameters, hidden selectors, URL hashes, cookies, storage, generated selectors, status+limit+anything combinations, and row-derived route inputs fail closed;
6. bounded aggregate task-list row shape and order remain intact and output adds only selected-status and selected-limit metadata required for source/freshness/authority;
7. selector-free, status-only, and limit-only task-list routes remain independently green;
8. no offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, automatic retry, workers, side channels, storage writes, mutation/control calls, or production operations;
9. fail-closed non-authoritative rendering for missing/invalid selectors, empty result, unauthorized/configuration failure, backend unavailable, non-2xx, route failure/read error, malformed payload, unexpected keys, over-limit response, stale/ambiguous freshness, and over-broad payload;
10. visible source route, selected status, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata;
11. existing aggregate task list, status-filter task list, limit-selected task list, and adjacent dashboard/API boundary tests remain green;
12. code-review APPROVE, architect CLEAR, UltraQA PASS or proportional QA, push, and remote CI green before completion.

## Verification plan for Story 115.1

- Confirm Phase 36 planning artifacts exist and select exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Confirm sprint status opens Epic 115 and leaves Story 115.2/115.3 backlog.
- Confirm feature status records Phase 36 as planning-only and does not claim status+limit runtime/API implementation.
- Confirm changed files are docs/status/planning/OMX evidence only before any implementation handoff.
- Run `git diff --check` and parse `sprint-status.yaml`.

## RALPLAN evidence

- Context snapshot: `.omx/context/phase-36-status-limit-composition-route-selection-20260628T013504Z.md`.
- Deep interview: `.omx/interviews/phase-36-status-limit-composition-route-selection-deep-interview.md`.
- Plan: `.omx/plans/phase-36-status-limit-composition-route-selection-plan.md`.
- Test spec: `.omx/specs/phase-36-status-limit-composition-route-selection-test-spec.md`.
- Architect review: `.omx/specs/phase-36-status-limit-composition-route-selection-architect-review.md` — native architect agent `019f0bde-0c87-7740-abc6-f43b08a05285`, `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/phase-36-status-limit-composition-route-selection-critic-review.md` — native critic agent `019f0be0-ad2a-7a71-933e-dc7ea320e652`, `APPROVE` / `CLEAR` after Architect approval.

## Completion note

Story 115.1 is complete only as a planning/status opening after sequential Architect and Critic approval. Story 115.2 is the first story allowed to modify runtime/API/tests, and only when a later execution lane consumes this RALPLAN consensus as the implementation handoff.

Generated: 2026-06-28T01:35:04Z

Updated: 2026-06-28T01:42:26Z
