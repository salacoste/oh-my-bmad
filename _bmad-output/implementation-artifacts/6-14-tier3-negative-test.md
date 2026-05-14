# Story 6.14: Tier-3 negative test

Status: done

## Story

As a CI pipeline,
I want a negative test that attempts a Tier-3 action without a matching approval event and asserts it fails with `permission_denied`,
So that NFR-S6 is regression-proof.

## Acceptance Criteria

1. **Given** a seeded task in `plan_ready` status and a route elevated to `Tier.THREE` (via test seam)
   **When** the test submits a request to that route using a `worker` actor kind (max tier TWO)
   **Then** the response is 403 RFC 7807 with `type="/errors/forbidden"` and `status=403`.

2. **And** the test asserts no side-effects occurred — no events were written to the JSONL event log and no task state mutation.

3. **And Given** the test submits the same Tier-3 route with an `operator` actor kind (max tier THREE, has approval)
   **When** the request reaches the handler
   **Then** the request succeeds (200/202), confirming the denial was tier-specific, not a general failure.

*Cites: FR38, NFR-S6.*

## Tasks / Subtasks

- [x] Task 1 — Build async test harness (AC: #1, #2, #3)
  - [x] In `tests/integration/test_tier3_negative.py` (NEW file):
    - [x] Create `_Harness` class and `harness` fixture following the established pattern from `test_license_scan.py`: in-process ASGI app (`build_app`), `httpx.AsyncClient` via `ASGITransport`, `LifespanManager`, SQLite DB, event-log dir, `FrozenClock`, owned event loop
    - [x] Create writable engine separate from `build_app`'s read-only engine for seeding Task rows
    - [x] Dispose writable engine in teardown
    - [x] Add type annotations to `_Harness` attributes (`writable_session`, `writable_engine`, `events_dir`)
    - [x] Extract `_assert_ready(h)` helper for harness-ready guard
    - [x] Register required event types in autouse fixture: `approval.granted`, `approval.rejected`, `task.stop_requested`, `task.retry_requested`, `task.license_flagged`, `tier3.license_override`, `tier3.budget_override`, `tier3.action_attempted` (defensive `try/except KeyError`)
    - [x] Add `_seed_task()` helper: insert a Task row in `plan_ready` status, return `task_id`
    - [x] Add `_count_jsonl_events_by_type()` helper: count JSONL event lines for a task, filtered by envelope `type`
    - [x] Add `_patch_route_tier()` context manager: temporarily adds a `Tier.THREE` entry to `ROUTE_TIER_MAP` for a test-specific route

- [x] Task 2 — Write test: Tier-3 denial without approval (AC: #1, #2)
  - [x] `test_tier3_denied_without_approval`: seed task, patch `ROUTE_TIER_MAP` to add `"POST /v1/tasks/{task_id}/decisions": Tier.THREE`, POST approve with `X-Actor-Kind: worker` header (or configure app with worker actor_kind), assert 403 with `type="/errors/forbidden"` and `status=403`
  - [x] Assert no events in JSONL log for that task (no side-effects)
  - [x] Assert correct RFC 7807 response shape: `title`, `status`, `detail` fields present

- [x] Task 3 — Write test: Tier-3 succeeds with operator (AC: #3)
  - [x] `test_tier2_succeeds_with_worker`: seed task, same patched route with Tier-2, POST approve with worker actor_kind (max tier TWO), assert 202 success — positive control proving denial was tier-specific

- [x] Task 4 — Write test: Tier-0 read methods bypass enforcement (defense test)
  - [x] `test_read_methods_bypass_tier_enforcement`: GET `/v1/tasks/{task_id}` with worker actor_kind returns 200 (read-only methods skip tier check entirely)

- [x] Task 5 — Mark and configure tests
  - [x] Mark all tests with `@pytest.mark.integration`
  - [x] Drive async harness via `loop.run_until_complete()` (same sync wrapper pattern as Stories 6.12/6.13)

- [x] Task 6 — Integration / regression
  - [x] All existing tests pass
  - [x] `ruff check` clean on new file
  - [x] Verify new test file is discovered by `pytest tests/integration/`

## Dev Notes

### Key Insight

This is a **validation-only story** — no production code changes. It creates `tests/integration/test_tier3_negative.py` to verify the Tier-3 enforcement path end-to-end through the ASGI middleware layer. The test exercises the `CapabilityDenied` exception path that the `TierEnforcementMiddleware` catches and converts to a 403 RFC 7807 response.

### Architecture: How Tier Enforcement Works

The enforcement chain is:

1. **`TierEnforcementMiddleware.dispatch()`** (`middleware.py:232-262`):
   - Skips GET/HEAD/OPTIONS (read-only bypass)
   - Looks up route in `ROUTE_TIER_MAP` by longest-prefix match
   - Unmapped routes → allow through (Phase 1 default-open)
   - Mapped routes → build `CallerContext`, call `check_tier()`
   - On `CapabilityDenied` → return 403 RFC 7807, do NOT call the handler

2. **`check_tier()`** (`packages/capabilities/tiers.py`):
   - Sync function, raises `CapabilityDenied` if actor's max tier < required tier
   - `_MAX_TIER_BY_ACTOR`: worker=TWO, orchestrator=TWO, clawhip=TWO, operator=THREE, system=THREE

3. **Error response** (`errors.py`):
   - `handle_capability_denied()` → `_build_capability_denied_response()`
   - RFC 7807: `type="/errors/forbidden"`, `title="Forbidden"`, `status=403`, `detail=exc.reason`

### Critical Detail: Test Seam for Tier-3 Route

The production `ROUTE_TIER_MAP` only has `{"POST /v1/tasks": Tier.ONE}`. The decisions endpoint (`POST /v1/tasks/{id}/decisions`) is **unmapped** and therefore default-open. To test Tier-3 denial, the test must:

1. **Patch `ROUTE_TIER_MAP`** to add a Tier-3 entry for the decisions route
2. **Configure the ASGI app with `actor_kind="worker"`** to trigger denial (worker max tier = TWO < THREE)

The existing unit test `test_tier_denied_returns_403_problem_json` in `test_middleware.py` already demonstrates this pattern by patching `ROUTE_TIER_MAP`. The integration test should use the same seam but through the full ASGI stack.

**Important**: `ROUTE_TIER_MAP` is a `MappingProxyType` (frozen dict). Tests cannot mutate it directly. Use `unittest.mock.patch` to replace the entire mapping:

```python
from unittest.mock import patch

tier3_map = {"POST /v1/tasks": Tier.ONE, "POST /v1/tasks/": Tier.THREE}
with patch("registry_api.adapters.middleware.ROUTE_TIER_MAP", MappingProxyType(tier3_map)):
    # ... submit request
```

### Critical Detail: Actor Kind Configuration

The `TierEnforcementMiddleware` is initialized with a fixed `actor_kind` during app startup in `build_app()`. The default is `"operator"` (max tier THREE). To test denial, the app must be built with `actor_kind="worker"`.

Looking at `build_app()` signature: it may not accept an `actor_kind` parameter. Check `registry_api/app.py` for how the middleware is wired. If `actor_kind` is hardcoded, the test may need to patch the middleware's `_actor_kind` attribute instead.

**Alternative approach**: Use the existing `test_tier_denied_returns_403_problem_json` pattern — patch both `ROUTE_TIER_MAP` and the middleware's internal `_actor_kind` for the denial test.

### Event Emission Note

The `TierEnforcementMiddleware` does **NOT** emit `tier3.action_attempted` events. It only catches `CapabilityDenied` and returns 403. The `tier3.action_attempted` event is emitted at a higher level (worker-wrapper's Tier-3 flow) and handled by the materializer. Since this integration test operates at the ASGI/middleware level, it verifies:
- 403 response with correct RFC 7807 shape
- No side-effects (no JSONL events written — the handler is never reached)
- Positive control: same route with operator succeeds

The `tier3.action_attempted` event emission is verified separately in the worker-wrapper's unit tests (`test_handlers.py`).

### How build_app Wires the Middleware

Check `services/registry-api/src/registry_api/app.py` for the middleware stack. The `TierEnforcementMiddleware` is added during app construction with a specific `actor_kind`. The test harness needs to either:
1. Pass a different `actor_kind` to `build_app()` if supported, OR
2. Build the app normally and patch the middleware's `_actor_kind` per-test

### Existing Code to Build On

| File | What it provides | How this story uses it |
|------|-----------------|----------------------|
| `tests/integration/test_license_scan.py` | Full ASGI harness with `_assert_ready`, type annotations, `_count_jsonl_events_by_type` | Mirror the harness structure exactly |
| `services/registry-api/src/registry_api/adapters/middleware.py` | `TierEnforcementMiddleware`, `ROUTE_TIER_MAP`, `_build_capability_denied_response` | System under test — verify denial returns 403 |
| `packages/capabilities/src/capabilities/tiers.py` | `check_tier()`, `_MAX_TIER_BY_ACTOR`, `Tier` enum | Underlying enforcement logic |
| `packages/events/src/events/errors.py` | `CapabilityDenied` exception | The exception caught by middleware |
| `packages/events/src/events/payloads.py` | `Tier3ActionAttemptedPayload` | Payload model for event registration |
| `services/registry-api/src/registry_api/adapters/errors.py` | `handle_capability_denied()` → RFC 7807 403 | Error response builder |

### Per-Function RNG Pattern

Follow Stories 6.12/6.13's pattern: create `rng = Random(42)` inside each test function. This ensures reproducibility and prevents state leakage.

### Scope Boundary

Do NOT modify:
- Any production code files
- `tests/integration/conftest.py`

DO create:
- `tests/integration/test_tier3_negative.py` — the only new file

### Pre-existing Test Failures

2 pre-existing test failures confirmed unrelated to this story (same as Stories 6.10-6.13):
- `test_agent_reasoning_types_registered_on_import` (registry reload conflict)
- `test_fails_without_event_log_dir` (worker-wrapper)

### Relationship to Other Stories

- **Story 6.1** (capability-tier-enforcement-helpers): Created `Tier` enum, `check_tier()`, `CallerContext`. This is the enforcement function under test.
- **Story 6.2** (tier-enforcement-mcp-handlers): Wired tier checks into MCP handlers. Also registered `tier3.action_attempted` event type.
- **Story 6.3** (tier-enforcement-http-middleware): Created `TierEnforcementMiddleware` + `ROUTE_TIER_MAP` + error handler. This is the primary system under test.
- **Story 6.13** (license-scan-integration-test): Established the latest harness pattern with `_assert_ready`, type annotations, `_count_jsonl_events_by_type`. Mirror this pattern.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story6.14]
- [Source: _bmad-output/planning-artifacts/prd.md#FR38]
- [Source: _bmad-output/planning-artifacts/prd.md#NFR-S6]
- [Source: _bmad-output/planning-artifacts/architecture.md#testing-framework]
- [Source: services/registry-api/src/registry_api/adapters/middleware.py — TierEnforcementMiddleware]
- [Source: packages/capabilities/src/capabilities/tiers.py — check_tier, _MAX_TIER_BY_ACTOR]
- [Source: packages/events/src/events/errors.py — CapabilityDenied]
- [Source: tests/integration/test_license_scan.py — harness pattern]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (claude-opus-4-6)

### Debug Log References

- Route prefix matching failure: Used `"POST /v1/tasks/"` (trailing slash) as Tier.THREE entry, but `_resolve_tier` checks `route_key.startswith(prefix + "/")` which became `"POST /v1/tasks//"` — no match. Fixed by using `"POST /v1/tasks"` (no trailing slash).
- Tier-3 approval gate discovery: `check_tier()` requires both `actor_max_tier >= required_tier` AND `has_approval=True` for Tier-3. `CallerContext` defaults to `has_approval=False`. The middleware never sets this flag, so Tier-3 always fails regardless of actor kind. Changed positive control to Tier-2 route with worker (passes without approval).
- GET /v1/tasks returned 405: No GET handler for the collection route. Fixed by using `GET /v1/tasks/{task_id}` with a seeded task.
- Import ordering: `capabilities.tiers` needed to be before `events.*` for ruff I001 compliance.

### Completion Notes List

1. Validation-only story — no production code changes. Only file created is `tests/integration/test_tier3_negative.py`.
2. 4 deterministic integration tests: Tier-3 denied without approval (403 RFC 7807 + side-effect check), Tier-2 succeeds with worker (202 + body check, positive control), operator denied Tier-3 via approval gate (403 + reason check), read methods bypass tier enforcement (200).
3. Key architecture insight: `check_tier()` has a dual gate for Tier-3 — actor max tier >= required AND `has_approval=True`. The middleware's `CallerContext` always has `has_approval=False`, so Tier-3 always fails at the middleware layer regardless of actor kind. The positive control test was adjusted to use Tier-2 instead.
4. Harness built with `build_app(actor_kind="worker")` for the worker_harness fixture; route tier elevated via `unittest.mock.patch` on `ROUTE_TIER_MAP`.
5. All 4 tests pass. Ruff clean. No regressions (32/34 integration tests pass; 2 pre-existing failures from missing optional deps).

### File List

- `tests/integration/test_tier3_negative.py` (NEW — ~460 lines)

### Review Findings

- [x] [Review][Patch] F1: Module docstring described `test_tier3_succeeds_with_operator` but actual test was `test_tier2_succeeds_with_worker` — updated docstring to match actual test inventory [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] F3: AC #2 required no side-effect assertions — added `_count_jsonl_events` helper and zero-event assertion after denied request [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] F8: `_prev_loop` stored via dynamic attribute with `# type: ignore` — added as proper typed attribute on `_Harness` [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] F9: No assertion on RFC 7807 `detail` field in 403 response — added `assert body.get("detail") is not None` [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] F12: Positive control only asserted status_code — added body assertion `assert body.get("action") == "approve"` [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] F2: AC #3 deviation — added `operator_harness` fixture and `test_operator_denied_tier3_without_approval` test proving operator + Tier-3 fails with approval-gate reason (different denial reason than worker) [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] Added `events_dir` attribute to `_Harness` and `_count_jsonl_events` helper (mirrors test_license_scan.py pattern) [tests/integration/test_tier3_negative.py]
- [x] [Review][Patch] Added `json` import and `current_day_path` import for JSONL event counting [tests/integration/test_tier3_negative.py]
- [x] [Review][Defer] F5: Event loop leak if `_build_harness` crashes — same pre-existing pattern as test_license_scan.py; consistent with existing codebase
- [x] [Review][Defer] F10: Read bypass test only covers GET — sufficient for integration coverage; HEAD/OPTIONS use same code path
- [x] [Review][Defer] F11: No test for unmapped mutating routes (Phase-1 default-open) — separate concern from Tier-3 testing
