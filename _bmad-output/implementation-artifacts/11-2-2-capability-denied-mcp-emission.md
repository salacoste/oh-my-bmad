# Story 11.2.2 — capability.denied MCP-boundary emission

Status: **in-progress** (Party-mode OQ resolution 2026-05-24 — OQ-1 confirmed, OQ-2 system-stamped, OQ-3 AC3-A, OQ-4 startup-spawn + PD-1)

## Story

**As** an operator relying on `omb_capability_denied_total{tier, boundary=mcp}` to alert on MCP-side capability-tier rejections and provide a forensic audit trail symmetric with the HTTP boundary,
**I want** every `CapabilityDenied` exception raised from `check_tier()` inside an MCP tool handler (`mcp-servers/task-registry/`, `mcp-servers/session-registry/`, and `mcp-servers/clawhip-bridge/` itself) to emit a `capability.denied` v1.1.0 event,
**so that** the omb_capability_denied_total counter's `boundary=mcp` cells stop sitting at 0 forever and the audit log carries a complete cross-surface record of capability-tier rejections — closing Epic 10 retro DD5 fully (Story 11.2.1 closed only the HTTP boundary).

## Background

### What Story 11.2.1 closed

Story 11.2.1 (done 2026-05-23, CI green @ c9bdd2a) emits `capability.denied` v1.1.0 from `TierEnforcementMiddleware.dispatch()`'s `except CapabilityDenied` block in `services/registry-api/src/registry_api/adapters/middleware.py:_emit_capability_denied_safe`. Counter `omb_capability_denied_total{tier=tierN, boundary=http}` now increments on every HTTP 403 deny. PD-1 fail-soft, CancelledError re-raise discipline, enum-drift contract test all in place.

### What Story 11.2.1 carved out (AC2 deferred)

MCP-boundary emission. Spec line 38 marked AC2 `[DEFERRED to Story 11.2.2]`. Sprint-status was annotated "blocked on Story 5.12 task.emit_event infrastructure". **This blocker note was investigated during Story 11.2.2 spec drafting and found to be misleading** — see "OQ-1 RESOLVED" below.

### Architectural picture

Per `_bmad-output/planning-artifacts/architecture.md` line 779: `registry-api ↔ clawhip-bridge` uses MCP stdio for "Event-emission tool calls"; "Direct registry mutation (would violate single-writer)" is forbidden. **clawhip-bridge MCP IS the event-emission single-writer surface** per FR26 / NFR-M1.

**clawhip-bridge already has the real emit path** (`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:234-265`):

```python
@mcp.tool()
async def emit_event(
    type: str,
    payload: dict[str, object],
    *,
    caller_trace_id: str,
    parent_event_id: str | None = None,
) -> dict[str, str]:
    """Emit a typed event to the spine. Validated against REGISTRY."""
    validate_caller_trace_id(caller_trace_id)
    check_tier("emit_event", CallerContext(...), TIER_MAP["emit_event"])
    return await _emit(type, payload, parent_event_id, caller_trace_id=...)
```

**MCP servers DO NOT currently call clawhip-bridge** — `task-registry` and `session-registry` have no `MCPClientGroup` wiring. The orchestrator-adapter and worker-wrapper services do (see `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py:MCPClientGroup`), so the canonical pattern is available for re-use.

**The `task.emit_event` stub** in `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py:218-261` returns `{"ok": True}` after a structured INFO log; it never writes to the event log. Story 5.12's "done" status refers to the orchestrator-adapter side (it wired clawhip-bridge for execution events). The stub remains a stub — but Story 11.2.2 doesn't need it; we route through `clawhip-bridge.emit_event` directly.

### Failure modes today (no emission)

- A task-registry MCP tool call by an under-tiered actor raises `CapabilityDenied` from `check_tier()`; the exception propagates up the MCP framework into an MCP tool-error reply; **no event emitted**, counter stays at 0.
- Same for session-registry tools and clawhip-bridge's own tools (its `check_tier("emit_event", ...)` call IS a tier gate at the spine).

