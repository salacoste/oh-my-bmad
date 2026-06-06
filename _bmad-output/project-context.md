---
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-05-15'
sections_completed:
  - technology_stack
  - language_rules
  - framework_rules
  - testing_rules
  - quality_style_rules
  - workflow_rules
  - critical_digest
status: 'complete'
rule_count: 386
section_count: 7
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

**Runtime**
- Python 3.12 (locked — `requires-python = ">=3.12"`, no broader).
- Node.js is present only inside the Claude Code CLI worker subprocess. Never import or invoke Node.js APIs from Python services.

**Build / Workspace**
- `uv ≥ 0.5` with `uv_build` backend. Workspace members under `services/*`, `packages/*`, `mcp-servers/*`.
- `just ≥ 1.14` is the single source of truth for operator recipes.
- Docker Engine ≥ 24, Docker Compose v2.24+.

**Core libraries** (exact versions in `uv.lock` — do not duplicate here)
- FastAPI — HTTP surface.
- aiogram v3 — Telegram bot (async webhook).
- aiosqlite + Alembic — SQLite WAL store; migrations are additive-only within a major.
- mcp — stdio transport only. Do not add SSE or HTTP transport; that boundary is an explicit ADR-gated decision. Code that opens a network socket for MCP is a hard rejection.
- pydantic, structlog, httpx, uvicorn.
- anthropic SDK — never import directly in platform services. All model output must be produced by constructing a task for the Claude Code worker via the event spine. ADR required before touching the SDK in service code.

**Dev tooling**
- ruff (line-length 100, py312 target).
- mypy `--strict`. `[mypy] mypy_path` must be a single comma-separated string, **not** a TOML array — multi-line silently drops entries 2..N.
- pytest with `asyncio_mode = "strict"`. Markers `slow`, `separability`, `crash`, `idempotency`, `integration`, `contract`, `migrator`, `fuzz`.
- hypothesis (NFR-S5 fuzz; see Category 7 for `@settings` discipline).
- pre-commit: `secret-hygiene-precommit` + `secret-hygiene-commit-msg`.

**Rules for agents**
- **Version pinning.** No major-version bumps without an ADR. Minor bumps to `aiogram`, `mcp`, `anthropic`, or upstream-shim deps require a compatibility-check commit (full suite green against the candidate). Patch bumps may merge if CI is green.
- **`uv sync` variants** (the wrong one breaks tests):
  - Day-to-day dev: `uv sync --frozen --all-packages`.
  - Docker image build: `uv sync --frozen --no-dev`.
  - Service-local iteration: `uv sync --package <member>`.
  - **Never** bare `uv sync` in scripts; `--no-dev` strips test-only deps (`asgi-lifespan`, `sniffio`) and breaks tests. See `docs/development.md`.
- **Workspace dep declaration** — adding a new shared package requires edits in **exactly two** places:
  1. Root `pyproject.toml`: `[tool.uv.sources]` AND `[project.dependencies]`.
  2. Each consuming service's `pyproject.toml`: `[project.dependencies]`.
  Omitting either causes `uv sync` to succeed but imports to fail at runtime. Verify with `uv sync --all-packages` + `uv tree` showing the dep under every consumer.
- **New runtime deps** require: `uv lock` clean, `just bootstrap-verify` green (13 workspace-member imports), and pre-commit (`uv run secret-hygiene-precommit`) clean.
- **MCP triple-naming** is load-bearing. Directory = `mcp-servers/<x>/` (kebab, no suffix); project `name` = `<x>-mcp`; Python module = `<x>_mcp` (snake, derived by `uv_build`). Never rename the import root by hand. Services and packages follow the simpler 1:1 kebab↔snake rule.
- **Scaffold `__main__.py`** is the correct code until the owning story replaces it. Don't add business logic on top of `signal.pause()`. See `docs/exceptions.md` for the replacement-story map.

## Critical Implementation Rules

### Language-Specific Rules (Python 3.12)

**Imports & module layout**
- `from __future__ import annotations` at the top of every module — defers evaluation so Pydantic + forward refs resolve cleanly.
- Public packages ship `src/<module>/py.typed`. Without it, downstream `--strict` resolution silently loses signatures.
- `__init__.py` exports `__version__: str`. `just bootstrap-verify` greps for it across all 13 workspace members.
- Absolute imports across workspace members only. Relative imports across packages are banned.
- **Service separability is enforced, not advisory.** `scripts/checks/check_imports.py` is the gate:
  - `services.<A>` may NOT import `services.<B>.*` under any circumstance.
  - `services.*` may import `packages.*` but never another service's package-private modules.
  - `packages.*` may NOT import `services.*` or `mcp-servers.*`.
  - `mcp-servers/<x>/` may import `packages.*` only, plus the declared public API of at most one service.
  - `upstream/*` is accessed only through its adapter shim (`upstream/<fork>/adapter.py`). Direct imports of vendored internals are rejected.
  - Permitted exceptions use the `# noqa: IMP001` tag with an ADR reference in the comment.

**Async**
- `pytest-asyncio` runs in strict mode. Every async test carries `@pytest.mark.asyncio` explicitly.
- Never block the loop: no `time.sleep`, sync subprocess, or sync HTTP. Use `asyncio.sleep`, `asyncio.create_subprocess_exec`, `httpx.AsyncClient`.
- Banned in production code: `asyncio.sleep(0)` (yield-point hack), module-level `asyncio.get_event_loop()`. Use `asyncio.get_running_loop()` inside coroutines only.
- Tasks that must NOT inherit caller context use `asyncio.create_task(coro(), context=contextvars.copy_context())` with explicit reset.
- Async generators return `AsyncIterator[T]` (not `AsyncGenerator[T, None]`) and handle `GeneratorExit` in `finally`. Consumers wrap with `contextlib.aclosing(...)`.
- One async test backend per repo (pytest-asyncio). Mixing with `anyio` is banned — produces intermittent `RuntimeError: no running event loop`.

**Typing**
- mypy `--strict` is non-negotiable. Every `# type: ignore` carries the specific code AND a one-line WHY: `# type: ignore[arg-type]  # SQLAlchemy dynamic attr`. Bare `# type: ignore` is rejected.
- Modern PEP 585 / 604 syntax: `list[str]`, `int | None`. Never `List[str]` / `Optional[int]`.
- `Any` requires PR-description justification; reviewers may reject.
- `TYPE_CHECKING` blocks are for circular-import breakage only — not a backdoor around strict mode.
- Service constructors accept `Protocol` types, not concrete classes. No module-level singletons for mutable state.

**Pydantic v2**
- Event payloads: `model_config = ConfigDict(frozen=True, strict=True)`. `strict=True` blocks silent str→int coercion that masks test bugs.
- Collection fields on frozen models use `tuple` / `frozenset`, never `list` / `set` / `dict` (mutable defaults break under `pytest-xdist`).
- Use `model_validate(...)` / `TypeAdapter(T).validate_python(...)`. Never v1 `parse_obj` / `parse_obj_as`.
- Access fields via `model_fields`, never `__fields__`. Call `model_rebuild()` when a model has forward refs.

**Error handling**
- Domain errors inherit from a service-local `<svc>Error` base; eventually that base will inherit from a workspace-level `BmadError` root. If the root doesn't exist yet and you need cross-service catch semantics, file an ADR first.
- Service-boundary `except` emits a typed `*.failed` event with `parent_event_id` set. The emit itself must be non-raising — if it fails, the original exception propagates.
- Async-context-manager cleanup goes in `finally`, not `except` (crash-injection harness asserts teardown runs on `CancelledError`).
- `assert` is for invariants only. CI may run with `python -O` — asserts disappear.

**Time & IDs**
- `events.FrozenClock` and `events.new_uuid7()` are the *only* sanctioned clock and ID sources in production code paths. Never `datetime.utcnow()` or `uuid.uuid4()`.
- A test that exercises production code without the autouse `fixed_clock` / `seeded_uuid7` fixtures is a defect.
- Monotonic timing: `time.monotonic_ns()`, stored as `emitted_at_monotonic_ns` on the envelope. Prefer injecting a `MonotonicClock` protocol when timing is load-bearing for tests.
- Idempotency: use `packages/idempotency`. Triggering event's UUIDv7 is threaded as the idempotency key into every downstream write. Don't roll your own dedupe.

**Logging & observability**
- `structlog.get_logger(__name__)` only. Stdlib `logging.getLogger` is banned — the secret-hygiene sanitizer is wired into the structlog processor chain only.
- Every entry point (HTTP, event handler, MCP tool, background task) calls `structlog.contextvars.clear_contextvars()` then binds `trace_id` + `parent_event_id` with `bind_contextvars(...)`. Missing inbound `trace_id` mints a new UUIDv7 and logs at WARNING.
- Reasoning breadcrumbs are structured events with keys `reasoning_step`, `model_id`, `token_est`. Never `print()` or ad-hoc strings.
- Log keys are snake_case. Never log secrets, tokens, or raw request bodies — the sanitizer is a safety net, not a license for negligence.

**Test discipline**
- Co-located `test_*.py` is for unit tests only. Anything needing a running event store goes under `tests/integration/`. No `sys.path` hacks.
- `tmp_path` for filesystem access in tests — never `tempfile.mkdtemp()`.
- Conftest fixture overrides in a subdir shadow parents silently — require `# noqa: F811` + a comment citing the parent fixture.
- `hypothesis` `suppress_health_check=[...]` requires a same-line justification comment.

### Framework-Specific Rules

**FastAPI (registry-api only)**
- Dependency lives only on `services/registry-api/`. No other service declares `fastapi`.
- Router layout: one file per resource at `services/registry-api/src/registry_api/v1/<resource>.py`. Each file owns its `APIRouter`; routers are collected and mounted under `/v1` in `app.py`. Router prefix owns `/v1`; routes own `/<resource>` — never double-prefix.
- Versions are additive. `/v1` semantics frozen once shipped; new versions live at `/v2/...`.
- Pydantic v2 models in/out. Set `response_model_exclude_unset=True` on routers or clients see null-flooded payloads.
- POST creates return 201; never default to 200.
- DI via `Depends(...)` only. No `Request.app.state` reads from handlers. `Depends(get_db)` returns `AsyncSession`; sync `Session` inside an async handler blocks the loop.
- `BackgroundTasks` is banned for anything with retry/durability semantics — emit a typed event onto the spine instead.
- Use `lifespan` (not deprecated `@app.on_event`) for startup/shutdown of DB pool, MCP clients, structlog config. `asgi-lifespan` test harness depends on this.
- Trace context: `trace_id` pulled from `X-Trace-Id` header (generate UUIDv7 if absent, log at WARNING); `parent_event_id` pulled from `X-Parent-Event-Id` header (None if absent — never fabricate). Bind both to structlog at middleware before business logic.
- Every mutating command handler emits exactly one typed `*.requested` event with `parent_event_id` set before returning 2xx. Read-only handlers are exempt but still bind trace context.
- Single registered exception handler maps `<svc>Error` → `{error_id, error_code, message, trace_id}`. Never let an exception escape to FastAPI's default 500. A parametrized contract test asserts this for every registered `<svc>Error` subclass.
- `/healthz`, `/readyz`, `/v1/health` (FR17) emit zero log lines under normal operation; a pytest assertion captures `structlog` output during the call.
- **TestClient is banned.** Synchronous TestClient runs the app in a thread with a different event loop than async fixtures. Use `httpx.AsyncClient` + `asgi-lifespan` exclusively.

