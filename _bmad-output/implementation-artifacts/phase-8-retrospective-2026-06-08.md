---
id: phase-8-retrospective
date: 2026-06-08
phase: 8
status: COMPLETE
title: "Phase 8 Retrospective — Platform Hardening & Debt Resolution"
---

# Phase 8 Retrospective — Platform Hardening & Debt Resolution

**Date:** 2026-06-08
**Phase:** 8 (final closure phase)
**Status:** COMPLETE

## Executive Summary

Phase 8 resolved all 20 open GATED deferred-work items, fixed all CI gate failures, and landed one critical security hardening (per-server env scoping). The deferred-work backlog is now at **zero open GATED items** — the project is architecturally clean.

## Epics Completed

| Epic | Theme | Stories | Status |
|------|-------|---------|--------|
| 41 | API Contract Formalization | 3 | COMPLETE |
| 42 | Operator Configuration Surface | 0 (closed as WONTDO/OPS) | COMPLETE |
| 43 | Security Defense-in-Depth | 3 | COMPLETE |
| 44 | Optional Feature Candidates | 0 (deferred to future) | SKIPPED |
| 45 | Deferred Work Backlog Closure | 2 | COMPLETE |

**Total stories executed:** 8 (6 implemented, 2 documentation-only)

## Ship-Blocker Checklist

| # | Item | Status |
|---|------|--------|
| 1 | CI gates green (ruff 0 errors, mypy 0 errors, tests pass) | ✅ GREEN |
| 2 | ADR-0021 (API versioning) accepted | ✅ ACCEPTED |
| 3 | Events composite index migrated (Alembic 0010) | ✅ MIGRATED |
| 4 | State machine GATED items closed with Phase 7 FSM docs | ✅ CLOSED (3 items) |
| 5 | Per-server env scoping defense-in-depth implemented | ✅ IMPLEMENTED |
| 6 | Deferred-work.md has zero open GATED items | ✅ ZERO |
| 7 | Phase 1-7 invariants regression-free | ✅ VERIFIED (3156 tests pass) |
| 8 | Phase 8 retrospective produced | ✅ THIS DOCUMENT |

**8/8 ship-blocker items GREEN.**

## Key Deliverables

### 1. CI Gate Recovery (pre-Phase 8 hygiene)

Before Phase 8 could start, the CI gates were red:
- **Ruff:** 57 errors → 0 errors (38 files fixed)
- **Mypy:** 162 errors → 0 errors in 326 source files (38 files fixed)
- **Tests:** 3101 passed, 0 failed

### 2. ADR-0021: API Versioning Strategy

Establishes the versioning rules for registry-api:
- Additive-only within v1 (clients must ignore unknown fields)
- New endpoints are free under v1
- v2 requires a new ADR
- response_model is opt-in for existing endpoints
- URL-path versioning only (no content negotiation)

### 3. Events Composite Index (Alembic migration 0010)

Added `ix_events_task_id_emitted_at_monotonic_ns` composite index on the events table to optimise the CLI follow-mode pagination query. The existing `ix_events_task_id_emitted_at` covers wall-clock time; this covers monotonic nanosecond cursors.

### 4. Per-Server Environment Scoping (G-SEC-2 defense-in-depth)

Each MCP child process now receives only `_BASE_ENV_VARS` plus its own server-specific vars. Previously, `GITHUB_MCP_SCOPED_TOKEN` (a repo-scoped GitHub PAT) was forwarded to all 9 MCP children. Now only the `github` MCP server receives it. Contract test `test_per_server_env_isolation_github_scoped_token` validates the isolation.

### 5. Deferred Work Backlog Closure

All 20 GATED items resolved:

| Disposition | Count | Examples |
|-------------|-------|---------|
| CLOSED | 8 | State machine (Phase 7 FSM), events index (migration 0010), module resolution (already fixed), response_model (ADR-0021) |
| WONTDO | 12 | Lock TOCTOU (single-operator), filesystem corruption (os.replace atomic), auth at infra layer, GitHub write tools (simulate=True), default-open policy, dedup architecture, etc. |

**Zero open GATED items remain.** The deferred-work backlog is clean.

## What Was NOT Done (and why)

| Item | Reason |
|------|--------|
| Epic 44: Worker pool auto-scaling (FC-P6-1) | Optional feature; manual `--scale` works fine. Revisit if scaling friction becomes tangible. |
| Epic 44: Gemini structured output (FC-P6-2) | Optional feature; best-effort parsing works. Revisit if multi-runtime reliability degrades. |
| Epic 42: GitHub write tools (real credentials) | Requires operator to provision GitHub credentials. Code already supports it via config gate (`GITHUB_MCP_WRITE_ENABLED`). |

These items are explicitly **not carried forward** — they are future scope, not blockers.

## Metrics

| Metric | Before Phase 8 | After Phase 8 |
|--------|---------------|---------------|
| Ruff errors | 57 | 0 |
| Mypy errors | 162 | 0 |
| Test count | 3101 | 3156 |
| GATED deferred items | 20 | 0 |
| ADRs | 20 | 21 |
| Alembic migrations | 9 | 10 |

## Project Completion Summary

**Phases 1-8 are COMPLETE.** The platform is production-ready for a single operator:

- **40+ epics, ~187 stories** delivered across 8 phases
- **3156 tests passing** (unit, integration, contract, ATDD)
- **0 lint errors, 0 type errors** across 326 source files
- **0 open deferred items** — architectural debt resolved
- **Defense-in-depth** security: scoped credentials, per-server env isolation, HMAC approval signing, allowlist-gated child environments
- **Self-healing:** automatic recovery loops, stale task detection, dead session detection, priority-aware dispatch
- **Full audit trail:** every state transition emits a `task.state_transition` audit event
- **API stability:** ADR-0021 establishes versioning rules, v1 surface is frozen

## Lessons Learned

1. **CI hygiene compounds.** The 57 ruff + 162 mypy errors accumulated because they weren't caught locally before commits. The CI caught them, but local pre-commit hooks would prevent the drift entirely.

2. **Deferred items cluster into themes.** The 20 GATED items weren't random — they clustered into 5 clear themes (API versioning, lock protocol, operator config, security, test infrastructure). Treating them as a phase rather than individual fixes was more efficient.

3. **Most GATED items resolve as WONTDO.** 12 of 20 items were WONTDO at single-operator scale. The architectural gatekeeping was correct — they were deferred, not forgotten — and the closure decision was clean because the deferral rationale was well-documented.

4. **Security hardening is a gradient, not a binary.** G-SEC-2 started as "broad token reaches children", became "scoped token reaches children", and is now "scoped token reaches only the server that needs it". Each step reduces blast radius without requiring a full security redesign.

---

*Phase 8 COMPLETE. Project ready for production deployment.*
*R2d2, 2026-06-08.*
