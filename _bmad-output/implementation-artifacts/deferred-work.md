# Deferred Work

A running log of issues that surfaced during code review but were not fixed at the time. Each entry cites its origin (story + commit + reviewer) so a future agent or human can pick it up with context.

## Deferred from: code review of story-3.6 (2026-04-30)

- **D1 — Allowlist-vs-rate-limiter layering on telegram-gateway** ~~(RESOLVED by Story 7.5.1 — `PerActorRateLimitMiddleware` added as aiogram inner middleware)~~
- **D2 — Token-bucket charge-on-attempt semantics undocumented** (Edge Case Hunter): `self._tokens -= 1.0` runs BEFORE `await call_next(request)`. If `call_next` raises (handler exception, asyncio.CancelledError on client disconnect), the token is permanently consumed. Acceptable as deliberate DoS-protection trade-off but neither the docstring nor a test pins the choice — a future maintainer could "fix" this by moving the decrement after `call_next` and silently disable rate-limiting for failing handlers. Document the contract in `rate_limit.py:74-102` and add a regression test.
- **D3 — HEAD/OPTIONS to webhook are rate-limited** (Edge Case Hunter): The path predicate ignores method, so HEAD/OPTIONS/CORS-preflight probes consume bucket tokens before FastAPI returns 405. Either restrict to POST (`if request.method != "POST" or request.url.path != self._webhook_path`) or document the all-methods accounting choice. Low real-world impact (Telegram only POSTs).
- **D4 — Streaming responses lose `request_id` during body iteration** (Edge Case Hunter): `BaseHTTPMiddleware.dispatch` returns when `call_next` produces headers, but for `StreamingResponse` the body iterator runs AFTER. The `try/finally: unbind_contextvars` fires too early. Today registry-api has no streaming endpoints; the docstring promise "downstream stdlib log records carry the `request_id` field" silently breaks for streaming. Document or migrate to a response wrapper.
- **D5 — `JSONRenderer` crash on non-UTF-8 bytes** (Edge Case Hunter): `JSONRenderer` uses `json.dumps` which raises `TypeError` / `UnicodeDecodeError` on raw `bytes` not pre-decoded. Rare in practice (Starlette decodes via latin-1), but a single bad log call would silently drop the LogRecord. Configure `JSONRenderer(serializer=lambda o, **kw: json.dumps(o, default=repr, **kw))` when convenient.
- **D6 — `_STRUCTLOG_CONFIGURED` test-pollution risk** (Blind Hunter + Edge Case Hunter): Module-level sentinel; tests that reset it from outside would clobber `caplog` handlers. No current test does this — pin the contract via a comment "do not reset from tests; configure via the production entrypoint only" + a guard test that the sentinel is unset only by `main()`. Or use pytest's caplog directly.
- **D7 — Empty-string `X-Request-ID` / `Idempotency-Key` regen is silent** (Edge Case Hunter): A misbehaving upstream sending `X-Request-ID:\r\n` (empty) regenerates silently with no warning log — the `if incoming:` guard at `middleware.py:80` skips empty strings. Operator visibility lost. Add `_log.debug(...)` at the empty-string branch (apply to both `RequestIdMiddleware` and `IdempotencyKeyMiddleware`).
- **D8 — `unbind_contextvars` clobber** (Edge Case Hunter): `unbind_contextvars("request_id")` does unconditional reset, not save-and-restore. A future middleware/handler that binds `request_id` to a different value (e.g. for a child task) would have its bind erased. No current binders. Pin the invariant via comment + regression test when the second binder lands. Idiomatic save/restore (`bound_contextvars` context manager) avoids the issue entirely.
- **D9 — Multiple `Idempotency-Key` / `X-Request-ID` headers ignored silently** (Edge Case Hunter): `request.headers.get(...)` returns the first if a client sends duplicates. RFC 7230 §3.2.2 forbids duplicate single-value headers but clients violate it. Either log a warning when `getlist(...)` length > 1, or accept first-wins with a comment.
- **D10 — `Retry-After: 1` lies under Phase 2 slow refill** ~~(RESOLVED by Story 7.5.3 — dynamic `math.ceil()` computation)~~
- **D11 — `model_dump(exclude_none=True)` may drop legitimate `None` for Story 3.7** (Blind Hunter): The behavior change from `model_dump()` to `model_dump(exclude_none=True)` is a subtle contract shift. Story 3.7's Telegram renderer is the consumer of `extensions` and may have legitimate need to send `null` values. Document the contract before 3.7 lands.
- **D12 — `_ManualClock` test fixture protocol drift risk** (Edge Case Hunter): Lacks the full `events.clock.Clock` Protocol surface; `# type: ignore[arg-type]` masks future Protocol additions (e.g. `time_ns()`). Use a real `FrozenClock`/`TickingClock` test double or extend the Protocol with a test seam.
- **D13 — Spec text vs API mismatch: `now_monotonic_ns()` vs `monotonic_ns()`** (Acceptance Auditor): Story 3.6 spec text in AC-5 + Task 5 says `clock.now_monotonic_ns()`; the actual `events.clock.Clock` Protocol method is `monotonic_ns()`. Implementation correctly uses the real method. Patch the spec text in a follow-up doc-PR (not a code defect).

