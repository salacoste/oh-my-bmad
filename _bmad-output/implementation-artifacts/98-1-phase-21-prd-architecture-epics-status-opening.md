# Story 98.1 — Phase 21 PRD, Architecture, Epics, and Sprint-Status Opening

Status: done
Date opened: 2026-06-19

## Summary

Story 98.1 opens **Phase 21 — Dashboard Live-Read Rendering Readiness** as a docs/status-only BMad slice. It creates Phase 21 planning artifacts and updates sprint status without changing runtime code, dashboard HTML, dashboard tests, backend/API code, CI, dependencies, lockfiles, scripts, deployment files, or mutation/control behavior.

## Scope

Allowed tracked files:

1. `_bmad-output/planning-artifacts/phase-21-prd-amendment.md`
2. `_bmad-output/planning-artifacts/phase-21-architecture-amendment.md`
3. `_bmad-output/planning-artifacts/phase-21-epics.md`
4. `_bmad-output/implementation-artifacts/98-1-phase-21-prd-architecture-epics-status-opening.md`
5. `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Non-goals

- No runtime live API calls.
- No browser `fetch`, XHR, WebSocket, EventSource, polling, frontend scripts, HTTP clients, or live API URLs.
- No dashboard/static behavior change.
- No backend/API route expansion.
- No aggregate/session live contract; aggregate/session remain unavailable/needs-contract.
- No digest integration.
- No mutation/control/destructive lifecycle affordance.
- No dependency, lockfile, CI, deployment, script, service, MCP, package, runtime, or test-code changes.

## RALPLAN gate evidence

- Architect gate: `.omx/specs/autopilot-story-98-1-ralplan-architect-review.md` — `ARCHITECT_VERDICT: APPROVE`, `ARCHITECTURAL_STATUS: CLEAR`, required changes: none.
- Critic gate: `.omx/specs/autopilot-story-98-1-ralplan-critic-review.md` — `CRITIC_VERDICT: APPROVE`, `QUALITY_STATUS: CLEAR`, findings: none.
- Plan: `.omx/plans/phase-21-dashboard-live-read-rendering-readiness-plan.md`.

## Implementation notes

- Created Phase 21 PRD amendment for rendering-readiness product scope, non-goals, FRs/NFRs, and follow-on gates.
- Created Phase 21 architecture amendment for presentation-model boundaries, fixture/snapshot semantics, allowed data categories, aggregate/session needs-contract handling, and no-runtime-wiring guardrails.
- Created Phase 21 epics sequencing docs/status first, presentation-contract tests second, fixture/snapshot rendering third, and live wiring only after a separate explicit story.
- Updated sprint status to `current_phase: 21` and opened Epic 98 / Story 98.1 without claiming runtime implementation.

## Verification plan

- Parse `_bmad-output/implementation-artifacts/sprint-status.yaml` and confirm `current_phase: 21` plus Epic 98 / Story 98.1 status.
- Verify exact changed-file allowlist against the five Story 98.1 files.
- Guardrail scan for forbidden runtime/live-wiring/aggregate/session/mutation/control/dependency/CI changes.
- Run `git diff --check`.
- Run relevant formatting/static checks for docs/status-only scope where applicable.
- Run independent code-reviewer and architect final gates.
- Record UltraQA skipped clean or pass with explicit docs/status-only rationale.
- Commit, push, and wait for GitHub Actions CI green before completion.

## Completion criteria

Story 98.1 can be marked done only when the five allowed files contain the Phase 21 opening, verification passes, independent review gates are clean, UltraQA is passed or explicitly skipped for docs/status-only scope, the change is pushed, and CI is green.

## Completion evidence

- Phase 21 PRD, architecture, epics, Story 98.1 artifact, and sprint-status updates are present.
- Local validation passed for YAML parse, exact changed-file allowlist, guardrail wording scan, and `git diff --check`.
- Focused dashboard read-only regression passed: `uv run pytest -q tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py` => 60 passed, 2 warnings.
- Independent code-reviewer APPROVE/CLEAR and architect APPROVE/CLEAR are recorded.
- UltraQA skipped clean for docs/status-only scope.
- Commit `636380fff55126fa2cc9166c5cde6cd606ff0dfa` was pushed and GitHub Actions CI run `27799413302` passed before marking this story done.
- Final Ultragoal checkpoint records the durable goal reconciliation and quality gate.
