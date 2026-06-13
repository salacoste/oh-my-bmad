# Phase 17 Retrospective — Destructive Lifecycle Apply Readiness

Date: 2026-06-13  
Scope: Epics 81-85 / P17-DLAR  
Status: COMPLETE

## Retrospective dialogue

Amelia (Developer): "Phase 17 is complete. We are reviewing a safety-contract phase, not a destructive lifecycle implementation."

Alice (Product Owner): "The product outcome is clear: future destructive apply work is better specified, but it is still not authorized or implemented."

Charlie (Senior Developer): "The strongest technical win is the exact dry-run `plan_hash` authorization contract. It gives a future implementation a fail-closed identity check instead of relying on broad operator intent."

Dana (QA Engineer): "Replay validation and rollback evidence became explicit release preconditions. That is the right quality bar before any mutation path exists."

R2d2 (Project Lead): "The phase should leave the repository safer, more explicit, and ready for the next BMAD decision point."

## Summary

Phase 17 converted the high-risk destructive lifecycle apply candidate into a bounded readiness package. It defined requirements, architecture invariants, future authorization evidence, replay-validation proof, rollback evidence, documentation reconciliation, quality gates, and the next-route boundary.

No destructive lifecycle apply, prune, archive mutation, object-storage lifecycle job, scheduled retention worker, credentialed production operation, runtime package behavior, public API surface, MCP tool, deployment path, dependency, or CI behavior was introduced.

## Shipped scope

| Epic | Scope | Status |
|---|---|---|
| 81 | Planning and scope lock | COMPLETE |
| 82 | Apply precondition contract | COMPLETE |
| 83 | Replay and rollback proof contract | COMPLETE |
| 84 | Documentation reconciliation and static guard | COMPLETE |
| 85 | Final verification and release hygiene | COMPLETE |

Key outcomes:
- PRD amendment captured FR156-FR161 with non-goals and acceptance criteria.
- Architecture amendment captured the allowed/forbidden write set and fail-closed lifecycle invariants.
- Future apply authorization is bound to an exact dry-run `plan_hash`, affected segment identities, safety policy version, retention input digest, replay validation reference, rollback evidence reference, operator identity, and authorization ledger reference.
- Future replay and rollback gates require archive manifest validation, retained hot+archive segment identity, backup artifacts outside the hot event-log directory, affected segment checksums/sizes, restore instructions, and restore drill evidence or a bounded risk-acceptance exception defined by a future implementation story.
- Documentation/status closure now marks Phase 17 complete and records that destructive lifecycle apply remains future work requiring a separate explicitly authorized BMAD phase/story.

## Verification evidence

Fresh closure evidence for the shipped Phase 17 state:
- Latest closure commit: `1f1269544253d4d35b024426e614be28f6288069` (`docs(bmad): mark phase 17 complete`).
- GitHub Actions `ci` run `27451918933` on `1f1269544253d4d35b024426e614be28f6288069`: `success`.
- Prior contract commit: `12092227dd11d3140428dbfd9f9b3d9a1b5db881` (`docs(bmad): complete phase 17 lifecycle contracts`).
- GitHub Actions `ci` run `27450009340` on `12092227dd11d3140428dbfd9f9b3d9a1b5db881`: `success`.
- Story 85.1 recorded local gates: docs/status/no-runtime proof, stale wording grep, destructive source-path scan, lifecycle/route regression suite, `ruff`, diff hygiene, and changed-file secret hygiene.
- Independent review evidence recorded in Story 85.1: code-reviewer `APPROVE`; architect `CLEAR` after WATCH resolution.

## What went well

1. **The phase preserved the destructive-action boundary.** Planning and contracts improved readiness without adding mutation surfaces.
2. **The authorization model became concrete.** The future operator gate now has exact plan identity, evidence references, and fail-closed preflight behavior.
3. **Replay and rollback moved from general safety language to required evidence.** Future implementation cannot claim readiness with operator intent alone.
4. **Architect review caught wording risk before release.** The contract language was corrected to separate durable invariants from incidental current implementation details.
5. **Closure reconciliation removed stale state.** README, docs index, project overview, architecture, and sprint status now agree that Phase 17 is shipped.