## Deferred from: code review of 3-11-blocker-notification-template (2026-05-01)

- D1 — `_assemble_blocker_sections` boolean-bag signature → drop-set refactor (mirrors Story 3.10 `_assemble_approval_sections` pattern; out-of-scope for review pass).
- D2 — Footer hardcoded English; no i18n hook (whole project is English-only Phase 1; i18n is out of MVP scope).
- D3 — `_extract_task_id` `<unknown>` sentinel; uniform fix across renderers belongs in a separate cross-cutting story.
- D4 — Sprint-status state-machine skipped intermediate states (process drift; Story 3.10 M16 carry-forward — same defer direction).
- D5 — `task_id` whitespace `pattern=` validator ~~(RESOLVED by Story 7.5.8 — pattern applied to all 18 task_id fields)~~
- D6 — Module constants lack `Final` annotation (project convention follows `_APPROVAL_*` without `Final`; inconsistency would create style drift).
- D7 — Header-overflow fail-fast (over-engineered; Step 5 emergency tier already handles pathological task_ids after H2 + H5 fixes).

## Deferred from: code review of 3-12-completion-summary-template (2026-05-01)

- D1 — `task_id` regex `pattern=` absent across all `Task*Payload` models ~~(RESOLVED by Story 7.5.8 — pattern applied to all 18 fields)~~
- D2 — `_collapse_newlines` doesn't strip U+2028 LINE SEPARATOR / U+2029 PARAGRAPH SEPARATOR ~~(RESOLVED by Story 7.5.8 — Unicode fix applied)~~
- D3 — `pr_branch` accepts characters git ref-name disallows ~~(RESOLVED by Story 7.5.8 — pattern + @field_validator)~~
- D4 — `pr_url` already-escaped `&amp;amp;` double-escape — operator-supplied input-sanitization concern; defer.
- D5 — `_COMPLETED_REGISTERED` global mutable flag pattern — consistent with Story 3.10 M8 / 3.11 H11 across 4+ test helpers; consolidation refactor deferred.
- D6 — `Random(312)` fixed seed — consistent with 3.10/3.11 (`Random(311)`, `Random(789)`); pytest single-threaded default.
- D7 — `isinstance(payload, ...)` docstring clarity — docs sweep across 3 renderers.

## Deferred from: code review of 3-13-self-recovered-summary-template (2026-05-03)

- D1 — `import structlog.testing` inside test body inconsistent with project convention — pre-existing pattern in 7 test functions across multiple stories; module-level import in `test_middleware.py` but inline in `test_telegram_sink.py`. Consider promoting or documenting the intentional choice.
- D2 — `_build_diff_stats_line` renders "1 files changed" (no singular form) — pre-existing UX polish gap in completion renderer (`telegram_sink.py:1148`). Low impact.
- D3 — `assert` in `_build_pr_line` stripped under `python -O` — pre-existing defensive pattern in completion renderer (`telegram_sink.py:1151`). Project likely never runs under `-O`.
- D4 — `_build_step_boundary_payload` linear scan could be binary search — pre-existing test utility (`test_telegram_sink.py:2088`). Acceptable at cap=1900.
- D5 — Missing test for `pr_url` containing only newlines — pre-existing test coverage gap in completion renderer. Symmetrical to the `pr_branch` newline test already present.

