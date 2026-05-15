---
report_type: implementation_readiness
phase: 2
date: 2026-05-15
author: R2d2
workflow: bmad-check-implementation-readiness
verdict: READY_TO_IMPLEMENT
companion_documents:
  - _bmad-output/planning-artifacts/phase-2-brainstorming.md
  - _bmad-output/planning-artifacts/prd.md (Phase 2 Scope Extension)
  - _bmad-output/planning-artifacts/architecture.md (Phase 2 Architecture Extension)
  - _bmad-output/planning-artifacts/epics.md (Phase 2 Epics — Observability Phase)
  - docs/adr/0003-phase-2-gate.md
---

# Phase 2 implementation-readiness report

> Verifies that Phase 2 planning artifacts (PRD amendment + architecture amendment + epics decomposition + Phase 2 gate ADR) are mutually aligned and ready for `bmad-sprint-planning`. Modeled after the Phase 1 report at [`implementation-readiness-report-2026-04-22.md`](./implementation-readiness-report-2026-04-22.md), but lighter — Phase 2 is incremental, not greenfield, so the gate focuses on additivity, cross-amendment alignment, and Phase 1 regression-freedom.

## Step 1 — Document discovery

All Phase 2 planning artifacts present and readable:

| Artifact | Path | Lines | Status |
|---|---|---|---|
| Brainstorming session | `_bmad-output/planning-artifacts/phase-2-brainstorming.md` | 339 | ✅ commit `687e3d6` |
| PRD amendment | `_bmad-output/planning-artifacts/prd.md` §"Phase 2 Scope Extension" | +141 | ✅ commit `2812b97` |
| Architecture amendment | `_bmad-output/planning-artifacts/architecture.md` §"Phase 2 Architecture Extension" | +430 | ✅ commit `bec8081` |
| Epics + stories | `_bmad-output/planning-artifacts/epics.md` §"Phase 2 Epics" | +324 | ✅ commit `4cc6309` |
| ADR-0003 Phase 2 gate | `docs/adr/0003-phase-2-gate.md` | — | ✅ this commit |
| This readiness report | `_bmad-output/planning-artifacts/implementation-readiness-report-phase-2-2026-05-15.md` | — | ✅ this commit |

**Discovery verdict:** ✅ all artifacts exist; total Phase 2 planning delta = **1,234 lines of new planning content** across 5 commits.

---

## Step 2 — PRD amendment analysis

### Functional Requirements added (19 across 6 epics)

| Epic | FRs | Coverage |
|---|---|---|
| Epic 8 (γ supply-chain) | FR53, FR54, FR55, FR56, FR56a | 5 FRs |
| Epic 9 (α trace_id) | FR57, FR58, FR59, FR59a | 4 FRs |
| Epic 10 (β metrics-subscriber) | FR60, FR61, FR62, FR62a | 4 FRs |
| Epic 11 (ξ approval inbox + HMAC) | FR63, FR64, FR65, FR65a | 4 FRs |
| Epic 12 (κ budget enforcement) | FR66, FR67, FR68, FR68a | 4 FRs |
| Epic 13 (δ litestream) | FR69, FR70, FR71, FR71a | 4 FRs |
| **Total** | FR53–FR71a | **25 FRs** (including `a`-suffix sub-clauses) |

**Capability-level vs implementation-level check:** every FR is capability-level (no specific framework names, file paths, or message formats inlined where avoidable). The Phase 1 PRD §"Completeness / Altitude Record" rule is preserved.

**FR completeness assessment:** PASS. Every Phase 2 capability called out in the architecture amendment has at least one FR.

### Non-Functional Requirements added (9 across 3 categories)

| Category | NFRs | Coverage |
|---|---|---|
| Observability | NFR-O7, NFR-O8, NFR-O9, NFR-O10 | 4 NFRs |
| Security | NFR-S9, NFR-S10, NFR-S11 | 3 NFRs |
| Reliability | NFR-R7, NFR-R8 | 2 NFRs |
| **Total** | | **9 NFRs** |

