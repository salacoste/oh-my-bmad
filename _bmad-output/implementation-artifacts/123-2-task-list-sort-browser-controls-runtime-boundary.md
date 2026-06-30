# Story 123.2 — Task list sort browser controls runtime boundary

Generated: 2026-06-30T09:34:30Z

## Summary

Story 123.2 locally implements the bounded dashboard/browser singleton sort-control boundary selected by Story 123.1. The aggregate task-list dashboard now exposes visible sort controls for the existing singleton API-local route and an explicit sorted-read action.

## Implemented boundary

- Exact browser route: `GET /v1/tasks?sort=updated_at_desc_id_asc`.
- Sort vocabulary: singleton `updated_at_desc_id_asc` only.
- Runtime source: visible `aggregate-task-list-sort-control` only, plus visible `aggregate-task-list-sort-load` action.
- Request: GET, bodyless, `credentials: "omit"`.
- Response validation requires route `GET /v1/tasks?sort={task_sort}`, `selected_sort: "updated_at_desc_id_asc"`, freshness/authority/provenance/request/trace/correlation metadata, bounded row shape, integer count metadata, and `next_offset: null`.
- Sorted reads render in a separate singleton-sort metadata/result branch and leave status/limit/offset/manual previous-next state unchanged.

## Preserved boundaries

- Existing status/limit/offset and manual previous/next behavior remains separate and covered by existing tests.
- No backend/API behavior changes.
- No sort composition with status, limit, or offset.
- No broader sort vocabulary.
- No search/discovery, hidden selectors, automatic traversal, row-driven traversal, replay/lifecycle mutation, services/MCP/dependencies/CI/deployment changes, credentials, or production operations.

## Changed implementation files

- `dashboard/static/index.html`
- `dashboard/static/aggregate-task-list.js`
- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`
- dashboard guardrail tests that assert exact visible control inventories and approved control IDs.

## Verification

- `node --check dashboard/static/aggregate-task-list.js`
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → 12 passed
- `uv run pytest tests/dashboard -q` → 218 passed
