# Story 127.2 Context — API-local Task Search/Discovery Runtime Boundary

## Objective
Implement the API-local runtime boundary for the Story 127.1 search/discovery contract on `GET /v1/tasks`.

## Source-of-truth requirements
- Story 127.1 contract is approved and committed in `13b80a2`.
- Search route is bodyless and route-local: `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}`.
- Canonical query order: `field`, `op`, `q`, optional existing selectors in order `status`, `limit`, `offset`, `sort`.
- Reject hidden selectors, arbitrary grammar, encoded/repeated keys, row-derived ids, GET bodies, URL/hash/storage values, unsupported compositions, and broader fallback search.
- Return bounded rows and explicit metadata: selected search field/operator/query, selected selectors, freshness, authority, provenance, request/trace/correlation IDs, pagination, redaction/display state.

## Implementation target
- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`

## Stop condition
Story 127.2 tests and API runtime pass targeted verification, code review/QA evidence is written, and changes are committed as a single story commit.
