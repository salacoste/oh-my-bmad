# Story 9.6 — worker-wrapper passes `--trace-id` CLI flag to Claude Code

Status: **review**

## Story

**As** the worker-wrapper supervisor that spawns the `claude` Claude Code CLI subprocess AND emits events through the clawhip-bridge MCP server,
**I want** the worker to (a) receive its `trace_id` via the `WORKER_TRACE_ID` environment variable (canonical; `OMB_WORKER_TRACE_ID` and `OMB_TRACE_ID` accepted as aliases — review pass-2 PH10), (b) pass `--trace-id <uuid>` as a CLI flag to every `claude` subprocess invocation (gated by `emit_trace_id_flag`, default OFF), AND (c) supply that trace_id as `caller_trace_id` to every `clawhip-bridge.emit_*` MCP tool call,
**so that** every event emitted in the causal chain of a single operator-originated task — `session.started`, `task.execution.started`, `agent.reasoning.*`, `file.edited`, `task.step.completed`, `task.completed` — carries the SAME `trace_id` as the inbound operator command, closing the FIFTH AND FINAL ingress (worker) in Epic 9's α propagation kernel.

This is Story 9.6 of Epic 9 — the **caller side** of Story 9.5's MCP `caller_trace_id` contract. After 9.6 lands, Epic 9's wiring is complete: the trace_id flows end-to-end from operator command → ingress (HTTP / Telegram / console / MCP / worker) → registry-api → event log → cross-service correlation. Story 9.7 will then make the field MANDATORY (schema_version 1.0.0 → 1.1.0) + add the `oh-my-bmad-cli trace <id>` operator query.

---

## Acceptance criteria

### AC1 — Worker reads `trace_id` from `WORKER_TRACE_ID` env var (also `OMB_WORKER_TRACE_ID` / `OMB_TRACE_ID` aliases)

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

**Orchestrator-adapter parity (pass-3 TL4):** `OrchestratorSettings.trace_id` mirrors this contract (validator + post_init eager resolve + resolver + alias-fallthrough + repr-escaped log preview). See pass-3 TH1 for implementation. The orchestrator-adapter forwards only a VALIDATED value (via `settings.resolve_trace_id()`) as `caller_trace_id` on every emission and as `OMB_TRACE_ID` in the worker subprocess env.

### AC2 — Validation: invalid value → log WARNING + mint fresh UUIDv7

If `trace_id` is present but invalid (fails `is_valid_trace_id` per Story 9.1 contract), the worker logs at WARNING and mints a fresh UUIDv7 via `events.ids.new_uuid7()`. The worker does NOT crash on a malformed value — defensive degradation per Story 9.4 pass-2 lesson S2 (production-safe paths use raise/log, not assert).

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
    if settings.resolve_trace_id():
        args.extend(["--trace-id", settings.resolve_trace_id()])
    return args
```

The runner consults `settings.resolve_trace_id()` (per Story 9.6 review pass-1) — a per-instance memoized accessor minted eagerly in `model_post_init`.

**Robustness note (review pass-2 PL2):** Implementation chose HYBRID — `--trace-id` flag gated behind `emit_trace_id_flag` (default OFF, opt-in via `WORKER_EMIT_TRACE_ID_FLAG=1`) **plus** `OMB_TRACE_ID` env var ALWAYS set in `_spawn()`. The env var is the always-on non-breaking surface that ships today; the CLI flag is opt-in until Claude Code upstream confirms it consumes `--trace-id`.

### AC4 — Worker's clawhip-bridge MCP calls supply `caller_trace_id`

In `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` (or wherever the worker invokes `clawhip_bridge.call_tool("emit_event", ...)`), every call MUST include `caller_trace_id: settings.resolve_trace_id()` in the args dict.

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
        "caller_trace_id": settings.resolve_trace_id(),  # NEW — Story 9.6 / FR59
    },
)
```

### AC5 — All emission paths thread the same resolved `trace_id`

The worker's resolved `trace_id` is minted ONCE per worker invocation (eagerly in `WorkerSettings.model_post_init`) and reused across:
- The Claude Code subprocess `--trace-id` flag / `OMB_TRACE_ID` env var
- Every clawhip-bridge MCP call's `caller_trace_id`
- Any direct envelope construction (rare in worker-wrapper; check `lifecycle_manager.py` for any direct `EventEnvelope.create()` calls)

The per-invocation singleton is correct (NOT per-event mint) — every event from the SAME worker invocation shares the same trace_id, supporting the "single operator command → causal chain" correlation model.

### AC6 — Unit tests (≥12 floor; ~+61 delivered across pre-9.6 baseline through pass-3)

New tests in `services/worker-wrapper/src/worker_wrapper/test_*.py`:

1. `test_settings_accepts_valid_uuidv7_trace_id` — set `WORKER_TRACE_ID=<uuidv7>`; assert `WorkerSettings().trace_id` equals it. (Review pass-1 L5: canonical field/env name)
2. `test_settings_accepts_valid_tg_form_trace_id` — set `WORKER_TRACE_ID=tg:42`; assert acceptance.
3. `test_settings_rejects_invalid_trace_id_with_warning` — set `WORKER_TRACE_ID=bad-format`; assert WARNING log + validator strips → None.
4. `test_settings_silent_when_trace_id_absent` — env var unset; `WorkerSettings().trace_id is None`; no warning.
5. `test_build_args_includes_trace_id_flag` — instantiate `ClaudeCodeRunner` with `trace_id=tid` and `worker_emit_trace_id_flag=True`; call `_build_args("prompt")`; assert `["--trace-id", tid]` in result. (Review pass-1 H2: flag-gated)
6. `test_build_args_omits_trace_id_flag_when_gate_off` — flag=False (default); assert no `--trace-id` in args (only baseline flags).
7. `test_run_task_emits_events_with_caller_trace_id` — mock the clawhip-bridge MCP client; invoke `run_task(...)` with `WORKER_TRACE_ID=<known>` (or any accepted alias); assert every captured `emit_event` call's args dict contains `caller_trace_id=<known>`.
8. `test_worker_mints_fresh_trace_id_when_env_unset` — env unset; invoke run_task; capture the trace_id used in MCP calls; assert it matches UUIDv7 regex.
9. `test_worker_mints_fresh_trace_id_when_env_invalid` — env=`"bad"`; assert WARNING log + UUIDv7 fallback minted + propagated to MCP calls.
10. `test_trace_id_consistent_across_emission_sites` — within one run_task invocation, capture all `emit_*` MCP calls; assert all `caller_trace_id` values are byte-identical.
11. `test_subprocess_env_contains_omb_trace_id` (if env-var path implemented) — assert the subprocess env contains `OMB_TRACE_ID=<value>`.
12. (Optional integration test) `test_journey_with_worker_trace_id_propagates_to_jsonl` — run a full mini-journey; assert JSONL events all share the same trace_id from a known input value.

