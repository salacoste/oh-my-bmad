# Story 12.2 — emit `task.budget_enforcement_triggered` audit event after budget enforcement (FR67)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** the worker-wrapper to emit a `task.budget_enforcement_triggered`
audit event immediately after it SIGTERMs the Claude Code subprocess for a
budget overage,
**so that** every enforcement action leaves a durable, queryable record
(budget threshold, actual spend at trigger, action taken, post-trigger task
transition) that the metrics-subscriber can count and operators can audit.

## Background

Epic 12's enforcement leg (**Story 12.1**, already shipped) added the
`budget_supervisor` that tails the event log for `task.budget_exceeded`
and, on the first match for the active task, invokes a
`terminate_callback` (SIGTERM → wait ≤5s → SIGKILL) on the Claude Code
subprocess. Story 12.1 deliberately left the AUDIT event to this story —
its docstring states verbatim (`budget_supervisor.py:18-19`):

> Subprocess SIGTERM/SIGKILL is process control, not state mutation; the
> audit event ``task.budget_enforcement_triggered`` is emitted by Story
> 12.2 AFTER [termination].

The `BudgetSupervisorResult` already carries the trigger data this story
needs to populate the event (`budget_supervisor.py:47-49`: `triggered`,
`event_id`, `tokens_used`, `token_limit`, `step`, `enforcement_failed`).

`task.budget_exceeded` (Story 5.15, FR44) is the SIGNAL that the ceiling
was crossed; `task.budget_enforcement_triggered` (this story, FR67) is the
ACTION RECORD that the platform terminated the subprocess in response.
They are distinct events.

## Acceptance Criteria

1. **AC1 — New `TaskBudgetEnforcementTriggeredPayload`.** Add a Pydantic
   payload class in `packages/events/src/events/payloads.py` (mirror
   `TaskBudgetExceededPayload` at line 763 — `frozen=True, strict=True,
   extra="forbid"`). Per FR67 the event contains:
   - `task_id: str` (min_length=1, max_length=64, pattern=`_TASK_ID_PATTERN`)
   - `budget_threshold: int` (gt=0) — the token (or dollar) ceiling
   - `actual_spend: int` (gt=0) — cumulative spend at trigger
   - `action_taken: str` — Literal/constrained to `"subprocess_terminated"`
     (the only action in this story; FR67 names it explicitly)
   - `post_trigger_transition: str` — Literal `"failed"` | `"awaiting_approval"`
     (per the per-task policy declared at submission; until Story 12.4
     stores per-task policy, default to the operator-configured default —
     see AC4)
   - `step: int` (ge=1) — step counter from the matching payload (carried
     through `BudgetSupervisorResult.step`)
   Export it from `packages/events/src/events/__init__.py` (mirror the
   `TaskBudgetExceededPayload` export) and from `payloads.py`'s `__all__`
   (line ~1143).

2. **AC2 — Register the event type at `schema_version=1.1.0`** (per the
   epics.md scope note: "Register at schema_version=1.1.0"). In
   `services/registry-state/src/registry_state/domain/event_types.py`
   (mirror the `task.budget_exceeded` registration at lines 237-238):
   `register("task.budget_enforcement_triggered", "1.1.0", TaskBudgetEnforcementTriggeredPayload)`.
   Add the import to that file's import block (line ~48) + `__all__`
   (line ~116). Register ONLY 1.1.0 (it's a brand-new type; no 1.0.0
   legacy to carry).

3. **AC3 — Emit the event after termination.** In
   `services/worker-wrapper/src/worker_wrapper/app/main.py`, after the
   `budget_result` is awaited (the post-termination block around line
   534-600), when `budget_result.triggered is True`, emit
   `task.budget_enforcement_triggered` via the existing clawhip-bridge
   `emit_event` path (`_call_tool_best_effort(clients.clawhip_bridge,
   "emit_event", ...)` — the same pattern session.started uses at line
   208-235). Populate the payload from `budget_result`
   (`token_limit`→`budget_threshold`, `tokens_used`→`actual_spend`,
   `step`, `task_id`), `action_taken="subprocess_terminated"`,
   `post_trigger_transition` per AC4. Emit MUST be best-effort (it already
   is via `_call_tool_best_effort`, which swallows exceptions) so an
   emit failure does not block the FSM transition the lifespan performs.

