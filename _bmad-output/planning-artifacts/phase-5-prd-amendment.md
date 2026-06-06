# Phase 5 Scope Extension — Multi-Runtime Support

> **Status:** Phase-5 PRD amendment. Formalizes the multi-runtime decision from the Phase-4 retrospective research and the operator's Phase-5 convergence. FR/NFR numbering continues the canonical series (FR89 → FR96; NFR-O12 → NFR-O13; NFR-M9 → M10; NFR-S13 → S14; NFR-R9 → R10). Epic numbering continues from Phase 4 (Epic 26 = Phase 5 start).
>
> **Selected via:** Phase-4 retrospective readiness assessment + operator convergence on the Codex CLI adapter decision. Core decision: add **Codex CLI** (`codex exec`) as the first second runtime behind a runtime-abstraction layer in worker-wrapper; the orchestrator selects the runtime per-task, and the same trace_id, worktree-lock, and budget-accounting infrastructure spans both runtimes. Gemini/GLM adapters are deferred (Phase 6+). Multi-task parallelism is deferred (Phase 6). Remote MCP transport stays deferred (Phase 6).

**Theme:** the **multi-runtime plane** — a runtime-abstraction layer in worker-wrapper that decouples the orchestrator from any single CLI agent, enabling per-task runtime selection, cross-runtime state continuity, and runtime-credential isolation. Built on the Phase-1–4 spine (event-only telemetry, `trace_id`, supply-chain pipeline, tier-enforced authz) with zero changes to the existing Claude Code runner path.

**Resolved scope (operator convergence, D1–D5):**

- **D1 (IN).** Runtime abstraction layer — `WorkerSettings` gains a `runtime` field (`"claude-code"` | `"codex"`); worker-wrapper selects the appropriate runner via a dispatch table. Existing Claude Code runner path is unchanged.
- **D2 (IN).** Codex CLI adapter — `codex_runner.py` (parallel to `claude_code_runner.py`) spawns `codex exec` with `--json` flag, parses JSONL event stream, extracts typed events. Parallel structure to the Claude Code runner.
- **D3 (IN).** Per-task runtime selection — `TaskCreatedPayload` gains `preferred_runtime` field; orchestrator-adapter routes to the correct runner. Default: `"claude-code"` (backward-compatible).
- **D4 (OUT, deferred).** Gemini/GLM adapters, remote MCP transport, multi-task parallelism. All deferred to Phase 6+.
- **D5 (entry point).** A **runtime-abstraction + separability warm-up epic FIRST** (FR89 + FR90 + S-11), then per-task selection, then handoff + cross-runtime continuity.

**Preserved invariants (carry from Phases 1–4 — non-negotiable):**

- **Single-writer (FR26) unchanged.** Both runtime runners are *producers* of events via the existing event spine; no second writer is introduced. Events from either runtime flow through `clawhip-bridge`'s `EventLogWriter.append`.
- **MCP transport remains stdio-only.** Runtime adapters spawn CLI binaries as stdio subprocesses; no HTTP/SSE/streamable transport is introduced. Remote-MCP stays deferred (Phase 6 D2).
- **Event-only telemetry (NFR-O1/O10) unchanged.** Both runtime runners emit typed events on the event spine; metrics remain *derived* in `metrics-subscriber`. No per-runtime instrumentation paths are added to any other service.
- **`trace_id` propagation (NFR-O7) unchanged.** Every runtime adapter stamps/propagates `trace_id` on every event it emits. Trace_id spans across runtime handoffs within a single task lifecycle (P5-I2).
- **Tier-enforced authz (Epic 6) unchanged.** Both runtime adapters route destructive operations through the existing approval flow. The approval gate is runtime-agnostic — it gates actions, not runtimes.
- **Supply-chain (Epic 8 + G-SEC-1/2) unchanged.** The Codex CLI binary is a runtime dependency (installed on the host or in the base image), not a Python dependency. No new pip/npm packages. The `codex_runner.py` adapter is stdlib-only Python (mirrors `claude_code_runner.py`).