### AC7 — DeprecationWarning count drops

Before 9.6, the suite emits ~95 callsite DeprecationWarnings (post-9.5 baseline). After 9.6, the worker-wrapper callsite cluster stops emitting via the clawhip-bridge proxy path. Expected drop: ~2-5 (worker emission sites + any direct envelope construction).

Document actual measurement in Dev Agent Record. Following Story 9.3/9.4/9.5 lesson: SHAPE matters more than count.

### AC8 — mypy --strict baseline preserved

`uv run mypy --strict packages/ services/registry-api services/registry-state` exits 0 (98 source files, +1 from PH8 ratchet). Do NOT extend the CI command to include `services/worker-wrapper`. All other Epic 8.7 baseline gates remain green.

Test count delta (pass-1 + pass-2): pre-9.6 baseline 2644 → post pass-2 ~2706 collected. Original 9.6 dev added +23; pass-1 added +31; pass-2 adds +7 (regression tests for PH0 / PH1 / PH3 / PH7 / PM10).

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

**Orchestrator-adapter parity (pass-3 TH0/TH1/TH2):** Pass-3 closes the architectural gap pass-2 PH0 introduced. All 13+ `_emit_event` callsites in orchestrator-adapter now include `caller_trace_id` (TH0). `OrchestratorSettings.trace_id` mirrors the full worker-side defense pattern: validator + post_init eager resolve + resolver + alias-fallthrough (TH1). `OMCRunner.trace_id` lifted from per-process `__init__` to per-call `run(prompt, *, trace_id=...)` (TH2). The orchestrator-adapter propagation link forwards a VALIDATED trace_id (not raw env value) at every emission point.

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
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | Add `trace_id: str \| None` Pydantic field + validator (canonical field name; review pass-2 PH10) |
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

# Review pass-2 PL3 — implementation sketch removed.
# The Dev Agent Record below (and pass-1 / pass-2 patch summaries) fully
# documents the as-built shape: field `trace_id`, `_resolved_trace_id`
# PrivateAttr, eager resolve in `model_post_init`, alias-fallthrough for
# empty canonical env (PH1), AliasChoices including `"trace_id"` for ctor
# kwarg (PM10), and `resolve_trace_id()` as a pure read.
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
| `WORKER_TRACE_ID` / `OMB_WORKER_TRACE_ID` / `OMB_TRACE_ID` env var name conflicts with existing `OMB_*` settings | Verify via `grep "OMB_" services/worker-wrapper/src/`. All three names are accepted via `AliasChoices` (review pass-1 M7). |
| Worker is spawned by orchestrator-adapter (`OMCRunner._spawn`) — the spawner must set the trace_id on the child env. | Review pass-2 PH0: addressed in this story — `OMCRunner.__init__` accepts an optional `trace_id` and threads it into `env["OMB_TRACE_ID"]` for the child subprocess. Registry-state's spawning surface is covered by the ratchet test added in PH8 (asserts zero subprocess-spawn primitives in registry-state). |
| Resolved `trace_id` minted per-instance — what if `run_task` is called multiple times? | Mint per `run_task` invocation, not per `ClaudeCodeRunner` instance. AC5 specifies "per worker invocation" but the worker's lifecycle is "one run_task per worker process" so this is naturally fine. |

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

## Review Findings — pass-3 (2026-05-18)

Triaged from 3-lane third-opinion review = 25 raw findings → 22 unique after dedup. Pass-3 caught architectural gaps pass-2 introduced via PH0 scope expansion: orchestrator-adapter got the spawner-env path wired but NOT the producer-side MCP contract (13+ `_emit_event` calls without `caller_trace_id`) NOR the worker-side defense-in-depth pattern (no validator / resolver / post_init on `OrchestratorSettings.trace_id`). Plus per-process vs per-task trace_id semantics broken.

### Decision-needed (resolved)

- [x] [Review][Decision] **Q3 — per-process vs per-task trace_id** → resolved as per-task: lift trace_id from `OMCRunner.__init__` to `OMCRunner.run(prompt, *, trace_id)` so each spawn carries the correct task-level token. Promoted to **TH2** below.
- [x] [Review][Decision] **Q4 — orchestrator-adapter caller_trace_id** → resolved: add full Story 9.5 caller_trace_id contract to all 13+ `_emit_event` callsites in orchestrator-adapter. Promoted to **TH0** below.

### Patch — HIGH (10)