4. **AC4 — `post_trigger_transition` source (interim, pre-Story-12.4).**
   Story 12.4 will store per-task budget policy on the task row. Until
   then, source `post_trigger_transition` from an operator-configurable
   default in the worker-wrapper config (`app/config.py`, pydantic-settings):
   add `OMB_DEFAULT_BUDGET_ACTION` (default `"awaiting_approval"`; the
   safe operator-in-the-loop default — `"failed"` is the fire-and-forget
   alternative). Constrain to the 2 literals. Document that Story 12.4
   will override this per-task. (Do NOT implement per-task storage here —
   that's 12.4's scope.)

5. **AC5 — metrics-subscriber counts it.** The Epic 12 acceptance gate
   requires `task_budget_enforcement_triggered_total`. Add the counter to
   metrics-subscriber so each emitted event increments it. Mirror the
   existing per-event-type counter pattern in
   `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py`.
   (If the subscriber auto-counts by event `type` already, just confirm +
   add the cardinality-allowlist entry; do not duplicate.)

6. **AC6 — Unit + integration tests.**
   - Unit: `TaskBudgetEnforcementTriggeredPayload` validates the FR67
     fields + rejects bad input (extra fields forbidden, action_taken /
     post_trigger_transition constrained, spend/threshold gt=0).
   - Unit: event_types registration round-trips
     (`task.budget_enforcement_triggered` @ 1.1.0 resolves the payload).
   - Worker-wrapper: after a simulated budget-exceeded → terminate, the
     emit path is invoked with the correct payload (mock the
     clawhip-bridge `emit_event` call; assert args). Mirror the existing
     budget_supervisor / run_task emit tests.
   - Integration (extend `tests/integration/test_budget_enforcement_latency.py`
     or a new test): a budget-exceeded run produces a
     `task.budget_enforcement_triggered` envelope in the event log with
     the expected fields. `@slow` if it boots subprocesses.

