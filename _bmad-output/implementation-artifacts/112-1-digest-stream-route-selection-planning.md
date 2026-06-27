# Story 112.1 — Digest Stream Route Selection Planning

## Status

Done — docs/status-only Phase 33 / Epic 112 opening after Autopilot deep-interview handoff, repaired Architect APPROVE/CLEAR, and subsequent Critic APPROVE/CLEAR RALPLAN consensus.

## Selected route family and exact future candidate

- Selected family: task log digest continuation.
- Exact future candidate: `GET /v1/tasks/{task_id}/logs/digest/stream`.
- Current brownfield state: the API contracts document non-streaming `GET /v1/tasks/{task_id}/logs/digest`; the stream route is not documented as implemented and `dashboard/live_read_adapter.py` keeps `/v1/tasks/{task_id}/logs/digest/stream` in `EXCLUDED_ROUTE_PATTERNS`.

## Non-authorization statement

Story 112.1 does not implement or authorize runtime behavior. It does not add browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, task-list/search/discovery, hidden selectors, automatic drill-down, stream transport selection, cache warming, polling/timers/background jobs, storage writes, browser-side generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Future Story 112.2 test obligations

A later tests-first implementation story must prove:

1. exact route `GET /v1/tasks/{task_id}/logs/digest/stream` only;
2. GET-only, query-free, body-free API/dashboard calls;
3. visible operator-provided `task_id` path parameter only, with percent-encoding discipline;
4. exactly one approved stream transport/framing contract, with unselected EventSource/WebSocket/XMLHttpRequest/workers/polling/retry paths forbidden;
5. bounded stream open/partial/final/error/timeout/close/stale semantics;
6. bounded digest-stream chunks and metadata only, omitting raw logs, event payloads, prompts, provider internals, paths, hrefs/URLs, generated browser summaries, joined Task/Event/Session data, and control hints;
7. no task-list/search/discovery, task detail, non-streaming digest fallback, history, trace, replay, session traversal, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers beyond the selected stream contract, automatic retry, workers, side channels, storage writes, mutation/control calls, or production operations;
8. fail-closed non-authoritative rendering for missing/invalid task_id, unauthorized, backend/provider unavailable, non-2xx, route failure/read error, invalid stream framing, malformed chunk, unexpected keys, excessive chunk volume, interrupted stream, stale/ambiguous freshness, over-broad payload, and path-like values;
9. visible source route, selected task_id, connection/opened-at, last-event/retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata;
10. existing digest and adjacent dashboard/API boundary tests remain green;
11. code-review APPROVE, architect CLEAR, UltraQA PASS or proportional QA, push, and remote CI green before completion.

## Verification plan for Story 112.1

- Confirm Phase 33 planning artifacts exist and select exactly `GET /v1/tasks/{task_id}/logs/digest/stream`.
- Confirm sprint status opens Epic 112 and leaves Story 112.2/112.3 backlog.
- Confirm feature status records Phase 33 as planning-only and does not claim digest stream runtime/API implementation.
- Confirm changed files are docs/status/planning/state only before any implementation handoff.
- Run `git diff --check`.

## RALPLAN evidence

- Context snapshot: `.omx/context/phase33-digest-stream-route-selection-20260626T220010Z.md`.
- Deep interview: `.omx/interviews/phase-33-digest-stream-route-selection-deep-interview.md`.
- Plan: `.omx/plans/phase-33-digest-stream-route-selection-plan.md`.
- Test spec: `.omx/specs/phase-33-digest-stream-route-selection-test-spec.md`.
- Architect review cycle 1: native architect agent `019f05f7-36e1-77b3-8933-69ce81fca9ca` returned `REVISE` / `WATCH` because the story/sprint/epic artifacts prematurely claimed done consensus before durable Architect/Critic evidence existed.
- Architect repair: status wording was normalized so Story 112.1 remained review-pending until Architect cycle 2 returned APPROVE/CLEAR and the subsequent Critic review returned APPROVE/CLEAR.
- Architect review cycle 2: `.omx/specs/phase-33-digest-stream-route-selection-architect-review.md` — native architect agent `019f05fc-4d51-7503-ada2-b5061098cd46`, `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/phase-33-digest-stream-route-selection-critic-review.md` — native critic agent `019f05fe-ee82-70d0-92a8-6f5e1fd94993`, `APPROVE` / `CLEAR`.

## Completion note

Story 112.1 is complete only as a planning/status opening after repaired sequential Architect and Critic approval. Story 112.2 is the first story allowed to modify runtime/API/tests, and only after this RALPLAN consensus is consumed as the implementation handoff.

Generated: 2026-06-26T22:03:46Z