---

## Phase 5 Functional Requirements

### α — Runtime abstraction layer (Epic 26)

- **FR89.** Worker-wrapper introduces a runtime abstraction: `WorkerSettings` gains a `runtime` field (`Literal["claude-code", "codex"]`, default `"claude-code"`). The worker-wrapper `run_task` path selects the appropriate runner via a dispatch table mapping runtime name to runner class. The existing `ClaudeCodeRunner` path is unchanged; the dispatch falls through to it when `runtime == "claude-code"`. A new `BaseRunner` protocol (or structural duck-typing) defines the runner interface: `run(prompt, worktree_path) -> RunnerResult`, `cancel() -> None`, `terminate_with_grace(grace_period_s) -> TerminationResult`. The protocol ensures every runtime adapter satisfies the same contract the lifecycle manager depends on.

  **Acceptance criteria:**
  - `WorkerSettings.runtime` field exists with default `"claude-code"`.
  - `run_task` dispatches to `ClaudeCodeRunner` when `runtime == "claude-code"` (zero behavioral change from Phase 4).
  - `run_task` dispatches to `CodexRunner` when `runtime == "codex"`.
  - Both runners satisfy the same structural contract (run/cancel/terminate_with_grace).
  - Budget supervisor works identically for both runtimes (uses the runner protocol, not the concrete class).
  - Existing test suite passes with zero modifications (backward-compatibility gate).

### α-2 — Codex CLI adapter (Epic 26)

