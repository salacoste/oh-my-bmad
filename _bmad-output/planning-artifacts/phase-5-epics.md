---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: 'complete'
finalStoryCount: 18
finalEpicCount: 4
inputDocuments:
  - _bmad-output/planning-artifacts/phase-5-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-5-architecture-amendment.md
  - docs/adr/0015-multi-runtime-adapter.md
  - docs/adr/0016-phase-5-gate.md
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-06-07'
---

# oh-my-bmad — Phase 5 Epic Breakdown: Multi-Runtime Plane

## Overview

This document provides the epic and story decomposition for **Phase 5** of oh-my-bmad — adding multi-runtime support via a runtime-abstraction layer in worker-wrapper and Codex CLI as the first second runtime. Phase 5 comprises 4 epics (26–29) and 18 stories, decomposing the Phase-5 PRD amendment (FR89–FR98, NFR-R10, NFR-O13, NFR-M10, NFR-S14) and the Phase-5 architecture amendment (Multi-Runtime Worker archetype, P5-I1/I2/I3 invariants, ADR-0015 adapter protocol).

This document **continues** the existing `epics.md` (Epic 1–22, Phases 1–4) and does not replace it.

## Requirements Inventory

### Functional Requirements

**Runtime Abstraction Layer (FR89):**
- **FR89.** Worker-wrapper introduces a runtime abstraction: `WorkerSettings` gains a `runtime` field (`Literal["claude-code", "codex"]`, default `"claude-code"`). The worker-wrapper `run_task` path selects the appropriate runner via a dispatch table mapping runtime name to runner class. The existing `ClaudeCodeRunner` path is unchanged; the dispatch falls through to it when `runtime == "claude-code"`. A `RuntimeAdapter` protocol defines the runner interface: `spawn()`, `is_healthy()`, `parse_output()`, `kill()`. The protocol ensures every runtime adapter satisfies the same contract the lifecycle manager depends on.

**Codex CLI Adapter (FR90):**
- **FR90.** Platform ships `codex_runner.py` (package `worker_wrapper.adapters`) — a parallel adapter to `claude_code_runner.py` that spawns `codex exec` with `--json` flag, reads JSONL from stdout, and extracts typed events. The adapter spawns via `asyncio.create_subprocess_exec`, uses `--sandbox` flag for OS-level sandboxing, passes `OPENAI_API_KEY` via explicit env injection (P5-I1), parses JSONL event stream extracting tool_use events and token usage, maps Codex tool names to `ExtractedEvent` types, and supports `--max-turns`.

**Per-Task Runtime Selection (FR91):**
- **FR91.** `TaskCreatedPayload` gains an optional `preferred_runtime` field (`Literal["claude-code", "codex"] | None`, default `None`). When `None`, the orchestrator uses the worker's default runtime. When set, the worker selects the corresponding runner. The field is advisory — if the requested runtime's binary is not installed (FR95 health check fails), the worker falls back to the default runtime and emits a `task.runtime_fallback` event.

**Runtime Handoff (FR92):**
- **FR92.** Platform exposes a `/handoff` command (Telegram + console) that transfers an in-progress task from one runtime to another. The handoff captures current task state, terminates the current runtime subprocess, spawns the target runtime with a resumption prompt, emits `task.runtime_handoff` event, and preserves the same trace_id across the entire lifecycle (P5-I2). Tier-2 operation requiring approval when the target runtime differs from the current.

**Cross-Runtime Session Continuity (FR93):**
- **FR93.** Session events include a `runtime` field identifying which runtime produced the event. The event spine carries this field additively — existing events without the field are interpreted as `"claude-code"` (backward-compatible default). `task.runtime_handoff` event type registered in `domain/event_types.py`. Trace_id spans the entire task lifecycle regardless of how many runtime handoffs occur.

**Per-Runtime Budget Tracking (FR94):**
- **FR94.** Budget supervisor tracks token consumption per-runtime within a single task. The budget limit is runtime-agnostic (the same token budget applies regardless of runtime), but the accounting is segmented: `tokens_consumed_by_runtime` map tracks how many tokens each runtime consumed. When a handoff occurs, the cumulative budget is checked — if exceeded, the handoff is rejected and the task is terminated.

**Runtime Health Probes (FR95):**
- **FR95.** Each runtime adapter exposes a health check: binary installed check (`shutil.which`), API key validity check (lazy, cached 60s), and version check (`runtime_binary --version`). Health check results emitted as `runtime.health_checked` events. Failed health checks prevent the runtime from being selected (FR91 fallback logic).

**Fleet-Level Integration Test (FR96):**
- **FR96.** Platform ships an end-to-end integration test exercising the full MCP fleet with Codex runtime: create task via task-registry, run on Codex, Codex performs file edit via `git-mcp`, runs tests via `verification-mcp`, commits, asserts all events emitted in correct order with same `trace_id`, and budget accounting includes Codex token consumption.

**Runtime Events (FR97):**
- **FR97.** Multi-runtime event types registered in `registry-state` `domain/event_types.py`: `task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked`. All payloads are metadata-only — no prompt text, no API response bodies, no secrets. Events carry `trace_id` per NFR-O7. Cardinality baseline updated; cardinality ratchet test green.

**Separability S-11 (FR98):**
- **FR98.** Codex runtime capability is conditionally available via `WORKER_CODEX_COMMAND` environment variable (separability S-11). Absent the variable, no Codex capability — `CodexRunner.health_check()` returns `installed=False`, tasks requesting Codex fall back to Claude Code. Present the variable, and `CodexRunner` is available for dispatch. Mirrors the Phase-3/4 separability pattern.

**Total: 10 FRs (FR89–FR98).**

### Non-Functional Requirements

**Runtime Isolation (NFR-R10):**
- **NFR-R10.** Runtime credential isolation — env vars for one runtime never leak to another. `ClaudeCodeRunner` child env contains `ANTHROPIC_API_KEY` but NOT `OPENAI_API_KEY`. `CodexRunner` child env contains `OPENAI_API_KEY` but NOT `ANTHROPIC_API_KEY`. Both use explicit, minimal allowlists. Verified by integration test inspecting each runner's child env (P5-I1).

**Per-Runtime Metrics (NFR-O13):**
- **NFR-O13.** Per-runtime metrics — `events_appended_total` includes a `runtime` label (additive). `metrics-subscriber` derives runtime-segmented counters from the event spine. Cardinality of the `runtime` label is bounded by the runtime registry (currently 2 values). Verified by metrics test asserting the `runtime` label appears on task events and the cardinality ratchet test passes.

