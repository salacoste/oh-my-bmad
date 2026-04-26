# Story 2.11: Synthetic crash-injection harness

Status: done

## Story

As **the CI pipeline (and operators verifying NFR-R2)**,
I want **a pytest harness that boots the registry tier under docker compose, drives a task through each of 4 lifecycle phases by appending synthesized events to the JSONL log, kills the host with `docker compose stop --timeout 1`, restarts with `docker compose up -d`, and asserts post-restart state-reconstruction with zero duplicate events**,
so that **NFR-R2 (zero tasks lost) and NFR-R1 (100% restart recoverability) are continuously verified by CI rather than discovered via production audit-log review**.

## Acceptance Criteria

1. **AC-1: New harness directory** `tests/crash-injection/` (already exists from Story 1.5 with placeholder). Add:
   - `tests/crash-injection/test_restart_recovery.py` — main test module
   - `tests/crash-injection/_compose.py` — compose orchestration helpers
   - `tests/crash-injection/_events.py` — event synthesis helpers
   - `tests/crash-injection/docker-compose.test.yml` — overlay overriding the data volume to a host bind-mount under `pytest`'s `tmp_path` (so the harness can read JSONL + assert against materialized SQLite without entering the container)

2. **AC-2: Compose helpers** in `_compose.py`:
   - `class CrashHarness` — context manager. `__init__(tmp_path: Path, project_name: str)`. `__enter__` runs `docker compose -p <name> -f docker-compose.yml -f tests/crash-injection/docker-compose.test.yml up -d registry-state` (limited to the registry-state service for Phase 1 — registry-api/telegram/etc. are not yet end-to-end testable). `__exit__` runs `docker compose -p <name> down -v`.
   - `kill_with_compose_stop(timeout: int = 1)` — invokes `docker compose -p <name> stop --timeout <timeout>` (Linux path).
   - `kill_with_signal_kill()` — invokes `docker compose -p <name> kill --signal SIGKILL` (macOS path; equivalent NFR-R1 trigger).
   - `kill(method: Literal["stop", "sigkill"])` — auto-routes by `sys.platform == "darwin"` (sigkill on macOS, stop on Linux); explicit override via param.
   - `restart()` — runs `docker compose -p <name> up -d registry-state` and waits for the healthcheck to flip to "healthy" via `docker inspect --format='{{.State.Health.Status}}' <container>` polled at 1s intervals up to 60s timeout.
   - `event_log_dir() -> Path` — returns the host-side bind-mount path under `tmp_path` (e.g. `tmp_path / "data" / "registry" / "events"`).
   - `db_path() -> Path` — host-side path to `state.sqlite3` for read-only assertion queries (open via `aiosqlite` with `mode=ro`).

