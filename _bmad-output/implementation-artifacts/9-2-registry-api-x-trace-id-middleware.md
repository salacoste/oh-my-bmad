# Story 9.2 — registry-api `TraceIdMiddleware` (`X-Trace-Id` header)

Status: **ready-for-dev**

## Story

**As** an operator command flowing through the registry-api HTTP surface,
**I want** the registry-api to extract the `X-Trace-Id` request header (validated against the Story 9.1 contract), mint a fresh UUIDv7 if absent or malformed, attach it to `request.state.trace_id`, bind it into the structlog context for the duration of the request, echo it on the response, AND propagate it into every `EventEnvelope.create(...)` callsite inside the route handlers,
**so that** every event emitted by registry-api's `POST /v1/tasks`, `POST /v1/tasks/{id}/decisions`, and any future mutating endpoint carries the same `trace_id` as the inbound HTTP request — closing the first end-to-end ingress in Epic 9's α propagation kernel.

This is Story 9.2 of Epic 9 (α `trace_id` propagation kernel). It establishes the **HTTP ingress wiring** that the console CLI (9.4), MCP tool handlers (9.5), and worker subprocess (9.6) will all mirror. The pattern follows `RequestIdMiddleware`'s existing template (commit `12a27c4` and surrounding stories 2.9 + 3.6) and shares the validate-or-mint discipline with `IdempotencyKeyMiddleware`.

---

## Acceptance criteria

### AC1 — `TraceIdMiddleware` class added to `middleware.py`

A new middleware class `TraceIdMiddleware(BaseHTTPMiddleware)` lives in `services/registry-api/src/registry_api/adapters/middleware.py`. Signature mirrors `RequestIdMiddleware`:

```python
class TraceIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, clock: Clock) -> None: ...
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response: ...
```

Behaviour:
1. Read `X-Trace-Id` header.
2. If present AND matches the Story 9.1 contract (bare UUIDv7 OR `\Atg:[1-9][0-9]{0,18}\Z` with int64 ceiling), use it.
3. If present BUT malformed: log at WARNING with truncated received value (mirror `RequestIdMiddleware:103-106` pattern, 80-char truncation), then mint via `new_uuid7(clock=self._clock)`.
4. If absent: mint via `new_uuid7(clock=self._clock)` — NO warning (absent is the common case).
5. Attach to `request.state.trace_id`.
6. Bind into structlog context vars: `structlog.contextvars.bind_contextvars(trace_id=trace_id)`.
7. `try/finally` unbind so worker reuse doesn't leak across requests (`RequestIdMiddleware:115-119` is the load-bearing template here).
8. Echo `X-Trace-Id: <value>` on response.

### AC2 — Validation regex reused from Story 9.1 contract

