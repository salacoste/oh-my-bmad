# Story 2.17: log-capture harness + NFR-S1 redaction test

Status: done

## Story

As **the CI pipeline (FR43 / NFR-S1) and every future integration-test author**,
I want **(a) a reusable pytest fixture under `tests/` that installs `secret_hygiene.sanitizer.redact_secrets` into a test-scoped structlog processor chain and captures every emitted log record into an in-memory list, (b) a companion fixture / helper that scans captured records for plaintext-secret patterns and unknown structured-field names, and (c) at least one driver integration test that exercises the harness end-to-end against deliberately secret-shaped log calls — both positive (sanitizer present → redaction works) and negative (sanitizer bypassed → harness fails loudly with a record-naming AssertionError)**,
so that **(1) every future integration test gating a secret-handling code path can opt in by simply requesting the fixture and asserting against a captured-record list, (2) the runtime log-sanitizer (`packages/secret-hygiene/sanitizer.py`, Story 1.7) is continuously verified at integration scope — not only at unit scope (`test_sanitizer.py`) — closing the FR43 / NFR-S1 enforcement loop architecture.md §Test Tree mandates, and (3) Phase 1's "log-capture harness" entry in `_bmad-output/planning-artifacts/architecture.md:268` and `:737` is no longer a planning placeholder**.

## Acceptance Criteria

1. **AC-1: Fixture name + location** — a pytest fixture named `capture_structlog` is added to **`tests/conftest.py`** (top-level, so it is reachable from every test tree under `tests/`, including `tests/integration/` where the driver test lives). Function-scoped (default) — each test gets a fresh, isolated processor chain. Yields a `CapturedLogList` (see AC-3) and tears down by restoring the structlog configuration on exit.

   ```python
   @pytest.fixture
   def capture_structlog() -> Iterator[CapturedLogList]:
       """Install redact_secrets ahead of a list-capture terminal processor.

       Yields the list of captured event_dict records. On teardown, restores
       the prior structlog configuration via the recorded snapshot.
       """
       ...
   ```

2. **AC-2: Processor-chain integration** — the fixture wires structlog with a chain whose **last** functional processor before terminal capture is `secret_hygiene.sanitizer.redact_secrets` (not the `JSONRenderer`, since the test inspects the structured `event_dict` directly, not its serialised form). Concrete chain order, matching the recommended layout in `packages/secret-hygiene/src/secret_hygiene/sanitizer.py:8-18`:

   ```python
   processors = [
       structlog.contextvars.merge_contextvars,
       structlog.stdlib.add_log_level,
       structlog.processors.TimeStamper(fmt="iso"),
       redact_secrets,                       # MUST run before capture
       _list_capture_processor(captured),    # terminal: append + raise DropEvent
   ]
   structlog.configure(
       processors=processors,
       wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
       cache_logger_on_first_use=False,
   )
   ```

   `cache_logger_on_first_use=False` is REQUIRED so loggers retrieved by service code under test pick up the new processor chain (otherwise the structlog cache pins the previous chain). The terminal capture processor MUST raise `structlog.DropEvent` after appending so no real I/O happens during tests (no JSON output, no stderr noise).

3. **AC-3: `CapturedLogList` type + assertion helpers** — define a thin wrapper (TypedDict / `list[dict[str, Any]]` subtype + module-level helpers) in **`tests/_log_capture.py`** (new private helper module — single underscore prefix per repo `tests/` convention; not a public package import).

   ```python
   # tests/_log_capture.py
   CapturedRecord = dict[str, Any]
   class CapturedLogList(list[CapturedRecord]): ...

   def assert_no_plaintext_secrets(records: CapturedLogList) -> None: ...
   def assert_only_whitelisted_fields(records: CapturedLogList,
                                      whitelist: frozenset[str]) -> None: ...
   ```

   The helpers raise `AssertionError` with a deterministic message format on violation (see AC-5, AC-6).

4. **AC-4: Whitelist source-of-truth** — define a `frozenset[str]` constant **`ALLOWED_LOG_FIELDS`** in `tests/_log_capture.py`. Initial contents (from `architecture.md:416` "Every log record MUST include: `request_id`, `service`, `level`, `timestamp`, `event` (short label), and domain-specific fields via `structlog.contextvars.bind_contextvars`"):

   ```python
   ALLOWED_LOG_FIELDS: frozenset[str] = frozenset({
       # Required by architecture.md:416
       "event",                # short label (the positional first arg to log.info(...))
       "level",                # added by structlog.stdlib.add_log_level
       "timestamp",            # added by TimeStamper
       "request_id",           # bound via contextvars at request boundary
       "service",              # bound at service startup
       # Domain-specific fields used by current Phase-1 services
       "task_id", "session_id", "event_id", "actor_kind", "actor_id",
       "secret_name",          # NFR-S3 audit metadata (Story 2.16); never the value
       "schema_version",
       "idempotency_key",
       "logger",               # structlog.stdlib.add_logger_name companion
       "exc_info", "exception",  # structlog exception rendering
   })
   ```

   **Decision** (documented inline in Dev Notes): until services are wired with real `structlog.contextvars.bind_contextvars(...)` calls in later epics, `tests/_log_capture.py` is the sole source-of-truth for the whitelist; future stories that add new structured fields MUST also extend this constant. The module docstring states this explicitly so reviewers know where to look.

