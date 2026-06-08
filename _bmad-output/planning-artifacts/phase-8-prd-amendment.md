# Phase 8 Scope Extension — Platform Hardening & Debt Resolution

> **Status:** Phase-8 PRD amendment. Closure phase that resolves all remaining deferred work items and lands 2 optional feature candidates from Phase 6 brainstorming. FR/NFR numbering continues the canonical series (FR108 → FR111; NFR-O17 → O18; NFR-S17 → S18; NFR-R13 → R14; NFR-M13 → M14). Epic numbering continues from Phase 7 (Epic 41 = Phase 8 start).
>
> **Selected via:** Phase-7 retrospective closure assessment. Phase 7 resolved all carry-forward items from Phase 6 and delivered the full reliability stack (audit trail, heartbeat, stale detection, recovery loops, priority queue). Phase 8 is the final planned phase — it resolves all 19 remaining GATED items in `deferred-work.md`, closes 7 WONTDO items with documented rationale, and optionally ships the 2 feature candidates deferred from Phase 6 (FC-P6-1, FC-P6-2).

**Theme:** the **debt resolution closure** — zero open GATED items, 2 optional feature candidates, and a final audit proving the platform's technical debt backlog is empty. Every GATED-ARCH item gets an ADR or an explicit WONTDO with rationale. Every GATED-OPS item gets an operator runbook entry or a config gate. No new features beyond the 2 candidates; no scope expansion.

**Resolved scope (from Phase-7 retrospective):**

- **IN.** Resolve all 19 GATED items in `deferred-work.md` (12 GATED-ARCH, 6 GATED-OPS, 1 GATED-P0).
- **IN.** Close 7 WONTDO items with documented rationale in deferred-work.md.
- **IN (optional).** FC-P6-1: Worker pool auto-scaling control loop.
- **IN (optional).** FC-P6-2: Gemini structured output schema enforcement.
- **OUT.** No new services, no new MCP servers, no new runtime adapters, no new architectural invariants.

**Preserved invariants (carry from Phases 1–7 — non-negotiable):**

- **All prior invariants stand unchanged (P1-I1 through P7-I1).** Phase 8 resolves debt; it does not introduce new invariant classes.
- **Single-writer (FR26) unchanged.** No new writers.
- **MCP transport remains stdio-only.** No HTTP/SSE/streamable transport.
- **Event-only telemetry (NFR-O1/O10) unchanged.** No new instrumentation paths.
- **Tier-enforced authz (Epic 6) unchanged.** No new tier definitions.
- **Runtime adapter protocol (ADR-0015) unchanged.** Gemini schema enforcement is adapter-internal.

---

## Phase 8 Functional Requirements

### Alpha — API contract formalization (Epic 41)

- **FR108.** Platform adopts ADR-0021 API Versioning Strategy: additive-only changes within the v1 HTTP API. New fields are optional with sensible defaults. Breaking changes require a new API version prefix (`/v2/`) and a migration guide. Existing `/v1/` endpoints are never mutated incompatibly.

  **Acceptance criteria:**
  - ADR-0021 accepted and filed.
  - Wire contract stability rule documented in the operator runbook.
  - `response_model` addition on existing endpoints is permitted (does not break clients).
  - No existing `/v1/` response field is ever removed or renamed.

- **FR109.** Alembic migration adds composite index `ix_events_task_id_mono_ns` on `(task_id, emitted_at_monotonic_ns)` to the events table. The migration is reversible and tested against both SQLite and Postgres.

  **Acceptance criteria:**
  - Migration runs cleanly on both backends.
  - Query latency for `GET /v1/tasks/{id}/events?after=` measurably improves.
  - Downgrade path removes the index without data loss.

