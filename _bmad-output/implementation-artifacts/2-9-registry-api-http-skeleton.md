# Story 2.9: Registry-api HTTP skeleton (`POST /v1/tasks` + `GET /v1/tasks/{id}`)

Status: review

## Story

As **the operator (via Telegram or console)**,
I want **a FastAPI service exposing `POST /v1/tasks` (creates a task by emitting `task.created` to the JSONL event log) and `GET /v1/tasks/{id}` (returns full materialized state from a read-only SQLite connection) — with `X-Request-ID` + `Idempotency-Key` header extraction middleware, RFC 7807 problem+json error envelopes, and OpenAPI auto-docs**,
so that **the platform has its first HTTP ingress + read surface (FR1 + FR4) other services (Telegram bot in Epic 3, console CLI in Epic 4) can consume; FR26 single-writer remains preserved (registry-api appends events but does NOT mutate SQLite — the materializer does)**.

## Acceptance Criteria

1. **AC-1: `services/registry-api/src/registry_api/app.py`** — FastAPI application factory. Exports:

   - `def build_app(*, base_dir: Path, db_url: str, clock: Clock) -> FastAPI` — factory taking the configuration injected at startup. Returns the wired-up `FastAPI` instance with lifespan handler, middlewares, and routes registered. Tests construct via this factory; production `__main__` reads env vars then calls it.

   - `@asynccontextmanager async def lifespan(app: FastAPI)` — async lifespan registered on the app. On startup: creates `EventLogWriter` (Story 2.4), creates read-only SQLite engine via `create_engine(db_url, read_only=True)` (Story 2.3), runs `await recover_all_logs(base_dir)` defensively (idempotent — doesn't conflict with the materializer's recovery). On shutdown: `await writer.close()` + `await engine.dispose()`. Stores both on `app.state` for handler access.

2. **AC-2: `POST /v1/tasks` endpoint** in `services/registry-api/src/registry_api/routes/tasks.py`:

   - **Request body** (`CreateTaskRequest` Pydantic model): `title: str`, `repo: str | None = None`, `hint: str | None = None`. `frozen=True, strict=True, extra="forbid"`.
   - **Response body** (`CreateTaskResponse`): `task_id: str`, `event_id: str`, `created_at: datetime`. Status `201 Created`.
   - **Headers consumed**: `Idempotency-Key` (optional; if absent generate via `new_idempotency_key(clock=clock)`). `X-Request-ID` (optional; generated UUIDv7 if absent — via middleware below).
   - **Behavior**:
     1. Generate `task_id = new_task_id(clock=clock)` and `event_id = new_event_id(clock=clock)`.
     2. Build `TaskCreatedPayload(task_id=task_id, title=title)` via Story 2.5's payload model.
     3. Build `EventEnvelope.create(event_id=event_id, type="task.created", schema_version="1.0.0", emitted_at=clock.now(), emitted_at_monotonic_ns=clock.monotonic_ns(), actor=Actor(kind="operator", id=request.state.actor_id), payload=task_created_payload, request_id=request.state.request_id, parent_event_id=None)`.
     4. `await app.state.writer.append(envelope)`.
     5. Return `CreateTaskResponse(task_id=task_id, event_id=event_id, created_at=envelope.emitted_at)`.
   - **Idempotency NOT yet enforced** at the dedup level — Story 2.7's cache wiring is explicitly deferred to Story 3.6 (FastAPI middleware stack). 2.9 reads the header + records it in `events.request_id` (which it already does via the envelope's `request_id` field — the header → `request.state.request_id` propagation handles this).
   - **Actor**: hardcoded as `Actor(kind="operator", id="http-api")` for Phase 1. Telegram bot integration (Epic 3) replaces this with the real Telegram user ID. Documented as a Phase-1 limitation in code comment + spec.

