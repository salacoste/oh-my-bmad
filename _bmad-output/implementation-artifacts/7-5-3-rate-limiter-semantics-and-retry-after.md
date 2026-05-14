# Story 7.5.3: Rate-limiter contract documentation + dynamic Retry-After

Status: done

## Story

As a maintainer,
I want the rate-limiter's charge-on-attempt contract documented and the `Retry-After` header computed dynamically from bucket state,
So that a future maintainer cannot accidentally weaken DoS protection by "fixing" the token-consumption ordering, and the `Retry-After` hint remains accurate when `refill_per_second` becomes operator-tunable in Phase 2.

Two deferred items from the Story 3.6 code review converge on this story:
- **D2** (Edge Case Hunter) — `self._tokens -= 1.0` runs BEFORE `await call_next(request)`. If `call_next` raises, the token is permanently consumed. This is a deliberate DoS-protection trade-off, but neither the docstring nor a test pins the choice. A future maintainer could move the decrement after `call_next` and silently disable rate-limiting for failing handlers.
- **D10** (Edge Case Hunter) — `Retry-After: 1` is hardcoded. At the current `refill_per_second=10/s` the 1-second hint is approximately correct, but Phase 2 makes the value tunable. A slow refill (e.g. 0.0001/s) would render the hint wildly inaccurate. The fix is `math.ceil((1.0 - self._tokens) / self._refill_per_second)`.

## Acceptance Criteria

1. **AC-1: Charge-on-attempt contract documented** — The `WebhookRateLimitMiddleware` class docstring includes a section explaining that token consumption happens BEFORE `call_next`, that tokens are NOT refunded on handler errors, and why this is the correct DoS-protection trade-off.
2. **AC-2: Charge-on-attempt regression test** — A test verifies that a token is consumed from the bucket even when `call_next` raises an exception. This pins the behavior against accidental weakening.
3. **AC-3: Dynamic Retry-After** — The `Retry-After` header in `WebhookRateLimitMiddleware.dispatch()` is computed from bucket state (`ceil((1.0 - tokens) / refill_per_second)`) instead of being hardcoded to `"1"`.
4. **AC-4: Existing tests pass** — All existing rate-limit tests (`test_rate_limit.py`, `test_per_actor_rate_limit.py`) and the full telegram-gateway suite continue to pass.

## Tasks / Subtasks