**Codex Separability (NFR-M10):**
- **NFR-M10.** Codex runtime separability (S-11) — the Codex runner is an optional, swappable stdio member. Disabling it is a single change to `WORKER_CODEX_COMMAND` spawn configuration with no source-code modification to any other service. Verified by separability test S-11.

**Credential Separation (NFR-S14):**
- **NFR-S14.** Credential separation — `OPENAI_API_KEY` never reaches the Claude Code subprocess and `ANTHROPIC_API_KEY` never reaches the Codex subprocess. Each runtime's API key is injected from its own `WorkerSettings` field. Verified by integration test spawning each runner with a canary API key and asserting the canary does NOT appear in the other runner's child env.

**Total: 4 NFRs.**

### Additional Requirements

**Architecture Requirements (from Phase-5 architecture amendment + ADR-0015):**
1. `RuntimeAdapter` protocol — `typing.Protocol` with `@runtime_checkable`; methods: `runtime_name`, `spawn()`, `is_healthy()`, `parse_output()`, `kill()`.
2. Factory function — `get_runtime_adapter(settings, *, runtime=None)` with lazy imports, fail-loud on unknown names.
3. 5th archetype: Multi-Runtime Worker — wraps CLI subprocess via async pipes, runtime selection configurable, output parsing adapter-specific, credential injection adapter-specific.
4. Per-runtime env-var allowlists — `_CHILD_ENV_ALLOWLIST` for Claude, `_CODEX_ENV_ALLOWLIST` for Codex; shared functional vars only; API keys injected in `_spawn()` from settings.
5. P5-I1 credential isolation — `ANTHROPIC_API_KEY` in Claude only, `OPENAI_API_KEY` in Codex only.
6. P5-I2 structured output only — `parse_output()` uses structured JSON deserialization only; no regex on subprocess output.
7. P5-I3 budget enforcement via `kill()` — every adapter implements SIGTERM -> grace -> SIGKILL returning `TerminationResult`.
8. ADR-0010 recipe extension — step 9: runtime adapter contract (adapter module, env allowlist, factory registration, output parsing, kill semantics, event registration, budget integration, separability entry).
9. `task.execution.started` gains `runtime` field at schema version `1.2.0`.
10. `task.runtime_handoff` event registered at `1.1.0`.
11. `task.runtime_fallback` event registered at `1.1.0`.
12. `runtime.health_checked` event registered at `1.1.0`.
13. `WorkerSettings.runtime` field with default `"claude-code"` for backward compat.
14. `WorkerSettings` gains `codex_command`, `codex_timeout_s`, `openai_api_key` fields.

**Preserved invariants (Phase 1 + Phase 2 + Phase 3 + Phase 4 carry forward):**
- FR26 single-writer — runtime adapters route spine mutations through `clawhip-bridge`'s `EventLogWriter.append`.
- MCP transport stdio-only — runtime adapters spawn CLI binaries as stdio subprocesses.
- Event-only telemetry — runtime adapters emit typed events only; no parallel instrumentation.
- `trace_id` propagation — every runtime adapter stamps/propagates `trace_id` on every event.
- Tier-enforced authz — both runtime adapters route destructive operations through the existing approval flow.
- Supply-chain — Codex binary is a pinned dependency in the base image; child-env allowlist discipline extended.
- Budget supervision — `BudgetSupervisor` is runtime-agnostic; calls injected `terminate_callback`.

**New invariants (P5-I1 through P5-I3):**
- **P5-I1:** Runtime credential isolation — each runtime's API key is in its own env var, never in the other's allowlist.
- **P5-I2:** Trace_id continuity across handoffs — same trace_id spans the entire task lifecycle regardless of runtime changes.
- **P5-I3:** Budget accounting per-runtime — token consumption tracked separately per runtime; handoff rejected on budget breach.

**Gating ADRs:**
- **ADR-0015** — Multi-runtime adapter protocol (gates Epic 26).
- **ADR-0016** — Phase 5 gate (gates Phase 5 `main`-branch merges).

### FR Coverage Map

| FR | Epic | Stories | Note |
|---|---|---|---|
| FR89 | E26 | 26.1, 26.2, 26.3 | Protocol + factory + ClaudeCodeRunner refactoring |
| FR90 | E26 | 26.4 | CodexRunner adapter |
| FR91 | E27 | 27.1, 27.2 | Per-task runtime selection + fallback |
| FR92 | E28 | 28.1, 28.2, 28.4 | Handoff command + execution + event |
| FR93 | E28 | 28.3, 28.4 | Session continuity + runtime field on events |
| FR94 | E29 | 29.1, 29.2 | Per-runtime budget tracking + handoff rejection |
| FR95 | E26 | 26.6 | Runtime health probes |
| FR96 | E29 | 29.3 | Fleet smoke test |
| FR97 | E27 | 27.3 | Runtime events registration |
| FR98 | E26 | 26.7 | Separability S-11 |

**100% FR coverage confirmed — 10 FRs mapped across 4 epics, zero orphans.**

### NFR Coverage Summary

- **E26:** NFR-R10, NFR-M10, NFR-S14
- **E27:** NFR-O13 (cardinality update)
- **E28:** (cross-cutting — P5-I2 verification)
- **E29:** NFR-R10, NFR-S14, NFR-O13

**4 NFRs covered across 4 epics; zero orphans.**

## Epic List

Dependency graph:

```
E26 (runtime abstraction + Codex adapter + S-11)
  │
  ├──→ E27 (per-task runtime selection + events)
  │       │
  │       └──→ E28 (runtime handoff + session continuity)
  │
  └──→ E29 (budget tracking + fleet integration)
```

**Each epic is standalone-valued:**
- E26 delivers a runtime-abstracted worker-wrapper with a `RuntimeAdapter` protocol, `ClaudeCodeRunner` satisfying the protocol structurally, and a `CodexRunner` adapter that spawns Codex CLI — the abstraction layer is complete and S-11 separability is proved.
- E27 delivers per-task runtime selection via `TaskCreatedPayload.preferred_runtime` with fallback logic and all three new runtime event types registered.
- E28 delivers the `/handoff` command, cross-runtime session continuity, and trace_id preservation across handoffs.
- E29 delivers per-runtime budget accounting, budget-aware handoff rejection, per-runtime metrics labels, and the fleet-level smoke test.

**Epic sequencing rationale:**
- E26 must land first — the runtime abstraction must land before every later feature is born under it.
- E27 depends on E26's abstraction; the task-driver dispatch needs the protocol and factory.
- E28 depends on E27's per-task selection; handoff requires the dispatch wiring to be in place.
- E29 can partially parallelize with E28 (budget tracking is independent of handoff), but the fleet smoke test requires the Codex runner from E26 and the event registration from E27.

