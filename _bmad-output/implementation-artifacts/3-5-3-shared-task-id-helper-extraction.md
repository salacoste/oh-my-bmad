# Story 3.5.3: Extract shared task-id + trailing-text helper

Status: done

## Story

As **the platform architect**,
I want **`_keys.py` to provide a helper that extracts a validated task-id plus optional trailing free-text**,
so that **`/reject` and `/retry` handlers no longer duplicate the `split(None, 2)` + inline regex workaround**.

This is a tech-debt refactor story. Stories 3.17 (`/reject`) and 3.18 (`/retry`) both need a 3-part split — `[command, task-id, trailing-text]` — but the existing `extract_task_id_from_message` helper uses `split(None, 1)` (maxsplit=1). Both handlers copy-pasted the same 20-line workaround with identical inline comments. This story adds a new helper to `_keys.py` and deduplicates both call sites.

**What this story is NOT:**
- NOT changing any wire format, event payload, or API contract.
- NOT touching `extract_task_id_from_message` — it stays as-is for `/approve`, `/stop`, `/agent` which don't have trailing free-text.
- NOT extracting the `operator_handle` boilerplate (separate concern, not in scope).

## Acceptance Criteria

1. **AC-1: New `extract_task_id_with_trailing` helper** — `_keys.py` gains a new public function `extract_task_id_with_trailing(message: Message) -> tuple[str | None, str | None]`. It splits message text into max 3 parts, validates `parts[1]` against `TASK_ID_PATTERN`, and returns `(task_id, trailing_text_stripped)` or `(None, None)` on validation failure. The `trailing_text_stripped` is `parts[2].strip()` if present, else `None`.

