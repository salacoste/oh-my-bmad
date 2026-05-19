# Story 10.5 — Cardinality discipline + regression test

Status: **ready-for-dev**

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
- Imports: `from metrics_subscriber.app.config import MetricsSubscriberSettings`; `from metrics_subscriber.app.main import build_app`; `from metrics_subscriber.app.metrics import _EVENT_FAMILIES, _ACTOR_KINDS, _TASK_LIFECYCLE_EVENT_TYPES, _SESSION_PHASES, MetricsState`; `from events import EventEnvelope, ...`; `from asgi_lifespan import LifespanManager`.
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
2. Emit 10000 envelope pairs into the test event-log directory: `task.execution.started{task_id=T_i, token_usage=N_i}` followed by `task.completed{task_id=T_i, token_usage=M_i}` for `i in range(10000)`. Each `T_i` is a distinct UUID-like string.
3. Poll cursor until tail loop drains all 20000 envelopes (verify via `/metrics` `omb_events_appended_total{event_family="task"}` reaching 20000).
4. Scrape `/metrics`; assert canonical_timeseries_count is **≤ 51** (baseline) — every per-task gauge was cleaned up on `task.completed` per Story 10.4 D3 cleanup rule.
5. Additionally assert `_terminated_task_ids` ring-buffer state: `len(state._terminated_task_ids_set) <= 10000` (the deque maxlen — Story 10.4 P1-H3 bound).

Performance budget: this test ingests 20K envelopes through the full tail loop. Use `tmp_path` event-log dir with high-throughput JSONL writing; reasonable wall-clock budget = **30 seconds** on a typical CI runner. Mark with `@pytest.mark.slow` so it doesn't bloat the inner-loop test runs. CI runs the slow marker on the same job that runs NFR-O8 benchmark.

Self-verification:
- Test asserts `canonical_timeseries_count <= 51` (NOT <= 200 as Story 10.4 spec hinted — the cleanup is synchronous in the tail loop so by the time we scrape, all per-task gauges are gone).
- Test asserts `omb_events_appended_total{event_family="task"} == 20000` (proves we actually processed all envelopes, not just a subset).

### AC4 — Concurrent-active-tasks scenario: cardinality bounded by N + baseline

Test `test_cardinality_with_n_concurrent_active_tasks`:
1. Spin up `build_app(...)`.
2. Emit `task.execution.started` for 100 distinct `task_id` values (no `task.completed` follow-up).
3. Scrape `/metrics`; assert `canonical_timeseries_count == 51 + 100 = 151` (baseline + one `omb_task_tokens_spent` child per active task).
4. Emit `task.completed` for ALL 100 task_ids.
5. Re-scrape; assert `canonical_timeseries_count == 51` (full cleanup).

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
- **D5 — Mypy strict scope unchanged.** Story 10.5 adds tests, not source code; `tests/integration/` is already in the strict scope per Story 10.4 baseline.

## Definition of done

- All 10 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `10-5-cardinality-discipline-regression-test: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- ADR-0005 §Cardinality cross-links to `tests/integration/test_metrics_cardinality.py` as the runtime contract gate.
- `app/main.py` docstring updated with Cardinality Discipline subsection (AC8).
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, NFR-O8 p95 unchanged (Story 10.4 measurement still valid), surprises/deviations).
- No regressions in: `tests/separability/`, full pytest suite.

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