---

## Epic 26: Runtime Abstraction Layer (alpha)

**Goal.** Worker-wrapper supports pluggable runtime adapters via a `RuntimeAdapter` protocol with `@runtime_checkable`; `ClaudeCodeRunner` satisfies the protocol via structural subtyping (no behavioral change); `CodexRunner` adapter spawns `codex exec --json`, parses JSONL output, extracts typed events, and enforces per-runtime credential isolation (P5-I1). The factory function `get_runtime_adapter()` resolves runtime names to concrete adapters with lazy imports and fail-loud semantics. Runtime health probes allow pre-flight checks. Separability S-11 proves Codex is fully optional.

**FRs covered:** FR89, FR90, FR95, FR98
**NFRs:** NFR-R10, NFR-M10, NFR-S14

### Story 26.1: ATDD — red-phase contracts for RuntimeAdapter protocol

As the Phase-5 platform operator,
I want contract tests that define the expected behavior of the `RuntimeAdapter` protocol before any implementation,
so that the protocol's behavioral contract is test-verified and every subsequent adapter can be validated against it.

**Acceptance Criteria:**

**Given** `tests/contract/test_runtime_adapter_conformance.py` exists
**When** the contract tests run
**Then** they assert: (1) `isinstance(ClaudeCodeRunner(...), RuntimeAdapter)` is `True` (structural subtyping); (2) `isinstance(CodexRunner(...), RuntimeAdapter)` is `True`; (3) both adapters expose `runtime_name`, `spawn()`, `is_healthy()`, `parse_output()`, `kill()` methods.

**And Given** `tests/contract/test_runtime_adapter_names.py` exists
**When** the tests run
**Then** they assert `runtime_name` returns a string in the closed set `{"claude-code", "codex"}` for each concrete adapter.

**And Given** `tests/contract/test_runtime_adapter_output.py` exists
**When** the tests run
**Then** they assert: (1) `parse_output()` returns `list[ExtractedEvent]` for valid JSON; (2) returns `[]` for non-event lines; (3) no `re.search`/`re.match` on subprocess output in adapter modules (P5-I2).

**And Given** `tests/contract/test_runtime_adapter_kill.py` exists
**When** the tests run
**Then** they assert: (1) `kill()` returns `TerminationResult`; (2) `method` field is in `{"noop", "sigterm", "sigkill"}`; (3) SIGTERM -> grace -> SIGKILL escalation path is exercised.

**And Given** `tests/contract/test_runtime_adapter_health.py` exists
**When** the tests run
**Then** they assert: (1) `is_healthy()` returns `True` while subprocess is alive; (2) returns `False` after `kill()` completes.

**And Given** `tests/contract/test_runtime_factory.py` exists
**When** the tests run
**Then** they assert: (1) `get_runtime_adapter()` returns correct adapter type for each registered name; (2) raises `ValueError` for unknown names; (3) lazy import does not fail when unused adapter binary is absent.

**And Given** `tests/integration/test_runtime_credential_isolation.py` exists
**When** the tests run
**Then** they assert: (1) `OPENAI_API_KEY` is absent from Claude's child env; (2) `ANTHROPIC_API_KEY` is absent from Codex's child env; (3) both keys set in parent env (P5-I1, NFR-S14, NFR-R10).

*Cites: FR89, P5-I1, P5-I2, P5-I3, NFR-R10, NFR-S14.*

### Story 26.2: RuntimeAdapter protocol + factory function (FR89)

As the Phase-5 platform operator,
I want a `RuntimeAdapter` protocol and a factory function that resolves runtime names to concrete adapters,
so that the task driver depends on the protocol (not on any concrete adapter class) and runtime selection is a single dispatch-table lookup.

**Acceptance Criteria:**

**Given** `services/worker-wrapper/src/worker_wrapper/domain/runtime_adapter.py` exists
**When** the module is imported
**Then** it defines a `RuntimeAdapter` protocol with `@runtime_checkable` and five members: `runtime_name` (property returning `str`), `spawn()`, `is_healthy()`, `parse_output()`, `kill()` — matching the ADR-0015 contract exactly.

**And Given** `services/worker-wrapper/src/worker_wrapper/adapters/runtime_factory.py` exists
**When** `get_runtime_adapter(settings, runtime="claude-code")` is called
**Then** it returns a `ClaudeCodeRunner` instance via lazy import.

**And Given** `get_runtime_adapter(settings, runtime="codex")` is called
**When** the call completes
**Then** it returns a `CodexRunner` instance via lazy import.

**And Given** `get_runtime_adapter(settings, runtime="unknown")` is called
**When** the call completes
**Then** it raises `ValueError` with a message listing supported runtimes — fail-loud, not silent fallback.

**And Given** `get_runtime_adapter(settings)` is called without runtime override
**When** `settings.runtime` is `"claude-code"`
**Then** it returns a `ClaudeCodeRunner` instance (backward compat default).

**And Given** all contract tests from Story 26.1
**When** run against the implemented protocol and factory
**Then** they pass.

*Cites: FR89, ADR-0015 D1, D2.*

### Story 26.3: Refactor ClaudeCodeRunner to satisfy protocol (structural only)

As the Phase-5 platform operator,
I want `ClaudeCodeRunner` refactored so it structurally satisfies the `RuntimeAdapter` protocol without any behavioral change,
so that the existing runner path is backward-compatible and the task driver can use it through the protocol interface.

**Acceptance Criteria:**

**Given** the refactored `ClaudeCodeRunner` in `adapters/claude_code_runner.py`
**When** inspected
**Then** it exposes: `runtime_name` property returning `"claude-code"`, `spawn()` wrapping `_spawn()`, `is_healthy()` checking `self._process.returncode is None`, `parse_output()` delegating to `_handle_message()`, `kill()` wrapping `terminate_with_grace()`.

**And Given** `isinstance(ClaudeCodeRunner(settings), RuntimeAdapter)` is evaluated
**When** the check runs
**Then** it returns `True` — structural subtyping via `@runtime_checkable` protocol.

**And Given** the existing test suite for `ClaudeCodeRunner`
**When** run
**Then** all tests pass with zero modifications — no behavioral change.

**And Given** the `run()` convenience method
**When** inspected
**Then** it is retained for backward compatibility; the task driver gains a new path that uses protocol methods directly.

*Cites: FR89, ADR-0015 D1.*

