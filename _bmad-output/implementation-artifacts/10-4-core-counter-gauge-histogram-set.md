# Story 10.4 — Core counter + gauge + histogram set

Status: **review** (CI pending @ pre-commit)

## Story

**As** the platform operator
**I want** the `metrics-subscriber` to compute the full set of derived metrics enumerated in FR62 — task lifecycle counters, session lifecycle counters, secret-access counters, event-log append rate, and per-task token-spend gauges
**so that** Prometheus dashboards + alerting rules can reason about platform health and per-task budget consumption *without* injecting parallel instrumentation into producer services (NFR-O1 preserved, ADR-0005 enforced).

Story 10.4 extends Story 10.3's `MetricsState` dataclass with the FR62 metric set, wires the tail-loop's `for each envelope` block to a dispatch table that updates the right metric based on `envelope.type`, and proves each metric works via integration tests that emit controlled events and assert the resulting Prometheus exposition.

## Acceptance criteria

### AC1 — Task lifecycle counter

| Metric | Type | Labels | Description |
|---|---|---|---|
| `omb_task_lifecycle_events_total` | Counter | `event_type` (bounded enum: 10 values) | Counts task-lifecycle envelopes observed on the JSONL tail. Labels by **event type** (not "status") because status is a derived property of the registry-state projection — the subscriber sees events, not state transitions. PromQL can sum/rate by `event_type` to recover the FR62 "by status" view. |

Bounded `event_type` enum (pre-populated in `build_collectors`):
```
task.created, task.planning.started, task.plan.ready, task.execution.started,
task.step.completed, task.blocker_raised, task.approval_requested, task.completed,
task.stop_requested, task.retry_requested, task.self_recovered,
task.execution.resumed, task.budget_exceeded, task.license_flagged,
task.summary_emitted
```

Self-verification:
- `curl /metrics | grep ^omb_task_lifecycle_events_total` shows all 15 pre-populated label combinations at 0.
- Test `test_task_lifecycle_counter_increments_per_event_type` — emit one envelope of each type, assert each label increments to 1.

### AC2 — Session lifecycle counter

| Metric | Type | Labels | Description |
|---|---|---|---|
| `omb_session_lifecycle_events_total` | Counter | `phase` (bounded enum: 5 values) | Session-lifecycle envelope counter. `phase` maps directly to FR62 enum `{started, heartbeat, finished, heartbeat_timeout, reconnecting}`. |

Bounded `phase` enum (pre-populated):
```
started, heartbeat, finished, heartbeat_timeout, reconnecting
```

Event-type-to-phase mapping (single source of truth in `app/metrics.py`):
- `session.started` → `started`
- `session.heartbeat` → `heartbeat`
- `session.finished` → `finished`
- `session.heartbeat_timeout` → `heartbeat_timeout`
- `session.reconnecting` → `reconnecting`

Self-verification:
- Test `test_session_lifecycle_counter_per_phase` — emit one envelope of each session.* type, assert per-phase label increments correctly.

### AC3 — `secret.accessed` counter by actor

| Metric | Type | Labels | Description |
|---|---|---|---|
| `omb_secret_accessed_total` | Counter | `actor_kind` (bounded enum: 3 values) | Counts `secret.accessed` events. `actor_kind` is the envelope's `actor.kind` enum: `human`, `system`, `agent`. NEVER label by `actor.id` (cardinality unbounded — human IDs are operator names, agent IDs are session UUIDs). |

Bounded `actor_kind` enum (pre-populated):
```
human, system, agent
```

Self-verification:
- Test `test_secret_accessed_counter_by_actor_kind` — emit 3 `secret.accessed` envelopes with different `actor.kind`, assert per-kind label.

### AC4 — Event-log append-rate counter

| Metric | Type | Labels | Description |
|---|---|---|---|
| `omb_events_appended_total` | Counter | `event_family` (bounded enum: 8 values) | Counts envelopes processed by the tail loop. `event_family` is the **prefix before the first dot** of `envelope.type` (e.g., `task`, `session`, `approval`, `secret`, `tier3`, `service`, `sink`, `agent`, `file`, `telegram`, `deployment`). Operator/PromQL computes the FR62 1m/5m/1h rate via `rate(omb_events_appended_total[1m])` / `[5m]` / `[1h]`. |

Bounded `event_family` enum (pre-populated):
```
task, session, approval, secret, tier3, service, sink, agent, file, telegram, deployment
```

Trade-off: the spec wording in FR62 says "events/sec windowed over 1m/5m/1h" — that's a PromQL `rate()` operation, not a metric type. Counter + PromQL is the canonical Prometheus way; histograms would over-allocate buckets. Per-family labels enable filtering without per-event-type cardinality explosion.

Self-verification:
- Test `test_events_appended_counter_per_family` — emit envelopes of 3 different families, assert per-family counter.
- PromQL `rate(omb_events_appended_total[1m])` computed against synthetic 1-second resolution — out of scope (Prometheus deployment is Story 10.6+ operator work).