**NFR-O10 is the critical one** — preserves Phase 1's NFR-O1 observability primacy. Explicitly forecloses the "OTel everywhere" anti-pattern: "Metrics + traces are *derived projections* of the event log, not parallel instrumentation paths."

**NFR completeness assessment:** PASS. Every Phase 2 NFR has a measurable target and either references a KPI or specifies one.

### Phase 1 invariants explicitly preserved

The PRD amendment §"Phase 2 Architectural Commitments" enumerates 5 Phase-1 invariants that Phase 2 **does not** modify:

- ✅ FR26 single-writer (every Phase 2 service is a read-only subscriber).
- ✅ MCP stdio-only (remote-MCP deferred to Phase 3).
- ✅ Envelope immutability (`schema_version` bumps 1.0.0 → 1.1.0 *additively* — adds `trace_id` field only; no existing fields modified).
- ✅ NFR-M3 additive-only schema evolution (no breaking changes; one additive field is the only delta).
- ✅ No `anthropic` SDK in platform code (worker-wrapper boundary unchanged).

**Preservation assessment:** PASS.

---

## Step 3 — Architecture amendment analysis

### Phase 2 architectural invariants (6 invariants P2-I1 through P2-I6)

| ID | Invariant | Enforcement |
|---|---|---|
| P2-I1 | Read-only-subscriber rule (all Phase 2 additions read-only consumers) | CI gate via existing `scripts/checks/check_imports.py` + extended to include new workspace member `metrics-subscriber` |
| P2-I2 | Envelope schema bumps once (1.0.0 → 1.1.0); only `trace_id` field added | Story 9.7 + AST check `scripts/checks/check_trace_id_required.py` |
| P2-I3 | Metrics + traces are derived projections, not parallel instrumentation | Grep gate: `tests/arch/test_no_otel_in_services.py` |
| P2-I4 | MCP transport stdio-only | Existing static-analysis check; unchanged from Phase 1 |
| P2-I5 | No new public-network ingress | Compose-file review + manual operator checklist |
| P2-I6 | Image-pull gate via cosign + SLSA + SBOM | `just verify-images` + ADR-0008 |

**Invariant assessment:** PASS. Every invariant has an enforcement mechanism, not just a documentation rule.

### Wiring decisions documented

The architecture amendment includes:

- ✅ Envelope schema 1.0.0 → 1.1.0 cutover plan (6-story walk: optional → required + backfill).
- ✅ `trace_id` propagation mermaid diagram (per-layer derivation + non-ambient propagation).
- ✅ `metrics-subscriber` topology (compose-stack position, cursor durability, cardinality discipline, separability test S-4).
- ✅ HMAC signing sequence diagram (key isolation, offline verification, rotation).
- ✅ litestream sidecar topology (shared-read WAL, opt-in via `OMB_LITESTREAM_CONFIG_PATH`).
- ✅ Per-task budget supervisor (lifespan task in worker-wrapper, no new workspace member).
- ✅ Supply-chain pipeline (`release.yml` additions + `just verify-images` recipe).
- ✅ Dependency direction (Phase 2 additions preserve service-separability gate).
- ✅ 6 new event types catalogued with owning epic.
- ✅ 8 new CI gates with failure modes.
- ✅ Phase 2 risk register (7 risks scored by likelihood × impact + mitigations).
- ✅ Phase 3 forward-references (deliberate non-decisions).

**Wiring assessment:** PASS. Every Phase 2 epic has a concrete code-touch sketch + ADR placeholder.

### ADRs forward-referenced (and their owning epic)

| ADR | Topic | Owning Epic | Status |
|---|---|---|---|
| ADR-0003 | Phase 2 gate | (this gate) | ✅ `accepted` 2026-05-15 |
| ADR-0004 | `trace_id` propagation policy + cutover | Epic 9 | ⏳ to be drafted before Epic 9 starts |
| ADR-0005 | metrics-subscriber as derived projection | Epic 10 | ⏳ to be drafted before Epic 10 starts |
| ADR-0006 | HMAC non-repudiation + key rotation | Epic 11 | ⏳ to be drafted before Epic 11 starts |
| ADR-0007 | litestream WAL replication | Epic 13 | ⏳ to be drafted before Epic 13 starts |
| ADR-0008 | cosign + SLSA + CycloneDX triumvirate | Epic 8 | ⏳ to be drafted before Epic 8 starts |