## Challenges and growth areas

1. **Closure required an extra reconciliation pass.** Some docs still described Phase 17 as open after the readiness contracts were complete.
2. **Historical artifacts can look like live drift.** Story files correctly preserve their local context, but final status checks need to distinguish historical notes from current repo state.
3. **Safety language can over-couple to current internals.** Early replay wording leaned too heavily on current HOT_ONLY_REPLAY and route-local behavior. The durable contract now focuses on invariants a future implementation must satisfy.
4. **Risk-acceptance fallback needed tighter boundaries.** Operator acknowledgement alone is not acceptable unless a future story defines scope, rationale, reviewer identity, and expiry.
5. **Aggregate completion reporting can be noisy.** Final evidence showed completion and reconciliation were clean, while one aggregate summary field was less clear. Future closure reports should prefer artifact-level and reconciliation evidence over a single ambiguous field.

## Lessons learned

1. **High-risk lifecycle work benefits from a readiness-only phase before code.** Contract-first sequencing kept destructive apply out of the repository while improving the future implementation path.
2. **Exact evidence beats intent.** `plan_hash`, segment identity, replay proof, rollback proof, and ledger references provide stronger safety than narrative approval.
3. **Docs-only phases still need implementation guards.** Forbidden-path scans and diff allowlists remain valuable even when no code change is intended.
4. **Final closure should include a current-vs-historical wording scan.** Stale open/backlog wording should be retired in current docs while preserved as historical context inside story artifacts.
5. **Future destructive implementation must be its own explicitly authorized phase.** Phase 17 prepared the gate; it did not open it.

## Carry-forward / future work

- Destructive lifecycle apply remains future work and requires a separate explicitly authorized BMAD phase/story.
- Any future apply phase must start with PRD acceptance, architecture review, test-first fail-closed contracts, independent security/architecture review, and final CI/operator evidence.
- Object-storage lifecycle jobs, scheduled retention workers, archive mutation, and credentialed production operations remain out of scope until the destructive apply safety path is explicitly approved.
- The bounded risk-acceptance exception for missing restore drill evidence needs a concrete schema before it can be used.

## Action items

| ID | Action | Owner | Success check |
|---|---|---|---|
| AI-17-R1 | Add a current-vs-historical wording check to future phase closure gates. | Developer / QA | Current docs do not contradict shipped phase status; historical artifacts are explicitly treated as historical. |
| AI-17-R2 | If Phase 18 is authorized, begin with a PRD for destructive apply scope, non-goals, and operator authority. | Product / Architecture | A new BMAD PRD exists before any runtime mutation work. |
| AI-17-R3 | Define the bounded risk-acceptance schema for any missing restore drill evidence. | Architect / QA | Schema includes scope, rationale, reviewer identity, expiry, affected segment identities, and fail-closed handling. |
| AI-17-R4 | Keep destructive apply implementation blocked until test-first fail-closed contracts exist. | Developer / QA | No apply/prune/delete/truncate/move/rewrite/chmod code lands without red tests and reviewed contracts. |
| AI-17-R5 | Preserve the docs/status-only closure artifact pattern for future safety phases. | Developer | Closure artifact records scope, non-goals, verification, CI, and next-route boundary. |

## Next epic preparation

No Phase 18 is currently defined. The correct next route depends on explicit operator intent:

1. If the next goal is destructive lifecycle apply, start with `$bmad-create-prd` for a new authorized phase and keep implementation blocked until readiness gates exist.
2. If the next goal is roadmap orientation, run `$bmad-sprint-status` and choose from documented future candidates.
3. If the next goal is maintenance, keep the current Phase 17 boundary intact and avoid runtime mutation work.

## Readiness assessment

- Product readiness: COMPLETE for Phase 17 readiness scope; destructive apply remains future work.
- Architecture readiness: CLEAR for contract/readiness docs; future mutation requires a new architecture gate.
- Quality readiness: COMPLETE for docs/status/no-runtime closure; latest Phase 17 CI evidence is green.
- Release readiness: COMPLETE for the Phase 17 documentation/status release.
- Technical health: No runtime behavior changed; the future destructive path is more explicit and better guarded.
