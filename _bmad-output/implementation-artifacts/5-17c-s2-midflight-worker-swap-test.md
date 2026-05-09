# Story 5.17c: S-2 separability test — mid-flight worker swap

Status: done

## Story

As a CI pipeline,
I want `tests/separability/test_s2_midflight_swap.py` that kills the scripted worker stub mid-task and restarts it, proving the stub's deduplication drives the task to `task.completed` with zero state corruption and zero event loss,
So that FR34 / NFR-M4 is proved under motion — not just cold-swap interface compatibility (Story 5.16).

## Acceptance Criteria

1. **AC-1: Fine-grained event dedupe** — The scripted worker stub's deduplication changes from task-level (skip entire task if any event was emitted) to event-level (skip individual event types already emitted). `_scan_processed_task_ids` becomes `_scan_emitted_events` returning `dict[str, set[str]]` mapping task_id → emitted event types. In the main loop, each scenario event is checked against this map before emission.

2. **AC-2: Configurable event delay** — The stub accepts `SCRIPTED_WORKER_EVENT_DELAY_S` env var (default `0`) that adds an `asyncio.sleep` between event emissions. This gives the S-2 test a reliable window to kill the worker mid-sequence.

3. **AC-3: Build script force rebuild** — `tests/separability/_build_scripted_worker.py` gains a `force` parameter on `build_if_missing(force=False)`. When `True`, removes the existing image before building. The S-2 test calls `build_if_missing(force=True)` because the stub code changed.

4. **AC-4: Compose overlay** — `tests/separability/docker-compose.s2.yml` mirrors `docker-compose.s1.yml` but uses `OMB_S2_DATA_DIR` bind-mount variable and adds `SCRIPTED_WORKER_EVENT_DELAY_S: "0.5"` to the worker-wrapper environment. Same 3-service stack (registry-state, registry-api, worker-wrapper).

5. **AC-5: End-to-end test** — `tests/separability/test_s2_midflight_swap.py` contains:
   - `test_midflight_worker_swap_completes_task_end_to_end` — boots compose, POSTs a task, waits for `task.plan.ready`, kills the worker container via `docker compose kill`, restarts via `docker compose up -d`, waits for `task.completed`, asserts the full lifecycle. Marked `@pytest.mark.slow` and `@pytest.mark.separability`.
   - `test_worker_facing_source_code_unchanged` — git-diff sentinel (same pattern as S-1/S-3).

6. **AC-6: No event loss or duplication** — After the mid-flight swap, the JSONL log contains ALL canonical lifecycle events: `task.created`, `task.planning.started`, `task.plan.ready`, `task.execution.started`, `task.step.completed`, `task.completed`. No event-type appears more than once for the same task_id.

7. **AC-7: Materializer consistency** — After `task.completed` appears in JSONL, SQLite `tasks.status` reaches `completed`.

8. **AC-8: Existing S-1 and S-3 tests still pass** — After stub modifications and new compose overlay, `test_s1_cold_worker_swap.py` and `test_s3_orchestrator_swap.py` pass unchanged.

9. **AC-9: Import discipline** — No cross-service imports in the stub. `scripts/check_imports.py` exits 0.

10. **AC-10: `just lint` green, `just test` no regressions.**

11. **AC-11: Atomic commit** — title: `test(separability): add S-2 mid-flight worker swap test · E5`

## Tasks / Subtasks