- **FR110.** State machine GATED items from Phase 1 (GATED-ARCH D4 from Story 3.11, GATED-ARCH D3 from Story 5.3, GATED-ARCH D3 from Story 5.17a) are closed with documentation: Phase 6's TaskStateMachine (ADR-0018) and Phase 7's FSM extensions have resolved the original design gaps. The closure is recorded in deferred-work.md with evidence citations.

  **Acceptance criteria:**
  - Each GATED-ARCH item updated to CLOSED with evidence citing the resolving ADR/story.
  - No state-machine GATED items remain open.

### Beta — Operator configuration surface (Epic 42)

- **FR111.** GitHub write tools (Story 16.4 deferred items 16.5/16.6) transition from `simulate=True` default to a config-gated explicit opt-in. Operator sets `GITHUB_TOOLS_LIVE_MODE=true` in `.env` to enable real GitHub API writes. Default remains simulate (safe).

  **Acceptance criteria:**
  - `GITHUB_TOOLS_LIVE_MODE` env var controls live vs simulate mode.
  - Default (absent) = simulate (backward compatible).
  - Explicit opt-in required for live writes (operator runbook updated).
  - Tier-3 approval gate still enforced for all write operations in live mode.

- **FR112.** Scoped-token authority model (Story 16.5 deferred P2): the repo scope for `GITHUB_MCP_SCOPED_TOKEN` derives from a configuration setting (`GITHUB_SCOPED_TOKEN_REPO`) rather than per-call arguments. The operator configures the target repo once; all GitHub write operations use the scoped token for that repo.

  **Acceptance criteria:**
  - `GITHUB_SCOPED_TOKEN_REPO` env var sets the target repo.
  - All GitHub MCP write operations use the scoped token scoped to the configured repo.
  - Absent config = scoped token not used (backward compatible).

- **FR113.** Input sanitization boundary (GATED-OPS D4 from Story 3.12): the responsibility boundary between platform-provided sanitization (tier enforcement, worktree confinement, pre-commit hooks) and operator-supplied input (free-text task descriptions) is documented as an ADR or runbook entry. The platform sanitizes structural inputs (task_id patterns, branch name patterns); operator-supplied free text is passed through as-is to the runtime adapter, which is responsible for safe handling.

  **Acceptance criteria:**
  - Sanitization boundary documented in operator runbook.
  - Structural sanitization (task_id, branch name, pr_branch) already enforced by Phase 1-3 validators.
  - Free-text task description is intentionally not sanitized beyond length bounds (documented rationale).

- **FR114.** Docker healthcheck hardening for stale `/tmp/ready` (GATED-OPS from Story 11.3.10): the healthcheck removes `/tmp/ready` on container startup before the readiness probe begins, preventing stale readiness signals from a previous container lifecycle.

  **Acceptance criteria:**
  - Healthcheck entrypoint script removes `/tmp/ready` before service start.
  - Readiness file is created only after health checks pass.
  - No stale `/tmp/ready` survives a container restart.

### Gamma — Security defense-in-depth (Epic 43)

- **FR115.** Per-server env scoping (GATED-P0 remainder from Story 16.5): each MCP server receives only the environment variables on its explicit allowlist. The parent process env is not inherited by default. New servers added to the fleet automatically get the minimal env required by their archetype.

  **Acceptance criteria:**
  - Each MCP server has an explicit `_ENV_ALLOWLIST` + `_ENV_PREFIXES`.
  - Child processes inherit ONLY allowlisted + prefixed vars.
  - Negative test: each server's child env does not contain vars from other servers' scopes.
  - GATED-P0 item in deferred-work.md updated to CLOSED.

- **FR116.** WONTDO items (7 items) are formally closed in deferred-work.md with documented rationale explaining why each is intentionally not implemented. Rationale must cite one of: already mitigated by design, out of project scope, cost exceeds benefit at single-operator scale, or superseded by a later implementation.

  **Acceptance criteria:**
  - All 7 WONTDO items have explicit rationale in deferred-work.md.
  - No open WONTDO items remain without rationale.
  - Rationale is auditable (references specific ADRs, stories, or design decisions).

