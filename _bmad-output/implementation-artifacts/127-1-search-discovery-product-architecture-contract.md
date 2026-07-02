# Story 127.1 — Search/Discovery Product and Architecture Contract

Generated: 2026-07-02T13:05:27Z
Status: done — docs/status-only contract.

## Consensus evidence

- Architect cycle 2: APPROVE / CLEAR — `.omx/artifacts/ralplan/story-127-1-architect-review-cycle-2.md` (native subagent `019f22ea-6862-7250-bc9e-547e7e6f934a`).
- Critic cycle 2: APPROVE / CLEAR — `.omx/artifacts/ralplan/story-127-1-critic-review-cycle-2.md` (native subagent `019f22eb-9d4f-70d2-a88d-d8148fb10134`).
- Prior Critic cycle 1 BLOCK was resolved by pinning exact `q` caps/encoding, fail-closed status duplication, and verification allowlist.

## Runtime authorization

Story 127.1 authorizes no runtime behavior. It does not change API handlers, dashboard JavaScript/HTML behavior, runtime tests, dependencies, lockfiles, CI/deployment, services/MCP, credentials, production operations, mutation/control behavior, or traversal.

## Story 127.1 search/discovery contract decisions

Story 127.1 is a product/architecture contract only; it does not implement runtime API, dashboard, test, dependency, deployment, credential, mutation, or production-operation behavior.

Future search/discovery, if implemented by Story 127.2+, must remain route-local to the aggregate task list as a bodyless GET on `/v1/tasks` with canonical query order:

`field={task_search_field}&op={task_search_operator}&q={task_search_query}` followed only by optional existing selectors in their approved order: `status`, `limit`, `offset`, `sort`.

The approved searchable fields and operators are:

| Field | Operators | `q` bounds and spelling | Notes |
|---|---|---|---|
| `task_id` | `eq` | `1..64` chars, raw ASCII `[A-Za-z0-9._:-]` | Identifier lookup only; no row-derived traversal. |
| `title` | `contains`, `prefix` | `1..64` chars, raw ASCII `[A-Za-z0-9._~:-]` | Matches only already-visible task summary title; no spaces or percent-encoded text in the first runtime slice. |
| `status` | `eq` | exactly one lifecycle token: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed` | If `field=status` appears with a separate `status=` selector, the request fails closed as duplicate status semantics. |
| `actor_id` | `eq`, `prefix` | `1..64` chars, raw ASCII `[A-Za-z0-9._:@-]` | Actor identifier only; no actor profile or credential lookup. |
| `last_event_type` | `eq` | `1..80` chars, raw ASCII `[A-Za-z0-9._:-]` | Event type token only; event payload is never searched. |
| `updated_at` | `gte`, `lte` | exactly 20 chars, UTC RFC3339 second precision `YYYY-MM-DDTHH:MM:SSZ` | Single-bound temporal filter only. |
| `created_at` | `gte`, `lte` | exactly 20 chars, UTC RFC3339 second precision `YYYY-MM-DDTHH:MM:SSZ` | Single-bound temporal filter only. |

Global bounds and encoding rules:

- `q` is required and has global raw size `1..96` raw ASCII bytes before parsing; the full raw query string is capped at `1..256` bytes.
- `field`, `op`, and `q` values must be raw ASCII query bytes only.
- Percent-encoded bytes (`%xx`), `+` space aliases, raw spaces, controls, Unicode/non-ASCII, normalization-dependent text, slash/backslash path syntax, NUL, CR/LF, tabs, empty values, repeated keys, encoded keys, aliases, reordered keys, extra keys, GET bodies, and arbitrary JSON fail closed before search evaluation.
- Boolean DSL, regex, fuzzy search, SQL-like syntax, wildcards, nesting, multiple search fields per request, and arbitrary query language remain forbidden.

Privacy and response metadata:

- Responses may expose only approved aggregate task summary fields already safe for the task-list surface: task identity, status, title, created/updated/state-since timestamps, actor kind/id, and last-event id/type/emitted_at/trace_id metadata.
- Worktree/resource paths, logs, event payloads, summaries/generated text, decision/approval text, credentials/secrets, raw JSON blobs, arbitrary metadata, and denied fields are never searched or returned as snippets.
- Search responses must include selected field/op/query, selected status/limit/offset/sort when present, returned count, bounded pagination metadata, freshness, authority, provenance, request_id, trace_id, correlation_id, redaction state, and explicit fail-closed display state.

Selector provenance and traversal:

- Future browser controls may use only visible form controls and one explicit operator action. URL/hash/storage/cookie, hidden input, row-derived, server-provided route string, and background-derived selectors remain forbidden.
- Story 127.1 does not enable traversal. Search results do not authorize automatic traversal, background prefetch, infinite scroll, timers, workers, observers, retry loops, cache warming, websocket/EventSource/XMLHttpRequest side channels, or automatic next-page reads. Traversal remains disabled until Story 127.4 defines an explicit bounded/cancellable mode.


## Closure verification expectations

- Contract docs must contain Story 127.1, exact `q` caps, encoding rejection rules, duplicate status fail-closed rule, hidden/row/url/storage/cookie selector denials, arbitrary grammar denials, and traversal denials.
- `git diff --name-only` must remain within docs/status/planning/evidence files approved by the Ralplan plan.
- `git diff --check` must pass.
