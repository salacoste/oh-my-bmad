# Story 6.7: Worker approval-wait state (FR36)

Status: ready-for-dev

## Story

As the worker,
I want to emit `task.awaiting_approval` when a Tier-3 action is reached, hold my worktree lock, sleep on a conditional wait, and resume on `approval.granted` or terminate on `approval.rejected`,
So that FR36 is coded up in the lifecycle state machine (couples with Story 5.17 HIGH-RISK).

## Acceptance Criteria

1. **AC-1: Git push detection** — The `ClaudeCodeRunner._classify_tool_use` method classifies `git push` commands as a new `ExtractedEvent` with `event_type="git.push"` (alongside existing `file.edited`, `test.run`, `commit.created`).

2. **AC-2: Approval-request emission** — When the execution driver detects a `git.push` event from the `ClaudeCodeResult`, it uses the `LifecycleManager` to transition the FSM to `AWAITING_APPROVAL` and emits `task.approval_requested` via clawhip-bridge's `emit_approval_request` MCP tool, including `action`, `justification`, and `diff_summary` context.

3. **AC-3: Approval wait** — The worker holds the worktree lock, polls the JSONL event log for an `approval.granted` event matching its `task_id` (using the existing `_make_approval_lookup` pattern from clawhip-bridge), and sleeps on an asyncio conditional wait between polls. On `approval.granted`: the worker executes the gated action (git push + PR draft via `LifecycleManager.resume_gated_action()`), emits `tier3.action_performed`, and transitions to `COMPLETED`. On `approval.rejected`: the worker transitions to `FAILED` and emits `tier3.action_performed` with `accepted=False`.

4. **AC-4: LifecycleManager wiring** — The `LifecycleManager` is wired into `app/main.py`'s session lifecycle. On task start: create `LifecycleManager` with FSM, sidecar path, task_id, emit_event callback (wrapping clawhip-bridge), gated_action callback (wrapping git push + PR creation), and idempotency cache. On restart: `LifecycleManager.restore_from()` sidecar; fast-forward if approval arrived during downtime (5.17b logic).

5. **AC-5: Exactly-once push** — The gated action (git push + PR draft) executes exactly once, enforced by the `IdempotencyCacheStore` and `LifecycleManager.resume_gated_action()`. The `GitHubClient.create_pr_draft()` receives an idempotency key for GitHub dedup.

6. **AC-6: `tier3.action_performed` emission** — After the gated action succeeds, the worker emits `tier3.action_performed` via clawhip-bridge's `emit_event` tool with `Tier3ActionPerformedPayload` fields: `task_id`, `action="git_push"`, `accepted=True`, `approval_event_id` (the event_id of the `approval.granted` event), and optional `reason`.

7. **AC-7: Test coverage** — Unit tests for git push classification. Integration tests for: (a) happy-path approval → push → `tier3.action_performed` emission, (b) rejection → FAILED transition, (c) approval arrives before worker restart (reattach), (d) duplicate approval idempotency. All existing tests pass.

8. **AC-8: No regression** — `ruff check` clean. All existing tests pass.

9. **AC-9: Atomic commit** — Single commit with title `feat(worker-wrapper): wire approval-wait state into execution lifecycle (Story 6.7)`.

## Tasks

- [ ] Task 1 — Add `git push` classification in `ClaudeCodeRunner._classify_tool_use` (AC-1)
  - [ ] Add `_GIT_PUSH_PATTERN` regex: `r"^\s*git\s+push\b"`
  - [ ] Add branch in `_classify_tool_use` returning `ExtractedEvent(event_type="git.push", tool_name="Bash", tool_input=tool_input)`
  - [ ] Add unit test for push classification (positive + negative cases)