**aiogram v3 (telegram-gateway only)**
- Async only. Handlers registered via `Router` + decorator (`@router.message(...)`). Deprecated `Dispatcher.register_*` is banned.
- Filters use `F.<field> == value`; `lambda` filters are not picklable with persistent FSM storage.
- FSM storage backend is **explicit** in code — `RedisStorage` for prod, `MemoryStorage` for local dev only. Default `MemoryStorage` silently loses state on restart.
- Production = webhook. Long-polling is local-dev only, gated behind `OMB_TELEGRAM_TRANSPORT=poll`.
- `AllowlistMiddleware` enforces actor allowlist before any handler runs. Unauthorized chat IDs are dropped with a single `secret.access_denied` audit event — never echoed back to the user. Deny-path test is **mandatory**.
- Templates live in `docs/message-design.md`; never inline Telegram-specific Markdown in handler code. Character-budget tests assert template-registry usage.
- Inbound idempotency key = `f"tg:{update_id}"`; threaded into the command envelope. Replay test asserts exactly one side-effect for a duplicate `update_id`.
- Allowlist middleware injects `trace_id = f"tg:{update_id}"` + `parent_event_id = None` into structlog context before any handler runs.
- Mutating handlers emit one typed event onto the spine; fire-and-forget to the event log, never await ACK in the handler.
- Tests use `MockBot`/`RecordBot` only — live polling leaks open connections and hangs CI. Each FSM test gets a fresh `MemoryStorage` + `Dispatcher` instance.
- SIGTERM: stop polling/webhook, `asyncio.gather(..., timeout=5)` for pending handlers, `await dp.storage.close()`. Unfinished updates are logged with `update_id` for replay; **not** retried automatically.

**SQLAlchemy 2.0 async + Alembic (registry-state only)**
- SQLite + WAL only in Phase 1. Connection URL fixed by `REGISTRY_STATE_DB_URL`.
- Single-writer invariant (FR26): only `registry-state` opens the DB for writes. No other service declares SQLAlchemy or holds an `AsyncSession`.
- The append-only JSONL event log is opened for write only by the `EventLogWriter` class in `registry-state`. Everyone else reads via `EventLogReader`.
- Snapshot materialization is the only path allowed to hold a write transaction >1s. All other writes complete inside the per-request timeout budget.
- 2.0 typed style: `DeclarativeBase`, `Mapped[T]`, `mapped_column(...)`. Legacy `Column()` + `declarative_base()` is banned. `session.query()` is banned — undefined behavior in async; always `select()` + `session.execute()`.
- Use `.scalars().all()` / `.scalar_one()` / `.scalar_one_or_none()`. Never `.scalar()` on multi-row.
- `AsyncSession(expire_on_commit=False)` is mandatory. The default triggers lazy-load after commit in async context → `MissingGreenlet`.
- All relationships declare `lazy="raise"`; eager-load explicitly with `selectinload(...)`. No accidental N+1.
- Alembic `env.py` uses the **async** pattern (`asyncio.run(run_async_migrations())` + `connectable = async_engine`). Sync `env.py` is a code-review reject.
- SQLite column/constraint operations require `with op.batch_alter_table(...)`; bare `op.add_column`/`drop_column` raises `OperationalError`.
- Migrations are **additive within a major**. A pytest-driven migration linter walks every migration file and fails CI on `DROP COLUMN`, `DROP TABLE`, `ALTER COLUMN` (type change), or `RENAME`. Two-phase destructive plans require an ADR.
- Date-prefixed migration filenames (`YYYY-MM-DD_NNNN_<desc>.py`) — `N999` ruff rule is suppressed for these (see `ruff.toml`).
- `alembic downgrade -1` smoke test runs in CI against a known schema state.
- A connection-open test asserts `PRAGMA journal_mode = wal` — one-liner that catches env misconfig before it becomes data loss.
- SIGTERM: `PRAGMA wal_checkpoint(FULL)`, then `await engine.dispose()`. Open sessions at shutdown roll back, never commit. 8s total shutdown budget.

**MCP servers (stdio)**
- One MCP server = one workspace member under `mcp-servers/<x>/`. Triple-naming from Category 1.
- All servers use `mcp.server.stdio.stdio_server()` — no HTTP/SSE transport. Imports of `mcp.server.sse` / `mcp.server.streamable_http` are rejected (static-analysis test in CI).
- Server entrypoint runs via `anyio.run(server.run_stdio())`. `asyncio.run` breaks on some backends.
- Tool handlers are pure async functions: pydantic-validated input, pydantic-modelled output. No raw dicts. One source of truth for the schema — either the pydantic model OR the `@tool`-inferred signature, never both (they drift).
- Tool errors raise `ToolError(...)` for structured client-visible errors. `raise ValueError(...)` produces untyped errors.
- Capability-tier enforcement is a middleware/decorator at every tool boundary, **shared across all MCP servers**. The handler body is logic-only. Mandatory tests per boundary: deny-path, default-deny (no claim → reject, not route to tier 0), escalation (claimed-tier > granted-tier → reject). Each boundary has recorded contract fixtures.
- All I/O (DB, HTTP, event-log writes) flows through injected clients. MCP tools that write go via the registry-state HTTP client — never touch the DB or event-log file directly.
- No `anthropic` SDK imports (Category 1 rule applies doubly). Static-analysis test in CI greps the MCP tree.
- Each tool invocation takes `caller_trace_id` and `caller_parent_event_id` as explicit arguments — not ambient context. Mutating tools emit one typed event onto the spine.
- Test harness: buffer the full stdio response before asserting (frame-aware). Fixture teardown does `process.wait(timeout=2)` + SIGKILL fallback to avoid zombie fds.
- SIGTERM / EOF on stdin = shutdown signal. Complete in-flight tool calls (≤5s), exit 0. Calls still running at the cap are cancelled with `tool_name` + `caller_trace_id` logged.

**structlog (framework-specific hooks)**
- General discipline lives in Category 2. Framework-specific hook locations:
  - FastAPI: a Starlette middleware binds `trace_id`, `parent_event_id`, `route` to the structlog context at the start of each request.
  - aiogram: `AllowlistMiddleware` binds before any handler runs.
  - MCP: the tool-boundary middleware binds before the handler.
- Processor chain order is **load-bearing**: `merge_contextvars` → secret-hygiene `redact_secrets` → renderer. A snapshot test of `structlog.get_config()["processors"]` against a pinned fixture catches reordering.
- Per-test isolation requires a `reset_structlog` autouse fixture in `tests/conftest.py`.

**hypothesis**
- Every `@given` test carries explicit `@settings(max_examples=N)`.
- PR gate: N ≈ 200 for business logic. Combined heavy fuzz (N ≥ 500, NFR-S5 combined 10K) runs only behind `@pytest.mark.slow` for nightly — never on every PR.
- `st.text()` and `st.binary()` must be bounded (`min_size`, `max_size`). Unbounded strategies banned.
- Hypothesis DB at `.hypothesis/`; CI seeds `HYPOTHESIS_DATABASE_DIR` so a failing example replays deterministically.
- Fuzz tests carry `@pytest.mark.fuzz`. Per-strategy fuzz runs in the PR gate; combined 10K runs only via `@pytest.mark.slow` nightly job.
- `suppress_health_check=[...]` requires a same-line justification comment.

