# Story 125.3 — Task List Search Discovery Implementation-Planning Gate

Status: implementation planning complete; runtime remains unauthorized
Scope: docs/status-only decision contract
Context snapshot: `.omx/context/story-125-3-search-discovery-125-4-inventory-test-guard-20260701T161249Z.md`
Deep-interview handoff: `.omx/interviews/story-125-3-125-4-deep-interview-complete-20260701T161249Z.md`
Ralplan plan: `.omx/plans/story-125-3-125-4-inventory-test-guard-plan-20260701T161454Z.md`
Test spec: `.omx/specs/story-125-3-125-4-inventory-test-guard-test-spec-20260701T161454Z.md`
Architect review: `.omx/artifacts/ralplan/story-125-3-125-4-architect-review.md`
Critic review: `.omx/artifacts/ralplan/story-125-3-125-4-critic-review.md`
Canonical architecture source: `../planning-artifacts/phase-46-architecture-amendment.md`

## Decision

Story 125.3 may move beyond the earlier planning record only as an implementation-planning gate. Task-list search/discovery runtime remains unselected and unauthorized.


## Structured gate

```json
{
  "schema_version": 1,
  "story": "125.3",
  "runtime_authorized": false,
  "runtime_selection": "unselected",
  "future_contract_required": true,
  "missing_runtime_contract_inputs": [
    "exact searchable fields",
    "exact query grammar and encoding policy",
    "minimum and maximum query lengths",
    "authority freshness provenance and privacy redaction semantics",
    "status limit offset sort selector interactions",
    "malformed hidden adjacent encoded repeated and body selector failure modes",
    "adversarial side-channel traversal storage cookie and generated-data tests",
    "explicit API and browser authorization boundaries"
  ],
  "non_authorized_surfaces": [
    "search/discovery runtime",
    "backend/API behavior",
    "browser/dashboard behavior",
    "arbitrary query grammar",
    "generated data",
    "hidden selectors",
    "automatic traversal",
    "row-derived selectors",
    "dependencies",
    "services/MCP",
    "CI/deployment",
    "credentials",
    "production operations"
  ],
  "next_allowed_surface": "Story 125.4 inventory and behavior-preserving test guards only"
}
```

## Missing runtime contract inputs

A future runtime story must first define all of the following in a separate product/architecture gate:

- exact searchable fields;
- exact query grammar and encoding policy;
- minimum and maximum query lengths;
- authority, freshness, provenance, and privacy/redaction semantics;
- interactions with status, limit, offset, and sort selectors;
- failure modes for malformed, hidden, adjacent, encoded, repeated, and body selectors;
- adversarial tests for side channels, automatic traversal, row-derived selectors, URL/hash/storage/cookie selectors, and generated data;
- explicit API and browser authorization boundaries.

## Non-authorization statement

This story does not add search/discovery runtime, backend/API behavior, browser/dashboard behavior, query grammar, generated data, hidden selectors, automatic traversal, row-derived selectors, dependencies, services/MCP, CI/deployment, credentials, or production operations.

## Handoff to Story 125.4

Because search/discovery remains closed, the next approved execution surface is Story 125.4 inventory and behavior-preserving test guards only.