7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # 242=baseline (0-new)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q packages/events services/worker-wrapper services/registry-state services/metrics-subscriber
   uv run pytest -x -q -m "not slow"
   ```
   NOTE: `scripts/check_event_registry.py` enforces that every emitted
   event type is registered — the new type MUST be in event_types.py or
   this gate fails (that's the point).

8. **AC8 — Code review** at default effort. New event type + emitter +
   counter is a small, well-bounded diff. Bump to `/bmad-code-review`
   3-lane only if a paranoid pass on the emit-ordering (does the emit race
   the FSM transition?) is wanted.

## Tasks / Subtasks

- [ ] **Task 1 — Payload class** (AC1): `TaskBudgetEnforcementTriggeredPayload`
      in payloads.py + exports.
- [ ] **Task 2 — Register event type** (AC2): event_types.py @ 1.1.0 + imports.
- [ ] **Task 3 — Default-action config** (AC4): `OMB_DEFAULT_BUDGET_ACTION`
      in worker-wrapper app/config.py.
- [ ] **Task 4 — Emit after termination** (AC3): wire the
      `_call_tool_best_effort(emit_event, ...)` in app/main.py's
      post-`budget_result` block.
- [ ] **Task 5 — metrics counter** (AC5): `task_budget_enforcement_triggered_total`.
- [ ] **Task 6 — Tests** (AC6): payload + registration + emit + integration.
- [ ] **Task 7 — Validation gates** (AC7).
- [ ] **Task 8 — Code review** (AC8); apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **Payload to mirror:** `packages/events/src/events/payloads.py:763`
  (`TaskBudgetExceededPayload`) — copy the model_config + field style;
  `_TASK_ID_PATTERN` is already defined in that file. `__all__` at ~1143.
- **Event-type registration to mirror:**
  `services/registry-state/src/registry_state/domain/event_types.py:237-238`
  (`task.budget_exceeded`); import block ~48, `__all__` ~116.
- **Supervisor result (trigger data source):**
  `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py:47-49`
  + `:84-138` (`BudgetSupervisorResult`: `triggered`, `event_id`,
  `tokens_used`, `token_limit`, `step`, `enforcement_failed`).
- **Emit site:** `services/worker-wrapper/src/worker_wrapper/app/main.py`
  — supervisor created at `:513-514`, `budget_result` awaited in the
  post-termination block (~`:534-600`). Emit via the existing
  `_call_tool_best_effort(clients.clawhip_bridge, "emit_event", ...)`
  pattern (the session.started emit at `:208-235` is the template).
- **Emit mechanism:** worker-wrapper emits events through the
  **clawhip-bridge MCP `emit_event` tool** (NOT a direct EventLogWriter) —
  `app/main.py:5,228-229`. Use the same helper; do NOT add a new writer.
- **metrics counter pattern:**
  `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py`
  (existing per-event-type counters + cardinality allowlist).
- **12.1 supervisor docstring** (`budget_supervisor.py:18-19`) explicitly
  defers this event to 12.2 — confirms scope.

### Constraints

- **NO `mcp_clients.py` touched** — this story emits THROUGH the existing
  clawhip-bridge client; it does not change MCP env/allowlist plumbing
  (the a0ca050 P0 area). [[diff-audit-delegated-security-work]]
- **Best-effort emit** — `_call_tool_best_effort` swallows exceptions so a
  failed audit emit never blocks the FSM transition or subprocess cleanup.
  The enforcement (SIGTERM) already happened; the audit event is a record,
  not a gate.
- **FR26 single-writer preserved** — the event is emitted via clawhip-bridge
  → registry-state materializes it; worker-wrapper does not write the DB.
- **Schema version 1.1.0** per the epics.md scope note (not 1.0.0).
- **`post_trigger_transition` is interim** — sourced from the default
  config until Story 12.4 adds per-task policy storage; the payload field
  + emit wiring are forward-compatible (12.4 just changes the SOURCE of
  the value, not the event shape).
- **NFR-R8 (5s p99)** is owned by Story 12.1's terminate path; this story's
  emit is AFTER termination and is best-effort, so it does not affect the
  enforcement-latency budget.

### Project Structure Notes

- Additive: 1 new payload class + 1 registration + 1 config field + 1
  emit call + 1 counter + tests. No file moves, no deletions, no schema
  migration (event types are code-registered, not DB-migrated).
- The `task.budget_enforcement_triggered` type joins the existing budget
  event family (`task.budget_exceeded` FR44, `tier3.budget_override` FR44,
  and the upcoming `budget.override` FR68 in Story 12.3).

### References

- [Source: `prd.md:1028` — FR67 verbatim: event contains budget_threshold,
  actual_spend, action_taken (`subprocess_terminated`), post_trigger_transition
  (`failed`|`awaiting_approval` per per-task policy).]
- [Source: `epics.md:2475-2478` — Story 12.2 scope: register @ 1.1.0;
  emit after termination with the 4 fields; metrics-subscriber counts it.]
- [Source: `budget_supervisor.py:18-19` — 12.1 defers this audit event to 12.2.]
- [Source: `payloads.py:763` (TaskBudgetExceededPayload) — payload template.]
- [Source: `event_types.py:237-238` — registration template.]

## Previous-story intelligence

- **Story 12.1** built the enforcement leg (supervisor + SIGTERM) and
  intentionally carried the trigger data in `BudgetSupervisorResult` for
  this story to consume — the integration is pre-designed, low-risk.
- **Story 5.15** (`task.budget_exceeded`, FR44) is the SIGNAL event; this
  story's `task.budget_enforcement_triggered` (FR67) is the distinct
  ACTION-RECORD event. Don't conflate them.
- **Story 12.3** (next, FR68) adds `budget.override`; **Story 12.4** (FR68a)
  stores per-task policy that will replace this story's interim
  `OMB_DEFAULT_BUDGET_ACTION` source for `post_trigger_transition`.
- **Stories 11.3.8–11.3.12** just closed the Epic-11.3 fresh-deploy-green
  tail (ROOT compose 7/7) — the worker-wrapper this story touches now
  boots cleanly in the ROOT compose, so the AC6 integration test has a
  green stack to run against.

## Git intelligence summary

Last commits on this lineage:

- `8d189bf` (epic-11.3.12) — sprint-status hygiene reconcile
- `edb8af8` (epic-11.3.12) — Story 11.3.12 DONE (Epic-11.3 tail complete)
- `13afa83` (epic-11.3.12) — 11.3.12 AC9 review fixes

Story 12.2 branches off `epic-11.3.12` (the latest tip) so the chain stays
linear. Branch `epic-12.2`. First story of the epic-12 budget-event
backlog (12.2 enforcement-event → 12.3 override-event → 12.4 policy-storage).

## Frontmatter

```yaml
---
story_id: 12.2
story_key: 12-2-budget-enforcement-triggered-event
parent_epic: 12
phase: 2
fr_refs: [FR67]
nfr_refs: [NFR-R8]
arch_refs:
  - "Story 12.1 budget_supervisor.py:18-19 — defers the task.budget_enforcement_triggered audit event to this story; BudgetSupervisorResult carries the trigger data"
  - "FR67 (prd.md:1028) — event fields: budget_threshold, actual_spend, action_taken=subprocess_terminated, post_trigger_transition=failed|awaiting_approval"
  - "TaskBudgetExceededPayload (payloads.py:763) — payload template; event_types.py:237 — registration template"
  - "worker-wrapper emits via clawhip-bridge emit_event MCP tool (app/main.py:228); NO mcp_clients.py change"
  - "epics.md:2475 — Story 12.2 scope (register @ 1.1.0, emit after termination, metrics counts it)"