## Acceptance criteria

**AC1 — Emission from task-registry MCP tool handlers.** [ ] Each tier-gated tool in `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` that calls `check_tier(...)` wraps the call in `try/except CapabilityDenied`. On the catch, the handler invokes `clawhip-bridge.emit_event` with a `capability.denied` v1.1.0 envelope (`boundary="mcp"`, `tier=<required>`, `actor_id=<caller>`, `attempted_action=<tool_name>`, `reason=exc.reason`) and **then re-raises** so MCP transport-level error semantics are preserved.

**AC2 — Emission from session-registry MCP tool handlers.** [ ] Same pattern applied to every `check_tier()` callsite in `mcp-servers/session-registry/src/session_registry_mcp/handlers/`.

**AC3 — Emission from clawhip-bridge's own `check_tier()` call.** [ ] Special case for `clawhip-bridge` (the spine itself): the `emit_event` tool at `server.py:255-260` calls `check_tier("emit_event", ...)`. If THAT raises CapabilityDenied, we cannot route through the same tool to emit the audit event (infinite recursion). Two acceptable resolutions — pick one in DAR:
  - **AC3-A**: clawhip-bridge has direct in-process access to its `EventLogWriter`. Emit the `capability.denied` envelope via the **internal `_emit` helper** (bypassing the `check_tier` gate, which is correct — the audit event is system-emitted, not actor-emitted). Re-raise the original CapabilityDenied.
  - **AC3-B**: clawhip-bridge's own denials log loudly but skip emission (documented limitation; ops-backlog item). Acceptable because clawhip-bridge denials are operator-misconfiguration-shaped, not adversarial.

**AC4 — Counter increments end-to-end (MCP).** [ ] New integration test under `tests/integration/test_capability_denied_mcp_emission.py` spawns a task-registry MCP server connected to a clawhip-bridge MCP server, denies a tier-gated tool with an under-tiered actor, asserts `omb_capability_denied_total{tier=tierN, boundary=mcp}` increments by 1 after metrics-subscriber materializes the envelope. Mirrors `tests/integration/test_capability_denied_emission.py` (Story 11.2.1).

**AC5 — Schema-version compatibility.** [ ] No change to `CapabilityDeniedPayload`, no new event-type registrations. Pure producer wiring.

**AC6 — Original MCP error semantics preserved.** [ ] The CapabilityDenied exception MUST re-raise after emission so the MCP framework returns the same tool-error reply shape it did before. Test the MCP-tool error response is byte-identical (modulo timing fields) to the pre-emission baseline.

**AC7 — PD-1 fail-soft.** [ ] Emission errors (clawhip-bridge unreachable, network timeout, ValidationError) are logged at ERROR but DO NOT block the re-raise of the original CapabilityDenied. Cancellation (`asyncio.CancelledError` / `KeyboardInterrupt`) re-raised explicitly before the broad except (Story 11.2.1 PP1 mirror-update).

**AC8 — Tier semantics.** [ ] `payload.tier` is the REQUIRED tier (denied threshold), matching Story 11.2.1's semantics and Story 10.4's counter docstring.

**AC9 — Tests added.**
- [ ] Unit: emission helper for MCP-boundary (likely shared `packages/capabilities/.../emit.py` OR per-MCP-server `_emit_capability_denied.py`).
- [ ] Unit: AC3 clawhip-bridge self-deny path (AC3-A or AC3-B chosen).
- [ ] Unit: PD-1 fail-soft — clawhip-bridge connection broken → re-raise still works.
- [ ] Unit: CancelledError re-raise (Story 11.2.1 PP1 mirror).
- [ ] Integration: end-to-end MCP-boundary → counter increment.

