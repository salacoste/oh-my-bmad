---
stepsCompleted: [document-discovery, prerequisite-check, architecture-alignment, codebase-readiness, gap-analysis, final-assessment]
documentsAssessed:
  - _bmad-output/planning-artifacts/phase-5-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-5-architecture-amendment.md
  - _bmad-output/implementation-artifacts/deferred-work.md
  - _bmad-output/implementation-artifacts/phase-4-retrospective-2026-06-06.md
  - services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py
  - services/worker-wrapper/src/worker_wrapper/app/config.py
  - services/worker-wrapper/src/worker_wrapper/app/main.py
  - services/registry-state/src/registry_state/domain/event_types.py
  - docs/adr/ (ADR-0001 through ADR-0014)
verdict: CONDITIONALLY-READY
---

# Implementation Readiness Assessment Report

**Date:** 2026-06-07
**Project:** oh-my-bmad
**Phase:** Phase 5 -- Multi-Runtime Support
**Assessor:** BMad check-implementation-readiness (automated)

---

## 1. Document Discovery

### Files Found

**PRD Documents:**
- `phase-5-prd-amendment.md` (12 KB, 2026-06-06) -- Phase 5 extension FR89-FR98 + NFRs
- `prd.md` (120 KB) -- canonical Phase 1-4 PRD (baseline reference)

**Architecture Documents:**
- `phase-5-architecture-amendment.md` (14 KB, 2026-06-06) -- Phase 5 runtime adapter protocol, Codex adapter, factory, credential isolation
- `architecture.md` (133 KB) -- canonical Phase 1-4 architecture (baseline reference)

**Retrospective / Deferred:**
- `phase-4-retrospective-2026-06-06.md` (8 KB, 2026-06-06) -- Phase 4 retro with Phase 5 readiness assessment
- `deferred-work.md` (10 KB, 2026-06-05) -- all open deferred items from Phases 1-4

**ADR Documents:**
- ADR-0001 through ADR-0014 exist (accepted status)
- **ADR-0015** (multi-runtime adapter) -- NOT YET AUTHORED
- **ADR-0016** (Phase 5 gate) -- NOT YET AUTHORED

**Epics/Stories:**
- Phase 4 epics (20-22) are complete (17 stories shipped)
- **Phase 5 epics (26-29 per PRD, 23-25 per architecture) NOT YET CREATED** -- decomposition pending

**Sprint Status:**
- `sprint-status.yaml` NOT FOUND -- no sprint tracking file located yet

**Separability Tests:**
- S-1 through S-10 exist (`tests/separability/test_s1_*.py` through `test_s10_*.py`)

### Issues
- ADR-0015 and ADR-0016 are missing (both are listed as proposed prerequisites in the architecture amendment)
- Phase 5 epics not yet decomposed
- Epic numbering discrepancy between PRD (26-29) and architecture (23-25) -- see Section 3

---

## 2. Prerequisites Check

### Phase 4 Completion

| Ship-Blocker | Status | Evidence |
|---|---|---|
| All FR78-FR88 implemented | COMPLETE | 15 browser tools, 6 event types, 3 tiers shipped |
| Separability S-10 | COMPLETE | `test_s10_browser_optional.py` exists |
| G-SEC-2 full closure | COMPLETE | Both MCP-subprocess + claude-agent halves closed (deferred-work.md D1/D4) |
| ADR-0013/0014 accepted | COMPLETE | Listed in docs/adr/ |
| Phase 4 retro produced | COMPLETE | `phase-4-retrospective-2026-06-06.md` exists |
| Phase 4 gate ADR accepted | COMPLETE | ADR-0014 |

**Verdict: Phase 4 is fully complete.** All 14 ship-blockers from Phase 4 are green. The PRD's carried-forward prerequisite ("Phase 4 must be fully complete before Phase 5 opens") is satisfied.

### Deferred Work Blockers

Review of `deferred-work.md` for Phase 5 blockers:

| Item | Status | Phase 5 Impact |
|---|---|---|
| AI-14.1: Ratchet mutation threshold above 82% | OPEN (monitor) | No blocker -- quality metric, not structural |
| AI-14.2 / G-FN-2: Re-enable spawner audit emission | OPEN (monitor) | No blocker -- audit emission is orthogonal to runtime adapter |
| AI-15.2: Broaden tier-declaration discovery | OPEN (monitor) | No blocker -- CI gate enhancement |
| AI-16.2: Flip simulate=False | OPEN (GATED-OPS) | No blocker -- github write tool configuration |
| Fleet-level integration test | OPEN (deferred) | **ADDRESSED by FR96** -- Phase 5 ships it as Epic 29 |
| P1 Navigation tool dedup | OPEN (carry) | No blocker -- browser-mcp maintenance |
| P2 Digest pinning is format-only | OPEN (carry) | No blocker -- supply chain hardening |
| P2 Naming convention gate | OPEN (carry) | No blocker -- CI gate enhancement |
| Docker-in-Docker CI support | OPEN (gap) | No blocker -- CI infrastructure |

**Verdict: No deferred items block Phase 5.** The fleet-level integration test gap is explicitly addressed by FR96/Epic 29.

### Event Schema Readiness

Current event registrations in `event_types.py`:
- 66 event types registered (cardinality baseline from Phase 4)
- Schema versions range from 1.0.0 to 1.2.0
- Phase 5 requires 3 new event types: `task.runtime_handoff`, `task.runtime_fallback`, `runtime.health_checked`
- Phase 5 requires extending `TaskExecutionStartedPayload` with optional `runtime` field (schema bump to 1.2.0)
- Phase 5 requires extending session events with optional `runtime` field
- All extensions are additive-only per NFR-M3

**Verdict: Event schema is ready for additive extension.** No migration conflicts.

---

## 3. Architecture Alignment (PRD vs Architecture Amendment)

### Epic Numbering Discrepancy

| PRD Epic | Architecture Epic | Scope |
|---|---|---|
| Epic 26 (runtime abstraction + Codex + S-11) | Epic 23 (runtime abstraction layer) | **Structural difference** |
| Epic 27 (per-task selection) | Epic 24 (Codex adapter) | **Structural difference** |
| Epic 28 (handoff + session continuity) | Epic 25 (events + metrics + CI) | **Structural difference** |
| Epic 29 (budget + fleet smoke test) | -- (part of Epic 25) | **Structural difference** |

The PRD uses 4 epics (26-29) while the architecture uses 3 epics (23-25). Both cover all 10 FRs (FR89-FR98). The numbering is inconsistent -- the PRD continues from Phase 4's Epic 22, while the architecture resets to Epic 23.

**Resolution needed:** Choose one numbering scheme. Recommend aligning with the PRD's 26-29 sequence (continuation from Phase 4's Epics 20-22), and using the architecture's structural content (which is more detailed on the protocol/factory/allowlist design).

### Scope Alignment

| FR | PRD Section | Architecture Section | Aligned? |
|---|---|---|---|
| FR89 Runtime abstraction | alpha | Runtime abstraction layer | YES |
| FR90 Codex adapter | alpha-2 | Concrete adapter: CodexRunner | YES |
| FR91 Per-task selection | alpha-3 | Runtime selection in worker-wrapper | YES |
| FR92 Runtime handoff | alpha-4 | Runtime handoff flow | YES |
| FR93 Session continuity | alpha-5 | Event schema extensions | YES |
| FR94 Budget per-runtime | alpha-6 | Budget tracking per-runtime | YES |
| FR95 Health probes | alpha-7 | (implied in protocol) | PARTIAL -- architecture lacks health probe detail |
| FR96 Fleet smoke test | alpha-8 | (not in architecture) | NO -- architecture omits FR96 |
| FR97 Runtime events | alpha-9 | Event schema extensions | YES |
| FR98 Separability S-11 | alpha-10 | (mentioned in factory) | PARTIAL -- architecture mentions separability but lacks S-11 test detail |

### Cross-Document Reconciliation Findings

