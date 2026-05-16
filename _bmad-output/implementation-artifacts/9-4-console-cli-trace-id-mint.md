# Story 9.4 — console-cli mints `trace_id` at command entry

Status: **ready-for-dev**

## Story

**As** an operator running `oh-my-bmad-cli task "..."`, `oh-my-bmad-cli approve <task-id>`, or any other console command,
**I want** every command-invocation to mint a fresh `trace_id` (bare UUIDv7) at the entry point and propagate it as an `X-Trace-Id` header on every outbound httpx call to registry-api,
**so that** the FR58 contract closes for the third entry-point ingress (after Story 9.2's HTTP middleware and Story 9.3's Telegram-update derivation), AND so that operators can trace any console-originated command end-to-end through the event spine using the Story 9.7 `oh-my-bmad-cli trace <trace-id>` query.

This is Story 9.4 of Epic 9 (α `trace_id` propagation kernel) — the **third of 5 entry-point ingresses**. The console-cli's `services/console-cli/src/console_cli/commands/*.py` files all mint `request_id` + `idempotency_key` via `new_request_id()` and `new_idempotency_key()` at command entry; 9.4 adds the symmetric `trace_id` mint and threads it through `RegistryAPIClient.*` calls.

---

## Acceptance criteria

### AC1 — Command-entry metadata helper

Add a helper to `services/console-cli/src/console_cli/app/runner.py` (or a new sibling module like `app/metadata.py`):

```python
from dataclasses import dataclass
from events import new_idempotency_key, new_request_id
from events.ids import new_uuid7

@dataclass(frozen=True)
class CommandMetadata:
    """Per-invocation correlation identifiers minted at command entry."""
    request_id: str
    idempotency_key: str
    trace_id: str  # bare UUIDv7 per Story 9.1 contract

def mint_command_metadata() -> CommandMetadata:
    """Mint a fresh ``(request_id, idempotency_key, trace_id)`` triple.

    Called once per command invocation by every command module. Returns
    canonical bare-UUIDv7 strings that validate against Story 9.1's
    ``is_valid_trace_id`` contract.
    """
    return CommandMetadata(
        request_id=new_request_id(),
        idempotency_key=new_idempotency_key(),
        trace_id=new_uuid7(),
    )
```

This centralizes the mint logic so future Stories don't need to update N command files when a new identifier joins the triple.

### AC2 — Every command module uses `mint_command_metadata()`

Audit the 10 command modules in `services/console-cli/src/console_cli/commands/`:

