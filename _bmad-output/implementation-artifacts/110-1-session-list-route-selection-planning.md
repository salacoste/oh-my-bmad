# Story 110.1 — Session List Route Selection Planning

## Status

Done — docs/status-only Phase 31 / Epic 110 opening and route-selection planning after repaired sequential Architect APPROVE/CLEAR and Critic APPROVE consensus.

## Selected scope

- Phase: 31
- Epic: 110
- Selected family: session visibility
- Selected exact future candidate: `GET /v1/sessions`

## Non-authorization statement

Story 110.1 does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations. It does not treat static session visibility copy or MCP session resources as an approved HTTP session list/detail route and does not claim session list runtime or API implementation.

## Evidence and rationale

- Phase 30 / Epic 109 is done for exactly `GET /v1/tasks`, with Story 109.3 closure and remote CI run `28213044828` green.
- `docs/feature-status.md` still lists session list/detail, digest streaming, broader task-list/search/discovery, hidden selectors, automatic drill-down, generated live data, and broad dashboard wiring as unavailable/needs-contract or fail-closed.
- `dashboard/live_read_adapter.py` marks `/v1/sessions` and `/v1/sessions/{session_id}` outside approved dashboard reads; current runtime suites still reject those routes.
- `services/registry-api/src/registry_api/test_app.py` asserts `GET /v1/sessions` returns 404 in the aggregate task-list boundary, confirming no session API/runtime route is shipped.
- Story 89.3 established static sessions visibility copy from existing MCP resources only; it did not authorize HTTP session live reads.
- `GET /v1/sessions` is selected as the narrowest session-visibility candidate because it can stay a bounded summary list. Session detail is deferred because it adds a selector and deeper traversal boundary.

## Future Story 110.2 obligations

A future runtime/API story must prove or implement the exact `GET /v1/sessions` contract with tests before claiming completion. The tests must cover:

1. exact route and GET-only/body-free behavior;
2. no `GET /v1/sessions/{session_id}`, POST/PUT/PATCH/DELETE, or mutation/control behavior;
3. bounded server-returned session summary rows only;
4. rows cannot automatically become hidden selectors for session detail, task detail, digest, history, trace, replay, search/discovery, or mutation controls;
5. no query/hash/local-storage/session-storage/cookie/hidden-form selectors;
6. no digest stream, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming, polling/timers, workers, side channels, storage writes, automatic refresh, or automatic retry;
7. missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, stale heartbeat metadata, and ambiguous freshness fail closed with non-authoritative copy;
8. source route, retrieved-at, freshness, authority/provenance, trace/request/correlation id where available, fixed limit/page metadata, and degraded-state metadata are visible;
9. existing dashboard runtime-boundary suites remain green;
10. independent review, proportional QA, push, and remote CI evidence exist before runtime completion is claimed.

## Verification plan for Story 110.1

- Verify Phase 31 PRD / architecture / epics artifacts exist.
- Verify sprint status parses as YAML and marks Phase 31 / Epic 110 opening correctly.
- Verify content includes selected exact candidate `GET /v1/sessions` and explicit deferred surfaces.
- Verify changed product files are docs/status/planning only.
- Run `git diff --check`.

## Completion evidence

Local verification passed before final consensus:

- `sprint-status.yaml` parsed as YAML.
- Status assertions passed for `current_phase: 31`, `epic-109: done`, `epic-110: in-progress`, `110-1: done`, and `110-2`/`110-3: backlog` after final consensus.
- Content checks found selected family `session visibility`, exact candidate `GET /v1/sessions`, and explicit deferred surfaces.
- `git diff --check` passed.

Consensus repair evidence:

- Architect cycle 1: `.omx/specs/phase-31-session-list-planning-architect-review.md` — native subagent `019f0320-eb4f-7e53-83e4-2e502552424e`, `verdict: approve`, `architectural_status: CLEAR`.
- Critic cycle 1: `.omx/specs/phase-31-session-list-planning-critic-review-cycle1.md` — native subagent `019f0328-923f-7681-959a-6aca63f3a680`, `verdict: request_changes`.
- Architect cycle 2: `.omx/specs/phase-31-session-list-planning-architect-review-cycle2.md` — native subagent `019f032b-cbd3-72f0-9b01-f1d93ab61169`, `verdict: request_changes`, `architectural_status: WATCH`.
- Architect cycle 3: `.omx/specs/phase-31-session-list-planning-architect-review-cycle3.md` — native subagent `019f0333-9c22-78a2-b4e5-98ce506e611c`, `verdict: request_changes`, `architectural_status: WATCH`.
- Final Architect review: `.omx/specs/phase-31-session-list-planning-architect-review-final.md` — native subagent `019f033a-566c-7fc3-8568-cd3af82ee222`, `verdict: approve`, `architectural_status: CLEAR`.
- Final Critic review: `.omx/specs/phase-31-session-list-planning-critic-review.md` — native subagent `019f033d-f310-70d2-ae36-fa11b53a05bc`, `verdict: approve`.
- RALPLAN consensus gate complete: final Architect approval was recorded before final Critic approval.

Generated: 2026-06-26T08:50:53Z
