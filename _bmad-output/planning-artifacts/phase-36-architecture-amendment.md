# Phase 36 Architecture Amendment — Task Status + Limit Composition Route Selection Planning

## Decision summary

Phase 36 may proceed from completed task-list-limit closure into the next narrow dashboard/API planning branch. This amendment selects:

- **Family:** read-only task-list bounded selector composition
- **Exact future candidate surface:** `GET /v1/tasks?status={task_status}&limit={task_list_limit}`
- **Allowed status selector domain:** `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`
- **Allowed limit selector domain:** an integer task_list_limit from 1 through 50 inclusive

Story 115.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, new selector vocabularies, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/114-3-phase-35-epic-114-final-closure.md`
- `_bmad-output/planning-artifacts/phase-35-epics.md`
- `docs/feature-status.md`
- `docs/api-contracts.md`
- `dashboard/static/aggregate-task-list.js`
- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`
- `services/registry-state/src/registry_state/schema.py`
- `.omx/context/phase-36-status-limit-composition-route-selection-20260628T013504Z.md`

## Current brownfield state

The platform already implements exact `GET /v1/tasks` as a selector-free bounded first page with server limit 50, `has_more`, and `next_offset: null`. Phase 34 implemented exact `GET /v1/tasks?status={task_status}` with one finite lifecycle status selector only. Phase 35 implemented exact `GET /v1/tasks?limit={task_list_limit}` with one bounded integer limit selector only. The route rejects GET bodies, extra query keys, repeated selector keys, unknown/empty status values, invalid limits, nested parameters, status+limit composition, free-text search, pagination, sorting, hidden selectors, traversal, replay execution, lifecycle mutation, and broad dashboard wiring.

## Route selection rationale

`GET /v1/tasks?status={task_status}&limit={task_list_limit}` is selected because it composes only two already-approved bounded selector vocabularies. It lets operators request a finite lifecycle subset with a bounded first-page row count while preserving the same task-summary row family and order. It is narrower than cursor/offset/page traversal, sorting, free-text search, arbitrary discovery, replay execution target selection, or lifecycle mutation planning. It can be tested as an additive route-local query contract without approving hidden selector discovery, adjacent route traversal, or pagination state.

## Architectural boundaries

### Boundary 1 — Story 115.1 is docs/status-only

Story 115.1 may create or update only Phase 36 planning artifacts, the Story 115.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 36 work may target only `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as a read-only first-page task-summary list filtered to one lifecycle status and bounded by one row-count selector. It may not silently include offset/cursor/page traversal, free-text search, arbitrary query language, sort controls, hidden discovery, automatic task/detail/digest/history/trace/replay/session traversal, replay execution jobs, lifecycle mutation controls, or broad dashboard live wiring.

### Boundary 3 — Two visible selectors, reused vocabularies only

Future dashboard/API calls must expose the selected status and selected limit visibly and send exactly one `status` query key plus exactly one `limit` query key. The status value must be one of the already-approved lifecycle statuses. The limit value must be an integer from 1 through 50 inclusive. Query strings with repeated keys, extra keys, empty values, unknown statuses, zero/negative/fractional/non-integer/out-of-range limits, encoded nested parameters, request bodies, URL hashes, cookies, local/session storage, hidden inputs, generated selectors, row-derived hidden attributes, and task-list discovery results are not approved selector sources.

### Boundary 4 — Query spelling is contract-owned

The approved candidate is named `GET /v1/tasks?status={task_status}&limit={task_list_limit}`. Story 115.2 must choose and document whether parsing is canonical-order-only or order-insensitive for the same two keys. Until that later test contract exists, planning artifacts must not imply acceptance of arbitrary query ordering, aliases, duplicate keys, or additional selector syntaxes.

### Boundary 5 — Bounded task summary row shape and order remain intact

Future implementation must preserve the current aggregate-task-list summary row shape and ordering (`updated_at DESC, id ASC`) unless a later planning gate changes them. The composition route may add visible `selected_status`, `selected_limit`, `returned_count`, `has_more`, freshness, and provenance metadata, but may not expose raw event payloads, logs, prompts, session internals, trace traversal payloads, filesystem/resource paths, hrefs/URLs, generated browser summaries, or control hints.

### Boundary 6 — No pagination, sorting, search, or broader selector composition pre-authorization

Selecting a status+limit route does not authorize offset/cursor/page traversal, next-page tokens, infinite scroll, sorting controls, free-text search, saved searches, status+limit+anything combinations, browser URL-state persistence, background polling, cache warming, automatic refresh, or broad discovery. Those remain separate future contracts.

### Boundary 7 — Existing exact routes remain independent

The completed selector-free aggregate route, status-filter route, and limit-selected route remain their own bounded reads. Selecting the composition candidate does not change current query-rejection behavior and does not authorize fallback or automatic mode switching among unfiltered, status-filtered, limit-selected, and status+limit modes until Story 115.2 defines tests and implementation.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact API/dashboard route contract for `GET /v1/tasks?status={task_status}&limit={task_list_limit}` only;
2. GET-only behavior, exactly one `status` query key, exactly one `limit` query key, and no request body;
3. accepted statuses are exactly `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, and `failed`;
4. accepted limit values are integers from 1 through 50 inclusive;
5. repeated/unknown/empty query keys, unknown statuses, zero/negative/fractional/non-integer/out-of-range limits, encoded nested parameters, request bodies, hashes, cookies, storage, and hidden selectors fail closed;
6. bounded task summary rows keep the existing aggregate list shape and route-local metadata, filtered by status before or with bounded limiting according to the documented implementation contract;
7. selector-free, status-only, and limit-only routes continue to work independently;
8. no offset/cursor/page traversal, next-page token semantics, free-text search, arbitrary filters beyond the two approved selectors, arbitrary query language, sort controls, hidden discovery, or saved searches;
9. no automatic task detail, digest, history, trace, replay, session, or lifecycle traversal from returned rows;
10. no replay execution target-selection calls, lifecycle apply/prune/rollback, mutation/control behavior, generated live data, browser-side LLM behavior, workers/service workers, storage writes, background refresh, polling/timers, automatic retry, or cache warming;
11. invalid selector, empty result, stale/ambiguous freshness, backend unavailable, unauthorized/configuration failure, malformed response, over-limit response, unexpected keys, and route failure/read error render non-authoritative fail-closed copy;
12. source route, selected status, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata are visible;
13. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, digest-stream, aggregate task list, status-filter task list, limit-selected task list, session-list, and session-detail runtime-boundary tests remain green;
14. independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green are recorded before closure.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 115.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-28T01:35:04Z
