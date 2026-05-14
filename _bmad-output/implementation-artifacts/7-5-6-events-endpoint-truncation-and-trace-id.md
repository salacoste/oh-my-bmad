# Story 7.5.6: Events endpoint truncation + trace_id propagation

Status: done

## Story

As **a registry API consumer**,
I want **the events endpoint to return complete event histories without silent truncation and to clearly document the trace_id Phase 2 dependency**,
So that **I can reliably reconstruct task lifecycles without data loss and understand the correlation ID roadmap**.

During stories 7.5 and 7.8, two related issues were deferred:

1. **ASC+limit=1000 truncation** — The events endpoint returns events in ascending order with a hard limit of 1000. For tasks with >1000 events, the tail (newest events, including restart pairs) is silently dropped. The `since` cursor helps for follow-mode polling but cannot retrieve the full history for the initial request.

2. **trace_id hardcoded to None** — `_row_to_envelope` at `events.py:43` hardcodes `"trace_id": None` with a comment "Phase 1 reserved — no ORM column yet." The `Event` ORM model (`registry_state/schema.py:147-176`) has no `trace_id` column, so the endpoint genuinely cannot return it. Full propagation requires ORM schema changes + migration + materializer update, which is Phase 2 work. This story documents the dependency clearly and improves the endpoint comment.

## Acceptance Criteria

1. **AC-1: Cursor-based pagination** — The events endpoint accepts an `after` cursor parameter (monotonic_ns) that allows paginating through the complete event history without silent truncation. The existing `since` and `limit` parameters remain backward-compatible.
2. **AC-2: trace_id documentation** — The `_row_to_envelope` comment at `events.py:43` is updated to clearly document the Phase 2 dependency (ORM column + migration + materializer) required for trace_id propagation. The wire contract remains `trace_id: None` until Phase 2.
3. **AC-3: Tests** — Tests verify: (a) `after` cursor returns events after the specified monotonic_ns, (b) combining `after` + `limit` paginates correctly, (c) existing `since` and `limit` behavior is unchanged (regression guard).
4. **AC-4: Existing tests pass** — All existing events tests (`test_events.py`) and the full registry-api suite continue to pass. Ruff clean on modified files.

## Tasks / Subtasks