### Story 26.4: CodexRunner adapter — spawn + JSONL parsing + event extraction (FR90)

As the Phase-5 platform operator,
I want a `CodexRunner` adapter that spawns `codex exec --json`, parses JSONL output, and extracts typed events,
so that the worker-wrapper can run tasks via OpenAI's Codex CLI with the same lifecycle management as Claude Code.

**Acceptance Criteria:**

**Given** `services/worker-wrapper/src/worker_wrapper/adapters/codex_runner.py` exists
**When** `spawn()` is called with `task_id`, `prompt`, `env`, `cwd`
**Then** it spawns `codex exec --json "<prompt>"` with `cwd` set to the worktree; returns the process handle.

**And Given** the Codex subprocess produces JSONL output
**When** `parse_output()` processes a `turn.completed` line
**Then** it extracts tool_use events, token usage from `usage` fields, and maps Codex tool names to `ExtractedEvent` types where possible (`file.edited`, `test.run`, `commit.created`, `git.push`); unmapped tools captured as `runtime.tool_executed` with raw tool name.

**And Given** `_CODEX_ENV_ALLOWLIST` in `codex_runner.py`
**When** inspected
**Then** it is an explicit, minimal frozenset containing process-basics vars (`PATH`, `HOME`, `USER`, locale, TLS certs) and `CODEX_` prefix vars — does NOT include `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, or any non-Codex secret (P5-I1, NFR-S14).

**And Given** `OPENAI_API_KEY` injection
**When** `_spawn()` builds the child env
**Then** `OPENAI_API_KEY` is injected from `settings.openai_api_key`, not from parent `os.environ`.

**And Given** `kill()` is called
**When** the grace period elapses
**Then** it follows SIGTERM -> 5s grace -> SIGKILL escalation, returning `TerminationResult` with `method` in `{"noop", "sigterm", "sigkill"}` (P5-I3).

**And Given** Codex exit codes
**When** the subprocess exits
**Then** exit code 0 maps to `completed`, 1 to `failed`, 2 to `failed` (config error), 130 to `cancelled` (SIGTERM), 137 to `cancelled` (SIGKILL), -1 to `timeout`.

**And Given** the Codex subprocess is spawned
**When** the `--sandbox` flag is present
**Then** Codex's built-in OS-level sandbox (Seatbelt/Landlock) is active — the adapter does NOT pass `--no-sandbox`.

**And Given** `OPENAI_API_KEY` is not present in settings
**When** `spawn()` is called
**Then** the runner returns an error immediately, does not hang (negative test).

*Cites: FR90, P5-I1, P5-I2, P5-I3, NFR-R10, NFR-S14, ADR-0015 D3, D4, D5.*

### Story 26.5: Per-runtime credential isolation — allowlists + CI gate (P5-I1, NFR-S14, NFR-R10)

As the CI pipeline,
I want credential isolation verified between runtimes so that one runtime's API key never appears in another runtime's child environment,
so that a compromised Claude Code subprocess cannot access OpenAI billing and vice versa.

**Acceptance Criteria:**

**Given** `tests/integration/test_runtime_credential_isolation.py` exists
**When** the test runs with both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` set in the parent env
**Then** the test spawns each runner, inspects the child env, and asserts: (1) `ANTHROPIC_API_KEY` is absent from Codex's child env; (2) `OPENAI_API_KEY` is absent from Claude Code's child env.

**And Given** `_CHILD_ENV_ALLOWLIST` in `claude_code_runner.py` and `_CODEX_ENV_ALLOWLIST` in `codex_runner.py`
**When** compared
**Then** they share only process-basics vars (`PATH`, `HOME`, `USER`, locale, TLS certs); each runtime's API key prefix is exclusive to its own allowlist.

**And Given** a canary API key test
**When** a unique canary key is injected as `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` is also set
**Then** the canary `ANTHROPIC_API_KEY` does NOT appear in the Codex child env, and vice versa (NFR-S14).

**And When** the test is wired into CI
**Then** it runs as a PR-required check (CI-blocking ratchet, never lowered).

*Cites: P5-I1, NFR-S14, NFR-R10, ADR-0015 D3.*

### Story 26.6: Runtime health probes (FR95)

As the Phase-5 platform operator,
I want each runtime adapter to expose a health check that verifies binary availability, API key validity, and version,
so that the worker can fail fast when a runtime is not available and the fallback logic can route to an alternative.

**Acceptance Criteria:**

**Given** `ClaudeCodeRunner.health_check()` and `CodexRunner.health_check()` exist
**When** called
**Then** each returns `{installed: bool, api_key_valid: bool, version: str}`.

**And Given** `shutil.which(runtime_binary)` is used for the installed check
**When** the binary is not on PATH
**Then** `installed` is `False` and the check returns immediately (no API call).

**And Given** the API key validity check
**When** called
**Then** it attempts a minimal API call (e.g., models list) and verifies it does not return an auth error; cached for 60 seconds to avoid per-task latency.

**And Given** the version check
**When** `runtime_binary --version` is parsed
**Then** it verifies the version meets a minimum threshold.

**And Given** `runtime.health_checked` event
**When** a health check completes
**Then** the event is emitted with `{runtime, installed, api_key_valid, version, trace_id}` — metadata-only payload.

**And Given** a failed `installed` check
**When** the runtime is requested via FR91
**Then** the fallback logic routes to the default runtime and emits `task.runtime_fallback`.

*Cites: FR95, FR91.*

### Story 26.7: Separability S-11 + WorkerSettings.runtime field (FR98, NFR-M10)

As the Phase-5 platform operator,
I want Codex capability to be fully toggleable via `WORKER_CODEX_COMMAND` with no source-code changes to any other service,
so that I can opt in or out of Codex functionality without modifying the rest of the fleet (NFR-M10).

**Acceptance Criteria:**

**Given** `tests/separability/test_s11_codex.py` exists
**When** `WORKER_CODEX_COMMAND` is set and `WORKER_RUNTIME` is `"codex"`
**Then** `CodexRunner` is available for dispatch; health check returns `installed=True`; tasks requesting Codex use the Codex runner.

**And Given** `WORKER_CODEX_COMMAND` is blank (default)
**When** the worker boots
**Then** Codex is not available; `CodexRunner.health_check()` returns `installed=False`; tasks requesting Codex fall back to Claude Code with `task.runtime_fallback` event emitted.

**And Given** `WorkerSettings.runtime` field
**When** `WORKER_RUNTIME` is not set
**Then** the field defaults to `"claude-code"` — existing deployments continue using Claude Code without code changes (backward compat).

