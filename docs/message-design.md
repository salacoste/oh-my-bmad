# Message design

Telegram message template catalog and design rules. This is the
catalog-before-content reference: the templates themselves are implemented per
owning story in Epic 3. Each section below provides the mock rendering,
character budget, field list, and rationale.

---

## Template discipline

Every bot message follows four rules:

1. **HTML parse mode** — set globally via `DefaultBotProperties(parse_mode=ParseMode.HTML)`
   in `lifespan.py`. Every `message.reply(...)` call inherits HTML mode.
2. **HTML escaping** — pass all user-supplied content through `html.escape()`
   (stdlib) before interpolation. This covers task IDs, usernames, reasons,
   summaries, branch names, error descriptions — any string sourced from an
   external system or operator input. Static labels and emoji prefixes do not
   need escaping.
3. **Escape exactly once** — `html.escape()` is idempotent only on strings
   that contain no HTML entities (`&`, `<`, `>`, `"`, `'`). On its own output
   it is NOT idempotent — applying it twice doubles `&amp;` to `&amp;amp;`,
   corrupting output. Never `html.escape(html.escape(x))`.
4. **Emoji minimalism** — one prefix emoji per category (see
   [Emoji discipline](#emoji-discipline)). No emoji decoration in body text.

---

## HTML safety

```python
import html

# Apply ONCE per externally-sourced value.
safe = html.escape(raw_string)
```

Call `html.escape()` on task IDs, usernames, reason text, branch names, PR URLs,
error descriptions, and any platform-generated string before interpolating into a
template.

**Call `html.escape()` exactly once per value.** Double-escaping corrupts output
(e.g. `<script>` → `&lt;script&gt;` → `&amp;lt;script&amp;gt;`).

**Truncate before escaping.** Character counts must be predictable — truncation
happens on the raw string, then the truncated result is escaped. Escaping first
makes the final length unpredictable because `&` and `<` expand to multi-char
entities.

---

## Emoji discipline

Fixed catalog — do not introduce emojis outside this table:

| Emoji | Category | When used |
|-------|----------|-----------|
| ✅ | success / approved | Task completed; `/approve` accepted |
| ⚠️ | warning / error | Non-fatal issue; all error replies; unhealthy `/ping` |
| ⛔ | blocker | Task blocked; operator action required |
| 🔒 | approval-required | Human approval gate reached |
| 🛠️ | self-recovered | Automatic self-recovery occurred |
| 🛑 | stopped | `/stop` command accepted |
| 🚫 | rejected | `/reject` command accepted |
| 🔄 | retried | `/retry` command accepted |
| 📋 | status / logs | `/status` and `/logs` command replies |
| 🤖 | agent | `/agent` command reply |

The catalog is fixed so the operator's Telegram thread is visually grep-able:
a ⛔ is always a blocker, never ambiguous.

---

## Event-driven template catalog

Templates rendered by `clawhip-daemon`'s `TelegramSink` in response to task
lifecycle events. Source: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`.

### Approval request — Story 3.10

```
🔒 Approval required — task t-01a2b3c4

Action: execute
Reason: implement retry logic in worker wrapper
Risk: medium

Pre-checks:
  ✅ Lint: 5/5
  ❌ Types: 3/5 (failed)
Diff: 3 files, +120, -15

Accepted commands:
  • /approve
  • /reject
  • /stop
```

**Budget:** `_APPROVAL_MESSAGE_MAX_CHARS = 1900`. Per-bullet cap: 200 chars.
Max commands shown: 10 (overflow line `… and N more`).

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | `TaskApprovalRequestedPayload.task_id` | `html.escape()` |
| `action` | `payload.action` | `html.escape()` |
| `justification` | `payload.justification` | `html.escape()` (after newline collapse) |
| `risk_class` | `payload.risk_class` (optional) | `html.escape()` |
| `pre_check_results` | `payload.pre_check_results` (optional) | Static labels + integer counters + emoji lookup |
| `diff_summary` | `payload.diff_summary` (optional) | Composed from integer counters |
| `accepted_commands` | `payload.accepted_commands` (optional) | `html.escape()` per command |

**Pre-check status emojis:**

| Status | Emoji |
|--------|-------|
| pass | ✅ |
| fail | ❌ |
| skipped | ⏭️ |
| error | ⚠️ |
| unmapped (fallback) | ❓ |

**Section-drop ladder (on overflow):**

1. Drop `(failed)` suffix from pre-check lines.
2. Drop diff summary section.
3. Binary-search for largest visible command count.
4. Drop pre-check block entirely.
5. Emergency one-liner: `🔒 Approval required — task {id}` + `(message body too large; see /logs {id})`.

**Rationale:** The approval request is the highest-stakes template — the operator
must decide whether to permit a code change. All available context (risk class,
pre-check results, diff stats, available actions) is shown so the decision can
be made without leaving Telegram. The section-drop ladder preserves the header
and reason (most actionable fields) while shedding optional detail under overflow.

---

### Blocker notification — Story 3.11

```
⛔ Task t-01a2b3c4 blocked. cannot resolve import path for upstream/cli.py. See /logs t-01a2b3c4 for detail.

Blocked since: 2026-05-04T14:30:22+00:00
Last event: task.execution.started at 2026-05-04T14:28:00+00:00
Last action: retry
Available commands:
  • /logs
  • /retry
  • /stop
  • /handoff
```

**Budget:** `_BLOCKER_MESSAGE_MAX_CHARS = 1900`.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | `TaskBlockerRaisedPayload.task_id` | `html.escape()` |
| `reason` | `payload.reason` | `html.escape()` (after newline collapse + trailing punctuation strip) |
| `blocked_since` | `payload.blocked_since` (optional) | ISO 8601 — no escape needed |
| `last_event` | `payload.last_event` (optional) | `html.escape()` (after newline collapse) |
| `last_action` | `payload.last_action` (optional) | `html.escape()` (after newline collapse) |

**Section-drop ladder (on overflow):**

1. Full message.
2. Drop `last_action`.
3. Drop `last_event`.
4. Drop `blocked_since`.
5. Emergency one-liner: `⛔ Task {id} blocked. (message body too large; see /logs {id})`.

**Rationale:** Blockers require operator intervention. The reason is embedded in
the header line so the operator sees what went wrong without scrolling. The
available-commands footer is preserved as long as possible so the operator can
act immediately (retry, stop, or check logs).

---

### Completion summary — Story 3.12

```
✅ Task t-01a2b3c4 complete.

PR #47: fix/retry-logic — https://github.com/org/repo/pull/47
3 files changed, 120+ / 15- lines.
12 tests added.
CI: ✅ green
1 blockers raised.
Retry logic in worker wrapper now handles transient failures with exponential backoff.
```

**Budget:** `_COMPLETED_MESSAGE_MAX_CHARS = 1900`.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | `TaskCompletedPayload.task_id` | `html.escape()` |
| `pr_number` | `payload.pr_number` (optional) | Integer — no escape |
| `pr_branch` | `payload.pr_branch` (optional) | `html.escape()` (after newline collapse) |
| `pr_url` | `payload.pr_url` (optional) | `html.escape()` (after newline collapse) |
| `files_changed` | `payload.files_changed` (optional) | Integer — no escape |
| `lines_added` | `payload.lines_added` (optional) | Integer — no escape |
| `lines_removed` | `payload.lines_removed` (optional) | Integer — no escape |
| `tests_added` | `payload.tests_added` (optional, >0 only) | Integer — no escape |
| `ci_state` | `payload.ci_state` (optional) | `html.escape()` |
| `blockers_count` | `payload.blockers_count` (optional, >0 only) | Integer — no escape |
| `summary` | `payload.summary` (required) | `html.escape()` (after newline collapse) |

**CI state emojis:**

| State | Emoji |
|-------|-------|
| green | ✅ |
| red | ❌ |
| unknown | ❓ |
| fallback (unmapped) | ❔ |

**PR line shapes (7 forms):** Composed from `pr_number`, `pr_branch`, `pr_url`
presence. Full form: `PR #{number}: {branch} — {url}`. Minimal forms: `PR: {url}`,
`Branch: {branch}`, `PR: #{number}`.

**Diff stats line shapes (7 forms):** Composed from `files_changed`, `lines_added`,
`lines_removed` presence. Full form: `{N} files changed, {A}+ / {R}- lines.`.
Zero counters treated as absent.

**Section-drop ladder (on overflow):**

1. Full message.
2. Drop diff stats.
3. Drop blockers count.
4. Drop tests count.
5. Drop CI state.
6. Drop PR line.
7. Emergency one-liner: `✅ Task {id} complete. (message body too large; see /logs {id})`.

The summary is dropped last — it's the operator-supplied human-readable
description and semantically the most valuable field.

**Rationale:** Completion summaries let the operator verify outcomes at a glance.
PR number, diff stats, and CI state answer "did it work?" without opening GitHub.
The summary provides the worker's own assessment. The section-drop ladder
prioritizes the summary over counters because the human-written text is more
useful than raw numbers when space is scarce.

---

### Self-recovered summary — Story 3.13

```
🛠️ Self-recovered from host restart at 2026-05-04T14:35:00+00:00. 3 events replayed in 142 ms. Zero intervention required.
```

**Budget:** `_SELF_RECOVERED_MESSAGE_MAX_CHARS = 1900`. Unreachable for valid
inputs (worst case ~140 chars); the cap defends against `model_construct` bypass.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `recovered_at` | `TaskSelfRecoveredPayload.recovered_at` | ISO 8601 — no escape needed |
| `events_replayed` | `payload.events_replayed` | Integer — pluralized (`event`/`events`) |
| `replay_duration_ms` | `payload.replay_duration_ms` | Integer — no escape |

**No section-drop ladder** — all fields are required and bounded by construction.
The single-line template has no optional sections. A defensive length clamp
exists but is unreachable for valid model-bound inputs.

**Rationale:** Self-recovery is informational — no operator action required.
The message is intentionally single-line so it reads as a status pulse rather
than a call-to-action. The "Zero intervention required" suffix reassures the
operator that the system healed itself.

---

## Command reply catalog

Success replies from `telegram-gateway` handlers in response to operator
commands. Source: `services/telegram-gateway/src/telegram_gateway/handlers/`.

### `/ping` pong reply — Story 3.5

```
pong · registry: healthy · worker: healthy · clawhip: 3 events queued · version: 1.2.3
```

**Budget:** No explicit cap. Well under 4096 chars.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `registry_status` | Health check response | `html.escape()` |
| `worker_status` | Health check response | `html.escape()` |
| `clawhip_queue_depth` | `HealthResponseLocal.clawhip_queue_depth` | Integer — no escape |
| `version` | Package version | `html.escape()` |

When registry is unhealthy, the reply uses `⚠️` prefix:
`⚠️ pong · registry: unhealthy · ...`

**Rationale:** `/ping` is the first debugging command an operator reaches for.
The multi-signal format (registry, worker, clawhip queue, version) answers
"is the system alive and healthy?" in a single message.

---

### `/task` creation reply — Story 3.3

```
Task <code>t-01a2b3c4</code> created. Planning. Events on thread.
```

On idempotent retry: `... Events on thread. (retry deduped)`

**Budget:** No explicit cap. Well under 4096 chars.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | `TaskCreatedResponse.task_id` | Wrapped in `<code>` tags (HTML parse mode) |
| `idempotency_status` | Response field | Appends `(retry deduped)` when `"replayed"` |

**Rationale:** The `/task` reply confirms task creation and sets expectations:
the task is now in the planning phase, and events will appear in the Telegram
thread. The `<code>` tag renders the task ID in monospace for easy visual
identification and copy-paste.

---

### `/approve` decision reply — Story 3.4

```
✅ Approved by @operator at 2026-05-04T14:30:22+00:00. Pushing.
```

On idempotent retry: `... (retry deduped). Pushing.`

**Budget:** No explicit cap. Well under 4096 chars.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `operator_handle` | `message.from_user.username` or fallback | `html.escape()` at assignment time |
| `decided_at` | `DecisionResponseLocal.decided_at` | ISO 8601 — no escape needed |

**Rationale:** The approve reply confirms the operator's decision and signals
that execution will resume. The `@operator` handle provides attribution. The
`(retry deduped)` suffix on replays prevents confusion about duplicate actions.

---

### `/status` reconstituted state — Story 3.14

```
📋 Task <code>t-01a2b3c4</code>
Status: running
Title: implement retry logic
Created: 2026-05-04T14:30:22+00:00
Updated: 2026-05-04T14:32:00+00:00
Actor: worker/w-001
Last event: task.execution.started at 2026-05-04T14:31:00+00:00
Available: /approve, /stop
```

**Budget:** `_MAX_REPLY_LEN = 4000`. Truncated with `\n… (truncated)` suffix
on overflow.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | Command argument | `<code>` tags + `html.escape()` |
| `status` | `TaskResponseLocal.status` | `html.escape()` |
| `title` | `TaskResponseLocal.title` | `html.escape()` |
| `created_at` | `TaskResponseLocal.created_at` | ISO 8601 — no escape needed |
| `updated_at` | `TaskResponseLocal.updated_at` | ISO 8601 — no escape needed |
| `actor` | `TaskResponseLocal.actor` | `html.escape()` on kind and id |
| `last_event` | `TaskResponseLocal.last_event` | `html.escape()` on type + timestamp |
| `next_commands` | `TaskResponseLocal.next_commands` | `html.escape()` per command |

**Rationale:** `/status` is the primary observability command. It renders the
full reconstituted task state in a single message so the operator can assess
progress without leaving Telegram. The 4000-char budget allows for long titles
and large command lists. Task not found renders: `⚠️ Task not found: <code>{id}</code>`.

---

### `/logs` LLM digest — Story 3.15

```
📋 Logs digest for <code>t-01a2b3c4</code>

Worker attempted patch 3 times; succeeded on attempt 3 after adjusting import path. No blockers remaining.

⚠️ Older events were truncated to fit the digest. Run `oh-my-bmad-cli events t-01a2b3c4` for the full raw stream.
```

**Budget:** `_MAX_REPLY_LEN = 4000`. Header overhead is subtracted to compute
`max_digest_chars` for the LLM digest body. Truncation notice appended when
digest is trimmed.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | Command argument | `<code>` tags + `html.escape()` |
| `digest` | LLM summarization (Story 7.3) | `html.escape()` |
| `truncation_notice` | Computed from budget | Static text |

When the LLM digest service is not deployed, a placeholder reply explains the
limitation and suggests `oh-my-bmad-cli events {id}` for raw events.

**Rationale:** Raw event streams are too long for Telegram. The LLM digest
compresses salient information into the available character budget. The
truncation notice points the operator to the CLI for full details.

---

### `/stop` decision reply — Story 3.16

```
🛑 Stopped by @operator at 2026-05-04T14:30:22+00:00. Task halted.
```

On idempotent retry: `... (retry deduped). Task halted.`

**Budget:** No explicit cap. Well under 4096 chars.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `operator_handle` | `message.from_user.username` or fallback | `html.escape()` at assignment time |
| `decided_at` | `DecisionResponseLocal.decided_at` | ISO 8601 — no escape needed |

**Rationale:** The stop reply confirms the task has been halted. "Task halted"
is the terminal state indicator — the operator knows no further work will happen
on this task.

---

### `/reject` decision reply — Story 3.17

```
🚫 Rejected by @operator at 2026-05-04T14:30:22+00:00. Task stopped.
```

On idempotent retry: `... (retry deduped). Task stopped.`

**Budget:** `MAX_REASON_LENGTH = 1000` caps the optional reason text. Reply
itself has no explicit cap.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `operator_handle` | `message.from_user.username` or fallback | `html.escape()` at assignment time |
| `decided_at` | `DecisionResponseLocal.decided_at` | ISO 8601 — no escape needed |
| `reason` | Command argument (optional) | Capped at 1000 chars; sent to API |

**Rationale:** The reject reply confirms the task has been stopped with prejudice.
"Task stopped" distinguishes from `/stop`'s "Task halted" — rejection implies
the plan was unsuitable. The reason is transmitted to the API for the worker's
logs but not echoed in the reply (the operator just typed it).

---

### `/retry` decision reply — Story 3.18

```
🔄 Retried by @operator at 2026-05-04T14:30:22+00:00. Task resumed.
```

On idempotent retry: `... (retry deduped). Task resumed.`

**Budget:** `MAX_HINT_LENGTH = 1000` caps the optional hint text. Reply itself
has no explicit cap.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `operator_handle` | `message.from_user.username` or fallback | `html.escape()` at assignment time |
| `decided_at` | `DecisionResponseLocal.decided_at` | ISO 8601 — no escape needed |
| `hint` | Command argument (optional) | Capped at 1000 chars; sent to API |

**Rationale:** The retry reply confirms the task has been resumed. "Task resumed"
signals that execution continues from where it left off (or from the beginning
depending on the task's last checkpoint). The optional hint is transmitted to
the worker for course-correction but not echoed in the reply.

---

### `/agent` runtime query reply — Story 3.19

```
🤖 Task t-01a2b3c4: runtime=claude-code @operator
```

**Budget:** No explicit cap. Well under 4096 chars.

**Fields:**

| Field | Source | Escape |
|-------|--------|--------|
| `task_id` | Command argument (locally validated) | `html.escape()` |
| `runtime` | Phase 1 static: `claude-code` | `_DEFAULT_RUNTIME` constant |
| `operator_handle` | `message.from_user.username` or fallback | `html.escape()` at assignment time |

**Rationale:** `/agent` is a read-only query showing which runtime/provider owns
the task. In Phase 1, there is exactly one runtime (Claude Code) so the response
is static. When Epic 5 ships the session-registry MCP server (Story 5.9), this
reply extends to include `worker_id` and `session_id` — a single-line template
change. The `@operator` handle provides attribution context.

---

## Character-budget principles

Telegram hard-caps `sendMessage` at **4096 characters** (UTF-16 code units). The
project uses three tiered budgets. Budget constants are enforced via Python's
`len()` which counts **codepoints** (not UTF-16 code units). The 1900-codepoint
cap provides ~5% headroom for worst-case UTF-16 expansion (e.g. emoji that
expand from 1 codepoint to 2 UTF-16 surrogates).

| Tier | Budget | Where used | Constant |
|------|--------|------------|----------|
| Event templates | 1900 | Approval, blocker, completion, self-recovered | `_*_MESSAGE_MAX_CHARS` in `telegram_sink.py` |
| Command replies | 4000 | `/status`, `/logs` | `_MAX_REPLY_LEN` in handler files |
| Free-text inputs | 1000 | `/reject` reason, `/retry` hint | `MAX_REASON_LENGTH` / `MAX_HINT_LENGTH` in handler files |

### Parity invariant

All four event-template caps (`_APPROVAL_MESSAGE_MAX_CHARS`,
`_BLOCKER_MESSAGE_MAX_CHARS`, `_COMPLETED_MESSAGE_MAX_CHARS`,
`_SELF_RECOVERED_MESSAGE_MAX_CHARS`) MUST move together. Changing one requires
changing all four.

### Truncation logic

The `_truncate(text, limit)` helper in `telegram_sink.py`:
- If `len(text) <= limit`: return text unchanged.
- If `limit <= 0`: return `""`.
- If `limit == 1`: return `"…"`.
- Otherwise: return `text[:limit - 1] + "…"` (append Unicode ellipsis U+2026).

### Truncation-before-escaping rule

Truncation MUST happen before `html.escape()`, not after. The character count
must be predictable — escaping can expand `&` to `&amp;` (1 char → 5 chars),
making post-escape truncation positions incorrect and potentially splitting
HTML entities mid-token.

### Emergency one-liner fallback

Templates with section-drop ladders have a final emergency one-liner when no
sections can be dropped to fit under the cap:

```
{emoji} Task {task_id} {verb}. (message body too large; see /logs {task_id})
```

The `task_id` is collapsed (newlines removed), sliced to 64 chars (matching the
model-boundary `max_length`), then HTML-escaped. The order (collapse → slice →
escape) prevents splitting HTML entities and prevents newlines from smuggling
content into a supposedly single-line message.

### Error replies

Command handlers share a consistent error-reply pattern using `⚠️` prefix.
The format varies by exception type. Two handler groups use slightly different
messages:

**Decision handlers** (`/task`, `/approve`, `/stop`, `/reject`, `/retry`, `/agent`):

| Exception | Reply pattern |
|-----------|---------------|
| `TooManyRedirects` | `⚠️ Registry misconfigured: too many redirects.` |
| `HTTPStatusError` | `format_http_error(exc, command_label=...)` — includes status code + detail |
| `RegistryResponseError` | `⚠️ Registry returned an unexpected response. Logs captured.` |
| `HTTPError` | `⚠️ Could not reach registry: {exception_type}.` |
| `Exception` (backstop) | `⚠️ Internal error. Logs captured.` |

**Read-only handlers** (`/ping`, `/status`, `/logs`) use softer messages for
network errors (no exception type exposed): `⚠️ Registry unreachable. Try again
in a moment.` or `⚠️ Could not reach registry. Please try again later.`

Error reply constants: `_VALIDATION_FIELD_CAP = 5`, `_VALIDATION_BULLET_MAX_CHARS = 200`,
`_VALIDATION_MESSAGE_MAX_CHARS = 3500` (in `_errors.py`).

---

## See also

- [Operator runbook](./operator-runbook.md) — tunnel health + bot token rotation.
- [Testing guide](./testing-guide.md) — contract-fixture recording for bot response templates.