| Finding | Severity | Resolution |
|---|---|---|
| Epic numbering mismatch (26-29 vs 23-25) | MEDIUM | Choose one scheme before decomposition |
| FR96 fleet smoke test absent from architecture | MEDIUM | Architecture should acknowledge FR96 or scope it out |
| FR95 health probes lack architecture detail | LOW | PRD ACs are sufficient for implementation |
| FR98 S-11 separability detail absent from architecture | LOW | Follow Phase 3/4 separability pattern |
| Architecture mentions `runtime_budget_overrides` latent scaffold not in PRD | LOW | Accept as architecture-level detail (not an FR) |
| Architecture uses `Protocol` while PRD mentions `BaseRunner` protocol | LOW | Architecture is more specific and correct |

---

## 4. Codebase Readiness

### Runner Structure (`claude_code_runner.py`)

The existing `ClaudeCodeRunner` (634 lines) already implements the patterns the architecture requires:

| Pattern | Current State | Phase 5 Compatibility |
|---|---|---|
| Subprocess spawn via `asyncio.create_subprocess_exec` | Implemented in `_spawn()` | READY -- CodexRunner mirrors this |
| JSON-line stdout reading | Implemented in `_read_stream()` | READY -- CodexRunner mirrors this |
| Explicit env allowlist (`_CHILD_ENV_ALLOWLIST`) | Implemented (lines 77-97) | READY -- P5-I1 requires a separate `_CODEX_ENV_ALLOWLIST` |
| Prefix allowlist (`_CHILD_ENV_PREFIXES`) | Implemented (line 102) | READY -- Codex gets `("OMB_", "CODEX_")` |
| `_build_child_env()` function | Implemented (lines 105-117) | READY -- Codex gets a parallel function |
| Graceful termination (SIGTERM -> SIGKILL) | Implemented in `terminate_with_grace()` | READY -- P5-I3 requires identical semantics |
| `TerminationResult` dataclass | Implemented (lines 144-184) | READY -- shared across adapters |
| `ExtractedEvent` dataclass | Implemented (lines 120-127) | READY -- shared across adapters |
| `ClaudeCodeResult` dataclass | Implemented (lines 130-141) | READY -- Codex gets parallel `CodexResult` |
| `_classify_tool_use()` event classification | Implemented (lines 338-371) | READY -- Codex gets parallel classifier |

**Key observation:** The architecture's `RuntimeAdapter` protocol maps 1:1 to existing `ClaudeCodeRunner` methods:
- `runtime_name` -> new property, returns `"claude-code"`
- `spawn()` -> wraps existing `_spawn()`
- `is_healthy()` -> wraps `self._process.returncode is None`
- `parse_output()` -> wraps existing `_handle_message()`
- `kill()` -> wraps existing `terminate_with_grace()`

The refactoring is structural only. No behavioral change to the existing Claude Code path.

### Configuration (`config.py`)

Current `WorkerSettings` fields relevant to Phase 5:

| Field | Current State | Phase 5 Addition |
|---|---|---|
| `claude_command` | `"claude"` (line 155) | Unchanged |
| `claude_max_turns` | `0` (line 156) | Unchanged |
| `claude_timeout_s` | `600.0` (line 157) | Unchanged |
| `claude_output_format` | `"stream-json"` (line 158) | Unchanged |
| `anthropic_api_key` | `""` (line 159) | Unchanged |
| (new) `runtime` | N/A | `"claude-code"` default |
| (new) `codex_command` | N/A | `"codex"` default |
| (new) `codex_timeout_s` | N/A | `600.0` default |
| (new) `openai_api_key` | N/A | `""` default |

The `WORKER_` env prefix and `SettingsConfigDict` pattern are well-established. Adding 4 new fields follows the existing pattern exactly.

**Note:** The `browser_command` field (line 139) demonstrates the blank-command separability toggle pattern that FR98/S-11 will mirror for `codex_command`.

### Task Driver (`main.py`)

The `run_task()` function (lines 431-983) is the primary integration point:

| Current Pattern | Phase 5 Change |
|---|---|
| `runner = ClaudeCodeRunner(settings)` (line 531) | Replace with `adapter = get_runtime_adapter(settings)` |
| `runner.run(prompt, worktree_path)` (line 610) | Replace with adapter protocol call |
| `runner.terminate_with_grace()` (line 555) | Replace with adapter's `kill()` |
| `ClaudeCodeResult` type annotations | Generalize to runtime-agnostic result type |

