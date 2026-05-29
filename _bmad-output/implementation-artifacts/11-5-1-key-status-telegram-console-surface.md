# Story 11.5.1 — `/key-status` Telegram + console-cli operator-facing key-fingerprint surface

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** a `/key-status` command (both in Telegram and on `console-cli`) that
shows the currently-active `OPERATOR_HMAC_KEY` fingerprint, the timestamp of
the most recent `key.rotated` event, and the actor that performed the rotation,
**so that** after rotating the key (or boot-detecting a rotation per Story 11.5
AC4) I can verify the deployed key matches my expectations **without grepping
the registry-state SQLite store directly** or correlating raw JSONL event-log
lines.

## Background — why this is 11.5.1 (carry-forward from 11.5 AC6)

Story 11.5 (`epic-11` close-out) shipped the key-rotation detection +
`key.rotated` event materialization into a singleton `KeyFingerprint`
registry-state table (AC2: schema; AC4: lifespan detection; AC5: archive-key
verifier). **AC6 of that spec proposed an operator-facing `/key-status` Telegram
command + `console-cli key-status` console command that reads the `KeyFingerprint`
row and renders a 4-line summary**:

```
Operator HMAC signing key:
  Fingerprint:    a1b2c3d4e5f6789a
  Last rotated:   2026-05-21T14:30:00Z
  Rotated by:     http-api
  Signing active: yes
```

Per the story spec's D-resolution at the time: **AC6 was DEFERRED to backlog
Story 11.5.1** "unless the executor finds the implementation truly trivial
(≤30 lines + 2 tests in each of telegram-gateway and console-cli). Default:
skip in 11.5 scope." (Spec source: `11-5-key-rotation-flow-key-rotated-event.md:241`.)

This story implements that deferred surface. The `KeyFingerprint` table + the
materializer that populates it already exist (Story 11.5 AC2 / AC4); this work
is purely a **read-side surface** on top.

## Acceptance Criteria

