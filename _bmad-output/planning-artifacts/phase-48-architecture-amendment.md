# Phase 48 Architecture Amendment — Production-Readiness Closure Boundaries

Generated: 2026-07-02T11:45:00Z

## Canonical decision

Phase 48 defines the architecture boundaries for the remaining production-readiness backlog. The complete story breakdown lives in `phase-48-production-readiness-epics.md`.

## Baseline

Phase 47 / Epic 126 is shipped/green. The current platform has narrow, explicit read boundaries for dashboard task-list selector composition. Search/discovery runtime, hidden selector policy finalization, automatic traversal/infinite scroll, broad dashboard rewiring, destructive lifecycle mutation, object-storage retention jobs, production operations, production credentials/GitHub write activation, split deployment/remote Postgres scaling, and DB connection mTLS are not yet productized.

## Architecture principles for all Phase 48 epics

1. Exact allowlists before implementation: route, selector, field, object, credential, operation, and certificate inputs must be enumerated before runtime work.
2. Fail closed by default: ambiguous, stale, missing, malformed, unauthorized, unsupported, or partially verified states must not degrade into broader behavior.
3. Visible provenance: browser/operator-facing inputs must come from visible controls or explicit commands, not hidden selectors, rows, URL/hash/storage/cookies, timers, workers, or side channels.
4. Approval and audit: destructive lifecycle mutations, production operations, GitHub writes, deployment changes, retention apply, and credential changes require explicit audit evidence; destructive actions require approval bound to exact parameters.
5. Rollback/disable: every production or destructive capability must ship with a rollback, restore, emergency disable, or documented unsupported-state guard.
6. Profile-gated infrastructure: split deployment, remote Postgres production mode, scheduled jobs, real GitHub writes, and DB mTLS are opt-in/profile-gated and must preserve local/default compatibility.
7. Closure evidence: no zone leaves deferred status until implementation, targeted tests, negative tests, docs/status updates, code-review APPROVE/CLEAR, UltraQA PASS or justified skip, and CI/nightly evidence exist.

## Deferred until implementation stories

This amendment does not authorize runtime code, dashboard behavior, mutation, scheduled jobs, deployment config, credentials, GitHub writes, remote Postgres rollout, or DB mTLS changes. It authorizes only the backlog shape and constraints.
