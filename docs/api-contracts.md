# API contracts

The platform exposes three categories of contract: HTTP (`registry-api`), MCP tools/resources (9 servers; stdio by default, Streamable HTTP where configured), and operator commands (Telegram + console). This file is an index, not a generated OpenAPI/MCP-schema dump — for exact shapes consult the source, which is `--strict`-typed Pydantic.

## HTTP API — `registry-api`

Versioned at `/v1`. Versions are additive; `/v1` semantics are frozen once shipped. All handlers are async; request/response models are Pydantic v2 with `extra="forbid"`. Errors normally flow through the registered exception handler that maps `<svc>Error` → `{error_id, error_code, message, trace_id}`. Replay/archive failures on replay, validate, and archive-aware task-history endpoints use route-local RFC 7807 ProblemDetails so invalid archive configuration fails closed without changing global error behavior.

| Path | Method | Handler | Purpose |
|---|---|---|---|
| `/v1/tasks` | POST | `post_tasks` | Create a task; emits `task.created`; returns **201** with the new task ID. Idempotency key threaded from the caller. |
| `/v1/tasks` | GET | `get_tasks` | Bounded aggregate task summary list. Selector-free/body-free fixed first page ordered by `updated_at DESC, id ASC`; exposes source/freshness/provenance metadata and no task-detail/session/digest/history/trace traversal. Story 113.2 additionally permits exactly `GET /v1/tasks?status={task_status}` with one `status` query key limited to `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`. Story 114.2 additionally permits exactly `GET /v1/tasks?limit={task_list_limit}` with one integer `limit` query key from 1 through 50. Story 115.2 additionally permits exactly canonical-order `GET /v1/tasks?status={task_status}&limit={task_list_limit}` with one approved status selector followed by one approved limit selector. Story 117.2 additionally permits exactly canonical-order `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` with one approved limit selector followed by one ASCII non-negative integer offset selector from 0 through 2147483647; response metadata exposes selected limit/offset, returned_count, has_more, next_offset/null, freshness, authority, provenance, and request/trace/correlation evidence. Story 120.2 additionally permits exactly canonical-order `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` with status filtering before offset windowing. Story 122.2 additionally permits exactly `GET /v1/tasks?sort=updated_at_desc_id_asc` through the parameterized route `GET /v1/tasks?sort={task_sort}` with singleton approved sort value `updated_at_desc_id_asc`, selected_sort metadata, deterministic `updated_at DESC, id ASC`, fixed first-page semantics, and no composition. Story 124.2 expands that same standalone API-local parameterized sort route to exactly two approved values, `updated_at_desc_id_asc` and `created_at_desc_id_asc`; the new value orders by `created_at DESC, id ASC`, returns `selected_sort`, remains bodyless/fixed first-page, and remains mutually exclusive with status/limit/offset. Reversed query order, extra/repeated query keys, GET bodies, status+offset without limit, sort composition with status/limit/offset, browser sort vocabulary beyond Story 123.2 singleton controls until a later implementation story, free-text search, cursor/page traversal beyond the exact limit+offset API-local boundary, hidden selectors, traversal, replay execution, lifecycle mutation, additional browser sort-control runtime, and broad dashboard wiring remain rejected/deferred. |
| `/v1/sessions` | GET | `get_sessions` | Bounded session summary list (Story 110.2). Selector-free/body-free fixed first page ordered by heartbeat/start/id; exposes Session-table display metadata and no session-detail/task/digest/history/trace traversal. |
| `/v1/sessions/{session_id}` | GET | `get_session_detail` | Bounded session detail read (Story 111.2). The path parameter is the only selector; query strings and GET bodies fail closed, unknown sessions return 404, and the response omits worktree paths, resource paths, event/log payloads, summaries, hrefs/URLs, generated text, and controls. |
| `/v1/tasks/{task_id}` | GET | `get_task_by_id` | Fetch reconstituted task state (FR4). |
| `/v1/tasks/{task_id}/decisions` | POST | `post_decision` | Operator decisions: approve / reject / stop / retry (Story 6.4). |
| `/v1/tasks/{task_id}/logs/digest` | GET | `get_logs_digest` | LLM-summarized event digest for a task (Story 7.3, FR5). |
| `/v1/tasks/{task_id}/logs/digest/stream` | GET | `get_logs_digest_stream` | Bounded task-scoped digest stream (Story 112.2). Query-free/body-free; returns `application/x-ndjson` frames (`open`, `chunk`, `final`) from the visible path `task_id` only. Provider-unavailable final frames remain non-authoritative. |
| `/v1/tasks/{task_id}/events` | GET | `get_task_events` | Raw event stream with pagination (Story 7.5, FR6). |
| `/v1/events/replay` | GET | `get_events_replay` | Read-only point-in-time replay from hot logs plus optional validated archives (FR135, FR139). |
| `/v1/tasks/{task_id}/history` | GET | `get_task_history` | Task event history from hot logs plus opt-in validated archive segments (FR136, FR152-FR155). |
| `/v1/events/replay/validate` | GET | `get_events_replay_validate` | Compare replayed state to live projection and return field diffs (FR137). |
| `/v1/events/replay/snapshots` | GET | `get_replay_snapshots` | List replay snapshots (FR138), hot-log-only. |
| `/v1/events/replay/snapshots` | POST | `post_replay_snapshot` | Create replay snapshot using `HOT_ONLY_REPLAY`; archive env vars are bypassed (FR141). |

