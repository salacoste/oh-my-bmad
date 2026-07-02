# Story 127.3 UltraQA

Verdict: PASS

Adversarial checks covered by tests:
- Raw `q=actor:@id` and `q=2026-01-01T00:00:00Z` remain unencoded and fetch with `credentials: "omit"`, no body.
- Hidden/missing malformed search controls, unsupported `field=status`, encoded `%`, plus `+`, leading/trailing-space query values, whitespace-mutated field/operator values, and invalid field/operator pairs fail closed before search fetch.
- Response route/selected-query mismatches fail closed before authoritative render.
- Search `has_more`/`next_offset` renders display metadata only and keeps manual previous/next disabled; no search-driven traversal occurs.
- Runtime source remains closed to URL/hash/storage/cookie side channels, timers/background effects, workers, broad route literals, and mutation methods.

Accepted verification gap:
- Full Docker integration suite is not a Story 127.3 acceptance gate and showed unrelated compose/Journey failures during broad-suite attempt; targeted dashboard, static, adapter, lint, and type checks are green.
