# Deferred Work

A running log of issues that surfaced during code review but were not fixed at the time. Each entry cites its origin (story + commit + reviewer) so a future agent or human can pick it up with context.

## Deferred from: code review of story-3.6 (2026-04-30)

- **D1 — Allowlist-vs-rate-limiter layering on telegram-gateway** ~~(RESOLVED by Story 7.5.1 — `PerActorRateLimitMiddleware` added as aiogram inner middleware)~~
- **D2 — ✅ CLOSED 2026-06-05.** Contract is documented in rate_limit.py docstring and the `self._tokens -= 1.0` line comment: "charge-on-attempt is deliberate DoS protection; token consumed regardless of handler outcome". Pattern mirrors Epic-11 L7 BaseException discipline. *Original:* token consumed before call_next, undocumented.
- D3 — ✅ CLOSED 2026-06-05. Documented: all-methods accounting is deliberate (low real-world impact — Telegram only POSTs). *Original:* path predicate ignores method.
- D4 — ✅ CLOSED 2026-06-05. Documented: BaseHTTPMiddleware `finally` fires after headers but before StreamingResponse body iteration. No streaming endpoints exist; docstring updated to note the limitation. *Original:* streaming response contextvar unbind timing.
- **D5 — ✅ WONTDO.** Starlette decodes request bodies via latin-1; raw bytes never reach structlog. Single bad log call would drop record but no evidence of occurrence. Default `json.dumps` error behavior is acceptable.
- **D6 — ✅ CLOSED 2026-06-05.** Contract pinned: module sentinel is set once by production entrypoint; tests use `caplog` or `structlog.testing` instead of resetting the flag. No test currently resets it. *Original:* sentinel could be clobbered from tests.
- **D7 — ✅ CLOSED 2026-06-05.** Both `RequestIdMiddleware` and `IdempotencyKeyMiddleware` now emit `_log.debug(...)` when the header is absent or empty, so operators can distinguish "absent" from "empty". Also added duplicate-header detection (D9). 46 middleware tests pass, ruff clean. *Original:* empty-string header regen was silent.
- D8 — ✅ CLOSED 2026-06-05. Invariant pinned: unbind runs in `finally` (load-bearing per Story 3.6 AC-1 comment at middleware.py:316-318). No second binder exists; save/restore refactor tracked if one lands. *Original:* unconditional reset could clobber future second binder.
- **D9 — ✅ CLOSED 2026-06-05.** Both middlewares now use `getlist()` + warn on duplicates (first-wins), mirroring the existing `X-Trace-Id` precedent at line 216. 46 middleware tests pass, ruff clean. *Original:* duplicate headers silently first-wins.
- **D10 — `Retry-After: 1` lies under Phase 2 slow refill** ~~(RESOLVED by Story 7.5.3 — dynamic `math.ceil()` computation)~~
- D11 — ✅ CLOSED 2026-06-05. Contract documented: `exclude_none=True` is intentional — None fields are "absent" not "null". Telegram renderer treats missing keys as absent. *Original:* subtle contract shift concern.
- **D12 — ✅ NIT.** `_ManualClock` provides the methods tests actually call; `# type: ignore` is scoped to the fixture. FrozenClock/TickingClock test doubles available in `events.clock`. Not worth extending Protocol for unused methods.
- D13 — ✅ CLOSED 2026-06-05. Implementation is correct (`monotonic_ns()`); spec text was stale reference only. No code change needed. *Original:* spec text referenced `now_monotonic_ns()`.

## Deferred from: code review of 3-11-blocker-notification-template (2026-05-01)

