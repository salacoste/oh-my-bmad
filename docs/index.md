# oh-my-bmad — Documentation Index

> Master entry point for human operators and AI agents. AI agents should read [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) first; it is the rule digest. This index navigates the human-readable documentation.

## Project Overview

- **Type:** monorepo (`uv` workspace, 24 Python members) — single backend platform plus optional MCP fleet.
- **Primary language:** Python 3.12 (locked).
- **Architecture:** event-sourced, append-only JSONL event log, single-writer materialization, capability-tier-gated MCP boundaries, and replayable state.
- **Current repo state:** Phase 27 closed / Epic 106 done — Story 106.1 selected `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display, Story 106.2 implemented the narrow Lifecycle / Snapshot browser runtime boundary, and Story 106.3 recorded final closure after remote CI run [`28139358221`](https://github.com/salacoste/oh-my-bmad/actions/runs/28139358221) passed.
- **Latest tagged release:** `v1.3.0`; the checked-out branch contains later BMad work through Phase 27. [`feature-status.md`](./feature-status.md) summarizes implemented, partial, and deferred features; sprint-status remains canonical.

## Quick Reference

- **Tech stack:** Python 3.12, FastAPI, aiogram v3, SQLAlchemy 2.0 async + Alembic, MCP stdio/Streamable HTTP where explicitly configured, structlog, Hypothesis, ruff, mypy `--strict`. Exact versions live in `uv.lock`.
- **Entry points:** see [source-tree-analysis.md](./source-tree-analysis.md) §“Entry-point map”.
- **Architecture pattern:** event-sourced spine; typed events; replay/snapshot safety; optional MCP fleet; runtime adapters; transport and credential isolation.
- **Canonical status:** [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml). Derivative human-readable matrix: [`feature-status.md`](./feature-status.md).

## Core Documentation

- [Project Overview](./project-overview.md) — top-level summary, repository structure, where to start.
- [Architecture](./architecture.md) — runtime view, invariants, data flow, shipped phases, future work.
- [Source Tree Analysis](./source-tree-analysis.md) — annotated directory map + entry-point table.
- [Component Inventory](./component-inventory.md) — workspace members and deployable components.
- [Feature Status Matrix](./feature-status.md) — derivative implemented/partial/deferred feature inventory.
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
- [ADR-0025 — Event Log Lifecycle Operations](./adr/0025-event-log-lifecycle-operations.md)

## Planning Artifacts

- [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) — AI-agent rule digest.
- [`../_bmad-output/planning-artifacts/product-brief.md`](../_bmad-output/planning-artifacts/product-brief.md)
- [`../_bmad-output/planning-artifacts/prd.md`](../_bmad-output/planning-artifacts/prd.md)
- [`../_bmad-output/planning-artifacts/architecture.md`](../_bmad-output/planning-artifacts/architecture.md)
- [`../_bmad-output/planning-artifacts/epics.md`](../_bmad-output/planning-artifacts/epics.md)
- Phase amendments: [`phase-10`](../_bmad-output/planning-artifacts/phase-10-prd-amendment.md), [`phase-11`](../_bmad-output/planning-artifacts/phase-11-prd-amendment.md), [`phase-12`](../_bmad-output/planning-artifacts/phase-12-prd-amendment.md), [`phase-13`](../_bmad-output/planning-artifacts/phase-13-prd-amendment.md), [`phase-14`](../_bmad-output/planning-artifacts/phase-14-prd-amendment.md), [`phase-15`](../_bmad-output/planning-artifacts/phase-15-prd-amendment.md), [`phase-16`](../_bmad-output/planning-artifacts/phase-16-prd-amendment.md), [`phase-23`](../_bmad-output/planning-artifacts/phase-23-prd-amendment.md).
- [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml) — current state and audit trail.

## Getting Started

**Operator:** project overview → deployment guide → operator runbook → backup/restore.

**Developer:** project context → development guide → architecture → component inventory → API/data models → testing guide.

**AI agent:** project context first, then architecture/source tree, then the targeted guide for the surface being changed.

## Current shipped phases

This table is a derivative summary. Use [`../_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml) for canonical story-by-story state and [`feature-status.md`](./feature-status.md) for the implemented/partial/deferred matrix.

| Phase | Scope | Current status |
|---|---|---|
| 1 | Core platform: event spine, registry, Telegram, console, Claude worker | Done |
| 2 | Observability/security: trace_id, metrics, HMAC approvals, budgets, Litestream, supply chain | Done |
| 3 | MCP tooling fleet: git, github, verification, memory/wiki, artifact | Done |
| 4 | Browser automation plane | Done |
| 5 | Multi-runtime adapters: Codex and Gemini alongside Claude Code | Done |
| 6 | Server execution pool: Postgres, FSM, worker pool | Done |
| 7 | Reliability and operator tooling | Done |
| 8 | Platform hardening and deferred-work closure | Done |
| 9 | Operational excellence and feature completion | Done |
| 10 | Streamable HTTP transport for MCP servers | Done |
| 11 | mTLS for the internal Docker network | Done |
| 12 | Historical event replay | Done |
| 13 | Event log lifecycle management: archive manifest, hot+archive replay, package streaming | Done |
| 14 | Event log lifecycle operations: ADR-0025 operator gate, non-destructive dry-run boundary, hot-only task-history lock | Done |
| 15 | Lifecycle documentation reconciliation and backlog triage | Done |
| 16 | Archive-aware task history: read-only hot+archive history query, destructive lifecycle work still future | Done |
| 17 | Destructive lifecycle apply readiness: planning/safety contract only, no destructive apply | Done |
| 18 | Destructive lifecycle apply product scope: PRD/status-only gate plus next non-destructive candidate selection | Done |
| 19 | Read-only dashboard shell and panel/read-only guardrails | Done |
| 20 | Dashboard live-read contracts and aggregate/session unavailable decision | Done |
| 21 | Dashboard rendering readiness and live-read wiring decision gate | Done |
| 22 | Health/readiness runtime boundary for `GET /v1/health` | Done |
| 23 | Task-detail runtime boundary for `GET /v1/tasks/{task_id}` | Done: route selection, runtime boundary, and final closure recorded |
| 24 | Phase 24 — Event timeline / transitions runtime boundary for exact `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions` | Done |
| 25 | Phase 25 — Trace correlation runtime boundary for exact `GET /v1/trace/{trace_id}` | Done |
| 26 | Phase 26 — History / Replay runtime boundary for exact `GET /v1/tasks/{task_id}/history`, `GET /v1/events/replay`, and `GET /v1/events/replay/validate` with visible replay target query discipline | Done |
| 27 | Phase 27 — Lifecycle / Snapshot runtime boundary for exact `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display | Done: Story 106.3 records Phase 27 / Epic 106 final closure with remote CI run `28139358221` |

## Scope of this refresh

- **Mode:** documentation canonicalization after Phase 27 lifecycle/snapshot runtime-boundary work.
- **Date:** 2026-06-25.
- **Project type:** backend monorepo / autonomous development platform.
- **State file:** [project-scan-report.json](./project-scan-report.json) may lag this index; use sprint-status as canonical.
- **Deferred boundaries after Phase 27:** aggregate/session/digest, task-list/search/discovery, mutation/control, broad dashboard wiring beyond approved narrow route families, destructive apply, object-storage lifecycle jobs, scheduled retention, production credential-gated writes, snapshot creation including `POST /v1/events/replay/snapshots`, lifecycle apply/prune/rollback, and services/MCP/dependencies/CI changes remain future separate-story work.
