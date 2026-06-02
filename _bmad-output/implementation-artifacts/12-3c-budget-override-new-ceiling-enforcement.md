# Story 12.3c — Budget-override NEW-CEILING enforcement (re-arm the orchestrator tracker after an override) (FR68 follow-up)

Status: ready-for-dev

<!-- Filed from the Story 12.3a 3-lane review (critic MAJOR-1, 2026-06-02). 12.3a
delivered the grace-window INTERCEPTION (abort the immediate SIGTERM when an
operator override lands in the window) — but it is a ONE-SHOT REPRIEVE: after the
abort, nothing enforces the override's new_limit going forward. This story closes
that gap. Decision resolved 2026-06-02 (architect analysis + operator sign-off):
OPTION A, orchestrator-adapter-scoped, WITH cross-restart persistence. -->

## Resolved decision (2026-06-02)

- **Approach: Option A, orchestrator-adapter-scoped.** The orchestrator-adapter's
  in-memory `BudgetTracker` is the *sole token-counter and ceiling owner*; the
  worker-wrapper supervisor only reacts to the `task.budget_exceeded` signal the
  orchestrator emits and has no ceiling of its own. So the new ceiling must be
  enforced in the orchestrator decision leg, not the worker reaction leg.
  **Option B is rejected** (re-arms the leg that does not own the ceiling →
  enforces nothing). **Option C is NOT chosen** (we are making FR68's "continue
  under the EXTENDED budget" literally true, not documenting the one-shot away).
- **Cross-restart durability: YES — persist the raised ceiling.** The raised
  `new_limit` MUST survive an orchestrator restart mid-task. Persist it to the
  Task row's `budget_token_limit` via **registry-state** (the sole `state.sqlite3`
  writer — FR26-safe; the column already exists). On restart, the orchestrator's
  `_resolve_budget_limit` reload then picks up the raised ceiling automatically.
- **No schema/event bump needed.** `BudgetOverridePayload.new_limit` is already on
  the wire (`packages/events/src/events/payloads.py:961`, computed by
  `registry_api/routes/decisions.py:428` via `calculate_new_limit`). Both
  `tier3.budget_override` and the `budget.override` @1.1.0 alias carry it.

## Problem (from the 12.3a critic lane)

Story 12.3a's override-intercepted path aborts the autonomous SIGTERM and lets the
subprocess run to natural completion — but:

1. The worker-wrapper `budget_supervisor` RETURNS (its task ends) on
   `override_received` — it is NOT re-spawned, so a SECOND `task.budget_exceeded`
   (if the orchestrator-adapter tracker emits one) goes unmonitored.
2. Nothing reads the override's `BudgetOverridePayload.new_limit` to raise the
   effective ceiling. The orchestrator-adapter `BudgetTracker` (the thing that
   emits `task.budget_exceeded`) keeps its ORIGINAL limit — the disjoint
   budget-model problem.

Net: after an override the task runs with NO further budget enforcement, bounded
only by `task_overall_timeout_s` (default 900s). THIS story makes "continue under
the EXTENDED budget" (the literal FR68 wording) real.

## Architecture (grounding evidence — verify line numbers at dev time)