- D1 — ✅ CLOSED 2026-06-05. Refactor deferred as cross-cutting renderer cleanup; pattern is consistent within blocker renderer. Not a defect.
- D2 — ✅ WONTDO. Whole project is English-only Phase 1; i18n deferred to post-MVP.
- D3 — ✅ CLOSED 2026-06-05. Sentinel is consistent across all renderers; uniform extraction tracked as cross-cutting story if behavior diverges.
- D4 — 🔄 GATED-ARCH. State machine design deferred to Story 3.10 M16 follow-up; current status tracking is functional for MVP.
- D5 — `task_id` whitespace `pattern=` validator ~~(RESOLVED by Story 7.5.8 — pattern applied to all 18 task_id fields)~~
- D6 — ✅ NIT. Project convention follows `_APPROVAL_*` without `Final`; consistency maintained.
- D7 — ✅ WONTDO. Over-engineered per review; emergency tier handles pathological task_ids.

## Deferred from: code review of 3-12-completion-summary-template (2026-05-01)

- D1 — `task_id` regex `pattern=` absent across all `Task*Payload` models ~~(RESOLVED by Story 7.5.8 — pattern applied to all 18 fields)~~
- D2 — `_collapse_newlines` doesn't strip U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR ~~(RESOLVED by Story 7.5.8 — Unicode fix applied)~~
- D3 — `pr_branch` accepts characters git ref-name disallows ~~(RESOLVED by Story 7.5.8 — pattern + @field_validator)~~
- D4 — 🔄 GATED-OPS. Operator-supplied input sanitization concern; needs decision on sanitization responsibility boundary.
- D5 — ✅ NIT. Pattern consistent across 4+ test helpers; consolidation refactor deferred.
- D6 — ✅ NIT. Fixed seed intentional for reproducibility; consistent across 3 renderer test files.
- D7 — ✅ NIT. Docstring convention is clear from codebase context.

## Deferred from: code review of 3-13-self-recovered-summary-template (2026-05-03)

- D1 — ✅ NIT. Inline import is intentional — module-level import triggers side effects in structlog testing. Pattern documented.
- D2 — ✅ CLOSED 2026-06-05. `_build_diff_stats_line` now uses singular "1 file changed" / plural "N files changed" (2 new parametrized test cases for fc=1). 162 telegram_sink tests pass, ruff clean. *Original:* rendered "1 files changed" (no singular form).
- D3 — ✅ NIT. Project never runs under `-O`; defensive pattern is standard.
- D4 — ✅ NIT. Acceptable at cap=1900; premature optimization in test utility.
- D5 — ✅ NIT. Test coverage gap symmetrical to pr_branch newline test; no evidence of issue in production. Add if pr_url newline rendering surfaces.

## Deferred from: code review of 3-5-4-pre-existing-test-failure-resolution (2026-05-04)

- D1 — ✅ CLOSED 2026-06-05. Code already simplified to `line = to_canonical_json(env) + b"\n"`; stale comments removed in prior cleanup.
- D2 — ✅ CLOSED 2026-06-05. Registry-population guard added: `assert len(_REGISTRY) > 0` fires loudly if registrations move behind a function call.
- D3 — ✅ CLOSED 2026-06-05. Added `TestBaseModelPayload.test_basemodel_payload_round_trips_via_canonical` proving `payload=_SimplePayload(...)` produces identical bytes to `payload={"value": "hello"}`. 24 canonical tests pass, ruff clean. *Original:* missing unit test for BaseModel payload path.

## Deferred from: code review of 5-3-worktree-lock-acquisition (2026-05-07)

- D1 — 🔄 GATED-ARCH. Same root as acquire_lock TOCTOU; fix requires architectural decision on lock protocol.
- D2 — 🔄 GATED-OPS. Requires filesystem-level corruption bypassing os.replace atomicity; operator intervention needed regardless.
- D3 — 🔄 GATED-ARCH. Belongs in task state machine (Story 5.12+); out of scope for lock primitive.

## Deferred from: code review of 5-17a-resume-after-approval-state-machine (2026-05-09)

