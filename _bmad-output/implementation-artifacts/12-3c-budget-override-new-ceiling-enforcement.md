# Story 12.3c — Budget-override NEW-CEILING enforcement (re-couple the tracker after an override) (FR68 follow-up)

Status: backlog

<!-- Filed from the Story 12.3a 3-lane review (critic MAJOR-1, 2026-06-02). 12.3a
delivered the grace-window INTERCEPTION (abort the immediate SIGTERM when an
operator override lands in the window) — but it is a ONE-SHOT REPRIEVE: after the
abort, nothing enforces the override's new_limit going forward. This story closes
that gap. -->

## Problem (from the 12.3a critic lane)

Story 12.3a's override-intercepted path aborts the autonomous SIGTERM and lets the
subprocess run to natural completion — but:

1. The worker-wrapper `budget_supervisor` RETURNS (its task ends) on
   `override_received` — it is NOT re-spawned, so a SECOND `task.budget_exceeded`
   (if the orchestrator-adapter tracker emits one) goes unmonitored.
2. Nothing reads the override's `BudgetOverridePayload.new_limit` to raise the
   effective ceiling. The orchestrator-adapter `BudgetTracker` (the thing that
   emits `task.budget_exceeded`) keeps its ORIGINAL limit — the disjoint
   budget-model problem (see the Story 12.3 architect gap analysis).

Net: after an override the task runs with NO further budget enforcement, bounded
only by `task_overall_timeout_s` (default 900s). 12.3a's comments + docs were
corrected to call this a "one-shot reprieve" (not "extended budget"); THIS story
makes "continue under the EXTENDED budget" (the literal FR68 wording) real.

## Scope / open design question

The crux is the SAME disjoint-budget-model coupling 12.3/12.3a kept deferring:
the override's `new_limit` lives in the registry-level model; the enforcing
tracker lives in orchestrator-adapter. Options to weigh in a create-story /
architect pass:
- (A) After override, re-spawn the supervisor AND deliver `new_limit` to the
  orchestrator-adapter `BudgetTracker` (so a re-breach of the NEW ceiling is
  enforced). Requires an orchestrator-adapter ↔ override coupling.
- (B) Keep enforcement in worker-wrapper: the supervisor re-arms with the
  new_limit and re-SIGTERMs if spend crosses it again (but worker-wrapper does
  not currently track cumulative spend — that's the orchestrator-adapter's job).
- (C) Accept one-shot-reprieve as the FR68 semantic of `--override budget` and
  REVISE FR68's wording instead (operator explicitly chose to let the task exceed
  its budget). Lowest effort; may be the honest product call.

Decision deferred to this story's create-story (needs the architect + likely an
operator decision, like 12.3/12.4).

## Carried-over nits from the 12.3a review (do here)

- **code-reviewer MEDIUM (DRY):** extract the duplicated step/threshold/spend
  null-coalescing + logging block (main.py override branch vs terminated branch,
  ~16 lines ×2) into a `_coerce_enforcement_fields(...)` helper.
- **critic minor (efficiency):** the grace-window override scan starts from
  offset 0 each window; for large daily JSONLs this re-scans. Consider seeding
  the override cursor from the budget-exceeded match position.
- **critic minor (test realism):** add a TRUE late-arrival test (override lands
  DURING the terminate callback, not after the function returns).
- **5s-window doc:** 12.3a added a config NOTE that ~5s is only usable by a
  pre-staged/automated override; if (C) is chosen, surface this in the FR68 docs.

## Acceptance Criteria (DRAFT — re-scope at create-story)

1. Decide A/B/C (architect + operator).
2. If A/B: after override, the override's `new_limit` is the effective ceiling;
   a re-breach of new_limit is enforced (test: override raises limit 1k→5k, spend
   to 6k → second enforcement fires). If C: FR68 doc revised; 12.3a's one-shot
   semantic documented as final; the DRY/efficiency/test nits still done.
3. Validation gates green; 3-lane review (authorization + autonomous-enforcement).

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
  - "Story 12.3 architect gap analysis — the disjoint registry-vs-worker budget models this coupling must bridge"
  - "Story 12.4 — per-task budget storage; the new_limit delivery may reuse its path"
estimated_complexity: MEDIUM-LARGE (cross-service coupling OR a product-scope decision + nits)
priority: LOW-MEDIUM (12.3a ships the safe interception; this is the full-fidelity extension)
blocks: []
unblocks:
  - FR68 "continue under the EXTENDED budget" becomes literally true (if A/B)
---
```
