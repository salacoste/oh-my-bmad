# Story 5.18: Journey 1 integration test (MVP gate — Phase 1)

Status: done

## Story

As a CI pipeline,
I want `tests/integration/test_journey_1_overnight.py` that exercises Journey 1 end-to-end with a scripted worker stub and auto-approval fixture,
So that the MVP gate is continuously verified — proving the complete event sequence from task submission through plan, execute, approval, push, PR draft, and completion.

## Acceptance Criteria

**Phase 1 (Epic 5 scope — this story):**

1. **AC-1: Journey 1 scenario** — The scripted worker stub gains a `journey_1` scenario that emits the complete Journey 1 lifecycle: `task.planning.started` → `task.plan.ready` → `task.execution.started` → `task.step.completed` → `task.awaiting_approval` → `task.push.completed` → `task.pr.opened` → `task.completed` (with PR URL, files_changed, tests_added, ci_state=green). Events after `task.awaiting_approval` are emitted after a configurable delay, giving the auto-approval stub time to inject `approval.granted`.

2. **AC-2: Auto-approval stub fixture** — A new test fixture at `tests/fixtures/auto_approval_stub/` that monitors the JSONL event log for `task.awaiting_approval` events and automatically emits `approval.granted` via clawhip-bridge's `emit_event` tool. Uses the same MCP SDK + structlog pattern as the scripted worker stub.

3. **AC-3: Build scripts and Dockerfile** — `tests/integration/_build_auto_approval.py` (idempotent builder, same SHA-tag pattern as `_build_scripted_worker.py`) and `tests/fixtures/auto_approval_stub/Dockerfile`.

4. **AC-4: Compose overlay** — `tests/integration/docker-compose.j1.yml` provides the Journey 1 service stack: registry-state, registry-api, worker-wrapper (scripted-worker-stub override), auto-approval-stub. Uses `OMB_J1_DATA_DIR` bind-mount variable. Adds `SCRIPTED_WORKER_EVENT_DELAY_S: "0.5"` and `SCRIPTED_WORKER_SCENARIO: "journey_1"` to worker environment.

5. **AC-5: Integration test** — `tests/integration/test_journey_1_overnight.py` contains:
   - `test_journey_1_overnight_pr` — boots compose, POSTs a task, waits for `task.completed`, asserts the complete Journey 1 event sequence is present in the JSONL log with correct ordering (approval.granted appears after awaiting_approval). Marked `@pytest.mark.integration` and `@pytest.mark.slow`.
   - `test_worker_facing_source_code_unchanged` — git-diff sentinel (same pattern as S-1/S-2).

6. **AC-6: Materializer consistency** — After `task.completed` appears in JSONL, SQLite `tasks.status` reaches `completed`.

7. **AC-7: No event duplication** — Each event type appears at most once per task_id (except `task.step.completed` which may appear N times by step number).

8. **AC-8: Existing tests pass** — S-1, S-2, S-3, and existing integration tests unaffected by stub modifications.

9. **AC-9: Import discipline** — No cross-service imports in stubs. `scripts/check_imports.py` exits 0.

10. **AC-10: `just lint` green, `just test` no regressions.**

11. **AC-11: Atomic commit** — title: `test(integration): add Journey 1 overnight PR integration test (Phase 1) · E5`

**Phase 2 (Epic 6 scope — deferred, not this story):**
- Re-enable with real approval flow (operator-decision endpoint + tier enforcement + license scan).
- Replace auto-approval stub with real Tier-3 gate.
- Wire `LifecycleManager` into `worker-wrapper/app/main.py` for production approval gating.

## Tasks / Subtasks

