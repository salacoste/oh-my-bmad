# Story 7.5.4: Configurable Anthropic model + digest hardening

Status: done

## Story

As the operator,
I want the Anthropic model name configurable via environment variable and malformed ISO timestamps to produce a clear sentinel instead of silent `???`,
So that I can switch LLM models without code changes and malformed data is surfaced rather than silently garbled.

Two deferred items from Story 7.3 (logs-digest LLM adapter):
- The Anthropic model name is hardcoded as `claude-haiku-4-5-20251001` in the LLM digest adapter, requiring a code change to use a different model.
- When the digest renderer encounters a malformed ISO timestamp, it silently substitutes `???` — an ambiguous sentinel that could be confused with legitimate content.

## Acceptance Criteria

1. **AC-1: Configurable model name** — The Anthropic model name is read from the `ANTHROPIC_MODEL` environment variable. If unset, it defaults to `claude-haiku-4-5-20251001` (the current hardcoded value). No code change is required to switch models.
2. **AC-2: Malformed timestamp sentinel** — Malformed ISO timestamps in the digest produce `[invalid-timestamp]` instead of `???`. The sentinel is clearly distinguishable from legitimate content and indicates a data-quality issue.
3. **AC-3: Tests for both behaviors** — Tests verify: (a) the model name is configurable via env var and falls back to the default, (b) malformed timestamps produce `[invalid-timestamp]`.
4. **AC-4: Existing tests pass** — All existing digest tests (`test_digest.py`) and the full registry-api suite continue to pass.

## Tasks / Subtasks