- [x] [Review][Patch] **TH0 — orchestrator-adapter 13+ `_emit_event` callsites missing `caller_trace_id`** [services/orchestrator-adapter/src/orchestrator_adapter/app/main.py:103-116, 182, 207, 218, 238, 250, 276, 289, 320, 374] — A1+B3. Story 9.5 made `caller_trace_id` REQUIRED on clawhip-bridge MCP tools. `_emit_event` signature has no `caller_trace_id` arg. Production runs will fail Pydantic validation; tests pass because they mock `_emit_event` entirely. Same blindspot as pass-1 H1 for session-registry. Fix: extend `_emit_event(clients, event_type, payload, *, label, caller_trace_id: str)`, thread `caller_trace_id=settings.resolve_trace_id()` to every callsite.
- [x] [Review][Patch] **TH1 — `OrchestratorSettings.trace_id` has no validator / resolver / post_init / fallthrough** [services/orchestrator-adapter/src/orchestrator_adapter/app/config.py:51-63] — A2+E2. Field is bare. Port the entire worker-side defense pattern (validator, post_init, resolver, alias-fallthrough, repr-escaped log preview) to `OrchestratorSettings`. Add regression tests.
- [x] [Review][Patch] **TH2 — `OMCRunner` trace_id is per-process, not per-task** [services/orchestrator-adapter/src/orchestrator_adapter/app/main.py:394-398, adapters/omc_runner.py:64,90] — E1. All tasks share ONE trace_id. Fix: lift trace_id from `OMCRunner.__init__` to `OMCRunner.run(prompt, *, trace_id)`. `adapter_loop` reads `task.trace_id` from each polled task. Regression test: two `runner.run()` with distinct trace_ids → distinct child envs.
- [x] [Review][Patch] **TH3 — PH8 ratchet regex weak** [services/registry-state/src/registry_state/test_no_subprocess_spawn.py] — B1+E3. Misses `os.system`, `subprocess.check_*`, `os.fork`, `multiprocessing.Process/Pool`, `pty.fork`. False positives on comments. Aliased imports bypass it. Fix: replace text-regex with AST-walk OR expand regex with `\b` anchors + self-test. Move test file out of scanned tree.
- [x] [Review][Patch] **TH4 — PH3 substring match false pos/neg** [services/worker-wrapper/src/worker_wrapper/app/main.py:113-122] — B4+E6. `"trace_id" in str(exc).lower()` over-matches. Fix: tighten to `"caller_trace_id" in str(exc).lower()`, OR define `CallerTraceIdContractError(ValueError)` in `events.envelope` for typed dispatch.
- [x] [Review][Patch] **TH5 — PH4 `_call_tool_best_effort` signature change risk** [services/worker-wrapper/src/worker_wrapper/app/main.py:103, 386-405] — B3. Pass-2 added `return_result: bool`; success path returning `None` indistinguishable from failure. Fix: verify all callers; consider extracting `_call_tool_best_effort_with_result()` as separate helper.
- [x] [Review][Patch] **TH6 — PH2 RuntimeError lacks instance context + no test** [services/worker-wrapper/src/worker_wrapper/app/config.py:286-289] — B10. Fix: add `cls={type(self).__name__}` to error messages. Add `test_resolve_trace_id_raises_when_post_init_skipped`.
- [x] [Review][Patch] **TH7 — PH7 backwards-compat alias precedence undefined + untested** [services/worker-wrapper/src/worker_wrapper/app/config.py:88-93] — B2. PM10 tested only `trace_id`. Fix: add `test_emit_flag_alias_priority_canonical_wins`.
- [x] [Review][Patch] **TH8 — PM5 eager session/worker_id breaks post-construction patching** [services/worker-wrapper/src/worker_wrapper/app/config.py:241-243] — B8. Grep tests for `patch.*new_session_id`; either restructure tests OR revert PM5 (keep lazy for session/worker, eager only for trace_id).
- [x] [Review][Patch] **TH9 — j-compose `WORKER_TRACE_ID: ${WORKER_TRACE_ID:-}` cosmetic** [tests/integration/docker-compose.j{1,3,6}.yml] — E5. No Makefile exports it. Fix: add harness recipe OR delete cosmetic lines and document in Dev Agent Record.

### Patch — MED (8)

- [x] [Review][Patch] **TM1 — PH0 leaks parent-process secrets in orchestrator-adapter** [services/orchestrator-adapter/.../omc_runner.py:75-82] — B5. Same B14-deferred pattern in new code. Fix: explicit allowlist OR document as deferred D4.
- [x] [Review][Patch] **TM2 — PM11 + PM13 duplicate trace_id_preview, three formats** — B6+A6. Extract `_safe_trace_preview(tid)` helper; reuse across PM11/PM13/H8. Add `trace_id_source: Literal["env","minted"]` field.
- [x] [Review][Patch] **TM3 — PH1 silently drops invalid aliases without per-fallback log** [services/worker-wrapper/src/worker_wrapper/app/config.py:118-135] — B7. Fix: emit `trace_id_alias_invalid` warning for each invalid alias visited in fallback loop.
- [x] [Review][Patch] **TM4 — Spec body "5 ingresses closed" contradicts 6-row table** [_bmad-output/.../9-6-...md:9, 144, 173, 554, 557] — A4. Reframe orchestrator-adapter row as "propagation link not ingress"; update 5 locations.
- [x] [Review][Patch] **TM5 — Line 442 AC10 deferral still says "no spawn site found"** [_bmad-output/.../9-6-...md:442] — A5. Rewrite to "Spawner closed by PH0+PH8; remaining blocker is pre-existing D2 integration test failures."
- [x] [Review][Patch] **TM6 — AC8 still says "97 source files"; actual 98** [_bmad-output/.../9-6-...md:129] — A3. Fix: `(98 source files, +1 from PH8 ratchet)`.
- [x] [Review][Patch] **TM7 — AC6 "≥12 unit tests" — actual ~+61** [_bmad-output/.../9-6-...md:104, 195, 208] — A7. Header → `### AC6 — Unit tests (≥12 floor; ~+61 delivered)`.
- [x] [Review][Patch] **TM8 — PH0 negative test doesn't verify PATH propagation** [services/orchestrator-adapter/.../test_omc_runner.py:546-565] — B9+E8. Strengthen with `assert captured_env.get("PATH") == os.environ["PATH"]`.

### Patch — LOW (4)

- [x] [Review][Patch] **TL1 — PH1 ctor kwarg with valid env alias silently falls through** — E7. Surprising precedence. Add info-log when fallback fires OR document explicitly.
- [x] [Review][Patch] **TL2 — PH0 unconditional `env=env` kwarg subtle semantic change** — E4. Document in Dev Agent Record OR conditional pass.
- [x] [Review][Patch] **TL3 — Worker's PH1 canonical-empty edge** — E2 partial. Worker's PH1 fallthrough doesn't cover canonical name being empty (only aliases). After TH1, orchestrator validates before forwarding so this shouldn't reach worker; still defensive to handle.
- [x] [Review][Patch] **TL4 — Documentation: spec body should mention orchestrator-adapter trace_id parity** — A2 partial overlap with TH1. After TH1 lands, add a paragraph to AC1 specifying orchestrator-adapter mirrors worker's contract.

---

## Review Findings — pass-2 (2026-05-17)

Triaged from 3-lane second-opinion review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) = 37 raw findings → 28 unique after dedup. Larger than typical pass-2 because pass-1 batch was rushed: missed `orchestrator-adapter/omc_runner.py` spawn site (real architectural gap, not just docs) and left ~half of L5 spec-body sweep undone.

### Decision-needed (resolved)

- [x] [Review][Decision] **Q2 — orchestrator-adapter/omc_runner.py is the spawn site H0 missed.** Resolved as option (a) — fix in this pass-2 (analogous to Q1). Promoted to **PH0** patch below.

### Patch — HIGH (12)

