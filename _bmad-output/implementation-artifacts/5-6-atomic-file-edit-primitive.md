# Story 5.6: Atomic file-edit primitive

Status: review

## Story

As the worker,
I want a higher-level atomic file-edit primitive that wraps the existing POSIX `atomic_write_bytes` with Claude Code Edit/Write tool semantics (input validation, string replacement, secret scanning),
So that file operations during task execution and crash recovery are atomic, validated, and secret-sanitized — and mid-write host interruption leaves the filesystem in a consistent state (FR30, NFR-R2).

## Acceptance Criteria

1. **AC-1: Higher-level edit function** — `apply_file_edit(target, old_string, new_string, *, replace_all=False, session_id="") -> FileEditResult` reads the target file, validates `old_string` exists (exactly once unless `replace_all=True`), applies the replacement, scans the result for secrets, and writes atomically via `atomic_write_bytes`. Returns a structured result.

2. **AC-2: Higher-level write function** — `apply_file_write(target, content, *, session_id="") -> FileEditResult` creates parent directories if needed, scans content for secrets, and writes atomically via `atomic_write_text`. Returns a structured result.

3. **AC-3: Validation helper** — `validate_edit(old_content, old_string, new_string, *, replace_all=False) -> EditValidation` is a pure function (no IO) that validates the edit parameters: `old_string` is non-empty, found in `old_content` (exactly once unless `replace_all`), and returns match count and positions. This is testable without touching the filesystem.

4. **AC-4: Secret scanning** — Both `apply_file_edit` and `apply_file_write` scan content through `secret_hygiene.scanner.scan_text()` before writing. If secrets are detected, the write is ABORTED (not suppressed — the file is not written) and the result records `secrets_detected=True` with the match details. This differs from reasoning breadcrumbs (which suppress text but still emit) because file writes with secrets must not reach the filesystem.

5. **AC-5: FileEditResult dataclass** — A result dataclass with fields: `target_path: str`, `success: bool`, `lines_added: int`, `lines_removed: int`, `secrets_detected: bool`, `secret_matches: list[str] | None`, `error: str | None`.

6. **AC-6: EditValidation dataclass** — A validation result with fields: `valid: bool`, `match_count: int`, `error: str | None`.

7. **AC-7: Atomicity verification** — The existing write-interrupt harness from Story 2.12 (`tests/crash-injection/_atomic_edit_runner.py`) passes when run against `apply_file_write`. This confirms the higher-level function preserves the atomicity guarantee of the underlying `atomic_write_bytes`.

8. **AC-8: Payload model registered** — A `FileEditedPayload` Pydantic model is defined in `packages/events/src/events/payloads.py` and registered in the schema registry for `file.edited` v1.0.0. Fields: `session_id`, `file_path`, `tool_name` (Literal["Write", "Edit"]), `lines_added`, `lines_removed`, `secrets_detected`. `scripts/check_event_registry.py` exits 0.

9. **AC-9: Domain module location** — New functions live in `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py` (extending the existing module, not a new file). The existing `atomic_write_bytes` and `atomic_write_text` remain unchanged.

10. **AC-10: NFR-O1 compliance** — `scripts/check_imports.py` exits 0. The domain module uses only stdlib (`os`, `pathlib`, `logging`, `dataclasses`, `typing`) and `packages/` imports (`secret_hygiene.scanner`). No `structlog`, no `asyncio`, no framework imports.

11. **AC-11: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

12. **AC-12: Tests** — At least 15 new tests covering: `validate_edit` (valid single match, no match, multiple matches, replace_all, empty old_string), `apply_file_edit` (happy path, file not found, old_string not found, multiple matches error, secret detection abort), `apply_file_write` (happy path, parent dir creation, secret detection abort, large file), atomicity via write-interrupt harness, schema registry registration.

13. **AC-13: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

14. **AC-14: Atomic commit** — title: `feat(worker-wrapper): add apply_file_edit/write with secret scanning · E5`

## Tasks / Subtasks

