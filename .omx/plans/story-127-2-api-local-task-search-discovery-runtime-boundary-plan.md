# Ralplan — Story 127.2 API-local Task Search/Discovery Runtime Boundary

## Goal
Implement the Story 127.1 search/discovery contract as a narrow API-local extension to `GET /v1/tasks`, with tests proving the route accepts only approved bodyless GET query shapes and fails closed for hidden or broader search behavior.

## Scope
- Modify `services/registry-api/src/registry_api/routes/tasks.py` only for `/v1/tasks` query validation, response typing, docs/comments, and SQL filtering.
- Modify `services/registry-api/src/registry_api/test_app.py` only for task aggregate/search tests and OpenAPI expectations.
- No dashboard/browser behavior, storage/URL/hash/cookie selectors, hidden selectors, prefetch/traversal, adjacent route discovery, mutation controls, dependencies, lockfiles, or deployment/credential changes.

## Exact search contract
- Route template: `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}`.
- Accepted field/operator pairs only:
  - `task_id:eq`
  - `title:contains`, `title:prefix`
  - `status:eq`
  - `actor_id:eq`, `actor_id:prefix`
  - `last_event_type:eq`
  - `updated_at:gte`, `updated_at:lte`
  - `created_at:gte`, `created_at:lte`
- Raw `q` global bounds: `1..96` ASCII bytes. Full raw query bounds: `1..256` ASCII bytes.
- Per-field raw value rules:
  - `task_id`: `1..64`, `[A-Za-z0-9._:-]`
  - `title`: `1..64`, `[A-Za-z0-9._~:-]`
  - `status`: one approved lifecycle token
  - `actor_id`: `1..64`, `[A-Za-z0-9._:@-]`
  - `last_event_type`: `1..80`, `[A-Za-z0-9._:-]`
  - `updated_at`/`created_at`: exactly `YYYY-MM-DDTHH:MM:SSZ` and semantically valid UTC RFC3339 second-precision timestamp.
- Reject `%`, `+`, raw spaces, `/`, `\\`, controls, Unicode/non-ASCII, empty values, repeated keys, encoded keys, aliases, reordered keys, extra keys, arbitrary grammar, wildcards, SQL/regex/fuzzy syntax, GET bodies, and path/URL/hash/storage selectors before search evaluation.
- `field=status` plus any separate `status=` suffix fails closed even if the values are identical.

## Accepted raw query shape matrix
Search prefix is always exactly `field=...&op=...&q=...`. Only these suffix families are accepted:
1. `field&op&q`
2. `field&op&q&status`
3. `field&op&q&limit`
4. `field&op&q&status&limit`
5. `field&op&q&limit&offset`
6. `field&op&q&status&limit&offset`
7. `field&op&q&sort`
8. `field&op&q&status&limit&offset&sort`

All other permutations and partial sort compositions fail closed, including `field&op&q&status&sort`, `field&op&q&limit&sort`, `field&op&q&limit&offset&sort`, `field&op&q&status&limit&sort`, `sort` before search, `offset` without `limit`, and duplicate selector keys.

## SQL/filtering semantics
- Search predicates are applied before pagination/limit/offset and combine with optional `status=` suffix by `AND` for non-`status` search fields.
- `title:contains|prefix` and `actor_id:prefix` must use literal matching. `_`, `%`, and any accepted punctuation must not act as SQL wildcards. Use escaped `LIKE ... ESCAPE` or non-`LIKE` string functions.
- Timestamp search values are parsed to UTC datetimes after raw regex validation and invalid dates/times are rejected with 400.
- `last_event_type:eq` filters only the current last-event pointer (`Task.last_event_id == Event.id AND Event.type == q`) before limit/offset/sort. It must not search historical events, payload JSON, summaries, generated text, or event history.

## Response contract
Add `TaskSearchDiscoveryListResponse` to the route response-model union and OpenAPI surface with exactly:
- `route`: literal `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}`
- `selected_field`
- `selected_op`
- `selected_query`
- `selected_status: TaskStatusFilter | None`
- `selected_limit: int | None`
- `selected_offset: int | None`
- `selected_sort: str | None`
- `redaction_state`: literal `summary-only-no-snippets`
- existing task-list metadata: `retrieved_at`, `freshness_state`, `display_state`, `authority_state`, `provenance`, `request_id`, `trace_id`, `correlation_id`, `limit`, `returned_count`, `has_more`, `next_offset`, `items`.

Search responses must not add snippets, scores, links, adjacent route hints, hidden selectors, raw event payloads, summaries, worktree/resource paths, credentials/secrets, decision/approval text, generated text, arbitrary metadata, or traversal controls.

## Test plan
1. Update OpenAPI query parameter expectation to include `field`, `op`, and `q` with finite field/operator domains/descriptions.
2. Add table-driven positive/semantic tests for every accepted field/operator pair:
   - `task_id:eq` exact match only.
   - `title:contains` literal substring match, not wildcard search.
   - `title:prefix` prefix-only match and explicitly not contains-only.
   - `status:eq` finite lifecycle exact match.
   - `actor_id:eq` exact match only.
   - `actor_id:prefix` prefix-only match, including canonical `status&limit&offset&sort` suffix proving suffix `status=` combines by `AND`.
   - `last_event_type:eq` current-last-event exact match only.
   - `updated_at:gte`, `updated_at:lte`, `created_at:gte`, and `created_at:lte` each prove the bound direction filters correctly.
   - Search response shape/metadata for representative successful search responses.
3. Add fail-closed tests for:
   - unknown fields/operators and mismatched field/operator pairs.
   - empty q, q max+1, field-specific max+1, raw query length 257, semantic invalid timestamps, encoded/raw non-ASCII, `%`, `+`, spaces, `/`, `\\`, hidden selectors, URL/hash/storage keys, SQL/regex/fuzzy/wildcard grammar.
   - repeated/encoded/reordered/extra keys and every non-listed selector composition.
   - `field=status` plus `status=` in every accepted suffix family.
   - GET body.
4. Add semantic safety tests:
   - `_` in title/actor prefix is literal and does not match `axb`.
   - `last_event_type:eq` only matches the current last event and filters before pagination; historical/payload-only matches do not count.
5. Assert search rows retain bounded summary shape and denied strings remain absent.

## Verification
- `uv run pytest services/registry-api/src/registry_api/test_app.py -q -k 'GetTasksAggregate'`
- `uv run pytest services/registry-api/src/registry_api/test_app.py -q`
- `uv run ruff format --check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py`
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py`
- `git diff --check`