## Deferred from: code review of 3-5-4-pre-existing-test-failure-resolution (2026-05-04)

- D1 — `append_envelope` workaround in `_crash_events.py:191-218` now redundant with `_serialize_payload` field serializer — defensive rebuild produces identical output but carries stale comments describing the now-fixed Pydantic bug. Cleanup in a follow-up to simplify `append_envelope` to `line = to_canonical_json(env) + b"\n"`.
- D2 — Side-effect import `import registry_state.domain.event_types` in `_crash_events.py:50` fragile to refactoring — if registrations move behind a function call, the import silently stops populating the registry. Consider adding an assertion after the import.
- D3 — Missing unit test for `to_canonical_json` with `BaseModel` payload in `test_canonical.py` — all 15 existing tests use dict payloads; the `_serialize_payload` serializer is verified via integration tests but lacks a dedicated unit test.

## Deferred from: code review of 5-3-worktree-lock-acquisition (2026-05-07)

- D1 — `release_lock` TOCTOU between `read_lock` and `unlink` (`worktree_lock.py:119-133`) — `contextlib.suppress(FileNotFoundError)` handles the missing-file race. Root cause is the same TOCTOU pattern as in `acquire_lock`; fixing acquire's TOCTOU (decision needed) would address both.
- D2 — `read_lock` returns `None` for corrupt lock, allowing acquisition (`worktree_lock.py:49-51`) — Requires filesystem-level corruption that bypasses `os.replace` atomicity. Very unlikely; corrupt lock would require operator intervention regardless.
- D3 — Lock not retained on blocked (AC-2) — AC-2 behavior belongs in the task state machine (future Story 5.12+). `finish_session` is only called on session end (SIGTERM/completion), not task blocked state. Out of scope for Story 5.3.

## Deferred from: code review of 5-17a-resume-after-approval-state-machine (2026-05-09)

- D1 — Transition log grows unboundedly for long-lived FSM instances (`lifecycle.py:136`) — by design for finite worker lifecycle (10-20 transitions per task). If FSM is reused across tasks, add `clear_log()` or cap size.
- D2 — AC-2 "rejection is audited" — invalid transitions are NOT appended to internal log (`lifecycle.py:144-145`) — exception carries `current_state` + `event` attributes (auditable data), but FSM itself doesn't record it. Acceptable for pure domain module; Story 5.17b runtime layer should log the exception.
- D3 — PAUSED cannot transition to AWAITING_APPROVAL directly, AWAITING_APPROVAL cannot receive TASK_PAUSED — by design: separate flow paths (pause/unpark vs. approval gate) that converge at RESUMED. If 5.17b runtime needs cross-path transitions, add them then.

## Deferred from: code review of 5-18-journey-1-integration-test (2026-05-09)

- D1 — ~62 lines duplicated code between auto_approval_stub and scripted_worker_stub (`_read_new_lines`, `_connect_mcp`, `_install_signal_handlers`, `main`) — intentional fixture independence per spec design; extracting shared code would cross fixture boundaries.
- D2 — Incomplete JSONL line causes offset stall in `_read_new_lines` — pre-existing in scripted_worker_stub; only triggered by log rotation during test run.
- D3 — Worker stub doesn't gate on `approval.granted` before emitting post-approval events — by-design Phase 1 per spec scope boundary ("Do NOT add a journey_1 scenario that gates on approval"). The 0.5s inter-event delay creates the timing window. Phase 2 (Epic 6) will add real approval gating.

## Deferred from: code review of 6-14-tier3-negative-test (2026-05-11)

