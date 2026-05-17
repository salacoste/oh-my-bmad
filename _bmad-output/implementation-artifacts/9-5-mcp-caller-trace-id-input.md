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

### Implementation summary

All 11 `@mcp.tool()` handlers across the 3 MCP servers now accept `caller_trace_id: str` as a kwarg-only required parameter, validate it via a byte-identical `_validate_caller_trace_id(...)` helper using `events.envelope.is_valid_trace_id` (Story 9.4 pass-2 S1 shape-validation, raises `ValueError` per pass-2 S2), and either thread it to `EventEnvelope.create(trace_id=...)` (clawhip-bridge's 5 emit_* tools) or log it at INFO level (task-registry/session-registry Phase 1 stubs). The `_emit()` helper in clawhip-bridge gained a `caller_trace_id` positional, silencing the Story 9.1 DeprecationWarning for the 5-tool cluster.

AC1, AC2, AC3, AC4, AC5, AC6, AC8, AC9 verified locally with `uv run pytest`. AC7 measured (see below). AC10 explicitly deferred — worker-side calling will land in Story 9.6.

### Files changed

- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` — added `_validate_caller_trace_id` helper, extended `_emit(...)` signature with `caller_trace_id` (now passed as `trace_id=` to `EventEnvelope.create`), made `caller_trace_id` a required kwarg-only param on all 5 `@mcp.tool()` handlers (`emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion`); imported `is_valid_trace_id` from `events.envelope`.
- `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` — added byte-identical `_validate_caller_trace_id` helper, added `caller_trace_id` kwarg to 3 Phase-1-stub tools (`task_add_note`, `task_attach_artifact`, `task_emit_event`), logs `caller_trace_id` at INFO.
- `mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py` — same pattern for 3 tools (`session_register`, `session_heartbeat`, `session_close`).
- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py` — updated 15 existing call sites to supply `caller_trace_id=_VALID_TRACE_ID`; added 20 new tests covering AC1/AC2/AC3/AC6/AC9 across `TestCallerTraceIdValidationHelper`, `TestCallerTraceIdEmitEvent`, `TestCallerTraceIdTypedEmitTools`, `TestCallerTraceIdToolSchemas`.
- `mcp-servers/task-registry/src/task_registry_mcp/test_server.py` — updated 11 existing call sites; added 14 new tests.
- `mcp-servers/session-registry/src/session_registry_mcp/test_server.py` — updated 12 existing call sites; added 16 new tests.
- `tests/contract/test_placeholder.py` — upgraded from a single skipped placeholder into 9 real round-trip tests covering AC5 (schema shape + helper positive/negative validation across all 3 servers).

### Test count delta

- Pre-9.5 PR-gate suite (`uv run pytest packages/ services/ mcp-servers/ tests/contract -q -m "not slow" --co`): **2438 tests collected**.
- Post-9.5: **2508 tests collected** (delta **+70**, vs. spec's expected +15-25). The overshoot comes from `pytest.mark.parametrize`-driven negative-case fanout in the 3 `TestCallerTraceIdValidationHelper` classes (~6 cases each × 3 servers = 18 extra) and the 9 new contract tests.
- All 2505 PR-gate tests pass (3 skipped, 5 deselected — all pre-existing).

### Callsite-warning observation

`uv run pytest mcp-servers/ -q --override-ini='filterwarnings=default' -m "not slow" 2>&1 | grep "EventEnvelope created without trace_id" | wc -l`:
- **Pre-9.5: 3 raw warning emissions** (mcp-servers/ scope only).
- **Post-9.5: 2 raw warning emissions** (-1).

Full-suite (`packages/ services/ mcp-servers/`):
- **Pre-9.5: 97**, **Post-9.5: 97** (unchanged at the aggregate level).

The 97 baseline is dominated by `services/clawhip-daemon/`, `services/registry-state/`, and other non-9.5 codepaths that still construct envelopes without `trace_id`. The clawhip-bridge `_emit()` cluster's contribution to the per-test sample (one per location-per-test) collapsed from 3 (the 3 distinct mcp-servers test files that exercise emit-tool code paths) to 2 (the same files but with one path now silenced — the remaining 2 are unrelated test-fixture `EventEnvelope.create(...)` calls inside `TestApprovalLookup` that seed approval events without `trace_id`). The spec's "expected ~5-10 drop" was framed in per-source-location terms but per-line collapse depends on whether warnings are deduplicated; in our `pytest -W default` mode they ARE deduplicated per source-location, so the visible drop is small. The mechanism is correctly wired — Story 9.7's filterwarnings removal will turn this into a hard failure if any clawhip-bridge `_emit` path regressed (it has not).

### Surprises / deviations from spec

- **Plus-70 tests, not +15-25.** Parametrized validation-helper tests fan out; this is preferable to a flat handful because it documents specific shape-contract invariants (whitespace/CRLF guard, leading-zero rejection, empty string, `tg:0`). Story 9.4 pass-2 S1 lesson explicitly motivates the shape-validation matrix.
- **Contract test file path retained.** Spec said "tests/contract/" with paths "to be discovered during dev". The only file there was `test_placeholder.py` (Story 1.5 scaffold + Story 2.8 placeholder). Upgraded in place rather than creating a new file — the placeholder sentinel `test_placeholder()` is preserved for backward-compat.
- **Helper duplicated byte-identically across 3 files.** Confirmed in `_validate_caller_trace_id` body — same docstring, same `is_valid_trace_id` import, same `raise ValueError(...)` message. Cross-referenced via the cross-server `test_caller_trace_id_negative_round_trip_rejected` parametrized test in `tests/contract/test_placeholder.py` which imports all 3 helpers and asserts they raise consistently.
- **Pre-existing un-committed modifications.** The tree had pre-existing modifications in `packages/secret-hygiene/`, `services/clawhip-daemon/`, `services/registry-state/` unrelated to Story 9.5. Left untouched.

### Follow-up TODOs surfaced for Epic 9

- **Story 9.6**: worker-wrapper must pass `--trace-id` via CLI flag to Claude Code; the MCP-tool-side `caller_trace_id` contract is now ready to receive.
- **Story 9.7**: schema_version bump + filterwarnings removal — once non-mcp-server callsites also stop emitting, the full-suite DeprecationWarning count will drop to ~0 and the filter can be removed.
- **Phase 2 stubs**: `task-registry` and `session-registry` tools currently log `caller_trace_id` at INFO but don't yet emit envelopes. When Story 5.12 wires these to the event spine, the logged `caller_trace_id` must flow into `EventEnvelope.create(trace_id=...)` (the contract is observable in the logs; the threading just needs to land).

---

## Review Findings

### Pass-1 review (adversarial 3-lane) — 2026-05-17

Three-lane review (Blind Hunter / Edge-Case Hunter / Acceptance Auditor) surfaced 16 unique findings. All 16 applied in a single follow-up commit (`fix(story-9.5): pass-1 review — 16 patches batch-applied`). Resolution table below.

| ID | Severity | Lane | Finding | Resolution | Files |
|---|---|---|---|---|---|
| T1 | HIGH | Edge H1 | Test fixture stubs (`auto_approval_stub`, `scripted_worker_stub`) call `session.call_tool("emit_event", ...)` without `caller_trace_id`. FastMCP Pydantic validation rejects the call before the tool body runs — Journey 1/3/6 + S1/S2 slow-lane integration tests hard-fail. | Added `from events import new_uuid7`; generated `_STUB_TRACE_ID = new_uuid7()` at module level; injected `args["caller_trace_id"] = _STUB_TRACE_ID` before every `call_tool()` invocation. Added Story 9.5 comment. | `tests/fixtures/auto_approval_stub/auto_approval_stub.py`, `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` |
| T2 | HIGH | Blind HIGH-1 | No test enforced byte-identical body sync of `_validate_caller_trace_id` across 3 servers — docstring NOTE clauses differ (each names the other siblings), so a naïve source-compare would fail and logic drift between servers wouldn't be caught. | Added `test_validate_caller_trace_id_byte_identical_across_servers` in contract test: strips docstring via AST, unparses body, asserts all 3 are equal. | `tests/contract/test_mcp_tool_schemas.py` |
| T3 | HIGH | Blind HIGH-2 + Edge H4 | Negative parametrize in contract test had only 4 entries (`""`, `"bad-format"`, `"tg:"`, `"tg:0"`). CRLF/whitespace vectors — the Story 9.4 pass-2 S1 lesson the spec explicitly cites — were absent. Per-server tests also had 6-entry lists vs bridge's 8. | Extended all 4 parametrize lists (contract + 3 per-server) to 11 entries: added trailing LF, leading space, trailing tab, CRLF, CRLF-injection attempt, `"not-a-uuid"`, `"tg:abc"`. Central source: `tests/contract/_trace_id_vectors.py`. | `tests/contract/test_mcp_tool_schemas.py`, 3× `test_server.py` |
| T4 | HIGH | Edge H2 | `_validate_caller_trace_id` has a private leading underscore but is imported cross-module in contract tests, tripping Ruff `SLF001` and coupling tests to a private symbol. These helpers are part of the public tool-validation contract. | Dropped the leading underscore across all 3 server files (renamed to `validate_caller_trace_id`). Updated all import sites (3× `test_server.py`, contract test, 3× production tools). | All 6 server + test files |
| T5 | HIGH | Blind HIGH-3 | `caller_trace_id` logged via printf-style (`%s` args) in 6 stub handler log calls. Not structured → can't be filtered/queried; `tg:` form is a low-grade PII leak; no defense-in-depth comment. | Switched all 6 log calls to stdlib structured `extra=` dict form. Added `# Story 9.5 pass-1 T5/T15` comment above each. | `session-registry/.../handlers/tools.py`, `task-registry/.../handlers/tools.py` |
| T6 | MEDIUM | Blind MEDIUM-4 | `_emit()` trusted upstream validation — a future internal caller bypassing `validate_caller_trace_id` at the tool boundary could pass garbage to `EventEnvelope.create`. | Added `validate_caller_trace_id(caller_trace_id)` call at top of `_emit()` with belt-and-braces comment. | `clawhip-bridge/.../server.py` |
| T7 | MEDIUM | Blind MEDIUM-5 + Edge H7 | Schema-required tests in session-registry + task-registry looped over ALL tools with `assert "caller_trace_id" in tool.inputSchema["required"]` — breaks the day a read-only tool is added. | Replaced with whitelist loop using `_SESSION_FR58_TOOLS` / `_TASK_FR58_TOOLS` frozensets + membership guard. Also updated clawhip-bridge and contract tests. Added assertion that all expected tools are present. | All 4 test files |
| T8 | MEDIUM | Blind MEDIUM-6 + Edge H9 | `TestFastMCPIntegration` exercised Python kwarg path only. The MCP wire path (`mcp.call_tool(...)` with missing field in JSON dict) was not tested — AC4's "validation error surfaced" claim was unverified for the production failure mode. | Added `test_emit_event_missing_caller_trace_id_in_json_payload_raises` in contract test: uses `mcp.call_tool()` inside `mcp._mcp_server.lifespan()`, asserts exception mentioning `caller_trace_id`. | `tests/contract/test_mcp_tool_schemas.py` |
| T9 | MEDIUM | Edge H3 | Negative parametrize vectors drifted: bridge had 8 entries, session/task had 6. Central vectors file didn't exist. | Created `tests/contract/_trace_id_vectors.py` with `INVALID_TRACE_IDS` (11 entries), `VALID_TG_BOUNDARY_TRACE_IDS`, `INVALID_TG_BOUNDARY_TRACE_IDS`. Contract test imports from it; per-server tests inline the same 11 entries with comments pointing to the source. | `tests/contract/_trace_id_vectors.py` (new) |
| T10 | MEDIUM | Blind LOW-9 + Edge H6 | `_VALID_TG_TRACE_ID = "tg:42"` magic duplicated across 4 test files. Cross-test bleed concern where envelope.trace_id assertions might silently share values. | Added `VALID_TG_TRACE_ID` constant to `_trace_id_vectors.py`; contract test imports it. Per-server tests retain their own `_VALID_TG_TRACE_ID = "tg:42"` local constants (import-graph constraint forbids mcp-servers from importing tests/contract). Documented in `_trace_id_vectors.py` docstring. | `tests/contract/_trace_id_vectors.py` |
| T11 | MEDIUM | Edge H5 | `assert "caller_trace_id" in tool.inputSchema["required"]` raises `KeyError` if `"required"` absent, producing a confusing error rather than a clear assertion failure. Missing `properties` check too. | Replaced all 4 sites with defensive helper `_assert_ctid_required` / `_assert_caller_trace_id_required` using `.get()` with clear failure messages. Also asserts `properties.caller_trace_id.type == "string"`. | All 4 test files |
| T12 | LOW | Blind LOW-8 | `tests/contract/test_placeholder.py` filename misleading — the file now contains 6+ real contract tests. | Renamed to `tests/contract/test_mcp_tool_schemas.py` via `git mv` (preserves history). Sentinel `test_placeholder()` function retained for git-bisect backward-compat. | `tests/contract/` |
| T13 | LOW | Blind LOW-10 | `tg:` boundary tests missing from all negative and positive test suites. The Story 9.1 regex admits `[1-9][0-9]{0,18}` with `int(update_id) ≤ INT64_MAX`; the boundary at 19-digit signed int64 max was untested. | Added `VALID_TG_BOUNDARY_TRACE_IDS` (`tg:1`, `tg:9999999999`, `tg:9223372036854775807`) and `INVALID_TG_BOUNDARY_TRACE_IDS` (`tg:01`, `tg:-1`, `tg:18446744073709551615` [u64 max, exceeds signed int64]) in `_trace_id_vectors.py`. Parametrized into contract tests. Note: u64 max is in the INVALID list, not VALID, because the Story 9.1 regex limits to 19 digits ≤ INT64_MAX. | `tests/contract/_trace_id_vectors.py`, `tests/contract/test_mcp_tool_schemas.py` |
| T14 | LOW | Blind LOW-11 | AC7's DeprecationWarning claim was only prose in Dev Agent Record — no test locked the invariant. Story 9.7 will remove the `pyproject.toml` filterwarnings entry; without a test, the filter would be the only thing hiding regressions. | Added `test_emit_tools_do_not_emit_deprecation_warning` — parametrized over all 5 emit_* tools, calls each via `fn(**kwargs)` inside `warnings.catch_warnings()` / `simplefilter("error", DeprecationWarning)`. | `tests/contract/test_mcp_tool_schemas.py` |
| T15 | LOW | Edge H8 | Validate-before-log invariant unmarked in handler bodies — covered as one-line comment in T5's structured-logging fix. | Resolved by T5 (comment added above each log call). | Merged into T5 |
| T16 | LOW | Edge H10 | `_emit()` accepted `caller_trace_id` as a positional 4th argument — positional drift could reorder the field silently across 5 callsites. | Changed `_emit()` signature to kwarg-only via `*, caller_trace_id: str`. Updated all 5 callsites to pass `caller_trace_id=caller_trace_id`. Merged with T6 fix. | `clawhip-bridge/.../server.py` |

**Test count delta after pass-1 batch-apply:**

| Suite | Pre-patch (2505) | Post-patch | Δ |
|---|---|---|---|
| `mcp-servers/` (`-m "not slow"`) | ~174 | 174 | ±0 (mcp-servers tests unchanged in count — replacements) |
| `tests/contract/` | 9 | 30 | **+21** |
| Full PR-gate (`packages/ services/ mcp-servers/ tests/contract/`) | 2505 | 2538 | **+33** |

The +33 delta (vs spec estimate +10-15) comes from: parametrized 11-vector negative list × 3 servers (+15 across per-server `test_rejects_invalid_shapes`), T13 boundary vectors (+5 contract parametrize), T14 DeprecationWarning parametrize (+5 contract), T2/T8 new single tests (+2). All Epic 8.7 baseline gates remain green (ruff check, ruff format, mypy --strict 97-file, check_imports, check_single_writer, secret-hygiene full-tree).

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