- [x] [Review][Patch] **PH0 — orchestrator-adapter `omc_runner.py` doesn't propagate trace_id** [services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py:53-68] — E5+E11. `OMCRunner._spawn` calls `asyncio.create_subprocess_exec("node", ..., "launch")` with NO `env=` kwarg → child inherits raw `os.environ` only. ANY OMC-spawned chain breaks at this boundary. Also: `tests/integration/docker-compose.j{1,3,6}.yml` worker-wrapper service has no WORKER_TRACE_ID env. Fix: add `trace_id: str | None` param to `OMCRunner.__init__`, set `env=dict(os.environ, OMB_TRACE_ID=trace_id)` in `_spawn`. Update j-compose files. Add regression test. THIS IS THE REAL H0 — pass-1 missed it.
- [x] [Review][Patch] **PH1 — empty `WORKER_TRACE_ID=""` blackholes valid `OMB_TRACE_ID` (alias-first-wins)** [services/worker-wrapper/src/worker_wrapper/app/config.py:83-100] — E1 (empirically reproduced). Pydantic `AliasChoices` picks first-present env var, not first-valid; empty string counts as present. Spawner using shell idiom `${VAR:-}` defensive-empty pattern silently drops valid fallback alias. Fix: in `_validate_trace_id_shape`, when value is invalid, fall through to remaining aliases via `os.environ.get()` manual lookup before logging WARNING and returning None.
- [x] [Review][Patch] **PH2 — `assert` in `resolve_trace_id` violates Story 9.4 S2 lesson** [services/worker-wrapper/src/worker_wrapper/app/config.py:215-217] — B2+A7+E2. `python -O` strips asserts; if `model_post_init` invariant ever fails, function returns None typed as str. Same lesson explicitly cited by AC2. Fix: replace `assert` with `if self._resolved_trace_id is None: raise RuntimeError("model_post_init invariant violated...")`.
- [x] [Review][Patch] **PH3 — H7 ValueError special-case is speculative and misclassifies benign errors** [services/worker-wrapper/src/worker_wrapper/app/main.py:103-122] — E3+B12+E6. No MCP server currently raises `ValueError` for caller_trace_id violations (`grep caller_trace_id` returns zero in server modules). MCP SDK can raise generic `ValueError` for unrelated transport reasons. Plus H8 sanitization now leaks worker's OWN valid trace_id to logs as `caller_trace_id_preview` even when violation is unrelated. Fix: narrow detection by checking `"trace_id" in str(exc).lower()` before classifying as trace_id-violation; otherwise re-raise into generic Exception branch. Add regression test asserting generic ValueError → `mcp_tool_call_failed`, trace-related ValueError → `mcp_tool_trace_id_invalid`.
- [x] [Review][Patch] **PH4 — `run_task._emit_event` closure swallows ValueError (H7 not applied)** [services/worker-wrapper/src/worker_wrapper/app/main.py:363-380] — A2. The inner `_emit_event` closure in `run_task` catches plain `Exception` with `emit_event_failed`. Three envelope types in hot task-execution path bypass H7. Fix: refactor to delegate to `_call_tool_best_effort`, OR duplicate the ValueError-special-case branch.
- [x] [Review][Patch] **PH5 — H6 fixture not in conftest.py; 3 test files leak env** [services/worker-wrapper/src/worker_wrapper/test_mcp_clients.py, test_main.py, adapters/test_github_client.py] — B1+E4. `_clean_trace_id_env` duplicated in 4 test files; missing in 3 others that construct `WorkerSettings()`. Plus 4-way duplication of `_TRACE_ID_ENV_NAMES` (E12). Fix: create `services/worker-wrapper/src/worker_wrapper/conftest.py` with single autouse fixture; delete 4 duplicates; reference shared constant from config module.
- [x] [Review][Patch] **PH6 — M6 tri-eq test uses `MagicMock()` not `AsyncMock(spec=Process)`** [services/worker-wrapper/src/worker_wrapper/test_run_task.py:607-610] — B3+E9. Contradicts M11 in the SAME patch series. Tolerant mock hides any future `_spawn` change that touches the returned process. Fix: replace with `AsyncMock(spec=asyncio.subprocess.Process)`.
- [x] [Review][Patch] **PH7 — `WORKER_WORKER_EMIT_TRACE_ID_FLAG` double-prefix (stuttering env name)** [services/worker-wrapper/src/worker_wrapper/app/config.py + test_config.py:1201] — B10. Exactly the same env-var-stutter trap M7 was added to fix. Fix: rename field to `emit_trace_id_flag` (env: `WORKER_EMIT_TRACE_ID_FLAG`), OR add `validation_alias=AliasChoices("WORKER_EMIT_TRACE_ID_FLAG", "WORKER_WORKER_EMIT_TRACE_ID_FLAG")` with the canonical name first.
- [x] [Review][Patch] **PH8 — H0 has no ratchet test** [services/registry-state/] — B6. Pass-1 H0 was marked `[x]` based on absence of code, not on a guard rail. Future commit adding `subprocess.Popen` to registry-state without setting WORKER_TRACE_ID would land silently. Fix: add `services/registry-state/test_no_subprocess_spawn.py` greping source tree for spawn primitives; assert zero matches (or, if any exist post-9.7, assert they pass WORKER_TRACE_ID env).
- [x] [Review][Patch] **PH9 — Q1 ↔ H0 internal contradiction** [_bmad-output/.../9-6-...md:364, 425, 487] — A3. Spec line 364 says Q1 resolved as option (a) "fix the spawner in this story"; H0's Dev Agent Record says no code modified. Two statements in same doc contradict. Fix: rewrite Q1 line to reflect that pass-1 search found zero in registry-state but pass-2 found omc_runner.py; PH0 in pass-2 addresses the real spawner.
- [x] [Review][Patch] **PH10 — L5 sweep incomplete: Story headline + AC1 title + multiple narrative sections** [_bmad-output/.../9-6-...md:8, 17, 41, 68, 194, 261-283, 339-340] — A1+E7+A10. `worker_trace_id`, `OMB_WORKER_TRACE_ID`, `_effective_trace_id` references remain in: Story headline (line 8), AC1 title (17), AC2 prose (41), AC3 prose (68), file-structure table (194), implementation sketch (261-283 — refs nonexistent `_effective_trace_id`), out-of-scope risks (339-340). Fix: sweep all 8+ sites with canonical names. Note AC1 now says "WORKER_TRACE_ID env var (also OMB_WORKER_TRACE_ID / OMB_TRACE_ID aliases)".
- [x] [Review][Patch] **PH11 — Pass-1 wrong commit count: L6 said 17, actual `git log --grep='story-9' --oneline \| wc -l` = 18** [_bmad-output/.../9-6-...md:509] — A5. The exact patch whose purpose was precision shipped a wrong precise number. Fix: re-run command; update count; if number drifts at finalization re-run again.

### Patch — MED (13)