**ADR readiness:** ADR-0003 is `accepted`, sufficient for sprint planning. ADR-0004 through ADR-0008 must be drafted as `proposed` before their owning epic's first story merges to `main` (per project-context.md Cat 6 rule "If you intend to violate a rule, file an ADR first — `proposed` status — *before* writing code that violates it"; applies here as "If you intend to make a load-bearing decision, file an ADR first.")

---

## Step 4 — Epic coverage validation (FR-to-story matrix)

### FR Coverage Matrix (25 FRs × 32 stories)

| FR | Owning Epic | Story | Coverage |
|---|---|---|---|
| FR53 (cosign sign) | Epic 8 | 8.3 | ✅ |
| FR54 (SLSA L2) | Epic 8 | 8.2 | ✅ |
| FR55 (SBOM generate + attach) | Epic 8 | 8.1, 8.4 | ✅ |
| FR56 (digest-pin) | Epic 8 | (env-var convention; touched in 8.5 deploy recipe) | ✅ |
| FR56a (verify-images deploy gate) | Epic 8 | 8.5 | ✅ |
| FR57 (envelope schema bump) | Epic 9 | 9.1 (optional), 9.7 (required + bump) | ✅ |
| FR58 (binding sites: HTTP / TG / console / MCP) | Epic 9 | 9.2, 9.3, 9.4, 9.5 | ✅ |
| FR59 (worker subprocess --trace-id flag) | Epic 9 | 9.6 | ✅ |
| FR59a (/trace operator query) | Epic 9 | 9.7 | ✅ |
| FR60 (metrics-subscriber scaffold + tail) | Epic 10 | 10.1, 10.2 | ✅ |
| FR61 (FastAPI /metrics endpoint) | Epic 10 | 10.3 | ✅ |
| FR62 (core counter/gauge/histogram set) | Epic 10 | 10.4 | ✅ |
| FR62a (separability test S-4) | Epic 10 | 10.6 | ✅ |
| FR63 (/approvals pinned thread) | Epic 11 | 11.3 | ✅ |
| FR64 (HMAC sign + task.approval_signed) | Epic 11 | 11.1, 11.2 | ✅ |
| FR65 (just verify-approval offline) | Epic 11 | 11.4 | ✅ |
| FR65a (key rotation + key.rotated event) | Epic 11 | 11.5 | ✅ |
| FR66 (budget supervisor SIGTERM 5s) | Epic 12 | 12.1 | ✅ |
| FR67 (task.budget_enforcement_triggered event) | Epic 12 | 12.2 | ✅ |
| FR68 (/approve --override budget) | Epic 12 | 12.3 | ✅ |
| FR68a (per-task policy + .env defaults) | Epic 12 | 12.4 | ✅ |
| FR69 (litestream sidecar in compose) | Epic 13 | 13.1 | ✅ |
| FR70 (OMB_LITESTREAM_CONFIG_PATH + template) | Epic 13 | 13.2 | ✅ |
| FR71 (just restore-from-litestream recipe) | Epic 13 | 13.3 | ✅ |
| FR71a (read-only sidecar preserves FR26) | Epic 13 | 13.1 (compose mount mode) + 13.3 (restore flow) | ✅ |

**Coverage statistics:**

- 25 FRs total; 25 with at least one owning story → **100%** FR coverage.
- 0 unmapped FRs.
- 32 stories total; every story carries at least one FR citation (verified by inspection of the epic decomposition).

**Coverage assessment:** PASS.

### NFR coverage