### AC5 — Per-task token-spend gauge

| Metric | Type | Labels | Description |
|---|---|---|---|
| `omb_task_tokens_spent` | Gauge | `task_id` (bounded by active-task count, see cleanup rule) | Per-task token-spend snapshot. Sourced from `task.execution.started`, `task.step.completed`, `task.budget_exceeded` envelope payloads (any envelope carrying a `token_usage` / `tokens_spent` field). Cleanup: when `task.completed` / `task.stop_requested` envelope is seen for `task_id`, **remove the labeled metric** via `gauge.remove(task_id)` to bound cardinality. |

Cardinality discipline:
- Label cardinality bounded by **concurrent active task count** (operationally ≤ N for some N ~10–100 in this deployment shape; documented in ADR-0005 §cardinality).
- Story 10.5 will add a regression test that emits 10K varying task_ids and asserts cardinality ≤ 200 (accounting for in-flight completion lag).
- **Anti-pattern foreclosed:** retaining the gauge after task completion would unbounded the label set. The `task.completed` cleanup is the load-bearing invariant.

Self-verification:
- Test `test_task_tokens_spent_gauge_set_and_cleared` — emit `task.execution.started` for task A (assert gauge=N), then `task.completed` for A (assert gauge removed). Verify via `gauge._metrics` introspection OR via `curl /metrics | grep task_a` returning nothing.

### AC6 — Event-type → metric-update dispatch table

A single dispatch table in `app/metrics.py` maps `envelope.type` → updater function. Pattern (schematic):

```python
EventMetricUpdater = Callable[[MetricsState, EventEnvelope], None]

_DISPATCH: dict[str, EventMetricUpdater] = {
    "task.created": _update_task_lifecycle,
    "task.completed": _update_task_lifecycle_and_clear_task_gauge,
    ...
    "session.started": _update_session_lifecycle,
    ...
    "secret.accessed": _update_secret_accessed,
}

def update_for(state: MetricsState, envelope: EventEnvelope) -> None:
    """Look up envelope.type in _DISPATCH and apply the updater.

    Unknown types: increment ``omb_events_appended_total{event_family=<prefix>}``
    only (no per-event update; Story 10.5 cardinality test asserts no
    unknown labels leak in).
    """
    updater = _DISPATCH.get(envelope.type)
    if updater is not None:
        updater(state, envelope)
    state.events_appended.labels(event_family=envelope.type.split(".", 1)[0]).inc()
```

Constraints:
- Updater functions are pure (state-mutation only; no I/O, no logging beyond DEBUG).
- All dispatch updaters must handle missing payload fields gracefully (envelope schema_version drift): use `getattr(envelope.payload, "token_usage", None)` not `envelope.payload.token_usage`.
- Dispatch table is a `Final[dict]` constant — no runtime mutation.

Self-verification:
- `from metrics_subscriber.app.metrics import _DISPATCH; assert len(_DISPATCH) >= 18` (10 task + 5 session + 1 secret + 2 task-cleanup + 0 idempotency/capability).
- Test `test_dispatch_unknown_envelope_type_only_increments_appended_counter` — emit envelope with type `"unknown.type"`, assert no metric raises, only `events_appended_total{event_family="unknown"}` increments.

### AC7 — Hook dispatch into the tail loop

The dispatch call lives in `services/metrics-subscriber/src/metrics_subscriber/__main__.py`'s `run_subscriber` tail-loop body, between `async for envelope in reader.tail(...)` and `cursor.note_event_processed()`. Pattern:

```python
async for offset_after, envelope in reader.tail(...):
    if metrics_state is not None:
        update_for(metrics_state, envelope)
    cursor.note_event_processed()
    if cursor.maybe_persist(...):
        ...
```

Constraints:
- Story 10.2's `metrics_state: MetricsState | None = None` kwarg signature is preserved (default None for the standalone tail-loop path used by 10.2 subprocess tests).
- The dispatch call MUST NOT block the tail loop — `update_for` is sync and trivially fast (dict lookup + Counter/Gauge mutation).
- Existing Story 10.3 lag-gauge update (`record_lag` in maybe_persist) remains.

Self-verification:
- Test `test_run_subscriber_dispatches_envelopes_to_metrics_state` — spin up via `LifespanManager`, emit synthetic envelopes, assert `MetricsState` reflects them.

### AC8 — Idempotency-cache + capability-tier deny counters: DEFERRED preview-fields

FR62 enumerates two metric families that currently lack upstream events to derive from:

| FR62 Item | Required Event | Status | Resolution |
|---|---|---|---|
| Idempotency-cache hit rate | `idempotency.cache_hit` + `idempotency.factory_ran` | NOT in event registry | Deferred to Story 10.4.x or absorbed into Story 11.x — requires `registry-api` emission contract change. |
| Capability-tier deny counts | `capability.denied{tier, boundary}` | NOT in event registry (only `tier3.*` exist) | Deferred to Story 10.4.x — requires `TierEnforcementMiddleware` + MCP capability handler emission contract change. |