- [x] [Review][Patch] **PM1 — AC8 stale: "+12 to +15 tests; full suite 2537 → ~2550-2560"** [_bmad-output/.../9-6-...md:135] — A4. Reality: 2668 → 2699 from pass-1 alone (+31); accounting for original dev (+23) total ~+54. Fix: update line 135 with actuals.
- [x] [Review][Patch] **PM2 — Legacy `test_settings_rejects_trace_id_with_crlf` still has `capture_logs()` w/o assert** [services/worker-wrapper/src/worker_wrapper/test_config.py:201-210] — A6. M9 added new parametrized test but never deleted/fixed the legacy one M9 explicitly named. Fix: delete the dead test (covered by new parametrized).
- [x] [Review][Patch] **PM3 — H6 fixture doesn't clear `WORKER_WORKER_EMIT_TRACE_ID_FLAG`** — A8+B11. CI exporting the flag silently overrides constructor kwargs in flag-gate tests. Fix: extend env-name list to include the flag env var (after PH7 rename, the canonical name).
- [x] [Review][Patch] **PM4 — H1 session.close assertion only checks key presence not value** [services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py:194-204] — B5. Regression that wires `caller_trace_id=""` would pass. Fix: strengthen to `assert args[1]["arguments"]["caller_trace_id"] == settings.resolve_trace_id()`.
- [x] [Review][Patch] **PM5 — `_resolved_session_id` / `_resolved_worker_id` retain lazy check-then-act despite H4's race fix for trace_id** [services/worker-wrapper/src/worker_wrapper/app/config.py:193-203] — B7. Asymmetric: if H4's race analysis is correct for trace_id, same applies to session_id/worker_id. Fix: eager-resolve all 3 in `model_post_init` for consistency.
- [x] [Review][Patch] **PM6 — Type narrowing fragile: `resolved: str = self.trace_id or new_uuid7()`** [services/worker-wrapper/src/worker_wrapper/app/config.py:186] — B8. Mypy can't see through validator `mode="before"`. Fix: explicit `if self.trace_id is not None else new_uuid7()`.
- [x] [Review][Patch] **PM7 — M9 parametrized assertion too weak (`preview.startswith("'")`)** [services/worker-wrapper/src/worker_wrapper/test_config.py:1130-1135] — B9. A regression that strips `repr()` and uses raw `value[:80]` still passes for alphanumeric inputs. Fix: assert `"\r" not in preview and "\n" not in preview and "\x00" not in preview`.
- [x] [Review][Patch] **PM8 — M6 test bypasses `_clean_trace_id_env` for flag env var** [services/worker-wrapper/src/worker_wrapper/test_run_task.py M6 test] — B11. AC9 cornerstone test should be most hermetic. After PH3 + PH7 fixed, extend fixture to cover new flag env name.
- [x] [Review][Patch] **PM9 — Dead `new_uuid7` import? Verify after H3** [services/worker-wrapper/src/worker_wrapper/app/main.py] — B12 partial. Pass-1 removed `new_uuid7` import. Re-verify no leftover usage; if all defensive mints gone, import is dead. Fix: remove if unused (verify with ruff first).
- [x] [Review][Patch] **PM10 — No alias-precedence test; `validation_alias=AliasChoices(...)` missing `"trace_id"` natural name** [services/worker-wrapper/src/worker_wrapper/app/config.py:26, 83-90] — B13+B15. When ALL THREE alias env vars set with different values, which wins? No test. Constructor kwarg works only via implicit `populate_by_name=True` coupling. Fix: add `test_alias_priority_canonical_wins_when_multiple_set`; add `"trace_id"` to AliasChoices.
- [x] [Review][Patch] **PM11 — No boot-time log announces flag state** [services/worker-wrapper/src/worker_wrapper/__main__.py:109] — E8. Operators can't tell if `--trace-id` is on. Fix: add `trace_id_emit_flag=settings.worker_emit_trace_id_flag, trace_id=settings.resolve_trace_id()` to the `"ready"` log call.
- [x] [Review][Patch] **PM12 — `model_post_init` non-defensive against future Pydantic v3 ordering changes** [services/worker-wrapper/src/worker_wrapper/app/config.py:186] — E10. If post_init fires before validator in v3, raw env string lands in cache, bypassing H8 sanitization. Fix: re-validate at cache layer: `resolved = self.trace_id if (self.trace_id and is_valid_trace_id(self.trace_id)) else new_uuid7()`.
- [x] [Review][Patch] **PM13 — `model_post_init` mints fresh w/o info log** [services/worker-wrapper/src/worker_wrapper/app/config.py:186] — B4. Validator emits WARNING then post_init mints silently; operators can't correlate "rejected upstream value" with "minted substitute". Fix: when self.trace_id is None and we mint, emit `info` log `worker_trace_id_minted_fresh` with `value_preview=resolved[:8]+"..."`.

### Patch — LOW (3)

- [x] [Review][Patch] **PL1 — Stale comment in test_session_lifecycle.py:182-184** — B14. Comment misleads after H1 added session.close MCP call. Fix: delete or strengthen.
- [x] [Review][Patch] **PL2 — AC3 robustness narrative still recommends option (b) env-var-only** [_bmad-output/.../9-6-...md:70-74] — A9. Pass-1 chose hybrid (env-var always-on + flag gated default-off) but AC3 narrative leads reader toward "drop the flag entirely". Fix: update AC3 robustness note to reflect hybrid choice.
- [x] [Review][Patch] **PL3 — Implementation sketch refs non-existent `_effective_trace_id` attribute** [_bmad-output/.../9-6-...md:277-297] — A10 (partial overlap with PH10). Either delete sketch (Dev Agent Record covers what was built) or update to match actual `resolve_trace_id()` pattern.

---

## Review Findings — pass-1 (2026-05-17)

Triaged from 3-lane adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) = 45 raw findings → 35 unique after dedup. Policy: dismiss-zero ("fix all issues even minors").

### Decision-needed (resolved)

- [x] [Review][Decision] **Q1 — spawner-side WORKER_TRACE_ID propagation** → resolved as option (a) — fix the spawner. Pass-1 investigated registry-state and found ZERO spawn sites (no code change required, H0 closed). Pass-2 discovered `services/orchestrator-adapter/adapters/omc_runner.py` — the REAL spawner — and addresses it as **PH0** below (review pass-2 PH9 — narrative reconciliation).

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

**pass-2 review (2026-05-17): 28 patches batch-applied.**

