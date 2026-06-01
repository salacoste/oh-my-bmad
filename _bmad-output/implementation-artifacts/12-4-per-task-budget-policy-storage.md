# Story 12.4 — Per-task budget policy storage + default policy in `.env` (FR68a)

Status: ready-for-dev

<!-- DECISION RESOLVED 2026-06-01 (operator): D1=(A) ship token-ceiling
consumption now + store budget_action but DEFER its worker-wrapper consumption
to Story 12.3a; D2 defer dollar-ceiling (YAGNI); D3 add OMB_DEFAULT_TASK_BUDGET_*
defaults + keep ORCHESTRATOR_TASK_TOKEN_BUDGET / OMB_DEFAULT_BUDGET_ACTION as
legacy fallbacks. The ACs as written already reflect this fork. -->

## ⚠️ Scoping decision required (read FIRST)

A read-only gap analysis + data-flow trace (2026-06-01) established the
architecture. Per-task budget has **two knobs living in two services**:

| Knob | Owner today | Default source today | Reaches it via |
|---|---|---|---|
| token-ceiling | orchestrator-adapter `BudgetTracker` | `settings.task_token_budget` (global, `ORCHESTRATOR_TASK_TOKEN_BUDGET`, default 50_000) | polls the **Task ORM row** (task-registry MCP resource) |
| action-on-exceed | worker-wrapper `run_task` | `settings.default_budget_action` (global, `OMB_DEFAULT_BUDGET_ACTION`, Story 12.2) | reads **global settings only** — does NOT read the Task row |

Two facts drive the scope:

1. **token-ceiling is achievable-now and clean.** orchestrator-adapter already
   reads the Task ORM row at dispatch. Store the ceiling on the row (materialized
   from `task.created`) + serialize it in `_task_to_dict()` → orchestrator-adapter
   reads `task.budget_token_limit ?? OMB_DEFAULT_TASK_BUDGET_TOKENS`. No new
   coupling.
2. **per-task action-on-exceed is premature + needs new coupling.** worker-wrapper
   has NO Task-row access (only `task_id` from env). Delivering a per-task
   `budget_action` to it requires a NET-NEW channel (env-injection at dispatch,
   or a registry query, or event subscription). AND its only non-default value
   (`awaiting_approval`) is **not wired** — Story 12.2's
   `_reject_unwired_budget_action` validator rejects it; **Story 12.3a** is what
   wires the `awaiting_approval` FSM path. Until 12.3a lands, every task's action
   is necessarily `failed`, so a per-task `budget_action` has no observable effect.

**THE DECISION (D1):** Does this story build the worker-wrapper per-task
`budget_action` DELIVERY now, or defer it?

- **(A) RECOMMENDED — store both fields, defer action *consumption* to 12.3a.**
  Store `budget_token_limit` + `budget_action` on the Task row + `TaskCreatedPayload`
  (so the data model is complete and forward-compatible), and wire the
  **token-ceiling consumption** end-to-end now (orchestrator-adapter reads
  per-task ceiling). The **action consumption** in worker-wrapper stays on the
  global default until Story 12.3a adds both the `awaiting_approval` FSM path AND
  the per-task delivery channel (the two are naturally co-designed). The AC
  inheritance test covers BOTH stored fields (default-vs-explicit on the row).
  This ships the genuinely-useful per-task token ceiling without building a
  delivery channel for a value that can't take effect yet.
- **(B) Full now — also build worker-wrapper delivery.** Add the per-task
  `budget_action` delivery channel to worker-wrapper now (likely env-injection at
  orchestrator-adapter dispatch). Larger, and partly inert until 12.3a wires
  `awaiting_approval`; risks building a channel that 12.3a's design supersedes.

**Sub-decisions (recommended defaults — override if you disagree):**
- **D2 — dollar-ceiling.** epics.md:2487 lists "dollar-ceiling" in scope, but
  there is **zero** dollar-based enforcement infra (BudgetTracker is token-only).
  RECOMMENDED: **defer dollar-ceiling** (YAGNI) — store token-ceiling + action
  only; note dollar-ceiling as a follow-up when cost-tracking exists.
- **D3 — env-var naming.** Add the epics-spec names `OMB_DEFAULT_TASK_BUDGET_TOKENS`
  + `OMB_DEFAULT_TASK_BUDGET_ACTION` as the operator-facing per-task-policy
  defaults. Keep `ORCHESTRATOR_TASK_TOKEN_BUDGET` (existing) and
  `OMB_DEFAULT_BUDGET_ACTION` (12.2) working as the lower-precedence/legacy
  fallbacks; document precedence: `per-task row value > OMB_DEFAULT_TASK_BUDGET_* >
  legacy`. Avoids breaking 12.2's wiring.

Until you pick D1, this story stays `needs-scoping-decision`.

## Story

**As** the platform operator,
**I want** each task to carry its own budget policy (token ceiling + action-on-
exceed), stored at submission and inheriting `.env` defaults when unspecified,
**so that** I can give expensive tasks a larger ceiling (and, once Story 12.3a
lands, a per-task action) without changing a global default for every task.

