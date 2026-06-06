---
id: ADR-0015
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0015: Multi-runtime adapter protocol — the RuntimeAdapter interface, factory, and credential isolation

## Status

**Accepted** — 2026-06-07. Gates **Epic 26** (runtime abstraction layer + Codex adapter + S-11). Extends the ADR-0010 MCP-server-authoring recipe with a 9th step for runtime adapter contracts. Mirrors the ADR-0010 lifecycle: must be `accepted` before Epic 26's first story merges to `main`.

## Context

Phase 4 is complete (ADR-0014 accepted 2026-06-05; all 14 ship-blockers green; 3,739+ tests passing; browser automation plane shipped across Epics 20–25). The Phase-4 retrospective identifies multi-runtime support as the next priority, and the operator converges on Codex CLI (`codex exec`) as the first second runtime.

The worker-wrapper currently hardcodes `ClaudeCodeRunner` in the task driver:

```python
# services/worker-wrapper/src/worker_wrapper/app/main.py (Phase 4)
runner = ClaudeCodeRunner(settings)
result = await runner.run(prompt, worktree_path)
```

This direct instantiation couples the task driver to a single CLI agent. Adding a second runtime (Codex) without abstraction would require either (a) if/else branching in the task driver for every runtime-specific code path, or (b) duplicating the entire task driver. Both options violate the Dependency Inversion principle and create a maintenance burden proportional to the number of runtimes.

The existing `ClaudeCodeRunner` already encodes a consistent subprocess-management shape — spawn via `asyncio.create_subprocess_exec`, JSON-lines stdout parsing, SIGTERM → SIGKILL escalation — but that shape lives as convention in a single class with no formal contract. Introducing a protocol before the first new runtime lands prevents divergence across adapters the same way ADR-0010 prevented divergence across MCP servers.

The reference implementations cited throughout:

- `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — subprocess spawn, `_CHILD_ENV_ALLOWLIST` + `_CHILD_ENV_PREFIXES` (G-SEC-2 D1), `_read_stream` JSON parsing, `_shutdown_process` SIGTERM → SIGKILL, `terminate_with_grace` → `TerminationResult`.
- `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` — `watch_for_budget_exceeded` terminates via an injected callback (runtime-agnostic by design).
- `packages/capabilities/src/capabilities/tiers.py` — `check_tier` / `check_tier_with_approval` (runtime-agnostic; gates actions, not subprocesses).

## Decision

Every runtime adapter added to worker-wrapper implements the six-decision contract documented below. The load-bearing decisions:

### D1: RuntimeAdapter protocol

A `typing.Protocol` with `@runtime_checkable` defines the adapter interface. Protocol, not ABC — the existing `ClaudeCodeRunner` satisfies the protocol via structural subtyping without inheriting from a base class, avoiding a breaking refactor.

```python
# services/worker-wrapper/src/worker_wrapper/domain/runtime_adapter.py

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol, runtime_checkable

from worker_wrapper.adapters.claude_code_runner import ExtractedEvent, TerminationResult


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Protocol for pluggable CLI runtime adapters (Phase 5 archetype).

    Each adapter manages the full lifecycle of one CLI agent subprocess:
    spawn, stream-read structured output, health-check, and terminate.
    The task driver (lifecycle.py:run_task) depends on this protocol,
    not on any concrete adapter class (Dependency Inversion).
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

        P5-I2: structured JSON deserialization only — no regex on raw text.
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

**ClaudeCodeRunner refactoring** is structural only — no behavioral change:

| Protocol method | Delegates to |
|---|---|
| `runtime_name` | Returns `"claude-code"` |
| `spawn()` | Wraps `_spawn()` |
| `is_healthy()` | Checks `self._process.returncode is None` |
| `parse_output()` | Delegates to `_handle_message()` (already JSON-only per `_SUPPORTED_OUTPUT_FORMATS`) |
| `kill()` | Wraps `terminate_with_grace()` |

### D2: Runtime factory function

A module-level factory resolves runtime name to concrete adapter. Lazy imports prevent loading `codex_runner.py` (and its binary dependency) on deployments using only Claude Code.

```python
# services/worker-wrapper/src/worker_wrapper/adapters/runtime_factory.py

from __future__ import annotations

from worker_wrapper.app.config import WorkerSettings
from worker_wrapper.domain.runtime_adapter import RuntimeAdapter


def get_runtime_adapter(
    settings: WorkerSettings,
    *,
    runtime: str | None = None,
) -> RuntimeAdapter:
    """Return the concrete RuntimeAdapter for the configured runtime.

    Selection priority:
    1. ``runtime`` override (per-task, from TaskCreatedPayload).
    2. ``settings.runtime`` (WorkerSettings field, defaults to "claude-code").
    3. Falls back to ClaudeCodeRunner if the field is blank/unrecognized
       (backward-compat: existing deployments without WORKER_RUNTIME set
       continue to use Claude Code without code changes).

    Raises ValueError for unrecognized runtime names (fail-loud, not silent
    fallback — a misspelled runtime name is an operator error, not a silent
    degradation).
    """
    name = runtime or settings.runtime or "claude-code"
    if name == "claude-code":
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner
        return ClaudeCodeRunner(settings)
    if name == "codex":
        from worker_wrapper.adapters.codex_runner import CodexRunner
        return CodexRunner(settings)
    raise ValueError(
        f"Unknown runtime: {name!r}. "
        f"Supported: 'claude-code', 'codex'"
    )
```

