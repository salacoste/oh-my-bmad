# Story 5.16: S-1 separability test — cold worker swap (FR34 / NFR-M4)

Status: ready-for-dev

## Story

As a CI pipeline,
I want `tests/separability/test_s1_cold_worker_swap.py` to replace the Claude Code worker
with a scripted-stub worker via env-var override and prove orchestrator + registry code is unchanged,
So that FR34 / NFR-M4 is verified as a fact, not a claim.

## Acceptance Criteria

1. **AC-1: Scripted worker stub fixture** — A `tests/fixtures/scripted_worker_stub/` directory containing a self-contained Python package (`__init__.py`, `__main__.py`, `scripted_worker_stub.py`, `pyproject.toml`, `Dockerfile`) that:
   - Connects to the same three MCP servers as the real worker (`task-registry`, `session-registry`, `clawhip-bridge`) using `MCPClientGroup` with the same `StdioServerParameters` pattern
   - Registers a session, acquires a worktree lock, emits `session.started`
   - Reads a canned task from `task-registry` resource `task://list`, emits a predefined event sequence (`task.planning.started`, `task.plan.ready`, `task.execution.started`, `task.step.completed` x N, `task.completed`) via `clawhip-bridge`
   - Emits `session.finished` on SIGTERM and exits cleanly
   - Accepts the canned event sequence via a JSON config file or env var (e.g., `SCRIPTED_WORKER_SCENARIO`)

2. **AC-2: Docker image build** — A `tests/separability/_build_scripted_worker.py` module (following `_build_null_orchestrator.py` pattern) that:
   - Builds `scripted-worker-stub:latest` from `tests/fixtures/scripted_worker_stub/Dockerfile`
   - Is idempotent (no-op if image already exists with matching tag)
   - Multi-stage build from repo root context, resolves workspace deps via `uv sync`