**And Given** `WorkerSettings` gains `codex_command`, `codex_timeout_s`, `openai_api_key` fields
**When** inspected
**Then** `codex_command` defaults to `"codex"`, `codex_timeout_s` defaults to `600.0`, `openai_api_key` defaults to `""` (latent scaffold — only consulted when `runtime="codex"`).

**And Given** the separability test
**When** run
**Then** both AVAILABLE and UNAVAILABLE states are green; no source-code modification to any other service required (NFR-M10).

*Cites: FR98, NFR-M10, ADR-0015 D2.*

### Epic 26 acceptance gate
- `RuntimeAdapter` protocol defined with `@runtime_checkable` in `domain/runtime_adapter.py`.
- `get_runtime_adapter()` factory returns correct adapter for each registered name; raises `ValueError` for unknown names; lazy imports.
- `ClaudeCodeRunner` structurally satisfies `RuntimeAdapter` — `isinstance(runner, RuntimeAdapter)` is `True`; existing test suite passes unchanged.
- `CodexRunner` adapter spawns `codex exec --json`, parses JSONL, extracts typed events, maps tool names to `ExtractedEvent`.
- Credential isolation verified: `ANTHROPIC_API_KEY` absent from Codex child env; `OPENAI_API_KEY` absent from Claude child env (P5-I1, NFR-S14, NFR-R10).
- Output parsing contract: no `re.search`/`re.match` on subprocess output in adapter modules (P5-I2).
- Kill contract: `kill()` returns `TerminationResult` with SIGTERM -> grace -> SIGKILL escalation (P5-I3).
- Health probes return `{installed, api_key_valid, version}`; results cached 60s; `runtime.health_checked` event emitted.
- `WorkerSettings.runtime` field present with default `"claude-code"`; `codex_command`, `codex_timeout_s`, `openai_api_key` fields present.
- Separability S-11 green — toggle `WORKER_CODEX_COMMAND` and assert available/unavailable states; zero changes to other services.
- All contract tests from Story 26.1 pass.
- ADR-0015 `accepted`.

---

## Epic 27: Per-Task Runtime Selection (alpha-3)

**Goal.** The orchestrator can select a runtime per-task via `TaskCreatedPayload.preferred_runtime`; the worker dispatches to the correct runner; fallback logic handles unavailable runtimes with `task.runtime_fallback` events; all three new runtime event types (`task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked`) are registered in `domain/event_types.py` with additive schema and updated cardinality baseline.

**FRs covered:** FR91, FR97
**NFRs:** NFR-O13 (cardinality update)

### Story 27.1: TaskCreatedPayload.preferred_runtime field + orchestrator routing (FR91)

As the Phase-5 platform operator,
I want `TaskCreatedPayload` to accept a `preferred_runtime` field so that the orchestrator can route tasks to a specific runtime,
so that heterogeneous workloads can use different runtimes for different tasks.

**Acceptance Criteria:**

**Given** `TaskCreatedPayload` in the events package
**When** the `preferred_runtime` field is added
**Then** it is typed as `Literal["claude-code", "codex"] | None` with default `None` (additive, backward-compatible).

**And Given** the orchestrator-adapter receives a task with `preferred_runtime="codex"`
**When** it dispatches the task to the worker
**Then** the worker calls `get_runtime_adapter(settings, runtime="codex")` and uses the returned `CodexRunner`.

**And Given** the orchestrator-adapter receives a task with `preferred_runtime=None`
**When** it dispatches the task
**Then** the worker uses `settings.runtime` default (`"claude-code"` unless overridden).

**And Given** `preferred_runtime="codex"` and Codex health check fails (`installed=False`)
**When** the worker attempts to use Codex
**Then** it falls back to the default runtime and emits `task.runtime_fallback` event with `{task_id, requested_runtime="codex", fallback_runtime="claude-code", trace_id, reason="health_check_failed"}`.

**And Given** existing tasks (no `preferred_runtime` field)
**When** processed
**Then** they use Claude Code runner unchanged — backward-compatibility gate.

*Cites: FR91.*

### Story 27.2: Runtime fallback logic + task.runtime_fallback event (FR91, FR97)

As the Phase-5 platform operator,
I want fallback logic that gracefully handles unavailable runtimes and emits a `task.runtime_fallback` event,
so that tasks never fail silently when a requested runtime is not available.

**Acceptance Criteria:**

**Given** the fallback logic in the task driver
**When** `preferred_runtime="codex"` and `CodexRunner.health_check()` returns `installed=False`
**Then** the task driver falls back to the default runtime (`settings.runtime`), emits `task.runtime_fallback` event, and proceeds with task execution.

**And Given** the `task.runtime_fallback` event payload
**When** emitted
**Then** it contains `{task_id, requested_runtime, fallback_runtime, trace_id, reason}` — metadata-only, no secrets, no prompt text.

**And Given** the default runtime is also unavailable
**When** fallback is attempted
**Then** the task fails with an error (no infinite fallback loops).

**And Given** `task.runtime_fallback` event registration
**When** `scripts/check_event_registry.py` runs
**Then** the event type is validated in the schema registry — exits 0.

*Cites: FR91, FR97.*

### Story 27.3: Runtime events registration in event_types.py (FR97, cardinality update)

As the platform event spine,
I want all three new runtime event types registered additively in `domain/event_types.py`,
so that runtime events are first-class citizens on the spine with bounded cardinality and schema validation.

**Acceptance Criteria:**

**Given** the three event types: `task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked`
**When** registered in `packages/events/payloads.py` (payload models) and `registry-state/domain/event_types.py` (`register()` calls)
**Then** `scripts/check_event_registry.py` validates all three type strings and exits 0.

**And Given** the event payloads
**When** inspected
**Then** all payloads are metadata-only — no prompt text, no API response bodies, no secrets. Every payload carries non-null `trace_id` (AST gate enforced).

**And Given** schema versioning
**When** `task.runtime_handoff` and `task.runtime_fallback` are registered
**Then** they are registered at schema version `1.1.0` (new Phase-5 event types, no predecessor).

**And Given** `task.execution.started` gains a `runtime` field
**When** registered
**Then** it is at schema version `1.2.0` (additive evolution per NFR-M3); field is `str | None` with default `None` for backward compat.

**And Given** the cardinality baseline
**When** captured for runtime events
**Then** cardinality is bounded by `task_id` and the closed `runtime` enum (`{"claude-code", "codex"}`); the cardinality ratchet test in `metrics-subscriber` is green for the new event family (NFR-O13).

