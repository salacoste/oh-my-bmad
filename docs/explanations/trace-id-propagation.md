# Trace-id propagation — how one operation is stitched across the platform

> Phase-2 deep-dive (Epic 9, NFR-O7). Companion to
> [ADR-0004](../adr/0004-trace-id-propagation.md) — the ADR records the *decision*;
> this explains *how it works* so you can debug with it.

## The problem it solves

A single operator action — "deploy this fix" — fans out across many processes:
telegram-gateway (or console-cli) → registry-api → registry-state, and
orchestrator-adapter → worker-wrapper → the `claude` subprocess, all emitting
events through MCP onto the JSONL event spine. When something goes wrong four
services deep at 3am, you need to reconstruct *that one operation's* causal chain
out of interleaved logs and thousands of events. `trace_id` is the single thread
you pull to do that.

## The one rule: mint once at the edge, forward everywhere

A `trace_id` is established **exactly once**, at the first boundary a logical
operation crosses, and then **carried verbatim** through every downstream call,
event, and log line. Re-minting mid-chain would split one operation into two
uncorrelated stories — so every boundary either *preserves* an inbound trace_id
or *mints* a fresh one, but never overwrites one that's already valid.

Two shapes are valid (one validator, `is_valid_trace_id`, `packages/events/src/events/envelope.py:155-181`):

- **Opaque UUIDv7** — the default, minted where there's no upstream hint.
- **`tg:<update_id>`** — derived from a Telegram `Update.update_id`, so a Telegram
  *retry* of the same update deterministically reuses the same trace_id (it
  composes with FR28 idempotency: a retried command correlates instead of forking).

Anything else is rejected at the envelope boundary — a malformed trace_id never
silently becomes part of an event.

## The propagation chain (where it's minted, where it rides)

```
Telegram Update.update_id ──derive──▶ tg:{update_id}
console-cli ──────────────mint──────▶ UUIDv7
        │ (X-Trace-Id header)
        ▼
registry-api  TraceIdMiddleware  ──preserve-if-valid / else mint──▶ request.state.trace_id
        ├──▶ structlog bind  → every log line for this request carries trace_id
        └──▶ EventEnvelope.create(trace_id=...) on every emit → events table `trace_id` column (indexed)

orchestrator-adapter ──spawns worker with env OMB_TRACE_ID──▶ worker-wrapper
        worker resolve_trace_id() (OMB_TRACE_ID | WORKER_TRACE_ID | mint)
        ├──▶ injects OMB_TRACE_ID into the `claude` subprocess env
        └──▶ passes caller_trace_id on every emit_event MCP call → emitted events carry it

MCP tools ──take an explicit caller_trace_id input (never ambient)──▶ emit with that trace_id
```

Key files:
- **HTTP boundary:** `services/registry-api/.../adapters/middleware.py:161-281`
  (`TraceIdMiddleware`) reads `X-Trace-Id`; valid → preserve, else mint UUIDv7;
  binds to structlog; echoes on the response.
- **Telegram boundary:** `services/telegram-gateway/.../app/middleware.py:187-320`
  derives `tg:{update_id}`.
- **Worker boundary:** `services/worker-wrapper/.../app/config.py` `resolve_trace_id()`
  + `OMB_TRACE_ID` subprocess injection.
- **MCP tools:** take `caller_trace_id` as an explicit argument (Story 9.5), never
  read ambient context — so a tool can't accidentally emit under the wrong trace.

## Debugging with it: `/trace <id>`

`GET /v1/trace/{trace_id}` (`services/registry-api/.../routes/trace.py:161-246`,
FR59a) validates the id, queries the registry-state `events` table by the indexed
`trace_id` column, and returns every event for that trace ordered by
`emitted_at_monotonic_ns` — the coherent causal chain, paginated (`limit` ≤ 2000,
`after_event_id` cursor). Grab a `trace_id` from any log line or event and replay
the whole operation.

## What it is NOT

`trace_id` is **correlation only** — never authentication or authorization. It is
operator-visible and freely forwarded; nothing may gate access on it. (Operator
*authenticity* is a separate mechanism — HMAC approval signing, ADR-0006.)

## The cutover (why old events still parse)

The envelope `schema_version` went `1.0.0 → 1.1.0` **additively** when `trace_id`
became required. Pre-Epic-9 `v1.0.0` events (no trace_id) remain *parseable* for a
one-month transition window (`packages/events/src/events/backfill.py` accepts
1.0.0/1.0.1/1.1.0), after which 1.0.0 support is dropped. The migrator can stamp a
**synthetic** trace_id onto pre-9.7 events (flagged via `trace_id_synthetic_source`)
so historical events still join `/trace` chains — with the marker making clear
those are backfilled, not original causality.

## See also
- [ADR-0004](../adr/0004-trace-id-propagation.md) — the decision + cutover plan.
- [event-spine.md](./event-spine.md) — the JSONL event log `trace_id` rides on.
- [ADR-0006](../adr/0006-approval-signing-and-rotation-protocol.md) — operator
  authenticity (distinct from correlation).