- D1 — Event loop leak if `_build_harness` crashes before returning — same pre-existing pattern as test_license_scan.py and test_decision_interleaving.py. The manual `asyncio.new_event_loop()` + `asyncio.set_event_loop()` pattern doesn't restore the previous loop on failure within `_build_harness`. Consistent with existing codebase; fix should be applied to all 3 test files together.
- D2 — Read bypass test only covers GET, not HEAD/OPTIONS — all three methods use the same `_MUTATING_METHODS` check in middleware. GET is the primary read method and sufficient for integration coverage.
- D3 — No test for unmapped mutating routes (Phase-1 default-open path) — the middleware allows unmapped routes through. This is a separate concern from Tier-3 negative testing; could be a dedicated story.

## Deferred from: code review of 7-7-worktree-lock-blocker-persistence (2026-05-12)

- D1 — `_close_active_session_for_task` only closes ONE session when multiples may exist ~~(RESOLVED by Story 7.5.2 — bulk UPDATE replaces single-session close)~~
- D2 — Missing compound index on `(sessions.task_id, sessions.status)` ~~(RESOLVED by Story 7.5.2 — Alembic migration 0003)~~
- D3 — ORM attribute mutation vs bulk UPDATE in `_close_active_session_for_task` ~~(RESOLVED by Story 7.5.2 — switched to bulk UPDATE)~~

## Deferred from: code review of 7-8-self-recovered-summary (2026-05-12)

- D1 — No "overnight" time-of-day filter in `detect_overnight_restart` (Acceptance Auditor) — spec says "timestamped overnight" but function detects ANY restart pair regardless of time. The word "overnight" describes the task context (the task ran overnight), not a filter condition. Adding a time-of-day check would narrow the feature incorrectly (midday restarts also deserve visibility).
- D2 — ASC+limit=1000 may truncate restart pair for long-running tasks ~~(MITIGATED by Story 7.5.6 — `after` cursor param enables pagination without truncation)~~
- D3 — No deduplication for daemon restart replay (Edge Case Hunter) — if clawhip-daemon restarts and replays the JSONL event log, it could send duplicate self-recovered messages. Architectural concern beyond story scope; best-effort synthesis is acceptable for now.

## Deferred from: code review of 7-9-journey-3-integration-test (2026-05-12)

(All findings were fixed during the review pass — no deferred items.)

## Deferred from: code review of 7-5-2-session-bulk-close-and-index (2026-05-13)

- **D1 — `assert isinstance` stripped in Python -O mode** (Blind Hunter, handlers.py:296,343): All handlers use `assert isinstance(payload, ...)` which is stripped by `python -O`. Pre-existing pattern not introduced by this story. Consider a runtime guard that survives optimization in a future cleanup pass.

## Deferred from: code review of 7-5-5-worktree-lock-release-touctou (2026-05-14)

- **D1 — No coverage for PermissionError or other OSError subclasses on unlink** (Edge Case Hunter, worktree_lock.py:139-144): The `try/except FileNotFoundError` block only catches FNFE. Any other `OSError` (e.g., `PermissionError`) will propagate up. Pre-existing design choice; the caller (`main.py:237-245`) has a broad `except Exception` wrapper. Consider documenting whether PermissionError is expected or widening the catch.
- **D2 — release_lock session_id mismatch does not check for missing key in corrupt lock** (Edge Case Hunter, worktree_lock.py:130): `existing.get("session_id")` returns `None` when the lock file is valid JSON but missing the `session_id` key. A corrupt/partial lock file will never be released by any session. Pre-existing, by design ("stale lock recovery is a manual procedure"). Consider documenting this edge case.

## Deferred from: code review of 7-5-6-events-endpoint-truncation-and-trace-id (2026-05-14)