- [x] **Task 1: Add `journey_1` scenario to scripted worker stub** (AC: #1)
  - [x] Add `_scenario_journey_1(task_id, session_id)` to `scripted_worker_stub.py`
  - [x] Emit: planning.started → plan.ready → execution.started → step.completed → awaiting_approval → push.completed → pr.opened → task.completed (with PR URL, files_changed, tests_added, ci_state=green)
  - [x] Register in `SCENARIOS` dict as `"journey_1"`
  - [x] Add `"task.awaiting_approval"`, `"task.push.completed"`, `"task.pr.opened"` to `STUB_EMITTED_TYPES` frozenset for dedupe tracking
  - [x] Add step-number composite dedupe key for `task.step.completed` (already handled by `_dedupe_key`)

- [x] **Task 2: Create auto-approval stub fixture** (AC: #2)
  - [x] Create `tests/fixtures/auto_approval_stub/` package with `__init__.py`, `__main__.py`, `auto_approval_stub.py`
  - [x] The stub: connect to clawhip-bridge via MCP, tail JSONL for `task.awaiting_approval` events, emit `approval.granted` for each detected task_id
  - [x] Use same `StdioServerParameters` + `stdio_client` pattern as scripted worker stub
  - [x] Use same `read_log_lines` from `registry_state.adapters.event_log` for JSONL parsing
  - [x] Add readiness marker `/tmp/auto-approval-ready` for healthcheck
  - [x] Environment variables: `EVENT_LOG_DIR`, `CLAWHIP_BRIDGE_*` (same as worker stub)
  - [x] Add `pyproject.toml` with dependencies: `mcp`, `structlog`, `pydantic-settings`, and workspace references

- [x] **Task 3: Build scripts and Dockerfile** (AC: #3)
  - [x] Create `tests/fixtures/auto_approval_stub/Dockerfile` (same pattern as scripted worker stub Dockerfile)
  - [x] Create `tests/integration/_build_auto_approval.py` with `build_if_missing(force=False)` and SHA-tag caching
  - [x] Track source files: Dockerfile, auto_approval_stub.py, pyproject.toml, __init__.py, __main__.py, uv.lock

- [x] **Task 4: Create compose overlay** (AC: #4)
  - [x] Create `tests/integration/docker-compose.j1.yml`
  - [x] 4 services: registry-state, registry-api, worker-wrapper, auto-approval-stub
  - [x] Worker-wrapper uses `WORKER_IMAGE` env-var override (same as S-1/S-2)
  - [x] Auto-approval-stub uses `AUTO_APPROVAL_IMAGE` env-var override
  - [x] Both stubs share the same event log volume via `OMB_J1_DATA_DIR`
  - [x] Add `SCRIPTED_WORKER_SCENARIO: "journey_1"` and `SCRIPTED_WORKER_EVENT_DELAY_S: "0.5"` to worker env
  - [x] Healthchecks: registry-state/worker/auto-approval via `/tmp/ready`, registry-api via TCP socket probe
  - [x] `depends_on` with `condition: service_healthy` for startup ordering
  - [x] `restart: "no"` — test controls lifecycle

- [x] **Task 5: Write integration test** (AC: #5, #6, #7)
  - [x] Create `tests/integration/test_journey_1_overnight.py`
  - [x] Implement `test_journey_1_overnight_pr`:
    - Build both stub images (force=True for worker stub since scenario added)
    - Boot compose stack, wait for all healthchecks
    - Resolve registry-api port, TCP probe
    - POST `/v1/tasks` with `{"title": "j1-overnight-test"}`
    - Poll JSONL for `task.completed`
    - Assert complete Journey 1 event sequence present
    - Assert `approval.granted` appears after `task.awaiting_approval` in the event log
    - Assert no event-type duplication (except step.completed by step number)
    - Verify materializer: SQLite `tasks.status = completed`
  - [x] Implement `test_worker_facing_source_code_unchanged` (git-diff sentinel)
  - [x] Add `@pytest.mark.integration`, `@pytest.mark.slow` markers
  - [x] Update `tests/integration/conftest.py` with `skip_if_no_docker` fixture and sys.path for `_build_auto_approval.py`

- [x] **Task 6: Verification + commit** (AC: #8, #9, #10, #11)
  - [x] `ruff check` and `ruff format` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` no regressions
  - [x] S-1, S-2, S-3 tests unaffected
  - [x] Existing integration tests (`test_resume_after_approval`, `test_task_thread_binding`, etc.) unaffected
  - [x] Remove `tests/integration/test_placeholder.py` (replaced by real test)
  - [x] Atomic commit

## Dev Notes

### Journey 1 data flow (from architecture.md lines 862-906)

The complete end-to-end sequence this test must prove:

```
[Operator] → POST /v1/tasks → [registry-api]
  → MCP emit_event(task.created) → [clawhip-bridge] → JSONL append
  → [registry-state subscriber] → SQLite materialize

[worker-wrapper stub] subscribes to task.created
  → emits: planning.started, plan.ready, execution.started, step.completed
  → emits: task.awaiting_approval (approval gate)

[auto-approval stub] detects awaiting_approval
  → emits: approval.granted

[worker-wrapper stub] continues
  → emits: push.completed, pr.opened, task.completed (with PR URL)
```

### What already exists

**`tests/fixtures/scripted_worker_stub/`** — The scripted worker stub from Story 5.16/5.17c. Key infrastructure:
- MCP connection to clawhip-bridge via `StdioServerParameters`
- Event-level dedupe with `_dedupe_key()` (composite keys for step events)
- Configurable delay via `SCRIPTED_WORKER_EVENT_DELAY_S`
- Two scenarios: `simple_green` (1 step), `with_pr` (2 steps + PR URL)
- `_scan_emitted_events()` for resume-after-restart dedupe
- Docker image builder at `tests/separability/_build_scripted_worker.py`

**`tests/separability/docker-compose.s2.yml`** — S-2 compose overlay with 3 services. Direct template for J-1 overlay (add auto-approval-stub as 4th service).

**`tests/integration/test_resume_after_approval.py`** — Worker lifecycle FSM integration test (287 lines, 8 tests). Uses `LifecycleFSM`, `LifecycleManager`, `IdempotencyCacheStore`. Key pattern: in-memory SQLite for idempotency cache, stub callbacks for MCP/GitHub.

**`tests/integration/test_task_thread_binding.py`** — End-to-end event flow test. Pattern: write JSONL to disk, stub HTTP, assert sink behavior.

**`tests/integration/conftest.py`** — Currently empty placeholder (single-line docstring).

**`tests/integration/test_placeholder.py`** — Skip-marked placeholder that will be replaced by this story.

**`packages/events/`** — `EventEnvelope`, `from_canonical_json`, `SystemClock`, `new_session_id`, `new_worker_id`.

**`packages/idempotency/`** — `IdempotencyCacheStore` with `get_or_run()` for exactly-once execution.

### Key design decisions

1. **Phase 1 uses scripted stubs, not real Claude Code.** A real Claude Code worker requires API keys and is non-deterministic. Phase 1 proves the event flow end-to-end with deterministic stubs. Phase 2 (Epic 6) will test the real approval mechanism.

2. **Auto-approval stub is a separate service.** It runs as its own Docker container, monitoring the shared JSONL event log. This mirrors the real architecture where approval decisions come from an external agent (operator via Telegram). Using a separate service proves the inter-process event flow.

3. **Journey 1 scenario emits all events sequentially.** The stub doesn't gate on approval — it emits `task.awaiting_approval` then continues after the configured delay. The auto-approval stub detects and injects `approval.granted` concurrently. The test verifies ordering (approval.granted appears after awaiting_approval in the log).

4. **Event delay creates timing window.** `SCRIPTED_WORKER_EVENT_DELAY_S=0.5` between events gives the auto-approval stub ~0.5s to detect `awaiting_approval` and inject `approval.granted` before the worker stub emits completion events.

5. **Separate compose overlay per test type.** `docker-compose.j1.yml` is independent of S-1/S-2/S-3 overlays. Uses `OMB_J1_DATA_DIR` bind-mount variable.

6. **Remove test_placeholder.py.** The placeholder explicitly references Story 5.18: `"placeholder -- real tests land in Stories 5.18 / 7.9 / 7.10"`. Once the real test exists, the placeholder is obsolete.

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Fixture structure | Self-contained Python package in `tests/fixtures/` | S-3 null_orchestrator |
| Image build | `_build_*.py` idempotent SHA-tagged builder | S-1/S-2/S-3 |
| Compose overlay | Separate `docker-compose.*.yml` per test | architecture.md line 188 |
| MCP connections | `StdioServerParameters` + `stdio_client` from MCP SDK | scripted worker stub |
| Event emission | `clawhip-bridge` `emit_event` tool | Stories 2.8, 5.2 |
| Event dedupe | Composite dedupe keys via `_dedupe_key()` | Story 5.17c |
| Test markers | `@pytest.mark.slow`, `@pytest.mark.integration` | architecture.md line 346 |
| Event parsing | `events.EventEnvelope`, `events.from_canonical_json` | S-3 test pattern |

### Scope boundary — what NOT to do

- Do NOT modify `services/registry-state/`, `services/registry-api/`, `mcp-servers/clawhip-bridge/`, `services/orchestrator-adapter/`
- Do NOT wire `LifecycleManager` into `worker-wrapper/app/main.py` — that's Phase 2 (Epic 6 scope)
- Do NOT implement real Tier-3 approval flow — Phase 1 uses auto-approval stub
- Do NOT modify `services/worker-wrapper/` production source — only test fixture stubs
- Do NOT modify `docker-compose.s1.yml`, `docker-compose.s2.yml`, or `docker-compose.test.yml`
- Do NOT add a `journey_1` scenario that gates on approval — emit all events sequentially, let auto-approval stub inject concurrently

### structlog gotcha (from 5.17b)

Never use `event=` as a keyword argument with structlog loggers — it clashes with structlog's positional `event` parameter. Use `fsm_event=`, `env_event=`, or similar.

### Downstream consumers

- **Epic 6** — Phase 2 of this test replaces the auto-approval stub with real Tier-3 approval flow
- **Stories 7.9/7.10** — Journey 3 and Journey 6 integration tests follow the same pattern
- **Bootstrap Milestone** — Story 5.18 completion is the MVP gate for the "first end-to-end Journey 1 run"

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1749-1770 — Story 5.18 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` lines 190-209 — Journey 1 "The Overnight PR"]
- [Source: `_bmad-output/planning-artifacts/prd.md` lines 812-858 — FR1/FR2/FR3/FR7/FR9/FR10/FR18a/FR31]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 917 — NFR-R6 (80% unattended completion rate)]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 862-906 — Journey 1 data flow diagram]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 1059-1068 — Minimum viable path to Bootstrap Milestone]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 344-348 — Integration test markers and layout]
- [Source: `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` — Stub to extend with journey_1 scenario]
- [Source: `tests/separability/docker-compose.s2.yml` — Compose overlay template]
- [Source: `tests/integration/test_resume_after_approval.py` — Integration test pattern]
- [Source: `_bmad-output/implementation-artifacts/5-17c-s2-midflight-worker-swap-test.md` — S-2 story learnings]
- [Source: `_bmad-output/implementation-artifacts/5-17b-cross-restart-approval-handling.md` — LifecycleManager + structlog gotcha]
- [Source: `tests/integration/test_placeholder.py` — Placeholder to replace]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

- Deferred `_build_auto_approval` / `_build_scripted_worker` imports inside `test_journey_1_overnight_pr` — pytest collects (imports) test modules before running conftest fixtures, so module-level imports of path-injected modules fail during collection when tests are marker-skipped (e.g. `just test` uses `-m "not slow"`).

### Review Findings

- [x] [Review][Patch] Blocking I/O on event loop in auto-approval stub [auto_approval_stub.py:151]
- [x] [Review][Patch] Sentinel test `:!` pathspec magic silently ignored after `--` separator [test_journey_1_overnight.py:406-421]
- [x] [Review][Patch] `asyncio.run()` in sync test — convert to synchronous sqlite3 polling [test_journey_1_overnight.py:384]
- [x] [Review][Patch] Sentinel test's `CalledProcessError` fallback too broad [test_journey_1_overnight.py:424-433]
- [x] [Review][Patch] `STUB_EVENTS` missing `approval.granted` for dedup check [test_journey_1_overnight.py:70]
- [x] [Review][Patch] `JOURNEY_1_EVENTS` constant defined but never used [test_journey_1_overnight.py:56-67]
- [x] [Review][Patch] `_read_new_lines` swallows JSONDecodeError silently [auto_approval_stub.py:73]
- [x] [Review][Patch] Auto-approval stub crashes on MCP emit failure — no retry [auto_approval_stub.py:165]
- [x] [Review][Patch] Inconsistent host: `localhost` vs `127.0.0.1` [test_journey_1_overnight.py:180,325]
- [x] [Review][Patch] Unused deps in auto_approval_stub pyproject.toml [pyproject.toml:10-11]
- [x] [Review][Defer] ~62 lines duplicated code between stubs (4 functions) — deferred, intentional fixture independence
- [x] [Review][Defer] Incomplete JSONL line causes offset stall — deferred, pre-existing in worker stub
- [x] [Review][Defer] Worker doesn't gate on approval before continuing — deferred, by-design Phase 1 per spec scope boundary

### Completion Notes List

- All 6 tasks complete. ruff check/format clean, check_imports.py clean (pre-existing violation in worker-wrapper unrelated), just test no regressions (pre-existing asgi_lifespan errors unrelated).
- test_placeholder.py removed.
- test_worker_facing_source_code_unchanged passes.

### File List

- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` — added `journey_1` scenario + 3 new event types
- `tests/fixtures/auto_approval_stub/__init__.py` — new package
- `tests/fixtures/auto_approval_stub/__main__.py` — entrypoint shim
- `tests/fixtures/auto_approval_stub/auto_approval_stub.py` — auto-approval stub main
- `tests/fixtures/auto_approval_stub/pyproject.toml` — dependencies
- `tests/fixtures/auto_approval_stub/Dockerfile` — multi-stage build
- `tests/integration/_build_auto_approval.py` — idempotent SHA-tagged builder
- `tests/integration/docker-compose.j1.yml` — 4-service compose overlay
- `tests/integration/conftest.py` — added skip_if_no_docker + sys.path injection
- `tests/integration/test_journey_1_overnight.py` — main integration test
- `tests/integration/test_placeholder.py` — removed
