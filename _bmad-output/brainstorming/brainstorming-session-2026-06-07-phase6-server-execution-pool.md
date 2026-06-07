---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - docs/architecture.md
  - _bmad-output/planning-artifacts/phase-5-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-5-architecture-amendment.md
  - _bmad-output/implementation-artifacts/phase-5-retrospective-2026-06-07.md
  - _bmad-output/implementation-artifacts/deferred-work.md
session_topic: 'Phase 6 scope definition: server execution pool'
session_goals: 'Define Phase 6 scope, select architecture direction, identify risks, produce PRD/arch amendment inputs'
selected_approach: 'AI-Recommended — Progressive Flow'
techniques_used: [Wardley Mapping, SCAMPER, Pre-mortem]
ideas_generated: []
context_file: ''
---

# Phase 6 Brainstorming: Server Execution Pool

**Date:** 2026-06-07
**Participants:** R2d2 (operator), Claude (facilitator)

## Session Overview

**Topic:** Phase 6 scope definition for oh-my-bmad — server execution pool, parallelism, and deployment topology evolution

**Goals:**
1. Define what's IN Phase 6 vs Phase 7
2. Select architecture direction (Postgres migration strategy, worker pool design)
3. Identify risk ordering and prerequisite resolution
4. Surface carry-forward items from Phases 1-5 that affect Phase 6
5. Produce actionable convergence decisions for PRD/architecture amendments

### Context Guidance

**From PRD (§Product Scope):**
Phase 6 = Server Execution Pool. Named deliverables:
- Docker worker pool with isolated worktrees
- Verification workers
- Remote browsers
- Postgres upgrade path (from SQLite via SQLAlchemy)
- Multi-task parallelism (running tasks concurrently on different runtimes)
- Remote MCP transport (HTTP/SSE/streamable, replacing stdio-only)
- mTLS between services for remote-worker support
- Gemini and GLM adapters (following ADR-0010 step-9 recipe)
- Codex session resume (`codex exec resume <session-id>`)
- Split deployment topology (operator on macOS, execution on VPS)

**From Phase 5 brainstorming (DEFERRED to Phase 6+):**
- Third/fourth runtime adapters (Gemini, GLM)
- Remote MCP transport (HTTP/SSE)
- Postgres upgrade
- Multi-task parallelism
- Docker-in-Docker CI support

**From Phase 5 retrospective carry-forwards:**
- CF1: Live Codex binary validation (operator milestone, not a code change)
- CF2: Heterogeneous token model documentation — RESOLVED (documented)
- CF3: Pre-commit lint gate — RESOLVED (pre-push ruff hooks added)
- CF4: Epic granularity review (LOW, process-only)

**From deferred-work.md (open GATED items affecting Phase 6):**
- 12 GATED-ARCH items (state machine design, lock protocol TOCTOU, API versioning, discovery architecture)
- 8 GATED-OPS items (sanitization boundary, simulate=True flip, scoped token authority)
- Per-server env scoping as defense-in-depth (GATED-P0, revisit if fleet grows)

**Current deployment topology:**
- 7 core services: registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon, metrics-subscriber
- 8 MCP stdio servers: task-registry, session-registry, clawhip-bridge, git, github, verification, memory, artifact
- Optional: migrator, litestream sidecar
- 1 worker instance, 1 task at a time (NFR-SC3)
- All on single host (single-target deployment)

## Brainstorming Analysis

### D1: What must Phase 6 deliver?

The PRD names 10 specific Phase 6 deliverables. Grouped by dependency:

**Layer 1 — Foundation (unblocks everything else):**
1. Postgres upgrade path — SQLite → Postgres via SQLAlchemy migration
2. Multi-task parallelism — multiple concurrent tasks per worker (or multiple workers)

**Layer 2 — Infrastructure (builds on foundation):**
3. Docker worker pool — isolated worktrees, task-to-worker assignment
4. Verification workers — dedicated verification executors
5. Remote browsers — browser-mcp accessible from remote workers

**Layer 3 — Connectivity (enables distributed topology):**
6. Remote MCP transport — HTTP/SSE/streamable for non-stdio MCP servers
7. mTLS between services — network encryption for multi-host deployment

**Layer 4 — Extensibility (adds surface area):**
8. Gemini adapter (following ADR-0010 step-9 + ADR-0015 recipe)
9. GLM adapter
10. Codex session resume

**Layer 5 — Topology (combines all layers):**
11. Split deployment — operator on macOS, execution on VPS

### D2: What's the minimum viable Phase 6?

The leanest Phase 6 that delivers meaningful value:

**Option A: Postgres + Parallelism (infrastructure play)**
- Postgres migration (unblocks shared-infrastructure deployment)
- Multi-task parallelism (multiple concurrent tasks)
- Keep single-host deployment
- Estimated: 5-7 epics, ~30 stories

**Option B: Worker Pool (execution play)**
- Docker worker pool with isolated worktrees
- Task-to-worker assignment via registry
- Multi-task parallelism via pool
- Keep SQLite (single-writer still works on single host)
- Estimated: 4-6 epics, ~25 stories

**Option C: Full Scope (everything in PRD)**
- All 10+ deliverables
- Estimated: 10-12 epics, ~60-80 stories
- Risk: too broad for solo-operator in one phase

**RECOMMENDATION: Option A (Postgres + Parallelism)** — Postgres is the architectural gate for everything else. Without it, split deployment and remote workers remain impractical. Multi-task parallelism is the most operator-visible feature. Together they form a coherent, valuable Phase 6.

### D3: Risk analysis (Pre-mortem)

**"Phase 6 fails because..."**

1. **Postgres migration breaks FR26 single-writer.** SQLAlchemy's session management could introduce transaction isolation bugs. Mitigation: keep explicit single-writer semantics at the application layer; Postgres row-level locking is stronger than SQLite WAL but requires different testing.

2. **Multi-task parallelism creates worktree contention.** Two tasks editing the same repo simultaneously will conflict. Mitigation: per-task isolated worktrees (already exist via worktree-lock); extend to per-task branch strategy.

3. **Postgres migration is a deployment-breaking change.** Data loss or incompatibility on upgrade. Mitigation: Alembic migration + backward-compatible read path + litestream backup before migration.

4. **Scope creep into remote MCP / mTLS.** These are Phase 7 concerns that distract from the core deliverable. Mitigation: strict IN/OUT decisions in D4.

5. **State machine debt blocks parallelism.** The GATED-ARCH state machine design (D4 from Story 3.10) is unresolved. Multi-task parallelism requires a formal task state machine. Mitigation: resolve state machine as a Phase 6 prerequisite epic.

### D4: Scope decisions

**IN Phase 6:**
- Postgres migration (SQLite → Postgres via Alembic + SQLAlchemy)
- Multi-task parallelism (N tasks concurrent on N workers or N tasks on 1 worker)
- Task state machine formalization (resolves GATED-ARCH D4 from Phase 1)
- Per-task worktree isolation strategy (branch-per-task or volume-per-task)
- Docker worker pool (multiple worker-wrapper instances, task assignment)
- Gemini adapter (following ADR-0010 + ADR-0015 recipe; 3rd runtime)
- Postgres-aware health probes and migration tooling
- Separability test S-12 (Postgres optional, SQLite fallback)

**DEFERRED to Phase 7+:**
- Remote MCP transport (HTTP/SSE/streamable) — requires its own ADR
- mTLS between services — only needed for split deployment
- Split deployment topology (operator on macOS, execution on VPS) — blocked on remote MCP
- GLM adapter — lower priority than Gemini
- Remote browsers — blocked on remote MCP transport
- Codex session resume — nice-to-have, not architecture-critical
- Web dashboard — Phase 7 per PRD
- Scheduled jobs — Phase 7 per PRD
- Dead-session detection — Phase 7 per PRD
- Recovery loops — Phase 7 per PRD

### D5: Architecture direction

**Postgres migration strategy:**
- **Decision: SQLAlchemy ORM is already in place.** Registry-api and registry-state use SQLAlchemy ORM for all database access. Migration path = add Alembic migration + Postgres connection string + CI test against Postgres.
- SQLite remains the default for local development. Postgres is opt-in via `REGISTRY_DATABASE_URL` env var.
- Both SQLite and Postgres must pass the full test suite (separability S-12).
- No schema changes required — only connection/backend swap.

**Worker pool strategy:**
- **Decision: Process-based pool (Docker Compose scale).** Multiple `worker-wrapper` containers, each processing one task at a time. The `orchestrator-adapter` assigns tasks to available workers via the task registry.
- Alternative rejected: Thread-based parallelism inside a single worker-wrapper. Too complex; breaks the single-task-per-worker invariant that the FSM depends on.
- Task assignment: registry-api gains a `worker_id` field on Task; orchestrator-adapter claims unassigned tasks in a polling loop.

**State machine formalization:**
- **Decision: Formalize the implicit FSM as a proper state machine module.** Resolves GATED-ARCH D4 (from Story 3.10). States: CREATED → QUEUED → ASSIGNED → RUNNING → COMPLETED/FAILED/CANCELLED. Transitions are event-driven.
- The state machine is a Phase 6 prerequisite epic (lands before parallelism).