## Gap analysis (BUILT vs NET-NEW — 2026-06-01)

| Item | Status | Where |
|---|---|---|
| Global default budget action | **BUILT** | worker-wrapper `app/config.py:109-118` (`default_budget_action`, `OMB_DEFAULT_BUDGET_ACTION`) |
| post_trigger_transition wiring (global) | **BUILT** | worker-wrapper `app/main.py:744` (`=settings.default_budget_action`) |
| Budget supervisor (enforcement leg) | **BUILT** | worker-wrapper `domain/budget_supervisor.py` |
| `TaskBudgetExceededPayload` (token_limit) | **BUILT** | `packages/events/.../payloads.py:763-790` |
| `BudgetTracker` + global token budget | **BUILT** | orchestrator-adapter `domain/task_dispatch.py`; `app/config.py:73` (`task_token_budget`, default 50_000); `app/main.py:301-302` |
| orchestrator-adapter reads Task row at dispatch | **BUILT** (polls task-registry MCP resource) | orchestrator-adapter `app/main.py:447-459,72-95`; task-registry `handlers/resources.py:27-43,57-63` |
| **Per-task token_ceiling column on Task row** | **NET-NEW** | registry-state `schema.py:97-129` (no budget columns) |
| **Per-task budget_action column on Task row** | **NET-NEW** | registry-state `schema.py:97-129` |
| **Budget fields on `TaskCreatedPayload`** | **NET-NEW** (additive schema bump) | `packages/events/.../payloads.py:43-79` |
| **`CreateTaskRequest` + `TaskResponse` budget fields** | **NET-NEW** | registry-api `routes/tasks.py:114-149,191-226` |
| **registry-api emits budget in task.created** | **NET-NEW** | registry-api `routes/tasks.py:395-402` |
| **Materializer populates budget columns** | **NET-NEW** | registry-state `domain/materializer.py` (task.created handler) |
| **task-registry serializes budget in `_task_to_dict`** | **NET-NEW** | task-registry `handlers/resources.py:27-43` |
| **orchestrator-adapter reads per-task ceiling** | **NET-NEW** (the useful delta) | orchestrator-adapter `app/main.py:301` (currently global only) |
| **`OMB_DEFAULT_TASK_BUDGET_*` in `.env.example`** | **NET-NEW** | `.env.example` (no such entries) |
| **worker-wrapper per-task budget_action delivery** | **NET-NEW — D1 fork (defer to 12.3a recommended)** | worker-wrapper has no Task-row access (`app/main.py:744`, `__main__.py`) |
| **`tests/integration/test_budget_policy_inheritance.py`** | **NET-NEW** | does not exist |

## Acceptance Criteria (scoped for RECOMMENDED D1=(A) + D2=defer + D3; revise if you pick otherwise)

1. **AC1 — Task row budget columns.** Add nullable `budget_token_limit: int | None`
   and `budget_action: str | None` (Literal-validated `failed|awaiting_approval`
   at the model boundary, NOT a DB CHECK) to the registry-state Task ORM
   (`schema.py`). Nullable = "inherit default".

2. **AC2 — `TaskCreatedPayload` budget fields (additive 1.1.0→1.2.0).** Add
   optional `budget_token_limit: int | None = Field(default=None, gt=0)` +
   `budget_action: Literal["failed","awaiting_approval"] | None = None` to
   `TaskCreatedPayload`; register the new schema version. Back-compat: old events
   with neither field still validate (both default None).

3. **AC3 — API contract.** `CreateTaskRequest` accepts optional `budget_token_limit`
   + `budget_action`; the POST /v1/tasks handler threads them into the emitted
   `TaskCreatedPayload`. `TaskResponse` (GET) surfaces the effective stored values.

4. **AC4 — Materializer + task-registry serialization.** registry-state's
   task.created handler writes the two columns; task-registry `_task_to_dict()`
   serializes them so orchestrator-adapter sees them in `task://list`.

5. **AC5 — Per-task token-ceiling consumption (the useful delta).**
   orchestrator-adapter sources the BudgetTracker limit as
   `task.budget_token_limit ?? OMB_DEFAULT_TASK_BUDGET_TOKENS ??
   ORCHESTRATOR_TASK_TOKEN_BUDGET` (per-task > new default > legacy). Add
   `OMB_DEFAULT_TASK_BUDGET_TOKENS` to its pydantic-settings.

6. **AC6 — `.env.example` defaults (D3).** Add `OMB_DEFAULT_TASK_BUDGET_TOKENS`
   (default 50_000) + `OMB_DEFAULT_TASK_BUDGET_ACTION` (default `failed`) with a
   comment documenting precedence and the 12.3a-pending note for the action.

7. **AC7 — Inheritance integration test.** `tests/integration/test_budget_policy_inheritance.py`:
   (a) a task submitted WITHOUT budget fields → Task row reflects the `.env`
   defaults (or NULL-then-default-at-read, per chosen materialization); the
   dispatched BudgetTracker uses the default ceiling. (b) a task submitted WITH
   explicit `budget_token_limit` → that value overrides the default end-to-end.