- [x] **Task 1: Document charge-on-attempt contract** (AC: #1)
  - [ ] In `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py`, add a "Token consumption contract" section to the `WebhookRateLimitMiddleware` class docstring (lines 69-83) explaining:
    - Token decrement occurs BEFORE `call_next` (charge-on-attempt, not charge-on-success).
    - Tokens are NOT refunded if `call_next` raises (handler exception, `asyncio.CancelledError` on client disconnect).
    - This is the correct DoS-protection trade-off: a failing handler must still consume a token so an attacker cannot bypass the limiter by triggering handler errors.
  - [ ] Add a brief inline comment at line 162 (`self._tokens -= 1.0`) referencing the docstring contract: `# Charge-on-attempt: consume BEFORE call_next (see class docstring).`

- [x] **Task 2: Add charge-on-attempt regression test** (AC: #2)
  - [ ] In `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py`, add a new test:
    - `test_token_consumed_even_when_handler_raises` — create a middleware with a `call_next` that raises `RuntimeError`. Send a request to the webhook path, catch the exception. Assert the bucket has one fewer token than before.

- [x] **Task 3: Implement dynamic Retry-After** (AC: #3)
  - [ ] In `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py`, add `import math` at the top of the file (after existing imports).
  - [ ] In `WebhookRateLimitMiddleware.dispatch()`, replace the hardcoded `"Retry-After": "1"` (line 157) with a computed value:
    ```python
    retry_after_s = math.ceil((1.0 - self._tokens) / self._refill_per_second)
    ```
    Use `str(retry_after_s)` for the header value.
  - [ ] The computation runs inside the lock, after `self._tokens` has been refilled but is confirmed `< 1.0` — this is the correct point to compute the deficit.

- [x] **Task 4: Update existing tests for dynamic Retry-After** (AC: #4)
  - [ ] In `test_rate_limit.py`, verify the existing test that asserts `Retry-After: 1` still passes — with capacity=20, refill=10/s, the bucket is at 0 tokens when the 21st arrives. `ceil((1.0 - 0.0) / 10.0) = 1`. So the value is still `"1"` for the default parameters.
  - [ ] Add a NEW test `test_retry_after_computed_from_bucket_deficit` that verifies the dynamic computation:
    - Set `refill_per_second=0.5` (slow), exhaust the bucket, assert `Retry-After` is `"2"` (not `"1"`).

- [x] **Task 5: Run full regression suite** (AC: #4)
  - [ ] `uv run pytest services/telegram-gateway/ -x -q` passes.
  - [ ] `uv run ruff check` clean on all modified files.

## Dev Notes

### Origin and Context

Two deferred items from Story 3.6 code review:

- **D2** — Token-bucket charge-on-attempt semantics undocumented. `self._tokens -= 1.0` runs BEFORE `await call_next(request)`. If `call_next` raises, the token is permanently consumed. This is an acceptable DoS-protection trade-off but no test or doc pins the choice. Documenting + testing prevents a future maintainer from "fixing" this.
- **D10** — `Retry-After: 1` lies under Phase 2 slow refill. Today `refill_per_second` is locked at 10/s so the 1-second hint is approximately correct. Phase 2 makes the value operator-tunable. Computing `ceil((1.0 - tokens) / refill_per_second)` makes the header accurate for any refill rate.

### Key Files (exact paths + line numbers)

| File | Lines | What changes |
|------|-------|-------------|
| `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` | 69-83 (docstring), 131-158 (429 response), 162 (token decrement) | Document contract, compute dynamic Retry-After |
| `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` | TBD | Add charge-on-attempt test + dynamic Retry-After test |

### Architecture Compliance

- **Token-bucket invariants**: The token decrement and Retry-After computation both happen inside `self._lock`, so no concurrency issues.
- **Deny-path refill guard (M6)**: `self._last_refill_ns` is NOT advanced on the deny path. The Retry-After computation reads `self._tokens` (already refilled) but does NOT modify `_last_refill_ns` — this is correct.
- **RFC 7807**: The 429 response body remains `application/problem+json`. Only the `Retry-After` header value changes.
- **Layer 2 (PerActorRateLimitMiddleware)**: Returns `None` on deny (no HTTP response), so no Retry-After header applies. No changes needed to Layer 2.

### Code Pattern to Follow

The existing test suite uses `TickingClock` and `FrozenClock` from `events.clock` for deterministic bucket behavior. Follow this pattern for the new tests.

The charge-on-attempt test should:
1. Create a middleware with a known token count (capacity=5, refill=10, `TickingClock`)
2. Set `call_next` to a function that raises `RuntimeError`
3. Call `dispatch()` and expect the exception to propagate
4. Assert `middleware._tokens == 4.0` (one token consumed despite the error)

The dynamic Retry-After test should:
1. Create a middleware with `capacity=2, refill_per_second=0.5`
2. Send 2 requests to exhaust the bucket (tokens → 1.0 → 0.0)
3. Send the 3rd request — expect 429 with `Retry-After: 2` (`ceil((1.0 - 0.0) / 0.5) = 2`)

### Previous Story Intelligence (7.5.1, 7.5.2)

- **Testing pattern**: Test rate-limit middleware directly via constructor + `dispatch()` (Layer 1) or `__call__()` (Layer 2). Use `TickingClock`/`FrozenClock` for deterministic timing. Do NOT test through the full HTTP stack for unit tests.
- **Regression**: Run `services/telegram-gateway/` test suite after changes (373 tests in 7.5.1).
- **Commit style**: Use conventional commits with scope, e.g. `docs(telegram-gateway): document charge-on-attempt contract + dynamic Retry-After (Story 7.5.3)`.

### References

- [Source: deferred-work.md — D2, D10 (story 3.6 code review)]
- [Source: epic-7-retro-2026-05-13.md — item 4 (MEDIUM)]
- [Source: services/telegram-gateway/src/telegram_gateway/app/rate_limit.py — lines 69-165]
- [Source: services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py — existing Layer 1 tests]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/_errors.py — `retry_after_seconds` rendering]

## Dev Agent Record

### Implementation Plan

### Debug Log References

### Completion Notes

All 4 ACs met:
- AC-1: "Token consumption contract" section added to `WebhookRateLimitMiddleware` class docstring (lines 86-96).
- AC-2: `TestChargeOnAttempt.test_token_consumed_even_when_handler_raises` verifies token consumed on handler error.
- AC-3: `Retry-After` header now computed via `math.ceil((1.0 - self._tokens) / self._refill_per_second)`.
- AC-4: 379 passed, 4 skipped. Ruff clean on all modified files.

### File List

- `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` — docstring contract, dynamic Retry-After, import math
- `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` — TestChargeOnAttempt + TestDynamicRetryAfter classes

## Change Log

- 2026-05-13: Story created from deferred-work.md D2 + D10. Status: ready-for-dev.
