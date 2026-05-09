# Story 6.3: Tier enforcement in HTTP API middleware

Status: ready-for-dev

## Story

As the platform,
I want a FastAPI middleware that enforces capability tiers on state-mutating endpoints where relevant (e.g., `/v1/tasks/{id}/decisions` requires operator-authenticated caller),
so that the HTTP ingress respects the same tier model as MCP.

## Acceptance Criteria

1. **AC-1: `TierEnforcementMiddleware` class** — New `BaseHTTPMiddleware` subclass in `services/registry-api/src/registry_api/adapters/middleware.py`. Constructor accepts `app: ASGIApp` plus `actor_kind: ActorKind` (configurable per deployment). For each incoming request, builds a `CallerContext(actor_kind=actor_kind, actor_id=request.state.actor_id)` and attaches it to `request.state.caller_context` for downstream handler consumption.

2. **AC-2: Tier-2 enforcement on mutating routes** — The middleware checks mutating endpoints (POST/PUT/PATCH/DELETE) against a `ROUTE_TIER_MAP: dict[str, Tier]` mapping URL patterns to required tiers. Current Phase 1 map: `POST /v1/tasks` → `Tier.ONE`, all other mutating routes default to `Tier.ONE` unless explicitly mapped. If `check_tier` raises `CapabilityDenied`, the middleware short-circuits with an RFC 7807 problem+json 403 response; the handler never runs.

3. **AC-3: `CapabilityDenied` exception handler** — New exception handler `handle_capability_denied` in `adapters/errors.py` that maps `CapabilityDenied` to RFC 7807 problem+json with `status=403`, `type="/errors/forbidden"`, `title="Forbidden"`, `detail` from the exception's `reason`. Registered via `app.add_exception_handler(CapabilityDenied, handle_capability_denied)` in `build_app`.

4. **AC-4: Middleware registration order** — `TierEnforcementMiddleware` is added AFTER `ActorIdMiddleware` in `build_app` (so `request.state.actor_id` is populated before tier check runs). Starlette LIFO means it must be `add_middleware`'d BEFORE `ActorIdMiddleware`. Full execution order: `RequestId → IdempotencyKey → ActorId → TierEnforcement → handler`.

5. **AC-5: `actor_kind` configuration** — The `actor_kind` parameter is passed from `build_app` to the middleware constructor. Phase 1 hardcodes `"operator"` (the registry-api HTTP surface is operator-facing). `build_app` signature gains `actor_kind: ActorKind = "operator"` kwarg.

6. **AC-6: Idempotency cache key scope** — Fix the `TODO(Story 6.1)` in `routes/tasks.py` line 349: idempotency cache key becomes `(actor_id, idempotency_key)` tuple so different actors cannot observe each other's cached responses.

7. **AC-7: Negative test** — A test sends a mutating request with a caller whose max tier is below the route's required tier (patch `_MAX_TIER_BY_ACTOR` to constrain, or use a Tier-2 route with a worker-kind middleware). Asserts: response 403, body is RFC 7807 problem+json with `type="/errors/forbidden"`, and the handler was never invoked.

8. **AC-8: Positive test** — A test sends a mutating request with a caller authorized for the route's tier. Asserts: response 201 (or appropriate success), `request.state.caller_context` is set in a debug probe, handler runs normally.

9. **AC-9: No regression** — All existing tests pass. `check_imports.py` exits 0. `ruff check` clean. `just test` green.

10. **AC-10: Atomic commit** — Single commit with title `feat(registry-api): tier enforcement HTTP middleware (Story 6.3)`.

## Tasks

- [ ] Task 1 — Add problem-type and exception handler for `CapabilityDenied` (AC-3)
  - [ ] Add `_PROBLEM_TYPE_FORBIDDEN = "/errors/forbidden"` to `adapters/errors.py`
  - [ ] Add `handle_capability_denied(request, exc) -> JSONResponse` returning 403 problem+json
  - [ ] Import `CapabilityDenied` from `events.errors`
  - [ ] Export new symbol from `__all__`
  - [ ] Add unit tests for the handler