8. **AC8 — budget_action stored but consumption documented as deferred.** The
   `budget_action` column/field is populated and surfaced, but worker-wrapper
   continues to read the global `OMB_DEFAULT_BUDGET_ACTION` (per-task delivery +
   `awaiting_approval` wiring → Story 12.3a). Document this in the story + the
   operator-runbook budget section. (D1=(B) only: also build the delivery.)

9. **AC9 — Validation gates green** (ruff/format, mypy --strict baseline,
   discipline incl. check_event_registry, regression no-new-fails).

10. **AC10 — Code review.** Touches the task-submission contract + a new event
    schema version → default `/code-review` minimum.

### Deferred (to Story 12.3a / a later cost story)

- worker-wrapper per-task `budget_action` DELIVERY channel + `awaiting_approval`
  consumption (co-designed with 12.3a's FSM path).
- dollar-ceiling storage + enforcement (needs cost-tracking infra first).

## Dev Notes — source map (file:line guardrails)

- registry-state Task ORM: `services/registry-state/src/registry_state/schema.py:97-129`.
- Materializer task.created handler: `services/registry-state/src/registry_state/domain/materializer.py`.
- `TaskCreatedPayload`: `packages/events/src/events/payloads.py:43-79`.
- Event registration: `services/registry-state/src/registry_state/domain/event_types.py` (task.created).
- registry-api: `services/registry-api/src/registry_api/routes/tasks.py` — `CreateTaskRequest` ~114-149, `TaskResponse` ~191-226, emit ~395-402.
- task-registry resource: `services/.../task-registry/handlers/resources.py:27-43,57-63`.
- orchestrator-adapter: `app/main.py:301-302,447-459`, `app/config.py:73`, `domain/task_dispatch.py` (`BudgetTracker`).
- worker-wrapper (deferred-consumption side): `app/main.py:744`, `app/config.py:109-118`.
- `.env.example` (append a budget-policy section).

### Constraints

- **NO `mcp_clients.py` touched.**
- **Additive-only event evolution (NFR-M3):** old `task.created` events MUST still
  validate; both new fields default None.
- **FR26 single-writer preserved:** budget travels task.created → materializer →
  Task row (the existing write path); orchestrator-adapter/task-registry are
  read-only consumers.
- **Precedence explicit + tested:** per-task row value > `OMB_DEFAULT_TASK_BUDGET_*`
  > legacy (`ORCHESTRATOR_TASK_TOKEN_BUDGET` / `OMB_DEFAULT_BUDGET_ACTION`).
- **No DB schema CHECK constraint for budget_action** — validate at the Pydantic
  model boundary (consistent with how the codebase constrains enums).

## Frontmatter

```yaml
---
story_id: 12.4
story_key: 12-4-per-task-budget-policy-storage
parent_epic: 12
phase: 2
fr_refs: [FR68a]
nfr_refs: [NFR-M3]
arch_refs:
  - "epics.md:2485-2488 — Story 12.4 scope + inheritance AC"
  - "Story 12.2 OMB_DEFAULT_BUDGET_ACTION — the global action default this story makes per-task-overridable (consumption deferred to 12.3a)"
  - "Story 12.3a — co-owns the worker-wrapper per-task budget_action delivery + awaiting_approval FSM"
  - "orchestrator-adapter BudgetTracker + task_token_budget — the global token ceiling this story makes per-task"
  - "gap analysis + data-flow trace 2026-06-01 — orchestrator-adapter reads the Task row (per-task ceiling clean); worker-wrapper does not (action delivery deferred)"
estimated_complexity: MEDIUM (D1=(A) — schema + event + API + orchestrator-adapter read) → LARGE (D1=(B) adds worker-wrapper delivery)
priority: MEDIUM (FR68a; per-task token ceiling is the useful delta, action-policy gated on 12.3a)
blocks: []
unblocks:
  - operators set per-task token ceilings without changing the global default
  - the data model is ready for Story 12.3a to consume per-task budget_action
---
```

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Definition of Done

(Scoped to D1=(A) + D2=defer + D3 — revise if a different fork is chosen.)

- Task row carries nullable `budget_token_limit` + `budget_action`; materialized
  from `task.created` (additive 1.2.0 payload); surfaced via API + task-registry.
- orchestrator-adapter sources the token ceiling per-task with documented
  precedence; `OMB_DEFAULT_TASK_BUDGET_TOKENS` added.
- `OMB_DEFAULT_TASK_BUDGET_*` in `.env.example` with precedence + 12.3a note.
- `test_budget_policy_inheritance.py` proves default-inherit AND explicit-override.
- budget_action stored but worker-wrapper consumption explicitly DEFERRED to
  12.3a (documented, NOT silently dropped).
- Validation gates green; code review discharged.
- `sprint-status.yaml` flips `12-4-per-task-budget-policy-storage` to done.
