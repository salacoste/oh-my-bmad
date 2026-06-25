# Story 108.1 — Aggregate/Session/Digest Route Selection Planning

## Status

Done — docs/status-only planning opening.

## Route family selected

- `aggregate/session/digest`

## Exact future candidate surface selected

- `GET /v1/tasks/{task_id}/logs/digest`

## Non-authorization statement

This story does not authorize runtime implementation, dashboard JavaScript/HTML behavior changes, browser network calls, backend/API changes, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, browser-side LLM prompt construction or summarization, cache warming, polling, timers, background jobs, automatic refresh, digest streaming, aggregate task list/read contracts, session list/detail contracts, task-list/search/discovery, broad dashboard wiring, mutation/control behavior, production credentials, or production operations.

## Planning evidence

- Context snapshot: `.omx/context/phase-29-aggregate-session-digest-planning-20260625T214500Z.md`
- Deep-interview handoff: `.omx/interviews/phase-29-aggregate-session-digest-planning-deep-interview.md`
- RALPLAN: `.omx/specs/phase-29-aggregate-session-digest-planning-ralplan.md`
- Test spec: `.omx/specs/phase-29-aggregate-session-digest-planning-test-spec.md`
- Architect review: `.omx/specs/phase-29-aggregate-session-digest-planning-architect-review.md` — APPROVE/CLEAR.
- Critic review: `.omx/specs/phase-29-aggregate-session-digest-planning-critic-review.md` — APPROVE with WATCH notes captured for Story 108.2.

## Acceptance criteria evidence

1. Phase 29 PRD amendment exists and selects the aggregate/session/digest family and exactly `GET /v1/tasks/{task_id}/logs/digest` as the future digest-read candidate.
2. Phase 29 architecture amendment defines exact route, visible task_id selector, digest provenance/freshness, fail-closed degraded states, no-hidden-generation, no-streaming, no-aggregate/session, no-discovery, and deferred-surface boundaries.
3. Phase 29 epics sequence Story 108.1 planning, Story 108.2 task log digest runtime boundary, and Story 108.3 final closure.
4. Sprint status opens Phase 29 / Epic 108, marks Story 108.1 done, leaves Story 108.2/108.3 backlog, and preserves Epic 107 done.
5. Runtime implementation is deferred to Story 108.2 and must be tests-first.
6. `docs/feature-status.md` is refreshed as a derivative status summary and does not claim digest runtime implementation is complete.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files changed in this planning story.

## Future Story 108.2 obligations

- Exact route allowlist for `GET /v1/tasks/{task_id}/logs/digest` only.
- GET-only and body-free dashboard calls.
- Explicit visible task_id as the only selector; no query/hash/storage/session/search/discovery/list/aggregate-derived task IDs.
- No `/v1/tasks/{task_id}/logs/digest/stream`, `/v1/tasks`, `/v1/sessions`, `/v1/sessions/{session_id}`, task-list/search/discovery, aggregate/session traversal, or broad dashboard wiring.
- No browser-side LLM generation, prompt construction, hidden summarization, external provider calls, generated live data, cache warming, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, automatic refresh, automatic retry loops, or mutation/control behavior.
- Missing task_id, unavailable/missing digest, no configured digest provider, provider unavailable, timeout, non-2xx, invalid response, empty digest, stale digest, unauthorized, and backend-unavailable states fail closed with non-authoritative copy and no automatic retry.
- Digest output is bounded display content with source route, visible task_id, retrieved-at/completed-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata.
- Digest text cannot become generated live data, route selector, control input, replay target, aggregate/session source, or discovery/search input.
- Existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, and snapshot-create runtime-boundary tests remain green.
- Preserve the existing dashboard exclusion tests while adding any future digest allowlist, because current runtime suites intentionally reject digest calls.
- Independent review, QA, push, and remote CI green before completion.

## Verification plan

- YAML parse for sprint-status.
- `git diff --check`.
- Changed-file gate including tracked and untracked files, proving only Story 108.1 docs/status and OMX evidence files changed.
- Grep checks for selected route, excluded aggregate/session/list/search/discovery/digest-stream/runtime/backend/API/dependency/CI/service/MCP/generated-data surfaces, and fail-closed deferred surfaces.