**And Given** `metrics-subscriber`
**When** the cardinality-regression test runs with runtime events included
**Then** no unregistered runtime event type is emitted; no high-cardinality labels are introduced.

**And Given** the `validate_caller_trace_id`-required AST gate
**When** scanning every `EventEnvelope.create(...)` callsite for runtime events
**Then** every callsite passes a non-null `trace_id` — the gate exits 0.

*Cites: FR97, NFR-O13.*

### Epic 27 acceptance gate
- `TaskCreatedPayload.preferred_runtime` field present with default `None`; orchestrator passes it to worker on task execution.
- Worker selects correct runner based on `preferred_runtime`; falls back to default when `None`.
- Fallback logic: unavailable runtime triggers fallback to default + `task.runtime_fallback` event emission.
- `task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked` event types registered in `domain/event_types.py`.
- `task.execution.started` gains `runtime` field at schema `1.2.0`.
- Cardinality baseline updated; cardinality ratchet test green (NFR-O13).
- `check_event_registry.py` green for all three new event types.
- `check_trace_id_required.py` green for all runtime event emissions.
- Backward-compatibility: existing tasks (no `preferred_runtime`) use Claude Code runner unchanged.

---

## Epic 28: Runtime Handoff + Session Continuity (alpha-4, alpha-5)

**Goal.** The `/handoff` command transfers an in-progress task from one runtime to another: terminates the current runtime subprocess, spawns the target runtime with a resumption prompt, and emits `task.runtime_handoff` event. The same `trace_id` spans the entire task lifecycle across handoffs (P5-I2). Session events carry a `runtime` field for cross-runtime continuity. The handoff is Tier-2 (approval-gated when the target runtime differs).

**FRs covered:** FR92, FR93
**NFRs:** P5-I2 (trace_id continuity)

### Story 28.1: /handoff command surface — Telegram + console (FR92)

As the platform operator,
I want a `/handoff <task_id> <target_runtime>` command available in Telegram and console,
so that I can switch an in-progress task from one runtime to another mid-execution.

**Acceptance Criteria:**

**Given** the `/handoff` command is registered in the command handler
**When** the operator issues `/handoff t-7f2a codex` via Telegram or console
**Then** `registry-api` receives the command, validates the task exists and is in `running` state, and initiates the handoff flow.

**And Given** the task is in `awaiting_approval` state
**When** `/handoff` is attempted
**Then** the command is rejected — handoff during an approval gate is blocked.

**And Given** the target runtime is the same as the current runtime
**When** `/handoff` is attempted
**Then** the command is a no-op (returns confirmation that the task is already on the requested runtime).

**And Given** the target runtime differs from the current runtime
**When** `/handoff` is issued
**Then** the command is Tier-2 — it requires operator approval before execution.

**And Given** the handoff command surface
**When** integrated with the Telegram bot and console handler
**Then** the operator receives a confirmation message indicating the handoff was initiated, including `from_runtime` and `to_runtime`.

*Cites: FR92.*

### Story 28.2: Handoff execution — subprocess termination + resumption spawn (FR92, P5-I2)

As the worker-wrapper task driver,
I want the handoff to terminate the current runtime subprocess and spawn the target runtime with a resumption prompt,
so that the task continues seamlessly on the new runtime with context preserved.

**Acceptance Criteria:**

**Given** a task running on Claude Code
**When** a handoff to Codex is initiated
**Then** the task driver: (1) requests a context summary from the active `ClaudeCodeRunner`; (2) calls `kill()` on the active adapter (SIGTERM -> grace -> SIGKILL); (3) calls `get_runtime_adapter(settings, runtime="codex")`; (4) calls `spawn()` on the new adapter with the context summary included in the prompt.

**And Given** the context summary
**When** the target runtime receives it
**Then** the summary is best-effort — if the source runtime cannot produce one, the target runtime starts with only the task description from the original `TaskCreatedPayload`.

**And Given** the worktree lock
**When** the handoff occurs
**Then** the lock is preserved (not released and re-acquired) — no unlock/re-lock race condition.

**And Given** the trace_id
**When** the target runtime is spawned
**Then** it receives the same `OMB_TRACE_ID` env var and the same `trace_id` in its resumption prompt — the byte-identical `validate_caller_trace_id` contract spans both runtimes (P5-I2).

**And Given** the budget consumed so far
**When** the handoff is attempted
**Then** the cumulative budget is checked; if already exceeded, the handoff is rejected and the task is terminated (P5-I3).

**And Given** the `task.runtime_handoff` event
**When** the handoff completes
**Then** it is emitted with `{task_id, from_runtime, to_runtime, trace_id, reason}` — metadata-only payload.

*Cites: FR92, P5-I2, P5-I3.*

### Story 28.3: Cross-runtime session continuity — runtime field on events (FR93)

As the platform event spine,
I want session events to carry a `runtime` field identifying which runtime produced the event,
so that the event log provides cross-runtime continuity and consumers can distinguish events from different runtimes.

**Acceptance Criteria:**

**Given** `SessionStartedPayload` and `SessionHeartbeatPayload`
**When** the `runtime` field is added
**Then** it is typed as `str | None` with default `None` (additive, backward-compatible). The field is populated by the task driver with the active adapter's `runtime_name`.

**And Given** `TaskExecutionStartedPayload`
**When** the `runtime` field is present
**Then** it identifies the runtime that started execution for this segment. Registered at schema version `1.2.0`.

**And Given** events without the `runtime` field (existing events)
**When** consumed
**Then** they are interpreted as `runtime="claude-code"` (backward-compatible default per FR93).

**And Given** `task.completed` event
**When** emitted after a handoff
**Then** it includes the runtime field for the runtime that completed the task.

**And Given** the metrics-subscriber
**When** processing events with `runtime` field
**Then** it derives `omb_session_active{runtime="codex"}` counters, bounded by the closed enum of registered runtimes.

*Cites: FR93, NFR-O13.*

### Story 28.4: task.runtime_handoff event + trace_id continuity (FR92, FR93, P5-I2)

As the platform event spine,
I want a `task.runtime_handoff` event that links the two runtime segments under one trace,
so that the full task lifecycle can be reconstructed from the event log regardless of how many handoffs occurred.

**Acceptance Criteria:**

**Given** `task.runtime_handoff` event type is registered in `domain/event_types.py`
**When** a handoff occurs
**Then** the event is emitted with `{task_id, trace_id, source_runtime, target_runtime, source_session_id, target_session_id, context_summary}` — the `context_summary` is a brief summary from the source runtime, not full tool results.