Pass-2's headline change is **PH0 — `orchestrator-adapter/adapters/omc_runner.py`**: the REAL worker-spawn site that pass-1 missed. `OMCRunner.__init__` gains an optional `trace_id` kwarg; `_spawn` builds an env (`dict(os.environ) + OMB_TRACE_ID`) and passes it to `create_subprocess_exec`. The construction site in `orchestrator-adapter/app/main.py:adapter_loop` was updated to thread `settings.trace_id`; `OrchestratorSettings` gained a `trace_id: str | None` field with the same `AliasChoices` shape (`trace_id` / `ORCHESTRATOR_TRACE_ID` / `OMB_ORCHESTRATOR_TRACE_ID` / `OMB_TRACE_ID`). The j-compose worker-wrapper services (j1/j3/j6) now propagate `WORKER_TRACE_ID` from the harness.

A new `services/worker-wrapper/src/worker_wrapper/conftest.py` autouse fixture (PH5) replaces 4 duplicate `_clean_trace_id_env` fixtures across `test_config.py`, `test_session_lifecycle.py`, `test_run_task.py`, `test_claude_code_runner.py`. The env-name list is referenced from `worker_wrapper.app.config._TRACE_ID_ALIASES` + `_EMIT_TRACE_ID_FLAG_ENV` constants so the fixture stays in sync with `AliasChoices` (covers PM3/PM8 as a side effect — the flag env var is also stripped).

`config.py` hardening: PH1 falls through to remaining aliases (`OMB_WORKER_TRACE_ID`, `OMB_TRACE_ID`) when the canonical `WORKER_TRACE_ID` is empty/invalid (the alias-first-wins blackhole is fixed); PM5 eager-resolves session_id/worker_id symmetrically with trace_id; PM6 narrows the type explicitly (`if self.trace_id is not None and is_valid_trace_id(self.trace_id)`); PM12 re-validates at the cache layer (defence-in-depth against future Pydantic v3 ordering changes); PM13 emits an `info` `worker_trace_id_minted_fresh` log when minting. PM10 added `"trace_id"` to `AliasChoices` so ctor kwarg works canonically; PH7 added `WORKER_WORKER_EMIT_TRACE_ID_FLAG` as a backwards-compat alias on `emit_trace_id_flag`.

`main.py` PH3 narrows the H7 ValueError special-case via `"trace_id" in str(exc).lower()` so benign transport ValueErrors are no longer misclassified; PH4 routes `run_task`'s `_emit_event` closure through `_call_tool_best_effort` (with a new `return_result=True` mode) so the H7 logic applies uniformly. `__main__.py` PM11 adds `trace_id_emit_flag` + `trace_id_preview` to the `ready` log call.

Tests: PH3 regression tests for narrowed ValueError detection (2 tests); PM4 strengthened session.close assertion to value-equality; PM2 deleted legacy CRLF test (covered by parametrized corpus); PM7 strengthened the parametrized assertion to check for absence of control chars in preview; PL1 clarified the stale comment; PM10 alias-precedence test (canonical wins when multiple set); PH1 regression test (empty canonical falls through to alias); PH7 backwards-compat alias test (legacy double-prefix still accepted); PH0 regression tests in `test_omc_runner.py` (2 tests — env contains/omits `OMB_TRACE_ID`). New `services/registry-state/src/registry_state/test_no_subprocess_spawn.py` ratchet test (PH8).

Spec hygiene: PH9 reconciled the Q1/H0 narrative; PH10 swept stale `worker_trace_id` / `OMB_WORKER_TRACE_ID` / `_effective_trace_id` references (8+ sites); PH11 corrected the commit count; PM1 updated the test-count delta; PL2 documented the chosen HYBRID approach in AC3 instead of recommending env-var-only; PL3 deleted the stale implementation sketch.

---

**pass-1 review (2026-05-17): 31 patches batch-applied.**

The worker-wrapper now participates fully in Epic 9's α trace_id propagation kernel. A `trace_id: str | None` Pydantic field was added to `WorkerSettings` with env var `WORKER_TRACE_ID` (canonical), plus `OMB_WORKER_TRACE_ID` and `OMB_TRACE_ID` aliases via `AliasChoices` (M7). A `@field_validator` validates the shape via `is_valid_trace_id()` — present-but-invalid values (including empty string, M2) log a WARNING with a `repr()`-escaped preview (H8) and fall back to None; absent values are silently None. `model_post_init` (H4) eagerly resolves the trace_id at construction so `resolve_trace_id()` is a pure read — no race. `_resolved_*` attrs converted to `PrivateAttr` (H5); class members reordered (L3).

The `--trace-id` CLI flag is now gated behind `worker_emit_trace_id_flag: bool = False` (H2, default OFF until Claude Code upstream consumes the flag). `OMB_TRACE_ID` env var is always set in `_spawn()` (the safe always-on surface). `finish_session` / `_emit_tier3_performed` now take `settings: WorkerSettings` instead of `trace_id: str | None = None` (H3 / M5) — defensive `or new_uuid7()` eliminated. All 3 session-registry MCP callsites (`session.register`, `session.heartbeat`, `session.close`) now carry `caller_trace_id` (H1). `_call_tool_best_effort` special-cases `ValueError` as `mcp_tool_trace_id_invalid` (H7).

All tests use `monkeypatch.delenv` for env isolation (H6), hoisted module-level imports (L1), `asyncio.wait_for` timeout guards on heartbeat tests (M4), deterministic iteration counter (M10), `AsyncMock(spec=Process)` for spawn mocks (M11), literal-constant trace_id assertions (M3), `new_uuid7` call_count verified by spy (M12), and `caller_trace_id` assertion in `test_finish_session_no_lock_without_worktree` (M13). M6 tri-surface byte-identity test added. M9 invalid-shape corpus parametrized (18 entries). H2 flag-gating tests added.

**H0 — registry-state spawner search results:** Exhaustive grep for `create_subprocess|Popen|spawn` in `services/registry-state/src/` returned zero hits. Registry-state is a pure event-log / SQLite service — it does NOT spawn worker-wrapper subprocesses. **Review pass-2 acknowledgment:** pass-1 stopped here, but the REAL spawner is `services/orchestrator-adapter/adapters/omc_runner.py` (the OMC subprocess supervisor that transitively spawns worker-wrapper instances). Pass-2 addresses it as PH0 (`OMCRunner.__init__(trace_id=...)` + `env["OMB_TRACE_ID"]` in `_spawn`). The registry-state ratchet test added in PH8 keeps that surface guarded going forward.

