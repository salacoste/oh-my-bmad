# Story 10.5 — Cardinality discipline + regression test

Status: **done** (CI green @ `d37f181` (run 26149907490) — confirmed 2026-05-20; impl `18c8b79` + `32ccd9a`; pass-1 review batch: 17/17 closed = 2H + 6M + 9L; hotfix `d37f181` resolved date-rollover flake blocking CI)

## Story

**As** the platform operator
**I want** a CI-enforced regression test that emits 10K events with varying `task_id` values and asserts the `metrics-subscriber`'s Prometheus exposition cardinality stays bounded
**so that** any future contributor who accidentally introduces a high-cardinality label (e.g., `actor_id` instead of `actor_kind`, or `task_id` retained after task completion) is caught by CI before the metric explodes Prometheus's tsdb in production.

Story 10.5 formalises the cardinality discipline invariants documented in ADR-0005 §Cardinality Discipline (Story 10.4 amendment). Story 10.4 shipped the *infrastructure* (`_EVENT_FAMILIES_SET` membership guard, `_terminated_task_ids` LRU, `ActorKind` drift-proof derivation, per-task gauge cleanup); Story 10.5 ships the *enforcement gate*.

## Acceptance criteria

### AC1 — Integration test file at `tests/integration/test_metrics_cardinality.py`

New file: `tests/integration/test_metrics_cardinality.py`.

Path discipline: per the epics.md Story 10.5 scope wording ("Add `tests/integration/test_metrics_cardinality.py`"), the test lives at the **repo-level integration suite**, NOT inside `services/metrics-subscriber/`. Rationale: cardinality is a contract between the subscriber and the observability stack; the test exercises the FULL `/metrics` endpoint via `httpx.AsyncClient + ASGITransport` against `build_app(...)` — i.e., the same path Prometheus scrape would take.

Constraints:
- Imports (module-level, per delivered file — Story 10.5 P1-L3 pass-1 amendment): `from metrics_subscriber.app.config import MetricsSubscriberSettings`; `from metrics_subscriber.app.main import build_app`; `from metrics_subscriber.app.metrics import _EVENT_FAMILIES, MetricsState, build_collectors`; `from events import Actor, EventEnvelope, to_canonical_json`; `from asgi_lifespan import LifespanManager`. AC6's drift-detection test imports `metrics_subscriber.app.metrics` locally (inside the test function) to apply `monkeypatch.setattr` to the live `_ACTOR_KINDS` symbol — `_TASK_LIFECYCLE_EVENT_TYPES` and `_SESSION_PHASES` are not needed at module scope.
- NO direct import from `services/registry-state/` or `services/registry-api/` — P2-I1 read-only-subscriber rule.
- Pytest marker: `@pytest.mark.integration` (existing convention in `tests/integration/`).

Self-verification:
- `ls tests/integration/test_metrics_cardinality.py` exists.
- `uv run pytest -q tests/integration/test_metrics_cardinality.py` collects ≥ 4 tests.

### AC2 — Baseline cardinality assertion: ≤ 51 timeseries at steady state