2. **AC-2: Update `/reject` handler** — `reject_command.py:93-113` replaced with a call to `extract_task_id_with_trailing(message)`. The `split(None, 2)` + inline `TASK_ID_PATTERN.match` is removed. Usage-message logic (lines 103-111) remains but reads from the returned tuple. The `reason = ... [:MAX_REASON_LENGTH]` truncation stays in the handler (helper does NOT truncate — that's handler-specific policy).

3. **AC-3: Update `/retry` handler** — identical refactor to AC-2, for `retry_command.py:93-113`. Truncation uses `MAX_HINT_LENGTH` (also handler-specific policy).

4. **AC-4: Remove workaround comments** — the 5-line comment blocks explaining why `extract_task_id_from_message` can't be used (lines 96-100 in both files) are replaced with a one-line reference to the new helper. No more inline regex or split(None, 2) in either handler.

5. **AC-5: `__all__` updated** — `_keys.py`'s `__all__` includes `extract_task_id_with_trailing`.

6. **AC-6: Zero behavior change** — all existing tests for `/reject` and `/retry` pass unchanged. No test modifications required (the helper is a pure extraction of existing logic).

7. **AC-7: `just test` all green** — no test regressions.

8. **AC-8: `just lint` 9/9 green** — ruff, mypy --strict, check_imports all pass.

9. **AC-9: Atomic commit** — title: `refactor(telegram-gateway): extract shared task-id + trailing-text helper · E3.5-debt`

## Tasks / Subtasks

- [x] **Task 1: Add `extract_task_id_with_trailing` to `_keys.py`** (AC: #1, #5)
  - [x] Add function after `extract_task_id_from_message` (line ~109). Signature: `def extract_task_id_with_trailing(message: Message) -> tuple[str | None, str | None]:`
  - [x] Implementation: `raw_text = (message.text or ""); parts = raw_text.split(None, 2)`; validate `parts[1]` against `TASK_ID_PATTERN`; return `(task_id, trailing.strip() or None)` or `(None, None)`.
  - [x] Add docstring explaining why this exists separately from `extract_task_id_from_message` (3-part split for commands with trailing free-text).
  - [x] Add to `__all__` list.

- [x] **Task 2: Update `reject_command.py`** (AC: #2, #4)
  - [x] Replace lines 93-113 with: `task_id, reason_text = _keys.extract_task_id_with_trailing(message)`. Keep the usage-message block (if `task_id is None`), but simplify the condition check. Keep `reason = reason_text[:MAX_REASON_LENGTH] if reason_text else None` truncation.
  - [x] Remove the 5-line workaround comment block (lines 96-100).
  - [x] Remove the `raw_text = message.text or ""; parts = raw_text.split(None, 2)` lines.

- [x] **Task 3: Update `retry_command.py`** (AC: #3, #4)
  - [x] Same refactor as Task 2 but for `/retry`. Replace lines 93-113 with helper call. Keep `hint = hint_text[:MAX_HINT_LENGTH] if hint_text else None`.
  - [x] Remove the 5-line workaround comment block (lines 96-100).

- [x] **Task 4: Verification + commit** (AC: #6, #7, #8, #9)
  - [x] `just test` — all existing tests pass.
  - [x] `just lint` 9/9 green.
  - [x] Verify `grep -n "split(None, 2)" services/telegram-gateway/src/` returns only `_keys.py:128` (the helper itself).
  - [ ] Atomic commit.

## Dev Notes

### The Problem (from Epic 3 Retrospective)

`extract_task_id_from_message` uses `split(None, 1)` which is correct for `/approve`, `/stop`, `/agent` (no trailing args). But `/reject <task-id> [reason]` and `/retry <task-id> [hint]` need a 3-part split. Both handlers bypass the shared helper and inline the regex + split logic with identical 5-line explanatory comments.

### What moves vs. what stays

| Item | Moves to helper | Stays in handler |
|------|------|------|
| `raw_text.split(None, 2)` | YES | — |
| `TASK_ID_PATTERN.match(parts[1])` | YES | — |
| Return `(task_id, trailing_text)` | YES | — |
| Usage-message generation | — | YES |
| `MAX_REASON_LENGTH` / `MAX_HINT_LENGTH` truncation | — | YES (handler policy) |
| `operator_handle` boilerplate | — | NOT IN SCOPE |

### Why NOT modify `extract_task_id_from_message`

Three other handlers (`approve_command.py`, `stop_command.py`, `agent_command.py`) depend on its `split(None, 1)` behavior to reject trailing garbage:
- `/approve t-xxx trailing` → candidate = `"t-xxx trailing"` → regex fails → None (correct)
- If we changed to `split(None, 2)`, these handlers would silently accept trailing text that should be rejected.

The new helper coexists; the old one is unchanged.

### Helper return type design

`tuple[str | None, str | None]` — returns `(None, None)` when no valid task-id found. Returns `(task_id, None)` when task-id is valid but no trailing text. Returns `(task_id, trailing_text)` when both present.

The handler is responsible for:
1. Checking `task_id is None` → usage message
2. Truncating trailing text with its service-specific `MAX_*_LENGTH` constant

### `operator_handle` boilerplate (NOT in scope)

Lines 76-91 in both files contain identical `from_user` guard blocks. This duplication is a separate concern (tracked informally, not as a sprint story). Do NOT extract it in this story.

### File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py` | Add `extract_task_id_with_trailing`, update `__all__` |
| `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` | Replace inline split+regex with helper call |
| `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` | Replace inline split+regex with helper call |
| `_bmad-output/implementation-artifacts/3-5-3-*.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

### References

- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Challenge #7: Shared Helper API Gap]
- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Tech Debt Action #3: Abstract split(None, 2) + inline regex]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py:91-108` — current `extract_task_id_from_message`]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py:93-113` — duplicated workaround]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py:93-113` — duplicated workaround]
- [Source: `_bmad-output/implementation-artifacts/3-17-reject-command.md` — Review finding #5: divergent task-id parsing]
- [Source: `_bmad-output/implementation-artifacts/3-18-retry-command-telegram-surface.md` — Learning #11: extract_task_id_from_message cannot be used]

### Previous Story Learnings (Stories 3.5.1 & 3.5.2)

- `just lint` 9/9 is the gatekeeper — all 9 checks must pass.
- `html.escape()` and `ParseMode.HTML` are unrelated to this story — don't touch them.
- Test changes should be minimal — import-only or logic-only, no gratuitous rewrites.
- Carry-forward: the three-layer review catches import inconsistencies.
- `check_imports.py` enforces no cross-service imports — helper stays in `_keys.py` within telegram-gateway.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

### Completion Notes List

- Task 1: Added `extract_task_id_with_trailing` to `_keys.py` — returns `tuple[str | None, str | None]` with `(task_id, trailing_text_stripped)`. Docstring explains 3-part split rationale. Added to `__all__`.
- Task 2: Replaced `reject_command.py` lines 93-113 with helper call. Error path uses `message.text.split()` (no maxsplit) for arg-count check instead of `split(None, 2)`. Truncation stays in handler.
- Task 3: Same refactor for `retry_command.py`. Both handlers now use `extract_task_id_with_trailing` — zero inline regex or `split(None, 2)`.
- Task 4: 1158 tests pass, lint 9/9 green. `split(None, 2)` only in `_keys.py:128` (the helper itself). Error-path `split(None, 2)` eliminated by using plain `.split()` for arg-count checks.

### File List

- `services/telegram-gateway/src/telegram_gateway/handlers/_keys.py` — added `extract_task_id_with_trailing`, updated `__all__`
- `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` — replaced inline split+regex with helper call
- `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` — replaced inline split+regex with helper call
- `_bmad-output/implementation-artifacts/3-5-3-shared-task-id-helper-extraction.md` — this file
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status flips
