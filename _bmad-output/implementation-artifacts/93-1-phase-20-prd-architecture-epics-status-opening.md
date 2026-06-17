# Story 93.1 — Phase 20 PRD, architecture, epics, and sprint-status opening

Status: done

## Scope

Open Phase 20 as the read-only dashboard live-read contracts planning branch. This story creates the Phase 20 PRD amendment, architecture amendment, epics artifact, and sprint-status opening evidence.

## Implementation constraints

Docs/status-only. This story does not edit runtime code, dashboard HTML, dashboard tests, API/backend code, CI, dependency manifests, lockfiles, scripts, or deployment files.

This story does not authorize live dashboard wiring, digest integration, aggregate/session-list read contracts, destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, or mutation/control surfaces.

Later implementation requires separate approved stories, contract tests first, independent review, UltraQA, and CI evidence.

## Created planning artifacts

- `_bmad-output/planning-artifacts/phase-20-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-epics.md`

## Source planning evidence

- Ralplan plan: `.omx/plans/phase-20-readonly-dashboard-live-read-contracts-plan.md`
- Ralplan test spec: `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-test-spec.md`
- Architect review: `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-architect-review.md` — APPROVE / WATCH, with WATCH items promoted into the plan/test spec.
- Critic review: `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-critic-review.md` — APPROVE.
- Consensus handoff: `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-consensus-handoff.md`.

## Verification plan

- Phase 20 planning artifact validation: required files exist, reference Phase 20, preserve read-only/no-runtime/destructive-lifecycle boundaries.
- Sprint-status validation: `current_phase: 20`, Epic 93 and Story 93.1 opened for Phase 20, then moved to `done` only after independent cleanup/review gates passed.
- Story artifact validation: this file records docs/status-only scope, non-goals, source Ralplan evidence, and verification plan.
- Allowed tracked-file diff check limited to:
  - `_bmad-output/planning-artifacts/phase-20-prd-amendment.md`
  - `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
  - `_bmad-output/planning-artifacts/phase-20-epics.md`
  - `_bmad-output/implementation-artifacts/sprint-status.yaml`
  - `_bmad-output/implementation-artifacts/93-1-phase-20-prd-architecture-epics-status-opening.md`
- Grep/scan proving no runtime/dashboard/test/API/CI/dependency/script/deployment files changed.
- `git diff --check`.
- Relevant docs/status validation; broader tests only if final gate policy requires them despite docs-only scope.
- Final cleanup/review gate per Ultragoal before completion.
- Story closure validation: independent code-reviewer APPROVE and architect CLEAR recorded before moving Story 93.1/Epic 93 to done.

## Completion criteria

- Phase 20 planning/status artifacts are present and internally consistent.
- No runtime/dashboard/test/API/CI/dependency/script/deployment files changed.
- Local docs/status validations pass.
- Independent review gates and final quality gate are recorded before marking this story done.

## Final gate evidence

- AI slop cleaner report: `.omx/specs/phase-20-story-93-1-ai-slop-cleaner-report.md` — PASS/no-op for docs/status-only scope.
- Docs/status validation: PASS for the five Phase 20 planning/status/story artifacts.
- Changed-path guardrails: PASS; changed files remain limited to Phase 20 planning/status/story artifacts and no runtime/dashboard/test/API/CI/dependency/script/deployment files changed.
- Diff hygiene: `git diff --check` PASS.
- Format/lint: `uv run ruff format --check .` PASS; `uv run ruff check .` PASS.
- Focused read-only dashboard regression: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` PASS (`60 passed, 2 warnings`).
- Independent code-reviewer lane: APPROVE after LOW audit wording finding was fixed.
- Independent architect lane: CLEAR after candidate/provisional route/panel wording resolved the WATCH concern.

## Closure notes

Story 93.1 is complete as a docs/status-only Phase 20 opening story. It does not implement or authorize live dashboard wiring, digest integration, aggregate/session-list read contracts, destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, or mutation/control surfaces. Later implementation remains blocked until separate approved stories, contract tests first, and review gates.