Decision leg (orchestrator-adapter) — the gap to close:
- `services/orchestrator-adapter/src/orchestrator_adapter/domain/task_dispatch.py:159-174`
  — `BudgetTracker` immutable in-memory ceiling owner; `consume()` returns a new
  tracker, so re-arm to a new limit is `BudgetTracker(limit=new_limit, used=tracker.used)`.
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py:333-401`
  — `process_task` builds `tracker` once per invocation, accumulates per OMC step,
  and on `tracker.is_exceeded` emits `task.budget_exceeded` then **`break`s** the
  step loop (main.py:401). THE GAP: the break must become a bounded wait-for-override
  → re-arm → resume.
- `services/orchestrator-adapter/src/orchestrator_adapter/app/main.py:175-199`
  — `_resolve_budget_limit` precedence (per-task row > OMB_DEFAULT_TASK_BUDGET_TOKENS
  > ORCHESTRATOR_TASK_TOKEN_BUDGET). Where a PERSISTED raised ceiling re-enters on
  restart.

Reaction leg (worker-wrapper) — UNCHANGED by this story:
- `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` —
  has no token counter / ceiling; only matches `task_id` on the override event.
  `_scan_for_override` (~:673-757) is a clean JSONL override-scan reference the
  orchestrator MAY reuse. After Option A re-arms and a future breach re-emits
  `task.budget_exceeded` against new_limit, the worker grace-window naturally
  re-engages — no worker code change.

Override source + persistence:
- `packages/events/src/events/payloads.py:948-967` — `BudgetOverridePayload`
  (`new_limit: int = Field(gt=0, le=1_000_000_000)`, validator `new_limit > old_limit`).
- `services/registry-state/src/registry_state/schema.py:135-136` —
  `budget_token_limit` + `budget_action` columns already exist.
- `services/registry-state/src/registry_state/domain/handlers.py:163-185` —
  registry-state (sole writer) persists per-task budget policy; the raise must flow
  through here (a materializer update on the override event), NOT a second writer.

## Hard constraints (non-negotiable)

- **FR26 single-writer:** registry-state is the SOLE writer of `state.sqlite3`. The
  raised-ceiling persistence MUST go through registry-state's materializer — do NOT
  add a writer in orchestrator-adapter.
- **Fail-closed:** if no override arrives within the bounded wait, `break` as today
  (terminate). The override-wait must use a MONOTONIC deadline (mirror 12.3a's
  `budget_grace_window_s` discipline).
- **MCP env = allowlist only.** Do NOT touch `mcp_clients.py`; NO `os.environ.copy()`/
  `dict(os.environ)` in production code; orch↔worker mcp env stays byte-identical.
- **Best-effort audit must not gate the FSM** (existing convention).
- **Idempotent re-arm:** multiple overrides raise to the latest `new_limit`.

## Acceptance Criteria

1. **AC1 — orchestrator re-arm.** On `tracker.is_exceeded`, `process_task` performs
   a bounded MONOTONIC wait for a `tier3.budget_override` / `budget.override` for
   THIS `task_id`; on arrival it rebuilds the tracker to `new_limit` (preserving
   `used`) and RESUMES the step loop instead of breaking.
2. **AC2 — fail-closed.** If no matching override arrives within the window, the
   loop `break`s and terminates exactly as the pre-12.3c behavior (no regression).
3. **AC3 — re-breach enforced.** A re-breach of the NEW ceiling fires enforcement
   again (test: limit 1k→override 5k→spend to 6k → second `task.budget_exceeded`
   emitted; with the worker supervisor present, its grace-window re-engages).
4. **AC4 — cross-restart durability.** The raised `new_limit` is persisted to
   `Task.budget_token_limit` via registry-state's materializer on the override
   event; after an orchestrator restart mid-task, `_resolve_budget_limit` reloads
   the raised ceiling (test: persist→reload returns new_limit, not the original).
5. **AC5 — FR26 preserved.** `check_single_writer.py` exit 0; no new `state.sqlite3`
   writer; persistence flows through registry-state only.
6. **AC6 — carried-over nits (below) all done.**
7. **AC7 — validation gates + 3-lane review.** ruff/format/discipline clean, mypy
   == baseline (0-new on touched files), targeted + integration tests pass;
   `/bmad-code-review` (or 3-lane: code-reviewer + security-reviewer + critic),
   focus on authorization correctness + autonomous-enforcement fail-closed + FR26.

## Carried-over nits from the 12.3a review (do here)

- **code-reviewer MEDIUM (DRY):** extract the duplicated step/threshold/spend
  null-coalescing + logging block (worker-wrapper main.py override branch vs
  terminated branch, ~16 lines ×2) into a `_coerce_enforcement_fields(...)` helper.
- **critic minor (efficiency):** the grace-window override scan starts from
  offset 0 each window; seed the override cursor from the budget-exceeded match
  position to avoid re-scanning large daily JSONLs.
- **critic minor (test realism):** add a TRUE late-arrival test (override lands
  DURING the terminate callback, not after the function returns).
- **5s-window doc:** keep/clarify the 12.3a config NOTE that ~5s is only usable by
  a pre-staged/automated override; ensure FR68 docs reflect the now-real extended
  budget semantic.

## Tasks (suggested sequence)

1. orchestrator-adapter: add a bounded monotonic override-wait + tracker re-arm in
   `process_task` (replace the unconditional break at main.py:401). New config knob
   for the orchestrator-side wait window (reuse OMB_BUDGET_GRACE_WINDOW_S semantics
   or a distinct `OMB_ORCH_OVERRIDE_WAIT_S`, gt0 le300; decide at dev — default
   matched to the worker window). Override source: prefer task-registry MCP query
   for the persisted `budget_token_limit` (avoids a 2nd JSONL tailer) — confirm at dev.
2. registry-state: materializer update on `tier3.budget_override`/`budget.override`
   → write `new_limit` to `Task.budget_token_limit` (sole-writer path).
3. orchestrator-adapter: confirm `_resolve_budget_limit` reload picks up the
   persisted raised ceiling on restart (AC4).
4. Worker-wrapper nits (DRY helper, scan-cursor seed, late-arrival test) — no
   behavior change to the reaction leg.
5. Tests: AC3 re-breach integration, AC4 persist→reload, fail-closed AC2, true
   late-arrival; update any cardinality/contract assertions if touched.
6. Docs: FR68 wording (extended-budget now real), operator-runbook override section.
7. Gates + 3-lane review (AC7).

## Open questions settled / remaining

- SETTLED: A vs B vs C → A. Restart durability → YES (persist).
- REMAINING for dev/architect: (a) override source = MCP query vs JSONL tail;
  (b) orchestrator wait-window knob name + default; (c) per-task override COUNT cap
  (value cap already exists via `le=1_000_000_000`) — likely out-of-scope, confirm.

## Frontmatter
```yaml
---
story_id: 12.3c
story_key: 12-3c-budget-override-new-ceiling-enforcement
parent_epic: 12
phase: 2
fr_refs: [FR68]
nfr_refs: []
arch_refs:
  - "Story 12.3a — delivered the grace-window interception (one-shot reprieve); this closes the new-ceiling-enforcement gap the critic lane flagged"
  - "Story 12.3c architect analysis 2026-06-02 — BudgetTracker is the sole ceiling owner (orchestrator-adapter); Option A orchestrator-scoped + persist new_limit; B rejected"
  - "Story 12.4 — per-task budget storage; new_limit persistence reuses Task.budget_token_limit + registry-state materializer path"
decision:
  approach: A-orchestrator-scoped
  cross_restart_persistence: true
  schema_bump_needed: false
estimated_complexity: MEDIUM-LARGE (process_task control-flow surgery + registry-state materializer persistence + tests + 3-lane review)
priority: LOW-MEDIUM (12.3a ships the safe interception; this is the full-fidelity extension)
blocks: []
unblocks:
  - FR68 "continue under the EXTENDED budget" becomes literally true
---
```