**AC10 — All gates green.** [ ] `ruff check`, `ruff format --check`, `mypy --strict packages/ services/registry-api services/registry-state` + MCP servers under their respective strict gates, `check_imports.py`, `check_event_registry.py`, `check_single_writer.py` (NEW emission sites must not violate FR26 — they route through clawhip-bridge which IS the single writer), `check_registry_isolation.py`, `just bootstrap-verify`, `uv run pytest -m "not slow"` — all exit 0.

## Approach options

### Option A — Per-handler decorator (selected starting point)

Introduce `@emit_capability_denied_on_deny(boundary="mcp", clawhip_client=...)` decorator in `packages/capabilities/src/capabilities/emit.py` (NEW module). Apply to every tier-gated MCP tool handler in task-registry and session-registry. The decorator:

1. Awaits the handler.
2. Catches `CapabilityDenied`.
3. Invokes `clawhip_client.emit_event(type="capability.denied", payload={...})` via the injected MCP client (fail-soft per AC7).
4. Re-raises the original exception (AC6).

Pros: localized; reusable for future MCP servers; testable independently of MCP framework.

Cons: requires plumbing a clawhip-bridge MCP client into every MCP server's handler factory; ~3-5 new mcp_clients.py-shape modules.

### Option B — `check_tier` wrapper at the package level

Modify `packages/capabilities/src/capabilities/tiers.py::check_tier` to accept an optional `emitter: CapabilityDeniedEmitter | None = None` parameter. When set + the call raises, the emitter is invoked.

Pros: single point of change for `check_tier`.

Cons: pushes I/O concern into the pure capabilities library (currently I/O-free); every call site must pass the emitter; rejected for the same reason in Story 11.2.1 Option B.

### Option C — Defer to a future cross-cutting "MCP middleware" pattern

Wait for an FastMCP-level middleware hook (similar to ASGI middleware) that intercepts every tool invocation. None exists today.

Pros: future-proof.

Cons: depends on upstream FastMCP feature; indefinite wait.

### Decision

**Option A selected.** Aligns with Story 11.2.1's helper-module pattern; doesn't couple `packages/capabilities/` to I/O; uses the existing `MCPClientGroup` shape (already proven by Story 5.12 and Story 5.1).

| Criterion | Option A (selected) | Option B | Option C |
|---|---|---|---|
| LOC delta | ~200 (3 new client wirings + decorator + tests) | ~60 + every-caller update | 0 (deferred) |
| Architecture purity | Localized to MCP layer | Couples capabilities/ to I/O | Pure but unavailable |
| FR26 compliance | YES (routes through clawhip-bridge single writer) | YES | N/A |
| Reusable for future MCP servers | YES (decorator) | YES | N/A |

## Non-goals

- **NOT** building `task.emit_event` (the stub at `task_registry_mcp/handlers/tools.py:218-261`) into a real emitter. That stub remains deferred until Story 5.12's scope is genuinely re-opened. Story 11.2.2 routes through `clawhip-bridge.emit_event` instead.
- **NOT** modifying `CapabilityDeniedPayload`, `check_tier`, or any schema registration.
- **NOT** unifying with Story 11.2.1's HTTP helper into a single cross-surface emit module yet — defer until both producers ship and patterns stabilize (Epic 12+ candidate).
- **NOT** addressing `omb_capability_denied_emission_failed_total` (Story 11.2.1 PP11 ops-backlog item).
- **NOT** Story 11.5.1 (`/key-status` Telegram command) — separate FR65a follow-up.

## Dev notes

### Files expected to touch

