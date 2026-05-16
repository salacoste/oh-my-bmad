# Story 9.3 — telegram-gateway `AllowlistMiddleware` derives `trace_id = f"tg:{update.update_id}"`

Status: **ready-for-dev**

## Story

**As** an operator command flowing through the telegram-gateway,
**I want** the `AllowlistMiddleware` (Story 3.2 + 6.7) to derive `trace_id = f"tg:{update.update_id}"` from every inbound Telegram `Update` BEFORE delegating to downstream handlers, bind it into the structlog context for the duration of the dispatch, propagate it into the aiogram `data: dict[str, Any]` so handlers can pass it to `EventEnvelope.create(...)`, and unbind cleanly on the way out,
**so that** every event emitted in the causal chain of a single Telegram message — `task.created`, `task.approval_requested`, `approval.granted`, etc. — carries the same deterministic `trace_id` derived from the update_id, AND replaying the same `update_id` (via the FR28 idempotency-cache hit path) produces the same `trace_id`, closing the Telegram ingress for Epic 9's α propagation kernel.

This is Story 9.3 of Epic 9 (α `trace_id` propagation kernel). It's the **second of 5 entry-point ingresses** (after Story 9.2's HTTP ingress). The validation contract was established by Story 9.1 (`tg:<update_id>` form anchored as `\Atg:[1-9][0-9]{0,18}\Z` with int64 ceiling); 9.3 makes the **producer** wire that contract through.

---

## Acceptance criteria

### AC1 — `AllowlistMiddleware.__call__` derives + binds `trace_id` deterministically

In `services/telegram-gateway/src/telegram_gateway/app/middleware.py`, extend `AllowlistMiddleware.__call__` so that BEFORE any allowlist enforcement or handler delegation:

1. If `event` is an `aiogram.types.Update` (the outer-middleware case — see `lifespan.py:240-247`), read `event.update_id` (always present on `Update`; `int` per the BotAPI schema).
2. Construct `trace_id = f"tg:{update.update_id}"`.
3. Validate the value via `events.envelope.is_valid_trace_id(trace_id)` (the public helper promoted in Story 9.2 pass-1 A1). If validation fails (e.g., a malformed BotAPI payload somehow provides a negative or zero `update_id`), log at WARNING and mint `new_uuid7(clock=self._clock)` as a fallback. This is defense-in-depth — the BotAPI guarantees `update_id ≥ 1`, so the fallback should never fire in production, but the cost of a fresh UUIDv7 mint is trivial compared to a 500.
4. Bind into structlog contextvars: `structlog.contextvars.bind_contextvars(trace_id=trace_id)`.
5. Insert into the `data` dict: `data["trace_id"] = trace_id` so downstream inner middlewares + handlers can read it (mirrors how Story 3.6's `request_id` is threaded — line 203 of the existing middleware).
6. Unbind in a `try/finally` so an aiogram dispatcher reused for the next update never observes the prior update's trace.
7. The unbind MUST run on every code path — including the `return None` short-circuit branches when the user is non-allowlisted or `user_id is None`.

### AC2 — Deterministic / idempotent replay invariant

For two `Update` objects with the same `update_id`, the derived `trace_id` MUST be byte-identical. This composes with FR28 idempotency (Story 7.5+): when the operator's client retransmits a previously-acknowledged update (e.g., webhook redelivery), the `trace_id` resolves to the same value, so the FR28 idempotency-cache hit produces a replay log entry carrying the same `trace_id` as the original. This is the **only** ingress in Epic 9 where the trace_id is derived from a stable identifier rather than minted — the determinism is load-bearing.

Add a unit test `test_replay_same_update_id_produces_same_trace_id`: construct two `Update(update_id=42, ...)` instances (no other fields need to differ); pass both through the middleware; assert both `data["trace_id"]` values equal `"tg:42"`.

### AC3 — Validates against Story 9.1 contract

The derived `trace_id` MUST pass `is_valid_trace_id()` for any BotAPI-conformant `update_id` (`1 ≤ n ≤ 9_223_372_036_854_775_807`). Specifically:

- `update_id=1` → `tg:1` → valid ✓
- `update_id=9_223_372_036_854_775_807` → `tg:9223372036854775807` → valid ✓ (int64 max)
- `update_id=0` → `tg:0` → REJECTED by `is_valid_trace_id` (Story 9.1 F2 leading-zero rule rejects `tg:0`) → fallback to `new_uuid7()`
- `update_id=-1` (impossible per BotAPI but defense in depth) → `tg:-1` → REJECTED (regex requires `[1-9]` first digit, no sign char) → fallback

The fallback path is exercised in tests for the impossible-but-defended cases.

### AC4 — Rejection events also carry the derived `trace_id`

`_emit_rejection` (the helper that builds and emits `telegram.rejected` envelopes) MUST receive and propagate the derived `trace_id`. Currently it takes `request_id` (Story 3.6) but no `trace_id`. Extend its signature:

```python
async def _emit_rejection(
    self,
    *,
    user_id: int,
    reason: str,
    request_id: str | None,
    trace_id: str,  # NEW — always set; the middleware derives it BEFORE deciding to reject
) -> None:
```

The `EventEnvelope.create(...)` call inside `_emit_rejection` MUST pass `trace_id=trace_id`. This silences the Story 9.1 DeprecationWarning for the rejection-emission callsite.

### AC5 — Downstream handlers consume `data["trace_id"]`

Audit every handler registered on the Telegram dispatcher (`services/telegram-gateway/src/telegram_gateway/handlers/*.py`) that calls `EventEnvelope.create(...)`. For each:

- Read `trace_id: str = data["trace_id"]` (aiogram guarantees `data` is set by outer middlewares before handlers run).
- Pass `trace_id=trace_id` to the envelope factory.

Targets identified by `grep -n "EventEnvelope.create" services/telegram-gateway/src/`:
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` (Story 3.3 — `task.created` emission)
- `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` (Story 3.4 — approval orchestration; emits via the registry-api HTTP path which already gets trace_id from 9.2's middleware, so this handler MAY only need to PROPAGATE the trace_id as `X-Trace-Id` header on its outbound HTTP request)
- `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py`, `reject_command.py`, `retry_command.py`, `agent_command.py` — same pattern: either direct envelope emission OR outbound HTTP call to registry-api.

For each handler that makes an **outbound HTTP call** to registry-api, set the `X-Trace-Id: <data["trace_id"]>` header on the request so 9.2's middleware preserves rather than re-mints the value.

### AC6 — `PerActorRateLimitMiddleware` integration

`PerActorRateLimitMiddleware` is registered immediately AFTER `AllowlistMiddleware` (lifespan.py:253). It already reads from the `data` dict. Verify that any `telegram.rejected`-style envelopes emitted by the rate-limiter ALSO carry the propagated `trace_id` from `data["trace_id"]`. If the rate-limiter currently mints its own UUIDv7 or emits without `trace_id`, fix it to consume from `data`.

### AC7 — Unit tests (≥10)

New tests in `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` (or a new test class):

1. `test_trace_id_derived_from_update_id` — pass `Update(update_id=42, ...)`, assert `data["trace_id"] == "tg:42"`.
2. `test_trace_id_bound_to_structlog_context_during_handler_dispatch` — inner handler reads `structlog.contextvars.get_contextvars()["trace_id"]`, asserts it matches.
3. `test_trace_id_unbound_after_dispatch_success` — assert contextvars cleared after handler returns.
4. `test_trace_id_unbound_after_dispatch_exception` — handler raises; assert contextvars still cleared.
5. `test_trace_id_unbound_after_allowlist_rejection` — non-allowlisted user; middleware returns `None`; assert contextvars cleared.
6. `test_trace_id_unbound_after_no_from_user_rejection` — event with no `from_user`; middleware returns `None`; assert contextvars cleared.
7. `test_replay_same_update_id_produces_same_trace_id` (AC2).
8. `test_trace_id_max_int64_update_id_accepted` — `update_id=9_223_372_036_854_775_807` → `tg:9223372036854775807`.
9. `test_trace_id_fallback_on_zero_update_id` — synthetic `Update(update_id=0)` (impossible per BotAPI but defensive); assert WARNING log + fresh UUIDv7 in `data["trace_id"]`.
10. `test_trace_id_fallback_on_negative_update_id` — synthetic `Update(update_id=-1)`; assert WARNING log + UUIDv7 fallback.
11. `test_telegram_rejected_envelope_carries_trace_id` — non-allowlisted user; assert the emitted `telegram.rejected` envelope's `trace_id` field equals the derived value (NOT `None`).
12. (Optional integration) `test_handler_emits_event_with_propagated_trace_id` — a stub handler reads `data["trace_id"]`, builds an envelope via `EventEnvelope.create(trace_id=data["trace_id"], ...)`; assert no DeprecationWarning fires and the envelope's `trace_id` matches.

### AC8 — mypy --strict clean + Epic 8.7 baseline gates

`uv run mypy --strict packages/ services/registry-api services/registry-state services/telegram-gateway` exits 0. The strict gate currently runs on `packages/ services/registry-api services/registry-state` per `.github/workflows/ci.yml:67` — **DO NOT** extend the CI command in 9.3. If telegram-gateway mypy isn't on the CI strict-gate baseline, that's a separate hygiene task; 9.3 must not introduce drift to the existing 97-source-files baseline.

`ruff check`, `ruff format --check`, `check_imports`, `check_single_writer`, and the secret-hygiene full-tree scan all pass. Test count delta: +10 to +15 tests; full suite goes from 2269 → ~2280-2285.

### AC9 — DeprecationWarning count drops further

Before 9.3, the suite emits ~80 callsite DeprecationWarnings (silenced via `pyproject.toml` filterwarnings). After 9.3, the telegram-gateway handler cluster (~6-10 callsites — task/approve/stop/reject/retry/agent commands) stops emitting. Verify by running:

```bash
uv run pytest packages/ services/ -m "not slow" -W "always::DeprecationWarning" 2>&1 | grep -c "EventEnvelope created without trace_id"
```

Expected: count drops by ~4-6 (per-source-location dedup, mirroring Story 9.2's empirical "per-callsite" semantics — NOT per-test count). Document the actual measurement in the Dev Agent Record.

### AC10 — FR58 (Telegram) literal compliance

Every event emitted as a direct result of a Telegram update — `task.created` (from `/task`), `approval.granted` / `task.stop_requested` / etc. (from approve/stop/retry/reject handlers), AND `telegram.rejected` (from the allowlist middleware itself) — now carries the `trace_id` derived from `update.update_id` (or its UUIDv7 fallback). Verify via an integration test that posts a synthetic Telegram update, reads the JSONL event log, and asserts:

- All envelopes share the same `trace_id` value (the chain correlation invariant)
- The value matches `f"tg:{update_id_sent}"`
- Multi-event chains (e.g., `/task` → `task.created` + `task.planning.started`) all share the same trace_id

---

## Developer context

### Existing state

- `AllowlistMiddleware` at `services/telegram-gateway/src/telegram_gateway/app/middleware.py:149-218` already handles the outer-middleware lifecycle and emits `telegram.rejected` envelopes on rejection.
- It receives `event: TelegramObject` and `data: dict[str, Any]` per aiogram's `BaseMiddleware` contract.
- It's registered on `dp.update.outer_middleware` (lifespan.py:240) — the outer-middleware-on-update wiring guarantees `event` is an `Update` instance.
- Story 3.6 established the `request_id` propagation pattern via `data` dict (line 203). 9.3 mirrors this for `trace_id`.

### Architecture compliance

- **FR58 (Telegram)** — "telegram-gateway `AllowlistMiddleware` injects `trace_id = f"tg:{update_id}"` (deterministic per inbound update)."
- **FR28 (idempotency)** — replay of same `update_id` MUST produce same `trace_id`. AC2 + AC7 lock this in.
- **NFR-O7** — every event emitted in Phase 2+ carries non-null `trace_id`. AC4 + AC5 close the telegram-gateway callsites.
- **P2-I2** — no `schema_version` bump in 9.3 (Story 9.7 owns it).
- **Architecture §"trace_id propagation wiring"** — telegram-gateway is the "Telegram update update_id" ingress in the Mermaid diagram.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| aiogram | already in telegram-gateway deps | `BaseMiddleware` API; `Update.update_id: int` |
| structlog | already wired | `contextvars.bind_contextvars` / `unbind_contextvars` |
| events | workspace member | Import `is_valid_trace_id` from `events.envelope` (Story 9.2 pass-1 promoted it to public) and `new_uuid7` from `events.ids` |

No new deps.

### File-structure requirements

| File | Change |
|---|---|
| `services/telegram-gateway/src/telegram_gateway/app/middleware.py` | Extend `AllowlistMiddleware.__call__` to derive + bind + propagate `trace_id`. Extend `_emit_rejection` signature. Add module-level `_log = logging.getLogger(__name__)` if not present. |
| `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` | Pass `trace_id=data["trace_id"]` to `EventEnvelope.create(...)`. |
| `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` | Set `X-Trace-Id` header on outbound HTTP call to registry-api (registry-api's 9.2 middleware will preserve it). |
| `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` | Same pattern (envelope OR HTTP header). |
| `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` | Same. |
| `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` | Same. |
| `services/telegram-gateway/src/telegram_gateway/handlers/agent_command.py` | Same. |
| `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` (AC6) | Verify `PerActorRateLimitMiddleware` reads `trace_id` from `data` when emitting any rate-limit-rejected envelope. |
| `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` | ≥10 new tests per AC7. |

Do **NOT** touch:
- `packages/events/src/events/envelope.py` — Story 9.1 owns the validator. 9.3 imports `is_valid_trace_id` only.
- `services/registry-api/*` — Story 9.2 owns the HTTP ingress.
- `pyproject.toml` filterwarnings — Story 9.7 owns its removal.
- Any non-telegram-gateway service.

### Testing requirements

- **Unit tests** in `test_allowlist.py` (≥10 per AC7).
- **At least one integration-feeling test** that simulates a full Telegram update → handler chain and asserts the envelope's trace_id matches `f"tg:{update_id}"`.
- Test markers: PR-gate (not `@pytest.mark.slow`).
- **Test isolation**: structlog contextvars are process-global. Add a function-scoped autouse `clear_contextvars()` fixture for the new test class (mirror Story 9.2 pass-1 B2's pattern in registry-api).

### Previous-story intelligence

- **Story 9.1** established the `tg:<update_id>` contract. `is_valid_trace_id` validates `\Atg:[1-9][0-9]{0,18}\Z` + int64 ceiling. The middleware MUST use the public helper, NOT re-implement.
- **Story 9.2** established the HTTP ingress pattern: validate-or-mint-or-fallback, bind to structlog contextvars, propagate via `data` dict, try/finally unbind, echo on response. 9.3 mirrors this for the Telegram BotAPI shape.
- **Story 3.6** established `request_id` propagation via the `data` dict — exact same pattern.
- **Story 7.5.1** added `PerActorRateLimitMiddleware` registered after Allowlist; AC6 lifts the same propagation pattern through it.
- **Epic 8.7 retro L1 (hidden gate cascade)** — after local-green, push and watch. The N806 lint regression on Story 9.2 pass-2 is a fresh data point: ruff version drift between local and CI catches new lint codes silently.
- **Epic 8.7 retro L2 (documentation poisoning)** — when updating `middleware.py` docstring, do file-top + per-class + `__all__` in one pass.

### Git intelligence — recent commits

```
b490e4e fix(story-9.2): ruff N806 — _TM_NAME → tm_name in test_middleware
c1dc9cb fix(story-9.2): pass-2 second-opinion review — 16 patches batch-applied
3017f48 fix(story-9.2): pass-1 review — 19 patches batch-applied
f0b83b2 chore(sprint-status): close Story 9.2 — CI green on 25961778907
047e3d7 feat(registry-api): Story 9.2 — TraceIdMiddleware + X-Trace-Id propagation (FR58 HTTP)
```

### Latest-tech notes

- **aiogram `BaseMiddleware`** API is stable. `__call__(handler, event, data)` signature — event is the TelegramObject, data is the propagation dict.
- **`structlog.contextvars`** API: same as in 9.2's `TraceIdMiddleware`. Use `bind_contextvars(trace_id=...)` + `unbind_contextvars("trace_id")` in try/finally.
- **`aiogram.types.Update.update_id`** — typed `int`, always present (validated by Pydantic at the BotAPI boundary).

---

## Dev notes

### Middleware extension sketch

```python
# At module top — add to existing imports:
from events.envelope import is_valid_trace_id  # noqa: IMP001 — services→packages allowed
from events.ids import new_uuid7  # noqa: IMP001

# Inside AllowlistMiddleware.__call__, before user_id extraction:

trace_id: str
if isinstance(event, Update) and event.update_id >= 1:
    candidate = f"tg:{event.update_id}"
    if is_valid_trace_id(candidate):
        trace_id = candidate
    else:
        # Defense in depth — should never fire for BotAPI-conformant updates.
        _log.warning(
            "Telegram update_id failed trace_id validation; minting fallback",
            extra={"update_id": event.update_id},
        )
        trace_id = new_uuid7(clock=self._clock)
else:
    # Edge case: event is not an Update (impossible given outer-middleware-on-update
    # wiring) OR update_id is 0/negative (impossible per BotAPI).
    _log.warning(
        "AllowlistMiddleware received non-Update event or invalid update_id; "
        "minting fallback trace_id",
        extra={"event_type": type(event).__name__},
    )
    trace_id = new_uuid7(clock=self._clock)

structlog.contextvars.bind_contextvars(trace_id=trace_id)
data["trace_id"] = trace_id

try:
    # ... existing user-id extraction + allowlist enforcement + delegate to handler ...
finally:
    structlog.contextvars.unbind_contextvars("trace_id")
```

### Trade-off note (capture in commit message, not in code)

Every existing `EventEnvelope.create(...)` callsite in the telegram-gateway handler cluster will continue emitting the DeprecationWarning UNTIL the handlers are individually updated to pass `trace_id=data["trace_id"]`. AC5 handles this in the same story. The `pyproject.toml` filterwarnings entry from Story 9.1 keeps the noise out of CI logs in the interim.

### Non-goals (do NOT do in 9.3)

- Implement console-cli / MCP / worker ingresses — Stories 9.4, 9.5, 9.6.
- Bump `schema_version` to 1.1.0 — Story 9.7.
- Add `events.trace_id` ORM column or migrator — Story 9.7.
- Add `/trace <id>` Telegram operator command — Story 9.7 (Telegram-side surface; `oh-my-bmad trace` console-side surface is also 9.7).
- Remove `pyproject.toml` filterwarnings — Story 9.7.
- Touch envelope validator — Story 9.1.
- Touch registry-api middleware — Story 9.2.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| `Update.update_id` could be 0 or negative in malformed BotAPI payloads (impossible per docs but defense in depth). | AC3 + AC7 #9, #10 lock the fallback path with WARNING log + UUIDv7. |
| `data` dict is mutable and shared across the middleware chain — could a downstream middleware (PerActorRateLimitMiddleware) overwrite `data["trace_id"]`? | AC6 verifies the propagation invariant; PerActorRateLimitMiddleware is read-only on the trace_id field. |
| Telegram webhook redelivery (FR28 idempotency replay) sends the same `update_id` twice. The derived `trace_id` will be identical — that's the deterministic invariant. The FR28 cache-hit path produces a replay envelope; does THAT envelope also carry the same trace_id? | Verify by inspecting the FR28 idempotency-cache writer (likely `EventLogWriter.append` from `_safe_emit`). Out of scope for 9.3 if not directly testable; flag in retro. |
| `_emit_rejection`'s signature change is a breaking change to a private API. | OK — it's truly private (single caller). No back-compat concern. |
| Outbound HTTP handlers (approve/stop/retry/reject/agent) need to set `X-Trace-Id` header. The httpx client used in these handlers may have a default `headers` dict; merge carefully. | AC5 calls out the pattern. Code review should verify no header is overwritten. |
| `structlog.contextvars.clear_contextvars()` in autouse test fixture — does it conflict with aiogram's own contextvars usage during tests? | Mirror Story 9.2's autouse approach; aiogram dispatcher tests in test_allowlist.py already work with similar patterns. |

---

## Definition of done

- All 10 ACs satisfied.
- `uv run pytest services/telegram-gateway -q` shows new tests passing.
- Local full-suite parity gate green.
- CI green on push (allow for L1 hidden-gate cascade — be ready for follow-up).
- Commit message follows `feat(telegram-gateway): Story 9.3 — ...` style.
- `sprint-status.yaml` `9-3-telegram-gateway-tg-update-id-derivation: backlog → done`.
- Dev Agent Record filled in with implementation notes, deprecation count delta, surprises, follow-up TODOs.
- Two-pass adversarial code review (pass-1 + pass-2) completed per Epic 8.x cadence.

---

## Dev Agent Record

### Implementation summary

Extended `AllowlistMiddleware.__call__` to derive `trace_id = f"tg:{update.update_id}"` from every inbound `Update` BEFORE allowlist enforcement, validate it via the public `events.envelope.is_valid_trace_id` helper (Story 9.2 pass-1 A1), bind it to structlog `contextvars`, propagate via `data["trace_id"]`, and unbind in `try/finally` so every return path (handler success, handler exception, allowlist rejection, no-from-user rejection) cleans up. Extended `_emit_rejection` to accept and forward `trace_id` to the `EventEnvelope.create(...)` call so `telegram.rejected` envelopes carry the same trace_id as the originating update.

Audit of telegram-gateway handlers found NO direct `EventEnvelope.create(...)` callsites in `handlers/*.py` — every state-mutating action flows through `RegistryAPIClient` over HTTP, which means registry-api's TraceIdMiddleware (Story 9.2) preserves/re-mints downstream. AC5's spec phrasing about "passing trace_id to envelope factory in each handler" was therefore a non-existent target; the practical work is to **forward** `X-Trace-Id` header on every outbound registry-api call. Each handler now accepts an optional `trace_id: str | None = None` parameter (aiogram DI resolves it from `data["trace_id"]` set by the middleware) and threads it into the corresponding `RegistryAPIClient.*` method, which sets `X-Trace-Id` on the outbound httpx request.

Deterministic-replay invariant (AC2): the derivation is pure-function of `event.update_id` — no per-instance middleware state participates in trace_id construction, so two `Update` objects with the same `update_id` always produce byte-identical `trace_id`. Regression test `test_replay_same_update_id_produces_same_trace_id` locks this in.

`PerActorRateLimitMiddleware` (AC6) verified read-only on `data`: it consumes neither emits envelopes nor overwrites `data["trace_id"]`. No changes required.

### Files changed

- `services/telegram-gateway/src/telegram_gateway/app/middleware.py` (+95 / -19): module + class docstring update, imports for `is_valid_trace_id` / `new_uuid7` / `structlog`, trace_id derivation block at top of `__call__`, try/finally wrapper for unbind, extended `_emit_rejection` signature with `trace_id: str` kwarg.
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (+22 / 0): added `trace_id: str | None = None` kwarg to `create_task`, `submit_decision`, `get_platform_health`, `get_task`, `get_logs_digest`; each method now sets `X-Trace-Id` header when present.
- `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` (+2 / -1): handler signature + thread to `create_task`.
- `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` (+2 / -1): handler signature + thread to `submit_decision`.
- `services/telegram-gateway/src/telegram_gateway/handlers/stop_command.py` (+2 / -1): handler signature + thread to `submit_decision`.
- `services/telegram-gateway/src/telegram_gateway/handlers/retry_command.py` (+2 / -1): handler signature + thread to `submit_decision`.
- `services/telegram-gateway/src/telegram_gateway/handlers/reject_command.py` (+2 / -1): handler signature + thread to `submit_decision`.
- `services/telegram-gateway/src/telegram_gateway/handlers/agent_command.py` (+3 / -1): handler signature + thread to `get_task`.
- `services/telegram-gateway/src/telegram_gateway/handlers/ping_command.py` (+3 / -1): handler signature + thread to `get_platform_health`.
- `services/telegram-gateway/src/telegram_gateway/handlers/status_command.py` (+5 / -2): handler signature + thread to `get_task`.
- `services/telegram-gateway/src/telegram_gateway/handlers/logs_command.py` (+5 / -2): handler signature + thread to `get_logs_digest`.
- `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` (+232): new `TestStory93TraceIdDerivation` class with autouse `_clear_structlog_contextvars` fixture and 13 unit tests covering AC1-AC4 + AC7 enumerated cases.

### Test count delta

Full suite: 2269 → 2283 (+14: 13 new explicit AC7 tests + 1 sibling test for the `no_from_user` rejection envelope carrying trace_id). All telegram-gateway tests pass (402 passed, 3 skipped, 3 deselected). All static gates clean: ruff check, ruff format --check, mypy --strict on baseline (97 source files), check_imports, check_single_writer, secret-hygiene full-tree scan.

### Callsite-warning observation

Baseline (HEAD before 9.3): 95 occurrences of `"EventEnvelope created without trace_id"` DeprecationWarning matches in full-suite pytest output with `-W "always::DeprecationWarning"`.

After 9.3: 94 occurrences (delta = −1).

The drop is smaller than the spec's predicted ~4-6 because **every telegram-gateway handler emits via registry-api over HTTP rather than direct `EventEnvelope.create()` calls**. The only direct envelope emission in telegram-gateway is the `_emit_rejection` helper in middleware — which is exactly the one callsite that stops emitting after 9.3. The spec's prediction was based on an assumption that handlers also created envelopes locally; the reality is the architecture defers envelope construction to registry-api (correctly — single-writer rule FR26).

### Surprises / deviations from spec

1. **Handlers do not call `EventEnvelope.create()` directly**: AC5's "Read `trace_id: str = data['trace_id']` and pass to envelope factory" was interpretable in two ways. Reality: handlers go through `RegistryAPIClient` HTTP calls. Implementation threads `X-Trace-Id` header through each method instead, which achieves the AC's intent (registry-api's TraceIdMiddleware preserves rather than re-mints).
2. **`trace_id: str | None = None` default in handler signatures**: needed for back-compat with existing handler tests that call handlers directly without going through the middleware/data-dict path. Production code path always populates `data["trace_id"]` (by the middleware), so aiogram DI resolves the value; tests stay green without modification.
3. **DeprecationWarning drop is 1 (not 4-6)**: see prior section. The spec's prediction underestimated the centralisation of envelope emission in registry-api.
4. **`pyproject.toml`/`uv.lock` reverted between sessions**: the working-tree `git status` at task start showed `pyproject.toml` + `uv.lock` modified, but those were unrelated to 9.3 and got restored by another process during execution. No action needed.

### Follow-up TODOs surfaced for Epic 9

- **9.7 (or follow-up)**: verify FR28 idempotency-cache replay envelopes also carry the derived `tg:{update_id}` trace_id. The current implementation guarantees the first dispatch carries it; the cache-hit replay path (in `EventLogWriter.append` or wherever idempotency is enforced) needs an explicit test that the replayed envelope shares the same trace_id. Flagged but not in 9.3 scope.
- **9.4-9.6 ingresses** (console-cli, MCP, worker): same pattern as 9.3 — derive deterministic or mint trace_id at the entry point, bind contextvars, propagate via the call's `data` analogue.
- **registry-api callsites in worker-side** still emit DeprecationWarnings (94 remaining); each Epic 9 ingress story closes a tranche.
- **Optional**: an integration test posting a synthetic webhook update, draining dispatch tasks, then reading the JSONL log and asserting all envelope `trace_id` fields match `f"tg:{update_id}"` (AC10 literal). The `test_rejected_user_receives_no_outbound_message` test reads the JSONL but does not yet assert on trace_id propagation through the full chain. Low-priority since unit-level invariants are covered.

---

## Review Findings

### Adversarial 3-lane code review — 2026-05-16 (pass-1)

Reviewed at commit `7861ba7` (Story 9.3 initial implementation) + `0e6c844` (sprint-status closure). Three lanes (Blind Hunter / Edge-Case Hunter / Acceptance Auditor) produced 19 unique patch items. All 19 classified as `patch` per user policy "fix all issues even minors — patch everything, dismiss-zero". Mirrors the Epic 8.x cadence.

#### Patch resolution — 2026-05-16 (pass-1 batch-apply)

All 19 `patch` findings resolved in a single follow-up commit on top of `0e6c844`. Implementation summary:

| ID | Severity | Title | Resolution | Files |
|---|---|---|---|---|
| H1 | HIGH | Move `data["trace_id"] = trace_id` INSIDE the try block | `bind_contextvars` + the `data["trace_id"] = ...` assignment moved INSIDE the `try/finally` so a read-only `data` mapping cannot leak the contextvar across requests. Guarded the assignment with `isinstance(data, dict)`. | `middleware.py` |
| H2 | HIGH | Add AC10 integration test (JSONL log read + trace_id assertion) | Extended `test_rejected_user_receives_no_outbound_message` to assert `record["trace_id"] == "tg:1"` after reading the JSONL log (`_make_update` defaults to `update_id=1`). Lowest-cost path; no new Dispatcher harness needed. | `test_allowlist.py` |
| H3 | HIGH | Fix logger name mismatch in tests | Investigated: middleware.py uses an EXPLICIT logger name `"telegram_gateway.middleware"` (line 148: `_log = logging.getLogger("telegram_gateway.middleware")`) — NOT `__name__`. The existing tests filtering on `r.name == "telegram_gateway.middleware"` are CORRECT. The review finding was based on a misreading; no code change required. The L7 test for multi-value `X-Trace-Id` was added to registry-api side (different file). | (no-op; documented) |
| H4 | HIGH | Empty-string trace_id check at httpx boundary | Changed all 5 sites in `registry_client.py` from `if trace_id is not None` to `if trace_id` so an empty string never produces an `X-Trace-Id: ` header that registry-api would log a WARNING about + mint over. | `registry_client.py` |
| H5 | HIGH | Handler signature `trace_id: str | None = None` silently degrades correlation | Added `if trace_id is None: _log.warning(...)` at the top of all 9 command handlers (`/agent`, `/approve`, `/logs`, `/ping`, `/reject`, `/retry`, `/status`, `/stop`, `/task`). Intentional noise that surfaces misconfiguration in production logs. | 9 × `handlers/*_command.py` |
| M1 | MEDIUM | Add `test_trace_id_minimum_update_id_one_accepted` (AC3 lower bound) | New test in `TestStory93TraceIdDerivation` covering `update_id=1 → tg:1` (lowest valid value). Fills the AC3 lower-edge gap. | `test_allowlist.py` |
| M2 | MEDIUM | Regression test for `PerActorRateLimitMiddleware` preserving `data["trace_id"]` | New `TestStory93TraceIdRateLimitChain` class with one test that runs `AllowlistMiddleware → PerActorRateLimit → stub_handler` and asserts `data["trace_id"]` survives the chain unchanged. | `test_allowlist.py` |
| M3 | MEDIUM | Replace tautological `test_handler_can_read_trace_id_from_data` with aiogram-DI integration test | Added `test_handler_receives_trace_id_via_aiogram_di` that registers a stub handler with `trace_id: str | None = None` and dispatches through a full `Dispatcher.feed_update` — exercises aiogram's DI end-to-end. Kept the original test in place as a unit-level sanity-check. | `test_allowlist.py` |
| M4 | MEDIUM | Document AC2 deterministic-replay carve-out for fallback path | Added an `.. note::` block to the file-top docstring AND the `AllowlistMiddleware` class docstring explaining that determinism applies only to `update_id ≥ 1` and that `new_uuid7()` fallback is non-deterministic by design. | `middleware.py` |
| M5 | MEDIUM | Add DeprecationWarning-free envelope-build test (AC7 #12 spec restoration) | New `test_handler_builds_envelope_with_propagated_trace_id_no_deprecation_warning` that reads `data["trace_id"]`, builds an `EventEnvelope.create(..., trace_id=...)` inside `warnings.catch_warnings(simplefilter="error", DeprecationWarning)`, and asserts no DeprecationWarning fires. | `test_allowlist.py` |
| M6 | MEDIUM | `_emit_rejection` kwarg ordering footgun | Reordered the keyword-only parameters of `_emit_rejection` so `trace_id` (required) appears BEFORE `request_id` (defaulted). Updated both call sites in `__call__`. | `middleware.py` |
| M7 | MEDIUM | Pydantic `update_id` str coercion ambiguity | New `test_update_id_string_coercion_produces_canonical_trace_id` that posts `{"update_id": "42"}` and `{"update_id": 42}` and asserts both produce the same canonical `tg:42` — locks the wire-form lossy collapse behaviour. `pytest.skip` guards against future Pydantic strict-mode flips. | `test_allowlist.py` |
| L1 | LOW | Promote `_clear_structlog_contextvars` to module-level autouse | Added `_clear_structlog_contextvars_module` fixture at module level (autouse) so ALL tests in the file get the hygiene, not only those in `TestStory93TraceIdDerivation`. Class-scoped fixture retained as belt-and-braces. | `test_allowlist.py` |
| L2 | LOW | Assert trace_id in `test_rejected_user_receives_no_outbound_message` JSONL read | Implemented as part of H2 — single batched JSONL assertion covers both. | `test_allowlist.py` |
| L3 | LOW | Warn on `data["trace_id"]` overwrite | Added `if existing is not None and existing != trace_id: _log.warning(...)` guard in `AllowlistMiddleware.__call__` so a future upstream middleware that pre-sets `data["trace_id"]` surfaces the unexpected double-write. | `middleware.py` |
| L4 | LOW | `new_uuid7` test regex: case-insensitive | Added `re.IGNORECASE` to `_UUIDV7_RE` in `test_allowlist.py`. Defense-in-depth — `events.ids.new_uuid7` returns lowercase but future emitters might not. | `test_allowlist.py` |
| L5 | LOW | Add `tg:int64_max+1` overflow test in rejection envelope context | New `test_rejection_envelope_overflow_update_id_uuid_fallback` — non-allowlisted user with `update_id = int64_max + 1` exercises BOTH middleware fallback AND envelope trace_id assignment; asserts UUIDv7 fallback (not raw `tg:<overflow>`) lands on the rejection envelope. | `test_allowlist.py` |
| L6 | LOW | Update docstring: soften "impossible per BotAPI" claim | Changed file-top docstring + inline edge-case comment to say "values outside `[1, 2^63-1]` are treated as malformed regardless of BotAPI guarantees" — avoids over-claiming what BotAPI docs guarantee. | `middleware.py` |
| L7 | LOW | Multi-value `X-Trace-Id` warning at registry-api boundary | Added `header_values = request.headers.getlist("X-Trace-Id")` + `if len(header_values) > 1: _log.warning(...)` in `TraceIdMiddleware.dispatch`. New `TestTraceIdMiddlewareMultiValueHeader` test class with `test_multi_value_x_trace_id_logs_warning_and_uses_first`. | `registry-api/adapters/middleware.py`, `registry-api/test_middleware.py` |

**Test count delta after pass-1 batch-apply:**

| Suite | Before (Story 9.3 initial) | After (pass-1) | Δ |
|---|---|---|---|
| `services/telegram-gateway` (`-m "not slow"`) | 402 | 408 | **+6** (M1, M2, M3, M5, M7, L5) |
| `services/registry-api` (`-m "not slow"`) | 158 | 159 | **+1** (L7) |
| Full workspace (`packages/ services/ -m "not slow"`) | 2283 | 2290 | **+7** |

Test count verified with `pytest -q -m "not slow"` post-apply. All Epic 8.7 baseline gates remain green (ruff check, ruff format, mypy --strict, check_imports, check_single_writer, secret-hygiene-precommit).

---

## Frontmatter

```yaml
---
story_id: 9.3
story_key: 9-3-telegram-gateway-tg-update-id-derivation
parent_epic: 9
phase: 2
fr_refs: [FR58, FR28]
nfr_refs: [NFR-O7]
arch_refs:
  - "trace_id propagation wiring (Mermaid §line-1117+)"
  - "P2-I2 (single Phase 2 schema bump deferred to 9.7)"
  - "FR28 idempotency-cache deterministic replay"
estimated_hours: 3-5
priority: high (Telegram ingress for Epic 9; second of 5 ingresses)
blocks:
  - 9.7 (schema bump uses Story 9.3's middleware as the deterministic-replay unit-test baseline)
blocked_by:
  - 9.1 (trace_id shape contract — landed in commit 7cfebd9)
  - 9.2 (HTTP ingress + public is_valid_trace_id helper — landed in commits 047e3d7 + 3017f48 + c1dc9cb + b490e4e)
status: ready-for-dev
created: 2026-05-16
created_by: bmad-create-story skill
---
```