| NFR | Verified by |
|---|---|
| NFR-O7 (trace_id correlation across services) | Epic 9 Story 9.7 + integration test in `tests/integration/test_trace_id_chain.py` |
| NFR-O8 (metrics-subscriber /metrics p95 <100ms) | Epic 10 Story 10.3 + CI benchmark on fixed runner |
| NFR-O9 (live reasoning breadcrumb view) | (deferred: 1-2 supporting stories in Epic 11 or later — placeholder, not blocking sprint planning) |
| NFR-O10 (typed-events primary; metrics derived) | P2-I3 architectural invariant + `tests/arch/test_no_otel_in_services.py` |
| NFR-S9 (image-signature verification) | Epic 8 Stories 8.1–8.5 |
| NFR-S10 (HMAC key isolation in .env only) | Epic 11 Story 11.1 + `tests/integration/test_hmac_key_isolation.py` |
| NFR-S11 (SBOM attestation verifiable + license-scan-at-publish) | Epic 8 Stories 8.1, 8.4 + release.yml license-scan addition |
| NFR-R7 (replication lag <30s p95) | Epic 13 Story 13.4 + just litestream-lag-check |
| NFR-R8 (budget enforcement latency p99 <5s) | Epic 12 Story 12.1 + integration test |

**NFR coverage:** 8/9 fully covered. **NFR-O9 has a deferred placeholder** — the live-reasoning-breadcrumb view is documented as a Phase 2 wish in the brainstorming artifact and PRD but does not have a dedicated story in the Epic 8–13 decomposition. Either (a) add a story to Epic 11 or (b) flag NFR-O9 as Phase 2.5 / Phase 3 and remove from the Phase 2 ship criterion. **Recommendation:** flag NFR-O9 as Phase 2.5 (operator-felt nice-to-have), not Phase 2 ship-blocker.

---

## Step 5 — UX alignment

**Phase 2 has no UI work.** Telegram + console + (new) `/approvals` pinned thread + `/trace <id>` query. All operator surfaces remain text-based and inherit Phase 1's [`message-design.md`](../../docs/message-design.md) discipline.

**UX alignment assessment:** PASS (trivial — same surfaces as Phase 1).

---

## Step 6 — Phase 2 epic quality review

### A. User-value focus per epic

| Epic | Operator-felt value |
|---|---|
| Epic 8 (γ) | Operator can verify Phase 2 image signatures before deploying — supply-chain trust. |
| Epic 9 (α) | Operator can `/trace <trace-id>` and see the complete causal chain of any past command. |
| Epic 10 (β) | Operator gets a Prometheus-scrapable view of system behavior with zero new instrumentation. |
| Epic 11 (ξ) | Approval requests stop getting lost in chat scrollback; approvals are non-repudiable. |
| Epic 12 (κ) | A runaway worker can't burn the operator's budget without intervention. |
| Epic 13 (δ) | A host loss doesn't lose data; restore drill is rehearsed nightly. |

**Verdict:** every epic delivers operator-felt value. PASS.

### B. Epic independence (per the brainstorming dependency graph)

- Epic 8: hard prerequisite for every other epic's release (must merge first).
- Epic 9: hard prerequisite for Epic 10, 11 (trace_id correlation).
- Epic 10: prerequisite for Epic 12 (metrics make budget enforcement transparent).
- Epic 11: depends on Epic 9. Independent of Epic 10/12/13.
- Epic 12: depends on Epic 9 + Epic 10.
- Epic 13: **orthogonal** — can ship in parallel with anything once Epic 8 is in.

**Dependency assessment:** clean partial order. No cycles. PASS.

### C. Story sizing

| Story-size range | Count |
|---|---|
| S (≤3 days operator attention) | ~22 |
| M (~1 week) | ~10 |
| L (>1 week, requires decomposition) | 0 |

Every story is S or M; no L stories in Phase 2. This is consistent with the project-context.md Cat 6 rule "L stories slice at value boundaries before entering `ready-for-dev`."

**Sizing assessment:** PASS.

### D. Acceptance criteria quality

Every story carries: FR citation, scope statement, acceptance-criteria sentence. The per-epic acceptance gate aggregates story-level AC into a verifiable epic-level claim.