- **D1 — Missing DB index for `after` cursor filter** (Edge Case Hunter, events.py:80): `ix_events_task_id_emitted_at` covers `(task_id, emitted_at)` but not `emitted_at_monotonic_ns`. The `after` filter causes a post-filter scan. Low urgency at current scale; requires Alembic migration. Add `ix_events_task_id_mono_ns` in Phase 2.
- **D2 — `since` uses inclusive `>=` creating potential duplicates on re-poll** (Blind Hunter, events.py:78): If two events share the same `emitted_at` timestamp and a client re-polls with `since=<timestamp>`, both events are returned again. Pre-existing behavior; changing to strict `>` would break backward compat. Documented in endpoint docstring.
- **D3 — No auth check on events endpoint** (Blind Hunter, events.py:48-51): Endpoint is unauthenticated. By design for CLI use; auth is handled at the infrastructure layer (API gateway). Defer to infrastructure hardening.
- **D4 — `trace_id: None` in wire contract** (Blind Hunter, events.py:43): ~~Hardcoded None with Phase 2 dependency documented. Not a defect — ORM column + migration + materializer required. Tracked in AC-2.~~ **RESOLVED by Phase 2 Epic 9 (α `trace_id` propagation kernel) — Story 9.7 ships schema_version bump 1.0.0 → 1.1.0 + `events.trace_id` column + index + migrator backfill. See ADR-0003 + (forthcoming) ADR-0004.**
- **D5 — `_TASK_ID_PATTERN` coupling between routes** (Blind Hunter, events.py:24): Pattern shared between `events.py` and `tasks.py`. Pre-existing pattern; extract to shared module if it changes again.
- **D6 — `list[dict]` return type lacks Pydantic response model** (Blind Hunter, events.py:61): No `response_model` annotation on the endpoint. Pre-existing pattern; adding a model would break the bare-array wire contract unless using `response_model=list[EventEnvelopeResponse]`.

## Deferred from: code review of 7-5-7-integration-test-harness-decision (2026-05-14)

- **D1 — Journey 1 not migrated to use shared `_compose_helpers.py`** (Code Reviewer): AC-2 says "at least 2 test files" and 2 were done (j3, j6). Module docstring names j1 as source but j1 still has local copies. Migrate in next test-harness pass.
- **D2 — `_wait_for_socket`, `_read_jsonl_envelopes`, `_poll_for_event`, `_wait_for_container_exit` still copy-pasted across j3/j6** (Code Reviewer): ADR decision #1 scoped extraction to compose helpers. These event-polling helpers are candidates for future extraction to `_compose_helpers.py` or a new `_event_helpers.py`.
- **D3 — `wait_for_all_healthy` silently loops on `docker compose ps` failure** (Code Reviewer, _compose_helpers.py:103): Pre-existing behavior preserved by extraction. Accumulate stderr on failure for diagnostic value.
- **D4 — `wait_for_all_healthy` doesn't distinguish unhealthy vs not-started** (Code Reviewer, _compose_helpers.py:130): Pre-existing behavior. Include per-service health summary in timeout message.
- **D5 — ADR-0002 adds `Rationale` section not in ADR-0001** (Code Reviewer): Style divergence. ADR-0001 has no Rationale section. Low priority — merge into Context or update ADR-0001 convention.

## Deferred from: code review of 7-5-8-renderer-validator-consistency (2026-05-14)

- **D1 — `_collapse_newlines` doesn't handle NEL (U+0085), VT (U+000B), FF (U+000C)** (Blind Hunter + Edge Case Hunter, telegram_sink.py): Spec (AC-3) specifically names U+2028/U+2029. These additional Unicode line breaks recognized by Python's `str.splitlines()` were not in scope. Consider using `re.sub(r"[\r\n  \x0b\x0c]+", " ", text)` for comprehensive coverage in a future pass.
- **D2 — Sequential `.replace()` produces multi-space for consecutive mixed separators** (Blind Hunter, telegram_sink.py): `_collapse_newlines` replaces each separator independently, so `\n ` produces two spaces. Consistent with pre-existing ASCII newline behavior. Consider normalizing all to one separator first, then collapsing, in a future pass.
- **D3 — `TaskExecutionResumedPayload` not covered in validator tests** (Acceptance Auditor, test_payload_validators.py): This model already had `pattern=_TASK_ID_PATTERN` before this story. The test file covers the 14 explicitly-named models in scope. Add coverage if model-specific validation behavior diverges.
- **D4 — `hint` min_length=1 allows whitespace-only strings** (Blind Hunter + Edge Case Hunter, payloads.py:60): Standard Pydantic pattern. No evidence of real issues. Consider a whitespace-stripping validator if semantic emptiness becomes a concern.

