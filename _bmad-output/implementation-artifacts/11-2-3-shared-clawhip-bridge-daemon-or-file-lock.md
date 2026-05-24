# Story 11.2.3 — clawhip-bridge multi-writer safety + dedicated audit-forwarding tool

Status: **review** (all 9 ACs satisfied; CI green @ 14a5f9e (run 26347222697) 2026-05-24; ready for `/bmad-code-review 11-2-3`)

## Story

**As** an operator who wants to enable MCP-boundary `capability.denied` audit emission (`OMB_MCP_AUDIT_EMISSION_ENABLED=1`) without architectural worry,
**I want** clawhip-bridge to be safe under concurrent multi-process writes to the event log AND a dedicated audit-forwarding tool that closes the PQ9 forgery vector,
**so that** Story 11.2.2's feature flag can be flipped to default-ON in production with FR26 single-writer compliance restored AND attacker-controlled `capability.denied` payloads can no longer be laundered through the public `emit_event` tool.

## Background

### What Story 11.2.2 left behind

Two issues caused Story 11.2.2 to ship the feature flag default-OFF:

1. **PQ1 FR26 multi-writer violation** (Edge Hunter pass-1 P0):
   Each MCP server (`task-registry`, `session-registry`, `orchestrator-adapter`, `worker-wrapper`) that needs to emit events to the spine spawns its OWN clawhip-bridge subprocess via stdio MCP transport. Each subprocess has its own `EventLogWriter` instance writing to `/var/lib/oh-my-bmad/registry/events/YYYY-MM-DD.jsonl`. Architecture line 779 documents "registry-api ↔ clawhip-bridge" as a 1:1 relationship, but the post-11.2.2 reality is N:1 (or rather, N:N — N writers all writing to 1 file). The kernel's `O_APPEND` atomicity protects individual sub-`PIPE_BUF` line writes from interleaving, but the documented FR26 invariant "single writer" is structurally violated.

2. **PQ9 audit-forgery vector** (Edge Hunter pass-1 MED, pass-2 reverted-with-known-limitation):
   clawhip-bridge's PUBLIC `emit_event` MCP tool accepts `type="capability.denied"` from any tier-1-or-better caller. The `_emit_overrides` lookup stamps the envelope with `Actor(kind="system", id="clawhip-bridge-mcp")` AND uses `schema_version="1.1.0"`. An attacker connected to clawhip-bridge can call this tool with a fully attacker-controlled payload — the envelope.actor is forged, looking like a real system-emitted audit. PQ9's pass-1 fix (reject the type) broke the LEGITIMATE forwarding path that task-registry / session-registry need, so it was reverted. The current state ships with the forgery vector documented as known limitation.

### Architectural picture