- `task.py` (POST /v1/tasks)
- `approve.py`, `reject.py`, `stop.py`, `retry.py` (POST /v1/tasks/{id}/decisions)
- `status.py`, `logs.py`, `events.py` (GET /v1/tasks/{id}/*)
- `ping.py` (GET /v1/ping or similar)
- `agent.py` (orchestrator commands)

For each, replace the existing local `new_request_id()` + `new_idempotency_key()` calls with a single `metadata = mint_command_metadata()` call, then use `metadata.request_id`, `metadata.idempotency_key`, `metadata.trace_id` in the subsequent `RegistryAPIClient.*` invocation.

### AC3 — `RegistryAPIClient` propagates `trace_id` as `X-Trace-Id` header

In `services/console-cli/src/console_cli/adapters/registry_api_client.py`, extend every public method that issues an httpx request:

- `create_task` (POST /v1/tasks)
- `get_task` (GET /v1/tasks/{id})
- `get_logs_digest` (GET /v1/tasks/{id}/logs/digest)
- `submit_decision` (POST /v1/tasks/{id}/decisions)
- `get_task_events` (GET /v1/tasks/{id}/events) — R18: real method name is `get_task_events`; spec earlier wording referenced `get_events`/`stream_events` placeholders that never materialised
- `get_platform_health` (GET /v1/health)
- Any other methods discovered by `grep "async def " services/console-cli/src/console_cli/adapters/registry_api_client.py`

Add `trace_id: str | None = None` parameter (consistent with the existing `request_id: str | None = None` convention). Inside each method, after constructing the headers dict:

```python
if trace_id is not None and trace_id != "":
    headers["X-Trace-Id"] = trace_id
```

The truthy check is intentional — empty string is a client bug worth filtering (mirrors Story 9.3 pass-2 Q9's pattern).

### AC4 — `trace_id` is a bare UUIDv7 (not `tg:<id>` form)

Console-cli mints `new_uuid7()` directly — the `tg:` form is ONLY for Telegram-derived flows (Story 9.3). A console command's trace_id must validate against the UUIDv7 branch of `is_valid_trace_id()`. Add a regression test that asserts the minted value matches `_UUIDV7_BARE_RE` (or use `is_valid_trace_id()` directly).

### AC5 — Existing `request_id` semantics unchanged

The new `trace_id` mint is ADDITIVE. The existing `request_id` + `idempotency_key` paths continue working identically. Specifically:

- `X-Request-ID` is still set per current contract
- `Idempotency-Key` is still set per current contract
- The `RegistryAPIClient`'s `_make_recording_emit`-style test fixtures (if any) continue passing

### AC6 — Unit tests (≥10)

New tests in a sibling `test_metadata.py` (or extend the existing `test_runner.py` if more appropriate):

1. `test_mint_command_metadata_returns_uuidv7_trace_id` — asserts `metadata.trace_id` matches `is_valid_trace_id()` AND specifically matches the bare UUIDv7 branch (NOT the `tg:` branch).
2. `test_mint_command_metadata_returns_distinct_values_per_call` — call twice; assert `request_id`, `idempotency_key`, `trace_id` are all distinct between the two CommandMetadata objects.
3. `test_mint_command_metadata_uses_clock_injection` (if applicable) — verify deterministic output under `FrozenClock`.
4. `test_create_task_sends_x_trace_id_header_when_provided` — call `RegistryAPIClient.create_task(..., trace_id="01917e5c-...")`; capture the outbound httpx request; assert `X-Trace-Id` header equals the provided value.
5. `test_create_task_omits_x_trace_id_header_when_none` — call with `trace_id=None`; assert no `X-Trace-Id` header set (registry-api will mint via Story 9.2's middleware).
6. `test_create_task_omits_x_trace_id_header_when_empty_string` — call with `trace_id=""`; assert no header set (defense-in-depth per Story 9.3 pass-2 Q9 pattern).
7-11. Repeat #4-#6 for at least 3 other methods (`get_task`, `submit_decision`, `get_events`).
12. (Optional integration test) `test_task_command_propagates_trace_id_to_registry_api` — invoke the `task` Typer command via `CliRunner`, mock the registry-api response, assert the captured outbound request carries `X-Trace-Id` matching the minted UUIDv7.

### AC7 — DeprecationWarning count drops

Before 9.4, the suite emits ~94 callsite DeprecationWarnings (post-9.3 pass-2 baseline). After 9.4, the console-cli callsite cluster stops emitting via the registry-api proxy path. Expected drop: ~0-3 per-source-location (console-cli doesn't construct `EventEnvelope` directly — handlers proxy through registry-api over HTTP; the warning silencing happens server-side once registry-api's `TraceIdMiddleware` receives the `X-Trace-Id` header).

Document the actual measurement in the Dev Agent Record. Following Story 9.3's lesson: spec's numeric predictions are aspirational — the SHAPE matters more than the count.

### AC8 — mypy --strict + Epic 8.7 baseline gates

`uv run mypy --strict packages/ services/registry-api services/registry-state` exits 0 (97 source files). **Do NOT** extend the CI command to include `services/console-cli` — preserve baseline.

`ruff check`, `ruff format --check`, `check_imports`, `check_single_writer`, secret-hygiene full-tree scan all pass. Test count delta: +10 to +15 tests; full suite goes from 2291 → ~2300-2305.

### AC9 — FR58 (console) literal compliance

Every console command now carries an explicit trace_id flowing through to registry-api's event log. Verify with at least one end-to-end test:

- Mock registry-api's response
- Invoke `oh-my-bmad-cli task "test"` via `CliRunner`
- Capture the outbound `X-Trace-Id` header
- Assert it matches the regex `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (bare UUIDv7)

### AC10 — `oh-my-bmad-cli trace <trace-id>` operator query deferred

Spec language from epics.md: "Every console command carries an explicit trace_id; `oh-my-bmad-cli trace <trace-id>` returns at least one event per command."

The `trace` subcommand is **Story 9.7's responsibility** (requires the `events.trace_id` ORM column + materializer update). Story 9.4 wires the inbound side; 9.7 implements the outbound query.

Document this explicitly in the Dev Agent Record so reviewers don't flag AC10 as "missing."

---

## Developer context

### Existing state

- `services/console-cli/src/console_cli/app/main.py` — Typer app factory; 10 subcommands registered.
- `services/console-cli/src/console_cli/commands/*.py` — each command function mints `new_request_id()` + `new_idempotency_key()` at function entry, then calls `RegistryAPIClient.*` with the values.
- `services/console-cli/src/console_cli/adapters/registry_api_client.py` — httpx-based client. Each method (`create_task`, `get_task`, `submit_decision`, etc.) takes `request_id: str | None = None` and conditionally sets `X-Request-ID` header. 9.4 adds the parallel `trace_id` pattern.
- Story 9.3 established the helper-import pattern: `from events.envelope import is_valid_trace_id` (public, promoted in Story 9.2 pass-1 A1). 9.4 doesn't need the validator (we mint, not validate), but should import `new_uuid7` from `events.ids`.

### Architecture compliance

- **FR58 (console)** — "console-cli mints `new_request_id(clock=...)` at command entry and threads as `X-Trace-Id` in the command envelope."
- **NFR-O7** — every event emitted in Phase 2+ carries non-null trace_id; 9.4 closes the console-originated callsites.
- **P2-I2** — no schema_version bump; Story 9.7 owns it.
- **Architecture §"trace_id propagation wiring"** — console-cli is the "Console CLI command entry" ingress in the Mermaid diagram.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| Typer | already in console-cli deps | command decorator API |
| httpx | already wired | `AsyncClient.post`/`.get` with `headers=...` |
| events | workspace member | Import `new_uuid7` from `events.ids` (not `new_request_id` — UUIDv7 generator is the semantic match) |

No new deps.

### File-structure requirements

| File | Change |
|---|---|
| `services/console-cli/src/console_cli/app/runner.py` (or new `app/metadata.py`) | Add `CommandMetadata` dataclass + `mint_command_metadata()` helper |
| `services/console-cli/src/console_cli/commands/task.py` | Replace local mints with `mint_command_metadata()`; pass `trace_id` to client |
| `services/console-cli/src/console_cli/commands/approve.py` | Same |
| `services/console-cli/src/console_cli/commands/reject.py` | Same |
| `services/console-cli/src/console_cli/commands/stop.py` | Same |
| `services/console-cli/src/console_cli/commands/retry.py` | Same |
| `services/console-cli/src/console_cli/commands/status.py` | Same |
| `services/console-cli/src/console_cli/commands/logs.py` | Same |
| `services/console-cli/src/console_cli/commands/events.py` | Same |
| `services/console-cli/src/console_cli/commands/ping.py` | Same |
| `services/console-cli/src/console_cli/commands/agent.py` | Same |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | Add `trace_id: str | None = None` kwarg + `X-Trace-Id` header logic to every public method |
| `services/console-cli/src/console_cli/test_metadata.py` (new) | ≥3 tests for `mint_command_metadata()` |
| `services/console-cli/src/console_cli/test_*_command.py` (each existing test file) | Add 1-2 tests asserting `X-Trace-Id` header propagation per AC6 |

Do **NOT** touch:
- `packages/events/src/events/envelope.py` — Story 9.1 owns it.
- `services/registry-api/*` — Story 9.2 owns the HTTP ingress; console-cli just sets the header.
- `services/telegram-gateway/*` — Story 9.3 owns it.
- `pyproject.toml` filterwarnings — Story 9.7 owns its removal.

### Testing requirements

- **Unit tests** — at least 10 new tests per AC6.
- **At least one CliRunner-based integration test** that exercises a full command (`task` is simplest) and asserts the outbound `X-Trace-Id` header.
- Test markers: PR-gate tests (not `@pytest.mark.slow`).
- Use the existing test patterns from `test_task_command.py`, `test_approve_command.py`, etc. — mock the httpx client, capture request, assert header.

### Previous-story intelligence

- **Story 9.1** — the `trace_id` shape contract (`is_valid_trace_id` accepts UUIDv7 OR `tg:<id>`). Console-cli mints UUIDv7 form (no `tg:` prefix).
- **Story 9.2** — HTTP ingress at registry-api. The `X-Trace-Id` header on console-cli's outbound httpx request will be received and propagated by `TraceIdMiddleware`.
- **Story 9.3** — Telegram ingress pattern. Console-cli mirrors the `X-Trace-Id` header forwarding from `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` (use the same `if trace_id is not None and trace_id != "":` pattern from pass-2 Q9).
- **Story 9.3 pass-2 lessons**:
  - Q2: handler WARNING-on-None should be `_log.debug`, not `_log.warning` (test-output hygiene)
  - Q9: empty-string filtering at httpx boundary
  - Q14: keep docstrings free of story-history pollution
- **Story 3.6** — established the `request_id` pattern in console-cli; 9.4 extends symmetrically.

### Git intelligence — recent commits

```
81ecaf6 fix(story-9.3): pass-2 second-opinion review — 16 patches batch-applied
8d05049 fix(story-9.3): pass-1 review — 19 patches batch-applied
0e6c844 chore(sprint-status): close Story 9.3 — CI green on 25967492908
7861ba7 feat(telegram-gateway): Story 9.3 — AllowlistMiddleware derives tg:{update_id} trace_id (FR58 Telegram + FR28)
558dcfe docs(story-9.3): spec — telegram-gateway tg:{update_id} derivation (FR58 Telegram + FR28)
```

### Latest-tech notes

- **Typer CLI testing** — use `typer.testing.CliRunner` for command invocation in tests.
- **httpx mock fixtures** — the existing test files use `pytest_httpx` or similar mocking. Mirror their pattern when adding `X-Trace-Id` assertions.
- **Frozen dataclass `CommandMetadata`** — `@dataclass(frozen=True)` prevents accidental mutation; matches the codebase's existing `frozen=True` discipline (Pydantic `ConfigDict(frozen=True)`).

---

## Dev notes

### Implementation sketch

`services/console-cli/src/console_cli/app/runner.py` (or new `metadata.py`):

```python
from dataclasses import dataclass

from events import new_idempotency_key, new_request_id  # noqa: IMP001
from events.ids import new_uuid7  # noqa: IMP001


@dataclass(frozen=True)
class CommandMetadata:
    """Per-invocation correlation identifiers minted at command entry."""

    request_id: str
    idempotency_key: str
    trace_id: str  # bare UUIDv7 per Story 9.1 contract


def mint_command_metadata() -> CommandMetadata:
    """Mint a fresh (request_id, idempotency_key, trace_id) triple."""
    return CommandMetadata(
        request_id=new_request_id(),
        idempotency_key=new_idempotency_key(),
        trace_id=new_uuid7(),
    )
```

`services/console-cli/src/console_cli/commands/task.py` (sketch — apply same pattern to all 10 commands):

```python
from console_cli.app.runner import mint_command_metadata, run_async  # or metadata module

def task(...) -> None:
    ...
    settings = ConsoleSettings()
    client = RegistryAPIClient(base_url=settings.registry_api_base_url)
    metadata = mint_command_metadata()

    try:
        result = run_async(
            client.create_task(
                title=title.strip(),
                idempotency_key=metadata.idempotency_key,
                request_id=metadata.request_id,
                trace_id=metadata.trace_id,  # NEW
                repo=repo,
                hint=hint,
            )
        )
        ...
```

`services/console-cli/src/console_cli/adapters/registry_api_client.py` (5+ methods — apply same pattern):

```python
async def create_task(
    self,
    *,
    title: str,
    idempotency_key: str,
    request_id: str | None = None,
    trace_id: str | None = None,  # NEW
    repo: str | None = None,
    hint: str | None = None,
) -> CreateTaskResponse:
    ...
    headers: dict[str, str] = {"Idempotency-Key": idempotency_key}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    if trace_id is not None and trace_id != "":  # NEW — defense in depth
        headers["X-Trace-Id"] = trace_id
    ...
```

### Non-goals (do NOT do in 9.4)

- Implement `oh-my-bmad-cli trace <trace-id>` query — Story 9.7.
- Bump `schema_version` to 1.1.0 — Story 9.7.
- Add `events.trace_id` ORM column or migrator backfill — Story 9.7.
- Remove `pyproject.toml` filterwarnings — Story 9.7.
- Implement MCP `caller_trace_id` parameter — Story 9.5.
- Implement worker-wrapper `--trace-id` CLI flag — Story 9.6.
- Touch envelope validator, registry-api middleware, or telegram-gateway — those are 9.1/9.2/9.3.

If you find yourself editing `events.envelope.py`, registry-api routes, the schema_version, alembic migrations, or any service outside `services/console-cli/` → you've drifted past 9.4's scope.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| Some commands may NOT make outbound HTTP calls (e.g., a future `version` or `help` subcommand). | AC2 applies only to commands that issue httpx requests. Document in dev notes; helper is still safe to call as no-side-effect prelude. |
| `mint_command_metadata()` returns a fresh UUIDv7 each call — tests using it twice get different values. | AC6 #2 locks the distinctness invariant; test code that needs determinism should inject `FrozenClock` via a parametrize-able variant of the helper OR construct `CommandMetadata` directly. |
| If a command function is invoked twice in the same process (e.g., a future REPL mode), each invocation mints a fresh trace_id — that's correct. | No risk; this is the desired per-invocation semantic. |
| Pass-2 reviewers historically catch tautological tests (Story 9.3 P5/P11). Make sure the X-Trace-Id assertions actually inspect the OUTBOUND request, not just the input arg. | AC6 #4-#11 explicitly says "capture the outbound httpx request" — use `pytest_httpx`'s `httpx_mock.get_requests()` or similar. |
| Empty-string `trace_id` from a future caller (e.g., env var with whitespace) should NOT set the header. | AC3 + Q9 pattern: `if trace_id is not None and trace_id != "":`. |
| `agent.py` may be a Typer subcommand group (not a single function) — different shape than `task.py`. | Handle the subcommand-group case in AC2 by adding the mint call to each leaf subcommand, OR add a Typer callback at the group level. Verify during dev. |
| **R6 — Idempotency replay trace_id divergence**: second invocation of the same command mints a fresh `trace_id` (B). If registry-api's idempotency cache returns a cached response (from the original invocation whose `trace_id` was A), the persisted envelope carries A ≠ B. An operator running `oh-my-bmad-cli trace B` (Story 9.7) will find zero events for the replay call. | Documented in `mint_command_metadata()` docstring and in `CommandMetadata` class docstring. Story 9.7's `/trace` query should support an `--idempotency-key` lookup mode OR `--include-idempotency-replay` flag as a sibling path. Tracked as Epic 9 follow-up; Story 9.4 owns documentation only. |

---

## Definition of done

- All 10 ACs satisfied (AC10 explicitly noted as deferred to Story 9.7).
- `uv run pytest services/console-cli -q` shows new tests passing.
- Local full-suite parity gate green.
- CI green on push.
- Commit message follows `feat(console-cli): Story 9.4 — ...` style.
- `sprint-status.yaml` `9-4-console-cli-trace-id-mint: backlog → done`.
- Dev Agent Record filled in with implementation notes, deprecation count delta, surprises, follow-ups.
- Two-pass adversarial code review (pass-1 + pass-2) completed per Epic 8.x cadence.

---

## Dev Agent Record

### Implementation summary

Story 9.4 closed the third entry-point ingress for Epic 9's α `trace_id` propagation kernel. Every console-cli command invocation now mints a fresh bare-UUIDv7 `trace_id` at command entry via a centralized `mint_command_metadata()` helper in `services/console-cli/src/console_cli/app/metadata.py`, and the value is forwarded as an `X-Trace-Id` header on every outbound httpx call to registry-api. The mint is symmetric to the pre-existing `request_id` + `idempotency_key` pattern; the helper packs all three into a frozen `CommandMetadata` dataclass so future identifiers can join the triple without touching the 10 command modules.

Defense-in-depth empty-string filter (`if trace_id is not None and trace_id != "":`) at the httpx boundary mirrors Story 9.3 pass-2 Q9. The `--follow` polling loop in `events.py` mints once per follow-session and reuses the same `trace_id` across all polls (correct semantic — one trace per command invocation, not per poll).

The `status.py` and `logs.py` commands previously did NOT pass `request_id` at all — Story 9.4 backfilled that gap so all 10 commands now thread the full triple.

### Files changed

| File | Status | Lines (added) |
|---|---|---|
| `services/console-cli/src/console_cli/app/metadata.py` | NEW | +57 |
| `services/console-cli/src/console_cli/test_metadata.py` | NEW | +70 |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | M | +18 (6 methods × `trace_id` kwarg + empty-string-filtered header set) |
| `services/console-cli/src/console_cli/commands/task.py` | M | -7/+7 (mint via helper, pass `trace_id`) |
| `services/console-cli/src/console_cli/commands/approve.py` | M | -7/+5 |
| `services/console-cli/src/console_cli/commands/reject.py` | M | -7/+5 |
| `services/console-cli/src/console_cli/commands/stop.py` | M | -7/+5 |
| `services/console-cli/src/console_cli/commands/retry.py` | M | -7/+5 |
| `services/console-cli/src/console_cli/commands/status.py` | M | +9 (also backfilled request_id which was missing) |
| `services/console-cli/src/console_cli/commands/logs.py` | M | +9 (also backfilled request_id which was missing) |
| `services/console-cli/src/console_cli/commands/events.py` | M | +14/-7 (both `--follow` poll loop AND non-follow path) |
| `services/console-cli/src/console_cli/commands/ping.py` | M | +7/-5 |
| `services/console-cli/src/console_cli/commands/agent.py` | M | +7/-4 |
| `services/console-cli/src/console_cli/test_task_command.py` | M | +115 (3 trace_id unit + 1 CliRunner integration) |
| `services/console-cli/src/console_cli/test_decision_commands.py` | M | +87 (3 trace_id unit + 1 CliRunner integration) |
| `services/console-cli/src/console_cli/test_events_command.py` | M | +50 (3 trace_id unit) |

### Test count delta

- **Baseline (pre-9.4)**: 2298 selected / 2303 collected (full repo, `-m "not slow"`)
- **Post-9.4**: 2313 selected / 2318 collected
- **Delta**: +15 tests (+4 in `test_metadata.py`, +4 in `test_task_command.py`, +4 in `test_decision_commands.py`, +3 in `test_events_command.py`)

Total console-cli tests grew 115 → 130 (+15).

### Callsite-warning observation

`EventEnvelope created without trace_id;…` DeprecationWarning count (full-suite, `-W default::DeprecationWarning`):

- **Pre-9.4 baseline (per spec AC7)**: ~94
- **Post-9.4 measured**: 95

Steady — no measurable local drop. This is **the expected SHAPE** per AC7's caveat: console-cli proxies through registry-api over HTTP; it constructs zero `EventEnvelope` objects directly. The trace_id silencing happens server-side once registry-api's Story 9.2 `TraceIdMiddleware` receives the `X-Trace-Id` header, but the local pytest suite does not invoke the live middleware path for console-cli unit tests (they mock httpx). The contract closure is **structural**: every command now mints + forwards a trace_id, and Story 9.7's `oh-my-bmad-cli trace <id>` will be able to query the event spine for any console-originated command.

### Surprises / deviations from spec

1. **`status.py` and `logs.py` had no `request_id` plumbing at all** — pre-9.4 these two commands called `client.get_task(task_id=...)` / `client.get_logs_digest(task_id=...)` without forwarding any correlation header. Story 9.4 backfilled the missing `request_id` alongside the new `trace_id`, bringing them in line with the other 8 commands. This is a tiny scope expansion but tightly aligned with AC2 ("each command function mints ... at function entry, then calls `RegistryAPIClient.*` with the values").
2. **`events.py --follow` polling loop**: each poll iteration still mints a fresh `request_id` (per-poll correlation), but reuses the **same** `trace_id` across the entire follow-session. This is the correct semantic: one trace per command invocation, not per network request. Documented inline in `_poll_events`'s docstring.
3. **No `# noqa: IMP001`** on the `from events import …` / `from events.ids import new_uuid7` lines in `metadata.py` — the existing console-cli command modules already use unsuppressed `from events import …` (services → packages is allowed by `scripts/check_imports.py`), and adding noqa would produce inconsistent style. The spec sketch (line 233-234) showed the suppression but the codebase's actual rule does not require it.
4. **Empty-string filter on `events.py --follow`** — the trace_id passed into `_poll_events` is always non-empty (minted by `mint_command_metadata()`), so the `if trace_id != "":` guard is defensive belt-and-braces. Kept for symmetry with the `RegistryAPIClient` boundary.

### AC10 — `oh-my-bmad-cli trace <id>` deferred to Story 9.7

Story 9.4 deliberately does NOT implement the `oh-my-bmad-cli trace <trace-id>` operator query subcommand. That command requires the `events.trace_id` ORM column (schema bump to 1.1.0) and a materializer backfill — both owned by Story 9.7. Story 9.4 closes the **inbound** side of the contract: every console command now mints and forwards a `trace_id`, so the event spine will carry the value. Story 9.7 closes the **outbound** query side. Reviewers should not flag AC10 as missing from this story.

See "Follow-up TODOs" below and the Out-of-scope risk flags table's R6 row for the idempotency-replay caveat that Story 9.7's query must handle.

### Follow-up TODOs surfaced for Epic 9

- **Story 9.5 (MCP `caller_trace_id`)**: same shape applies — MCP server adapter should accept optional `caller_trace_id` parameter and propagate downstream. Reuse `mint_command_metadata()`'s pattern.
- **Story 9.6 (worker-wrapper `--trace-id` CLI flag)**: same shape — accept flag, default to `new_uuid7()` if missing, thread through outbound calls.
- **Story 9.7 (`oh-my-bmad-cli trace <trace-id>` operator query)**: now unblocked — every console-originated command emits an event with a trace_id; the `trace` subcommand can filter on `events.trace_id` once the schema column lands. NOTE: Story 9.7's query must handle the idempotency-replay divergence documented in the R6 risk flag — a second call's `trace_id` won't match the cached envelope's; Story 9.7 should support `--idempotency-key` lookup as a fallback.
- **CI scope expansion gate**: keep `services/console-cli` OUT of the `mypy --strict packages/ services/registry-api services/registry-state` baseline as planned. 97-source-files baseline preserved.

---

## Frontmatter

```yaml
---
story_id: 9.4
story_key: 9-4-console-cli-trace-id-mint
parent_epic: 9
phase: 2
fr_refs: [FR58]
nfr_refs: [NFR-O7]
arch_refs:
  - "trace_id propagation wiring (Mermaid §line-1117+) — console-cli ingress"
  - "P2-I2 (single Phase 2 schema bump deferred to 9.7)"
estimated_hours: 3-5
priority: high (console ingress for Epic 9; third of 5 ingresses)
blocks:
  - 9.7 (schema bump uses console-cli as the unit-test baseline for the `trace` operator query)
blocked_by:
  - 9.1 (trace_id shape contract — done at 7cfebd9)
  - 9.2 (HTTP ingress + public is_valid_trace_id helper — done at b490e4e)
status: ready-for-dev
created: 2026-05-16
created_by: bmad-create-story skill
---
```

---

## Review Findings

### Pass-1 review (adversarial 3-lane) — 2026-05-17

Three-lane review (Blind Hunter / Edge Case Hunter / Acceptance Auditor) surfaced 18 unique findings. All 18 applied in a single follow-up commit (`fix(story-9.4): pass-1 review — 18 patches batch-applied`). Resolution table below.

| ID | Severity | Lane | Finding | Resolution |
|---|---|---|---|---|
| R1 | HIGH | Blind+Edge | Type-unsafe `X-Trace-Id` guard (`!= ""` allows `bytes`/`int 0`/`list` through). 6 sites in `RegistryAPIClient` + 1 in `events.py`. | Tightened all 7 sites to `isinstance(trace_id, str) and trace_id`. |
| R2 | HIGH | Blind+Edge | `_poll_events(trace_id: str)` filter was `if trace_id != "":`; inconsistent with R1 tightening. No entry assertion. | Updated filter to match R1 idiom; added `assert trace_id, "..."` at function entry; documented per-command semantic in docstring. |
| R3 | HIGH | Blind+Edge | `mint_command_metadata()` had no clock/RNG injection — tests needing determinism must monkey-patch at the wrong layer. | Added `clock: Clock | None = None` + `rng: Random | None = None` kwargs; threads through to all three `new_*` calls. Added deterministic test `test_mint_command_metadata_deterministic_under_frozen_clock`. |
| R4 | HIGH | Blind+Edge | `test_command_metadata_is_frozen` accepted `(FrozenInstanceError, AttributeError)` — would silently green-light a future `__slots__` refactor. | Tightened to `pytest.raises(FrozenInstanceError)` only. |
| R5 | HIGH | Edge | `status.py`, `logs.py`, `agent.py`, `ping.py` had zero `X-Trace-Id` CliRunner integration tests. | Added 4 CliRunner tests (one per command file) asserting `X-Trace-Id` is bare UUIDv7 and distinct from `X-Request-ID`. |
| R6 | HIGH | Edge | Idempotency replay `trace_id` correlation gap — a retry's `trace_id` (B) won't match the cached envelope's `trace_id` (A); Story 9.7 operator search finds zero events for B. | Documented in `mint_command_metadata()` and `CommandMetadata` docstrings; added row to Out-of-scope risk flags table; noted Story 9.7 must add `--idempotency-key` lookup mode. |
| R7 | HIGH | Blind+Edge | `status.py`/`logs.py` backfill (pre-9.4 omitted `X-Request-ID`) had no regression tests. | Added `test_status_command_sends_x_request_id_header` and `test_logs_command_sends_x_request_id_header` asserting bare-UUIDv7 shape. |
| R8 | MED | Blind | `test_approve_command_propagates_x_trace_id` only asserted shape; didn't check that X-Request-ID, Idempotency-Key, X-Trace-Id are three distinct values. | Added pairwise distinct-mint assertion to the approve CliRunner test. |
| R9 | MED | Blind | `_poll_events` built `X-Request-ID` inline (`_new_request_id()` imported locally), bypassing the centralised helper. | Extracted `mint_poll_request_id()` in `app/metadata.py`; `events.py` now imports + calls it. |
| R10 | MED | Blind | `CommandMetadata` had no documented validation contract — silent about whether it validates values. | Added explicit prose to class docstring: transport-only carrier, no `__post_init__` validation, callers going through `mint_command_metadata()` get shape by construction. |
| R11 | MED | Auditor | `get_task`, `get_logs_digest`, `get_platform_health` had no per-method `X-Trace-Id` unit tests at the `RegistryAPIClient` boundary. | Added 3 `@pytest.mark.asyncio` unit tests in `test_task_command.py`. |
| R12 | LOW | Blind | Agent CliRunner test missing (duplicate of R5). | Resolved by R5. |
| R13 | LOW | Blind | `"X-Trace-Id"` / `"X-Request-ID"` magic strings duplicated across 6+1 sites. | Added module-level `TRACE_ID_HEADER` / `REQUEST_ID_HEADER` constants in `registry_api_client.py` (public, exported in `__all__`); all 6 client sites and `events.py` use the constants. |
| R14 | LOW | Blind | Inline `import re`, `from typer.testing import CliRunner`, `from console_cli.app.main import app` inside test methods in `test_decision_commands.py`, `test_task_command.py`, `test_ping_command.py`, `test_agent_command.py`. | Promoted all to module-top imports in all 4 files. |
| R15 | LOW | Blind | `"01917e5c-a7d1-7000-8abc-0123456789ab"` literal duplicated across 3 test files; UUIDv7 regex duplicated across 2 test files. | Created `console_cli/_test_fixtures.py` with `FAKE_TRACE_ID_UUIDV7` and `UUIDV7_BARE_RE_PATTERN` constants; all 5 duplicate sites updated to import from it. |
| R16 | LOW | Edge | `_poll_events` retry-loop per-poll `request_id` minting lacked comment explaining the per-poll vs per-command semantics asymmetry. | Added inline comment block and updated function docstring. |
| R17 | LOW | Auditor | AC10 deferment was only a Follow-up TODO bullet in Dev Agent Record, not a dedicated subheading. | Added `### AC10 — ... deferred to Story 9.7` subheading with explanatory paragraph above the Follow-up TODOs section. |
| R18 | LOW | Auditor | Spec AC3 method list referenced `get_events`/`stream_events` (placeholder names that never materialised); real method is `get_task_events`. | Updated AC3 to list `get_task_events` with the corrected method name and a note. |
