---
stepsCompleted: [document-discovery, prd-analysis, epic-coverage-validation, ux-alignment, epic-quality-review, final-assessment]
documentsAssessed:
  - _bmad-output/planning-artifacts/phase-4-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-4-architecture-amendment.md
  - docs/adr/0013-playwright-mcp-transport.md
  - docs/adr/0014-phase-4-gate.md
verdict: CONDITIONALLY-READY
---

# Implementation Readiness Assessment Report

**Date:** 2026-06-05
**Project:** oh-my-bmad
**Phase:** Phase 4 — Browser Automation Plane
**Assessor:** BMad check-implementation-readiness (automated)

## Document Discovery

### Files Found

**PRD Documents:**
- `phase-4-prd-amendment.md` (30 KB, 2026-06-05) — Phase 4 extension FR78-FR88 + NFRs
- `prd.md` (120 KB, 2026-06-03) — canonical Phase 1-3 PRD (baseline reference)

**Architecture Documents:**
- `phase-4-architecture-amendment.md` (29 KB, 2026-06-05) — Phase 4 extension P4-I1/I2/I3 + Browser Worker
- `architecture.md` (133 KB, 2026-06-03) — canonical Phase 1-3 architecture (baseline reference)

**ADR Documents:**
- `docs/adr/0013-playwright-mcp-transport.md` (10 KB, 2026-06-05) — transport decision
- `docs/adr/0014-phase-4-gate.md` (10 KB, 2026-06-05) — Phase 4 gate

**Epics/Stories:**
- `epics.md` (196 KB) — Phase 1-3 epics (Epic 1-19, all done)
- **Phase 4 epics NOT YET CREATED** — decomposition pending

**UX Documents:** None (backend MCP server — no UX surface)

### Issues
- ⚠️ Phase 4 epics (20-22) not yet decomposed — this is the expected pre-implementation gate

---

## PRD Analysis

### Functional Requirements

| FR | Title | Tier | Has ACs? |
|---|---|---|---|
| FR78 | Browser server scaffold (Epic 20) | N/A (infrastructure) | ✅ 7 ACs |
| FR79 | Navigation tools (Epic 21) | Tier-1 | ✅ 5 ACs |
| FR80 | Interaction tools (Epic 22) | Tier-2 | ✅ 5 ACs |
| FR81 | Screenshot capture (Epic 22) | Tier-1 | ✅ 4 ACs |
| FR82 | JS execution browser_evaluate (Epic 23) | Tier-3 | ✅ 5 ACs |
| FR83 | Tab management (Epic 23) | Tier-1/Tier-2 | ✅ 4 ACs |
| FR84 | Session isolation (Epic 24) | N/A (policy) | ✅ 5 ACs |
| FR85 | Origin control (Epic 24) | N/A (policy) | ✅ 5 ACs |
| FR86 | Browser events (cross-cutting) | N/A (telemetry) | ✅ 4 ACs |
| FR87 | Container sandboxing (Epic 25) | N/A (infrastructure) | ✅ 5 ACs |
| FR88 | Separability S-10 (Epic 20) | N/A (separability) | ✅ 4 ACs |

**Total FRs:** 11 (FR78-FR88)

### Non-Functional Requirements

| NFR | Category | Maps to FRs | Testable? |
|---|---|---|---|
| NFR-B1 | Dependency discipline | FR78 | ✅ license gate |
| NFR-B2 | Session isolation | FR84 | ✅ isolation test |
| NFR-B3 | Artifact integration | FR81 | ✅ round-trip test |
| NFR-B4 | Trace ID enforcement | FR78-FR88 | ✅ AST gate + contract tests |
| NFR-B5 | Tier-3 denial gate | FR82 | ✅ negative denial test |
| NFR-O12 | Event cardinality | FR86 | ✅ cardinality ratchet |
| NFR-M9 | Browser separability | FR88 | ✅ S-10 test |
| NFR-S13 | Supply chain + sandbox | FR87 | ✅ verify-images + audit test |
| NFR-R9 | Session cleanup | FR87 | ✅ cleanup test |

**Total NFRs:** 9

### Additional Requirements

- **Ship-blocker checklist:** 14 items, each with verification method and owner
- **Out-of-scope discipline:** 8 items explicitly deferred (browser-harness, remote-MCP, second CLI agent, etc.)
- **Phase boundary discipline:** every epic carries `phase: 4`, no merges until gate ADR accepted
- **Preserved invariants:** 6 carry-forward invariants from Phases 1-3 explicitly stated

---