- `services/registry-state/src/registry_state/adapters/event_log.py:227-296` — `EventLogWriter` uses `O_APPEND` + `os.fdatasync` per write. Intra-process `asyncio.Lock` guards. NO inter-process locking.
- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:_emit` — the single writer surface. Currently each subprocess has its own.
- MCP stdio transport (Architecture line 938 P2-I4) is the current binding. Remote MCP (HTTP/SSE) deferred to Phase 3.

## Acceptance criteria

**AC1 — Multi-writer safety via fcntl file lock.** [ ] `EventLogWriter.append()` acquires an exclusive `fcntl.flock(LOCK_EX)` on its file descriptor before each write, releases on completion. Inter-process serialization guarantees: concurrent clawhip-bridge subprocesses appending to the same daily JSONL file produce well-formed line-by-line output with NO interleaving regardless of write size (no longer dependent on `PIPE_BUF` atomicity).

**AC2 — Lock contention metrics.** [ ] New counter `omb_event_log_lock_wait_ms` (Histogram with buckets `[0.1, 1, 10, 100, 1000]`) records lock-acquisition latency. Story 10.4 pre-population pattern: pre-populated with zero values. Pre-registration in `metrics-subscriber/app/metrics.py` to avoid Story 11.2 pass-1 P1-H2-style routing gaps.

**AC3 — Dedicated audit-forwarding tool.** [ ] New clawhip-bridge MCP tool `forward_capability_denied_audit(payload, *, caller_trace_id, caller_actor_kind, caller_actor_id)`:
- Tier-gated to require `Tier.ONE` (same as `emit_event` today).
- ADDITIONALLY restricts callers to `actor_kind in {"orchestrator", "worker", "system", "clawhip"}` (rejects `operator` — operators don't forward MCP-boundary audits; they emit via HTTP boundary which has its own Story 11.2.1 path).
- Validates `caller_actor_kind` against the connecting actor's CONFIGURED `actor_kind` (a `worker`-configured MCP server cannot claim to be forwarding on behalf of `operator`).
- Stamps envelope.actor as `Actor(kind="system", id="clawhip-bridge-mcp")` (preserves Story 11.2.2 OQ-2 semantic).
- Sets payload's `actor_id` to the validated `caller_actor_id` (caller cannot forge `payload.actor_id`).

**AC4 — Public `emit_event` rejects `capability.denied`.** [ ] With AC3's dedicated tool live, restore Story 11.2.2 pass-1 PQ9's rejection in public `emit_event`: `type in _emit_overrides → raise PermissionError`. The legitimate forwarding path now goes through `forward_capability_denied_audit`. The known-limitation test from Story 11.2.2 pass-2 (`test_emit_event_capability_denied_known_limitation`) is REPLACED by a forgery-rejection test.

**AC5 — task-registry / session-registry adapters switch to the dedicated tool.** [ ] Both `ClawhipBridgeClient.emit_event` invocations for `capability.denied` are routed to `forward_capability_denied_audit`. The decorator's `emitter` callable wraps the new tool. Other event types still use `emit_event`.

**AC6 — Feature flag flipped to default-ON.** [ ] After AC1-AC5 land + CI green, `OMB_MCP_AUDIT_EMISSION_ENABLED` default flips from `"" → 0 → OFF` to default ON. Legacy `*_DISABLE_AUDIT_EMISSION=1` still works as an operator kill-switch. Story 11.2.2's pass-1 PQ1 mitigation is now redundant; feature can ship operationally.

**AC7 — All Story 11.2.2 invariants preserved.**
- AC6 from 11.2.2 (re-raise CapabilityDenied): preserved by AC5 (decorator semantics unchanged).
- AC7 from 11.2.2 (PD-1 fail-soft): preserved.
- PP1 env-allowlist + PP2 shutdown-race + PP4-PP10 + PP15 from pass-2: all preserved.

**AC8 — Tests added.**
- [ ] Unit: `EventLogWriter.append()` acquires + releases lock; lock contention measured.
- [ ] Unit: `forward_capability_denied_audit` rejects `caller_actor_kind="operator"` + rejects mismatched caller_actor_kind vs server configured actor_kind.
- [ ] Unit: public `emit_event(type="capability.denied")` raises `PermissionError`.
- [ ] Integration: two clawhip-bridge subprocesses appending concurrently to the same JSONL file produce N lines (no truncation, no interleaving).
- [ ] Integration: end-to-end audit-forwarding via the new tool — counter increments end-to-end like Story 11.2.2's AC4 but with the dedicated path.

**AC9 — All gates green.** [ ] ruff, ruff format, mypy --strict, all 4 check scripts, `just bootstrap-verify`, `pytest -m "not slow"` — exit 0.

## Approach options

### Option A — File-lock in EventLogWriter (RECOMMENDED, lowest blast radius)

Add `fcntl.flock(fd, fcntl.LOCK_EX)` around the existing `os.write` in `EventLogWriter._sync_append_impl`. On Linux + macOS, this is an advisory lock that all conforming `flock`-using processes respect. The lock is released when the fd is closed OR explicitly via `LOCK_UN`. With each `append()` call holding `LOCK_EX` for the duration of the `write + fdatasync`, inter-process serialization is guaranteed.

| | LOC delta | Risk | Throughput impact |
|---|---|---|---|
| Option A | ~30 in `event_log.py` + tests | Lock contention under high concurrent write load | ~1-10μs per write under low contention; can grow under contention |

**Why this is the right call:** The MCP transport stays stdio, the architecture invariants (Architecture line 779/938) are preserved, and the fix is local to one file. Operators benefit immediately from FR26 compliance once the flag is on.

### Option B — Shared clawhip-bridge daemon

A LONG-RUNNING clawhip-bridge process started by compose/systemd holds the ONLY `EventLogWriter`. All MCP clients connect via a network socket (Unix domain socket OR HTTP/SSE per Architecture P2-I4 deferral).

Trade-off: requires switching MCP transport from stdio to HTTP/SSE OR building a custom Unix-socket multiplexer. Architecture line 1089 explicitly defers Remote-MCP (HTTP/SSE) to Phase 3. Out of scope for Phase 2.

### Option C — Hybrid (lock + dedicated tool)

Land Option A's file-lock immediately (closes FR26) AND the dedicated `forward_capability_denied_audit` tool (closes PQ9). Defer Option B to a future epic.

**Selected: Option C.** Lowest-risk path; closes both pass-2 carved-out concerns.

## Non-goals

- **NOT** switching MCP transport from stdio to HTTP/SSE (Phase 3 per Architecture P2-I4).
- **NOT** consolidating clawhip-bridge into a shared daemon — that's Option B, deferred.
- **NOT** changing the EventLogWriter's `O_APPEND` + `fdatasync` discipline — additive change only.
- **NOT** new event types or schema changes.
- **NOT** removing the Story 11.2.2 PQ1 feature-flag kill-switch — `OMB_MCP_AUDIT_EMISSION_ENABLED=0` and `*_DISABLE_AUDIT_EMISSION=1` remain as operator escape hatches.
- **NOT** addressing Story 11.2.1 PP11 (`omb_capability_denied_emission_failed_total` ops-backlog) — separate concern.

## Dev notes

### Files expected to touch

1. **`services/registry-state/src/registry_state/adapters/event_log.py`** — `_sync_append_impl` wraps `os.write + os.fdatasync` with `fcntl.flock(fd, LOCK_EX)` / `LOCK_UN`. Measure lock-wait time and record via injected metrics counter (or log if no counter available).
2. **`packages/events/`** — no changes (the writer lives in registry-state, not the package).
3. **`services/metrics-subscriber/src/metrics_subscriber/app/metrics.py`** — pre-register `omb_event_log_lock_wait_ms` Histogram. Story 10.4 pattern.
4. **`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py`** — new `forward_capability_denied_audit` tool (AC3). Restore PQ9 `PermissionError` on public `emit_event(type="capability.denied")` (AC4).
5. **`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py`** — replace `test_emit_event_capability_denied_known_limitation` with `test_emit_event_capability_denied_rejected_now_that_forward_tool_exists`.
6. **`mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py`** — `ClawhipBridgeClient.emit_event` (or a new `forward_capability_denied`) routes capability.denied through the new tool. Sibling change in session-registry.
7. **`mcp-servers/{task,session}-registry/src/.../__main__.py`** — flip `OMB_MCP_AUDIT_EMISSION_ENABLED` default to ON (AC6). Operators can still disable via legacy kill-switch.
8. **`tests/integration/test_capability_denied_mcp_emission.py`** — update the in-process adapter to route through the new tool.
9. **NEW: `tests/integration/test_event_log_multi_writer_safety.py`** — spawns 2 (or more) clawhip-bridge subprocesses concurrently emitting; asserts line count matches sum of intended writes + no truncation/interleaving.
10. **`_bmad-output/implementation-artifacts/11-2-2-capability-denied-mcp-emission.md`** — append a closure note linking to this story.
11. **`_bmad-output/implementation-artifacts/sprint-status.yaml`** — `11-2-3-shared-clawhip-bridge-daemon-or-file-lock` status flips.

### `fcntl.flock` discipline

```python
import fcntl
import time