1. **AC1 — `GET /v1/key-status` registry-api endpoint.** New route at
   `services/registry-api/src/registry_api/routes/key_status.py` (following the
   `routes/*.py` + `app.include_router(prefix="/v1")` convention established by
   Story 11.3.7 R3 for the `/v1/health` route). Reads the singleton
   `KeyFingerprint` row from registry-state's read-only engine
   (`request.app.state.session_maker`). Returns 200 with a typed `KeyStatusResponse`
   pydantic body containing `fingerprint` (16-hex), `rotated_at` (UTC ISO-Z with
   millisecond precision per `_datetime_to_iso_z`), `rotated_by_actor_id`
   (≤128 chars per Story 11.2 P1-H1 invariant). Returns 404 with RFC 7807
   problem+json if the `KeyFingerprint` row is absent (cold-start before first
   `key.rotated` event materializes — same shape as registry-api's other 404s).
   Subject to the standard middleware stack (TraceId / RequestId /
   IdempotencyKey / ActorId / TierEnforcement); GET is idempotent so
   `Idempotency-Key` is not required on the client side (matches `/v1/health`).
2. **AC2 — `KeyStatusResponse` typed contract mirrored client-side.** Both
   `services/telegram-gateway/.../handlers/registry_client.py` AND
   `services/console-cli/.../adapters/registry_api_client.py` add a
   `KeyStatusResponseLocal` pydantic model with the same 3 fields + the same
   `Field(min_length=1, max_length=...)` constraints as the server-side schema.
   Pattern mirrors `HealthResponseLocal` (Story 3.5 / Story 11.3.7 R1). Both
   clients add an `async def get_key_status(*, request_id, trace_id) -> KeyStatusResponseLocal`
   method. **Wire-shape contract is the binding source of truth** — when the
   server-side response shape changes in any future story, both client mirrors
   MUST be updated in lock-step (AI-1 mandate; Epic 11 retro L9 mirror-identity
   pattern).
3. **AC3 — Telegram `/key-status` handler.** New file
   `services/telegram-gateway/src/telegram_gateway/handlers/key_status_command.py`
   following the `ping_command.py` pattern exactly:
   - `async def handle_key_status(message, registry_client, trace_id=None)`
     module-level (NOT a closure inside the make-router factory — matches
     Story 3.4 M6 + Story 3.5 ping pattern).
   - Wraps the registry call in the standard exception envelope (4xx → format_http_error,
     5xx → "Try again", TimeoutException → "Registry unreachable",
     `RegistryResponseError` → "unexpected response", `TelegramAPIError` swallowed).
   - Reply text uses the 4-line template from the story body above (HTML
     parse-mode inherited from `DefaultBotProperties` at lifespan level — Story
     3.1 M5; ALL string fields HTML-escaped via `html.escape(...)` even though
     they're hex/ISO/actor-id — Epic 11 retro L4 defense-in-depth).
   - Handler ALWAYS returns normally (Story 3.1 M3 contract — Telegram receives
     200 ACK regardless of what happens inside).
   - No audit event emitted (read-only command — same FR26 rationale as `/ping`).
   - Add a `make_key_status_router()` factory + register it in
     `telegram_gateway/handlers/__init__.py` and wire into `app/lifespan.py`'s
     dispatcher setup alongside the existing routers (mirror `make_ping_router`
     setup site exactly).
4. **AC4 — Console-CLI `key-status` command.** New file
   `services/console-cli/src/console_cli/commands/key_status.py` following the
   `ping.py` pattern exactly:
   - Sync `def key_status() -> None:` entrypoint.
   - Constructs `ConsoleSettings()` + `RegistryAPIClient(...)`.
   - `metadata = mint_read_metadata()` (read-only GET — no idempotency key).
   - Calls `client.get_key_status(...)` via `run_async(...)`.
   - Handles `httpx.ConnectError` / `httpx.TimeoutException` /
     `httpx.HTTPStatusError` / `RegistryResponseError` / `ValueError` exactly
     as `ping.py` does (same error-renderer surface; SystemExit(1) on any
     non-recoverable error).
   - Prints the 4-line template (NOT one-line like `ping`; the multi-line is
     intentional — operators copy-paste the fingerprint into runbooks).
   - Register the command in `console_cli/app/main.py` (typer add_command call)
     next to `ping`.
5. **AC5 — Two tests, one per service (the deferral note's "≤30 lines + 2
   tests in each" budget).**
   - `services/telegram-gateway/src/telegram_gateway/test_key_status_command.py`
     (NEW): `test_key_status_telegram_command_renders_current_fingerprint`
     (per Story 11.5 AC6 spec) — mocks `RegistryAPIClient.get_key_status` →
     returns a `KeyStatusResponseLocal` with known fingerprint/timestamp/actor;
     drives `handle_key_status(...)`; asserts the reply text contains the
     verbatim 4-line block AND that all 3 string fields are HTML-escaped.
   - `services/console-cli/src/console_cli/test_key_status_command.py` (NEW):
     `test_key_status_console_cli_renders_current_fingerprint` (per Story 11.5
     AC6 spec) — uses `typer.testing.CliRunner`; mocks
     `RegistryAPIClient.get_key_status`; asserts stdout contains the 4-line
     block AND that exit code is 0.
   - BONUS (recommended per Epic 11 retro L4 + AI-7 test-realism): one
     additional **404 test per service** asserting the cold-start path (no
     `KeyFingerprint` row yet) renders a clear "key fingerprint not yet
     materialized — try after first key.rotated event" message rather than a
     stack trace. Costs ~15 lines each; explicitly in budget.
6. **AC6 — Registry-api `KeyStatusResponse` server-side typed schema +
   route-level test.** Mirror of `routes/health.py` shape:
   - `KeyStatusResponse(BaseModel)` with `frozen=True`, `extra="ignore"` model_config.
   - Field types + constraints match the client-side `KeyStatusResponseLocal`.
   - Inline test in a new `services/registry-api/src/registry_api/routes/test_key_status.py`
     OR extend `test_app.py` — pattern matches the existing route-level test
     style (TestClient against `build_app(...)` with a seeded `KeyFingerprint`
     row via the session_maker fixture).
7. **AC7 — Wire-shape parity contract test.** New contract test
   `tests/contract/test_key_status_client_server_shape_parity.py` asserts that
   `KeyStatusResponse` (registry-api) and `KeyStatusResponseLocal` (telegram-gateway
   AND console-cli) have **byte-identical field-name sets and identical
   Field constraints** (min_length, max_length, pattern, ge/le). Pattern mirrors
   `tests/contract/test_clawhip_client_env_allowlist_mirror.py` introduced in
   Story 11.3.6. This pins Epic 11 retro **L9 (mirror-identity contract canon)**
   for the third surface beyond Story 11.3.6's MCP-env allowlists + Story 11.4's
   HMAC-signing canonical string.
8. **AC8 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # no NEW errors vs baseline (240 on main as of 11.3.7 close)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q -m "not slow"
   ```
9. **AC9 — Code-review high (7-angle) review at pass-1.** Same convention as
   Story 11.3.7 close-out: run `/code-review high` and batch-apply any findings.
   This story is small + read-only (no MCP-env code; no secrets; no event
   emission), so the review surface is narrow — but **L1 cross-cutting mandate
   applies** because the diff touches 3 service-trees (registry-api +
   telegram-gateway + console-cli) plus a contract test, and **L9 mirror-identity
   contract** is being established as a new canonical pattern.

## Tasks / Subtasks

- [x] **Task 1 — registry-api server-side route** (AC1, AC6)
  - [x] Create `services/registry-api/src/registry_api/routes/key_status.py` with
        `KeyStatusResponse` pydantic model + `APIRouter` + `@router.get("/key-status", ...)`.
  - [x] Add `from registry_api.routes.key_status import router as key_status_router`
        to `services/registry-api/src/registry_api/app.py` and `app.include_router(key_status_router, prefix="/v1")`
        next to the existing `health_router` (added by Story 11.3.7 R3).
  - [x] Handler reads via `request.app.state.session_maker` (Story 2.9 F10
        pattern — same as other routes); returns 404 problem+json on absent row.
  - [x] Add route-level test asserting 200 path (seeded row → typed response) AND
        404 path (empty table → problem+json).
- [x] **Task 2 — telegram-gateway client + handler + router wiring** (AC2, AC3, AC5)
  - [x] Add `KeyStatusResponseLocal` model + `async def get_key_status(...)`
        method to `services/telegram-gateway/.../handlers/registry_client.py`
        mirroring the `HealthResponseLocal` + `get_platform_health` pattern.
  - [x] Create `services/telegram-gateway/.../handlers/key_status_command.py`
        with `handle_key_status` + `make_key_status_router` factory.
  - [x] Export `make_key_status_router` from `handlers/__init__.py`.
  - [x] Wire the router into `app/lifespan.py` dispatcher setup alongside
        existing router registrations (mirror `make_ping_router` site).
  - [x] Create `services/telegram-gateway/.../test_key_status_command.py` with
        the happy-path test + the 404 cold-start test (AC5 bonus).
- [x] **Task 3 — console-cli client + command + main wiring** (AC2, AC4, AC5)
  - [x] Add `KeyStatusResponseLocal` model + `async def get_key_status(...)`
        method to `services/console-cli/.../adapters/registry_api_client.py`
        mirroring the existing `HealthResponseLocal` + `get_platform_health` shape.
  - [x] Create `services/console-cli/.../commands/key_status.py` with the
        sync `key_status() -> None` entrypoint.
  - [x] Register the command in `console_cli/app/main.py` (typer `add_command`
        or equivalent — match the existing `ping` registration site).
  - [x] Create `services/console-cli/.../test_key_status_command.py` with
        the happy-path test + the 404 cold-start test (AC5 bonus).
- [x] **Task 4 — Wire-shape parity contract test** (AC7)
  - [x] Create `tests/contract/test_key_status_client_server_shape_parity.py`
        modeled on `tests/contract/test_clawhip_client_env_allowlist_mirror.py`.
  - [x] Assert: same field names across all 3 schemas; same `min_length` /
        `max_length` / pattern constraints; same field types after pydantic
        `model_fields` introspection.
  - [x] Self-verification: regression mutation — temporarily widen one server
        constraint and confirm the contract test catches the drift; revert.
- [ ] **Task 5 — Validation gates** (AC8)
  - [ ] Run ruff / format / mypy / discipline scripts / pytest -m "not slow".
  - [ ] Confirm 0 new mypy errors vs main baseline (240).
- [ ] **Task 6 — /code-review high (7-angle)** (AC9); batch-apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **KeyFingerprint schema:**
  `services/registry-state/src/registry_state/schema.py:258-293` — singleton
  `id="current"` (CheckConstraint enforces), `fingerprint: String(16)`,
  `rotated_at: UTCDateTime`, `rotated_by_actor_id: String(128)`.
  Materialized by `services/registry-state/.../app/handlers.py:handle_key_rotated` from
  Story 11.5 (UPSERT on every rotation).
- **registry-api route pattern:**
  Latest example: `services/registry-api/src/registry_api/routes/health.py` (added
  by Story 11.3.7 R3) — minimal stub showing the typed-response + `frozen=True`
  + `tags=["meta"]` + `include_router(prefix="/v1")` shape.
  Reference: `routes/digest.py` for the session-maker access pattern + RFC 7807
  404 envelope (`handle_http_exception` from `adapters/errors.py`).
- **registry-api session_maker access:** Story 2.9 F10 pattern — read via
  `request.app.state.session_maker`; build with
  `async with session_maker() as session: ...` then `await session.execute(...)`.
  Example: `routes/tasks.py:get_task` for a single-row read.
- **telegram-gateway `RegistryAPIClient`:**
  `services/telegram-gateway/.../handlers/registry_client.py:82-113`
  (`HealthResponseLocal` mirror class) + `:485-544` (`get_platform_health`
  method). Both are the canonical pattern to copy.
- **telegram-gateway `/ping` handler:**
  `services/telegram-gateway/.../handlers/ping_command.py` — full reference
  for error-envelope shape, trace_id forwarding, HTML-escape discipline,
  `safe_reply` wrapper, `make_ping_router` factory.
- **telegram-gateway lifespan dispatcher wiring:**
  `services/telegram-gateway/.../app/lifespan.py` — search for
  `make_ping_router` to find the existing router-registration site;
  `make_key_status_router` registers identically.
- **console-cli `ping` command:**
  `services/console-cli/.../commands/ping.py` — full reference for the typer
  command shape including the exception envelope + `run_async` usage +
  `mint_read_metadata` (read-only metadata helper).
- **console-cli `RegistryAPIClient`:**
  `services/console-cli/.../adapters/registry_api_client.py:155+` (class) +
  `:369+` (`get_platform_health`). Same shape to copy as telegram-gateway's.
- **Contract-test reference:**
  `tests/contract/test_clawhip_client_env_allowlist_mirror.py` — Story 11.3.6
  introduction of the cross-service shape-parity pattern; new contract test
  follows the same `mirror` discipline.

### Constraints

- **L1 cross-cutting story → 3-lane review mandate APPLIES** (touches 3 service
  trees: registry-api + telegram-gateway + console-cli + a new contract test).
  Run `/code-review high` at pass-1 (matches the convention Story 11.3.7
  established).
- **L9 mirror-identity contract APPLIES** — this is the THIRD codebase surface
  to adopt the "client mirrors server schema via contract-test parity"
  pattern (after Story 11.3.6's MCP-env allowlists + Story 11.4's HMAC-signing
  canonical string). The new contract test is mandatory.
- **NO audit event emission** — `/key-status` is a read-only GET, same FR26
  rationale as `/ping`. Do NOT call `EventLogWriter.append` from any of the new
  handlers/commands. (If a future story decides operator-action audit is
  desirable, that's a separate event-type registration.)
- **NO `OPERATOR_HMAC_KEY` value exposure** — the response carries the
  16-hex `fingerprint` (already a one-way SHA-256 truncation per Story 11.5
  AC1; safe to surface). The key itself NEVER touches any client / log /
  response. Acceptance gate `tests/integration/test_hmac_key_isolation.py`
  Epic-wide grep will fail-loud if regressed.
- **Single-writer rule unchanged** — `KeyFingerprint` writes are the
  registry-state materializer's exclusive responsibility (per Story 11.5
  AC4 + the FR26 invariant). registry-api READS only (read-only engine in
  `build_app`). DO NOT introduce a write path from the route.
- **`request_id` + `trace_id` propagation** — both clients MUST forward
  `X-Request-ID` (from `mint_read_metadata` / `new_request_id`) and
  `X-Trace-Id` (Story 9.3 / FR58) on the GET. Same pattern as `get_platform_health`.
- **HTML-escape discipline (telegram side)** — Story 3.1 M5 / Epic 11 retro
  L4: even though `fingerprint` is hex, `rotated_at` is ISO-Z, and
  `rotated_by_actor_id` is a constrained service-name string, **call
  `html.escape(...)` on all three** before substituting into the HTML reply.
  Defense-in-depth: a future schema change that loosens the actor-id charset
  must not silently introduce a Telegram-side XSS.

### Project Structure Notes

- The `routes/health.py` file just added by Story 11.3.7 R3 is the closest
  precedent; copy its shape. The only material differences:
  - `key_status.py` needs DB access (via `request.app.state.session_maker`),
    `health.py` doesn't.
  - `key_status.py` has a 404 path; `health.py` is always 200.
- Both `telegram-gateway/handlers/__init__.py` and
  `telegram-gateway/app/lifespan.py` will see additions; mirror the
  `make_ping_router` setup site for both.
- `console-cli/app/main.py` will see a typer `add_command` call addition;
  mirror the `ping` registration site.

### References

- [Source: `_bmad-output/implementation-artifacts/11-5-key-rotation-flow-key-rotated-event.md:227-245`
  — Story 11.5 AC6 spec for `/key-status` Telegram + console-cli surface,
  including the 4-line reply template + the deferral resolution
  pointing here.]
- [Source: `_bmad-output/implementation-artifacts/11-5-key-rotation-flow-key-rotated-event.md:444-464`
  — Story 11.5 task-list confirming AC6 deferral landed in `sprint-status.yaml`
  as `11-5-1-key-status-telegram-console-surface: backlog`.]
- [Source: `_bmad-output/planning-artifacts/epics.md:2454`
  — epic-11 carry-forward table entry: "Story 11.5.1 — `/key-status` Telegram +
  console-cli surface (11.5 AC6) — **backlog**".]
- [Source: `services/registry-state/src/registry_state/schema.py:258-293`
  — `KeyFingerprint` ORM definition (singleton, 16-hex fingerprint,
  UTC rotated_at, 128-char rotated_by_actor_id).]
- [Source: `services/registry-api/src/registry_api/routes/health.py` (Story
  11.3.7 R3) — closest precedent for the new route shape.]
- [Source: `services/telegram-gateway/.../handlers/ping_command.py` +
  `test_ping_command.py` (Story 3.5 / 3.6 / 3.7) — handler + test
  reference patterns to mirror exactly.]
- [Source: `services/console-cli/.../commands/ping.py` +
  `test_ping_command.py` (Story 4.3) — console-cli command + test
  reference patterns to mirror exactly.]
- [Source: `tests/contract/test_clawhip_client_env_allowlist_mirror.py`
  (Story 11.3.6) — contract-test cross-service parity pattern.]
- [Source: `docs/adr/0006-approval-signing-and-rotation-protocol.md`
  (Story 11.5 AC7) — ADR-0006 §Key-fingerprint section authorising the
  16-hex SHA-256[:8] operator-readable form this story surfaces.]
- [Source: `_bmad-output/implementation-artifacts/epic-11-retro-addendum-2026-05-24.md`
  — L9 mirror-identity contract canon + AI-8 (mirror-identity contract canon)
  + AI-9 (constants-extraction-and-grep) action items this story
  operationalises.]

## Previous-story intelligence (most-recent learnings)

From the latest 5 commits + Story 11.3.7 close-out:

- **`/v1/health` route addition (Story 11.3.7 R3 fix `5f730a6`)** showed the
  cleanest registry-api route-mounting pattern: `routes/<name>.py` with typed
  `<Name>Response` pydantic model + `APIRouter` + `app.include_router(prefix="/v1")`.
  COPY this shape verbatim for `routes/key_status.py`.
- **`HealthResponse` ≡ `HealthResponseLocal` shape contract (Story 11.3.7 R1)**
  established that "client-mirror models MUST match the server response shape
  byte-for-byte (field names + Field constraints)". A regression there would
  have ValidationError'd every `/ping` call silently. AC7's contract test
  here pins this for `/key-status` upfront — DON'T defer the contract test.
- **Circular-import on `__version__` (Story 11.3.7 refactor)** — when adding
  files to `services/registry-api/src/registry_api/routes/`, be aware that
  `from registry_api import __version__` triggers the package `__init__.py`
  top-level import chain (which imports `build_app`). If `key_status.py`
  doesn't need `__version__`, just don't import it; if it does, use
  `from registry_api._version import __version__` (the dedicated module added
  by Story 11.3.7 to break the circular path).
- **Soft-warning hooks** — every direct Edit/Write to `services/**/*.py`
  surfaces a `[DELEGATION NOTICE] Direct Edit on source file` hook warning
  recommending the `executor` subagent. Per the memory note
  (`diff-audit-delegated-security-work`), direct surgical edits are SAFER
  for narrow well-scoped changes in security-adjacent code. This story is
  NOT in the MCP-env / HMAC-key path (read-only fingerprint surface), so
  direct edits OR executor delegation both work. Either is fine; if you
  delegate, **diff-audit the result before commit** per Epic 11 retro L4.
- **CI-vs-local pytest flake bench** — 11 tests flake locally under
  `pytest -m "not slow"` (registry-state perf thresholds + MCP-server
  env-pollution flakes); all VERIFIED pre-existing on main. Same bench
  applies here; don't waste time chasing them if they appear.

## Git intelligence summary

Last 4 commits on `epic-11.3.7` branch (most recent first):

- `5f730a6` — Story 11.3.7 /code-review 7-angle batch-apply (10 fixes
  including `routes/health.py` extraction — direct precedent for the new
  `routes/key_status.py`).
- `d01bfcd` — Story 11.3.7 AI-1 3-lane review fixes.
- `6c1c221` — Story 11.3.7 initial implementation (added `routes/health.py`
  to satisfy the cross-service `HealthResponseLocal` contract — the
  pattern this story propagates to `KeyStatusResponse`).
- `611b96f` — Story 11.3.6 7-angle code-review close + 3140 tests / 0 failed
  baseline ratification.

This story's branch should be `epic-11.5.1` (per the established
`epic-<story-id>` convention).

## Frontmatter

```yaml
---
story_id: 11.5.1
story_key: 11-5-1-key-status-telegram-console-surface
parent_epic: 11
phase: 2
fr_refs: [FR65a]
nfr_refs: [NFR-S10]
arch_refs:
  - "Story 11.5 AC2 — KeyFingerprint ORM singleton table (registry-state schema.py:258-293)"
  - "Story 11.5 AC4 — registry-api lifespan rotation-detection (adapters/key_rotation.py)"
  - "Story 11.5 AC6 — DEFERRED to this Story 11.5.1 (per D-resolution at 11-5-key-rotation-flow-key-rotated-event.md:241)"
  - "Story 11.3.7 R3 — routes/health.py + include_router(prefix=/v1) pattern this story mirrors"
  - "Story 11.3.7 R1 — server-shape ≡ client-mirror contract this story propagates to KeyStatusResponse"
  - "Story 11.3.6 — tests/contract/test_clawhip_client_env_allowlist_mirror.py pattern"
  - "Story 3.5 / FR17 / NFR-O4 — /ping handler + test_ping_command.py (handler reference)"
  - "Story 4.3 / AC-5 — console-cli ping command (CLI reference)"
  - "ADR-0006 §Key-fingerprint section — surface contract authorisation"
estimated_complexity: SMALL
priority: low (operator quality-of-life; not on the Epic 11 acceptance gate)
blocks: []
unblocks:
  - Operator can verify deployed OPERATOR_HMAC_KEY fingerprint without grepping registry-state SQLite
  - Closes the last open backlog item under epic-11 (11.5.1)
  - Establishes KeyStatusResponse client-mirror contract for any future key-status surface
    (e.g., key-archive listing endpoint)
---
```

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context) via `/bmad-dev-story` skill — single-context execution
of Tasks 1-6 on branch `epic-11.5.1`.

### Debug Log References

Story is small (read-only operator-facing surface; no MCP-env code; no event
emission; no production secret-access path); no extended debug sessions needed.

### Completion Notes List

**Implementation summary:**

- **AC1, AC6** — `services/registry-api/src/registry_api/routes/key_status.py`
  new module with typed `KeyStatusResponse` pydantic model + `APIRouter` +
  inline `@router.get("/key-status", ...)`. Reads singleton `KeyFingerprint`
  row via `request.app.state.session_maker`. 404 path uses `HTTPException` →
  the registry-api RFC 7807 problem+json envelope (`handle_http_exception`
  adapter). Mounted via `app.include_router(key_status_router, prefix="/v1")`
  next to the existing `health_router` (Story 11.3.7 R3 precedent). 2
  route-level tests: 200 happy-path + 404 cold-start (`routes/test_key_status.py`).
- **AC2** — `KeyStatusResponseLocal` pydantic mirror added to BOTH
  `services/telegram-gateway/.../handlers/registry_client.py` AND
  `services/console-cli/.../adapters/registry_api_client.py`. Both clients
  expose `async def get_key_status(...)` mirroring `get_platform_health`
  exactly (same request_id / trace_id forwarding; same RegistryResponseError
  on malformed body; no idempotency-key per read-only GET semantics).
- **AC3** — `services/telegram-gateway/.../handlers/key_status_command.py`
  new module mirroring `ping_command.py` structure: module-level `handle_key_status`
  with full exception envelope (TooManyRedirects, HTTPStatusError with
  special 404 cold-start branch, RegistryResponseError, HTTPError, broad
  Exception backstop). Reply uses the 4-line operator-readable block with
  `html.escape` on all 3 string fields (Epic 11 retro L4 defense-in-depth).
  `make_key_status_router()` factory accepts both `"key-status"` AND
  `"key_status"` command aliases for forgiveness. Exported from
  `handlers/__init__.py` + wired into `app/lifespan.py` dispatcher setup
  alongside the existing routers.
- **AC4** — `services/console-cli/.../commands/key_status.py` new module
  mirroring `commands/ping.py`. Same exception envelope (ConnectError /
  TimeoutException / HTTPStatusError with 404 cold-start branch /
  RegistryResponseError / ValueError). 4-line block to stdout; SystemExit(1)
  on any non-recoverable error. Registered in `app/main.py` with explicit
  `name="key-status"` so the hyphenated command form matches the Telegram
  surface.
- **AC5** — 3 tests in telegram-gateway test_key_status_command.py (happy +
  404 cold-start + HTML-escape defense), 6 tests in console-cli (3 client +
  3 command, including cold-start + network error). Total 9 service tests
  beyond the AC5 budget of 2+2 — additional tests are AC5 bonus per the
  story spec's recommended bonus 404-cold-start tests.
- **AC6** — see AC1.
- **AC7** — `tests/contract/test_key_status_client_server_shape_parity.py`
  pins the 3-schema mirror-identity contract via per-field metadata
  comparison (`field.metadata` repr comparison covers min_length/max_length/
  pattern/ge/le without depending on annotated_types ordering). 4 tests:
  field-name identity, full-signature identity, telegram≡console direct,
  fingerprint security invariant (16-hex pin). L9 mirror-identity canon
  propagated to its 3rd codebase surface.
- **AC8** — gates green: ruff/format/discipline clean; mypy total = 240 =
  baseline on main (0 new errors from this diff); pytest 854 passed / 3
  skipped on the focused services + contract test set.
- **AC9** — runs next via `/code-review high`.

**Side-effect note:** `console-cli/app/main.py` `key_status` import was added
in alphabetical order to keep ruff I001 import-sort clean (no `noqa`
suppression needed unlike Story 11.3.7's circular-import case).

**No spec deviations.** All 9 ACs implemented exactly as the story spec
described; no shortcuts; no scope creep.

**Security audit:**
- Zero new `os.environ.copy()` / `dict(os.environ)` calls; `mcp_clients.py`
  files untouched (this story is not in that code path).
- No `OPERATOR_HMAC_KEY` value exposure: the response carries only the
  16-hex `fingerprint` (one-way SHA-256[:8] truncation per ADR-0006). The
  Epic 11 acceptance gate `tests/integration/test_hmac_key_isolation.py`
  Epic-wide grep continues to pass.
- No audit-event emission from the new read-only surface (FR26 single-writer
  rule preserved).
- HTML-escape discipline applied even for constrained string fields
  (defense-in-depth per Epic 11 retro L4).

### File List

**New files:**

- `services/registry-api/src/registry_api/routes/key_status.py` — server-side
  route + `KeyStatusResponse` pydantic model (AC1, AC6).
- `services/registry-api/src/registry_api/routes/test_key_status.py` — 2
  route-level tests (AC1 happy-path + 404 cold-start).
- `services/telegram-gateway/src/telegram_gateway/handlers/key_status_command.py`
  — `handle_key_status` + `make_key_status_router` (AC3).
- `services/telegram-gateway/src/telegram_gateway/test_key_status_command.py`
  — 3 handler tests (AC5 happy + cold-start + HTML-escape).
- `services/console-cli/src/console_cli/commands/key_status.py` — `key_status`
  typer command (AC4).
- `services/console-cli/src/console_cli/test_key_status_command.py` — 6
  client + command tests (AC5).
- `tests/contract/test_key_status_client_server_shape_parity.py` — 4-test
  cross-service wire-shape parity contract (AC7).

**Modified files:**

- `services/registry-api/src/registry_api/app.py` — import +
  `app.include_router(key_status_router, prefix="/v1")`.
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`
  — `KeyStatusResponseLocal` + `get_key_status` method.
- `services/telegram-gateway/src/telegram_gateway/handlers/__init__.py` —
  export `make_key_status_router` + add to `__all__`.
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` —
  import + `dp.include_router(make_key_status_router())` registration.
- `services/console-cli/src/console_cli/adapters/registry_api_client.py` —
  `KeyStatusResponseLocal` + `get_key_status` method.
- `services/console-cli/src/console_cli/app/main.py` — import + typer
  `add_command(name="key-status")` registration.

**BMad tracking:**

- `_bmad-output/implementation-artifacts/sprint-status.yaml` —
  `11-5-1-key-status-telegram-console-surface: backlog → ready-for-dev →
  in-progress → review` (Dev sets `review` at Step 9 completion).
- `_bmad-output/implementation-artifacts/11-5-1-key-status-telegram-console-surface.md`
  — this Dev Agent Record + Change Log appended.

## Change Log

| Date | Author | Summary |
|---|---|---|
| 2026-05-29 | Claude Opus 4.7 (1M ctx) via /bmad-dev-story | Initial implementation of Tasks 1-6 on branch epic-11.5.1. AC1+AC6 registry-api /v1/key-status route + KeyStatusResponse pydantic model + 2 route tests. AC2 KeyStatusResponseLocal mirror + get_key_status method on BOTH telegram-gateway + console-cli clients. AC3 telegram-gateway handlers/key_status_command.py + dispatcher wiring + 3 tests. AC4 console-cli commands/key_status.py + typer registration + 6 tests. AC5 9 service-level tests (3 telegram + 6 console-cli) including AC5 bonus 404-cold-start + HTML-escape defense tests. AC7 tests/contract/test_key_status_client_server_shape_parity.py — 4-test wire-shape parity (field-name + signature + telegram≡console direct + fingerprint security invariant) propagates L9 mirror-identity canon to 3rd codebase surface. AC8 gates green (ruff/format/discipline clean, mypy 240=baseline 0-new, 854 service+contract tests pass). AC9 runs next via /code-review high. No spec deviations. No production secrets exposed (16-hex fingerprint only). No mcp_clients.py touched. No audit-event emission from new surface (FR26-safe). Sprint-status: ready-for-dev → in-progress → review. |
| 2026-05-29 | Claude Opus 4.7 (1M ctx) via /bmad-code-review | AI-1 3-lane review (Blind+Edge+Acceptance) discharged. Blind Hunter returned 12 findings; Edge Case Hunter returned empty (failed lane noted); Acceptance Auditor returned APPROVE verdict + verified AC7 contract mutation + reproduced AC8 mypy baseline. 6 fixes batch-applied (H3 console-cli get_key_status adds KeyError to except-tuple for parity with telegram-gateway sibling; M1 hoisted `datetime` import + naive-datetime UTC normalization in BOTH telegram _iso_z helper AND console-cli inline rendering — fixes silent trailing-Z drop on naive datetimes; M3 tightened 404 test to assert `application/problem+json` Content-Type after verifying registry-api's `handle_http_exception` adapter does render RFC 7807 correctly; L2 logged 404 cold-start hits at INFO using the previously-unused `_log` for operator observability; L3 moved `KeyStatusResponse(...)` construction INSIDE the `async with session_maker()` block as defense against future schema evolution introducing deferred columns; Dev1 added `extra="ignore"` to server `KeyStatusResponse.model_config` for spec-verbatim symmetry with both client mirrors). 6 findings discharged with rationale: H1 (`render_http_error` is `NoReturn` typed + raises `SystemExit` — Blind speculated fall-through wrongly); H2 (dispatcher DI wiring inherited from sibling ping handler; cross-cutting test improvement out of scope); M2 (two-engine SQLite test fixture matches `test_app.py` precedent); M4 (per-call AsyncClient is intentional console-cli pattern); L1 (TooManyRedirects handler matches `ping_command.py:91` — pattern parity preserved); L4 (httpx.AsyncClient construction is lazy — mocked `.get` intercepts before any network IO); N1 (rotated_by_actor_id charset pattern would be a codebase-wide change to Story 11.2 P1-H1 actor-id invariant — defer). Auditor's 3 enumerated deviations all confirmed sound: Dev1 fixed above; Dev2 (telegram alias both `key-status` + `key_status`) is operator-friendly above-spec addition justified in original Dev Agent Record; Dev3 (9 vs 2+2 test budget) within spec's explicit AC5 bonus authorization. Gates re-verified post-fix: ruff/format clean (398 files), 15/15 targeted tests pass (route + 2 handler files + contract), mypy total 240 = baseline (0 new from this review-fix delta). Edge Case Hunter lane noted as failed (empty response) — Blind Hunter independently covered substantial edge territory. Sprint-status: review → **done**. |

## Definition of Done

- `GET /v1/key-status` route serves 200 + 404 with the typed response shape.
- `/key-status` Telegram command renders the 4-line operator-readable block;
  HTML-escapes all 3 string fields; handler always returns normally.
- `console-cli key-status` command renders the same 4-line block;
  SystemExit(1) on any non-recoverable error.
- 4 unit tests pass (1 happy-path + 1 cold-start per service); 1 contract test
  pins the cross-service `KeyStatusResponse` shape parity.
- Validation gates green: ruff/format/mypy 0-new/discipline 0/`pytest -m "not slow"`.
- Code-review high (7-angle) findings batch-applied.
- `sprint-status.yaml`: `11-5-1-key-status-telegram-console-surface: backlog → ready-for-dev → in-progress → review → done`.
- No new `os.environ.copy()` / `dict(os.environ)` introduced; `mcp_clients.py` files untouched (this story is not in that code path).
- `OPERATOR_HMAC_KEY` Epic-11 acceptance gate `tests/integration/test_hmac_key_isolation.py` still passes (read-side surface MUST NOT regress key isolation).