- **FR117.** Module resolution path fix for integration tests (GATED-ARCH D2 from Story 9.6): integration test harness uses absolute module resolution paths that do not depend on the working directory at invocation time.

  **Acceptance criteria:**
  - Integration tests pass regardless of CWD.
  - Module resolution uses `__file__`-relative or `PYTHONPATH`-based paths.
  - No `sys.path` manipulation relative to CWD.

### Delta — Optional feature candidates (Epic 44)

- **FR118 (FC-P6-2).** Gemini adapter enforces structured output via a JSON Schema supplied to the Gemini CLI. When `GEMINI_OUTPUT_SCHEMA` is configured, the adapter validates that the Gemini CLI output conforms to the schema before processing. Non-conforming output is treated as a runtime error with a descriptive message.

  **Acceptance criteria:**
  - `GEMINI_OUTPUT_SCHEMA` env var accepts a path to a JSON Schema file.
  - When configured, `GeminiRunner.parse_output()` validates each output record against the schema.
  - Invalid output raises `GeminiSchemaValidationError` with the validation details.
  - When not configured, behavior is unchanged (backward compatible, NFR-M12).
  - Schema validation is adapter-internal; no changes to the RuntimeAdapter protocol (ADR-0015).

- **FR119 (FC-P6-1).** Worker pool auto-scaling control loop: the orchestrator monitors the task queue depth and adjusts the worker count via Docker Compose scaling. When `WORKER_AUTOSCALE_ENABLED=true`, the orchestrator increases workers when queue depth exceeds `WORKER_AUTOSCALE_UP_THRESHOLD` and decreases when idle workers exceed `WORKER_AUTOSCALE_DOWN_THRESHOLD`.

  **Acceptance criteria:**
  - `WORKER_AUTOSCALE_ENABLED` env var enables/disables auto-scaling (default: disabled).
  - Queue depth monitored at configurable interval.
  - Scale-up: `docker compose up --scale worker-wrapper=N+1` when queue exceeds threshold.
  - Scale-down: `docker compose up --scale worker-wrapper=N-1` when idle workers exceed threshold.
  - Min/max bounds: `WORKER_AUTOSCALE_MIN` (default 1), `WORKER_AUTOSCALE_MAX` (default 5).
  - Existing manual `--scale` continues to work when autoscale is disabled.
  - Scale operations emit `pool.scaled` events with old_count, new_count, trigger_reason.

### Epsilon — Deferred work backlog closure (Epic 45)

- **FR120.** Lock protocol closure: TOCTOU acceptance (GATED-ARCH D1 from Story 5.3) is documented as acceptable at single-operator scale. The TOCTOU window between `os.path.exists(lock_file)` and `os.open(lock_file, O_CREAT|O_EXCL)` is acknowledged and mitigated by: (a) single-writer discipline preventing concurrent lock attempts, (b) lock file content including PID + timestamp for stale detection, (c) operator runbook for manual stale lock recovery.

  **Acceptance criteria:**
  - TOCTOU acceptance documented in an ADR or code comment with rationale.
  - Stale lock runbook entry added to operator documentation.
  - GATED-ARCH D1 and GATED-OPS D2 (Story 5.3) updated to CLOSED.

- **FR121.** Final audit: zero open GATED items in deferred-work.md. Every item is in one of three terminal states: CLOSED, WONTDO (with rationale), or NIT (accepted as-is). The audit produces a summary count and saves it to the Phase 8 retrospective.

  **Acceptance criteria:**
  - `grep -c 'GATED' deferred-work.md` returns 0.
  - All items are in a terminal state.
  - Phase 8 retrospective includes the final debt count.
  - `deferred-work.md` header updated to reflect "all items resolved."

## Phase 8 Non-Functional Requirements

