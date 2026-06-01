# Story 12.3 — `/approve --override budget` reaches the Epic-12 enforcement loop + `budget.override` event (FR68)

Status: review

<!-- DECISION RESOLVED 2026-06-01 (operator): D1=(A) keep tier3.budget_override + add budget.override @1.1.0 alias; D2=(II) DEFER grace-window interception to Story 12.3a. Scope = the achievable delta below (console-cli parity + event-naming + /retry sharp-edge docs). The ACs as written already reflect this fork. -->

## ⚠️ Scoping decision required (read FIRST)

A read-only gap analysis (architect pass, 2026-06-01) found that **most of
FR68's surface is ALREADY BUILT** by Stories 6.10/6.11 (FR44), but for a
DIFFERENT, disjoint budget model. Two facts drive the scope:

1. **The existing `/approve --override budget` works at the REGISTRY level**
   (unblocks a `blocked` task row → `executing`, emits `tier3.budget_override`).
   It has ZERO interaction with Epic-12's `budget_supervisor`, which SIGTERMs
   the live subprocess within 5s of `task.budget_exceeded` — autonomously,
   reading the JSONL log, with no inbound channel from registry-api.
2. So FR68's headline AC — *"override before the 5-second grace extends the
   budget and PREVENTS enforcement"* — is **NOT achievable by the existing
   flow**. It requires NEW coupling between the worker-wrapper supervisor and
   override events.

**TWO decisions the operator (you) must make before dev-story:**

- **D1 — Event naming.** `architecture.md:1423` specs a NEW `budget.override`
  @ 1.1.0 (Epic-12 namespace), but the built flow emits `tier3.budget_override`
  @ 1.0.0 (FR44). Options:
  - (A) Keep `tier3.budget_override`; treat `budget.override` as an alias/1.1.0
    bump on the same payload. Lowest churn.
  - (B) Add a genuinely separate `budget.override` event; update materializer +
    registrations + tests. Aligns with architecture.md.
  - (C) Emit both (audit bloat — not recommended).
- **D2 — Grace-window interception (the hard part).** Does this story DO the
  supervisor↔override coupling, or DEFER it?
  - (I) **DO IT:** the supervisor also tails for the override event during a
    grace window and aborts termination; the run_task path gains the
    `awaiting_approval` FSM branch (removing the Story-12.2
    `_reject_unwired_budget_action` validator). Real architectural work
    (the supervisor has no override awareness today; budget_supervisor.py).
  - (II) **DEFER it (Option D from analysis):** scope 12.3 to the achievable
    delta (console-cli parity + event-naming + docs), and document the
    sharp edge: *override after the supervisor has already SIGTERMed requires
    `/retry`*. The "prevent within grace" AC moves to a follow-up (12.3a).
    This SHRINKS 12.3 dramatically and keeps it shippable.

**Recommendation:** **D1=(A)** (lowest churn; rename is cosmetic tech-debt that
a later pass can do) + **D2=(II)** (defer the grace-window interception to a
dedicated follow-up). Rationale: the interception is a real cross-service
coupling change to the autonomous supervisor — it deserves its own story with
its own review, not to be bolted onto the console-cli parity work. Shipping
the achievable delta now (console-cli `--override budget` + event-naming +
the documented `/retry` sharp edge) closes the FR68 *operator-surface* gap
while the harder enforcement-interception gets proper treatment.

Until you pick, this story stays `needs-scoping-decision`.

## Story

**As** the platform operator,
**I want** `/approve --override budget <task-id>` to work from BOTH Telegram
AND the console-cli, extending the task's budget and (per the chosen scope)
either preventing the pending enforcement or cleanly directing me to `/retry`
after termination, with a `budget.override` audit event,
**so that** I can rescue a budget-overrun task without losing its work,
through whichever operator surface I'm using.

## Gap analysis (architect pass — what is ALREADY BUILT vs NET-NEW)