1. **NEW: `packages/capabilities/src/capabilities/emit.py`** — `emit_capability_denied_on_deny` decorator (or async context manager). I/O-free interface; takes a callable `Emitter = Callable[[str, dict], Awaitable[None]]` injected by the caller. Keeps capabilities/ library pure.
2. **NEW: `mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py`** — thin MCPClientGroup-shape connector to clawhip-bridge. Mirror of `services/orchestrator-adapter/.../adapters/mcp_clients.py`.
3. **NEW: `mcp-servers/session-registry/src/session_registry_mcp/adapters/clawhip_client.py`** — same.
4. **`mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py`** — apply decorator to each tier-gated tool (`task_add_note`, `task_attach_artifact`, `task_emit_event`).
5. **`mcp-servers/session-registry/src/session_registry_mcp/handlers/`** — audit + apply decorator.
6. **`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py`** — AC3 special case: emit via internal `_emit` helper, bypassing `check_tier`.
7. **`mcp-servers/task-registry/src/task_registry_mcp/app/main.py`** — wire the clawhip client into the lifespan / handler factory.
8. **`mcp-servers/session-registry/src/session_registry_mcp/app/main.py`** — same.
9. **NEW: `tests/integration/test_capability_denied_mcp_emission.py`** — end-to-end test.
10. **`tests/integration/_db_helpers.py`** — re-use Story 11.2.1 PP8 helpers.
11. **`_bmad-output/implementation-artifacts/sprint-status.yaml`** — status flips.
12. **`_bmad-output/implementation-artifacts/11-2-2-capability-denied-mcp-emission.md`** — this spec + Dev Agent Record.

### Canonical emission payload (Story 11.2.1 mirror)

```python
CapabilityDeniedPayload(
    tier=cast(_TierLiteral, _TIER_INT_TO_LITERAL[required_tier.value]),  # mirror PP3/PP5
    boundary="mcp",
    actor_id=getattr(caller, "actor_id", None) or "unknown",  # mirror PP4
    attempted_action=tool_name,  # e.g. "task.add_note"
    reason=exc.reason,
)
```

Envelope `actor.kind` for MCP boundary — see **OQ-2** below.

### MCP-client wiring pattern reference

`services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py:MCPClientGroup` is the canonical shape:

```python
class MCPClientGroup:
    async def __aenter__(self) -> MCPClientGroup: ...
    async def emit_event(self, type: str, payload: dict[str, object], **kwargs) -> dict: ...
```