- D1 — ✅ CLOSED 2026-06-05. By design for finite worker lifecycle (10-20 transitions per task). If FSM is reused across tasks, add `clear_log()`.
- D2 — ✅ CLOSED 2026-06-05. Design pinned: exception carries auditable data; runtime layer (5.17b) logs it. FSM is pure domain module.
- D3 — 🔄 GATED-ARCH. By design: separate flow paths converge at RESUMED. Add cross-path transitions if 5.17b runtime needs them.

## Deferred from: code review of 5-18-journey-1-integration-test (2026-05-09)

- D1 — ✅ NIT. Intentional fixture independence; extraction would cross fixture boundaries.
- D2 — ✅ NIT. Only triggered by log rotation during test; no real-world impact.
- D3 — ✅ WONTDO. By-design Phase 1 scope; Phase 2 Epic 6 adds real approval gating.

## Deferred from: code review of 6-14-tier3-negative-test (2026-05-11)

- D1 — ✅ NIT. Pre-existing pattern in 3 test files; fix should be applied together. Low priority — `_build_harness` rarely crashes in practice.
- D2 — ✅ NIT. All three methods share the same `_MUTATING_METHODS` check; GET is sufficient.
- D3 — 🔄 GATED-ARCH. Phase-1 default-open policy; separate concern from Tier-3 testing. Dedicated story when hardening.

## Deferred from: code review of 7-7-worktree-lock-blocker-persistence (2026-05-12)

- D1 — `_close_active_session_for_task` only closes ONE session when multiples may exist ~~(RESOLVED by Story 7.5.2 — bulk UPDATE replaces single-session close)~~
- D2 — Missing compound index on `(sessions.task_id, sessions.status)` ~~(RESOLVED by Story 7.5.2 — Alembic migration 0003)~~
- D3 — ORM attribute mutation vs bulk UPDATE in `_close_active_session_for_task` ~~(RESOLVED by Story 7.5.2 — switched to bulk UPDATE)~~

## Deferred from: code review of 7-8-self-recovered-summary (2026-05-12)

- D1 — ✅ WONTDO. Adding the filter would narrow the feature incorrectly.
- D2 — ✅ CLOSED 2026-06-05. Mitigated by Story 7.5.6 `after` cursor param enabling pagination without truncation.
- D3 — 🔄 GATED-ARCH. Best-effort synthesis is acceptable; dedup architecture needs dedicated story.

## Deferred from: code review of 7-9-journey-3-integration-test (2026-05-12)

(All findings were fixed during the review pass — no deferred items.)

## Deferred from: code review of 7-5-2-session-bulk-close-and-index (2026-05-13)

- D1 — ✅ NIT. Project never runs under `-O`; pre-existing pattern across all handlers.

## Deferred from: code review of 7-5-5-worktree-lock-release-touctou (2026-05-14)

- D1 — ✅ CLOSED 2026-06-05. Caller has broad `except Exception` wrapper; FileNotFoundError is the expected race. PermissionError propagation is correct behavior (signals real permission issue). Documented in code review.
- D2 — 🔄 GATED-OPS. Stale lock recovery is a manual operator procedure; missing key in corrupt lock is an operator intervention case.

## Deferred from: code review of 7-5-6-events-endpoint-truncation-and-trace-id (2026-05-14)

- D1 — 🔄 GATED-ARCH. Low urgency at current scale; requires Alembic migration. Add `ix_events_task_id_mono_ns` when query latency warrants.
- D2 — ✅ CLOSED 2026-06-05. Inclusive `>=` is documented in endpoint docstring as "re-poll may return the last-seen event". Backward-compat preserved. *Original:* potential duplicates on re-poll.
- D3 — 🔄 GATED-OPS. By design for CLI use; auth handled at infrastructure layer (API gateway).
- **D4 — `trace_id: None` in wire contract** (Blind Hunter, events.py:43): ~~Hardcoded None with Phase 2 dependency documented. Not a defect — ORM column + migration + materializer required. Tracked in AC-2.~~ **RESOLVED by Phase 2 Epic 9 (α `trace_id` propagation kernel) — Story 9.7 ships schema_version bump 1.0.0 → 1.1.0 + `events.trace_id` column + index + migrator backfill. See ADR-0003 + (forthcoming) ADR-0004.**
- D5 — ✅ NIT. Shared constant; extract to shared module only if it changes again.
- D6 — 🔄 GATED-ARCH. Adding response_model would break wire contract; requires API versioning decision.

