# Story 6.2: Tier enforcement at MCP handler boundaries

Status: done

## Story

As the platform,
I want every MCP tool handler to enforce capability tiers including the Tier-3 approval gate,
so that Tier-3 actions cannot be triggered through the MCP surface without a matching approval event.

## Acceptance Criteria

1. **AC-1: Tier-2 enforcement at handler level** — Given a caller whose max tier is below Tier-2 attempts a Tier-2 tool, the handler returns `CapabilityDenied` before any side-effect runs. Verified by a negative test that patches a tool's TIER_MAP entry to `Tier.TWO` and asserts denial from a constrained caller.

2. **AC-2: Tier-3 approval gate** — `check_tier` accepts a `has_approval: bool` parameter (default `True` for backward compat). When `required_tier == Tier.THREE` and `has_approval is False`, it raises `CapabilityDenied` with reason containing `"no_matching_approval"`.

3. **AC-3: `tier3.action_attempted` event type** — Registered in `events/schema_registry` with payload `{action, task_id, accepted: bool, reason: str | None}`. Emitted with `{accepted: false, reason: "no_matching_approval"}` on denied Tier-3 attempts.

4. **AC-4: Approval lookup helper** — `packages/capabilities/` exports an async `check_tier_with_approval(action, caller, required_tier, approval_lookup)` that:
   - Calls `check_tier` first (actor-kind gate)
   - For Tier-3 only: awaits `approval_lookup(task_id, action)` → if False, raises `CapabilityDenied` with "no_matching_approval"
   - Returns `CapabilityOk` on success

5. **AC-5: MCP handler pattern for Tier-3** — Each MCP server's tools module exports a `_make_approval_lookup(session_maker)` factory that returns an async `(task_id, action) -> bool` callable querying the materialized `Event` table for `approval.granted` events. No Tier-3 tools exist yet (6.7 adds them); this story creates the mechanism.

6. **AC-6: Negative Tier-3 test** — A test simulates a Tier-3 tool call without a matching approval event. Asserts: `CapabilityDenied` raised, reason contains `"no_matching_approval"`, and a `tier3.action_attempted` event would have been emitted (tested via log capture or mock).

7. **AC-7: No regression** — All existing 291 tests continue to pass. `check_imports.py` exits 0. `ruff check` clean.

8. **AC-8: Atomic commit** — Single commit with title `feat(capabilities): tier enforcement at MCP handler boundaries (Story 6.2)`.

## Tasks

- [ ] Task 1 — Extend `check_tier` with `has_approval` parameter (AC-2)
  - [ ] Add `has_approval: bool = True` parameter to `check_tier` in `tiers.py`
  - [ ] When `required_tier >= Tier.THREE` and `not has_approval`: raise `CapabilityDenied` with action, actor_kind, required_tier=3, reason containing `"no_matching_approval"`
  - [ ] Update `CapabilityOk` return unchanged for the happy path
  - [ ] Add tests: test_tier3_denied_without_approval, test_tier3_allowed_with_approval, test_tier2_ignores_has_approval
- [ ] Task 2 — Add `tier3.action_attempted` event type (AC-3)
  - [ ] Define `Tier3ActionAttemptedPayload` in `packages/events/` (Pydantic model: `action: str`, `task_id: str`, `accepted: bool`, `reason: str | None = None`)
  - [ ] Register `"tier3.action_attempted"` in the event registry
  - [ ] Export from `events/__init__.py`
- [ ] Task 3 — Add `check_tier_with_approval` to capabilities (AC-4)
  - [ ] New async function in `tiers.py` accepting `approval_lookup: Callable[[str, str], Awaitable[bool]] | None`
  - [ ] Calls `check_tier` first; only queries approval for Tier.THREE
  - [ ] Re-exports from `__init__.py`
  - [ ] Add tests with mock approval_lookup
- [ ] Task 4 — Add approval lookup helpers to MCP servers (AC-5)
  - [ ] task-registry: `_make_approval_lookup(session_maker)` → queries `Event` rows where `type="approval.granted"` and `task_id` matches
  - [ ] session-registry: same pattern
  - [ ] clawhip-bridge: `_make_approval_lookup(base_dir, clock)` → queries JSONL log for `approval.granted` events
  - [ ] Add unit tests for each lookup helper with seeded data
- [ ] Task 5 — Add enforcement tests (AC-1, AC-6)
  - [ ] Test Tier-2 denial at handler level (patch TIER_MAP entry to Tier.TWO, use worker caller which can still reach Tier.TWO — need a scenario; alternatively patch actor max tier to simulate Tier-0 caller)
  - [ ] Test Tier-3 denial without approval (mock tool at Tier.THREE, no approval event → CapabilityDenied)
  - [ ] Test Tier-3 allowed with approval (mock tool at Tier.THREE, approval event seeded → succeeds)