**docker-compose**
- Services alphabetized in `docker-compose.yml`. Healthchecks mandatory; a service without one breaks the 6/6-healthy gate.
- `depends_on` MUST use the mapping form with `condition: service_healthy`. Bare list form (`depends_on: [foo]`) is a start-order hint only, NOT a health gate, and is rejected in review.
- `registry-state` reaches healthy first; its healthcheck validates the WAL file exists and `/readyz` returns 200.
- `restart: unless-stopped` for all long-running services; `console-cli` is the only one with `restart: "no"` (it's not in compose `up`).
- Environment from `.env` only; no inline `environment:` blocks except non-secret defaults documented in `.env.example`.
- Named volumes only (`oh-my-bmad-data`). Bind mounts allowed only in `docker-compose.macos.yml`.
- MCP stdio servers MUST NOT appear in `docker-compose.yml` — they are orchestrator-spawned subprocesses. A compose entry for them is rejected in review.
- `console-cli` is intentionally not in `docker compose up` — see `README.md`. Don't "fix" this.

### Testing Rules

**Test-tree layout (load-bearing)**
- Unit tests are **co-located** with their subject: `packages/<pkg>/src/<module>/test_*.py`, `services/<svc>/src/<module>/test_*.py`, `mcp-servers/<srv>/src/<module>/test_*.py`. Co-located = unit only (no DB, no event store, no subprocess).
- Cross-service test trees under `tests/` are mandatory locations for their type:
  - `tests/separability/` — S-1/S-2/S-3 adapter-swap (NFR architectural).
  - `tests/crash-injection/` — process-crash + recovery (NFR-R2).
  - `tests/idempotency/` — duplicate-event UUIDv7-key replay.
  - `tests/integration/` — cross-service journey tests.
  - `tests/contract/` — upstream-adapter contract fixtures.
  - `tests/migrator/` — event-log schema migrator.
  - `tests/replay/` — projector determinism (byte-identical snapshot from frozen event-log fixture).
  - `tests/arch/` — static import-graph invariants (PR gate).
  - `tests/unit/invariants/` — structural proofs (envelope immutability, capability deny shapes); NO I/O.
  - `tests/e2e/` — when added, hermetic Telegram → gateway → registry round-trip; runs nightly + release, not PR gate.
  - `tests/perf/` — NFR-P3 assertions via `pytest-benchmark` on a fixed runner; nightly only.
  - `tests/fixtures/` — shared payloads (NOT pytest test files).
- New cross-service category requires: tree under `tests/`, registered marker, `docs/testing-guide.md` update.

**Markers (single source of truth = `pyproject.toml`)**
- Registered: `slow`, `separability`, `crash`, `idempotency`, `integration`, `contract`, `migrator`, `fuzz`. Add `e2e`, `perf`, `security` as those test categories land.
- `--strict-markers` + `--import-mode=importlib` + `-ra` are fixed in `addopts`. Don't override locally.
- `python_files = test_*.py`. Files named `feature_test.py` (Go habit) are silently uncollected — declare this guard explicitly.

**`tests/__init__.py`**
- Omit in importlib mode. Add only in mypy-re-enabled subtrees: `tests/crash_injection/`, `tests/idempotency/`, `tests/migrator/`, `tests/separability/`, `tests/fixtures/null_orchestrator/`.

**Test-only deps**
- Service-specific test deps live in service-level `[project.optional-dependencies] test = [...]` (e.g., `asgi-lifespan` in `services/registry-api/`). Root `[dependency-groups] dev` holds only cross-cutting tools (pytest, hypothesis, ruff, mypy, pre-commit, coverage).
- CI installs via `uv sync --frozen --all-packages` (see Category 1) plus per-service `--extra test`.

**Canonical cross-cutting fixtures (`tests/conftest.py`)**
- `fixed_clock` — `events.FrozenClock` seeded to `FROZEN_EPOCH`. Use in every test that exercises clock-dependent production code.
- `seeded_uuid7` — deterministic UUIDv7 factory.
- `capture_structlog` — installs the secret-hygiene sanitizer ahead of a list-capture terminal processor; yields captured records (for FR43 / NFR-S1 assertions).
- Service-level `conftest.py` MAY re-export these but MUST NOT redefine. Name collision = nearest wins silently — the #1 AI-agent mistake. Override pattern is `# noqa: F811` + an explicit re-export comment.

**Fixture authoring**
- Fixtures used by >1 test live in a `conftest.py`, never in `test_*.py`.
- `tmp_path_factory.mktemp("label")` for session-scoped temp roots. Mixing `tmp_path` (function-scoped) into session fixtures = collection-time scope error.
- `monkeypatch` is the sanctioned patching tool. `unittest.mock.patch` as a decorator on async tests breaks under strict asyncio mode.
- httpx mocking via `respx` only. Manual `httpx.MockTransport` wiring is rejected — agents forget to assert routes were hit.

**Async test discipline**
- `asyncio_mode = "strict"` is non-negotiable; every async test carries explicit `@pytest.mark.asyncio`.
- Event-loop scope: function (default). Module/session scope = cross-test bleed; never widen to silence teardown errors.
- One async backend per repo (pytest-asyncio); `anyio` is banned as a test backend.
- FastAPI: `httpx.AsyncClient` + `asgi-lifespan` exclusively. `TestClient` is banned (different event loop than fixtures).
- aiogram: `MockBot` / `RecordBot`. Each FSM test gets a fresh `MemoryStorage` + `Dispatcher`. No live polling.
- MCP stdio: harness buffers the full response before asserting (frame-aware). Teardown does `process.wait(timeout=2)` + SIGKILL fallback.
- `AsyncSession(expire_on_commit=False)` mandatory on test fixtures.

**Unit vs integration boundary**
- Co-located = no I/O outside the function under test. Importing `httpx` / `aiosqlite` / `subprocess` / `aiofiles` from a co-located test moves it to `tests/integration/`.
- Cross-service journey = `tests/integration/`. Real Compose stack or per-service fakes; do not mock at the type-boundary (mocking pydantic models defeats contract testing).

**Architectural invariants as tests (`tests/arch/` — static import-graph)**
- Runs on PR gate via `scripts/checks/check_imports.py` plus structural-analysis tests; required status check.
- Separability: `services.<A>` never imports `services.<B>.*`. Closure is computed statically — not a hand-curated list.
- Single-writer fence: only `registry-state` writer modules contain `session.add` / `session.execute(...write...)` / `commit()` call-sites. AST or grep scan over `src/`.
- Upstream-fork boundary: direct imports of vendored internals from outside the adapter shim → hard CI fail.
- MCP transport: imports of `mcp.server.sse` / `mcp.server.streamable_http` from `mcp-servers/` → hard CI fail.
- No `anthropic` SDK imports outside `worker-wrapper/`.

**Architectural invariants as unit proofs (`tests/unit/invariants/`)**
- Envelope immutability: post-construction field assignment raises `ValidationError`; deserialization produces a new frozen copy, never a reference.
- Capability-tier deny shapes: deny envelope structure is asserted directly, independent of any specific tool boundary.

**Crash-injection (NFR-R2)**
- Every service entry point with an event-emission side effect has a crash-injection test asserting:
  1. Recovery completes within the documented RTO.
  2. No duplicate side-effect (idempotency key wins).
  3. Originating event remains in the log; partial writes are detected and rejected.
- Recovery assertions use the injected clock; never `asyncio.sleep`.

**Idempotency**
- Every command handler has a `tests/idempotency/` test driving the same `(idempotency_key, payload)` twice; assert one side-effect AND identical response.
- Idempotency key is always the triggering event's UUIDv7 (Category 2 rule). Test inputs parametrize `update_id` / `command_id` explicitly — never auto-increment.

**Contract tests (`tests/contract/`)**
- Recorded stdin/stdout fixtures per adapter under `tests/contract/fixtures/<adapter>/`. Recording workflow: `just sync-upstream <name>` (see `docs/testing-guide.md`).
- Each contract asserts: well-formed request → well-formed response; malformed request (truncated JSON, unknown method, oversized payload) → structured error, never a crash or silence.
- Forward-compatibility matrix on contract fixtures: consumer at vN reads an event emitted at vN+1 without corrupting known fields.

**Replay correctness (`tests/replay/`)**
- Frozen event-log fixture replayed through the projector produces a byte-identical state snapshot. Mandatory on every projector / event-handler change.

**Migrator (`tests/migrator/`)**
- `migrate(v_n) == migrate(migrate(v_n))` (idempotent migration) per version step.
- Per-check named assertions (not one bool): `DROP COLUMN`, `DROP TABLE`, `ALTER COLUMN (type change)`, `ADD COLUMN NOT NULL without DEFAULT`. Reports must be actionable.
- `just migrator-test-additive` runs the trivial v1.0.0→v1.0.1 fixture; full migrator suite runs on lock changes.

**Capability-tier deny-path (CRITICAL)**
- Per boundary: deny-path test, default-deny test (no claim → reject, not tier 0), escalation test (claimed-tier > granted-tier → reject). All three required.
- `@pytest.mark.security` (register marker when adopted). Security-marked tests are NEVER skipped under `-k` / `--ignore-glob`. PRs reducing security test count require architect sign-off.

**Fuzz / Hypothesis**
- Per Category 3: `@settings(max_examples=N)`, bounded strategies, `@pytest.mark.fuzz`. Combined 10K-example NFR-S5 run lives behind `@pytest.mark.slow` for nightly only.

**Performance (NFR-P3)**
- `tests/perf/` with `@pytest.mark.perf` + `@pytest.mark.slow`. Snapshot-replay latency asserted via `pytest-benchmark`; threshold T is set in `tests/perf/constants.py` and updated only by architect decision citing measurement evidence.
- Runs on a fixed CI runner size only — varied hardware = meaningless results.

**Test data discipline**
- All test data is synthetic. Production-derived fixtures are prohibited.
- Telegram user IDs use the reserved test range (`user_id ∈ [1, 999]`, `chat_id ∈ [-999, -1]`). Outside-range values fail a pre-commit check.
- Bot-token-pattern regex (`^\d{8,10}:[A-Za-z0-9_-]{35}$`) is grep-checked in CI; any match in fixtures fails.

**Determinism & flakiness**
- `pytest-randomly` is ON. Seed is logged at the top of every run; reproduce with `--randomly-seed=<n>`.
- `pytest-ordering` (`@pytest.mark.run(order=...)`) is banned. Order-dependent passes are shared-state bugs — fix the bug.
- `pytest-rerunfailures` blanket re-runs are banned. The only escape is an explicit `@pytest.mark.flaky(reruns=2, reason="...")` with the named reason.
- **Flake quarantine SLA.** A test failing on a green-code commit ≥2× in 7 days is quarantined within 24h via `@pytest.mark.skip(reason="FLAKE-<id> ... <date> ... issue #<n>")`. Owner = last substantive author (git blame). Quarantined-and-unfixed for 14d → delete + replacement test in the same PR.

**Coverage**
- `pytest-cov` + `coverage.py`. Config lives in `pyproject.toml` `[tool.coverage.*]` — no separate `.coveragerc`.
- `branch = true`. `source = ["packages", "services", "mcp-servers"]`. `omit = ["*/migrations/*", "*/conftest.py", "*/__main__.py", "tests/*"]`.
- `addopts` includes `--cov --cov-branch --cov-fail-under=85`. Coverage without `--cov-fail-under` is theater.
- Exemptions: `# pragma: no cover` requires a co-located one-line rationale.
- PRs that reduce branch coverage by >2pp trigger a reviewer-annotation via diff-cover.

**Pyramid ratchet**
- Target distribution: unit (incl. invariants) ~40–60%, integration ~30%, contract+crash+E2E+perf ≤25%.
- `pytest --co -q` parsed in CI to count tests by marker tier. PR that pushes integration% above 30% is blocked until unit tests are added or architect overrides with a `pyramid-exception` PR label.

**CI gate interpretation**
- `just test` = `pytest -m "not slow"` → the PR-merge bar.
- `just test-slow` = full matrix (nightly).
- `just test-contract` = run after every `just sync-upstream <name>`.
- `main` test failures are P0. Fix or revert; don't pile new work on top.
- CI uses `-x`; local dev uses `--maxfail=3`. Set explicitly, not by default.

**Test naming and structure**
- File `test_*.py`. Function `test_<feature>_<scenario>_<expected>` — three underscore-separated segments. BDD-style `test_should_*`, `test_it_*`, `test_that_*` is **banned**.
- Class-grouped tests only when ≥3 tests share fixtures.
- One assertion *concept* per test (multi-assert is OK when asserting one behavior across state inspections).
- `@pytest.mark.parametrize` requires `ids=[...]` when the input matrix is non-obvious; auto-generated `[0], [1]` ids are rejected.
- Integration / contract / crash-injection / replay / E2E / perf tests SHOULD carry a one-line docstring naming the FR/NFR they cover. Co-located unit tests don't require docstrings (ruff `D` is off for `tests/**`).

### Code Quality & Style Rules

**Linting & formatting (single source of truth = `ruff.toml`)**
- `ruff` is the only formatter and the only linter. No black, no isort, no flake8. Pin: `ruff>=0.4` (standalone `ruff.toml` uses `[lint]` directly; do not migrate to embedded `[tool.ruff.lint]` under `pyproject.toml`).
- Line length 100. Target `py312`. Never override per-file.
- Selected rule families: `E`, `F`, `I`, `UP`, `B`, `SIM`, `N`. **Plus** the bandit family `S` (see Security gates below). Adding a family requires updating `ruff.toml` AND running the new rule across the whole tree in the same PR — never half-on.
- `[lint.isort]` declares `known-first-party = [...]` listing every workspace module so workspace imports don't sort as third-party (e.g., `events`, `idempotency`, `secret_hygiene`, `registry_api`, `registry_state`, `telegram_gateway`, `console_cli`, `orchestrator_adapter`, `worker_wrapper`, `clawhip_daemon`, `task_registry_mcp`, `session_registry_mcp`, `clawhip_bridge_mcp`).
- Per-file ignores live in `ruff.toml [lint.per-file-ignores]`. Don't sprinkle `# noqa` ad hoc.
- `# noqa: <CODE>` MUST cite the specific code(s) — stacked codes are comma-separated (`# noqa: E501,F401`). Bare `# noqa` is rejected. Pair with a one-line WHY comment when the code isn't self-evident (`IMP001` requires an ADR reference; see Category 2).

**Security gates (ruff `S` / bandit)**
- `S101` `assert` is banned in production code (strips under `python -O`; use explicit `if not ...: raise`).
- `T201` / `T203` `print()` / `pprint()` are banned in non-test paths — covert channel + log-hygiene violation. Use `structlog` (Categories 2 & 3).
- `S104` hardcoded `0.0.0.0` / `127.0.0.1` bind addresses banned outside test fixtures.
- `S311` — never use `random` for tokens, nonces, session IDs. Use `secrets`.
- `E722` / `BLE001` — bare `except:` / `except Exception: pass` are banned. Exception handling must follow Category 2 (typed `*.failed` event at the service boundary).
- TODO/FIXME format: `TODO(#<issue>): ...` or `FIXME(#<issue>): ...`. Anonymous TODOs and bare-author TODOs are rejected by a grep gate.

**Naming conventions**
- Files / directories: kebab-case (`registry-state/`, `secret-hygiene/`). Workspace-member name = directory name.
- Python modules: snake_case, derived from project `name` via `uv_build` (`-` → `_`). Never manually rename.
- MCP triple-naming (Category 1) is load-bearing.
- Classes: `PascalCase`. Functions / variables: `snake_case`. Module-level constants: `UPPER_SNAKE`.
- Pydantic model names are PascalCase **noun phrases**:
  - ✓ `UserCreatedEvent`, `TaskRequested`, `SessionClosed`, `PaymentRequest`, `ConfigSnapshot`
  - ✗ `CreateUser`, `HandlePayment`, `IsValid` (those are verb phrases or predicates)
- Event types end with the past-tense verb (`*Requested`, `*Closed`, `*Failed`, `*Crashed`). Command types end with `Command` when ambiguity would otherwise exist (`CloseSessionCommand`).
- Test functions: `test_<feature>_<scenario>_<expected>` (Category 4). BDD prefixes banned.
- File-name suffix: `test_*.py`. `_test.py` (Go habit) is silently uncollected.
- Alembic migrations: date-prefixed (`YYYY-MM-DD_NNNN_<desc>.py`). `N999` suppressed only for `services/*/src/*/migrations/versions/*.py`.

**File and folder structure**
- Workspace members: `services/<svc>/`, `packages/<pkg>/`, `mcp-servers/<srv>/`.
- Canonical layout per member: `pyproject.toml`, optional `Dockerfile` (services + mcp-servers), `src/<module>/__init__.py` (exports `__version__`), `src/<module>/__main__.py` (scaffold pattern until owning story lands; see `docs/exceptions.md`), `src/<module>/py.typed`, `src/<module>/test_*.py` (co-located unit tests).
- Public API surface is exported from `src/<module>/__init__.py` via explicit `__all__`. Re-exports require `__all__` — `# noqa: F401` is the fallback only when `__all__` is impractical.
- Internal modules are package-private by convention; `__all__` MUST NOT export them.
- New top-level directories require updating `ruff.toml extend-exclude` (if not project code) AND `pyproject.toml norecursedirs`.
- **File length is a discipline rule, not a tool gate.** ~500 lines is the soft warning bell during review; large files signal a missing abstraction boundary, not a style violation. No hard cap.

**Documentation requirements**
- Module docstrings: required on every public module (one-paragraph purpose + the FR/NFR or story it implements). Scaffold modules carry a brief "replaced by Story X.Y" note (see `docs/exceptions.md`).
- Public function / class docstrings: one-sentence summary minimum. `Args` / `Returns` / `Raises` sections only when the contract isn't obvious from types.
- Private functions (`_prefixed`): no docstring requirement.
- **Inline comments — decision rule:** omit if the comment restates the code in English. Add one only when intent is not expressible in code (non-obvious algorithm choice, regulatory constraint, known footgun, ADR reference).
  - ✗ `# increment counter` followed by `counter += 1`
  - ✓ `# 200ms cap = TG webhook timeout per Telegram Bot API §6.3`
- ADR references in code use the form `# See ADR-NNNN` matching `docs/adr/NNNN-<slug>.md`. **If a pre-commit hook does not yet validate that the referenced ADR exists with `status: accepted`, this rule is discipline-only — treat as such until the gate is wired.**

**Pydantic conventions (delta from Category 2)**
- Public DTO models add `model_config = ConfigDict(extra="forbid")`. Silent-drop of unknown fields is banned.
- Use `Annotated[T, Field(...)]` **only** when attaching validators or metadata. Plain `Field(default=...)` for simple defaults is fine; don't bloat every field with `Annotated`.
- (Frozen/strict on event payloads is in Category 2 — do not duplicate here.)

**Error and exception style**
- One `<svc>Error` base per service in `<module>/errors.py`; re-exported from `__init__.py`.
- **Internal vs external error dichotomy** — load-bearing security boundary:
  - *Internal*: rich, descriptive messages for logs/audit (sanitizer redacts secrets).
  - *External* (API response, MCP tool return, CLI output): stable `error_code` + non-leaky `message`. Stack traces, file paths, module names, DB schema hints **never** cross to the external surface.
  - The single FastAPI exception handler (Category 3) enforces this for HTTP; the MCP tool-boundary middleware (Category 3) enforces it for MCP.
- Error messages never include raw request payloads.
- Never raise inside a pytest fixture teardown; use `contextlib.suppress(SpecificError)` or log + swallow with an explicit one-line comment.

**Log content discipline (security-critical addendum to Categories 2/3)**
- **No f-string interpolation of request bodies, auth tokens, passwords, API keys, or user-identifying fields at any log level.** Bind named fields with `bind_contextvars(...)` (Cat 2) and let the sanitizer redact.
- `repr(...)`, `%r`, `!r` are banned on objects touching auth, secrets, or external data — they dump internal state and bypass field-level redaction.
- Severity policy: typed events on the event spine are the **primary** observability stream (NFR-O1); structured logs are secondary. `DEBUG` for internal state transitions; `INFO` only for cross-boundary or user-visible transitions; `WARNING` for fall-back/retry; `ERROR` for unexpected state; `CRITICAL` reserved for shutdown failures.
- One log line per state transition. Never bracket a transition with entry-and-exit log calls — doubles volume without information.

**Deterministic output**
- `json.dumps(...)` to disk MUST set `sort_keys=True` and `ensure_ascii=False`. Non-deterministic JSON diffs in a repo with AI-agent commits produce noise commits that obscure real changes.
- Pydantic: serialize via `model_dump(mode="json")` when the output is JSON-bound; never the bare default (mode differs in subtle ways). Validators must return deterministic structures — no `set` in serialized output without explicit sorted conversion.
- File-writing code forbids iterating over `dict.keys()` / `set` when constructing output; iterate `sorted(...)` explicitly.
- Cache key canonical pattern: `hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()`.

**Import order (managed by ruff `I`)**
- stdlib → third-party → first-party (workspace) → local. `known-first-party` is configured (above) so workspace packages sort correctly.
- Avoid `from <pkg> import *`. Re-exports go through explicit `__all__`.
- Defer expensive imports into the function that uses them; type-only imports go under `if TYPE_CHECKING:`.

**Dead code**
- Unused imports / variables / functions fail ruff (`F401`, `F841`). Don't `# noqa: F401` to "save it for later" — delete it. Git history holds the receipt.
- `# pragma: no cover` requires a co-located one-line rationale (Category 4 already enforces).

**Configuration files**
- Single source of truth per concern: `pyproject.toml` (build, deps, pytest, coverage), `ruff.toml` (lint + format), `mypy.ini` (typing — single-line `mypy_path` form, Category 1), `docker-compose.yml` (services), `.env` + `.env.example` (env vars).
- `.env` for secrets + per-host overrides; `.env.example` documents every variable with a default + one-line comment. Adding an env var without updating `.env.example` is a code-review reject.
- Config file paths inside code are hardcoded or validated against an allowlist — never construct dynamically from external input. Config contents are NEVER logged at startup (they contain secrets by definition).

### Development Workflow Rules

**Repository hygiene**
- `main` is the only long-lived branch. No `develop`, no `staging`.
- Feature branches: `<author>/<short-slug>`. Story branches: `story-<epic>.<n>-<short-slug>`.
- No long-lived feature branches — rebase daily. Branches >7d idle get auto-flagged in the PR-status report.
- **Rebase, not merge.** `git pull --rebase` daily. Merge only as the final squash-into-main.
- `--force-with-lease` only. `--force` is banned (blocked via local hook + CI rejection).
- `git commit --amend` allowed pre-push on a personal branch; banned post-push.
- Force-push to `main` is hard-banned at the branch-protection level.
- `git config user.email` for automated commits MUST be a non-human identity (e.g., `bot@noreply.<owner>`). Agent commits claiming a human identity from an automated context are an audit-trail failure.

**Commits**
- Imperative-mood subject (`add`, `fix`, `update`, `refactor`), ≤72 chars. Body wraps at 72.
- Conventional-commit prefixes recommended (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`, `ci:`, scoped where helpful: `fix(security):`). Once `commitlint` lands, the prefix list becomes the enforced allowlist.
- One logical change per commit. Multi-purpose commits are split during review.
- Co-author trailer on AI-assisted commits — **exact form, GitHub-strict:** `Co-Authored-By: Name <email>` (capital A, capital B, single space after colon, angle brackets required).
- `[skip ci]` is banned — same threat surface as `--no-verify`.
- pre-commit hooks (`uv run pre-commit install`) run `secret-hygiene-precommit` + `secret-hygiene-commit-msg`. `--no-verify` is banned; if a hook is wrong, fix the hook.
- `pre-commit autoupdate` is banned from agent scope. Quarterly only, human-triggered, dedicated PR. Agents that run it silently break SHA pins.
- Never paste tokens / `.env` fragments into commit messages — the commit-msg hook will reject; consider this rule a backstop, not a guarantee.

**Pull requests**
- One PR = one logical change.
- PR title follows commit-subject conventions. PR body MUST cover: *what* changed, *why* (link FR/NFR or issue), *test plan* (which `just test*` recipes were run), *risk* (rollback path).
- **Size cap (two-axis):** hard cap = 800 *meaningful* LOC excluding generated files, migrations, lock files, and fixtures. Soft cap = >1 semantic domain changed in a single PR. Mechanical-only diffs (codemod, vendored sync) are exempt with the diff intent stated in the body.
- **No auto-merge.** All merges require ≥1 human approval. (If Dependabot auto-merge for patches is ever adopted, it requires its own ADR.)
- All required status checks must pass: `lint`, `typecheck`, `test`, `contract`, `migrator-test-additive`, `arch-invariants`, `secret-hygiene`, `coverage-floor`, `coverage-trend`, `lock-integrity`, `ai-diff-guardrails`. Bypassing required checks is administrator-only and audit-logged.
- Pre-push: run `just check` (or `just pr-status` when defined) locally. Don't push → watch CI red → iterate; that wastes CI minutes and clutters history.

**Autonomous-AI commit guardrails (Cat 5 deferred items, with enforcement layers)**
- **Mass-deletion → CI gate, not pre-commit.** `git diff --stat` check in `ci.yml` blocks merges that delete >200 lines without an issue reference (`#NNNN` in commit body) or explicit `del:` prefix. Pre-commit alone is bypassable; CI is required.
- **Binary allowlist → pre-commit hook + pinned `.allowed-binaries`.** Extensions `.pkl`, `.bin`, `.exe`, `.so`, `.dylib`, `.whl` rejected by default. Test-fixture binaries (PNG/PDF/JSONL under `tests/fixtures/`) require explicit PR-review approval.
- **Workflow / CI tampering → CODEOWNERS + branch protection.** Edits to `.github/workflows/**`, `Dockerfile.*`, `docker-compose.yml`, `.pre-commit-config.yaml` require a CODEOWNERS-named human reviewer. The CI check alone is insufficient — must also be in the CODEOWNERS file.
- **Dependency tampering → two-layer.** Pre-commit blocks local; CI verifies (a) `uv.lock` only changes alongside a `pyproject.toml` change, and (b) the lock-file hash matches. `uv.lock` is generated by `uv lock`, never hand-edited.
- **Dependency-update SLA:** security patches within 48h; minor/patch versions within 7d; major versions require ADR. CVE/license-scan must pass on the new lock (Cat 1 + Cat 5 gates apply).
- **Secret rotation cadence:** repository secrets rotate every 90d or immediately upon suspected compromise. Rotation runbook is linked from `.env.example`.
- **Runners:** GitHub-hosted only in Phase 1. Self-hosted runners require an ADR (attack-surface considerations).

**Code review**
- Non-trivial diffs require ≥1 human reviewer. AI-reviewer comments (Roborev, etc.) are advisory — they do not satisfy the human-review requirement.
- Reviewers gate on: project-context.md rules followed, FR/NFR linked, tests added, ADR referenced if architectural, secret-hygiene clean.
- "LGTM" without engagement is not approval — at least one specific observation or question.
- Stylistic preferences go in `nit:` comments; never block merge on style if ruff/mypy pass.

**Daily ops via `justfile`**
- `just bootstrap-verify` runs after every `uv sync` or pull (13 workspace imports green).
- `just dev` for local stack; `just deploy-vps` / `just deploy-macos` for host parity.
- `just test` is the PR-merge bar. `just test-slow` runs the full matrix nightly. `just test-contract` after every `just sync-upstream <name>`.
- `just sync-upstream <name>` is the only sanctioned vendoring path; updates the pinned SHA in `VENDORED.md`.
- `just migrate` applies pending Alembic revisions; `just migrator-test-additive` exercises the migrator container.
- `just backup [suffix]` snapshots the named volume (`[A-Za-z0-9._-]+`).
- `just rollback-drill` (nightly) spins the DB, migrates to HEAD, runs the down-migration sequence, asserts the schema matches the pre-migration snapshot. Release PR template requires "rollback-drill passed within 48h."
- New recipes carry a header comment with purpose + the story that added them. `just --list` ordering is **topical** (via `# --- Group ---` comment blocks), not alphabetical.

**`uv` discipline**
- CI: `uv sync --frozen` (fails if lock is stale).
- Local fresh install: `uv sync`.
- Local dep upgrade: `uv lock --upgrade-package <pkg>` then `uv sync`.
- **`uv.lock` merge conflicts** are resolved by `git checkout --theirs uv.lock && uv sync --frozen`. Never hand-edit; never `--ours`. Three-way merge of lockfiles produces broken envs.

**Story / epic discipline (BMad)**
- Sprint state lives in `_bmad-output/implementation-artifacts/sprint-status.yaml`. States: `pending`, `ready-for-dev`, `in-progress`, `blocked`, `review`, `done`. Don't skip states. `blocked` is **distinct** from `in-progress` and requires timestamp + specific blocker (not "waiting") + owning party. Blocked >1 sprint cycle = automatic escalation; either unblocked, descoped, or killed.
- **Pre-conditions for `ready-for-dev`:** one-sentence JTBD (`When I do X, I need Y, so that Z`), 2–5 Given/When/Then acceptance criteria, declared `depends_on: []`, size signal `S` / `M` / `L`. `L` stories slice at value boundaries before entering `ready-for-dev`.
- **AC is frozen at `in-progress` transition.** New requirements discovered mid-flight become new stories, not folded in. If AC turns out wrong, the story moves *back* to `ready-for-dev` for AC revision — never silently rewritten.
- Each completed story produces an implementation artifact (`_bmad-output/implementation-artifacts/<story-id>.md`) capturing: scope, deltas, `scope_delta` field if AC drifted, deferred work, retrospective points. Any non-empty `scope_delta` files a follow-up story automatically.
- **Phase boundary:** every epic and story carries `phase: 1 | 2 | ...`. No `phase: 2` work merges to `main` until a Phase-N gate ADR is accepted and `current_phase` in `sprint-status.yaml` increments.

**Retrospectives**
- Run at every epic boundary. A retro is **useful** only if it produces three falsifiable outputs: (a) the *wrong assumption* made at epic start; (b) the *single* specific process change for the next epic (one, named, actionable — never "improve communication"); (c) deferred-item triage (promoted vs parked vs killed, with decider). Missing outputs = log "retro incomplete" and schedule a re-run.

**Deferred work**
- `_bmad-output/deferred-work.md` is the canonical list (not code comments, not PR descriptions).
- Every item records: origin epic, deferral reason (specific, not "deprioritized"), `review_by:` date (≤2 epics out).
- Every sprint-plan cycle reviews items past `review_by:` — promote, kill (with rationale), or extend with a new `review_by:` and the written reason. **Maximum one extension** without escalation to product-brief level.
- If `deferred-work.md` exceeds 15 open items, stop adding epics — triage first.

**"Done" definition**
- Code merged, tests passing, docs updated, `sprint-status.yaml` reflects state, retrospective entry exists if epic-end.
- **Plus validation evidence:** for agent-consumed artifacts (prompts, workflows, configs), one documented "agent execution trace" — actual run, not "should work." For internal tooling / infra, one documented "operator walkthrough" — a human runs the workflow end-to-end and records friction.
- Missing validation evidence → story stays `review`, not `done`.

**ADRs**
- ADRs live in `docs/adr/`, named `NNNN-<slug>.md`, with YAML frontmatter (`status: proposed | accepted | superseded | deprecated`, `date: YYYY-MM-DD`, `supersedes: ADR-NNNN?`).
- New ADRs increment by one. **Collision rule** (two PRs adding ADRs in parallel): lowest open PR number wins; rebasing PR takes the next available number. CI lint asserts uniqueness.
- A machine-readable index lives at `docs/adr/index.md` (YAML frontmatter per entry: `id`, `status`, `supersedes`). A CI script validates frontmatter consistency and asserts no story references an ADR in `superseded` / `draft` status without an explicit `# noqa: adr-superseded` annotation.
- Status transitions (`proposed`→`accepted`, `accepted`→`superseded`) require a PR with a human reviewer and a one-line justification in the ADR body.
- A superseded ADR's body MUST list call-sites that need updating before the next epic boundary.

**Upstream-fork integration**
- Forks live under `upstream/<name>/`; vendored via `just sync-upstream <name>` only. Manual edits to vendored code are rejected.
- Every vendoring bump is its own commit (`chore(upstream): bump <name> to <short-sha>`) and updates `VENDORED.md` in the same commit.
- `VENDORED.md` is a **table**: pinned commit SHA, ISO date, upstream source URL, local path. No prose.
- Contract fixtures (`tests/contract/fixtures/<adapter>/`) MUST be re-recorded as part of the bump; `just test-contract` must pass before the PR opens.
- A CI integrity check verifies `VENDORED.md` SHA matches the actual vendored tree HEAD; drift fails the build.
- Adapter shim (`upstream/<name>/adapter.py`) is the only import path for everyone else (Cat 2 + Cat 4 arch gates enforce).

**Environment / secrets**
- `.env` is gitignored; `.env.example` is committed and documents every variable with a one-line comment + default.
- `.env` is NOT auto-loaded by `os.getenv`. Loading is via `pydantic-settings` with `env_file=".env"`, or explicit `load_dotenv()` at app entry. Agents that skip this ship silent config failures.
- New env vars: update `.env.example` + the consuming `pydantic-settings` model in the same PR. Stale `.env.example` is a code-review reject.
- Production secrets are operator-provisioned per `docs/deployment/vps.md` / `docs/deployment/macos.md`. Phase 1 ships no secret manager.

**Deployment & release**
- Released images live on GHCR (`ghcr.io/<owner>/oh-my-bmad-<service>`).
- Semver tags drive deploys: `OMB_VERSION=0.X.Y` in `.env`. Prerelease tags (`v0.X.Y-rcN`) publish the versioned tag but do NOT move `:latest`.
- **Tag immutability** — GHCR package settings must have "allow tag overwrite" disabled until digest-pinning lands in Phase 2.
- Release PR procedure: `release:` commit, tag, GitHub release notes, `docker compose pull` smoke on a staging VPS before announcing, SBOM + cosign attestation (when adopted) verified on the produced image.
- **Rollback decision criteria:** initiated if post-deploy smoke fails OR error rate in the first 10 min exceeds baseline by >2×. Without a threshold, rollback becomes a judgment call under pressure.
- Rollback path: revert `OMB_VERSION` in `.env`, `docker compose pull` + `up -d`. Volumes survive — schema rollback follows `docs/schema-evolution.md`. The two most recent release tags are pre-pulled on runner startup so rollback isn't cold-cache-bound.
- Phase 1 = tag-based versioning. Digest-pinning + signed-image verification (`cosign` + SLSA L2 + SBOM) land in a Phase 2 hardening story; until then the tag-immutability setting is the only line.

**Documentation upkeep**
- `README.md`, `docs/operator-runbook.md`, `docs/schema-evolution.md`, `docs/exceptions.md`, `docs/testing-guide.md`, `docs/backup-restore.md`, `docs/message-design.md` are first-class artifacts; updating them is part of "done" for any story that touches their domain.
- `docs/exceptions.md` is the single source of truth for naming/convention exceptions; new exception → new entry with story reference.
- `bmad-index-docs` regenerates `docs/index.md` whenever a doc is added or removed.

**Incident response**
- A `main` test failure is P0. Fix-forward or revert; never pile new work on top.
- Production incidents log a one-page postmortem under `docs/incidents/YYYY-MM-DD-<slug>.md` within 48h (cause, blast radius, fix, follow-up).
- Hot-fixes branch from `main` (`hotfix/<slug>`), merge via PR with one human reviewer, get tagged immediately.
- **No-bypass + break-glass.** Branch protection requires the standard checks. If a genuine incident requires bypass: a *second* human (not the one pushing) logs the bypass in a designated channel within 15 min; the bypassed commit gets retroactively reviewed within 24h and a finding filed. Without a codified escape valve, "no bypass" gets silently broken under pressure.
- Nightly CI failures auto-open a GitHub issue with `ci-failure` label and rotating assignee — silent nightly failure means rollback-drill, contract tests, mutation scores go unmonitored.

### Category 7 — Critical Don't-Miss Digest

> **Reading note:** This section distills Categories 1–6. It does not override or extend them. When Cat 7 conflicts with an earlier category, the earlier category governs.

---

**Absolute Floor — Five rules that override all others**

1. No secrets in output, logs, or generated artifacts. Ever.
2. Fail loudly at the boundary. Never swallow errors that cross a service or agent line.
3. Prefer idempotent operations. If you cannot guarantee idempotency, say so *before* acting.
4. Do not take irreversible action without explicit confirmation from the orchestrator.
5. When uncertain, return structured uncertainty — do not guess and proceed silently.

---

**Architecture invariants (in cascade-severity order)**

1. **State-mutation contract — load-bearing.** Agents and services write to shared state *only* through the designated state-management layer (event spine, `registry-state` writer, BMad `sprint-status.yaml` via the sprint-planning skill). *Violating this corrupts downstream agent decisions irreversibly within the session.*
2. **Idempotency.** Every command handler dedupes by the triggering event's UUIDv7. Retries happen; design for them. Non-idempotent writes are guarded explicitly.
3. **No implicit global mutable state between agent turns.** Each agent invocation treats shared state as potentially stale; reads come from the canonical source.
4. **Service-to-service imports are banned.** `services.<A>` never imports `services.<B>.*`. Communication is via the event spine or the registry HTTP API. (Cat 2 + Cat 4 gate.)
5. **Single-writer invariant (FR26).** Only `registry-state` opens the DB for writes; nobody else holds an `AsyncSession`.
6. **Append-only event log.** Only `EventLogWriter` in `registry-state` opens the JSONL log for write. Envelopes are immutable once emitted.
7. **Additive schema within a major.** `DROP COLUMN` / `DROP TABLE` / `ALTER COLUMN` (type change) / `RENAME` / `ADD COLUMN NOT NULL` without `DEFAULT` are banned. (Cat 3 + Cat 4.)
8. **MCP transport is stdio-only.** `mcp.server.sse` / `mcp.server.streamable_http` imports are rejected by static analysis.
9. **Upstream-fork boundary.** Vendored code under `upstream/*` is accessed *only* via `upstream/<name>/adapter.py`. Direct imports of vendored internals are rejected.
10. **No `anthropic` SDK in platform code.** All model output flows through the Claude Code worker via the event spine. Only `worker-wrapper` may import `anthropic`.
11. **Capability-tier enforcement at every MCP tool boundary** is non-bypassable. Required tests: deny-path, default-deny, escalation. (Cat 3 + Cat 4.)
12. **Never perform synchronous I/O or CPU-bound work on the main execution thread.**

Cross-refs: secret hygiene → Cat 2 + Cat 5 (3-layer model, no f-string of secrets, repr/!r ban, reserved test ID range, no startup config logging). Observability → Cat 2 + Cat 3 (typed events primary, structlog secondary, `trace_id` + `parent_event_id` binding at every entry point). Lifecycle / shutdown → Cat 3 (lifespan / SIGTERM / EOF / `wal_checkpoint(FULL)`). Autonomous-AI commit threat model → Cat 6 (CODEOWNERS, mass-deletion, binary allowlist, dep tampering, `--force-with-lease`, no-auto-merge).

---

**Hard-banned constructs** (CI-gated via `ruff --select S` plus grep checks)

- `eval()`, `exec()` — `S307`.
- `pickle.loads()` on data crossing a trust boundary — `S301`.
- `yaml.load()` without `SafeLoader` — `S506`.
- `subprocess(..., shell=True)` with non-literal `cmd`, AND `subprocess(..., shell=False)` with unsanitized list args — both are injection. `S602`/`S603`/`S604`.
- `os.system(...)` — `S605`.
- `tempfile.mktemp()` — `S306`. Use `mkstemp()` or `TemporaryDirectory()`.
- `hashlib.md5()` / `hashlib.sha1()` for security — `S324`. Non-security uses pass `usedforsecurity=False`.
- `random.*` for tokens/nonces/session IDs — `S311`. Use `secrets.*`.
- `assert` in production — `S101`. Use `if not ...: raise <Svc>Error(...)`.
- `print()` / `pprint()` in non-test paths — `T201`/`T203`.
- Bare `except:` / `except Exception: pass` — `E722`/`BLE001`.
- `requests.*` / `httpx.*` without explicit `timeout=` — DoS surface (grep check).
- `datetime.now()` without `tz=UTC`, `datetime.utcnow()`, `uuid.uuid4()` in production — use `events.FrozenClock` + `events.new_uuid7()`.
- `time.sleep` / sync subprocess / sync HTTP in async paths; `asyncio.sleep(0)` as yield-point hack.
- `BackgroundTasks` (FastAPI) for retry/durability work — use the event spine.
- `TestClient` (FastAPI sync) — `httpx.AsyncClient` + `asgi-lifespan` only.
- `copy.deepcopy` on SQLAlchemy ORM objects — detaches from session, breaks lazy-load.
- `functools.lru_cache` on methods — pins `self`. Use module-level `functools.cache` or explicit TTL.
- `os.environ.get("KEY")` without a required-key assertion at startup — silent `None` propagates.
- `id()` as a durable identifier — process-lifetime only.
- `pickle` / `dill` / `joblib` for caching anything touching user data.
- Mutable Pydantic field defaults (`field: list = []`) — use `default_factory=list`.
- SQLAlchemy `text()` f-stringed without `:param` binding — SQL injection.
- `model_validate(...)` with `strict=False` on data from external/MCP/network sources (Cat 2 / Cat 3 rule).

---

**MCP-specific anti-patterns** (novel-architecture, not covered elsewhere)

- **Schema drift** between the MCP tool registration and the Pydantic model used inside the handler. Mandate schema round-trip tests: serialize the registered schema, deserialize as if a client sent it, run through the handler.
- **Raw stdio reads** (`sys.stdin.readline()`) instead of the SDK's framing layer — partial reads on large payloads look like valid smaller payloads.
- **Tool-call concurrency without idempotency** — MCP clients retry on timeout. Side-effect tools must be idempotent by design.
- **Raw exception messages in tool error returns** leak stack traces into the calling LLM's context. Use the internal/external error boundary (Cat 5).
- **Unbounded tool return size** — tool returns are pointers/digests, not full payloads. Pagination/truncation with explicit signal; or stream.

---

**LLM-loop anti-patterns** (most under-covered area)

- **Token-bombing via verbose tool returns.** Tool returns are digests; the agent fetches details via a second tool explicitly.
- **Infinite-retry loops.** Tools distinguish terminal (don't retry) from transient (retry with backoff) errors. The agent loop enforces a *hard* retry ceiling — not configurable by the agent itself.
- **Prompt injection from user content** (`</tool_call>`, `\nSystem:` sequences). User content is wrapped in a typed envelope with explicit boundaries; never f-stringed into prompt templates or tool schemas.
- **Silent context truncation.** When the window fills, the model silently drops early messages including system-prompt sections. Critical invariants (action boundary, banned actions, idempotency contract) MUST be repeated in compressed form at the *end* of the system prompt.
- **Hallucinated tool names** are terminal errors (CRITICAL event, halt task), never transient.

---

**Rollback & Recovery contract**

- **Rollback.** Tasks that write events are designed so partial execution leaves the log in a *consistent prefix* — every written event is valid alone, none requires a future event to make sense. Atomic multi-event commits use the saga pattern with explicit compensating events; never a transaction. There is no rollback of appended events — design so you never need one.
- **Recovery.** On restart, an agent reconstructs in-progress task state *entirely from the event log*. No in-memory state, no local files, no external cache is authoritative. If reconstruction isn't possible, the task is failed and restarts from the beginning — never resumed from a guess.

---

**Determinism**

- `json.dumps(...)` to disk: `sort_keys=True` AND `ensure_ascii=False`.
- Pydantic: `model_dump(mode="json")` when output is JSON-bound. Validators return deterministic structures — no `set` without explicit `sorted(...)`.
- File-writing code iterates `sorted(...)`; never `dict.keys()` / `set` directly.
- Cache-key canonical pattern: `hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()`.
- Tests touching the clock or UUIDs use the autouse `fixed_clock` / `seeded_uuid7` fixtures (Cat 2 + Cat 4). Otherwise the test is a defect.
- `pytest-randomly` is on; seed is logged; order-dependent passes are bugs.

---

**Internal vs external error boundary**

- *Internal* (logs, audit): rich, descriptive — sanitizer redacts secrets.
- *External* (HTTP response, MCP tool return, CLI / Telegram reply): stable `error_code` + non-leaky `message`. Stack traces, file paths, module names, DB schema hints **never** cross to external surfaces. Map exceptions to stable codes before they leave; never echo `str(e)` to a caller.

---

**High-frequency gotchas** (this codebase has been bitten by each; imperative form)

1. Day-to-day dev uses `uv sync --frozen --all-packages`. `--no-dev` strips test-only deps and breaks tests; only use it for Docker image builds. (`docs/development.md`)
2. `[mypy] mypy_path` is a single comma-separated string — multi-line silently drops entries 2..N.
3. `AsyncSession(expire_on_commit=False)` is mandatory; the default raises `MissingGreenlet` after commit.
4. `docker-compose depends_on` uses the mapping form with `condition: service_healthy` — bare list is a start-order hint, not a health gate.
5. Pydantic `frozen=True` with mutable collection fields is not actually immutable under `pytest-xdist` — use `tuple` / `frozenset`.
6. `from __future__ import annotations` is required for Pydantic forward-ref resolution — not because mypy `--strict` needs it.
7. Scaffold `__main__.py` (`signal.pause()` + healthcheck touch) is the correct code until the owning story replaces it; consult `docs/exceptions.md`.
8. MCP triple-naming is owned by `uv_build`: directory `<x>` → project `<x>-mcp` → module `<x>_mcp`. Never rename the import root by hand.
9. `uv.lock` merge conflicts: `git checkout --theirs uv.lock && uv sync --frozen`. Never hand-edit; never `--ours`.
10. Test files are `test_*.py`. `*_test.py` (Go habit) is silently uncollected.
11. Never call `structlog.get_logger()` at module import — before `configure()` runs you get an unconfigured logger with a different processor chain. Logs look fine locally and fail structured parsing in prod.
12. Pydantic v1 remnants (`parse_obj`, `__fields__`, `class Config`) survive a v2 install with no import error and wrong behavior. Use `model_validate`, `model_fields`, `model_config = ConfigDict(...)`.

---

**Phase 2 gap — explicit ban, not acknowledgment** *(superseded by the Phase-2 additions below — see "## Phase 2 additions")*

Do NOT add OpenTelemetry, Prometheus exporters, or distributed-tracing instrumentation in Phase 1. Placeholder spans and stub metrics are also banned — they create false coverage signals. Features that require tracing to be correct are *blocked*, not shipped with amateur instrumentation. The `trace_id` field is reserved on the envelope for the Phase 2 wiring story.

> **PHASE-2 UPDATE (Epics 9 + 10, shipped):** `trace_id` is now WIRED (required on
> the envelope @ 1.1.0 — see ADR-0004) and metrics ARE exposed — but ONLY via the
> `metrics-subscriber` derived-projection service (ADR-0005), NOT by instrumenting
> `services/*`. The Phase-1 ban on in-service instrumentation still holds; the
> derived-projection model is the *only* sanctioned metrics surface. See
> "## Phase 2 additions" for the framework rules.

---

**When in doubt**

1. Read the FR/NFR referenced in the story or comment, not the code alone.
2. If you intend to violate a rule, file an ADR first — `proposed` status — *before* writing code that violates it.
3. Search `docs/exceptions.md` before introducing a new naming/convention exception.
4. Search ADRs (`docs/adr/`) for the *why* behind any rule. If the ADR is `superseded`/`deprecated`, find the successor before acting.
5. Search `docs/development.md` for tooling gotchas.
6. **Hard stop.** If the correct behavior remains ambiguous after the steps above, emit a structured `BLOCKED` event with the specific ambiguity, halt the task, and surface to a human. Ambiguity is not a license to proceed with reduced confidence. Never ask an LLM to interpret a rule that LLM is currently executing under — that's circular and produces hallucinated policy.

---

## Phase 2 additions (Epics 8–13)

Digest of the Phase-2 framework rules + high-frequency gotchas. Earlier
categories still govern; these EXTEND Cat 3 (framework) and Cat 7 (don't-miss).

### Cat-3 framework rules (Phase-2 services)

- **metrics-subscriber is the ONLY metrics surface (ADR-0005).** It tails the
  JSONL event spine and exposes `/metrics` (internal-only, port 9090). NEVER add
  Prometheus/OTel instrumentation inside `services/*` — `scripts/check_imports.py`
  + the derived-projection model enforce this. Metric labels are BOUNDED enums
  (`_EVENT_FAMILIES`, `_TASK_LIFECYCLE_EVENT_TYPES`); adding an event type that
  introduces a new family/label REQUIRES extending the bounded enum AND bumping
  the AC10 cardinality-test assertions (`test_metrics_state.py` ×2 +
  `tests/integration/test_metrics_cardinality.py`) in lockstep — a missed bump is
  the #1 Phase-2 cardinality-test break.
- **litestream sidecar = disaster recovery, NOT HA (ADR-0007).** Optional, OFF by
  default (`profiles: ["litestream"]` + `OMB_LITESTREAM_CONFIG_PATH`). It mounts
  `oh-my-bmad-data` **read-write** (it writes a `.state.sqlite3-litestream/` meta
  dir + checkpoints the DB — a `:ro` mount BREAKS replication). FR26 is preserved
  at the application layer (registry-state is the sole row-author), NOT by the
  mount mode. Credentials via `LITESTREAM_ACCESS_KEY_ID/SECRET` env; the filled
  `litestream.yml` is gitignored.
- **MCP transport is stdio-only (P2-I4).** No `mcp.server.sse` / `streamable_http`
  imports anywhere — enforced by `scripts/check_mcp_transport.py` (rule MCP001).
- **trace_id propagation (ADR-0004 / Cat-2 observability).** Mint-once-at-the-edge,
  forward-everywhere; required on the envelope @ 1.1.0; `caller_trace_id` is an
  explicit MCP-tool argument, never ambient. See `docs/explanations/trace-id-propagation.md`.
- **Anthropic SDK is confined to registry-api's LLM digest (Story 7.3).** The
  worker does NOT import the SDK — it spawns the `claude` CLI subprocess.
  (Supersedes the stale Phase-1 "no-anthropic-outside-worker" wording.)

### Cat-7 don't-miss (Phase-2 gotchas from retros)

1. **Cross-uid event-log file mode = `0o660` + `os.fchmod` (Stories 11.3.11 / 13.4).**
   Any writer that creates the per-day JSONL (incl. external emit scripts like
   `check_replication_lag.py` / `emit_signature_rejected.py`) MUST create it
   `0o660` and `os.fchmod` it (umask 022 strips group-write back to `0o640`,
   which crash-loops registry-state's cross-uid recovery). Others-triad stays 0
   (audit logs are never world-readable).
2. **`just build-base` before any src change takes effect in compose.**
   `services/*/Dockerfile` are thin `FROM oh-my-bmad-base:local` overrides; source
   is baked into the base image (`Dockerfile.base` COPY + `uv sync --no-editable`).
   A code change won't appear in a container until `build-base` re-runs.
2b. **Same-class re-registration is a no-op, but a NEW event family must extend
    the metrics `_EVENT_FAMILIES` enum** (see Cat-3 above) — `check_event_registry`
    gates registration; the cardinality tests gate the metrics side.
3. **Additive-only env-var aliases.** Use a single explicit
   `validation_alias=AliasChoices("OMB_…")` — a redundant field-name alias creates
   a ghost UNPREFIXED env var (Story 12.4 bug). Bound numeric env fields
   (`gt=0, le=…`) so a misconfig can't create an unbounded window/budget.
4. **Disjoint budget models (Epic 12).** The registry-level override
   (`tier3.budget_override`, FR44) and the worker-level autonomous SIGTERM
   (budget_supervisor, FR66) are SEPARATE. The grace-window interception (12.3a)
   couples them via the shared JSONL tail (override = one-shot reprieve, NOT a
   re-enforced ceiling — that's deferred to 12.3c). `post_trigger_transition` in
   the audit event MUST match the actual FSM outcome (failed↔TASK_FAILED).

---

## Phase 3 additions (Epics 15–19)

Digest of the Phase-3 framework rules (the MCP-server-authoring recipe + P3-I1/I2/I3)
+ high-frequency gotchas. Earlier categories still govern; these EXTEND Cat 3 and
Cat 7. Full walkthrough: `docs/explanations/mcp-server-authoring-recipe.md`;
decision: ADR-0010.

### Cat-3 framework rules (Phase-3 fleet MCP servers)

- **Author every fleet server to the eight-step recipe (ADR-0010).** stdio-only
  (1) · synchronous `build_server` factory, I/O in the lifespan, no `os.environ`
  inside it (2) · every tool in `TIER_MAP` + `check_tier`/`check_tier_with_approval`
  before any side effect (3) · keyword-only required `caller_trace_id`, validated by
  the byte-identical `validate_caller_trace_id` (4) · events through the FR26 writer,
  registered two-location (`payloads.py` + `event_types.py`) at `1.1.0` (5) · child-env
  allowlist (6) · base image, no compose/matrix row (7) · separability entry (8).
- **P3-I1 — every MCP tool declares a tier.** Untiered tool = build failure
  (`scripts/check_tier_declarations.py`). Tier-3 (destructive/external) uses
  `check_tier_with_approval` + a negative denial test + `emit_capability_denied_on_deny`.
- **P3-I2 — a store-owning server owns an ISOLATED file/subtree, never the registry
  DB.** `memory`/`artifact` live under their own `oh-my-bmad-data/<x>-mcp/` subtree.
  Use **stdlib `sqlite3` + raw SQL (NOT SQLAlchemy)** for any server-local store —
  SQLAlchemy mutation patterns outside `registry-state` trip `check_single_writer.py`;
  raw `conn.execute("INSERT …")` does not, and it adds zero deps (FTS5 + content-
  addressing are stdlib). Create store dirs `0o2775` (setgid) + DB/`-wal`/`-shm`/blobs
  `0o660` **in store-init** (the cross-uid umask fix is a precondition, ADR-0012 §7 —
  never a later point-fix). Event payloads are METADATA ONLY — never the stored
  bytes/body/content.
- **P3-I3 — servers ship as wheels in the base image, spawned as stdio subprocesses.**
  Workspace glob member; NO `services/*` Dockerfile, compose entry, or `release.yml`
  matrix row. Supply-chain inherited transitively from the one signed base image; the
  five fleet servers added zero matrix rows and zero new third-party deps.
- **Child-env allowlist, NEVER `os.environ.copy()` (the a0ca050 P0).** REQUIRED vars
  go in the BYTE-IDENTICAL `_ENV_ALLOWLIST` frozensets in `worker-wrapper` +
  `orchestrator-adapter` (mirror enforced by `test_clawhip_client_env_allowlist_mirror.py`).
  The ONE credential in the whole fleet allowlist is `github`'s `GITHUB_MCP_SCOPED_TOKEN`
  (repo-scoped, narrowly-named — ADR-0010 §6); the broad `GITHUB_TOKEN`/`ANTHROPIC_API_KEY`/
  `OPERATOR_HMAC_KEY` stay forbidden. This file is the P0 area: author allowlist changes
  in the main implementation context (do NOT delegate); credential additions get an
  independent security-review pass.

### Cat-7 don't-miss (Phase-3 gotchas from retros)

1. **Repo-local `.git/config` is an RCE surface; env-hermeticity is insufficient
   (Epic 15).** Over an attacker-writable worktree, `core.fsmonitor`/`hooksPath`
   execute on a mere `git status`, and `filter.<name>.clean`/`merge.<name>.driver`
   on `add`/`rebase` (attacker-NAMED — `-c` can't target them). TWO defenses:
   per-invocation `-c` shields for fixed keys + a pre-op `git config --local --list`
   scrub for named drivers. Lock every fix with a prove-live→assert-shielded
   regression test. Any subprocess-executing server (`git`, `verification`) is
   sandboxed: cwd-pinned to the worktree + secret-free env-allowlist + wall-clock
   timeout + realpath containment + `create_subprocess_exec` (never `_shell`).
2. **`GITHUB_TOKEN` (broad) is forbidden; `GITHUB_MCP_SCOPED_TOKEN` (repo-scoped) is
   the only allowed github credential (Epic 16 / G-SEC-2).** A tool result must NEVER
   echo a credential — pin "scoped token used" by recording the OUTBOUND
   `Authorization` header, never by returning the token. G-SEC-2 is HALF-closed: the
   MCP-subprocess half is done; the spawned `claude` agent
   (`claude_code_runner.py:89`) still gets the broad PAT for `git push` — don't claim
   it fully closed (deferred-work).
3. **The uv workspace hook-deadlock (recurs on every new workspace member).** Adding a member to root
   `[project.dependencies]` without the matching `[tool.uv.sources]` entry breaks `uv`
   → every `uv run` PreToolUse hook fails → all Bash/Write/Edit block (catch-22). Add
   `[tool.uv.sources]` BEFORE/atomically-with `[project.dependencies]`; if deadlocked,
   the Monitor tool (not hook-gated) can patch the sources line. (Also: this repo lives
   under `~/Documents` — macOS TCC can EPERM `getcwd`; prefix bash with `cd /tmp && …`.)
4. **Nested-stdio audit-emission deadlock (G-FN-2, ADR-0010 §9).** A server that emits
   `capability.denied` may spawn a clawhip-bridge stdio child from inside its own stdio
   server — 3-level nesting that deadlocks. Worked around via
   `OMB_MCP_AUDIT_EMISSION_ENABLED=0` on the spawners; the real fix (nested-context
   detection or lifting emission to the spawner) is the recipe precondition before any
   Tier-3 tool ships.
5. **Diff-audit delegated work: gate-green ≠ correct/secure.** Three real Phase-3
   defects shipped gate-green by delegated passes and were caught only by an
   independent diff-audit/review: the github scoped-token leak (16.3), the artifact
   binary-safety asymmetry (a content store whose `put` took UTF-8 text — 19.3), and
   the 16.5 G-SEC-2 scoping over-claim. Audit the PURPOSE + the security axis, not just
   the gates + style.

---

## Phase 4 additions (Epics 20–22, 2026-06-05/06)

Digest of the Phase-4 framework rules (browser worker archetype + P4-I1/I2/I3)
+ high-frequency gotchas. Earlier categories still govern; these EXTEND Cat 3 and
Cat 7.

### Browser Worker archetype (4th worker archetype)

- Wraps a Playwright MCP subprocess via a Docker container
  (`docker run -i --rm --init`). The worker-wrapper spawns `browser-mcp`, which
  in turn spawns the Playwright container (Docker-in-Docker prerequisite).
- Per-task browser sessions: a container is spawned on the first browser tool
  call and killed when the task ends. Lifecycle:
  `PlaywrightSubprocessManager` → spawn → stdio MCP transport → kill.
- The browser worker is a workspace member under `mcp-servers/browser-mcp/`.
  It follows the same eight-step MCP-server recipe (ADR-0010) as the Phase-3
  fleet servers, plus the browser-specific rules below.

### P4-I1 — Ephemeral sessions

- `--isolated` flag is hardcoded — browser state (cookies, localStorage) never
  persists across sessions.
- `storage` and `network` capabilities are blocklisted (P4-I1 / P4-I3). Zero
  state leakage across task-scoped sessions.

### P4-I2 — Tier-3 approval gating

- `browser_evaluate` (JS execution) is the only Tier-3 browser tool.
- Requires an `approval.granted` event matching the caller's `task_id`.
- `make_approval_lookup` scans the JSONL event log for approval events.
- SHA-256 expression hash appears in audit events — never the raw expression
  (NFR-S13).

### P4-I3 — Container sandbox

- Always `docker run`, never `npx` (bare-metal). The browser MCP server itself
  runs inside the base image; it spawns a Playwright Docker container for
  actual browser work.
- Container flags: `--init` (PID-1 signal forwarding), `--rm` (auto-remove),
  `--headless` (no display).
- `--memory=` and `--cpus=` resource limits with safe defaults.
- **Never** `--network host`. **Never** `--no-sandbox`.
- Image pinned by `@sha256:` digest — no tag-only references.

### NFR compliance

- **NFR-B1:** Zero new third-party deps (only `mcp` from base SBOM; `events` /
  `capabilities` are first-party).
- **NFR-B3:** Screenshot output → artifact-mcp content-addressed store;
  metadata-only in tool results and events.
- **NFR-B5:** Tier-3 denial test verifies `CapabilityDenied` +
  `capability.denied` audit emission.
- **NFR-R9:** Container cleanup within 30 s of session end (graceful 10 s
  SIGTERM → SIGKILL).
- **NFR-S13:** Expression hash (SHA-256) in audit events, never raw
  expression.

### 15 browser tools

- Tier-1 (6): `navigate`, `navigate_back`, `snapshot`, `take_screenshot`,
  `tab_list`, `tab_select`.
- Tier-2 (8): `click`, `type`, `fill`, `select_option`, `press_key`, `hover`,
  `tab_create`, `tab_close`.
- Tier-3 (1): `evaluate`.

### 6 browser events

- `browser.navigated`, `browser.navigation_blocked`,
  `browser.action_completed`, `browser.screenshot_captured`,
  `browser.tab_opened`, `browser.tab_closed`.

### Cat-3 framework rules (Phase-4 browser-mcp)

- **Browser MCP follows the eight-step recipe (ADR-0010)** with browser-specific
  additions: Docker-in-Docker for Playwright container, per-task session
  lifecycle, `--isolated` flag, Tier-3 approval gating for `evaluate`.
- **Screenshot content lives in artifact-mcp, NEVER in tool results or events.**
  Tool returns and event payloads carry metadata (content hash, dimensions,
  mime type) only — the blob itself is stored via artifact-mcp's
  content-addressed `put`. This is the same pattern as P3-I2 (store-owning
  servers own an isolated subtree; event payloads are metadata-only).
- **Container image pinning is `@sha256:` digest, never tag-only.** The
  Playwright image reference in config/env must be a full
  `repo@sha256:<hex>` string. A grep gate in CI rejects tag-only references
  in browser-mcp code.

### Cat-7 don't-miss (Phase-4 gotchas)

1. **Never add `--no-sandbox` to the Playwright container invocation.** The
   container is already sandboxed by Docker; `--no-sandbox` removes the
   Chromium sandbox inside it — a defense-in-depth violation. If tests fail
   inside the container without it, fix the container (user, permissions),
   not the flag.
2. **Always use Docker, never npx.** The browser-mcp server must invoke
   Playwright via `docker run`, not via `npx @anthropic/mcp-server-playwright`
   or any bare-metal path. Bare-metal execution escapes the resource limits,
   network isolation, and cleanup guarantees that Docker provides.
3. **Always use digest pinning for the Playwright image.** Tag-only references
   (`mcr.microsoft.com/playwright:latest`) are mutable — a pushed tag can
   change the image behind your back. Pin to
   `mcr.microsoft.com/playwright@sha256:<digest>`.
4. **The approval-gating scan reads the JSONL event log, not an in-memory
   cache.** `make_approval_lookup` must tail the event log file for the
   `approval.granted` event. An in-memory approval store would bypass the
   audit trail and lose approvals on restart.
5. **Container cleanup is a two-phase SIGTERM → SIGKILL with a 30 s total
   budget (NFR-R9).** Send SIGTERM, wait 10 s, then SIGKILL. Never skip the
   graceful phase — Playwright needs it to flush pending operations. Never
   extend beyond 30 s — a stuck container must not leak resources.
6. **The browser worker inherits the child-env allowlist (P3-I3).** No
   additional env vars are passed to the Playwright container beyond what the
   browser-mcp server itself receives from the worker-wrapper allowlist.

---

## Usage Guidelines

**For AI agents**
- Read this file before implementing any code in this repository. Treat it as injected context for the duration of the session.
- Earlier categories govern later ones. When Cat 7 conflicts with Cat 1–6, Cat 1–6 wins.
- When in doubt, follow the procedure in Cat 7 §"When in doubt". Ambiguity is not a license to proceed.
- The pyproject `[tool.uv.sources]`, `ruff.toml`, `mypy.ini`, `docker-compose.yml`, `.env.example`, and `docs/exceptions.md` are the canonical sources for their respective rules. This file is the *digest* of how to honor them in code, not a replacement.

**For humans**
- Keep this file lean. New rules earn their place only when they prevent a recurring failure mode an agent has actually hit.
- Update when the technology stack or architectural invariants change. The frontmatter `sections_completed` tracks which sections have been touched.
- Review at every epic retrospective. Remove rules that have become obvious from code or that have been superseded by an ADR.
- Cross-references (e.g., "Cat 2", `docs/exceptions.md`) keep this file small. When you find yourself duplicating, refactor toward a cross-reference instead.

---

_Last updated: 2026-06-06. Status: complete (Phase 1 baseline + Phase 2 additions — Epics 8–13 + Phase 3 additions — Epics 15–19 + Phase 4 additions — Epics 20–22, browser worker)._
