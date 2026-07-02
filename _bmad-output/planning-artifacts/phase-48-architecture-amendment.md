# Phase 48 Architecture Amendment — Production-Readiness Closure Boundaries

Generated: 2026-07-02T11:45:00Z

## Canonical decision

Phase 48 defines the architecture boundaries for the remaining production-readiness backlog. The complete story breakdown lives in `phase-48-production-readiness-epics.md`.

## Baseline

Phase 47 / Epic 126 is shipped/green. The current platform has narrow, explicit read boundaries for dashboard task-list selector composition. Search/discovery runtime, hidden selector policy finalization, automatic traversal/infinite scroll, broad dashboard rewiring, destructive lifecycle mutation, object-storage retention jobs, production operations, production credentials/GitHub write activation, split deployment/remote Postgres scaling, and DB connection mTLS are not yet productized.

## Architecture principles for all Phase 48 epics

1. Exact allowlists before implementation: route, selector, field, object, credential, operation, and certificate inputs must be enumerated before runtime work.
2. Fail closed by default: ambiguous, stale, missing, malformed, unauthorized, unsupported, or partially verified states must not degrade into broader behavior.
3. Visible provenance: browser/operator-facing inputs must come from visible controls or explicit commands, not hidden selectors, rows, URL/hash/storage/cookies, timers, workers, or side channels.
4. Approval and audit: destructive lifecycle mutations, production operations, GitHub writes, deployment changes, retention apply, and credential changes require explicit audit evidence; destructive actions require approval bound to exact parameters.
5. Rollback/disable: every production or destructive capability must ship with a rollback, restore, emergency disable, or documented unsupported-state guard.
6. Profile-gated infrastructure: split deployment, remote Postgres production mode, scheduled jobs, real GitHub writes, and DB mTLS are opt-in/profile-gated and must preserve local/default compatibility.
7. Closure evidence: no zone leaves deferred status until implementation, targeted tests, negative tests, docs/status updates, code-review APPROVE/CLEAR, UltraQA PASS or justified skip, and CI/nightly evidence exist.

## Story 127.1 architecture contract — task-list search/discovery

Story 127.1 selects no runtime implementation. It constrains later runtime stories to a route-local, fail-closed extension of the existing aggregate task-list read boundary.

### Route and query boundary

- Future candidate route family: bodyless `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}` followed only by optional existing selectors in canonical order: `status`, `limit`, `offset`, `sort`.
- Existing shipped `/v1/tasks` selector routes remain unchanged until a later implementation story changes route code and tests.
- Raw query order is exact. Reordered, repeated, extra, encoded, alias, unknown, or body-bearing requests fail closed.
- `field=status` and a separate `status=` selector in the same request are duplicate status semantics and must fail closed; there is no implicit intersection, override, or fallback.

### Field/operator/encoding boundary

- Search fields/operators are exactly those in the Story 127.1 product contract: `task_id:eq`, `title:contains|prefix`, `status:eq`, `actor_id:eq|prefix`, `last_event_type:eq`, `updated_at:gte|lte`, `created_at:gte|lte`.
- Global `q` is `1..96` raw ASCII bytes; full raw query string is `1..256` bytes.
- Per-field `q` caps are `task_id 1..64`, `title 1..64`, `actor_id 1..64`, `last_event_type 1..80`, timestamp fields exactly 20 chars in UTC `YYYY-MM-DDTHH:MM:SSZ`.
- The route rejects percent-encoded bytes (`%xx`), `+`, raw spaces, controls, Unicode/non-ASCII, normalization-dependent text, slash/backslash path syntax, NUL, CR/LF, tabs, empty values, repeated/encoded keys, boolean DSL, regex, fuzzy search, SQL-like syntax, wildcards, nesting, multiple fields, and arbitrary JSON.

### Data, privacy, and metadata boundary

- Search may evaluate only approved aggregate task-list columns and already-safe summary metadata.
- Worktree/resource paths, logs, event payloads, summaries/generated text, decision/approval text, credentials/secrets, trace internals beyond existing trace/correlation ids, raw JSON blobs, and arbitrary metadata are denied.
- Future response models must carry selected field/op/query, optional selected status/limit/offset/sort, returned_count, bounded pagination metadata, freshness, authority, provenance, request_id, trace_id, correlation_id, redaction_state, and fail-closed display_state.

### Browser and traversal boundary

- Browser search may be implemented only from visible controls and one explicit operator-triggered read. It must not consume URL/hash/storage/cookie state, hidden controls, row-derived values, server-provided route strings, or background-derived selectors.
- Story 127.1 leaves traversal disabled. Search results cannot trigger automatic next reads, background prefetch, infinite scroll, timers, workers, observers, retry loops, cache warming, websocket/EventSource/XMLHttpRequest side channels, or hidden traversal.
- Story 127.4 is the first story that may authorize explicit bounded/cancellable traversal.

## Deferred until implementation stories

This amendment does not authorize runtime code, dashboard behavior, mutation, scheduled jobs, deployment config, credentials, GitHub writes, remote Postgres rollout, or DB mTLS changes. It authorizes only the backlog shape and constraints.