estimated_complexity: SMALL-MEDIUM
priority: MEDIUM (Epic 12 audit completeness; the enforcement ACTION currently leaves no durable record — 12.1 terminates but emits nothing)
blocks: []
unblocks:
  - Every budget enforcement leaves a queryable audit record (FR67)
  - metrics-subscriber task_budget_enforcement_triggered_total counter (Epic 12 acceptance gate)
  - Story 12.4 per-task policy can later override the interim post_trigger_transition source without changing the event shape
---
```

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Definition of Done

- `TaskBudgetEnforcementTriggeredPayload` (FR67 fields) added + exported;
  registered as `task.budget_enforcement_triggered` @ schema 1.1.0.
- worker-wrapper emits the event via clawhip-bridge `emit_event` after
  every subprocess termination for budget overage, populated from
  `BudgetSupervisorResult` + the default-action config.
- `post_trigger_transition` sourced from `OMB_DEFAULT_BUDGET_ACTION`
  (interim until Story 12.4); payload + wiring forward-compatible.
- metrics-subscriber exposes `task_budget_enforcement_triggered_total`.
- Unit tests (payload validation, registration round-trip, emit-args) +
  integration test (event present in log after enforcement) pass.
- Validation gates green: ruff/format clean, mypy 242=baseline 0-new,
  discipline 0 (incl. check_event_registry), regression no new fails.
- Code-review at default effort discharged; findings applied.
- `sprint-status.yaml` flips `12-2-budget-enforcement-triggered-event`:
  backlog → ready-for-dev → in-progress → review → done.
- No `mcp_clients.py` touched; no new writer (emit via clawhip-bridge);
  FR26 single-writer preserved; best-effort emit does not gate the FSM.