**AC10 deferral (M14):** Deferred to Story 9.7. Spawner-side wiring closed by pass-2 PH0 (orchestrator-adapter OMCRunner) + pass-3 TH0 (caller_trace_id on all 13+ emissions in orchestrator-adapter) + PH8 (registry-state ratchet). Remaining blocker: pre-existing D2 integration test failures (`_build_scripted_worker` ModuleNotFoundError). Story 9.7's `/trace <id>` query test will naturally exercise the now-complete chain once D2 is unblocked.

### Files changed

| File | Change |
|---|---|
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | H5 PrivateAttr; H4 model_post_init eager resolve; M1 local-var pattern; L3 member reorder; M7 AliasChoices; M2 empty→WARNING; L4 non-str→WARNING (merged); H8 repr() preview; H2 emit_trace_id_flag field; **PH1 alias-fallthrough; PH7 backwards-compat AliasChoices on flag; PM5 eager session_id/worker_id resolve; PM6 explicit narrowing; PM10 `"trace_id"` in AliasChoices; PM12 re-validate at cache; PM13 info log on mint; PH5 shared `_TRACE_ID_ALIASES` constant** |
| `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` | H2 flag-gated `--trace-id`; L2 comment rephrase |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | H1 caller_trace_id on all 3 session-registry callsites; H7 ValueError special-case; H3/M5 uniform `settings` API; L7 docstring updated; **PH3 narrowed ValueError detection; PH4 closure routed through `_call_tool_best_effort` (new `return_result=True` mode)** |
| `services/worker-wrapper/src/worker_wrapper/__main__.py` | L8/M5 finish_session call uses new signature; **PM11 ready log carries `trace_id_emit_flag` + `trace_id_preview`** |
| `services/worker-wrapper/src/worker_wrapper/conftest.py` (NEW) | **PH5 single autouse `_clean_worker_env` fixture replacing 4 duplicates** |
| `services/worker-wrapper/src/worker_wrapper/test_config.py` | H6 isolation fixture; M9 invalid-shape parametrize; M12 new_uuid7 spy; M7 alias tests; H2 flag tests; **PM2 legacy CRLF test removed; PM7 stronger preview-shape assertion; PM10 alias-priority test; PH1 empty-canonical-fallthrough test; PH7 legacy-flag-alias test; PH5 fixture deleted (moved to conftest)** |
| `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` | H6 isolation fixture (removed by PH5); L1 hoisted imports; M3 literal trace_id assertion; M11 AsyncMock(spec=Process); H2 flag tests |
| `services/worker-wrapper/src/worker_wrapper/test_session_lifecycle.py` | H6 isolation fixture (removed by PH5); L1 hoisted imports; M4 wait_for timeout; M10 deterministic counter; M13 caller_trace_id assertion; H1 session-registry caller_trace_id tests; **PM4 stronger session.close value-equality; PL1 stale comment fixed; PH3 narrowed ValueError regression tests** |
| `services/worker-wrapper/src/worker_wrapper/test_run_task.py` | H6 isolation fixture (removed by PH5); L1 hoisted imports; H3/M5 updated signatures; M6 tri-surface byte-identity test; H2 flag-gating tests |
| `services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py` (PH0) | **PH0: `OMCRunner.__init__(trace_id=...)` + `env["OMB_TRACE_ID"]` in `_spawn`** |
| `services/orchestrator-adapter/src/orchestrator_adapter/app/config.py` (PH0) | **PH0: `trace_id: str \| None` field with `AliasChoices`** |
| `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py` (PH0) | **PH0: thread `settings.trace_id` into `OMCRunner(...)` construction** |
| `services/orchestrator-adapter/src/orchestrator_adapter/test_omc_runner.py` (PH0) | **PH0: regression tests asserting env contains/omits `OMB_TRACE_ID`** |
| `services/registry-state/src/registry_state/test_no_subprocess_spawn.py` (NEW, PH8) | **PH8: ratchet test asserting zero `subprocess.Popen` / `create_subprocess_*` in registry-state** |
| `tests/integration/docker-compose.j{1,3,6}.yml` (PH0) | **PH0: propagate `WORKER_TRACE_ID` from harness to worker-wrapper service env** |
| `_bmad-output/implementation-artifacts/9-6-worker-wrapper-trace-id-cli-flag.md` | All 31 pass-1 + 28 pass-2 checkboxes checked; Dev Agent Record updated; PH9 Q1/H0 narrative reconciled; PH10 stale-refs swept; PH11 commit count corrected; PM1 test-count delta updated; PL2 AC3 hybrid choice documented; PL3 stale sketch deleted |
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

**pass-1 review update (H2): `--trace-id` CLI flag is now gated behind `emit_trace_id_flag: bool = False` (default OFF).**
**pass-2 review update (PH7): canonical env var is `WORKER_EMIT_TRACE_ID_FLAG`; the legacy `WORKER_WORKER_EMIT_TRACE_ID_FLAG` double-prefix name is kept as a backwards-compat alias only.**

Rationale: the spec explicitly warned that `claude --help` may not yet accept `--trace-id`; enabling an unknown flag risks subprocess spawn failure (every worker run). The env-var surface (`OMB_TRACE_ID`, always set in `_spawn()`) is non-breaking today. The flag will be flipped ON once Claude Code upstream confirms it accepts `--trace-id`. Document in CI: set `WORKER_EMIT_TRACE_ID_FLAG=1` to opt in (legacy double-prefix name still accepted).

`AliasChoices("trace_id", "WORKER_TRACE_ID", "OMB_WORKER_TRACE_ID", "OMB_TRACE_ID")` declared (M7 + review pass-2 PM10) so spawning services that set any of the three env names — or callers passing `trace_id=` to the ctor directly — reach the worker correctly. Review pass-2 PH1 closes the empty-canonical-blackholes-fallback edge case (alias-first-wins on presence vs. validity).

**Env var name deviation (unchanged from initial implementation):** `WORKER_TRACE_ID` is canonical (Pydantic `env_prefix="WORKER_"` + field name `trace_id`). `OMB_WORKER_TRACE_ID` and `OMB_TRACE_ID` accepted as aliases.

### Surprises / deviations from spec