**Task driver wiring** — single-line change in `main.py`:

```python
# Before (Phase 4):
runner = ClaudeCodeRunner(settings)

# After (Phase 5):
adapter = get_runtime_adapter(settings, runtime=task_runtime_override)
```

### D3: Credential isolation architecture (P5-I1)

Each runtime adapter has its own explicit env-var allowlist. The existing `_CHILD_ENV_ALLOWLIST` in `claude_code_runner.py:77-97` is the model; Phase 5 extends it to a per-runtime allowlist system.

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

**Key property:** `ANTHROPIC_API_KEY` is absent from `_CODEX_ENV_ALLOWLIST` and its prefixes. `OPENAI_API_KEY` is absent from `_CHILD_ENV_ALLOWLIST` and its prefixes. Neither API key is in the shared functional vars. Each adapter injects its own API key in `_spawn()` from settings:

- `ClaudeCodeRunner._spawn()`: `env["ANTHROPIC_API_KEY"] = self._settings.anthropic_api_key`
- `CodexRunner._spawn()`: `env["OPENAI_API_KEY"] = self._settings.openai_api_key`

The CI gate is a negative test: set both keys in the parent env, spawn each runner, inspect the child env, and assert the other runtime's key is absent.

### D4: Structured output only (P5-I2)

Every adapter declares its output format (`stream-json` for Claude Code, `--json` JSONL for Codex). The `parse_output(raw)` method deserializes structured JSON only. Regex-based text parsing of subprocess stdout is forbidden — this is the existing NFR-O1 invariant extended to the adapter surface.

The CI gate asserts that no adapter module contains `re.search` or `re.match` calls on subprocess output. (The existing `_TEST_PATTERN` / `_COMMIT_PATTERN` / `_GIT_PUSH_PATTERN` in `claude_code_runner.py` apply to tool-use input classification, not to stdout line parsing — they remain valid.)

### D5: Budget enforcement via kill() (P5-I3)

`BudgetSupervisor.watch_for_budget_exceeded` calls an injected `terminate_callback`. The task driver wires this to the active adapter's `kill()` method. Every runtime adapter MUST implement `kill()` with SIGTERM → grace period → SIGKILL escalation, matching `ClaudeCodeRunner.terminate_with_grace` semantics exactly.

The CI gate is a contract test asserting every registered `RuntimeAdapter` subclass:
1. Implements `kill()` returning `TerminationResult`.
2. The terminate-with-grace path produces `method` in `{"noop", "sigterm", "sigkill"}`.

### D6: ADR-0010 recipe extension — step 9

The MCP-server-authoring recipe (ADR-0010) is extended with a new step for the runtime adapter contract. Applies when a new CLI runtime is added to the multi-runtime worker:

1. **Create adapter module.** `adapters/<runtime>_runner.py` implementing the `RuntimeAdapter` protocol.
2. **Define env allowlist.** A `_RUNTIME_ENV_ALLOWLIST` frozenset and `_RUNTIME_ENV_PREFIXES` tuple. The allowlist MUST NOT include any other runtime's API key (P5-I1). The contract test asserts this.
3. **Register in factory.** Add the new runtime name to `get_runtime_adapter()` with a lazy import.
4. **Add to `runtime` documentation.** The `WorkerSettings.runtime` field docstring and the factory's `ValueError` message list all supported runtimes.
5. **Output parsing contract.** `parse_output()` MUST use structured JSON deserialization only (P5-I2). The contract test asserts no regex on subprocess output.
6. **Kill contract.** `kill()` MUST implement SIGTERM → grace → SIGKILL with the same semantics as `ClaudeCodeRunner.terminate_with_grace` (P5-I3). The contract test asserts `TerminationResult`-compatible return.
7. **Event registration.** Register runtime-specific event payloads in `domain/event_types.py` at the next additive schema version.
8. **Budget integration.** Token usage extracted from the runtime's structured output and normalized into the platform's `token_usage` field.
9. **Separability entry.** Add a `WORKER_<RUNTIME>_COMMAND` toggle (separability pattern) and a `tests/separability/` test proving the member is optional.

## Consequences