- [ ] Task 6 — Verification + commit (AC-7, AC-8)
  - [ ] Run `check_imports.py`
  - [ ] Run `ruff check`
  - [ ] Run `just test` (all tests pass)
  - [ ] Atomic commit

## Dev Notes

### Key Architecture Decision: Two-Layer Enforcement

Story 6.1 created `check_tier` which does actor-kind → max-tier mapping. Story 6.2 adds the Tier-3 approval gate on top. The design is:

```
MCP handler entry
  └─ check_tier_with_approval(action, caller, tier, approval_lookup)
       ├─ check_tier(action, caller, tier)           # Layer 1: actor-kind gate (6.1)
       │    └─ raises CapabilityDenied if max_tier < required_tier
       └─ if tier == Tier.THREE:
            └─ approval_lookup(task_id, action)       # Layer 2: approval gate (6.2)
                 └─ raises CapabilityDenied if no matching approval
```

### Why `has_approval` parameter on `check_tier` AND a separate `check_tier_with_approval`

- `check_tier(has_approval=...)` is the synchronous, simple case — useful when the caller has already looked up approval status
- `check_tier_with_approval(approval_lookup=...)` is the async, complete case — handles the full two-layer check in one call
- Both are needed: MCP handlers use `check_tier_with_approval` for convenience, while other code paths (e.g., HTTP middleware in 6.3) may use `check_tier` directly with pre-computed `has_approval`

### Approval Lookup Implementation

The approval lookup queries the materialized `Event` table for:
- `type = "approval.granted"`
- `task_id = <caller's task_id>`
- `payload_json` contains the action (or a wildcard approval)

No `approval.granted` event type exists yet — it will be registered in Story 6.5. For this story, the lookup helper queries for the event type string and returns `False` if no rows found. Tests seed `approval.granted` events directly via the `Event` model.

### Import Graph Constraints

- `packages/capabilities/` may import from `packages/events/` only
- `mcp-servers/*` may import from `packages/*` only
- `mcp-servers/*` may import from `services/registry-state/` (per AC-7/Arch exception)
- The approval lookup helper lives in each MCP server's tools module, NOT in `capabilities`, because it needs SQLAlchemy/session access

### Files to Touch

| File | Change |
|------|--------|
| `packages/capabilities/src/capabilities/tiers.py` | Add `has_approval` param to `check_tier`, add `check_tier_with_approval` |
| `packages/capabilities/src/capabilities/__init__.py` | Re-export new symbol |
| `packages/capabilities/src/capabilities/test_tiers.py` | Tests for Tier-3 gate |
| `packages/events/src/events/__init__.py` | Export `Tier3ActionAttemptedPayload` |
| `packages/events/src/events/payloads.py` (or equivalent) | Define `Tier3ActionAttemptedPayload` |
| `mcp-servers/task-registry/.../handlers/tools.py` | Add `_make_approval_lookup` |
| `mcp-servers/task-registry/.../test_server.py` | Tier-2/Tier-3 enforcement tests |
| `mcp-servers/session-registry/.../handlers/tools.py` | Add `_make_approval_lookup` |
| `mcp-servers/session-registry/.../test_server.py` | Tier-2/Tier-3 enforcement tests |
| `mcp-servers/clawhip-bridge/.../server.py` | Add `_make_approval_lookup` |
| `mcp-servers/clawhip-bridge/.../test_server.py` | Tier-2/Tier-3 enforcement tests |

### Gotchas from Previous Stories

- **structlog**: Never use `event=` as kwarg with structlog loggers — clashes with positional `event` param. Use `cap_event=` or similar.
- **Event registration**: Test files must register event types via `_reg()` in autouse fixture. The `test_server.py` files already do this for existing types.
- **`approval.granted` event type**: Does NOT exist yet. Story 6.5 adds it. This story's lookup helper queries for the string `"approval.granted"` and tests seed `Event` rows directly. The event type must be registered in the test fixture for tests to pass.

### Scope Boundary

- Do NOT add actual Tier-3 tools (e.g., `git_push`) — that is Story 6.7
- Do NOT add HTTP middleware enforcement — that is Story 6.3
- Do NOT add `approval.granted` event emission — that is Story 6.5
- Do NOT add `tier3.action_performed` event — that is Story 6.6
- DO create the mechanism (approval lookup, Tier-3 gate in check_tier, tests)

### References

- [Source: epics.md — Epic 6 Story 6.2]
- [Source: prd.md — FR37, FR38, NFR-S6]
- [Source: architecture.md — line 214 authorization model, line 823 enforcement locations]
- [Source: 6-1 story artifact — design decisions, scope boundary, CallerContext.task_id purpose]

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