3. **AC-3: `GET /v1/tasks/{task_id}` endpoint** in same routes module:

   - **Path parameter**: `task_id: str` — must match `^t-<uuidv7>$` regex (Pydantic `Field(pattern=...)`).
   - **Response body** (`TaskResponse`):
     ```python
     class TaskResponse(BaseModel):
         task_id: str
         status: str  # canonical lifecycle states
         title: str | None
         created_at: datetime
         updated_at: datetime
         actor: ActorOut  # nested {kind, id}
         last_event: LastEventOut | None  # {id, type, emitted_at}
         next_commands: list[str]  # available commands per current state
     ```
   - **Behavior**:
     1. Open async session via `app.state.engine` + `async_sessionmaker`.
     2. Query `SELECT * FROM tasks WHERE id = task_id` via SQLAlchemy ORM.
     3. If not found: raise `HTTPException(status_code=404, ...)` (mapped to RFC 7807 problem+json by AC-5's exception handler).
     4. Query `SELECT * FROM events WHERE id = task.last_event_id` to populate `last_event`.
     5. Compute `next_commands` per current status via a small lookup table (helper function `_next_commands_for(status)`). For Phase 1: `pending → ["stop"]`, `planning → ["stop"]`, `plan_ready → ["approve", "reject", "stop"]`, `executing → ["stop"]`, `completed → []`, etc.
     6. Return populated `TaskResponse` model.
   - Status `200 OK` on success.

4. **AC-4: HTTP middleware stack** in `services/registry-api/src/registry_api/adapters/middleware.py`:

   - **`RequestIdMiddleware`**: reads `X-Request-ID` header; if absent, generates via `new_request_id(clock=clock)`. Attaches to `request.state.request_id`. Sets the same value on the response header for echo.
   - **`IdempotencyKeyMiddleware`**: reads `Idempotency-Key` header; if absent, generates via `new_idempotency_key(clock=clock)`. Attaches to `request.state.idempotency_key`. **Does NOT yet enforce dedup** (deferred to Story 3.6 per Story 2.7 AC-12).
   - **`ActorIdMiddleware`** (Phase 1 placeholder): sets `request.state.actor_id = "http-api"` as a hardcoded operator ID. Real auth lands in Story 6.1+. Documented as a TODO.
   - All three middlewares are class-based (subclass `BaseHTTPMiddleware`) and registered in `app.add_middleware(...)` in `build_app`. Architecture line 213's order: request-id → idempotency-key → log-sanitizer → rate-limiter. Story 2.9 ships the first three (log-sanitizer can be a passthrough no-op for 2.9; full sanitization is Story 1.7 territory + Story 3.6 integration); rate-limiter is Telegram-specific (Story 3.x).

5. **AC-5: RFC 7807 problem+json error envelope** in `services/registry-api/src/registry_api/adapters/errors.py`:

   - `ProblemDetails` Pydantic model: `type: str = "about:blank"`, `title: str`, `status: int`, `detail: str | None = None`, `instance: str | None = None` (request URL).
   - `_problem_details_from_http_exc(exc: HTTPException, request: Request) -> JSONResponse`: maps FastAPI's `HTTPException` to `application/problem+json` content-type response.
   - Registered via `app.exception_handler(HTTPException)(_problem_details_from_http_exc)` in `build_app`.
   - Also handle `RequestValidationError` (Pydantic validation): map to 400 + RFC 7807 with detailed field errors in `detail`.

6. **AC-6: `services/registry-api/src/registry_api/__main__.py`** — entry point:

   - Reads env vars:
     - `REGISTRY_API_DB_URL` (default `sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3`)
     - `REGISTRY_API_LOG_DIR` (default `/var/lib/oh-my-bmad/registry/events`)
     - `REGISTRY_API_HOST` (default `0.0.0.0`)
     - `REGISTRY_API_PORT` (default `8080`)
   - Constructs `SystemClock`; calls `build_app(base_dir, db_url, clock)`.
   - Runs `uvicorn.run(app, host=host, port=port)` (programmatic; no separate `uvicorn` CLI invocation).
   - Logs to stderr; structured logging via Python `logging`.

7. **AC-7: registry-api does NOT write SQLite state** (FR26). Verified by:
   - `scripts/check_single_writer.py` runs against `services/registry-api/` and exits 0 (the gate's `_EXCLUDED_ROOTS` is `services/registry-state/` only — registry-api MUST be scanned).
   - The only mutation in registry-api is `EventLogWriter.append()` to JSONL files, NOT SQLAlchemy session.add/commit. The scanner targets SQLite writes specifically.

8. **AC-8: Read-only engine** for GET handlers. The lifespan creates the engine via `create_engine(db_url, read_only=True)` — connection-level enforcement that the API process CANNOT write to SQLite even by accident. Empirical proof: a test that constructs the read-only engine and attempts a hand-rolled INSERT must raise `OperationalError` matching "readonly|read-only".

9. **AC-9: `services/registry-api/pyproject.toml`** dependencies:
   - `fastapi>=0.110`
   - `uvicorn[standard]>=0.30`
   - `pydantic>=2.8`
   - `events>=0.3.0` (workspace)
   - `registry-state>=0.5.0` (workspace — for `EventLogWriter`, `Task` ORM model, `create_engine`, etc.)
   - Version bumped `0.1.0 → 0.2.0`. uv.lock regenerated.

10. **AC-10: `services/registry-api/src/registry_api/__init__.py`** re-exports:
    ```python
    from registry_api.app import build_app
    __version__ = "0.2.0"
    __all__ = ["build_app"]
    ```

11. **AC-11: Co-located tests in `services/registry-api/src/registry_api/test_app.py`** — 18-22 tests:

    **TestPostTasks** (~6):
    - `test_post_tasks_returns_201_with_task_id`.
    - `test_post_tasks_writes_envelope_to_jsonl_log`.
    - `test_post_tasks_envelope_has_task_created_type`.
    - `test_post_tasks_uses_request_id_from_header_when_provided`.
    - `test_post_tasks_generates_request_id_when_header_absent`.
    - `test_post_tasks_rejects_extra_fields_in_body` (Pydantic `extra="forbid"`).

    **TestGetTaskById** (~5):
    - `test_get_task_returns_200_with_full_state` (after seeding tasks row directly).
    - `test_get_task_returns_404_with_problem_json_when_not_found`.
    - `test_get_task_404_response_has_correct_content_type` (`application/problem+json`).
    - `test_get_task_includes_last_event_field`.
    - `test_get_task_includes_next_commands_per_status`.

    **TestMiddleware** (~4):
    - `test_request_id_middleware_generates_when_absent`.
    - `test_request_id_middleware_propagates_when_present` (echoes back in response header).
    - `test_idempotency_key_middleware_attaches_to_request_state`.
    - `test_actor_id_middleware_sets_http_api_default`.

    **TestErrorHandlers** (~3):
    - `test_validation_error_returns_400_problem_json`.
    - `test_http_exception_returns_problem_json`.
    - `test_unhandled_exception_returns_500_problem_json` (500 path covered by FastAPI's default + our handler).

    **TestEntryPoint** (~2):
    - `test_main_uses_default_host_port_when_env_absent`.
    - `test_main_respects_env_overrides` (mock uvicorn.run, check args).

12. **AC-12: mypy --strict clean.** No `Any`, `cast()`, `# type: ignore` outside justified SDK-stub gaps. FastAPI's typing is mature in 0.110+; should be clean.

13. **AC-13: Single-writer CI green.** Per AC-7. No `# noqa: SW001` permitted.

14. **AC-14: scan-secrets clean.** No new patterns.

15. **AC-15: check_event_registry green.** registry-api emits `task.created` (a literal); the scanner accepts the literal type kwarg. No noqa needed.

16. **AC-16: check_imports green.** registry-api imports from `events` (packages) + `registry-state` (services). The `services/ → services/` direction is allowed. The `services/ → packages/` direction is allowed.

17. **AC-17: Regression green.**
    - `just test` count bumps from **366 passed, 6 skipped** (post-Story-2.8-fixes) by ≥18 (target: 384+).
    - `just lint` — all 7 green; mypy strict on ≥60 source files (was 55; +app.py, +middleware.py, +errors.py, +tasks.py, +test_app.py).
    - `just bootstrap-verify` — `registry_api 0.2.0`.
    - `just check-gates-self-test` — 3/3.

18. **AC-18: Atomic commit titled** `feat(registry-api): story 2.9 — POST /v1/tasks + GET /v1/tasks/{id} (FastAPI skeleton) · FR1 FR4 FR8 FR26`.

## Tasks / Subtasks

- [x] **Task 1: `app.py` factory + lifespan** (AC: #1, #5)
  - [x] `build_app(*, base_dir, db_url, clock) -> FastAPI` factory.
  - [x] `@asynccontextmanager async def lifespan(...)` — creates writer, engine on startup; cleans up on shutdown; stores on `app.state`.
  - [x] Registers middlewares + exception handlers + routes.

- [x] **Task 2: `routes/tasks.py` — POST + GET endpoints** (AC: #2, #3)
  - [x] `CreateTaskRequest`, `CreateTaskResponse` Pydantic models.
  - [x] `TaskResponse`, `ActorOut`, `LastEventOut` Pydantic models.
  - [x] `POST /v1/tasks` handler (emit `task.created`, return 201).
  - [x] `GET /v1/tasks/{task_id}` handler (read-only SQLite query, return 200 or 404).
  - [x] `_next_commands_for(status)` helper.

- [x] **Task 3: `adapters/middleware.py`** (AC: #4)
  - [x] `RequestIdMiddleware`, `IdempotencyKeyMiddleware`, `ActorIdMiddleware` — each subclasses `BaseHTTPMiddleware`.
  - [x] Inject `Clock` via constructor (FastAPI middleware-with-deps pattern).

- [x] **Task 4: `adapters/errors.py`** (AC: #5)
  - [x] `ProblemDetails` Pydantic model.
  - [x] `_problem_details_from_http_exc` handler mapping HTTPException + RequestValidationError.
  - [x] Sets content-type `application/problem+json`.

- [x] **Task 5: `__main__.py`** (AC: #6)
  - [x] Read env vars; default fallbacks.
  - [x] Construct SystemClock; call `build_app`.
  - [x] `uvicorn.run(app, host=..., port=...)`.

- [x] **Task 6: `__init__.py` re-exports + version** (AC: #10)
  - [x] Re-export `build_app`. `__version__ = "0.2.0"`.

- [x] **Task 7: `pyproject.toml` + uv.lock** (AC: #9)
  - [x] Add `fastapi>=0.110`, `uvicorn[standard]>=0.30`, `pydantic>=2.8`, `events`, `registry-state`.
  - [x] Version 0.1.0 → 0.2.0.
  - [x] `uv sync --all-groups`.

- [x] **Task 8: `test_app.py`** (AC: #11)
  - [x] 5 test classes per AC-11. Use `httpx.AsyncClient(transport=ASGITransport(app=app))` for async test client. Each test builds a fresh app via `build_app` with tmp_path + in-memory SQLite (`sqlite+aiosqlite:///:memory:`).
  - [x] Pre-create tables via `Base.metadata.create_all` in test fixtures.
  - [x] Use `FrozenClock` + `Random(42)` for deterministic test data.

- [x] **Task 9: Regression + atomic commit** (AC: #13, #14, #15, #16, #17, #18)
  - [x] `just test` count ≥384.
  - [x] `just lint` 7/7 green; mypy strict on ≥60 files.
  - [x] `just bootstrap-verify` → `registry_api 0.2.0`.
  - [x] `just check-gates-self-test` 3/3.
  - [x] Single atomic commit per AC-18.

## Dev Notes

### Architecture patterns for this story

- **registry-api is stateless** (Arch line 41): "Stateless container; delegates all persistence to 1b." Means: no in-memory caches that cross requests; engine + writer on `app.state` are dependencies, not state.
- **registry-api appends events directly** (NOT via clawhip-bridge MCP): per Phase 1 deployment story, registry-api is a service process. The clawhip-bridge MCP is for AGENT consumers (workers, orchestrator) over stdio — NOT for in-process HTTP services. registry-api imports `EventLogWriter` from `registry_state` and calls it directly. **FR26 single-writer is preserved at the SQLite layer**: the materializer (registry-state subscriber) is the only SQLite mutator. registry-api appends events to JSONL (which is the source of truth, not the materialized state).
- **GET reads through read-only engine** (Story 2.3 pattern). Belt-and-braces with single-writer CI gate: if a future bug attempts `session.add(...)` on the read-only engine, SQLite raises `OperationalError`. Defense in depth.
- **Idempotency dedup deferred** to Story 3.6 per Story 2.7 AC-12. 2.9 reads the header into `request.state.idempotency_key` but doesn't call `IdempotencyCacheStore.get_or_run`. The header value flows into envelope's `request_id` field for audit trail.
- **Actor identity is `("operator", "http-api")` for Phase 1.** Telegram bot integration in Epic 3 will replace this with real user IDs. Auth/policy enforcement is Story 6.1+.

### `next_commands` lookup table (Phase 1)

```python
_NEXT_COMMANDS: dict[str, list[str]] = {
    "pending": ["stop"],
    "planning": ["stop"],
    "plan_ready": ["approve", "reject", "stop"],
    "executing": ["stop"],
    "completed": [],
    "failed": [],
    "stopped": [],
    "blocked": ["retry", "stop"],  # placeholder; full state machine in Stories 5.x/6.x
}

def _next_commands_for(status: str) -> list[str]:
    return _NEXT_COMMANDS.get(status, [])
```

This is intentionally minimal. Full lifecycle + command-availability logic lands in Stories 5.x (worker lifecycle) and 6.x (approval gate).

### `build_app` sketch

```python
# services/registry-api/src/registry_api/app.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from events.clock import Clock, SystemClock
from registry_state import EventLogWriter, create_engine, recover_all_logs
from registry_state.adapters.sqlite_store import get_session

from registry_api.adapters.errors import (
    handle_http_exception,
    handle_validation_error,
)
from registry_api.adapters.middleware import (
    ActorIdMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
)
from registry_api.routes.tasks import router as tasks_router


def build_app(*, base_dir: Path, db_url: str, clock: Clock) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup
        writer = EventLogWriter(base_dir=base_dir, clock=clock)
        # Defensive recovery (idempotent with materializer's recovery)
        await recover_all_logs(base_dir)
        engine = create_engine(db_url, read_only=True)
        session_maker = get_session(engine)

        app.state.writer = writer
        app.state.engine = engine
        app.state.session_maker = session_maker
        app.state.clock = clock
        try:
            yield
        finally:
            # Shutdown
            await writer.close()
            await engine.dispose()

    app = FastAPI(
        title="oh-my-bmad registry API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Middlewares (Architecture line 213 order; rate-limiter deferred to Story 3.x)
    app.add_middleware(ActorIdMiddleware)
    app.add_middleware(IdempotencyKeyMiddleware, clock=clock)
    app.add_middleware(RequestIdMiddleware, clock=clock)

    # Exception handlers — RFC 7807 problem+json
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)

    # Routes
    app.include_router(tasks_router, prefix="/v1")

    return app
```

### What this story does NOT do

- **No idempotency dedup logic** — Story 3.6 wires `IdempotencyCacheStore.get_or_run`.
- **No real auth** — Phase 1 hardcodes `actor_id="http-api"`; Story 6.1+ adds tier enforcement.
- **No rate limiter** — Story 3.x adds it on the Telegram webhook only.
- **No `GET /v1/tasks/{id}/events`** — separate endpoint, lands later in Story 7.5.
- **No `GET /v1/tasks/{id}/logs/digest`** — Stories 7.3-7.4.
- **No `POST /v1/tasks/{id}/decisions`** — Story 6.4.
- **No `GET /v1/sessions/{id}`** — Stories 5.x.
- **No `GET /v1/health`** — could be added trivially in 2.9; deferring to keep scope tight (Story 2.10 may pick it up alongside failure-detection events).
- **No log-sanitizer middleware** — Story 1.7 + 3.6.
- **No clawhip-bridge MCP integration** — registry-api emits directly via EventLogWriter.

### Previous Story Intelligence

- **Story 2.8** (`43f24cb` done) shipped clawhip-bridge MCP server + 4 new event types (`task.blocker_raised` / `task.summary_emitted` / `task.approval_requested` / `task.completed`) bringing REGISTRY to 8 types. registry-api emits `task.created` (Story 2.5 type, registered then).
- **Story 2.7** (`2f5ccd6` done) shipped `IdempotencyCacheStore`. NOT integrated here per its own AC-12.
- **Story 2.5** (`bc700f7` done) shipped `Materializer` + handler dispatch + 4 initial event types. registry-api triggers `task.created` emission; materializer creates the task row; GET /v1/tasks/{id} reads it back.
- **Story 2.4** (`8ec2891` done) shipped `EventLogWriter`. registry-api uses it.
- **Story 2.3** (`cc915d2` done) shipped `create_engine(url, read_only=True)`. GET handlers use this.
- **Story 2.2** shipped `Clock`, `new_task_id`, `new_event_id`, `new_request_id`, `new_idempotency_key`. registry-api uses these throughout.
- **Story 2.1** shipped `EventEnvelope.create()` + canonical JSON. registry-api calls these.

### Latest Tech Information

- **FastAPI 0.110+**: stable, async-native, OpenAPI auto. Pydantic v2 native.
- **uvicorn 0.30+**: standard ASGI server. `uvicorn.run(app, host, port)` programmatic API.
- **httpx + ASGITransport** for tests: `from httpx import AsyncClient, ASGITransport; client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")`. Async-native; supports lifespan via `LifespanManager` from `asgi-lifespan` package OR the built-in transport handles it.
- **`@asynccontextmanager` lifespan**: FastAPI 0.93+. Replaces deprecated `@app.on_event("startup")`.
- **`BaseHTTPMiddleware`**: from `starlette.middleware.base`. Subclass for class-based middlewares with constructor args.
- **`HTTPException` + RFC 7807**: FastAPI's `HTTPException` doesn't auto-emit problem+json; custom exception handler returns `JSONResponse(content=problem.model_dump(), status_code=..., media_type="application/problem+json")`.
- **`asgi-lifespan` (optional dev dep)**: enables proper lifespan execution in test client. May be needed for AC-1's startup-shutdown verification.

### References

- `epics.md` Story 2.9 (lines 826-845).
- `architecture.md` lines 41-42 (registry-api role), 122 (uvicorn), 155 (file layout), 213-218 (HTTP endpoints + middleware order).
- `prd.md` FR1 (812), FR4 (815), FR8 (819), FR23 (843), FR26 (850), FR28 (852), NFR-S7 (network trust).
- `2-1-event-envelope-schema-registry.md` — EventEnvelope.create.
- `2-3-registry-state-sqlite-schema.md` — create_engine read_only.
- `2-4-event-log-append-writer.md` — EventLogWriter.
- `2-5-event-log-subscriber-materializer.md` — TaskCreatedPayload, materializer.
- `2-7-idempotency-cache.md` — AC-12 deferral note.
- `2-8-clawhip-bridge-mcp-server.md` — sibling MCP path; not called by registry-api.

## Dev Agent Record

### Agent Model Used

**Claude Sonnet 4.6** (executor subagent). All 9 tasks delivered in one continuous pass. Lifespan + middleware integration via `asgi-lifespan` LifespanManager for tests; no significant deviations.

### Debug Log References

None of note. Lifespan-aware tests required `asgi-lifespan` dep + `LifespanManager` wrapper around the app for httpx AsyncClient integration; documented in test_app.py docstring.

### Completion Notes List

All 18 ACs satisfied.

- **AC-1 (build_app + lifespan):** factory signature `build_app(*, base_dir, db_url, clock) -> FastAPI` exact. Lifespan creates writer + read-only engine + session_maker on startup; closes both on shutdown. Stores all on `app.state`.
- **AC-2 (POST /v1/tasks):** `CreateTaskRequest` (frozen + strict + extra=forbid); generates task_id + event_id; calls `EventEnvelope.create()` with payload `TaskCreatedPayload`; calls `await writer.append(envelope)`; returns 201 with `{task_id, event_id, created_at}`.
- **AC-3 (GET /v1/tasks/{id}):** path regex enforces `^t-<uuidv7>$`; reads Task + dereferences last_event; returns full state with next_commands; 404 RFC 7807 on missing.
- **AC-4 (3 middlewares):** RequestIdMiddleware + IdempotencyKeyMiddleware + ActorIdMiddleware, all subclassing `BaseHTTPMiddleware` with Clock dependency injected via constructor. ActorIdMiddleware hardcodes "http-api" for Phase 1 (TODO comment for Story 6.1+).
- **AC-5 (RFC 7807 problem+json):** `ProblemDetails` Pydantic model + 2 exception handlers; sets `application/problem+json` content type; both HTTPException and RequestValidationError mapped.
- **AC-6 (entry point):** reads 4 env vars with documented defaults; `uvicorn.run(app, host=..., port=...)` programmatic API.
- **AC-7 (single-writer green):** `check_single_writer.py` scans registry-api → 0 violations. registry-api appends to JSONL only; SQLAlchemy session is read-only.
- **AC-8 (read-only engine):** lifespan creates engine via `create_engine(db_url, read_only=True)`. Empirical probe: hand-rolled INSERT through `app.state.engine` raises `OperationalError("readonly database")`.
- **AC-9 (deps + version):** fastapi>=0.110, uvicorn[standard]>=0.30, pydantic>=2.8, events>=0.3.0, registry-state>=0.5.0, asgi-lifespan added. Version 0.1.0 → 0.2.0.
- **AC-10 (re-exports):** `build_app` re-exported alongside legacy `hello()`. `__version__ = "0.2.0"`.
- **AC-11 (20 tests across 5 classes):** TestPostTasks (6) + TestGetTaskById (5) + TestMiddleware (4) + TestErrorHandlers (3) + TestEntryPoint (2) = 20 (within 18-22 target).
- **AC-12 (mypy strict):** 62 source files clean. No `Any`, `cast()`, `# type: ignore` in production.
- **AC-13 (single-writer green):** zero `# noqa: SW001`. CI gate clean.
- **AC-14 (scan-secrets):** clean.
- **AC-15 (check_event_registry green):** `task.created` is a literal in the POST handler; scanner accepts.
- **AC-16 (check_imports green):** services→packages + services→services both allowed.
- **AC-17 (regression):** `just test` 366+6 → **386+6** (+20 — exceeds spec's +18 minimum). mypy 55 → 62 files. `registry_api 0.2.0` on bootstrap. check-gates 3/3.
- **AC-18 (atomic commit):** `56e44b7 feat(registry-api): story 2.9 — POST /v1/tasks + GET /v1/tasks/{id} (FastAPI skeleton) · FR1 FR4 FR8 FR26`.

**Empirical probes (all PASSED):**
- POST round-trip → 201 + JSONL log contains the envelope.
- GET round-trip → 200 with full state from pre-seeded DB.
- Single-writer scan → 0 violations on registry-api.
- Read-only engine INSERT → OperationalError("readonly").
- 404 → application/problem+json with RFC 7807 body.

### File List

**New (7):**
- `services/registry-api/src/registry_api/app.py` — `build_app` factory + lifespan + middleware/exception/route registration.
- `services/registry-api/src/registry_api/routes/__init__.py` — empty marker.
- `services/registry-api/src/registry_api/routes/tasks.py` — POST + GET handlers + Pydantic models.
- `services/registry-api/src/registry_api/adapters/__init__.py` — empty marker.
- `services/registry-api/src/registry_api/adapters/middleware.py` — 3 middlewares.
- `services/registry-api/src/registry_api/adapters/errors.py` — RFC 7807 ProblemDetails + handlers.
- `services/registry-api/src/registry_api/test_app.py` — 20 tests across 5 classes.

**Modified (4):**
- `services/registry-api/pyproject.toml` — deps + version 0.1.0 → 0.2.0.
- `services/registry-api/src/registry_api/__init__.py` — re-exports + version.
- `services/registry-api/src/registry_api/__main__.py` — entry point with env vars + uvicorn.run.
- `uv.lock` — FastAPI + uvicorn + transitives locked.

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-25 | 0.1 | Initial story draft (create-story). |
| 2026-04-25 | 1.0 | Implementation complete. 20 new tests (366+6 → **386+6**). `registry_api` 0.1.0 → 0.2.0. mypy scope 55 → 62 files. **First HTTP-tier ingress + read surface**. registry-api emits events DIRECTLY via `EventLogWriter` (NOT through clawhip-bridge MCP — that's for agent consumers); FR26 single-writer preserved at SQLite layer (only the materializer subscriber writes to SQLite tables). RFC 7807 problem+json error envelopes; 3 middlewares (RequestId/IdempotencyKey/ActorId placeholder); read-only SQLite engine for GET handlers (Story 2.3 pattern). Idempotency-Key header is read into request.state but dedup logic deferred to Story 3.6 per Story 2.7's AC-12. All 5 empirical probes PASSED (POST round-trip, GET round-trip, single-writer scan, read-only engine INSERT-rejection, 404 RFC 7807). Status → review. Scaffold commit: `56e44b7`. |
