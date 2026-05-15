---
id: ADR-0003
status: accepted
date: 2026-05-15
supersedes: null
---

# ADR-0003: Phase 2 gate — formally opens `phase: 2` for `main`-branch merges

## Status

**Accepted** — 2026-05-15.

## Context

Phase 1 of oh-my-bmad shipped on 2026-05-14 (10 epics, 88 stories — see [`_bmad-output/planning-artifacts/epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"MVP Ship-Blocker Checklist" for the green-CI proof). Phase 1's architecture document explicitly deferred 5 hooks to Phase 2 — see [`_bmad-output/planning-artifacts/architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase-2 hooks" — with the corresponding placeholders preserved in the codebase (e.g., the `trace_id?` field reserved on `EventEnvelope`, the unused-but-pinned envelope `schema_version` field, the tag-immutability-only image discipline).

Between 2026-05-14 and 2026-05-15 the following Phase 2 planning artifacts were produced via the BMad workflow:

- [`_bmad-output/planning-artifacts/phase-2-brainstorming.md`](../../_bmad-output/planning-artifacts/phase-2-brainstorming.md) — 78-idea exploration across 9 orthogonal domains; convergence on **Narrative I (Observability Phase)** via 5-criteria scoring + pairwise dependency analysis.
- [`_bmad-output/planning-artifacts/prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 2 Scope Extension" — 19 new functional requirements (FR53–FR71a) + 9 new non-functional requirements (NFR-O7–O10, NFR-S9–S11, NFR-R7–R8). Schema bump 1.0.0 → 1.1.0 with one additive field (`trace_id`).
- [`_bmad-output/planning-artifacts/architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 2 Architecture Extension" — 6 new architectural invariants (P2-I1 through P2-I6) + per-epic wiring decisions + 5 forward-referenced ADR placeholders (ADR-0004 through ADR-0008).
- [`_bmad-output/planning-artifacts/epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"Phase 2 Epics — Observability Phase" — 6 epics (Epic 8–13) decomposed into 32 stories with explicit FR/NFR citations + per-epic acceptance gates.
- [`_bmad-output/planning-artifacts/implementation-readiness-report-phase-2-2026-05-15.md`](../../_bmad-output/planning-artifacts/implementation-readiness-report-phase-2-2026-05-15.md) — readiness gate confirming all four amendments are aligned.

The workflow rule recorded in [`_bmad-output/project-context.md`](../../_bmad-output/project-context.md) Cat 6 (BMad story / epic discipline) requires that "every epic and story carries `phase: 1 | 2 | ...`. No `phase: 2` work merges to `main` until a Phase-N gate ADR is accepted and `current_phase` in `sprint-status.yaml` increments." This ADR is that gate.

## Decision

1. **Phase 2 is formally open for `main`-branch merges as of 2026-05-15.** Stories carrying `phase: 2` in `sprint-status.yaml` may now transition through the normal workflow states (`pending` → `ready-for-dev` → `in-progress` → `blocked` → `review` → `done`) and may merge to `main` via the standard PR gate.

2. **`sprint-status.yaml` is amended to set `current_phase: 2`** at the same time as this ADR transitions to `accepted`. Stories from Phase 1 that are `done` retain their `phase: 1` label; stories from Epic 8–13 are added with `phase: 2`.

3. **The Phase 2 baseline scope is exactly Narrative I** as selected in the brainstorming artifact:
   - **Epic 8 (γ supply-chain hardening)** — FR53–FR56a — lands first.
   - **Epic 9 (α `trace_id` propagation kernel)** — FR57–FR59a — second.
   - **Epic 10 (β `metrics-subscriber` service)** — FR60–FR62a — third.
   - **Epic 11 (ξ approval inbox + HMAC signature)** — FR63–FR65a — fourth.
   - **Epic 12 (κ per-task budget enforcement)** — FR66–FR68a — fifth.
   - **Epic 13 (δ litestream WAL replication)** — FR69–FR71a — sixth, orthogonal.

   Items rejected from Phase 2 scope (deferred to Phase 3+, per the brainstorming convergence):
   - η Remote-MCP transports (HTTP/SSE).
   - θ/ι Browser-automation plane (either flavor).
   - ζ Second CLI agent (Codex / Gemini / GLM).
   - μ Historical replay mode.
   - Translation-layer chat surfaces.

4. **Five forward-referenced ADRs (ADR-0004 through ADR-0008)** are staked out in the architecture amendment. Each lands as `status: proposed` first; each must be promoted to `status: accepted` before its owning epic's first story merges to `main`:
   - **ADR-0004** — `trace_id` propagation policy + cutover plan (gates Epic 9).
   - **ADR-0005** — metrics-subscriber as derived projection (gates Epic 10).
   - **ADR-0006** — operator HMAC non-repudiation + key rotation (gates Epic 11).
   - **ADR-0007** — litestream WAL replication (read-only sidecar; replication ≠ HA) (gates Epic 13).
   - **ADR-0008** — cosign keyless + SLSA L2 + CycloneDX SBOM triumvirate (gates Epic 8).

5. **The Phase 2 architectural invariants (P2-I1 through P2-I6) are non-negotiable.** A Phase 2 PR that introduces an instrumentation path inside `services/*`, a public-network ingress, a non-additive envelope change, a new writer to the JSONL log or registry DB, or an MCP non-stdio transport is rejected at review regardless of its other merits. Violating one of these requires an explicit ADR amendment to this set of invariants.

6. **Phase 1 invariants stand unchanged.** Every Phase 2 epic's acceptance gate includes "Phase 1 invariants regression-free" — `tests/separability/`, `tests/crash-injection/`, `tests/idempotency/`, `tests/contract/`, `tests/arch/`, and `tests/replay/` must all be green at every Phase 2 epic boundary. A Phase 2 PR that breaks a Phase 1 invariant is a Sev1 regression.

7. **Phase 2 ship criterion** is the green Phase 2 Ship-Blocker Checklist at the end of [`epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"Phase 2 Ship-Blocker Checklist". Phase 2 has not shipped until every gate listed there is green.

## Consequences

- **Sprint planning may now start.** `bmad-sprint-planning` populates `sprint-status.yaml` with the 32 Phase 2 stories at initial state `pending` (or `ready-for-dev` for stories whose prerequisites are already met from Phase 1).
- **Implementation may begin on Epic 8.** Epic 8 has no dependencies on other Phase 2 epics. Its acceptance gate is the cheapest of the six and lands ~3 days of work.
- **Phase 2 changes ship via the same PR workflow as Phase 1.** No special phase-2 branch is created; `main` carries mixed Phase-1-`done` and Phase-2-`in-progress` work. The `phase:` label on each `sprint-status.yaml` entry is the canonical distinguishing field.
- **The PR-required-status-checks list expands** as each Phase 2 epic ships its CI gates (per the "CI gate additions" section in the architecture amendment). At minimum: cosign verify (Epic 8), `trace_id`-required AST check (Epic 9), `metrics-subscriber` cardinality regression test + separability S-4 (Epic 10), HMAC offline-verify test (Epic 11), budget enforcement latency test (Epic 12), litestream restore drill (Epic 13).
- **Phase 1 stays operational throughout Phase 2 implementation.** Phase 2 is fully additive: every change preserves the Phase 1 architectural invariants (P1 invariants encoded in the test trees above; P2 invariants P2-I1–P2-I6 enforce the additive property).
- **The 1-month consumer-compat window after Epic 9** (during which `EventLogReader` accepts both `schema_version=1.0.0` and `1.1.0`) is a Phase 2 obligation that crosses an epic boundary — its cleanup is a follow-up story documented in the Epic 9 acceptance gate.
- **Phase 3 scope is not pre-decided.** The 5 forward-referenced ADRs (ADR-0004–ADR-0008) are Phase 2-specific. Phase 3 will require its own gate ADR (ADR-0009 or later) preceded by its own brainstorming + PRD + architecture + epics amendments, following the same BMad workflow path.
- **A Phase 2 retrospective** is required at every epic boundary (per project-context.md Cat 6: three falsifiable outputs — wrong-assumption, single-process-change, deferred-item-triage). Retros land in `_bmad-output/implementation-artifacts/epic-<n>-retro-<date>.md`, matching Phase 1's convention.

## Alternatives considered

- **Defer Phase 2 to Q3+ in favor of Phase 1.5 tech-debt sweep.** Rejected at the brainstorming stage because the deferred-work backlog from Phase 1 retrospectives totals ~3 medium-effort items (post Epic-7.5) — below the threshold (≥3) that would justify a dedicated cleanup phase before Phase 2. The remaining items are folded into individual Phase 2 stories where they naturally compose.
- **Pick Narrative II (Multi-Agent Phase) or Narrative III (Operator-UX Phase) instead of Narrative I.** Rejected per the brainstorming scoring matrix — Narrative I has lowest regret + highest compound value and closes Phase 1's explicitly-acknowledged largest gap. Narratives II and III remain valid Phase 3+ candidates.
- **Ship Phase 2 as a long-lived feature branch and merge once-complete.** Rejected because (a) `main` is the only long-lived branch per project-context.md Cat 6, and (b) long-lived branches accumulate merge conflicts proportionally to phase duration — 6–8 weeks of solo work on a side branch would be substantially more painful than incremental epic-by-epic merges to `main`.

## Linked artifacts

- [`_bmad-output/planning-artifacts/phase-2-brainstorming.md`](../../_bmad-output/planning-artifacts/phase-2-brainstorming.md) — scope selection rationale.
- [`_bmad-output/planning-artifacts/prd.md`](../../_bmad-output/planning-artifacts/prd.md) — FR53–FR71a + 9 new NFRs.
- [`_bmad-output/planning-artifacts/architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) — P2-I1–P2-I6 + wiring decisions.
- [`_bmad-output/planning-artifacts/epics.md`](../../_bmad-output/planning-artifacts/epics.md) — Epic 8–13 + 32 stories + Phase 2 ship-blocker checklist.
- [`_bmad-output/planning-artifacts/implementation-readiness-report-phase-2-2026-05-15.md`](../../_bmad-output/planning-artifacts/implementation-readiness-report-phase-2-2026-05-15.md) — readiness gate verdict.

— *R2d2, 2026-05-15.*
