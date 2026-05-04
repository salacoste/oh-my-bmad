# Renderer conventions

Consolidated reference for the Telegram message-rendering subsystem in
`clawhip-daemon`. Covers patterns established across Stories 3.10–3.13 and
carried forward into all subsequent renderers.

---

## HTML escape contract

1. **Escape exactly once.** `html.escape()` is idempotent only on strings
   containing no HTML entities. Applying it twice doubles `&amp;` to
   `&amp;amp;`, corrupting output.
2. **Truncate before escaping.** Character counts must be predictable.
   Truncation happens on the raw string; the truncated result is then escaped.
   Escaping first makes final length unpredictable because `&` and `<` expand
   to multi-character entities.
3. **Escape all operator-supplied strings.** Task IDs, usernames, reasons,
   summaries, branch names, error descriptions — any string sourced from an
   external system or operator input. Static labels and emoji prefixes do not
   need escaping.
4. **Defense-in-depth on registry-controlled strings.** Values bound by
   `Literal[...]` (e.g. `risk_class`, `ci_state`) are escaped despite being
   safe today. If the model is relaxed to a bare `str`, the renderer is already
   drift-safe.

---

## Newline collapse

The `_collapse_newlines(text)` helper replaces all line-break sequences
(`\r\n`, `\r`, `\n`) with single spaces. Order matters: `\r\n` is collapsed
first so the trailing `\n` doesn't decay a CRLF to `" \n"` → `"  "`.

Used on both operator-supplied free-form text (`reason`, `last_action`,
`summary`) and registry-controlled strings (`last_event`, `justification`)
before HTML-escaping. This keeps section separators (`\n\n`) visually
unambiguous.

**Known gap:** `_collapse_newlines` does not handle Unicode line separators
U+2028 and U+2029. These are extremely rare in practice and deferred to a
future story.

---

## Character budget discipline

Telegram hard-caps `sendMessage` at 4096 characters (UTF-16 code units). The
project uses three tiered budgets measured in Python `len()` (codepoints, not
UTF-16 code units):

| Tier | Budget | Where used | Constant |
|------|--------|------------|----------|
| Event templates | 1900 | Approval, blocker, completion, self-recovered | `_*_MESSAGE_MAX_CHARS` |
| Command replies | 4000 | `/status`, `/logs` | `_MAX_REPLY_LEN` |
| Free-text inputs | 1000 | `/reject` reason, `/retry` hint | `MAX_REASON_LENGTH` / `MAX_HINT_LENGTH` |

The 1900-codepoint cap provides ~5% headroom for worst-case UTF-16 expansion
(e.g. emoji that expand from 1 codepoint to 2 UTF-16 surrogates: 1900 × 2 =
3800 < 4096).

### Parity invariant

All four event-template caps (`_APPROVAL_MESSAGE_MAX_CHARS`,
`_BLOCKER_MESSAGE_MAX_CHARS`, `_COMPLETED_MESSAGE_MAX_CHARS`,
`_SELF_RECOVERED_MESSAGE_MAX_CHARS`) MUST move together. Changing one requires
changing all four.

---

## Section-drop ladder pattern

Templates with optional sections implement progressive removal on overflow:

1. Assemble fully-populated message; return if under cap.
2. Drop least-valuable optional section.
3. Continue dropping sections in priority order.
4. Emergency one-liner fallback when no sections can be dropped.

