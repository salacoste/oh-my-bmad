---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: complete
finalStoryCount: 14
finalEpicCount: 5
inputDocuments:
  - _bmad-output/planning-artifacts/phase-8-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-8-architecture-amendment.md
  - _bmad-output/implementation-artifacts/deferred-work.md
  - _bmad-output/implementation-artifacts/phase-7-retrospective-2026-06-08.md
workflowType: epics-and-stories
project_name: oh-my-bmad
user_name: R2d2
date: '2026-06-08'
---

# oh-my-bmad -- Phase 8 Epic Breakdown: Platform Hardening & Debt Resolution

## Overview

Phase 8 is the **closure phase** -- resolve all 19 GATED items in `deferred-work.md`, close 7 WONTDO items with documented rationale, and optionally ship 2 feature candidates deferred from Phase 6 (FC-P6-1 auto-scaling, FC-P6-2 Gemini schema enforcement). This document decomposes FR108-FR121 and associated NFRs into **5 epics (41-45) and 14 stories**, continuing the epic numbering from Phase 7 (Epics 35-40).

Source documents:
- PRD amendment: `_bmad-output/planning-artifacts/phase-8-prd-amendment.md` (FR108-FR121)
- Architecture amendment: `_bmad-output/planning-artifacts/phase-8-architecture-amendment.md` (P8-I2)
- Deferred work: `_bmad-output/implementation-artifacts/deferred-work.md`
- Phase 7 retrospective: `_bmad-output/implementation-artifacts/phase-7-retrospective-2026-06-08.md`

## Requirements Inventory

### Functional Requirements

**FR108.** Platform adopts ADR-0021 API Versioning Strategy: additive-only changes within v1. Breaking changes require `/v2/` prefix.

**FR109.** Alembic migration adds composite index `ix_events_task_id_mono_ns` on `events(task_id, emitted_at_monotonic_ns)`. Reversible, tested on both SQLite and Postgres.

**FR110.** State machine GATED items from Phase 1 closed with documentation citing Phase 6's ADR-0018 and Phase 7's FSM extensions.

**FR111.** GitHub write tools transition from `simulate=True` default to config-gated explicit opt-in via `GITHUB_TOOLS_LIVE_MODE`.

**FR112.** Scoped-token authority model: repo scope derives from `GITHUB_SCOPED_TOKEN_REPO` config, not per-call args.

**FR113.** Input sanitization boundary documented: platform sanitizes structural inputs; free-text task descriptions are intentionally passed through.

**FR114.** Docker healthcheck hardening: entrypoint removes stale `/tmp/ready` before service start.

**FR115.** Per-server env scoping: each MCP server receives only its allowlisted env vars, enforced at spawn time.

**FR116.** WONTDO items (7) formally closed with documented rationale.

**FR117.** Module resolution path fix: integration tests use absolute paths independent of CWD.

**FR118 (FC-P6-2).** Gemini adapter enforces structured output via JSON Schema when `GEMINI_OUTPUT_SCHEMA` is configured.

**FR119 (FC-P6-1).** Worker pool auto-scaling control loop: queue-driven Docker Compose scaling with config-gated opt-in.

**FR120.** Lock protocol TOCTOU acceptance: documented as acceptable at single-operator scale with stale lock runbook.

**FR121.** Final audit: zero open GATED items in deferred-work.md.

### Non-Functional Requirements

**NFR-O17.** API stability: `/v1/` surface is stable post-Phase 8, governed by ADR-0021.

**NFR-O18.** Event pagination query performance with composite index: <2ms p95 for tasks with up to 10K events.

**NFR-S17.** Per-server env isolation: each MCP server's child env independently auditable.

**NFR-S18.** Config-gated live mode: destructive GitHub ops require two explicit opt-ins.

**NFR-R13.** Autoscale operations complete within 30s; scale-down drains worker first.

**NFR-R14.** Index migration does not lock events table for >5s on 10K-event table.

**NFR-M13.** Zero GATED items in deferred-work.md (both FR and NFR).

**NFR-M14.** Gemini schema enforcement backward-compatible: absent config = no validation.

### GATED Item Coverage Map