## Deferred from: code review of 7-5-7-integration-test-harness-decision (2026-05-14)

- D1 — ✅ NIT. Journey 1 has local copies; j3/j6 already migrated. Migration deferred to next j1 touch.
- D2 — ✅ NIT. Candidates for future extraction; not blocking any test work.
- D3 — ✅ NIT. Accumulating stderr would improve diagnostics but not correctness; low priority.
- D4 — ✅ NIT. Per-service health summary in timeout message would improve DX; low priority.
- D5 — ✅ NIT. ADR template evolution is acceptable.

## Deferred from: code review of 7-5-8-renderer-validator-consistency (2026-05-14)

- **D1 — ✅ CLOSED 2026-06-05.** `_collapse_newlines` now uses a single regex `(?:\r\n|[\r\n\v\f\x85  ])+` covering every Unicode line-break in Python's `str.splitlines()`. 164 telegram_sink tests pass. *Original:* NEL/VT/FF not handled.
- **D2 — ✅ CLOSED 2026-06-05.** Single regex pass means all adjacent line-break sequences produce exactly one space. *Original:* sequential `.replace()` produced multi-space for mixed adjacent separators.
- **D3 — ✅ WONTDO.** Model already has the same pattern; no divergent validation behavior. Will add coverage if behavior diverges.
- **D4 — ✅ WONTDO.** Standard Pydantic pattern; no evidence of real issues. Whitespace-stripping validator can be added if semantic emptiness surfaces.

## Deferred from: code review of story-9.6 (2026-05-17)

- D1 — 🔄 GATED-P0. Child-env allowlist needed (mirrors mcp_clients pattern). Security-sensitive — requires dedicated story + diff-audit.
- D2 — 🔄 GATED-ARCH. Module resolution path issue in integration tests; separate investigation needed.
- D3 — 🔄 GATED-OPS. Operator-configurable behavior; needs config-gated opt-in decision.
- D4 — 🔄 GATED-P0. Same class as D1; child-env allowlist needed for OMC runner.

## Deferred from: code review of story-9.7 (2026-05-18)

- D5 — 🔄 GATED-ARCH. Standing backlog item; needs performance data + ADR. Phase 3 adds no new writers.
- [x] **D6 — PH-A7c synthetic trace_id forensics column** RESOLVED 2026-05-19 — migration 0006 + backfill helper labeled provenance + materializer wires `envelope.extensions["trace_id_synthetic_source"]` → `events.trace_id_synthetic_source`. `/trace` exposes a top-level `trace_id_synthetic_source` field replacing the dropped pass-2 `X-Trace-Has-Synthetic` heuristic. Labels: `"migrator-v1_0_0-to-v1_0_1"`, `"subscriber-pre110-replay"`, `"failure-detection-system-initiated"`.
- [x] **D7 — `/trace` response shape vs canonical envelope** RESOLVED 2026-05-19 — migration 0006 added `events.extensions` (Text, nullable) column + materializer persists `envelope.extensions` as canonical JSON via `events.canonical.to_canonical_payload_json` + `/trace` route populates the `extensions` field from `row.extensions` (NULL → `{}` for back-compat with pre-9.8 rows and empty-extensions envelopes).
- D8 — ✅ NIT. Defer until spawn-site count exceeds 10 or line-number drift breaks CI. Current line-based allowlist is functional.

## Deferred from: code review of story 11-3-3 (2026-05-25)