**And Given** the trace_id
**When** pre-handoff and post-handoff events are compared
**Then** they carry the same `trace_id` — the handoff does NOT break trace continuity (P5-I2).

**And Given** a second handoff (Codex -> back to Claude Code)
**When** the second `task.runtime_handoff` event is emitted
**Then** the same `trace_id` continues to span the entire lifecycle — trace_id is invariant across any number of handoffs.

**And Given** the event registration
**When** `scripts/check_event_registry.py` runs
**Then** `task.runtime_handoff` is validated in the schema registry at version `1.1.0` — exits 0.

**And Given** the cardinality baseline
**When** updated for `task.runtime_handoff`
**Then** cardinality is bounded by `task_id` (not by runtime names or session IDs); the cardinality ratchet test is green.

*Cites: FR92, FR93, P5-I2.*

### Epic 28 acceptance gate
- `/handoff` command available in Telegram and console; rejects during approval gate; no-op for same-runtime.
- Handoff execution: terminates current runtime, spawns target runtime with context summary, preserves worktree lock.
- Same `trace_id` spans pre-handoff and post-handoff events (P5-I2 verified by integration test).
- `task.runtime_handoff` event emitted with `from_runtime`, `to_runtime`, `trace_id`.
- Session events carry `runtime` field; events without it interpreted as `"claude-code"`.
- `task.execution.started` carries `runtime` field at schema `1.2.0`.
- Budget cumulative across handoff; handoff rejected if budget exceeded (P5-I3).
- Integration test: start task on Claude Code; handoff to Codex; assert task continues with same `trace_id`.
- `check_event_registry.py` green for `task.runtime_handoff`.
- Cardinality ratchet test green for handoff events.

---

## Epic 29: Budget Tracking + Fleet Integration (alpha-6, alpha-8)

**Goal.** Budget supervisor tracks token consumption per-runtime within a single task, with cumulative enforcement and handoff rejection on breach. Per-runtime metrics labels are added with bounded cardinality. A fleet-level smoke test exercises Codex + git-mcp + verification-mcp + event spine end-to-end, verifying that all Phase-3 MCP servers compose correctly with the new Codex runtime.

**FRs covered:** FR94, FR96
**NFRs:** NFR-R10, NFR-S14, NFR-O13

### Story 29.1: Per-runtime budget tracking — tokens_consumed_by_runtime map (FR94)

As the Phase-5 platform operator,
I want budget tracking to segment token consumption per-runtime within a single task,
so that cost attribution and debugging can distinguish between Claude Code and Codex token usage.

**Acceptance Criteria:**

**Given** `BudgetSupervisor` tracks `tokens_consumed_by_runtime: dict[str, int]` per task
**When** tokens are consumed by the active runtime
**Then** the map is updated: `tokens_consumed_by_runtime["codex"] += turn_tokens` (or `"claude-code"`).

**And Given** the budget limit
**When** enforced
**Then** it applies to the cumulative total across all runtimes (`sum(tokens_consumed_by_runtime.values())`), not to any single runtime.

**And Given** `task.budget_exceeded` event
**When** emitted
**Then** it includes the `runtime` field for the runtime that triggered the breach.

**And Given** `task.completed` event
**When** emitted
**Then** it includes a per-runtime token breakdown in the `token_usage` field: `{"claude-code": 50000, "codex": 30000}` or equivalent.

**And Given** `BudgetSupervisor` is runtime-agnostic
**When** the active adapter changes (handoff)
**Then** budget accounting continues accumulating without reset — the `tokens_consumed_by_runtime` map persists across handoffs.

*Cites: FR94, P5-I3.*

### Story 29.2: Budget-aware handoff rejection on breach (FR94, P5-I3)

As the worker-wrapper task driver,
I want handoff to be rejected when the cumulative budget is already exceeded,
so that a task cannot exceed its limit on one runtime and continue fresh on another.

**Acceptance Criteria:**

**Given** a task whose cumulative token consumption exceeds its budget limit
**When** a handoff is requested
**Then** the handoff is rejected and the task is terminated (not handed off) — P5-I3 enforcement.

**And Given** a task whose cumulative token consumption is below the budget limit
**When** a handoff is requested
**Then** the handoff proceeds normally; budget accounting continues accumulating on the target runtime.

**And Given** `task.completed` event after budget-breach rejection
**When** emitted
**Then** it includes `reason="budget_exceeded"` and the per-runtime token breakdown showing which runtime(s) contributed to the breach.

**And Given** the handoff rejection integration test
**When** run
**Then** it asserts: (1) handoff is rejected on budget breach; (2) task is terminated; (3) `task.completed` event includes `reason="budget_exceeded"` with per-runtime breakdown.

*Cites: FR94, P5-I3.*

### Story 29.3: Fleet smoke test — Codex + git-mcp + verification-mcp end-to-end (FR96)

As the CI pipeline,
I want an end-to-end integration test that exercises the full MCP fleet with the Codex runtime,
so that I can verify all Phase-3 MCP servers compose correctly with the new runtime.

**Acceptance Criteria:**

**Given** `tests/integration/test_codex_fleet_smoke.py` exists
**When** the test runs
**Then** it: (1) creates a task via task-registry with `preferred_runtime="codex"`; (2) runs the task on Codex runtime; (3) Codex performs a file edit (triggers `file.edited` event); (4) Codex runs tests via `verification-mcp` (triggers `verification.completed` event); (5) Codex commits via `git-mcp` (triggers `git.committed` event); (6) task completes.

**And Given** the test assertions
**When** verified
**Then**: (1) all events emitted in correct order with same `trace_id`; (2) budget accounting includes Codex token consumption in `tokens_consumed_by_runtime`; (3) `task.completed` event includes per-runtime breakdown.

**And Given** CI environment requirements
**When** the test runs in CI
**Then** it requires Codex binary + `OPENAI_API_KEY` available in the CI environment; the test is skipped (not failed) when these prerequisites are absent.

**And Given** the fleet composition
**When** the test completes
**Then** it proves: task-registry -> Codex runner -> git-mcp -> verification-mcp -> event spine all compose correctly under the multi-runtime abstraction.

*Cites: FR96.*

### Story 29.4: Per-runtime metrics label (NFR-O13, cardinality regression)

As the `metrics-subscriber`,
I want a `runtime` label on task event metrics bounded by the closed enum of registered runtimes,
so that runtime-segmented counters are observable without high-cardinality label explosion.

**Acceptance Criteria:**

**Given** `events_appended_total` metric
**When** a runtime event is processed
**Then** it includes a `runtime` label with value in `{"claude-code", "codex"}` (bounded by the registered adapter set).