**Key observation:** The budget supervisor integration (lines 543-696) already uses a `terminate_callback` pattern -- the callback is wired to `runner.terminate_with_grace()`. This maps cleanly to the adapter's `kill()` method with zero architectural change.

### Event Types (`event_types.py`)

Current state:
- `ensure_registered()` function handles all registrations (lines 178-419)
- `register()` wrapper provides idempotent, collision-tolerant registration
- Phase 4 browser events registered at schema 1.1.0 (lines 391-410)
- `TaskExecutionStartedPayload` registered at 1.0.0, 1.0.1, 1.1.0 (lines 190-193) -- Phase 5 adds 1.2.0

Phase 5 additions needed:
- 3 new event registrations in `ensure_registered()`
- Extended payload imports from `events.payloads`
- Cardinality baseline update (66 -> 69 event types)

### Separability Test Pattern

Existing S-1 through S-10 tests demonstrate the pattern:
- Each test lives in `tests/separability/test_s<n>_<name>.py`
- Uses docker-compose test harness
- Toggles the blank-command field and verifies the MCP member is absent/present
- S-11 (`test_s11_codex_optional.py`) will follow this exact pattern

---

## 5. Gaps and Risks

### BLOCKING Gaps (must resolve before implementation)

| # | Gap | Severity | Resolution |
|---|---|---|---|
| G1 | **Epic numbering not resolved** -- PRD says 26-29, architecture says 23-25 | HIGH | Choose one scheme before `bmad-create-epics-and-stories` |
| G2 | **ADR-0015 not authored** -- multi-runtime adapter protocol ADR is required before Epic 26's first story merges | HIGH | Author and accept before implementation |
| G3 | **ADR-0016 not authored** -- Phase 5 gate ADR is required before any Phase 5 work merges to main | HIGH | Author and accept before implementation |
| G4 | **Epics/stories not decomposed** -- no Phase 5 epic or story files exist | HIGH | Run `bmad-create-epics-and-stories` |
| G5 | **Codex CLI binary not pinned** -- `Dockerfile.base` must pin the `codex` binary with verified checksum (same discipline as Playwright image pinning) | HIGH | Must land before FR90 implementation |

### NON-BLOCKING Gaps (should address but do not gate implementation start)

| # | Gap | Severity | Notes |
|---|---|---|---|
| G6 | `runtime.health_checked` event type is in the PRD but not detailed in the architecture | LOW | PRD ACs are sufficient |
| G7 | FR96 fleet smoke test is in the PRD but not mentioned in the architecture | MEDIUM | Architecture should acknowledge; test will follow existing integration pattern |
| G8 | `runtime_budget_overrides` latent scaffold is in the architecture but not in the PRD | LOW | Architecture-level detail; acceptable |
| G9 | `get_context_summary()` adapter method mentioned in architecture handoff flow but not in the `RuntimeAdapter` protocol definition | LOW | Clarify in ADR-0015 or add to protocol |
| G10 | No `sprint-status.yaml` found -- sprint tracking infrastructure may need setup | LOW | Phase 4 may have used a different tracking mechanism |
| G11 | `CodexResult` dataclass not defined in architecture (only `ClaudeCodeResult` and `RuntimeResult` mentioned) | LOW | Define during implementation following existing pattern |

### Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Codex CLI JSONL output format differs from documented assumptions | MEDIUM | HIGH (adapter parse failure) | Spike: validate Codex `--json` output shape before Epic 26 starts |
| R2 | Codex CLI not available in CI | MEDIUM | MEDIUM (integration tests skip) | FR96 AC already requires CI availability; environment setup needed |
| R3 | Structural subtyping (Protocol) diverges from `ClaudeCodeRunner`'s actual method signatures | LOW | MEDIUM (isinstance check fails) | Contract test in Epic 26 story 1 |
| R4 | Refactoring `run_task` to use adapter introduces regression in existing Claude Code path | LOW | HIGH (Phase 1-4 regression) | Existing test suite is the backward-compat gate (FR89 AC) |
| R5 | Per-runtime allowlist maintenance burden as runtimes grow | LOW | LOW | P5-I1 CI gate catches cross-runtime leakage; closed enum bounds cardinality |
| R6 | Handoff prompt context loss between runtimes | MEDIUM | MEDIUM | Best-effort by design (PRD D5); context summary is operator-visible via events |