- [ ] Task 2 — Create approval-gated execution driver (AC-2, AC-3)
  - [ ] Add `services/worker-wrapper/src/worker_wrapper/domain/approval_gate.py` — pure domain function `needs_approval(events: list[ExtractedEvent]) -> bool` that scans for `git.push` events
  - [ ] Add `services/worker-wrapper/src/worker_wrapper/adapters/approval_waiter.py` — `ApprovalWaiter` class that polls JSONL for `approval.granted`/`approval.rejected` events, with configurable poll interval and timeout
  - [ ] Wire into execution flow: after `ClaudeCodeRunner.run()` returns, check if result contains `git.push` event → if yes, enter approval gate
- [ ] Task 3 — Wire `LifecycleManager` into `app/main.py` session lifecycle (AC-4)
  - [ ] On task start: instantiate `LifecycleManager` with FSM, sidecar path, emit_event callback, gated_action callback
  - [ ] On restart: call `LifecycleManager.restore_from()` with sidecar path
  - [ ] Connect `emit_event` callback to `mcp_clients["clawhip_bridge"].call_tool("emit_event", ...)`
  - [ ] Connect `gated_action` callback to git push + PR creation logic
- [ ] Task 4 — Emit `tier3.action_performed` after gated action (AC-6)
  - [ ] After `resume_gated_action()` succeeds, emit `tier3.action_performed` with `Tier3ActionPerformedPayload` fields
  - [ ] Include `approval_event_id` from the approval lookup result
  - [ ] On rejection, emit with `accepted=False` and `reason`
- [ ] Task 5 — Add `emit_approval_request` wiring (AC-2)
  - [ ] Before entering wait loop, emit `task.approval_requested` via clawhip-bridge with `action`, `justification`, and `diff_summary` from the push context
- [ ] Task 6 — Write tests (AC-7)
  - [ ] Unit: `test_classify_git_push` in `test_claude_code_runner.py`
  - [ ] Unit: `test_needs_approval` in `test_approval_gate.py`
  - [ ] Unit: `test_approval_waiter` with mock JSONL
  - [ ] Integration: approval → push → `tier3.action_performed` emission
  - [ ] Integration: rejection → FAILED transition
- [ ] Task 7 — Verification + commit (AC-8, AC-9)

## Dev Notes

### Key Insight: This Story Wires Existing Infrastructure

The FSM (`lifecycle.py`) and adapter (`lifecycle_manager.py`) are **already built** in Stories 5.17a/5.17b. This story's job is to **connect them to the actual execution path** — detecting when a Tier-3 action is needed, entering the approval wait, and resuming after approval.

The existing `LifecycleManager` already handles:
- FSM transitions (5.17a)
- State persistence to sidecar JSON (5.17b, FR29)
- Idempotent approval via `IdempotencyCacheStore` (5.17b, FR28)
- `resume_gated_action()` for exactly-once execution (5.17b)
- `restore_from()` for restart recovery (5.17b)

What's NEW in this story:
- Git push **detection** in `ClaudeCodeRunner._classify_tool_use`
- Approval **waiting** (polling JSONL for `approval.granted`/`approval.rejected`)
- **Wiring** the LifecycleManager into `app/main.py`
- `tier3.action_performed` **emission** after the gated action succeeds

### Git Push Classification Pattern

The `ClaudeCodeRunner._classify_tool_use` at `claude_code_runner.py:192-218` already classifies:
- `Write` / `Edit` → `file.edited`
- `Bash` with test pattern → `test.run`
- `Bash` with `git commit` → `commit.created`

Add `Bash` with `git push` → `git.push` following the same pattern:

```python
_GIT_PUSH_PATTERN: re.Pattern[str] = re.compile(r"^\s*git\s+push\b")

# In _classify_tool_use, after _COMMIT_PATTERN check:
if tool_name == "Bash":
    command = tool_input.get("command", "")
    if isinstance(command, str) and _GIT_PUSH_PATTERN.match(command):
        return ExtractedEvent(
            event_type="git.push",
            tool_name=tool_name,
            tool_input=tool_input,
        )
```

### Approval Wait Strategy: Poll JSONL

The clawhip-bridge already has `_make_approval_lookup(base_dir, clock)` at `server.py` which scans today's JSONL for `approval.granted` events matching a `task_id`. This pattern should be reused in the worker wrapper.