- [x] **Task 1: Define EditValidation + FileEditResult** (AC: #5, #6)
  - [x] Add `EditValidation` dataclass to `domain/atomic_edit.py`
  - [x] Add `FileEditResult` dataclass to `domain/atomic_edit.py`
  - [x] Add to `__all__`

- [x] **Task 2: Implement `validate_edit`** (AC: #3)
  - [x] Pure function: `validate_edit(old_content, old_string, new_string, *, replace_all=False) -> EditValidation`
  - [x] Validates: `old_string` non-empty, found in `old_content`, exactly one match unless `replace_all`
  - [x] Returns match count and error message

- [x] **Task 3: Implement `apply_file_edit`** (AC: #1, #4)
  - [x] Read target file via `pathlib.Path.read_text()`
  - [x] Call `validate_edit` — return error result if invalid
  - [x] Apply replacement
  - [x] Scan result via `scan_text()` — abort if secrets detected
  - [x] Write via `atomic_write_text()`
  - [x] Build and return `FileEditResult`

- [x] **Task 4: Implement `apply_file_write`** (AC: #2, #4)
  - [x] Create parent directories via `os.makedirs(parent, exist_ok=True)`
  - [x] Scan content via `scan_text()` — abort if secrets detected
  - [x] Write via `atomic_write_text()`
  - [x] Build and return `FileEditResult`

- [x] **Task 5: Define FileEditedPayload** (AC: #8)
  - [x] Add `FileEditedPayload` to `packages/events/src/events/payloads.py`
  - [x] Fields: `session_id`, `file_path`, `tool_name`, `lines_added`, `lines_removed`, `secrets_detected`
  - [x] Register in schema registry: `file.edited` v1.0.0
  - [x] Add to `__all__`

- [x] **Task 6: Write tests** (AC: #12)
  - [x] Extend `test_atomic_edit.py` with new test classes
  - [x] `TestValidateEdit`: valid, no match, multiple match, replace_all, empty old_string
  - [x] `TestApplyFileEdit`: happy path, file not found, old_string not found, secret abort
  - [x] `TestApplyFileWrite`: happy path, parent dir creation, secret abort
  - [x] `TestAtomicityVerification`: verify write-interrupt harness passes for `apply_file_write`
  - [x] Schema registry registration verified (direct `register()` from package imports)

- [x] **Task 7: Verification + commit** (AC: #7, #10, #11, #13, #14)
  - [x] `mypy --strict` clean on all modified files
  - [x] `ruff check` clean on all modified files
  - [x] `scripts/check_imports.py` — no new violations (1 pre-existing from Story 5.5)
  - [x] `scripts/check_event_registry.py` exits 0
  - [x] `just test` — 1486 passed, 0 failed, 5 skipped (no regressions)
  - [x] Atomic commit

## Dev Agent Record

**Completed**: 2026-05-08
**Status**: review

### Files Modified

| File | Change |
|------|--------|
| `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py` | Added `EditValidation`, `FileEditResult` dataclasses; `validate_edit`, `apply_file_edit`, `apply_file_write` functions |
| `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py` | Added 25 new tests across 4 test classes |
| `packages/events/src/events/payloads.py` | Added `FileEditedPayload` model |
| `services/registry-state/src/registry_state/domain/event_types.py` | Registered `file.edited` v1.0.0; re-exported `FileEditedPayload` |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status update |

### Verification

- `just test`: 1486 passed, 0 failed, 5 skipped
- `mypy --strict`: clean on all 3 modified source files
- `ruff check`: clean
- `scripts/check_event_registry.py`: exit 0
- `scripts/check_imports.py`: no new violations (1 pre-existing from Story 5.5 test)

### Notes

- `validate_edit` is a pure function (no IO) for maximum testability
- Secret scanning aborts the write entirely (unlike reasoning breadcrumbs which suppress text but still emit)
- Schema registry tests use direct `register()` from `events.schema_registry` instead of cross-service `importlib.reload` pattern to satisfy import-graph rules
- Pre-existing `check_imports.py` violation in `test_reasoning.py` (Story 5.5) is outside this story's scope

## Dev Notes

### What already exists (Story 2.12)

`services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py` contains the low-level POSIX primitive:

- `atomic_write_bytes(target, data, *, fsync_data=True, fsync_dir=True)` — write bytes to tmpfile + fsync + `os.replace`
- `atomic_write_text(target, text, *, encoding="utf-8", errors="strict", ...)` — text wrapper
- `_chunked_write(fd, data)` — monkey-patchable for the interrupt harness
- Uses stdlib only: `os`, `pathlib`, `secrets`, `logging`, `errno`

The write-interrupt harness at `tests/crash-injection/_atomic_edit_runner.py` monkey-patches `_chunked_write` to kill after N bytes, then verifies the file is either old or new content.

### Secret scanning vs reasoning breadcrumb suppression

Story 5.5's reasoning breadcrumbs use **full text suppression** (emit event with empty text + `suppressed=True`) when secrets are detected, because the event still needs to be emitted for observability.

For file edits, the approach is different: **abort the write entirely** when secrets are detected. Rationale:
1. Writing a file with secret content to disk is the actual leak — suppressing text in the event doesn't help
2. The operator should be notified that a file edit was blocked due to secret content
3. The worker should NOT silently skip the edit — it should return a result with `secrets_detected=True`
4. Future stories (approval flow) may allow the operator to explicitly approve writing files with secrets

### Import-graph rules

`domain/atomic_edit.py` already uses `os`, `pathlib`, `secrets`, `logging`, `errno` — all stdlib. The new functions add:

| Import | Allowed? | Notes |
|---|---|---|
| `secret_hygiene.scanner.scan_text` | ALLOWED | packages/ import, same as Story 5.5 |
| `pathlib.Path.read_text()` | ALLOWED | stdlib, same file already uses `pathlib` |
| `os.makedirs` | ALLOWED | stdlib, same file already uses `os` |
| `structlog` | **FORBIDDEN** | use stdlib `logging` |
| `asyncio` | **FORBIDDEN** | synchronous module |
| `services/*` | **FORBIDDEN** | cross-service import |

### Line counting

For `FileEditResult.lines_added` / `lines_removed`, use a simple diff approach:
- Split old and new content into lines
- Count lines that were added (in new but not in old)
- Count lines that were removed (in old but not in new)
- Or simpler: `new_count - old_count` for net change, with `lines_added = max(0, new_count - old_count)` and `lines_removed = max(0, old_count - new_count)`

The simplest approach is just counting total lines in old vs new content. The exact per-line diff is unnecessary for the payload model.

### Key patterns from previous stories

1. **Domain = zero IO** (with exception for file-op primitives) — `atomic_edit.py` IS the IO primitive, so file operations are its core purpose
2. **`@dataclass` for results** — parallel to `ReasoningBreadcrumb` from Story 5.5
3. **Pydantic payload discipline** — `ConfigDict(frozen=True, strict=True, extra="forbid")` on ALL payload models
4. **Schema registry** — `register(event_type, schema_version, payload_model)` from `events.schema_registry`
5. **`importlib.reload()`** for schema registry tests — handles `unregister_all()` pollution from autouse fixture
6. **Secret scanning** — `scan_text()` returns `list[SecretMatch]`; empty list means clean
7. **Best-effort event emission** — file edit failures should not crash the runner

### Relationship to downstream stories

- **Story 5.12 (Task execution driver)** — Will use `apply_file_edit`/`apply_file_write` when replaying or executing file operations
- **Story 5.17b (Cross-restart approval)** — Needs the atomic-edit primitive for crash recovery; the FSM integrates FR30
- **Story 7.1 (Reconstituted state handler)** — May use atomic writes for state snapshots

### File.edited event emission

The `ClaudeCodeRunner._extract_events()` already classifies `Write`/`Edit` tool_use blocks as `file.edited` `ExtractedEvent`s. The `FileEditedPayload` registered in this story provides the schema for when those events are emitted through the event log. The actual emission through `clawhip-bridge` is deferred to Story 5.12 (task execution driver).

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1519-1531 — Story 5.6 definition]
- [Source: `_bmad-output/planning-artifacts/epics.md` lines 896-911 — Story 2.12 write-interrupt harness]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 694 — `atomic_edit.py` in directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 811 — Persistence & Recovery mapping]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 386-401 — event envelope strict schema]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 789 — domain layer IO rules]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 854 — FR30 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 119 — NFR-R2 definition]
- [Source: `services/worker-wrapper/src/worker_wrapper/domain/atomic_edit.py` — existing low-level primitive]
- [Source: `services/worker-wrapper/src/worker_wrapper/domain/test_atomic_edit.py` — existing tests]
- [Source: `packages/secret-hygiene/src/secret_hygiene/scanner.py` — `scan_text()` API]
- [Source: `packages/events/src/events/payloads.py` — existing payload model patterns]
- [Source: `packages/events/src/events/schema_registry.py` — `register()` API]
- [Source: `_bmad-output/implementation-artifacts/5-5-reasoning-breadcrumb-emission.md` — previous story patterns]