| GATED Item | Source | Epic | Story | Resolution |
|------------|--------|------|-------|------------|
| GATED-ARCH D4 (3.11) - State machine design | Story 3.11 | 41 | 41.3 | CLOSED: ADR-0018 resolved |
| GATED-OPS D4 (3.12) - Input sanitization | Story 3.12 | 42 | 42.3 | CLOSED: boundary documented |
| GATED-ARCH D1 (5.3) - Lock TOCTOU | Story 5.3 | 45 | 45.1 | CLOSED: acceptance documented |
| GATED-OPS D2 (5.3) - Stale lock recovery | Story 5.3 | 45 | 45.1 | CLOSED: runbook entry |
| GATED-ARCH D3 (5.3) - Task state machine | Story 5.3 | 41 | 41.3 | CLOSED: ADR-0018 resolved |
| GATED-ARCH D3 (5.17a) - Cross-path transitions | Story 5.17a | 41 | 41.3 | CLOSED: FSM extensions resolved |
| GATED-ARCH D3 (6.14) - Default-open policy | Story 6.14 | 41 | 41.3 | CLOSED: documented as Phase-1 design |
| GATED-ARCH D3 (7.8) - Best-effort synthesis | Story 7.8 | 41 | 41.3 | CLOSED: documented as acceptable |
| GATED-OPS D2 (7.5.5) - Stale lock recovery | Story 7.5.5 | 45 | 45.1 | CLOSED: runbook entry |
| GATED-ARCH D1 (7.5.6) - Composite index | Story 7.5.6 | 41 | 41.2 | CLOSED: Alembic migration |
| GATED-OPS D3 (7.5.6) - CLI auth | Story 7.5.6 | 41 | 41.3 | CLOSED: documented as infra-layer |
| GATED-ARCH D6 (7.5.6) - API versioning | Story 7.5.6 | 41 | 41.1 | CLOSED: ADR-0021 |
| GATED-ARCH D2 (9.6) - Module resolution | Story 9.6 | 43 | 43.3 | CLOSED: absolute paths |
| GATED-OPS D3 (9.6) - Config-gated behavior | Story 9.6 | 42 | 42.2 | CLOSED: config-gated model |
| GATED-OPS (11.3.10) - Stale /tmp/ready | Story 11.3.10 | 42 | 42.4 | CLOSED: entrypoint fix |
| GATED-ARCH P2 (15.2) - Discovery architecture | Story 15.2 | 41 | 41.3 | CLOSED: ADR-0010 follow-up |
| GATED-OPS 16.5/16.6 - GitHub write tools | Story 16.4 | 42 | 42.1 | CLOSED: config-gated opt-in |
| GATED-OPS P2 (16.5) - Scoped token authority | Story 16.5 | 42 | 42.2 | CLOSED: config-driven model |
| GATED-P0 (16.5) - Per-server env scoping | Story 16.5 | 43 | 43.1 | CLOSED: spawn-time enforcement |

**100% GATED item coverage confirmed -- 19 items mapped across 5 epics, zero orphans.**

### FR Coverage Map

| FR | Epic | Story IDs | Notes |
|----|------|-----------|-------|
| FR108 | 41 | 41.1 | ADR-0021 |
| FR109 | 41 | 41.2 | Alembic migration |
| FR110 | 41 | 41.3 | Documentation closures |
| FR111 | 42 | 42.1 | Config-gated live mode |
| FR112 | 42 | 42.2 | Scoped token config |
| FR113 | 42 | 42.3 | Sanitization boundary doc |
| FR114 | 42 | 42.4 | Healthcheck hardening |
| FR115 | 43 | 43.1 | Per-server env scoping |
| FR116 | 43 | 43.2 | WONTDO closures |
| FR117 | 43 | 43.3 | Module resolution fix |
| FR118 | 44 | 44.1 | Gemini schema enforcement |
| FR119 | 44 | 44.2 | Auto-scaling control loop |
| FR120 | 45 | 45.1 | Lock protocol closure |
| FR121 | 45 | 45.2 | Final audit |

**100% FR coverage confirmed -- 14 FRs mapped across 5 epics, zero orphans.**

## Epic List

### Dependency Graph

```
Epic 41 (API Contract) ─────┐
Epic 42 (Operator Config) ──┤──► Epic 45 (Backlog Closure)
Epic 43 (Security) ─────────┤
Epic 44 (Feature Candidates)┘ (optional, parallel with 41-43)
```

