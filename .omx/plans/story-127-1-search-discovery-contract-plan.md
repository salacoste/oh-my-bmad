# Ralplan Plan — Story 127.1 Search/Discovery Product and Architecture Contract

Generated: 2026-07-02T13:00:00Z
Context snapshot: `.omx/context/story-127-1-search-discovery-contract-20260702T125153Z.md`
Deep-interview handoff: `.omx/artifacts/deep-interview/story-127-1-search-discovery-contract-handoff.md`
Scope: docs/status-only product and architecture contract; no runtime behavior.

## Objective

Complete Story 127.1 by turning the previously deferred task-list search/discovery risk area into an exact future implementation contract. The contract must be narrow enough that Story 127.2 can implement an API-local route without introducing arbitrary query grammar, hidden selectors, row-driven traversal, automatic traversal, or privacy/authority ambiguity.

## Contract decisions to document

1. **Future route family**: candidate runtime stays API-local and route-local to the existing aggregate list path as bodyless `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}...` (not implemented in Story 127.1). Existing shipped `/v1/tasks` selector-family routes remain unchanged until Story 127.2.
2. **Canonical query order**: `field`, then `op`, then `q`, then optional existing selectors in their approved order (`status`, `limit`, `offset`, `sort`). No repeated, extra, encoded-key, alias, or reordered keys. The future route must reject `field=status` when a separate `status=` selector is also present; status search and status filtering cannot be duplicated or intersected in one request.
3. **Searchable field allowlist and exact `q` bounds**:
   - Global raw `q` cap: `1..96` raw ASCII query bytes before parsing; empty `q` is invalid for every field.
   - Encoding/spelling: `field`, `op`, and `q` values must be raw ASCII query bytes only; percent-encoded bytes (`%xx`), `+` space aliases, raw spaces, controls, Unicode/non-ASCII, normalization-dependent text, slash/backslash path syntax, NUL, CR/LF, tabs, and repeated/encoded keys are rejected before parsing.
   - `task_id`: `op=eq`; `q` `1..64` chars, raw ASCII `[A-Za-z0-9._:-]` only.
   - `title`: `op=contains` or `op=prefix`; `q` `1..64` chars, raw ASCII safe text `[A-Za-z0-9._~:-]` only; no spaces or percent-encoded text in the first runtime slice.
   - `status`: `op=eq`; `q` must be one of the existing lifecycle status tokens (`pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`); a separate `status=` selector in the same request is fail-closed duplicate status semantics.
   - `actor_id`: `op=eq` or `op=prefix`; `q` `1..64` chars, raw ASCII `[A-Za-z0-9._:@-]` only.
   - `last_event_type`: `op=eq`; `q` `1..80` chars, raw ASCII event-type token `[A-Za-z0-9._:-]` only; never searches event payload.
   - `updated_at` and `created_at`: `op=gte` or `op=lte`; `q` exactly 20 chars in raw UTC RFC3339 second precision `YYYY-MM-DDTHH:MM:SSZ`; date-time ranges remain single-bound per request until a later contract expands them.
4. **Denied fields/data**: worktree/resource paths, logs, event payloads, summaries/generated text, decisions/approval text, credentials/secrets, trace internals beyond existing trace/correlation ids, raw JSON blobs, arbitrary metadata, and any field not listed.
5. **Operator vocabulary**: exact `eq`, textual `contains`/`prefix` only where named, temporal `gte`/`lte` only where named. No boolean DSL, regex, fuzzy search, SQL-like syntax, wildcards, nesting, multiple fields per request, or arbitrary JSON.
6. **Bounds**: global raw `q` cap is `1..96` bytes with the per-field caps above; the full raw query string cap is `1..256` bytes; percent encoding and `+` aliases are rejected rather than decoded; limit remains `1..50`, offset remains `0..2147483647`, and server work remains row-capped.
7. **Response metadata**: selected field/op/query, selected status/limit/offset/sort when present, result count, pagination metadata, freshness, authority, provenance, request_id, trace_id, correlation_id, redaction state, and explicit fail-closed display state.
8. **Privacy/redaction**: rows expose only approved aggregate summary fields. Matches must not leak denied field values or snippets from logs/events/generated text. Redaction state is explicit even when no redaction is needed.
9. **Browser provenance**: future Story 127.3 may use only visible form controls and one explicit operator action; no URL/hash/storage/cookie/hidden/row-derived values.
10. **Traversal**: Story 127.1 documents traversal as disabled until Story 127.4. No background prefetch, infinite scroll, timers, workers, observers, retry loops, cache warming, websocket/EventSource/XHR side channels, or automatic next-page reads are authorized by search results.

## Planned file changes

- `_bmad-output/planning-artifacts/phase-48-prd-amendment.md`: add Story 127.1 product contract section with exact fields/operators, bounds, response metadata, privacy/redaction, and non-goals.
- `_bmad-output/planning-artifacts/phase-48-architecture-amendment.md`: add Story 127.1 architecture contract with route/provenance/fail-closed/traversal boundaries.
- `_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md`: add Story 127.1 completion/contract details while preserving future story sequence.
- `_bmad-output/implementation-artifacts/127-1-search-discovery-product-architecture-contract.md`: durable story evidence artifact summarizing the decisions and consensus gate.
- `docs/api-contracts.md`: add a clearly marked non-runtime future contract note so derivative API docs distinguish current shipped `/v1/tasks` behavior from the Story 127.1 approved candidate.
- `docs/feature-status.md`: mark Epic 127 / Story 127.1 as planning/contract in progress or complete after gates, while keeping search/discovery runtime deferred.
- `_bmad-output/implementation-artifacts/sprint-status.yaml`: advance current phase/status for Epic 127 and Story 127.1 after verification.

## Verification plan

- Static structural validation:
  - assert all planned docs contain `Story 127.1` and `no runtime`/`not implemented` guards;
  - assert exact field/operator tokens and exact q caps (`1..96`, title `1..64`, event type `1..80`, timestamp 20) appear in the story artifact and PRD/architecture amendments;
  - assert encoding policy rejects percent-encoded bytes, `+`, spaces, controls, Unicode/non-ASCII, repeated/encoded keys, and empty `q`;
  - assert `field=status` plus separate `status=` is documented as fail-closed duplicate status semantics;
  - assert denied sources include hidden, row-derived, URL/hash/storage/cookie, arbitrary grammar, and automatic traversal/prefetch;
  - assert `git diff --name-only` stays within `_bmad-output/planning-artifacts/phase-48-prd-amendment.md`, `_bmad-output/planning-artifacts/phase-48-architecture-amendment.md`, `_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md`, `_bmad-output/implementation-artifacts/127-1-search-discovery-product-architecture-contract.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`, `docs/api-contracts.md`, `docs/feature-status.md`, `.omx/plans/story-127-1-search-discovery-contract-plan.md`, and `.omx/artifacts/...` evidence files.
- `python` YAML parse for `_bmad-output/implementation-artifacts/sprint-status.yaml`.
- `git diff --check`.
- Docs-only UltraQA skip is acceptable only after code-review APPROVE/CLEAR because no runtime/API/dashboard/test behavior changes are made.

## Non-goals

No source/runtime/test/backend/API/dashboard JS/HTML behavior, dependencies, lockfiles, CI/deployment, services/MCP, credentials, production operations, mutation/control behavior, real GitHub writes, automatic traversal, infinite scroll, background reads, or object-storage/deployment/mTLS changes.
