# Story 11.2.1 — capability.denied event emission

Status: **done** (pass-1 review CI green @ c9bdd2a (run 26340834289) 2026-05-23 — 11 fixes batch-applied incl. 3 P1-H robustness; HTTP-only scope; MCP-boundary deferred to Story 11.2.2)

## Story

**As** an operator relying on `omb_capability_denied_total{tier, boundary}` to alert on capability-tier rejections and provide a forensic audit trail,
**I want** every `CapabilityDenied` exception raised at the HTTP boundary (`TierEnforcementMiddleware`) or the MCP boundary (capability-handler decorators / `check_tier()` call sites in `mcp-servers/`) to emit a `capability.denied` v1.1.0 event to the JSONL audit log,
**so that** the Story 10.4 preview counter `omb_capability_denied_total` stops sitting at 0 forever and starts incrementing on real denials — closing the half-done DD5 state inherited from Story 11.2 and giving operators an observable signal for capability-tier policy regressions.

## Background

Epic 10 retro **debt item DD5** identified two coupled gaps:

1. **No `capability.denied` event type registered** → Story 10.4's `omb_capability_denied_total{tier, boundary}` counter (pre-populated with 6 zero-value combinations: tier1/tier2/tier3 × mcp/http) had no upstream emission path.
2. **No emission wiring** → even after schema registration, the counter would remain at zero.

**Story 11.2 closed the schema half (done 2026-05-20):**

- Registered `capability.denied` v1.1.0 in `services/registry-state/src/registry_state/domain/event_types.py:275`.
- Defined `CapabilityDeniedPayload` in `packages/events/src/events/payloads.py:1054-1093` with `tier` / `boundary` / `actor_id` / `attempted_action` / `reason` fields.
- Added the contract-fixture forward-compat pair under `tests/contract/fixtures/`.
- **Pass-1 P1-H3 wired the dispatch side**: `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:555-571` now has `_update_capability_denied` + `_DISPATCH["capability.denied"]` entry → when a `capability.denied` envelope arrives at metrics-subscriber, `state.capability_denied_total.labels(tier=, boundary=).inc()` fires.
- **Pass-1 P1-H2 added `"capability"` to `_EVENT_FAMILIES`** so the family counter routes correctly (not bucketed under `"unknown"`).

**Story 11.2 spec D5 + DAR explicitly deferred emission to this story.** The dispatch and counter are wired; the **only thing missing** is the producer side: actually calling `event_log_writer.append(<CapabilityDenied envelope>)` at the two exception sites.

**Current observable state:**

- HTTP path: `services/registry-api/src/registry_api/adapters/middleware.py:481-486` catches `CapabilityDenied`, logs structured warning `tier_enforcement_denied`, builds RFC 7807 403 response via `_build_capability_denied_response`. **No event emitted.**
- MCP path: each `mcp-servers/*/handlers/tools.py` tool calls `check_tier(action, caller, tier)` which raises `CapabilityDenied` on tier mismatch. Exception propagates up the MCP framework, becomes an MCP error reply. **No event emitted.**
- Metric stays at 0 even when middleware returns 403.

## Acceptance criteria

**AC1 — HTTP emission.** [x] `TierEnforcementMiddleware.dispatch()` in `services/registry-api/src/registry_api/adapters/middleware.py:479-486`, on catching `CapabilityDenied`, appends a `capability.denied` v1.1.0 envelope to the `EventLogWriter` **before** returning the 403 response. Emission must not block the 403 path on transient errors (see PD-1 below).

**AC2 — MCP emission.** [DEFERRED to Story 11.2.2 — 2026-05-23 scope amendment] MCP-side `capability.denied` emission requires `task.emit_event` infrastructure (currently a Story 5.12 stub at `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py:219` — logs at INFO, does NOT write to event log). Building the MCP→event-log path is out of scope for 11.2.1. New backlog entry `11-2-2-capability-denied-mcp-emission` filed; depends on Story 5.12 landing first OR an architecture decision to add `POST /v1/internal/events` on registry-api.

