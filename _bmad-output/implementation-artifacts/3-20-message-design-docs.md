# Story 3.20: Optional sidecar — `docs/message-design.md`

Status: done

## Story

As **the operator**,
I want **a single reference doc specifying Telegram message templates, character budgets, HTML safety conventions, emoji discipline, and the `/status` reconstitution schema**,
so that **message-design choices are documented and reviewable outside of code**.

This is a documentation-only story. No production code changes. The existing `docs/message-design.md` was written early in Epic 3 (after Story 3.5) against spec assumptions that have since diverged from implementation. This story rewrites it to match the actual code.

**What this story is NOT:**
- NOT a code change — zero production files modified.
- NOT a new template — all templates already ship with Stories 3.10–3.19.
- NOT a test story — no test files created or modified.

## Acceptance Criteria

1. **AC-1: Template discipline section** — replaces the stale "Markdown-v2" section with:
   - HTML parse mode (`ParseMode.HTML`) set globally via `DefaultBotProperties` in `lifespan.py`.
   - `html.escape()` on ALL externally-sourced strings (task IDs, usernames, reasons, summaries).
   - Call `html.escape()` exactly once per value — double-escaping corrupts output.
   - 4096-char Telegram hard cap documented; 4000-char target for command replies; 1900-char target for event-driven templates (clawhip-daemon renderers).
   - Field order contract: `task_id` → summary → actions (where applicable).

2. **AC-2: Emoji discipline table** — updated to match actual implementation:

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

   No emojis outside this table.

3. **AC-3: Event-driven template catalog** — each template from `clawhip-daemon/adapters/sinks/telegram_sink.py` gets its own section with:
   - Example rendering (mock data, matching the actual f-string).
   - Character budget constant and value.
   - Field list with source (payload field names).
   - Section-drop ladder summary (for templates that have one).
   - Rationale paragraph.

   Templates to document (4):
   - Approval request (Story 3.10) — emoji `🔒`, budget 1900.
   - Blocker notification (Story 3.11) — emoji `⛔`, budget 1900.
   - Completion summary (Story 3.12) — emoji `✅`, budget 1900.
   - Self-recovered summary (Story 3.13) — emoji `🛠️`, budget 1900.

4. **AC-4: Command reply catalog** — each Telegram command's success reply gets its own section with:
   - Example rendering (mock data, matching actual f-string).
   - Character budget constant and value (if any).
   - Field list with source.
   - Rationale paragraph.

   Commands to document (9):
   - `/ping` (Story 3.5) — no emoji prefix on healthy.
   - `/task` (Story 3.3) — no emoji prefix.
   - `/approve` (Story 3.4) — emoji `✅`.
   - `/status` (Story 3.14) — emoji `📋`, budget 4000.
   - `/logs` (Story 3.15) — emoji `📋`, budget 4000.
   - `/stop` (Story 3.16) — emoji `🛑`.
   - `/reject` (Story 3.17) — emoji `🚫`, budget `MAX_REASON_LENGTH=1000`.
   - `/retry` (Story 3.18) — emoji `🔄`, budget `MAX_HINT_LENGTH=1000`.
   - `/agent` (Story 3.19) — emoji `🤖`.

5. **AC-5: Pre-check status emoji sub-table** — nested within the approval request section:

   | Status | Emoji |
   |--------|-------|
   | pass | ✅ |
   | fail | ❌ |
   | skipped | ⏭️ |
   | error | ⚠️ |
   | unknown | ❓ |

   Plus the CI-state emoji sub-table within the completion summary section:

   | State | Emoji |
   |-------|-------|
   | green | ✅ |
   | red | ❌ |
   | unknown | ❓ |
   | fallback | ❔ |

6. **AC-6: Character-budget principles section** — documents:
   - 4096-char Telegram hard cap.
   - 1900-char target for event-driven templates (clawhip-daemon `_*_MESSAGE_MAX_CHARS` constants).
   - 4000-char target for command replies (`_MAX_REPLY_LEN` in status/logs handlers).
   - 1000-char caps for `/reject` reason and `/retry` hint.
   - Truncation-before-escaping rule (character count must be predictable).
   - `_truncate(text, limit)` helper behavior (ellipsis appended).
   - Emergency one-liner fallback for templates that overflow despite section-drop ladder.

