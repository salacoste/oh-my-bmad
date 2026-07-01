# Phase 46 Epics — Dashboard Task-list Expansion Planning Matrix

Generated: 2026-06-30T21:44:33Z

## Phase 46 theme

Phase 46 opens Epic 125 as a docs/status-only planning matrix for four aggregate-task-list/dashboard follow-up candidates requested by the operator. The canonical contract source is `phase-46-architecture-amendment.md`.

## Epic 125 — Dashboard task-list expansion planning matrix

### Objective

Plan four follow-up points one by one after Phase 45 closed API-local finite sort vocabulary support: browser sort vocabulary expansion, sort composition, task-list search/discovery, and broad dashboard wiring cleanup. Only the browser vocabulary expansion is selected as a future runtime candidate in this phase; the higher-risk candidates remain planning/deferred until separate contracts exist.

### Story 125.1 — Task list sort browser vocabulary planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Select the next future browser/dashboard candidate: visible aggregate-task-list sort controls may expose exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc`, using the already implemented API-local finite sort vocabulary, with no sort composition, no search/discovery, no hidden selectors, no automatic traversal, and no backend/API changes.

**Scope:** docs/status-only.

### Story 125.2 — Task list sort composition planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Record a future API-local-only composition candidate for `status` + `limit` + `offset` + `sort` with finite/bounded selector domains and one canonical raw query order. This story does not authorize implementation or browser composition.

**Scope:** docs/status-only.

### Story 125.3 — Task list search/discovery planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Classify search/discovery as high-risk and keep it unselected for runtime in Phase 46. A later phase must define exact fields, grammar, authority/freshness/provenance, privacy, interaction with sort/pagination, and adversarial tests.

**Scope:** docs/status-only.

### Story 125.4 — Broad dashboard wiring cleanup planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Classify broad dashboard wiring cleanup as high-risk and keep broad implementation unauthorized. A later phase may start with inventory and behavior-preserving test guards only.

**Scope:** docs/status-only.

## Dependency and sequencing notes

1. Story 125.1 is the only selected future runtime candidate from Phase 46.
2. Story 125.2 must remain API-local and separate from browser work until another consensus gate selects implementation.
3. Stories 125.3 and 125.4 are planning classifications only and must not be treated as runtime authorization.
4. No implementation story may start without a new tests-first plan and Architect then Critic consensus for that exact boundary.