3. **AC-3: Compose overlay** — The existing `tests/separability/docker-compose.test.yml` gains a `worker-wrapper` service definition (or a separate `docker-compose.s1.yml` overlay) that:
   - Uses `image: ${WORKER_IMAGE:?WORKER_IMAGE must be set by the S-1 harness}` (matching S-3's `ORCHESTRATOR_IMAGE` pattern)
   - Shares the same network as `registry-state`, `registry-api`, `orchestrator-adapter`
   - Has the same env vars as the real worker-wrapper (`WORKER_*` prefix) but with canned MCP command paths

4. **AC-4: End-to-end test** — `tests/separability/test_s1_cold_worker_swap.py` contains:
   - `test_worker_swap_with_scripted_stub_completes_task_end_to_end` — boots the compose stack with `WORKER_IMAGE=scripted-worker-stub:latest`, creates a task via `registry-api`, asserts the task transitions to `completed` within 60s, and asserts the event log contains the canonical lifecycle events. Marked `@pytest.mark.slow` and gated on `skip_if_no_docker`.
   - `test_worker_facing_source_code_unchanged` — runs `git diff --name-only` against the worker-facing source paths (`services/registry-*`, `mcp-servers/*`, `services/orchestrator-adapter/`) and asserts zero modifications. Sub-second; runs unconditionally.

5. **AC-5: No source changes to spine** — The scripted worker stub must NOT import from `services/worker-wrapper/`. It only uses `packages/events/` (for event construction) and the MCP SDK. This is the core separability proof: the stub proves the MCP surface contract is sufficient without depending on worker-wrapper internals.

6. **AC-6: Existing S-3 test still passes** — After the compose overlay changes, `test_s3_orchestrator_swap.py` still passes unchanged. If a separate compose overlay is used for S-1, S-3 must not be affected.

7. **AC-7: Test markers** — E2E test marked `@pytest.mark.slow` and `@pytest.mark.separability`. Git-diff sentinel runs without markers.

8. **AC-8: Import discipline** — No cross-service imports in the scripted worker stub. `scripts/check_imports.py` exits 0 (any new violations from stub are excluded via the `tests/fixtures/` exclusion).

9. **AC-9: `just lint` green, `just test` no regressions.**

10. **AC-10: Atomic commit** — title: `test(separability): add S-1 cold worker swap test with scripted stub · E5`

## Tasks / Subtasks

- [ ] **Task 1: Create scripted worker stub fixture** (AC: #1, #5)
  - [ ] Create `tests/fixtures/scripted_worker_stub/` directory
  - [ ] Add `pyproject.toml` with `workspace` deps (`events`, `mcp` SDK, `structlog`, `pydantic-settings`)
  - [ ] Add `__init__.py` and `__main__.py` (entrypoint: structlog config + MCP startup)
  - [ ] Add `scripted_worker_stub.py` with `ScriptedWorkerSettings` (env prefix `WORKER_`), MCP client connections, session lifecycle, canned event emission
  - [ ] Add `Dockerfile` — multi-stage build from repo root, `uv sync`, entrypoint `python -m scripted_worker_stub`
  - [ ] Add `.dockerignore`

- [ ] **Task 2: Create image builder** (AC: #2)
  - [ ] Add `tests/separability/_build_scripted_worker.py` — idempotent `docker build` for `scripted-worker-stub:latest`
  - [ ] Follow `_build_null_orchestrator.py` pattern exactly

- [ ] **Task 3: Create/update compose overlay** (AC: #3)
  - [ ] Add `worker-wrapper` service to `tests/separability/docker-compose.test.yml` with `image: ${WORKER_IMAGE:-}` and proper env vars, network config
  - [ ] OR create `tests/separability/docker-compose.s1.yml` as separate overlay
  - [ ] Ensure S-3 test is not affected (AC: #6)

- [ ] **Task 4: Write S-1 test file** (AC: #4, #7)
  - [ ] Create `tests/separability/test_s1_cold_worker_swap.py`
  - [ ] Implement `test_worker_swap_with_scripted_stub_completes_task_end_to_end` — compose boot, task creation, lifecycle assertion
  - [ ] Implement `test_worker_facing_source_code_unchanged` — git-diff sentinel
  - [ ] Add `@pytest.mark.slow` and `@pytest.mark.separability` markers
  - [ ] Follow `test_s3_orchestrator_swap.py` structure: `_compose_env()`, `_wait_for_url()`, `_read_event_log()`, etc.

- [ ] **Task 5: Verification + commit** (AC: #8, #9, #10)
  - [ ] `ruff check` and `ruff format` clean
  - [ ] `scripts/check_imports.py` exits 0
  - [ ] `just test` no regressions (S-3 test may need Docker — verify it still works or skip with `skip_if_no_docker`)
  - [ ] Atomic commit

## Dev Notes

### What already exists

**`tests/separability/test_s3_orchestrator_swap.py`** (581 lines) — The S-3 test is the direct template for S-1. Key patterns to follow:
- `_REPO_ROOT`, `_COMPOSE_FILE`, `_NULL_TAG` module-level constants
- `_compose_env()` — builds `os.environ` dict for compose
- `_wait_for_url()` — polls HTTP endpoint with timeout
- `_read_event_log()` — reads JSONL events from SQLite
- `test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end` — the e2e test
- `test_spine_source_code_unchanged` — the git-diff sentinel
- Uses `skip_if_no_docker` fixture from `conftest.py`

**`tests/separability/docker-compose.test.yml`** (143 lines) — 3-service compose overlay (registry-state, registry-api, orchestrator-adapter). S-1 needs a similar overlay that includes the worker-wrapper service instead of replacing the orchestrator. Two approaches:
1. **Extend existing compose file** — add a `worker-wrapper` service that's only active when `WORKER_IMAGE` is set
2. **Separate compose file** — create `docker-compose.s1.yml` with a 4-service stack (registry-state, registry-api, orchestrator-adapter, worker-wrapper)

The separate file approach is safer because it avoids affecting S-3's compose stack.

**`tests/separability/_build_null_orchestrator.py`** (~90 lines) — Idempotent image builder. Key pattern:
- `build_if_needed()` function checks if image exists, builds if not
- Uses `subprocess.run(["docker", "build", ...])` from repo root
- Injected into `sys.path` via conftest.py

**`tests/fixtures/null_orchestrator/`** — Complete fixture package. Key files:
- `null_orchestrator.py` (525 lines) — Connects to 3 MCP servers, reads tasks, emits canned events, responds to SIGTERM
- `Dockerfile` — Multi-stage build: `oh-my-bmad-base:local` → `uv sync --workspace` → entrypoint
- `pyproject.toml` — Workspace deps: `events`, `mcp`, `structlog`, `pydantic-settings`

**`tests/separability/conftest.py`** — Provides `skip_if_no_docker` fixture.

**`services/worker-wrapper/`** — The real worker implementation. The scripted stub must NOT import from here, but should follow the same MCP connection pattern.

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Fixture structure | Self-contained Python package in `tests/fixtures/` | S-3 null_orchestrator |
| Image build | `_build_*.py` idempotent builder in `tests/separability/` | S-3 _build_null_orchestrator |
| Compose overlay | Separate `docker-compose.s*.yml` per separability test | architecture.md |
| MCP connections | `StdioServerParameters` + `stdio_client` from MCP SDK | worker-wrapper mcp_clients.py |
| Event emission | `clawhip-bridge` `emit_event` tool | Stories 2.8, 5.2 |
| Session lifecycle | `session.started` → heartbeat → `session.finished` | Story 5.2 |
| Task lifecycle | `task.planning.started` → `task.plan.ready` → `task.execution.started` → `task.step.completed` → `task.completed` | Stories 5.11-5.13 |
| Test markers | `@pytest.mark.slow`, `@pytest.mark.separability` | architecture.md line 346 |
| Event parsing | `events.EventEnvelope`, `events.from_canonical_json` | S-3 test pattern |

### Key design decisions

1. **Separate compose file for S-1** — Using `docker-compose.s1.yml` avoids interfering with S-3's compose stack. The S-1 stack needs all 4 services (registry-state, registry-api, orchestrator-adapter, worker-wrapper) running together.

2. **Scripted worker stub uses same env prefix as real worker** — The stub reads `WORKER_*` env vars for MCP server connection commands, matching the real worker's `WorkerSettings`. This proves FR34: a single `WORKER_IMAGE` env var swaps the implementation while all other config remains identical.

3. **Canned scenario via env var** — `SCRIPTED_WORKER_SCENARIO` env var names a predefined event sequence (e.g., `"simple_green"`, `"with_pr"`). The stub has a dict of canned scenarios. Default is `"simple_green"`: plan → execute 1 step → complete with green CI.

4. **No Claude Code dependency in stub** — The stub doesn't need Node.js or the Claude Code CLI. It's a pure Python process that only talks MCP. This proves the MCP surface contract is self-sufficient.

5. **Git-diff assertion scoped to worker-facing paths** — The sentinel checks `services/registry-*`, `mcp-servers/*`, `services/orchestrator-adapter/`. It does NOT check `services/worker-wrapper/` because the whole point is that the worker is swappable — changes to worker-wrapper are allowed.

### Downstream consumers

- **Story 5.17c** (S-2 mid-flight swap) — extends S-1 to test worker swap during active task execution
- **Story 5.18** (Journey 1 integration test) — validates full end-to-end flow with real worker

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1674-1686 — Story 5.16 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 861 — FR34 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 944 — NFR-M4 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 118 — hexagonal pattern enables separability]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 960 — FR34/FR35 enforcement table]
- [Source: `tests/separability/test_s3_orchestrator_swap.py` — S-3 test pattern to follow]
- [Source: `tests/fixtures/null_orchestrator/` — Fixture package pattern to follow]
- [Source: `tests/separability/_build_null_orchestrator.py` — Image builder pattern to follow]
- [Source: `tests/separability/docker-compose.test.yml` — Compose overlay pattern]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — MCP connection pattern]

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