# Inside _sync_append_impl, BEFORE write:
lock_start = time.monotonic_ns()
fcntl.flock(self._fd, fcntl.LOCK_EX)
lock_wait_ms = (time.monotonic_ns() - lock_start) / 1_000_000

try:
    os.write(self._fd, data)
    _fdatasync(self._fd)
finally:
    fcntl.flock(self._fd, fcntl.LOCK_UN)

# Inject lock_wait_ms into metrics (or log if metrics injection not available).
```

**Important:** `fcntl.flock` is per-file-descriptor (not per-file), so each process holds an independent FD. The kernel's flock semantics serialize across FDs to the same underlying inode. Linux + macOS both support this.

`fcntl.flock` is NOT supported on Windows. The codebase targets Linux (CI) + macOS (dev); Windows is not a supported runtime. Add a startup warning if `fcntl` import fails (for the sake of defensive completeness).

### `forward_capability_denied_audit` tool signature

```python
@mcp.tool()
async def forward_capability_denied_audit(
    payload: dict[str, object],
    *,
    caller_trace_id: str,
    caller_actor_kind: ActorKind,
    caller_actor_id: str,
    parent_event_id: str | None = None,
) -> dict[str, str]:
    """Forward a capability.denied audit event from an upstream MCP server.

    Authorized callers: orchestrator-adapter, worker-wrapper, task-registry,
    session-registry MCP servers (kind in {orchestrator, worker, system,
    clawhip}). NOT for operator-kind callers — operators emit via the
    HTTP-boundary path (Story 11.2.1).

    The caller MUST claim a caller_actor_kind matching its CONFIGURED
    actor_kind (the kind it was launched with). This prevents a
    worker-configured MCP server from forging an audit claiming to be
    forwarded on behalf of an operator.
    """
    validate_caller_trace_id(caller_trace_id)
    # Tier gate (Tier.ONE, same as emit_event).
    check_tier("forward_capability_denied_audit", CallerContext(...), TIER_MAP["forward_capability_denied_audit"])
    # AC3: reject operator kind explicitly.
    if caller_actor_kind == "operator":
        raise PermissionError(
            "forward_capability_denied_audit not available for operator kind — "
            "use the HTTP boundary (Story 11.2.1)"
        )
    # AC3: caller_actor_kind must match configured actor_kind.
    if caller_actor_kind != actor_kind:
        raise PermissionError(
            f"caller_actor_kind={caller_actor_kind!r} does not match "
            f"configured actor_kind={actor_kind!r}"
        )
    # Validate payload structure (CapabilityDeniedPayload), force payload.actor_id
    # to the validated caller_actor_id (caller cannot forge subject identity).
    payload["actor_id"] = caller_actor_id
    return await _emit(
        type="capability.denied",
        payload=payload,
        parent_event_id=parent_event_id,
        caller_trace_id=caller_trace_id,
        schema_version="1.1.0",
        actor_override=_SYSTEM_EMITTER,
    )
