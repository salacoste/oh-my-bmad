# Phase 34 Architecture Amendment — Task Status Filter Route Selection Planning

## Decision summary

Phase 34 may proceed from completed digest-stream closure into the next narrow dashboard/API planning branch. This amendment selects:

- **Family:** read-only task-list/search/discovery
- **Exact future candidate surface:** `GET /v1/tasks?status={task_status}`
- **Allowed selector domain:** one explicit task lifecycle status from `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`

Story 113.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, hidden selectors, automatic drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/112-3-phase-33-epic-112-final-closure.md`
- `_bmad-output/planning-artifacts/phase-33-epics.md`
- `docs/feature-status.md`
- `dashboard/live_read_adapter.py`
- `dashboard/static/aggregate-task-list.js`
- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-state/src/registry_state/domain/task_fsm.py`
- `services/registry-state/src/registry_state/schema.py`
- `.omx/context/phase-34-select-the-next-exact-dashboard-api-rou-20260627T162736Z.md`

## Current brownfield state

The platform already implements exact `GET /v1/tasks` as a selector-free bounded first page. The route rejects every query string and every GET body. Dashboard rendering is route-local in `dashboard/static/aggregate-task-list.js`, and feature status keeps broader task-list/search/discovery deferred. The task lifecycle model documents eight current statuses, and the registry-state schema includes `ix_tasks_status_updated_at`, which makes status-filtered task listing a plausible future read-only extension.

## Route selection rationale

`GET /v1/tasks?status={task_status}` is selected because it is the smallest concrete step beyond selector-free `GET /v1/tasks` inside the user's recommended safest branch. It is narrower than free-text search, arbitrary discovery, replay execution target selection, or lifecycle mutation planning. It reuses the task-list row family, uses an explicit finite selector vocabulary, and can be tested as an additive route-local query contract without approving hidden selector discovery or adjacent route traversal.

## Architectural boundaries

### Boundary 1 — Story 113.1 is docs/status-only

Story 113.1 may create or update only Phase 34 planning artifacts, the Story 113.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 34 work may target only `GET /v1/tasks?status={task_status}` as a read-only filtered task-summary list. It may not silently include free-text search, multi-field filtering, arbitrary query language, pagination/offset/cursor controls, sort controls, hidden discovery, automatic task/detail/digest/history/trace/replay/session traversal, replay execution jobs, lifecycle mutation controls, or broad dashboard live wiring.

### Boundary 3 — One visible status selector only

Future dashboard calls must expose the selected status visibly and send exactly one `status` query key with a lifecycle status value. Query strings with repeated `status`, extra keys, empty values, unknown status values, encoded nested parameters, request bodies, URL hashes, cookies, local/session storage, hidden inputs, generated selectors, row-derived hidden attributes, and task-list discovery results are not approved selector sources.

### Boundary 4 — Bounded task summary row shape remains intact

Future implementation must preserve the current aggregate-task-list summary row shape and bounds unless a later planning gate changes them. The filter may add visible `selected_status` / filter metadata and count/freshness/provenance fields, but may not expose raw event payloads, logs, prompts, session internals, trace traversal payloads, filesystem/resource paths, hrefs/URLs, generated browser summaries, or control hints.

### Boundary 5 — No pagination/sort/search pre-authorization

Selecting a status-filtered route does not authorize pagination, cursoring, offset/limit controls, sorting controls, free-text search, saved searches, browser URL-state persistence, background polling, cache warming, automatic refresh, or broad discovery. Those remain separate future contracts.

### Boundary 6 — Existing exact `GET /v1/tasks` remains independent

The completed selector-free aggregate route remains its own bounded first-page read. Selecting the status-filter candidate does not change its current query-rejection contract and does not authorize fallback between filtered and unfiltered modes until Story 113.2 defines tests and implementation.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact API/dashboard route contract for `GET /v1/tasks?status={task_status}` only;
2. GET-only behavior, exactly one `status` query key, and no request body;
3. accepted status values are limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`;
4. repeated/unknown/empty status keys, extra query keys, encoded nested parameters, request bodies, hashes, cookies, storage, and hidden selectors fail closed;
5. bounded task summary rows keep the existing aggregate list shape and route-local metadata;
6. no free-text search, multi-field filtering, arbitrary query language, pagination/cursor/offset/limit/sort controls, hidden discovery, or saved searches;
7. no automatic task detail, digest, history, trace, replay, session, or lifecycle traversal from returned rows;
8. no replay execution target-selection calls, lifecycle apply/prune/rollback, mutation/control behavior, generated live data, browser-side LLM behavior, workers/service workers, storage writes, background refresh, polling/timers, or cache warming;
9. empty-filter results, stale/ambiguous freshness, backend unavailable, unauthorized/configuration failure, malformed response, over-limit response, unexpected keys, and route failure/read error render non-authoritative fail-closed copy;
10. source route, selected status, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, count/has_more, and degraded-state metadata are visible;
11. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, digest-stream, aggregate task list, session-list, and session-detail runtime-boundary tests remain green;
12. independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green are recorded before closure.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 113.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-27T16:32:18Z
