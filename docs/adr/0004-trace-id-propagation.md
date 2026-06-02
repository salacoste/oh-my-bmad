---
id: ADR-0004
status: accepted
date: 2026-06-02
supersedes: null
---

# ADR-0004: trace_id propagation policy + cutover plan

## Status

**Accepted** — 2026-06-02. Documents the `trace_id` correlation kernel built by
**Epic 9** (Stories 9.1–9.7, closed 2026-05-18; FR57/FR58/FR59/FR59a, NFR-O7).
This ADR is the second of the five Phase-2 forward-referenced ADR acceptance-gate
items declared in [ADR-0003](./0003-phase-2-gate.md) (ADR-0004 through ADR-0008);
it was authored after the implementation shipped, to close the Phase-2
Ship-Blocker checklist item "ADR-0004 — trace_id propagation policy + cutover
plan." It MUST be `accepted` for the Phase-2 ship claim (NFR-O7 is part of the
Phase-2 spine).

## Context

oh-my-bmad runs multi-day autonomous coding-agent sessions across a fan of
services (telegram-gateway / console-cli → registry-api → registry-state, plus
orchestrator-adapter → worker-wrapper → Claude Code, all emitting events through
MCP). When something goes wrong mid-session, an operator must reconstruct the
**causal chain** of a single logical operation across service boundaries, event
records, and structured logs. Phase 1 had no correlation id — events and logs
could not be stitched into one story.

NFR-O7 (FR57–FR59a) introduced a single correlation token, `trace_id`, that
threads through every entry point, every cross-service call, every emitted event,
and every structured-log line. This ADR records the policy (how it propagates +
its two forms) and the cutover plan (the additive schema bump + back-compat
window) so the decision survives the implementation.

## Decision

### 1. `trace_id` is a REQUIRED envelope field at schema 1.1.0.

`EventEnvelope.trace_id` is a required string
(`packages/events/src/events/envelope.py:219-225`); the default envelope
`schema_version` was bumped `1.0.0 → 1.1.0` for it (`envelope.py:212`, Story 9.7).
Every event emitted post-Epic-9 carries a `trace_id` — enforced in CI (the
emit-site discipline gate / contract fixtures), satisfying the ship-blocker
"every new event in CI carries trace_id."

### 2. Two valid `trace_id` forms (one validator, `is_valid_trace_id`).

`is_valid_trace_id()` (`envelope.py:155-181`, enforced by the
`_trace_id_shape` field validator at `:275-302`) accepts exactly two shapes:

- **Opaque UUIDv7** (default, e.g. `01917e5c-a7d1-7000-8abc-...`) — minted at an
  entry boundary that has no upstream correlation hint.
- **Deterministic Telegram form** `tg:<update_id>` (`[1, 2^63-1]`, no leading
  zeros) — derived from the inbound Telegram `Update.update_id` so that Telegram
  retries of the SAME update deterministically map to the SAME trace_id (it
  composes with FR28 idempotency). Any other shape is rejected at the model
  boundary (fail-loud, not silently re-minted into the event).

### 3. Propagation policy: PRESERVE-OR-MINT at every ENTRY boundary; FORWARD thereafter.

A `trace_id` is established once, at the first boundary a logical operation
crosses, then carried verbatim downstream:

- **HTTP (registry-api):** `TraceIdMiddleware`
  (`services/registry-api/src/registry_api/adapters/middleware.py:161-281`) reads
  the `X-Trace-Id` header; if present AND valid it is **preserved**, otherwise a
  fresh UUIDv7 is **minted**; the value is bound to structlog contextvars and
  echoed on the response.
- **Telegram (telegram-gateway):** `AllowlistMiddleware`
  (`telegram-gateway/.../app/middleware.py:187-320`) **derives** `tg:{update_id}`
  from the inbound Update, binds it, and forwards it to the registry HTTP client
  as `X-Trace-Id`.
- **console-cli:** mints a UUIDv7 at command entry and forwards it as
  `X-Trace-Id`.
- **MCP tools:** take an explicit required `caller_trace_id` input (Story 9.5
  contract; `tests/contract/_trace_id_vectors.py`) — the caller MUST pass its
  resolved trace_id; the tool never invents one silently.