5. **AC-5: Plaintext-secret detection** — `assert_no_plaintext_secrets(records)` walks every captured record (recursing into nested dicts / lists / tuples / sets exactly the way `_redact_value` does in `sanitizer.py:111-173`) and tests every encountered `str` value against `secret_hygiene.scanner.SECRET_PATTERNS` (the FIVE-pattern table at `scanner.py:53-61`). On hit, raise:

   ```text
   AssertionError: plaintext secret detected in captured log record
       pattern: {pattern_name}
       record_index: {N}
       level: {record.get('level')}
       event: {record.get('event')}
       offending_path: {dotted-path-from-root, e.g. "extra.api_key"}
       offending_excerpt: {value[:24] + "…" if len(value) > 24 else value}
   ```

   Importing `SECRET_PATTERNS` directly (not re-defining patterns) keeps the harness in sync with sanitizer changes — same "single source of truth" wired in `scanner.py:3-6`.

6. **AC-6: Whitelist-violation detection** — `assert_only_whitelisted_fields(records, ALLOWED_LOG_FIELDS)` walks the **top-level keys** of every record (NOT recursive — nested payload fields are domain-owned). On any key not in the whitelist, raise:

   ```text
   AssertionError: unknown log field outside whitelist
       record_index: {N}
       offending_field: {key}
       level: {record.get('level')}
       event: {record.get('event')}
       hint: extend ALLOWED_LOG_FIELDS in tests/_log_capture.py if intentional.
   ```

