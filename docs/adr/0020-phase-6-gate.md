---
id: ADR-0020
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0020: Phase 6 gate — formally opens `phase: 6` for `main`-branch merges

## Status

**Accepted** — 2026-06-07. Phase 5 shipped (Epics 26-29 `done` — RuntimeAdapter protocol, CodexRunner, per-task runtime selection, runtime handoff, per-runtime budget tracking, fleet smoke test). ADR-0015 and ADR-0016 both accepted. Phase 5 retrospective produced 5 lessons and 4 carry-forward items (CF2, CF3 resolved; CF1 live Codex validation and CF4 epic granularity carry forward). Phase 6 brainstorming converged on D1-D5 and planning artifacts are complete.

## Context

Phase 5 of oh-my-bmad shipped on 2026-06-07 as the multi-runtime plane (Epics 26-29 `done` — RuntimeAdapter protocol, CodexRunner, per-task runtime selection, runtime handoff, per-runtime budget tracking, fleet smoke test). ADR-0015 (RuntimeAdapter protocol) and ADR-0016 (Phase 5 gate) are both accepted.

Phase 5 retrospective produced 5 lessons and 4 carry-forward items:
- CF1: Live Codex validation (open)
- CF2: Token model docs (RESOLVED)
- CF3: Pre-push lint gate (RESOLVED)
- CF4: Epic granularity (open)

Phase 6 brainstorming converged on D1-D5. Scope decisions:
- **IN**: Postgres migration, task state machine, multi-task parallelism, Gemini adapter.
- **OUT**: Remote MCP, mTLS, split deployment, GLM, web dashboard, scheduled jobs.

Phase 6 planning artifacts:
- [`phase-6-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-6-prd-amendment.md) — FR99-FR107, NFR-O14-O16, NFR-M11-M12, NFR-S15-S16, NFR-R11-R12.
- [`brainstorming-session-2026-06-07-phase6-server-execution-pool.md`](../../_bmad-output/brainstorming/brainstorming-session-2026-06-07-phase6-server-execution-pool.md) — convergence decisions D1-D5.

## Decision

1. **Phase 6 is formally open for `main`-branch merges once this ADR is accepted.** Stories carrying `phase: 6` may then transition through the normal workflow and merge via the standard PR gate. `sprint-status.yaml` increments to `current_phase: 6` at acceptance.

2. **The Phase 6 baseline scope is the server execution pool:**
   - **Epic 30 (Postgres migration)** — FR99-FR101, lands **first** (foundation for parallelism).
   - **Epic 31 (Task state machine)** — FR102-FR103, lands **second** (prerequisite for worker pool).
   - **Epic 32 (Multi-task parallelism)** — FR104-FR106, lands **third** (depends on Epics 30+31).
   - **Epic 33 (Gemini adapter)** — FR107, can partially parallelize with Epic 32.
   - **Epic 34 (CI hardening + finalization)** — ship-blocker verification.

3. **Forward-referenced ADRs** accepted alongside this gate:
   - **ADR-0017** — Postgres migration strategy.
   - **ADR-0018** — Task state machine.
   - **ADR-0019** — Worker pool assignment.
   - **ADR-0020** — this document.

4. **Phase 1-5 invariants are non-negotiable in Phase 6.** Every PR preserves all prior invariants (FR26 single-writer, stdio-only MCP transport, event-only telemetry, `trace_id` propagation, tier-enforced authz, supply-chain triumvirate, credential isolation, budget per-runtime). A Phase-6 PR violating a prior invariant is rejected at review regardless of merits.

5. **Five new Phase-6 invariants are non-negotiable:**
   - **P6-I1:** Backward compatibility — SQLite remains the default; Postgres is opt-in via `WORKER_DB_URL`. All tests pass on both backends.
   - **P6-I2:** Single-task-per-worker invariant preserved — each worker handles exactly one task at a time. Parallelism comes from multiple workers, not from multiplexing within a worker.
   - **P6-I3:** Event-driven state transitions — all task state changes emit events. No direct DB mutations bypassing the event spine.
   - **P6-I4:** Worker identity — every worker has a unique `worker_id` surfaced in events and metrics. No anonymous workers.
   - **P6-I5:** Credential isolation extends to Gemini — `GOOGLE_API_KEY` appears only in `GeminiRunner`'s allowlist. Same discipline as P5-I1.

6. **Phase 6 ship criterion** is the green Phase-6 Ship-Blocker Checklist (14 items in [`phase-6-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-6-prd-amendment.md) §"Phase 6 Ship-Blocker Checklist"). Phase 6 has not shipped until every gate there is green.

## Consequences

- **Positive:** Multi-task parallelism is the most operator-visible improvement since Phase 1 — operators can run N tasks concurrently across N workers.
- **Positive:** Postgres unblocks split deployment in Phase 7 (SQLite single-writer is the deployment-coupling root cause).
- **Positive:** Formal state machine resolves 5-phase technical debt — task lifecycle has been implicit since Phase 1; explicit states prevent edge-case bugs.
- **Risk:** Postgres migration is the largest single infrastructure change in project history — dual-backend support adds CI matrix complexity and schema migration discipline.
- **Risk:** Multi-task parallelism introduces concurrency edge cases (worker assignment, resource contention, cancellation cascades) not present in single-task execution.
- **`main` carries mixed `phase: 5`-`done` and `phase: 6`-`in-progress` work**; the `phase:` label is the canonical distinguishing field. No long-lived phase branch.
- **A retrospective is required at every Phase-6 epic boundary** (project-context Cat 6), landing in `_bmad-output/implementation-artifacts/epic-<n>-retro-<date>.md`.

## Linked artifacts

- [`phase-6-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-6-prd-amendment.md) — FR99-FR107 + NFRs + ship-blocker checklist.
- [`brainstorming-session-2026-06-07-phase6-server-execution-pool.md`](../../_bmad-output/brainstorming/brainstorming-session-2026-06-07-phase6-server-execution-pool.md) — convergence decisions D1-D5.
- ADR-0017 — Postgres migration strategy.
- ADR-0018 — Task state machine.
- ADR-0019 — Worker pool assignment.
- ADR-0016 — Phase 5 gate (precedent for this document's structure).
- ADR-0015 — Multi-runtime adapter protocol.

— *R2d2, 2026-06-07 (proposed; via the BMad Phase-6 planning chain).*