### Standalone Value

- **Epic 41** delivers: API stability commitment (ADR-0021), query performance improvement (composite index), and state machine debt closure (documentation).
- **Epic 42** delivers: Safe GitHub write tool activation, scoped credential model, input sanitization clarity, and healthcheck reliability.
- **Epic 43** delivers: Per-server env isolation (defense-in-depth), WONTDO rationale, and test infrastructure fix.
- **Epic 44** delivers: Two optional feature candidates -- Gemini schema enforcement and worker pool auto-scaling.
- **Epic 45** delivers: Zero open GATED items. The definitive closure audit.

### Sequencing Rationale

Epics 41-43 are independent and can execute in any order (or partially in parallel). Epic 44 is optional and parallel with 41-43. Epic 45 (backlog closure) lands last because it verifies that all prior epics completed their debt closures.

## Epic 41: API Contract Formalization (backlog)

**Goal.** Formalize the API versioning strategy, add the composite index for event pagination performance, and close state-machine-related GATED items with documentation. This epic resolves 7 GATED-ARCH items through ADRs, migrations, and documentation.

**FRs covered:** FR108, FR109, FR110
**NFRs:** NFR-O17, NFR-O18, NFR-R14

### Story 41.1: Author ADR-0021 API Versioning Strategy

**Title:** Author and accept ADR-0021 -- API Versioning Strategy

**Description:** Write ADR-0021 defining the additive-only evolution policy for the `/v1/` HTTP API. The ADR specifies permitted changes (new optional fields, new endpoints, response_model addition) and prohibited changes (field removal/rename, type changes, endpoint removal). Breaking changes require a `/v2/` prefix with migration guide.

**Acceptance criteria:**
1. ADR-0021 filed at `docs/adr/0021-api-versioning-strategy.md` with `status: accepted`.
2. Permitted and prohibited change classes are enumerated.
3. Breaking change procedure documented (new prefix + migration guide + deprecation timeline).
4. GATED-ARCH D6 from Story 7.5.6 updated to CLOSED with reference to ADR-0021.

**Size:** S
**FR/NFR reference:** FR108, NFR-O17
**ATDD contracts:**
- Given the ADR text, when reviewed against the existing `/v1/` endpoints, then every current response field is classified as "protected -- must not be removed or renamed."
- Given a hypothetical breaking change (field removal), when evaluated against ADR-0021, then the change is correctly classified as requiring `/v2/`.

### Story 41.2: Add Alembic Migration for ix_events_task_id_mono_ns Index

**Title:** Add composite index migration for event pagination performance

**Description:** Create an Alembic migration that adds the `ix_events_task_id_mono_ns` composite index on `events(task_id, emitted_at_monotonic_ns)`. The migration must be reversible and tested on both SQLite and Postgres backends.

**Acceptance criteria:**
1. Alembic migration creates `ix_events_task_id_mono_ns` on `events(task_id, emitted_at_monotonic_ns)`.
2. Migration runs cleanly on SQLite (no special handling needed).
3. Migration runs cleanly on Postgres (consider `CONCURRENTLY` for large tables).
4. Downgrade removes the index without data loss.
5. Query latency for `GET /v1/tasks/{id}/events?after=` measurably improves (benchmark before/after).
6. Migration does not lock events table for >5s on a 10K-event table (NFR-R14).
7. GATED-ARCH D1 from Story 7.5.6 updated to CLOSED with migration reference.

**Size:** M
**FR/NFR reference:** FR109, NFR-O18, NFR-R14
**ATDD contracts:**
- Given a task with 5000 events, when paginating with `after` cursor, then query completes in <2ms p95.
- Given the migration, when `alembic downgrade -1` runs, then the index is removed and queries still work (full table scan fallback).
- Given both SQLite and Postgres backends, when migration runs, then the index exists on both.

### Story 41.3: Close State Machine GATED Items with Phase 7 Documentation

**Title:** Document state machine GATED item closures

**Description:** Update the remaining state-machine-related GATED-ARCH items in `deferred-work.md` to CLOSED status, citing Phase 6's ADR-0018 (TaskStateMachine) and Phase 7's FSM extensions (auto_retry, auto_stop transitions) as evidence that the original design gaps have been resolved.