3. **AC-3: Event synthesis helpers** in `_events.py`:
   - `synthesize_envelope(*, type: str, schema_version: str, task_id: str, payload: BaseModel, clock: Clock, ...) -> EventEnvelope` — wraps `EventEnvelope.create(...)` with sane test defaults (Actor `(kind="system", id="crash-harness")`, fresh request_id).
   - `append_envelope(log_dir: Path, env: EventEnvelope, *, day: date | None = None) -> Path` — synchronous JSONL append using `to_canonical_json` (no fsync — matches `EventLogWriter`'s default). Returns the path written to.
   - `drive_task_through_phase(harness: CrashHarness, *, task_id: str, phase: Phase, clock: Clock) -> list[EventEnvelope]` — appends the canonical event sequence for a given phase (see AC-5) and returns the list of envelopes written. Does NOT wait for materialization; the test must call `wait_for_materialization` separately.
   - `wait_for_materialization(db_path: Path, *, last_event_id: str, timeout_s: float = 30.0) -> None` — polls the read-only DB via `aiosqlite` (1s interval) until `SELECT 1 FROM events WHERE id = :last_event_id` returns a row, or raises `TimeoutError`.

4. **AC-4: `Phase` enum** in `_events.py`:
   ```python
   class Phase(StrEnum):
       PLANNING = "planning"           # task.created → task.planning.started
       EXECUTING = "executing"         # + task.plan.ready → task.execution.started
       AWAITING_APPROVAL = "awaiting_approval"  # + task.approval_requested
       VERIFYING = "verifying"         # + task.summary_emitted (Phase 1 proxy; real "verifying" status lands in Epic 5 — documented inline)
   ```

5. **AC-5: Phase event sequences** (canonical, additive — each phase reuses the prior sequence + new events):

   | Phase | Event sequence appended |
   |---|---|
   | `PLANNING` | `task.created` → `task.planning.started` |
   | `EXECUTING` | (above) → `task.plan.ready` → `task.execution.started` (with synthesized `session_id`) |
   | `AWAITING_APPROVAL` | (above) → `task.approval_requested` |
   | `VERIFYING` | (above) → `task.summary_emitted` |

   Phase 1 mapping note: the spec's `verifying` lifecycle phase (Epic 5 worker-lifecycle territory) does not yet have a typed status in the materializer. `task.summary_emitted` is the closest existing event signaling post-execution observability. Document this proxy mapping prominently in the test module docstring + add a `# TODO Story 5.x` for the real `verifying` state once Epic 5 lands.

6. **AC-6: Per-phase test functions** in `test_restart_recovery.py`:
   - `test_crash_recovery_planning_phase` — drives PLANNING, kills, restarts, asserts.
   - `test_crash_recovery_executing_phase` — drives EXECUTING, kills, restarts, asserts.
   - `test_crash_recovery_awaiting_approval_phase` — drives AWAITING_APPROVAL, kills, restarts, asserts.
   - `test_crash_recovery_verifying_phase` — drives VERIFYING, kills, restarts, asserts.

   All four marked `@pytest.mark.crash` AND `@pytest.mark.slow`. PR-level CI excludes `slow`; nightly runs them.

7. **AC-7: Per-phase assertions** (each phase test must verify):
   - **AC-7a — Zero duplicate events.** Compute `count_jsonl = total non-empty lines across all *.jsonl in event_log_dir`. Compute `count_db = SELECT COUNT(*) FROM events`. Assert `count_jsonl == count_db`.
   - **AC-7b — All emitted event_ids materialized.** Compute `ids_jsonl = {env.event_id for env in synthesized_envelopes}`. Query `SELECT id FROM events WHERE id IN (:ids_jsonl)`. Assert returned set equals `ids_jsonl`.
   - **AC-7c — Task row exists post-restart.** `SELECT id, status FROM tasks WHERE id = :task_id` returns a row.
   - **AC-7d — `last_event_id` matches the final synthesized envelope.** `tasks.last_event_id == synthesized_envelopes[-1].event_id`.
   - **AC-7e — Replay-cursor advanced.** `MAX(events.emitted_at_monotonic_ns) >= synthesized_envelopes[-1].emitted_at_monotonic_ns` (the materializer applied past the kill point).
   - **AC-7f — No partial JSONL line.** After restart, `recover_all_logs` (Story 2.4) trimmed any partial trailing line. Assert each `.jsonl` file ends with `\n`.

8. **AC-8: Summary artifact** — emitted to `_bmad-output/test-artifacts/crash-injection-summary-<UTC-timestamp>.json` after the suite completes (via a session-scoped fixture finalizer). Schema:
   ```json
   {
     "harness_version": "1",
     "started_at": "<iso>",
     "completed_at": "<iso>",
     "platform": "darwin|linux",
     "kill_method": "stop|sigkill",
     "phases": [
       {"phase": "planning", "task_id": "t-...", "events_synthesized": N,
        "events_in_db_post_restart": N, "duplicate_count": 0,
        "restart_duration_s": <float>, "passed": true}
       ...
     ],
     "passed_total": 4,
     "failed_total": 0
   }
   ```
   Path created via `output_folder / test-artifacts/...`. Read `output_folder` from `_bmad/core/config.yaml` if available; fallback to `_bmad-output/`.

9. **AC-9: Compose overlay** `tests/crash-injection/docker-compose.test.yml`:
   - Override `oh-my-bmad-data` to a bind-mount: `${OMB_HARNESS_DATA_DIR:-./.harness-data}:/var/lib/oh-my-bmad`. The harness sets `OMB_HARNESS_DATA_DIR` to its `tmp_path / "data"` and exports it before invoking compose.
   - Override `registry-state` to set `REGISTRY_STATE_LOG_DIR=/var/lib/oh-my-bmad/registry/events` and `REGISTRY_STATE_DB_URL=sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3` (matches existing defaults but explicit for the test environment).
   - Drop services other than `registry-state` (Phase 1 scope).

10. **AC-10: justfile recipe** — add `just test-crash` that runs `uv run pytest -m crash --tb=short -v tests/crash-injection/`. Document in justfile help comment that this requires Docker. CI nightly workflow must invoke this recipe.

11. **AC-11: CI wiring** — add `.github/workflows/nightly.yml` (if not already present from Story 1.5) with a `crash-injection` job that:
    - Runs on `ubuntu-latest` (Linux compose-stop path).
    - Sets up Docker, builds the base image (`just build-base`), then runs `just test-crash`.
    - Uploads the `_bmad-output/test-artifacts/crash-injection-summary-*.json` as a workflow artifact.
    - Schedule: `cron: "0 3 * * *"` (3am UTC daily).

    If `nightly.yml` already exists from Story 1.5, ADD a job to it; do not replace.

12. **AC-12: Docker availability skip** — `tests/crash-injection/conftest.py` adds a fixture that calls `docker info` (subprocess, 5s timeout) and `pytest.skip("docker not available")` if it fails. The skip reason includes "Story 2.11 crash-injection requires Docker — install or run via CI". Local dev without Docker does not break `just test`.

13. **AC-13: macOS compatibility** — when `sys.platform == "darwin"`, `CrashHarness.kill()` defaults to `kill_with_signal_kill` (per NFR-R1: "docker stop --signal SIGKILL on macOS"). The summary artifact records `kill_method`.

14. **AC-14: Idempotency under harness re-run** — running `pytest tests/crash-injection/` twice in the same `tmp_path` must succeed (the harness uses a unique compose project name per test, e.g. `omb-crash-{uuid4().hex[:8]}`, and tears down volumes in `__exit__`).

15. **AC-15: mypy --strict clean** — both `_compose.py` and `_events.py` and `test_restart_recovery.py` must pass `mypy --strict`. If `tests/crash-injection/` is not currently in the mypy strict list (per justfile's `lint` recipe), ADD it.

16. **AC-16: `check_event_registry` and `check_single_writer` green** — the harness emits events using the existing schema_registry types (no new types added); the harness writes JSONL but never SQLite. Both gates pass.

17. **AC-17: Regression** — `just test` (PR-level, excludes `slow`) count stays at **476 passed, 6 skipped** (no new fast tests). `just lint` 7/7 green. `mypy --strict` source file count grows by ≥3. `just test-crash` passes locally on a developer machine with Docker — 4 passed, 0 failed (slow; ~3-5 minutes). The placeholder `tests/crash-injection/test_placeholder.py` MAY remain (skipped) OR be deleted; if deleted, sprint-status's `2-11` and `2-12` references in the placeholder skip message remain valid (Story 2.12 still uses the placeholder mechanism for atomic-edit harness).

18. **AC-18: Atomic commit** titled `feat(tests): story 2.11 — synthetic crash-injection harness (registry-state restart recovery) · FR24 NFR-R1 NFR-R2`.

## Tasks / Subtasks

- [x] **Task 1: Compose overlay + harness orchestration** (AC: #1, #2, #9, #13, #14)
  - [x] Create `tests/crash-injection/docker-compose.test.yml` with bind-mount override + service-subset.
  - [x] Create `tests/crash-injection/_compose.py` with `CrashHarness` class.
  - [x] Implement `kill()` auto-routing by platform; expose `kill_with_compose_stop` and `kill_with_signal_kill` separately.
  - [x] Implement `restart()` with healthcheck polling (60s timeout).
  - [x] Use unique compose project name per test (`omb-crash-{uuid4().hex[:8]}`).

- [x] **Task 2: Event synthesis helpers** (AC: #3, #4, #5)
  - [x] Create `tests/crash-injection/_events.py` with `synthesize_envelope`, `append_envelope`, `drive_task_through_phase`, `wait_for_materialization`.
  - [x] Define `Phase` StrEnum with 4 values.
  - [x] Implement phase event sequences (canonical, additive).
  - [x] Document `VERIFYING` proxy mapping inline.

- [x] **Task 3: Per-phase test functions + assertions** (AC: #6, #7, #12)
  - [x] Create `tests/crash-injection/test_restart_recovery.py` with 4 phase tests.
  - [x] Each test wires CrashHarness → drive_task_through_phase → kill → restart → wait_for_materialization → 6 AC-7 assertions.
  - [x] Add `_skip_if_no_docker` fixture in `conftest.py` (extend existing).
  - [x] Mark all 4 tests `@pytest.mark.crash @pytest.mark.slow`.

- [x] **Task 4: Summary artifact emission** (AC: #8)
  - [x] Session-scoped fixture in `conftest.py` collects per-phase results into a list.
  - [x] Finalizer writes JSON summary to `_bmad-output/test-artifacts/`.
  - [x] Resolve `output_folder` from `_bmad/core/config.yaml` if available; fallback `_bmad-output/`.

- [x] **Task 5: justfile recipe + CI wiring** (AC: #10, #11)
  - [x] Add `test-crash` recipe to justfile.
  - [x] Add or extend `.github/workflows/nightly.yml` with a `crash-injection` job (Linux runner, builds base image, runs `just test-crash`, uploads artifact).

- [x] **Task 6: Type discipline + regression + atomic commit** (AC: #15, #16, #17, #18)
  - [x] mypy --strict clean on `tests/crash-injection/` (extend `lint` recipe if needed).
  - [x] `just test` count unchanged at 476+5 (placeholder deleted per AC-17).
  - [x] `just test-crash` passes locally (4 passed, ~34s on warm machine with cached image).
  - [x] `just lint` 8/8 green; `check_event_registry` + `check_single_writer` green.
  - [x] Single atomic commit per AC-18.

### Review Findings

#### Batch 1 — Adversarial code review (Acceptance Auditor + Blind Hunter + Edge Case Hunter round 1)

**CRITICAL**

- [x] **[Review][Patch] C1: AC-7 semantic gap — pre-kill wait defeats reconstruction test** [`test_restart_recovery.py:_run_phase_test`] — **CRITICAL.** Pre-kill `wait_for_materialization` materialized events before kill, making post-restart assertions verify durability not JSONL replay. Fix: dropped pre-kill wait entirely; sequence is now append→kill→restart→wait→assert. Single `asyncio.run` in `_async_phase_body` consolidates all async work.
- [x] **[Review][Patch] C2: `Base.metadata.create_all` in production startup bypasses Alembic** [`app/main.py:run_subscriber`] — **CRITICAL.** Unconditional `create_all` conflicts with Alembic-as-authoritative-migration-source. Fix: gated behind `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1` env var; set only in `docker-compose.test.yml`. Production schema via Alembic (Story 2.14).
- [x] **[Review][Patch] C3: Nightly workflow — `just` not installed on runner** [`.github/workflows/nightly.yml`] — **CRITICAL.** Fix: added `extractions/setup-just@v2` step before build step.
- [x] **[Review][Patch] C4: Empty `ids_jsonl` produces invalid SQL `IN ()`** [`test_restart_recovery.py:_assert_phase_recovery`] — **CRITICAL.** Fix: assert `len(envelopes_typed) >= 1` at function entry with clear message.
- [x] **[Review][Patch] C5: Per-task duplicate assertion accumulates across phases** [`test_restart_recovery.py:_assert_phase_recovery`] — **CRITICAL.** Global `count_jsonl == count_db` didn't catch per-task duplicates. Fix: added per-task `json_extract(payload_json, '$.task_id')` count alongside global invariant.
- [x] **[Review][Patch] C6: Test order dependency under pytest-randomly** [`test_restart_recovery.py`] — **CRITICAL.** Fix: added `pytest_collection_modifyitems` hook in `conftest.py` pinning 4 phase tests in declaration order.

**MAJOR**

- [x] **[Review][Patch] M1: `subprocess.run(check=False)` swallows stderr in `__exit__`** [`_crash_compose.py:CrashHarness.__exit__`] — **MAJOR.** Fix: log stderr on non-zero returncode via `logging.warning`; added `--remove-orphans` flag.
- [x] **[Review][Patch] M2: `_wait_for_healthy` raises but leaves container running** [`_crash_compose.py:CrashHarness.__enter__`] — **MAJOR.** Fix: wrapped `_wait_for_healthy()` in try/except; on TimeoutError runs `compose down -v --remove-orphans` then re-raises.
- [x] **[Review][Patch] M3: `_container_id()` race after kill** [`_crash_compose.py:_container_id`] — **MAJOR.** Fix: filter `compose ps --status=running -q registry-state` to skip dead containers.
- [x] **[Review][Patch] M4: `docker inspect` Health.Status empty handling** [`_crash_compose.py:_wait_for_healthy`] — **MAJOR.** Fix: format string captures `State.Status`, `State.Health.Status`, `State.ExitCode`; all reported in timeout message.
- [x] **[Review][Patch] M5: `/tmp/ready` false positive on `compose start`** [`app/main.py`] — **MAJOR.** Fix: delete `/tmp/ready` in `finally` branch of `run_subscriber` on graceful shutdown. Comment clarified (Em1): `/tmp` is writable container layer, not tmpfs — deletion needed for stop/start reuse cycle.
- [x] **[Review][Patch] M6: `harness.restart()` uses `up -d` instead of `compose start`** [`_crash_compose.py:CrashHarness.restart`] — **MAJOR.** Fix: use `compose start registry-state` first; fall back to `up -d` only if start fails.
- [x] **[Review][Patch] M7: Three separate `asyncio.run` per phase test** [`test_restart_recovery.py:_run_phase_test`] — **MAJOR.** Fix: consolidated all async work into single `asyncio.run(_async_phase_body(...))`.
- [x] **[Review][Patch] M8: `wait_for_materialization` swallows all exceptions** [`_crash_events.py:wait_for_materialization`] — **MAJOR.** Fix: only catch `aiosqlite.OperationalError`; re-raise everything else.
- [x] **[Review][Patch] M9: Bind-mount path on macOS may not be Docker-shared** [`_crash_compose.py:CrashHarness.__enter__`] — **MAJOR.** Fix: write sentinel file + `compose exec test -f ...`; clear error message on failure.
- [x] **[Review][Patch] M10: `sys.path.insert` with generic module names pollutes namespace** [`conftest.py`] — **MAJOR.** Fix: renamed `_compose.py` → `_crash_compose.py`, `_events.py` → `_crash_events.py`; updated all imports.
- [x] **[Review][Patch] M11: Per-test summary `passed: True` hardcoded** [`test_restart_recovery.py:_run_phase_test`] — **MAJOR.** Fix: wrap body in try/except AssertionError; append `passed=False, error_message=str(exc)` before re-raising.
- [x] **[Review][Patch] M12: Compose project name 8-hex collision at 65k concurrent runs** [`_crash_compose.py:CrashHarness.__init__`] — **MAJOR.** Fix: use `f"omb-crash-{os.getpid()}-{uuid4().hex[:12]}"`.
- [x] **[Review][Patch] M13: `harness.restart()` return value discarded** [`test_restart_recovery.py:_run_phase_test`] — **MAJOR.** Fix: capture `restart_duration_s` directly from `harness.restart()`.
- [x] **[Review][Patch] M15: AC-7d for AWAITING_APPROVAL/VERIFYING — `last_event_id` update verification** [`handlers.py`] — **MAJOR.** Verified: `handle_task_approval_requested` (line 260) and `handle_task_summary_emitted` (line 236) both update `last_event_id`. No code change needed; documented in test docstrings.
- [x] **[Review][Patch] M16: `synthesize_envelope` missing `task_id` param** [`_crash_events.py:synthesize_envelope`] — **MAJOR.** Fix: added `task_id: str` kw-only parameter; runtime assertion guards against payload/kwarg mismatch.
- [x] **[Review][Patch] M17: AC-17 spec says 7/7, implementation is 8/8** — **MAJOR.** Fix: documented in Spec Amendments section below; 8/8 is correct per AC-15 intent.

**MINOR**

- [x] **[Review][Patch] Mn1: subprocess calls without `text=True`** [`_crash_compose.py`] — Added `text=True` to all `subprocess.run` calls so `.stderr` is `str` not `bytes`.
- [x] **[Review][Patch] Mn2: Pre-creating `event_log_dir` undocumented** [`_crash_compose.py:CrashHarness.__init__`] — Added docstring explaining the root-ownership avoidance rationale.
- [x] **[Review][Patch] Mn3: Dead code `_ = datetime.now(UTC)`** [`test_restart_recovery.py:1102,1387`] — Removed dead assignment and unused `datetime` import.
- [x] **[Review][Patch] Mn4: `type` parameter shadows builtin** [`_crash_events.py:synthesize_envelope`] — Renamed `type` → `event_type`; updated all call sites in `drive_task_through_phase`.
- [x] **[Review][Patch] Mn5: `dict(env.payload)` for non-BaseModel branch** [`_crash_events.py:append_envelope`] — Changed to `{**env.payload}` for safety with frozen mappings.
- [x] **[Review][Patch] Mn6: `from events.clock import Clock` not type-only** [`_crash_events.py`] — Moved `Clock` import into `TYPE_CHECKING` block; already had `from events.clock import Clock` there.
- [x] **[Review][Patch] Mn7: Naive YAML parser** [`conftest.py:_resolve_output_folder`] — Replaced with `yaml.safe_load` when PyYAML available; falls back to line-scan with explanatory comment.
- [x] **[Review][Patch] Mn8: Filename collision in same UTC second** [`conftest.py`] — Added `os.getpid()` to filename.
- [x] **[Review][Patch] Mn9: `OMB_PRINT_CRASH_ARTIFACT` env var undocumented** [`conftest.py`] — Added to module docstring.
- [x] **[Review][Patch] Mn10: Missing `concurrency:` block in nightly** [`nightly.yml`] — Added `concurrency: {group: nightly-crash, cancel-in-progress: false}`.
- [x] **[Review][Patch] Mn11: `if-no-files-found: warn` permits silent failure** [`nightly.yml`] — Changed to `error`.
- [x] **[Review][Patch] Mn12: `timeout-minutes: 15` may be tight** [`nightly.yml`] — Bumped to 25.
- [x] **[Review][Patch] Mn13: Add 10s buffer above healthcheck total** [`_crash_compose.py:CrashHarness.restart`] — Timeout bumped to 70s (60s poll + 10s buffer).
- [x] **[Review][Patch] Mn14: Add `--strict-markers` to `just test-crash`** [`justfile:test-crash`] — Deferred; `--strict-markers` is already enforced globally via `pyproject.toml`.
- [x] **[Review][Patch] Mn16: Use `new_request_id` instead of `new_uuid7` for request_id** [`_crash_events.py`] — `new_request_id` exists in `events.__init__`; updated to use it.
- [x] **[Review][Patch] Mn17: Module docstring "fsync implicitly on close" claim wrong** [`_crash_events.py`] — Corrected to: kill targets the registry-state container, not the harness; writes safe via kernel page cache.
- [x] **[Review][Patch] Mn18: `parents[2]` comment says "3 parents" but uses index 2** [`conftest.py:898`] — Fixed comment to accurately describe `parents[0]=crash-injection, [1]=tests, [2]=repo root`.
- [x] **[Review][Patch] Mn19: Add `REGISTRY_STATE_AUTO_CREATE_SCHEMA: "1"` to test compose** [`docker-compose.test.yml`] — Added (pairs with C2 fix).
- [x] **[Review][Patch] Mn20: Verify `Path` imported in `app/main.py`** — Already imported at line 41; no change needed.

**DEFER/DISMISS**

- [x] **[Review][Defer] BH "time.monotonic_ns determinism gap"** — Known partial-determinism trade-off. Clock anchoring via `time.monotonic_ns() + 1_000_000` is the accepted approach; documented in `make_clock_and_rng` docstring.
- [x] **[Review][Defer] BH "platform.system() == Darwin Windows assumption"** — Windows containers are out of scope for this project. No action needed.
- [x] **[Review][Defer] BH "fsync after append_envelope"** — Kill targets container, not harness. Comment in `_crash_events.py` updated to clarify.

---

#### Batch 2 — Edge Case Hunter round 2

**CRITICAL**

- [x] **[Review][Patch] EC1: Linux bind-mount uid mismatch (EACCES)** [`_crash_compose.py` + `docker-compose.test.yml`] — **CRITICAL.** Container runs as uid 10002/gid 10000; host runner is uid 1001 on ubuntu-latest. Fix: pre-create all bind-mount dirs with `chmod(0o777)`; add `user: "${OMB_HARNESS_UID:-10002}:${OMB_HARNESS_GID:-10000}"` in `docker-compose.test.yml`; export `OMB_HARNESS_UID`/`OMB_HARNESS_GID` from `_compose_env()`.
- [x] **[Review][Patch] EC2: AC-7e cursor-advancement assertion vacuously true** [`test_restart_recovery.py:_assert_phase_recovery`] — **CRITICAL.** With pre-kill wait removed (C1), this becomes meaningful. Added `pre_kill_max_mono_ns` baseline captured before kill; post-restart asserts `post_max > pre_kill_max` (strictly advanced). Comment explains why post-restart-only wait is what makes this non-vacuous.

**MAJOR**

- [x] **[Review][Patch] EM1: Linux kill path is graceful shutdown, not crash** [`_crash_compose.py:kill_with_compose_stop`] — **MAJOR.** `compose stop --timeout 1` sends SIGTERM → subscriber drains in <100ms. Fix: SIGKILL (`kill_hard()`) is now the default on ALL platforms. `kill_graceful()` kept for debugging but not used by default. `KillMethod` renamed to `"hard"|"graceful"` for semantic clarity (Em4).
- [x] **[Review][Patch] EM2: Nightly path filter omits `Dockerfile.base` and `justfile`** [`nightly.yml`] — **MAJOR.** Fix: added both to push-trigger path filter.
- [x] **[Review][Patch] EM3: `docker info` 5s timeout too short for cold Docker Desktop** [`conftest.py:skip_if_no_docker`] — **MAJOR.** Fix: bumped to 30s.
- [x] **[Review][Patch] EM4: Docker-skip leaves summary artifact indistinguishable from clean run** [`conftest.py:crash_summary_collector`] — **MAJOR.** Fix: added `"status": "skipped"|"passed"|"failed"` field to artifact payload.
- [x] **[Review][Patch] EM5: `_skip_if_no_docker` leading underscore makes it non-standard** [`conftest.py`] — **MAJOR.** Fix: renamed to `skip_if_no_docker` with `autouse=True`; `crash_harness` fixture still explicitly depends on it to ensure ordering.

**MINOR**

- [x] **[Review][Patch] Em1: `/tmp/ready` comment wrong about tmpfs** [`app/main.py`] — Comment corrected: `/tmp` is the writable container layer, not tmpfs. Deletion on graceful shutdown prevents stale healthcheck on `compose start` reuse cycles.
- [x] **[Review][Patch] Em2: `Phase` docstring claims "additive sequences" for one task** [`_crash_events.py:Phase`] — Fixed: each phase synthesizes an **independent** task; "additive" refers to event sequences within a single phase, not across phase tests.
- [x] **[Review][Patch] Em3: `_container_id()` assumes single replica** [`_crash_compose.py`] — Added assertion: raises `RuntimeError` if `len(lines) > 1`.
- [x] **[Review][Patch] Em4: `KillMethod` Literal semantically inconsistent** [`_crash_compose.py`] — Renamed `"stop"|"sigkill"` → `"hard"|"graceful"` (applied as part of EM1).
- [x] **[Review][Patch] Em5: `make_clock_and_rng` docstring wrong about `+1_000_000` rationale** [`_crash_events.py`] — Fixed: `TickingClock` returns `start_ns` on first call (not `start_ns + tick`); offset prevents collision with prior cursor; correct rationale documented.
- [x] **[Review][Patch] Em7: No assertion that `mode=ro` URI actually opens DB read-only** [`test_restart_recovery.py`] — Added `_assert_ro_enforced()` that attempts INSERT and asserts `OperationalError("readonly")`.

**DEFER**

- [x] **[Review][Defer] Em6: No assertion that subscriber emitted "startup replay" log line** — Would require log-capture wiring not present in Phase 1. Deferred to Story 2.17.
- [x] **[Review][Defer] Snapshot replay path not exercised** — `snapshot_interval=1000`, harness emits ~17 events per session. Story 2.6 unit tests cover snapshot-restore. Deferred.
- [x] **[Review][Defer] Idempotency cache not exercised** — Story 2.13 territory.
- [x] **[Review][Defer] WAL leftover verification post-restart** — SQLite WAL auto-checkpointed on connection close; not a correctness risk for aiosqlite. Deferred.
- [x] **[Review][Defer] `_wait_for_healthy` retry on cold image-pull** — 60s budget is sufficient for warm runner; cold pull covered by `build-base` + `build registry-state` steps in nightly before `test-crash` runs.

## Dev Notes

### Architecture context

- **`tests/crash-injection/test_restart_recovery.py`** is explicitly named in Architecture line 743 as the NFR-R1 / NFR-R2 enforcement point.
- **NFR-R1** (PRD line 912): 100% restart recoverability across `docker compose restart` (Linux) / `docker stop --signal SIGKILL` (macOS), kill at each lifecycle phase.
- **NFR-R2** (PRD line 913): zero tasks lost per calendar month, continuously verified by **synthetic-crash-injection harness**. Any non-zero count is Sev1.
- **FR24** (PRD line 847): Registry persists task and session state surviving host/container/bot restart with zero loss.

### Lifecycle-phase scoping

The 4 lifecycle phases the spec mentions (`planning`, `executing`, `awaiting_approval`, `verifying`) map onto the EVENT SEQUENCES required to drive a task to that conceptual point:

- `planning`: `task.created` → `task.planning.started` (handler updates `tasks.status = "planning"`).
- `executing`: above + `task.plan.ready` → `task.execution.started` (handler updates `tasks.status = "executing"`).
- `awaiting_approval`: above + `task.approval_requested` (handler updates `last_event_id` + `updated_at` only — no `awaiting_approval` status enum exists in Phase 1; it's a derived state). The harness asserts the event is in the events table post-restart, NOT that `tasks.status == "awaiting_approval"` (that would fail).
- `verifying`: above + `task.summary_emitted` (Phase 1 proxy — the real `verifying` status lands in Epic 5 worker-lifecycle stories).

The harness verifies **event-log integrity + materialization idempotency** at each conceptual phase. It does NOT verify `tasks.status` value drift across phases beyond what the existing materializer handlers do.

### Why a registry-state-only subset of compose

Phase 1 has no operational telegram-gateway, no real worker, and no orchestrator-adapter that emits events. End-to-end testing the full stack via real-task submission is impossible until Epic 3 (Telegram) and Epic 5 (workers). Story 2.11's harness exercises **the only failure surface that exists today**: the JSONL→SQLite materialization path owned by `registry-state`. Direct JSONL synthesis is the canonical Phase 1 way to drive lifecycle events (matches Story 2.5/2.6 unit tests' approach).

When Epic 5 lands real worker emission, Story 5.18 (Journey 1 integration test) extends the harness's coverage to include real-worker-driven phases. Story 2.11's harness scaffold is designed to be additive — `_compose.py` + `_events.py` are reusable.

### Compose mechanics

Use `docker compose -p <project_name> ...` to namespace the harness's compose state away from the operator's `just dev`. The `-p` flag sets `COMPOSE_PROJECT_NAME`; without it the default project name is the directory name (`oh-my-bmad`), which would collide with a developer's running stack.

The bind-mount overlay is the key: it gives the test direct host-side access to the JSONL log + SQLite DB without `docker exec` / `docker cp` (slower, Docker Desktop sandboxing on macOS makes the latter unreliable). Set `OMB_HARNESS_DATA_DIR` env var BEFORE invoking compose; compose substitutes it into the volume bind path.

The compose `init: true` directive (already in base compose) ensures `tini` reaps zombies. Combined with `docker compose stop --timeout 1` this delivers a SIGTERM → 1s grace → SIGKILL sequence that mimics realistic operator-induced kill (mid-write, mid-fsync).

### Healthcheck polling for `restart()`

The base compose has a `*healthcheck: ["CMD", "test", "-f", "/tmp/ready"]` directive. registry-state must touch `/tmp/ready` after lifespan startup completes (verify in `app/main.py`; if not present, this story may need to ADD a `/tmp/ready` touchpoint to the subscriber loop's startup completion — note as a pre-flight check during Task 1).

If the touchpoint is missing, add it to `services/registry-state/src/registry_state/app/main.py` after `register_default_handlers(materializer)` and before the tail loop:

```python
Path("/tmp/ready").touch()
```

This is a small additive change consistent with the healthcheck directive shipped in compose; flag in Completion Notes.

### Event ordering + monotonic_ns

Synthesized envelopes must use strictly-increasing `emitted_at_monotonic_ns` to satisfy the materializer's cursor advancement. Use a `TickingClock` (Story 2.2) or a `FrozenClock` whose `monotonic_ns` increments by 1ms between calls. Use `clock.now()` for `emitted_at` (UTC-aware) — `EventEnvelope`'s `_emitted_at_utc` validator rejects naive datetimes (Story 2.10 round-2 reinforced this for `last_heartbeat_at`).

### Snapshot interaction

Story 2.6's snapshot policy fires every `snapshot_interval` events (default 1000). At Phase 1 with ~5 events per harness phase × 4 phases = ~20 events, no snapshot will fire during a single test run — the harness exercises the **events-table-replay** path, NOT the snapshot-restore path. Snapshot-restore coverage is OK to defer (Story 2.6's existing unit tests cover that path).

If the harness wants to also exercise snapshot-restore: pass `snapshot_interval=2` via env var (`REGISTRY_STATE_SNAPSHOT_INTERVAL`) — but this requires `registry-state/app/main.py` to read that env var. If not currently wired, do NOT add it in this story; flag in Dev Agent Record as a Story 2.6 follow-up.

### Test artifact directory

`_bmad-output/test-artifacts/` does not yet exist. The session-scoped fixture should `mkdir(parents=True, exist_ok=True)` before writing. Add a `.gitignore` entry `_bmad-output/test-artifacts/*.json` (artifacts are CI-only, NOT committed).

### Performance budget

Each phase test does: compose up (~10s) + drive (~1s) + kill (~2s) + restart (~10s) + wait_for_materialization (~3s) + assertions (~1s) ≈ 27s. 4 phases × 27s ≈ 1m48s. With overhead (docker pull, image build cache misses on cold CI), budget **5 minutes total**.

Mitigation: tests share a single compose stack across 4 phases by using a **session-scoped harness fixture** that boots once and tears down once. Per-phase tests reset state via `docker compose stop && docker compose up -d` (without `down -v`). This drops total time to ~80s. Document in test module docstring.

### Previous Story Intelligence

- **Story 2.10** added the 4 failure-detection event types but they're NOT used in the lifecycle phases the harness exercises. The harness uses the 8 task.* event types from Stories 2.5 + 2.8.
- **Story 2.6** established `restore_state_from_latest_snapshot` + `compute_replay_cursor`; the harness exercises the cursor advance path implicitly (no explicit snapshot in Phase 1 default).
- **Story 2.5** established `Materializer.apply_many` with `INSERT ... ON CONFLICT DO NOTHING` — the **idempotency belt-and-braces** that AC-7a depends on.
- **Story 2.4** established `EventLogWriter` + `recover_all_logs(base_dir)` (trims trailing partial lines on startup) — the harness's AC-7f assertion verifies this.
- **Story 2.3** established the `events.id UNIQUE` constraint — duplicate event-row INSERTs raise an integrity error (caught by ON CONFLICT DO NOTHING).
- **Story 1.4** shipped the docker-compose stack + `just dev` recipe — the harness reuses these.
- **Story 1.5** created the `tests/crash-injection/` tree + `@pytest.mark.crash` marker.

### What this story does NOT do

- **No real-worker integration** — Phase 1 has no real workers. Real-task-submission flows land in Story 5.18 (Journey 1 integration).
- **No snapshot-restore stress test** — Story 2.6's unit tests cover that. Adding snapshot pressure is deferred to a Phase 2 hardening pass.
- **No write-interrupt mid-syscall coverage** — that's Story 2.12's atomic-edit harness (different failure mode, different mechanism).
- **No idempotency-cache coverage** — that's Story 2.13's 100× replay test.
- **No telegram-gateway / orchestrator / clawhip-daemon coverage** — those services don't exist functionally yet.
- **No `REGISTRY_STATE_SNAPSHOT_INTERVAL` env-var wiring** — flag for Story 2.6 follow-up if needed.

### File List (predicted)

**New (4):**
- `tests/crash-injection/test_restart_recovery.py`
- `tests/crash-injection/_compose.py`
- `tests/crash-injection/_events.py`
- `tests/crash-injection/docker-compose.test.yml`

**Modified (3-5):**
- `tests/crash-injection/conftest.py` — add `_skip_if_no_docker` + summary-artifact session fixture.
- `tests/crash-injection/test_placeholder.py` — DELETE (real tests now exist) OR keep with updated skip reason. Recommend DELETE.
- `justfile` — add `test-crash` recipe; potentially extend `lint` recipe to include `tests/crash-injection/` in mypy strict scope.
- `.github/workflows/nightly.yml` — add or extend with `crash-injection` job.
- `services/registry-state/src/registry_state/app/main.py` — IF `/tmp/ready` touchpoint is missing, add it (small additive change for healthcheck compatibility).
- `.gitignore` — add `_bmad-output/test-artifacts/*.json`.

### References

- `epics.md` Story 2.11 (lines 867–882).
- `architecture.md` line 174 — `tests/crash-injection/` tree purpose.
- `architecture.md` line 558 — `nightly.yml` runs the slow matrix.
- `architecture.md` line 743 — `test_restart_recovery.py` filename mandate.
- `architecture.md` line 834 — recovery.py exercised by this harness.
- `prd.md` FR24, NFR-R1, NFR-R2 — the requirements being verified.
- `services/registry-state/src/registry_state/domain/recovery.py` — `restore_state_from_latest_snapshot`, `compute_events_max_cursor`.
- `services/registry-state/src/registry_state/adapters/event_log.py` — `recover_all_logs`, `_read_new_envelopes_since`, `EventLogWriter`.
- `services/registry-state/src/registry_state/domain/handlers.py` — handler dispatch table the materializer uses.
- `services/registry-state/src/registry_state/test_event_log.py` — pattern for direct JSONL appends in tests.
- `2-4-event-log-append-writer.md`, `2-5-event-log-subscriber-materializer.md`, `2-6-snapshot-capture-replay.md` — supporting infrastructure.
- `docker-compose.yml` (root) — base compose with healthcheck directives.
- `docker-compose.macos.yml` — macOS overlay (referenced for understanding the multi-overlay pattern).
- `justfile` — `dev`, `build-base`, `lint`, `test`, `test-slow` recipes.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 4.6 (claude-sonnet-4-6) — executor subagent

### Debug Log References

- Pydantic v2 `model_dump()` on outer `EventEnvelope` returns `{}` for nested BaseModel payloads (union type `dict[str,Any]|BaseModel` serialization edge-case). Fixed in `append_envelope` by extracting payload dict before rebuilding envelope for `to_canonical_json`.
- `TickingClock(start_ns=0)` causes first `monotonic_ns()` to return 0, which fails materializer's `> cursor_ns` filter on fresh DB. Fixed with `start_ns=time.monotonic_ns()+1_000_000` so each phase's clock anchors at the real host monotonic value — guaranteeing events are always > any prior phase's cursor.
- SQLite DB never created in container on fresh boot (no Alembic migration). Fixed by adding `Base.metadata.create_all` at startup in `run_subscriber`.
- `py.typed` marker required for mypy `--explicit-package-bases` to resolve registry_state imports from the crash-injection test tree.

### Completion Notes List

- AC-17: `just test` is 476 passed, **5 skipped** (not 6). The placeholder `tests/crash-injection/test_placeholder.py` was deleted (AC-17 permits this), reducing the skip count by 1. All other 5 placeholders remain.
- AC-17: `just lint` is **8/8** (not 7/7) — added a second mypy invocation for `tests/crash-injection/` with `--explicit-package-bases`.
- Added `/tmp/ready` healthcheck touchpoint to `services/registry-state/src/registry_state/app/main.py` (predicted in Dev Notes) + `services/registry-state/src/registry_state/py.typed` marker for mypy.
- Clock anchoring: `make_clock_and_rng()` uses `time.monotonic_ns()+1_000_000` as `start_ns` so each phase's synthesized events have strictly-greater monotonic_ns than any prior phase's materialized cursor.

### File List

**New (6):**
- `tests/crash-injection/test_restart_recovery.py`
- `tests/crash-injection/_compose.py`
- `tests/crash-injection/_events.py`
- `tests/crash-injection/docker-compose.test.yml`
- `tests/crash-injection/conftest.py` (extended from stub — fixtures added)
- `services/registry-state/src/registry_state/py.typed`

**Modified (4):**
- `services/registry-state/src/registry_state/app/main.py` — added `Base.metadata.create_all` + `/tmp/ready` touchpoint
- `justfile` — added `test-crash` recipe + second mypy invocation in `lint`
- `.github/workflows/nightly.yml` — created (new file, crash-injection job)
- `.gitignore` — added `_bmad-output/test-artifacts/*.json`

**Deleted (1):**
- `tests/crash-injection/test_placeholder.py`

### Spec Amendments (from code review)

- **C1 — Drop pre-kill wait**: The original implementation called `wait_for_materialization` BEFORE the kill, materializing events prior to the crash. This defeats AC-7's purpose (verifying JSONL→SQLite reconstruction). Amended: the post-restart wait is the ONLY `wait_for_materialization` call per phase test. The sequence is: append events → SIGKILL → restart → wait (post-restart) → assert. This is the only path that exercises NFR-R1 / NFR-R2.
- **C2 — `REGISTRY_STATE_AUTO_CREATE_SCHEMA` env var**: `Base.metadata.create_all` is now gated behind `REGISTRY_STATE_AUTO_CREATE_SCHEMA=1`. This var is set ONLY in `tests/crash-injection/docker-compose.test.yml`. Production gets schema via Alembic (Story 2.14). This is a test-only bypass.
- **EM1 — SIGKILL on all platforms**: Changed default kill from `compose stop --timeout 1` (Linux) to `docker compose kill --signal SIGKILL` (both platforms). `compose stop` sends SIGTERM which the subscriber handles gracefully (<100ms drain). SIGKILL is the only true crash-recovery test per NFR-R1. `kill_with_compose_stop` renamed to `kill_graceful()`; `kill_with_signal_kill` renamed to `kill_hard()`. `KillMethod` Literal values changed from `"stop"|"sigkill"` to `"hard"|"graceful"`.
- **M10 / File renames**: `_compose.py` → `_crash_compose.py`, `_events.py` → `_crash_events.py`. The original generic names could collide with other test trees' `sys.path` entries. All imports updated.
- **M16 — `synthesize_envelope(task_id=...)` param restored**: `task_id: str` added as a kw-only parameter per AC-3. Also verifies `payload.task_id == task_id` at runtime to catch wiring bugs.
- **Mn16 — `new_request_id` over `new_uuid7`**: Using the semantically correct `new_request_id()` from `events.__init__` instead of raw `new_uuid7()` for the `request_id` field.
- **AC-17 7/7 → 8/8 acknowledgement**: `just lint` runs 8 steps (added second mypy invocation for `tests/crash-injection/` with `--explicit-package-bases`). 8/8 is correct per AC-15's intent. The story's AC-17 text saying "7/7" was written before AC-15 added the crash-injection mypy step.

### Change Log

| Date | Version | Description |
|---|---|---|
| 2026-04-26 | 1.0 | Story 2.11 implemented — synthetic crash-injection harness, 4 phase tests, CI nightly job |
| 2026-04-26 | 1.1 | Code-review fixes: 6 CRITICAL (C1-C6), 16 MAJOR (M1-M17 ex M14), 20 MINOR (Mn1-Mn20), 2 CRITICAL round-2 (EC1-EC2), 5 MAJOR round-2 (EM1-EM5), 6 MINOR round-2 (Em1-Em5, Em7); deferred: Em6, snapshot, idempotency-cache, WAL. Final: just test 476+5, just lint 8/8, just check-gates-self-test 3/3. Files renamed: _compose→_crash_compose, _events→_crash_events. |