---

## 6. Summary and Recommendations

### Overall Readiness Status: CONDITIONALLY READY

The Phase 5 planning artifacts are well-structured, the codebase is architecturally ready, and Phase 4 is fully complete. The architecture's `RuntimeAdapter` protocol maps cleanly to the existing `ClaudeCodeRunner` structure -- the refactoring is structural, not behavioral. The credential isolation architecture builds directly on the proven `_CHILD_ENV_ALLOWLIST` discipline from G-SEC-2.

**5 blocking conditions** must be resolved before implementation can begin.

### Critical Issues Requiring Action

1. **Resolve epic numbering** -- Choose between PRD's 4-epic model (Epic 26-29) and architecture's 3-epic model (Epic 23-25). Recommendation: use the PRD's 4-epic structure (26-29) for continuity from Phase 4's Epics 20-22, incorporating the architecture's detailed protocol/factory/allowlist design within those epics.

2. **Author ADR-0015** (`docs/adr/0015-multi-runtime-adapter.md`) -- Must define the `RuntimeAdapter` protocol, factory function, credential isolation architecture, and per-runtime allowlists. Change status from proposed to accepted before Epic 26's first story merges.

3. **Author ADR-0016** (`docs/adr/0016-phase-5-gate.md`) -- Must define Phase 5 acceptance criteria and open Phase 5 for main-branch merges. Change status from proposed to accepted before any Phase 5 work merges.

4. **Run `bmad-create-epics-and-stories`** -- Decompose Phase 5 scope into formal epics with full story specs. All 10 FRs (FR89-FR98) must have a traceable story home.

5. **Pin Codex CLI binary** -- Add pinned `codex` binary to `Dockerfile.base` with verified checksum (same supply-chain discipline as Playwright Docker image pinning per Phase 4).

### Recommended Next Steps

1. **Resolve epic numbering** (PRD 26-29 vs architecture 23-25) with operator input
2. **Run `bmad-create-epics-and-stories`** to decompose Phase 5 scope
3. **Author ADR-0015 and ADR-0016** (both proposed -> accepted)
4. **Spike: validate Codex CLI `--json` output shape** (de-risk R1)
5. **Pin `codex` binary in `Dockerfile.base`** (de-risk G5)
6. **Run `bmad-sprint-planning`** to generate sprint tracking
7. **Dev Epic 26 Story 1** -- define `RuntimeAdapter` protocol + factory + refactor `ClaudeCodeRunner` to satisfy protocol

### Quality Summary

| Dimension | Score | Notes |
|---|---|---|
| FR completeness (ACs) | 10/10 | Every FR has testable acceptance criteria |
| NFR coverage | 4/4 | NFR-R10, NFR-O13, NFR-M10, NFR-S14 all testable |
| Phase 4 completion | 14/14 | All Phase 4 ship-blockers green |
| Deferred work blockers | 0 blocking | No deferred items block Phase 5 |
| Cross-document alignment | PARTIAL | Epic numbering + FR96/FR98 gaps need resolution |
| Codebase readiness | HIGH | Runner, config, event types all align with Phase 5 design |
| Invariant preservation | 6/6 carried | All Phase 1-4 invariants explicitly preserved |
| New invariants (P5-I1/I2/I3) | 3/3 | Well-motivated, testable, CI-gated |
| ADR readiness | 0/2 authored | ADR-0015 + ADR-0016 must be authored |
| Epic decomposition | PENDING | Must be completed before implementation |
| Supply chain (Codex binary) | NOT STARTED | Must pin before FR90 implementation |

**Verdict:** Phase 5 planning is **CONDITIONALLY READY** for implementation. The 5 blocking conditions (epic numbering, 2 ADRs, epic decomposition, Codex binary pinning) are all achievable in a single pre-implementation sprint. All other readiness criteria are met.

---

*Assessment by BMad check-implementation-readiness, 2026-06-07.*