**Acceptance criteria:**
1. GATED-ARCH D4 (Story 3.11 -- state machine design) updated to CLOSED: "Phase 6 ADR-0018 TaskStateMachine resolves."
2. GATED-ARCH D3 (Story 5.3 -- task state machine) updated to CLOSED: "Phase 6 ADR-0018 resolves."
3. GATED-ARCH D3 (Story 5.17a -- cross-path transitions) updated to CLOSED: "Phase 7 FSM extensions (auto_retry, auto_stop) cover cross-path flows."
4. GATED-ARCH D3 (Story 6.14 -- default-open policy) updated to CLOSED: "Phase 1 design decision; documented as acceptable at single-operator scale."
5. GATED-ARCH D3 (Story 7.8 -- best-effort synthesis) updated to CLOSED: "Dedup architecture deferred; current best-effort is acceptable."
6. GATED-OPS D3 (Story 7.5.6 -- CLI auth) updated to CLOSED: "Auth handled at infrastructure layer; documented in runbook."
7. GATED-ARCH P2 (Story 15.2 -- discovery architecture) updated to CLOSED: "ADR-0010 follow-up documented; explicit registry preferred."
8. Each closure includes a one-line rationale citing the resolving ADR/story/design-decision.

**Size:** S
**FR/NFR reference:** FR110
**ATDD contracts:**
- Given deferred-work.md after updates, when `grep -c 'GATED-ARCH.*state machine\|GATED-ARCH.*D4\|GATED-ARCH.*D3.*5\.3\|GATED-ARCH.*D3.*5\.17a\|GATED-ARCH.*D3.*6\.14\|GATED-ARCH.*D3.*7\.8\|GATED-OPS.*D3.*7\.5\.6\|GATED-ARCH.*P2.*15\.2' deferred-work.md` runs, then count is 0 (all resolved).

---

## Epic 42: Operator Configuration Surface (backlog)

**Goal.** Resolve operator-facing GATED-OPS items: GitHub write tool activation, scoped token authority model, input sanitization boundary documentation, and Docker healthcheck hardening. These are configuration and documentation changes that make the operator's interaction with the platform safer and clearer.

**FRs covered:** FR111, FR112, FR113, FR114
**NFRs:** NFR-S18

### Story 42.1: GitHub Write Tools -- Provision Real Credentials + Config Gate

**Title:** Config-gate GitHub write tools for live mode

**Description:** Transition GitHub write tools from `simulate=True` default to a config-gated explicit opt-in. The `GITHUB_TOOLS_LIVE_MODE` env var controls whether write tools execute real GitHub API calls or return simulated responses. Default remains simulate (safe, backward compatible). The Tier-3 approval gate still enforces approval for all destructive operations in live mode.

**Acceptance criteria:**
1. `GITHUB_TOOLS_LIVE_MODE` env var added to settings (default: `false`).
2. When `false` (default), write tools return simulated responses (backward compatible).
3. When `true`, write tools execute real GitHub API calls using `GITHUB_MCP_SCOPED_TOKEN`.
4. Tier-3 approval gate enforced for all write operations regardless of mode.
5. Operator runbook updated with live mode activation instructions.
6. GATED-OPS 16.5/16.6 (Story 16.4) updated to CLOSED.
7. Negative test: live mode without scoped token configured returns a clear error.

**Size:** M
**FR/NFR reference:** FR111, NFR-S18
**ATDD contracts:**
- Given `GITHUB_TOOLS_LIVE_MODE=true` and a valid scoped token, when a Tier-3 write operation is approved, then the real GitHub API is called.
- Given `GITHUB_TOOLS_LIVE_MODE=false` (default), when any write operation is invoked, then a simulated response is returned without network calls.
- Given `GITHUB_TOOLS_LIVE_MODE=true` without `GITHUB_MCP_SCOPED_TOKEN`, when any write operation is invoked, then an error is returned.

### Story 42.2: Config-Driven Scoped-Token Authority Model

**Title:** Derive scoped token repo scope from configuration

**Description:** The repo scope for `GITHUB_MCP_SCOPED_TOKEN` should derive from a configuration setting (`GITHUB_SCOPED_TOKEN_REPO`) rather than per-call arguments. The operator configures the target repo once in `.env`; all GitHub write operations use the scoped token for that repo.