The `ApprovalWaiter` should:
1. Poll the JSONL event log for `approval.granted` or `approval.rejected` events
2. Use `asyncio.sleep(poll_interval)` between polls (not busy-wait)
3. Accept a configurable timeout (default from `WorkerSettings`)
4. Return the approval event (or raise on timeout/rejection)

Do NOT implement a subscription/MQTT pattern — JSONL polling is the Phase-1 strategy per architecture.md line 231: "Workers communicate via event emission only through clawhip-bridge."

### `tier3.action_performed` Emission

After the gated action (git push + PR draft) succeeds, emit `tier3.action_performed` using the `Tier3ActionPerformedPayload` model (added in Story 6.6):

```python
payload = Tier3ActionPerformedPayload(
    task_id=task_id,
    action="git_push",
    accepted=True,
    approval_event_id=approval_event_id,  # from the approval.granted event
    reason=None,
)
await emit_event("tier3.action_performed", payload.model_dump())
```

The `Tier3ActionPerformedPayload` model is in `packages/events/src/events/payloads.py` (line 714) and re-exported from `event_types.py`. It's registered for schema `"1.0.0"`.

On rejection:
```python
payload = Tier3ActionPerformedPayload(
    task_id=task_id,
    action="git_push",
    accepted=False,
    approval_event_id=None,
    reason="operator rejected via approval.rejected",
)
```

Note: Both Tier3 payload models now have a cross-field validator requiring `reason` when `accepted=False` (added in Story 6.6 review).

### LifecycleManager Wiring Pattern

The `app/main.py` already manages session lifecycle (`start_session`, `heartbeat_loop`, `finish_session`). Add LifecycleManager as part of the task execution flow:

```python
# On task start (after worktree lock acquired)
lifecycle_mgr = LifecycleManager(
    fsm=LifecycleFSM(),
    state_path=worktree_path / ".lifecycle-state.json",
    task_id=task_id,
    emit_event=lambda type, payload: mcp_clients["clawhip_bridge"].call_tool("emit_event", {"type": type, "payload": payload}),
    gated_action=lambda: execute_git_push_and_pr(worktree_path, task_id, github_client),
    idempotency_cache=idempotency_cache,
)

# On restart (before running Claude Code)
restored = LifecycleManager.restore_from(
    state_path=worktree_path / ".lifecycle-state.json",
    task_id=task_id,
    emit_event=emit_event_callback,
    gated_action=gated_action_callback,
    idempotency_cache=idempotency_cache,
)
if restored and restored.current_state == WorkerState.AWAITING_APPROVAL:
    # Approval may have arrived during downtime — check
    approval = await approval_waiter.wait_for_approval(task_id)
    if approval:
        await restored.handle_approval(idempotency_key=approval["idempotency_key"])
```

### Execution Flow (End-to-End)

```
1. Worker starts task → acquires worktree lock → creates LifecycleManager(RUNNING)
2. Worker runs Claude Code subprocess via ClaudeCodeRunner
3. Claude Code output streams in → events extracted (file.edited, test.run, commit.created)
4. If git.push event detected in result.events:
   a. LifecycleManager.handle_event(TASK_AWAITING_APPROVAL) → FSM → AWAITING_APPROVAL
   b. Emit task.approval_requested via clawhip-bridge with action/justification/diff_summary
   c. ApprovalWaiter polls JSONL for approval.granted/rejected matching task_id
   d. On approval.granted:
      - LifecycleManager.handle_approval(idempotency_key) → FSM → RESUMED
      - resume_gated_action() → git push + PR draft → FSM → COMPLETED
      - Emit tier3.action_performed with accepted=True
   e. On approval.rejected:
      - LifecycleManager.handle_event(APPROVAL_REJECTED) → FSM → FAILED
      - Emit tier3.action_performed with accepted=False + reason
5. If no git.push: normal completion → LifecycleManager.handle_event(TASK_COMPLETED)
```

