# Story 3.5.6: Verify check_imports multi-tag noqa regex

Status: review

## Story

As **the platform engineer**,
I want **the `_NOQA_RE` regex in `scripts/checks/_common.py` verified to correctly support multi-tag noqa lines**,
so that **future stories (5.4+) that need combined suppressions like `# noqa: IMP001, SHELL001` don't silently lose coverage**.

This is a verification/close-out story from the Epic 3 retrospective. The original issue — the regex only captured the first tag in `# noqa: PLC0415, SHELL001` — was already fixed in commit `97d67ee` as part of Story 3.8 code-review finding H9. However, the fix was never formally tracked as a story, so this item remains in the sprint backlog. This story verifies the fix is complete, adds any missing test coverage, and closes the item.

**What this story IS:**
- A verification that the existing regex and fixture are correct.
- Adding any missing explicit unit tests for the `has_noqa()` helper if none exist.
- Updating sprint status to done.

**What this story is NOT:**
- NOT changing the regex (it's already correct).
- NOT adding new production code.
- NOT touching deferred-work.md items beyond closing this item.

## Acceptance Criteria

1. **AC-1: `_NOQA_RE` regex verified** — The regex at `scripts/checks/_common.py:58-61` correctly parses multi-tag noqa lines (e.g. `# noqa: PLC0415, IMP001 — reason`). Both tags are extracted and individually suppressable via `has_noqa()`.

2. **AC-2: Multi-tag fixture verified** — `scripts/checks/fixtures/imports/clean/multi_tag_noqa_service.py` passes as a clean fixture (zero violations) when `check_imports.py --self-test` runs.

3. **AC-3: Unit tests for `has_noqa()`** — If no dedicated unit tests exist for the `has_noqa()` helper in `_common.py`, add a small test file (`scripts/checks/test_common.py`) covering: single-tag match, multi-tag match, no-match, bare `# noqa:` without tags, and case-insensitive `noqa:` keyword.

4. **AC-4: `just lint` 9/9 green** — all lint gates pass.

5. **AC-5: `just test` unchanged** — no test count change (1161 passed, 5 skipped, 14 deselected).

6. **AC-6: Atomic commit** — title: `test(scripts): add unit tests for has_noqa multi-tag regex · E3.5-debt`

## Tasks / Subtasks

- [x] **Task 1: Verify existing regex and fixture** (AC: #1, #2)
  - [x] Read `scripts/checks/_common.py` — confirm `_NOQA_RE` captures comma-separated tags
  - [x] Read `scripts/checks/fixtures/imports/clean/multi_tag_noqa_service.py` — confirm it exercises the multi-tag path
  - [x] Run `uv run python scripts/check_imports.py --self-test` — confirm clean pass

- [x] **Task 2: Check for existing unit tests** (AC: #3)
  - [x] Search for `test_common.py` or any test file covering `has_noqa()`
  - [x] If tests exist: verify coverage of single-tag, multi-tag, no-match, bare-noqa, case-insensitive
  - [x] If tests don't exist: create `scripts/checks/test_common.py` with the 5 required cases

- [x] **Task 3: Verification + commit** (AC: #4, #5, #6)
  - [x] `just lint` 9/9 green
  - [x] `just test` — 1161 passed, 5 skipped, 14 deselected (unchanged unless new tests added, in which case count increases by the number of new tests)
  - [x] Atomic commit

## Dev Notes

### The fix is already in place

The `_NOQA_RE` regex was fixed in commit `97d67ee` ("fix(tests,scripts): apply story 3.8 code-review fixes"). The comment block at `scripts/checks/_common.py:42-57` (labeled "Story 3.8 review H9") documents the old bug and the fix.

The current regex:
```python
_NOQA_RE = re.compile(
    r"#\s*noqa:\s*([A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)\b\s+(\S+.*)",
    re.IGNORECASE,
)
```

This correctly captures comma-separated tags in group 1, and the `_TAG_RE` helper extracts individual tags:
```python
tags = {t.strip() for t in _TAG_RE.findall(m.group(1))}
```

### Why this story exists

The Epic 3 retrospective identified this as a tech-debt item before the code-review fix was discovered. The sprint-status.yaml entry says "fix _NOQA_RE regex to support multi-tag noqa lines before 5.4" — the "before 5.4" refers to Story 5.4 (claude-code-subprocess-supervision), where combined `IMP001 + SHELL001` suppressions will be needed. Since the fix is already in place, this story is about verification and formal closure.

### Self-test harness

`check_imports.py` has a `--self-test` mode (function `_self_test()` at line 287) that runs the checker against fixture files under `scripts/checks/fixtures/imports/`. Clean fixtures must produce zero violations; violation fixtures must produce exactly the expected violations. The multi-tag fixture is registered in `scripts/checks/fixtures/imports/clean/_meta.py`.

### File List

| File | Change |
|---|---|
| `scripts/checks/test_common.py` | New (if no existing tests) — unit tests for `has_noqa()` |
| `_bmad-output/implementation-artifacts/3-5-6-check-imports-noqa-multi-tag-fix.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `scripts/checks/_common.py:42-97` — `_NOQA_RE` regex and `has_noqa()` function]
- [Source: `scripts/checks/fixtures/imports/clean/multi_tag_noqa_service.py` — multi-tag regression fixture]
- [Source: `scripts/checks/fixtures/imports/clean/_meta.py` — fixture metadata]
- [Source: commit `97d67ee` — "fix(tests,scripts): apply story 3.8 code-review fixes"]
- [Source: `_bmad-output/planning-artifacts/epics.md` — Epic 3.5 description mentioning multi-tag fix]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None — verification/close-out story, no debug cycles needed.

### Completion Notes List

- `_NOQA_RE` regex verified: correctly captures comma-separated tags in group 1, `_TAG_RE` extracts individual tags for `has_noqa()` matching.
- Multi-tag fixture `multi_tag_noqa_service.py` passes clean (7 fixtures, 0 failures via `--self-test`).
- No existing unit tests for `has_noqa()` found. Created `scripts/checks/test_common.py` with 11 tests covering: single-tag match, single-tag no-match, multi-tag match (both tags), multi-tag no-match, bare noqa without reason, bare noqa without tags, no noqa at all, case-insensitive NOQA keyword, case-insensitive mixed case, and tag identifier case sensitivity.
- `just lint` 9/9 green. `just test` 1161 passed, 5 skipped, 14 deselected (unchanged — new tests in `scripts/` are outside pytest `testpaths`).
- Code review: 7 findings from Edge Case Hunter (all patch). Expanded test file from 11 to 18 tests: added three-tag coverage, reason-without-em-dash, trailing-whitespace strip, empty/whitespace-only input, double-noqa greedy capture documentation. Restructured into class-based groups with `sys.path` fixture for cleanup.

### File List

| File | Change |
|---|---|
| `scripts/checks/test_common.py` | New → expanded — 18 unit tests for has_noqa() (was 11) |
| `_bmad-output/implementation-artifacts/3-5-6-check-imports-noqa-multi-tag-fix.md` | Status → review, tasks checked off, dev agent record filled |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status → in-progress → review |
