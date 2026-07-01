# Story 125.4 — Broad Dashboard Wiring Cleanup Further Planning

Status: further planning complete
Scope: docs/status-only; no broad runtime cleanup
Context snapshot: `.omx/context/start-story-125-2-planning-implementation-125-3-20260701T142650Z.md`
Canonical architecture source: `../planning-artifacts/phase-46-architecture-amendment.md`

## Decision

Broad dashboard wiring cleanup remains high-risk and unselected for broad runtime rewiring. Story 125.4 further planning narrows any future starting point to inventory and behavior-preserving test guards only.

## Required future first step

A later cleanup story must begin with:

- inventory of existing dashboard modules/contracts;
- classification of live vs dead wiring;
- behavior-preserving regression tests before edits;
- narrow file-level cleanup slices;
- explicit non-goals for broad rewiring, generated live data, service/dependency changes, CI/deployment changes, credentials, and production operations.

## Non-authorization statement

This story does not add dashboard runtime cleanup, broad rewiring, backend/API behavior, browser behavior, generated data, hidden selectors, automatic traversal, dependencies, services/MCP, CI/deployment, credentials, or production operations.