**And Given** existing events without the `runtime` label
**When** processed
**Then** they use `runtime="claude-code"` as the default (additive, backward-compatible).

**And Given** the cardinality ratchet test
**When** run with runtime events included
**Then** the `runtime` label cardinality is bounded by the closed enum (2 values); no unregistered runtime values appear; the ratchet test is green (NFR-O13).

**And Given** `metrics-subscriber` derives `omb_session_active{runtime=...}` counters
**When** inspected
**Then** the counters are segmented by runtime with bounded cardinality — no free-form labels.

**And Given** the cardinality-regression test
**When** run
**Then** it asserts no new high-cardinality labels are introduced beyond the closed `runtime` enum and `task_id`.

*Cites: NFR-O13.*

### Epic 29 acceptance gate
- `BudgetSupervisor` tracks `tokens_consumed_by_runtime` per task; budget limit enforced on cumulative total.
- `task.budget_exceeded` event includes `runtime` field; `task.completed` event includes per-runtime token breakdown.
- Handoff rejected when cumulative budget exceeded; task terminated, not handed off (P5-I3).
- Fleet smoke test green: Codex + git-mcp + verification-mcp + event spine end-to-end (FR96).
- `runtime` label on metrics bounded by closed enum `{"claude-code", "codex"}`; cardinality ratchet test green (NFR-O13).
- Integration test `test_codex_fleet_smoke.py` passes; skipped (not failed) when Codex binary/API key unavailable.

---

## Phase 5 Ship-Blocker Checklist

Phase 5 has not shipped until every item below is green.

### Architectural commitments (preserved invariants + P5-I1/I2/I3)
- [ ] **FR26 single-writer unchanged** — both runtime runners route spine mutations through `clawhip-bridge`'s `EventLogWriter.append`; neither is a second DB writer. (`scripts/check_single_writer.py` exit 0.)
- [ ] **MCP transport stdio-only (P2-I4)** — no `mcp.server.sse` / `streamable_http` in runtime adapter code. (`scripts/check_mcp_transport.py` exit 0.)
- [ ] **No instrumentation outside `metrics-subscriber` (P2-I3 / NFR-O1/O10)** — runtime adapters emit typed events only; metrics for runtime events are derived by `metrics-subscriber`.
- [ ] **Every runtime event carries `trace_id` (NFR-O7)** — `validate_caller_trace_id` byte-identical across all runtime event emissions; AST gate exits 0.
- [ ] **P3-I1 — every MCP tool declares a capability tier** — `scripts/check_tier_declarations.py` green; runtime adapters do not bypass tier checks.
- [ ] **P5-I1 — runtime credential isolation** — `ANTHROPIC_API_KEY` absent from Codex child env; `OPENAI_API_KEY` absent from Claude Code child env; explicit allowlists for both runners; negative test green.
- [ ] **P5-I2 — trace_id continuity across handoffs** — same `trace_id` spans pre-handoff and post-handoff events; handoff test asserts trace continuity.
- [ ] **P5-I3 — budget accounting per-runtime** — `tokens_consumed_by_runtime` map tracks per-runtime consumption; cumulative budget enforced; handoff rejected on breach.
- [ ] **Supply-chain** — Codex binary pinned in base image with verified checksum; `codex_runner.py` is stdlib-only Python; no new pip/npm packages.

### Per-epic gates
- [ ] **Epic 26** — `RuntimeAdapter` protocol + factory complete; `ClaudeCodeRunner` satisfies protocol structurally; `CodexRunner` spawns + parses JSONL + extracts events; credential isolation verified (P5-I1); output parsing contract (P5-I2); kill contract (P5-I3); health probes implemented; S-11 separability green; ADR-0015 accepted.
- [ ] **Epic 27** — `TaskCreatedPayload.preferred_runtime` field present; orchestrator routing wired; fallback logic + `task.runtime_fallback` event; all three runtime event types registered; cardinality baseline updated; `check_event_registry.py` green.
- [ ] **Epic 28** — `/handoff` command available in Telegram + console; handoff execution terminates + respawns with context; `task.runtime_handoff` event emitted; trace_id continuity verified (P5-I2); session events carry `runtime` field; handoff rejected on budget breach (P5-I3).
- [ ] **Epic 29** — Per-runtime budget tracking in `BudgetSupervisor`; handoff rejection on budget breach; fleet smoke test green (Codex + git-mcp + verification-mcp); per-runtime metrics label with bounded cardinality (NFR-O13).

### Phase 1 + Phase 2 + Phase 3 + Phase 4 invariants regression-free
- [ ] `tests/separability/` **S-1 through S-11** all green.
- [ ] `tests/crash-injection/` all green.
- [ ] `tests/idempotency/` all green.
- [ ] `tests/contract/` all green — `validate_caller_trace_id`-byte-identical + credential isolation tests for both runtimes.
- [ ] Arch gates (`check_{single_writer,imports,event_registry,mcp_transport,tier_declarations}.py` + `check_trace_id_required.py`) all exit 0.
- [ ] Replay / byte-for-byte equivalence holds after additive runtime event-type registration.

### New ADRs accepted
- [ ] **ADR-0015** — Multi-runtime adapter protocol (`status: accepted`). Gates Epic 26.
- [ ] **ADR-0016** — Phase 5 gate (`status: accepted`). Gates Phase 5 `main`-branch merges.

### Documentation
- [ ] `docs/operator-runbook.md` extended with: multi-runtime operator notes (runtime selection, handoff, credential isolation, Codex binary pinning, health probes).
- [ ] `_bmad-output/project-context.md` updated with Phase 5 additions: the Multi-Runtime Worker archetype, P5-I1/I2/I3 invariants, ADR-0010 recipe step 9.
- [ ] A retrospective lands at every Phase-5 epic boundary.

### Principle

If any item above is not green/complete, **Phase 5 has not shipped**. The three new invariants (P5-I1 credential isolation, P5-I2 trace_id continuity, P5-I3 budget per-runtime) plus the preserved Phase-1+2+3+4 spine (FR26 single-writer, stdio-only transport, event-only telemetry, `trace_id` correlation, tier-enforced authz, signed supply-chain) are the contract. Phase 5 adds a **runtime abstraction and a second adapter**, not a new trust boundary — any credential leak, trace discontinuity, budget reset, or unverified binary is a ship-blocker, not a feature.

---

*Decomposed by R2d2, 2026-06-07, via the BMad `bmad-create-epics-and-stories` workflow (Phase-5 extension mode).*
