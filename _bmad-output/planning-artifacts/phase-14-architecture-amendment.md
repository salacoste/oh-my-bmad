# Phase 14 Architecture Amendment — Event Log Lifecycle Operations

## Decision Summary
Phase 14 keeps the event-log lifecycle path fail-safe: archive discovery and replay remain read-only; prune/apply remains design-gated until a future operator-authorized story adds destructive execution.

## Architectural Invariants
1. **Single writer remains intact.** Lifecycle planning may inspect event segments but must not mutate hot logs outside the established writer/registry boundaries.
2. **Read-only by default.** Dry-run/plan generation can calculate eligibility and blockers, but it must not delete, truncate, move, or rewrite event files.
3. **Replay first, prune later.** A future apply path must require archive manifest validation and replay compatibility evidence before destructive pruning is allowed.
4. **Operator gate required.** Any future destructive apply must be Tier-3/operator-gated and documented as a separate authorization event.
5. **Hot task history remains default.** `get_task_history` remains hot-log only until an explicit archive-aware history story changes the contract and tests.

## Components and completed scope
- `docs/adr/0025-event-log-lifecycle-operations.md`: accepted ADR for lifecycle operation boundaries and future destructive-gate requirements.
- Sprint-status hygiene: Phase 14 tracking remains parseable with valid BMad story statuses.
- Non-destructive lifecycle dry-run planner: package-level planning data only; no file mutation or apply path.
- Task-history boundary lock: hot-log-only remains the operator-facing contract; archive-aware task history is future work.
- Epic 73 closure: planning/status/docs/retrospective evidence only.

## Current Implementation Handoff Boundary — Epic 73 Closure

The operative handoff for this run is **Epic 73 verification, docs, and
retrospective closure only**. It supersedes the historical Epic 72 handoff. Epic
72 is complete and is not active authorization for this run.

### Allowed tracked write set for Epic 73

- `_bmad-output/planning-artifacts/phase-14-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-14-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-14-epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/retrospectives/phase-14-retrospective.md`
- `docs/project-overview.md`
- `docs/index.md`

### Forbidden tracked write set for Epic 73

- `services/`
- `packages/`
- `mcp-servers/`
- `scripts/`
- `docs/api-contracts.md`
- `docs/operator-runbook.md`
- `pyproject.toml`
- `uv.lock`
- `docker-compose.yml`
- `docker-compose.macos.yml`

## Verification Strategy
- YAML parse/status check for sprint-status values.
- Stale-boundary check: Epic 72 references must be historical/completed/not operative.
- Diff allowlist check: final tracked diff must stay inside the Epic 73 allowed set.
- Forbidden-path check: runtime/API/service/package/MCP/script/dependency/deployment paths must have no diff.
- Documentation review for explicit destructive-operation non-goals and operator gate language.
- If code is added, stop and return to ralplan; this Epic 73 closure run is not a code-change lane.

## Deliberate-Mode Safety Pre-Mortem
1. **Accidental destructive surface appears.** A future executor adds `apply=True`, `--apply`, `delete`, `unlink`, `truncate`, `rename`, or move behavior while claiming dry-run support. Mitigation for this run: no runtime files may change; verification checks the diff has no forbidden-path files.
2. **Archive validation is bypassed.** A lifecycle plan treats archived segments as safe without checksum/order/manifest validation. Mitigation: ADR-0025 requires manifest validation and replay validation before any future apply can be considered.
3. **Task history silently expands to archives.** Operators receive mixed hot+archive history without a separately tested contract. Mitigation: Phase 14 preserves hot-only history and records archive-aware history as a future story.
4. **Historical Epic 72 handoff is mistaken for current authorization.** Mitigation: this amendment marks Epic 72 as completed historical context and makes Epic 73 the only active run boundary.

## Negative Verification Required
- Status check: every `development_status` value is a valid BMad status.
- Contract check: task-history remains hot-log-only and archive-aware history is still documented as a future story.
- Lifecycle check: no destructive `apply`, `delete`, `truncate`, `move`, `rewrite`, or `chmod` implementation path is introduced.
- Boundary check: no active Epic 72 write-set or route/API authorization remains operative.

## Future Apply Gate Refinement From Review
Future dry-run plan identity must be content-addressed. The operator authorization must bind to the exact dry-run plan hash, and apply must re-compute that hash immediately before any mutation. Authorization records must be event-spine/audit-ledger durable; a transient terminal display is not enough.

Future planner code must not blur replay boundaries. Prefer a dedicated lifecycle module/package; if a read-only replay-adjacent helper hosts lifecycle planning, keep it lifecycle-scoped and prohibit write/apply helpers in replay APIs.
