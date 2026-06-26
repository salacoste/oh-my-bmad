# Story 111.1 — Session Detail Route Selection Planning

## Status

Done — docs/status-only Phase 32 / Epic 111 opening after Autopilot deep-interview handoff and RALPLAN consensus.

## Selected route family and exact future candidate

- Selected family: session visibility continuation.
- Exact future candidate: `GET /v1/sessions/{session_id}`.
- Current brownfield state: the API implements `GET /v1/sessions` only; the detail route is not implemented and dashboard contracts still treat `/v1/sessions/{session_id}` as needs-separate-contract until Story 111.2.

## Non-authorization statement

Story 111.1 does not implement or authorize runtime behavior. It does not add browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, digest streaming, task-list/search/discovery, hidden selectors, automatic drill-down from session-list rows, cache warming, polling/timers/background jobs, storage writes, browser-side generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 111.2 test obligations

A later tests-first implementation story must prove:

1. exact route `GET /v1/sessions/{session_id}` only;
2. GET-only, query-free, body-free API/dashboard calls;
3. visible operator-provided `session_id` path parameter only, with percent-encoding discipline;
4. no automatic session-list row click/prefetch/hidden selector propagation;
5. bounded Session-table-only output and omission of raw worktree paths, filesystem/resource paths, event payloads, logs, summaries, hrefs/URLs, generated text, joined Task/Event data, and control hints;
6. no task detail, digest, history, trace, replay, digest stream, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers, automatic retry, workers, side channels, storage writes, mutation/control calls, or production operations;
7. fail-closed non-authoritative rendering for missing/invalid session_id, not found, unauthorized, backend unavailable, non-2xx, route failure/read error, invalid response, malformed row, unexpected keys, stale/ambiguous freshness, over-broad payload, and path-like values;
8. visible source route, selected session_id, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata;
9. existing session-list and adjacent dashboard/API boundary tests remain green;
10. code-review APPROVE, architect CLEAR, UltraQA PASS, push, and remote CI green before completion.

## Verification plan for Story 111.1

- Confirm Phase 32 planning artifacts exist and select exactly `GET /v1/sessions/{session_id}`.
- Confirm sprint status opens Epic 111 and leaves Story 111.2/111.3 backlog.
- Confirm feature status records Phase 32 as planning-only and does not claim runtime/API implementation.
- Confirm changed files are docs/status/planning/state only before any implementation handoff.
- Run `git diff --check`.

## RALPLAN evidence

- Context snapshot: `.omx/context/phase32-session-detail-route-selection-20260626T180632Z.md`.
- Deep interview: `.omx/interviews/phase-32-session-detail-route-selection-deep-interview.md`.
- Plan: `.omx/plans/phase-32-session-detail-route-selection-plan.md`.
- Test spec: `.omx/specs/phase-32-session-detail-route-selection-test-spec.md`.
- Architect review: `.omx/specs/phase-32-session-detail-route-selection-architect-review.md` — native architect agent `019f0524-be95-7ae1-865c-8ea1021103a9`, `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/phase-32-session-detail-route-selection-critic-review.md` — native critic agent `019f0527-9d0b-7ce3-916f-75a2765f2263`, `APPROVE` / `CLEAR`.

## Completion note

Story 111.1 is complete only as a planning/status opening. Story 111.2 is the first story allowed to modify runtime/API/tests, and only after RALPLAN consensus is durable.

Generated: 2026-06-26T18:14:59Z