The priority order is template-specific (see [message-design.md](./message-design.md)
for each template's ladder).

---

## Emergency one-liner fallback

The final fallback when no sections can be dropped:

```
{emoji} Task {task_id} {verb}. (message body too large; see /logs {task_id})
```

The `task_id` is processed in strict order: **collapse → slice → escape**.

1. `_collapse_newlines(task_id)` — prevents newlines from smuggling content
   into a supposedly single-line message.
2. `task_id_safe[:64]` → produces `task_id_capped` — caps at the
   model-boundary `max_length=64`.
3. `html.escape(task_id_capped)` — prevents HTML injection.

This order is critical. Reversing it (escape → slice) would split HTML
entities mid-token: 64 raw `<` chars escape to 320 chars of `&lt;`, and
slicing at 64 would produce broken markup like `...&l`.

A defensive final-length self-clamp exists as a safety net:

```python
if len(result) > _APPROVAL_MESSAGE_MAX_CHARS:  # or equivalent per-template cap
    result = result[:_APPROVAL_MESSAGE_MAX_CHARS]
```

---

## Renderer dispatcher architecture

`_RENDERERS` is a `MappingProxyType[str, Callable[[EventEnvelope], str]]`
dispatch table mapping event types to renderer functions.

- **`MappingProxyType`** enforces runtime immutability; mypy enforces at the
  type level.
- **`EventEnvelope` input** — renderers receive the full envelope, not just the
  payload, so they can extract `event_type` and `task_id` for fallback paths.
- **Placeholder fallback** — event types without a registered renderer get
  `Task {id}: {type}` HTML-escaped. This preserves Story 3.9's shape.
- **`isinstance` guard** — each renderer checks `isinstance(payload, ExpectedType)`
  before rendering. If the check fails (schema-registry race, version drift),
  a structured WARN is emitted and the placeholder shape is returned.

---

## Renderer purity contract

Renderers are **synchronous** (`def`, not `async def`). They perform no I/O,
no network calls, no database queries, no mutations. They take an
`EventEnvelope` and return a `str`.

The only side effect is a structured WARN log on type-mismatch (defense-in-depth
for SRE observability).

---

## Model-boundary validators

Payload models use Pydantic `ConfigDict(frozen=True, strict=True, extra="forbid")`
with per-field validators:

- `task_id`: `max_length=64`, non-empty string.
- `reason`, `justification`, `summary`: `max_length` bounded by the template's
  character budget minus header/footer overhead.
- Integer counters (`files_changed`, `lines_added`, etc.): `ge=0`.

These validators are the first line of defense. Renderers apply additional
defense-in-depth (emergency truncation, HTML escape) for any path that bypasses
Pydantic validation (e.g. `model_construct`).

---

## Schema registration convention

Event type registrations live in
`services/registry-state/src/registry_state/domain/event_types.py`,
side-by-side with the model class. Registration is:

```python
register("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
```

The schema registry (`events.schema_registry`) is a global `REGISTRY` dict
keyed by `(type, version)` tuples. Idempotent — duplicate registrations are
no-ops.

---

## Test conventions

### Idempotent registration guard

Test files use an `autouse` fixture that calls `unregister_all()` before and
after each test to prevent cross-test registry contamination.

### Parametric cap-overflow tests

Each renderer has parametric tests at the character-budget boundary:
- Message exactly at the cap (passes).
- Message 1 char over the cap (triggers section-drop ladder).
- Message so long only the emergency one-liner fits.

### Per-character HTML assertions

Tests assert specific HTML-escaped output characters rather than using
substring matching, preventing false passes from overlapping escape sequences.

---

## Emoji discipline

Fixed catalog from `docs/message-design.md` — one prefix emoji per category.
Do not introduce emojis outside the catalog.

| Emoji | Category |
|-------|----------|
| ✅ | success / approved |
| ⚠️ | warning / error |
| ⛔ | blocker |
| 🔒 | approval-required |
| 🛠️ | self-recovered |
| 🛑 | stopped |
| 🚫 | rejected |
| 🔄 | retried |
| 📋 | status / logs |
| 🤖 | agent |

---

## Cross-service import pattern

Renderers live in `services/clawhip-daemon` and import from `packages/events`
only (enforced by `scripts/check_imports.py`). The `events` package re-exports
payload models, so renderers use:

```python
from events import TaskApprovalRequestedPayload, EventEnvelope
```

Direct imports from `registry_state.domain.event_types` are prohibited by the
import graph rules. The `IMP001` noqa cluster on `telegram_sink.py` is a
remnant of the pre-3.5.2 refactor; Story 3.5.2 moved payload models to
`packages/events/`, eliminating the cross-service import.

---

## See also

- [Message design](./message-design.md) — template mockups, field lists,
  character budgets.
- [Development guide](./development.md) — project structure and tooling quirks.