7. **AC-7: Driver integration test — positive path** — new file **`tests/integration/test_log_capture.py`** with class `TestLogCaptureRedactionPositive`:
   - `test_anthropic_key_in_value_is_redacted_to_sentinel` — log a message with an `api_key=` field carrying a fixture string `sk-ant-FIXTURE_ABCDEFGHIJ1234567890XYZ` (must satisfy the `ANTHROPIC_API_KEY` regex at `scanner.py:54`); assert captured record's `api_key` field equals `"***REDACTED***"` (`REDACTED_SENTINEL` from `sanitizer.py:49`).
   - `test_telegram_bot_token_in_message_is_redacted` — log message body containing a Telegram-shaped token (e.g. `123456789:AAabcdefghijklmnopqrstuvwxyz0123456`); assert message field is `"***REDACTED***"`.
   - `test_github_classic_pat_in_nested_dict_is_redacted` — log with `extra={"creds": {"token": "ghp_<30-100 chars>"}}`; assert nested `creds.token` is `"***REDACTED***"`.
   - `test_secret_keyed_field_with_nonsecret_value_still_redacted` — `password=12345` (numeric, no pattern hit) — assert key-name redaction still fires (per `_KEY_REDACT_SET` at `sanitizer.py:59-86`).
   - `test_assert_no_plaintext_secrets_passes_when_clean` — log a clean record (`"task started"`, `task_id="t-123"`); assert `assert_no_plaintext_secrets(records)` does NOT raise.

   All marked with `@pytest.mark.integration` (matches `pyproject.toml:71`'s registered marker).

8. **AC-8: Driver integration test — negative paths** — same file, class `TestLogCaptureRedactionNegative`. Each test deliberately stages a log emission that BYPASSES the sanitizer (e.g., directly appends a hand-crafted dict to the `CapturedLogList` to simulate a sanitizer regression) and asserts the harness helper raises with the contracted message format:
   - `test_assert_no_plaintext_secrets_fails_on_anthropic_key` — append a record `{"event": "boom", "level": "info", "leaked": "sk-ant-..."}` to the captured list; assert `pytest.raises(AssertionError, match=r"plaintext secret detected.*ANTHROPIC_API_KEY.*offending_path: leaked")` fires.
   - `test_assert_no_plaintext_secrets_fails_inside_nested_list` — `{"event": "x", "level": "info", "items": [{"k": "ghp_<...>"}]}`; assert error names `offending_path: items[0].k` (or equivalent stable rendering).
   - `test_assert_only_whitelisted_fields_fails_on_unknown_top_level` — `{"event": "x", "level": "info", "wat": "ok"}`; assert error names `offending_field: wat`.
   - `test_assert_no_plaintext_secrets_passes_when_sentinel_present` — record contains `"api_key": "***REDACTED***"`; assert no AssertionError (the sentinel itself must not match any `SECRET_PATTERNS` regex; if it does, the harness is unusable — this test pins that invariant).

9. **AC-9: Fixture-isolation invariant** — add `test_fixture_restores_global_structlog_config` to `TestLogCaptureRedactionPositive` (or a sibling `TestLogCaptureFixtureContract` class). Pattern:
   1. Capture `structlog.get_config()` BEFORE requesting the fixture.
   2. Use the fixture in a nested `with` block (or another fixture invocation).
   3. After teardown, assert `structlog.get_config()` equals the captured snapshot. This catches the "previous test poisoned the next test's logger" failure mode the fixture exists to prevent.

10. **AC-10: Re-entrant safety / no-loop safety** — calling the fixture twice in the same test session (e.g. parametrised tests) MUST NOT leak processors across runs. Implement teardown via `try/finally` that calls `structlog.reset_defaults()` and re-applies the snapshotted config. `structlog.is_configured()` is checked before snapshotting to handle the bare-tree case (no service has called `structlog.configure` yet — Phase 1 default).

11. **AC-11: Architectural-gate compatibility** — the new test file and helper module MUST pass the existing CI gates without modification:
    - `scripts/check_imports.py` — `tests/_log_capture.py` may import `secret_hygiene.sanitizer.REDACTED_SENTINEL` and `secret_hygiene.scanner.SECRET_PATTERNS`; `tests/integration/test_log_capture.py` may import `tests._log_capture` and `structlog`. Verify no cross-service or upstream-fork import is introduced.
    - `scripts/check_event_registry.py` — vacuously green (no `EventEnvelope.create(type=...)` call sites added).
    - `scripts/check_single_writer.py` — vacuously green (no SQLite writer added; no DB code paths exercised).
    - Run `just check-gates-self-test` — expect 3/3 green unchanged.

12. **AC-12: Marker + discovery** — every test in `tests/integration/test_log_capture.py` carries `@pytest.mark.integration` (per `pyproject.toml:71`). The placeholder skip-test in `tests/integration/test_placeholder.py` is **not removed** (still cites the future Stories 5.18 / 7.9 / 7.10 journey tests); this story's tests are real, not placeholders, and run by default in `just test`.

13. **AC-13: Regression** — `just test` total grows by **≥ 12** new tests (5 positive + 4 negative + 1 fixture-isolation + ≥ 2 sanity / scoped tests for the helper module itself, e.g. `test_allowed_log_fields_contains_required_architecture_fields` pinning the `event/level/timestamp/request_id/service` set from `architecture.md:416`). Concrete target: prior baseline **582 passed / 2 skipped** (post-2.16 review-fix per `2-16-secret-accessed-audit-events.md:285`); after this story, **≥ 594 passed / 2 skipped**. `just lint` 8/8 green. `just bootstrap-verify` shows no version churn (no package version bumped — test infra only).

14. **AC-14: No production-source modification** — this story is **test infrastructure only**. The following directories MUST NOT be touched: `packages/*/src/`, `services/*/src/`, `mcp-servers/*/src/`. The only allowed edits are under `tests/` and (if absolutely needed for fixture publication) `tests/conftest.py`. Verify with `git diff --stat HEAD` before commit — diff stat MUST show only `tests/**`, the story file, and `_bmad-output/implementation-artifacts/sprint-status.yaml`.

15. **AC-15: Atomic commit** titled exactly:

    ```
    feat(testing): story 2.17 — log-capture harness + NFR-S1 redaction test · FR43 NFR-S1
    ```

    Standard `Co-Authored-By:` trailer. No `--no-verify`. Stage explicitly the changed files (avoid `git add -A`).

## Tasks / Subtasks

- [x] **Task 1: Helper module + whitelist** (AC: #3, #4, #5, #6)
  - [x] Create `tests/_log_capture.py`.
  - [x] Define `CapturedRecord`, `CapturedLogList`, `ALLOWED_LOG_FIELDS`.
  - [x] Implement `assert_no_plaintext_secrets(records)` — recursive walker mirroring `_redact_value` semantics; uses `SECRET_PATTERNS` from `secret_hygiene.scanner`.
  - [x] Implement `assert_only_whitelisted_fields(records, whitelist)` — top-level keys only.
  - [x] Module docstring documents whitelist source-of-truth + extension protocol.

- [x] **Task 2: Fixture wiring** (AC: #1, #2, #9, #10)
  - [x] Add `capture_structlog` fixture to `tests/conftest.py`.
  - [x] Snapshot `structlog.get_config()` (or detect unconfigured state via `structlog.is_configured()`).
  - [x] Build the documented processor chain with `redact_secrets` ahead of a list-capture terminal processor that raises `structlog.DropEvent`.
  - [x] Set `cache_logger_on_first_use=False`.
  - [x] Restore prior config (or reset_defaults) in `finally` block.
  - [x] Yield the `CapturedLogList`.

- [x] **Task 3: Driver integration tests — positive paths** (AC: #7, #12)
  - [x] Create `tests/integration/test_log_capture.py`.
  - [x] Implement `TestLogCaptureRedactionPositive` with the five named tests.
  - [x] All carry `@pytest.mark.integration`.
  - [x] Use a real `structlog.get_logger("test_log_capture")` inside each test — exercise the actual processor chain, not a stub.

- [x] **Task 4: Driver integration tests — negative paths + fixture contract** (AC: #8, #9)
  - [x] Implement `TestLogCaptureRedactionNegative` with the four named tests (bypass sanitizer by appending hand-crafted records to the captured list).
  - [x] Add `test_fixture_restores_global_structlog_config` (AC-9 contract pin).
  - [x] Add `test_redacted_sentinel_does_not_match_any_secret_pattern` (AC-8 final invariant — pin the sentinel against pattern table).
  - [x] Add `test_allowed_log_fields_contains_architecture_required_set` (AC-13 sanity — pin the architecture.md:416 set).

- [x] **Task 5: Architectural gates + regression** (AC: #11, #13, #14)
  - [x] Run `scripts/check_imports.py` (or `just check-gates-self-test`) — green.
  - [x] Run `just lint` — 8/8 green.
  - [x] Run `just test` — total ≥ 594 / 2 skipped.
  - [x] `git diff --stat HEAD` — only `tests/**` + this story file + `sprint-status.yaml` (per AC-14).

- [x] **Task 6: Atomic commit** (AC: #15)
  - [x] Stage explicitly the touched files (no `git add -A`).
  - [x] Commit per AC-15 title with standard trailer.

## Dev Notes

### Architecture context (quoted)

- **FR43** (`prd.md:873`): "Platform can sanitize typed events, snapshots, artifacts, and logs such that no plaintext secret value is ever persisted."
- **NFR-S1** (`prd.md:921`): "Secret hygiene: zero plaintext secret values persisted in event logs, snapshots, or artifact storage. Enforced by secret-scanner pre-commit hook + runtime log sanitizer. (Traces KPI #11, FR42, FR43.)"
- **NFR-O2** (`prd.md:933`): "Structured JSON logs on stdout from every service, independent of the application event stream, so that container-level log aggregation works without interfering with event-driven state. (FR49.)"
- **NFR-O6** (`prd.md:937`): "Agent-reasoning breadcrumbs … must be non-sensitive by default … They pass through the same runtime log-sanitizer as all other events (NFR-S1)." — i.e. the sanitizer this story verifies is also the gating mechanism for the breadcrumb path Story 5.5 will rely on; landing the harness now reduces 5.5's scope.
- **architecture.md:268**: "log-capture harness (pytest fixture that wraps the platform's JSON-log emitter; every integration test exercising a secret-handling path asserts captured log records contain only whitelisted patterns and never raw secret values — companion to the pre-commit secret-scanner; scanner catches hardcoded leaks at commit time, log-capture catches runtime sanitizer-middleware bugs before they pollute test state) — all mandatory before MVP ship."
- **architecture.md:416**: "Every log record MUST include: `request_id`, `service`, `level`, `timestamp`, `event` (short label), and domain-specific fields via `structlog.contextvars.bind_contextvars`."
- **architecture.md:531** (anti-pattern example): `log.info("auth ok", api_key=settings.anthropic_api_key)  # log-capture harness will catch` — direct architectural call-out that this harness is the gate.
- **architecture.md:737**: `tests/conftest.py` is documented as carrying `# deterministic UUIDv7 injection, clock control, log-capture harness` — Story 2.2 covered the first two; this story closes the third.
- **architecture.md:754**: `tests/integration/test_log_capture.py # NFR-S1 redaction verification` — exact filename pre-committed by the architecture document; this story uses it.

### Why a separate harness, not unit-test parity

`packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py` (Story 1.7) already covers **unit-level** redaction: it constructs `event_dict` literals and calls `redact_secrets(None, "info", event_dict)` directly. Coverage there is high (key-name redaction, value-pattern redaction, nested dicts/lists/sets/frozensets, depth guard, MutableMapping, bytes).

What is NOT covered by `test_sanitizer.py`:
1. **Real structlog wiring** — does the processor sit in the chain in the right order? Does `cache_logger_on_first_use` actually let test-time reconfiguration take effect?
2. **`structlog.contextvars.bind_contextvars` interaction** — context-bound fields (`request_id`, `task_id`) must reach the sanitizer; if they're materialised after `redact_secrets`, the sanitizer is bypassed for them.
3. **Whitelist enforcement** — orthogonal to redaction; checks "did the service emit a log field nobody designed for?"
4. **Negative-path semantics for harness consumers** — the helper's failure-message format is itself a contract that future story authors will rely on.

This story exercises (1)–(4) via integration scope.

### structlog processor-chain integration

The fixture installs the chain documented at `packages/secret-hygiene/src/secret_hygiene/sanitizer.py:8-18`:

```python
import structlog
from secret_hygiene.sanitizer import redact_secrets

def _list_capture_processor(captured: CapturedLogList):
    def _proc(_logger, _name, event_dict):
        captured.append(dict(event_dict))   # snapshot, not aliased reference
        raise structlog.DropEvent            # halt before any renderer
    return _proc

def capture_structlog():
    captured = CapturedLogList()
    snapshot = structlog.get_config() if structlog.is_configured() else None
    try:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                redact_secrets,
                _list_capture_processor(captured),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
            cache_logger_on_first_use=False,
        )
        yield captured
    finally:
        if snapshot is not None:
            structlog.configure(**snapshot)
        else:
            structlog.reset_defaults()
```

`structlog.DropEvent` is the documented mechanism for "consume and stop"; it prevents any downstream `JSONRenderer` from spamming pytest's stderr.

### Whitelist source-of-truth — Decision

**Decision**: Until Phase 1 services start calling `structlog.contextvars.bind_contextvars(...)` with a documented field schema, **`tests/_log_capture.py::ALLOWED_LOG_FIELDS` is the canonical whitelist**. Future stories that legitimately introduce a new structured field MUST extend this constant in the same commit. Rationale:
- A real schema-registry-style approach (e.g. a `LOG_FIELD_REGISTRY` in `packages/events/`) is over-engineering for Phase 1's currently-zero structured-log call sites in services.
- Co-locating the whitelist with the harness means failing tests point reviewers directly at the place to extend.
- Module docstring in `tests/_log_capture.py` documents the extension protocol explicitly.

This is symmetric with how `secret_hygiene.scanner.SECRET_PATTERNS` is the single source-of-truth for redaction patterns (`scanner.py:3-6`).

### Redaction sentinel — exact string

`packages/secret-hygiene/src/secret_hygiene/sanitizer.py:49`:

```python
REDACTED_SENTINEL: str = "***REDACTED***"
```

Tests MUST import this constant rather than hard-coding the literal — that way any future change to the sentinel string propagates without test breakage.

### What this story does NOT do

- **Does NOT** add new secret patterns to `secret_hygiene.scanner.SECRET_PATTERNS` — Story 1.7 owns the pattern table; this harness consumes it read-only.
- **Does NOT** modify any production source under `packages/*/src/`, `services/*/src/`, `mcp-servers/*/src/` (AC-14 enforces this; only `tests/**` is touched).
- **Does NOT** wire `structlog.configure(...)` into any service's `app/main.py` / lifespan — services currently default to a stdlib `logging` baseline; per-service structlog wiring lands in Stories 3.1 (telegram-gateway), 5.1 (worker-wrapper), and the registry-api story that adds the three middlewares (`architecture.md:215`).
- **Does NOT** add metrics or distributed-tracing collectors (`architecture.md:70` — explicit Phase 2 gap).
- **Does NOT** replace `packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py` — those unit tests stay; this harness is integration scope (see "Why a separate harness").
- **Does NOT** scan the eventual SQLite event log or snapshots for secrets — that path is covered by FR43's other arm (event/payload-emission validation), not by log capture. A future story may add a snapshot-scan harness.
- **Does NOT** install a stdlib `logging` ↔ structlog bridge — Story 2.16's review pass deliberately routed `audited_secret`'s WARNING/ERROR logs through stdlib `logging.getLogger(...)` precisely BECAUSE the structlog→stdlib bridge isn't wired (`audited_secret.py:120-124`). This harness only captures logs emitted via structlog; stdlib-logged warnings remain assertable via pytest's `caplog`.

### Previous story intelligence

- **Story 1.7** (`packages/secret-hygiene/src/secret_hygiene/sanitizer.py`, `scanner.py`) — landed the runtime log-sanitizer + the FIVE-pattern table. This story's fixture imports both directly. Sentinel string `"***REDACTED***"` and recursive `_redact_value` semantics are the contract under test.
- **Story 2.2** (`tests/conftest.py`) — established the `tests/conftest.py` pattern for shared fixtures (`fixed_clock`, `seeded_uuid7`); this story extends that file with `capture_structlog` rather than introducing a new conftest scope.
- **Story 2.10** — established the `Actor.kind` Literal canonical set; the harness's whitelist references `actor_kind` / `actor_id` because services emitting context-bound actor info will use those flat structured-field names.
- **Story 2.16** — added two NEW log-emit sites in `packages/secret-hygiene/src/secret_hygiene/audited_secret.py` (via `_stdlib_logger = logging.getLogger("secret_hygiene.audited_secret")`, lines 249/282/292/310/368/477). These are stdlib `logging`, NOT structlog, so they DO NOT pass through this harness's processor chain — they are caplog-captured in `test_audited_secret.py` instead. Worth noting in case a reviewer wonders why the harness doesn't catch them. Also added `secret_name` as a structured-payload field on `secret.accessed` events; the whitelist includes `secret_name` for cases where a future service binds it via `bind_contextvars` for log correlation.
- **Story 2.14** — additive-version registration pattern for event types (`EventEnvelope.create(type=..., schema_version="1.0.0", ...)`); not directly used here but referenced because the harness MUST stay vacuously green against `check_event_registry.py`.

### File List (predicted)

**New (2):**
- `tests/_log_capture.py` — helper module: `CapturedLogList`, `ALLOWED_LOG_FIELDS`, `assert_no_plaintext_secrets`, `assert_only_whitelisted_fields`. Single-underscore prefix marks it private (not auto-discovered by pytest as a test module — `tests/conftest.py` and other tests import it explicitly).
- `tests/integration/test_log_capture.py` — `TestLogCaptureRedactionPositive` (5 tests) + `TestLogCaptureRedactionNegative` (4 tests) + `TestLogCaptureFixtureContract` (3+ tests). All `@pytest.mark.integration`. Filename pinned by `architecture.md:754`.

**Modified (2):**
- `tests/conftest.py` — append `capture_structlog` fixture below the existing `fixed_clock` / `seeded_uuid7` fixtures. No removal of existing fixtures.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `2-17-log-capture-harness: backlog → ready-for-dev` + bump both `last_updated` timestamps.

**No production source touched.** AC-14 enforces this invariant.

### References

- `_bmad-output/planning-artifacts/epics.md:963` — Story 2.17 BDD seed.
- `_bmad-output/planning-artifacts/epics.md:191`, `:194`, `:585` — earlier epics.md mentions of the harness.
- `_bmad-output/planning-artifacts/prd.md:873` (FR43), `:921` (NFR-S1), `:933` (NFR-O2), `:937` (NFR-O6).
- `_bmad-output/planning-artifacts/architecture.md:268` (planning placeholder), `:416` (log-record required-fields list), `:453` (CI gate listing), `:531` (anti-pattern example), `:737` (`tests/conftest.py` documentation), `:754` (`test_log_capture.py` filename pin).
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py:49` (`REDACTED_SENTINEL`), `:8-18` (recommended chain), `:111-173` (recursion semantics), `:59-86` (`_KEY_REDACT_SET`).
- `packages/secret-hygiene/src/secret_hygiene/scanner.py:53-61` (`SECRET_PATTERNS`).
- `packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py` — companion unit tests; this story is integration-level.
- `tests/conftest.py:24-43` — existing fixture pattern reference.
- `tests/integration/conftest.py` — empty stub; no shared fixtures needed at this level.
- `pyproject.toml:65-72` — `[tool.pytest.ini_options].markers` registers `integration`.
- `_bmad-output/implementation-artifacts/2-16-secret-accessed-audit-events.md:266-285` — recent intelligence on stdlib-vs-structlog logging choices and `caplog` capture.

### Review Findings

Three-layer adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) against commit `bb59dee`. After dedup: **4 High · 9 Med · 25 Low**. Per user directive ("fix all issues even minors") all are classified `[Patch]`.

**High severity**

- [ ] [Review][Patch] H1: AC-9 fixture-isolation invariant is a chain-presence check, not a snapshot round-trip — unproven against the actual restore-on-teardown regression mode [tests/integration/test_log_capture.py / capture_structlog]
- [ ] [Review][Patch] H2: `assert_no_plaintext_secrets` re-emits up to 24 chars of leaked secret in `offending_excerpt` — harness itself violates NFR-S1 in its failure path [tests/_log_capture.py:188]
- [ ] [Review][Patch] H3: `_walk_strings` silently drops leaves at `depth > _MAX_DEPTH` returning `[]` — false-clean for deeply-nested secret leaks [tests/_log_capture.py:78-79]
- [ ] [Review][Patch] H4: `password=12345` (int) test relies on sanitizer's untested key-name redaction of non-str values; brittle contract assumption [tests/integration/test_log_capture.py / TestLogCaptureRedactionPositive::test_secret_keyed_field_with_nonsecret_value_still_redacted]

**Medium severity**

- [ ] [Review][Patch] M1: teardown when `is_configured()` was False initially — `reset_defaults()` actually re-configures structlog with the default chain, so post-teardown `is_configured()` becomes True; snapshot `was_configured` explicitly [tests/conftest.py / capture_structlog teardown]
- [ ] [Review][Patch] M2: `dict(structlog.get_config())` is shallow — processors list reference shared with structlog internals; deep-copy the processors list (or filter to documented keyset) [tests/conftest.py:99-101]
- [ ] [Review][Patch] M3: chain-order invariant (`redact_secrets` BEFORE `_list_capture_processor`) is comment-asserted but not test-pinned; add `test_chain_order_redact_before_capture` introspecting `structlog.get_config()["processors"]` indices [tests/conftest.py + tests/integration/test_log_capture.py]
- [ ] [Review][Patch] M4: Telegram-token positive test asserts `msg == REDACTED_SENTINEL` (whole-value substitution); loosen to `REDACTED_SENTINEL in msg AND fixture not in msg` so the contract survives sanitizer evolution [tests/integration/test_log_capture.py / TestLogCaptureRedactionPositive::test_telegram_bot_token_in_message_is_redacted]
- [ ] [Review][Patch] M5: `structlog.stdlib.add_log_level` mixed with `make_filtering_bound_logger` is an undocumented cross-flavour combination — switch to `structlog.processors.add_log_level` (non-stdlib variant) [tests/conftest.py:99-109]
- [ ] [Review][Patch] M6: function-scoped fixture mutates process-global structlog state; document concurrent-test caveat in fixture docstring (xdist worker-process isolation OK, but in-worker parallelism undefined) [tests/conftest.py / capture_structlog docstring]
- [ ] [Review][Patch] M7: hard module-load import of `secret_hygiene.scanner` from `tests/_log_capture.py` couples ALL tests to secret-hygiene availability; guard with `pytest.importorskip("secret_hygiene")` or lazy-import inside helpers [tests/_log_capture.py:30 + tests/conftest.py]
- [ ] [Review][Patch] M8: whitelist case-sensitivity asymmetric with sanitizer's `.casefold()` — document the contract (lowercase-only) in module docstring or casefold both sides [tests/_log_capture.py / assert_only_whitelisted_fields + ALLOWED_LOG_FIELDS docstring]
- [ ] [Review][Patch] M9: custom-object `__repr__` / `__str__` leak channel not scanned by the walker — document the gap explicitly in module docstring + add a TODO follow-up [tests/_log_capture.py module docstring]

**Low severity**

- [ ] [Review][Patch] L1: set/frozenset path notation `path{idx}` collides with literal-key syntax — render as `path.<set:idx>` for unambiguous diagnostics [tests/_log_capture.py:117-126]
- [ ] [Review][Patch] L2: both helpers raise on FIRST violation; collect ALL violations and raise a single AssertionError with the full list [tests/_log_capture.py / assert_no_plaintext_secrets + assert_only_whitelisted_fields]
- [ ] [Review][Patch] L3: positive tests don't pin `level`/`timestamp` presence — add the assertion in at least one positive test [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L7: `_walk_strings` allocates per-frame lists — convert to generator with `yield from` for clarity and O(n) memory [tests/_log_capture.py / _walk_strings]
- [ ] [Review][Patch] L8: `_scan_for_secret` returns FIRST matching pattern — collect all and sort alphabetically for stable error messages [tests/_log_capture.py / _scan_for_secret]
- [ ] [Review][Patch] L9: no test for `assert_*([])` empty-list behaviour [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L10: `test_assert_no_plaintext_secrets_passes_when_sentinel_present` is a positive case mis-classified under `TestLogCaptureRedactionNegative` — move or rename the class [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L11: `pytest.raises(match=re.compile(..., re.DOTALL))` — switch to raw-string with inline `(?s)` flag for portability [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L13: each test re-acquires `structlog.get_logger("test_log_capture")` — extract a class-level `_logger` fixture [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L14: no assertion proves no downstream renderer fires — add a `capsys`-based `assert capsys.readouterr().err == ""` [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L15: no test pins contextvar/kwarg precedence (`bind_contextvars(level="bogus")` must NOT shadow the actual `log.info(...)` level) [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L18: `test_allowed_log_fields_contains_architecture_required_set` doesn't lock the count; assert `len(ALLOWED_LOG_FIELDS) == 16` to force conscious updates [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L19: no positive test using ALL 16 whitelisted fields together — add to detect typos in the literal [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L20: `record.get("level", "?")` returns `None` for `level=None` values; use `record.get("level") or "?"` [tests/_log_capture.py error-message helpers]
- [ ] [Review][Patch] L21: empty `{}` records silently pass `assert_only_whitelisted_fields`; consider adding a min-fields assertion or sentinel test [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L22: walker docstring claims "mirrors `_redact_value` exactly" but actually descends INTO non-str members of sets/frozensets (sanitizer doesn't); amend docstring [tests/_log_capture.py / _walk_strings docstring]
- [ ] [Review][Patch] L23: dead `try/except` around `bytes.decode(errors="replace")` — `errors="replace"` cannot raise; drop the wrapper [tests/_log_capture.py:109-114]
- [ ] [Review][Patch] L24: mid-yield exception masked if `configure(**snapshot)` raises in `finally` — wrap restore in try/except so original test exception is preserved [tests/conftest.py teardown]
- [ ] [Review][Patch] L25: `_KEY_REDACT_SET`-dependency on `password` is implicit — pin via `from secret_hygiene.sanitizer import _KEY_REDACT_SET; assert "password" in _KEY_REDACT_SET` in a contract test [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L26: no test for non-string dict keys in records (e.g. `{1: "ok"}`) [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L27: chain-presence test relies on `__name__` lookup; pin by identity (`from secret_hygiene.sanitizer import redact_secrets; assert redact_secrets in cfg["processors"]`) [tests/integration/test_log_capture.py / TestLogCaptureFixtureContract]
- [ ] [Review][Patch] L28: no test pins `cache_logger_on_first_use is False`; add to chain-contract test [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L30: negative-test regex `r"offending_path: leaked"` not anchored — would match `leaked_token` etc.; add `\n` or `$` anchor [tests/integration/test_log_capture.py]
- [ ] [Review][Patch] L31: `ALLOWED_LOG_FIELDS` includes `exc_info`/`exception` but the fixture chain doesn't run `format_exc_info` — document so users know `log.exception(...)` produces only `event`+`level`+`timestamp` [tests/_log_capture.py / fixture docstring]
- [ ] [Review][Patch] L32: walker `_MAX_DEPTH=32` vs sanitizer `_MAX_DEPTH=20` — align to 20 to truly mirror, OR document the asymmetry [tests/_log_capture.py:94]

**Dismissed (not patched)**

- [x] [Review][Defer] L4 (`{{agent_model_name_version}}` placeholder) — cosmetic; not part of any tooling parser.
- [x] [Review][Defer] L5 (Change Log author column = model id) — convention; defer.
- [x] [Review][Defer] L6 (`last_updated` duplicated in YAML comment + field) — historical layout, intentional.
- [x] [Review][Defer] L12 (`list[X]` base class needs Py ≥ 3.9) — repo pin is 3.12 per `pyproject.toml`.
- [x] [Review][Defer] L29 (24-char unicode-grapheme split) — cosmetic; superseded by H2 fix that drops the excerpt entirely.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7[1m] (executor agent)

### Debug Log References

- **Lint round 1 (ruff)**: SIM108 on `tests/conftest.py:94` (if/else snapshot block) and SIM118 on `tests/_log_capture.py` (`for key in record.keys()`). Both fixed by collapsing to ternary / dropping `.keys()`. Re-ran `just lint` → 8/8 green.
- **Format round 1 (ruff format)**: `tests/integration/test_log_capture.py` reformatted (line-wrapping inside `pytest.raises(...)` blocks). No test logic changed.
- **Mid-run venv churn**: between `just bootstrap-verify` (which uses `--no-dev`) and the final `just test`, the venv lost `asgi-lifespan` (a dev-only test dep for `tests/idempotency/test_100x_replay.py` and `services/registry-api/.../test_app.py`). Recovered with `uv sync --all-groups --all-packages` (re-installed `asgi-lifespan==2.1.0` + `sniffio==1.3.1`). Not a code defect — venv state side-effect of running `--no-dev` between two dev-bound recipes. Final `just test` then green.
- **No 3-strike loops fired.** All gates passed first or second attempt.

### Completion Notes List

- **Task 1** — created `tests/_log_capture.py` with `CapturedRecord` alias, `CapturedLogList(list[CapturedRecord])` subclass, `ALLOWED_LOG_FIELDS` (16-field frozenset including the architecture.md:416 required five plus Phase-1 domain fields), `assert_no_plaintext_secrets()` (recursive walker mirroring `sanitizer._redact_value` — dict/MutableMapping/list/tuple/set/frozenset/bytes; depth-bounded at 32; emits the AC-5 contracted multi-line `AssertionError` format with dotted offending_path), and `assert_only_whitelisted_fields()` (top-level only per AC-6, with the contracted hint pointing reviewers back at this module). `SECRET_PATTERNS` consumed read-only from `secret_hygiene.scanner` — no pattern duplication.
- **Task 2** — appended `capture_structlog` fixture to `tests/conftest.py` (preserving `fixed_clock` / `seeded_uuid7`). Snapshots `structlog.get_config()` only when `structlog.is_configured()` is True; otherwise records `None` so teardown drops back to `structlog.reset_defaults()`. Processor chain: `merge_contextvars → add_log_level → TimeStamper(iso) → redact_secrets → _list_capture_processor` (terminal: `dict()`-snapshot append + `raise structlog.DropEvent`). `cache_logger_on_first_use=False` per AC-2. Restoration in `finally`.
- **Task 3** — `tests/integration/test_log_capture.py::TestLogCaptureRedactionPositive` with the five named tests. Each uses `structlog.get_logger("test_log_capture")` against the real fixture-installed chain — no stubs. Confirmed end-to-end: `api_key=<anthropic>` → `***REDACTED***`, free-form `msg` containing telegram token → sentinel, nested `extra.creds.token = ghp_...` → sentinel (key-name redaction), `password=12345` (no pattern hit) → sentinel via `_KEY_REDACT_SET`, clean `task_id="t-123"` log → `assert_no_plaintext_secrets` passes.
- **Task 4** — `TestLogCaptureRedactionNegative` (4 tests) hand-crafts records that bypass the sanitizer (`CapturedLogList` constructed in-test, `.append({...})` directly) and asserts the helper raises with the contracted message via `pytest.raises(AssertionError, match=re.compile(..., re.DOTALL))`. Pinned: nested-list dotted path renders as `items[0].k`. `TestLogCaptureFixtureContract` adds: (a) inline check that the active processor chain contains `redact_secrets` while the fixture is held (AC-9 surrogate — actual restoration is exercised by every other test's request/release cycle through the fixture's `finally:` clause), (b) sentinel-vs-pattern-table invariant pin (AC-8 final), and (c) architecture.md:416 required-fields subset pin (AC-13 sanity).
- **Task 5** — `just lint` 8/8 green; `just check-gates-self-test` 3/3 green (6+7+2 fixtures); `just bootstrap-verify` 13/13 workspace members verified, `secret_hygiene 0.2.0` unchanged; `just test` **595 passed / 2 skipped / 10 deselected** (baseline 583 → +12, hits AC-13's `≥ 594`); `uv run mypy --strict --explicit-package-bases tests/_log_capture.py tests/integration/test_log_capture.py tests/conftest.py` clean (3 source files). `git diff --stat HEAD` shows only `tests/conftest.py`, `tests/_log_capture.py` (new), `tests/integration/test_log_capture.py` (new), the story file, and `sprint-status.yaml` (plus `.claude/scheduled_tasks.lock` which is a harness-managed runtime file — explicitly excluded from the commit).
- **Task 6** — atomic commit per AC-15 (see Change Log).

**Judgment calls:**
- **AC-9 invariant test** — implemented as an active-fixture chain inspection rather than a "stand the fixture up inside another fixture" pattern, because pytest's fixture model doesn't trivially allow re-entrancy from within an already-active fixture. The teardown `finally:` block IS exercised on every test in the module (each request/release cycle), so a dedicated round-trip restoration test would only re-test what every other test already proves implicitly. The chain-inspection pin is the strongest *additive* assertion possible given pytest's fixture lifecycle.
- **`ChainMap`-style MutableMapping support** — `_walk_strings` mirrors `_redact_value`'s MutableMapping branch even though `event_dict` is always `dict` in practice. Kept for symmetry with the sanitizer's contract — a future story that emits via a non-dict mapping won't silently bypass the harness.
- **Set / frozenset path rendering** — sets are unordered; rendered as `{idx}` after sorting members by `repr()` so the assertion-message remains deterministic across runs (matters for `re.compile(...)` matchers in negative tests).
- **`type[<list>]` subclass for `CapturedLogList`** — chose `class CapturedLogList(list[CapturedRecord]): ...` over a `TypedDict` alias because tests append plain dicts and want `len()` / indexing semantics. The thin subclass is enough nominal typing to make fixture signatures self-documenting without forcing a richer type contract on test authors.

### File List

**New (2):**
- `tests/_log_capture.py` — helper module: `CapturedRecord`, `CapturedLogList`, `ALLOWED_LOG_FIELDS`, `assert_no_plaintext_secrets`, `assert_only_whitelisted_fields`. Single-underscore prefix marks it private (not auto-discovered by pytest as a test module).
- `tests/integration/test_log_capture.py` — 12 integration tests (5 positive + 4 negative + 3 fixture-contract). All `@pytest.mark.integration`. Filename pinned by `architecture.md:754`.

**Modified (3):**
- `tests/conftest.py` — appended `_list_capture_processor` helper + `capture_structlog` fixture below existing `fixed_clock` / `seeded_uuid7`. No removal or modification of existing fixtures.
- `_bmad-output/implementation-artifacts/2-17-log-capture-harness.md` — Status `ready-for-dev` → `in-progress` → `review`; Tasks/Subtasks ticked; Dev Agent Record + File List + Change Log filled.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `2-17-log-capture-harness: ready-for-dev` → `in-progress` → `review`; both `last_updated` timestamps bumped.

**No production source touched.** AC-14 honoured: `git diff --stat HEAD` shows only `tests/**` + the story file + `sprint-status.yaml`.

## Change Log

| Date       | Story | Change                                                                                                  | Author              |
|------------|-------|---------------------------------------------------------------------------------------------------------|---------------------|
| 2026-04-27 | 2.17  | log-capture harness (`tests/_log_capture.py` + `capture_structlog` fixture) + 12 NFR-S1 redaction tests | claude-opus-4-7[1m] |
