# Phase 14 Architecture Amendment — Event Log Lifecycle Operations

## Decision Summary
Phase 14 keeps the event-log lifecycle path fail-safe: archive discovery and replay remain read-only; prune/apply remains design-gated until a future operator-authorized story adds destructive execution.

## Architectural Invariants
1. **Single writer remains intact.** Lifecycle planning may inspect event segments but must not mutate hot logs outside the established writer/registry boundaries.
2. **Read-only by default.** Dry-run/plan generation can calculate eligibility and blockers, but it must not delete, truncate, move, or rewrite event files.
3. **Replay first, prune later.** A future apply path must require archive manifest validation and replay compatibility evidence before destructive pruning is allowed.
4. **Operator gate required.** Any future destructive apply must be Tier-3/operator-gated and documented as a separate authorization event.
5. **Hot task history remains default.** `get_task_history` remains hot-log only until an explicit archive-aware history story changes the contract and tests.

## Proposed Components
- `docs/adr/0025-event-log-lifecycle-operations.md`: accepted ADR for lifecycle operation boundaries and future destructive-gate requirements.
- Sprint-status hygiene update: normalize the legacy `bootstrap-minimum-milestone` tracking row to a valid BMad story status while preserving the closure rationale as a comment.
- Optional future code component: `packages/replay` dry-run planner returning structured non-destructive lifecycle plan data.

## Verification Strategy
- YAML parse/status check for sprint-status values.
- Documentation review for explicit destructive-operation non-goals and operator gate language.
- If code is added, targeted replay/lifecycle tests plus ruff/mypy for touched packages.

## Implementation Handoff Boundary (Autopilot Slice)
The implementation handoff for this run is a package-only lifecycle dry-run
planner slice. The allowed tracked write set is:

- `docs/adr/0025-event-log-lifecycle-operations.md`
- `_bmad-output/planning-artifacts/phase-14-*.md`
- `packages/replay/src/replay/archive_manifest.py`
- `packages/replay/src/replay/lifecycle.py`
- `packages/replay/src/replay/test_lifecycle.py`
- `packages/replay/src/replay/__init__.py`
- `docs/operator-runbook.md`

The forbidden write set for this run is runtime code under:
- `services/registry-api/src/`
- `services/registry-state/src/`
- `services/worker-wrapper/src/`
- `mcp-servers/`

The package-only dry-run planner may additively expose read-only helpers and
immutable plan dataclasses from `packages/replay` while preserving existing
`replay.__init__` exports; it must not add HTTP routes, API-contract
changes, task-history archive expansion, or destructive apply helpers. Route-level
contract tests are only allowed in a future story if an API surface is explicitly
approved.

## Deliberate-Mode Safety Pre-Mortem
1. **Accidental destructive surface appears.** A future executor adds `apply=True`, `--apply`, `delete`, `unlink`, `truncate`, `rename`, or move behavior while claiming dry-run support. Mitigation for this run: no runtime files may change; verification checks the diff has no code files under the forbidden write set.
2. **Archive validation is bypassed.** A lifecycle plan treats archived segments as safe without checksum/order/manifest validation. Mitigation: ADR-0025 requires manifest validation and replay validation before any future apply can be considered.
3. **Task history silently expands to archives.** Operators receive mixed hot+archive history without a separately tested contract. Mitigation: this slice preserves hot-only history and records archive-aware history as a future story.

## Negative Verification Required
- Diff check: no files under the forbidden runtime write set changed.
- Status check: every `development_status` value is a valid BMad status.
- ADR check: ADR-0025 contains explicit non-authorization of destructive apply and future operator-gate preconditions.
- Contract check: no API-contract or route file changed in this slice.
- Lifecycle check: package code exposes only read-only dry-run planner surfaces
  and contains no `apply`, `delete`, `truncate`, `move`, `rewrite`, or `chmod`
  implementation path.

## Future Apply Gate Refinement From Review
Future dry-run plan identity must be content-addressed. The operator authorization must bind to the exact dry-run plan hash, and apply must re-compute that hash immediately before any mutation. Authorization records must be event-spine/audit-ledger durable; a transient terminal display is not enough.

Future planner code must not blur replay boundaries. Prefer a dedicated lifecycle module/package; if `packages/replay` hosts read-only planner helpers, keep them lifecycle-scoped and prohibit write/apply helpers in replay APIs.
