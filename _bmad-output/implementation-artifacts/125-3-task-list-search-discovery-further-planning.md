# Story 125.3 — Task List Search Discovery Further Planning

Status: further planning complete
Scope: docs/status-only; no runtime implementation
Context snapshot: `.omx/context/start-story-125-2-planning-implementation-125-3-20260701T142650Z.md`
Canonical architecture source: `../planning-artifacts/phase-46-architecture-amendment.md`

## Decision

Task-list search/discovery remains high-risk and unselected for runtime. Story 125.3 further planning records the future decision inputs required before any implementation may be considered.

## Required future decision inputs

A later product/architecture gate must define:

- exact searchable fields;
- exact query grammar and encoding policy;
- minimum/maximum query lengths;
- authority, freshness, provenance, and privacy redaction semantics;
- interaction with status/limit/offset/sort selectors;
- failure modes and adversarial tests;
- explicit browser/API authorization boundaries.

## Non-authorization statement

This story does not add search/discovery runtime, backend/API behavior, browser/dashboard behavior, generated data, hidden selectors, automatic traversal, arbitrary query grammar, dependencies, services/MCP, CI/deployment, credentials, or production operations.
