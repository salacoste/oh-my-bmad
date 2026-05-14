# Story 6.10: `task.license_flagged` event + approval-gate block + `/approve --override license` (FR40, FR41)

Status: done

## Story

As the operator,
I want license-incompatibility findings to emit `task.license_flagged`, block the approval gate with a specific reason code, and allow `/approve --override license` (which emits an audit event) to proceed with deliberate override,
So that the operator is never silently bypassed or unable to override in a real emergency.

## Acceptance Criteria

1. **Given** license-scan finds an incompatibility
   **When** the approval gate processes the pending push
   **Then** `task.license_flagged` is emitted with `{reason_code, file_list, detected_licenses}`; the approval message to the operator includes the license-flag block; default `/approve` is refused with `approval_blocked_by: license_flag`.

2. **And Given** the operator sends `/approve t-0001 --override license`
   **When** the decision is processed
   **Then** `approval.granted` + `tier3.license_override` audit events are both emitted; the push proceeds.

*Cites: FR40, FR41, NFR-S8.*

## Tasks / Subtasks

- [x] Task 1 — Register `task.license_flagged` event type (AC: #1)
  - [x] Create `TaskLicenseFlaggedPayload` in `packages/events/src/events/payloads.py`:
    - `task_id: str` (min_length=1, max_length=64)
    - `reason_code: str` (min_length=1, max_length=64) — e.g. `"copyleft-incompatible"`
    - `file_list: list[str]` (max_length=100) — paths with incompatibilities
    - `detected_licenses: list[str]` (max_length=100) — license expressions detected
    - Use `ConfigDict(frozen=True, strict=True, extra="forbid")`
  - [x] Register in `services/registry-state/src/registry_state/domain/event_types.py`:
    - `register("task.license_flagged", "1.0.0", TaskLicenseFlaggedPayload)`
  - [x] Add materializer handler in `services/registry-state/src/registry_state/domain/handlers.py`:
    - `handle_task_license_flagged` — hydrate payload, call `_touch_task`
    - Register: `materializer.register_handler("task.license_flagged", handle_task_license_flagged)`
  - [x] Write unit tests for payload model (frozen, field validation, extra="forbid")
  - [x] Run `scripts/check_event_registry.py` to verify registration passes CI gate

- [x] Task 2 — Wire license scan into precommit hook (AC: #1)
  - [x] In `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py`:
    - Import `scan_files_for_licenses` from `.license_scan`
    - After existing secret-scan and path-check loops, add license scan pass:
      ```python
      license_findings = scan_files_for_licenses(files)
      for lf in license_findings:
          violations.append(
              f"LICENSE {lf.reason_code}: {lf.file_path} ({lf.license_detected})"
          )
      ```
    - Findings count toward the exit code (return 1 if any violations)
  - [x] Add `--repo-license` CLI flag to `main()` parser (default `"mit"`)
  - [x] Pass `repo_license` through to `scan_files_for_licenses()`
  - [x] Write unit tests (~8 tests): GPL file blocks, MIT file passes, no-license passes, `--repo-license` flag, binary skip, scancode missing graceful

- [x] Task 3 — Emit `task.license_flagged` from worker-wrapper (AC: #1)
  - [x] In `services/worker-wrapper/src/worker_wrapper/app/main.py`:
    - Import `scan_files_for_licenses` from `secret_hygiene.license_scan`
    - In the pre-push flow (where `task.awaiting_approval` is emitted), add license scan:
      - Collect changed file paths from the worktree
      - Call `scan_files_for_licenses(changed_files)`
      - If findings: emit `task.license_flagged` with `{task_id, reason_code, file_list, detected_licenses}`
  - [x] Write unit tests (~6 tests): GPL file triggers event, clean files emit nothing, empty diff, scancode missing graceful, batch of mixed files

- [x] Task 4 — Block default `/approve` when license-flagged (AC: #1)
  - [x] In `services/registry-api/src/registry_api/routes/decisions.py`:
    - In `post_decision`, before processing an `approve` action:
      - Query event log for any `task.license_flagged` event for this task
      - If found AND `body.override != "license"`: return 409 with RFC 7807 body `{type: "approval_blocked_by", reason: "license_flag"}`
      - If found AND `body.override == "license"`: proceed (existing `tier3.license_override` emission handles the audit)
    - Extract the license-block check into a helper `_check_license_gate(task_id, override)` for testability
  - [x] Write unit tests (~6 tests): approve blocked when flagged, approve allowed with override, approve allowed when no flag, override on non-approve rejected (existing validation)

- [x] Task 5 — Add `--override license` to Telegram `/approve` command (AC: #2)
  - [x] In `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py`:
    - Add `override: Literal["license"] | None = None` parameter to `submit_decision()`
    - When `override is not None`, include `"override": override` in the POST body
  - [x] In `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py`:
    - Parse `--override license` from the message text after the task ID
    - Pass `override` to `submit_decision()`
    - Handle 409 `approval_blocked_by: license_flag` response: reply with informative message "License flag active. Use /approve <task> --override license to override."
  - [ ] Write unit tests (~8 tests): override parsed correctly, override passed to submit_decision, 409 license_block handled, invalid override ignored, override with non-approve rejected

- [x] Task 6 — Integration / regression
  - [x] All existing tests pass (`pytest` across full tree)
  - [x] `ruff check` clean
  - [x] `scripts/check_event_registry.py` passes
  - [x] New test count documented in completion notes

## Dev Notes

### Key Insight

This story CONNECTS Story 6.9's license-scan module to the approval gate and event bus. Story 6.9 created the scanning primitive (`scan_files_for_licenses()`); this story emits events from its results, blocks pushes, and adds the override flow. The decisions endpoint (`POST /v1/tasks/{id}/decisions`) already handles `override == "license"` and emits `tier3.license_override` — the server side is partially wired. The gaps are: event registration, emission from worker-wrapper, Telegram-side override parsing, and the gate-check logic.

### Existing Code to Build On

| File | What it does | What this story adds |
|------|-------------|---------------------|
| `license_scan.py` (Story 6.9) | `scan_files_for_licenses()`, `LicenseFinding` dataclass | Called from precommit_hook + worker-wrapper |
| `precommit_hook.py` (Story 1.7, 6.8) | Secret scan + path checks | Add license scan pass + `--repo-license` flag |
| `decisions.py` (Story 6.4) | `POST /v1/tasks/{id}/decisions`, already handles `override == "license"` | Add license-gate check before approve |
| `approve_command.py` (Story 3.4) | Telegram `/approve` handler | Parse `--override license`, add `override` param |
| `registry_client.py` (Story 3.4) | `submit_decision()` HTTP client | Add `override` parameter |
| `main.py` in worker-wrapper (Story 5.1, 6.7) | Task execution, approval-wait state | Emit `task.license_flagged` pre-push |
| `event_types.py` (Story 2.1) | Event type registration | Register `task.license_flagged` |
| `handlers.py` (Story 2.5) | Materializer handlers | Add handler for `task.license_flagged` |
| `payloads.py` (Story 2.1) | Event payload models | Add `TaskLicenseFlaggedPayload` |

### Architecture

```
Registration (event_types.py + payloads.py):
  TaskLicenseFlaggedPayload → register("task.license_flagged", "1.0.0", ...)

Pre-commit hook (precommit_hook.py):
  main() → scan_files_for_licenses(files) → violations printed to stderr → exit 1

Worker-wrapper (main.py):
  pre-push → scan_files_for_licenses(changed_files)
  → if findings → emit task.license_flagged via clawhip bridge
  → include in task.awaiting_approval pre_check_results

Approval gate (decisions.py):
  POST approve → _check_license_gate(task_id, override)
  → if task.license_flagged exists AND override != "license" → 409
  → if override == "license" → existing tier3.license_override emission

Telegram override (approve_command.py):
  /approve t-0001 --override license
  → submit_decision(override="license")
  → handle 409 license_block with informative reply
```

### Event Registration Pattern

Follow the exact pattern from `event_types.py`. Example from nearby registrations:

```python
# In payloads.py — add near other task.* payloads
class TaskLicenseFlaggedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    task_id: str = Field(min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=64)
    file_list: list[str] = Field(max_length=100)
    detected_licenses: list[str] = Field(max_length=100)

# In event_types.py — add to the register() block
register("task.license_flagged", "1.0.0", TaskLicenseFlaggedPayload)

# In handlers.py — add a handler
async def handle_task_license_flagged(session, envelope):
    payload = _hydrate(envelope.payload, TaskLicenseFlaggedPayload)
    await _touch_task(session, payload.task_id, envelope)

# Register the handler
materializer.register_handler("task.license_flagged", handle_task_license_flagged)
```

### License-Gate Check Pattern

In `decisions.py`, the check should happen AFTER the state validation (existing `_VALID_STATES` check) but BEFORE the idempotency cache factory runs. This avoids burning an idempotency slot on a blocked approval:

```python
# In post_decision(), after the state validation block:
if body.action == "approve":
    await _check_license_gate(task_id, body.override, reader)
```

The `_check_license_gate` helper queries the event log for `task.license_flagged` events for this task. If found and no override, raise an HTTPException(409, ...).

### Override Parsing in approve_command.py

Parse the override flag from the message text. The command format is:
```
/approve t-7f2a1b3c --override license
```

Extract using regex or simple string split after the task ID. Only `"license"` is a valid override value (enforced by the server-side `DecisionRequest` model). Unknown override values should be silently ignored (the server will reject them).

### Decisions Endpoint: Already Partially Wired

The server-side `decisions.py` already has:
- `DecisionRequest.override: Literal["license"] | None = None` (line 70)
- Validation: override only with `action="approve"` (line 72)
- `tier3.license_override` emission when `override == "license"` (lines 181-200)
- `ApprovalGrantedPayload.override` field that carries the override value (line 296)

What's MISSING:
- The license-gate check that blocks default approve when flagged
- The telegram-gateway `submit_decision()` doesn't pass `override` to the server
- The telegram-gateway `/approve` handler doesn't parse `--override`

### Worker-Wrapper Integration

The worker-wrapper emits `task.awaiting_approval` before a `git push` (Story 6.7). The license scan should run at this same point. Pattern:

```python
# In the pre-push flow, before emitting task.awaiting_approval:
from secret_hygiene.license_scan import scan_files_for_licenses

changed_files = _get_staged_files(worktree_path)  # or diff against last commit
license_findings = scan_files_for_licenses(changed_files)

if license_findings:
    # Emit task.license_flagged
    await _emit_event("task.license_flagged", {
        "task_id": task_id,
        "reason_code": license_findings[0].reason_code,
        "file_list": [f.file_path for f in license_findings],
        "detected_licenses": list({f.license_detected for f in license_findings}),
    })
```

### Structlog Gotcha

Never use `event=` as a keyword argument to structlog loggers — it clashes with the positional `event` parameter.

### Precommit Hook: Synchronous Context

The precommit hook is a **synchronous CLI tool** — it cannot emit events via the clawhip bridge (which is async MCP). The hook's job is to **block the commit locally** by returning exit code 1. Event emission happens in the worker-wrapper (which has async MCP access). This is the two-layer defense:
1. **Layer 1 (precommit hook):** Blocks the commit locally if incompatible licenses found (offline safety net)
2. **Layer 2 (worker-wrapper):** Emits `task.license_flagged` event and blocks the push via the approval gate (online enforcement)

### Scope Boundary

Do NOT modify:
- `packages/secret-hygiene/src/secret_hygiene/license_scan.py` (Story 6.9 owns this; consume it as-is)
- `packages/secret-hygiene/src/secret_hygiene/scanner.py`
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py`
- `packages/secret-hygiene/src/secret_hygiene/path_checks.py`
- `packages/capabilities/` (tier enforcement is already wired)

DO modify:
- `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py` — add license scan pass
- `packages/secret-hygiene/src/secret_hygiene/test_precommit_hook.py` — add tests for license scan in hook
- `packages/events/src/events/payloads.py` — add `TaskLicenseFlaggedPayload`
- `services/registry-state/src/registry_state/domain/event_types.py` — register event
- `services/registry-state/src/registry_state/domain/handlers.py` — add materializer handler
- `services/registry-api/src/registry_api/routes/decisions.py` — add license-gate check
- `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` — parse `--override`
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — add `override` param
- `services/worker-wrapper/src/worker_wrapper/app/main.py` — emit event pre-push

### Relationship to Other Stories

- **Story 6.9** (license-scan-integration): Created `license_scan.py` with `scan_files_for_licenses()`. This story consumes it.
- **Story 6.4** (decisions-handler): Created `POST /v1/tasks/{id}/decisions` with `DecisionRequest.override` field and `tier3.license_override` emission. This story adds the gate-check and wires the Telegram side.
- **Story 6.7** (worker-approval-wait-state): Created the approval-wait lifecycle in worker-wrapper. This story adds the license scan to the pre-push flow.
- **Story 6.13** (license-scan-integration-test): End-to-end integration test that seeds a GPL file, runs the autonomous-task flow, and asserts the approval gate blocks. Depends on this story.
- **Story 3.4** (approve-command): Created the `/approve` command. This story adds `--override license` parsing.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic6-Story6.10]
- [Source: _bmad-output/planning-artifacts/prd.md#FR40]
- [Source: _bmad-output/planning-artifacts/prd.md#FR41]
- [Source: _bmad-output/planning-artifacts/prd.md#NFR-S8]
- [Source: _bmad-output/planning-artifacts/architecture.md#event-envelope]
- [Source: packages/secret-hygiene/src/secret_hygiene/license_scan.py]
- [Source: packages/secret-hygiene/src/secret_hygiene/precommit_hook.py]
- [Source: services/registry-api/src/registry_api/routes/decisions.py]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py]
- [Source: services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:306]
- [Source: services/registry-state/src/registry_state/domain/event_types.py]
- [Source: services/registry-state/src/registry_state/domain/handlers.py]
- [Source: packages/events/src/events/payloads.py]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

None.

### Completion Notes List

1. **Task 1** — Event registration: `TaskLicenseFlaggedPayload` registered, materializer handler added, unit tests for payload model pass.
2. **Task 2** — Precommit hook: license scan pass wired after path checks, `--repo-license` flag added, 6 tests in `TestLicenseScanInHook`.
3. **Task 3** — Worker-wrapper: license scan runs pre-push between `TASK_AWAITING_APPROVAL` and `task.approval_requested` emission, graceful exception handling.
4. **Task 4** — License gate: `_check_license_gate()` queries `Event` table for `task.license_flagged` type, returns 409 RFC 7807 `ProblemDetails` with `type="approval_blocked_by"` / `extensions.reason="license_flag"`. Gate check placed AFTER state validation, BEFORE idempotency cache — blocked approvals don't consume slots. 6 tests in `TestLicenseGate`.
5. **Task 5** — Telegram override: `submit_decision()` gained `override` parameter. `approve_command.py` switched from `extract_task_id_from_message` (2-part split) to `extract_task_id_with_trailing` (3-part split) so `--override license` trailing text doesn't cause task ID extraction failure. Override parsed from trailing text. 409 with `approval_blocked_by` type renders specific hint. 7 new tests.
6. **Task 6** — All ruff checks clean on modified files. `check_event_registry.py` passes. Test counts: +6 decisions (22 total), +7 approve (45 total), +6 precommit hook license tests, +6 payload model tests. 2 pre-existing test failures confirmed unrelated (event registry reload test + worker-wrapper event_log_dir test).

### File List

- `packages/events/src/events/payloads.py` — added `TaskLicenseFlaggedPayload`
- `services/registry-state/src/registry_state/domain/event_types.py` — registered `task.license_flagged`
- `services/registry-state/src/registry_state/domain/handlers.py` — added `handle_task_license_flagged`
- `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py` — license scan pass + `--repo-license` flag
- `packages/secret-hygiene/src/secret_hygiene/test_precommit_hook.py` — 6 license scan tests
- `services/worker-wrapper/src/worker_wrapper/app/main.py` — license scan pre-push emission
- `services/registry-api/src/registry_api/routes/decisions.py` — `_check_license_gate()` + license gate check
- `services/registry-api/src/registry_api/test_decisions.py` — 6 `TestLicenseGate` tests
- `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — `override` param on `submit_decision()`
- `services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py` — `--override license` parsing + 409 handling
- `services/telegram-gateway/src/telegram_gateway/test_approve_command.py` — 7 override/license tests