- **FR90.** Platform ships `codex_runner.py` (package `worker_wrapper.adapters`) — a parallel adapter to `claude_code_runner.py` that spawns `codex exec` with `--json` flag, reads JSONL from stdout, and extracts typed events. The adapter:
  - Spawns via `asyncio.create_subprocess_exec` (same pattern as Claude Code runner).
  - Uses `--sandbox` flag for OS-level sandboxing (Seatbelt on macOS, Landlock on Linux — Codex's built-in sandbox, stronger than Claude Code's process model).
  - Passes `OPENAI_API_KEY` via explicit env injection (NOT from parent env — follows the `_CHILD_ENV_ALLOWLIST` discipline, P5-I1).
  - Parses JSONL event stream: extracts tool_use events, token usage per turn, session/resume metadata.
  - Maps Codex tool names to the same `ExtractedEvent` types where possible (`file.edited`, `test.run`, `commit.created`, `git.push`).
  - Tracks token consumption via Codex's per-turn `usage` fields for budget accounting.
  - Supports `--max-turns` equivalent via Codex's `--max-turns` flag.

  **Acceptance criteria:**
  - `codex_runner.py` exists in `worker_wrapper/adapters/`.
  - Spawns `codex exec` with correct flags; reads JSONL stdout; builds `CodexResult` (parallel to `ClaudeCodeResult`).
  - `_CODEX_CHILD_ENV_ALLOWLIST` is an explicit, minimal frozenset — does NOT include `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, or any non-Codex secret.
  - `OPENAI_API_KEY` injected from settings, not from parent env.
  - Event extraction maps Codex tool names to `ExtractedEvent` types.
  - Budget supervisor can terminate the Codex subprocess via `terminate_with_grace`.
  - Integration test: spawn Codex with a trivial prompt; assert structured result returned.
  - Negative test: `OPENAI_API_KEY` not present → runner returns error, does not hang.

### α-3 — Per-task runtime selection (Epic 27)

- **FR91.** `TaskCreatedPayload` gains an optional `preferred_runtime` field (`Literal["claude-code", "codex"] | None`, default `None`). When `None`, the orchestrator uses the worker's default runtime (from `WorkerSettings.runtime`). When set, the orchestrator passes the runtime to the worker via the task-execution protocol, and the worker selects the corresponding runner. The field is advisory — if the requested runtime's binary is not installed (FR95 health check fails), the orchestrator falls back to the default runtime and emits a `task.runtime_fallback` event.

  **Acceptance criteria:**
  - `TaskCreatedPayload` includes `preferred_runtime` field with default `None`.
  - Orchestrator passes `preferred_runtime` to worker on task execution.
  - Worker selects the correct runner based on the field.
  - When `preferred_runtime` is `None`, worker uses `WorkerSettings.runtime` default.
  - Runtime fallback: if requested runtime's health check fails, worker falls back to default and emits `task.runtime_fallback` event.
  - Backward-compatibility: existing tasks (no `preferred_runtime`) use Claude Code runner unchanged.

### α-4 — Runtime handoff (Epic 28)

- **FR92.** Platform exposes a `/handoff` command (Telegram + console) that transfers an in-progress task from one runtime to another. The handoff:
  1. Captures current task state: worktree contents, session events, trace_id, budget consumed so far.
  2. Terminates the current runtime subprocess (graceful SIGTERM → SIGKILL escalation).
  3. Spawns the target runtime with a resumption prompt that includes the task context.
  4. Emits `task.runtime_handoff` event with `from_runtime`, `to_runtime`, `trace_id`.
  5. The same trace_id spans the entire task lifecycle across the handoff (P5-I2).

  The handoff is **Tier-2** (it alters task execution state but does not perform external mutations). The operator must approve the handoff if the target runtime is different from the current one. Handoff to the same runtime is a no-op.

  **Acceptance criteria:**
  - `/handoff codex` command available in Telegram and console.
  - Handoff terminates current runtime, spawns target runtime with resumption context.
  - `task.runtime_handoff` event emitted with `from_runtime`, `to_runtime`, `trace_id`.
  - Same trace_id spans pre-handoff and post-handoff events (P5-I2).
  - Budget accounting is cumulative across the handoff (tokens consumed before handoff + tokens consumed after).
  - Worktree lock is preserved across the handoff (no unlock/re-lock race).
  - Integration test: start task on Claude Code; handoff to Codex; assert task continues with same trace_id.

### α-5 — Cross-runtime session continuity (Epic 28, shared)

- **FR93.** Session events include a `runtime` field identifying which runtime produced the event. The event spine carries this field additively — existing events without the field are interpreted as `"claude-code"` (backward-compatible default). The `task.runtime_handoff` event type is registered in `domain/event_types.py`. Trace_id spans the entire task lifecycle regardless of how many runtime handoffs occur.

  **Acceptance criteria:**
  - Session events (`session.started`, `session.finished`) include `runtime` field.
  - Task events (`task.execution.started`, `task.completed`) include `runtime` field.
  - `task.runtime_handoff` event type registered in `domain/event_types.py` at schema version `1.1.0`.
  - `task.runtime_fallback` event type registered in `domain/event_types.py` at schema version `1.1.0`.
  - Cardinality baseline updated; cardinality ratchet test green.
  - Events without `runtime` field are interpreted as `"claude-code"` (backward-compatible).

### α-6 — Runtime-specific budget tracking (Epic 29)

- **FR94.** Budget supervisor tracks token consumption per-runtime within a single task. The budget limit is runtime-agnostic (the same token budget applies regardless of runtime), but the accounting is segmented: `tokens_consumed_by_runtime` map tracks how many tokens each runtime consumed. This enables cost attribution and debugging. When a handoff occurs, the cumulative budget is checked against the task's limit — if the budget is already exceeded, the handoff is rejected (the task is terminated, not handed off).

  **Acceptance criteria:**
  - `BudgetSupervisor` tracks `tokens_consumed_by_runtime: dict[str, int]` per task.
  - Budget limit is enforced on the cumulative total across all runtimes.
  - Handoff is rejected if cumulative budget is exceeded (task terminated, not handed off).
  - `task.budget_exceeded` event includes `runtime` field for the runtime that triggered the breach.
  - `task.completed` event includes per-runtime token breakdown in `token_usage` field.

### α-7 — Runtime health probes (Epic 26, shared)

- **FR95.** Each runtime adapter exposes a synchronous health check:
  - **Binary installed check:** `shutil.which(runtime_binary)` returns a path.
  - **API key validity check:** attempt a minimal API call (e.g., models list) and verify it does not return an auth error. This check is lazy (called once on first use, cached) to avoid startup latency.
  - **Version check:** parse `runtime_binary --version` output and verify it meets a minimum version threshold.

  Health check results are emitted as `runtime.health_checked` events. Failed health checks prevent the runtime from being selected (FR91 fallback logic).

  **Acceptance criteria:**
  - `ClaudeCodeRunner.health_check()` returns `{installed: bool, api_key_valid: bool, version: str}`.
  - `CodexRunner.health_check()` returns the same shape.
  - Health checks are cached for 60 seconds (avoid per-task API calls).
  - Failed `installed` check returns immediately (no API call).
  - `runtime.health_checked` event emitted with `runtime`, `installed`, `api_key_valid`, `version`, `trace_id`.

### α-8 — Fleet-level integration test (Epic 29, shared)

- **FR96.** Platform ships an end-to-end integration test that exercises the full MCP fleet in a single workflow:
  1. Create a task via task-registry.
  2. Run the task on Codex runtime.
  3. Codex performs a file edit (triggers `file.edited` event).
  4. Codex runs tests via `verification-mcp` (triggers `verification.completed` event).
  5. Codex commits via `git-mcp` (triggers `git.committed` event).
  6. Task completes; assert all events emitted in correct order with same `trace_id`.
  7. Assert budget accounting includes Codex token consumption.

  This is the **fleet smoke test** — it verifies that all Phase-3 MCP servers compose correctly with the new Codex runtime.

  **Acceptance criteria:**
  - Integration test exists in `tests/integration/`.
  - Test exercises: task-registry → Codex runner → verification-mcp → git-mcp → event spine.
  - All events carry the same `trace_id`.
  - Budget accounting includes token breakdown.
  - Test passes in CI (Codex binary + API key must be available in CI environment).

### α-9 — Runtime events (cross-cutting, all Epics)

- **FR97.** Multi-runtime event types registered in `registry-state` `domain/event_types.py`. Event types include:
  - `task.runtime_handoff` — `{task_id, from_runtime, to_runtime, trace_id, reason}`
  - `task.runtime_fallback` — `{task_id, requested_runtime, fallback_runtime, trace_id, reason}`
  - `runtime.health_checked` — `{runtime, installed, api_key_valid, version, trace_id}`

  All payloads are **metadata-only** — no prompt text, no API response bodies, no secrets in events. Events carry `trace_id` per NFR-O7 and are derived from the event spine.

  **Acceptance criteria:**
  - All three event types registered in `domain/event_types.py` with additive schema.
  - Cardinality baseline updated; cardinality ratchet test green.
  - Zero secret strings in any event payload (verified by schema validation test).
  - Every event carries non-null `trace_id` (AST gate enforced).

### α-10 — Separability S-11 (Epic 26, cross-cutting)

- **FR98.** Codex runtime capability is conditionally available via `WORKER_CODEX_COMMAND` environment variable (separability S-11). Absent the variable, no Codex capability is available — `CodexRunner.health_check()` returns `installed=False`, the `"codex"` runtime is excluded from dispatch, and tasks requesting Codex runtime fall back to Claude Code. Present the variable, and `CodexRunner` is available for dispatch. This mirrors the Phase-3/4 separability pattern (NFR-M8/M9) with the same blank-command toggle.

  **Acceptance criteria:**
  - With `WORKER_CODEX_COMMAND` unset: Codex runtime not available; health check returns `installed=False`; tasks requesting Codex fall back to Claude Code; `task.runtime_fallback` event emitted.
  - With `WORKER_CODEX_COMMAND` set: Codex runtime available; health check passes; tasks requesting Codex use Codex runner.
  - Separability test S-11 in `tests/separability/`: toggle `WORKER_CODEX_COMMAND` and assert the above.
  - No source-code modification to any other service required to toggle Codex capability.

---

## Phase 5 Non-Functional Requirements

### Runtime isolation

- **NFR-R10.** Runtime credential isolation — env vars for one runtime never leak to another. The `ClaudeCodeRunner` child env contains `ANTHROPIC_API_KEY` but NOT `OPENAI_API_KEY`. The `CodexRunner` child env contains `OPENAI_API_KEY` but NOT `ANTHROPIC_API_KEY`. Both use explicit, minimal allowlists (the `_CHILD_ENV_ALLOWLIST` discipline from G-SEC-2 D1). Verified by an integration test that inspects the child env of each runner and asserts the other runtime's API key is absent. This is the runtime-level sibling of the existing worker-wrapper credential isolation (P5-I1).

### Observability (extends section Observability)

- **NFR-O13.** Per-runtime metrics — `events_appended_total` includes a `runtime` label (additive — existing events without the label use `"claude-code"` default). The `metrics-subscriber` derives runtime-segmented counters from the event spine. No new instrumentation paths in any service. Cardinality of the `runtime` label is bounded by the runtime registry (currently 2 values: `"claude-code"`, `"codex"`). Verified by a metrics test that asserts the `runtime` label appears on task events and the cardinality ratchet test passes.

### Maintainability (extends section Maintainability)

- **NFR-M10.** Codex runtime separability (S-11) — the Codex runner is an **optional, swappable stdio member** — disabling it is a single change to the `WORKER_CODEX_COMMAND` spawn configuration, with **no source-code modification** to any other service. Verified by separability test **S-11** in `tests/separability/`, continuing the S-1...S-10 series. The Codex runner follows the same structural contract as the Claude Code runner (run/cancel/terminate_with_grace), so the budget supervisor, lifecycle manager, and approval waiter work identically for both.

### Security (extends section Security)

- **NFR-S14.** Credential separation — `OPENAI_API_KEY` never reaches the Claude Code subprocess and `ANTHROPIC_API_KEY` never reaches the Codex subprocess. Each runtime's API key is injected from its own settings field (`WorkerSettings.anthropic_api_key` / `WorkerSettings.openai_api_key`), not from the parent environment. The `_CHILD_ENV_ALLOWLIST` for each runner is an explicit frozenset that excludes the other runtime's key. Verified by an integration test that spawns each runner with a canary API key and asserts the canary does NOT appear in the other runner's child env.

---

## Phase 5 Invariants (delta from P4-I1..I3)

Phase 5 introduces **three** new discipline rules on top of the preserved set.

| # | Invariant | Why |
|---|---|---|
| **P5-I1** | **Runtime credential isolation** — each runtime's API key is in its own env var, never in the other's allowlist. `ANTHROPIC_API_KEY` is injected into the Claude Code subprocess env only; `OPENAI_API_KEY` is injected into the Codex subprocess env only. Both are sourced from `WorkerSettings` fields, not from the parent `os.environ`. The `codex_runner._CODEX_CHILD_ENV_ALLOWLIST` is a separate frozenset from `claude_code_runner._CHILD_ENV_ALLOWLIST` — they share the process-basics vars (`PATH`, `HOME`, `USER`, locale, TLS) but diverge on secrets. | The G-SEC-2 discipline (D1) established that the child env must be an explicit allowlist to prevent secret leakage. Multi-runtime amplifies the risk: a single leaked env var now exposes credentials for TWO cloud providers instead of one. Isolating credentials at the runner level ensures a compromised Claude Code subprocess cannot access OpenAI billing, and vice versa. |
| **P5-I2** | **Trace_id continuity across handoffs** — the same trace_id spans the entire task lifecycle regardless of runtime changes. When a task starts on Claude Code and is handed off to Codex, the Codex subprocess receives the same `OMB_TRACE_ID` env var and the same `trace_id` in its resumption prompt. All events — from both runtimes — carry the same `trace_id`. The `task.runtime_handoff` event links the two runtime segments under one trace. | Trace_id is the primary correlation key for the event spine (NFR-O7). Breaking trace_id at a handoff would make it impossible to reconstruct the full task lifecycle from the event log — the operator would see two unrelated tasks instead of one continuous task. The handoff is a runtime-implementation detail, not a semantic task boundary. |
| **P5-I3** | **Budget accounting per-runtime** — token consumption is tracked separately per runtime within a single task. The budget limit applies to the cumulative total (both runtimes combined), but the breakdown is visible in `task.completed` events. If a handoff is requested when the cumulative budget is already exceeded, the handoff is rejected and the task is terminated. | Different runtimes have different token costs (Codex vs Claude Code pricing models differ). Per-runtime accounting enables accurate cost attribution and debugging. Rejecting handoff on budget breach prevents a "budget reset" exploit where a task could exceed its limit on one runtime and continue fresh on another. |

---

## Phase 5 Decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Runtime dispatch via settings field, not service mesh.** `WorkerSettings.runtime` + per-task override, NOT a separate runtime-router microservice. | Adding a microservice for 2 runtimes is over-engineering. A settings field + dispatch table is the lightest viable abstraction. If/when we reach 5+ runtimes, we can extract a router then (YAGNI). |
| **D2** | **Codex `exec` mode, not interactive mode.** `codex exec "prompt"` (non-interactive, single-shot), NOT `codex` (interactive REPL). | Mirrors the `claude -p` pattern. The worker-wrapper manages the subprocess lifecycle; interactive mode would require PTY management and is not needed. `exec` mode gives us structured JSONL output and deterministic exit. |
| **D3** | **Same worktree, same event spine.** Both runtimes share the same worktree path and emit events on the same event spine. No per-runtime event log or worktree. | The worktree is the task's workspace (bound by FR27 worktree lock). The event spine is the single source of truth (FR26). Duplicating either per-runtime would violate existing invariants and complicate handoff. |
| **D4** | **Codex's built-in sandbox is the primary sandbox.** `codex exec --sandbox` provides OS-level sandboxing (Seatbelt/Landlock). No Docker wrapping for the Codex subprocess (unlike the browser server's Playwright-in-Docker). | Codex's sandbox is stronger than Claude Code's process model (OS-level vs process-level). Wrapping it in Docker would add latency without security benefit. The browser server needs Docker because Playwright runs Chromium (a full browser); Codex runs code in a sandboxed subprocess — different threat model. |
| **D5** | **Event mapping is best-effort, not structural.** Codex tool names are mapped to `ExtractedEvent` types where a clear mapping exists (`file.edited`, `test.run`, `commit.created`, `git.push`). Unmapped tools are captured as `runtime.tool_executed` with the raw tool name. No attempt to force a 1:1 mapping between Codex and Claude Code tool vocabularies. | The two runtimes have different tool vocabularies. Forcing structural parity would create a fragile coupling. Best-effort mapping preserves the useful event types (file edits, test runs, commits, pushes) while capturing everything else for observability. |

---

## Phase 5 Out-of-Scope (deferred)

Per the operator convergence (D1–D5):

- **Gemini/GLM adapters.** Additional CLI agent adapters are deferred to Phase 6+. The runtime-abstraction layer is designed to accommodate them, but no implementation work occurs in Phase 5.
- **Remote MCP transport** (HTTP/SSE/streamable). MCP stays stdio-only; the runtime adapters spawn CLI binaries as local subprocesses. Remote-MCP stays deferred (Phase 6 D2).
- **Multi-task parallelism.** Running multiple tasks concurrently (each potentially on a different runtime) is deferred to Phase 6. Phase 5 supports one task at a time, possibly switching runtimes via handoff.
- **Postgres upgrade.** SQLite remains the storage engine. Postgres upgrade stays deferred (Phase 6).
- **Web dashboard.** The multi-runtime plane adds no new operator-facing control surface. Web dashboards remain Phase 7 scope.
- **Docker-in-Docker CI.** CI runs the Codex integration tests with a real Codex binary and API key, not inside Docker-in-Docker. This is a CI infrastructure concern, not a platform feature.
- **Runtime-specific MCP server fleets.** Each runtime uses the same MCP server fleet (git-mcp, verification-mcp, artifact-mcp, etc.). No per-runtime MCP server configurations.
- **Cross-runtime tool result sharing.** Tool results from one runtime are NOT directly accessible by the other runtime after handoff. The resumption prompt includes a summary, not the full tool result history. Full context transfer is a future enhancement.

**Phase boundary discipline:** every Phase 5 epic and story carries `phase: 5` in `sprint-status.yaml`. No `phase: 5` work merges to `main` until a Phase-5 gate ADR (`docs/adr/0015-phase-5-gate.md`, to be authored) is accepted.

---

## Phase 5 Sequencing

| Order | Epic | Item | Effort | Why this order |
|---|---|---|---|---|
| 1 | **Epic 26** | α Runtime abstraction + Codex adapter + S-11 (FR89, FR90, FR95, FR98, NFR-M10) | ~3 days | Runtime dispatch table + Codex runner + separability. Must land first so every later feature is born under the abstraction. |
| 2 | **Epic 27** | α-3 Per-task runtime selection (FR91, FR97) | ~2 days | TaskCreatedPayload extension + dispatch wiring. Depends on Epic 26's abstraction. |
| 3 | **Epic 28** | α-4/α-5 Runtime handoff + session continuity (FR92, FR93, P5-I2) | ~4 days | Most complex epic: subprocess termination + resumption prompt + event continuity. Depends on Epic 27's per-task selection. |
| 4 | **Epic 29** | α-6/α-8 Budget tracking + fleet integration test (FR94, FR96, NFR-R10, NFR-S14) | ~3 days | Budget per-runtime + end-to-end fleet smoke test. Can partially parallelize with Epic 28 (budget tracking is independent of handoff). |

**Total estimated effort:** ~12 days of solo-operator work.

---

## Phase 5 Success Criteria

Phase 5 success means **at minimum:**

1. **All FR89–FR98 implemented** and verified via the BMad workflow (sprint planning → create-story → validate-story → dev-story → code-review → testarch-automate/trace/nfr → retrospective per epic).
2. **NFR-R10 verified** — integration test inspects each runner's child env and asserts the other runtime's API key is absent.
3. **NFR-M10 verified** — separability test S-11 green; toggling `WORKER_CODEX_COMMAND` enables/disables Codex capability with zero changes to other services.
4. **NFR-O13 verified** — metrics test asserts `runtime` label on task events; cardinality ratchet test green.
5. **NFR-S14 verified** — canary key test asserts `OPENAI_API_KEY` never appears in Claude Code subprocess env and `ANTHROPIC_API_KEY` never appears in Codex subprocess env.
6. **P5-I1 verified** — credential isolation test green; explicit allowlist audit for both runners.
7. **P5-I2 verified** — handoff test asserts same trace_id spans pre-handoff and post-handoff events.
8. **P5-I3 verified** — budget accounting test asserts per-runtime token breakdown in `task.completed` event; handoff rejection on budget breach.
9. **FR96 fleet smoke test green** — end-to-end Codex workflow exercising git-mcp + verification-mcp + event spine.
10. **Phase 1–4 invariants regression-free** — `tests/separability/`, `tests/crash-injection/`, `tests/idempotency/`, `tests/contract/`, `tests/arch/` all green at every Phase 5 epic boundary.
11. **Phase 5 retrospective produced** (per epic) following the Cat-6 "three falsifiable outputs" rule: wrong-assumption, single-process-change, deferred-item triage.

---

## Phase 5 Ship-Blocker Checklist

All items must be green before Phase 5 can be declared complete. Any single blocker holds the phase.

| # | Blocker | Verification method | Owner |
|---|---|---|---|
| 1 | **All FR89–FR98 implemented with passing AC tests** | CI green on all runtime-related test suites | Epic leads |
| 2 | **Separability S-11 green** — Codex capability fully toggleable via `WORKER_CODEX_COMMAND` with no other-service changes | `tests/separability/test_s11_codex.py` green | Epic 26 |
| 3 | **Credential isolation verified** — `ANTHROPIC_API_KEY` absent from Codex child env; `OPENAI_API_KEY` absent from Claude Code child env | `tests/integration/test_runtime_credential_isolation.py` green | Epic 26 |
| 4 | **Runtime dispatch verified** — `WorkerSettings.runtime` dispatches to correct runner; existing Claude Code path unchanged | `tests/test_config.py` + `tests/test_run_task.py` green | Epic 26 |
| 5 | **Codex runner spawns and returns structured result** — JSONL parsing, event extraction, token tracking | `tests/test_codex_runner.py` green | Epic 26 |
| 6 | **Per-task runtime selection verified** — `TaskCreatedPayload.preferred_runtime` field routes to correct runner | `tests/test_run_task.py` green (with runtime selection) | Epic 27 |
| 7 | **Runtime handoff verified** — same trace_id spans pre/post handoff; budget cumulative; worktree lock preserved | `tests/integration/test_runtime_handoff.py` green | Epic 28 |
| 8 | **Budget per-runtime accounting verified** — token breakdown by runtime in `task.completed` event; handoff rejected on budget breach | `tests/integration/test_runtime_budget.py` green | Epic 29 |
| 9 | **Fleet smoke test green** — Codex + git-mcp + verification-mcp + event spine in single workflow | `tests/integration/test_codex_fleet_smoke.py` green | Epic 29 |
| 10 | **Runtime health checks verified** — installed/api_key_valid/version checks for both runtimes | `tests/test_runtime_health.py` green | Epic 26 |
| 11 | **All three new event types registered** — `task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked`; cardinality baseline updated | `tests/arch/test_event_cardinality.py` green (with runtime events) | Epic 26 |
| 12 | **Phase 1–4 regression suite green** — no regressions in separability, crash-injection, idempotency, contract, or arch tests | Full CI pipeline green | CI |
| 13 | **Phase 5 retrospective produced** — three falsifiable outputs per epic | Retro documents reviewed and accepted | Operator |
| 14 | **Phase-5 gate ADR accepted** (`docs/adr/0015-phase-5-gate.md`) | ADR status = accepted | Operator |

---

## Amendment Traceability

- **Core decision source:** Phase-4 retrospective readiness assessment (`phase-4-retrospective-2026-06-06.md` — Phase 5 Readiness Assessment).
- **Runtime decision:** Codex CLI (`codex exec`) as first second runtime behind abstraction layer; Gemini/GLM deferred to Phase 6+.
- **Architecture impact:** future `architecture.md` extension will document the runtime dispatch table, the `BaseRunner` protocol, the Codex adapter's JSONL parsing, the per-task runtime selection flow, and the handoff protocol.
- **Implementation-readiness gate:** before Phase 5 implementation begins, `bmad-check-implementation-readiness` must validate that this PRD amendment + a Phase 5 architecture amendment + a Phase 5 epics/stories decomposition are aligned. Phase 5 sprint planning cannot start until the readiness report passes.
- **Phase boundary discipline:** every Phase 5 epic and story carries `phase: 5` in `sprint-status.yaml`. No `phase: 5` work merges to `main` until a Phase-5 gate ADR (`docs/adr/0015-phase-5-gate.md`, to be authored) is accepted.
- **Carried-forward prerequisite:** Phase 4 must be fully complete (all 14 ship-blockers green) before Phase 5 opens. The runtime abstraction must not be built on top of an incomplete browser automation plane.

— *Amendment by R2d2, 2026-06-06, via the BMad `bmad-create-prd` workflow (Phase-5 extension; operator convergence D1–D5).*