**AC quality assessment:** PASS.

### E. Implementation-readiness checklist (per architecture amendment §"Phase 2 readiness gate exit criteria")

- [x] PRD amendment present.
- [x] Architecture amendment present.
- [x] `bmad-create-epics-and-stories` has decomposed FR53–FR71a into Epic 8–13 stories.
- [ ] Each Phase 2 epic has its `phase: 2` label set in `sprint-status.yaml` ⏳ — **this is the `bmad-sprint-planning` step, executed next.**
- [x] ADR-0003 (Phase 2 gate) authored as `status: accepted`.
- [ ] ADR-0004 through ADR-0008 drafted as `status: proposed` ⏳ — **drafts due before each owning epic's first story merges.** Not a blocker for sprint planning.
- [x] `deferred-work.md` reviewed; items superseded by Phase 2 marked `killed: superseded_by_phase_2_epic_<n>` (to be verified by sprint-planning skill).

**Readiness assessment:** PASS for sprint-planning entry. Two pending items (ADR-0004–0008 drafting, deferred-work supersession marking) are sprint-planning prerequisites for individual stories, not gate blockers.

---

## Step 7 — Phase 1 invariant regression-freedom

Phase 2's `Phase 2 Architectural Commitments` section in the PRD amendment lists 5 Phase 1 invariants explicitly preserved:

| Phase 1 invariant | Phase 2 stance |
|---|---|
| FR26 single-writer | Unchanged. Every Phase 2 service is a read-only subscriber. |
| MCP stdio-only | Unchanged. Remote-MCP deferred to Phase 3. |
| Envelope immutability | Preserved. The 1.0.0 → 1.1.0 bump is additive (one new field, no modifications). |
| NFR-M3 additive-only schema | Preserved. No breaking changes; no migrator container required for this bump (the additive field is the only delta). |
| No anthropic SDK in platform code | Unchanged. Worker-wrapper boundary preserved. |

**Phase 1 regression-freedom assessment:** PASS.

**Test-suite regression gates** (must pass at every Phase 2 epic boundary):

- `tests/separability/` (S-1 through S-4 once Epic 10 ships S-4)
- `tests/crash-injection/`
- `tests/idempotency/` (100× concurrent retry with trace_id-extended envelope)
- `tests/contract/` (forward-compat fixtures for the 6 new event types)
- `tests/arch/` (single-writer, separability, transport, no-anthropic-outside-worker)
- `tests/replay/` (byte-for-byte equivalence after trace_id migration)

---

## Step 8 — Cross-amendment alignment matrix

This is the load-bearing alignment check. Inconsistencies between PRD / architecture / epics would surface as implementation rework later.

| Claim | PRD | Architecture | Epics |
|---|---|---|---|
| 6 epics in Phase 2 (Epic 8–13) | ✅ §"Phase 2 Sequencing" | ✅ §"Implementation handoff for Phase 2" | ✅ §"Phase 2 Epic Summary" |
| Schema bump 1.0.0 → 1.1.0 is additive (trace_id only) | ✅ FR57 + §"Phase 2 Architectural Commitments" | ✅ §"Envelope schema migration" | ✅ Story 9.7 |
| metrics-subscriber is a new workspace member | ✅ FR60 | ✅ §"metrics-subscriber topology" | ✅ Story 10.1 |
| litestream sidecar is opt-in via OMB_LITESTREAM_CONFIG_PATH | ✅ FR70 | ✅ §"litestream sidecar topology" | ✅ Story 13.2 |
| HMAC key isolation: .env only | ✅ NFR-S10 | ✅ §"HMAC signing flow for approvals" | ✅ Story 11.1 + acceptance gate |
| Per-task budget supervisor inside worker-wrapper (no new workspace member) | ✅ FR66 | ✅ §"Per-task budget enforcement supervisor" | ✅ Story 12.1 |
| 6 new event types at schema_version 1.1.0 | ✅ implicit in FR54–FR71a | ✅ §"New event types added in Phase 2" (table) | ✅ Stories 8.6, 11.2, 12.2, 12.3, 11.5, 13.4 |
| 8 new CI gates | (implied by NFRs) | ✅ §"CI gate additions" (table) | ✅ stories carry the AC for each |
| Phase 1 invariants preserved | ✅ §"Phase 2 Architectural Commitments" | ✅ P2-I1, P2-I2, P2-I4, P2-I5, P2-I6 | ✅ ship-blocker checklist §"Phase 1 invariants regression-free" |
| ADR-0003 through ADR-0008 forward-referenced | (mentioned in §"Amendment Traceability") | ✅ embedded in each section | ✅ in each epic's acceptance gate |

