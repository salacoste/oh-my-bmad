# Story 12.3a — Budget-override grace-window interception (supervisor↔override coupling, FR68 enforcement-prevention)

Status: backlog

<!-- Split out of Story 12.3 (D2=(II) decision, 2026-06-01). Story 12.3 shipped
the achievable FR68 delta (console-cli --override parity + budget.override
event-naming + the documented /retry sharp edge). THIS story owns the hard,
separable piece: making the autonomous budget_supervisor honor an override
within the 5-second grace window so enforcement is PREVENTED rather than
recovered-from. It is a real cross-service coupling change to the autonomous
supervisor and deserves its own review. -->

## Story

**As** the platform operator,
**I want** `/approve --override budget <task-id>` issued DURING the budget grace
window to abort the pending subprocess termination and let the task continue
under the extended budget,
**so that** I can rescue an over-budget task *without losing its in-flight work*
to a `/retry` from scratch — the FR68 headline behavior.

## Why this is separate from 12.3

Story 12.3's gap analysis (architect pass, 2026-06-01) established that the
existing `/approve --override budget` operates at the **registry level**
(`blocked → executing`) and is **disjoint** from Epic-12's `budget_supervisor`,
which SIGTERMs the live subprocess autonomously by tailing the JSONL log, with
**no inbound channel** from registry-api. Closing FR68's "prevent within 5s" AC
requires NET-NEW coupling that does not exist today. Per the D2=(II) decision,
that coupling was deferred here.

## Acceptance Criteria (DRAFT — re-validate against architecture before dev)

1. **AC1 — Grace window before enforcement.** On `task.budget_exceeded`, the
   `budget_supervisor` does NOT SIGTERM immediately; it opens a bounded grace
   window (config, default ~5s per FR68) during which it watches for an inbound
   override signal for that task. (Today: `budget_supervisor.py:153-342`
   terminates with no delay/override awareness.)

2. **AC2 — Inbound override channel.** A mechanism by which an operator override
   (`tier3.budget_override` / `budget.override` for the task) reaches the
   worker-wrapper supervisor within the grace window. Design options to weigh:
   (a) supervisor tails the registry event stream / a per-task signal file;
   (b) registry-api → worker-wrapper notification. MUST preserve FR26
   single-writer and NOT introduce a new secret/credential path; MUST NOT touch
   `mcp_clients.py` (a0ca050 P0 area).

3. **AC3 — Abort-on-override.** If the override arrives within the window, the
   supervisor ABORTS termination, the task continues under the new limit, and an
   audit event records the prevented enforcement. If the window expires first,
   the existing SIGTERM path runs unchanged (→ `task.budget_enforcement_triggered`
   per Story 12.2, → `TASK_FAILED`).

4. **AC4 — `awaiting_approval` FSM path.** Add the `awaiting_approval` lifecycle
   branch in `run_task` (`worker-wrapper/.../app/main.py` ~758 currently
   hard-codes `TASK_FAILED`) and **remove the Story 12.2
   `_reject_unwired_budget_action` validator** (`config.py:109,120-137`) that
   deliberately rejects `OMB_DEFAULT_BUDGET_ACTION=awaiting_approval` until this
   path exists. The Story 12.2 audit-integrity guard (the config must not claim
   an action the FSM cannot perform) is satisfied by IMPLEMENTING the action.

5. **AC5 — `post_trigger_transition` honored.** Story 12.2's
   `TaskBudgetEnforcementTriggeredPayload.post_trigger_transition` (`failed` |
   `awaiting_approval`) becomes genuinely bivalued — emitted as
   `awaiting_approval` only when this path actually parks the task for approval.

6. **AC6 — Tests.** Grace-window race tests (override-wins, window-expires,
   override-after-expiry → still `/retry`); FSM `awaiting_approval` transition;
   removal of the 12.2 validator gate covered by a config round-trip test.

7. **AC7 — Validation gates green; code review.** This is an authorization +
   autonomous-enforcement coupling change → **3-lane `/code-review` minimum**.
   Security lane MUST confirm: the abort cannot be triggered by a non-operator
   signal; no new credential/secret path; the supervisor still terminates when
   no valid override arrives (fail-closed).

## Source map (guardrails)

- `worker-wrapper/.../domain/budget_supervisor.py:153-342` — the autonomous
  SIGTERM loop (no grace/override today).
- `worker-wrapper/.../app/main.py:480-771` (hard-codes `TASK_FAILED` at ~758).
- `worker-wrapper/.../app/config.py:109,120-137` — the Story-12.2
  `_reject_unwired_budget_action` validator to remove HERE.
- registry-api override branch (already built; the inbound side):
  `registry-api/.../routes/decisions.py:251,401-448`.
- Event names: `tier3.budget_override` / `budget.override` @1.1.0
  (registered by Story 12.3).

## Constraints

- **NO `mcp_clients.py` touched** (a0ca050 P0 area).
- **Fail-closed:** absent a valid, operator-authenticated override within the
  window, enforcement MUST proceed (no override = task still terminated).
- **FR26 single-writer preserved.**
- The grace window must be bounded and configurable; an override channel that
  can hang MUST NOT be able to delay enforcement indefinitely.

## Frontmatter

```yaml
---
story_id: 12.3a
story_key: 12-3a-budget-override-grace-window-interception
parent_epic: 12
phase: 2
fr_refs: [FR68]
nfr_refs: []
arch_refs:
  - "Story 12.1 budget_supervisor — the autonomous SIGTERM enforcement this story makes override-aware"
  - "Story 12.2 TaskBudgetEnforcementTriggeredPayload.post_trigger_transition + _reject_unwired_budget_action — this story makes awaiting_approval real and removes the guard"
  - "Story 12.3 — shipped the achievable FR68 delta; this is the deferred grace-window interception (D2=(II) split)"
  - "architecture.md:1423 — budget.override @1.1.0"
estimated_complexity: LARGE (cross-service coupling to the autonomous supervisor + new FSM path)
priority: MEDIUM (FR68 enforcement-prevention; the operator-surface parity already shipped in 12.3)
blocks: []
unblocks:
  - operators can prevent budget enforcement within the 5s grace window (FR68 headline)
  - OMB_DEFAULT_BUDGET_ACTION=awaiting_approval becomes a valid, wired config
---
```

## References

- [Source: Story 12.3 architect gap analysis 2026-06-01 — the two-disjoint-budget-models root cause + D2 fork.]
- [Source: prd.md:1029 (FR68).]
- [Source: Story 12.2 config.py:120-137 — the validator this story removes once the FSM path lands.]