- **worker-wrapper / Claude Code subprocess:** the worker resolves its trace_id
  from `OMB_TRACE_ID` (aliases `WORKER_TRACE_ID` / `OMB_WORKER_TRACE_ID`;
  `app/config.py:311-347`, `resolve_trace_id()`), injects it into the Claude Code
  subprocess env as `OMB_TRACE_ID`, and threads it as `caller_trace_id` on every
  `emit_event` MCP call so emitted events land with the correct `trace_id`.

The invariant: **a trace_id is minted at most once per logical operation** (at its
origin boundary) and is otherwise forwarded — never re-minted mid-chain (which
would split one operation into two uncorrelated chains).

### 4. Cutover plan: additive bump, one-month back-compat window.

The `1.0.0 → 1.1.0` bump is **additive** (NFR-M3): pre-Epic-9 `v1.0.0` envelopes
(which have no `trace_id`) remain **parseable** — the reader/backfill path accepts
`1.0.0` / `1.0.1` / `1.1.0` (`packages/events/src/events/backfill.py`) — for a
**one calendar month** transition window after Epic 9 shipped, after which
`1.0.0` support is dropped. The migrator can stamp a **synthetic** trace_id onto
pre-9.7 events (marked via a `trace_id_synthetic_source` field) so historical logs
join `/trace` chains, with the synthetic marker making clear those are
backfilled, not original. New emissions always use `1.1.0` with a real trace_id.

### 5. The `/trace <id>` query reconstructs the chain.

`GET /v1/trace/{trace_id}` (`services/registry-api/src/registry_api/routes/trace.py:161-246`,
FR59a) validates the id shape, queries the registry-state `events` table by the
`trace_id` column (indexed), and returns the events for that trace ordered by
`emitted_at_monotonic_ns` (paginated, `limit` ≤ 2000, `after_event_id` cursor) —
the coherent causal chain the ship-blocker requires.

## Consequences

**Positive.**
- One token correlates HTTP request ↔ events ↔ structured logs ↔ subprocess
  across every service — the core observability win of Phase 2.
- `/trace <id>` gives operators a single command to reconstruct an operation.
- Telegram's deterministic `tg:<update_id>` form composes with idempotency
  (retries correlate instead of fanning out).
- Additive bump preserves Phase-1 replay/back-compat (no destructive migration).

**Negative / accepted trade-offs.**
- Every entry boundary MUST mint/derive (a forgotten boundary silently mints a
  fresh id, splitting a chain) — mitigated by the uniform `resolve_*`/middleware
  helpers + 30+ preserve/mint tests (`registry-api/.../test_middleware.py`).
- The one-month dual-version (`1.0.0`+`1.1.0`) window is scheduled debt — after it
  closes, `1.0.0` parsing must be removed (tracked by the cutover date).
- Synthetic backfilled trace_ids are correlation aids, not true causal chains —
  the `trace_id_synthetic_source` marker keeps that honest.
- `trace_id` is correlation only — NOT authentication or authorization (it is
  operator-visible and forwardable; it must never gate access).

## References

- [Source: epics.md — Epic 9 (Stories 9.1–9.7) + the Phase-2 Ship-Blocker item "ADR-0004 — trace_id propagation policy + cutover plan".]
- [Source: prd.md — FR57 (envelope field), FR58 (propagation across HTTP/Telegram/console/MCP), FR59 (worker→Claude Code), FR59a (/trace query), NFR-O7 (correlation contract).]
- [Source: ADR-0003 §Phase-2 gate — the ADR-0004..0008 forward-reference list this closes.]
- [Source: packages/events/src/events/envelope.py:155-225,275-302 — trace_id field + is_valid_trace_id + shape validator; schema_version 1.1.0.]
- [Source: services/registry-api/.../adapters/middleware.py:161-281 — TraceIdMiddleware preserve-or-mint; routes/trace.py:161-246 — /trace query.]
- [Source: services/telegram-gateway/.../app/middleware.py:187-320 — tg:{update_id} derivation; worker-wrapper app/config.py:311-347 — OMB_TRACE_ID resolve + subprocess injection.]
- [Source: packages/events/src/events/backfill.py — 1.0.0/1.0.1/1.1.0 acceptance (the one-month back-compat window).]
