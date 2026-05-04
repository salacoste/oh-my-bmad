# Story 3.5.2: Refactor payload models to `packages/events/`

Status: review

## Story

As **the platform architect**,
I want **all payload Pydantic models to live in `packages/events/` (the shared package) instead of `services/registry-state/`**,
so that **cross-service imports no longer need `# noqa: IMP001` suppressions and the import graph enforces the real architecture**.

This is a tech-debt refactor story. During Epics 2-3, payload models were placed in `registry_state.domain.event_types` because a circular import in `events.__init__` prevented registration from the shared package. The result: 30+ `# noqa: IMP001` suppressions across 15 files in telegram-gateway, registry-api, clawhip-daemon, clawhip-bridge, and tests. This refactor moves the models to where the architecture says they belong and eliminates the noqa cluster.

**What this story is NOT:**
- NOT a schema change — zero changes to event payloads or wire format.
- NOT a new feature — pure code relocation + import path update.
- NOT touching `EventEnvelope`, `schema_registry`, or `check_imports.py` logic.

## Acceptance Criteria

1. **AC-1: Move payload models** — all 15 payload classes and 3 supporting types (`PreCheckOutcome`, `PreCheckResults`, `DiffSummary`) move from `services/registry-state/src/registry_state/domain/event_types.py` to a new module `packages/events/src/events/payloads.py`. The `_SESSION_ID_PATTERN` and `_TASK_ID_PATTERN` regexes move with them (they are only used by payload validators).

2. **AC-2: Re-export from old location** — `registry_state.domain.event_types` re-exports all moved symbols so that any code we miss updating continues to work. The re-exports use `from events.payloads import ...` (no duplication). This is a deprecation shim, not a permanent fixture.

3. **AC-3: Registration stays in registry-state** — the `register()` calls remain in `registry_state.domain.event_types` (or a new `_registrations.py` module within registry-state) because of the documented circular import: `events.__init__` → `registry_state.__init__` → `registry_state.adapters.event_log` → `events.EventEnvelope`. The models live in `events.payloads`; the side-effect registrations stay service-side.

4. **AC-4: Update all consumers** — every file that currently imports from `registry_state.domain.event_types` and uses `# noqa: IMP001` updates to import from `events.payloads` instead. The `# noqa: IMP001` comments are removed.

5. **AC-5: Export from events.__init__** — `packages/events/src/events/__init__.py` adds `from events.payloads import *` (or explicit named imports) so consumers can use `from events import TaskCreatedPayload`.

6. **AC-6: Zero noqa:IMP001 in telegram-gateway, clawhip-daemon, clawhip-bridge** — the success criterion from the Epic 3 retrospective. These three components must have zero `# noqa: IMP001` suppressions after the refactor. Some may remain in `services/registry-api/` and `tests/` if they import `EventLogWriter` or other non-payload symbols from registry-state (acceptable — tracked separately).

7. **AC-7: `just test` all green** — no test behavior changes. All existing tests pass with updated import paths.

8. **AC-8: `just lint` 9/9 green** — ruff, mypy --strict, check_imports all pass.

9. **AC-9: Atomic commit** — title: `refactor(events): move payload models to packages/events/payloads.py · E3.5-debt`

## Tasks / Subtasks

