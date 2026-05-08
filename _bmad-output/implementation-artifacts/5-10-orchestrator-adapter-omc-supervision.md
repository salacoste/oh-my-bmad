# Story 5.10: orchestrator-adapter: OMC subprocess supervision

Status: done

## Story

As the platform,
I want `services/orchestrator-adapter/` to supervise the vendored OMC subprocess via `adapters/omc_runner.py` and translate between OMC's task model and the platform's typed events via `domain/task_dispatch.py`,
So that OMC's orchestration logic drives platform tasks without leaking OMC specifics into registry or worker code.

## Acceptance Criteria

1. **AC-1: OMC subprocess supervision** — `adapters/omc_runner.py` provides an async `OMCRunner` class that spawns the OMC CLI (`node bridge/cli.cjs`) from `upstream/omc/`, captures structured stdout, handles graceful shutdown (SIGTERM → 5s → SIGKILL), enforces timeouts, and returns an `OMCResult` dataclass.

2. **AC-2: Task dispatch translation** — `domain/task_dispatch.py` provides:
   - `build_omc_prompt(task_id, title, hint, repo)` → constructs OMC input from platform task fields
   - `parse_omc_plan_output(raw_output)` → extracts plan summary from OMC output
   - `build_planning_started_payload(task_id)` → `TaskPlanningStartedPayload` dict
   - `build_plan_ready_payload(task_id, plan_summary)` → `TaskPlanReadyPayload` dict

3. **AC-3: Event emission** — When the adapter processes a task, it emits `task.planning.started` then `task.plan.ready` typed events via clawhip-bridge MCP server. Events use `EventEnvelope.create()` with `actor_kind="orchestrator"`.

4. **AC-4: MCP client connections** — `adapters/mcp_clients.py` provides `MCPClientGroup` (same pattern as worker-wrapper Story 5.1) connecting to `clawhip-bridge`, `task-registry`, and `session-registry` MCP servers via stdio. `verify_connectivity()` calls `list_tools()` on each concurrently.

5. **AC-5: Configuration** — `app/config.py` defines `OrchestratorSettings` with `pydantic-settings` (`env_prefix="ORCHESTRATOR_"`):
   - MCP server commands/args (clawhip_bridge, task_registry, session_registry)
   - `omc_path` (default: `upstream/omc`), `omc_timeout_s` (default: 120)
   - `actor_id` (auto-generated UUIDv7 if empty)
   - `poll_interval_s` (default: 5) for task polling

6. **AC-6: Main lifecycle** — `app/main.py` runs the adapter loop: connect MCP clients → poll task-registry for tasks needing planning → drive OMC → emit events → repeat. Signal handling via `loop.add_signal_handler`. Ready-file healthcheck.

7. **AC-7: Import discipline** — No Python file imports from `upstream/omc/` directly (enforced by `scripts/check_imports.py` — `upstream/` is excluded from scan roots, so this is structural). No `services/` file outside `orchestrator-adapter/` references OMC internals. `# noqa: IMP001` per architecture import rules.

8. **AC-8: Directory structure** — Files at `services/orchestrator-adapter/src/orchestrator_adapter/`:
   - `app/__init__.py`, `app/main.py`, `app/config.py`
   - `adapters/__init__.py`, `adapters/omc_runner.py`, `adapters/mcp_clients.py`
   - `domain/__init__.py`, `domain/task_dispatch.py`
   - `__main__.py` — updated entrypoint (replaces hello-world scaffold)
   - `__init__.py` — updated to export `OMCRunner`, version `"0.2.0"`
   - `test_omc_runner.py`, `test_task_dispatch.py`, `test_config.py`, `test_mcp_clients.py`

9. **AC-9: Tests** — At least 20 tests across 4 test files:
   - `test_omc_runner.py` — subprocess spawn, output capture, graceful shutdown, timeout enforcement, SIGKILL fallback (mock subprocess)
   - `test_task_dispatch.py` — prompt building, plan parsing, payload construction
   - `test_config.py` — settings validation, defaults, env var loading
   - `test_mcp_clients.py` — connection lifecycle, verify_connectivity

10. **AC-10: Dependencies** — `pyproject.toml` updated with: `mcp>=1.0`, `events`, `structlog>=24.1`, `pydantic-settings>=2.5,<3.0`. `uv sync` succeeds.

11. **AC-11: `just lint` green** — All lint gates pass including `mypy --strict`.

12. **AC-12: `just test` no regressions** — Existing test count unchanged. New tests increase count.

13. **AC-13: Atomic commit** — title: `feat(orchestrator-adapter): add OMC subprocess supervision and task-dispatch translation · E5`

## Tasks / Subtasks