- [x] **Task 1: Add `after` cursor parameter to events endpoint** (AC: #1)
  - [x] In `services/registry-api/src/registry_api/routes/events.py`, add an `after` query parameter to `get_task_events`.
  - [x] Add the cursor filter: `stmt.where(Event.emitted_at_monotonic_ns > after)` after the `since` filter and before the limit.
  - [x] Update the docstring to document the `after` parameter.
  - [x] Update the debug log line to include `after`.

- [x] **Task 2: Update trace_id comment** (AC: #2)
  - [x] In `services/registry-api/src/registry_api/routes/events.py`, updated comment to document Phase 2 dependency: ORM column + migration + materializer update.

- [x] **Task 3: Add pagination tests** (AC: #3)
  - [x] Added `TestEventsCursorPagination` with 4 tests: after-cursor-returns-correct-events, after+limit-pagination, after-past-end-empty, since+after-combine.
  - [x] Added `TestTraceIdDocumentation` with 1 test: trace_id-is-none-awaiting-phase-2 regression guard.

- [x] **Task 4: Run full regression suite** (AC: #4)
  - [x] `uv run pytest services/registry-api/ -x -q` — 125 passed.
  - [x] `uv run ruff check` clean on all modified files.

## Dev Notes

### Origin and Context

Two deferred items from code reviews:

- **D2 from Story 7.5** — `trace_id` hardcoded to `None` in the events endpoint. The `Event` ORM model (`registry_state/schema.py:147-176`) has no `trace_id` column. The `EventEnvelope` Pydantic model (`packages/events/src/events/envelope.py:169`) supports `trace_id: str | None = None`. The JSONL event log layer supports trace_id round-trips. The gap is: (1) ORM column, (2) Alembic migration, (3) materializer extracting trace_id from JSONL to ORM. Full propagation is Phase 2 work — this story documents the dependency clearly.

- **D2 from Story 7.8** — ASC+limit=1000 truncation. The query at `events.py:67-72` uses `ORDER BY emitted_at ASC LIMIT N`. For tasks with >1000 events, only the oldest N events are returned. The CLI follow-mode uses `since` for forward polling, but the initial request (no `since`) is truncated. Adding an `after` cursor allows the client to paginate through the complete history.

### Key Files (exact paths + line numbers)

| File | Lines | What changes |
|------|-------|-------------|
| `services/registry-api/src/registry_api/routes/events.py` | 43 (trace_id comment), 52-56 (add `after` param), 73-74 (add cursor filter) | Cursor pagination, trace_id comment |
| `services/registry-api/src/registry_api/test_events.py` | TBD | Add `TestEventsCursorPagination` + `TestTraceIdDocumentation` |

### Architecture Compliance

- **Cursor design**: Using `emitted_at_monotonic_ns` as the cursor key because: (a) it's an indexed BIGINT column (efficient range queries), (b) it's monotonically increasing (no gaps from clock skew), (c) it's already part of the ORDER BY clause (`Event.emitted_at_monotonic_ns.asc()`). Using `event_id` as cursor is less ideal since UUIDs aren't naturally ordered.
- **Backward compatibility**: The `after` parameter is optional with default `None`. Existing requests without `after` behave identically to current behavior. The `since` parameter remains unchanged for timestamp-based filtering.
- **No schema changes**: This story does NOT add a `trace_id` column to the Event ORM model. That requires Phase 2 planning (migration, materializer, event writer coordination).
- **Wire contract**: The response remains a bare JSON array `[...]`. No wrapper object needed for pagination metadata — the client detects end-of-data when fewer than `limit` events are returned.

### Code Pattern to Follow

The existing test file uses `httpx.AsyncClient` + `ASGITransport` + `LifespanManager` for integration tests. The new pagination tests should use the existing `many_events_client` fixture (10 events seeded) or create a new fixture with more events.

The `_seed_task_and_events` helper already seeds events with sequential `emitted_at_monotonic_ns` values (`_FROZEN_MONO_NS + i * 1000`), making cursor values predictable:
```python
# Event 4's monotonic_ns: _FROZEN_MONO_NS + 4 * 1000 = 1_004_000
after = _FROZEN_MONO_NS + 4 * 1000
```

### Previous Story Intelligence (7.5.1–7.5.5)

- **Commit style**: `fix(registry-api): add cursor pagination to events endpoint + document trace_id Phase 2 dependency (Story 7.5.6)`.
- **Test at the appropriate layer**: For endpoint behavior (pagination, filtering), integration tests through the HTTP stack are appropriate (unlike unit-level tests for internal helpers).
- **Different service, different regression**: Story 7.5.6 modifies `registry-api`. Run `uv run pytest services/registry-api/ -x -q`. Current test count is ~119 tests (per 7.5.4 completion notes).
- **Ruff**: Watch for B008 (Query() in arg defaults) — existing code already has `# noqa: B008` on similar lines.

### References

- [Source: deferred-work.md — D2 (story 7.5), D2 (story 7.8)]
- [Source: epic-7-retro-2026-05-13.md — items 2 (LOW), 6 (LOW)]
- [Source: services/registry-api/src/registry_api/routes/events.py — lines 31-80]
- [Source: services/registry-api/src/registry_api/test_events.py — existing test patterns]
- [Source: services/registry-state/src/registry_state/schema.py — Event ORM model, lines 147-176]
- [Source: packages/events/src/events/envelope.py — EventEnvelope trace_id field, line 169]

## Dev Agent Record

### Implementation Plan

Add `after` (monotonic_ns) cursor parameter to events endpoint for complete pagination. Document trace_id Phase 2 dependency. Add 5 regression tests.

### Debug Log References

None — clean implementation, no issues encountered.

### Completion Notes

All 4 ACs met:
- AC-1: `after` cursor parameter added using `emitted_at_monotonic_ns`. Backward-compatible with existing `since` and `limit` parameters.
- AC-2: trace_id comment updated to document Phase 2 requirements (ORM column + migration + materializer).
- AC-3: `TestEventsCursorPagination` (4 tests) + `TestTraceIdDocumentation` (1 test) added.
- AC-4: 125 passed (was ~119, +6 new including existing events test count). Ruff clean.

### Review Findings

- [x] [Review][Patch] `json.loads(row.payload_json)` crashes on corrupt payload — entire endpoint returns 500 for one bad row. Fixed: added `try/except (json.JSONDecodeError, TypeError)` with `{"_raw": row.payload_json}` fallback. Added `TestCorruptPayloadDefense` test.
- [x] [Review][Patch] `after` parameter accepts negative values — `after=-1` causes nonsensical query. Fixed: added `ge=0` constraint to `Query()`. Added `TestAfterParameterValidation` test.
- [x] [Review][Patch] Docstring lacks `since` inclusivity semantics and `since`+`after` clock-divergence note. Fixed: expanded docstring to document inclusive `>=` for `since`, strict `>` for `after`, and clock-skew caveat.
- [x] [Review][Defer] Missing DB index for `after` cursor — `ix_events_task_id_emitted_at` doesn't cover `emitted_at_monotonic_ns`. Adding an index requires schema migration; deferred as Phase 2 work.
- [x] [Review][Defer] `since` uses inclusive `>=` creating potential duplicates on re-poll — pre-existing behavior; changing would break backward compat. Documented in docstring.
- [x] [Review][Defer] No auth check on events endpoint — by design for CLI use. Defer.
- [x] [Review][Defer] `trace_id: None` in wire contract — by design, documented as Phase 2 dependency. Defer.
- [x] [Review][Defer] `_TASK_ID_PATTERN` coupling between routes — pre-existing pattern. Defer.
- [x] [Review][Defer] `list[dict]` return type lacks response model — pre-existing pattern. Defer.
- [x] [Review][Dismiss] Uniform test spacing — cosmetic, no functional impact.

### File List

- `services/registry-api/src/registry_api/routes/events.py` — added `after` cursor parameter, cursor filter, updated docstring and debug log, improved trace_id comment, added `ge=0` guard, added `json.loads` defense
- `services/registry-api/src/registry_api/test_events.py` — added `TestEventsCursorPagination` (4 tests) + `TestTraceIdDocumentation` (1 test) + `TestCorruptPayloadDefense` (1 test) + `TestAfterParameterValidation` (1 test)

## Change Log

- 2026-05-13: Story created from deferred-work.md D2 (7.5) + D2 (7.8). Status: backlog.
- 2026-05-14: Comprehensive story created with cursor pagination design. Status: ready-for-dev.
- 2026-05-14: Implementation complete. Status: review.
- 2026-05-14: Code review — 3 patches applied, 6 deferred, 1 dismissed. Status: done.
