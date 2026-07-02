# Architect Review — Story 127.2 Cycle 1

Status: BLOCK

The plan is directionally aligned but must be tightened before implementation.

Required repairs:
- Explicit `TaskSearchDiscoveryListResponse` schema and OpenAPI wiring, including `route`, `selected_field`, `selected_op`, `selected_query`, suffix metadata when present, `returned_count`, `display_state`, `redaction_state`, `freshness_state`, `authority_state`, `provenance`, request/trace/correlation ids, and pagination metadata.
- Literal-safe matching for `title:contains|prefix` and `actor_id:prefix`; `_` must not become a SQL wildcard.
- `last_event_type:eq` must filter current last-event before pagination/limit/offset, not post-page rows.
- Explicit raw-query matrix and negative tests for non-listed permutations.
- Update route docs/comments that currently say search is unsupported.
