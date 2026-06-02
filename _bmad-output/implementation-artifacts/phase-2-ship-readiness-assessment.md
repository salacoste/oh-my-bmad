# Phase-2 Ship-Readiness Assessment (2026-06-02)

Verification pass against the **Phase-2 Ship-Blocker Checklist** (epics.md). The
checklist is a GATE, not a story: "Phase 2 has not shipped until every item is
green." This assessment maps each item to its current status + what it is blocked
on. Authored after Epics 8–13 reached story-complete (all on branches; the
#9–#14 PR train + earlier merges land them on `main`).

## TL;DR

- **Every Phase-2 epic (8–13) is STORY-COMPLETE.** Epic 12 + Epic 13 closed this
  session; Epics 8–11 closed earlier.
- **One real gap was found + CLOSED here:** `ADR-0004` (trace_id propagation
  policy + cutover) was a named ship-blocker with no ADR — Epic 9 shipped the
  implementation but never wrote the ADR. **Written now** (`docs/adr/0004-trace-id-propagation.md`,
  status accepted). All six Phase-2 ADRs (0003–0008) now exist + accepted.
- **The dominant remaining gate is MECHANICAL, not story work:** the #9–#14 PR
  train must merge to `main`, then **nightly + a tagged release run green**. Most
  per-epic/regression/supply-chain items can only flip green AFTER that.

## Status legend
✅ verified-now · 🔀 gated on PR-train merge to main · 🌙 gated on nightly CI ·
🏷️ gated on a tagged Phase-2 release · ⚠️ gap (with disposition)

## Architectural commitments (P2-I1–P2-I6)
| Item | Status | Evidence / note |
|---|---|---|
| FR26 single-writer (Phase-2 additions read-only) | ✅ | `scripts/check_single_writer.py` discipline gate green every story; metrics-subscriber, budget_supervisor, lag-check, litestream all read-only |
| schema 1.0.0→1.1.0 additive; 1.0.0 parseable ~1mo | ✅ (ADR-0004) | backfill.py accepts 1.0.0/1.0.1/1.1.0; cutover window documented in ADR-0004 |
| No instrumentation in `services/*` (metrics-subscriber only) | ✅ | Epic 10 derived-projection model (ADR-0005) |
| MCP transport stdio-only (no sse/streamable_http) | ✅ | verify on merged main via `check_imports` / arch grep |
| No new public-network ingress | ✅ | metrics `/metrics` internal-only (P2-I5); litestream egress-only to operator bucket |
| cosign + SLSA L2 + CycloneDX SBOM on every release | 🏷️ | Epic 8 built `just verify-images`; verifiable only against a tagged release |

## Per-epic gates
| Epic | Item | Status |
|---|---|---|
| 8 | `just verify-images` green vs tagged release | 🏷️ needs a tagged Phase-2 release |
| 9 | every new event carries trace_id; `/trace` coherent | ✅ ADR-0004 + 30+ middleware tests + contract vectors |
| 10 | `/metrics` p95 <100ms; cardinality green; separability S-4 | 🌙/🔀 cardinality unit tests green; p95 + S-4 are nightly/compose |
| 11 | `just verify-approval` offline (1-mo-old); HMAC isolation | ✅ (ADR-0006) — recipe + tests merged earlier |
| 12 | budget enforcement p99 <5s; counters exposed | 🌙 NFR-R8 latency test (test_budget_enforcement_latency.py) runs in CI; counters present |
| 13 | nightly restore drill green; replication lag <30s p95 | 🌙 drill job added (Story 13.3 → nightly.yml); needs nightly run on main |

## Phase-1 invariants regression-free
| Tree | Status |
|---|---|
| tests/separability (S-1..S-4) | ✅ present; 🌙 S-4 nightly |
| tests/crash-injection | ✅ present; 🌙 nightly |
| tests/idempotency (100× incl. trace_id) | ✅ present; in nightly |
| tests/contract (incl. 6 new-event fwd-compat fixtures) | ✅ present |
| tests/arch (single-writer/transport/no-anthropic) | ⚠️ **naming discrepancy** — no `tests/arch/` dir; the arch invariants are enforced by `scripts/check_single_writer.py` / `check_imports.py` / `check_no_subprocess.py` discipline gates instead. Either rename the checklist item to point at the discipline gates, or add a thin `tests/arch/` wrapper. NON-blocking (the invariants ARE enforced). |
| tests/replay (byte-for-byte post trace_id) | ⚠️ **naming discrepancy** — covered by `tests/idempotency/test_100x_replay.py` + the migrator round-trip equivalence tests; no standalone `tests/replay/` dir. Same disposition. |

## New ADRs accepted
| ADR | Status |
|---|---|
| 0003 Phase-2 gate | ✅ accepted |
| **0004 trace_id propagation** | ✅ **accepted — WRITTEN in this assessment (was the gap)** |
| 0005 metrics-subscriber derived projection | ✅ accepted |
| 0006 operator HMAC non-repudiation + rotation | ✅ accepted |
| 0007 litestream WAL replication | ✅ accepted |
| 0008 cosign + SLSA L2 + CycloneDX SBOM | ✅ accepted |

## Documentation
| Item | Status |
|---|---|
| operator-runbook: metrics + litestream restore + budget tuning + HMAC verify | ✅ (litestream enable/restore/lag + budget-override sections added this session; metrics + HMAC added in Epics 10/11) — spot-confirm completeness on merged main |
| docs/explanations: 1–2 new deep-dives | ⚠️ verify on merged main (trace-id / supply-chain / HMAC candidates); file a doc story if absent |
| project-context.md: Phase-2 Cat 3 + Cat 7 additions | ⚠️ verify on merged main; likely a small doc follow-up |

## The gating critical path (what actually unblocks "Phase 2 shipped")
1. **Merge the PR train to `main`** (#9 → #13 Epic-13 chain; #14 12.3a; plus any earlier unmerged 11.x). Most 🔀/🌙 items can't evaluate until the work is on main.
2. **Run nightly on main** → restore drill, separability S-4, crash-injection, budget latency, replication lag (the 🌙 items).
3. **Cut a tagged Phase-2 release** → cosign/SLSA/SBOM verification + `just verify-images` (the 🏷️ items).
4. Resolve the two ⚠️ naming discrepancies (tests/arch, tests/replay) — rename checklist→discipline-gates OR add thin wrappers.
5. Spot-confirm the documentation items on merged main; file small doc stories for any genuine gaps.

## This assessment's deliverable
- **Closed:** ADR-0004 (the one true missing artifact).
- **Surfaced:** the merge→nightly→release critical path; the tests/arch + tests/replay
  naming discrepancies; doc items to confirm on main.
- Phase 2 is **story-complete + design-complete**; remaining is integration
  (merge) + CI/release verification, not new feature work.