- [x] **Task 1: Update dependencies** (AC: #10)
  - [x] Add `mcp>=1.0`, `events`, `structlog>=24.1`, `pydantic-settings>=2.5,<3.0` to `pyproject.toml`
  - [x] Bump version to `0.2.0`
  - [x] Run `uv sync` to verify resolution

- [x] **Task 2: Create directory structure** (AC: #8)
  - [x] Create `src/orchestrator_adapter/app/`, `adapters/`, `domain/` directories
  - [x] Create empty `__init__.py` files for each package

- [x] **Task 3: Discover OMC CLI contract** (AC: #1)
  - [x] Read `upstream/omc/README.md` — understand CLI subcommands
  - [x] Read `upstream/omc/CLAUDE.md` — understand orchestration capabilities
  - [x] Experiment: run `node bridge/cli.cjs --help` to discover available subcommands
  - [x] Document the invocation: which subcommand produces a plan, what arguments it takes, what output format it uses
  - [x] If OMC lacks a "plan-only" mode, design a contract that extracts plan from OMC's full output

- [x] **Task 4: Implement `app/config.py`** (AC: #5)
  - [x] `OrchestratorSettings` with `env_prefix="ORCHESTRATOR_"`
  - [x] MCP server configs (clawhip_bridge_command/args, task_registry, session_registry)
  - [x] `omc_path: str` (default `"upstream/omc"`)
  - [x] `omc_timeout_s: int` (default 120)
  - [x] `actor_id: str` (auto-generate UUIDv7 if empty)
  - [x] `poll_interval_s: float` (default 5.0)

- [x] **Task 5: Implement `adapters/omc_runner.py`** (AC: #1)
  - [x] `OMCResult` dataclass (exit_code, stdout, stderr, duration_ms)
  - [x] `OMCRunner` class with `run(prompt: str) -> OMCResult`
  - [x] Spawn `node bridge/cli.cjs` via `asyncio.create_subprocess_exec`
  - [x] Capture stdout + stderr concurrently (drain stderr in background task)
  - [x] Timeout enforcement via `asyncio.wait_for`
  - [x] Graceful shutdown: `terminate()` → 5s `wait()` → `kill()`
  - [x] Duration tracking

- [x] **Task 6: Implement `domain/task_dispatch.py`** (AC: #2)
  - [x] `build_omc_prompt(task_id, title, hint, repo) -> str`
  - [x] `parse_omc_plan_output(raw_output: str) -> str` (extracts plan summary)
  - [x] `build_planning_started_payload(task_id) -> dict`
  - [x] `build_plan_ready_payload(task_id, plan_summary) -> dict`
  - [x] Use `events.payloads.TaskPlanningStartedPayload`, `TaskPlanReadyPayload`

- [x] **Task 7: Implement `adapters/mcp_clients.py`** (AC: #4)
  - [x] `MCPClientGroup` dataclass (same pattern as worker-wrapper)
  - [x] Async context manager with `AsyncExitStack`
  - [x] Connect to clawhip-bridge, task-registry, session-registry via stdio
  - [x] `verify_connectivity()` method

- [x] **Task 8: Implement `app/main.py`** (AC: #6)
  - [x] Main adapter loop: connect MCP → poll for tasks → drive OMC → emit events
  - [x] Task polling: read task-registry resource, find tasks needing planning
  - [x] Event emission: call clawhip-bridge `emit_event` tool
  - [x] Signal handling (SIGTERM/SIGINT)
  - [x] Ready-file healthcheck (`/tmp/ready`)

- [x] **Task 9: Update `__main__.py` and `__init__.py`** (AC: #8)
  - [x] Replace hello-world entrypoint with real lifecycle (structlog, settings, main loop)
  - [x] Update `__init__.py` to export `OMCRunner`, version `"0.2.0"`

- [x] **Task 10: Write tests** (AC: #9)
  - [x] `test_omc_runner.py` — subprocess mock, output capture, shutdown, timeout, SIGKILL fallback
  - [x] `test_task_dispatch.py` — prompt building, plan parsing, payload construction
  - [x] `test_config.py` — defaults, env vars, validation
  - [x] `test_mcp_clients.py` — connection lifecycle, verify_connectivity

- [x] **Task 11: Verification + commit** (AC: #7, #11, #12, #13)
  - [x] `ruff check` clean
  - [x] `scripts/check_imports.py` exits 0
  - [x] `just test` — no regressions
  - [x] Atomic commit

### Review Findings

- [x] [Review][Patch] Payload builders use typed Pydantic models from events.payloads [`domain/task_dispatch.py`]
- [x] [Review][Patch] _resolved_actor_id uses PrivateAttr() [`app/config.py:41`]
- [x] [Review][Patch] OMCRunner.run() clears _process on success path [`adapters/omc_runner.py:170`]
- [x] [Review][Patch] process_task emits task.planning.failed on OMC error [`app/main.py:136-145`]
- [x] [Review][Patch] _read_task_list null-checks result.contents [`app/main.py:69`]
- [x] [Review][Patch] process_task validates empty task_id [`app/main.py:115`]
- [x] [Review][Patch] repo parameter wired through in process_task [`app/main.py:131`]
- [x] [Review][Patch] hint field TODO comment added (not materialized by task-registry) [`app/main.py:118`]
- [x] [Review][Defer] Same task reprocessed indefinitely — no status update mechanism yet
- [x] [Review][Defer] Connectivity failure doesn't prevent startup — same pattern as worker-wrapper

## Dev Notes

### What already exists

**`services/orchestrator-adapter/`** — hello-world scaffold from Story 1.4:
- `pyproject.toml` — name `orchestrator-adapter` v0.1.0, zero dependencies
- `__init__.py` — stub with `__version__ = "0.1.0"`
- `__main__.py` — signal-based pause loop, `/tmp/ready` healthcheck
- `Dockerfile` — based on `oh-my-bmad-base:local`, uid 10004

**`upstream/omc/`** — vendored oh-my-claudecode v4.13.2:
- CLI entry: `bridge/cli.cjs` (3.1 MB bundled JS)
- npm binaries: `omc`, `oh-my-claudecode`, `omc-cli`
- Main ESM entry: `dist/index.js`
- `README.md` (27 KB), `CLAUDE.md` (6.6 KB)
- Key skills: `autopilot`, `ralph`, `ultrawork`, `omc-plan`, `ralplan`
- State tools: `state_read`, `state_write`, `state_list_active`
- Teams: `TeamCreate`, `TeamDelete`, `SendMessage`, `TaskCreate`, `TaskList`

### Architecture alignment

| Aspect | Pattern | Source |
|---|---|---|
| Subprocess supervision | `asyncio.create_subprocess_exec` + timeout + SIGTERM/SIGKILL | `worker-wrapper/adapters/claude_code_runner.py` (Story 5.4) |
| NFR-O1 stdout parsing ban | Parse structured output (JSON-lines), NOT `subprocess.check_output().decode()` | architecture.md line 513 |
| Ports-and-adapters | `app/` + `domain/` + `adapters/` split | architecture.md lines 670-682 |
| MCP client group | `MCPClientGroup` with `AsyncExitStack` | worker-wrapper Story 5.1 pattern |
| Config | `pydantic-settings` with env_prefix | worker-wrapper `app/config.py` pattern |
| Event emission | `EventEnvelope.create()` with `actor_kind="orchestrator"` | `events/envelope.py` |
| Import boundary | `upstream/` not in scan roots → structural enforcement | `scripts/check_imports.py` |

### OMC CLI contract (to be discovered in Task 3)

The dev MUST read these files to understand OMC's CLI:
1. `upstream/omc/README.md` — full usage documentation
2. `upstream/omc/CLAUDE.md` — orchestration instructions, skill catalog
3. `upstream/omc/package.json` — CLI binaries, entry points

Key questions to answer:
- What subcommand produces a plan? (`omc plan <task>`? `omc run --plan-only <task>`?)
- What arguments does it accept? (task description, config paths, etc.)
- What output format does it produce? (JSON-lines, plain text, structured markdown?)
- How does it signal completion vs error? (exit codes, stderr patterns?)

If OMC lacks a standalone "plan-only" mode, the adapter may need to:
- Invoke OMC with a planning-focused prompt
- Parse the plan section from OMC's full orchestration output
- Extract the plan summary from OMC's state files (`.omc/plans/`)

### Import-graph rules

| Import | Allowed? | Notes |
|---|---|---|
| `events` (workspace) | ALLOWED | `packages/events` — payloads, IDs, envelope |
| `registry_state` | ALLOWED | `# noqa: IMP001` — same exception as MCP servers |
| `mcp.server.fastmcp` | ALLOWED | FastMCP SDK for MCP client |
| `structlog` | ALLOWED | Structured logging |
| `pydantic_settings` | ALLOWED | Configuration management |
| `upstream.omc.*` | **FORBIDDEN** | OMC is JS, not Python — structural enforcement |
| Other `services/*` | **FORBIDDEN** | Cross-service import ban |
| `subprocess` / `asyncio` | ALLOWED | stdlib for subprocess supervision |

### Relevant event types and payloads

| Event Type | Payload Model | Direction | Notes |
|---|---|---|---|
| `task.created` | `TaskCreatedPayload(task_id, title, repo?, hint?)` | Subscribe | Trigger for planning |
| `task.planning.started` | `TaskPlanningStartedPayload(task_id)` | Emit | Planning begins |
| `task.plan.ready` | `TaskPlanReadyPayload(task_id, plan_summary)` | Emit | Plan produced |

All payloads are frozen Pydantic models with `ConfigDict(frozen=True, strict=True, extra="forbid")`.

### Event emission pattern

```python
from events.envelope import EventEnvelope, Actor
from events.ids import new_event_id
from events.clock import utc_now

envelope = EventEnvelope.create(
    event_id=new_event_id(clock, rng),
    type="task.planning.started",
    payload=TaskPlanningStartedPayload(task_id=task_id),
    actor=Actor(kind="orchestrator", id=settings.actor_id),
)
# Emit via clawhip-bridge MCP tool
```

### OMC subprocess pattern (reference: claude_code_runner.py)

```python
class OMCRunner:
    async def run(self, prompt: str) -> OMCResult:
        process = await asyncio.create_subprocess_exec(
            "node", str(self._omc_cli), *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._omc_path),
        )
        # Write prompt to stdin, close it
        # Drain stderr concurrently (prevent deadlock)
        # Apply timeout via asyncio.wait_for
        # Graceful shutdown: terminate → wait(5s) → kill
```

### Task polling strategy (Phase 1)

The adapter polls the task-registry MCP server for tasks needing planning:
1. Read `task://list` resource
2. Filter for tasks with status indicating planning is needed
3. For each such task, drive OMC and emit planning events
4. Sleep `poll_interval_s` before next poll

This avoids building event subscription infrastructure in Phase 1. Event subscription can replace polling in a later story.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `ORCHESTRATOR_CLAWHIP_BRIDGE_COMMAND` | Yes | Command to start clawhip-bridge MCP server |
| `ORCHESTRATOR_TASK_REGISTRY_COMMAND` | Yes | Command to start task-registry MCP server |
| `ORCHESTRATOR_SESSION_REGISTRY_COMMAND` | Yes | Command to start session-registry MCP server |
| `ORCHESTRATOR_ACTOR_ID` | No | Auto-generated UUIDv7 if empty |
| `ORCHESTRATOR_OMC_PATH` | No | Default: `upstream/omc` |
| `ORCHESTRATOR_OMC_TIMEOUT_S` | No | Default: 120 |
| `ORCHESTRATOR_POLL_INTERVAL_S` | No | Default: 5.0 |

### Downstream consumers

- **Story 5.12** (task execution driver) — worker picks up after plan is ready + approved
- **Story 6.1-6.3** (capability tier enforcement) — applies to tool calls
- **Story 5.17a-c** (resume after approval) — orchestrator drives re-execution after operator approval
- **Story 5.18** (Journey 1 integration test) — end-to-end test of task → plan → execute → PR

### Key patterns from worker-wrapper (reference — Stories 5.1-5.7)

1. **`MCPClientGroup`**: Async context manager with `AsyncExitStack`. Three stdio MCP connections. `verify_connectivity()` calls `list_tools()` concurrently. Pattern from Story 5.1.

2. **`ClaudeCodeRunner`**: Async subprocess supervisor. Spawns process with `asyncio.create_subprocess_exec`. Drains stderr concurrently (prevent deadlock — Story 5.4 code review fix). Graceful terminate → 5s wait → kill. Timeout via `asyncio.wait_for`. Result dataclass with exit_code, stdout, stderr, duration_ms.

3. **`WorkerSettings`**: pydantic-settings with `env_prefix="WORKER_"`. MCP server commands/args. `resolve_session_id()` / `resolve_worker_id()` generate UUIDv7 if empty.

4. **`__main__.py`**: structlog JSON rendering + `redact_secrets` processor. Signal handlers via `loop.add_signal_handler`. Ready-file healthcheck. Graceful shutdown in `finally` block.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1581-1596 — Story 5.10 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 670-682 — orchestrator-adapter directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 788 — subprocess-only rule for upstream/*]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 847-849 — task flow involving orchestrator-adapter]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 124 — vendored-with-sync pattern]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — subprocess supervision reference]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — MCP client group reference]
- [Source: `services/worker-wrapper/src/worker_wrapper/app/config.py` — settings reference]
- [Source: `packages/events/src/events/payloads.py` — TaskPlanningStartedPayload, TaskPlanReadyPayload]
- [Source: `packages/events/src/events/envelope.py` — EventEnvelope, Actor]
- [Source: `packages/events/src/events/ids.py` — new_event_id, new_uuid7]
- [Source: `upstream/omc/README.md` — OMC CLI usage docs]
- [Source: `upstream/omc/CLAUDE.md` — OMC orchestration instructions]
- [Source: `scripts/check_imports.py` — import boundary enforcement]
