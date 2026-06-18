# Story 97.1 — Aggregate overview/session-list contract decision

Status: done

## Decision

Aggregate overview and session-list live dashboard reads remain
`unavailable` / `needs-contract` for Phase 20.

No new aggregate/session-list GET contract is proposed or implemented in this
story.

## Rationale

Phase 20 architecture states aggregate task/session lists have no approved Phase
20 contract yet and must render unavailable/needs-contract until separately
approved. A safe future aggregate/session-list contract would need pagination,
freshness, provenance, no-hidden-write proof, no background-dispatch proof, and
no cache-warming side-effect proof before any implementation.

## Evidence

- PRD: `_bmad-output/planning-artifacts/phase-20-prd-amendment.md`
  - FR178 requires unavailable/needs-contract for missing aggregate reads.
  - New aggregate task/session GET contracts require a later architecture gate
    and test-first story.
- Architecture: `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
  - Decision 3 says aggregate/session reads are unavailable until separately
    approved.
- Tests:
  - `tests/dashboard/test_live_read_contracts.py`
  - `tests/dashboard/test_live_read_state_contracts.py`
  - `tests/dashboard/test_live_read_adapter.py`
  - `tests/dashboard/test_phase20_final_validation.py`

## Non-goals

- No aggregate/session live wiring.
- No aggregate/session route expansion.
- No aggregate/session data synthesis from logs, event-spine guesses, unsafe
  discovery, or side-effectful reads.
- No background dispatch, cache warming, hidden writes, mutation/control
  behavior, digest integration, dependencies, or CI/deployment changes.