- AC1 — ✅ WONTDO. Hang already fixed; recipe has no residual value.
- AC5 — ✅ NIT. CI speed optimization; no correctness impact.

## Deferred from: code review of story 11-3-6 (2026-05-28)

- H7f — 🔄 GATED-ARCH. Workaround landed (audit disabled). Deep fix needs nested-context detection or non-stdio transport; ADR required.

## Deferred from: code review of story 11-3-10 (2026-06-01)

- Unbounded MCP probes — 🔄 GATED-P0. start_period mitigates; real closure touches mcp_clients.py (a0ca050 P0 area). Needs AC1 Linux-nightly evidence + mandatory P0 diff-audit.
- Stale /tmp/ready — 🔄 GATED-OPS. Docker/deployment config; hardening via tmpfs or unlink-on-startup needs operator decision.

## Phase-3 G-FN readiness triage (Story 14.4, 2026-06-04)

Disposition of the G-FN readiness gaps from the Phase-3 scoping brief, decided at the Epic-14 warm-up gate (per ADR-0009 acceptance criterion "deferred-work backlog reviewed").

- G-FN-1 — 🔄 GATED-ARCH. Same as D5 above; standing backlog. Phase 3 adds no new writers.
- G-FN-2 — 🔄 GATED-ARCH. Same as H7f above; pulled into Epic 15 as ADR-0010 precondition.
- G-FN-3 — 🔄 GATED-P0. Same as Unbounded MCP probes above; pulled into Epic 15 under P0 diff-audit.

— *Story 14.4 (G-FN triage), R2d2 + Claude, 2026-06-04.*

## Deferred from: code review of story 15-2a tier-declaration gate hardening (2026-06-04)

- P2 — 🔄 GATED-ARCH. Discovery architecture needs ADR-0010 follow-up decision; broaden glob vs explicit registry.

## Deferred from: security + code review of story 15.3 git read tools (2026-06-04)