Health endpoints (`/healthz`, `/readyz`, `/v1/health` — FR17) emit **no** log lines under normal operation. A pytest assertion captures `structlog` output during the call to assert silence.

Trace context is pulled from inbound headers:
- `X-Trace-Id` → bound as `trace_id`; if absent, a new UUIDv7 is minted and logged at WARNING.
- `X-Parent-Event-Id` → bound as `parent_event_id`; if absent, stays None (never fabricated).

Both are bound to the structlog context at the middleware layer **before** any handler runs.

## MCP tool catalog

The fleet has 9 MCP servers. Stdio is the default transport; Phase 10 added Streamable HTTP as an explicit opt-in for remote MCP deployments. Unplanned transports remain rejected by static analysis. Tool handlers are pure async functions with pydantic-validated input and pydantic-modelled output; capability-tier middleware runs at every boundary.

Errors raise `ToolError(...)` for structured client-visible errors; never `raise ValueError(...)` (untyped). Tool error responses are mapped through the internal-vs-external error boundary — stack traces, file paths, and module names never reach the calling LLM context.

### `clawhip-bridge` MCP

**Append-only event-emission surface — sole mutation path to the event log.** Every mutating tool emits exactly one typed event onto the spine with `parent_event_id` set; read-only tools are exempt.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `emit_event` | Generic typed-event emission (worker-owned events) | the typed event itself |
| `emit_blocker` | Worker reports a blocker on a task | `task.blocker_raised` |
| `emit_summary` | Worker emits a task summary | `task.summary_emitted` |
| `emit_approval_request` | Worker requests operator approval | `task.approval_requested` |
| `emit_completion` | Worker reports task completion | `task.completed` |

### `session-registry` MCP

Read-only **resources** for session queries; **tools** for bounded writes.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `session_register` | Begin a session for a task | `session.started` |
| `session_heartbeat` | Liveness ping | `session.heartbeat` (also detects timeout) |
| `session_close` | End a session | `session.finished` |

### `task-registry` MCP

Read-only **resources** for task / approval-queue / blockers queries; **tools** for bounded writes.

| Tool | Effect | Emitted event(s) |
|---|---|---|
| `task_add_note` | Attach a typed note to a task | typed event (note kind) |
| `task_attach_artifact` | Attach an artifact pointer | typed event (artifact kind) |
| `task_emit_event` | Worker-routed typed event for the task | the typed event itself |

### Capability-tier enforcement