Same lifecycle pattern (stack-managed via `AsyncExitStack` in the MCP server's lifespan) applies to the new task-registry / session-registry → clawhip-bridge wiring.

### AC3 — clawhip-bridge self-deny: AC3-A details

If selected (recommended), `clawhip_bridge_mcp/server.py:emit_event` wraps `check_tier("emit_event", ...)` with:

```python
try:
    check_tier("emit_event", CallerContext(...), TIER_MAP["emit_event"])
except CapabilityDenied as exc:
    # Story 11.2.2 AC3-A: emit directly via internal _emit (bypass check_tier).
    # The audit envelope is system-emitted, not actor-emitted, so the actor's
    # missing capability doesn't apply.
    await _emit(
        type="capability.denied",
        payload=_build_payload(boundary="mcp", actor=..., action="emit_event", reason=exc.reason),
        parent_event_id=None,
        caller_trace_id=caller_trace_id,
    )
    raise
```

This is safe because `_emit` writes directly to the event log; it doesn't loop back through `check_tier`.

### FR26 single-writer compliance

`scripts/check_single_writer.py` flags any `.write()` / `.append()` against event-log paths from outside `services/registry-state/`. **The new emission goes through clawhip-bridge, which is the single writer.** task-registry / session-registry never touch the event log directly — they invoke `clawhip-bridge.emit_event` via MCP RPC. No FR26 violation expected.

### Pre-existing patterns (Story 11.2.1 mirror discipline)

The following patterns from Story 11.2.1 MUST be mirrored:
- PP1: explicit `except (asyncio.CancelledError, KeyboardInterrupt): raise` before the broad PD-1 swallow.
- PP2: loud WARNING log if MCP-client context (caller_trace_id, parent_event_id) is missing — don't silently mint.
- PP3: typed `_TIER_INT_TO_LITERAL` + enum-drift contract test (reuse Story 11.2.1's `_TIER_INT_TO_LITERAL` by importing — single source of truth).
- PP4: `getattr(...) or "unknown"` for actor_id.
- PP5: `required_tier.value` not `int(required_tier)`.
- PP6: tests round-trip via `CapabilityDeniedPayload.model_validate(...)`.
- PP7: tests use `from_canonical_json`, not `json.loads`.

## References

- **Parent story (HTTP boundary):** `_bmad-output/implementation-artifacts/11-2-1-capability-denied-emission.md` (status: done, CI green @ c9bdd2a)
- **Parent grandparent:** `_bmad-output/implementation-artifacts/11-2-register-approval-signed-key-rotated-events.md` (Story 11.2 — schema + dispatch)
- **DD5 origin:** Epic 10 retrospective
- **clawhip-bridge emission tool:** `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:234-265`
- **MCPClientGroup canonical pattern:** `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py:23+`
- **Architecture line 779:** MCP→event-log via clawhip-bridge.
- **FR/NFR refs:** FR26 single-writer; NFR-M1; Epic 10 retro DD5; NFR-O8 metric discipline.

## Tasks / Subtasks

- [ ] Phase 0: Flip sprint-status.yaml `11-2-2-capability-denied-mcp-emission` → `in-progress`.
- [ ] Phase 1 — Resolve OQs (architecture review by dev): OQ-1 confirm (already resolved here), OQ-2 actor.kind selection, OQ-3 clawhip-bridge AC3-A vs AC3-B.
- [ ] Phase 2 — `packages/capabilities/src/capabilities/emit.py`: I/O-free decorator/helper module.
- [ ] Phase 3 — task-registry wiring:
  - [ ] `adapters/clawhip_client.py` (NEW MCPClientGroup shape)
  - [ ] Apply decorator to tier-gated handlers in `handlers/tools.py`
  - [ ] Lifespan wiring in `app/main.py`
- [ ] Phase 4 — session-registry wiring (same shape, audit handlers).
- [ ] Phase 5 — clawhip-bridge AC3-A: emit on self-deny via internal `_emit`.
- [ ] Phase 6 — Tests:
  - [ ] Unit: decorator (5+ tests including PD-1 fail-soft + CancelledError re-raise)
  - [ ] Unit: AC3 path
  - [ ] Integration: `tests/integration/test_capability_denied_mcp_emission.py`
- [ ] Phase 7 — Validation gates: ruff, mypy, check_imports, check_event_registry, check_single_writer (FR26!), check_registry_isolation, bootstrap-verify, pytest.
- [ ] Phase 8 — Flip sprint-status to `review`; commit + push; run `/bmad-code-review 11-2-2`.

## Dev Agent Record

**Approach selected:** Option A (per-handler decorator from
``packages/capabilities/src/capabilities/emit.py``). Foundation
module + 18 unit tests landed in commit 114d171; this pass landed
phases 3-7 (wiring + integration test).

**OQ-1 resolution:** Confirmed. No Story 5.12 dependency — Story 11.2.2
routes through ``clawhip-bridge.emit_event`` (FR26-compliant single
writer; already live since Story 2.8). The misleading
sprint-status "blocked on Story 5.12" annotation was cleared in spec
drafting.

**OQ-2 resolution:** ``Actor(kind="system", id="<server-name>-mcp")``
per spec. clawhip-bridge stamps ``Actor(kind="system", id="clawhip-bridge-mcp")``
on capability.denied envelopes via the new ``actor_override`` parameter on
``_emit`` (server.py:192). task-registry / session-registry route through
clawhip-bridge.emit_event which applies the same override when
``type == "capability.denied"``.

**OQ-3 resolution: AC3-A (in-process ``_emit`` bypass).** clawhip-bridge's
``_check_tier_with_self_emit`` (server.py:240) wraps ``check_tier``; on
``CapabilityDenied`` it calls ``_emit`` directly with
``schema_version="1.1.0"`` and ``actor_override=Actor(kind="system",
id="clawhip-bridge-mcp")``, bypassing tier enforcement for the audit
envelope itself. The helper is invoked from all 5 ``@mcp.tool()``
handlers (``emit_event``, ``emit_blocker``, ``emit_summary``,
``emit_approval_request``, ``emit_completion``).

**OQ-4 resolution: startup-spawn + lifespan-managed MCPClientGroup.**
task-registry / session-registry now accept optional
``clawhip_bridge_command`` / ``clawhip_bridge_args`` parameters; when
provided, ``build_server`` registers a FastMCP lifespan that spawns a
``ClawhipBridgeClient`` (single-connection variant of MCPClientGroup
— mcp-servers cannot share code per Story 5.8 import-graph constraint
so the adapter is duplicated per-server). The lifespan populates an
``EmitterHolder`` that the tool decorators reference; startup failure
is fail-loud (BaseException out of ``__aenter__`` propagates); mid-
request failure (broken pipe etc.) is PD-1 fail-soft per the decorator's
contract.

**Files modified:**
- NEW ``mcp-servers/task-registry/src/task_registry_mcp/adapters/__init__.py``
- NEW ``mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py``
- NEW ``mcp-servers/session-registry/src/session_registry_mcp/adapters/__init__.py``
- NEW ``mcp-servers/session-registry/src/session_registry_mcp/adapters/clawhip_client.py``
- NEW ``tests/integration/test_capability_denied_mcp_emission.py``
- MOD ``mcp-servers/task-registry/src/task_registry_mcp/app/main.py``
  (lifespan wiring + clawhip_bridge_command/args kwargs)
- MOD ``mcp-servers/task-registry/src/task_registry_mcp/__main__.py``
  (3 new env vars: CLAWHIP_BRIDGE_COMMAND/ARGS/DISABLE_AUDIT_EMISSION)
- MOD ``mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py``
  (decorator-wrapping via ``_maybe_wrap`` on all 3 tier-gated handlers)
- MOD ``mcp-servers/session-registry/src/session_registry_mcp/app/main.py`` (mirror of task-registry)
- MOD ``mcp-servers/session-registry/src/session_registry_mcp/__main__.py`` (mirror)
- MOD ``mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py`` (mirror)
- MOD ``mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py``
  - new ``_check_tier_with_self_emit`` helper (AC3-A)
  - ``_emit`` parameterized with ``schema_version`` + ``actor_override``
    (defaults preserve pre-11.2.2 behaviour for the 5 existing tools)
  - ``emit_event`` tool selects ``schema_version="1.1.0"`` and the
    system-emitter ``Actor`` for ``type == "capability.denied"``

**Test count delta:** +3 (3 new integration tests in
``tests/integration/test_capability_denied_mcp_emission.py``). Foundation
18 unit tests already landed in commit 114d171. Total Story 11.2.2
contribution = 21 tests (foundation + integration). All 3 pass + 174
existing task-registry/session-registry/clawhip-bridge tests stay
green; full ``pytest -m "not slow"`` = 3114 passed, 3 skipped.

**Mypy delta:** canonical strict gate (``mypy --strict packages/
services/registry-api services/registry-state``) Success: 121 source
files, 0 errors — unchanged from baseline. MCP servers are not in the
canonical strict gate (mypy.ini config); pre-existing 28 informational
errors there are unaffected by this work modulo:
- 3 NEW informational errors for ``capabilities.emit`` import-untyped
  (mirrors the pre-existing ``capabilities`` import-untyped — no py.typed
  marker; out of 11.2.2 scope to add)
- 2 NEW informational ``no-any-return`` errors in ``handlers/tools.py``'s
  ``_maybe_wrap`` helper (lambda fallthrough path). Both are confined to
  the same files that already carry untyped suppressions.

**Deviations from spec:**
- AC4 integration test uses an in-process emitter adapter (calls
  clawhip-bridge's ``emit_event`` tool fn directly) instead of a pure
  stdio MCP-to-MCP subprocess harness. Rationale documented in the test
  file's module docstring: stdio subprocess plumbing requires
  process supervision + ready signaling that exceeds the wiring
  validation the test is meant to provide. The stdio entry point itself
  is already exercised by each MCP server's existing ``TestEntryPoint``
  subprocess tests. The in-process variant exercises the full
  producer→writer→consumer chain (decorator → EmitterHolder →
  clawhip-bridge emit_event tool → EventLogWriter → JSONL on-disk →
  metrics-subscriber update_for → Prometheus counter increment).
- ``_emit`` in clawhip-bridge was extended with two new optional
  parameters (``schema_version``, ``actor_override``) rather than
  duplicating the envelope-build logic in
  ``_check_tier_with_self_emit``. This was necessary because
  ``capability.denied`` is registered ONLY at v1.1.0
  (``services/registry-state/src/registry_state/domain/event_types.py:275``);
  the default ``schema_version="1.0.0"`` would fail
  ``EventEnvelope.create``. Defaults preserve every existing tool
  call's pre-11.2.2 behaviour byte-identically.
- A new ``non_v1_0_0_schema_versions`` map (server.py:189) lives in the
  clawhip-bridge build_server closure to drive the ``emit_event`` tool's
  schema-version selection. Future audit-only event types added at
  v1.1.0+ should be added there.

## Open questions — RESOLVED Party-mode 2026-05-24

- **OQ-1 — RESOLVED in spec drafting.** Story 5.12 wired orchestrator-adapter→clawhip-bridge; `task.emit_event` stub is unrelated. Story 11.2.2 routes through `clawhip-bridge.emit_event` (FR26-compliant single-writer; already live). No Story 5.12 dependency.
- **OQ-2 — RESOLVED:** `Actor(kind="system", id="<server-name>-mcp")`. Mirrors Story 11.2.1 HTTP (`registry-api`) and Story 11.5 (`registry-api`). Specific IDs:
  - task-registry → `id="task-registry-mcp"`
  - session-registry → `id="session-registry-mcp"`
  - clawhip-bridge (self-deny) → `id="clawhip-bridge-mcp"`
  - Caller identity lives in `payload.actor_id` (subject), not envelope.actor (emitter).
- **OQ-3 — RESOLVED: AC3-A (in-process `_emit` bypass).** Symmetry > simplicity. AC3-B's "rare → acceptable" reasoning rots — future MCP servers with their own `check_tier()` calls would silently drop audits. AC3-A makes the pattern universal.
- **OQ-4 — RESOLVED: (a) startup-spawn + lifespan-managed + fail-loud on startup / PD-1 fail-soft during requests.** Mirrors `MCPClientGroup` pattern from Story 5.1 + 5.12. Lazy-connect (b) hides the dependency; per-request (c) thrashes connections.
- **OQ-5 — NEW, flagged for ops-backlog.** clawhip-bridge restart while task-registry/session-registry are connected: PD-1 fail-soft catches the broken pipe (re-raises CapabilityDenied correctly), but emissions silently drop until next server-restart. Future enhancement: watchdog reconnect with exponential backoff. Out of 11.2.2 scope; tracked alongside Story 11.2.1 PP11 (`omb_capability_denied_emission_failed_total`).

## Frontmatter

```yaml
---
story_id: 11.2.2
parent_epic: 11
parent_story: 11.2.1
phase: 2
priority: medium
estimated_hours: 8-14 (3 wiring sites + decorator + ~12 tests; OQ-3 picks an arm of 2-4h difference)
blocks: nothing
blocked_by: nothing (OQ-1 cleared 2026-05-24 — `task.emit_event` stub was not the actual blocker; clawhip-bridge.emit_event is the canonical FR26-compliant emission surface and is already live)
status: ready-for-dev
created: 2026-05-24
created_by: bmad/Claude (Story 11.2.1 scope-carveout follow-up + OQ-1 clarification on misleading blocker note)
predecessor_commits: c9bdd2a (Story 11.2.1 pass-1 review), f838b7d (Story 11.2.1 close)
ddo: Epic 10 retro DD5 — MCP boundary half of the counter
---
```
