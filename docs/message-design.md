# Message design

Telegram message template catalog and design rules. This is the
catalog-before-content reference: the templates themselves are implemented per
owning story in Epic 3. Each section below provides the mock rendering,
4096-character budget allocation, field list, and rationale.

---

## Template discipline

Every bot message follows four rules:

1. **4096-char hard cap** — Telegram rejects `sendMessage` calls over 4096
   chars. Target under 3800 to leave headroom for Markdown-v2 escaping.
2. **Markdown-v2 escaping** — Characters requiring a backslash escape in
   `parse_mode=MarkdownV2`: `` _ * [ ] ( ) ~ ` > # + - = | { } . ! ``
   Pass all user-supplied content through `escape_md()` before interpolation.
3. **Field order** — every message: `task_id` → `summary` → `actions`. This
   order is fixed so the operator sees context before call-to-action.
4. **Emoji minimalism** — one prefix emoji per category (see
   [Emoji discipline](#emoji-discipline)). No emoji decoration in body text.

---

## Markdown-v2 safety

```python
import re

_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#+=|{}.!\\-])')

def escape_md(text: str) -> str:
    return _ESCAPE_RE.sub(r'\\\1', text)
```

Call `escape_md()` on task IDs, file paths, error messages, and any
platform-generated string before interpolating into a template. Static labels
and emoji prefixes should be pre-escaped in the template string.

**Call `escape_md()` exactly once per value.** The regex includes `\\` in
its replacement class, so applying it to already-escaped strings doubles
the escapes and corrupts output. Never `escape_md(escape_md(x))`.

---

## Emoji discipline

Fixed catalog — do not introduce emojis outside this table:

| Emoji | Category | When to use |
|-------|----------|-------------|
| ✅ | success | Task completed successfully |
| ⚠️ | warning | Non-fatal issue; operator awareness needed |
| 🛑 | blocker | Task halted; operator action required |
| 🔒 | approval-required | Human approval gate reached |
| 🔄 | recovery | Automatic self-recovery occurred |
| 📝 | plan | Task plan emitted for review |
| 🎯 | task | General task status or progress |

The catalog is fixed so the operator's Telegram thread is visually grep-able:
a 🛑 is always a blocker, never ambiguous.

---

## Template catalog

**Note on `[button-label]` placeholders**: In the template renderings below,
text in square brackets (e.g. `[Approve]`, `[Reject]`, `[Full log in console]`)
represents **Telegram inline-keyboard buttons**, not literal MarkdownV2
link syntax. Message body text with actual `[` or `]` characters must be
backslash-escaped per the escape rules above.

### `/ping` pong reply — Story 3.5

```
✅ pong
```

Static reply; no platform-event fields. Budget: 8 chars emoji+label, 4088
padding. Confirms bot reachability and tunnel health after a restart.

---

### Approval request — Story 3.10

```
🔒 Approval required

Task: task\-20260101\-abc123
Plan: implement retry logic in worker\_wrapper

[Approve] [Reject]
```

Budget: header 24 + task\_id 60 + summary 3600 + keyboard 256 + padding 156 = 4096.
Fields: `task_id` from `task.approval.requested` (`payload.task_id`);
`summary` from `task.plan.committed` (`payload.plan_summary`, Story 5.11).
Inline keyboard avoids a follow-up command round-trip.

---

### Blocker notification — Story 3.11

```
🛑 Task blocked

Task: task\-20260101\-abc123
Blocked on: cannot resolve import path for upstream/omc/cli\.py

Operator action required\.
```

Budget: header 20 + task\_id 60 + description 3800 + footer 28 + padding 188 = 4096.
Fields: `task_id` and `blocker_description` from `task.blocked`
(`payload.task_id`, `payload.reason`).
The `reason` field is given the largest slice so operators can unblock without
reading logs.

---

### Completion summary — Story 3.12

```
✅ Task complete

Task: task\-20260101\-abc123
Result: opened PR \#47 — retry logic in worker\_wrapper
Duration: 4m 23s
```

Budget: header 18 + task\_id 60 + result 3500 + duration 20 + labels/padding 498 = 4096.
Fields from `task.completed`: `payload.task_id`, `payload.summary`;
`duration` derived from `payload.started_at` + `payload.completed_at`.
Result summary may contain a PR URL plus a one-sentence description.

---

### Self-recovered summary — Story 3.13

```
🔄 Task self\-recovered

Task: task\-20260101\-abc123
Recovered from: JSON parse error on omc stdout at attempt 2
Resumed at: step 4 of 7
```

Budget: header 24 + task\_id 60 + description 3600 + resume\_point 60 +
labels/padding 352 = 4096.
Fields from `task.recovered`: `payload.task_id`, `payload.error_summary`,
`payload.resume_step`. Message is informational — no operator action required.

---

### `/status` reconstituted state — Stories 3.14 + 7.2

```
🎯 Status: task\-20260101\-abc123

State: running
Step: 3 of 7 — writing patch
Started: 2026\-01\-15 14:30:22 UTC
Elapsed: 12m 04s
```

Budget: header 30 + task\_id 60 + state 20 + step 120 + timestamps 60 +
labels/padding 3806 = 4096.
`task_id` from command argument; `state` from registry materializer (Story 2.5);
`current_step` from last `task.step.started` event; `started_at` from
`task.created`. Reconstituted state (Story 7.1) is the single source of truth —
no in-memory state required.

---

### `/logs` LLM digest — Stories 3.15 + 7.3

```
📝 Log digest: task\-20260101\-abc123

Events: 23 total \(last 10 shown\)
Summary: worker attempted patch 3 times; succeeded on attempt 3 after
adjusting import path\. No blockers remaining\.

[Full log in console]
```

Budget: header 30 + task\_id/count 60 + digest 3600 + action hint 40 +
labels/padding 366 = 4096.
`task_id` from command argument; `event_count` from event log query;
`digest` from LLM summarisation over last N events (Story 7.3).
Raw event streams are too long for Telegram — the LLM digest compresses
salient information into the 3600-char slice.

---

## Character-budget principles

Telegram hard-caps at 4096 characters. Target under 3800 to leave a 296-char
buffer for Markdown-v2 escaping overhead — every escaped character costs one
extra byte. Fields that grow unboundedly (summaries, error descriptions, LLM
digests) must be truncated to their budget with an ellipsis (`…`) appended.
Truncation must happen before escaping, not after, or the character count
becomes unpredictable.

---

## See also

- [Operator runbook](./operator-runbook.md) — tunnel health + bot token rotation.
- [Testing guide](./testing-guide.md) — contract-fixture recording for bot response templates.