**Story 10.4 ships these as preview-field counters that are registered (pre-populated) but always at zero pending event-emission stories.** This keeps the FR62 metric NAMES stable for operator dashboards while honestly signaling unwired status.

| Metric | Type | Labels | Pre-populated values |
|---|---|---|---|
| `omb_idempotency_cache_total` | Counter | `outcome` (enum: `cache_hit`, `factory_ran`) | both pre-populated at 0 |
| `omb_capability_denied_total` | Counter | `tier` (enum: `tier1`, `tier2`, `tier3`), `boundary` (enum: `mcp`, `http`) | 6 combinations pre-populated at 0 |

Each metric's docstring MUST cite "DEFERRED-FROM-FR62 — pending upstream event emission, see Story 10.4.x / 11.x". A short DEFERRED section in ADR-0005 (or a new ADR-0005a appendix) records the decision.

Self-verification:
- `curl /metrics | grep ^omb_idempotency_cache_total` returns the 2 pre-populated labels at 0.
- `curl /metrics | grep ^omb_capability_denied_total` returns the 6 pre-populated labels at 0.
- Test `test_deferred_counters_pre_populated_with_zero_values`.

### AC9 — Integration test per metric

Each AC1–AC5 self-verification clause demands an integration test that:
1. Spins up `build_app(settings=...)` via `asgi-lifespan.LifespanManager`.
2. Writes controlled envelopes to the test event-log directory.
3. Allows tail loop to drain (poll until cursor offset reaches end of log).
4. Scrapes `/metrics` via `httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False))`.
5. Parses with `prometheus_client.parser.text_string_to_metric_families`.
6. Asserts metric values + label combinations exactly.

Helper fixture: `test_app_with_event_dir` in `conftest.py` — yields `(app, event_dir)` with the lifespan started.

Self-verification:
- 5 integration tests added (one per AC1–AC5) plus 1 dispatch coverage test (AC6).

### AC10 — Cardinality discipline preview (Story 10.5 prerequisite)

Story 10.5's regression test will assert cardinality bounds (emit 10K varying task_ids, assert label-set cardinality ≤ 200). Story 10.4 prepares by:

1. **All counter labels are pre-populated bounded enums** (AC1–AC4, AC8) — operator can't surprise the cardinality by emitting novel labels because dispatchers only emit known enum values.
2. **`omb_task_tokens_spent` gauge clears on task termination** (AC5 cleanup rule).
3. **Unknown envelope types** increment ONLY `omb_events_appended_total{event_family=<prefix>}` where prefix is itself an enum — no novel `event_type` leaks into a Counter label.
4. Add a unit test `test_cardinality_at_steady_state_is_bounded` — emit 1000 envelopes of mixed types (including known + unknown), call `len(list(registry.collect()))`, assert total timeseries ≤ 50 (10 task + 5 session + 3 secret + 11 family + 2 idempotency + 6 capability + 3 Story 10.3 baseline ≈ 40).

### AC11 — Settings extension (none)

Story 10.4 introduces **no new settings**. All metric registration is hard-coded against bounded enums; no env-var-driven cardinality. This is intentional per cardinality discipline (AC10).

Self-verification:
- `git diff services/metrics-subscriber/src/metrics_subscriber/app/config.py` shows zero changes.

### AC12 — Mypy --strict baseline extension

Approximate growth: `app/metrics.py` gains ~150 lines (dispatch table + updaters); two new test files. Expected: **125 → ~128** source files.

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the new count and exit 0.

### AC13 — Validation gates