- **NFR-O17 (API stability).** The `/v1/` API surface is stable post-Phase 8. Additive-only changes permitted. ADR-0021 governs any future API evolution. No breaking changes without a new `/v2/` prefix.
- **NFR-O18 (Query performance with composite index).** Event pagination queries using `after` cursor with composite index `ix_events_task_id_mono_ns` complete in <2ms p95 for tasks with up to 10K events (improvement over pre-index baseline).
- **NFR-S17 (Per-server env isolation).** Each MCP server's child process env is independently auditable. No server receives env vars from another server's scope. Verified by per-server negative tests.
- **NFR-S18 (Config-gated live mode).** Destructive GitHub operations require two explicit opt-ins: (a) `GITHUB_TOOLS_LIVE_MODE=true` and (b) Tier-3 approval event. Single opt-in is insufficient. Verified by negative test.
- **NFR-R13 (Autoscale reliability).** Worker auto-scaling operations complete within 30s of trigger condition. Scale-up does not lose queued tasks. Scale-down drains the worker before termination (waits for active task to complete).
- **NFR-R14 (Index migration reliability).** Composite index migration is reversible and does not lock the events table for more than 5s on a 10K-event table (Postgres `CONCURRENTLY` or SQLite online operation).
- **NFR-M13 (Zero GATED items).** Phase 8's success criterion is zero open GATED items. This is both a functional requirement (FR121) and a maintainability gate — the deferred-work backlog is empty.
- **NFR-M14 (Schema enforcement separability).** Gemini schema enforcement is conditionally available via `GEMINI_OUTPUT_SCHEMA`. Absent the env var, the adapter works identically to Phase 6 (backward compatible).

## Phase 8 Invariants

- **P8-I1: No new invariants.** Phase 8 resolves existing debt and optionally adds 2 feature candidates. It does not introduce new architectural invariants beyond what is already established in Phases 1–7.
- **P8-I2: Closure is the deliverable.** The primary value of Phase 8 is the absence of open items, not the presence of new features. A Phase 8 that closes all GATED items but ships neither feature candidate is a success. A Phase 8 that ships both candidates but leaves GATED items open is a failure.

## Phase 8 Architecture Decisions Required

- **ADR-0021: API Versioning Strategy** — additive-only within v1, new prefix for breaking changes, `response_model` permitted
- Lock protocol TOCTOU acceptance — documented as acceptable at single-operator scale (may be a code comment + runbook entry rather than a formal ADR, at operator discretion)

## Phase 8 Ship-Blocker Checklist

1. [ ] All Phase 1–7 invariants regression-free
2. [ ] `grep -c 'GATED' deferred-work.md` returns 0
3. [ ] All 7 WONTDO items have documented rationale
4. [ ] ADR-0021 accepted
5. [ ] Alembic composite index migration runs on both SQLite and Postgres
6. [ ] Per-server env scoping negative tests pass for all MCP servers
7. [ ] GitHub write tools config-gated (live mode requires explicit opt-in)
8. [ ] Docker healthcheck stale /tmp/ready fix verified
9. [ ] Module resolution path fix verified (integration tests pass regardless of CWD)
10. [ ] `just lint` EXIT 0
11. [ ] All discipline scripts exit 0
12. [ ] No new third-party Python dependencies without ADR
13. [ ] Event cardinality ratchet updated for any new event types
14. [ ] FC-P6-1 (auto-scaling) either shipped or formally re-deferred with rationale
15. [ ] FC-P6-2 (Gemini schema enforcement) either shipped or formally re-deferred with rationale
16. [ ] Phase 8 retrospective produced with final debt count

## Estimated Effort

**5 epics, 14 stories, ~3-4 weeks solo-operator work.**

| Epic | Stories | Estimate |
|------|---------|----------|
| 41 — API contract formalization | 3 | ~4 days |
| 42 — Operator configuration surface | 4 | ~5 days |
| 43 — Security defense-in-depth | 3 | ~4 days |
| 44 — Optional feature candidates | 2 | ~5 days |
| 45 — Deferred work backlog closure | 2 | ~2 days |

Phase 8 is intentionally the smallest phase — it is a closure phase, not a feature phase. The 2 feature candidates are optional; if either proves complex enough to expand the estimate significantly, it is re-deferred rather than allowed to delay closure.
