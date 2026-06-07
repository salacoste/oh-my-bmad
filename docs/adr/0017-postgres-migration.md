---
id: ADR-0017
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0017: Postgres migration strategy — dual-backend registry with SQLite default

## Status

**Accepted** — 2026-06-07. Phase 5 is complete (all 4 epics, Epics 26–29 shipped; multi-runtime support live). Unblocks Phase 6 multi-task parallelism. Follows the ADR-0007 (Litestream WAL replication) precedent for database-layer decisions.

## Context

Phase 5 shipped on 2026-06-07 (ADR-0016 accepted; Epics 26–29 `done`; multi-runtime support across Claude Code + Codex; see [`epics.md`](../../_bmad-output/planning-artifacts/epics.md)). The architecture uses SQLite with WAL mode for all registry state — `registry-api`, `registry-state`, and `metrics-subscriber` all access the same database file through SQLAlchemy ORM.

This works well for the single-operator deployment profile. However, Phase 6 scope (per the PRD) introduces multi-task parallelism: multiple workers executing tasks concurrently on different runtimes. SQLite's single-writer constraint means only one worker can hold a write lock at a time, serialising all registry mutations. Under concurrent multi-task workloads this becomes a throughput bottleneck and a source of `SQLITE_BUSY` contention errors.

The existing SQLAlchemy ORM layer already abstracts most SQL dialect specifics. Alembic manages schema migrations (8 revisions, 0001–0008). The PRD explicitly names a Postgres upgrade as Phase 6 scope.

## Decision

### D1: Dual-backend support

Both SQLite and Postgres are supported as registry backends. Selection is via the `REGISTRY_DATABASE_URL` environment variable:

- Unset or `sqlite+aiosqlite:///...` → SQLite (existing behavior, zero-change default).
- `postgresql+asyncpg:///...` → Postgres.

No deployment breaks on upgrade — the default remains SQLite. Existing single-operator setups require no action.

### D2: Connection pooling

For Postgres, the session factory uses SQLAlchemy's `create_async_engine` with the `asyncpg` driver. Pool size formula: `5 + 2 * num_workers` (where `num_workers` is the configured worker count). For SQLite, the existing `aiosqlite` session factory is retained unchanged.

### D3: Alembic migrations are backend-agnostic

The existing 8 Alembic migrations (0001–0008) are re-validated against Postgres. All new migrations use SQLAlchemy Core operations (not raw SQL) so they execute correctly against either backend. The operator-facing command is `just migrate`, which runs Alembic against whichever backend `REGISTRY_DATABASE_URL` selects.

### D4: CI strategy

A new `postgres` job in `.github/workflows/ci.yml` runs the full test suite against a Postgres service container. The existing SQLite job remains the primary CI gate. Both jobs must pass before merge.

## Consequences

- **Positive:** Multi-task parallelism is unblocked — Postgres row-level locking eliminates the single-writer bottleneck that SQLite WAL imposes.
- **Positive:** Production deployments get stronger durability guarantees (Postgres write-ahead logging, point-in-time recovery).
- **Positive:** Path to split deployment — Postgres accessible from multiple hosts enables horizontal scaling of the registry layer.
- **Negative:** Postgres is an additional operational dependency. Operators choosing Postgres must manage a Postgres container (or external instance), including backups, upgrades, and connection security.
- **Negative:** Some SQLite-specific optimizations (`PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout`, `PRAGMA synchronous=NORMAL`) require backend-conditional code paths or removal.
- **Neutral:** The test suite effectively doubles in CI wall-time (full suite runs against both backends). The `postgres` job runs in parallel with the SQLite job, so the merge-blocking delay is the slower of the two, not the sum.

## Alternatives considered

- **Postgres-only, drop SQLite.** Rejected — breaks every existing single-operator deployment. The dual-backend approach preserves zero-change upgrades (same rationale as ADR-0007 keeping Litestream optional).
- **SQLite with optimistic concurrency (retry on SQLITE_BUSY).** Rejected — retries mask contention but do not eliminate the serialisation bottleneck. Under sustained multi-task workloads, retry storms degrade throughput and increase tail latency. Postgres MVCC is the correct solution for concurrent writes.
- **External lock service (e.g., Redis) coordinating SQLite writes.** Rejected — adds a second operational dependency (Redis) to work around the first dependency's limitation (SQLite single-writer). The complexity budget is better spent on Postgres, which solves the problem natively.
- **Turso / libSQL (distributed SQLite).** Rejected — introduces a new database technology to the stack. Postgres is a well-understood operational choice with mature tooling. The SQLAlchemy layer already provides sufficient abstraction.

## Linked artifacts

- ADR-0007 — Litestream WAL replication (precedent for database-layer decisions).
- ADR-0016 — Phase 5 gate (prerequisite: Phase 5 complete).
- ADR-0015 — Multi-runtime adapter protocol (Phase 5 architecture, sets the stage for multi-task parallelism).
- [`phase-5-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-prd-amendment.md) — Postgres upgrade explicitly scoped to Phase 6.

— *R2d2, 2026-06-07 (accepted).*