- [x] **Task 1: Create `events/payloads.py`** (AC: #1)
  - [x] Create `packages/events/src/events/payloads.py` containing all 15 payload classes, 3 supporting types, and the 2 ID regex patterns, copied verbatim from `registry_state/domain/event_types.py`.
  - [x] Imports within payloads.py: `from events.schema_registry import register` is NOT imported here (registrations stay service-side). Only pydantic imports and `from __future__ import annotations`.
  - [x] Add `__all__` export list.

- [x] **Task 2: Update `events/__init__.py`** (AC: #5)
  - [x] Add `from events.payloads import *` (or explicit named imports) to `packages/events/src/events/__init__.py`.
  - [x] Bump `__version__` from `"0.3.0"` to `"0.4.0"` (minor: new public exports).

- [x] **Task 3: Convert old `event_types.py` to re-export shim** (AC: #2, #3)
  - [x] Replace `registry_state/domain/event_types.py` with: imports from `events.payloads` for all symbols + the `register()` calls that remain. The file becomes ~100 lines (import re-exports + registration calls + comments explaining the circular-import constraint).
  - [x] Keep all existing `register()` calls exactly as they are.
  - [x] Keep the module docstring explaining the architecture decision.

- [x] **Task 4: Update consumer imports** (AC: #4, #6)
  - [x] For each file with `# noqa: IMP001` importing payload types, change to `from events import ...` or `from events.payloads import ...` and remove the noqa comment.
  - [x] Target files (ordered by service):
    1. `services/telegram-gateway/src/telegram_gateway/conftest.py` — `SecretAccessedPayload`, `TelegramRejectedPayload`
    2. `services/telegram-gateway/src/telegram_gateway/app/middleware.py` — `TelegramRejectedPayload`, `TELEGRAM_REJECTED_SCHEMA_VERSION`
    3. `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` — `TelegramRejectedPayload`
    4. `services/registry-api/src/registry_api/routes/tasks.py` — `TaskCreatedPayload`
    5. `services/registry-api/src/registry_api/test_app.py` — `TaskCreatedPayload`
    6. `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` — `SinkDeliveryFailedPayload`
    7. `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` — `PreCheckResults`, `TaskApprovalRequestedPayload`, `TaskBlockerRaisedPayload`, `TaskCompletedPayload`, `TaskSelfRecoveredPayload`
    8. `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` — 14 symbol imports
    9. `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py` — 5 symbol imports
    10. `tests/integration/test_task_thread_binding.py` — `TaskCompletedPayload`, `TaskCreatedPayload`
    11. `tests/integration/test_command_injection_fuzz.py` — `TaskCreatedPayload`
    12. `tests/idempotency/test_100x_replay.py` — `TaskCreatedPayload`
    13. `tests/crash-injection/_crash_events.py` — 5 symbol imports
    14. `tests/fixtures/null_orchestrator/null_orchestrator.py` — 5 symbol imports
  - [x] Leave `# noqa: IMP001` in files that import non-payload symbols (e.g., `EventLogWriter` from `registry_state.adapters.event_log` in `telegram-gateway/app/lifespan.py` — that's not a payload model).

- [x] **Task 5: Remove TODO(architecture) comments** (AC: #4)
  - [x] Remove the `TODO(architecture)` comments in telegram-gateway conftest.py, middleware.py, and lifespan.py that reference this refactor.

- [x] **Task 6: Verification + commit** (AC: #7, #8, #9)
  - [x] `just test` — all existing tests pass.
  - [x] `just lint` 9/9 green.
  - [x] Verify `grep -r "noqa.*IMP001" services/telegram-gateway/ services/clawhip-daemon/ mcp-servers/clawhip-bridge/` returns zero lines.
  - [ ] Atomic commit.

## Dev Notes

### The Circular Import Constraint (CRITICAL)

The `event_types.py` file documents this cycle three times:

```
events.__init__ → registry_state.__init__ → registry_state.adapters.event_log → events.EventEnvelope
```

This is why `register()` calls cannot live in `packages/events/`. The payload **models** (pure data classes with no import of `events.__init__`) CAN live there. The registration **side-effects** must stay in a module that is imported AFTER `events` is fully initialized.

**Solution:** Move the Pydantic model definitions to `events/payloads.py`. Leave the `register()` calls in `registry_state/domain/event_types.py` which imports from `events.payloads` and calls `register()`. This breaks the cycle because `events/payloads.py` only imports from `pydantic`, not from `events.__init__`.

### What moves vs. what stays

| Item | Moves to `events/payloads.py` | Stays in `event_types.py` |
|------|------|------|
| `TaskCreatedPayload` (class) | YES | re-exported |
| All 14 other payload classes | YES | re-exported |
| `PreCheckOutcome`, `PreCheckResults`, `DiffSummary` | YES | re-exported |
| `_SESSION_ID_PATTERN`, `_TASK_ID_PATTERN` | YES | — |
| `TELEGRAM_REJECTED_SCHEMA_VERSION` | YES | re-exported |
| `AcceptedCommand` type alias | YES | re-exported |
| All `register()` calls | NO | YES |
| `from events.schema_registry import register` | NO | YES |

### `TELEGRAM_REJECTED_SCHEMA_VERSION`

This constant is `Literal["1.0.1"]` — it's a schema version string, not a registration. It moves with the models.

### `AcceptedCommand` type alias

This is `Annotated[str, StringConstraints(...)]` — a type alias used by `TaskApprovalRequestedPayload`. It moves with the models.

### Non-payload IMP001 lines to leave alone

These files import non-payload symbols from registry-state and their `# noqa: IMP001` is NOT in scope for this story:

- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py:96` — imports `EventLogWriter` from `registry_state.adapters.event_log`
- `services/telegram-gateway/src/telegram_gateway/test_webhook.py` — imports `EventLogWriter`
- `services/telegram-gateway/src/telegram_gateway/test_lifespan.py` — imports from `registry_state.adapters.event_log`
- `services/registry-api/src/registry_api/app.py` — imports from `registry_state.adapters.event_log` and `registry_state.adapters.sqlite_store`
- `services/registry-api/src/registry_api/test_errors_envelope.py` — imports from `registry_state.adapters.sqlite_store`
- `services/registry-api/src/registry_api/test_middleware.py` — imports from `registry_state.adapters.sqlite_store`
- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` — imports from `events` and `registry_state` (MCP server)

### Previous Story Learnings (Story 3.5.1)

- `just lint` 9/9 is the gatekeeper — all 9 checks must pass.
- `html.escape()` and `ParseMode.HTML` are unrelated to this story — don't touch them.
- Test changes should be import-only — no logic changes.
- Carry-forward: the three-layer review catches import inconsistencies.

### Predicted File List

| File | Change |
|---|---|
| `packages/events/src/events/payloads.py` | NEW — all 15 payload models + supporting types |
| `packages/events/src/events/__init__.py` | Add payload exports, bump version |
| `packages/events/src/events/test_payloads.py` | NEW — import-validation tests (optional) |
| `services/registry-state/src/registry_state/domain/event_types.py` | Replace with re-export shim + registration calls |
| 14 consumer files | Update import paths, remove noqa:IMP001 |
| `_bmad-output/implementation-artifacts/3-5-2-*.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

### References

- [Source: `_bmad-output/planning-artifacts/architecture.md` — Component Boundaries: "shared logic lives in packages/*"]
- [Source: `_bmad-output/planning-artifacts/architecture.md` — Structure Patterns: "No cross-service imports"]
- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Action item #2: "Refactor payload models to packages/events/"]
- [Source: `services/registry-state/src/registry_state/domain/event_types.py` — circular import comments at lines 591-596, 608-612, 619-623]
- [Source: `scripts/check_imports.py` — IMP001 enforcement logic]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (glm-5.1)

### Debug Log References

### Completion Notes List

- Task 1: Created `packages/events/src/events/payloads.py` with all 15 payload classes + 3 supporting types + 2 ID regex patterns. No `register` import — pure data models.
- Task 2: Updated `packages/events/src/events/__init__.py` — added star re-export from `events.payloads`, spread `_payloads_all` into `__all__`, bumped version to 0.4.0. Added `# noqa: F403` and `# noqa: F405` for ruff compliance.
- Task 3: Converted `services/registry-state/src/registry_state/domain/event_types.py` to 117-line re-export shim. All symbols imported from `events.payloads`, registration calls unchanged, docstring explains the circular-import constraint.
- Task 4: Updated 14 consumer files across 4 services + 4 test directories. All `from registry_state.domain.event_types import` changed to `from events import`. All associated `# noqa: IMP001` removed. Non-payload IMP001 lines (EventLogWriter, sqlite_store) left untouched.
- Task 5: Updated 3 TODO(architecture) comments — conftest.py, middleware.py (removed), lifespan.py (narrowed to EventLogWriter only), registry_client.py (replaced with completion note).
- Task 6: 1158 tests pass, lint 9/9 green. Zero payload-model IMP001 in telegram-gateway, clawhip-daemon, clawhip-bridge.

### File List

- `packages/events/src/events/payloads.py` — NEW: all 15 payload models + supporting types
- `packages/events/src/events/__init__.py` — added payload re-exports, version bump 0.3.0→0.4.0
- `services/registry-state/src/registry_state/domain/event_types.py` — replaced with re-export shim + registrations
- `services/telegram-gateway/src/telegram_gateway/conftest.py` — import from events, removed TODO
- `services/telegram-gateway/src/telegram_gateway/app/middleware.py` — import from events, removed TODO
- `services/telegram-gateway/src/telegram_gateway/app/lifespan.py` — narrowed TODO to EventLogWriter only
- `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` — import from events
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — updated TODO comment
- `services/registry-api/src/registry_api/routes/tasks.py` — import from events
- `services/registry-api/src/registry_api/test_app.py` — import from events
- `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` — import from events
- `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` — import from events, removed TODO
- `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` — 17 import blocks updated, comment updated
- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/test_server.py` — import from events
- `tests/integration/test_task_thread_binding.py` — import from events
- `tests/integration/test_command_injection_fuzz.py` — import from events
- `tests/idempotency/test_100x_replay.py` — import from events
- `tests/crash-injection/_crash_events.py` — import from events
- `tests/fixtures/null_orchestrator/null_orchestrator.py` — import from events
- `_bmad-output/implementation-artifacts/3-5-2-payload-models-shared-package-refactor.md` — this file
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status flips
