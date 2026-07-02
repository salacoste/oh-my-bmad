# UltraQA — Story 127.2

Status: PASS

Story 127.2 changes are API-local and covered by targeted aggregate/search tests plus full `test_app.py` regression, ruff format/check, strict mypy, YAML parse, and diff check.

## Acceptance coverage

- Approved bodyless GET search/discovery requests validate exact query order, field allowlist, query length, encoding, finite domains, and no GET body.
- Unknown fields, arbitrary grammar, encoded/repeated/reordered keys, hidden selectors, GET body, URL/hash/storage values, unsupported compositions, duplicate `field=status`/`status=`, and broader search fallback fail closed.
- Literal `_` semantics are tested for title/actor matching.
- Timestamp bounds are semantically parsed.
- `last_event_type:eq` filters current `Task.last_event_id` before pagination.
- Response metadata includes selected search/suffix metadata, redaction state, freshness, authority, provenance, request/trace/correlation ids, and pagination metadata.

Verification evidence: `.omx/artifacts/ultragoal/story-127-2/rework-cycle-2-verification.log`.
