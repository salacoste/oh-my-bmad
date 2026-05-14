# oh-my-bmad — Documentation Index

> Master entry point for both human operators and AI agents working on this codebase. AI agents should read [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) FIRST — it is the rule digest. This index navigates the human-readable documentation.

## Project Overview

- **Type:** monorepo (uv workspace, 14 members) — single unified backend platform.
- **Primary Language:** Python 3.12 (locked).
- **Architecture:** event-sourced, single-writer SQLite WAL + append-only JSONL; typed event spine connecting operator surfaces (Telegram + console) to a Claude Code worker via an orchestrator adapter.
- **Status:** Phase 1 shipped — 10 epics, 88 stories done (as of 2026-05-15).

## Quick Reference

- **Tech stack** Python 3.12, FastAPI, aiogram v3, SQLAlchemy 2.0 async + Alembic, MCP stdio, structlog, hypothesis, ruff, mypy `--strict`. Exact versions in `uv.lock`.
- **Entry points** see [source-tree-analysis.md](./source-tree-analysis.md) §"Entry-point map".
- **Architecture pattern** event-sourced with single-writer projection; typed spine; capability-tier-gated MCP boundaries; vendored upstream forks behind adapter shims.

## Generated Documentation (this scan)

- [Project Overview](./project-overview.md) — top-level summary, repository structure, where-to-start.
- [Architecture](./architecture.md) — operator-oriented runtime view, invariants, data flow.
- [Source Tree Analysis](./source-tree-analysis.md) — annotated directory map + entry-point table.
- [Component Inventory](./component-inventory.md) — the 14 workspace members catalogued.
- [API Contracts](./api-contracts.md) — HTTP endpoints + MCP tool catalog + Telegram surface.
- [Data Models](./data-models.md) — event envelope, payload catalog, registry-state DB schema.
- [Development Guide](./development-guide.md) — AI-context entry into the dev workflow.
- [Deployment Guide](./deployment-guide.md) — AI-context entry into deployment.

## Existing Operator Documentation

- [Operator Runbook](./operator-runbook.md) — paging conditions + per-service recovery playbooks.
- [Schema Evolution](./schema-evolution.md) — add an event type + ship a migrator + roll-back procedure.
- [Exceptions](./exceptions.md) — documented naming-rule + convention exceptions (scaffold replacement map, MCP triple-naming rationale, suppression-tag registry).
- [Testing Guide](./testing-guide.md) — test-tree layout + harness usage + contract-fixture recording workflow.
- [Backup / Restore](./backup-restore.md) — volume snapshot + off-host rsync + fresh-host restore.
- [Message Design](./message-design.md) — Telegram template catalog + character budgets.
- [Renderer Conventions](./RENDERER_CONVENTIONS.md) — Telegram/console renderer output conventions.
- [Development](./development.md) — tooling quirks rediscovered across Epics 1–3 (`uv sync` variants, `mypy_path` form, etc.). **Read this when stuck.**

## Deployment Runbooks

- [VPS (Linux)](./deployment/vps.md)
- [macOS (local host)](./deployment/macos.md)

## Architecture Decision Records

- [ADR-0001 — Allowlist Middleware Auth](./adr/0001-allowlist-middleware-auth.md) — accepted
- [ADR-0002 — Integration Test Harness](./adr/0002-integration-test-harness.md) — accepted

## Planning Artifacts (outside `docs/`)

The original solution-design lives under `_bmad-output/`. AI agents writing new stories or extending the architecture should consult these for the *why* behind the rules in `project-context.md`.

- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) — **AI-agent rule digest (Cats 1–7).** Required reading before writing code.
- [`_bmad-output/planning-artifacts/product-brief.md`](../_bmad-output/planning-artifacts/product-brief.md)
- [`_bmad-output/planning-artifacts/prd.md`](../_bmad-output/planning-artifacts/prd.md)
- [`_bmad-output/planning-artifacts/architecture.md`](../_bmad-output/planning-artifacts/architecture.md) — full decision rationale, FR/NFR mapping, starter-template evaluation.
- [`_bmad-output/planning-artifacts/epics.md`](../_bmad-output/planning-artifacts/epics.md) — full backlog + MVP ship-blocker checklist.
- [`_bmad-output/implementation-artifacts/sprint-status.yaml`](../_bmad-output/implementation-artifacts/sprint-status.yaml) — current state.

## Getting Started

**Operator (just wants to run it):**
1. [project-overview.md](./project-overview.md) — orient (5 min).
2. [deployment-guide.md](./deployment-guide.md) → [deployment/vps.md](./deployment/vps.md) or [deployment/macos.md](./deployment/macos.md) — set up the stack.
3. [operator-runbook.md](./operator-runbook.md) — bookmark for paging / recovery.
4. [backup-restore.md](./backup-restore.md) — set up backups.

**Developer (writing code):**
1. [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) — the rule digest.
2. [development-guide.md](./development-guide.md) — workflow entry point.
3. [development.md](./development.md) — tooling quirks (read when bitten).
4. [architecture.md](./architecture.md) → [component-inventory.md](./component-inventory.md) → [api-contracts.md](./api-contracts.md) → [data-models.md](./data-models.md) — system mental model.
5. [testing-guide.md](./testing-guide.md) — test harness usage.

**AI agent (new context window):**
1. Read [`../_bmad-output/project-context.md`](../_bmad-output/project-context.md) FIRST — it's the rule digest (Cats 1–7).
2. Then read [architecture.md](./architecture.md) for the system mental model.
3. Then [source-tree-analysis.md](./source-tree-analysis.md) for the directory layout.
4. For specific work, follow the targeted sections in [development-guide.md](./development-guide.md) (adding a new event type, HTTP endpoint, MCP tool, workspace member).
5. When in doubt, follow Cat 7 §"When in doubt" — emit a `BLOCKED` event, never guess.

## Scope of this scan

- **Mode:** initial scan, deep level (files read from critical directories).
- **Date:** 2026-05-15.
- **Project type:** backend (single-part monorepo, unified backend platform).
- **State file:** [project-scan-report.json](./project-scan-report.json).
