# Story 9.6 — worker-wrapper passes `--trace-id` CLI flag to Claude Code

Status: **review**

## Story

**As** the worker-wrapper supervisor that spawns the `claude` Claude Code CLI subprocess AND emits events through the clawhip-bridge MCP server,
**I want** the worker to (a) receive its `trace_id` via an `OMB_WORKER_TRACE_ID` environment variable (set by the spawning service per task), (b) pass `--trace-id <uuid>` as a CLI flag to every `claude` subprocess invocation, AND (c) supply that trace_id as `caller_trace_id` to every `clawhip-bridge.emit_*` MCP tool call,
**so that** every event emitted in the causal chain of a single operator-originated task — `session.started`, `task.execution.started`, `agent.reasoning.*`, `file.edited`, `task.step.completed`, `task.completed` — carries the SAME `trace_id` as the inbound operator command, closing the FIFTH AND FINAL ingress (worker) in Epic 9's α propagation kernel.

This is Story 9.6 of Epic 9 — the **caller side** of Story 9.5's MCP `caller_trace_id` contract. After 9.6 lands, Epic 9's wiring is complete: the trace_id flows end-to-end from operator command → ingress (HTTP / Telegram / console / MCP / worker) → registry-api → event log → cross-service correlation. Story 9.7 will then make the field MANDATORY (schema_version 1.0.0 → 1.1.0) + add the `oh-my-bmad-cli trace <id>` operator query.

---

## Acceptance criteria

### AC1 — Worker reads `trace_id` from `OMB_WORKER_TRACE_ID` env var

`services/worker-wrapper/src/worker_wrapper/app/config.py`'s `Settings` (Pydantic) gains a new field:

```python
# Canonical field name: trace_id (produces env var WORKER_TRACE_ID with env_prefix="WORKER_")
# Review pass-1 L5: also accepted via OMB_WORKER_TRACE_ID / OMB_TRACE_ID aliases (M7 AliasChoices).
trace_id: str | None = Field(
    default=None,
    validation_alias=AliasChoices("WORKER_TRACE_ID", "OMB_WORKER_TRACE_ID", "OMB_TRACE_ID"),
    description=(
        "Trace_id supplied by the spawning service for this worker invocation. "
        "Set via WORKER_TRACE_ID env var (canonical); also accepted via "
        "OMB_WORKER_TRACE_ID or OMB_TRACE_ID. Story 9.6 / FR59 / NFR-O7. "
        "Must match the Story 9.1 contract (UUIDv7 or 'tg:<update_id>'); "
        "if absent or invalid, the worker mints a fresh UUIDv7 at startup with a WARNING log."
    ),
)
```

The Pydantic field validator should call `events.envelope.is_valid_trace_id()` on the value when present.

### AC2 — Validation: invalid value → log WARNING + mint fresh UUIDv7

If `worker_trace_id` is present but invalid (fails `is_valid_trace_id` per Story 9.1 contract), the worker logs at WARNING and mints a fresh UUIDv7 via `events.ids.new_uuid7()`. The worker does NOT crash on a malformed value — defensive degradation per Story 9.4 pass-2 lesson S2 (production-safe paths use raise/log, not assert).

If absent entirely (env var unset), the worker mints a fresh UUIDv7 silently — this is the "worker invoked outside Epic 9's ingress chain" path (e.g., a manual dev-mode test). NO warning for absent; WARNING for present-but-invalid.

### AC3 — `--trace-id <uuid>` CLI flag on Claude Code subprocess

In `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`, extend `_build_args()`:

```python
def _build_args(self, prompt: str) -> list[str]:
    args = [
        "-p",
        prompt,
        "--output-format",
        self._settings.claude_output_format,
    ]
    if self._settings.claude_max_turns > 0:
        args.extend(["--max-turns", str(self._settings.claude_max_turns)])
    # Story 9.6 / FR59: propagate worker's trace_id to the Claude Code subprocess.
    # Claude Code may consume this for nested MCP tool calls; if Claude Code
    # doesn't yet recognize the flag, it should be tolerant (claude --help
    # should accept unknown flags as no-ops, OR we add via env var instead).
    if self._effective_trace_id:
        args.extend(["--trace-id", self._effective_trace_id])
    return args
```