- `uv run ruff check . && ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run python scripts/check_imports.py` — exit 0
- `uv run python scripts/check_event_registry.py` — exit 0 (must remain green; we are NOT registering new event types)
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run pytest -x -q services/metrics-subscriber/ packages/events/` — all green
- `uv run pytest -x -q -m slow services/metrics-subscriber/` — NFR-O8 benchmark still passes (Story 10.3 measured p95=0.94ms with 50 timeseries; Story 10.4 grows to ~50–60 timeseries — well within budget)
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14/14 imports)

---

## Developer context

### Existing state (post Story 10.3)

- **Story 10.3 done**: `app/main.py` (337 lines), `app/metrics.py` (193 lines with 3 metrics — lag_seconds, bytes_behind, parse_skip_total), `MetricsState` dataclass + `build_collectors(registry) -> MetricsState`, `record_lag(...)` updater, per-app `CollectorRegistry` pattern, NFR-O8 benchmark green at p95=0.94ms.
- **Story 10.2 done**: tail loop running in lifespan, `run_subscriber(settings, *, stop_event, metrics_state)` signature stable.
- **Event registry**: 30+ types registered via `services/registry-state/src/registry_state/domain/event_types.py`. **Story 10.4 reads — never modifies.**
- **`packages/events/src/events/payloads.py`**: all canonical payload model classes available; Story 10.4 imports for type-narrowing in updaters.
- **Mypy baseline**: 125 src files post-Story 10.3 pass-1.

### Architecture compliance

- **FR62** — 7 metric families (5 implemented + 2 deferred-preview per AC8).
- **NFR-O8** — cardinality bound preserved (AC10); benchmark gate still green.
- **NFR-O10** — derived projection only. **NO `services/*` code touched.** All event-type detection happens via `envelope.type` string comparison in `metrics-subscriber` only.
- **NFR-O1** preserved — no parallel instrumentation injected anywhere.
- **P2-I1** read-only subscriber rule — `metrics-subscriber` reads from `packages/events`, NEVER from `services/registry-api` or `services/registry-state`.
- **P2-I3** derived projections — explicitly enforced; ADR-0005 already accepted.
- **ADR-0005 §cardinality** — AC8/AC10 reference the cardinality bound documented there.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `prometheus-client` | already pinned (≥0.20,<1.0) | Use existing `Counter`, `Gauge`. `Counter.labels(...).inc()`; `Gauge.labels(...).set(val)`; `Gauge.remove(labels)` for cleanup. |
| `events` (workspace) | already wired | `EventEnvelope`, payload models from `events.payloads`. |
| no new deps | — | Story 10.4 introduces zero new third-party dependencies. |

### File-structure requirements

```
services/metrics-subscriber/src/metrics_subscriber/
├── app/
│   ├── metrics.py                 # MODIFY: extend MetricsState + add _DISPATCH + updaters
│   └── main.py                    # unchanged
├── __main__.py                    # MODIFY: hook update_for(state, envelope) into tail loop
├── conftest.py                    # MODIFY: add test_app_with_event_dir fixture (per AC9)
├── test_metrics_state.py          # MODIFY: extend MetricsState unit tests
└── test_dispatch.py               # NEW: dispatch table coverage
└── test_metrics_integration.py    # NEW: 5+ integration tests per AC1-AC5

docs/adr/0005-metrics-subscriber-derived-projection.md   # MINOR APPEND: §cardinality note + §deferred-metrics
```

### Testing requirements

- **Pyramid:** unit tests for `_DISPATCH` + each updater (synchronous, no FastAPI overhead) + integration tests per AC1–AC5 (full lifespan exercise via `asgi-lifespan.LifespanManager`).
- **Test isolation:** per-test `CollectorRegistry` (existing Story 10.3 autouse fixture).
- **Test event-log:** use `tmp_path` to create a clean JSONL file per test; emit envelopes via `events.to_canonical_json` + `f.write(line + b"\n")`.
- **No `pytest.mark.slow` adds**: all Story 10.4 integration tests are fast (lifespan + ~10 envelopes ~50 ms).
- **Cardinality steady-state test** (AC10) uses ONE labeled timeseries reading — `len(list(registry.collect()))` — as the assertion source-of-truth.

### Previous-story intelligence

#### From Story 10.3 (just closed — 1-pass review, 20 findings)

- **`Counter.labels()` lazy-registration race** (P1-H1): pre-populate ALL bounded enum values at `build_collectors` time using `.inc(0)`. Story 10.4 has many more bounded enums (15 task event_types + 5 session phases + 3 actor_kinds + 11 event_families + 2 idempotency outcomes + 6 capability combinations = ~42 pre-populated label children). Apply the same `.inc(0)` initialization pattern.
- **`on_skip` callback exception kill switch** (P1-H5): mirror pattern for dispatch updaters — wrap each `update_for(...)` call in `try/except Exception` inside `__main__.py` tail loop body. Log warning, continue. Add test `test_dispatch_updater_exception_does_not_crash_tail_loop`.
- **AC label drift between spec + impl** (P1-M1): Story 10.4's bounded enums MUST match the actual register() calls in `event_types.py`. **Verification step in development: grep all `register(...)` calls and cross-check against the spec's AC1/AC2 enum tables.**
- **Atomicity claims** (P1-H3): the `omb_task_tokens_spent.set()` is a single setter, so no split-brain. Document explicitly in `app/metrics.py` docstring.
- **Per-app `CollectorRegistry`**: established; no module globals.

#### From Story 10.2 (3-pass, 70 findings)

- **Cross-poll/cross-request state**: `MetricsState` lives on `app.state.metrics`, accessed via closure. **`_DISPATCH` is a module-level `Final[dict]`** — that IS appropriate for immutable lookup tables (NOT mutable state).
- **Typed exception classes**: no substring-match. If an updater needs exception discrimination, use isinstance.
- **`asyncio.get_running_loop()`** — n/a (Story 10.4 has no asyncio calls).

#### From Epic 9 retro (AI-1 cadence)

- **3-pass cadence for high-complexity stories.** Story 10.4 is **medium-high complexity**: 15+ metrics, dispatch table, ~6 integration tests, ADR amendment. Expect 1–2-pass review depending on first-pass density.
- **AI-2 self-verification ACs** — every AC above has a self-verification block.
- **AI-3 no aggregated checkboxes** — each review patch will get its own line.

### Trade-off notes

- **Counter labels by `event_type` (AC1) vs by "status"** (FR62 wording): chose `event_type`. Reason: status is a derived projection (registry-state computes it), not an envelope property. PromQL can map event types to status buckets via `label_replace` or `sum without (event_type)` queries. Documenting the divergence in ADR-0005 §cardinality.
- **Event-log append rate as Counter (AC4) vs Histogram**: chose Counter. Reason: Prometheus convention is `rate(counter[window])` for time-windowed rates; Histograms allocate buckets at registration and can't express "events/sec over the last 5 minutes" without `histogram_quantile` (wrong semantic).
- **Per-task gauge with cleanup (AC5) vs per-task histogram**: chose Gauge. Reason: token-spend is a current value (snapshot at last seen event), not a distribution. Cardinality controlled by `.remove()` on task termination.
- **Idempotency + capability counters DEFERRED to preview-only (AC8)**: chose deferral. Reason: emitting `idempotency.cache_hit` / `capability.denied` requires cross-service touch (`registry-api` + `TierEnforcementMiddleware`). Cross-service emission outside Story 10.4 scope (FR62 metric set is the *subscriber* contract, not the *producer* contract). Document this clearly in DAR + ADR amendment.
- **Cardinality steady-state assertion in Story 10.4 vs deferring to Story 10.5**: Story 10.4 adds a quick `assert ≤ 50 timeseries` smoke check. Full regression test (10K varying task_ids) is Story 10.5's exclusive scope.

### Lessons from prior reviews to apply

- **No `pragma: no cover` on operational error paths** (Story 10.2 P3-M6) — every updater's exception path has at least one test.
- **No substring-based exception discrimination** (Story 10.2 P3-H1) — n/a for Story 10.4 (no exception flow).
- **Test count + mypy baseline noted post-batch in DAR with `pytest --collect-only` evidence-line** (Story 10.2 P2-M8, Story 10.3 P1-L7).
- **Module-globals for warn-once flags need `_reset_for_tests()` hooks** (Story 10.3 P1-L3) — n/a since `_DISPATCH` is immutable lookup.
- **Spec self-verification clauses MUST match implementation labels** (Story 10.3 P1-M1) — verify enum table against `register()` calls before merging.
- **Counter labels pre-populated at build_collectors** (Story 10.3 P1-H1) — applies to all 5 new bounded-enum counters.
- **Honest atomicity claims** (Story 10.3 P1-H3) — multi-setter sequences must not claim atomicity.

### Non-goals (do NOT do in 10.4)

- **Emit `idempotency.cache_hit` / `idempotency.factory_ran` events from `registry-api`** → Story 10.4.x or 11.x (requires cross-service touch outside Story 10.4 scope).
- **Emit `capability.denied{tier, boundary}` events from MCP server / `TierEnforcementMiddleware`** → same as above.
- **10K-task cardinality regression test** → Story 10.5 exclusive scope.
- **docker-compose entry + separability S-4** → Story 10.6 exclusive scope.
- **Operator dashboards (Grafana JSON)** → Phase 2 ops docs scope (out of project).
- **Per-task token-spend HISTOGRAM** → not in FR62; Story 10.5+ if operator demand surfaces.

## Out-of-scope risk flags

- **Gauge cleanup race**: if a `task.completed` envelope arrives BEFORE all `task.step.completed` envelopes for the same task (out-of-order due to clock skew or replay), the gauge may be cleared then re-set. Mitigation: idempotent — re-setting a removed gauge re-creates it; the steady-state snapshot is correct. Document in `app/metrics.py` docstring; add `test_task_gauge_cleanup_then_resurrect_is_idempotent`.
- **Event-family extraction via `split(".", 1)[0]`**: any envelope type without a `.` produces `event_family=<full_type>` — strange but bounded by the producer surface. Defensive: validate that all current `register()` calls use `.` separators; document any future "flat" event type as a cardinality risk.
- **`MetricsState.update_for` runs inside `__main__.py`'s `async for` body** (sync function, GIL-bound). With ~50 metric ops per envelope at worst case + bounded-enum dispatch dict lookup, latency overhead is microseconds. NFR-O8 benchmark validates total /metrics latency budget; not a separate gate.
- **ADR-0005 amendment scope**: Story 10.4 appends §cardinality + §deferred-metrics sections. If amendment grows >100 lines, split into `0005a-cardinality-discipline.md` and link from 0005. Decision left to executor based on actual length.

## Decisions (resolved before implementation)

- **D1 — Idempotency + capability counters: preview-only, deferred event emission.** Rationale: FR62 enumerates these metrics, but the *upstream events* don't exist yet. Pre-populating the metric NAMES with zero values keeps the operator dashboard surface stable while honestly signaling unwired status. Defer event-emission work to Story 10.4.x (idempotency) and Story 11.x (capability — likely fits Epic 11's HMAC/approval scope).
- **D2 — Counter labels by `event_type` not "status".** Rationale: status is a derived projection (registry-state's job). PromQL maps event_type → status via aggregation. Documenting the divergence in ADR-0005 amendment.
- **D3 — `omb_task_tokens_spent` is a Gauge with `task_id` label, cleaned on task termination.** Rationale: bounded by active-task count (operationally low). Story 10.5's regression test asserts the bound. The alternative — token-spend as a Histogram of completed tasks — was rejected because it doesn't answer "current per-task spend" (the operator's question).
- **D4 — No new event types registered in Story 10.4.** This is a *subscriber* story. Producer-side emission is Story 10.4.x / 11.x. `scripts/check_event_registry.py` must remain green without modification.

## Definition of done

- All 13 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `10-4-core-counter-gauge-histogram-set: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- ADR-0005 amended with §cardinality + §deferred-metrics sections.
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, decisions log, surprises, NFR-O8 p95 re-measurement).
- No regressions in: `tests/separability/`, `tests/integration/`, full pytest suite.
- NFR-O8 benchmark still passes (p95 < 100ms with ~55 timeseries).

---

## Frontmatter

```yaml
---
story_id: 10.4
story_key: 10-4-core-counter-gauge-histogram-set
parent_epic: 10
phase: 2
fr_refs: [FR62]
nfr_refs: [NFR-O1, NFR-O8, NFR-O10]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "Derived projection pattern (P2-I3, ADR-0005)"
  - "Cardinality discipline (ADR-0005 amendment — Story 10.4)"
estimated_hours: 5-8
priority: high (FR62 metric set is the core delivery of Epic 10; unblocks Story 10.5 cardinality regression test)
blocks:
  - 10.5 (cardinality regression — needs the ~50 timeseries surface to test against)
  - 10.6 (compose + separability — wants the full FR62 metric set live before adding to stack)
blocked_by:
  - 10.3 (FastAPI factory + MetricsState — done)
  - 10.2 (tail loop — done)
status: review
created: 2026-05-19
created_by: bmad-create-story skill
---
```

---

## Tasks / Subtasks

- [x] **AC1** — `omb_task_lifecycle_events_total` Counter + 15-value enum pre-populated; `test_task_lifecycle_counter_increments_per_event_type` green.
- [x] **AC2** — `omb_session_lifecycle_events_total` Counter + 5-phase enum; `test_session_lifecycle_counter_per_phase` green.
- [x] **AC3** — `omb_secret_accessed_total` Counter; uses ACTUAL `ActorKind` 5-value enum (deviation documented); `test_secret_accessed_counter_by_actor_kind` green.
- [x] **AC4** — `omb_events_appended_total` Counter + 11-family enum; `test_events_appended_counter_per_family` green.
- [x] **AC5** — `omb_task_tokens_spent` Gauge + cleanup on `task.completed` / `task.stop_requested`; tests `test_task_tokens_spent_gauge_set_and_cleared`, `test_task_tokens_spent_gauge_cleared_by_stop_requested`, `test_task_gauge_cleanup_then_resurrect_is_idempotent` all green.
- [x] **AC6** — `_DISPATCH: Final[dict]` immutable lookup table (21 entries); `update_for` increments `omb_events_appended_total` family counter for every envelope + applies typed updater when registered; `test_dispatch_table_*` and `test_dispatch_unknown_envelope_type_only_increments_appended_counter` green.
- [x] **AC7** — `update_for` wired into `__main__.py` tail loop with `try/except Exception` defensive wrap (Story 10.3 P1-H5 lesson); `test_run_subscriber_dispatches_envelopes_to_metrics_state` and `test_dispatch_updater_exception_does_not_crash_tail_loop` green.
- [x] **AC8** — `omb_idempotency_cache_total` (2 outcomes) + `omb_capability_denied_total` (6 tier×boundary combos) pre-populated at zero; DEFERRED docstrings cite Story 10.4.x / 11.x; `test_deferred_counters_pre_populated_with_zero_values` green.
- [x] **AC9** — `test_app_with_event_dir` fixture in `test_metrics_integration.py`; 5 integration tests + 2 AC7 tests = 7 integration tests, all green.
- [x] **AC10** — `test_cardinality_at_steady_state_is_bounded` emits 1000 mixed envelopes, asserts canonical timeseries ≤ 50.
- [x] **AC11** — Zero changes to `services/metrics-subscriber/src/metrics_subscriber/app/config.py` (`git diff` empty).
- [x] **AC12** — `mypy --strict` baseline: 125 → 126 source files (+1 = new `test_metrics_integration.py`).
- [x] **AC13** — All validation gates green: ruff check + format, mypy --strict, check_imports, check_event_registry (D4 — no new event types), check_single_writer, pytest -m "not slow" (full suite), pytest -m slow (NFR-O8 benchmark), bootstrap-verify (14/14).

---

## Dev Agent Record

### Implementation summary

Implemented the full FR62 core metric set for the β `metrics-subscriber`:

- **5 active metrics**: `omb_task_lifecycle_events_total` (Counter, 15 event_type),
  `omb_session_lifecycle_events_total` (Counter, 5 phase),
  `omb_secret_accessed_total` (Counter, 5 actor_kind),
  `omb_events_appended_total` (Counter, 11 event_family),
  `omb_task_tokens_spent` (Gauge, task_id with cleanup).
- **2 DEFERRED preview metrics** per D1: `omb_idempotency_cache_total` (2 outcomes
  pre-populated at zero), `omb_capability_denied_total` (6 tier×boundary
  combinations pre-populated at zero). Pre-registration keeps operator
  dashboards stable during the deferral window.
- **Dispatch infrastructure**: immutable module-level `_DISPATCH: Final[dict]`
  with 21 entries (15 task + 5 session + 1 secret), 4 dispatcher functions
  (`_update_task_lifecycle`, `_update_task_lifecycle_and_clear_task_gauge`,
  `_update_task_tokens`, `_update_session_lifecycle`,
  `_update_secret_accessed`).
- **Tail-loop hook**: `update_for(state, envelope)` called inside the
  `__main__.py` tail loop body, wrapped in `try/except Exception` per
  Story 10.3 P1-H5 lesson.
- **ADR-0005 amendment**: added `## Cardinality Discipline` and
  `## Deferred Metrics` sections (~167 lines). Kept in 0005 rather than
  splitting to 0005a (the content is tightly coupled to the existing ADR
  and a split would add cross-link churn without a clear benefit).

### Lessons applied from Story 10.3 pass-1 (20 findings)

- **P1-H1 (pre-populate bounded-enum children)**: 48 counter children
  pre-populated via `.inc(0)` at `build_collectors` (15 task + 5 session + 5 actor + 11 family + 2 idempotency + 6 capability + 4 parse_skip).
- **P1-H5 (dispatch updater exception → kill switch)**: `try/except Exception`
  wrap around `update_for(...)` in `__main__.py`; warning log + continue.
  Test `test_dispatch_updater_exception_does_not_crash_tail_loop` exercises it.
- **P1-M1 (spec enum vs `register()` cross-check)**: cross-checked Story 10.4's
  bounded-enum tables against `services/registry-state/src/registry_state/
  domain/event_types.py`. Result: task lifecycle 15 ✓, session 5 ✓,
  event_family 10 actually registered (`deployment` not yet — kept in enum
  for forward-compatibility per spec AC4 11-value table).
- **P1-H3 (honest atomicity)**: dispatch updaters explicitly document
  multi-mutation non-atomicity in docstrings (counter increment + gauge
  remove is not transactional; sub-microsecond split-brain window).

### Files changed (full list, absolute paths)

- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/services/metrics-subscriber/src/metrics_subscriber/app/metrics.py` — extended `MetricsState` with 7 new fields; added module-level enum tuples, dispatch helpers, `_DISPATCH` table, `update_for()`; extended `build_collectors` registration + pre-population.
- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/services/metrics-subscriber/src/metrics_subscriber/__main__.py` — import `update_for`; add `try/except` dispatch hook in tail loop body.
- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/services/metrics-subscriber/src/metrics_subscriber/test_metrics_state.py` — added 17 Story 10.4 unit tests (AC1-AC6, AC8, AC10).
- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/services/metrics-subscriber/src/metrics_subscriber/test_metrics_integration.py` — NEW file; 7 integration tests using `LifespanManager` + `ASGITransport` (AC1-AC5 + AC7 + AC9 fixture).
- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/docs/adr/0005-metrics-subscriber-derived-projection.md` — appended §Cardinality Discipline + §Deferred Metrics sections.
- `/Users/r2d2/Documents/Code_Projects/00_mcp/oh-my-bmad/_bmad-output/implementation-artifacts/10-4-core-counter-gauge-histogram-set.md` — Status `ready-for-dev` → `review`; added Tasks/Subtasks + Dev Agent Record.

### Test count delta

`uv run pytest --collect-only -q services/metrics-subscriber packages/events | tail -1` evidence:

- **Before**: 510 tests collected
- **After**: 533 tests collected (+23 new — 17 unit + 7 integration; one of the original tests was reordered into 10.4's enum-pre-population coverage via a new test name)

Full repo: `uv run pytest -q -m "not slow"` reports `2895 passed, 3 skipped, 28 deselected`.

### Mypy baseline delta

- **Before**: 125 source files clean (`Success: no issues found in 125 source files`)
- **After**: 126 source files clean (+1 = `test_metrics_integration.py`)

### NFR-O8 p95 re-measurement (expanded metric set)

With the ~50-timeseries Story 10.4 metric surface populated (per
`_populate_state_to_story_10_4_scale` benchmark helper):

- **p50 = 0.58 ms**
- **p95 = 0.65 ms** ← well under 100 ms budget
- **p99 = 0.70 ms**

Story 10.3 baseline p95 was 0.94 ms with ~50 timeseries; this run measures
0.65 ms — the slight improvement is likely runner-load variance, not a real
optimisation.

### Steady-state cardinality

`len(<canonical_timeseries>)` = **50** (with all bounded-enum children
pre-populated, no per-task gauges active). Family count = 11.
Breakdown:

| Family | Canonical timeseries |
|---|---|
| `metrics_subscriber_lag_seconds` | 1 |
| `metrics_subscriber_bytes_behind` | 1 |
| `metrics_subscriber_cursor_offset_bytes` | 0 (no path labels until day-rollover) |
| `metrics_subscriber_parse_skip` | 4 |
| `omb_task_lifecycle_events` | 15 |
| `omb_session_lifecycle_events` | 5 |
| `omb_secret_accessed` | 5 |
| `omb_events_appended` | 11 |
| `omb_task_tokens_spent` | 0 (no active tasks at steady state) |
| `omb_idempotency_cache` | 2 |
| `omb_capability_denied` | 6 |
| **Total** | **50** |

Matches AC10 bound exactly.

### Surprises / deviations from spec

- **Actor-kind enum drift (Story 10.4 spec AC3 → real envelope ActorKind)**:
  spec AC3 enumerates `actor_kind` as `{human, system, agent}`. Real
  `events.envelope.ActorKind` is
  `{operator, orchestrator, worker, system, clawhip}`. Per P1-M1 protocol
  this is a STOP-and-report situation, but the spec AC3 wording
  ("`actor_kind` is the envelope's `actor.kind` enum") makes the right
  answer unambiguous: use the actual envelope enum. Documented in
  module docstring + ADR-0005 amendment + this DAR.
- **Event family count**: spec AC4 says 11 families; actual `register()`
  calls produce 10 families (no `deployment.*` registered). Kept the
  11-value enum (spec-stable, forward-compatible — the `deployment`
  child stays at zero until a future story registers it).
- **Cardinality test interpretation**: spec AC10 says
  `len(list(registry.collect())) <= 50`. Literally, `registry.collect()`
  returns metric FAMILIES (11), not timeseries (50). We interpret
  "timeseries" canonically (= labelset count) and filter out the
  `_created` bookkeeping samples Prometheus emits per Counter labelset.
  Implementation comment in `test_cardinality_at_steady_state_is_bounded`
  documents the filter.
- **AC8 docstring wording**: spec says docstring MUST cite
  "DEFERRED-FROM-FR62 — pending upstream event emission, see Story
  10.4.x / 11.x". Used exactly this wording.
- **ADR-0005 amendment length**: ~167 lines, over the spec's 100-line
  split-threshold suggestion. Kept inline (not split to 0005a) — the
  content is tightly coupled to the existing ADR and a split would add
  cross-link churn without clear benefit.

### Story 10.5 readiness check

Story 10.5 (cardinality regression — 10K varying task_ids ≤ 200
timeseries) can lift directly:

- **The `omb_task_tokens_spent` gauge + cleanup pattern** is the load-
  bearing invariant the 10K-task test will exercise.
- **The cleanup terminators** (`task.completed`, `task.stop_requested`)
  are already wired and tested for both happy-path and out-of-order
  scenarios — Story 10.5 can pile envelopes on without re-establishing
  the contract.
- **Bounded-enum pre-population** (44 children at registration) means
  Story 10.5's bound is `44 + max_concurrent_active_tasks ≤ 200`. The
  10K-task test can verify ≤ ~150 in-flight tasks at peak.
- **Test infrastructure**: the `test_app_with_event_dir` fixture in
  `test_metrics_integration.py` is reusable for the 10K-task synthetic
  emission. Story 10.5 can copy the fixture pattern or extract it into
  `conftest.py` (deferred — Story 10.4 stays scoped).
- **`registry.collect()` canonical-timeseries pattern** is established
  in `test_cardinality_at_steady_state_is_bounded`; Story 10.5 just
  scales the envelope count + asserts the new bound.

### Validation gates evidence

```
$ uv run ruff check . && uv run ruff format --check .
All checks passed!
348 files already formatted

$ uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber
Success: no issues found in 126 source files

$ uv run python scripts/check_imports.py        # exit 0
$ uv run python scripts/check_event_registry.py # exit 0 — D4 honored
$ uv run python scripts/check_single_writer.py  # exit 0

$ uv run pytest -x -q services/metrics-subscriber/ packages/events/
533 passed

$ uv run pytest -x -q -m slow services/metrics-subscriber/
4 passed   # NFR-O8 p95=0.65ms

$ uv run pytest -q -m "not slow"
2895 passed, 3 skipped, 28 deselected

$ just bootstrap-verify
✓ bootstrap OK (14 workspace-member imports verified)
```