- [ ] Task 2 — Implement `TierEnforcementMiddleware` (AC-1, AC-2)
  - [ ] Add `TierEnforcementMiddleware(BaseHTTPMiddleware)` to `adapters/middleware.py`
  - [ ] Constructor: `app: ASGIApp, *, actor_kind: ActorKind`
  - [ ] `ROUTE_TIER_MAP: dict[str, Tier]` — Phase 1: `{"POST /v1/tasks": Tier.ONE}`
  - [ ] `dispatch`: on mutating methods, look up route in map, call `check_tier`, attach `CallerContext` to `request.state.caller_context`, short-circuit 403 on `CapabilityDenied`
  - [ ] Skip tier check on read methods (GET/HEAD/OPTIONS) and unknown routes
  - [ ] Export from `__all__`
- [ ] Task 3 — Wire middleware + exception handler into `build_app` (AC-4, AC-5)
  - [ ] Add `actor_kind: ActorKind = "operator"` to `build_app` signature
  - [ ] `app.add_middleware(TierEnforcementMiddleware, actor_kind=actor_kind)` BEFORE `ActorIdMiddleware`
  - [ ] `app.add_exception_handler(CapabilityDenied, handle_capability_denied)`
  - [ ] Import `ActorKind` from `events.envelope`
- [ ] Task 4 — Fix idempotency cache key scope (AC-6)
  - [ ] In `routes/tasks.py`, change cache key from `idempotency_key` to `(request.state.actor_id, idempotency_key)`
  - [ ] Update side-channel cache lookup and storage to match
  - [ ] Remove the `TODO(Story 6.1)` comment at line 349
- [ ] Task 5 — Add enforcement tests (AC-7, AC-8)
  - [ ] Test: tier-denied on mutating route returns 403 problem+json
  - [ ] Test: tier-allowed on mutating route succeeds normally
  - [ ] Test: read routes bypass tier check
  - [ ] Test: `request.state.caller_context` populated correctly
  - [ ] Test: `CapabilityDenied` handler returns RFC 7807 shape
- [ ] Task 6 — Verification + commit (AC-9, AC-10)
  - [ ] Run `check_imports.py`
  - [ ] Run `ruff check`
  - [ ] Run `just test`
  - [ ] Atomic commit

## Dev Notes

### Key Architecture Decision: Middleware Position in Stack

The middleware must run AFTER `ActorIdMiddleware` (which sets `request.state.actor_id`) so the tier check has a valid actor identity. Starlette reverses `add_middleware` call order (LIFO), so the registration in `build_app` must be:

```python
# Execution order: RequestId → IdempotencyKey → ActorId → TierEnforcement → handler
# Starlette LIFO: add last → runs first. So add TierEnforcement FIRST.
app.add_middleware(TierEnforcementMiddleware, actor_kind=actor_kind)  # runs 4th (innermost)
app.add_middleware(ActorIdMiddleware)                                  # runs 3rd
app.add_middleware(IdempotencyKeyMiddleware, clock=clock)             # runs 2nd
app.add_middleware(RequestIdMiddleware, clock=clock)                  # runs 1st (outermost)
```

### Why `actor_kind` is Configurable per Deployment

Phase 1 hardcodes `"operator"` because the HTTP API is operator-facing (Telegram bot, console CLI). But the middleware is generic — a future deployment could configure `"worker"` or `"orchestrator"` if the API surface expands. The `actor_kind` parameter keeps the middleware reusable without changing code.

### Why `ROUTE_TIER_MAP` Instead of Decorators

- Single source of truth — one dict at module level, easy to audit
- No route handler changes — handlers don't import capabilities directly
- Consistent with MCP server `TIER_MAP` pattern established in Stories 6.1/6.2
- The map is checked by path prefix so `/v1/tasks` matches both `/v1/tasks` and `/v1/tasks/{id}` sub-paths

### Relationship to Stories 6.1 and 6.2

