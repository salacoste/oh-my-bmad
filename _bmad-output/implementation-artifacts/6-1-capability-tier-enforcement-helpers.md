# Story 6.1: Capability-tier enforcement helpers

Status: ready-for-dev

## Story

As the platform,
I want `packages/capabilities/` to provide tier-classification and tier-check helpers,
So that every MCP handler and HTTP endpoint can enforce Tier 0–3 access uniformly.

## Acceptance Criteria

1. **AC-1: `Tier` enum** — `packages/capabilities/` exports a `Tier` enum with values `ZERO` (read-only), `ONE` (bounded write), `TWO` (repo mutation), `THREE` (high-risk). Each tier level has an `int` value (0, 1, 2, 3) enabling comparison (`Tier.THREE > Tier.ONE`).

2. **AC-2: `CallerContext` frozen model** — A Pydantic frozen model with fields `actor_kind: ActorKind`, `actor_id: str`, `task_id: str | None = None`. Importable from `packages/capabilities/`.

3. **AC-3: `check_tier` helper** — `check_tier(action: str, caller: CallerContext, required_tier: Tier) -> CapabilityOk` returns `CapabilityOk` when the caller's actor kind is authorized for the requested tier; raises `CapabilityDenied` otherwise. `CapabilityOk` is a frozen dataclass with `action`, `caller`, `tier` fields. `CapabilityDenied` is a typed exception in `packages/events/errors.py`.