**Acceptance criteria:**
1. `GITHUB_SCOPED_TOKEN_REPO` env var added to settings (e.g., `owner/repo`).
2. All GitHub MCP write operations use the scoped token scoped to the configured repo.
3. Absent config: scoped token not used, existing behavior preserved (backward compatible).
4. GATED-OPS P2 (Story 16.5) updated to CLOSED.
5. GATED-OPS D3 (Story 9.6 -- config-gated behavior) updated to CLOSED: "Config-gated model pattern established by this story."

**Size:** S
**FR/NFR reference:** FR112
**ATDD contracts:**
- Given `GITHUB_SCOPED_TOKEN_REPO=myorg/myrepo`, when a GitHub write operation runs, then the scoped token is used for `myorg/myrepo`.
- Given no `GITHUB_SCOPED_TOKEN_REPO`, when a GitHub operation runs, then the scoped token is not used (fallback behavior).

### Story 42.3: Input Sanitization Boundary Documentation

**Title:** Document the input sanitization responsibility boundary

**Description:** Document the boundary between platform-provided sanitization (tier enforcement, worktree confinement, pre-commit hooks, task_id/branch_name patterns) and operator-supplied input (free-text task descriptions). The platform sanitizes structural inputs; operator-supplied free text is intentionally passed through to the runtime adapter, which handles safe execution.

**Acceptance criteria:**
1. Input sanitization boundary documented in operator runbook (or as a section in an existing doc).
2. Structural inputs sanitized by platform are enumerated: `task_id` (pattern validator), `pr_branch` (git ref-name pattern), event type names (bounded enum), tier classification.
3. Free-text inputs intentionally not sanitized beyond length bounds: task description, operator hints, approval reasons.
4. Rationale documented: free text is consumed by the runtime adapter, which is responsible for safe handling (prompt isolation, no shell injection, no eval).
5. GATED-OPS D4 (Story 3.12) updated to CLOSED.

**Size:** S
**FR/NFR reference:** FR113
**ATDD contracts:**
- Given the documentation, when a new developer reads it, then they can determine which inputs are platform-sanitized and which are adapter-handled.

### Story 42.4: Docker Healthcheck Hardening (/tmp/ready)

**Title:** Harden Docker healthcheck against stale /tmp/ready

**Description:** Modify the container entrypoint script to remove `/tmp/ready` on startup before the readiness probe begins. This prevents stale readiness signals from a previous container lifecycle.

**Acceptance criteria:**
1. Entrypoint script removes `/tmp/ready` (if it exists) before service start.
2. Readiness file is created only after health checks pass (existing behavior).
3. No stale `/tmp/ready` survives a container restart (`docker compose restart`).
4. GATED-OPS (Story 11.3.10 -- stale /tmp/ready) updated to CLOSED.
5. Healthcheck continues to function correctly for fresh starts and restarts.

**Size:** S
**FR/NFR reference:** FR114
**ATDD contracts:**
- Given a stale `/tmp/ready` from a previous run, when the container starts, then the file is removed before service initialization.
- Given a fresh start (no stale file), when the container starts, then healthcheck works normally.
- Given `docker compose restart`, when the container restarts, then no stale readiness signal persists.

---

## Epic 43: Security Defense-in-Depth (backlog)

**Goal.** Resolve security-related GATED items: per-server env scoping (GATED-P0), WONTDO item closures (7 items with documented rationale), and module resolution path fix for integration tests. This epic strengthens the platform's defense-in-depth posture and closes documentation debt.

**FRs covered:** FR115, FR116, FR117
**NFRs:** NFR-S17

### Story 43.1: Per-Server Env Scoping (G-SEC-2 Remainder)

**Title:** Enforce per-server env allowlists at spawn time

**Description:** Each MCP server receives only the environment variables on its explicit allowlist. The MCP spawner (`mcp_clients.py`) enforces the allowlist centrally at process spawn time, rather than relying on each server to self-enforce. This is the defense-in-depth remainder from G-SEC-2.