- [x] **Task 1: Make model name configurable** (AC: #1)
  - [ ] In `services/registry-api/src/registry_api/adapters/llm_digest.py`, add `import os` at the top (after existing imports).
  - [ ] Replace the module-level constant `_MODEL = "claude-haiku-4-5-20251001"` (line 33) with a function that reads from the environment:
    ```python
    def _get_model() -> str:
        return os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    ```
  - [ ] Update line 136 (`model=_MODEL`) to `model=_get_model()`.
  - [ ] Rationale for function-over-constant: reading at call time (not module load time) makes `monkeypatch.setenv` work in tests without module reload. This matches the `os.environ.get("ANTHROPIC_API_KEY", "")` pattern in `app.py` line 227.

- [x] **Task 2: Fix malformed timestamp sentinel** (AC: #2)
  - [ ] In `services/registry-api/src/registry_api/adapters/llm_digest.py`, line 52, replace `"???"` with `"[invalid-timestamp]"`:
    ```python
    hhmm = ev.emitted_at_iso[11:16] if len(ev.emitted_at_iso) >= 16 else "[invalid-timestamp]"
    ```
  - [ ] Verify the output format `[[invalid-timestamp]]` (double brackets from the f-string `[{hhmm}]`) is acceptable — it clearly marks a malformed field while remaining visually consistent with the `[HH:MM]` format.

- [x] **Task 3: Add tests** (AC: #3)
  - [ ] Add a new test class `TestConfigurableModel` in `services/registry-api/src/registry_api/test_digest.py`:
    - `test_model_name_from_env_var` — set `ANTHROPIC_MODEL` via `monkeypatch`, call `_get_model()`, assert it returns the env var value.
    - `test_model_name_default_when_unset` — ensure `ANTHROPIC_MODEL` is unset, call `_get_model()`, assert it returns `"claude-haiku-4-5-20251001"`.
  - [ ] Add a new test class `TestMalformedTimestampSentinel`:
    - `test_format_event_invalid_timestamp` — create `EventRow` with `emitted_at_iso=""`, call `_format_event()`, assert output contains `[invalid-timestamp]` (not `???`).
    - `test_format_event_truncated_timestamp` — create `EventRow` with `emitted_at_iso="2026-01-01T10"` (too short), assert `[invalid-timestamp]`.
    - `test_format_event_valid_timestamp` — create `EventRow` with `emitted_at_iso="2026-01-01T10:30:00Z"`, assert `[10:30]` (regression guard).

- [x] **Task 4: Run full regression suite** (AC: #4)
  - [ ] `uv run pytest services/registry-api/ -x -q` passes.
  - [ ] `uv run ruff check` clean on all modified files.

## Dev Notes

### Origin and Context

Two deferred items from Story 7.3 (logs-digest LLM adapter):

- **Hardcoded model name** — `_MODEL = "claude-haiku-4-5-20251001"` at line 33 of `llm_digest.py`. The value is passed directly to `client.messages.create(model=_MODEL)` at line 136. No env-var override exists.
- **Silent `???` sentinel** — At line 52, when `emitted_at_iso` is shorter than 16 characters, the fallback `"???"` is ambiguous and could be mistaken for legitimate content. Replacing with `[invalid-timestamp]` makes the data-quality issue obvious.

### Key Files (exact paths + line numbers)

| File | Lines | What changes |
|------|-------|-------------|
| `services/registry-api/src/registry_api/adapters/llm_digest.py` | 12 (add `import os`), 33 (replace `_MODEL` constant), 52 (replace `"???"`), 136 (use `_get_model()`) | Env-var model, timestamp sentinel |
| `services/registry-api/src/registry_api/test_digest.py` | TBD | Add `TestConfigurableModel` + `TestMalformedTimestampSentinel` |

### Architecture Compliance

- **Env-var pattern**: `services/registry-api/src/registry_api/app.py` line 227 uses `os.environ.get("ANTHROPIC_API_KEY", "")` directly — follow the same pattern for consistency.
- **Module-level vs call-time**: Reading the env var inside `_get_model()` (called at request time) rather than at module import time ensures `monkeypatch.setenv` works in tests. This is the correct pattern when the value may change between test cases.
- **No schema changes**: This story touches only adapter logic. No Alembic migration, no ORM changes.
- **Fallback digest**: `_format_event()` is used by both the LLM path and the fallback path (`_build_fallback_digest`), so the sentinel fix covers both code paths.

### Code Pattern to Follow

The existing test file `test_digest.py` uses `httpx.AsyncClient` + `ASGITransport` + `LifespanManager` for integration tests. However, the new tests are unit-level (testing `_get_model()` and `_format_event()` directly), so they should follow the simpler pattern:

```python
from registry_api.adapters.llm_digest import _format_event, _get_model, EventRow

def test_model_name_from_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6-20250514")
    assert _get_model() == "claude-sonnet-4-6-20250514"
```

For `_format_event`, construct an `EventRow` directly:

```python
ev = EventRow(type="task.blocker_raised", emitted_at_iso="", payload_json='{}')
result = _format_event(ev)
assert "[invalid-timestamp]" in result
```

### Previous Story Intelligence (7.5.1, 7.5.2, 7.5.3)

- **Test at the appropriate layer**: For internal helpers like `_format_event` and `_get_model`, unit tests are the right granularity. Don't test through the full HTTP stack.
- **Different service, different regression**: Story 7.5.4 modifies `registry-api`. Run `uv run pytest services/registry-api/ -x -q`. Current test count is ~114 tests (per 7.5.2 completion notes).
- **Commit style**: `feat(registry-api): configurable Anthropic model + timestamp sentinel fix (Story 7.5.4)`.
- **Private function imports**: Testing `_format_event` and `_get_model` directly is fine — established pattern from 7.5.2 (testing `_close_active_session_for_task`) and 7.5.3 (testing `dispatch()` directly).

### References

- [Source: deferred-work.md (story 7.3)]
- [Source: epic-7-retro-2026-05-13.md — item 1 (MEDIUM) + item 8 (LOW)]
- [Source: services/registry-api/src/registry_api/adapters/llm_digest.py — lines 33, 52, 136]
- [Source: services/registry-api/src/registry_api/app.py — line 227 (env-var pattern)]
- [Source: services/registry-api/src/registry_api/test_digest.py — existing test patterns]

## Dev Agent Record

### Implementation Plan

### Debug Log References

### Completion Notes

All 4 ACs met:
- AC-1: `_MODEL` constant replaced with `_get_model()` function reading `ANTHROPIC_MODEL` env var.
- AC-2: `"???"` replaced with `"[invalid-timestamp]"` in `_format_event()`.
- AC-3: `TestConfigurableModel` (2 tests) + `TestMalformedTimestampSentinel` (3 tests) added.
- AC-4: 119 passed (was 114, +5 new), ruff clean.

### File List

- `services/registry-api/src/registry_api/adapters/llm_digest.py` — `_get_model()` env-var function, `[invalid-timestamp]` sentinel
- `services/registry-api/src/registry_api/test_digest.py` — `TestConfigurableModel` + `TestMalformedTimestampSentinel`

## Change Log

- 2026-05-13: Story created from deferred-work.md (story 7.3) + epic-7-retro. Status: ready-for-dev.