4. **AC-4: Tier-by-actor mapping** — The helper uses a deterministic mapping:
   - `operator`: Tier 0–3 (all actions, no approval check at this layer — approval enforcement is Story 6.2/6.4)
   - `system`: Tier 0–3 (platform infrastructure)
   - `clawhip`: Tier 0–2 (event bridge cannot push directly)
   - `orchestrator`: Tier 0–2 (orchestrates but doesn't push)
   - `worker`: Tier 0–2 (Tier 3 requires approval flow — enforced by caller, not this helper)

   When a caller's tier is insufficient, `CapabilityDenied` is raised with `action`, `caller.actor_kind`, `required_tier`, and `reason` (e.g., "actor_kind 'worker' not authorized for Tier.THREE action 'git_push'").

5. **AC-5: `CapabilityDenied` exception** — Added to `packages/events/src/events/errors.py` as a subclass of `EventsError`. Fields: `action: str`, `actor_kind: str`, `required_tier: int` (int, not Tier, to avoid circular dep — capabilities depends on events, not vice versa), `reason: str`. Exported via `__all__`.

6. **AC-6: Package scaffold** — `packages/capabilities/` follows the established pattern:
   - `pyproject.toml` with `name = "capabilities"`, `version = "0.1.0"`, `dependencies = ["events"]`, `requires-python = ">=3.12"`, build-backend `uv_build`
   - `src/capabilities/__init__.py` with `__version__`, `__all__`, re-exports
   - `src/capabilities/tiers.py` — `Tier` enum, `CallerContext`, `CapabilityOk`, `check_tier`
   - `src/capabilities/test_tiers.py` — comprehensive tests

7. **AC-7: Replace `_check_tier` stubs** — All three MCP servers' `_check_tier` functions updated to import and delegate to `capabilities.check_tier`:
   - `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` (3 call sites)
   - `mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py` (3 call sites)
   - `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` (5 call sites)
   
   The replacement signature at each call site becomes `check_tier(action, CallerContext(actor_kind=actor_kind, actor_id=actor_id, task_id=task_id), TIER_MAP[action])` where `TIER_MAP` maps each tool name to its required tier.

8. **AC-8: Tool-to-tier classification** — A `TIER_MAP: dict[str, Tier]` constant in each MCP server's tools.py maps tool names to required tiers:
   - `task.add_note`, `task.attach_artifact`, `task.emit_event` → `Tier.ONE`
   - `session.register`, `session.heartbeat`, `session.close` → `Tier.ONE`
   - `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` → `Tier.ONE`
   - (Tier.THREE tools like `git_push` will be added in Story 6.2/6.7 — not this story)

9. **AC-9: Existing tests pass** — All MCP server tests updated to work with new `check_tier` import. `test_check_tier_returns_true` updated to verify the real helper allows valid callers. `test_tool_raises_permission_error_when_tier_denies` updated to verify `CapabilityDenied` (not generic `PermissionError`).

10. **AC-10: `scripts/check_imports.py` exits 0.** New `packages/capabilities/` imports only from `packages/events/` — never from `services/` or `mcp-servers/`.

11. **AC-11: `just lint` green, `just test` no regressions.**

12. **AC-12: `uv sync --all-packages` resolves** — New package added to workspace. `from capabilities import __version__` works.

13. **AC-13: Atomic commit** — title: `feat(capabilities): add Tier enum + check_tier helper + replace MCP stubs · E6`

## Tasks / Subtasks

- [ ] **Task 1: Create `packages/capabilities/` scaffold** (AC: #6, #12)
  - [ ] Create `packages/capabilities/pyproject.toml` following `packages/idempotency/pyproject.toml` pattern
  - [ ] Create `packages/capabilities/src/capabilities/__init__.py` with `__version__ = "0.1.0"`, `__all__`, re-exports
  - [ ] Verify `uv sync --all-packages` resolves and `from capabilities import __version__` works

- [ ] **Task 2: Add `CapabilityDenied` to events errors** (AC: #5)
  - [ ] Add `CapabilityDenied(EventsError)` to `packages/events/src/events/errors.py`
  - [ ] Add `action`, `actor_kind`, `required_tier`, `reason` fields with `__init__` and `_format()`
  - [ ] Update `__all__` in errors.py
  - [ ] Update `packages/events/src/events/__init__.py` to re-export

- [ ] **Task 3: Implement `Tier` enum + `CallerContext` + `CapabilityOk` + `check_tier`** (AC: #1, #2, #3, #4)
  - [ ] Create `packages/capabilities/src/capabilities/tiers.py`
  - [ ] `Tier(IntEnum)` with `ZERO=0`, `ONE=1`, `TWO=2`, `THREE=3`
  - [ ] `CallerContext(BaseModel)` frozen with `actor_kind: ActorKind`, `actor_id: str`, `task_id: str | None = None`
  - [ ] `CapabilityOk` frozen dataclass with `action`, `caller`, `tier`
  - [ ] `_MAX_TIER_BY_ACTOR: dict[ActorKind, Tier]` constant
  - [ ] `check_tier(action, caller, required_tier) -> CapabilityOk` with tier comparison logic
  - [ ] Update `__init__.py` to re-export all public symbols

- [ ] **Task 4: Write comprehensive tests** (AC: #9)
  - [ ] Create `packages/capabilities/src/capabilities/test_tiers.py`
  - [ ] Test `Tier` enum ordering and comparison
  - [ ] Test `CallerContext` validation (valid actor kinds, task_id optional)
  - [ ] Test `check_tier` returns `CapabilityOk` for authorized callers (all actor kinds × their allowed tiers)
  - [ ] Test `check_tier` raises `CapabilityDenied` for unauthorized callers with correct reason message
  - [ ] Test edge cases: empty action string, boundary tier levels
  - [ ] Update MCP server tests (task-registry, session-registry, clawhip-bridge) to use new imports

- [ ] **Task 5: Replace `_check_tier` stubs in MCP servers** (AC: #7, #8)
  - [ ] Update `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py`: replace local `_check_tier` with import from `capabilities`, add `TIER_MAP`
  - [ ] Update `mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py`: same
  - [ ] Update `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py`: same
  - [ ] Update all 11 call sites to use `CallerContext` and `TIER_MAP`
  - [ ] Remove old `_check_tier` function definitions

- [ ] **Task 6: Verification + commit** (AC: #10, #11, #13)
  - [ ] `scripts/check_imports.py` exits 0
  - [ ] `ruff check` and `ruff format` clean
  - [ ] `just test` no regressions
  - [ ] `uv sync --all-packages` resolves
  - [ ] Atomic commit

## Dev Notes

### What already exists

**`packages/events/src/events/envelope.py:133`** — `ActorKind = Literal["operator", "orchestrator", "worker", "system", "clawhip"]`. The new `CallerContext` uses this type.

**`packages/events/src/events/errors.py`** — `EventsError` base class with `EventSchemaUnknown`, `WorktreeLockHeld`, `BudgetExceeded` as examples. `CapabilityDenied` must follow the same pattern (subclass `EventsError`, store fields, format message in `_format()`).

**Three `_check_tier` stubs** — All return `True` unconditionally with docstrings saying "Story 6.1-6.3 replaces this." Each has existing tests:
- `test_check_tier_returns_true` — must be updated for real enforcement
- `test_tool_raises_permission_error_when_tier_denies` — must be updated from `PermissionError` to `CapabilityDenied`

**11 total call sites** across 3 servers:
- task-registry: `task.add_note`, `task.attach_artifact`, `task.emit_event` (3)
- session-registry: `session.register`, `session.heartbeat`, `session.close` (3)
- clawhip-bridge: `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` (5)

### Key design decisions

1. **New `packages/capabilities/` rather than extending `packages/secret_hygiene/`.** The AC mentions "or a new `packages/capabilities/`". A new package is cleaner — tier enforcement is conceptually distinct from secret hygiene. Both are in the FR37-45 range but serve different purposes.

2. **Tier-by-actor mapping is a fixed constant, not configurable.** Phase 1 uses a hardcoded `_MAX_TIER_BY_ACTOR` dict. The mapping is deterministic from the PRD (lines 361-369): operator=3, system=3, clawhip=2, orchestrator=2, worker=2.

3. **`check_tier` does NOT check approval events.** Story 6.1 only enforces actor-kind → tier mapping. Approval event lookup for Tier 3 actions is Story 6.2 (MCP handler enforcement). This story creates the primitive; 6.2 adds the approval gate on top.

4. **All current tools are Tier.ONE.** None of the 11 existing tools require Tier 2 or 3. Tier 3 tools (like `git_push`) will be added in Stories 6.2 and 6.7. The `TIER_MAP` in this story maps everything to `Tier.ONE` — this is correct because all existing MCP tools are bounded writes.

5. **`CallerContext` carries `task_id` but check_tier doesn't use it yet.** The `task_id` field is for Story 6.2's approval event lookup (which scopes approvals to specific tasks). Including it now avoids a signature change later.

6. **`CapabilityDenied` in events/errors.py, not in capabilities/.** The exception must be importable by all MCP servers and the HTTP middleware. Since MCP servers can only import from `packages/`, putting it in `events/errors.py` (alongside `WorktreeLockHeld`, `BudgetExceeded`) keeps the import graph clean.

### Package structure

Follow the established `packages/<name>/src/<name>/` pattern:

```
packages/capabilities/
  pyproject.toml              # name="capabilities", deps=["events"], build-backend="uv_build"
  src/capabilities/
    __init__.py               # __version__, __all__, re-exports from tiers
    tiers.py                  # Tier, CallerContext, CapabilityOk, check_tier, _MAX_TIER_BY_ACTOR
    test_tiers.py             # comprehensive tests
```

### Import graph constraints

- `packages/capabilities/` may import from `packages/events/` only (ActorKind, EventsError)
- `mcp-servers/*` may import from `packages/capabilities/` (check_tier, Tier, CallerContext)
- `packages/capabilities/` must NOT import from `services/` or `mcp-servers/`
- Enforced by `scripts/check_imports.py`

### structlog gotcha (from 5.17b/5.18)

Never use `event=` as a keyword argument with structlog loggers — it clashes with structlog's positional `event` parameter. Use `cap_event=` or `tier_event=` if needed.

### Scope boundary — what NOT to do

- Do NOT check for approval events — that's Story 6.2
- Do NOT add Tier 3 tool handlers — that's Story 6.7 (worker approval-wait state)
- Do NOT add HTTP middleware — that's Story 6.3
- Do NOT add `POST /v1/tasks/{id}/decisions` — that's Story 6.4
- Do NOT add new event types (approval.granted, tier3.*) — that's Stories 6.5/6.6
- Do NOT modify `services/` (only `packages/` and `mcp-servers/`)

### Downstream consumers

- **Story 6.2** — Wraps `check_tier` with approval-event lookup for Tier 3 actions
- **Story 6.3** — HTTP middleware imports `Tier`, `CallerContext`, `check_tier`
- **Story 6.4** — Decisions handler emits approval events that 6.2 checks
- **Story 6.7** — Worker approval-wait state uses Tier.THREE for git push gating
- **Story 6.14** — Negative test imports `CapabilityDenied`, `Tier`

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1778-1790 — Story 6.1 definition]
- [Source: `_bmad-output/planning-artifacts/prd.md` lines 361-369 — Tier 0-3 classification]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 867 — FR37 capability tiers]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 868 — FR38 tier enforcement]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 214 — LOCKED authorization model]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 220 — Audit event types]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 823 — Tier enforcement locations]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 337-341 — Import graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 577-598 — Package structure patterns]
- [Source: `packages/events/src/events/envelope.py:133` — ActorKind type]
- [Source: `packages/events/src/events/errors.py` — Exception hierarchy pattern]
- [Source: `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py:24` — _check_tier stub]
- [Source: `mcp-servers/session-registry/src/session_registry_mcp/handlers/tools.py:27` — _check_tier stub]
- [Source: `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:62` — _check_tier stub]

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