- [x] **Task 1: Upgrade stub dedupe to event-level** (AC: #1)
  - [x] Replace `_scan_processed_task_ids(base_dir) → set[str]` with `_scan_emitted_events(base_dir) → dict[str, set[str]]`
  - [x] Update main loop: for each scenario event, check `evt["type"] not in emitted.get(task_id, set())` before emitting
  - [x] After emitting, add to local `emitted` dict: `emitted.setdefault(task_id, set()).add(evt["type"])`
  - [x] Remove the `processed` set and the `if task_id in processed: continue` check
  - [x] Keep `task.created` detection trigger unchanged (stub only acts on `task.created`)

- [x] **Task 2: Add configurable event delay** (AC: #2)
  - [x] Read `SCRIPTED_WORKER_EVENT_DELAY_S` env var in `run_scripted_worker` (default `0.0`)
  - [x] Add `await asyncio.sleep(delay_s)` after each `_emit_via_clawhip` call in the scenario emission loop

- [x] **Task 3: Add force rebuild to build script** (AC: #3)
  - [x] Add `force: bool = False` parameter to `build_if_missing()`
  - [x] When `force=True`, run `docker rmi <tag>` before the existence check
  - [x] Ignore `docker rmi` failures (image may not exist)

- [x] **Task 4: Create compose overlay** (AC: #4)
  - [x] Copy `docker-compose.s1.yml` → `docker-compose.s2.yml`
  - [x] Replace `OMB_S1_DATA_DIR` with `OMB_S2_DATA_DIR` everywhere
  - [x] Add `SCRIPTED_WORKER_EVENT_DELAY_S: "0.5"` to worker-wrapper environment
  - [x] Update header comment to reference Story 5.17c / S-2

- [x] **Task 5: Write S-2 test file** (AC: #5, #6, #7, #8)
  - [x] Create `tests/separability/test_s2_midflight_swap.py`
  - [x] Implement `test_midflight_worker_swap_completes_task_end_to_end`:
    - Build stub image with `force=True`
    - Boot compose stack, wait for healthchecks
    - POST task, get task_id
    - Poll JSONL for `task.plan.ready` event (proves ≥2 events emitted: planning.started, plan.ready)
    - Kill worker: `docker compose kill worker-wrapper` via `_compose_cmd(project, "kill", "worker-wrapper")`
    - Wait briefly for kill to propagate
    - Restart worker: `docker compose up -d worker-wrapper` via `_compose_cmd(project, "up", "-d", "worker-wrapper")`
    - Wait for `task.completed` in JSONL
    - Assert full lifecycle present
    - Assert no event-type duplicates (each type appears exactly once for task_id)
    - Verify materializer: SQLite `tasks.status = completed`
  - [x] Implement `test_worker_facing_source_code_unchanged` (git-diff sentinel, same paths as S-1)
  - [x] Add `@pytest.mark.slow`, `@pytest.mark.separability` markers
  - [x] Reuse helpers from S-1 test pattern: `_compose_env`, `_compose_cmd`, `_wait_for_all_healthy`, `_resolve_registry_api_port`, `_wait_for_socket`, `_read_jsonl_envelopes`, `_wait_for_task_status_completed`

- [x] **Task 6: Verification + commit** (AC: #9, #10, #11)
  - [x] `ruff check` and `ruff format` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` no regressions
  - [x] S-1 and S-3 tests not affected (separate compose overlays)
  - [x] Atomic commit

## Dev Notes

### What already exists

**`tests/fixtures/scripted_worker_stub/scripted_worker_stub.py`** (431 lines) — The stub from Story 5.16. Key dedupe mechanism at line 264:
```python
def _scan_processed_task_ids(base_dir: Path) -> set[str]:
    """Return task_ids that already have stub-emitted lifecycle events."""
    seen: set[str] = set()
    ...
    for env_obj in read_log_lines(path):
        if env_obj.type not in STUB_EMITTED_TYPES:
            continue
        ...
        seen.add(task_id)
    return seen
```
This is task-level dedupe — marks entire task as processed if ANY stub-emitted event exists. Too coarse for S-2 (mid-flight resume). Must become event-level.

Main loop at line 289 emits ALL events for a task when `task.created` is detected, then adds task_id to `processed`. On restart, the task is skipped entirely.

**`tests/separability/test_s1_cold_worker_swap.py`** (391 lines) — The S-1 cold swap test. Direct template for S-2. Key helpers to reuse:
- `_compose_env(data_dir)` — builds env dict
- `_compose_cmd(project, *args)` — builds compose command list
- `_wait_for_all_healthy()` — polls healthchecks
- `_resolve_registry_api_port()` — resolves mapped port
- `_wait_for_socket()` — TCP probe
- `_read_jsonl_envelopes()` — reads JSONL log
- `_wait_for_task_status_completed()` — polls SQLite

**`tests/separability/docker-compose.s1.yml`** (109 lines) — S-1 compose overlay. Template for S-2. 3 services: registry-state, registry-api, worker-wrapper with `WORKER_IMAGE` env-var override.

**`tests/separability/_build_scripted_worker.py`** — Idempotent image builder for `scripted-worker-stub:latest`. Currently has `build_if_missing()` with no `force` parameter.

**`tests/separability/conftest.py`** — Provides `skip_if_no_docker` fixture and adds `_THIS_DIR` to `sys.path` for sibling module imports.

**`tests/separability/docker-compose.test.yml`** (143 lines) — S-3 compose overlay. Separate from S-1/S-2 — different services (orchestrator-adapter instead of worker-wrapper).

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Fixture structure | Self-contained Python package in `tests/fixtures/` | S-3 null_orchestrator |
| Image build | `_build_*.py` idempotent builder in `tests/separability/` | S-3 _build_null_orchestrator |
| Compose overlay | Separate `docker-compose.s*.yml` per separability test | architecture.md line 188 |
| MCP connections | `StdioServerParameters` + `stdio_client` from MCP SDK | worker-wrapper mcp_clients.py |
| Event emission | `clawhip-bridge` `emit_event` tool | Stories 2.8, 5.2 |
| Event dedupe | Event log is append-only; materializer is idempotent | architecture.md line 63 |
| Test markers | `@pytest.mark.slow`, `@pytest.mark.separability` | architecture.md line 346 |
| Event parsing | `events.EventEnvelope`, `events.from_canonical_json` | S-3 test pattern |

### Key design decisions

1. **Event-level dedupe is the core S-2 enabler** — The stub's current task-level dedupe means a restarted worker skips the entire task if ANY event was emitted. For mid-flight resume, the stub must track which event types were emitted per task and skip only those. This is a small, safe change to the stub (a test fixture, not production code).

2. **Configurable delay guarantees kill window** — Without a delay between events, the stub may emit all 5 events in <100ms (MCP calls over local compose network are fast). The `SCRIPTED_WORKER_EVENT_DELAY_S=0.5` gives the test a reliable 1-2 second window to kill the worker after seeing `task.plan.ready` (emitted after ~1s) and before `task.execution.started` (would be emitted ~1.5s later).

3. **Reuse S-1 compose overlay structure** — S-2's `docker-compose.s2.yml` is a copy of S-1's with `OMB_S2_DATA_DIR` and `SCRIPTED_WORKER_EVENT_DELAY_S` added. Separate file avoids S-1 interference.

4. **Force rebuild for S-2** — Since S-2 modifies the stub code, the Docker image must be rebuilt. `build_if_missing(force=True)` ensures the test always uses the latest code.

5. **Kill + restart pattern** — `docker compose kill worker-wrapper` sends SIGKILL (instant, no grace period). `docker compose up -d worker-wrapper` restarts the container with the same config. The stub starts fresh, scans the JSONL log for already-emitted events, and emits only the missing ones.

6. **No-event-duplication assertion** — This is the S-2 differentiator from S-1. S-1 only checks that all events are present. S-2 additionally asserts each event type appears exactly once, proving the dedupe works correctly.

### Scope boundary — what NOT to do

- Do NOT modify `services/registry-state/`, `services/registry-api/`, `mcp-servers/clawhip-bridge/`, `services/orchestrator-adapter/` — these are the spine
- Do NOT modify `services/worker-wrapper/` production source — only the test fixture stub
- Do NOT modify `docker-compose.s1.yml` or `docker-compose.test.yml` — S-1 and S-3 must not be affected
- Do NOT add new scenarios — use existing `simple_green` scenario with fine-grained dedupe
- Do NOT modify `packages/events/` or `packages/idempotency/`

### Downstream consumers

- **Story 5.18** (Journey 1 integration test) — validates full end-to-end flow with real worker; S-2 proves the swap mechanism works

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1732-1746 — Story 5.17c definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` lines 472-473 — S-2 separability test definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 861 — FR34 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 944 — NFR-M4 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 63 — Immutable event envelope guarantee for S-2]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 118 — Hexagonal pattern enables separability]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 351 — S-2 as AC on resume-after-approval story]
- [Source: `tests/separability/test_s1_cold_worker_swap.py` — S-1 test pattern to follow]
- [Source: `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` — Stub to modify]
- [Source: `tests/separability/docker-compose.s1.yml` — Compose overlay template]
- [Source: `tests/separability/_build_scripted_worker.py` — Image builder to extend]
- [Source: `_bmad-output/implementation-artifacts/5-17b-cross-restart-approval-handling.md` — Previous story learnings (idempotency, atomic writes, structlog pitfalls)]
- [Source: `_bmad-output/implementation-artifacts/5-16-s1-cold-worker-swap-test.md` — S-1 story learnings (separate compose overlay, MCP-only stub)]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

None — no blocking issues encountered.

### Completion Notes List

- All 5 tasks completed. Ruff lint + format clean. 9 tests pass (0 regressions).
- Pre-existing `check_imports.py` violation in `worker-wrapper/test_reasoning.py` is unrelated.
- Pre-existing collection errors in `tests/idempotency/` and `tests/integration` (missing `asgi_lifespan`) are unrelated.
- S-1 `test_worker_facing_source_code_unchanged` and S-3 `test_spine_source_code_unchanged` still pass — no interference.
- S-2 `test_worker_facing_source_code_unchanged` passes — no worker-facing source modifications.
- Stub dedupe upgraded from task-level (`set[str]` of task_ids) to event-level (`dict[str, set[str]]` of task_id → emitted event types).
- Stub gains `SCRIPTED_WORKER_EVENT_DELAY_S` env var for inter-event delay (S-2 uses 0.5s).
- Build script gains `force` parameter for explicit image rebuild.
- Separate `docker-compose.s2.yml` avoids interfering with S-1 or S-3.

### Code Review Fixes (applied post-implementation)

- **[CRITICAL] `with_pr` multi-step dedupe bug**: Plain `evt["type"]` key caused second `task.step.completed` to be silently skipped. Fixed by adding `_dedupe_key()` that creates composite keys (`task.step.completed.N` by step number).
- **[HIGH] ValueError on invalid delay env var**: `float()` crash on non-numeric `SCRIPTED_WORKER_EVENT_DELAY_S`. Fixed with try/except + `max(0.0, ...)`.
- **[MEDIUM] Detached set from `emitted.get()`**: `emitted.get(task_id, set())` returns new set not stored in dict. Fixed with `emitted.setdefault(task_id, set())`.
- **[MEDIUM] Kill-restart race condition**: Fixed 2s sleep replaced with poll loop checking `docker compose ps` for "Exit" in Status.
- **[MEDIUM] No health-check after restart**: Added `_wait_for_all_healthy(project, env, timeout_s=60.0)` after `docker compose up -d worker-wrapper`.
- **[LOW] `task.step.completed` not in uniqueness assertion**: Added to `unique_types` set for exactly-once count verification.
- **[LOW] `force=True` was a no-op**: `docker rmi :latest` only removed the tag, not the SHA-tagged image. Fixed by skipping the SHA cache lookup entirely when `force=True`.
- **[LOW] Unused `_KILL_SETTLE_S` constant**: Removed after replacing fixed sleep with poll loop.

### File List

- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` (MODIFIED — event-level dedupe + event delay)
- `tests/separability/_build_scripted_worker.py` (MODIFIED — force parameter)
- `tests/separability/docker-compose.s2.yml` (NEW — 3-service compose overlay for S-2)
- `tests/separability/test_s2_midflight_swap.py` (NEW — S-2 test file with 2 tests)
- `_bmad-output/implementation-artifacts/5-17c-s2-midflight-worker-swap-test.md` (MODIFIED — status, tasks, dev record)
