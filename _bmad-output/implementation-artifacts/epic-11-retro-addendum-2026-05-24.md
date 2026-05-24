# Epic 11 Retrospective — Addendum (Carry-forward stream)

**Date:** 2026-05-24
**Parent retro:** `epic-11-retro-2026-05-21.md` (closed Epic 11 main stream 11.1–11.5)
**Addendum scope:** Carry-forward stories 11.2.1, 11.2.2, 11.2.3, 11.3.1 closed between 2026-05-23 and 2026-05-24
**Status:** ✅ Supplement to closed retro; existing L1–L6 + AI-1–AI-5 stand; new L7–L12 + AI-6–AI-10 added

---

## Why an addendum

The parent retro was authored on 2026-05-21 when Epic 11's 5 main stories closed. Four carry-forward
stories then closed in rapid succession over the following 3 days. Each pass-1 review caught P0/P1-H
residuals that pass-1 dev shipping CI-green had missed — extending the L1 validation streak from
3 (Stories 11.3/11.4/11.5) to **11 consecutive cross-cutting stories**. The new lessons here are
**pattern-level** discoveries — none of them appear in L1–L6 of the parent retro.

## Carry-forward delivery summary

| Story | Title | Final SHA | Pass | P0/P1-H caught by pass-1 review |
|---|---|---|---|---|
| 11.2.1 | `capability.denied` HTTP-boundary emission (DD5 phase 1) | `c9bdd2a` | 1-pass | 4 P1-H robustness (PP1 PD-1 swallow, PP2 trace_id WARN, PP3 typed enum-drift, PP4 actor_id default) |
| 11.2.2 | `capability.denied` MCP-boundary emission (DD5 phase 2) | `ddc8828` | **2-pass** | **P0** env-allowlist incompleteness (would brick feature on first opt-in) + PP2 shutdown-race finally-block |
| 11.2.3 | fcntl.flock + `forward_capability_denied_audit` (FR26 + PQ9 + DD5 phase 3) | `0ed21d1` | 1-pass | **P0 LIVE-REPRODUCED** cardinality test gap + PP3 P1-H BaseException-leak in flock acquisition |
| 11.3.1 | 10-event approval-inbox replay integration test (Story 11.3 AC5 closure) | `367eca0` | 1-pass | **P0** test-intent-vs-outcome (`tracking_flock` recorded ops BEFORE real call) + PP1 tautological mock branch |

**Stories shipped:** 4/4 (100%)
**L1 consecutive-validation count entering this addendum:** 5 (Stories 11.1–11.5)
**L1 consecutive-validation count exiting this addendum:** **11**
**Epic 10 retro DD5 status:** ✅ Architecturally closed across HTTP + MCP boundaries
**FR26 multi-writer status:** ✅ Solved via fcntl.flock(LOCK_EX) in EventLogWriter
**PQ9 audit-forgery vector:** ✅ Closed via dedicated `forward_capability_denied_audit` tool + restored `emit_event` rejection

---

## New lessons (L7–L12)

### L7 — BaseException-leak pattern in resource acquisition

**Discovery:** Stories 11.2.2 PP2 and 11.2.3 PP3 are the same defect class. In both cases, a resource
(an async-context exit handler in 11.2.2; an `fcntl.flock(LOCK_EX)` in 11.2.3) was acquired **outside**
the `try` block whose `finally` released it. On `BaseException` (KeyboardInterrupt, SystemExit,
asyncio.CancelledError) raised between acquisition and `try:`, the release path is bypassed.

```python
# Wrong — acquisition outside try; LOCK_UN leaks on BaseException
fcntl.flock(self._fd, fcntl.LOCK_EX)
try:
    os.write(self._fd, line)
finally:
    fcntl.flock(self._fd, fcntl.LOCK_UN)

# Right — acquisition inside try; locked_fd local enables narrow finally
locked_fd = None
try:
    fcntl.flock(self._fd, fcntl.LOCK_EX)
    locked_fd = self._fd
    os.write(self._fd, line)
finally:
    if locked_fd is not None:
        fcntl.flock(locked_fd, fcntl.LOCK_UN)
```

