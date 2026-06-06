## Phase 5 Architecture Amendment -- Multi-Runtime Worker Support

> **Amendment added:** 2026-06-06.
>
> **Companion documents:**
> - PRD amendment: see [`prd.md`](./prd.md) S"Phase 5 Scope Extension" (multi-runtime plane).
> - Runtime-abstraction decision: see [`docs/adr/0015-multi-runtime-adapter.md`](../../docs/adr/0015-multi-runtime-adapter.md) (proposed) -- resolves the pluggable-runtime surface deferred in architecture.md S"Runtime Execution" (FR31-36).
> - Authoring recipe: see [`docs/adr/0010-mcp-server-authoring.md`](../../docs/adr/0010-mcp-server-authoring.md) -- the Phase-3 canonical recipe extended for the 5th archetype.
> - Gate: see [`docs/adr/0016-phase-5-gate.md`](../../docs/adr/0016-phase-5-gate.md) (proposed) -- this section is the architecture amendment its acceptance criteria require.

**Theme.** The multi-runtime plane -- generalize the worker-wrapper's hardcoded `ClaudeCodeRunner` subprocess supervision into a runtime-abstracted adapter protocol, then introduce a **Codex adapter** (OpenAI's `codex` CLI) as the second concrete implementation. Phase 5 adds a **5th archetype: Multi-Runtime Worker** and a **runtime-selection factory**, not a new trust boundary -- every runtime adapter inherits the same tier-enforced authz, event-only telemetry, `trace_id` propagation, budget-supervisor discipline, and supply-chain hygiene as the Phase-4 fleet. Every Phase-1 through Phase-4 invariant stands.

### Preserved invariants (Phase 1 + Phase 2 + Phase 3 + Phase 4 carry forward)

All prior invariants stand unchanged. As they apply to the multi-runtime surface:

- **FR26 single-writer (P2-I1).** Runtime adapters do not write persisted state. They emit typed events through the single FR26 writer path (`clawhip-bridge`'s `EventLogWriter.append`). The adapter's subprocess stdout/stderr is consumed in-process and never directly persisted.
- **MCP transport stdio-only (P2-I4).** Multi-runtime adapters communicate with their CLI subprocess via stdio pipes (stdin/stdout/stderr), the same pattern as `ClaudeCodeRunner`. No new network surfaces.
- **Event-only telemetry (P2-I3 / NFR-O1/O10).** Runtime adapters emit typed events on the spine; they add **zero** instrumentation paths to any other service. Metrics for `task.execution.*` are derived by `metrics-subscriber` tailing the log, under the same bounded-cardinality discipline. The new `runtime` label is bounded by the registered adapter set (a closed enum, not free-form).
- **`trace_id` propagation (NFR-O7).** Every runtime adapter injects `OMB_TRACE_ID` into the child environment (and `--trace-id` CLI flag where the runtime supports it). The byte-identical `validate_caller_trace_id` contract covers all runtime adapters.
- **Tier-enforced authz (Epic 6 / P3-I1).** Runtime adapters do not bypass tier checks. The task driver (`lifecycle.py:run_task`) enforces approval gates before allowing Tier-3 actions regardless of which runtime produced the action. The `needs_approval` classification is runtime-agnostic -- it inspects `ExtractedEvent.event_type`, not the subprocess that produced it.
- **Supply-chain (Epic 8 + G-SEC-1/2).** The `codex` binary is a pinned dependency in the base image (pinned version, verified checksum in `Dockerfile.base`). It is NOT installed at runtime. The child-env allowlist is expanded for `CODEX_*` vars, following the same explicit-allowlist discipline as `_CHILD_ENV_ALLOWLIST` in `claude_code_runner.py:77-97`.
- **Budget supervision (Story 12.1 / NFR-R8).** `BudgetSupervisor` continues to tail the JSONL event log for `task.budget_exceeded` and invoke the runtime adapter's terminate callback. The `watch_for_budget_exceeded` function is runtime-agnostic -- it calls the injected `terminate_callback`, which the task driver wires to the active adapter's `kill()` method.

### New invariants (delta from P4-I1..I3)

Phase 5 introduces **three** new discipline rules on top of the preserved set.

| # | Invariant | Why |
|---|---|---|
| **P5-I1** | **Credential isolation between runtimes -- no cross-runtime secret leakage.** Each runtime adapter has its own explicit env-var allowlist. `ANTHROPIC_API_KEY` appears ONLY in `ClaudeCodeRunner`'s allowlist. `OPENAI_API_KEY` appears ONLY in `CodexRunner`'s allowlist. Shared functional vars (PATH, HOME, LANG, TMPDIR, TLS certs) appear in both. The factory function `get_runtime_adapter(settings)` selects the adapter; the adapter selects the allowlist. The CI-gate is a negative test asserting that `OPENAI_API_KEY` is absent from Claude's child env and `ANTHROPIC_API_KEY` is absent from Codex's child env, even when BOTH are present in the parent process. | The parent process (worker-wrapper) has access to operator secrets for multiple LLM providers. Forwarding all secrets to every child subprocess would leak Anthropic credentials into an OpenAI-managed process (and vice versa). The existing `_CHILD_ENV_ALLOWLIST` discipline (G-SEC-2 / D1) prevents this for Claude Code; Phase 5 extends it to a per-runtime allowlist architecture. |
| **P5-I2** | **Runtime output is parsed exclusively via structured formats -- no stdout scraping.** Each adapter declares its output format (`stream-json` for Claude Code, `--json` JSONL for Codex). The `RuntimeAdapter.parse_output(raw)` method deserializes structured JSON only. Regex-based text parsing of stdout is forbidden (NFR-O1). The CI-gate asserts that no adapter module contains `re.search` or `re.match` calls on subprocess output. | The platform's NFR-O1 "no stdout parsing" invariant was established in Phase 1 and enforced by a custom `ruff` rule. Codex's JSONL mode (`--json`) provides the same structured-output guarantee as Claude Code's `stream-json`. Mandating structured output per-adapter prevents regression to fragile text parsing. |
| **P5-I3** | **Budget enforcement is runtime-agnostic -- every adapter implements `kill()`.** The `BudgetSupervisor.watch_for_budget_exceeded` function calls an injected `terminate_callback`. The task driver wires this to the active adapter's `kill()` method. Every runtime adapter MUST implement `kill()` with SIGTERM -> grace period -> SIGKILL escalation, matching the `ClaudeCodeRunner.terminate_with_grace` semantics. The CI-gate is a contract test asserting that every registered `RuntimeAdapter` subclass implements `kill()` and that the terminate-with-grace path produces a `TerminationResult`-compatible return. | Budget enforcement must work identically regardless of runtime. A Codex subprocess that exceeds its token budget must be SIGTERMed with the same grace period and escalation guarantees as a Claude Code subprocess. Without this invariant, a new runtime could leak tokens indefinitely if its kill path were missing or broken. |

### New archetype: Multi-Runtime Worker (5th archetype)

The existing four archetypes (subprocess-sandbox, REST-client, own-store, browser worker) describe how an MCP server interacts with the outside world. Phase 5 adds a fifth:

**Multi-Runtime Worker archetype:**
- **Wraps a CLI subprocess** managed via async pipes (stdin/stdout/stderr), like the existing `ClaudeCodeRunner` (4th archetype's sibling). The adapter owns the subprocess lifecycle: spawn, stream-read, health-check, kill.
- **Runtime selection is configurable** at two levels: (a) `WorkerSettings.runtime` sets the default for all tasks; (b) `TaskCreatedPayload.runtime` overrides per-task, allowing heterogeneous workloads (one task on Claude, the next on Codex).
- **Output parsing is adapter-specific** but produces a uniform result type (`RuntimeResult`) consumed by the task driver. The driver does not know which runtime produced the events -- it sees `ExtractedEvent` objects and `RuntimeResult` dataclasses.
- **Credential injection is adapter-specific** with per-runtime allowlists (P5-I1). The factory selects the adapter; the adapter selects its allowlist. The parent process never forwards secrets not on the active adapter's list.

### Runtime abstraction layer

The core architectural addition is the `RuntimeAdapter` protocol -- a runtime-abstracted interface that decouples the task driver from any specific CLI subprocess.

#### Protocol definition

```python
# services/worker-wrapper/src/worker_wrapper/domain/runtime_adapter.py

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from worker_wrapper.adapters.claude_code_runner import ExtractedEvent, TerminationResult


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Protocol for pluggable CLI runtime adapters (P5 archetype).

    Each adapter manages the full lifecycle of one CLI agent subprocess:
    spawn, stream-read structured output, health-check, and terminate.
    The task driver (lifecycle.py:run_task) depends on this protocol,
    not on any concrete adapter class (Dependency Inversion per D2).
    """

    @property
    def runtime_name(self) -> str:
        """Runtime identifier: 'claude-code' | 'codex' | future names."""
        ...

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        env: dict[str, str] | None,
        cwd: Path,
    ) -> asyncio.subprocess.Process:
        """Spawn the runtime subprocess. Returns the process handle.

        The adapter builds CLI args from its own configuration. The optional
        ``env`` overlay is merged on top of the adapter's built-in allowlist
        (P5-I1). ``cwd`` is the task's worktree (acquired via acquire_lock).
        """
        ...

    async def is_healthy(self) -> bool:
        """Return True if the subprocess is still alive and responsive."""
        ...

    async def parse_output(self, raw: bytes) -> list[ExtractedEvent]:
        """Parse a single stdout line into typed events.

        P5-I2: structured JSON deserialization only -- no regex on raw text.
        Returns an empty list for non-event lines (heartbeat, system info).
        """
        ...

    async def kill(self, grace_period_s: float = 5.0) -> TerminationResult:
        """Terminate the subprocess with SIGTERM -> wait -> SIGKILL escalation.

        P5-I3: every adapter MUST implement this with the same semantics as
        ClaudeCodeRunner.terminate_with_grace (SIGTERM -> grace -> SIGKILL).
        Returns a TerminationResult for budget-supervisor telemetry.
        """
        ...
```

#### Concrete adapter: `ClaudeCodeRunner` (refactored)

The existing `ClaudeCodeRunner` in `adapters/claude_code_runner.py` is refactored to satisfy the `RuntimeAdapter` protocol without breaking its existing public API. The adapter's existing methods (`run`, `cancel`, `terminate_with_grace`) are retained; the protocol methods delegate to them:

- `runtime_name` -> `"claude-code"`
- `spawn()` -> wraps `_spawn()`
- `is_healthy()` -> checks `self._process.returncode is None`
- `parse_output()` -> delegates to `_handle_message()` (already JSON-only per `_SUPPORTED_OUTPUT_FORMATS`)
- `kill()` -> wraps `terminate_with_grace()`

No behavioral change. The refactoring is structural only -- the `ClaudeCodeRunner` class already implements all protocol semantics. The `run()` convenience method is retained for backward compatibility; the task driver gains a new `run_with_adapter()` path that uses the protocol methods directly.

#### Concrete adapter: `CodexRunner`

New adapter in `adapters/codex_runner.py`. Spawns OpenAI's `codex` CLI as a subprocess.

**Codex CLI specifics mapped to the adapter:**

| Adapter method | Codex CLI invocation |
|---|---|
| `spawn()` | `codex exec --json "<prompt>"` with `cwd` set to the worktree. The `--json` flag produces JSONL output with `turn.completed` events including token usage (P5-I2). |
| `runtime_name` | Returns `"codex"`. |
| `is_healthy()` | Checks `self._process.returncode is None`. |
| `parse_output()` | Deserializes each JSONL line. Extracts events from `turn.completed` messages (tool calls mapped to `ExtractedEvent` via a Codex-specific `_classify_tool_use` -- mirrors Claude Code's pattern). Token usage extracted from `usage` fields in `turn.completed`. |
| `kill()` | SIGTERM -> 5s grace -> SIGKILL. Mirrors `ClaudeCodeRunner.terminate_with_grace` semantics exactly (P5-I3). |

**Codex exit code mapping:**

| Exit code | `RuntimeResult` state |
|---|---|
| 0 | `completed` (success) |
| 1 | `failed` (task error, message in stderr) |
| 2 | `failed` (invalid arguments / configuration) |
| 130 | `cancelled` (SIGTERM received) |
| 137 | `cancelled` (SIGKILL received) |
| -1 | `timeout` (adapter-level timeout, not from Codex itself) |

**Codex sandboxing:** Codex has built-in OS-level sandboxing (Seatbelt on macOS, Landlock on Linux) that is **stronger** than Claude Code's sandbox. The adapter does NOT disable it. This is defense-in-depth: Codex's built-in sandbox + Docker's seccomp/user-namespace isolation + the credential-isolation allowlist (P5-I1).

#### Factory function

```python
# services/worker-wrapper/src/worker_wrapper/adapters/runtime_factory.py

from __future__ import annotations

from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.runtime_adapter import RuntimeAdapter


def get_runtime_adapter(settings: WorkerSettings) -> RuntimeAdapter:
    """Return the concrete RuntimeAdapter for the configured runtime.

    Selection priority:
    1. settings.runtime (WorkerSettings field, defaults to "claude-code").
    2. Falls back to ClaudeCodeRunner if the field is blank/unrecognized
       (backward-compat: existing deployments without WORKER_RUNTIME set
       continue to use Claude Code without code changes).

    Raises ValueError for unrecognized runtime names (fail-loud, not silent fallback).
    """
    runtime = settings.runtime or "claude-code"
    if runtime == "claude-code":
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner
        return ClaudeCodeRunner(settings)
    if runtime == "codex":
        from worker_wrapper.adapters.codex_runner import CodexRunner
        return CodexRunner(settings)
    raise ValueError(
        f"Unknown runtime: {runtime!r}. "
        f"Supported: 'claude-code', 'codex'"
    )
```

### Runtime selection in worker-wrapper

**`WorkerSettings` gains a `runtime` field:**

```python
# services/worker-wrapper/src/worker_wrapper/app/config.py

# Phase 5 -- runtime selection. Defaults to "claude-code" for backward compat:
# existing deployments without WORKER_RUNTIME set continue using Claude Code.
# The factory (runtime_factory.py) resolves this to a concrete RuntimeAdapter.
# Per-task override: TaskCreatedPayload.runtime (set by the orchestrator).
runtime: str = "claude-code"

# Codex-specific settings (latent scaffold -- only consulted when runtime="codex").
codex_command: str = "codex"
codex_timeout_s: float = 600.0
openai_api_key: str = ""  # WORKER_OPENAI_API_KEY
```

**Task-level override:** `TaskCreatedPayload` gains an optional `runtime: str | None` field. When set, the task driver uses `get_runtime_adapter(settings)` with the override value instead of the worker-level default. This allows heterogeneous workloads: the operator dispatches some tasks to Claude Code and others to Codex via the orchestrator.

**Task driver integration:** `lifecycle.py:run_task` currently creates a `ClaudeCodeRunner(settings)` directly. This is replaced with `get_runtime_adapter(settings)` (or the task-level override). The returned `RuntimeAdapter` is used for the entire task lifecycle: spawn -> stream -> parse -> health-check -> kill. No other changes to the task driver -- it already operates on structured events and results, not on runtime-specific APIs.

### Credential isolation architecture

The existing `_CHILD_ENV_ALLOWLIST` in `claude_code_runner.py:77-97` is the model. Phase 5 extends this to a per-runtime allowlist system:

**Claude Code allowlist** (unchanged from existing):

```python
# claude_code_runner.py -- existing _CHILD_ENV_ALLOWLIST
# ANTHROPIC_API_KEY is injected separately in _spawn(), NOT in the allowlist.
_CHILD_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER",
    "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TMP", "TEMP",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})
_CHILD_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "CLAUDE_")
```

**Codex allowlist** (new, parallel structure):

```python
# codex_runner.py -- P5-I1 credential isolation
_CODEX_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USER",
    "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TMP", "TEMP",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})
_CODEX_ENV_PREFIXES: tuple[str, ...] = ("OMB_", "CODEX_")
# OPENAI_API_KEY is injected separately in _spawn(), NOT in the allowlist.
```

**Key property:** `ANTHROPIC_API_KEY` is absent from `_CODEX_ENV_ALLOWLIST` and its prefixes. `OPENAI_API_KEY` is absent from `_CHILD_ENV_ALLOWLIST` and its prefixes. Neither API key is in the shared functional vars. The CI-gate asserts this via a negative test that sets both keys in the parent env and verifies each adapter's `_build_child_env()` excludes the other's key.

**Secret injection:** Each adapter injects its own API key in `_spawn()` from settings, mirroring the existing Claude Code pattern (`claude_code_runner.py:258-259`):

- `ClaudeCodeRunner._spawn()`: `env["ANTHROPIC_API_KEY"] = self._settings.anthropic_api_key`
- `CodexRunner._spawn()`: `env["OPENAI_API_KEY"] = self._settings.openai_api_key`

### Event schema extensions

**`task.execution.started` gains a `runtime` field.** The payload `TaskExecutionStartedPayload` gains an optional `runtime: str | None` field (additive, default `None` for backward compat with existing events). This field is populated by the task driver with the active adapter's `runtime_name`. Registered at `1.2.0` (additive evolution per NFR-M3).

```python
# packages/events/payloads.py -- TaskExecutionStartedPayload extension
class TaskExecutionStartedPayload(BasePayload):
    task_id: str
    session_id: str
    trace_id: str | None = None
    runtime: str | None = None  # P5: "claude-code" | "codex" | None (legacy)
```

**New `task.runtime_handoff` event type.** Emitted when a task switches runtime mid-execution (the `/handoff` command). Payload includes source runtime, target runtime, and a context summary from the source runtime:

```python
class TaskRuntimeHandoffPayload(BaseModel):
    task_id: str
    trace_id: str | None = None
    source_runtime: str       # "claude-code"
    target_runtime: str       # "codex"
    source_session_id: str    # session being terminated
    target_session_id: str    # new session being created
    context_summary: str      # brief summary from source runtime
```

Registered at `1.1.0` (new Phase-5 event type, no `1.0.0` predecessor, same convention as Phase-3/4 new types).

**Session events gain `runtime` label.** `SessionStartedPayload` and `SessionHeartbeatPayload` gain an optional `runtime: str | None` field. This is propagated from the active adapter at session start. The `metrics-subscriber` can derive `omb_session_active{runtime="codex"}` counters from this field, bounded by the closed enum of registered runtimes.

**All new `runtime` fields are optional (default `None`)** for backward compat with existing events. Existing consumers that do not check the field are unaffected. The schema-registry migration is additive-only per NFR-M3.

### Budget tracking per-runtime

**`BudgetSupervisor` is runtime-agnostic by design.** It tails the JSONL event log for `task.budget_exceeded` and invokes a `terminate_callback`. The callback is wired by the task driver to the active adapter's `kill()` method. No changes to `BudgetSupervisor`'s core loop.

**Token counting uses runtime-specific parsers:**

- **Claude Code:** Token usage comes from the `result` message in `stream-json` output (`cost_usd`, `duration_ms`, `num_turns` fields in `_result_msg`). The existing `ClaudeCodeRunner._build_result()` already extracts these.
- **Codex:** Token usage comes from `usage` fields in `turn.completed` JSONL events. The `CodexRunner.parse_output()` extracts input/output token counts from each turn.

**Budget limits CAN differ by runtime.** The `TaskCreatedPayload` budget fields (`token_limit`, `budget_policy`) apply uniformly. However, the **token accounting** is runtime-specific because Claude Code and Codex report token usage in different formats and granularities. The adapter is responsible for normalizing its runtime's token report into the platform's `token_usage` event field.

**Per-runtime budget configuration (future):** The architecture reserves a `runtime_budget_overrides` field in `WorkerSettings` (latent scaffold, not wired in Phase 5):

```python
# Latent scaffold -- not wired in Phase 5.
# Allows operators to set different token limits per runtime
# (e.g., codex_token_limit: 50000 vs claude_token_limit: 100000).
# Phase 5 uses the unified token_limit from TaskCreatedPayload.
runtime_budget_overrides: dict[str, int] = {}
```

### Runtime handoff flow

The `/handoff <task_id> <target_runtime>` command allows the operator to switch a running task from one runtime to another mid-execution.

**Flow:**

1. Operator issues `/handoff t-7f2a codex` via Telegram or Console.
2. `registry-api` receives the command, emits `task.runtime_handoff` event.
3. Worker-wrapper's task driver detects the handoff event (via JSONL tail, same pattern as approval events).
4. Task driver requests a **context summary** from the active runtime adapter (a new `get_context_summary()` method on the adapter protocol -- optional, returns empty string if unsupported).
5. Task driver calls `kill()` on the active adapter (SIGTERM -> grace -> SIGKILL).
6. Task driver calls `get_runtime_adapter()` with the target runtime.
7. Task driver calls `spawn()` on the new adapter, passing the context summary as part of the prompt.
8. New adapter starts in the same worktree (lock is preserved, not released) with the same `trace_id`.
9. `task.execution.started` event emitted with the new `runtime` field.

**Constraints:**

- Worktree lock is NOT released during handoff (the task owns it for the entire lifecycle).
- The handoff is **one-way per invocation** -- a second handoff requires a second `/handoff` command.
- The context summary is best-effort -- if the source runtime cannot produce one, the target runtime starts with only the task description from the original `TaskCreatedPayload`.
- Handoff during an approval gate is blocked -- the task must be in `running` state, not `awaiting_approval`.

### ADR-0010 recipe extension

The MCP-server-authoring recipe (ADR-0010) is extended with a new server type: **runtime adapter**.

**New recipe step 9: Runtime adapter contract.** Applies when a new CLI runtime is added to the multi-runtime worker:

1. **Create adapter module.** `adapters/<runtime>_runner.py` implementing the `RuntimeAdapter` protocol.
2. **Define env allowlist.** A `_RUNTIME_ENV_ALLOWLIST` frozenset and `_RUNTIME_ENV_PREFIXES` tuple. The allowlist MUST NOT include any other runtime's API key (P5-I1). The contract test asserts this.
3. **Register in factory.** Add the new runtime name to `get_runtime_adapter()`.
4. **Add to `runtime` enum.** The `WorkerSettings.runtime` field documentation and the factory's `ValueError` message list all supported runtimes.
5. **Output parsing contract.** `parse_output()` MUST use structured JSON deserialization only (P5-I2). The contract test asserts no regex on subprocess output.
6. **Kill contract.** `kill()` MUST implement SIGTERM -> grace -> SIGKILL with the same semantics as `ClaudeCodeRunner.terminate_with_grace` (P5-I3). The contract test asserts `TerminationResult`-compatible return.
7. **Event registration.** Register `task.runtime_handoff` payload at `1.1.0` if not already registered.
8. **Budget integration.** Token usage extracted from the runtime's structured output and normalized into the platform's `token_usage` field.

**ATDD contracts for runtime adapters:**

| Contract | What it asserts |
|---|---|
| Credential isolation | `OPENAI_API_KEY` absent from Claude's child env; `ANTHROPIC_API_KEY` absent from Codex's child env. |
| Output parsing | `parse_output()` returns `list[ExtractedEvent]`; no `re.search`/`re.match` in adapter module. |
| Kill semantics | `kill()` returns `TerminationResult` with `method` in `{"noop", "sigterm", "sigkill"}`. |
| Health check | `is_healthy()` returns `False` after `kill()` completes. |
| Runtime name | `runtime_name` returns a string in the closed set `{"claude-code", "codex"}`. |
| Factory completeness | `get_runtime_adapter()` returns the correct adapter for each registered name; raises `ValueError` for unknown names. |

### Fleet integration

**No new MCP fleet member.** Phase 5 is a worker-wrapper internal refactoring. The multi-runtime adapter is an internal abstraction, not a new externally-visible MCP server. The existing fleet members (git, github, verification, memory, artifact, browser) are runtime-agnostic and are spawned identically regardless of which runtime adapter is active.

**`WorkerSettings` additions (latent scaffold pattern):**

```python
# services/worker-wrapper/src/worker_wrapper/app/config.py

# Phase 5 -- runtime selection (defaults to "claude-code" for backward compat).
# Set via WORKER_RUNTIME env var. Per-task override via TaskCreatedPayload.runtime.
runtime: str = "claude-code"

# Codex-specific settings (only consulted when runtime="codex").
codex_command: str = "codex"
codex_timeout_s: float = 600.0
openai_api_key: str = ""  # WORKER_OPENAI_API_KEY
```

**Task driver wiring:**

```python
# services/worker-wrapper/src/worker_wrapper/app/main.py (run_task modification)

# Before (Phase 4):
runner = ClaudeCodeRunner(settings)

# After (Phase 5):
adapter = get_runtime_adapter(settings)
# Per-task override (if TaskCreatedPayload.runtime is set):
if task_runtime_override:
    adapter = get_runtime_adapter(settings, runtime=task_runtime_override)
```

### Per-epic wiring decisions

**Epic 23 -- Runtime abstraction layer.** `services/worker-wrapper/src/worker_wrapper/domain/runtime_adapter.py` (protocol definition) + `adapters/runtime_factory.py` (factory function) + refactoring `ClaudeCodeRunner` to satisfy the protocol. Key decisions:

- **Protocol, not ABC.** `RuntimeAdapter` is a `typing.Protocol` with `@runtime_checkable`. This allows the existing `ClaudeCodeRunner` to satisfy the protocol without inheriting from a base class (structural subtyping). No changes to `ClaudeCodeRunner`'s existing public API.
- **Lazy imports in factory.** `get_runtime_adapter()` uses lazy imports for each adapter module. This avoids importing `codex_runner.py` (and its `codex` binary dependency) when only Claude Code is configured. Codex can be absent from the base image on deployments that only use Claude Code.
- **Fail-loud on unknown runtime.** The factory raises `ValueError` for unrecognized runtime names. Silent fallback to Claude Code would mask operator misconfiguration.

**Epic 24 -- Codex adapter implementation.** `services/worker-wrapper/src/worker_wrapper/adapters/codex_runner.py`. The reference implementation for the 2nd runtime. Key decisions:

- **`--json` flag is mandatory.** The adapter always passes `--json` to `codex exec`. The `--json` flag produces JSONL output with structured `turn.completed` events. Non-JSON output mode is not supported (P5-I2).
- **Built-in sandbox is preserved.** Codex's built-in OS-level sandbox (Seatbelt/Landlock) is NOT disabled. This provides defense-in-depth: the adapter does not pass `--no-sandbox` or equivalent flags.
- **Session resume is not wired in Phase 5.** `codex exec resume <session-id>` exists but is deferred. Phase 5 uses `codex exec` for fresh sessions only. Resume is a Phase 6 follow-up.
- **Structured output (`--output-schema`) is deferred.** The `--output-schema schema.json` flag for constrained output is a future optimization. Phase 5 parses unconstrained JSONL output.

**Epic 25 -- Event schema + metrics + CI hardening.** Registers `task.runtime_handoff` event type in `domain/event_types.py` + extends `TaskExecutionStartedPayload` with `runtime` field. Extends `metrics-subscriber` cardinality regression for the new `runtime` label. CI-gate additions:

- P5-I1 credential isolation negative test (no cross-runtime API key leakage).
- P5-I2 output-parsing contract test (no regex in adapter modules).
- P5-I3 kill-semantics contract test (every adapter produces `TerminationResult`).
- Separability test: worker functions with `runtime="codex"` AND with `runtime=""` (backward compat).
- Factory completeness test: all registered names resolve; unknown names raise `ValueError`.

### Forward-referenced ADRs (proposed; each gates its epic)

Each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges.

- **ADR-0015** -- Multi-runtime adapter protocol (RuntimeAdapter protocol, factory function, credential isolation architecture, per-runtime allowlists, output-parsing contract). **Gates Epic 23.** `docs/adr/0015-multi-runtime-adapter.md`.
- **ADR-0016** -- Phase 5 gate (opens Phase 5 for `main`-branch merges; lists acceptance criteria including this architecture amendment). **Gates Phase 5.** `docs/adr/0016-phase-5-gate.md`.

### Phase 5 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 23:** `RuntimeAdapter` protocol defined + `@runtime_checkable`; `ClaudeCodeRunner` satisfies the protocol (contract test via `isinstance(runner, RuntimeAdapter)`); `get_runtime_adapter()` factory returns correct adapter for "claude-code"; raises `ValueError` for unknown names; `WorkerSettings.runtime` field present with default "claude-code".
- **Epic 24:** `CodexRunner` satisfies `RuntimeAdapter` (contract test); P5-I1 credential isolation negative test (no cross-runtime key leakage); P5-I2 output-parsing contract test; P5-I3 kill-semantics contract test; `codex --json` output parsing produces `ExtractedEvent` list; exit-code mapping covers 0/1/2/130/137/-1; token usage extracted from `turn.completed` events.
- **Epic 25:** `task.runtime_handoff` event type registered in `domain/event_types.py`; `TaskExecutionStartedPayload.runtime` field present + registered at `1.2.0`; `metrics-subscriber` cardinality regression green for new `runtime` label; no new high-cardinality labels (bounded by closed enum); separability test green with `runtime="codex"` and `runtime=""`.

### Acceptance checklist (for ADR-0016 gate)

- [ ] Architecture amendment (this section) accepted; P5-I1 through P5-I3 invariants explicitly stated.
- [ ] ADR-0015 (`docs/adr/0015-multi-runtime-adapter.md`) authored and `status: accepted` -- formally defines the runtime adapter protocol.
- [ ] ADR-0016 (`docs/adr/0016-phase-5-gate.md`) authored and `status: accepted` -- formally opens Phase 5 for `main`-branch merges.
- [ ] `bmad-create-epics-and-stories` has decomposed the multi-runtime scope into Epic 23-25 stories.
- [ ] Each Phase 5 epic has its `phase: 5` label set in `sprint-status.yaml`.
- [ ] `deferred-work.md` reviewed; any items now superseded by Phase 5 marked `killed: superseded_by_phase_5_epic_<n>`.
- [ ] `codex` binary pinned in `Dockerfile.base` with verified checksum (same discipline as Playwright Docker image pinning per Phase 4).

-- *Amendment by R2d2, 2026-06-06, via the BMad `bmad-create-architecture` workflow (amendment mode).*