### structlog Gotcha

Never use `event=` as a kwarg with structlog loggers — clashes with positional `event` param. Use descriptive kwarg names like `fsm_event=`, `approval_action=`, etc.

### Files to Touch

| File | Change |
|------|--------|
| `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` | Add `_GIT_PUSH_PATTERN` + classify `git.push` |
| `services/worker-wrapper/src/worker_wrapper/domain/approval_gate.py` | NEW — `needs_approval()` pure domain function |
| `services/worker-wrapper/src/worker_wrapper/adapters/approval_waiter.py` | NEW — `ApprovalWaiter` class for JSONL polling |
| `services/worker-wrapper/src/worker_wrapper/app/main.py` | Wire LifecycleManager + approval gate into execution flow |
| `services/worker-wrapper/src/worker_wrapper/app/config.py` | Add `approval_poll_interval_s`, `approval_timeout_s` to `WorkerSettings` |
| `services/worker-wrapper/src/worker_wrapper/test_approval_gate.py` | NEW — unit tests |
| `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` | Add git push classification tests |

### Relationship to Previous Stories

- **Story 5.17a** — Built the pure FSM (`lifecycle.py`) with 6 states, 7 events, 15 transitions. This story uses it unchanged.
- **Story 5.17b** — Built the `LifecycleManager` adapter with persistence, idempotent approval, and `resume_gated_action()`. This story wires it into the execution path.
- **Story 5.17c** — S-2 midflight worker swap test. The sidecar persistence and event-level dedup patterns from this story apply.
- **Story 6.2** — Defined `Tier3ActionAttemptedPayload` and the capability-tier enforcement helpers. The git push is a Tier-3 action.
- **Story 6.5** — Added materializer handlers for `approval.granted`, `approval.rejected`. These handlers update `last_event_id` in the tasks table.
- **Story 6.6** — Added `Tier3ActionPerformedPayload` model + materializer handler. This story emits the event that 6.6's handler consumes.
- **Story 6.4** — Added `POST /v1/tasks/{id}/decisions` handler that emits `approval.granted`/`approval.rejected` events. This is what the worker waits for.

### Scope Boundary

- Do NOT modify `domain/lifecycle.py` — the FSM is complete and tested (61 tests)
- Do NOT modify `adapters/lifecycle_manager.py` — the adapter is complete and tested (8 integration tests)
- Do NOT add new event types — `task.approval_requested`, `approval.granted`, `approval.rejected`, `tier3.action_performed` are all already registered
- Do NOT modify clawhip-bridge — `emit_event` and `emit_approval_request` tools already exist
- Do NOT modify registry-state — materializer handlers for approval and tier3 events already exist (Stories 6.5, 6.6)
- DO add git push detection in `ClaudeCodeRunner`
- DO add approval-wait polling logic
- DO wire `LifecycleManager` into `app/main.py`
- DO emit `tier3.action_performed` after gated action

### References

- [Source: epics.md — Story 6.7 lines 1880-1896]
- [Source: architecture.md — line 833 FSM states + HIGH-RISK designation]
- [Source: architecture.md — lines 847-903 Journey 1 approval flow]
- [Source: lifecycle.py — FSM with 6 states, 7 events, 15 transitions (Story 5.17a)]
- [Source: lifecycle_manager.py — LifecycleManager adapter with persistence, idempotency, gated action (Story 5.17b)]
- [Source: claude_code_runner.py — _classify_tool_use at line 192]
- [Source: payloads.py — Tier3ActionPerformedPayload at line 714]
- [Source: payloads.py — TaskApprovalRequestedPayload at line 269]
- [Source: clawhip-bridge server.py — emit_approval_request tool, _make_approval_lookup]
- [Source: test_resume_after_approval.py — integration test pattern (Story 5.17b)]

## Dev Agent Record

### Agent Model Used

(TBD)

### Debug Log References

None.

### Completion Notes List

(TBD)

### File List

(TBD)