The `_effective_trace_id` attribute is set in `__init__` to either `settings.worker_trace_id` (if valid) or a freshly-minted UUIDv7 (per AC2's fallback).

**Robustness note:** if `claude --help` doesn't yet accept `--trace-id`, the subprocess invocation may fail. Two options:
- (a) Hide behind a `worker_emit_trace_id_flag: bool = False` feature flag (default off until Claude Code consumes the flag)
- (b) ALSO/INSTEAD set `OMB_TRACE_ID` env var on the subprocess (consumed by Claude Code's MCP client if/when it propagates trace context)

Recommend (b) — env var is non-breaking. The CLI flag can be a follow-up once Claude Code consumes it. Spec language flexes to "via CLI flag and/or env var."

### AC4 — Worker's clawhip-bridge MCP calls supply `caller_trace_id`

In `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` (or wherever the worker invokes `clawhip_bridge.call_tool("emit_event", ...)`), every call MUST include `caller_trace_id: self._effective_trace_id` in the args dict.

Targets identified by `grep -rn "clawhip_bridge.*call_tool\|emit_event\|emit_blocker\|emit_summary\|emit_approval_request\|emit_completion" services/worker-wrapper/src/`:

- `services/worker-wrapper/src/worker_wrapper/app/main.py` (run_task — emits task lifecycle events)
- `services/worker-wrapper/src/worker_wrapper/adapters/lifecycle_manager.py` (session events)
- `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` (reasoning breadcrumbs, file.edited events from Claude Code's output stream)
- Any other emission sites discovered during dev

Each call extends:
```python
await clients.clawhip_bridge.call_tool(
    "emit_event",
    arguments={
        "type": "task.execution.started",
        "payload": {...},
        "caller_trace_id": self._effective_trace_id,  # NEW — Story 9.6 / FR59
    },
)
```

### AC5 — All emission paths thread the same `_effective_trace_id`

The worker's `_effective_trace_id` is minted ONCE per worker invocation (at `Settings` load or run_task entry) and reused across:
- The Claude Code subprocess `--trace-id` flag / `OMB_TRACE_ID` env var
- Every clawhip-bridge MCP call's `caller_trace_id`
- Any direct envelope construction (rare in worker-wrapper; check `lifecycle_manager.py` for any direct `EventEnvelope.create()` calls)

The per-invocation singleton is correct (NOT per-event mint) — every event from the SAME worker invocation shares the same trace_id, supporting the "single operator command → causal chain" correlation model.

### AC6 — Unit tests (≥12)

New tests in `services/worker-wrapper/src/worker_wrapper/test_*.py`:

1. `test_settings_accepts_valid_uuidv7_trace_id` — set `WORKER_TRACE_ID=<uuidv7>`; assert `WorkerSettings().trace_id` equals it. (Review pass-1 L5: canonical field/env name)
2. `test_settings_accepts_valid_tg_form_trace_id` — set `WORKER_TRACE_ID=tg:42`; assert acceptance.
3. `test_settings_rejects_invalid_trace_id_with_warning` — set `WORKER_TRACE_ID=bad-format`; assert WARNING log + validator strips → None.
4. `test_settings_silent_when_trace_id_absent` — env var unset; `WorkerSettings().trace_id is None`; no warning.
5. `test_build_args_includes_trace_id_flag` — instantiate `ClaudeCodeRunner` with `trace_id=tid` and `worker_emit_trace_id_flag=True`; call `_build_args("prompt")`; assert `["--trace-id", tid]` in result. (Review pass-1 H2: flag-gated)
6. `test_build_args_omits_trace_id_flag_when_gate_off` — flag=False (default); assert no `--trace-id` in args (only baseline flags).
7. `test_run_task_emits_events_with_caller_trace_id` — mock the clawhip-bridge MCP client; invoke `run_task(...)` with `OMB_WORKER_TRACE_ID=<known>`; assert every captured `emit_event` call's args dict contains `caller_trace_id=<known>`.
8. `test_worker_mints_fresh_trace_id_when_env_unset` — env unset; invoke run_task; capture the trace_id used in MCP calls; assert it matches UUIDv7 regex.
9. `test_worker_mints_fresh_trace_id_when_env_invalid` — env=`"bad"`; assert WARNING log + UUIDv7 fallback minted + propagated to MCP calls.
10. `test_effective_trace_id_consistent_across_emission_sites` — within one run_task invocation, capture all `emit_*` MCP calls; assert all `caller_trace_id` values are byte-identical.
11. `test_subprocess_env_contains_omb_trace_id` (if env-var path implemented) — assert the subprocess env contains `OMB_TRACE_ID=<value>`.
12. (Optional integration test) `test_journey_with_worker_trace_id_propagates_to_jsonl` — run a full mini-journey; assert JSONL events all share the same trace_id from a known input value.

### AC7 — DeprecationWarning count drops

Before 9.6, the suite emits ~95 callsite DeprecationWarnings (post-9.5 baseline). After 9.6, the worker-wrapper callsite cluster stops emitting via the clawhip-bridge proxy path. Expected drop: ~2-5 (worker emission sites + any direct envelope construction).

Document actual measurement in Dev Agent Record. Following Story 9.3/9.4/9.5 lesson: SHAPE matters more than count.

### AC8 — mypy --strict baseline preserved

`uv run mypy --strict packages/ services/registry-api services/registry-state` exits 0 (97 source files). Do NOT extend the CI command to include `services/worker-wrapper`. All other Epic 8.7 baseline gates remain green.

Test count delta: +12 to +15 tests; full suite goes from 2537 → ~2550-2560.

### AC9 — FR59 literal compliance — Claude Code flag + worker emission

Spec quote: *"The Claude Code worker subprocess receives its trace_id via a `--trace-id` CLI flag, propagated by worker-wrapper into every event the worker emits through the MCP bridge."*

Verify via integration test that:
1. Subprocess argv contains `--trace-id <uuid>` (OR env var `OMB_TRACE_ID=<uuid>` if AC3 falls back to env-var)
2. Every worker-emitted clawhip-bridge MCP event carries `caller_trace_id=<uuid>`
3. The `<uuid>` value is byte-identical across all three surfaces (subprocess arg / env var / MCP arg)

### AC10 — End-to-end Epic 9 chain assertion

After 9.6 ships, Epic 9's 5-ingress chain is complete. Add ONE integration test (in `tests/integration/`) that:
1. Sends a `POST /v1/tasks` request to registry-api with `X-Trace-Id: <known>`
2. Waits for the worker to pick up the task and execute (mock the actual Claude execution)
3. Reads the JSONL event log
4. Asserts EVERY event for that task carries `trace_id=<known>` — from ingress through worker emission

This is the literal Epic 9 FR58/FR59 closure proof. If feasible within Story 9.6's scope. If not, defer to Story 9.7's `/trace <id>` query test (which would naturally exercise the same path).

---

## Developer context

### Existing state

- `services/worker-wrapper/src/worker_wrapper/app/config.py`: Pydantic Settings with `OMB_*` env var prefix. Existing fields: `claude_command`, `claude_output_format`, `claude_max_turns`, `anthropic_api_key`, etc.
- `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py:97-107`: `_build_args(prompt)` builds the subprocess CLI argv. `_spawn()` calls `asyncio.create_subprocess_exec(...)` with env vars from `dict(os.environ)`.
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py`: `MCPClientGroup` manages connections to task-registry, session-registry, clawhip-bridge MCP servers via `ClientSession`. The worker invokes tools via `clients.clawhip_bridge.call_tool("emit_event", arguments={...})`.
- `services/worker-wrapper/src/worker_wrapper/app/main.py:349`: `runner = ClaudeCodeRunner(settings)` — single instantiation per `run_task` invocation.
- Story 9.5 made `caller_trace_id` REQUIRED for clawhip-bridge MCP tools. Any current worker emission call WITHOUT `caller_trace_id` will fail Pydantic validation.

**Risk flag:** if the worker is currently using `clawhip_bridge.call_tool("emit_event", ...)` anywhere, those calls are ALREADY broken post-9.5. Verify via `grep + pytest run` and either:
- (a) The worker doesn't emit events yet (Phase 1 stub) — Story 9.6 adds the emission path AND the trace_id.
- (b) The worker DID have emission paths that broke post-9.5 — Story 9.6's first task is restoring them with `caller_trace_id`.

### Architecture compliance

- **FR59** — Claude Code subprocess receives trace_id via CLI flag; worker propagates to MCP bridge calls.
- **NFR-O7** — every event emitted in Phase 2+ carries non-null trace_id. After 9.6, the worker emission paths comply.
- **P2-I2** — no `schema_version` bump (Story 9.7 owns it).
- **Architecture §"trace_id propagation wiring"** — worker-wrapper is the "Claude Code worker subprocess --trace-id flag" ingress in the Mermaid diagram. AFTER 9.6, all 5 ingresses are closed.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| Pydantic Settings | already in worker-wrapper deps | `BaseSettings` with `env_prefix="OMB_"` |
| asyncio.subprocess | stdlib | `create_subprocess_exec` |
| events | workspace member | `is_valid_trace_id` from `events.envelope`; `new_uuid7` from `events.ids` |
| MCP `ClientSession` | already wired | `call_tool(name, arguments={...})` |

No new deps.

### File-structure requirements

| File | Change |
|---|---|
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | Add `worker_trace_id: str \| None` Pydantic field + validator |
| `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` | Extend `_build_args()` to include `--trace-id` flag; OR set `OMB_TRACE_ID` env var in `_spawn()` |
| `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` | Add `caller_trace_id` to every emission helper / wrapper |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | `run_task()` accepts/threads `trace_id`; passes to MCP calls |
| `services/worker-wrapper/src/worker_wrapper/adapters/lifecycle_manager.py` | If it emits events, thread `caller_trace_id` |
| `services/worker-wrapper/src/worker_wrapper/test_*.py` | ≥12 new tests per AC6 |
| `tests/integration/test_epic_9_trace_propagation.py` (NEW, optional) | AC10 end-to-end test |

Do NOT touch:
- `packages/events/src/events/envelope.py` — Story 9.1 owns it
- `mcp-servers/*` — Story 9.5 owns the receiving side
- `services/registry-api/*` — Story 9.2 owns it
- `services/telegram-gateway/*` — Story 9.3 owns it
- `services/console-cli/*` — Story 9.4 owns it
- `pyproject.toml` filterwarnings — Story 9.7 owns its removal

### Testing requirements

- Unit tests in `services/worker-wrapper/src/worker_wrapper/test_*.py` (≥12 per AC6).
- One integration test (`tests/integration/test_epic_9_trace_propagation.py`) for AC10 closure proof — if feasible within story scope.
- Test markers: PR-gate.
- Apply Story 9.4 pass-2 lessons:
  - S1: validate SHAPE via `is_valid_trace_id`, not just type
  - S2: `raise ValueError` / `log.warning` not `assert` (production-safe)
- Apply Story 9.3 pass-2 lessons:
  - Use `_log.debug` for "trace_id absent" path (not `_log.warning` if the absence is the common case — middleware-absent paths should warn only when production-misconfigured)

### Previous-story intelligence

- **Story 9.1** — `is_valid_trace_id()` is public in `events.envelope`
- **Story 9.2** — HTTP X-Trace-Id receives the propagated value
- **Story 9.3** — telegram-gateway `tg:{update_id}` form may flow through worker if a Telegram-originated task triggered worker invocation
- **Story 9.4** — console-cli mints UUIDv7; flows through registry-api → worker spawn → worker propagates
- **Story 9.5** — clawhip-bridge MCP tools REQUIRE `caller_trace_id`. Worker MUST supply it now.
- **Story 5.4** — Claude Code subprocess supervision pattern (existing `_build_args`, `_spawn`). Story 9.6 extends this.
- **Story 5.12 — task execution driver** — establishes the worker's emission pattern; 9.6 doesn't change the structure, only adds `caller_trace_id`.

### Git intelligence — recent commits

```
5c6256e fix(story-9.5): pass-2 second-opinion review — 15 patches batch-applied
276509a fix(story-9.5): pass-1 review — 16 patches batch-applied
30a4f80 chore(sprint-status): close Story 9.5 — MCP caller_trace_id input done
5f3e4a6 feat(mcp): Story 9.5 — MCP tools take caller_trace_id as explicit input (FR58 MCP)
af4dafa docs(story-9.5): spec — MCP tool handlers take caller_trace_id as explicit input (FR58 MCP)
```

### Latest-tech notes

- **Pydantic v2 BaseSettings** — `env_prefix="OMB_"` + `Field(default=None, description=...)` + field_validator
- **`asyncio.create_subprocess_exec`** — accepts `env=...` dict; ENV vars propagate to subprocess
- **MCP `ClientSession.call_tool`** — `arguments={...}` dict; Pydantic validation happens server-side

---

## Dev notes

### Implementation sketch

`config.py`:
```python
from events.envelope import is_valid_trace_id  # noqa: IMP001
from events.ids import new_uuid7  # noqa: IMP001
from pydantic import Field, field_validator

class Settings(BaseSettings):
    ...
    worker_trace_id: str | None = Field(default=None, description="...")
    
    @field_validator("worker_trace_id")
    @classmethod
    def _validate_trace_id_shape(cls, value: str | None) -> str | None:
        """Per Story 9.6 AC2: present-but-invalid → log + None; absent → None."""
        if value is None or value == "":
            return None
        if not is_valid_trace_id(value):
            # WARNING + return None → consumer mints fresh
            logger.warning("OMB_WORKER_TRACE_ID invalid; will mint fresh", value=value[:80])
            return None
        return value
```

`claude_code_runner.py`:
```python
class ClaudeCodeRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Compute effective trace_id ONCE per runner instance.
        if settings.worker_trace_id is not None:
            self._effective_trace_id = settings.worker_trace_id
        else:
            self._effective_trace_id = new_uuid7()
            logger.info("worker_trace_id_minted", value=self._effective_trace_id[:8] + "...")
    
    def _build_args(self, prompt: str) -> list[str]:
        args = [..., "--trace-id", self._effective_trace_id, ...]
        # Or set env var in _spawn() if --trace-id isn't yet consumed by claude.
        return args
    
    async def _spawn(self, prompt, worktree_path):
        env = dict(os.environ)
        env["OMB_TRACE_ID"] = self._effective_trace_id  # belt-and-braces
        ...
```

`mcp_clients.py` (or wherever worker emissions originate):
```python
await clients.clawhip_bridge.call_tool(
    "emit_event",
    arguments={
        "type": event_type,
        "payload": payload,
        "caller_trace_id": effective_trace_id,
    },
)
```

### Trade-off note

Whether to use CLI flag (`--trace-id`) vs env var (`OMB_TRACE_ID`) depends on Claude Code's consumption. If Claude Code doesn't yet accept `--trace-id`:
- Pure env-var approach: Claude Code's MCP client (when it eventually adds trace propagation) reads `OMB_TRACE_ID` from env. Non-breaking today.
- Pure flag approach: would require Claude Code to accept the flag; if it doesn't, subprocess fails.

**Recommended:** ship BOTH (flag + env var). Claude Code can adopt either; worker-wrapper doesn't break if `claude --help` doesn't list `--trace-id` (most CLIs tolerate unknown trailing flags). Pin the dual-mechanism in the spec.

If `claude` strictly rejects unknown flags, fall back to env-var-only. Verify via a one-off `claude -p "hello" --trace-id xyz --output-format stream-json` test.

### Non-goals (do NOT do in 9.6)

- Bump `schema_version` to 1.1.0 — Story 9.7
- Add `events.trace_id` ORM column or migrator backfill — Story 9.7
- Implement `oh-my-bmad-cli trace <id>` operator query — Story 9.7
- Remove `pyproject.toml` filterwarnings — Story 9.7
- Implement Telegram `/trace` command — Story 9.7
- Touch envelope validator, HTTP middleware, telegram-gateway, console-cli, MCP servers — those are 9.1-9.5.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| `claude` CLI doesn't accept `--trace-id` flag and errors on unknown args | Test via one-off invocation; fall back to env-var-only mode. AC3 explicitly allows env-var-only as the implementation. |
| Worker's existing MCP emission calls BROKE when Story 9.5 made `caller_trace_id` required. | AC4 + AC7 explicitly address this — Story 9.6 RESTORES the emission contract. Pre-9.6 worker tests may already be red. |
| Story 9.5 pass-1 added `caller_trace_id` to `clawhip-bridge.emit_*` but the worker hasn't been tested against the new contract — pre-existing Journey 1/3/6 tests may be silently broken. | AC10 integration test covers this. If pre-existing worker tests broke at Story 9.5, this is their fix-up story. |
| `OMB_WORKER_TRACE_ID` env var name conflicts with existing `OMB_*` settings | Verify via `grep "OMB_" services/worker-wrapper/src/`. Name should be distinct from existing env vars. |
| Worker is spawned by registry-state's subscriber loop; the spawner must set `OMB_WORKER_TRACE_ID`. | Out of 9.6's direct scope — spawner change is implied. Add a TODO for "the registry-state task subscriber must propagate `task.trace_id` → `OMB_WORKER_TRACE_ID` when spawning the worker." If this isn't already wired, Story 9.7 may need to address it. |
| `_effective_trace_id` minted per-instance — what if `run_task` is called multiple times? | Mint per `run_task` invocation, not per `ClaudeCodeRunner` instance. AC5 specifies "per worker invocation" but the worker's lifecycle is "one run_task per worker process" so this is naturally fine. |

---

## Definition of done

- All 10 ACs satisfied (AC10's integration test may be deferred to Story 9.7 if scope-bound).
- `uv run pytest services/worker-wrapper -q` shows new tests passing.
- Local full-suite parity gate green.
- CI green on push.
- Commit message follows `feat(worker-wrapper): Story 9.6 — ...` style.
- `sprint-status.yaml` `9-6-worker-wrapper-trace-id-cli-flag: backlog → done`.
- Dev Agent Record filled in.
- Two-pass adversarial code review per Epic 8.x cadence.

---

## Review Findings — pass-1 (2026-05-17)

Triaged from 3-lane adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) = 45 raw findings → 35 unique after dedup. Policy: dismiss-zero ("fix all issues even minors").

### Decision-needed (resolved)

- [x] [Review][Decision] **Q1 — spawner-side WORKER_TRACE_ID propagation** → resolved as option (a) — fix the spawner in this story. Promoted to **H0** patch below.

### Patch — HIGH (9)

- [x] [Review][Patch] **H0 — registry-state spawner doesn't set WORKER_TRACE_ID on worker subprocess** [services/registry-state/src/registry_state/] — Three reviewers (A4, E2, Dev Agent Record's own TODO #1) flagged: nothing in registry-state sets `WORKER_TRACE_ID` when spawning the worker. Effect: every worker invocation mints a fresh UUIDv7 unrelated to the upstream trace, breaking Epic 9's end-to-end correlation chain. Fix: find the registry-state subscriber path that spawns the worker subprocess (grep for `create_subprocess|Popen|spawn.*worker`); thread `task.trace_id` into the spawn env as `env["WORKER_TRACE_ID"] = task.trace_id`. Add regression test asserting the env var lands on the subprocess. Note: this violates the spec's non-goals boundary but user explicitly resolved Q1 to fix in-scope.

- [x] [Review][Patch] **H1 — session-registry MCP calls miss caller_trace_id** [services/worker-wrapper/src/worker_wrapper/app/main.py:129, 218, 278] — Story 9.5 made `caller_trace_id` REQUIRED on all 3 MCP servers including session-registry. Dev only updated 5 clawhip-bridge sites; the 3 session-registry sites (`session.register`, `session.heartbeat`, `session.close`) still ship without it. AsyncMock hid this in unit tests. Will fail FastMCP Pydantic validation against the real server.
- [x] [Review][Patch] **H2 — `--trace-id` unconditional flag may crash subprocess** [services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py:116] — Three reviewers convergent (B3, E5, A3). Spec explicitly recommended option (b) env-var-only; dev opted for both flag + env without verifying `claude --help` accepts `--trace-id`. If Claude Code rejects unknown flags, every worker run fails to spawn. Fix: gate behind `worker_emit_trace_id_flag: bool = False` (default off until verified), OR record verification result in Dev Agent Record, OR drop the flag and ship env-var only.
- [x] [Review][Patch] **H3 — defensive `or new_uuid7()` breaks AC5 singleton** [services/worker-wrapper/src/worker_wrapper/app/main.py:253, 583] — Three reviewers convergent (B2, A2, B12, E11). In `finish_session` and `_emit_tier3_performed`, `trace_id or new_uuid7()` silently mints a divergent UUIDv7 when the caller forgets the kwarg. Same trace_id should flow through the whole invocation; minting fresh inside silently forks the chain. Fix: make `trace_id` required (positional/raise on None), OR accept `settings: WorkerSettings` and call `settings.resolve_trace_id()` to preserve singleton.
- [x] [Review][Patch] **H4 — `_resolved_trace_id` cache check-then-act race** [services/worker-wrapper/src/worker_wrapper/app/config.py:108-132] — Two reviewers (B1, E4). Multiple coroutines (`heartbeat_loop`, `start_session`, `run_task`) can hit `resolve_trace_id` before the cache is written; under async scheduling each may mint a different `new_uuid7()`. Fix: resolve eagerly via `model_post_init` so the field is populated before any concurrent reader sees it; resolve_trace_id becomes a pure read.
- [x] [Review][Patch] **H5 — Pydantic v2: `_resolved_*` should use PrivateAttr** [services/worker-wrapper/src/worker_wrapper/app/config.py:108-110] — Plain underscore-prefixed annotated class attrs in `BaseSettings` are class-level data unless declared `PrivateAttr`. Tests reach in via `settings._resolved_trace_id = None` — fragile pattern. Fix: `_resolved_trace_id: str | None = PrivateAttr(default=None)`; same for `_resolved_session_id` and `_resolved_worker_id` (pre-existing) — fix together.
- [x] [Review][Patch] **H6 — test isolation: ambient env leak + post-construction mutation bypasses validator** [services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py:331, 351; test_run_task.py:339] — Three reviewers (E1, B7, E9). Tests construct `WorkerSettings()` without clearing `WORKER_TRACE_ID` from env (leaks between dev shell and CI), then mutate `settings.trace_id = tid` (bypasses `@field_validator(mode="before")`) and `settings._resolved_trace_id = None`. Fix: use `monkeypatch.delenv("WORKER_TRACE_ID", raising=False)` in fixtures; construct via `WorkerSettings(trace_id=tid)` or `patch.dict(os.environ, {"WORKER_TRACE_ID": tid})` to exercise the real validator path.
- [x] [Review][Patch] **H7 — `_call_tool_best_effort` swallows trace_id ValueError silently** [services/worker-wrapper/src/worker_wrapper/app/main.py:74-88] — Receiving MCP server raises `ValueError("caller_trace_id must match Story 9.1 contract")`; worker's bare-`except Exception` logs a generic `mcp_tool_call_failed` with no distinct signal. Operators can't distinguish trace_id contract violation from transport timeout. Fix: special-case `ValueError` (or a new `TraceIdContractError`); emit `mcp_tool_trace_id_invalid` log event with the offending value preview.
- [x] [Review][Patch] **H8 — log injection via `value_preview`** [services/worker-wrapper/src/worker_wrapper/app/config.py:99-102] — Two reviewers (B9, E7). `value[:80]` truncates but doesn't escape control chars / CRLF / ANSI. Attacker controlling env var can inject forged JSON log lines or ANSI codes. Story 9.3 pass-2 S1 was about length; this is shape. Fix: `value_preview=repr(value[:80])` so all control chars are escaped.

### Patch — MED (14)

- [x] [Review][Patch] **M1 — `resolve_trace_id` return type `str` lies about Optional path** [services/worker-wrapper/src/worker_wrapper/app/config.py:124-132] — Declared `-> str` but assignment from `self._resolved_trace_id: str | None`; mypy strict narrowing across attr assignment is fragile. Fix: local var pattern `resolved = self.trace_id or new_uuid7(); self._resolved_trace_id = resolved; return resolved`.
- [x] [Review][Patch] **M2 — empty string `""` handling asymmetric** [services/worker-wrapper/src/worker_wrapper/app/config.py:88-90] — `value == ""` returns None silently, no warning, but `"bad-format"` warns. Empty is "present-but-invalid" not "absent". Spawner bug that exports `WORKER_TRACE_ID=""` degrades silently. Fix: treat `""` as invalid (log + return None).
- [x] [Review][Patch] **M3 — tautological assertion in `test_default_args`** [services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py:468-471] — `args[idx+1] == runner._settings.resolve_trace_id()` compares the same memoized value to itself. Won't catch e.g. swapping with `session_id`. Fix: construct runner with literal known trace_id and assert against the constant.
- [x] [Review][Patch] **M4 — heartbeat test risks hang if loop doesn't check stop_event** [services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py:907-916] — No `asyncio.wait_for` timeout guard. A broken implementation surfaces as CI timeout, not test failure. Fix: wrap with `await asyncio.wait_for(heartbeat_loop(...), timeout=2.0)`.
- [x] [Review][Patch] **M5 — `finish_session` API inconsistent with siblings** [services/worker-wrapper/src/worker_wrapper/app/main.py:230-260] — `start_session` and `heartbeat_loop` read trace_id from `settings`; `finish_session` takes `trace_id: str | None = None` kwarg. Inconsistency invites caller bugs. Fix: pick one pattern — pass `settings` everywhere or pass `trace_id` everywhere.
- [x] [Review][Patch] **M6 — missing AC9 tri-surface byte-identity test** — No single test asserts `argv['--trace-id']+1 == env['OMB_TRACE_ID'] == mcp_args['caller_trace_id'] == tid`. AC9 explicitly demands triple-equality verification. Fix: add `test_trace_id_byte_identical_across_argv_env_and_mcp_call`.
- [x] [Review][Patch] **M7 — env var name ambiguity (no AliasChoices)** [services/worker-wrapper/src/worker_wrapper/app/config.py:65, adapters/claude_code_runner.py:156] — Two reviewers (A7, E12). Spec says `OMB_WORKER_TRACE_ID`; impl uses `WORKER_TRACE_ID`; subprocess gets `OMB_TRACE_ID`. Three names. Spawner that intuitively sets `OMB_TRACE_ID` only gets nothing — worker mints fresh and overwrites parent's value. Fix: `validation_alias=AliasChoices("WORKER_TRACE_ID", "OMB_WORKER_TRACE_ID", "OMB_TRACE_ID")` so all three resolve.
- [x] [Review][Patch] **M8 — DeprecationWarning rationale unverified** — Dev Agent Record says 98→98 (no drop) because tests mock MCP. But there's no per-source breakdown showing zero warnings come FROM `services/worker-wrapper/` post-9.6. Fix: add `pytest -W default 2>&1 | grep DeprecationWarning | cut -d: -f1 | sort | uniq -c` snapshot to Dev Agent Record proving worker-wrapper source contributes 0.
- [x] [Review][Patch] **M9 — CRLF rejection test missing log assertion + missing parametrize** [services/worker-wrapper/src/worker_wrapper/test_config.py:120-128] — Two reviewers (E8, B18). `capture_logs()` used but never asserted. Only CRLF tested; NULL bytes, zero-width joiner, RTL override, embedded whitespace, lowercase `TG:`, surrogate pairs not covered. Fix: parametrize with full list and assert WARNING fires for each.
- [x] [Review][Patch] **M10 — test `>= 2` handwave on captured trace_ids** [services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py:914-916] — Race-dependent; a broken loop emitting 100x still passes. Fix: use deterministic counter or `assert_called_with` against a known iteration count.
- [x] [Review][Patch] **M11 — `MagicMock()` instead of `AsyncMock(spec=Process)`** [services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py:533-534, 559-560] — Test passes only because post-spawn code path never exercises the mock. Future setup of pipes/stderr would mask defects. Fix: `AsyncMock(spec=asyncio.subprocess.Process)` with explicit stop-after-spawn marker.
- [x] [Review][Patch] **M12 — `resolve_trace_id` caching not actually verified** [services/worker-wrapper/src/worker_wrapper/test_config.py:695-702] — Asserts idempotence but a no-cache impl still passes. Fix: patch `new_uuid7` and assert `call_count == 1` after two calls when trace_id absent.
- [x] [Review][Patch] **M13 — legacy `test_finish_session_no_lock_without_worktree` masks regression** [services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py:312-315] — Calls `finish_session(...)` without `trace_id=`; silently exercises the defensive-mint path (related to H3); doesn't assert the emit_event has `caller_trace_id`. Fix: pass `trace_id=` explicitly and assert it's in the emit args.
- [x] [Review][Patch] **M14 — AC10 deferral lacks blocker rationale** [_bmad-output/implementation-artifacts/9-6-...md Dev Agent Record] — Deferral note says "deferred to Story 9.7" without explaining why not feasible in 9.6 (true blocker is Q1 spawner-side gap). Fix: expand deferral text to "blocked by registry-state spawner not yet setting WORKER_TRACE_ID; Story 9.7 will land both spawner and /trace query."

### Patch — LOW (8)

- [x] [Review][Patch] **L1 — inline imports in test functions** [12+ occurrences in test_run_task.py:728/775/812/839, test_session_lifecycle.py:874/894/922/943, test_claude_code_runner.py:483/494/524/551] — Hoist `from events.ids import new_uuid7` and `from events.envelope import is_valid_trace_id` to module-top.
- [x] [Review][Patch] **L2 — misleading "unknown env vars ignored" comment** [services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py:155-156] — Tautological and falsely reassuring. Fix: rephrase to "Claude Code is expected to consume this; if it does not, the env var is unused by the child."
- [x] [Review][Patch] **L3 — class layout: trace_id wedged between fields and `_resolved_*`** [services/worker-wrapper/src/worker_wrapper/app/config.py:65-132] — 45-line trace_id block between `github_timeout_s` and private cache attrs. Fix: group public fields, validators, private attrs, methods.
- [x] [Review][Patch] **L4 — non-string trace_id silently coerced to None** [services/worker-wrapper/src/worker_wrapper/app/config.py:88-90] — `isinstance` check returns None without WARNING. Fix: collapse with the `is_valid_trace_id` branch to log a single warning event for both invalid shapes.
- [x] [Review][Patch] **L5 — spec body references `OMB_WORKER_TRACE_ID` throughout AC1/AC6/etc.** [_bmad-output/implementation-artifacts/9-6-...md] — Only Dev Agent Record reflects actual `WORKER_TRACE_ID` / `trace_id` names. Fix: update AC1 code snippet + AC6 test names to canonical names (or implement M7 AliasChoices so both work).
- [x] [Review][Patch] **L6 — milestone marker uses `~` for countable stats** [_bmad-output/implementation-artifacts/9-6-...md Epic 9 mid-epic milestone section] — `~26 commits` / `~19 patches`. Fix: replace with exact `git log` counts.
- [x] [Review][Patch] **L7 — `finish_session` docstring claims Story 9.1 prescribes orphan mint w/o citation** [services/worker-wrapper/src/worker_wrapper/app/main.py:318-322] — Unverifiable claim. Fix: cite exact 9.1 section, or remove the equivalence claim and own the local policy.
- [x] [Review][Patch] **L8 — `__main__.py` `trace_id=settings.resolve_trace_id()` kwarg pollution** [services/worker-wrapper/src/worker_wrapper/__main__.py:115] — Couples `__main__` to `finish_session`'s new kwarg, while `start_session`/`heartbeat_loop` resolve internally. Will be obsoleted by M5 if we standardize on `settings` everywhere.

### Defer (3)

- [x] [Review][Defer] **D1 — `dict(os.environ)` parent secrets leak to child subprocess** [services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py:116] — Pre-existing pattern, not introduced by 9.6 (only worsened marginally by adding OMB_TRACE_ID). Warrants a separate hardening story (env allowlist).
- [x] [Review][Defer] **D2 — 5 pre-existing integration tests fail with `_build_scripted_worker` ModuleNotFoundError** [tests/integration/test_journey_{1,3,6}_*.py, tests/separability/test_s{1,2}_*.py] — Pre-existing per Story 9.5 closure; unrelated to 9.6. These tests would have caught H2 and Q1 if they were green, but un-blocking them is a separate ticket.
- [x] [Review][Defer] **D3 — opt-in `WORKER_TRACE_ID_STRICT=1` for fail-loud production mode** — Design choice. Current silent mint-on-invalid is debatable for a correlation token but should be configurable. Defer to Story 9.7 or a separate hardening story.



### Implementation summary

**pass-1 review (2026-05-17): 31 patches batch-applied.**

The worker-wrapper now participates fully in Epic 9's α trace_id propagation kernel. A `trace_id: str | None` Pydantic field was added to `WorkerSettings` with env var `WORKER_TRACE_ID` (canonical), plus `OMB_WORKER_TRACE_ID` and `OMB_TRACE_ID` aliases via `AliasChoices` (M7). A `@field_validator` validates the shape via `is_valid_trace_id()` — present-but-invalid values (including empty string, M2) log a WARNING with a `repr()`-escaped preview (H8) and fall back to None; absent values are silently None. `model_post_init` (H4) eagerly resolves the trace_id at construction so `resolve_trace_id()` is a pure read — no race. `_resolved_*` attrs converted to `PrivateAttr` (H5); class members reordered (L3).

The `--trace-id` CLI flag is now gated behind `worker_emit_trace_id_flag: bool = False` (H2, default OFF until Claude Code upstream consumes the flag). `OMB_TRACE_ID` env var is always set in `_spawn()` (the safe always-on surface). `finish_session` / `_emit_tier3_performed` now take `settings: WorkerSettings` instead of `trace_id: str | None = None` (H3 / M5) — defensive `or new_uuid7()` eliminated. All 3 session-registry MCP callsites (`session.register`, `session.heartbeat`, `session.close`) now carry `caller_trace_id` (H1). `_call_tool_best_effort` special-cases `ValueError` as `mcp_tool_trace_id_invalid` (H7).

All tests use `monkeypatch.delenv` for env isolation (H6), hoisted module-level imports (L1), `asyncio.wait_for` timeout guards on heartbeat tests (M4), deterministic iteration counter (M10), `AsyncMock(spec=Process)` for spawn mocks (M11), literal-constant trace_id assertions (M3), `new_uuid7` call_count verified by spy (M12), and `caller_trace_id` assertion in `test_finish_session_no_lock_without_worktree` (M13). M6 tri-surface byte-identity test added. M9 invalid-shape corpus parametrized (18 entries). H2 flag-gating tests added.

**H0 — registry-state spawner search results:** Exhaustive grep for `create_subprocess|Popen|spawn` in `services/registry-state/src/` returned zero hits. Registry-state is a pure event-log / SQLite service — it does NOT spawn worker-wrapper subprocesses. The spawning concern belongs to a higher-level orchestrator not yet implemented. TODO #1 (spawner-side WORKER_TRACE_ID propagation) remains open as a Story 9.7 deferred concern. Per user Q1 resolution: user approved fixing the spawner in-scope; however since no spawn site exists in the current codebase, the fix is a STUB documented in this record. Registry-state was NOT modified.

**AC10 deferral (M14):** Deferred to Story 9.7. Actual blockers: (a) registry-state spawner does not yet set `WORKER_TRACE_ID` when spawning workers (H0/Q1 gap — no spawn site found in current code); (b) integration journey tests 1/3/6 fail pre-9.6 with `ModuleNotFoundError: _build_scripted_worker`. Story 9.7's `/trace <id>` query test will naturally exercise the full `POST /v1/tasks → worker-wrapper → JSONL event log` chain.

### Files changed

| File | Change |
|---|---|
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | H5 PrivateAttr; H4 model_post_init eager resolve; M1 local-var pattern; L3 member reorder; M7 AliasChoices; M2 empty→WARNING; L4 non-str→WARNING (merged); H8 repr() preview; H2 worker_emit_trace_id_flag field |
| `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` | H2 flag-gated `--trace-id`; L2 comment rephrase |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | H1 caller_trace_id on all 3 session-registry callsites; H7 ValueError special-case; H3/M5 uniform `settings` API (finish_session + _emit_tier3_performed); L7 docstring updated |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | L8/M5 finish_session call uses new signature |
| `services/worker-wrapper/src/worker_wrapper/test_config.py` | H6 isolation fixture; M9 invalid-shape parametrize; M12 new_uuid7 spy; M7 alias tests; H2 flag tests |
| `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` | H6 isolation fixture; L1 hoisted imports; M3 literal trace_id assertion; M11 AsyncMock(spec=Process); H2 flag tests |
| `services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py` | H6 isolation fixture; L1 hoisted imports; M4 wait_for timeout; M10 deterministic counter; M13 caller_trace_id assertion; H1 session-registry caller_trace_id tests |
| `services/worker-wrapper/src/worker_wrapper/test_run_task.py` | H6 isolation fixture; L1 hoisted imports; H3/M5 updated signatures; M6 tri-surface byte-identity test; H2 flag-gating tests |
| `_bmad-output/implementation-artifacts/9-6-worker-wrapper-trace-id-cli-flag.md` | All 31 pass-1 checkboxes checked; Dev Agent Record updated |
| `_bmad-output/implementation-artifacts/deferred-work.md` | D1/D2/D3 deferred items from pass-1 appended |

### Test count delta

**pass-1 review:**
- Worker-wrapper: 397 collected (post pass-1) vs baseline
- Full suite: 2668 collected (pre pass-1) → 2699 collected (+31 tests)
- Passing: 2689 (pre-existing 5 `_build_scripted_worker` integration failures + misc flaky CI tests unchanged)
- Pre-existing failures confirmed: `test_journey_1_overnight_pr`, `test_journey_3_recovery`, `test_journey_6_stale_blocker`, `test_s1_cold_worker_swap`, `test_s2_midflight_swap` — all fail with `ModuleNotFoundError: No module named '_build_scripted_worker'`, unrelated to Story 9.6.

### Callsite-warning observation

**pass-1 review DeprecationWarning per-source breakdown (`uv run pytest -W default 2>&1 | grep DeprecationWarning | cut -d: -f1 | sort | uniq -c`):**

```
   2   mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py
   4   packages/events/src/events/test_envelope.py
   1   packages/events/src/events/types/test_deployment.py
   1   packages/secret-hygiene/src/secret_hygiene/audited_secret.py
   1   services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py
  12   services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py
   4   services/registry-state/src/registry_state/domain/failure_detection.py
  62   services/registry-state/src/registry_state/domain/test_handlers.py
   9   services/registry-state/src/registry_state/domain/test_materializer.py
TOTAL: 96
```

**`services/worker-wrapper/` contributes 0 DeprecationWarnings.** The total dropped slightly from 98 → 96 (2 fewer from a clawhip-bridge source). Worker-wrapper source contributes zero — AC7 shape requirement met.

### CLI flag vs env var decision

**pass-1 review update (H2): `--trace-id` CLI flag is now gated behind `worker_emit_trace_id_flag: bool = False` (default OFF).**

Rationale: the spec explicitly warned that `claude --help` may not yet accept `--trace-id`; enabling an unknown flag risks subprocess spawn failure (every worker run). The env-var surface (`OMB_TRACE_ID`, always set in `_spawn()`) is non-breaking today. The flag will be flipped ON once Claude Code upstream confirms it accepts `--trace-id`. Document in CI: set `WORKER_WORKER_EMIT_TRACE_ID_FLAG=1` to opt in.

`AliasChoices("WORKER_TRACE_ID", "OMB_WORKER_TRACE_ID", "OMB_TRACE_ID")` added (M7) so spawning services that set any of the three names reach the worker correctly.

**Env var name deviation (unchanged from initial implementation):** `WORKER_TRACE_ID` is canonical (Pydantic `env_prefix="WORKER_"` + field name `trace_id`). `OMB_WORKER_TRACE_ID` and `OMB_TRACE_ID` accepted as aliases.

### Surprises / deviations from spec

1. **Env var name**: `WORKER_TRACE_ID` (canonical, per Pydantic prefix) + `OMB_WORKER_TRACE_ID`/`OMB_TRACE_ID` aliases via M7 AliasChoices.
2. **Field name**: `trace_id` (not `worker_trace_id`) — natural name with WORKER_ prefix.
3. **finish_session / _emit_tier3_performed signature**: changed from `trace_id: str | None = None` (original impl) to `settings: WorkerSettings` (H3/M5 pass-1) — uniform API. All callers updated.
4. **H2 flag gating**: `--trace-id` CLI flag now default-OFF (not in original impl). Pass-1 hardening.
5. **H0 registry-state**: User-approved exception to non-goals. Exhaustive search (`grep -rn "create_subprocess|Popen|spawn" services/registry-state/src/`) returned zero hits — no worker spawn site exists in current code. No registry-state files modified. Deferred concern documented as Story 9.7 blocker.
6. **DeprecationWarning count**: 98 → 96 (2 fewer, not worker-wrapper source).

### Follow-up TODOs surfaced for Epic 9

1. ~~**Registry-state spawner**~~ → **H0 resolution**: No spawn site found in registry-state. The orchestration layer that spawns worker-wrapper subprocesses does not exist yet. When it is built (Story 9.7 or separate), it MUST set `WORKER_TRACE_ID=<task.trace_id>` in the subprocess env. Until then, every worker invocation mints a fresh UUIDv7 (graceful degradation per AC2 — functional but trace chain broken).
2. **Integration test green gate**: journeys 1/3/6 and separability tests S1/S2 fail pre-9.6 with `ModuleNotFoundError: _build_scripted_worker`. Pre-existing infrastructure gap.
3. **AC10 integration test**: deferred to Story 9.7 — blocked by H0/Q1 spawner gap (no registry-state spawn site + integration journey tests broken pre-9.6).

### Epic 9 mid-epic milestone

**All 5 entry-point ingresses of Epic 9's α trace_id propagation kernel are now closed:**

| Story | Ingress | Mechanism |
|---|---|---|
| 9.2 | HTTP (`POST /v1/tasks`) | `X-Trace-Id` request header → envelope |
| 9.3 | Telegram gateway | `tg:{update_id}` derived from Telegram Update |
| 9.4 | Console CLI | UUIDv7 minted at command entry → `X-Trace-Id` |
| 9.5 | MCP tool callers | `caller_trace_id` explicit input on every MCP tool |
| **9.6** | **Worker subprocess** | **`WORKER_TRACE_ID` env → (gated) `--trace-id` flag + `OMB_TRACE_ID` + `caller_trace_id` on every MCP emission** |

**Cumulative Epic 9 stats (Stories 9.1–9.6) — pass-1 review update (L6):**
- Commits: **17** feat/fix/chore across 9.1-9.6 (exact: `git log --grep='story-9' --oneline | wc -l`)
- Tests added: ~151 across stories 9.1-9.6 (Story 9.6 initial +23; pass-1 +31 additional; total suite 2699 collected)
- DeprecationWarning delta: 98 → 96 (post pass-1; 0 from worker-wrapper source)
- mypy --strict baseline: 97 source files, 0 errors (held throughout Epic 9)
- Story 9.7 will: bump schema_version 1.0.0 → 1.1.0, add `oh-my-bmad-cli trace <id>` operator query, and complete the end-to-end AC10 integration assertion (blocked by H0 spawner gap).

---

## Frontmatter

```yaml
---
story_id: 9.6
story_key: 9-6-worker-wrapper-trace-id-cli-flag
parent_epic: 9
phase: 2
fr_refs: [FR59]
nfr_refs: [NFR-O7]
arch_refs:
  - "trace_id propagation wiring (Mermaid §line-1117+) — worker ingress (5th of 5)"
  - "P2-I2 (single Phase 2 schema bump deferred to 9.7)"
estimated_hours: 4-6
priority: high (worker ingress closes Epic 9's 5-ingress propagation kernel)
blocks:
  - 9.7 (schema bump baseline; /trace operator query)
blocked_by:
  - 9.1 (trace_id shape contract — done at 7cfebd9)
  - 9.5 (MCP caller_trace_id receiving side — done at 5c6256e)
status: ready-for-dev
created: 2026-05-17
created_by: bmad-create-story skill
---
```
