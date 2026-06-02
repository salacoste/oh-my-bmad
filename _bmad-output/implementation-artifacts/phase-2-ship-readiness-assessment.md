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
| MCP transport stdio-only (no sse/streamable_http) | ✅ **now GUARDED** | was UNGUARDED — added `scripts/check_mcp_transport.py` (MCP001 AST gate, wired into `just check-gates` + ci.yml + self-test). Real tree exit 0 (stdio-only today); self-test detects planted sse/streamable violations. |
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
| tests/arch (single-writer/transport/no-anthropic) | ⚠️ **resolved + corrected** — the arch invariants are enforced by the `scripts/check_*.py` discipline GATES (CI-wired), not a `tests/arch/` dir: single-writer=`check_single_writer.py`, import-graph=`check_imports.py`, no-shell=`check_no_subprocess.py`, registry-isolation=`check_registry_isolation.py`, event-registry=`check_event_registry.py`, **transport=`check_mcp_transport.py` (added this pass)**. **"no-anthropic-outside-worker" is STALE**: the actual Anthropic-SDK consumer is `registry-api` (Story 7.3 LLM digest); worker-wrapper does NOT import the SDK (it spawns the `claude` CLI subprocess). The real current rule is "Anthropic SDK confined to registry-api's LLM digest" — the checklist item should be reworded. NON-blocking. |
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
| docs/explanations: 1–2 new deep-dives | ✅ **closed** — was a genuine gap (4 existing dives all Phase-1). Added `docs/explanations/trace-id-propagation.md` (pairs with ADR-0004). |
| project-context.md: Phase-2 Cat 3 + Cat 7 additions | ✅ **closed** — was stale (still said "trace_id reserved / no metrics in Phase 1"). Added a "Phase 2 additions (Epics 8–13)" section (Cat-3: metrics-subscriber-only + litestream RW + stdio-only + trace_id + anthropic-in-registry-api; Cat-7: 0o660 file-mode, build-base, env-alias bounds, disjoint-budget-model) + corrected the stale Phase-1 ban note. |

## The gating critical path (what actually unblocks "Phase 2 shipped")
1. **Merge the PR train to `main`** (#9 → #13 Epic-13 chain; #14 12.3a; plus any earlier unmerged 11.x). Most 🔀/🌙 items can't evaluate until the work is on main.
2. **Run nightly on main** → restore drill, separability S-4, crash-injection, budget latency, replication lag (the 🌙 items).
3. **Cut a tagged Phase-2 release** → cosign/SLSA/SBOM verification + `just verify-images` (the 🏷️ items).
4. Resolve the two ⚠️ naming discrepancies (tests/arch, tests/replay) — rename checklist→discipline-gates OR add thin wrappers.
5. Spot-confirm the documentation items on merged main; file small doc stories for any genuine gaps.

## Integration verification — the #9–#15 train composes clean + green (2026-06-02)

Built a local `integration-phase2-verify` branch off `main` (which already has
#1–#8) and merged every remaining root — Epic-13 chain (#9–#13), `epic-12.3a`
(#14), `epic-phase2-shipgate` (#15):

- **Zero merge conflicts** across all three separate roots (despite overlapping
  `payloads.py`, `config.py`, `main.py`, `justfile`, `ci.yml`, `sprint-status.yaml`).
- **`just check-gates` exit 0** on the combined tree — all 5 discipline gates,
  incl. `check_event_registry` validating EVERY new event type together (12.2
  enforcement, 12.3 budget.override, 13.4 replication.lagging) + `check_mcp_transport`.
- **mypy --strict 44 = full-set baseline** — 0 net-new across the entire train.
- **139 cross-cutting unit tests pass** — the metrics-cardinality bumps
  (12.2→62, 12.3→63, 13.4→64) and the 12.3a `override_intercepted` payload enum
  all reconcile in one tree.
- **Full PR-gate suite (`pytest -m "not slow"`): 3239 passed, 31 failed, 3 skipped.**
  Every one of the 31 failures was **REPRODUCED on clean `main`** (which has none
  of this work) → **the train introduces ZERO new failures.** The 31 are
  pre-existing: docker-gated integration tests (test_license_scan, test_tier3_negative,
  test_command_injection_fuzz, idempotency 100×, test_health — need a live compose
  stack; they pass in nightly/compose CI, the 🌙 items, not a bare `pytest`) + the
  documented TMPDIR-setgid `test_filesystem` flakes + the registry-state 500ms
  timing test.

**Verdict: the #9–#15 PR train is verified clean-to-merge** — composes onto `main`
with no conflicts, no new failures, gates green, mypy at baseline. The remaining
ship gates (nightly drills, tagged-release supply-chain) run AFTER merge, by design.

## This assessment's deliverable
- **Closed:** ADR-0004 (the one true missing artifact).
- **Verified:** the full #9–#15 train composes clean + green on a combined tree
  (0 conflicts, 0 new test failures vs main, gates green, mypy baseline).
- **Surfaced:** the merge→nightly→release critical path; the tests/arch + tests/replay
  naming discrepancies; doc items to confirm on main.
- Phase 2 is **story-complete + design-complete**; remaining is integration
  (merge) + CI/release verification, not new feature work.