Every tool boundary above has **three mandatory tests** (per [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 4):

1. **Deny path** — a caller below the granted tier receives a structured deny envelope.
2. **Default-deny** — a caller with no capability claim is rejected, not silently routed to tier 0.
3. **Escalation** — a caller claiming a higher tier than provisioned is rejected.

Each boundary's allowed and rejected request shapes are pinned as contract fixtures.

## Telegram bot commands — `telegram-gateway`

Inbound idempotency key = `f"tg:{update_id}"`, threaded into the command envelope. `AllowlistMiddleware` is the single auth gate (ADR-0001); unauthorized chat IDs are dropped with a single `secret.access_denied` audit event — never echoed back to the user.

Outbound messages are rendered through the template registry documented in [message-design.md](./message-design.md). Templates are validated against the Telegram character budget by tests; inlining Telegram-specific Markdown in handler code is banned.

For the complete command list and FSM/state details, read `services/telegram-gateway/src/telegram_gateway/handlers/`. The set is intentionally kept in code (not duplicated here) because parity with the console CLI is enforced by the integration tests, not by documentation.

## Console-CLI commands

`console-cli` mirrors the Telegram surface for full parity (FR12). For the catalog, run:

```sh
uv run python -m console_cli --help
```

The CLI is published as a GHCR image (`ghcr.io/<owner>/oh-my-bmad-console-cli`) but is NOT in `docker compose up` by design — it's invoked ad-hoc on the host. See [README](../README.md) §"Upgrading" and [exceptions.md](./exceptions.md).

### Optional fleet MCP servers

Fleet servers introduced across Phase 3 and Phase 4 (ADR-0010 pattern). Each declares a module-level `TIER_MAP`; every handler calls `check_tier` (Tier-0..2) or `check_tier_with_approval` (Tier-3) before any side effect. `caller_trace_id` is a required keyword-only input on every tool, validated by the byte-identical `validate_caller_trace_id` helper. Mutating spine events route through the single FR26 writer (clawhip-bridge). Most ship in the base image and are spawned only when configured; browser automation uses the Phase 4 Playwright container discipline.

#### `git-mcp`

All tools run through a sandboxed git subprocess.

| Tool | Tier | Effect |
|---|---|---|
| `git_status` | 1 (read) | Working-tree status |
| `git_diff` | 1 (read) | Diff of staged or unstaged changes |
| `git_log` | 1 (read) | Commit log with configurable range |
| `git_branch_list` | 1 (read) | List local and remote branches |
| `git_branch_current` | 1 (read) | Return the name of the current branch |
| `git_add` | 2 (write) | Stage paths for the next commit |
| `git_commit` | 2 (write) | Create a commit from the staged tree |
| `git_push` | 3 (approval-gated) | Push commits to a remote |
| `git_reset` | 3 (approval-gated) | Reset HEAD to a specified ref |
| `git_rebase` | 3 (approval-gated) | Rebase the current branch onto a target |
| `git_merge` | 3 (approval-gated) | Merge a branch into the current branch |
| `git_cherry_pick` | 3 (approval-gated) | Cherry-pick a commit onto the current branch |

#### `github-mcp`

API calls via `aiohttp` + `tenacity` retry, scoped Bearer token (`GITHUB_MCP_TOKEN`). Write operations are approval-gated; Phase 1 defaults `simulate=True` to validate without mutating GitHub state.

| Tool | Tier | Effect |
|---|---|---|
| `github_issues_list` | 1 (read) | List issues for a repository |
| `github_issues_get` | 1 (read) | Fetch a single issue by number |
| `github_prs_list` | 1 (read) | List pull requests for a repository |
| `github_prs_get` | 1 (read) | Fetch a single PR by number |
| `github_reviews_list` | 1 (read) | List reviews on a pull request |
| `github_issues_create` | 3 (approval-gated) | Create a new issue; `simulate=True` default |
| `github_issues_comment` | 3 (approval-gated) | Add a comment to an issue; `simulate=True` default |
| `github_prs_create` | 3 (approval-gated) | Create a pull request; `simulate=True` default |
| `github_prs_merge` | 3 (approval-gated) | Merge a pull request; `simulate=True` default |
| `github_prs_review` | 3 (approval-gated) | Submit a review on a PR; `simulate=True` default |
| `github_prs_close` | 3 (approval-gated) | Close a pull request; `simulate=True` default |
| `github_prs_reopen` | 3 (approval-gated) | Reopen a closed pull request; `simulate=True` default |

#### `verification-mcp`

Sandboxed subprocess, cwd-pinned, worktree-contained.

| Tool | Tier | Effect |
|---|---|---|
| `verification_run_build` | 2 (write) | Execute the project build in a contained worktree |
| `verification_run_tests` | 2 (write) | Execute the project test suite in a contained worktree |

#### `memory-mcp`

SQLite FTS5 store for persistent key-value memory and full-text search.

| Tool | Tier | Effect |
|---|---|---|
| `memory_read` | 1 (read) | Retrieve a value by key |
| `memory_search` | 1 (read) | Full-text search via FTS5 `MATCH` + `bm25` ranking |
| `memory_write` | 2 (write) | Upsert a key-value entry |


#### `browser-mcp`

Playwright-backed browser automation server. Core navigation and snapshot operations are low-tier; JavaScript evaluation and similarly risky actions are Tier-3 approval-gated. Browser execution is containerized and image-pinned by digest.

| Tool family | Tier | Effect |
|---|---|---|
| Navigation / snapshot / screenshot | 1 | Inspect pages and capture metadata/artifacts |
| Interaction / tab management | 2 | Click/type/select/wait and manage browser state |
| Evaluation / file-sensitive operations | 3 | Execute page JavaScript or high-risk browser operations |

#### `artifact-mcp`

Content-addressed filesystem store; objects keyed by `sha256` digest.

| Tool | Tier | Effect |
|---|---|---|
| `artifact_get` | 1 (read) | Retrieve an artifact by its content hash |
| `artifact_list` | 1 (read) | List stored artifacts |
| `artifact_put` | 2 (write) | Store a base64-encoded payload (content-addressed) |
| `artifact_delete` | 3 (approval-gated) | Delete an artifact by content hash |

## Replay and event-log lifecycle contracts

Phase 12-17 replay and lifecycle-operation contracts live in `packages/replay`, `services/registry-api/src/registry_api/routes/replay.py`, and ADR-0025:

- `archive_manifest_path` passed directly to package APIs has highest precedence.
- `REPLAY_ARCHIVE_MANIFEST` is the primary env var; `EVENT_LOG_ARCHIVE_MANIFEST` is a legacy alias. If both point to different files, replay fails closed.
- `lifecycle-manifest.json` schema version `1` references archived JSONL segments by relative path and `sha256`; segments are rejected on checksum mismatch, missing file, malformed metadata, duplicate keys, or sequence overlap.
- `HOT_ONLY_REPLAY` forces hot-log-only behavior and is used by snapshot creation.
- `get_task_history` is archive-aware when archive manifest configuration is present; with no archive manifest it preserves the Phase 12-15 hot-log-only default. Invalid archive config fails closed with route-local ProblemDetails.
- Fail-closed archive validation is intentional: a bad configured archive manifest can make task history return a route-local 5xx ProblemDetails instead of falling back to partial hot-log results.
- `replay_events_stream()` is package-only; there is no public HTTP streaming endpoint yet.
- Phase 14 authorizes planning/validation and non-destructive lifecycle dry-run data only; Phase 17 defines readiness requirements for a future destructive apply contract, but destructive prune/apply remains unimplemented. Story 82.1 requires any later apply surface to be distinct from dry-run and bound to durable authorization evidence for the exact dry-run `plan_hash`, affected segment identities, replay validation, rollback evidence, and explicit operator identity/event or ledger reference. Story 83.1 further requires durable replay proof and rollback evidence before any future mutation; absent, failed, stale, ambiguous, or unverifiable evidence blocks apply.

## Cross-references

- [data-models.md](./data-models.md) — event types catalog + registry-state DB schema.
- [message-design.md](./message-design.md) — Telegram template catalog + character budgets.
- [schema-evolution.md](./schema-evolution.md) — how to add an event type + ship a migrator.
- [adr/0001-allowlist-middleware-auth.md](./adr/0001-allowlist-middleware-auth.md) — single-auth-gate decision.
- [adr/0010-mcp-server-authoring.md](./adr/0010-mcp-server-authoring.md) — canonical recipe for Phase-3 fleet MCP servers.
- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 3 — FastAPI / aiogram / MCP framework rules and trace-context binding.
