# Story 6.8: Pre-commit validation hook (FR39)

Status: done

## Story

As the platform,
I want `packages/secret-hygiene/` to provide a pre-commit hook that blocks (a) changes to `.env*`/`secrets/`/`*.pem`/`*.key`/`*.credentials*`, (b) worktree-boundary violations (writes outside the assigned worktree), (c) commit-message injection patterns (null bytes, command substitution),
So that Tier-3 stays reserved for truly irreversible operations and cheap-to-catch violations are caught at commit time.

## Acceptance Criteria

1. **Given** an agent attempts to commit `.env`
   **When** the pre-commit hook runs
   **Then** the commit is blocked with `Refusing to commit sensitive path: .env`.

2. **And Given** an agent attempts to commit a file outside the assigned worktree
   **When** the hook runs
   **Then** it blocks with a clear message naming the violated boundary.

*Cites: FR39.*

## Tasks / Subtasks

- [x] Task 1 — Sensitive path checker module (AC: #1)
  - [x] Create `packages/secret-hygiene/src/secret_hygiene/path_checks.py`
  - [x] Define `SENSITIVE_PATH_PATTERNS: list[re.Pattern]` matching: `.env*`, `secrets/`, `*.pem`, `*.key`, `*.credentials*`
  - [x] Implement `check_sensitive_paths(staged_files: list[str]) -> list[Violation]`
  - [x] Each `Violation` dataclass: `file_path`, `rule`, `message`
  - [x] Write co-located tests in `test_path_checks.py` (9 tests)
- [x] Task 2 — Worktree boundary checker (AC: #2)
  - [x] Add `check_worktree_boundary(staged_files: list[str], worktree_root: Path) -> list[Violation]` to `path_checks.py`
  - [x] Resolve each staged file path; verify `resolve(file).is_relative_to(resolve(worktree_root))`
  - [x] Handle symlinks that escape the worktree
  - [x] Write co-located tests (6 tests)
- [x] Task 3 — Commit-message injection checker
  - [x] Add `check_commit_message(msg_path: Path) -> list[Violation]` to `path_checks.py`
  - [x] Block: null bytes (`\x00`), command substitution patterns (`` `...` ``, `$(...)`)
  - [x] This runs as a `commit-msg` hook (separate from `pre-commit`)
  - [x] Write co-located tests (7 tests)
- [x] Task 4 — Extend `precommit_hook.py` main entrypoint (AC: #1, #2)
  - [x] Import and call `check_sensitive_paths()` from `path_checks` in `main()`
  - [x] Import and call `check_worktree_boundary()` — worktree root from `--worktree-root` CLI arg (defaults to `Path.cwd()`)
  - [x] Print violations to stderr, exit 1 if any found
  - [x] Update `pyproject.toml` to add `secret-hygiene-commit-msg` entrypoint for the commit-msg hook
  - [x] Update existing tests that expect clean exit — they may need worktree-root awareness
- [x] Task 5 — Update `.pre-commit-config.yaml` (AC: #1, #2)
  - [x] Add `commit-msg` hook entry wiring `secret-hygiene-commit-msg`
  - [x] The existing `secret-hygiene-precommit` hook continues unchanged (content scan)
- [x] Task 6 — Integration / regression
  - [x] All existing tests pass (`pytest packages/secret-hygiene/` — 148 passed)
  - [x] `ruff check` clean
  - [x] New test count: 31 (22 in test_path_checks + 9 in test_precommit_hook)

## Dev Notes

### Key Insight

This story EXTENDS the existing `secret-hygiene` package. Do NOT create a new package or duplicate the existing secret-pattern scanning. The existing `precommit_hook.py` scans file **content** for secret patterns — this story adds **path-based** checks and **commit-message** checks, which are orthogonal.

### Existing Code to Build On

| File | What it does | What this story adds |
|------|-------------|---------------------|
| `precommit_hook.py` | Content-based secret scanning via `scanner.scan_file()` | Calls new `check_sensitive_paths()` + `check_worktree_boundary()` |
| `scanner.py` | `SECRET_PATTERNS`, `scan_text()`, `scan_file()` | Not modified — content scanning stays as-is |
| `sanitizer.py` | structlog processor `redact_secrets()` | Not modified |
| `.pre-commit-config.yaml` | Wires `secret-hygiene-precommit` hook | Add `commit-msg` hook for message injection check |

### Architecture

```
pre-commit hook (pre-commit stage):
  1. Content scan (existing — scanner.scan_file on each staged file)
  2. Sensitive path check (NEW — check file paths against SENSITIVE_PATH_PATTERNS)
  3. Worktree boundary check (NEW — resolve paths, verify within worktree root)

commit-msg hook (commit-msg stage):
  4. Commit message injection check (NEW — null bytes, command substitution)
```

### Sensitive Path Patterns

```python
SENSITIVE_PATH_PATTERNS = [
    (re.compile(r"^\.env($|.*)"), ".env files"),
    (re.compile(r"^secrets/"), "secrets/ directory"),
    (re.compile(r"\.pem$"), "PEM certificate/key files"),
    (re.compile(r"\.key$"), "Key files"),
    (re.compile(r"\.credentials"), "Credential files"),
]
```

Use `fnmatch` or `PurePosixPath.match` for portability. Patterns match against the staged-file path relative to repo root.

### Worktree Boundary Check

- Hook runs with `cwd = worktree_root` (git worktree)
- Default `--worktree-root` = `Path.cwd()`
- For each staged file: `resolve(Path(file)).is_relative_to(resolve(worktree_root))`
- Symlinks: resolve the symlink target too; if it points outside, flag it
- This catches the edge case where Claude Code creates a symlink pointing outside the worktree and then commits the target

### Commit-Message Injection Check

Runs as a separate `commit-msg` hook (git provides the commit message file as `$1`).

Blocked patterns:
- Null byte (`\x00`)
- Backtick command substitution: `` `...` ``
- `$(...)` command substitution
- `!\n` line-split injection (git trailer exploit)

### Relationship to Previous Stories

- **Story 1.7** (secret-scanner-sanitizer): Created the `secret-hygiene` package with content scanning. This story extends it with path/message checks.
- **Story 6.7** (worker-approval-wait-state): Worker emits `task.awaiting_approval` for Tier-3 actions. This story catches cheaper violations BEFORE they reach Tier-3.
- **Story 5.4** (claude-code-subprocess-supervision): Worker spawns Claude Code with `cwd=worktree_path`. The pre-commit hook runs inside that worktree.

### structlog Gotcha

Never use `event=` as a keyword argument to structlog loggers — it clashes with the positional `event` parameter.

### Scope Boundary

Do NOT modify:
- `services/worker-wrapper/` (worker doesn't install hooks — that's a future story)
- `packages/secret-hygiene/src/secret_hygiene/scanner.py`
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py`
- `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`

### References

- [Source: _bmad-output/planning-artifacts/prd.md#FR39]
- [Source: _bmad-output/planning-artifacts/epics.md#Epic6-Story6.8]
- [Source: _bmad-output/planning-artifacts/architecture.md#Precommit-Validation]
- [Source: _bmad-output/implementation-artifacts/1-7-secret-scanner-sanitizer.md]
- [Source: packages/secret-hygiene/src/secret_hygiene/precommit_hook.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- Task 1: Created `path_checks.py` with `Violation` dataclass and `check_sensitive_paths()` — 5 regex patterns, 9 tests
- Task 2: Added `check_worktree_boundary()` with symlink resolution — 6 tests
- Task 3: Added `check_commit_message()` for null bytes + command substitution — 7 tests
- Task 4: Extended `precommit_hook.py` main() with path+boundary checks, `--worktree-root` CLI arg, `commit_msg_main()` entrypoint. Updated `pyproject.toml` with `secret-hygiene-commit-msg` console script. Fixed 7 existing tests to pass `--worktree-root`. Added 9 integration tests
- Task 5: Updated `.pre-commit-config.yaml` with `commit-msg` stage hook
- Task 6: 148 tests pass (31 new), ruff clean
- Code review fixes (15 issues across 3 reviewers):
  - Removed backtick pattern (false positives on Markdown inline code)
  - Restructured main() to collect all violations before returning (both content + path violations reported)
  - Added `list[Violation]` type annotation
  - Fixed TOCTOU in worktree boundary check (resolve first, then check existence)
  - Harmonized `$()` quantifier to `+` (requires content, empty parens not flagged)
  - Fixed fragile `test_outside_worktree_blocked` test
  - Added 28 new tests: negative path cases (11), worktree edge cases (5), commit message edge cases (7), integration tests (5)
  - Final: 176 tests pass, ruff clean

### File List

- `packages/secret-hygiene/src/secret_hygiene/path_checks.py` — CREATE — Violation dataclass, check_sensitive_paths, check_worktree_boundary, check_commit_message
- `packages/secret-hygiene/src/secret_hygiene/test_path_checks.py` — CREATE — 46 unit tests
- `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py` — MODIFY — import path_checks, add --worktree-root arg, wire checks into main(), add commit_msg_main() entrypoint
- `packages/secret-hygiene/src/secret_hygiene/test_precommit_hook.py` — MODIFY — fix 7 existing tests with --worktree-root, add 17 integration tests
- `packages/secret-hygiene/pyproject.toml` — MODIFY — add secret-hygiene-commit-msg console script
- `.pre-commit-config.yaml` — MODIFY — add commit-msg hook entry
