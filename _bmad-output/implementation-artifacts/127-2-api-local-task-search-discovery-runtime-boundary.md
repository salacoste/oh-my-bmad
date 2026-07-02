# Story 127.2 — API-local Task Search/Discovery Runtime Boundary

Generated: 2026-07-02T13:46:00Z
Status: done — API-local runtime implementation.

## Implementation summary

Story 127.2 implements the Story 127.1 contract on `GET /v1/tasks` only. It adds bodyless API-local task search/discovery with exact raw query grammar:

`field={task_search_field}&op={task_search_operator}&q={task_search_query}`

Only these optional suffix families are accepted after `q`: no suffix, `status`, `limit`, `status&limit`, `limit&offset`, `status&limit&offset`, `sort`, and `status&limit&offset&sort`.

## Runtime boundaries

- Search fields/operators are exactly `task_id:eq`, `title:contains|prefix`, `status:eq`, `actor_id:eq|prefix`, `last_event_type:eq`, `updated_at:gte|lte`, and `created_at:gte|lte`.
- Raw `q` stays `1..96` ASCII bytes with per-field caps and timestamp semantic UTC parsing.
- Full raw search query strings are capped at `1..256` bytes.
- Percent encoding, `+`, raw spaces, slash/backslash, controls, Unicode/non-ASCII, empty values, repeated/encoded/reordered/extra keys, unsupported compositions, arbitrary grammar, and GET bodies fail closed.
- `field=status` plus any `status=` suffix fails closed as duplicate status semantics.
- `title` and `actor_id` substring/prefix matching uses literal SQL semantics; `_` does not become a wildcard.
- `last_event_type:eq` filters only the current `Task.last_event_id` event before pagination.
- Search responses return the existing bounded task summary rows only, plus selected field/op/query, selected suffix metadata, redaction state, freshness, authority, provenance, request/trace/correlation ids, and pagination metadata.

## Files changed

- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`
- `docs/api-contracts.md`
- `docs/feature-status.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md`

## Verification evidence

- `uv run pytest services/registry-api/src/registry_api/test_app.py -q -k 'GetTasksAggregate'` — 25 passed.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` — 76 passed.
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.

## Remaining deferred scope

Browser search controls, visible selector provenance, controlled traversal/infinite scroll, broad dashboard rewiring cleanup, lifecycle mutations, object storage retention, production ops/credentials/GitHub writes, split deployment, remote Postgres scaling, and DB connection mTLS remain deferred to later stories/epics.
