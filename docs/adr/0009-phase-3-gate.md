---
id: ADR-0009
status: proposed
date: 2026-06-03
supersedes: null
---

# ADR-0009: Phase 3 gate — formally opens `phase: 3` for `main`-branch merges

## Status

**Proposed** — 2026-06-03. Transitions to **accepted** once the Phase-3 architecture amendment, epics/stories decomposition, and implementation-readiness report are aligned (see [Acceptance criteria](#acceptance-criteria)). Mirrors the ADR-0003 (Phase-2 gate) lifecycle, which was accepted only after all four planning amendments aligned.

## Context

Phase 2 of oh-my-bmad shipped on 2026-06-03 as **v0.3.0** (Epics 8–13 `done`; supply-chain-verified — see [`epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"Phase 2 Ship-Blocker Checklist" + the 2026-06-03 verification stamp + [`phase-2-traceability-matrix-2026-06-03.md`](../../_bmad-output/test-artifacts/phase-2-traceability-matrix-2026-06-03.md), gate **PASS**). Two Phase-3 readiness-hardening items landed before this gate: **G-SEC-1** (license gate fail-closed) and **G-SEC-2** (child-env secret allowlist) — both merged, main green.

Phase 1's architecture explicitly named Phase-3 forward-references ([`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §1485): the MCP tooling fleet, remote-MCP transport, a second CLI agent, browser-automation, and the mutation-testing gate. The following Phase-3 planning artifacts were produced via the BMad workflow (2026-06-03):

- [`_bmad-output/planning-artifacts/phase-3-scoping-brief.md`](../../_bmad-output/planning-artifacts/phase-3-scoping-brief.md) — analyst candidate inventory + dependency/enablement map + decisions D1–D7 + readiness gaps.
- [`_bmad-output/planning-artifacts/phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) — brainstorming-convergence output: resolved scope (D1–D4), proposed FR72–FR77 + NFRs, epic breakdown (Epics 14–19), ADRs needed, Phase-3 ship-blocker checklist.
- [`_bmad-output/planning-artifacts/prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension" — FR72–FR77 + NFR-O11/M8/S12.

The project-context Cat-6 workflow rule requires every epic/story carry `phase: N`, and no `phase: N` work merges to `main` until a Phase-N gate ADR is accepted and `current_phase` in `sprint-status.yaml` increments. This ADR is that gate for Phase 3.

## Decision

1. **Phase 3 will be formally open for `main`-branch merges once this ADR is accepted.** Stories carrying `phase: 3` may then transition through the normal workflow and merge via the standard PR gate. `sprint-status.yaml` increments to `current_phase: 3` at acceptance.

2. **The Phase 3 baseline scope is the MCP tooling fleet** (operator brainstorming convergence D1–D4):
   - **Epic 14 (ψ tests-first hardening warm-up)** — FR77 digest-deprecation + NFR-O11 mutation-testing gate — lands **first** (Epic-8-before-features pattern).
   - **Epic 15 (σ `git` MCP server)** — FR72 — second; establishes the reusable MCP-server-authoring recipe.
   - **Epic 16 (τ `github` MCP server)** — FR73 — closes the G-SEC-2 `GITHUB_TOKEN` scoped-credential follow-up.
   - **Epic 17 (υ `build`/`verification` MCP server)** — FR74.
   - **Epic 18 (φ `memory`/`wiki` MCP server)** — FR75.
   - **Epic 19 (χ `artifact` MCP server + store)** — FR76.

   Items rejected from Phase 3 scope (deferred per the convergence):
   - **D2** — Remote-MCP transport (HTTP/SSE/streamable) + its auth/rate-limit layer. MCP stays stdio-only.
   - **D3** — Second CLI agent (Codex / Gemini / GLM) → deferred to **Phase 5**.
   - Browser-automation plane → Phase 4. The `workspace`/`docker-pool`/`db-schema`/`docs-research`/`telegram-control-direct` servers + replay mode → Phase 3+/later.

3. **Forward-referenced ADRs** are staked out; each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges:
   - **ADR-0010** — MCP-server-authoring pattern (stdio + tier-authz + event-telemetry + separability + supply-chain + child-env allowlist) — gates Epic 15, reused by 16–19.
   - **ADR-0011** — artifact-store design (content-addressed local-FS; retention; FR26-safe) — gates Epic 19.
   - **ADR-0012** — memory/wiki store (SQLite FTS5; own file; registry-DB isolation) — gates Epic 18.
   - **Deferred ADRs stay deferred:** remote-MCP transport (D2) and browser-automation surface — explicitly non-decisions for Phase 3.

4. **Phase-1+2 invariants are non-negotiable in Phase 3.** Every new server preserves FR26 single-writer, stdio-only MCP transport, event-only telemetry (no instrumentation outside metrics-subscriber), `trace_id` propagation, tier-enforced authz, and the supply-chain triumvirate + fail-closed license gate + child-env allowlist. A Phase-3 PR violating one is rejected at review regardless of merits.

5. **Phase-3 ship criterion** is the green Phase-3 Ship-Blocker Checklist in [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) §7 (to be promoted into `epics.md` at create-epics). Phase 3 has not shipped until every gate there is green.

6. **Incremental release.** Each server epic is independently shippable (a new optional stdio member), so Phase 3 releases incrementally (e.g. v0.4.0 after Epic 15) rather than big-bang.

## Acceptance criteria

This ADR transitions `proposed → accepted` only when:

- [x] Phase-3 brainstorming convergence recorded (`phase-3-plan.md`).
- [x] PRD extension authored (`prd.md` §"Phase 3 Scope Extension", FR72–FR77 + NFR-O11/M8/S12).
- [ ] Phase-3 **architecture amendment** authored (`architecture.md` §"Phase 3 Architecture Extension" — the MCP-server-authoring pattern + per-epic wiring + ADR-0010/0011/0012 placeholders).
- [ ] Phase-3 **epics/stories** decomposed (`epics.md` §"Phase 3 Epics" — Epics 14–19 + stories + per-epic acceptance gates + the Phase-3 ship-blocker checklist).
- [ ] Phase-3 **implementation-readiness report** confirms the PRD + architecture + epics are aligned, the deferred-work backlog is reviewed, and G-FN-1/2/3 dispositions are set.

Until accepted, NO `phase: 3` story merges to `main` and `current_phase` stays `2`.

## Consequences

- **Implementation order is FR77/Epic-14 first** (pure verification/CI: digest-deprecation + mutation gate) — tests-first per operator priority, de-risking the deploy path before five new server images are published.
- **The PR-required-checks list expands** as each server epic ships its CI gates (separability S-5…S-9, per-server `verify-images`, Tier-3-denial negative tests, the mutation-gate threshold).
- **Phase 1+2 stay operational throughout** — Phase 3 is fully additive (new optional stdio servers).
- **`main` carries mixed `phase: 2`-`done` and `phase: 3`-`in-progress` work**; the `phase:` label is the canonical distinguishing field. No long-lived phase branch.
- **Phase 4+ scope is not pre-decided** — browser-automation (Phase 4), second agent (Phase 5), and the remaining servers each require their own gate ADR + planning chain.
- **A retrospective is required at every Phase-3 epic boundary** (project-context Cat 6), landing in `_bmad-output/implementation-artifacts/epic-<n>-retro-<date>.md`.

## Alternatives considered

- **Broader 11-server scope** (the `prd.md:595` "Phase 3+" list). Rejected (D1) — the more-specific 5-server roadmap is the canonical boundary; the rest are later-phase backlog.
- **Include remote-MCP transport this phase.** Rejected (D2) — no concrete remote-worker use case has emerged; it pulls in a new auth/rate-limit sub-project. Stays deferred until justified.
- **Pull the second CLI agent forward to Phase 3** (architecture frames it as feasible). Rejected (D3) — follows the roadmap's Phase-5 placement; Phase 3 stays focused on the tool fleet. The trace_id + metrics enablers remain in place for when it lands.
- **Servers-first (skip the hardening warm-up).** Rejected (D4) — mirrors Epic-8-before-features; the digest-deprecation + mutation gate are cheap, tests-first, and de-risk the deploy path before publishing new images.

## Linked artifacts

- [`phase-3-scoping-brief.md`](../../_bmad-output/planning-artifacts/phase-3-scoping-brief.md) — candidate inventory + decisions.
- [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) — resolved scope + epics + ship-blocker checklist.
- [`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension" — FR72–FR77 + NFR-O11/M8/S12.
- ADR-0010 / ADR-0011 / ADR-0012 — to be authored in the architecture amendment.

— *R2d2, 2026-06-03 (proposed; via the BMad Phase-3 planning chain).*
