# Story 3.5.1: Backport `approve_command.py` double-@ fix

Status: ready-for-dev

## Story

As **the operator**,
I want **the `/approve` command to render my handle correctly when my Telegram profile is incomplete**,
so that **I see `@operator` in the reply instead of `@@operator`**.

This is a bug-backport story. Story 3.4 (`approve_command.py`) shipped with a double-`@` rendering bug when `from_user` is `None`. Story 3.16 (`stop_command.py`) established the fix pattern, but the original handler was never updated. Every subsequent handler (`/stop`, `/reject`, `/retry`, `/agent`) uses the correct pattern. This story brings `/approve` into alignment.

**What this story is NOT:**
- NOT a new feature — zero new behavior.
- NOT a handler rewrite — one-line production fix + test tightening.
- NOT touching other handlers — only `approve_command.py` has the bug.

## Acceptance Criteria

1. **AC-1: Fix the double-@ bug** — in `approve_command.py`, the `from_user is None` branch sets `operator_handle = "@operator"` but the f-string `@{operator_handle}` prepends another `@`, producing `@@operator`. Change to `operator_handle = "operator"` (no `@` prefix), matching the pattern in `stop_command.py` line 85, `reject_command.py` line 91, `retry_command.py` line 91, and `agent_command.py`.

2. **AC-2: Tighten existing test** — the test `test_handle_approve_handles_null_from_user_with_valid_task_id` currently passes despite the bug because `assert "@operator" in reply_text` matches the substring inside `"@@operator"`. Add `assert "@@operator" not in reply_text` to the existing test.

3. **AC-3: Add dedicated double-@ regression test** — new test `test_handle_approve_from_user_none_no_double_at` that:
   - Sets `msg.from_user = None`
   - Calls `handle_approve`
   - Asserts `"@operator"` is in the reply
   - Asserts `"@@"` is NOT in the reply
   - Asserts `operator_actor_id` in the captured decision call is `"unknown"`

4. **AC-4: No other handler regressions** — verify that `stop_command.py`, `reject_command.py`, `retry_command.py`, and `agent_command.py` all already use the correct `operator_handle = "operator"` pattern (no fix needed, just confirmation).

5. **AC-5: `just lint` 9/9 green**.

6. **AC-6: All existing tests still pass** — the test tightening in AC-2 will fail before the fix is applied and pass after. No other test behavior changes.

7. **AC-7: Atomic commit** — title: `fix(telegram-gateway): backport operator_handle double-@ fix into approve_command.py · E3.5-debt`

## Tasks / Subtasks

- [ ] **Task 1: Fix the production bug** (AC: #1, #4)
  - [ ] In `approve_command.py`, change `operator_handle = "@operator"` to `operator_handle = "operator"` in the `else` branch (when `from_user is None`).
  - [ ] Grep all other handlers to confirm they already use the correct pattern (no `@` prefix).

- [ ] **Task 2: Tighten tests + add regression test** (AC: #2, #3)
  - [ ] Add `assert "@@operator" not in reply_text` to `test_handle_approve_handles_null_from_user_with_valid_task_id`.
  - [ ] Add new test `test_handle_approve_from_user_none_no_double_at` with explicit double-@ assertion and `operator_actor_id == "unknown"` check.

- [ ] **Task 3: Verification + atomic commit** (AC: #5, #6, #7)
  - [ ] `just test` — all existing tests pass (the tightened test now validates the fix).
  - [ ] `just lint` 9/9 green.
  - [ ] Atomic commit.

## Dev Notes

### The Bug

`approve_command.py` line 100:
```python
# BUGGY — the @ prefix is baked into the handle
operator_handle = "@operator"
```

Then the f-string on lines 182/185:
```python
f"✅ Approved by @{operator_handle} at {decided_at_iso}..."
```

Result: `"Approved by @@operator at ..."` — visible double-@ in the Telegram reply.

### The Fix (from stop_command.py line 85)

```python
# CORRECT — no @ prefix; the f-string adds it
operator_handle = "operator"
```

### Why Tests Didn't Catch It

`test_approve_command.py` line ~621:
```python
assert "@operator" in reply_text
```

`"@operator"` is a substring of `"@@operator"`, so the assertion passes. The test needs `assert "@@operator" not in reply_text` to actually validate correct rendering.

### Other Handlers (Already Correct)

| Handler | Line | Value | Status |
|---------|------|-------|--------|
| `approve_command.py` | 100 | `"@operator"` | BUGGY (this story) |
| `stop_command.py` | 85 | `"operator"` | Correct |
| `reject_command.py` | 91 | `"operator"` | Correct |
| `retry_command.py` | 91 | `"operator"` | Correct |
| `agent_command.py` | — | `"operator"` | Correct |

Only `approve_command.py` has the bug. All four subsequent handlers use the correct pattern established in Story 3.16's code-review fix.

### Carry-Forwards from Stories 3.16-3.19 Code Reviews

| Carry-forward | Source | How 3.5.1 uses it |
|---|---|---|
| `html.escape()` on all externally-sourced strings | Story 3.5+ | No change — already correct in approve_command.py |
| `operator_handle` computed after task-id validation | Story 3.19 fix #5 | No change — bug is in the value, not the placement |
| Test all 5 error-catch branches from the start | Stories 3.3-3.4 | No new error branches — just tightening existing tests |
| `@@` double-prefix prevention | Story 3.16 fix #3 | This IS that fix, backported |

### Predicted File List

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` | 1-line fix: `"@operator"` → `"operator"` |
| `services/telegram-gateway/src/telegram_gateway/test_approve_command.py` | Tighten existing test + add 1 new test |
| `_bmad-output/implementation-artifacts/3-5-1-approve-command-double-at-backport.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