Import the validation logic from `events.envelope` (don't duplicate the regex). Two options for implementation, pick one:

- **Preferred:** module-level `_validate_trace_id(value: str) -> str | None` helper at the top of `middleware.py` that returns the value if valid, `None` otherwise. Mirrors the `_UUIDV7_BARE_RE` pattern at line 73-75 (which is itself an envelope-side mirror per Epic 9 retro candidate).
- **Alternative:** call into a new public helper exported from `events.envelope` (e.g., `validate_trace_id_shape(v: str | None) -> bool`). This avoids regex duplication entirely but requires a public-API addition.

The Telegram form (`tg:<update_id>`) MUST be accepted at the HTTP boundary even though no Telegram client realistically sends `X-Trace-Id: tg:42` — Story 9.3 will set `trace_id = f"tg:{update.update_id}"` directly in `AllowlistMiddleware`, and the registry-api may later receive that value as an `X-Trace-Id` from a co-deployed bridge.

### AC3 — Middleware registration in `build_app`

`build_app` in `services/registry-api/src/registry_api/app.py` registers the new middleware as the **outermost** layer:

```python
app.add_middleware(TierEnforcementMiddleware, actor_kind=actor_kind)
app.add_middleware(ActorIdMiddleware)
app.add_middleware(IdempotencyKeyMiddleware, clock=clock)
app.add_middleware(RequestIdMiddleware, clock=clock)
app.add_middleware(TraceIdMiddleware, clock=clock)  # NEW — runs FIRST
```

Reasoning: Starlette reverses the `add_middleware` call order. Listing `TraceIdMiddleware` LAST in the call sequence means it runs FIRST in the execution flow. The trace_id must be bound before `RequestIdMiddleware` runs (so `request_id` log records can carry `trace_id` too) and certainly before any handler-level `EventEnvelope.create(...)` call.

Update the docstring at `middleware.py:38-46` to reflect the new order:

```
app.add_middleware(TierEnforcementMiddleware, ...)        # runs 5th (innermost)
app.add_middleware(ActorIdMiddleware)                      # runs 4th
app.add_middleware(IdempotencyKeyMiddleware, clock=clock)  # runs 3rd
app.add_middleware(RequestIdMiddleware, clock=clock)       # runs 2nd
app.add_middleware(TraceIdMiddleware, clock=clock)         # runs 1st (outermost)
```

### AC4 — Route handlers propagate `request.state.trace_id` into envelope.create()

Every `EventEnvelope.create(...)` callsite in `services/registry-api/src/registry_api/routes/*.py` must pass `trace_id=request.state.trace_id`. Targets identified by `grep "EventEnvelope.create\|new_uuid7" services/registry-api/src/registry_api/routes/*.py`:

- `services/registry-api/src/registry_api/routes/tasks.py:405, 436, ...` (4 callsites — task.created + task.planning.started)
- `services/registry-api/src/registry_api/routes/decisions.py:276, 304, 353, 379` (4 callsites — approval.granted / approval.rejected / task.stop_requested / task.retry_requested / tier3.license_override / tier3.budget_override)
- Any other `EventEnvelope.create(...)` callsites discovered during dev (use `grep` to confirm coverage)

The DeprecationWarning from Story 9.1 will stop firing for these callsites once 9.2 lands.

### AC5 — `routes/events.py:49` hardcoded `None` removed

`services/registry-api/src/registry_api/routes/events.py:49` currently reads:

```python
"trace_id": None,  # noqa: ERA001 — Phase 2: requires Event ORM column + migration + materializer update
```

This is the **outbound** projection (GET `/v1/tasks/{id}/events`). Story 9.2's scope is the **inbound** middleware + emitter wiring; the outbound projection requires an `events.trace_id` ORM column which is **Story 9.7's responsibility**. Leave this line UNCHANGED in 9.2. Update the noqa comment to reference Story 9.7 specifically:

```python
"trace_id": None,  # noqa: ERA001 — Story 9.7: ORM column + materializer update
```

### AC6 — Unit tests for `TraceIdMiddleware` (≥10 tests)

New test class `TestTraceIdMiddleware` in `services/registry-api/src/registry_api/test_middleware.py`, mirroring `TestRequestIdMiddlewareStructlog` (line 101-208):

1. `test_trace_id_minted_on_missing_header` — sends a request with no `X-Trace-Id`, asserts response has `X-Trace-Id` matching `_UUIDV7_BARE_RE`.
2. `test_trace_id_preserved_on_valid_uuidv7_header` — sends `X-Trace-Id: 01917e5c-a7d1-7000-8abc-...`, asserts response echoes the same value.
3. `test_trace_id_preserved_on_valid_telegram_form_header` — sends `X-Trace-Id: tg:42`, asserts response echoes `tg:42`.
4. `test_trace_id_regenerated_on_malformed_header` — sends `X-Trace-Id: bad-value`, asserts WARNING log + response carries fresh UUIDv7 (NOT `bad-value`).
5. `test_trace_id_regenerated_on_tg_zero_header` — sends `X-Trace-Id: tg:0`, asserts WARNING log + fresh UUIDv7 (the Story 9.1 leading-zero rejection applies here).
6. `test_trace_id_regenerated_on_int64_overflow_header` — sends `X-Trace-Id: tg:9999999999999999999`, asserts WARNING log + fresh UUIDv7.
7. `test_trace_id_attached_to_request_state` — handler under test reads `request.state.trace_id` and returns it in JSON body; assert matches the response header.
8. `test_trace_id_bound_to_structlog_context_during_request` — handler under test invokes `structlog.contextvars.get_contextvars()` and returns the trace_id key; assert == response header value.
9. `test_trace_id_unbound_from_structlog_context_after_request` — assert `structlog.contextvars.get_contextvars().get("trace_id")` is absent after the response is returned (worker-reuse safety, mirrors line 145-179).
10. `test_trace_id_unbound_even_when_handler_raises` — handler raises; assert structlog `trace_id` is still unbound after the exception propagates (mirror of `test_request_id_middleware_unbinds_on_handler_exception`).
11. `test_trace_id_truncated_in_log_for_malformed_header` — assert log record's `received` field is at most 80 chars.

Plus an integration test verifying that `EventEnvelope.create()` calls inside route handlers receive the trace_id:

12. `test_event_envelope_emitted_from_post_tasks_carries_trace_id` — `POST /v1/tasks` with `X-Trace-Id: <fixed>`; read the JSONL event log; assert the emitted `task.created` envelope's `trace_id` field equals the sent header.

### AC7 — `RequestIdMiddleware` semantics unchanged

The existing `request_id` plumbing must NOT change. Specifically:
- `RequestIdMiddleware` still validates against `_UUIDV7_BARE_RE` (line 73-75) — the `tg:` form is NOT a valid `request_id`.
- The structlog `request_id` context binding remains independent from the new `trace_id` binding.
- Response headers carry BOTH `X-Request-ID` AND `X-Trace-Id`.

### AC8 — mypy --strict clean + Epic 8.7 baseline gates

`uv run mypy --strict packages/ services/registry-api services/registry-state` exits 0 (97 source files baseline). `ruff check`, `ruff format --check`, `check_imports`, `check_single_writer`, and the secret-hygiene full-tree scan all pass. Test count delta: +12 to +15 new tests in `test_middleware.py`; full suite goes from 2227 → ~2240.

### AC9 — Deprecation warning count drops

Before 9.2, the suite emits ~80-90 `EventEnvelope created without trace_id` DeprecationWarnings (silenced via `pyproject.toml` filterwarnings). After 9.2, the registry-api callsites no longer emit. Verify with a sampling test or grep:

```bash
uv run pytest packages/ services/ -m "not slow" -W "default::DeprecationWarning" 2>&1 | grep -c "EventEnvelope created without trace_id"
```

Expected: the warning count drops by ~10-15 (the registry-api callsite cluster). Stories 9.3 – 9.6 will progressively reduce it further; Story 9.7 deletes the `filterwarnings` entry once all callsites pass `trace_id=`.

### AC10 — FR58 (HTTP) literal compliance

Every event emitted **as a direct result of an HTTP request** to registry-api now carries the `trace_id` bound by `TraceIdMiddleware`. Verify by tail-running an integration test that issues a `POST /v1/tasks`, reads the event log, and asserts the envelope's `trace_id` field is populated (not `None`).

---

## Developer context

### Architecture compliance

- **FR58 (HTTP)** — "registry-api `TraceIdMiddleware` mints `new_uuid7()` if absent, echoes on response." This story implements that literally.
- **FR58 narrative (PRD line 996-1001)** — every Platform entry point binds a `trace_id` to structlog context before any business logic runs. AC6 #8 tests this binding directly.
- **NFR-O7** — every event emitted in Phase 2+ carries a non-null trace_id. Once 9.2 lands, the registry-api callsites comply; 9.3-9.6 close the remaining ingresses.
- **Architecture §"trace_id propagation wiring" (Mermaid diagram, line 1117+)** — registry-api is the "HTTP X-Trace-Id header" ingress in the diagram.
- **P2-I2 (single Phase 2 schema bump)** — 9.2 does NOT bump `schema_version`. The envelope stays at `1.0.0` until Story 9.7.

### Library / framework requirements

| Library | Version | Source | Notes |
|---|---|---|---|
| Starlette `BaseHTTPMiddleware` | already in registry-api deps | — | Use the `middleware/base.py` class — same parent as `RequestIdMiddleware` |
| structlog | already in registry-api deps | — | `contextvars.bind_contextvars` / `unbind_contextvars` API |
| events | workspace member | — | Import `new_uuid7` (NOT `new_request_id` — we want the bare UUIDv7 generator, semantically distinct from request_id even though they share output shape) |
| pytest + httpx + asgi-lifespan | dev deps (Epic 8.7 elevated to workspace root) | — | Standard test harness |

No new deps.

### File-structure requirements

| File | Change |
|---|---|
| `services/registry-api/src/registry_api/adapters/middleware.py` | Add `TraceIdMiddleware` class + optional `_validate_trace_id` module helper. Update `__all__`. Update docstring at `:38-46`. |
| `services/registry-api/src/registry_api/app.py` | Add `app.add_middleware(TraceIdMiddleware, clock=clock)` line 250 (after `RequestIdMiddleware`). Update the comment block above the middleware stack. |
| `services/registry-api/src/registry_api/routes/tasks.py` | Add `trace_id=request.state.trace_id` to every `EventEnvelope.create(...)` callsite (lines ~405, ~436 + others). |
| `services/registry-api/src/registry_api/routes/decisions.py` | Add `trace_id=request.state.trace_id` to every `EventEnvelope.create(...)` callsite (lines ~276, ~304, ~353, ~379). |
| `services/registry-api/src/registry_api/routes/events.py:49` | Update noqa comment text only; keep the `None` value. |
| `services/registry-api/src/registry_api/test_middleware.py` | New `TestTraceIdMiddleware` class with ≥12 tests (AC6). |
| `services/registry-api/src/registry_api/test_events.py` | Update tests that assert `trace_id is None` to ALSO assert the route DOES pass through the inbound header when set. |

Do **not** touch:
- `events.py:49`'s `None` value — that's an outbound projection requiring an ORM column (Story 9.7).
- Any `services/registry-state` materializer code — that's Story 9.7.
- The `packages/events/src/events/envelope.py` validator — Story 9.1 owns it; 9.2 reuses.
- `pyproject.toml` filterwarnings — stays in place until Story 9.7.

### Testing requirements

- **Unit tests** — `test_middleware.py` ≥12 new tests (per AC6). Mirror `TestRequestIdMiddlewareStructlog` patterns exactly.
- **Integration test** — at least one `POST /v1/tasks` end-to-end test asserting the emitted envelope's `trace_id` matches the inbound header. Likely in `test_tasks.py` or `test_decisions.py`.
- **No new contract tests** — wire contract changes belong to Story 9.7's `schema_version` bump.
- Markers: PR-gate tests (not `@pytest.mark.slow`).
- **Test isolation**: AT LEAST one test should bind / unbind / re-bind / re-unbind to prove the worker-reuse safety pattern doesn't leak. The existing `test_request_id_middleware_unbinds_on_handler_exception` is the template (line 145-179).

### Previous-story intelligence

Closest analogues:

- **Story 2.9 + Story 3.6** — original `RequestIdMiddleware` + `IdempotencyKeyMiddleware` work. The patterns and `try/finally` discipline are the established template; copy them faithfully.
- **Story 9.1** (just landed) — established the `trace_id` shape contract (UUIDv7 OR `\Atg:[1-9][0-9]{0,18}\Z` with int64 ceiling). The `EventEnvelope` validator REJECTS malformed values, so the middleware MUST validate before writing to `request.state.trace_id` — otherwise a malformed value would cause `EventEnvelope.create()` to raise `ValidationError` mid-request.
- **Story 9.1 deprecation warning** — every `EventEnvelope.create(...)` callsite without `trace_id=` emits a warning. Story 9.2 silences the registry-api cluster. The filterwarnings entry in `pyproject.toml` stays until Story 9.7 (per Story 9.1's plan).
- **Epic 8 retro lesson L2 (documentation poisoning)** — when updating `middleware.py` docstring, update EVERY occurrence (file-top docstring + per-class docstrings + `__all__`). Pass-1 reviewers in Epic 8 routinely caught spec-body drift; AC8 covers the mypy gate but a doc-drift sweep is on you.
- **Epic 8.7 retro lesson L1 (hidden cascade)** — after local-green, push to CI and watch the next gate. Don't assume "local mypy/ruff/pytest green = CI green".

### Git intelligence

Latest 5 commits as of 2026-05-16:

```
7cfebd9 fix(story-9.1): pass-2 second-opinion review — 12 minors applied
1ea5e90 fix(story-9.1): pass-1 review — 12 patches batch-applied
dae92d8 feat(events): Story 9.1 — trace_id field hardening + deprecation warning
07a9804 docs(story-9.1): spec — trace_id optional envelope + deprecation warning
3cbacec docs(epic-8.7): close retro + spec Story 8.7.6 aiosqlite teardown root-fix
```

No other Epic 9 commits in flight. Story 9.2 is the only story currently in scope.

### Latest-tech notes

- **Starlette `BaseHTTPMiddleware`** is the right parent — `RequestIdMiddleware` already uses it. There is a newer raw-ASGI pattern via `ASGIApp` directly but it's unnecessary churn here.
- **structlog `contextvars`** API is stable. `bind_contextvars(trace_id=value)` adds; `unbind_contextvars("trace_id")` removes by key name (positional arg). The `try/finally` placement is load-bearing.
- **httpx `AsyncClient`** + **asgi-lifespan `LifespanManager`** — already wired for test harness (Epic 8.7 elevated `asgi-lifespan` + `httpx` to workspace dev deps). Use the same fixture pattern as `test_request_id_middleware_binds_to_structlog_context_and_unbinds_on_success` (line 105-145).

---

## Dev notes

### Middleware implementation sketch

```python
# middleware.py — after _UUIDV7_BARE_RE at line 73:

# Story 9.1 contract mirror — anchored UUIDv7 OR Telegram-derived form.
# Wraps both forms to validate inbound X-Trace-Id headers symmetrically
# with the envelope-side validator (events.envelope._trace_id_shape).
_TRACE_ID_TELEGRAM_RE = re.compile(r"\Atg:[1-9][0-9]{0,18}\Z")
_INT64_MAX = 9_223_372_036_854_775_807


def _is_valid_trace_id(value: str) -> bool:
    """True if *value* matches the Story 9.1 trace_id contract."""
    if _UUIDV7_BARE_RE.match(value):
        return True
    if _TRACE_ID_TELEGRAM_RE.match(value):
        return int(value[3:]) <= _INT64_MAX
    return False


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Read ``X-Trace-Id`` header; validate per Story 9.1 contract;
    generate UUIDv7 if absent or malformed; attach to ``request.state.trace_id``;
    bind into structlog context; echo on response.

    This is the HTTP ingress for Epic 9's α trace_id propagation kernel
    (FR58). Stories 9.3-9.6 implement the Telegram / console / MCP / worker
    ingresses; Story 9.7 makes trace_id mandatory.
    """

    def __init__(self, app: ASGIApp, *, clock: Clock) -> None:
        super().__init__(app)
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Trace-Id")
        if incoming and _is_valid_trace_id(incoming):
            trace_id = incoming
        else:
            if incoming:
                _log.warning(
                    "invalid X-Trace-Id header; generating fresh",
                    extra={"received": incoming[:80]},
                )
            trace_id = new_uuid7(clock=self._clock)
        request.state.trace_id = trace_id
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("trace_id")
        response.headers["X-Trace-Id"] = trace_id
        return response
```

Don't forget `new_uuid7` (NOT `new_request_id`) in the import list. Semantically the bare-UUIDv7 generator is what's needed; `new_request_id` happens to delegate to `new_uuid7` today but coupling to its identity in the import would obscure intent.

### Route-handler edit pattern

Wherever a callsite reads `request_id = request.state.request_id`, add the parallel line:

```python
trace_id: str = request.state.trace_id
```

Then thread `trace_id=trace_id` through to every `EventEnvelope.create(...)` call in the function. Mechanical.

### Non-goals (do NOT do in 9.2)

- Implement the Telegram / console / MCP / worker ingresses — Stories 9.3 – 9.6.
- Bump `schema_version` to 1.1.0 — Story 9.7.
- Add `events.trace_id` ORM column / index — Story 9.7.
- Backfill historical events — Story 9.7.
- Remove `pyproject.toml` filterwarnings — Story 9.7.
- Add `/trace <id>` operator query — Story 9.7.
- Touch `routes/events.py:49` `None` value — Story 9.7 (only the comment text changes here).
- Change `RequestIdMiddleware` semantics — keep `request_id` as bare-UUIDv7-only.

If you find yourself editing `events.envelope.py`, the schema-registry, alembic migrations, or any `services/registry-state` materializer code → out of scope. Stop.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| `TraceIdMiddleware` runs BEFORE `RequestIdMiddleware`; structlog log records emitted from `RequestIdMiddleware` itself will now carry `trace_id`. Audit: is this desired? | Yes — desired. Trace_id is the parent correlation, request_id is per-request. Both should appear in every log record. Verify in AC6 #8. |
| Malformed `X-Trace-Id` header from a hostile caller — could the warning log spew leak header value to disk? | The 80-char truncation in the log mirrors `RequestIdMiddleware:105`. AC6 #11 locks the contract. |
| Pydantic `ValidationError` from `EventEnvelope.create(trace_id=...)` if the middleware passes through a malformed value. | The middleware ALWAYS validates before writing to `request.state.trace_id`. AC2 guarantees a 1:1 contract match with the envelope validator. If they ever diverge, this is a story-bug. |
| Test isolation: structlog contextvars are process-global. Parallel async tests could see each other's trace_id. | The `try/finally` unbind is the protection. AC6 #9 + #10 lock it. |
| Story 9.1's `pyproject.toml` filterwarnings entry will keep silencing warnings from the routes that 9.2 now wires correctly. That's fine — those callsites won't emit at all once `trace_id=` is passed. But the filter still suppresses warnings from OTHER (Stories 9.3 – 9.6) callsites. Don't remove the filter until 9.7. | Explicitly listed in non-goals. |
| `routes/events.py:49`'s outbound projection. If a developer thinks "9.2 wires trace_id; let me wire the projection too" — they'd need an ORM column + migration. | Explicit non-goal + comment update only. AC5 locks the constraint. |

---

## Definition of done

- All 10 ACs satisfied.
- `uv run pytest services/registry-api -q` shows the new `TestTraceIdMiddleware` tests passing.
- Local full-suite parity gate green (mypy --strict / ruff / format / imports / single-writer / secrets / pytest).
- CI green on push (allow for L1 hidden-gate cascade — be ready to fix a follow-up).
- Commit message follows the established `feat(registry-api): Story 9.2 — ...` style.
- `sprint-status.yaml` `9-2-registry-api-x-trace-id-middleware: backlog → done`.
- The Dev Agent Record section is filled in with: implementation notes, surprises, callsite-warning count drop (AC9), follow-up TODOs surfaced for 9.3 – 9.7.
- Three-lane code review (pass-1 + pass-2 if warranted) completed per Epic 8.x cadence.

---

## Dev Agent Record

_(To be completed by the dev agent at story closure.)_

### Implementation summary
_(tbd)_

### Files changed
_(tbd)_

### Test count delta
_(tbd)_

### Callsite-warning observation
_(How many DeprecationWarnings still fire after Story 9.2? Expected drop: ~10-15 from the registry-api cluster.)_

### Surprises / deviations from spec
_(tbd)_

### Follow-up TODOs surfaced for Epic 9
_(tbd)_

---

## Frontmatter

```yaml
---
story_id: 9.2
story_key: 9-2-registry-api-x-trace-id-middleware
parent_epic: 9
phase: 2
fr_refs: [FR58]
nfr_refs: [NFR-O7]
arch_refs:
  - "trace_id propagation wiring (Mermaid §line-1117+)"
  - "Phase 2 Invariants P2-I2"
  - "Envelope schema migration: 1.0.0 → 1.1.0 — Story 9.2 step 2"
estimated_hours: 3-5
priority: high (HTTP ingress for Epic 9; unblocks every later epic's HTTP-emitter callsites)
blocks:
  - 9.7 (schema bump uses Story 9.2's HTTP plumbing as the unit-test baseline)
blocked_by:
  - 9.1 (trace_id shape contract — landed in commit 7cfebd9)
status: ready-for-dev
created: 2026-05-16
created_by: bmad-create-story skill
---
```
