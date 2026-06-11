# oh-my-bmad — Documentation Index

> Master entry point for human operators and AI agents. AI agents should read [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) first; it is the rule digest. This index navigates the human-readable documentation.

## Project Overview

- **Type:** monorepo (`uv` workspace, 24 Python members) — single backend platform plus optional MCP fleet.
- **Primary language:** Python 3.12 (locked).
- **Architecture:** event-sourced, append-only JSONL event log, single-writer materialization, capability-tier-gated MCP boundaries, and replayable state.
- **Current repo state:** Phase 13 complete as of 2026-06-10 — Event Log Lifecycle Management after Historical Event Replay.
- **Latest tagged release:** `v1.3.0`; the checked-out `main` branch contains later Phase 10–13 work.

## Quick Reference

- **Tech stack:** Python 3.12, FastAPI, aiogram v3, SQLAlchemy 2.0 async + Alembic, MCP stdio/Streamable HTTP where explicitly configured, structlog, Hypothesis, ruff, mypy `--strict`. Exact versions live in `uv.lock`.
- **Entry points:** see [source-tree-analysis.md](./source-tree-analysis.md) §“Entry-point map”.
- **Architecture pattern:** event-sourced spine; typed events; replay/snapshot safety; optional MCP fleet; runtime adapters; transport and credential isolation.
- **Canonical status:** [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml).

## Core Documentation

- [Project Overview](./project-overview.md) — top-level summary, repository structure, where to start.
- [Architecture](./architecture.md) — runtime view, invariants, data flow, shipped phases, future work.
- [Source Tree Analysis](./source-tree-analysis.md) — annotated directory map + entry-point table.
- [Component Inventory](./component-inventory.md) — workspace members and deployable components.
- [API Contracts](./api-contracts.md) — HTTP endpoints + MCP tool catalog + Telegram surface.
- [Data Models](./data-models.md) — event envelope, replay/archive contracts, registry-state DB schema.
- [Development Guide](./development-guide.md) — AI-context entry into the dev workflow.
- [Testing Guide](./testing-guide.md) — test-tree layout + harness usage + contract-fixture recording workflow.
- [Deployment Guide](./deployment-guide.md) — deployment entry point.
- [Operator Runbook](./operator-runbook.md) — paging conditions + recovery / lifecycle playbooks.
- [Backup / Restore](./backup-restore.md) — volume snapshot + Litestream restore.
- [Schema Evolution](./schema-evolution.md) — add event types, migrators, replay compatibility.
- [Exceptions](./exceptions.md) — documented naming-rule + convention exceptions.
- [BMad Workflow](./bmad-workflow.md) — process companion to the rule digest.

## Deployment Runbooks

- [VPS (Linux)](./deployment/vps.md)
- [macOS (local host)](./deployment/macos.md)

## Architecture Decision Records

- [ADR-0001 — Allowlist Middleware Auth](./adr/0001-allowlist-middleware-auth.md)
- [ADR-0002 — Integration Test Harness](./adr/0002-integration-test-harness.md)
- [ADR-0003 — Phase-2 Gate](./adr/0003-phase-2-gate.md)
- [ADR-0004 — trace_id Propagation](./adr/0004-trace-id-propagation.md)
- [ADR-0005 — Metrics Subscriber Derived Projection](./adr/0005-metrics-subscriber-derived-projection.md)
- [ADR-0006 — Approval Signing and Rotation Protocol](./adr/0006-approval-signing-and-rotation-protocol.md)
- [ADR-0007 — Litestream WAL Replication](./adr/0007-litestream-wal-replication.md)
- [ADR-0008 — Cosign / SLSA / SBOM](./adr/0008-cosign-slsa-sbom.md)
- [ADR-0009 — Phase-3 Gate](./adr/0009-phase-3-gate.md)
- [ADR-0010 — MCP Server Authoring](./adr/0010-mcp-server-authoring.md)
- [ADR-0011 — Artifact Store](./adr/0011-artifact-store.md)
- [ADR-0012 — Memory / Wiki Store](./adr/0012-memory-wiki-store.md)
- [ADR-0013 — Playwright MCP Transport](./adr/0013-playwright-mcp-transport.md)
- [ADR-0014 — Phase-4 Gate](./adr/0014-phase-4-gate.md)
- [ADR-0015 — Multi-Runtime Adapter](./adr/0015-multi-runtime-adapter.md)
- [ADR-0016 — Phase-5 Gate](./adr/0016-phase-5-gate.md)
- [ADR-0017 — Postgres Migration](./adr/0017-postgres-migration.md)
- [ADR-0018 — Task State Machine](./adr/0018-task-state-machine.md)
- [ADR-0019 — Worker Pool Assignment](./adr/0019-worker-pool-assignment.md)
- [ADR-0020 — Phase-6 Gate](./adr/0020-phase-6-gate.md)
- [ADR-0021 — API Versioning](./adr/0021-api-versioning.md)
- [ADR-0022 — Remote MCP Transport](./adr/0022-remote-mcp-transport.md)
- [ADR-0023 — mTLS Internal Network](./adr/0023-mtls-internal-network.md)
- [ADR-0024 — Historical Event Replay](./adr/0024-historical-event-replay.md)

## Planning Artifacts

- [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) — AI-agent rule digest.
- [`../_bmad-output/planning-artifacts/product-brief.md`](../_bmad-output/planning-artifacts/product-brief.md)
- [`../_bmad-output/planning-artifacts/prd.md`](../_bmad-output/planning-artifacts/prd.md)
- [`../_bmad-output/planning-artifacts/architecture.md`](../_bmad-output/planning-artifacts/architecture.md)
- [`../_bmad-output/planning-artifacts/epics.md`](../_bmad-output/planning-artifacts/epics.md)
- Phase amendments: [`phase-10`](../_bmad-output/planning-artifacts/phase-10-prd-amendment.md), [`phase-11`](../_bmad-output/planning-artifacts/phase-11-prd-amendment.md), [`phase-12`](../_bmad-output/planning-artifacts/phase-12-prd-amendment.md), [`phase-13`](../_bmad-output/planning-artifacts/phase-13-prd-amendment.md).
- [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml) — current state and audit trail.

## Getting Started

**Operator:** project overview → deployment guide → operator runbook → backup/restore.

**Developer:** project context → development guide → architecture → component inventory → API/data models → testing guide.

**AI agent:** project context first, then architecture/source tree, then the targeted guide for the surface being changed.

## Current shipped phases

| Phase | Scope |
|---|---|
| 1 | Core platform: event spine, registry, Telegram, console, Claude worker |
| 2 | Observability/security: trace_id, metrics, HMAC approvals, budgets, Litestream, supply chain |
| 3 | MCP tooling fleet: git, github, verification, memory/wiki, artifact |
| 4 | Browser automation plane |
| 5 | Multi-runtime adapters: Codex and Gemini alongside Claude Code |
| 6 | Server execution pool: Postgres, FSM, worker pool |
| 7 | Reliability and operator tooling |
| 8 | Platform hardening and deferred-work closure |
| 9 | Operational excellence and feature completion |
| 10 | Streamable HTTP transport for MCP servers |
| 11 | mTLS for the internal Docker network |
| 12 | Historical event replay |
| 13 | Event log lifecycle management: archive manifest, hot+archive replay, package streaming |

## Scope of this refresh

- **Mode:** documentation canonicalization after Phase 13 reconciliation.
- **Date:** 2026-06-11.
- **Project type:** backend monorepo / autonomous development platform.
- **State file:** [project-scan-report.json](./project-scan-report.json) may lag this index; use sprint-status as canonical.