- **Epic 26 is the reference implementation.** The `RuntimeAdapter` protocol, factory, and `CodexRunner` are built in Epic 26. Subsequent runtime additions (Gemini, GLM — Phase 6+) follow the step-9 recipe without protocol changes.
- **Zero behavioral change to the Claude Code runner path.** `ClaudeCodeRunner` is refactored to satisfy the protocol structurally; its `run()` convenience method is retained. The existing test suite passes without modification.
- **The `BudgetSupervisor` is unchanged.** It already calls an injected `terminate_callback`; the task driver wires it to `adapter.kill()`. Runtime-agnostic by design.
- **The tier-enforced authz gate is runtime-agnostic.** The `needs_approval` classification inspects `ExtractedEvent.event_type`, not which subprocess produced it. Both runtimes route destructive operations through the same approval flow.
- **Code duplication across adapters is accepted, by constraint.** The same trade-off as ADR-0010: the import-graph constraint (Story 5.8) means adapters duplicate allowlist construction and subprocess-management boilerplate. Contract tests enforce behavioral identity.
- **The `WorkerSettings.runtime` field defaults to `"claude-code"`.** Existing deployments without `WORKER_RUNTIME` set continue using Claude Code without code changes — backward-compatibility by default.

## Alternatives considered

- **ABC base class with inheritance.** Rejected — `ClaudeCodeRunner` has 10 months of accumulated production behavior; forcing it to inherit from a new base class risks behavioral regressions for zero gain. Structural subtyping via `Protocol` achieves the same contract without touching the class hierarchy.
- **Runtime-router microservice.** Rejected — a settings field + dispatch table is the lightest viable abstraction for 2 runtimes. A microservice adds deployment surface, network hops, and supply-chain attestations (YAGNI). If/when the platform reaches 5+ runtimes, extracting a router is a legitimate Phase-6 tech-debt item.
- **Ambient runtime selection via environment variable only (no protocol).** Rejected — an env-var toggle without a protocol contract means every runtime adapter is free to diverge on subprocess management, output parsing, and kill semantics. The protocol prevents this the same way ADR-0010's 8-step recipe prevents MCP-server divergence.
- **Regex-based output parsing for Codex fallback mode.** Rejected (P5-I2) — Codex's `--json` flag provides structured JSONL output. Supporting a non-JSON text mode would violate the NFR-O1 no-stdout-parsing invariant and create a fragile parsing surface. The `--json` flag is mandatory.

## ATDD contracts

| Contract | What it asserts | Test location |
|---|---|---|
| **Credential isolation** | `OPENAI_API_KEY` absent from Claude's child env; `ANTHROPIC_API_KEY` absent from Codex's child env. Both keys set in parent env. | `tests/integration/test_runtime_credential_isolation.py` |
| **Output parsing** | `parse_output()` returns `list[ExtractedEvent]` for valid JSON; returns `[]` for non-event lines. No `re.search`/`re.match` on subprocess output in adapter modules. | `tests/contract/test_runtime_adapter_output.py` |
| **Kill semantics** | `kill()` returns `TerminationResult` with `method` in `{"noop", "sigterm", "sigkill"}`. SIGTERM → grace → SIGKILL escalation path exercised. | `tests/contract/test_runtime_adapter_kill.py` |
| **Health check** | `is_healthy()` returns `True` while subprocess is alive; returns `False` after `kill()` completes. | `tests/contract/test_runtime_adapter_health.py` |
| **Runtime name** | `runtime_name` returns a string in the closed set `{"claude-code", "codex"}`. | `tests/contract/test_runtime_adapter_names.py` |
| **Factory completeness** | `get_runtime_adapter()` returns correct adapter type for each registered name; raises `ValueError` for unknown names; lazy import does not fail when unused adapter binary is absent. | `tests/contract/test_runtime_factory.py` |
| **Protocol conformance** | `isinstance(ClaudeCodeRunner(...), RuntimeAdapter)` is `True`; `isinstance(CodexRunner(...), RuntimeAdapter)` is `True`. | `tests/contract/test_runtime_adapter_conformance.py` |
| **Separability S-11** | With `WORKER_CODEX_COMMAND` unset: Codex not available, health check returns `installed=False`, tasks requesting Codex fall back. With it set: Codex available. Zero changes to other services. | `tests/separability/test_s11_codex.py` |

## Linked artifacts

- [`phase-5-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-prd-amendment.md) — FR89–FR98 + P5-I1/I2/I3 invariants + Phase 5 ship-blocker checklist.
- [`phase-5-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-architecture-amendment.md) — RuntimeAdapter protocol definition, factory function, credential isolation architecture, budget tracking per-runtime, handoff flow, ADR-0010 step-9 extension.
- ADR-0010 — MCP-server-authoring recipe (extended by step 9).
- ADR-0014 — Phase 4 gate (prerequisite: Phase 4 complete before Phase 5 opens).
- Reference code: `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py`, `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py`, `packages/capabilities/src/capabilities/tiers.py`.

— *R2d2, 2026-06-07 (accepted; via the BMad Phase-5 planning chain).*
