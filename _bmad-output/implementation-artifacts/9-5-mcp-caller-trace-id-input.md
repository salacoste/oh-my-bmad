# Story 9.5 — MCP tool handlers take `caller_trace_id` as explicit input

Status: **ready-for-dev**

## Story

**As** the Phase 2 trace_id propagation kernel that needs ALL events emitted through MCP tools (`emit_event`, `emit_blocker`, `task.add_note`, `session.heartbeat`, etc.) to carry the operator's originating trace_id,
**I want** every MCP tool handler in the three MCP servers (`task-registry`, `session-registry`, `clawhip-bridge`) to accept `caller_trace_id: str` as an **explicit Pydantic-validated input parameter** (not ambient context), validate it against the Story 9.1 contract, and thread it through every `EventEnvelope.create(...)` callsite the tool reaches,
**so that** when the worker subprocess (Story 9.6) calls an MCP tool with its `--trace-id` value, the event emitted by clawhip-bridge carries that trace_id end-to-end — closing the MCP ingress (4th of 5) in Epic 9's α propagation kernel.

This is Story 9.5 of Epic 9. **Structurally distinct from 9.2/9.3/9.4:** unlike HTTP middleware (9.2), Telegram derivation (9.3), or console-cli minting (9.4), MCP tools receive `caller_trace_id` as an **explicit input field**. The MCP protocol uses Pydantic for tool-argument validation; adding `caller_trace_id` as a required kwarg means callers MUST supply it (otherwise FastMCP returns a validation error to the caller). This is the spec from epics.md verbatim: "Tool invocations without `caller_trace_id` fail validation; with it, value appears in every event the tool emits."

---

## Acceptance criteria

### AC1 — Every tool handler accepts `caller_trace_id` as a required kwarg