| FR68 / Story 12.3 scope item | Status | Where |
|---|---|---|
| Telegram `/approve --override budget` parse | **BUILT** | `approve_command.py:130-145` |
| Telegram registry_client `override=` kwarg | **BUILT** | `registry_client.py:408,474` |
| registry-api `DecisionRequest.override` field | **BUILT** | `decisions.py:80-86` |
| registry-api budget-gate bypass on override | **BUILT** | `decisions.py:251` |
| Budget calc (`calculate_new_limit`) | **BUILT** | `budget_policy.py:18-35` |
| `BudgetOverridePayload` model | **BUILT** | `payloads.py:926-945` |
| Override audit event emission | **BUILT** (as `tier3.budget_override`) | `decisions.py:401-448` |
| Materializer handler (blocked→executing) | **BUILT** | `handlers.py:468-501` |
| `/retry` command (both surfaces) | **BUILT** | `retry_command.py`, `console_cli/commands/retry.py` |
| **console-cli `--override budget`** | **NET-NEW** | `console_cli/commands/approve.py:21-64` + `adapters/registry_api_client.py:325-389` lack `override` |
| **`budget.override` event (vs tier3.)** | **NET-NEW** (D1) | architecture.md:1423 specs 1.1.0; code emits tier3.@1.0.0 |
| **Grace-window interception** | **NET-NEW** (D2 — architecturally critical) | `budget_supervisor.py` has zero override awareness; no inbound channel |
| **`awaiting_approval` FSM path** | **NET-NEW** (D2) | `main.py:758` hard-codes `TASK_FAILED`; `config.py:120-137` rejects the value (Story 12.2 gate) |
| post-termination `/retry` sharp-edge docs | **PARTIAL** | `/retry` works on failed tasks; no budget-specific doc |

## Acceptance Criteria (scoped for the RECOMMENDED D1=(A) + D2=(II); revise if you pick otherwise)