## Epic Coverage Validation

**Status:** Phase 4 epics NOT YET DECOMPOSED — this is the expected gate.

The PRD amendment specifies 6 logical epics (Epic 20-25) in its Sequencing table. The architecture amendment specifies 3 epics (Epic 20-22). Both agree on Epic 20 (scaffold) as the entry point.

### Coverage Matrix (PRD FRs → Proposed Epic Mapping)

| FR | PRD Epic | Architecture Epic | Status |
|---|---|---|---|
| FR78 | Epic 20 (ω scaffold) | Epic 20 | ✅ Aligned |
| FR79 | Epic 21 (ω-2 navigation) | Epic 21 | ✅ Aligned |
| FR86 | Epic 21 (events cross-cutting) | Epic 21 | ✅ Aligned |
| FR80 | Epic 22 (ω-3 interaction) | Epic 22 | ✅ Aligned |
| FR81 | Epic 22 (ω-4 screenshot) | Epic 22 | ✅ Aligned |
| FR82 | Epic 23 (ω-5 JS execution) | Epic 22 (CI hardening) | ⚠️ Structural difference |
| FR83 | Epic 23 (ω-6 tab mgmt) | Epic 22 (CI hardening) | ⚠️ Structural difference |
| FR84 | Epic 24 (ω-7 session isolation) | — (part of Epic 20/22) | ⚠️ Structural difference |
| FR85 | Epic 24 (ω-8 origin control) | — (part of Epic 20/22) | ⚠️ Structural difference |
| FR87 | Epic 25 (ω-10 container sandbox) | — (part of Epic 22) | ⚠️ Structural difference |
| FR88 | Epic 20 (S-10 separability) | Epic 20 | ✅ Aligned |

**Coverage:** All 11 FRs have an epic home in both proposals. The difference is granularity (PRD: 6 epics, Architecture: 3 epics).

**Resolution needed:** Choose one decomposition. The architecture's 3-epic model is tighter but collapses policy (isolation, origin control, container sandboxing) into existing epics rather than giving them dedicated epics. The PRD's 6-epic model provides better isolation of concerns.

**Recommendation:** Use the architecture's 3-epic structure for initial decomposition (simpler, fewer epic gates), but ensure all 11 FRs appear as stories within those 3 epics. This aligns with the existing Phase 3 pattern where Epics 15-19 each had 5-6 stories covering multiple FRs.

---

## UX Alignment Assessment

### UX Document Status: Not Applicable

Phase 4 adds a backend MCP server (browser-mcp) — a stdio subprocess with no user interface. The browser plane is a worker/orchestrator tool, not an operator-facing control surface. No UX documentation is required or implied.

### Warnings: None

The PRD explicitly states in out-of-scope: "Browser-based control surface. The browser plane is a worker/orchestrator tool, not an operator-facing control surface. Web dashboards remain Phase 7 scope."

---

## Epic Quality Review

**Note:** Phase 4 epics have not been formally decomposed into stories yet. This review assesses the *proposed* epic structure from the architecture amendment.

### Proposed Epic Structure (Architecture Amendment)

**Epic 20 — Browser MCP server scaffold (ADR-0013 transport + ADR-0010 recipe)**
- ✅ Delivers user value: "Worker can invoke browser tools via MCP"
- ✅ Independent: standalone scaffold, no dependency on later epics
- ✅ ADR-0010 recipe: well-mapped (steps 1-8 documented)
- ⚠️ Scope is broad: FR78 + FR88 + FR84 + FR85 + FR87 all touch Epic 20 per architecture

**Epic 21 — Browser events + metrics**
- ✅ Delivers user value: "Browser events observable on the spine"
- ✅ Independent: event registration is additive
- ✅ Well-bounded: 6 event types + cardinality regression
- ℹ️ Thin epic — may merge into Epic 20 as stories

**Epic 22 — Browser CI hardening**
- ⚠️ Partially a "technical milestone" epic (CI gates, not user-facing features)
- ⚠️ Collapses FR82 (Tier-3 JS execution), FR83 (tab management), FR87 (container sandboxing) into one epic
- ℹ️ The PRD's 6-epic model separates these concerns more cleanly

### Quality Issues

**🟠 Major: Epic 22 has mixed concerns**
The architecture's Epic 22 ("CI hardening") conflates:
1. Security-sensitive tool implementation (browser_evaluate — Tier-3)
2. Container sandboxing (Docker configuration)
3. CI gate assertions (test infrastructure)

These are three different risk profiles. Mixing them means a failure in container sandboxing blocks the Tier-3 tool implementation.