In `mcp-servers/{task-registry,session-registry,clawhip-bridge}/src/.../handlers/tools.py` (and `clawhip-bridge`'s `server.py`), every `@mcp.tool()`-decorated async function gains `caller_trace_id: str` as a kwarg-only required parameter (NO default):

```python
@mcp.tool()
async def emit_event(
    type: str,
    payload: dict[str, object],
    *,
    caller_trace_id: str,  # NEW — required per Story 9.5
    parent_event_id: str | None = None,
) -> dict[str, str]:
    ...
```

Targets identified by `grep "@mcp.tool" mcp-servers/`:

- **task-registry**: `task_add_note`, `task_attach_artifact`, `task_emit_event` (3 tools)
- **session-registry**: `session_register`, `session_heartbeat`, `session_close` (3 tools)
- **clawhip-bridge**: `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` (5 tools — verify via grep, may be more)

**Total: ~11 tool handlers.**

### AC2 — Validate `caller_trace_id` against the Story 9.1 contract

At the top of each tool handler (BEFORE `check_tier`), validate:

```python
from events.envelope import is_valid_trace_id  # noqa: IMP001
...
if not is_valid_trace_id(caller_trace_id):
    raise ValueError(
        f"caller_trace_id must match Story 9.1 contract (UUIDv7 or tg:<update_id>); "
        f"got {caller_trace_id!r}"
    )
```

The `ValueError` will be surfaced by FastMCP to the caller as a tool-error response. Document this contract in each tool's docstring.

**Alternative implementation:** centralize the validation in a `validate_caller_trace_id()` helper at the top of each `tools.py` (mirrors Story 9.3's `_keys.py` approach). Recommend the helper pattern — DRY across 11 sites.

### AC3 — `caller_trace_id` propagates to every `EventEnvelope.create(...)` callsite

For tools that emit envelopes (most of them — especially clawhip-bridge's 5 emit_* tools), pass `trace_id=caller_trace_id` to `EventEnvelope.create(...)`.

Specifically, in `clawhip-bridge`'s `_emit()` helper (currently at `server.py:155-176`), extend the signature:

```python
async def _emit(
    event_type: str,
    payload: dict[str, object],
    parent_event_id: str | None,
    caller_trace_id: str,  # NEW
) -> dict[str, str]:
    envelope = EventEnvelope.create(
        ...
        trace_id=caller_trace_id,  # NEW
        request_id=new_request_id(clock=clock),
    )
    await writer.append(envelope)
    ...
```

Each tool that calls `_emit()` passes its `caller_trace_id` through. This silences the Story 9.1 DeprecationWarning for the 5 clawhip-bridge emit callsites.

For `task-registry` + `session-registry` tools that are currently STUBS (Phase 1 — they return `{"ok": True}` without emitting), accept `caller_trace_id` and log it at INFO level so Stories 5.12+ can observe the contract is wired even before the persistence path lands.

### AC4 — Validation rejection mode

When `caller_trace_id` is missing or invalid, the tool MUST return a structured error (not raise an uncaught exception):

- **Missing:** FastMCP's Pydantic validation handles this automatically — the tool call fails before the handler runs.
- **Present-but-invalid:** the handler raises `ValueError` (per AC2). FastMCP wraps this in an MCP error response. Document the error-response shape in the tool docstring.

Test that BOTH paths produce an actionable error to the caller (not a silent fallback to a server-minted trace_id — that would defeat the FR58 contract).

### AC5 — Contract tests in `tests/contract/` updated

`tests/contract/` contains schema round-trip tests for the MCP tool surface (Story 2.8). Update them to:

1. Assert every MCP tool's input schema (auto-derived by FastMCP from the function signature) INCLUDES `caller_trace_id` as a required `string` field.
2. Add a negative round-trip: an input JSON missing `caller_trace_id` produces a Pydantic validation error.
3. Add a positive round-trip: an input JSON with a valid `caller_trace_id` (UUIDv7) succeeds.
4. Add a "tg:" form positive: `caller_trace_id="tg:42"` also succeeds (Telegram-originated calls).

### AC6 — Unit tests (≥15 — 3 servers × ~5 tools, plus validation/contract tests)

For each of the 3 MCP servers, add tests in the existing `test_server.py`:

1. `test_<tool>_requires_caller_trace_id` — call without the kwarg; assert Pydantic validation rejects (TypeError or ValidationError).
2. `test_<tool>_rejects_invalid_caller_trace_id` — call with `caller_trace_id="bad-format"`; assert ValueError raised with helpful message.
3. `test_<tool>_accepts_uuidv7_caller_trace_id` — call with bare UUIDv7; assert success + (for emit tools) the emitted envelope's trace_id matches.
4. `test_<tool>_accepts_telegram_caller_trace_id` — call with `tg:42`; assert success.

For `clawhip-bridge`, additionally:
5. `test_emit_event_envelope_carries_caller_trace_id` — call `emit_event` with UUIDv7; read JSONL log; assert `envelope.trace_id == caller_trace_id`.
6. `test_emit_blocker_envelope_carries_caller_trace_id` — same for `emit_blocker`.
7. (Repeat for `emit_summary`, `emit_approval_request`, `emit_completion`.)

Use the existing test patterns in `test_server.py` files (each MCP server already has a `MockedSession`-like setup for in-process tool invocation).

### AC7 — DeprecationWarning count drops

Before 9.5, the suite emits ~95 callsite DeprecationWarnings (post-9.4 baseline). After 9.5, the clawhip-bridge `_emit()` callsite cluster stops emitting (5+ tools all route through `_emit()`). Expected drop: ~5-10 per-source-location.

Document the actual measurement in the Dev Agent Record.

### AC8 — mypy --strict baseline preserved

`uv run mypy --strict packages/ services/registry-api services/registry-state` exits 0 (97 source files). Do NOT extend the CI command to include `mcp-servers/*`. `ruff check`, `ruff format --check`, `check_imports`, `check_single_writer`, secret-hygiene full-tree all pass.

Test count delta: +15 to +25 tests; full suite goes from 2330 → ~2350-2360.

### AC9 — Tool input schemas (auto-generated) include `caller_trace_id`

FastMCP derives JSON schemas for tools from their signatures. After 9.5, every tool's input schema MUST list `caller_trace_id` as a `required` field. Verify via:

```python
# In a test, inspect the FastMCP server's tool registry:
tool_schema = server.tools["emit_event"].schema  # or equivalent
assert "caller_trace_id" in tool_schema["required"]
assert tool_schema["properties"]["caller_trace_id"]["type"] == "string"
```

Document the exact API surface in the test (FastMCP version-specific — check the installed version).

### AC10 — Worker integration deferred to Story 9.6

Story 9.6 (worker-wrapper passes `--trace-id` CLI flag to Claude Code) will call MCP tools (specifically `clawhip-bridge.emit_*`) passing `caller_trace_id=<trace-id-from-worker-flag>`. Story 9.5 is the **receiving** side; Story 9.6 is the **calling** side. AC9 verifies the receiving contract.

Document this dependency explicitly in the Dev Agent Record so reviewers don't flag AC10 as "missing implementation."

---

## Developer context

### Existing state

- **task-registry MCP server** (`mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py`): 3 tools, all Phase 1 stubs (`{"ok": True}` returns). Use `check_tier()` + `_validate_task_exists()` then log + return.
- **session-registry MCP server** (`mcp-servers/session-registry/.../handlers/tools.py`): 3 tools, similar stubs.
- **clawhip-bridge MCP server** (`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py`): 5+ tools that DO emit envelopes via the `_emit()` helper at `server.py:155-176`. This is THE central propagation point — the helper currently passes `request_id=new_request_id(clock=clock)` but no `trace_id`. Story 9.5 fixes this.

### Architecture compliance

- **FR58 (MCP)** — "MCP tool handlers receive `caller_trace_id` as an explicit input field (not ambient context) and propagate it when calling downstream clients."
- **NFR-O7** — every event emitted in Phase 2+ carries non-null trace_id. After 9.5, MCP-emitted events comply.
- **P2-I2** — no `schema_version` bump (Story 9.7 owns it).
- **Architecture §"trace_id propagation wiring"** — MCP is the "MCP tool handlers `caller_trace_id` input" ingress in the Mermaid diagram.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| FastMCP | already in mcp-servers deps | `@mcp.tool()` decorator API; auto-generates Pydantic schemas from function signatures |
| Pydantic v2 | already wired | strict mode + frozen model_config |
| events | workspace member | import `is_valid_trace_id` from `events.envelope` (public, Story 9.2 pass-1 A1) |

No new deps.

### File-structure requirements

| File | Change |
|---|---|
| `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` | Add `caller_trace_id` to 3 tools + helper validation function |
| `mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py` | Add `caller_trace_id` to 3 tools + helper |
| `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` | Add `caller_trace_id` to 5+ tools + extend `_emit()` signature + helper |
| Each `test_server.py` (3 files) | Add ~5 tests per server per AC6 |
| `tests/contract/` | Update schema round-trip tests per AC5 (file paths to be discovered during dev) |

Do **NOT** touch:
- `packages/events/src/events/envelope.py` — Story 9.1 owns it.
- `services/registry-api/*`, `services/telegram-gateway/*`, `services/console-cli/*` — Stories 9.2/9.3/9.4 own them.
- `services/worker-wrapper/*` — Story 9.6 owns it (and will be the caller of these MCP tools).
- `pyproject.toml` filterwarnings — Story 9.7 owns its removal.

### Testing requirements

- **Per-server unit tests** in `test_server.py` (≥5 per server × 3 servers = ≥15 tests).
- **At least one integration test** in `clawhip-bridge`'s `test_server.py` that calls `emit_event`, reads the JSONL log, and asserts the envelope's `trace_id` matches the supplied `caller_trace_id`.
- **Contract tests** in `tests/contract/` updated per AC5.
- Test markers: PR-gate.
- **Defense:** mirror Story 9.4 pass-2 lessons:
  - Use `is_valid_trace_id()` for validation (not just type check) — avoids whitespace/CRLF injection (Story 9.4 pass-2 S1).
  - No `assert` for production-runtime checks (Story 9.4 pass-2 S2).
  - Test names should follow `test_<tool>_<scenario>` pattern.

### Previous-story intelligence

- **Story 9.1** — `is_valid_trace_id()` is public in `events.envelope`. Use it directly.
- **Story 9.2** — registry-api receives `X-Trace-Id` over HTTP and validates. MCP servers receive `caller_trace_id` via Pydantic — same validation contract.
- **Story 9.3** — telegram-gateway derives `tg:{update_id}`; that value MAY flow into MCP tools via the worker subprocess (Story 9.6) → `caller_trace_id`. AC2 explicitly accepts the `tg:` form.
- **Story 9.4** — console-cli mints bare UUIDv7 → flows into registry-api → may flow into MCP tools via worker. The `caller_trace_id` accepts both forms (UUIDv7 or `tg:`).
- **Story 9.4 pass-2 lessons**:
  - S1: validate SHAPE not just type — `is_valid_trace_id()` rejects whitespace/CRLF.
  - S2: use `raise ValueError`, not `assert` (production-safe).
  - S6: header constants in a shared module — not directly applicable here (MCP tools use Pydantic kwargs, not HTTP headers), but the principle of clean module boundaries applies.

### Git intelligence — recent commits

```
79da039 fix(story-9.4): pass-2 second-opinion review — 13 patches batch-applied
653e2a9 fix(story-9.4): pass-1 review — 18 patches batch-applied
3b4781a chore(sprint-status): close Story 9.4 — CI green on 25975174321
b731940 feat(console-cli): Story 9.4 — mint trace_id at command entry + X-Trace-Id propagation (FR58 console)
712538e docs(story-9.4): spec — console-cli mints trace_id at command entry (FR58 console)
```

### Latest-tech notes

- **FastMCP `@mcp.tool()`** — derives input schema from function signature. Adding a kwarg-only param with no default makes it required in the schema.
- **Pydantic v2** — handles validation; rejection produces a structured error response per MCP protocol.
- **`is_valid_trace_id()`** — already supports both UUIDv7 and `tg:<int>` forms (Story 9.1).

---

## Dev notes

### Implementation sketch

`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py`:

```python
from events.envelope import is_valid_trace_id  # noqa: IMP001

def _validate_caller_trace_id(caller_trace_id: str) -> None:
    """Reject invalid caller_trace_id per Story 9.1 contract.
    
    Raises:
        ValueError: if caller_trace_id doesn't match UUIDv7 OR tg:<digits>.
    """
    if not is_valid_trace_id(caller_trace_id):
        raise ValueError(
            f"caller_trace_id must match Story 9.1 contract "
            f"(UUIDv7 or tg:<update_id>); got {caller_trace_id!r}"
        )


async def _emit(
    event_type: str,
    payload: dict[str, object],
    parent_event_id: str | None,
    caller_trace_id: str,  # NEW
) -> dict[str, str]:
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        schema_version="1.0.0",
        type=event_type,
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind=actor_kind, id=actor_id),
        payload=payload,
        parent_event_id=parent_event_id,
        trace_id=caller_trace_id,  # NEW — silences Story 9.1 DeprecationWarning
        request_id=new_request_id(clock=clock),
    )
    await writer.append(envelope)
    return {
        "event_id": envelope.event_id,
        "emitted_at": envelope.emitted_at.isoformat(),
    }


@mcp.tool()
async def emit_event(
    type: str,
    payload: dict[str, object],
    *,
    caller_trace_id: str,  # NEW — required
    parent_event_id: str | None = None,
) -> dict[str, str]:
    """Emit a typed event to the spine. Validated against REGISTRY.
    
    Args:
        caller_trace_id: Story 9.5 / FR58. Required correlation ID matching
            Story 9.1 contract (bare UUIDv7 OR ``tg:<update_id>``). Invalid
            values raise ``ValueError`` surfaced as MCP tool error.
    
    Raises:
        EventSchemaUnknown: if type not registered.
        ValueError: if caller_trace_id fails validation.
    """
    _validate_caller_trace_id(caller_trace_id)
    check_tier(
        "emit_event",
        CallerContext(actor_kind=actor_kind, actor_id=actor_id),
        TIER_MAP["emit_event"],
    )
    return await _emit(type, payload, parent_event_id, caller_trace_id)
```

### Trade-off note

Making `caller_trace_id` REQUIRED is a breaking change for any existing MCP caller. Currently:
- Worker subprocess (Story 9.6) will be the primary caller — will supply `caller_trace_id` from its `--trace-id` flag.
- Test code in `test_server.py` will need to supply a valid value (use `is_valid_trace_id`-compatible literal like `"01917e5c-a7d1-7000-8abc-0123456789ab"`).

The breaking change is intentional — FR58's contract says missing `caller_trace_id` MUST fail validation. Any existing test that doesn't supply it WILL fail; this is the expected behavior and forces the spec contract.

### Non-goals (do NOT do in 9.5)

- Implement worker-wrapper `--trace-id` flag — Story 9.6.
- Bump `schema_version` — Story 9.7.
- Add `events.trace_id` ORM column — Story 9.7.
- Implement `/trace <id>` operator query — Story 9.7.
- Remove `pyproject.toml` filterwarnings — Story 9.7.
- Touch envelope validator, HTTP middleware, telegram-gateway, console-cli.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| Breaking change to MCP tool API — any caller without `caller_trace_id` now fails. | Intentional per FR58 contract. Document migration path in Dev Agent Record (worker is the only production caller; Story 9.6 will supply it). |
| Story 9.4 pass-2 S1 lesson (CRLF/whitespace injection) — use `is_valid_trace_id()` not just type check. | AC2 explicitly says `is_valid_trace_id()`. |
| Story 9.4 pass-2 S2 lesson — no `assert` in production paths. | AC2 uses `raise ValueError`. |
| Pydantic-validation-error format may differ between FastMCP versions. | Pin the test against the installed FastMCP version. Document the expected error shape. |
| Empty-string `caller_trace_id` — `is_valid_trace_id("")` returns False, so validation rejects (good). | Add explicit test. |
| `tg:0` and leading-zero forms — `is_valid_trace_id` rejects per Story 9.1 F2. | Inherits Story 9.1's invariant. Add a regression test (optional). |
| Worker (Story 9.6) hasn't shipped yet — no integration test possible for the full chain. | AC10 explicitly defers the worker integration. Story 9.6 will close the loop. |
| Some MCP tools (`task_add_note` etc.) are Phase 1 stubs and don't emit envelopes. AC3 says "thread to every envelope.create() callsite" — for stub tools, there's no envelope to thread. | Stubs log `caller_trace_id` at INFO level so the contract is observable even without emission. When Story 5.12 lands envelope emission, the wiring is ready. |

---

## Definition of done

- All 10 ACs satisfied (AC10 explicitly noted as deferred to Story 9.6 calling side).
- `uv run pytest mcp-servers -q` shows new tests passing.
- Local full-suite parity gate green.
- CI green on push.
- Commit message follows `feat(mcp): Story 9.5 — ...` style.
- `sprint-status.yaml` `9-5-mcp-caller-trace-id-input: backlog → done`.
- Dev Agent Record filled in.
- Two-pass adversarial code review per Epic 8.x cadence.

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
_(How many DeprecationWarnings still fire after Story 9.5? Expected drop: ~5-10 from clawhip-bridge emit_* cluster.)_

### Surprises / deviations from spec
_(tbd)_

### Follow-up TODOs surfaced for Epic 9
_(tbd)_

---

## Frontmatter

```yaml
---
story_id: 9.5
story_key: 9-5-mcp-caller-trace-id-input
parent_epic: 9
phase: 2
fr_refs: [FR58]
nfr_refs: [NFR-O7]
arch_refs:
  - "trace_id propagation wiring (Mermaid §line-1117+) — MCP ingress"
  - "P2-I2 (single Phase 2 schema bump deferred to 9.7)"
estimated_hours: 5-8 (11 tool handlers + 3 contract files + 15+ tests)
priority: high (MCP ingress for Epic 9; fourth of 5 ingresses)
blocks:
  - 9.6 (worker-wrapper supplies caller_trace_id to MCP tool calls)
  - 9.7 (schema bump baseline)
blocked_by:
  - 9.1 (trace_id shape contract — done at 7cfebd9)
  - 9.2 (public is_valid_trace_id helper — done at b490e4e)
status: ready-for-dev
created: 2026-05-17
created_by: bmad-create-story skill
---
```