**Acceptance criteria:**
1. Each MCP server has an explicit `_ENV_ALLOWLIST` + `_ENV_PREFIXES` defined in a centralized config.
2. Child processes inherit ONLY allowlisted + prefixed vars from the parent env.
3. Negative test: each server's child env does NOT contain vars from other servers' scopes (e.g., `GITHUB_MCP_SCOPED_TOKEN` is absent from git-mcp's child env).
4. Negative test: `GITHUB_TOKEN` (broad PAT) is absent from ALL MCP server child envs.
5. GATED-P0 (Story 16.5 -- per-server env scoping) updated to CLOSED.
6. Adding a new MCP server to the fleet requires defining its allowlist (CI gate or checklist item).

**Size:** M
**FR/NFR reference:** FR115, NFR-S17
**ATDD contracts:**
- Given the git-mcp server's allowlist, when spawned, then `GITHUB_MCP_SCOPED_TOKEN` is absent from its child env.
- Given the github-mcp server's allowlist, when spawned, then `GITHUB_MCP_SCOPED_TOKEN` IS present in its child env but `ANTHROPIC_API_KEY` is absent.
- Given the verification-mcp server's allowlist, when spawned, then only `PATH`, `HOME`, `USER`, `LANG`, `LC_*`, and `OMB_*` vars are present.
- Given any MCP server, when spawned, then `GITHUB_TOKEN` (broad PAT) is absent from its child env.

### Story 43.2: Close WONTDO Items (7 Items with Documented Rationale)

**Title:** Document rationale for all WONTDO deferred items

**Description:** Formally close all WONTDO items in `deferred-work.md` with documented rationale. Each rationale must cite one of: already mitigated by design, out of project scope, cost exceeds benefit at single-operator scale, or superseded by a later implementation.

**Acceptance criteria:**
1. Each WONTDO item has an explicit rationale paragraph in deferred-work.md.
2. Rationale cites a specific reason (design, scope, cost, or supersession).
3. Rationale is auditable (references ADRs, stories, or design decisions where applicable).
4. The 7 WONTDO items to close:
   - D5 (Story 3.5 -- Starlette latin-1 log encoding): "Default json.dumps error behavior is acceptable; no evidence of real issues."
   - D7 (Story 3.11 -- emergency tier over-engineering): "Emergency tier handles pathological inputs; further hardening is over-engineering."
   - D3 (Story 5.18 -- Phase 1 scope): "Phase 1 design boundary; Phase 2 Epic 6 adds real approval gating."
   - D3 (Story 7.5.7 -- ADR template evolution): "Template evolution is acceptable; no structural changes needed."
   - D3 (Story 7.5.8 -- divergent validation): "No evidence of divergent behavior; add coverage if divergence surfaces."
   - D4 (Story 7.5.8 -- whitespace-stripping): "Standard Pydantic pattern; no real issues observed."
   - D1 (Story 7.8 -- narrow filter): "Adding the filter would narrow the feature incorrectly; current behavior is correct."

**Size:** S
**FR/NFR reference:** FR116
**ATDD contracts:**
- Given deferred-work.md after updates, when `grep 'WONTDO' deferred-work.md` runs, then every WONTDO line is followed by a rationale paragraph (not just the original note).

### Story 43.3: Fix Module Resolution Path in Integration Tests

**Title:** Use absolute module resolution in integration test harness

**Description:** Fix the integration test harness to use absolute module resolution paths that do not depend on the working directory at invocation time. Use `__file__`-relative or `PYTHONPATH`-based paths instead of CWD-relative `sys.path` manipulation.

**Acceptance criteria:**
1. Integration tests pass regardless of CWD (run from repo root, from tests dir, or from any subdirectory).
2. Module resolution uses `__file__`-relative or `PYTHONPATH`-based paths.
3. No `sys.path.insert(0, os.getcwd())` or CWD-relative `sys.path` manipulation.
4. GATED-ARCH D2 (Story 9.6 -- module resolution path) updated to CLOSED.
5. Existing test suite continues to pass on both SQLite and Postgres backends.

**Size:** S
**FR/NFR reference:** FR117
**ATDD contracts:**
- Given integration tests, when run from repo root, then all tests pass.
- Given integration tests, when run from `tests/` directory (`cd tests && pytest`), then all tests pass.
- Given integration tests, when run from a temporary directory with `pytest /abs/path/to/tests/`, then all tests pass.

---

## Epic 44: Optional Feature Candidates (backlog)

**Goal.** Optionally ship the 2 feature candidates deferred from Phase 6. Each is config-gated and disabled by default. If either proves too complex for the closure phase timeline, it is formally re-deferred with rationale rather than allowed to delay Phase 8 completion.

**FRs covered:** FR118, FR119
**NFRs:** NFR-M14, NFR-R13

### Story 44.1: Gemini Structured Output Schema Enforcement (FC-P6-2)

**Title:** Add optional JSON Schema validation to Gemini adapter

**Description:** When `GEMINI_OUTPUT_SCHEMA` is configured (path to a JSON Schema file), the Gemini adapter validates each output record against the schema before processing. Non-conforming output raises `GeminiSchemaValidationError`. When not configured, behavior is unchanged (backward compatible).

**Acceptance criteria:**
1. `GEMINI_OUTPUT_SCHEMA` env var accepts a path to a JSON Schema file.
2. When configured, `GeminiRunner.parse_output()` validates each output record against the schema.
3. Invalid output raises `GeminiSchemaValidationError` with validation details and record index.
4. When not configured, behavior is identical to Phase 6 Gemini adapter (backward compatible, NFR-M14).
5. Schema validation is adapter-internal; no changes to `RuntimeAdapter` protocol (ADR-0015).
6. No new third-party dependency introduced without ADR (use lightweight validator or existing `jsonschema` if available).
7. Negative test: invalid schema produces `GeminiSchemaValidationError`, not a crash.
8. Negative test: absent config produces no validation, no import error.

**Size:** M
**FR/NFR reference:** FR118, NFR-M14
**ATDD contracts:**
- Given `GEMINI_OUTPUT_SCHEMA` pointing to a valid schema, when Gemini produces conforming output, then `parse_output()` returns the records normally.
- Given `GEMINI_OUTPUT_SCHEMA` pointing to a valid schema, when Gemini produces non-conforming output, then `GeminiSchemaValidationError` is raised with the record index.
- Given no `GEMINI_OUTPUT_SCHEMA`, when `parse_output()` is called, then no schema validation occurs (backward compatible).
- Given an invalid schema file path, when `GeminiRunner` initializes, then a clear error is raised at startup (not deferred to parse time).

### Story 44.2: Worker Pool Auto-Scaling Control Loop (FC-P6-1)

**Title:** Implement queue-driven worker pool auto-scaling

**Description:** The orchestrator monitors the task queue depth and adjusts the worker count via Docker Compose scaling. When `WORKER_AUTOSCALE_ENABLED=true`, the orchestrator increases workers when queue depth exceeds `WORKER_AUTOSCALE_UP_THRESHOLD` and decreases when idle workers exceed `WORKER_AUTOSCALE_DOWN_THRESHOLD`. Scale operations emit `pool.scaled` events.

**Acceptance criteria:**
1. `WORKER_AUTOSCALE_ENABLED` env var enables/disables auto-scaling (default: `false`).
2. Queue depth monitored at `WORKER_AUTOSCALE_POLL_INTERVAL` (default: 30s).
3. Scale-up: worker count increases when `QUEUED` task count exceeds `WORKER_AUTOSCALE_UP_THRESHOLD` (default: 3).
4. Scale-down: worker count decreases when idle workers exceed `WORKER_AUTOSCALE_DOWN_THRESHOLD` (default: 2), but only after the excess worker completes its active task (drain first).
5. Bounds enforced: `WORKER_AUTOSCALE_MIN` (default: 1), `WORKER_AUTOSCALE_MAX` (default: 5).
6. Existing manual `--scale` continues to work when autoscale is disabled.
7. Each scale operation emits `pool.scaled` event with `old_count`, `new_count`, `trigger_reason` (queue_depth | idle_excess).
8. Scale operations complete within 30s (NFR-R13).
9. `pool.scaled` event registered in the event schema registry.
10. Event cardinality ratchet updated.

**Size:** L
**FR/NFR reference:** FR119, NFR-R13
**ATDD contracts:**
- Given `WORKER_AUTOSCALE_ENABLED=true` and 5 `QUEUED` tasks with `UP_THRESHOLD=3`, when the poll loop runs, then worker count increases to `min(current+1, MAX)`.
- Given 3 idle workers with `DOWN_THRESHOLD=2`, when the poll loop runs, then worker count decreases to `max(current-1, MIN)` after the excess worker drains.
- Given `WORKER_AUTOSCALE_ENABLED=false` (default), when tasks are queued, then no auto-scaling occurs (manual scaling still works).
- Given a scale operation, when it completes, then a `pool.scaled` event is emitted with correct old_count, new_count, and trigger_reason.
- Given worker count at `WORKER_AUTOSCALE_MAX`, when more tasks are queued, then worker count does not exceed MAX.
- Given worker count at `WORKER_AUTOSCALE_MIN`, when all workers are idle, then worker count does not drop below MIN.

---

## Epic 45: Deferred Work Backlog Closure (backlog)

**Goal.** Close the remaining deferred-work items (lock protocol TOCTOU acceptance, stale lock runbook) and perform the final audit confirming zero open GATED items. This is the ship gate for Phase 8 -- the phase is not complete until `deferred-work.md` has zero GATED entries.

**FRs covered:** FR120, FR121
**NFRs:** NFR-M13

### Story 45.1: Lock Protocol Closure (TOCTOU Acceptance + Stale Lock Runbook)

**Title:** Document TOCTOU acceptance and add stale lock runbook

**Description:** Document the TOCTOU acceptance in the lock protocol (acknowledged as acceptable at single-operator scale due to single-writer discipline preventing concurrent lock attempts). Add a stale lock runbook entry to operator documentation covering manual recovery from stale lock files (PID + timestamp inspection, safe removal procedure).

**Acceptance criteria:**
1. TOCTOU acceptance documented in code comments (at the lock acquisition site) with rationale: single-writer discipline prevents concurrent lock attempts; TOCTOU window is theoretical at single-operator scale.
2. Lock file format includes PID + timestamp for stale detection (verify existing implementation).
3. Stale lock runbook entry added to operator documentation:
   - How to identify a stale lock (check PID liveness, check timestamp age).
   - Safe removal procedure (`rm` with verification that PID is dead).
   - Warning against removing locks for active tasks.
4. GATED-ARCH D1 (Story 5.3 -- TOCTOU) updated to CLOSED: "TOCTOU acceptance documented; single-writer discipline mitigates."
5. GATED-OPS D2 (Story 5.3 -- stale lock) updated to CLOSED: "Runbook entry provides manual recovery procedure."
6. GATED-OPS D2 (Story 7.5.5 -- stale lock recovery) updated to CLOSED: "Same runbook entry."

**Size:** S
**FR/NFR reference:** FR120
**ATDD contracts:**
- Given the lock acquisition code, when reviewed, then a comment explains why TOCTOU is acceptable at single-operator scale.
- Given the operator runbook, when a stale lock is encountered, then the runbook provides a step-by-step recovery procedure.
- Given deferred-work.md after updates, when `grep 'GATED.*5\.3\|GATED.*7\.5\.5' deferred-work.md` runs, then count is 0 (all resolved).

### Story 45.2: Final Audit -- Zero Open GATED Items

**Title:** Verify zero open GATED items in deferred-work.md

**Description:** Perform the final audit of `deferred-work.md` confirming that every GATED item has been resolved by one of the preceding epics. Produce the final debt count and verify the ship-blocker checklist.

**Acceptance criteria:**
1. `grep -c 'GATED' deferred-work.md` returns 0.
2. Every item in deferred-work.md is in a terminal state: CLOSED, WONTDO (with rationale), or NIT.
3. Summary count produced: total items, CLOSED count, WONTDO count, NIT count.
4. Phase 8 retrospective includes the final debt count.
5. deferred-work.md header updated to reflect "All items resolved -- Phase 8 closure."
6. All 16 ship-blocker items verified green with cited evidence.
7. Phase 8 retrospective saved to `_bmad-output/implementation-artifacts/phase-8-retrospective-2026-06-XX.md`.

**Size:** S
**FR/NFR reference:** FR121, NFR-M13
**ATDD contracts:**
- Given deferred-work.md, when `grep -c '🔄 GATED' deferred-work.md` runs, then count is 0.
- Given deferred-work.md, when `grep -c '✅' deferred-work.md` runs, then count > 0 (items are resolved, not deleted).
- Given the ship-blocker checklist, when each item is verified, then all 16 items are green with evidence.
