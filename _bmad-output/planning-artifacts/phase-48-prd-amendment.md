# Phase 48 PRD Amendment — Remaining Production-Readiness Closure Portfolio

Generated: 2026-07-02T11:45:00Z

## Scope statement

Phase 48 is a comprehensive planning portfolio for the remaining deferred / fail-closed production-readiness zones after Phase 47 / Epic 126 shipped browser full selector composition. It does not implement runtime behavior by itself. It creates the BMAD epic/story backlog required to make the project production-ready once implemented.

## Product decision

Open seven production-readiness epics:

1. Epic 127 — Search, discovery, selector provenance, and controlled traversal.
2. Epic 128 — Behavior-preserving broad dashboard rewiring cleanup.
3. Epic 129 — Destructive lifecycle mutation controls.
4. Epic 130 — Object-storage lifecycle jobs and scheduled retention.
5. Epic 131 — Production operations, deployment changes, credentials, and GitHub write activation.
6. Epic 132 — Split deployment and remote Postgres horizontal scaling.
7. Epic 133 — DB connection mTLS.

## Product goals

- Convert all named deferred/fail-closed areas into implementation-ready BMAD epics and stories.
- Preserve current Phase 47 route and dashboard safety boundaries until a specific story changes them.
- Treat dangerous areas as production capabilities only when they have exact contracts, approval gates, rollback/disable behavior, tests, review, QA, and CI evidence.

## Non-goals

Phase 48 itself does not authorize runtime implementation, backend/API changes, dashboard JavaScript/HTML behavior changes, destructive lifecycle mutation, scheduled jobs, object-storage deletion, production credentials, real GitHub writes, deployment topology changes, remote Postgres rollout, DB mTLS enablement, dependencies, lockfiles, CI/deployment edits, or production operations.

## Acceptance criteria

1. A phase-scoped BMAD epic/story artifact exists and covers every user-listed deferred zone.
2. Each epic has independently valuable user outcome, FR mapping, story sequence, and testable acceptance criteria.
3. The artifact records that implementation requires future Architect/Critic consensus, code-review, UltraQA, and CI evidence per story/epic.
4. Current docs remain truthful: Phase 48 is planning, not implementation.
