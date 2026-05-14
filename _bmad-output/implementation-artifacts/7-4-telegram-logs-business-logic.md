# Story 7.4: Telegram `/logs` business logic

Status: done

## Story

As the operator,
I want Telegram `/logs <task-id>` to call `GET /v1/tasks/{id}/logs/digest` and return the digest text,
So that the Telegram surface story (3.15) has real business logic backing it.

## Acceptance Criteria

1. **Given** Story 3.15 delivered the Telegram surface
   **When** this story completes
   **Then** `/logs` returns LLM-digest content and existing tests exercise the happy path.

*Cites: FR5.*

## Tasks / Subtasks

- [x] Task 1 — Verify end-to-end wire-up (AC: #1)
  - [x] Confirm `logs_command.py` calls `registry_client.get_logs_digest()` which calls `GET /v1/tasks/{id}/logs/digest`.
  - [x] Confirm `LogsDigestResponseLocal` field names match `LogsDigestResponse` from Story 7.3.
  - [x] Run existing test suite: `uv run pytest services/telegram-gateway/src/telegram_gateway/test_logs_command.py` passes (19 tests).

- [x] Task 2 — Update stale docstrings (AC: #1)
  - [x] Update `logs_command.py` module docstring — remove "does NOT exist yet" placeholder text.
  - [x] Update `registry_client.py:get_logs_digest()` docstring — remove "does NOT exist server-side yet".
  - [x] Remove `TODO(story-7.3)` from `LogsDigestResponseLocal` docstring.

- [x] Task 3 — Run full regression suite (AC: #1)
  - [x] `uv run pytest services/telegram-gateway/ -x -q` passes.
  - [x] `uv run pytest services/registry-api/ -x -q` passes.
  - [x] `ruff check` clean on all modified files.

## Dev Notes

### Architecture: What This Story Does

This is a **verification-only story**. The Telegram handler (`logs_command.py`) was fully built in Story 3.15 — it already calls `registry_client.get_logs_digest()`, renders the digest with HTML escaping, handles truncation, and has comprehensive error handling for 404/5xx/network errors. Story 7.3 built the server-side endpoint (`GET /v1/tasks/{id}/logs/digest`). This story confirms the wire-up works end-to-end.

The only code changes are docstring updates to remove stale "not yet implemented" comments.

### Critical Architecture Constraints

1. **No new functionality**: The handler, client, and tests are already complete.
2. **Wire contract**: `LogsDigestResponseLocal` fields already match `LogsDigestResponse` (verified in Story 7.3 review).
3. **19 existing tests**: Already cover happy path, truncation, 404 placeholder, network errors, HTML escaping, malformed responses, and router factory.

### Relationship to Other Stories

- **Story 3.15** (logs-command-telegram-surface): Created the complete Telegram handler, router, error branches, and digest rendering.
- **Story 7.3** (logs-digest-llm-adapter): Created the server-side `GET /v1/tasks/{id}/logs/digest` endpoint. This story verifies the connection.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story7.4]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py — complete handler]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py — get_logs_digest(), LogsDigestResponseLocal]
- [Source: services/telegram-gateway/src/telegram_gateway/test_logs_command.py — 19 existing tests]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

No debug cycles needed — verification-only story.

### Completion Notes List

- Verified end-to-end wire-up: `handle_logs` → `registry_client.get_logs_digest()` → `GET /v1/tasks/{id}/logs/digest` → `LogsDigestResponse` → rendered as Telegram message.
- Wire contract confirmed: `LogsDigestResponseLocal` fields (task_id, digest, truncated, line_count) match `LogsDigestResponse` from Story 7.3.
- Updated 3 stale docstrings: removed "does NOT exist yet" comments in logs_command.py and registry_client.py, removed TODO(story-7.3).
- All 19 existing logs tests pass, all 366 telegram-gateway tests pass, ruff clean.

### File List

- services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py — updated module docstring (removed stale placeholder text)
- services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py — updated get_logs_digest() and LogsDigestResponseLocal docstrings (removed stale TODO/comments)