1. **Env var name**: `WORKER_TRACE_ID` (canonical, per Pydantic prefix) + `OMB_WORKER_TRACE_ID`/`OMB_TRACE_ID` aliases via M7 AliasChoices.
2. **Field name**: `trace_id` (not `worker_trace_id`) — natural name with WORKER_ prefix.
3. **finish_session / _emit_tier3_performed signature**: changed from `trace_id: str | None = None` (original impl) to `settings: WorkerSettings` (H3/M5 pass-1) — uniform API. All callers updated.
4. **H2 flag gating**: `--trace-id` CLI flag now default-OFF (not in original impl). Pass-1 hardening.
5. **H0 registry-state**: User-approved exception to non-goals. Exhaustive pass-1 search (`grep -rn "create_subprocess|Popen|spawn" services/registry-state/src/`) returned zero hits — no worker spawn site exists in registry-state. **Pass-2 caveat:** pass-1 stopped at the obvious search target; review pass-2 PH0 discovered `services/orchestrator-adapter/adapters/omc_runner.py` (the REAL spawner) and addresses it in this story — `OMCRunner.__init__(trace_id=...)` + `env["OMB_TRACE_ID"]` in `_spawn`. PH8 added a ratchet test asserting registry-state stays spawn-free.
6. **PH7 field rename (review pass-2)**: original pass-1 introduced `emit_trace_id_flag` with effective env var `WORKER_EMIT_TRACE_ID_FLAG` already correct, but the spec/pass-1 narrative mentioned the double-prefix name. Pass-2 makes the canonical name explicit and adds the legacy double-prefix as a backwards-compat alias.
7. **PH0 scope expansion (review pass-2)**: user-approved exception via Q2 (analogous to Q1 in pass-1). orchestrator-adapter modification is outside the original non-goals boundary; user explicitly approved the scope-extension to land the real spawner-side fix.
8. **DeprecationWarning count**: 98 → 96 (2 fewer, not worker-wrapper source).
9. **PH0 unconditional `env=env` kwarg (pass-3 TL2)**: Pass-2 PH0's `OMCRunner._spawn` changed from no `env=` kwarg (pure `os.environ` inheritance) to always building an explicit `env = dict(os.environ)` dict and passing `env=env`. This is a subtle semantic change: previously the child process would inherit any env mutation made between process-start and the exec call; now it gets a dict copy at `_spawn` entry time. This is strictly safer (no TOCTOU on env mutation) but changes the inheritance model. Pass-3 TH2 completes this: trace_id is per-call kwarg, not stored on runner.
10. **TH0/TH1/TH2 scope expansion (review pass-3)**: user-approved via Q3+Q4 decisions. Three architectural gaps that PH0 introduced: (a) 13+ `_emit_event` sites in orchestrator-adapter lacked `caller_trace_id` (TH0); (b) `OrchestratorSettings.trace_id` had no defense-in-depth (TH1); (c) `OMCRunner` stored trace_id per-instance making all tasks share one token (TH2). All three closed in pass-3.

### Follow-up TODOs surfaced for Epic 9

1. ~~**Registry-state spawner**~~ → **PH0 resolution (pass-2)**: orchestrator-adapter/`OMCRunner` now propagates `trace_id` to the spawned subprocess env. Registry-state holds no spawn site (covered by the PH8 ratchet test going forward).
2. **Integration test green gate**: journeys 1/3/6 and separability tests S1/S2 fail pre-9.6 with `ModuleNotFoundError: _build_scripted_worker`. Pre-existing infrastructure gap.
3. **AC10 integration test**: deferred to Story 9.7 — blocked by the pre-existing scripted-worker infrastructure gap (above). The PH0 + TH0 + TH2 fixes have wired the full producer side; AC10 can now land once the journey harness is restored.
4. ~~**orchestrator-adapter `_emit_event` caller_trace_id**~~ → **TH0 resolution (pass-3)**: all 13+ orchestrator-adapter emission sites now carry `caller_trace_id`.
5. ~~**`OrchestratorSettings.trace_id` defense-in-depth**~~ → **TH1 resolution (pass-3)**: full validator + post_init + resolver ported.
6. ~~**`OMCRunner` per-process vs per-task trace_id**~~ → **TH2 resolution (pass-3)**: trace_id lifted to per-call `run()` kwarg.
7. **D4 — orchestrator-adapter `dict(os.environ)` env leak**: same hardening gap as worker-wrapper D1 — deferred to a separate hardening story (see deferred-work.md D4).

### Epic 9 mid-epic milestone

**All 5 ingresses + orchestrator-adapter internal propagation link now complete (pass-3 TH0/TH1/TH2 closed the gaps PH0 introduced).**

Note: the table has 5 ingress rows (9.2–9.6) plus one internal propagation link row (9.6 PH0). The propagation link is NOT a new ingress — the orchestrator-adapter forwards a task-scoped trace_id through OMCRunner into the worker env, so the worker's ingress (9.6) resolves it via `AliasChoices`. Pass-3 TM4 reframes this distinction explicitly.

| Story | Ingress / Link | Mechanism |
|---|---|---|
| 9.2 | Ingress — HTTP (`POST /v1/tasks`) | `X-Trace-Id` request header → envelope |
| 9.3 | Ingress — Telegram gateway | `tg:{update_id}` derived from Telegram Update |
| 9.4 | Ingress — Console CLI | UUIDv7 minted at command entry → `X-Trace-Id` |
| 9.5 | Ingress — MCP tool callers | `caller_trace_id` explicit input on every MCP tool |
| **9.6** | **Ingress — Worker subprocess** | **`WORKER_TRACE_ID` env → (gated) `--trace-id` flag + `OMB_TRACE_ID` + `caller_trace_id` on every MCP emission** |
| **9.6 PH0+TH0** | **Propagation link — OMC subprocess (orchestrator-adapter)** | **`OMCRunner.run(trace_id=task_trace_id)` per-call → `env["OMB_TRACE_ID"]`; all 13+ `_emit_event` sites carry `caller_trace_id=task_trace_id`** |

**Cumulative Epic 9 stats (Stories 9.1–9.6) — pass-3 review update:**
- Commits: **20** feat/fix/chore across 9.1-9.6 (pass-2 commit was 19; pass-3 adds 1 more — rerun `git log --grep='story-9' --oneline | wc -l` at finalization).
- Tests added: pre-9.6 baseline 2644 → after pass-3 ≈2725-2745 collected (original 9.6 dev +23, pass-1 +31, pass-2 +7, pass-3 ~20-30 net).
- DeprecationWarning delta: 98 → 96 (post pass-1; 0 from worker-wrapper source — preserved through pass-3).
- mypy --strict baseline: 98 source files, 0 errors (held throughout pass-3; orchestrator-adapter excluded from --strict CI command).
- Story 9.7 will: bump schema_version 1.0.0 → 1.1.0, add `oh-my-bmad-cli trace <id>` operator query, and land the AC10 integration assertion (PH0 + TH0 + TH2 unblocked the full producer side; only the scripted-worker harness restoration remains).

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