7. **AC-7: No production code changes** — the diff touches ONLY `docs/message-design.md` and this story file. Zero changes to `services/`, `packages/`, or `tests/`.

8. **AC-8: `just lint` still 9/9 green** — documentation-only change; no code to break.

9. **AC-9: Atomic commit** — title: `docs(telegram-gateway): story 3.20 — rewrite message-design.md to match implementation · E3-sidecar`

## Tasks / Subtasks

- [x] **Task 1: Rewrite `docs/message-design.md`** (AC: #1–#6)
  - [x] Replace stale MarkdownV2 / `escape_md()` section with HTML parse mode / `html.escape()` section.
  - [x] Update emoji discipline table to match implementation (add `🛑`, `🚫`, `🔄`, `📋`, `🤖`; change `🛑`→`⛔` for blocker, `🔄`→`🛠️` for self-recovered).
  - [x] Write event-driven template catalog sections (approval, blocker, completion, self-recovered) with actual f-strings, budgets, field lists, ladders, and rationale.
  - [x] Write command reply catalog sections (9 commands) with actual f-strings, budgets, field lists, and rationale.
  - [x] Write character-budget principles section with truncation logic and emergency fallback.
  - [x] Include pre-check status emoji sub-table and CI-state emoji sub-table.
  - [x] Remove stale references to `escape_md()`, MarkdownV2, backslash-escaped mock renderings.

- [x] **Task 2: Verification + atomic commit** (AC: #7, #8, #9)
  - [x] Confirm `just lint` 9/9 green (no code changed).
  - [x] Confirm no production files modified.
  - [x] Atomic commit.

## Dev Notes

### Staleness Inventory

The existing `docs/message-design.md` has the following divergences from implementation:

| # | Stale content | Actual implementation |
|---|---------------|-----------------------|
| 1 | `parse_mode=MarkdownV2` | `ParseMode.HTML` via `DefaultBotProperties` |
| 2 | `escape_md()` function | `html.escape()` from stdlib |
| 3 | Backslash-escaped mock renderings (`task\-`, `\_`) | HTML-escaped renderings (`&lt;`, `&amp;`) |
| 4 | 4096-char budget for everything | 1900 for event templates, 4000 for commands, 1000 for reason/hint |
| 5 | `🛑` for blocker | `⛔` (from `_render_blocker_raised`) |
| 6 | `🔄` for self-recovered | `🛠️` (from `_render_self_recovered`) |
| 7 | `📝` for logs | `📋` (from `handle_status` and `handle_logs`) |
| 8 | `🎯` for status | `📋` (from `handle_status`) |
| 9 | Missing `/stop` template | `🛑 Stopped by @{handle} at {iso}. Task halted.` |
| 10 | Missing `/reject` template | `🚫 Rejected by @{handle} at {iso}. Task stopped.` |
| 11 | Missing `/retry` template | `🔄 Retried by @{handle} at {iso}. Task resumed.` |
| 12 | Missing `/agent` template | `🤖 Task {id}: runtime=claude-code @{handle}` |
| 13 | Self-recovered is multi-line | Single-line template in `_render_self_recovered` |
| 14 | `/status` rendering doesn't match actual fields | Actual: task_id, status, title, created, updated, actor, last_event, available commands |
| 15 | Template renderings use `[button-label]` | No inline keyboards implemented yet (Phase 1) |
| 16 | `/ping` says `pong` only | Actual: `pong · registry: healthy · worker: healthy · clawhip: N events queued · version: v...` |

### Source Files for Template Content

| Content | Source file |
|---------|-------------|
| Approval renderer | `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` (`_render_approval_request`) |
| Blocker renderer | Same file (`_render_blocker_raised`) |
| Completion renderer | Same file (`_render_completed`) |
| Self-recovered renderer | Same file (`_render_self_recovered`) |
| Dispatcher | Same file (`_RENDERERS` MappingProxyType) |
| `/stop` reply | `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` |
| `/reject` reply | `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` |
| `/retry` reply | `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` |
| `/agent` reply | `services/telegram-gateway/src/telegram_gateway/handlers/agent_command.py` |
| `/status` reply | `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` |
| `/logs` reply | `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` |
| `/ping` reply | `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py` |
| `/task` reply | `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` |
| `/approve` reply | `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` |
| Parse mode setting | `services/telegram-gateway/src/telegram_gateway/app/lifespan.py:219` |
| Error templates | `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py` |

### Predicted File List

| File | Change |
|---|---|
| `docs/message-design.md` | Rewritten — matches implementation |
| `_bmad-output/implementation-artifacts/3-20-message-design-docs.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

### Completion Notes List

- ✅ Task 1: Rewrote `docs/message-design.md` — replaced stale MarkdownV2 content with HTML parse mode documentation. Updated emoji table (16 divergences fixed). Added 4 event-driven template sections (approval, blocker, completion, self-recovered) with accurate f-strings, field tables, budget constants, section-drop ladders, and rationale. Added 9 command reply sections with accurate renderings. Added character-budget principles section with parity invariant, truncation logic, emergency fallback, and error reply patterns.
- ✅ Task 2: `just lint` 9/9 green. Only `docs/message-design.md` + sprint-status modified — zero production code changes.

## Code Review Record

### Review Date: 2026-05-04
### Reviewers: Blind Hunter, Edge Case Hunter, Acceptance Auditor
### Outcome: Changes Requested → Fixed

### Fixes Applied (12):

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | HIGH | Approval mock missing `Accepted commands:` label | Added `Accepted commands:` before bullet list |
| 2 | HIGH | Approval Diff line format wrong (`120+ / 15- lines.` vs code `+120, -15`) | Fixed to `Diff: 3 files, +120, -15` |
| 3 | HIGH | `_truncate` ellipsis is ASCII `...` but code uses Unicode `…` | Changed to `…` (U+2026) |
| 4 | MED | Pre-check mock shows `passed` word and `(2 failed)` — code uses `{passed}/{total} (failed)` | Fixed to `✅ Lint: 5/5` and `❌ Types: 3/5 (failed)` |
| 5 | MED | `html.escape()` "idempotent on clean input" is misleading | Rephrased: idempotent only on strings with no HTML entities |
| 6 | MED | Pre-check `unknown` listed as first-class status but is `.get()` fallback | Renamed to "unmapped (fallback)" |
| 7 | MED | `/status` truncation suffix `\n...` but code uses `\n…` | Fixed to `\n… (truncated)` |
| 8 | HIGH | `/status` field table claims title truncated to 80 chars — no truncation in code | Removed "(first 80 chars)" |
| 9 | HIGH | Error reply table claims uniform pattern but `/ping`, `/status`, `/logs` differ | Documented two handler groups with distinct messages |
| 10 | MED | `/ping` field table says `queue_depth` but model field is `clawhip_queue_depth` | Fixed to `clawhip_queue_depth` with correct source |
| 11 | LOW | Completion `summary` marked `(optional)` but is required in model | Changed to `(required)` |
| 12 | LOW | Budget section says "UTF-16 code units" but constants use Python codepoints | Added note explaining codepoint-vs-UTF-16 distinction |

### Dismissed Findings (3):

| # | Finding | Reason |
|---|---------|--------|
| 1 | Parity invariant enshrines wider contract than original code comments | All four constants exist at 1900 with matching comments — doc is accurate |
| 2 | Blocker mock shows cosmetic double-period for reasons ending in file extensions | Accurate reflection of code behavior (rstrip only strips `.?!:` chars) |
| 3 | `/logs` placeholder header differs from main header | Accurate — placeholder uses "Logs for" while digest uses "Logs digest for" |