**Cross-amendment alignment:** PASS. Every load-bearing claim appears in all three amendments with consistent terminology.

### Drift hazards noted (but not blocking)

- **NFR-O9 has no dedicated story.** Flag this for sprint-planning decision: either add a story to Epic 11 or move NFR-O9 to Phase 2.5.
- **FR56 (digest-pin) is touched by Epic 8 but no dedicated story owns the `OMB_IMAGE_DIGEST_<service>` `.env.example` documentation update.** Recommend folding into Story 8.5 acceptance criteria explicitly.
- **The `metrics-subscriber` separability test S-4 (Story 10.6) is the first new separability test since Phase 1.** Worth a half-day investigation during Story 10.6 implementation to verify the existing separability harness extends cleanly to the new subscriber, OR to surface harness changes as a sub-story.

These three items are noted as **sprint-planning inputs**, not readiness-gate blockers.

---

## Step 9 — Gap analysis

| Gap | Severity | Resolution |
|---|---|---|
| NFR-O9 live-breadcrumb view has no story | LOW | Flag as Phase 2.5 in sprint planning; do not block Phase 2 ship on it. |
| FR56 digest-pin operator config not explicitly story-owned | LOW | Fold into Story 8.5 AC during sprint planning. |
| Separability harness might need extension for S-4 | MEDIUM (potential half-day surprise during Story 10.6) | Allow buffer in Epic 10 estimate; surface as a sub-story if harness changes are substantial. |
| ADR-0004–0008 drafting | LOW | Not a Phase-2-gate blocker; due before each owning epic's first PR merges to main. |
| Deferred-work.md supersession marking | LOW | `bmad-sprint-planning` will catch this; not a readiness blocker. |

**No HIGH or CRITICAL gaps.** Three LOW + one MEDIUM. Phase 2 is **ready to implement**.

---

## Step 10 — Verdict

> **Verdict: READY_TO_IMPLEMENT.**
>
> Phase 2 planning artifacts (PRD amendment + architecture amendment + epics decomposition + ADR-0003 Phase 2 gate) are mutually aligned, additivity-preserving relative to Phase 1, and decomposed into 32 stories with 100% FR coverage and clean dependency partial-order.
>
> The next BMad workflow step is `bmad-sprint-planning` to populate `sprint-status.yaml` with the 32 Phase 2 stories at initial state `pending` (or `ready-for-dev` where prerequisites are already met). After sprint planning, Epic 8 Story 8.1 may begin implementation.
>
> Five forward-referenced ADRs (ADR-0004 through ADR-0008) must be drafted as `status: proposed` before their owning epic's first story merges to `main`. This is a per-epic gate, not a sprint-planning gate.

### Recommendations for sprint planning

1. **Open Epic 8 first.** Cheapest, ~3 days, lands the supply-chain gate that hardens every later release.
2. **NFR-O9 decision** — flag as Phase 2.5 (recommended) or add a Story 11.6 to Epic 11.
3. **Story 8.5 AC** — explicitly include `OMB_IMAGE_DIGEST_<service>` env-var documentation update.
4. **Story 10.6 buffer** — allow ½ day for separability harness extension if needed.
5. **ADR-0004 (trace_id propagation policy)** — start drafting as soon as Story 9.1 lands; required for Story 9.7's merge to `main`.

— *Report by R2d2, 2026-05-15.*