**Recommendation:** If using 3-epic model, rename Epic 22 to "Browser tools + CI hardening" and organize stories by risk:
- Story 1: Container sandbox (FR87) — infrastructure, low risk
- Story 2: Tab management (FR83) — Tier-1/2 tools, medium risk
- Story 3: JS execution (FR82) — Tier-3 tool, high risk
- Story 4: Session isolation + origin control (FR84, FR85) — policy, composes with all above
- Story 5: CI hardening (P4-I1/I2/I3 tests, separability, cardinality)

**🟡 Minor: Epic 21 is very thin**
Epic 21 (browser events) covers only FR86 — 6 event type registrations + cardinality regression. This is typically 1-2 stories. Consider merging into Epic 20 as event-registration stories.

---

## Cross-Document Alignment (Post-Reconciliation)

The PRD and architecture amendments were reconciled during this session (2026-06-05) based on adversarial review findings. Reconciliation fixes applied:

| Finding | Resolution | Status |
|---|---|---|
| ADR numbering (0013 vs 0014 for gate) | Created 0013=transport, 0014=gate | ✅ Fixed |
| `--no-sandbox` contradiction | Both docs now agree: Docker sandbox sufficient, Chromium sandbox enabled | ✅ Fixed |
| Dockerfile conflict (own image vs base image) | Both docs now agree: code ships in base image, Playwright is runtime subprocess | ✅ Fixed |
| Event naming mismatch (7 vs 5 events, different names) | Reconciled to 6 events with unified names | ✅ Fixed |
| `_connect` non-existent parameter | Removed `browser_env`, uses allowlist pattern | ✅ Fixed |
| `browser_file_upload` in Tier-3 but deferred | Marked as deferred in both docs | ✅ Fixed |
| Stale ship-blocker #14 (G-SEC-2) | Changed to verified-prerequisite | ✅ Fixed |
| Tab create/close tier contradiction (Tier-3 vs Tier-2) | Clarified as Tier-2 in both docs | ✅ Fixed |

---

## Summary and Recommendations

### Overall Readiness Status: CONDITIONALLY READY

The Phase 4 planning artifacts are well-structured, thoroughly specified, and cross-document alignment has been verified after reconciliation. The **only blocking condition** is the absence of formal epic/story decomposition.

### Critical Issues Requiring Action

1. **Epic decomposition required** — Phase 4 epics (20-22) must be formally created via `bmad-create-epics-and-stories` before any implementation begins. All 11 FRs (FR78-FR88) must have a traceable story home.

2. **Epic structure decision** — Choose between the PRD's 6-epic model (Epic 20-25) and the architecture's 3-epic model (Epic 20-22). Both cover all FRs. Recommendation: use the architecture's 3-epic model for simplicity, ensuring all FRs map to stories within those epics.

### Recommended Next Steps

1. **Run `bmad-create-epics-and-stories`** to decompose Phase 4 scope into Epics 20-22 with full story specs
2. **Run `bmad-sprint-planning`** to generate sprint-status.yaml entries and update `current_phase: 4`
3. **Accept ADR-0013 and ADR-0014** — change status from `proposed` to `accepted` before Epic 20's first story merges
4. **Dev Epic 20 Story 1** — scaffold the browser-mcp workspace member (ADR-0010 recipe step 1)

### Quality Summary

| Dimension | Score | Notes |
|---|---|---|
| FR completeness (ACs) | ✅ 11/11 | Every FR has testable acceptance criteria |
| NFR coverage | ✅ 9/9 | All NFRs map to specific FRs with test methods |
| Cross-document alignment | ✅ Resolved | 8 contradictions reconciled |
| Ship-blocker sufficiency | ✅ 14/14 | Comprehensive checklist with verification methods |
| Out-of-scope discipline | ✅ 8 items | Well-bounded, no scope creep risk |
| Phase invariant preservation | ✅ 6/6 | All Phase 1-3 invariants explicitly carried forward |
| New invariants (P4-I1/I2/I3) | ✅ 3/3 | Well-motivated, testable, CI-gated |
| Epic decomposition | ⏳ Pending | **Blocking** — must be completed before implementation |
| ADR readiness | ✅ 2/2 | ADR-0013 + ADR-0014 authored (proposed status) |

**Verdict:** Phase 4 planning is **CONDITIONALLY READY** for implementation. The single condition is formal epic/story decomposition. All other readiness criteria are met.

— *Assessment by BMad check-implementation-readiness, 2026-06-05.*
