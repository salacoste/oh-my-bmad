# Phase 35 Architecture Amendment — Task List Limit Route Selection Planning

## Decision summary

Phase 35 may proceed from completed task-status-filter closure into the next narrow dashboard/API planning branch. This amendment selects:

- **Family:** read-only task-list sizing / bounded-list control
- **Exact future candidate surface:** `GET /v1/tasks?limit={task_list_limit}`
- **Allowed selector domain:** an integer task_list_limit from 1 through 50 inclusive

Story 114.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, status+limit combinations, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/113-3-phase-34-epic-113-final-closure.md`
- `_bmad-output/planning-artifacts/phase-34-epics.md`
- `docs/feature-status.md`
- `docs/api-contracts.md`
- `dashboard/static/aggregate-task-list.js`
- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-state/src/registry_state/schema.py`
- `.omx/context/1-create-phase-35-route-selection-planning-artif-20260627T185148Z.md`

## Current brownfield state

The platform already implements exact `GET /v1/tasks` as a selector-free bounded first page with server limit 50, `has_more`, and `next_offset: null`. Phase 34 implemented exact `GET /v1/tasks?status={task_status}` with one finite lifecycle status selector only. The route rejects GET bodies, extra query keys, repeated status selectors, unknown/empty status values, nested parameters, free-text search, pagination, sorting, hidden selectors, traversal, replay execution, lifecycle mutation, and broad dashboard wiring. Dashboard rendering remains route-local in `dashboard/static/aggregate-task-list.js`.

## Route selection rationale

`GET /v1/tasks?limit={task_list_limit}` is selected because it is the smallest concrete step beyond fixed-size task-list reads. It makes the existing bounded row limit explicit while keeping the response on the first page only. It is narrower than cursor/offset/page traversal, sorting, free-text search, arbitrary discovery, replay execution target selection, or lifecycle mutation planning. It reuses the existing task-list row family and order and can be tested as an additive route-local query contract without approving hidden selector discovery, adjacent route traversal, or pagination state.

## Architectural boundaries

### Boundary 1 — Story 114.1 is docs/status-only

Story 114.1 may create or update only Phase 35 planning artifacts, the Story 114.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 35 work may target only `GET /v1/tasks?limit={task_list_limit}` as a read-only first-page task-summary list with caller-selected bounded row count. It may not silently include offset/cursor/page traversal, free-text search, multi-field filtering, status+limit combinations, arbitrary query language, sort controls, hidden discovery, automatic task/detail/digest/history/trace/replay/session traversal, replay execution jobs, lifecycle mutation controls, or broad dashboard live wiring.

### Boundary 3 — One visible limit selector only

Future dashboard/API calls must expose the selected limit visibly and send exactly one `limit` query key with an integer value from 1 through 50 inclusive. Query strings with repeated `limit`, extra keys, empty values, zero/negative/fractional/non-integer/out-of-range values, encoded nested parameters, request bodies, URL hashes, cookies, local/session storage, hidden inputs, generated selectors, row-derived hidden attributes, and task-list discovery results are not approved selector sources.

### Boundary 4 — Bounded task summary row shape and order remain intact

Future implementation must preserve the current aggregate-task-list summary row shape and ordering (`updated_at DESC, id ASC`) unless a later planning gate changes them. The limit selector may add visible `selected_limit` metadata and returned_count/has_more/freshness/provenance fields, but may not expose raw event payloads, logs, prompts, session internals, trace traversal payloads, filesystem/resource paths, hrefs/URLs, generated browser summaries, or control hints.

### Boundary 5 — No pagination, sorting, search, or selector composition pre-authorization

Selecting a limit-only route does not authorize offset/cursor/page traversal, next-page tokens, infinite scroll, sorting controls, free-text search, saved searches, status+limit combinations, browser URL-state persistence, background polling, cache warming, automatic refresh, or broad discovery. Those remain separate future contracts.

### Boundary 6 — Existing exact routes remain independent

The completed selector-free aggregate route and status-filter route remain their own bounded reads. Selecting the limit candidate does not change current query-rejection behavior and does not authorize fallback or composition among unfiltered, status-filtered, and limit-selected modes until Story 114.2 defines tests and implementation.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact API/dashboard route contract for `GET /v1/tasks?limit={task_list_limit}` only;
2. GET-only behavior, exactly one `limit` query key, and no request body;
3. accepted limit values are integers from 1 through 50 inclusive;
4. repeated/unknown/empty query keys, zero/negative/fractional/non-integer/out-of-range values, encoded nested parameters, request bodies, hashes, cookies, storage, hidden selectors, and status+limit combinations fail closed;
5. bounded task summary rows keep the existing aggregate list shape and route-local metadata;
6. no offset/cursor/page traversal, next-page token semantics, free-text search, multi-field filtering, arbitrary query language, sort controls, hidden discovery, or saved searches;
7. no automatic task detail, digest, history, trace, replay, session, or lifecycle traversal from returned rows;
8. no replay execution target-selection calls, lifecycle apply/prune/rollback, mutation/control behavior, generated live data, browser-side LLM behavior, workers/service workers, storage writes, background refresh, polling/timers, automatic retry, or cache warming;
9. invalid selector, empty result, stale/ambiguous freshness, backend unavailable, unauthorized/configuration failure, malformed response, over-limit response, unexpected keys, and route failure/read error render non-authoritative fail-closed copy;
10. source route, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata are visible;
11. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, digest-stream, aggregate task list, status-filter task list, session-list, and session-detail runtime-boundary tests remain green;
12. independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green are recorded before closure.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 114.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-27T18:56:38Z