**AC3 — Envelope shape.** [x] Emitted `CapabilityDeniedPayload` carries:
- `tier` ∈ {tier1, tier2, tier3} — the **required** tier for the attempted action (matches the `required_tier` arg of `check_tier`, NOT the actor's max tier; this matches Story 10.4 label semantics).
- `boundary` ∈ {http, mcp} — http for `TierEnforcementMiddleware`, mcp for MCP handlers.
- `actor_id` — from `request.state.actor_id` (HTTP) or `CallerContext.actor_id` (MCP).
- `attempted_action` — the route key (e.g., `"POST /v1/decisions"`) for HTTP, OR the MCP tool name (e.g., `"task.add_note"`) for MCP.
- `reason` — `exc.reason` from `CapabilityDenied` (e.g., `"actor_kind_max_tier=1, required_tier=2"`).
- Envelope `actor`: kind matches the boundary (`http` → service actor, `mcp` → MCP client identity).
- Envelope `schema_version: "1.1.0"` (matches `event_types.py:275` registration).
- Envelope `event_id`: UUIDv7 per `EventEnvelope.create()` standard.

**AC4 — Counter increments end-to-end (HTTP).** [x] Existing unit test `services/metrics-subscriber/src/metrics_subscriber/test_metrics_state.py:522` continues to pass. **New** integration test `tests/integration/test_capability_denied_emission.py` emits one HTTP-boundary denial; asserts `omb_capability_denied_total{tier=tier3, boundary=http}` increments by 1 AND `omb_events_appended_total{event_family=capability}` increments by 1 (Story 11.2 P1-H2 family routing). (MCP-boundary part deferred to Story 11.2.2.)

**AC5 — Schema-version compatibility.** [x] No change to `CapabilityDeniedPayload` or registry entry. Story 11.2.1 is pure producer wiring.

**AC6 — 403 contract preserved.** [x] All pre-existing tests under `services/registry-api/src/registry_api/test_middleware.py:390+` (TierEnforcementMiddleware tier gate tests) + `test_errors_envelope.py:858+` (`test_403_capability_denied_problem_json_carries_trace_id` etc.) continue to pass byte-for-byte. Full pytest -m "not slow" green @ 3092 passed.

**AC7 — Telegram-gateway approvals_command.py TODO closed.** [x] `ROUTE_TIER_MAP["POST /v1/approvals/inbox"] = Tier.TWO` already exists at `middleware.py:432` (added by Story 11.3 review P35). Updated `approvals_command.py:33-39` docstring to explicitly state: Telegram-side gate is allowlist-only by design; tier enforcement is server-side via registry-api; tier-mismatch denials emit `capability.denied` (Story 11.2.1).

**AC8 — Tests added.**
- [x] Unit: HTTP middleware emits envelope on `CapabilityDenied` (`test_capability_denied_emits_v1_1_0_envelope_to_event_log`).
- [x] Unit: HTTP emission failure (PD-1 fail-soft) does NOT block the 403 response (`test_capability_denied_emission_does_not_block_403_on_writer_failure`).
- [x] Unit: schema_version pinned at 1.1.0 + envelope shape AC3 (covered inside the v1.1.0 unit test above).
- [x] Unit: no-writer (test-fixture path) returns silently + logs INFO (`test_capability_denied_emission_skipped_when_no_writer`).
- [x] Integration: end-to-end counter increment for HTTP boundary (`tests/integration/test_capability_denied_emission.py::test_http_capability_denied_emits_envelope_and_increments_counter`).
- [DEFERRED to Story 11.2.2] Unit: MCP handler emits envelope on `CapabilityDenied` from `check_tier`.
- [DEFERRED to Story 11.2.2] Integration: end-to-end counter increment for MCP boundary.

**AC9 — All gates green.** [x] `ruff check` ✓; `ruff format --check` ✓ (376 files); `mypy --strict packages/ services/registry-api services/registry-state` ✓ (119 files); `check_imports.py` ✓; `check_event_registry.py` ✓; `check_single_writer.py` ✓; `check_registry_isolation.py` ✓; `just bootstrap-verify` ✓; `uv run pytest -m "not slow"` ✓ (3092 passed / 3 skipped / 35 deselected — zero failures).

## Approach options

### Option A — Emit at the exception-catch site (selected starting point)

**HTTP:** Add a 2-line emit call inside `TierEnforcementMiddleware.dispatch()`'s `except CapabilityDenied` block, **before** returning the 403 response:

```python
except CapabilityDenied as exc:
    _log.warning(
        "tier_enforcement_denied",
        extra={"route": route_key, "actor_id": actor_id, "reason": exc.reason},
    )
    await self._emit_capability_denied(  # NEW (PD-1 below: best-effort, never raises)
        actor_id=actor_id,
        boundary="http",
        tier=required_tier,
        attempted_action=route_key,
        reason=exc.reason,
    )
    return _build_capability_denied_response(request, exc)
```

`_emit_capability_denied` is a thin helper that:
1. Builds the `CapabilityDeniedPayload` + `EventEnvelope.create()`.
2. Calls `event_log_writer.append(envelope)`.
3. **PD-1 fail-soft**: wraps the append in `try/except` + structured `_log.error("capability_denied_emission_failed", ...)` so an event-log write failure CANNOT mask the 403 (operator priority: deny first, observability is best-effort).

**MCP:** Each tool calls `check_tier()` directly. Two sub-options:

- **A-MCP-1 (per-call-site):** Wrap each `check_tier()` call in `try/except CapabilityDenied`, emit, re-raise. ~12 call sites across 3 MCP servers; mechanical but voluminous.
- **A-MCP-2 (handler decorator):** Add a `@_emit_on_tier_denial(boundary="mcp")` decorator that wraps any handler function and catches `CapabilityDenied`, emits, re-raises. Single new helper in `packages/capabilities/` or each MCP server's `handlers/__init__.py`. Cleaner.

**Recommended:** A + A-MCP-2.

### Option B — Emit inside `check_tier()` itself

Modify `packages/capabilities/src/capabilities/tiers.py` `check_tier()` to take an optional `event_emitter` dependency and emit on the deny path.

Trade-off: pushes producer concern into the pure capability-checking library (architecture coupling). Also requires the `boundary` arg to be threaded everywhere `check_tier` is called → API change. **Not recommended** — `packages/capabilities/` is currently I/O-free.

### Option C — Emit from the exception handler (HTTP only) + Option A for MCP

`services/registry-api/src/registry_api/adapters/errors.py:313` (`handle_capability_denied`) is FastAPI's registered exception handler. Could emit from there.

Trade-off: in the actual request path, `TierEnforcementMiddleware` catches the exception **inline** (line 481) and short-circuits — `handle_capability_denied` only fires for exceptions raised from inside the route handler (not from middleware). Splitting emission across two sites is error-prone. **Not recommended.**

### Decision

**Selected:** Option A + A-MCP-2. Spec defaults to this; dev agent may justify a switch in DAR.

| Criterion | Option A (selected) | Option B | Option C |
|---|---|---|---|
| LOC delta | ~80 | ~30 + caller updates | ~40 |
| Architecture purity | Localized | Couples `capabilities/` to I/O | Split (fragile) |
| Test surface | Middleware + decorator | check_tier + all callers | Two emission sites |
| Reusable for Epic 12 | Yes (decorator) | Partial | No |

## Non-goals

- **NOT** adding new capability tiers or changing tier policy. (Story 6.x scope.)
- **NOT** adding new Prometheus metric series. (`omb_capability_denied_total` already exists from Story 10.4.)
- **NOT** changing `CapabilityDeniedPayload` schema or registry entry. (Story 11.2 sealed at v1.1.0.)
- **NOT** modifying the RFC 7807 403 response shape. (AC6 — pre-existing tests pin this.)
- **NOT** touching Story 11.5.1 (`/key-status` Telegram command) — separate FR65a follow-up.
- **NOT** wiring `approval.inbox_opened` emission (Story 11.3 territory).
- **NOT** adding emission for MCP read-only tools — only tier-gated tools that call `check_tier`.

## Dev notes

### Files to touch (expected)

1. **`packages/events/src/events/__init__.py`** — re-export `CapabilityDeniedPayload` if not already exposed (check imports).
2. **`services/registry-api/src/registry_api/adapters/middleware.py`** — `TierEnforcementMiddleware`: accept optional `event_log_writer` + `clock` constructor args; emit on deny.
3. **`services/registry-api/src/registry_api/app.py`** — wire `event_log_writer` + `clock` into `TierEnforcementMiddleware` construction in `build_app` (or wherever the middleware is added).
4. **`mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py`** — apply `@emit_on_tier_denial` decorator (or per-call-site wrap) for each tier-gated tool.
5. **`mcp-servers/session-registry/src/session_registry_mcp/handlers/`** — same (audit which handlers call `check_tier`).
6. **`mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/handlers/`** — same.
7. **NEW: `packages/capabilities/src/capabilities/emit.py`** OR **`mcp-servers/_shared/emit.py`** — `emit_on_tier_denial(boundary, event_log_writer, clock)` decorator helper. Decision: put in `packages/capabilities/` (already has `check_tier` — natural sibling) but keep emission STRICTLY optional via dependency injection so `capabilities/` itself remains I/O-free.
8. **`services/telegram-gateway/src/telegram_gateway/handlers/approvals_command.py`** — close AC7 docstring TODO.
9. **Tests:**
   - `services/registry-api/src/registry_api/test_middleware.py` — new test class `TestCapabilityDeniedEmission`.
   - `mcp-servers/task-registry/src/task_registry_mcp/test_server.py` — new test for tier-denial emission.
   - `tests/integration/test_capability_denied_emission.py` (NEW) — end-to-end HTTP + MCP → metrics-subscriber → counter increment.
10. **`_bmad-output/implementation-artifacts/sprint-status.yaml`** — status flips (backlog → in-progress → review → done).

### Canonical emission pattern (cite Story 11.5)

The canonical pattern lives at `services/registry-api/src/registry_api/adapters/key_rotation.py:160-300` (`detect_and_emit_key_rotation`). Follow that shape:

```python
envelope = EventEnvelope.create(
    event_type="capability.denied",
    schema_version="1.1.0",
    payload=CapabilityDeniedPayload(
        tier=required_tier,
        boundary=boundary,  # "http" or "mcp"
        actor_id=actor_id,
        attempted_action=attempted_action,
        reason=reason,
    ),
    actor=Actor(kind=..., id=actor_id),  # see Story 11.5 for actor-kind selection
    clock=clock,
)
await event_log_writer.append(envelope)
```

### EventLogWriter discipline (FR26)

**Single-writer invariant:** registry-api owns the one process-wide `EventLogWriter` instance (Story 11.5 lifespan). MCP servers DO NOT own their own writer. **MCP emission MUST go through a shared writer instance** (likely accessed via dependency injection or a thin HTTP "emit-event" endpoint on registry-api).

**Open question OQ-1:** How does an MCP server emit a `capability.denied` event when the single-writer is in registry-api?

Two candidate answers (dev should pick one in DAR + document rationale):

- **OQ-1-A**: MCP servers POST to a registry-api endpoint (e.g., `POST /v1/internal/events` with the envelope body). Adds latency + auth surface; preserves single-writer cleanly.
- **OQ-1-B**: MCP servers run in the same process as registry-api and share the writer via in-process DI. Check architecture — are MCP servers separate processes or co-hosted with registry-api?

**Architecture cross-reference needed:** `_bmad-output/planning-artifacts/architecture.md` "MCP topology" section. If MCP servers are separate processes per architecture, OQ-1-A is forced; FR26 cannot tolerate two writers.

### Schema-version emission discipline

`event_types.py:275` registers `capability.denied` at **v1.1.0 ONLY** (no v1.0.0 predecessor). Emission code MUST use `"1.1.0"` literally. If a future emission uses `"1.0.0"` the schema registry will raise. Pin this with a unit test (`test_emit_uses_schema_version_1_1_0`).

### `_EVENT_FAMILIES` family routing

Story 11.2 pass-1 P1-H2 added `"capability"` to `_EVENT_FAMILIES` in `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:204`. **No change needed here**, but the integration test in AC8 should also assert `omb_events_appended_total{event_family="capability"}` increments (NOT `unknown`).

### Tier semantics — actor's tier vs required tier

`CapabilityDeniedPayload.tier` is **the required tier** (the policy threshold the actor failed to meet), not the actor's current max tier. This matches `omb_capability_denied_total{tier=}` semantics where `tier` answers "what tier was needed?" — operators ask "how many tier-2 attempts is this actor making" not "what tier is the actor". Confirm in DAR by referencing Story 10.4's counter docstring.

### Backwards-compat for `TierEnforcementMiddleware` construction

`TierEnforcementMiddleware.__init__` currently takes only `app, actor_kind`. New optional kwargs `event_log_writer: EventLogWriter | None = None`, `clock: Clock | None = None`. When `None`, emission is skipped (logged as `_log.warning("capability_denied_emission_skipped_no_writer")`). This preserves existing test fixtures that construct the middleware without a writer.

### Pre-existing pattern reference — `_log.warning` is already there

The middleware already calls `_log.warning("tier_enforcement_denied", ...)` (line 482-485). The new emission is **in addition to** the structured log, not a replacement.

## References

- **Parent story:** `_bmad-output/implementation-artifacts/11-2-register-approval-signed-key-rotated-events.md`
- **DD5 origin:** Epic 10 retrospective (see retro file `_bmad-output/implementation-artifacts/epic-10-retro-*.md` — debt item DD5).
- **Schema registration:** `services/registry-state/src/registry_state/domain/event_types.py:275`
- **Payload model:** `packages/events/src/events/payloads.py:1054-1093`
- **Pre-populated counter:** `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:808-823`
- **Dispatch wiring (already done):** `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:555-571, 613`
- **HTTP exception site:** `services/registry-api/src/registry_api/adapters/middleware.py:479-486`
- **HTTP 403 response builder:** `services/registry-api/src/registry_api/adapters/errors.py:290`
- **MCP `check_tier` call sites:** `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` (and session-registry / clawhip-bridge equivalents)
- **Canonical emission pattern:** `services/registry-api/src/registry_api/adapters/key_rotation.py:160-300`
- **EventLogWriter contract:** `services/registry-state/src/registry_state/adapters/event_log.py:227-296`
- **Telegram-gateway TODO:** `services/telegram-gateway/src/telegram_gateway/handlers/approvals_command.py:33`
- **FR/NFR refs:** FR — capability-tier policy (Epic 6); NFR-O8 (`omb_*` counter discipline); Epic 10 retro DD5 (`omb_capability_denied_total` wired-but-zero).

## Tasks / Subtasks

- [x] Phase 0: Flip sprint-status.yaml `11-2-1-capability-denied-emission` → `in-progress`; add new backlog entry `11-2-2-capability-denied-mcp-emission`; commit `chore(sprint-status)`.
- [x] Phase 1 — OQ-1 RESOLVED (HTTP-only scope; MCP deferred to 11.2.2 — see Open questions).
- [x] Phase 2 — HTTP emission:
  - [x] Add `_emit_capability_denied_safe` helper in `middleware.py`.
  - [x] Read `event_log_writer` + `clock` from `request.app.state` (cleaner than constructor plumbing — lifespan already exposes them).
  - [x] PD-1 fail-soft: emission errors logged + swallowed; 403 path never blocked.
- [x] Phase 3 — AC7 close: `approvals_command.py:33-39` docstring updated; ROUTE_TIER_MAP entry already in place (`middleware.py:432`, Story 11.3 review P35).
- [x] Phase 4 — Tests:
  - [x] Unit: HTTP middleware emit (`test_middleware.py::TestCapabilityDeniedEmission` — 3 tests).
  - [x] Unit: schema_version pinned at 1.1.0; envelope shape (covered).
  - [x] Unit: emission failure does NOT block 403 (PD-1).
  - [x] Integration: `tests/integration/test_capability_denied_emission.py` — HTTP → metrics → counter (1 test).
- [x] Phase 5 — Validation gates: ruff, mypy, check_imports, check_event_registry, check_single_writer, check_registry_isolation, bootstrap-verify, pytest — all green.
- [x] Phase 6 — Flip sprint-status to `review`; commit `feat(epic-11.2.1)` + push (commit 393f69e); CI green run 26340206058. Ready for `/bmad-code-review 11-2-1`.

**Deferred to Story 11.2.2:**
- MCP-side emission (originally AC2 + Phase 3 MCP plumbing)
- MCP unit + integration tests
- Requires Story 5.12 (`task.emit_event` actual emission path) OR a new `POST /v1/internal/events` API surface.

## Dev Agent Record

**Approach selected:** Option A (HTTP-only scope after scope amendment) — emit at the existing `except CapabilityDenied` catch site in `TierEnforcementMiddleware.dispatch()`. **Deviation from spec Dev notes:** the spec proposed plumbing `event_log_writer` + `clock` through `TierEnforcementMiddleware.__init__`. Implementation reads them from `request.app.state.writer` / `request.app.state.clock` instead — the lifespan in `build_app` already exposes them there (used by `key_rotation.py` too). This is cleaner: no constructor change → no test-fixture migration → reads via `getattr(...)` allow graceful no-emission in fixtures that skip the lifespan.

**Rationale (scope amendment):** OQ-1 investigation found `mcp-servers/task-registry/.../handlers/tools.py:219` (`task.emit_event`) is a Story 5.12 stub — logs at INFO, does NOT write to the event log. Closing AC2 in this story would require landing Story 5.12 infrastructure (`task.emit_event` real plumbing, or a new `POST /v1/internal/events` API). User selected **Path A** (HTTP-only now, MCP later) — AC2 carved out to new Story 11.2.2.

**Files modified (7 total):**
1. `services/registry-api/src/registry_api/adapters/middleware.py` — added `_emit_capability_denied_safe` helper (~90 LOC) + 1-line call inside `TierEnforcementMiddleware.dispatch()`'s `except CapabilityDenied` block. Imports added: `Actor`, `EventEnvelope` from `events.envelope`; `new_event_id` from `events.ids`; `CapabilityDeniedPayload` from `events.payloads`.
2. `services/telegram-gateway/src/telegram_gateway/handlers/approvals_command.py` — closed AC7 docstring TODO (lines 33-39).
3. `services/registry-api/src/registry_api/test_middleware.py` — added `TestCapabilityDeniedEmission` class with 3 unit tests.
4. `tests/integration/test_capability_denied_emission.py` — NEW; 1 integration test (producer → consumer → counter).
5. `_bmad-output/implementation-artifacts/11-2-1-capability-denied-emission.md` — spec scope amendment + tick boxes + this DAR.
6. `_bmad-output/implementation-artifacts/sprint-status.yaml` — status flips + 11.2.2 backlog entry.

**Test count delta:** +4 (3 unit + 1 integration). Full suite: 3088 → 3092 passed, zero failures.

**Mypy delta:** 0 errors → 0 errors (119 files under `mypy --strict`).

**OQ-1 resolution:** documented in spec — MCP-boundary scope split out to Story 11.2.2.

**OQ-2 (Actor.kind for envelope) — RESOLVED:** envelope `actor.kind="system"` + `actor.id="registry-api"` (matches Story 11.5 `key.rotated` pattern at `key_rotation.py:301`). The PAYLOAD `actor_id` field carries the denied caller's identity (from `request.state.actor_id`), keeping envelope-actor (who emitted) separate from payload-actor (who was denied).

**OQ-3:** deferred to ops backlog (out of 11.2.1 scope).

**Deviations from spec:**
- **Constructor plumbing → app.state read** (see Approach above). Cleaner; documented in middleware docstring.
- **Test split: unit + integration**, not a single end-to-end test. Cross-service import (`registry-api` test importing `metrics_subscriber`) is blocked by `check_imports.py` — integration test moved to `tests/integration/` (outside per-service import graph).
- **Tier semantics confirmed:** `payload.tier` = required tier (denied threshold), NOT actor's max tier. Matches Story 10.4 counter docstring.

## Pass-1 Review Findings (3-lane review of `393f69e..6e575c1` — 2026-05-23)

**Reviewer dedup:** 27 raw findings (Blind 16 + Edge 9 + Acceptance 2) → **11 unique** real findings (3 P0 claims from Blind Hunter were false positives — verified against actual code: tests use isolated `app` per-test; `EventEnvelope.create` uses `type=` not `event_type=`; tests pass on Ubuntu CI). Acceptance Auditor APPROVED with 2 L observations both addressed.

**P1-H (3):**

- [x] [Review][Patch] PP1 — **PD-1 swallow includes `asyncio.CancelledError`** (Blind P1-H + Edge P1-H-A 2-lane) — bare `except Exception` would swallow `BaseException`-derived cancel signals via third-party libraries' `Exception`-derived cancel subclasses. Compare key_rotation.py's fail-LOUD discipline (D3). Fix: explicit `except (asyncio.CancelledError, KeyboardInterrupt): raise` before the broad except [middleware.py:_emit_capability_denied_safe, P1-H]
- [x] [Review][Patch] PP2 — **Silent trace_id/request_id fallback masks middleware-order regression** (Blind P1-M + Edge P1-H-B 2-lane) — dead defensive code mints fresh UUIDs if upstream middleware skipped; correlation is load-bearing for audit, so silent breakage is operationally invisible. Fix: log WARNING before minting [middleware.py:_emit_capability_denied_safe, P1-H]
- [x] [Review][Patch] PP3 — **`# type: ignore[arg-type]` on tier literal masks enum-drift risk** (Blind P1-H + Edge P1-M-E + Acceptance L1 3-lane) — silent KeyError if Tier enum gains a new denyable member. Fix: type `_TIER_INT_TO_LITERAL` as `dict[int, _TierLiteral]` so mypy narrows the lookup (cast unnecessary), add `test_tier_int_to_literal_covers_every_denyable_tier_member` contract test asserting `set(_TIER_INT_TO_LITERAL) == {t.value for t in Tier if t != Tier.ZERO}` [middleware.py + test_middleware.py, P1-H]

**P1-M (5):**

- [x] [Review][Patch] PP4 — **`actor_id` None vs "unknown" fallback bug** (Blind P1-M + Edge P1-M-C 2-lane) — `getattr(request.state, "actor_id", "unknown")` only handles ABSENT attribute; if state explicitly sets `actor_id=None`, Pydantic `min_length=1` rejects it → ValidationError caught by PD-1 swallow → audit dropped. Fix: `getattr(...) or "unknown"` [middleware.py:_emit_capability_denied_safe, P1-M]
- [x] [Review][Patch] PP5 — **`int(required_tier)` couples to IntEnum** (Blind P1-H) — silently breaks if Tier refactors to StrEnum/Enum. Fix: `required_tier.value` [middleware.py:_emit_capability_denied_safe, P1-M]
- [x] [Review][Patch] PP6 — **Tests assert payload via raw dict-indexing** (Blind P1-M) — `env.payload["tier"]` style; field renames silently slip through. Fix: round-trip via `CapabilityDeniedPayload.model_validate(env.payload)` in both unit + integration tests [test_middleware.py + test_capability_denied_emission.py, P1-M]
- [x] [Review][Patch] PP7 — **Unit test uses `json.loads`; integration uses `from_canonical_json` — asymmetric** (Edge P1-M-F) — canonical-JSON discipline regression hidden. Fix: use `from_canonical_json` in both [test_middleware.py, P1-M]
- [ops-backlog] PP11 — **No metric for emission failures** (Edge P1-L) — silent observability paths should themselves be observable. Out of 11.2.1 scope; flagged for ops backlog as new counter `omb_capability_denied_emission_failed_total{boundary}`. Logged in spec OQ-3.

**P1-L (3):**

- [x] [Review][Patch] PP8 — **DRY: `_db_url` / `_seed_tables` duplicated** (Edge P1-L-G) — extracted to `tests/integration/_db_helpers.py` (mirrors existing `_compose_helpers.py` sibling-module pattern; conftest not used because pytest treats it as fixture-special) [_db_helpers.py NEW + test_capability_denied_emission.py, P1-L]
- [x] [Review][Patch] PP9 — **3 unit tests duplicate 30+ lines of LifespanManager setup** (Blind P1-L) — extracted to `_denied_app_ctx` module-level `pytest_asyncio.fixture` consumed by all 3 tests. ~80 LOC saved [test_middleware.py, P1-L]
- [x] [Review][Patch] PP10 — **Redundant `REGISTRY_API_TEST_PROBES=1` in integration test** (Blind P1-M) — copy-paste from app_client; this fixture doesn't register `/debug/state`, env var was dead. Fix: removed [test_capability_denied_emission.py, P1-L]

**False positives — NOT applied:**

- Blind P0 "cross-test fixture pollution corrupts `app.state.writer`" — each test owns its own `app` via `LifespanManager` scope; no shared state across tests. Verified: 3 tests run independently in sequence + parallel via pytest-xdist with no leakage.
- Blind P0 "`EventEnvelope.create` `type=` kwarg vs spec `event_type=`" — verified `envelope.py:401`: `type: str` is the canonical kwarg; spec's narrative example block was illustrative, not literal API.
- Blind P1-H "Unicode/log injection via unsanitized `actor_id` + `reason`" — `ActorIdMiddleware` already validates `X-Actor-Id` against `_ACTOR_ID_HEADER_RE` before populating `request.state.actor_id` (middleware.py:_ACTOR_ID_HEADER_RE). `reason` comes from `CapabilityDenied.reason` constructed in `capabilities/tiers.py:check_tier` from `f"actor_kind {caller.actor_kind!r} allows Tier.X at most"` — not user-controlled. Pydantic Field max_length=4096 caps any pathological value.

## Open questions

- **OQ-1 — RESOLVED 2026-05-23.** Investigation found `mcp-servers/task-registry/.../handlers/tools.py:219` (`task.emit_event`) is a Story 5.12 stub that logs INFO but does NOT write to the event log. Architecture line 779 routes MCP→event-log through clawhip-bridge, which also has no emission wiring yet. Therefore Story 11.2.1's MCP-side scope (AC2) cannot be satisfied without first landing Story 5.12 infrastructure. **Decision:** scope 11.2.1 to HTTP-only; new Story 11.2.2 will close the MCP boundary once Story 5.12 (or an alternative POST /v1/internal/events surface) lands.
- **OQ-2 — Actor.kind for MCP-boundary envelopes.** HTTP path uses `ActorKind.service` (the registry-api process). For MCP boundary, is the actor `MCPClient`? `Operator`? Check `events.envelope.Actor` enum values + look at how `task_registry_mcp` or `session_registry_mcp` emit other events today.
- **OQ-3 — Should `omb_capability_denied_total` go from preview → operationally-watched after this story?** Story 10.4 documented it as "preview-only" counter pending real emission. Once emission lands, operators may want to add Prometheus alerting. Out-of-scope for 11.2.1 but worth flagging for ops backlog.

## Frontmatter

```yaml
---
story_id: 11.2.1
parent_epic: 11
parent_story: 11.2
phase: 2
priority: medium
estimated_hours: 4-8 (depends on OQ-1 resolution — A-route adds ~2h)
blocks: nothing (Story 10.4 counter sits at 0 until this closes)
blocked_by: 11.2 (done — schema + dispatch wiring)
status: ready-for-dev
created: 2026-05-23
created_by: bmad/Claude (Story 11.2 P1-M2 follow-up — emission deferred from parent)
predecessor_commits: 4382af7 (Story 11.2 done), c9adf7e (Story 11.2 pass-1 review batch)
ddo: Epic 10 retro DD5 (`omb_capability_denied_total` deferred-preview counter)
---
```
