# Phase 37 Architecture Amendment — Status + Limit Browser Consumption Boundary

Generated: 2026-06-28T17:26:00Z

## Decision

Phase 37 may proceed from completed Phase 36 backend/API status+limit closure into one dashboard browser-consumption planning branch:

- **Family:** read-only aggregate task-list status+limit browser consumption.
- **Exact future candidate surface:** dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- **Selector source:** visible aggregate-task-list panel controls only.
- **Query order:** exact `status` key followed by exact `limit` key.

Story 116.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, pagination, sorting, search, arbitrary discovery, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Brownfield context

The platform now has these bounded task-list read contracts:

1. `GET /v1/tasks` — selector-free bounded first page with fixed server limit 50.
2. `GET /v1/tasks?status={task_status}` — one finite lifecycle status selector only.
3. `GET /v1/tasks?limit={task_list_limit}` — one bounded integer limit selector only.
4. `GET /v1/tasks?status={task_status}&limit={task_list_limit}` — exact canonical status+limit composition with status-first query order, selected-status/selected-limit metadata, bounded rows, and fail-closed rejection of reversed/extra/repeated/nested/invalid selectors.

`dashboard/static/aggregate-task-list.js` currently consumes selector-free `GET /v1/tasks` only. Phase 37 does not change that runtime. It selects the next smallest future browser/runtime boundary: consuming the completed status+limit API route from visible aggregate-task-list controls while preserving the existing route-local contracts.

## Architectural boundary for future Story 116.2

Future Story 116.2, if authorized, must keep the implementation route-local and browser-local:

1. Add no backend/API route beyond the already implemented canonical status+limit endpoint.
2. Build the request from visible aggregate-task-list status and limit controls only.
3. Issue GET with no body and `credentials: "omit"`.
4. Construct only `/v1/tasks?status=<allowed-status>&limit=<allowed-limit>` with canonical status-then-limit order.
5. Validate response metadata for exact route identity, `selected_status`, `selected_limit`, retrieved_at/freshness, authority/provenance, returned_count, has_more, and row shape before rendering authoritative rows.
6. Preserve existing selector-free, status-only, limit-only, and status+limit backend/API contract tests.
7. Keep selector-free aggregate task-list display behavior explicitly tested if the future implementation preserves both modes; if the future implementation intentionally promotes status+limit as the panel's primary mode, that promotion must be explicit in tests and docs and must not imply pagination/search/discovery.
8. Do not add URL hash/query-state persistence, cookies, local/session storage, service workers, workers, timers, polling, automatic refresh, automatic retry, generated selector values, hidden inputs, task-row-derived selectors, adjacent route calls, or mutation/control calls.

## Required future test obligations

Future Story 116.2 tests must fail closed for:

- missing status control, missing limit control, or missing explicit apply/init mechanism;
- status outside `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`;
- limit outside integer 1 through 50 inclusive, including empty, zero, negative, fractional, non-integer, Unicode digit, encoded digit, and very large values;
- query order not `status` then `limit`;
- extra/repeated query keys, nested parameters, URL hash/query-state persistence, cookies, local/session storage, row-derived selectors, generated selectors, background refresh, polling/timers, workers, automatic retry, or adjacent route calls;
- response route mismatch, selected-status mismatch, selected-limit mismatch, missing freshness/provenance, over-limit item count, unexpected keys, stale/ambiguous freshness, backend unavailable, unauthorized/configuration failure, non-2xx, malformed JSON, and network errors.

## Deferred surfaces

The following remain fail-closed until a later explicit planning gate selects one exact mechanism:

- offset/cursor/page traversal, next-page tokens, infinite scroll, and pagination state;
- sorting controls;
- free-text task search, arbitrary discovery, multi-field filtering beyond status+limit, arbitrary query language, and saved searches;
- status+limit+anything combinations or any other selector composition;
- automatic task/detail/digest/history/trace/replay/session drill-down from returned rows;
- replay execution target selection;
- lifecycle apply/prune/rollback, snapshot deletion/restore, archive/manifest mutation, and other mutation/control behavior;
- broad dashboard wiring or dashboard-wide mode switching;
- browser-side LLM generation, prompt construction, summarization, generated live data, cache warming, polling, timers, background workers, automatic retry, and automatic refresh;
- services/MCP/dependencies/CI/deployment modifications unless a separate explicit planning gate authorizes that scope;
- production credentials and production operations.

## Architecture acceptance criteria

1. The future candidate is exact dashboard aggregate-task-list consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}` only.
2. Future selectors come only from visible aggregate-task-list controls and finite/bounded vocabularies.
3. Future request construction is GET-only, bodyless, credentials-omitted, canonical status-then-limit order, and no extra/repeated query keys.
4. Future rendering exposes source route, selected status, selected limit, retrieved_at, freshness, authority/provenance, request/trace/correlation id where available, returned_count/has_more, and degraded-state metadata.
5. Future tests preserve all completed backend/API task-list route contracts and add fail-closed browser tests for the selected composition-consumption path.
6. This planning story changes docs/status only.
