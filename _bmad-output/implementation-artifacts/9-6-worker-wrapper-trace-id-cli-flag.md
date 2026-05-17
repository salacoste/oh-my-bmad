# Story 9.6 — worker-wrapper passes `--trace-id` CLI flag to Claude Code

Status: **ready-for-dev**

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
worker_trace_id: str | None = Field(
    default=None,
    description=(
        "Trace_id supplied by the spawning service for this worker invocation. "
        "Set via OMB_WORKER_TRACE_ID env var. Story 9.6 / FR59 / NFR-O7. "
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

1. `test_settings_accepts_valid_uuidv7_worker_trace_id` — set `OMB_WORKER_TRACE_ID=<uuidv7>`; assert `Settings().worker_trace_id` equals it.
2. `test_settings_accepts_valid_tg_form_worker_trace_id` — set `OMB_WORKER_TRACE_ID=tg:42`; assert acceptance.
3. `test_settings_rejects_invalid_worker_trace_id_with_warning` — set `OMB_WORKER_TRACE_ID=bad-format`; assert WARNING log + Pydantic accepts (don't crash) OR validator strips → None (then run_task mints fresh).
4. `test_settings_handles_absent_worker_trace_id_silently` — env var unset; `Settings().worker_trace_id is None`; no warning.
5. `test_build_args_includes_trace_id_flag_when_set` — instantiate `ClaudeCodeRunner` with a trace_id; call `_build_args("prompt")`; assert `["--trace-id", "<uuid>"]` in result.
6. `test_build_args_omits_trace_id_flag_when_unset` — instantiate without trace_id; assert no `--trace-id` in args (only baseline flags).
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

## Dev Agent Record

_(To be completed by the dev agent at story closure.)_

### Implementation summary
_(tbd)_

### Files changed
_(tbd)_

### Test count delta
_(tbd)_

### Callsite-warning observation
_(How many DeprecationWarnings still fire after Story 9.6? Expected drop: ~2-5 worker emission sites.)_

### CLI flag vs env var decision
_(Document whether `--trace-id` flag, `OMB_TRACE_ID` env var, or BOTH was implemented, and why.)_

### Surprises / deviations from spec
_(tbd)_

### Follow-up TODOs surfaced for Epic 9
_(tbd — likely: registry-state spawner must set OMB_WORKER_TRACE_ID; Story 9.7 trace-query test.)_

### Epic 9 mid-epic milestone
_(After 9.6 lands, all 5 ingresses are closed. Cumulative Epic 9 stats — commits, tests, review findings — worth pinning here as the milestone marker before Story 9.7 finishes the schema bump.)_

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