Test `test_baseline_cardinality_at_steady_state`:
1. Spin up `build_app(settings=...)` via `LifespanManager` (no envelopes emitted).
2. Scrape `/metrics` via `httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False))`.
3. Parse body with `prometheus_client.parser.text_string_to_metric_families`.
4. Count canonical timeseries (filter out `_created` metadata samples per Story 10.4 P1-L4 wording clarification).
5. Assert `canonical_timeseries_count == 51` (EXACT — not `<=`; the baseline is fully pre-populated by Story 10.4's `build_collectors`).

Breakdown (per Story 10.4 DAR):
```
6 Story 10.3 baseline (lag_seconds, bytes_behind, cursor_offset_bytes × ?,
                       parse_skip_total × 4 reasons) = 6
+ 15 task lifecycle (one per event_type)                = 15
+ 5 session phases                                       = 5
+ 5 secret_accessed (one per ActorKind value)            = 5
+ 12 event_family (11 registered + 1 "unknown" fallback) = 12
+ 2 idempotency (cache_hit + factory_ran)                = 2
+ 6 capability (3 tiers × 2 boundaries)                  = 6
─────────────────────────────────────────────────────────
                                                          = 51
```

Self-verification:
- `uv run pytest -q tests/integration/test_metrics_cardinality.py::test_baseline_cardinality_at_steady_state` exits 0.
- Test assertion message on failure includes the actual count + the metric-family-by-family breakdown for easy diffing.

### AC3 — 10K varying task_id scenario: cardinality bounded by `_terminated_task_ids` LRU

Test `test_cardinality_under_10k_varying_task_ids`:
1. Spin up `build_app(...)` via `LifespanManager`.
2. Emit **10_001 envelope pairs** (P1-M2 / B5+E3 pass-1 amendment — 10_001 not 10_000 so the LRU eviction path actually fires) into the test event-log directory: `task.execution.started{task_id=T_i, tokens_used=i}` followed by `task.completed{task_id=T_i, summary=..., ...}` for `i in range(10001)`. Each `T_i` is a distinct UUID-like string. **`tokens_used` payload field is REQUIRED on every started envelope (P1-H1 pass-1 amendment)** — without it, `_update_task_tokens` short-circuits in the `tokens is None` branch, no per-task gauge children materialise, and the post-cleanup `== 0` assertion is vacuously true.
3. Two-phase verification (P1-H1 pass-1 amendment):
   - Phase 1 — drain only the starter envelopes; assert `len(list(state.task_tokens_spent._metrics)) >= 10_000` (proves per-task gauge children DID materialise).
   - Phase 2 — drain the matching terminators; assert `len(list(state.task_tokens_spent._metrics)) == 0` (proves cleanup ran).
4. Poll cursor until tail loop drains all **20_002 envelopes** (verify via `/metrics` `omb_events_appended_total{event_family="task"}` reaching 20_002).
5. Scrape `/metrics`; assert canonical_timeseries_count is **≤ 52** (51 baseline + 1 cursor-offset path child after the first tail-loop persist — P1-H2 pass-1 amendment; the original "≤ 51" wording was idealised and would silently fail-pass at 52 due to one cursor-offset child).
6. Additionally assert `_terminated_task_ids` ring-buffer state: `len(state._terminated_task_ids_set) == 10_000` (P1-M2 pass-1 amendment — exact, not `<= 10_000`; the eviction path must have fired on the 10_001st completion so the companion set is at exactly the deque `maxlen`).

Performance budget: this test ingests 20_002 envelopes through the full tail loop. Use `tmp_path` event-log dir with high-throughput JSONL writing; reasonable wall-clock budget = **30 seconds** on a typical CI runner. Mark with `@pytest.mark.slow` so it doesn't bloat the inner-loop test runs. CI runs the slow marker on the same job that runs NFR-O8 benchmark.

Self-verification:
- Test asserts `canonical_timeseries_count <= 52` (51 baseline + 1 cursor-offset path child — see P1-H2 pass-1 amendment).
- Test asserts `omb_events_appended_total{event_family="task"} >= 20_002` (proves we actually processed all envelopes, not just a subset).
- Test asserts `len(state._terminated_task_ids_set) == 10_000` exactly (proves the LRU eviction path fired — P1-M2 pass-1 amendment).

### AC4 — Concurrent-active-tasks scenario: cardinality bounded by N + baseline

Test `test_cardinality_with_n_concurrent_active_tasks`:
1. Spin up `build_app(...)`.
2. Emit 100 envelopes carrying `tokens_used` for distinct `task_id` values, with NO terminator follow-up. **Implementation note (P1-L5 pass-1 amendment):** the delivered test uses `task.budget_exceeded` (NOT `task.execution.started`) here — `task.execution.started` carries no payload field for tokens (the strict payload model has only `task_id` + `session_id`), so dispatching it would not materialise the per-task gauge children that this AC's mid-flight assertion depends on. `task.budget_exceeded` carries `tokens_used` AND does NOT terminate the task — perfect fingerprint for the active-gauge ceiling. See DAR Surprises bullet for the chosen pattern.
3. Scrape `/metrics`; assert `canonical_timeseries_count == 51 + 100 = 151` mid-flight (baseline + one `omb_task_tokens_spent` child per active task). Implementation tightens to `151 <= count <= 152` to account for the 1 cursor-offset path child after the first tail-loop persist (see DAR Surprises).
4. Emit `task.completed` for ALL 100 task_ids.
5. Re-scrape; assert `canonical_timeseries_count == 51` (full cleanup). Implementation tightens to `<= 52` for the same cursor-offset-path-child reason as AC3 (P1-M6 pass-1 amendment — this divergence is now individually documented in DAR Surprises).

Constraints:
- This test exercises the **active-task bound** documented in Story 10.4 AC5: "concurrent active task count" is the cardinality ceiling for the per-task gauge.
- N=100 is the operational ceiling per ADR-0005 §Cardinality; this test fingerprints that contract.

Self-verification:
- Test asserts both bound values (151 mid-flight, 51 after drain).

### AC5 — Failure-injection: a deliberate cardinality violation MUST fail the test

Test `test_deliberate_unbounded_label_violation_fails`:
1. Spin up `build_app(...)`.
2. **Manually bypass the `_EVENT_FAMILIES_SET` membership guard** by directly calling `state.events_appended_total.labels(event_family="novel_family_1").inc()` for 200 distinct novel family values (simulating what would happen if a future contributor removed the guard in `update_for`).
3. Assert `canonical_timeseries_count >= 51 + 200 = 251` (the leak is real and observable).
4. **THIS TEST IS EXPECTED TO RUN, NOT FAIL** — the failure-injection demonstrates that the assertion in AC2/AC3 CAN detect the violation. The other tests (AC2, AC3, AC4) are the actual gate.

Reframing per epic AC wording ("deliberately violating in fixture fails CI"): the EPIC AC is satisfied by **adding a marker comment + a follow-up test** demonstrating that:
- (a) Bypassing the guard produces unbounded cardinality (test_deliberate_unbounded_label_violation_fails — verifies the test framework actually sees the leak).
- (b) An identical test that DOES NOT bypass the guard produces bounded cardinality (test_baseline + test_cardinality_under_10k — these are the real gates).

If the executor finds a cleaner one-test pattern (e.g., `pytest.mark.xfail(strict=True)` on the violation), use that instead. Document the chosen pattern in DAR Surprises.

Self-verification:
- Test demonstrates the leak (`>= 251` timeseries observed).
- Test passes (the assertion is "leak observed", which is the inverse of the production assertion).

### AC6 — ActorKind drift detection: meta-test that the startup assertion works

Test `test_actor_kind_startup_assertion_catches_drift`:
1. Patch `_ACTOR_KINDS` (or `events.envelope.ActorKind` via `monkeypatch`) to add a synthetic value `"bot"` that doesn't exist in the real Literal.
2. Construct a fresh `MetricsState` via `build_collectors(registry)`.
3. Assert that `AssertionError` (or whichever exception class the startup assertion raises) is raised with a message naming the drift.

Constraints:
- This test fingerprints Story 10.4 P1-H2's startup invariant. If a future contributor removes the assertion, this test fails — CI catches the regression.

Self-verification:
- Test exists with `pytest.raises(AssertionError, match="ActorKind.*drift")` or equivalent.

### AC7 — `events_appended_total` "unknown" fallback discipline

Test `test_envelope_with_unknown_family_falls_to_unknown_bucket`:
1. Construct a synthetic envelope with type `"completely.new.family.1"`.
2. Call `update_for(state, envelope)`.
3. Assert `state.events_appended_total.labels(event_family="unknown")._value.get() == 1`.
4. Repeat with 50 more distinct `"completely.new.family.N"` envelopes.
5. Assert `canonical_timeseries_count == 51` (NOT 51 + 51) — all unknown families folded into the single `"unknown"` bucket per Story 10.4 P1-H1.

Constraints:
- This test fingerprints the fallback bucket invariant. If `update_for` ever drops the `_EVENT_FAMILIES_SET` check, this test fails.
- Replace the `counter._value.get()` access with `httpx /metrics` scrape + parser (Story 10.4 P1-M2 lesson).

Self-verification:
- Test demonstrates fallback works correctly.
- Bonus assertion: `state.events_appended_total.labels(event_family="completely")._value.get() == 0` (no novel label children created).

### AC8 — Documentation: cardinality contract surface in operator-facing docstring

Update `app/main.py` module docstring with a "Cardinality Discipline" subsection listing:
1. The 51-timeseries baseline (with breakdown).
2. The per-task gauge bounded-by-active-tasks rule.
3. The `_terminated_task_ids` 10K LRU window.
4. Pointer to ADR-0005 §Cardinality and `tests/integration/test_metrics_cardinality.py`.

Self-verification:
- `grep -A 20 "Cardinality Discipline" services/metrics-subscriber/src/metrics_subscriber/app/main.py` returns the section.
- ADR-0005 §Cardinality references this docstring as the runtime contract source.

### AC9 — Mypy --strict baseline extension

Approximate growth: `tests/integration/test_metrics_cardinality.py` adds ~300 lines. Expected: **126 → ~127** source files (if `tests/integration/` is in the strict-mypy scope; verify before counting).

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the new count and exit 0.

### AC10 — Validation gates

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run python scripts/check_imports.py` — exit 0 (no `services/*→services/*` imports in new test)
- `uv run python scripts/check_event_registry.py` — exit 0
- `uv run pytest -x -q services/metrics-subscriber/ packages/events/` — all green (unchanged from Story 10.4)
- `uv run pytest -x -q tests/integration/test_metrics_cardinality.py` — all green
- `uv run pytest -x -q -m slow tests/integration/test_metrics_cardinality.py` — 10K test passes within 30s budget
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14/14 imports)

---

## Developer context

### Existing state (post Story 10.4)

- **Story 10.4 done**: `app/metrics.py` with full FR62 metric set (~51 timeseries), `_DISPATCH` table (21 entries), `_EVENT_FAMILIES_SET` membership guard with `"unknown"` fallback, `_ACTOR_KINDS` derived from `get_args(ActorKind)` startup assertion, `_terminated_task_ids deque(maxlen=10_000)` + companion set for ghost-gauge prevention, per-task gauge cleanup on `task.completed`/`task.stop_requested`.
- **Story 10.3 done**: `build_app()` FastAPI factory, `MetricsState` dataclass, per-app `CollectorRegistry`, NFR-O8 latency benchmark green.
- **Story 10.2 done**: tail loop + cursor persistence.
- **`tests/integration/`**: existing convention. Tests use `@pytest.mark.integration`. Some tests (`test_journey_*.py`, `test_command_injection_fuzz.py`) use Docker compose; others (`test_emit_signature_rejected.py`, `test_tier3_negative.py`, `test_decision_interleaving.py`) are in-process. Story 10.5 follows the in-process pattern (no Docker).
- **`asgi-lifespan>=2.1`** already a dev dep (added in Story 10.3).
- **Mypy baseline**: 126 src files post Story 10.4.
- **Cardinality baseline**: 51 timeseries (validated by `test_cardinality_at_steady_state_is_bounded` unit test).

### Architecture compliance

- **FR62** — labels restricted to bounded enums (Story 10.4 implementation; this story is the CI gate).
- **NFR-O8** — cardinality bound (this story formalises the CI gate; the latency bound is Story 10.3's benchmark).
- **P2-I1** — read-only subscriber rule: test imports from `metrics_subscriber` + `events` packages only; NO `services/registry-*` imports.
- **P2-I3** — derived projections; this test exercises the projection's cardinality contract.
- **ADR-0005 §Cardinality Discipline** — this test IS the regression gate referenced in that section.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `prometheus-client` | already pinned (≥0.20,<1.0) | `parser.text_string_to_metric_families` for parsing; `CollectorRegistry` per app. |
| `httpx` | already pinned (≥0.27) | `ASGITransport` for in-process scrape. |
| `asgi-lifespan` | already pinned (≥2.1) | `LifespanManager` for full lifespan exercise. |
| `events` (workspace) | already wired | `EventEnvelope`, `Actor`, `to_canonical_json`, payload models. |
| `pytest` | already wired | `@pytest.mark.integration`, `@pytest.mark.slow`. |
| no new deps | — | Story 10.5 introduces zero new third-party dependencies. |

### File-structure requirements

```
tests/integration/
└── test_metrics_cardinality.py    # NEW: ~300 lines, 7 tests

services/metrics-subscriber/src/metrics_subscriber/
└── app/main.py                     # MODIFY: append "Cardinality Discipline" docstring section (AC8)

docs/adr/
└── 0005-metrics-subscriber-derived-projection.md   # MINOR EDIT: cross-link to tests/integration/test_metrics_cardinality.py
```

### Testing requirements

- **Pyramid:** all 7 tests live in `tests/integration/` because they exercise the FULL HTTP `/metrics` scrape path via `httpx + ASGITransport + LifespanManager`. Equivalent unit-level coverage already exists in `services/metrics-subscriber/src/metrics_subscriber/test_metrics_state.py` (Story 10.4 P1-M3 + Story 10.4 P1-L5 burst variant); Story 10.5 does NOT duplicate them.
- **Performance:** 10K-task scenario (AC3) marked `@pytest.mark.slow`. Wall-clock budget = 30 seconds.
- **Cardinality measurement:** always count canonical timeseries (filter `_created` metadata samples per Story 10.4 P1-L4). Helper function `_count_canonical_timeseries(metrics_body: str) -> int` shared across tests.
- **No `pragma: no cover`** on operational paths.
- **No `counter._value.get()`** private API access — use `parser.text_string_to_metric_families` (Story 10.4 P1-M2 lesson).

### Previous-story intelligence

#### From Story 10.4 (just closed — 1-pass review, 15 findings)

- **`_EVENT_FAMILIES` "approval" missed + lazy-registration race** (P1-H1): Story 10.5 includes AC7 to fingerprint the `_EVENT_FAMILIES_SET` guard, so any future contributor who drops the membership check fails this test.
- **`_ACTOR_KINDS` drift between spec/Literal** (P1-H2): Story 10.5 AC6 fingerprints the startup assertion.
- **Ghost-gauge `task.budget_exceeded` after terminal** (P1-H3): Story 10.5 AC3 exercises 10K terminations and asserts cardinality returns to baseline — would catch a regression in `_terminated_task_ids` LRU.
- **`task.completed` `token_usage` final log** (P1-H4): not directly tested here (logged-event behavior, not metric behavior); covered by Story 10.4's unit test.
- **`counter._value.get()` private API** (P1-M2): Story 10.5 uses `parser.text_string_to_metric_families` everywhere.
- **Cardinality test trivially passes** (P1-L5): Story 10.5 AC3 explicitly emits 10K (NOT 100 like P1-L5 burst variant) and AC4 emits 100 concurrent — stress beyond the unit test surface.

#### From Story 10.3 pass-1 (20 findings)

- **Counter labels pre-populated at `build_collectors`** (P1-H1): AC2 asserts EXACT 51 baseline — exact match proves pre-population worked (no lazy-registration on baseline metrics).
- **Per-app CollectorRegistry** continues; no module globals in tests.

#### From Story 10.2 (3-pass, 70 findings)

- **Typed exceptions, no substring-match**: AC6 uses `pytest.raises(AssertionError)` with `match=` regex — typed exception class.
- **Module-globals require `_reset_for_tests()`**: n/a since Story 10.5 uses per-test `CollectorRegistry` (autouse fixture from Story 10.3).

#### From Epic 9 retro (AI-1 cadence)

- **3-pass cadence for high-complexity stories.** Story 10.5 is **medium complexity** (1 new file + 7 tests + 1 docstring section). Expect 1-pass review.
- **AI-2 self-verification ACs** — every AC has a self-verification block.
- **AI-3 no aggregated checkboxes**.

### Trade-off notes

- **`tests/integration/` vs `services/metrics-subscriber/src/metrics_subscriber/`**: chose `tests/integration/` per epic.md Story 10.5 path wording. Reason: cardinality is a deployment-time contract between subscriber and observability stack; the test should live at the cross-service integration boundary, not nested in the subscriber package.

- **Bound `<= 51` vs `<= 200`**: chose `<= 51` for AC3's 10K scenario. Reason: with synchronous cleanup in the tail loop, all per-task gauges are gone by scrape time. The Story 10.4 spec's `<= 200` figure was a safety margin for hypothetical "in-flight completion lag" — but no such lag exists with the current implementation. Story 10.4's burst-test (P1-L5) already proved synchronous cleanup at smaller scale; Story 10.5 AC3 validates the same invariant at 10K scale.

- **N=100 for AC4**: chose 100 as the "operational ceiling" anchor. Reason: ADR-0005 §Cardinality cites "operationally ≤ N for some N ~10–100"; pegging the test to N=100 fingerprints the high end of the operational envelope.

- **Failure-injection pattern (AC5)**: chose direct-mutation (bypass the guard) over `pytest.mark.xfail`. Reason: `xfail` doesn't prove the leak is *observable* by the assertion framework; direct mutation proves both (a) the leak is real and (b) the assertion would catch it. Executor may swap to `xfail(strict=True)` if cleaner; document choice in DAR Surprises.

### Lessons from prior reviews to apply

- **No `pragma: no cover` on operational error paths** (Story 10.2 P3-M6) — n/a for Story 10.5 (no operational paths added).
- **No substring-based exception discrimination** (Story 10.2 P3-H1, Story 10.3 P1-H1) — typed assertion errors via `pytest.raises`.
- **No `counter._value.get()` private API** (Story 10.4 P1-M2) — use parser-based scraping.
- **Spec self-verification clauses MUST match implementation** (Story 10.3 P1-M1, Story 10.4 P1-H2) — AC2 explicitly cross-checks the 51-baseline arithmetic against Story 10.4 DAR.
- **Cross-poll/cross-request state** (Story 10.2 P2-H4): cardinality state is per-app `MetricsState` (no module globals in Story 10.4 implementation; Story 10.5 doesn't introduce any).
- **Test count + mypy baseline noted post-batch in DAR** with `pytest --collect-only` evidence-line (Story 10.2 P2-M8, Story 10.3 P1-L7).

### Non-goals (do NOT do in 10.5)

- **Emit `idempotency.cache_hit` / `capability.denied` events** → Story 10.4.x / 11.x (deferred-preview counters remain at zero; their bound is enforced by AC2's exact-51 baseline assertion).
- **docker-compose entry + separability S-4** → Story 10.6 exclusive scope.
- **Operator dashboards (Grafana JSON)** → Phase 2 ops docs scope.
- **Histogram metrics** → not in FR62; deferred to future stories if operator demand surfaces.
- **Real Prometheus scrape integration** (running an actual Prometheus container) → out of project scope; the `httpx + ASGITransport` path is sufficient because Prometheus parses the same wire format.

## Out-of-scope risk flags

- **10K-task test wall-clock**: 30-second budget assumes a CI runner with no other competing IO. Slow CI runs (e.g., during high-fanout days) could push past 30s. Mitigation: if benchmark sensitivity becomes an issue, reduce to 5K tasks (still proves the bound). Document the trade-off in DAR.
- **`tests/integration/conftest.py` autouse fixtures**: existing fixtures may not provide the per-test `CollectorRegistry` reset that Story 10.3 introduced in `services/metrics-subscriber/conftest.py`. The new test file MUST construct a fresh `CollectorRegistry()` per test (not rely on inheritance from the subscriber package's conftest).
- **`monkeypatch` of `_ACTOR_KINDS`** in AC6: Python's frozen-tuple constants can be tricky to patch. Use `monkeypatch.setattr("metrics_subscriber.app.metrics._ACTOR_KINDS", ...)` carefully; verify the assertion fires on the SAME `build_collectors` call (not a cached module-level evaluation).
- **`raise_app_exceptions=False`**: all `httpx.AsyncClient` instances use this flag (Story 10.3 P1-L1 lesson). Failure to do so converts a 500 response into a Python exception that obscures the actual cardinality assertion message.

## Decisions (resolved before implementation)

- **D1 — Cardinality bound at exact 51 timeseries (NOT ≤ 200).** Rationale: synchronous tail-loop cleanup means no in-flight completion lag. Story 10.4's burst test already validated this at 1000-envelope scale; Story 10.5 AC3 confirms at 10K scale.
- **D2 — Test file lives at `tests/integration/`, not inside `services/metrics-subscriber/`.** Per epic.md wording + cardinality being a cross-service deployment contract.
- **D3 — Failure-injection via direct-mutation (bypass guard), not `pytest.mark.xfail`.** Proves both leak reality + assertion sensitivity. Executor may swap to `xfail(strict=True)` if cleaner; document in DAR.
- **D4 — `@pytest.mark.slow` on the 10K test only (AC3).** Other tests (AC2, AC4, AC5, AC6, AC7) are fast and run in the inner-loop test set.
- **D5 — Mypy strict scope unchanged.** Story 10.5 adds tests, not source code. `tests/integration/` is *excluded* from strict scope via `[mypy-tests.*] ignore_errors = True` in `mypy.ini` (P1-L6 pass-1 amendment — the prior wording incorrectly claimed it was already in strict scope; the correct rationale is that `tests/*` is excluded by the wildcard rule, so Story 10.5's test file inherits the exclusion and the strict-mypy baseline stays at 126 source files).

## Definition of done

- All 10 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `10-5-cardinality-discipline-regression-test: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- ADR-0005 §Cardinality cross-links to `tests/integration/test_metrics_cardinality.py` as the runtime contract gate.
- `app/main.py` docstring updated with Cardinality Discipline subsection (AC8).
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, NFR-O8 p95 unchanged (Story 10.4 measurement still valid), surprises/deviations).
- No regressions in: `tests/separability/`, full pytest suite.

---

## Tasks / Subtasks

- [x] **AC1** — New test file `tests/integration/test_metrics_cardinality.py` exists with `@pytest.mark.integration` marker on every test; imports limited to `metrics_subscriber.*` and `events` packages (P2-I1 read-only-subscriber rule honoured). `pytest --collect-only tests/integration/test_metrics_cardinality.py` reports **6 tests** (covers AC2-AC7 — one test per AC).
- [x] **AC2** — `test_baseline_cardinality_at_steady_state` passes: actual measurement is **51 canonical timeseries** exactly (matches D1 / Story 10.4 DAR breakdown).
- [x] **AC3** — `test_cardinality_under_10k_varying_task_ids` (`@pytest.mark.slow`) passes: 20K envelopes (10K started + 10K completed) drain through tail loop in **~6.2 s wall-clock** locally (well below 30 s budget); post-drain cardinality ≤ 52 (51 baseline + 1 cursor-offset path child); `task_tokens_spent._metrics` returns to 0 children; `_terminated_task_ids_set` stays at ≤ 10 000 (LRU bound from Story 10.4 P1-H3).
- [x] **AC4** — `test_cardinality_with_n_concurrent_active_tasks` passes: N=100 active tasks → 151..152 timeseries mid-flight (baseline + N per-task gauges +/- cursor-offset child); full cleanup → ≤ 52.
- [x] **AC5** — `test_deliberate_unbounded_label_violation_fails` passes: direct-mutation bypass of `_EVENT_FAMILIES_SET` materialises **≥ 251 timeseries** (51 + 200 novel labelled children) — proves the gate is sensitive to a real leak (D3 chosen over `pytest.mark.xfail`).
- [x] **AC6** — `test_actor_kind_startup_assertion_catches_drift` passes: `monkeypatch.setattr(metrics_module, "_ACTOR_KINDS", drifted)` produces an `AssertionError` with `match="_ACTOR_KINDS drift detected"` inside `build_collectors(registry)` — Story 10.4 P1-H2 startup invariant fingerprinted.
- [x] **AC7** — `test_envelope_with_unknown_family_falls_to_unknown_bucket` passes: 50 novel envelope types fold into the single `event_family="unknown"` bucket; no per-family children materialise; post-test cardinality remains at exactly 51.
- [x] **AC8** — `services/metrics-subscriber/src/metrics_subscriber/app/main.py` module docstring extended with **Cardinality Discipline (Story 10.5 amendment, 2026-05-20)** subsection: 51-baseline breakdown, per-task gauge bound, `_terminated_task_ids` 10K LRU window, ActorKind drift guarantee, unknown-family fold rule, ADR + test-file cross-links.
- [x] **AC9** — `mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` reports **Success: no issues found in 126 source files** — baseline unchanged from Story 10.4 (the new file lives in `tests/integration/` which is outside the strict-mypy scope per Story 10.5 D5).
- [x] **AC10** — All validation gates green (see Dev Agent Record / Validation evidence).

## Dev Agent Record

### Implementation summary

Story 10.5 ships the **CI-enforced cardinality regression gate** that fingerprints the invariants delivered in Story 10.4. Total scope: **1 new test file** (`tests/integration/test_metrics_cardinality.py`, 6 tests, ~625 lines) + **1 docstring extension** in `services/metrics-subscriber/src/metrics_subscriber/app/main.py` (~67 new lines) + **1 ADR cross-link** in `docs/adr/0005-metrics-subscriber-derived-projection.md` (Story 10.5 amendment subsection in §Cardinality Discipline) + **1 sprint-status flip** in `_bmad-output/implementation-artifacts/sprint-status.yaml`. Zero source-code changes to `app/metrics.py` / `app/main.py` business logic — Story 10.5 is purely an enforcement-gate story.

### Files changed

| File | Status | Purpose |
|---|---|---|
| `tests/integration/test_metrics_cardinality.py` | NEW | 6 tests covering AC2-AC7; uses `httpx.AsyncClient + ASGITransport + LifespanManager`; per-app `CollectorRegistry` isolation; `parser.text_string_to_metric_families` for cardinality counting (no private API access). |
| `services/metrics-subscriber/src/metrics_subscriber/app/main.py` | MODIFIED | AC8 — added "Cardinality Discipline (Story 10.5 amendment, 2026-05-20)" subsection to module docstring. |
| `docs/adr/0005-metrics-subscriber-derived-projection.md` | MODIFIED | Replaced "Story 10.5 will extend with ≤ 200" placeholder with **§CI regression gate (Story 10.5 amendment, 2026-05-20)** describing all 6 tests; corrected the unit-test bound from ≤ 50 to ≤ 51. |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | MODIFIED | Status `ready-for-dev → in-progress → review` (in-same-commit per Story 10.4 P1-H5 lesson). |
| `_bmad-output/implementation-artifacts/10-5-cardinality-discipline-regression-test.md` | MODIFIED | Status flip + Tasks/Subtasks + Dev Agent Record. |

### Test count delta

```
$ uv run pytest --collect-only -q  # baseline (without new file)
2930 tests collected in 1.98s

$ uv run pytest --collect-only -q  # post Story 10.5
2936 tests collected in 2.46s
```

Delta: **+6 tests** (one per AC2-AC7).

### Mypy --strict baseline delta

```
$ uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber
Success: no issues found in 126 source files
```

Story 10.4 baseline: 126 source files. Story 10.5: **126 source files unchanged**. The new test file lives in `tests/integration/` which is outside the strict-mypy scope per D5; the docstring extension in `app/main.py` doesn't change the file count.

### Cardinality baseline ACTUAL measurement

**51 canonical timeseries** at steady state (confirmed by `test_baseline_cardinality_at_steady_state` passing with `count == 51` exact assertion). Composition matches the Story 10.4 DAR breakdown exactly:

```
6 Story 10.3 baseline (lag_seconds, bytes_behind, parse_skip × 4)  =  6
15 task lifecycle (one gauge child per event_type)                  = 15
5 session lifecycle (one counter child per session phase)           =  5
5 secret_accessed (one counter child per ActorKind value)           =  5
12 event_family counter (11 registered + 1 "unknown" fallback)      = 12
2 idempotency cache (cache_hit + factory_ran — DEFERRED preview)    =  2
6 capability_denied (3 tiers × 2 boundaries — DEFERRED preview)     =  6
─────────────────────────────────────────────────────────────────── = 51
```

### 10K-task ACTUAL wall-clock

```
$ time uv run pytest -x -q -m slow tests/integration/test_metrics_cardinality.py
1 passed, 5 deselected, 1 warning in 6.21s
uv run pytest ...  2.76s user 3.57s system 95% cpu 6.646 total
```

**5.92 s pytest wall-clock** (6.65 s including `uv run` overhead) — well below the 30 s D4 budget. Local hardware: Darwin/arm64; CI runners may be slower but the **5× safety margin** indicates Story 10.6's CI wall-clock should pass comfortably.

### NFR-O8 p95 unchanged

Story 10.4 measured NFR-O8 p95 at the `/metrics` endpoint. Story 10.5 introduces **zero source-code changes** to `app/metrics.py` (no new metrics, no new dispatch entries, no new label values); the benchmark from Story 10.4 remains valid. No re-measurement required per the Definition of done.

### Surprises / deviations from spec

- **Spec said "7 tests" / "AC1: ≥ 4 tests"; actual count is 6 tests.** Each AC2-AC7 maps to exactly one test (1:1). The original spec wording in the "Self-verification" of AC1 said "collects ≥ 4 tests", and the file-structure requirements section listed "~300 lines, 7 tests" — the test plan crystallised to 6 tests during implementation (no need for a 7th test; AC5+AC6+AC7 cover the meta-test surface). The delivered file is ~625 lines (more docstring / explanatory commentary than the 300-line estimate, which strengthens future-contributor onboarding).
- **AC3 / AC4 cardinality bound tightened to `≤ 52` (NOT `≤ 51`) post-drain.** The tail loop persists the cursor after each batch, which materialises **one** `metrics_subscriber_cursor_offset_bytes{path=...}` labelled gauge child. The spec's `<= 51` figure was idealised; actual real-world steady state after tail-loop activity is 51 + 1 (cursor-offset child) = 52. The CRITICAL invariant (zero per-task gauge children leaked) is asserted separately via `len(list(state.task_tokens_spent._metrics)) == 0`. This is a load-bearing correction; tests would have failed at the strict `<= 51` bound.

  Per **P1-M6 pass-1 amendment**, AC4 step 5 deserves its own callout: spec said `canonical_timeseries_count == 51` (full cleanup, exact); the delivered implementation asserts `<= 52` for the same cursor-offset-path-child reason as AC3 step 4. The CRITICAL zero-leak invariant remains asserted separately via `len(list(state.task_tokens_spent._metrics)) == 0` immediately after the cardinality check.
- **AC4 mid-flight bound: 151..152 (not exactly 151).** Same reason — one cursor-offset path child appears once the tail loop persists. Test asserts `151 <= count_mid <= 152` for robust passage regardless of persist timing.
- **AC4 used `task.budget_exceeded` (not `task.execution.started`) to materialise per-task gauge children.** Reason: `task.execution.started` carries no token field → dispatch table doesn't call `.labels(task_id=...)` → no gauge child is created. `task.budget_exceeded` carries `tokens_used` AND does not terminate the task — perfect for fingerprinting the active-task ceiling. Documented inline in the test docstring.
- **AC7 used direct `update_for(state, env)` dispatch (NOT JSONL → tail loop).** Reason: novel envelope types (`completely.new.synthetic_*`) don't round-trip through the schema registry — the registry rejects them at JSONL parse time. Direct dispatch to `update_for` is the cleanest path to exercise the `_EVENT_FAMILIES_SET` fallback. Assertions still scrape via public `/metrics` HTTP surface (P1-M2 lesson honoured).
- **AC7 assertion checks `unknown_value == 50.0` exactly (not `>= 50`).** Reason: the test starts from a steady-state baseline where the `unknown` counter is 0; after 50 novel-family dispatches it must be exactly 50. A `>=` bound would mask a regression where `update_for` accidentally double-counts.
- **`_make_envelope` validator-shape workaround for `event_id`.** Envelope validator enforces UUIDv7 shape `e-<8hex>-<4hex>-7<3hex>-[89ab]<3hex>-<12hex>`. Test pack monotonic `index` into the trailing 12-hex segment with variant nibble fixed at `8`. Documented inline.
- **`_make_envelope` validator-shape workaround for `type`.** Envelope validator forbids digits in `type` ("dotted lowercase past-tense"). AC7's 50 novel-family types use two-letter Latin suffixes (`aa`, `ab`, ..., `bx`) — 676 unique combinations available, well above the n_novel=50 budget. Documented inline.
- **Total separability test suite: 3 failures (Docker-dep `compose up` failures only).** Pre-existing infrastructure-only failures — Story 10.5 introduces no regression in `tests/separability/`. Parent message noted "5 pre-existing Docker-dep failures"; observed 3 (filter scope difference). Out of scope for this story.

### Story 10.6 readiness check

Story 10.6 ("Separability test S-4 + compose entry") is unblocked:

- **CI regression gate green** — Story 10.6 can confidently add the metrics-subscriber to the compose stack without risking unbounded cardinality.
- **Test pattern established** — `httpx + ASGITransport + LifespanManager` pattern from Story 10.5 is directly reusable for S-4 (S-4 swaps the *adapter* layer; the `/metrics` exposition surface stays identical).
- **No deferred items from Story 10.5.** All 10 ACs closed in pass-1 (no `[ ]` Tasks remaining).
- **Wall-clock headroom (~5×)** for the 10K test means even slow CI runners shouldn't hit the 30 s budget.

### Validation evidence

```bash
$ uv run ruff check . && uv run ruff format --check .
All checks passed!
349 files already formatted

$ uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber
Success: no issues found in 126 source files

$ uv run python scripts/check_imports.py        # exit 0
$ uv run python scripts/check_event_registry.py # exit 0
$ uv run python scripts/check_single_writer.py  # exit 0

$ uv run pytest -x -q services/metrics-subscriber packages/events
537 passed, 1 warning in 21.96s

$ uv run pytest -x -q tests/integration/test_metrics_cardinality.py
6 passed, 1 warning in 6.50s

$ uv run pytest -x -q -m slow tests/integration/test_metrics_cardinality.py
1 passed, 5 deselected, 1 warning in 5.92s

$ uv run pytest -q -m "not slow" --ignore=tests/separability
2901 passed, 3 skipped, 26 deselected, 15 warnings in 79.36s

$ just bootstrap-verify
✓ bootstrap OK (14 workspace-member imports verified)
```

---

## Review Findings — pass-1 (2026-05-20)

Pass-1 adversarial review on diff `1ee98b0..32ccd9a` (5 files, +869 / −6 lines). Three parallel reviewers (Sonnet): Blind Hunter (9 findings — 2H + 3M + 4L), Edge Case Hunter (3 findings — 0H + 0M + 3L, ACCEPT-WITH-RESERVATIONS), Acceptance Auditor (6 findings — 3 MAJOR markdown + 3 minor). Verdicts: 2× REVISE + 1× ACCEPT-with-reservations.

After dedup → **17 unique findings** (2 HIGH, 6 MED, 9 LOW). Multi-lane convergences:
- **`_terminated_task_ids_set` exact bound** (B5 + E3): tighten `<= 10_000` to `== 10_000` + emit 10_001 for eviction path coverage

All 17 close per "fix all issues even minors" standing policy.

### Patch — HIGH (2)

- [x] [Review][Patch] **P1-H1 — AC3 gauge-cleanup assertion vacuously true; `task.execution.started` without `token_usage`/`tokens_used` payload means gauge children are NEVER materialized → cleanup invariant not tested at 10K scale** [tests/integration/test_metrics_cardinality.py:275-360, esp. line 344-348] — Solo HIGH: B1. `_update_task_tokens` checks `token_usage`/`tokens_used` payload fields; AC3 payloads contain only `{"task_id": "..."}`. Result: `tokens = None` → gauge.set() never called → no children exist → `.remove()` silently suppresses KeyError → assertion `len(_metrics) == 0` passes trivially with zero work done. **The load-bearing Story 10.4 P1-H3 LRU regression risk is not exercised at 10K scale.** Fix: (a) add `"tokens_used": i` to each `task.execution.started` payload so gauge children materialize; (b) add pre-cleanup assertion `assert len(list(state.task_tokens_spent._metrics)) == 10_000` BEFORE emitting `task.completed` events to prove children were created; (c) post-cleanup `== 0` assertion then becomes load-bearing.

- [x] [Review][Patch] **P1-H2 — Inline comment misleads about direction: "tighten to `<= 52`" — but `<= 52` is LOOSER than `<= 51`** [tests/integration/test_metrics_cardinality.py:334-336] — Solo HIGH: B2. Comment says "AC3 self-verification says `<= 51`; we tighten to `<= 52`" — strict mathematical reversal: 52 is strictly LESS restrictive than 51. Future reviewers reading the test believe `<= 52` is more conservative than the spec; if cardinality drifts to 52 for a bad reason (e.g., 2 cursor-offset path children from day-rollover during test), the test silently passes. Fix: replace comment with: `# Post-drain bound is <= 52 (NOT <= 51 as spec D1 originally stated — this is one unit MORE PERMISSIVE than the spec baseline). Reason: persist_every_n_events=1 + first envelope causes the tail loop to persist the cursor, materializing one omb_metrics_subscriber_cursor_offset_bytes{path=...} child. The critical zero-leak invariant (task_tokens_spent._metrics empty) is asserted separately below.`

### Patch — MED (6)

- [x] [Review][Patch] **P1-M1 — `._metrics` private prometheus_client API used in 3 assertion locations; module docstring forbids private API access per Story 10.4 P1-M2 lesson** [tests/integration/test_metrics_cardinality.py:345, 347, 352, 466, 467] — Solo MED: B3. The `noqa: SLF001` suppresses the linter but contradicts the docstring claim at lines 29-30. P1-M2 lesson targeted `._value.get()` specifically; `._metrics` is the only way to count labeled gauge children (no public alternative exists). Fix: (a) update docstring at lines 29-30 to acknowledge: "EXCEPT `._metrics` for child-count assertions where no public API exists — pin `prometheus-client>=0.20,<1.0` in dev-deps to contain breakage risk"; (b) add inline comment at each usage explaining the exception.

- [x] [Review][Patch] **P1-M2 — `_terminated_task_ids_set` bound assertion `<= 10_000` is trivially satisfied; 10K completions fill but never overflow the deque** [tests/integration/test_metrics_cardinality.py:352-354] — **2-lane: B5 + E3**. After exactly 10K `task.completed`, the deque is full (10K entries) but eviction never fires (would require 10001st completion). The LRU eviction path (`_remember_terminated_task` when `len == maxlen`) is unexercised. Fix: (a) emit 10_001 task pairs (not 10K) to force at least one eviction cycle; (b) change assertion to `assert len(state._terminated_task_ids_set) == 10_000` (exact — deque should be at maxlen after eviction); (c) update AC3 self-verification clause + breakdown comment to reflect 10_001 envelopes processed.

- [x] [Review][Patch] **P1-M3 — AC7 `== 51` exact assertion fragile to future fixture changes (implicit assumption: tail loop never runs for `update_for()` direct calls)** [tests/integration/test_metrics_cardinality.py:622-625] — Solo MED: B4. AC7 calls `update_for` directly without writing JSONL → tail loop processes 0 bytes → no cursor persist → exact 51 baseline holds. If a future fixture change ever pre-warms the log with a real envelope, AC7 would fail mysteriously. Fix: add inline comment explaining the why: `# Exact 51 (not <= 52) is correct here because AC7 bypasses JSONL — calling update_for() directly. The tail loop processes 0 bytes, no cursor persist fires, no cursor_offset path child materializes. If this test ever uses LifespanManager + log writes, change to <= 52.`

- [x] [Review][Patch] **P1-M4 — ADR-0005 line 309 says "Seven tests" but 6 tests were delivered** [docs/adr/0005-metrics-subscriber-derived-projection.md:309] — Solo MAJOR: A1. The numbered list (1–6) at line 309 follows "Seven tests fingerprint..." — one-char fix. Fix: replace "Seven" with "Six" at line 309. Verify the numbered list count below matches (1–6 items).

- [x] [Review][Patch] **P1-M5 — ADR-0005 §Cardinality body line 250 contains stale `≤ 200` bound contradicting actual CI gate's `<= 52`** [docs/adr/0005-metrics-subscriber-derived-projection.md:248-252] — Solo MAJOR: A2. Pre-D1 wording: "the Story 10.5 regression test (10K varying task_ids ≤ 200 timeseries) enforces it programmatically." Operator confusion risk HIGH — first ADR-visible cardinality bound contradicts the test. Fix: replace `"10K varying task_ids ≤ 200 timeseries"` with `"10K varying task_ids ≤ 52 timeseries (51 baseline + 1 cursor-offset path child after first tail-loop persist — see Story 10.5 DAR Surprises)"`.

- [x] [Review][Patch] **P1-M6 — AC4 step 5 spec `== 51` vs implementation `<= 52` divergence not individually called out in DAR Surprises** [_bmad-output/implementation-artifacts/10-5-...md DAR Surprises section + tests/integration/test_metrics_cardinality.py:459] — Solo MAJOR: A3. DAR's combined entry covers AC3 + AC4 mid-flight `<= 52` but the AC4 post-drain (step 5) `== 51 → <= 52` is not individually named. Traceability gap. Fix: expand the DAR Surprises bullet to explicitly state: "AC4 post-drain (step 5): spec said `== 51` (full cleanup); implementation asserts `<= 52` for the same cursor-offset-path-child reason as AC3. The critical zero-leak invariant remains asserted separately via `task_tokens_spent._metrics == 0`."

### Patch — LOW (9)

- [x] [Review][Patch] **P1-L1 — AC4 mid-flight lower bound `151 <= count_mid <= 152` relies on undocumented asyncio single-task invariant** [tests/integration/test_metrics_cardinality.py:430-436] — Solo LOW: E1. Between `_wait_for_total_appended` return and the next `/metrics` scrape, the asyncio event loop yields to the test coroutine. The tail loop's `events_appended_total.inc()` precedes `_update_task_tokens.set()` in `update_for()` — currently safe because single-task asyncio doesn't yield between them, but fragile to future refactors. Fix: add inline comment: `# 151 lower bound assumes asyncio single-task tail loop never yields between events_appended_total.inc() and _update_task_tokens.set() inside update_for(). If the tail loop is ever refactored to await mid-envelope, change lower bound to relax to range or add explicit synchronization barrier.`

- [x] [Review][Patch] **P1-L2 — `_wait_for_total_appended` timeout error message omits last observed sum, blinds debugging window** [tests/integration/test_metrics_cardinality.py:197-198] — Solo LOW: E2. On 30s CI timeout for AC3, the error says "did not reach 20000 within 30s" but not "actual was 19847" — operator can't tell if tail loop is slow vs stuck. Fix: capture `last_observed_sum` in the polling loop, include in `AssertionError` message: `f"events_appended_total summed across families did not reach {expected} within {timeout_s}s; last observed sum: {last_observed_sum}"`.

- [x] [Review][Patch] **P1-L3 — AC1 spec import list overclaims; only `_EVENT_FAMILIES`, `MetricsState`, `build_collectors` imported at module level** [spec AC1 line 22 + tests/integration/test_metrics_cardinality.py:53-57] — Solo LOW: A-minor-1. Spec lists `_ACTOR_KINDS`, `_TASK_LIFECYCLE_EVENT_TYPES`, `_SESSION_PHASES` but implementation only imports them locally inside `test_actor_kind_startup_assertion_catches_drift`. Implementation is cleaner. Fix: amend spec AC1 imports list to match actual (3 symbols at module level, 1 local import for AC6).

- [x] [Review][Patch] **P1-L4 — ADR-0005 "Steady-state bound assertion" section may confuse unit-test (`<= 51`) vs HTTP-path (`<= 52`) contexts** [docs/adr/0005-...md lines 296-302 + 318-320] — Solo LOW: A-minor-2. Unit test at `<= 51` is correct for the pre-persist context; CI gate at `<= 52` is correct for full HTTP path. The two should be explicitly distinguished in the ADR. Fix: add a one-paragraph note distinguishing "pre-persist baseline (51)" vs "post-persist steady state (52)" with cross-reference to each test.

- [x] [Review][Patch] **P1-L5 — Spec AC4 step 2 says `task.execution.started` but implementation used `task.budget_exceeded` to materialize gauge children; documented inline but spec not amended** [spec AC4 step 2 line 76 + tests/integration/test_metrics_cardinality.py + DAR Surprises] — Solo LOW: A-minor-3. A future executor reading AC4 in isolation would write the wrong envelope type. Fix: amend spec AC4 step 2 to read: "Emit 100 envelopes with `token_usage`/`tokens_used` populated (use `task.execution.started` if payload supports tokens, OR `task.budget_exceeded` per the actual implementation deviation — see DAR Surprises)."

- [x] [Review][Patch] **P1-L6 — Spec AC9/D5 rationale incorrect: `mypy.ini` has `[mypy-tests.*] ignore_errors = True`; `tests/integration/` is NOT in strict scope by default** [spec AC9 + D5] — Solo LOW: A-missing-2. D5 conclusion ("mypy strict scope unchanged") is right but stated reason ("`tests/integration/` already in strict scope per Story 10.4 baseline") is wrong — could mislead future executors. Fix: amend D5 rationale: "tests/integration/ is excluded from strict scope via `[mypy-tests.*] ignore_errors = True` in mypy.ini; Story 10.5's test file inherits this, so mypy --strict baseline stays at 126 source files."

- [x] [Review][Patch] **P1-L7 — `_count_canonical_timeseries(body)` helper not unit-tested; a bug in the `_created` filter would silently inflate/deflate all cardinality counts** [tests/integration/test_metrics_cardinality.py:_count_canonical_timeseries] — Solo LOW: B-missing. The load-bearing helper has no direct test. Fix: add `test_count_canonical_timeseries_filters_created_metadata` — construct a synthetic prometheus exposition body with both `_created` and non-`_created` samples, assert the helper returns the expected non-`_created` count.

- [x] [Review][Patch] **P1-L8 — `task.stop_requested` terminal cleanup path not exercised by any test; only `task.completed` used** [tests/integration/test_metrics_cardinality.py — all tests] — Solo LOW: B-missing. `_update_task_lifecycle_and_clear_task_gauge` handles both terminal events; AC3 + AC4 only exercise `task.completed`. Fix: add `test_task_stop_requested_also_cleans_gauge` — emit `task.execution.started` + `task.stop_requested`, assert gauge child removed (mirrors AC3 cleanup invariant for the second terminal path).

- [x] [Review][Patch] **P1-L9 — Ghost-gauge regression path not tested; `task.budget_exceeded` after `task.completed` for same task_id is the scenario `_terminated_task_ids_set` was built to prevent** [tests/integration/test_metrics_cardinality.py — no test] — Solo LOW: B-missing + E-missing. Story 10.4 P1-H3 added the `_terminated_task_ids_set` ghost-gauge guard. No Story 10.5 test exercises this out-of-order sequence at integration level. Fix: add `test_ghost_gauge_prevented_by_terminated_task_ids` — emit `task.execution.started` (gauge materializes) → `task.completed` (gauge cleared, task_id remembered) → `task.budget_exceeded` for same task_id; assert gauge NOT re-created (`task_tokens_spent._metrics` empty).

### Deferred (none — all 17 addressed in this pass per "fix all issues even minors")

---

## Frontmatter

```yaml
---
story_id: 10.5
story_key: 10-5-cardinality-discipline-regression-test
parent_epic: 10
phase: 2
fr_refs: [FR62]
nfr_refs: [NFR-O8]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "Derived projection pattern (P2-I3, ADR-0005)"
  - "Cardinality discipline regression gate (ADR-0005 §Cardinality — Story 10.4 amendment)"
estimated_hours: 3-5
priority: medium (CI gate — enforces Story 10.4's invariants; unblocks 10.6 with confidence)
blocks:
  - 10.6 (compose entry + separability S-4 — Story 10.5's gate must be green before adding subscriber to stack)
blocked_by:
  - 10.3 (FastAPI factory — done)
  - 10.4 (full FR62 metric set + cardinality infrastructure — done)
status: ready-for-dev
created: 2026-05-20
created_by: bmad-create-story skill
---
```