1. **AC1 — console-cli `--override budget` parity.** Add an `--override`
   option (`Literal["license","budget"]`) to
   `services/console-cli/src/console_cli/commands/approve.py` (mirror the
   Telegram handler's semantics) AND an `override` kwarg to
   `console_cli/adapters/registry_api_client.py::submit_decision`
   (mirror `telegram_gateway/.../registry_client.py:408,474` — include in the
   POST body when set). After this, both operator surfaces reach the
   already-built registry-api override branch identically.

2. **AC2 — `budget.override` event registration (D1=(A) alias form).**
   Register `budget.override` @ schema `1.1.0` pointing at the existing
   `BudgetOverridePayload` (mirror the `tier3.budget_override` registration at
   `event_types.py:307-308`). KEEP `tier3.budget_override` registered for
   backward-compat. The decisions route continues emitting
   `tier3.budget_override` for now; a follow-up may switch the emit to
   `budget.override` once consumers migrate. (If you chose D1=(B), this AC
   becomes "switch the emit + materializer + tests to `budget.override`".)

3. **AC3 — Documented `/retry` sharp edge (D2=(II)).** Document — in the
   `/approve` help text (both surfaces) AND the operator runbook — that
   `--override budget` only extends the budget for a task still in the
   registry-gate `blocked` state; once Epic-12's supervisor has SIGTERMed the
   subprocess (the task is `failed`), the override cannot resurrect it and the
   operator must `/retry`. This is the honest current behavior given D2=(II).

4. **AC4 — `budget.override` counted by metrics-subscriber.** Per the Epic-12
   acceptance gate (`budget_override_total`). If `tier3.budget_override` is
   already counted, confirm + extend to `budget.override`; else add to the
   bounded enum (mirror Story 12.2's `task.budget_enforcement_triggered` enum
   addition). NO new lazy-cardinality label.

5. **AC5 — Tests.** console-cli override unit test (asserts the POST body
   carries `override="budget"`); the contract-parity test for the
   console-cli ⇄ registry-api decision shape still passes; `budget.override`
   registration round-trip; a payload-validation test if a new payload is
   introduced (D1=(B) only).

6. **AC6 — Validation gates green** (ruff/format, mypy 242=baseline, discipline
   incl. check_event_registry, regression no-new-fails).

7. **AC7 — Code review.** Touches the operator decision path (security-adjacent:
   override bypasses a budget GATE) → default `/code-review` minimum; consider
   3-lane given it's an authorization-bypass surface.

### Deferred to a follow-up (Story 12.3a, if D2=(II) chosen)

- Grace-window interception: budget_supervisor tails for the override event
  during a grace delay and aborts termination; run_task gains the
  `awaiting_approval` FSM branch; remove the `_reject_unwired_budget_action`
  validator from Story 12.2's config. This is the FR68 "prevent within 5s"
  AC and is a real cross-service coupling change deserving its own review.

## Dev Notes

### Source map (file:line guardrails)

- **console-cli (NET-NEW):** `services/console-cli/src/console_cli/commands/approve.py:21-64`
  (add `--override`); `console_cli/adapters/registry_api_client.py:325-389`
  (`submit_decision` — add `override` kwarg + POST-body inclusion).
- **Telegram mirror (reference, already built):**
  `telegram-gateway/.../handlers/approve_command.py:130-145`,
  `registry_client.py:408,474`.
- **registry-api override branch (already built — DO NOT re-implement):**
  `registry-api/.../routes/decisions.py:80-86,251,401-448`.
- **Event registration:** `registry-state/.../domain/event_types.py:307-308`
  (`tier3.budget_override`). Add `budget.override` @ 1.1.0 alongside.
- **Existing payload:** `packages/events/src/events/payloads.py:926-945`
  (`BudgetOverridePayload`).
- **Materializer:** `registry-state/.../domain/handlers.py:468-501,747-748`.
- **metrics enum:** `metrics-subscriber/.../app/metrics.py` (mirror Story 12.2).
- **The DEFERRED interception territory (for 12.3a):**
  `worker-wrapper/.../domain/budget_supervisor.py:153-342` (no override
  awareness), `worker-wrapper/.../app/main.py:480-771` (hard-codes TASK_FAILED
  at 758), `worker-wrapper/.../app/config.py:109,120-137` (the Story-12.2
  `_reject_unwired_budget_action` validator).

### Constraints

- **NO `mcp_clients.py` touched.**
- **Reuse the built registry-api override branch** — AC1 only adds the
  console-cli ENTRY to a path that already exists; do not fork the logic.
- **Backward-compat:** keep `tier3.budget_override` registered/emitted under
  D1=(A); FR44 consumers must not break.
- **Authorization-bypass surface:** `--override budget` deliberately bypasses
  the budget gate — the AC7 review must confirm the bypass still requires a
  valid operator decision (tier enforcement, actor identity) and can't be
  triggered by a non-operator.
- **FR26 single-writer preserved** (emit via the existing decisions-route path
  → registry-state materializes).

## References

- [Source: architect gap analysis 2026-06-01 — the full BUILT/NET-NEW table +
  the two-disjoint-budget-models root cause.]
- [Source: prd.md:1029 (FR68) vs prd.md:874 (FR44) — the new-vs-old budget model.]
- [Source: architecture.md:1423 — `budget.override` @ 1.1.0 as an Epic-12 event.]
- [Source: epics.md:2480-2483 — Story 12.3 scope + ACs.]
- [Source: Story 12.2 `config.py:120-137` — the `_reject_unwired_budget_action`
  validator this story (D2=(I)) or its follow-up (D2=(II) → 12.3a) removes.]

## Frontmatter

```yaml
---
story_id: 12.3
story_key: 12-3-approve-override-budget-event
parent_epic: 12
phase: 2
fr_refs: [FR68]
nfr_refs: []
arch_refs:
  - "architecture.md:1423 — budget.override @ 1.1.0 (Epic-12 event)"
  - "Stories 6.10/6.11 (FR44) — the BUILT /approve --override budget registry-level flow (tier3.budget_override)"
  - "Story 12.1 budget_supervisor — the autonomous SIGTERM enforcement the grace-window AC must intercept (DEFERRED to 12.3a under D2=(II))"
  - "Story 12.2 config.py _reject_unwired_budget_action — the awaiting_approval gate this story's interception piece removes"
  - "architect gap analysis 2026-06-01 — most of FR68 surface already built; the delta is console-cli parity + event-naming + (deferred) grace-window interception"
estimated_complexity: SMALL (D2=(II) achievable delta) → LARGE (D2=(I) with grace-window interception)
priority: MEDIUM (FR68 operator-surface parity; the enforcement-interception is the harder, separable piece)
blocks: []
unblocks:
  - console-cli operators get --override budget parity with Telegram
  - budget.override event lands in the Epic-12 namespace
  - (if D2=(I)) operators can prevent budget enforcement within the 5s grace window
---
```

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (dev-story, 2026-06-01).

### Debug Log References

- ruff check + format: clean (1 auto-format applied to test_event_types.py).
- discipline gates (check_imports / check_event_registry / check_single_writer): pass.
- mypy --strict (packages + registry-api + registry-state + worker-wrapper):
  44 errors = baseline (verified unchanged via git-stash A/B); ZERO in any file
  this story touched (event_types.py clean).
- Tests: console-cli test_decision_commands 26 pass; registry-state
  test_event_types 56 pass; metrics-subscriber 98 pass + 1 PRE-EXISTING flake
  (`test_restart_recovery_subprocess::...sigterm_persists_cursor...`, proven
  pre-existing on clean epic-12.2 baseline via stash — NOT this story).
- Cardinality bounds bumped 62→63 in BOTH AC10 tests (steady-state +
  burst-cleanup) for the new pre-populated `event_family="budget"` child.

### Completion Notes List

- D1=(A) + D2=(II) per operator decision (2026-06-01).
- AC1: console-cli `approve --override license|budget` mirrors the Telegram
  surface; the override reaches the decisions POST body and the already-built
  registry-api override branch. Did NOT fork the registry logic.
- AC2: `budget.override` @1.1.0 registered as an alias on `BudgetOverridePayload`;
  `tier3.budget_override` kept (still the emitted name) for FR44 back-compat.
- AC3: `/retry`-after-termination sharp edge documented in console-cli help,
  the Telegram override-parse block, AND a new operator-runbook section.
- AC4: `budget` added to metrics `_EVENT_FAMILIES` (bounded enum); counter stays
  at 0 until the emit migrates. No lazy-cardinality label.
- Grace-window interception + `awaiting_approval` FSM DEFERRED to Story 12.3a
  (filed `backlog`, NOT silently dropped).

### File List

- services/console-cli/src/console_cli/commands/approve.py (M — `--override` option)
- services/console-cli/src/console_cli/adapters/registry_api_client.py (M — `override` kwarg)
- services/console-cli/src/console_cli/test_decision_commands.py (M — 4 override tests)
- services/registry-state/src/registry_state/domain/event_types.py (M — `budget.override` @1.1.0)
- services/registry-state/src/registry_state/domain/test_event_types.py (M — alias round-trip test)
- services/metrics-subscriber/src/metrics_subscriber/app/metrics.py (M — `budget` family)
- services/metrics-subscriber/src/metrics_subscriber/test_metrics_state.py (M — 62→63 bounds ×2)
- services/telegram-gateway/src/telegram_gateway/handlers/approve_command.py (M — sharp-edge doc)
- docs/operator-runbook.md (M — budget-override section)
- _bmad-output/implementation-artifacts/12-3a-budget-override-grace-window-interception.md (NEW — deferred story)

## Definition of Done

(Scoped to D1=(A) + D2=(II) — revise if a different fork is chosen.)

- console-cli `/approve --override budget` reaches the built registry-api
  override branch (parity with Telegram); unit test proves the POST carries it.
- `budget.override` registered @ 1.1.0 (alias on `BudgetOverridePayload`);
  `tier3.budget_override` kept for back-compat; counted by metrics.
- The `/retry`-after-termination sharp edge is documented in both surfaces' help.
- Validation gates green; code review discharged (authorization-bypass surface).
- Grace-window interception + `awaiting_approval` FSM path explicitly DEFERRED
  to Story 12.3a (filed) — NOT silently dropped.
- `sprint-status.yaml` flips `12-3-approve-override-budget-event` to done.
