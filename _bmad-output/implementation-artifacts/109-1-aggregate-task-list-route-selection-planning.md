# Story 109.1 — Aggregate Task List Route Selection Planning

## Status

Done — docs/status-only Phase 30 / Epic 109 opening and route-selection planning after sequential Architect APPROVE/CLEAR and Critic APPROVE consensus.

## Selected scope

- Phase: 30
- Epic: 109
- Selected family: aggregate task list read
- Selected exact future candidate: `GET /v1/tasks`

## Non-authorization statement

Story 109.1 does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session list/detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations. It does not treat `POST /v1/tasks` as a read/list route and does not claim aggregate task list runtime or API implementation.

## Evidence and rationale

- Phase 29 / Epic 108 is done for exactly `GET /v1/tasks/{task_id}/logs/digest`, with Story 108.3 closure and remote CI run `28205787033` green.
- `docs/feature-status.md` still lists aggregate task list reads, session list/detail, digest streaming, and task-list/search/discovery as deferred/fail-closed.
- `dashboard/live_read_adapter.py` marks `/v1/tasks` as `needs-separate-contract`, which is preserved by this docs/status-only story.
- `docs/api-contracts.md` documents `POST /v1/tasks` for task creation but not a shipped `GET /v1/tasks` read contract; future Story 109.2 must therefore prove or add the exact read contract tests-first before dashboard runtime completion.
- `GET /v1/tasks` is selected as a narrower overview candidate than full task-list/search/discovery and less coupled than session list/detail or digest streaming.

## Future Story 109.2 obligations

A future runtime/API story must prove or implement the exact `GET /v1/tasks` contract with tests before claiming completion. The tests must cover:

1. exact route and GET-only/body-free behavior;
2. no `POST /v1/tasks`, POST/PUT/PATCH/DELETE, or mutation/control behavior;
3. bounded server-returned task summary rows only;
4. rows cannot automatically become hidden selectors for task detail, digest, history, trace, replay, session traversal, search/discovery, or mutation controls;
5. no query/hash/local-storage/session-storage/cookie/hidden-form selectors;
6. no `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming, polling/timers, workers, side channels, storage writes, automatic refresh, or automatic retry;
7. missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, and ambiguous freshness fail closed with non-authoritative copy;
8. source route, retrieved-at, freshness, authority/provenance, trace/request/correlation id where available, pagination/limit metadata, and degraded-state metadata are visible;
9. existing dashboard runtime-boundary suites remain green;
10. independent review, proportional QA, push, and remote CI evidence exist before runtime completion is claimed.

## Verification plan for Story 109.1

- Verify Phase 30 PRD / architecture / epics artifacts exist.
- Verify sprint status parses as YAML and marks Phase 30 / Epic 109 opening correctly.
- Verify content includes selected exact candidate `GET /v1/tasks` and explicit deferred surfaces.
- Verify changed product files are docs/status/planning only.
- Run `git diff --check`.

## Completion evidence

Local verification passed before final consensus:

- `sprint-status.yaml` parsed as YAML.
- Interim status assertions passed while consensus was pending; final status assertions now pass for `current_phase: 30`, `epic-108: done`, `epic-109: in-progress`, `109-1: done`, and `109-2`/`109-3: backlog`.
- Content checks found selected family `aggregate task list read`, exact candidate `GET /v1/tasks`, and explicit deferred surfaces.
- `git diff --check` passed.
- Initial Architect review (`019f011f-3436-7b52-8986-4157d22f5173`) returned `REQUEST CHANGES / WATCH` only because this artifact claimed `done` while final consensus evidence was pending. This artifact and sprint/feature status were corrected to in-progress until final approval.

Final consensus evidence:

- Architect review: `.omx/specs/phase-30-aggregate-task-list-planning-architect-review.md` — native subagent `019f011f-3436-7b52-8986-4157d22f5173`, `verdict: approve`, `architectural_status: CLEAR`.
- Critic review: `.omx/specs/phase-30-aggregate-task-list-planning-critic-review.md` — native subagent `019f012b-52de-72c1-ade0-1b6f9c1db271`, `verdict: approve`.
- RALPLAN consensus gate complete: Architect approval was recorded before Critic approval.

Completion status updated after consensus.

Final review and QA evidence:

- Code review: `.omx/specs/phase-30-aggregate-task-list-planning-code-review.md` — native subagent `019f012e-ab9e-72f1-ae03-11f8beaaecfc`, `final_recommendation: APPROVE`, `architectural_status: CLEAR`.
- UltraQA: `.omx/ultraqa/phase-30-aggregate-task-list-planning-skip.md` — skipped as docs/status-only with clean evidence.

Updated: 2026-06-26T00:10:31Z

Generated: 2026-06-25T23:30:38Z