```

### TIER_MAP entry

Add `"forward_capability_denied_audit": Tier.ONE` to `mcp-servers/clawhip-bridge/.../server.py:TIER_MAP`.

### Adapter changes

`ClawhipBridgeClient.emit_event` keeps its current signature for non-audit events. Add a new method:

```python
async def forward_capability_denied(
    self,
    payload: dict[str, object],
    *,
    caller_trace_id: str | None = None,
    parent_event_id: str | None = None,
) -> None:
    """Forward a capability.denied audit via the dedicated tool."""
    if self._session is None:
        raise RuntimeError(...)
    if caller_trace_id is None:
        caller_trace_id = new_uuid7()
    await self._session.call_tool(
        "forward_capability_denied_audit",
        {
            "payload": payload,
            "caller_trace_id": caller_trace_id,
            "caller_actor_kind": self._caller_actor_kind,  # NEW field, passed at construction
            "caller_actor_id": self._caller_actor_id,
            "parent_event_id": parent_event_id,
        },
    )
```

The `EmitterHolder.emit_event` callable in task-registry / session-registry switches to invoke `forward_capability_denied` when `event_type == "capability.denied"`, otherwise falls back to `emit_event`.

### Lock-wait metric injection

The writer doesn't have direct access to a metrics counter. Two options:
- (a) Inject the metric callable at `EventLogWriter` construction.
- (b) Emit a structured log line `event_log_lock_acquired wait_ms=...` and let metrics-subscriber materialize it from the log. Cleaner architecturally (single emission path) but slower observability.

**Recommended:** (a) — direct counter injection. Simple, fast, follows the Story 10.4 pre-population pattern.

## References

- **Parent story:** `_bmad-output/implementation-artifacts/11-2-2-capability-denied-mcp-emission.md` (status: done; pass-2 carved this story out)
- **PQ9 forgery limitation note:** server.py:362-379 (pass-1 reversal comment)
- **EventLogWriter:** `services/registry-state/src/registry_state/adapters/event_log.py:227-450`
- **Architecture FR26 + MCP topology:** `_bmad-output/planning-artifacts/architecture.md:779,793,938,1089`
- **Story 11.2.1 HTTP-boundary parallel:** `services/registry-api/src/registry_api/adapters/middleware.py:_emit_capability_denied_safe` (uses in-process EventLogWriter — registry-api is the writer process; FR26 compliance trivial for HTTP boundary because registry-api OWNS the writer).

## Tasks / Subtasks

- [ ] Phase 0: Flip sprint-status to `in-progress`.
- [ ] Phase 1 — file-lock in EventLogWriter:
  - [ ] `fcntl.flock(LOCK_EX)` around `_sync_append_impl` write+fdatasync block
  - [ ] `LOCK_UN` in finally so the lock releases even on writer poison
  - [ ] Measure lock-wait time via injected metric callable
  - [ ] macOS + Linux test parity (CI gates Linux; local dev macOS)
  - [ ] Windows: fcntl unavailable — log warning at startup; writer still works (single-process)
- [ ] Phase 2 — metrics-subscriber registration:
  - [ ] `omb_event_log_lock_wait_ms` Histogram pre-registered
  - [ ] Story 10.4 pattern: pre-populated zero samples
- [ ] Phase 3 — `forward_capability_denied_audit` MCP tool:
  - [ ] New tool in clawhip-bridge with caller_actor_kind/id validation
  - [ ] Add to TIER_MAP (Tier.ONE)
  - [ ] Internal `_emit` invocation with actor_override + schema_version
- [ ] Phase 4 — restore PQ9 rejection on public emit_event:
  - [ ] `if type in _emit_overrides: raise PermissionError(...)` at top of public path
  - [ ] Replace `test_emit_event_capability_denied_known_limitation` with `test_emit_event_capability_denied_rejected`
- [ ] Phase 5 — adapter wiring:
  - [ ] task-registry adapter: route `capability.denied` to `forward_capability_denied`
  - [ ] session-registry adapter: same
  - [ ] Both: pass `caller_actor_kind` + `caller_actor_id` from server-construction args
- [ ] Phase 6 — integration test:
  - [ ] Update `test_mcp_capability_denied_emits_envelope_and_increments_counter` to use the dedicated tool path
  - [ ] NEW `test_event_log_multi_writer_safety` spawning concurrent clawhip-bridge subprocesses
- [ ] Phase 7 — flip feature flag default to ON:
  - [ ] Both `__main__.py` files: `enable_audit = OMB == "1"` becomes `enable_audit = OMB != "0"` (default-ON)
  - [ ] Docstrings updated to reflect default-ON
  - [ ] Legacy `*_DISABLE_AUDIT_EMISSION=1` still respected
- [ ] Phase 8 — Story 11.2.2 spec closure note appended.
- [ ] Phase 9 — Validation gates: ruff, mypy, check_imports, check_event_registry, check_single_writer (the new tool is in clawhip-bridge — already exempt; the lock change is in registry-state — should remain compliant), check_registry_isolation, bootstrap-verify, pytest.
- [ ] Phase 10 — Flip sprint-status to `review`; commit + push; run `/bmad-code-review 11-2-3`.

## Dev Agent Record

_To be filled by executor._

**Approach selected:**
**LOC delta:**
**Lock-contention measurements:**
**Files modified:**
**Test count delta:**
**Mypy delta:**
**Deviations from spec:**

## Open questions

- **OQ-1 — `fcntl.flock` on macOS NFS / SMB volumes.** flock on networked filesystems has historical reliability issues. Local dev (macOS HFS+/APFS) + CI (Linux ext4) are the supported targets — assert this and document. Operators running on NFS-backed volumes will see degraded multi-writer safety.
- **OQ-2 — Lock-wait histogram buckets.** `[0.1, 1, 10, 100, 1000]` ms is the default proposal. Refine based on local timing measurements during Phase 1.
- **OQ-3 — How to inject the lock-wait metric counter into EventLogWriter.** Constructor argument vs. module-level singleton. Recommend constructor argument matching existing `clock` + `base_dir` injection pattern.
- **OQ-4 — Feature flag default-ON timing.** Should AC6 (default-ON flip) land in the same PR as AC1-AC5, OR a follow-up PR after operators have a chance to verify in their environments? Recommend SAME PR — the whole point of 11.2.3 is to make it safe to flip.

## Frontmatter

```yaml
---
story_id: 11.2.3
parent_epic: 11
parent_story: 11.2.2
phase: 2
priority: medium-high
estimated_hours: 8-14
blocks: nothing (Story 11.2.2 ships gated; operators can opt in once 11.2.3 lands)
blocked_by: 11.2.2 (done — provides the wiring + tests this story hardens)
status: ready-for-dev
created: 2026-05-24
created_by: bmad/Claude (Story 11.2.2 pass-2 P0 carve-out)
predecessor_commits: ddc8828 (Story 11.2.2 pass-2 review batch), 542868c (closure)
ddo: Epic 10 retro DD5 — architectural closure (multi-writer + forgery vector)
---
```