**Conclusion:** Resource-acquisition lines belong INSIDE the `try` block whose `finally` releases them.
A `locked_fd is None` sentinel in `finally` makes the release conditional on successful acquisition.
This pattern was missed twice in 24 hours by pass-1 dev — it deserves a codified review check.

**Action AI-6 (new):** Add a BaseException-leak audit pass to `bmad-code-review` skill — grep for
`flock(`, `acquire(`, `__enter__(`, `connect(` followed by `\ntry:` (acquisition NOT inside try)
across changed files. Flag P1-H whenever acquisition precedes `try:`. Cheap automated check; high
signal.

### L8 — Test-intent vs test-outcome (the deadliest test smell)

**Discovery:** Three sibling instances across the addendum stream:

1. **Story 11.3.1 PP3 P0** — `test_keyboard_interrupt_releases_flock` had `tracking_flock` record
   the operation **before** calling the real `fcntl.flock`. Result: the assertion passed even if
   LOCK_UN was never executed. The test asserted **what the test intended to verify**, not
   **what the production code actually did**.
2. **Story 11.3.1 PP1** — `_get_pinned_inbox` mock returned the same value regardless of input,
   making the downstream assertion tautological (it would pass even if the production code
   ignored the input parameter entirely).
3. **Story 11.2.2 PP10** — `OMB_MCP_AUDIT_EMISSION_ENABLED=0` unit test made the kill-switch
   contract concrete: without it, a regression that silently re-enabled emission would not
   have failed any test.

**Pattern:** A test that fails ONLY when the test itself is misconfigured — not when the production
code is buggy — is a tautology. The shortest sanity check: **insert a deliberate one-line bug in
the production path. Does the test fail?** If no, the test pins nothing.

**Conclusion:** "CI is green" and "this story's tests would catch a regression" are different
properties. Many of Epic 11's pass-1-dev test additions optimized for the former without
verifying the latter.

**Action AI-7 (new):** Augment AI-5 with: "for tests that assert resource-cleanup invariants
(lock release, connection close, file fsync, kill-switch honored), the test author must verify
that a known-buggy substitute production implementation fails the test. Document the substitute
in a `# Sanity check:` comment if non-obvious." Add to `bmad-create-story` skill prompts.

### L9 — Env-allowlist completeness as a security-critical attribute

**Discovery:** Story 11.2.2 PP1 was a **CRITICAL** finding: the clawhip-bridge subprocess env-allowlist
was missing `CLAWHIP_BRIDGE_ACTOR_KIND` and `CLAWHIP_BRIDGE_ACTOR_ID` (the very env vars that opt-in
to capability.denied emission). Result: the feature would have been **default-OFF in code but
incapable of being turned ON in production** — the subprocess simply wouldn't see the env vars.
Pass-1 dev's "CI green" was meaningless because no integration test exercised the actual subprocess
env-passing path with a non-default opt-in.

**Resolution:** PP1 fixed the gap. PP8 (separate finding, same story) added a **mirror-identity
contract test**: a test that imports both the `_ENV_ALLOWLIST` constant in clawhip_client.py AND
the allowlist filter in clawhip-bridge's `__main__.py` startup, and asserts they agree. Drift
becomes a test failure.

**Pattern:** Any time two lists-of-keys must agree (env-allowlist + consumer; ORM schema + alembic
migration; persisted enum + Literal type alias), write a contract test that compares them at runtime.
The convention "we'll just keep them in sync by hand" is empirically insufficient.

**Conclusion:** Story 11.2.1 PP3 had already pioneered this pattern (`_TIER_INT_TO_LITERAL[int, _TierLiteral]`
typed enum-drift contract test). Story 11.2.2 PP8 generalized it. Add to canon.

**Action AI-8 (new):** Codify the **mirror-identity contract test** technique in
`docs/patterns/cross-file-contract-tests.md` (or equivalent). Reference Story 11.2.1 PP3 and
Story 11.2.2 PP8 as exemplars. When `bmad-create-story` identifies a list-of-keys SSoT-vs-consumer
relationship, prompt for a contract test in AC.

### L10 — Constants-as-single-source-of-truth (3+ rule)

**Discovery:** Story 11.2.3 PP9 collapsed three separate hardcoded literal sites for `capability.denied`
emission metadata (schema_version "1.1.0" + actor_override Actor("system", "clawhip-bridge-mcp") +
tier-check) into a single dict-of-tuples at module scope:

```python
_SYSTEM_EMITTER = Actor(kind="system", id="clawhip-bridge-mcp")
_CAPABILITY_DENIED_TYPE = "capability.denied"
_emit_overrides: dict[str, tuple[str, Actor]] = {
    _CAPABILITY_DENIED_TYPE: ("1.1.0", _SYSTEM_EMITTER),
}
```

Three downstream sites (`_check_tier_with_self_emit`, the dedicated forward tool, the PQ9 rejection
in `emit_event`) now look up the same constant. Drift becomes impossible.

**Conclusion:** When you find yourself fixing a bug by changing 2+ hardcoded literals in lockstep,
extract a constant **and** audit for other references. Story 11.2.3 PP9 found 3 sites; without the
audit, a 4th drift site would have re-introduced the bug.

**Action AI-9 (new):** When `bmad-code-review` flags a fix that touches 2+ hardcoded literal sites,
require the implementer to grep for additional references in the same commit. Surface this as a
checklist item in the dev-story workflow's "before declaring done" pre-flight.

### L11 — Blind Hunter false-positive rate is non-zero (and that's fine)

**Discovery:** Story 11.2.1 pass-1 review had 3 Blind Hunter P0 claims that, on verification with
codebase context, turned out to be P2 or false positives entirely (the claimed broken invariant
was actually preserved by code Blind Hunter couldn't see). This is **not** an indictment of Blind
Hunter; it's expected behavior given Blind Hunter operates on diff-only with no codebase context.

**Pattern:** Severity claims that depend on codebase context (e.g., "this caller doesn't handle X"
where the caller is outside the diff) must be **verified** before action. Treat Blind Hunter P0
as "**investigate this priority area**" not "**this is definitely P0**". Cross-lane convergence
(Blind + Edge + Acceptance all flagging the same area) remains the strongest signal — single-lane
P0 claims are weaker.

**Conclusion:** The 3-lane review's value is in **convergence**, not in any single lane's verdict.
Story 11.4's hardcoded `action="approve"` was P0 precisely because all 3 lanes flagged it. Story
11.2.1's 3 Blind-only P0s were verifiably less severe.

**Action AI-10 (new):** Update `bmad-code-review` skill: when summarizing findings, mark P0/P1-H
claims as **"cross-lane convergent"** (≥2 lanes flagged) or **"single-lane"**. Single-lane P0s
require verification step before batch-apply. Cross-lane convergent P0s ship directly to fix.

### L12 — Carry-forward streams are the strongest L1 evidence

**Discovery:** The parent retro's L1 ("cross-cutting stories require 3-lane review regardless of
complexity estimate") was based on 3 consecutive validations (Stories 11.3/11.4/11.5). The carry-forward
stream added 4 more, bringing the consecutive count to 11. Among those 4:

- Story 11.2.1: 4 P1-H robustness findings missed by pass-1 dev
- Story 11.2.2: **P0** env-allowlist incompleteness missed by pass-1 dev (required pass-2 to catch);
  pass-1 review caught a separate PP2 finally-block leak
- Story 11.2.3: **P0** LIVE-REPRODUCED cardinality test gap + PP3 P1-H BaseException-leak
- Story 11.3.1: **P0** test-intent-vs-outcome pinning failure (the test that was supposed to
  pin Story 11.2.3 PP3's BaseException-leak fix was itself tautological)

**Recursive validation:** Story 11.3.1 P0 (L8) is meta — it's a test that was supposed to pin
Story 11.2.3 PP3's fix (L7). The test passed when the fix was applied, but it would have **also**
passed without the fix. Without the L1 mandate, both the L7 pattern and the L8 test-realism
gap would have shipped as silent gaps.

**Conclusion:** L1 is now the **most empirically grounded** lesson in any oh-my-bmad retro.
11 consecutive validations across 11 cross-cutting stories with **zero** counter-examples (stories
where pass-1 dev shipped a cross-cutting story with zero pass-1-review-caught residuals).

**No new AI:** L12 reinforces existing AI-1 + AI-4. No additional action needed beyond continued
discipline.

---

## New action items (AI-6–AI-10)

| # | Action | Owner | Trigger condition for Epic 12 |
|---|---|---|---|
| AI-6 | BaseException-leak audit pass in `bmad-code-review` skill (grep for `flock(`/`acquire(`/`connect(` followed by `\ntry:`) | code-review skill | Stories touching `try`/`finally` resource acquisition |
| AI-7 | Test-realism "insert known-buggy substitute" sanity check in `bmad-create-story` skill prompts | create-story skill | All stories asserting resource-cleanup invariants |
| AI-8 | Codify mirror-identity contract test pattern (Story 11.2.1 PP3 + Story 11.2.2 PP8) as canon | dev | Stories with list-of-keys SSoT-vs-consumer relationships |
| AI-9 | Constants-extraction: when fixing 2+ literal sites in lockstep, grep for further references in same commit | dev-story workflow | All fixes that touch 2+ hardcoded literals |
| AI-10 | `bmad-code-review` marks P0/P1-H findings as "cross-lane convergent" vs "single-lane"; single-lane P0s require verification before batch-apply | code-review skill | All reviews with ≥2 P0/P1-H claims |

## Patterns ratified by the addendum stream (no new AI)

- **L1 (cross-cutting stories → 3-lane review mandate):** 11 consecutive validations, 0 counter-examples. **Strongest claim in any oh-my-bmad retro.**
- **L4 (3-lane review pays for itself):** Story 11.2.2 pass-2 alone caught a P0 that would have bricked the feature on first opt-in.
- **L6 (test-fixture realism):** L8 extends L6 from "fixture shapes" to "test intent vs outcome" — both are realism failures.

## Anti-patterns added to Epic 12+ watchlist

1. **Acquisition before `try:` on resource-holding lines** — see L7.
2. **Tests that record actions BEFORE the action happens** — see L8 (`tracking_flock` recorded LOCK_EX before `real_flock(LOCK_EX)` succeeded).
3. **Mocks that return constant values for parameter-varied calls** — see L8 PP1 (`_get_pinned_inbox` returned same inbox for any chat_id, making the "lookup-by-chat_id" claim untestable).
4. **Env-allowlist additions without corresponding mirror-identity contract test** — see L9.
5. **Fix-by-extracting-constant without auditing for additional references** — see L10.
6. **Single-lane P0 claims taken at face value without codebase-context verification** — see L11.

## Readiness for Epic 12 (re-validated post-addendum)

- ✅ DD5 architecturally closed across HTTP + MCP boundaries (Stories 11.2.1 + 11.2.2 + 11.2.3)
- ✅ FR26 multi-writer invariant preserved via fcntl.flock(LOCK_EX) inside try-block (Story 11.2.3)
- ✅ PQ9 audit forgery vector closed via dedicated forward tool + restored emit_event rejection (Story 11.2.3)
- ✅ Story 11.3 AC5 deferral closed via in-process mock-harness (Story 11.3.1)
- ⚠️ Story 11.3.3 (nightly deeper diagnosis) remains backlog — explicitly deferred per spec ("can wait until Epic 12+ surfaces local-repro opportunity"); no production impact
- ⚠️ Story 11.5.1 (`/key-status` Telegram + console surface) remains backlog — small Epic 11 cleanup
- ✅ PR-gate CI green throughout addendum stream; no regressions

**Recommendation unchanged from parent retro:** Proceed to Epic 12. The L1 mandate now has 11
consecutive validations and is the operative discipline for Epic 12 spec authoring + review cadence.

## Commitments

- AI-6 through AI-10 carry forward into Epic 12 spec authoring + code-review skill prompts
- Parent retro AI-1 through AI-5 remain in force; AI-1 (3-lane review for cross-cutting) is **upgraded**
  from "recommended" to **"empirically mandatory"** based on the 11-validation streak
- L7 + L8 + L9 + L10 patterns added to anti-patterns watchlist for Epic 12+

---

**Epic 11 retrospective addendum complete.** Six new lessons (L7–L12) captured; five new action items
(AI-6–AI-10) carried forward into Epic 12. The 11 consecutive L1 validations across Stories 11.1–11.5
+ 11.2.1–11.2.3 + 11.3.1 ratify the cross-cutting-story 3-lane review mandate as the strongest
empirically grounded discipline in the project.
