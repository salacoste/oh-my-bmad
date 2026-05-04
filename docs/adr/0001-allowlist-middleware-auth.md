# ADR-0001: Allowlist middleware is the single auth gate

## Status

Accepted

## Context

Story 3.2 introduced `AllowlistMiddleware` as an aiogram outer middleware
that checks every inbound Telegram `Update` against a configured frozenset
of allowed user IDs. Allowlisted users pass through to command handlers;
non-allowlisted users are silently rejected with a `telegram.rejected` event.

During Stories 3.16–3.19 (decision commands), the question "what happens when
`from_user` is `None`?" was raised in four consecutive code reviews. Each time
the answer was the same: the middleware already handles it by emitting
`user_id=0, reason="no_from_user"`. No handler-level auth check is needed or
desired.

The repeated reviewer energy indicated a documentation gap, not a design gap.

## Decision

1. **`AllowlistMiddleware` is the single auth gate.** No handler-level auth
   checks shall be added. The middleware runs as the first user-registered
   outer middleware in the aiogram dispatcher chain, before any inner
   middleware or handler.

2. **`from_user=None` updates are rejected with sentinel values.** When an
   `Update` arrives with no `from_user` or `user` attribute (bot-only update
   types like `message_reaction_count`), the middleware emits a
   `telegram.rejected` event with `user_id=0` and
   `reason="no_from_user"`. The handler is never invoked.

3. **Rejected users receive no outbound message.** The middleware returns
   `None`, suppressing handler invocation. The webhook returns `200` regardless
   (fire-and-forget contract). Telegram observes a clean ACK with zero
   `sendMessage` calls.

## Consequences

- Handlers can assume `from_user` is allowlisted if they are reached at all.
  No defensive `if user_id not in allowlist` checks are needed in handler code.
- The `from_user=None` case is handled once, centrally, with a test that pins
  the sentinel values (`test_event_without_from_user_rejected_with_sentinel` in
  `test_allowlist.py`).
- Future middleware (request-id, rate-limiter) must register *after*
  `AllowlistMiddleware` in the outer chain so allowlist check runs first.
  `test_allowlist_middleware_is_first_in_chain` guards this ordering.
- Empty allowlist (`TG_ALLOWLIST_USER_IDS=[]`) rejects everyone. A startup
  WARNING is logged to surface misconfiguration.