## Deferred from: code review of story-9.6 (2026-05-17)

- **D1 — `dict(os.environ)` parent secrets leak to Claude Code subprocess** (Blind Hunter, claude_code_runner.py:116): Pre-existing pattern in `_spawn`; not introduced by 9.6 (only marginally worsened by adding `OMB_TRACE_ID` to the env dict). Subprocess inherits all parent env vars including unrelated API keys / cloud creds. Warrants a separate hardening story to build child env from an explicit allowlist (PATH, HOME, ANTHROPIC_API_KEY, OMB_TRACE_ID, locale).
- **D2 — 5 pre-existing integration tests fail with `_build_scripted_worker` ModuleNotFoundError** (Edge Case Hunter, tests/integration/test_journey_{1,3,6}_*.py + tests/separability/test_s{1,2}_*.py): Pre-existing per Story 9.5 closure. Un-blocking them is a separate ticket. NB: these tests would have surfaced H2 (--trace-id flag rejection by real claude binary) and Q1 (spawner-side WORKER_TRACE_ID gap) if they were green.
- **D3 — opt-in `WORKER_TRACE_ID_STRICT=1` for fail-loud production mode** (Edge Case Hunter, app/config.py): Current silent mint-on-invalid is debatable for a correlation token; should be configurable per operator. Defer to Story 9.7 or a separate hardening story.
- **D4 — `dict(os.environ)` parent secrets leak to OMC subprocess in orchestrator-adapter** (pass-3 TM1, omc_runner.py:_spawn): Same pattern as worker-wrapper D1 — ``OMCRunner._spawn`` builds child env by copying ``os.environ`` then layering ``OMB_TRACE_ID``.  Inherits unrelated secrets (API keys, cloud creds) into the Node subprocess.  Warrants a child-env allowlist (PATH, HOME, NODE_PATH, locale, OMB_TRACE_ID) hardening story analogous to D1.

## Deferred from: code review of story-9.7 (2026-05-18)

- **D5 — Story 2.6.X — re-evaluate cursor-filter design (decoupled from Story 9.7)** (pass-1 PH-B11/E3): Story 9.7's executor removed the `emitted_at_monotonic_ns > cursor_ns` filter from subscriber startup replay and the tail loop on the grounds that monotonic clocks are process-local and not globally comparable across registry-api/workers/orchestrator/capture helpers. Pass-1 reverted this change as out-of-scope for 9.7 — it bundled a Story 2.6 architectural decision into a schema-bump story without separate ADR analysis, and removed the only mechanism preventing snapshot-covered re-application of events (relying entirely on `apply_many`'s event-id dedup). Open a dedicated story to: (a) audit the monotonic-clock claim across all writers; (b) measure startup-replay performance with vs without the cursor-filter on a 100K+ event corpus; (c) decide whether to keep, refine, or remove the filter with proper ADR. Until that story lands, the cursor-filter stays in place. **TL-A6 reconcile (pass-2 2026-05-18):** The Dev Agent Record Q2 entry says "Restored original `compute_replay_cursor` filter logic" but `main.py:232` calls `compute_events_max_cursor` — that is the actual production function name. The `compute_replay_cursor` label in this D5 entry was a transcription error; both refer to the same helper that returns `MAX(events.emitted_at_monotonic_ns)` from the events table. Correct reading: the cursor-filter uses `compute_events_max_cursor` and the D5 deferral stands unchanged.
- **D6 — PH-A7c synthetic trace_id forensics column** (pass-1 PH-A7 follow-up): Migrator back-fill currently sets `trace_id = request_id` for pre-1.1.0 envelopes without distinguishing synthetic from real. Operators inspecting `/trace` cannot tell which causal-chain links were inferred vs originated from an operator command. Add a separate `trace_id_synthetic_source: str | None` column to the `events` table (NULL for real, "migrator-v1_0_0-to-v1_0_1" for back-fill, "failure-detection-system-initiated" for system mints). Defer to a separate hardening story — non-blocking for Phase 2 correlation baseline.