- **Story 6.1** created `check_tier`, `Tier`, `CallerContext`, `CapabilityDenied` in `packages/capabilities/` — this story CONSUMES them
- **Story 6.2** added `check_tier_with_approval` and the approval-lookup pattern — the HTTP middleware does NOT need `check_tier_with_approval` in Phase 1 (no Tier-3 routes exist yet; Story 6.4's `/decisions` endpoint will add Tier-2 enforcement when it lands)
- The middleware uses synchronous `check_tier` only — no async approval lookup needed for Tier-1 routes

### Import Graph Constraints

- `services/registry-api/` may import from `packages/*` — `from capabilities import check_tier, CallerContext, Tier` is valid
- `services/registry-api/` may import from `events.envelope` for `ActorKind` — valid
- `services/registry-api/` may NOT import from `mcp-servers/*` or other `services/*`
- Domain layer (`domain/`) must have zero IO imports — the middleware lives in `adapters/`

### Error Response Shape

Follow the existing RFC 7807 pattern from `adapters/errors.py`:

```python
ProblemDetails(
    type="/errors/forbidden",
    title="Forbidden",
    status=403,
    detail=exc.reason,  # from CapabilityDenied
    instance=str(request.url),
    extensions=_build_idempotency_extensions(request),
)
```

The problem-type slug `/errors/forbidden` is new — add it to the catalog alongside the existing `/errors/not-found`, `/errors/validation`, etc.

### Idempotency Cache Key Fix

Current code (routes/tasks.py line 349):
```python
# TODO(Story 6.1): cache key must be (actor_id, idempotency_key) once
# tier enforcement lands
```

After this story:
```python
cache_key = (request.state.actor_id, idempotency_key)
```

This affects both `idempotency_cache.get_or_run()` and `response_body_cache` lookups. Both must use the tuple key consistently. Phase 1 actor_id is hardcoded `"http-api"`, so this change is transparent to existing tests.

### Files to Touch

| File | Change |
|------|--------|
| `services/registry-api/src/registry_api/adapters/middleware.py` | Add `TierEnforcementMiddleware` class |
| `services/registry-api/src/registry_api/adapters/errors.py` | Add `handle_capability_denied`, `/errors/forbidden` type |
| `services/registry-api/src/registry_api/app.py` | Wire middleware + exception handler, add `actor_kind` param |
| `services/registry-api/src/registry_api/routes/tasks.py` | Fix idempotency cache key to `(actor_id, key)` tuple |
| `services/registry-api/src/registry_api/test_app.py` | Tier-enforcement integration tests |
| `services/registry-api/src/registry_api/test_middleware.py` | Tier-middleware unit tests |

### Gotchas from Previous Stories

- **structlog**: Never use `event=` as kwarg with structlog loggers — clashes with positional `event` param. Use `cap_event=` or similar.
- **BaseHTTPMiddleware**: Must call `call_next(request)` on the happy path — forgetting to return the response is a common mistake.
- **RFC 7807**: All error responses must use `application/problem+json` media type, never `application/json`.
- **`_MUTATING_METHODS`**: Already defined in `adapters/errors.py` as `frozenset({"POST", "PUT", "PATCH", "DELETE"})` — reuse this constant, do not redefine.
- **`_MAX_TIER_BY_ACTOR`**: In `tiers.py` — for negative tests, patch this dict to constrain the actor's max tier.
- **`request.state`**: Use `getattr(request.state, "actor_id", None)` defensively — middleware ordering is not guaranteed if someone misconfigures the stack.

### Scope Boundary

- Do NOT add the `/v1/tasks/{id}/decisions` endpoint — that is Story 6.4
- Do NOT add Tier-3 approval lookup to the middleware — no Tier-3 routes exist yet
- Do NOT replace `ActorIdMiddleware` with real auth — Phase 1 keeps the hardcoded `"http-api"` value
- DO create the tier-enforcement mechanism so Story 6.4 can add Tier-2 routes to `ROUTE_TIER_MAP`
- DO fix the idempotency cache key scope (AC-6) — it was deferred from Story 6.1

### References

- [Source: epics.md — Epic 6 Story 6.3]
- [Source: prd.md — FR37, FR38, NFR-S6]
- [Source: architecture.md — line 214 authorization model, line 823 enforcement locations]
- [Source: architecture.md — line 616 registry-api directory tree]
- [Source: architecture.md — lines 338-341 import graph constraints]
- [Source: 6-1 story artifact — check_tier, Tier, CallerContext, CapabilityDenied design]
- [Source: 6-2 story artifact — check_tier_with_approval, TIER_MAP pattern, approval lookup]

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
