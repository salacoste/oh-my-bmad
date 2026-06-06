---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/phase-4-prd-amendment.md
  - _bmad-output/implementation-artifacts/phase-4-retrospective-2026-06-06.md
  - _bmad-output/implementation-artifacts/phase-3-retrospective-2026-06-05.md
  - _bmad-output/implementation-artifacts/deferred-work.md
session_topic: 'Phase 5 scope definition: multi-runtime support'
session_goals: 'Define Phase 5 scope, select first runtime, identify architecture changes, produce PRD/arch amendments'
selected_approach: 'AI-Recommended — Progressive Flow'
techniques_used: [SCAMPER, Wardley Mapping, Pre-mortem]
ideas_generated: []
context_file: ''
---

# Phase 5 Brainstorming: Multi-Runtime Support

**Date:** 2026-06-06
**Participants:** R2d2 (operator), Claude (facilitator)

## Session Overview

**Topic:** Phase 5 scope definition for oh-my-bmad — introducing multi-runtime support (second CLI agent beyond Claude Code)

**Goals:**
1. Define what's IN Phase 5 vs Phase 6/7
2. Select the first second runtime (Codex, Gemini, GLM, or other)
3. Identify architecture changes needed for runtime abstraction
4. Surface risks and carry-forward items that affect scope
5. Produce actionable inputs for PRD/architecture amendments

### Context Guidance

**From PRD:** Phase 5 is explicitly named as "Multi-Runtime" with second CLI agent (Codex/Gemini/GLM). Phase 6 = Postgres + parallelism + remote MCP. Phase 7 = Web dashboard + scheduled jobs.

**From Phase 3 retrospective carry-forwards:**
- AI-16.2: GitHub write surface still simulated (needs live validation)
- AI-15.3 + AI-17.1: Output caps — **RESOLVED in debt sweep**
- G-FN-2: Spawner audit emission disabled (workaround in place)
- G-FN-3: Liveness probe unbounded (needs asyncio.wait_for)
- Fleet-level integration test gap (no multi-server workflow test)

**From Phase 4 retrospective carry-forwards:**
- Docker-in-Docker CI gap (ephemerality tests skip)
- Digest freshness gate (format-only, not content-verified)
- Fleet-level integration test gap (same as Phase 3)
- Naming convention AST gate (prevent underscore deviations)

**From deferred-work.md (open GATED items):**
- 9 GATED-ARCH items (state machine, lock protocol, dedup architecture, etc.)
- 7 GATED-OPS items (sanitization boundary, operator procedures, etc.)

## Brainstorming Analysis

### D1: What must Phase 5 deliver?

The PRD names three specific Phase 5 deliverables:
1. **Second CLI agent adapter** — a worker-wrapper variant that launches Codex/Gemini/GLM instead of Claude Code
2. **OMX orchestrator adapter** — the upstream fork of oh-my-claudecode adapted for multi-runtime dispatch
3. **Runtime handoff** — `/handoff` command to transfer a task from one runtime to another

### D2: What's the minimum viable Phase 5?

The leanest Phase 5 that delivers value:
- ONE additional runtime (not all three)
- A runtime abstraction layer in worker-wrapper
- Runtime selection per-task (config-gated)
- Cross-runtime trace_id continuity (already built in Phase 2)
- Metrics per-runtime (already grouped by actor_kind)

### D3: Risk analysis (Pre-mortem)

**"Phase 5 fails because..."**
1. The second runtime's CLI interface is unstable or poorly documented
2. Runtime abstraction adds too much complexity to the worker-wrapper
3. The OMX fork diverges too far from upstream to maintain
4. Cross-runtime state (worktree locks, session data) is inconsistent
5. The operator can't validate the second runtime without live credentials

### D4: Scope decisions

**IN Phase 5:**
- Runtime abstraction layer in worker-wrapper
- ONE second runtime adapter (Codex recommended — most CLI-like, stable API)
- Per-task runtime selection via WorkerSettings
- Cross-runtime trace_id and metrics continuity
- Runtime-specific budget/token tracking
- Fleet-level integration test (multi-server workflow)

**DEFERRED to Phase 6+:**
- Third/fourth runtime adapters (Gemini, GLM)
- Remote MCP transport (HTTP/SSE)
- Postgres upgrade
- Multi-task parallelism
- Web dashboard
- Docker-in-Docker CI support
- Digest freshness gate

### D5: First runtime selection

**Codex (OpenAI)** recommended as first second runtime:
- CLI-first interface (closest to Claude Code's model)
- Well-documented subprocess protocol
- Existing OMC integration patterns
- Large user base for validation

**Gemini** as second candidate (Phase 6):
- Google's CLI agent is newer, less mature
- Different auth model (ADC vs API key)
- Worth exploring but higher risk

**GLM** as third candidate:
- Chinese-market specific
- Authentication complexity
- Lowest priority for solo-operator