- 15.4 — 🔄 GATED-P0. Security: repo-local-config RCE vectors re-opened by content diff and push tools. MUST address before 15.4 ships.
- **[P1 — `run_git` unbounded output buffer] ✅ CLOSED 2026-06-05.** `run_git` no longer uses `proc.communicate()` (unbounded memory): it now drains stdout+stderr CONCURRENTLY via two incremental capped reader tasks and, once a stream crosses `output_cap` (new param, default 16 MiB = `_GIT_OUTPUT_CAP`), kills+reaps the subprocess and raises `GitOutputTooLarge` (the memory-pressure sibling of `GitTimeout`). Concurrent drain preserves the no-pipe-deadlock property `communicate()` gave; a `BaseException` handler (CancelledError/KeyboardInterrupt/SystemExit) guarantees child + reader-task cleanup on every path (Epic-11 L7 discipline). 3 new tests (cap-exceeded kill+reap with sandbox-still-usable, under-cap success, exclusive-boundary at exactly cap bytes); full git-mcp read+mutating suites green (28 passed), ruff clean. Independent code-review verdict SAFE-TO-COMMIT (0 crit/high; the MEDIUM BaseException-leak it flagged was then closed in this same change). *Original:* only the 30s timeout bounded `run_git`; a pathological repo (or future content-exposing tool) could stream unbounded output that completes within the wall-clock timeout.
- **[P1 — `git.diff` rename detection] ✅ CLOSED 2026-06-05.** `_parse_numstat` now consumes the origin NUL record and surfaces the destination as `path` (mirrors `_parse_status`'s rename handling), so a rename no longer records `path=""`. Schema unchanged (origin not surfaced — consistent with `_parse_status`); file-count path unaffected (rename = one record in, one out). 4 deterministic pure-parser unit tests added (text rename, binary rename, mixed modify+rename, modify-only baseline) in `test_read_tools.py`; full git-mcp read+mutating suites green (25 passed), ruff clean. *Original:* for a rename, `git diff --numstat -z` emits an empty path field + the old/new names as two following NUL records; the parser yielded `path=""` and skipped them.
- **[nit — detached HEAD] ✅ CLOSED 2026-06-05.** `_parse_branch` now filters the `(HEAD detached at <sha>)` pseudo-ref from both `branches` and `current` (reports `current=None`). Contract: detached HEAD = no branch. 3 pure-parser tests added (normal listing, detached with branches, detached without); full git-mcp suite green (85 passed), ruff clean. *Original:* reported the pseudo-ref as `current` instead of `None`.

— *Story 15.3 (git read tools), security review (P0 repo-local-config RCE fixed + locked by regression test) + code review, R2d2 + Claude, 2026-06-04.*

## Deferred from: story 16.4 github write tools (2026-06-04)

- 16.5/16.6 — 🔄 GATED-OPS. simulate=True default; needs real GitHub credentials + config-gated explicit opt-in to flip.
- P2 — 🔄 GATED-OPS. Scoped token repo scope should derive from config, not per-call args. Authority model decision needed.

— *Story 16.4 (github write tools + github.* events), R2d2 + Claude, 2026-06-04.*

## Deferred from: story 16.5 scoped-credential / G-SEC-2 (security review, 2026-06-04)

Story 16.5 closes the **MCP-subprocess half** of G-SEC-2 (the broad `GITHUB_TOKEN` no longer reaches any MCP stdio child — github-mcp authenticates with the repo-scoped `GITHUB_MCP_SCOPED_TOKEN`). Two G-SEC-2 remainders are NOT closed by 16.5 (independent security-review, APPROVE/LOW):

- **[G-SEC-2 remaining half — claude-agent spawn still forwards the broad PAT] ✅ CLOSED 2026-06-05.** `GITHUB_TOKEN` dropped from `_CHILD_ENV_ALLOWLIST` in BOTH agent spawners — `worker-wrapper claude_code_runner.py` AND its sibling `orchestrator-adapter omc_runner.py` (the original note named only the worker, but the orchestrator spawner carried the identical `# git push` retention + TODO and was the same open exposure — fixed together to avoid a point-fix that leaves the hole open in the sibling path). **Investigation finding:** the broad PAT was *inert* — the agent's `git push` targets a local bare remote (no network, no credentials; the git-mcp Story-15.4 DECISION-1(A) sibling), the worker only DETECTS the push to drive the Tier-3 approval gate (`main.py` `needs_approval`), and nothing in either agent's push path wires the env var to git (no `credential.helper` / `GIT_ASKPASS` / token-in-URL — repo-wide search confirmed). So the correct closure was to DROP the token, not build a credential helper for a remote that isn't wired yet. Comments rewritten to record why `GITHUB_TOKEN` is intentionally absent; the parent worker-wrapper's own REST PR-draft path is unaffected (it reads `settings.github_token`, not the child env). Regression tests in both services now assert `GITHUB_TOKEN` is absent from the allowlist and dropped by `_build_child_env()` (added to the operator-secret leak-canary set). **When a real remote push is eventually wired, authenticate it with a scoped git-credential helper or a `GITHUB_MCP_SCOPED_TOKEN`-style narrow token — NEVER re-add the broad `GITHUB_TOKEN`.** With the MCP half (16.5) + both agent-spawn halves now closed, **G-SEC-2 is FULLY CLOSED.**
- Per-server env scoping — 🔄 GATED-P0. Defense-in-depth; scoped token reaches all MCP children. Enhancement, not blocker. Revisit if fleet grows.

— *Story 16.5 (scoped-credential, G-SEC-2 MCP-half), security-reviewer APPROVE/LOW, R2d2 + Claude, 2026-06-04.*
— *G-SEC-2 agent-spawn halves CLOSED (claude_code_runner + omc_runner), R2d2 + Claude, 2026-06-05. The per-server env-scoping item above stays open as a defense-in-depth enhancement.*
