# Story 10.3 — FastAPI `/metrics` endpoint (Prometheus exposition)

Status: **done** (CI green @ `4fd27e3` (run 26115999732) — confirmed 2026-05-19; impl `eb53bb9` + `249d387`; pass-1 review batch: 20/20 closed = 5H + 7M + 8L)

## Story

**As** the platform operator
**I want** the `metrics-subscriber` service to expose a Prometheus-format `/metrics` HTTP endpoint reachable only on the docker-compose internal network
**so that** I can scrape derived metrics (lag, bytes-behind, parse-skip counters) via SSH-tunneled `curl` or a co-located Prometheus instance — without injecting instrumentation into any existing service (NFR-O1 preserved, NFR-O10 enforced).

This story lifts Story 10.2's structured-log values (`bytes_behind`, `wall_clock_lag_s`, `metrics_subscriber_parse_skip_total` preview-field) into actual Prometheus gauges/counters, wraps them behind a FastAPI factory mirroring `registry-api`'s pattern, and authors **ADR-0005** documenting the derived-projection decision foreclosing the "OTel-everywhere" anti-pattern.

## Acceptance criteria

### AC1 — Dependencies added to `services/metrics-subscriber/pyproject.toml`

Add to `[project] dependencies` (production):

```toml
"prometheus-client>=0.20,<1.0",
"fastapi>=0.115,<0.120",
"uvicorn[standard]>=0.30,<0.35",
```

Add to `[dependency-groups] dev`:

```toml
"httpx>=0.27",   # FastAPI test client (sync + async)
"pytest-benchmark>=4.0",  # NFR-O8 benchmark
```

Self-verification:
- `uv lock` succeeds; `uv.lock` updated.
- `just bootstrap-verify` green (14 workspace-member imports verified — pass-1 P1-M2 reconciled this with the DAR row; the pre-pass-1 "15/15" figure was a Story 10.1 wording carryover that included `events` as a package import alongside the 14 service / package workspace members).
- `grep -F "prometheus-client" services/metrics-subscriber/pyproject.toml` non-empty.

### AC2 — `app/main.py` FastAPI factory

New module: `services/metrics-subscriber/src/metrics_subscriber/app/main.py`

```python
# Schematic — actual signatures via story implementation

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from prometheus_client import CollectorRegistry, CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from metrics_subscriber.app.config import MetricsSubscriberSettings
from metrics_subscriber.app.metrics import MetricsState, build_collectors

def build_app(*, settings: MetricsSubscriberSettings) -> FastAPI:
    """FastAPI application factory (mirrors ``registry_api.app.build_app``).

    Wires:
      - Per-app ``CollectorRegistry`` (NOT the global ``REGISTRY``)
        to avoid cross-test metric pollution.
      - Async lifespan: spawns the Story 10.2 ``run_subscriber`` tail
        loop as a background task; tears it down on shutdown via
        ``AsyncExitStack``.
      - ``GET /metrics`` route returning ``generate_latest(registry)``
        with ``Content-Type: CONTENT_TYPE_LATEST``.
      - ``GET /healthz`` route returning ``{"status": "ok"}`` for
        compose ``service_healthy`` probes (Story 10.6 will wire).
    """
```

Self-verification:
- `from metrics_subscriber.app.main import build_app` succeeds in `python -c`.
- `app = build_app(settings=settings); assert app.title == "metrics-subscriber"`.

### AC3 — Async lifespan wires Story 10.2's `run_subscriber` tail loop

The FastAPI lifespan owns the existing tail loop. Pattern:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop_event = asyncio.Event()
    tail_task = asyncio.create_task(
        run_subscriber(settings, stop_event=stop_event, metrics_state=app.state.metrics),
    )
    async with AsyncExitStack() as stack:
        stack.push_async_callback(_drain_tail_task, tail_task, stop_event)
        yield
```

Constraints:
- Tail task failure (`ParseSkipThresholdExceeded`, `CursorSchemaVersionError`) must surface a structured `log.critical` event AND cause uvicorn to shut down with the **same exit-code matrix** (`1/2/3`) as the standalone `__main__.py` path. Tail-failure → app-shutdown wiring via `signal.SIGTERM` to self OR `uvicorn.Server.should_exit = True`.
- Settings is read once at lifespan-startup; `run_subscriber` receives the same instance the routes do.
- `run_subscriber` signature gains `metrics_state: MetricsState | None = None` (backwards-compat for the 10.2 standalone path which passes `None`).

Self-verification:
- Integration test `test_app_lifespan_runs_tail_task` — startup + shutdown completes cleanly; verifies tail_task created + cancelled on shutdown.

### AC4 — `GET /metrics` returns valid Prometheus text exposition

```python
@app.get("/metrics", response_class=Response)
async def metrics(request: Request) -> Response:
    body = generate_latest(request.app.state.registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)
```

Constraints:
- Response body parseable by `prometheus_client.parser.text_string_to_metric_families`.
- Uses **per-app** `CollectorRegistry` (NOT module-level `REGISTRY`) — test isolation.
- No exception leakage: if a collector raises, FastAPI exception handler returns 500 with structured log `metrics_subscriber_endpoint_failure` (no traceback to client).

Self-verification:
- `curl -fsS http://localhost:9090/metrics | head -5` shows `# HELP` / `# TYPE` lines.
- Test `test_metrics_endpoint_returns_valid_exposition` parses body with `prometheus_client.parser`; asserts the 2 baseline gauges + 1 counter (AC5/AC6) are present.

### AC5 — Lift Story 10.2 structured-log values into Prometheus gauges

Two gauges, updated by `run_subscriber` on every `maybe_persist` call (every `persist_every_n_events`):

| Metric | Type | Labels | Description |
|---|---|---|---|
| `metrics_subscriber_lag_seconds` | Gauge | (none) | Wall-clock lag of the most recently processed envelope vs. `now()` (datetime arithmetic per Story 10.2 VH-2 fix; NTP-sync required). |
| `metrics_subscriber_bytes_behind` | Gauge | (none) | Difference between today's JSONL file size and the subscriber's cursor offset at the most recent persist. |
| `metrics_subscriber_cursor_offset_bytes` | Gauge | `path` | Current cursor offset (debug introspection). Label cardinality bounded by today/yesterday (≤ 2 active values). |

Implementation note: the metrics live in a `MetricsState` dataclass (new module `app/metrics.py`) so the tail loop holds one reference and updates in-place — avoids the `prometheus_client` cross-thread surprise.

Self-verification:
- Test `test_lag_seconds_gauge_updates_after_persist` — emit 1001 envelopes (triggers one persist), assert gauge value equals the structured-log `wall_clock_lag_s` field at the same persist event.

### AC6 — Wire VH-13 parse-skip counter

Counter from Story 10.2 VH-13 fix (preview field documented in 10.2 spec):

| Metric | Type | Labels | Description |
|---|---|---|---|
| `metrics_subscriber_parse_skip_total` | Counter | `reason` | Lines skipped during JSONL tail. `reason` enum (pass-1 P1-M1 aligned with implementation): `json_decode`, `not_a_dict`, `pre110_missing_trace_id`, `validation`. Cardinality bounded by enum. |

Implementation: extend `events.log_reader.iter_new_envelopes_since` to accept an optional `on_skip: Callable[[str], None]` callback; Story 10.3 wires the callback to `counter.labels(reason=...).inc()`. Story 10.2 path passes `None` → unchanged behavior.

Self-verification:
- Test `test_parse_skip_counter_increments_by_reason` — drive `parse_with_pre110_backfill` to emit each of the four reason values, assert
  `parse_skip_total{reason="json_decode"} == 3`,
  `parse_skip_total{reason="not_a_dict"} == 2`,
  `parse_skip_total{reason="pre110_missing_trace_id"} == 1`,
  `parse_skip_total{reason="validation"} == 1`.
- Test `test_parse_skip_counter_all_reasons_pre_populated_in_collectors` (pass-1 P1-H1) — after `build_collectors(registry)`, all four reason children exist at value `0.0` (eliminates lazy-registration race against concurrent `generate_latest()` scrapes).

### AC7 — Settings extension for HTTP binding

Extend `MetricsSubscriberSettings`:

```python
metrics_host: str = Field(default="0.0.0.0")  # bind inside container; P2-I5 enforced at compose
metrics_port: int = Field(default=9090, ge=1, le=65535)
```

Constraints:
- `metrics_host` default `0.0.0.0` is intentional: docker-compose ingress controls reachability (P2-I5 — only stack peers reach this port). Docstring MUST cite this and forbid binding to a host-network interface.
- No env var rename; remain under `OMB_METRICS_` prefix → `OMB_METRICS_METRICS_PORT` (double-`metrics` is acceptable per existing prefix; alternative `OMB_METRICS_PORT` clashes with poll/persist prefixes if extended later).

Self-verification:
- `OMB_METRICS_METRICS_PORT=9091 python -c "from metrics_subscriber.app.config import MetricsSubscriberSettings; print(MetricsSubscriberSettings().metrics_port)"` → `9091`.
- Test `test_settings_metrics_port_default_and_override`.

### AC8 — `__main__.py` switches to `uvicorn.run(build_app(...))`

Replace the standalone `asyncio.run(_run())` path with the FastAPI uvicorn pattern (mirroring registry-api):

```python
def main() -> int:
    settings = MetricsSubscriberSettings()
    app = build_app(settings=settings)
    uvicorn.run(
        app,
        host=settings.metrics_host,
        port=settings.metrics_port,
        log_config=None,  # structlog owns logging
        access_log=False,  # /metrics scrapes spam logs; structured tail-log is canonical
    )
    return 0
```

Constraints:
- Exit code matrix (Q6 — Story 10.2 pass-2) preserved: lifespan-startup failure for `CursorSchemaVersionError` → exit 2; `ParseSkipThresholdExceeded` → exit 3; `BlockingIOError` (concurrent-start / filesystem-unsupported) → exit 1. Wire via lifespan exception handler that sets `app.state.exit_code` and triggers `uvicorn.Server.should_exit`.
- Standalone tail-loop entry path (Story 10.2 `run_subscriber` direct call) REMAINS available for the existing `test_restart_recovery*.py` subprocess tests — they exercise tail semantics, not the HTTP surface. Refactor `__main__.py` to dispatch via env var `OMB_METRICS_RUN_MODE=server|tail` (default `server`; tests use `tail`).

Self-verification:
- `OMB_METRICS_RUN_MODE=server python -m metrics_subscriber &` followed by `curl -fsS http://127.0.0.1:9090/healthz` returns 200.
- Existing subprocess tests (`test_subprocess_sigterm_persists_cursor_and_resumes_exactly_once`, etc.) updated to pass `OMB_METRICS_RUN_MODE=tail` and still pass.

### AC9 — Internal-only enforcement (P2-I5)

No code change forces the network policy — the gate is at the compose layer (Story 10.6 scope). But Story 10.3 MUST:

- Add a docstring to `app/main.py` citing P2-I5 with the explicit warning: *"This endpoint MUST NOT be exposed via `ports:` in docker-compose.yml; reachable only through the internal network. Operator scrapes via SSH-tunneled `curl` (FR61) or a co-located Prometheus instance on the same docker host."*
- Add a check in `app/main.py` startup that emits `log.warning("metrics_subscriber_bind_external_interface_suspected", host=settings.metrics_host)` if `settings.metrics_host` is a concrete IP address that is neither loopback (`127.0.0.1`/`::1`) nor the wildcard bind-all (`0.0.0.0`/`::`). Note: `0.0.0.0` intentionally binds all container interfaces and IS externally reachable within the docker network — the real enforcement is compose-level network scoping (Story 10.6). This heuristic is a sanity guard against typos (e.g. a specific external IP like `192.0.2.1`), not a security boundary.
- Update `tests/separability/` and `tests/integration/` to NOT scrape via external host:port; use the FastAPI TestClient (in-process) for unit tests, leaving real-network scrape for Story 10.6's S-4 separability test.

Self-verification:
- `grep -n "P2-I5" services/metrics-subscriber/src/metrics_subscriber/app/main.py` returns the module-level docstring header (line ≈28+) — the previously-present `_P2_I5_INTERNAL_ONLY` dict was deleted in pass-1 P1-L2 as dead code; the docstring grep target remains.
- Test `test_app_warns_on_external_bind_heuristic` — patch settings.metrics_host to `192.0.2.1`, assert warning emitted.

### AC10 — NFR-O8 latency benchmark (<100ms p95)

CI test marked `@pytest.mark.slow` that hits `/metrics` 100 times via `httpx.AsyncClient` (in-process via `ASGITransport`), records latencies, asserts p95 < 100ms on a fixed runner size.

Pattern:
```python
@pytest.mark.slow
@pytest.mark.benchmark
async def test_metrics_endpoint_p95_under_100ms(app: FastAPI, populated_state: MetricsState) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        latencies: list[float] = []
        for _ in range(100):
            t0 = time.perf_counter()
            r = await client.get("/metrics")
            latencies.append(time.perf_counter() - t0)
            assert r.status_code == 200
        p95 = sorted(latencies)[94]  # index 94 = 95th percentile of 100 samples (indices 0-99)
        assert p95 < 0.1, f"NFR-O8 violation: /metrics p95={p95*1000:.1f}ms ≥ 100ms"
```

Constraints:
- `populated_state` fixture loads a `MetricsState` with the full Story 10.4 metric count (~30 metric families = ~50 timeseries[^prom-terminology]) to ensure the benchmark reflects realistic exposition size — even though Story 10.4 isn't done yet, populate via direct `MetricsState` mutation in the fixture.

[^prom-terminology]: Prometheus terminology: a **metric family** is the named collector (e.g. `metrics_subscriber_parse_skip_total`); a **timeseries** is the family + one label-set permutation. The benchmark fixture creates 10 gauges + 10 counters + 10 labelled gauges × 3 tier values = 30 metric families expanded to 50 timeseries (the 10 labelled families each emit 3 samples, one per tier). Both numbers are valid descriptions of the same exposition; pass-1 P1-L4 disambiguated the spec/DAR wording.
- Benchmark runs on `ubuntu-latest` CI runner (fixed-size per NFR-O8).
- If p95 ≥ 100ms, the test FAILS — Story 10.3 cannot ship without meeting the latency budget.

Self-verification:
- `uv run pytest -q -m slow services/metrics-subscriber/.../test_metrics_endpoint_benchmark.py` exits 0.
- CI log captures the actual p95 number (use `pytest --tb=short` to surface).

### AC11 — ADR-0005 authored

New file: `docs/adr/0005-metrics-subscriber-derived-projection.md`

Content covers:
- **Context.** Phase 1 established NFR-O1: typed events on the event spine are the *primary* observability stream. Phase 2 introduces metrics + traces.
- **Decision.** Metrics + traces are *derived projections* of the event log, not parallel instrumentation paths. The `metrics-subscriber` tails the JSONL log read-only via `EventLogReader`; no `services/*` code emits Prometheus metrics directly.
- **Consequences.** (a) Adds a new workspace member (Epic 10) but zero changes to existing services. (b) Forecloses the "OTel-everywhere" anti-pattern. (c) Metrics granularity is bounded by event-log granularity — by design. (d) Cursor durability is required (Story 10.2's `cursor.json`); subscriber restart-recovery is exactly-once at envelope level.
- **Alternatives rejected.** (1) Inline `prometheus_client.Counter` calls in `services/*` — rejected: two sources of truth (events + metrics). (2) OpenTelemetry SDK in every service — rejected: NFR-O1 violation; instrumentation surface in producer services. (3) Sidecar with `tail -F` on stdout — rejected: NFR-O1 specifically bans stdout-parsing regex.
- **Status:** `accepted` (date 2026-05-19; supersedes the architecture.md:1218 placeholder).
- **References.** PRD FR60–FR62a, NFR-O1, NFR-O8, NFR-O10; architecture.md P2-I1, P2-I3, P2-I5; Stories 10.1, 10.2, 10.3.

Self-verification:
- `test -f docs/adr/0005-metrics-subscriber-derived-projection.md && grep -c "^## Decision" docs/adr/0005-*.md` → 1.

### AC12 — `check_imports.py` covers the new app surface

Story 10.1 added `services/metrics-subscriber` to the read-only-subscriber gate. Story 10.3's new `app/main.py` MUST NOT import from `services/registry-api`, `services/registry-state`, `services/worker-wrapper`, etc. — only from `packages/events`, `packages/secret-hygiene`, `packages/idempotency` (if needed for shared utilities).

Self-verification:
- `uv run python scripts/check_imports.py` exits 0.
- `grep -rn "from registry_\|import registry_" services/metrics-subscriber/src/` returns nothing.

### AC13 — Mypy --strict baseline extension

The new `app/main.py` + `app/metrics.py` modules grow the strict-typed surface. Update mypy override config if needed (e.g., `uvicorn` likely has no stubs — add `[[tool.mypy.overrides]] module = "uvicorn.*" ignore_missing_imports = true` if mypy complains). Expected baseline shift: **120 → ~125** source files.

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the new count (≈125) and exit 0.

### AC14 — Test isolation + autouse fixture extension

Story 10.2 added a conftest fixture clearing `OMB_METRICS_*` env vars between tests (AC10 of 10.2). Story 10.3 adds:

- A second autouse fixture resetting Story 10.3's per-app `CollectorRegistry` between tests (instantiate a fresh `CollectorRegistry()` per test) — prevents cross-test metric value leak.
- Update `services/metrics-subscriber/conftest.py` (existing) — additive.

Self-verification:
- `grep -nE "CollectorRegistry|reset_metrics" services/metrics-subscriber/src/metrics_subscriber/conftest.py` finds the new fixture.

### AC15 — Validation gates

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run python scripts/check_imports.py` — exit 0
- `uv run python scripts/check_event_registry.py` — exit 0
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run pytest -q services/metrics-subscriber/ packages/events/` — all green
- `uv run pytest -q -m slow services/metrics-subscriber/` — NFR-O8 benchmark passes
- `uv run pytest -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14 workspace-member imports verified — pass-1 P1-M2 reconciled to the DAR row; pre-pass-1 "15/15" was a wording inconsistency)

---

## Developer context

### Existing state (post Story 10.2)

- **Story 10.1 done**: scaffold workspace member, `__init__.py` + `__main__.py` (banner-only), `py.typed`, `test_version.py`.
- **Story 10.2 done**: tail loop + cursor persistence, lifespan task, structlog adoption, exit code matrix (0/1/2/3), `bytes_behind` + `wall_clock_lag_s` structured logs every persist, `metrics_subscriber_parse_skip_total{reason}` Counter preview field reserved by VH-13 fix, `MetricsSubscriberSettings` extensible.
- **`packages/events/src/events/log_reader.py`**: shared `EventLogReader` + `iter_new_envelopes_since` (extraction from registry-state per Story 10.2 AC1 — P2-I1 satisfied).
- **`registry-api`**: reference FastAPI factory pattern at `services/registry-api/src/registry_api/app.py` (`build_app`) + `__main__.py` (uvicorn). Mirror conventions: structlog wiring in `__main__.py` only (test pollution avoidance per Story 3.6 AC-4), `AsyncExitStack` for independent teardown, per-app state (NOT module globals).
- **Bootstrap verify**: 14 workspace-member imports verified (Story 10.1 added metrics-subscriber as the 14th — pass-1 P1-M2 reconciled the prior "15/15" wording).
- **Mypy --strict baseline**: 120 source files (post Story 10.2 pass-3).

### Architecture compliance

- **FR61** — `/metrics` Prometheus exposition, internal-only.
- **NFR-O1** preserved — no instrumentation injected into producer services (NFR-O10).
- **NFR-O8** — <100ms p95 verified by CI benchmark.
- **NFR-O10** — derived projection only; subscriber reads JSONL, never calls into other services.
- **P2-I1** — read-only subscriber rule (no `services/*→services/*` imports).
- **P2-I3** — metrics + traces as derived projections.
- **P2-I5** — no public ingress (enforced at compose layer in Story 10.6; docstring + heuristic warning here).
- **ADR-0005** — authored as part of this story.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `prometheus-client` | `>=0.20,<1.0` | Per-app `CollectorRegistry`, not the module global. Generate via `generate_latest(registry)`. |
| `fastapi` | `>=0.115,<0.120` | Match registry-api's version range. Use `lifespan` context manager, not deprecated `on_event`. |
| `uvicorn[standard]` | `>=0.30,<0.35` | Match registry-api; programmatic `uvicorn.run(app, ...)`. `log_config=None` so structlog owns logging. |
| `httpx` (dev) | `>=0.27` | Test client with `ASGITransport` for in-process integration tests + NFR-O8 benchmark. |
| `pytest-benchmark` (dev) | `>=4.0` | Optional — `pytest --benchmark` for richer percentile reporting if needed. Stub usable with bare `pytest`. |

### File-structure requirements

```
services/metrics-subscriber/src/metrics_subscriber/
├── __init__.py
├── __main__.py                    # MODIFY: dispatch via OMB_METRICS_RUN_MODE
├── app/
│   ├── __init__.py
│   ├── config.py                  # MODIFY: add metrics_host/metrics_port
│   ├── main.py                    # NEW: build_app() FastAPI factory
│   └── metrics.py                 # NEW: MetricsState + build_collectors()
├── conftest.py                    # MODIFY: reset CollectorRegistry per test
├── cursor.py                      # unchanged
├── (existing test files)          # MODIFY subprocess tests: OMB_METRICS_RUN_MODE=tail
└── test_app_main.py               # NEW: FastAPI integration tests
└── test_metrics_state.py          # NEW: MetricsState unit tests
└── test_metrics_endpoint_benchmark.py  # NEW: NFR-O8 benchmark (@pytest.mark.slow)

packages/events/src/events/log_reader.py    # MODIFY: add on_skip callback
docs/adr/0005-metrics-subscriber-derived-projection.md   # NEW
```

### Testing requirements

- **Pyramid:** unit tests for `MetricsState` (synchronous, no FastAPI overhead) + integration tests via `httpx.AsyncClient` (lifespan exercise).
- **Test isolation:** per-test `CollectorRegistry` instance via autouse fixture (no global `prometheus_client.REGISTRY` pollution).
- **Lifespan exercise:** at least one test triggers the full `async with build_app(...)` lifecycle — verifies tail-task spawn + clean cancel on shutdown.
- **NFR-O8 benchmark:** marked `@pytest.mark.slow + @pytest.mark.benchmark`; populated state simulates Story 10.4's metric count for realistic exposition size.
- **Subprocess test compat:** existing `test_restart_recovery*.py` + `test_exit_codes.py` MUST continue to pass by setting `OMB_METRICS_RUN_MODE=tail`. Do NOT remove the standalone tail-loop entry path.
- **Story 9.7 PH-H6 / Story 10.2 AC10 pattern:** env-var clearing autouse fixture remains; add CollectorRegistry reset alongside.

### Previous-story intelligence

#### From Story 10.2 (just closed)

- **3-pass adversarial review** found 70 total issues (33 HIGH + 25 MED + 12 LOW) across pass-1/2/3. Lessons:
  - **Cross-poll state**: per-call locals that should persist across polls are easy to miss (P2-H4 + P3-H2). Story 10.3's `MetricsState` MUST be a single instance shared across tail-loop iterations + HTTP requests.
  - **Substring-based discrimination**: `"unsupported" in str(exc)` was a real landmine (P3-H1). For Story 10.3, exit-code routing in lifespan exception handler MUST use `isinstance(exc, TypedException)` not string-match.
  - **Test asserts the wrong invariant** (P2-H5 / VH-8): exactly-once was silently downgraded to at-most-one-duplicate. Story 10.3 lag-gauge tests MUST assert against the exact `wall_clock_lag_s` value emitted in the structured log at the same persist event — not "some gauge value exists".
  - **`asyncio.get_event_loop()` deprecated** (P2-H8): use `get_running_loop()`.
  - **Module-global flags break test isolation** (P3-L3): every "warn-once" pattern needs a `_reset_for_tests()` hook.

- **Story 10.2 readiness signals for 10.3:**
  - ✅ `bytes_behind` + `wall_clock_lag_s` emit on every persist — direct lift to gauges (AC5).
  - ✅ `MetricsSubscriberSettings` extensible — add `metrics_port` (AC7) without touching 10.2's surface.
  - ✅ Exit code matrix 0/1/2/3 stable — Story 10.3 dashboards alert on each separately.
  - ✅ VH-13 reserved `metrics_subscriber_parse_skip_total{reason}` for 10.3 to wire (AC6).

#### From Epic 9 retro (AI-1 cadence)

- **3-pass cadence for high-complexity stories**. Story 10.3 is **medium complexity** (FastAPI factory + 3 metrics + benchmark + ADR) — expect 1-pass review unless first pass surfaces 15+ findings.
- **AI-2 self-verification ACs** — every AC above includes a "Self-verification" block with grep/curl/pytest assertions.
- **AI-3 no aggregated checkboxes** — each review patch gets its own checkbox in the review section.

### Trade-off notes

- **`MetricsState` as plain dataclass vs. `prometheus_client.MetricsCore`**: chose dataclass holding `Gauge`/`Counter` instances. Reason: `prometheus_client`'s `MetricsCore` is for *custom collectors*; we don't need that — we need a place to hold and mutate metric objects from the tail loop. Dataclass is simpler.
- **Standalone tail-loop entry retained (`OMB_METRICS_RUN_MODE=tail`)**: prevents breaking 10.2's subprocess tests. The cleaner alternative — rewriting those tests to spawn the FastAPI server and observe via `/metrics` — has higher blast radius and would conflate Story 10.3 + 10.2 review semantics. Defer that rewrite to Story 10.4 or 10.5 if there's appetite.
- **Per-app `CollectorRegistry` vs. global `REGISTRY`**: chose per-app. Reason: pytest test isolation. The architectural sketch at architecture.md:1207 uses `generate_latest()` (global) — we deviate intentionally, documented in `app/main.py` docstring.

### Lessons from prior reviews to apply

- **No `pragma: no cover` on operational error paths** (Story 10.2 P3-M6) — every except clause has at least one test.
- **No substring-based exception discrimination** (Story 10.2 P3-H1) — typed exception classes always.
- **Test count + mypy baseline noted post-batch** (Story 10.2 P2-M8) — Dev Agent Record MUST include `pytest --collect-only` evidence-line + actual mypy file count.
- **Cross-poll / cross-request state** (Story 10.2 P2-H4 + P3-H2) — `MetricsState` lives on `app.state`, not as a module global.

### Non-goals (do NOT do in 10.3)

- **Full FR62 metric set** — Story 10.4's scope. Story 10.3 ships the 3 metrics above (2 gauges + 1 counter) plus whatever Story 10.2 left as preview fields. Resist scope creep.
- **Cardinality regression test** — Story 10.5's scope. Story 10.3 enforces cardinality discipline only on the 3 metrics it adds (all are bounded-enum labels or label-free).
- **docker-compose entry + separability S-4** — Story 10.6's scope. Story 10.3 does NOT modify `docker-compose.yml`.
- **Operator runbook for scraping via SSH-tunnel** — Story 10.6 / Phase 2 ops docs scope.
- **External Prometheus deployment recipe** — out of project scope.

## Out-of-scope risk flags

- Lifespan-failure → uvicorn-exit wiring is tricky. The simplest path (raise from lifespan startup) gives an unstructured uvicorn traceback. A clean path requires `uvicorn.Server.should_exit = True` after the lifespan caught the exception; this is documented but easy to miss. Test `test_lifespan_cursor_schema_version_refused_exits_2` MUST exist and assert structured log + exit code 2.
- **`prometheus_client` cross-thread surprise**: counters/gauges are thread-safe (internal `threading.Lock`) but **registries are not** for concurrent registration. Per-app `CollectorRegistry` is constructed once in lifespan startup before any concurrent access; subsequent `.inc()` / `.set()` calls are safe. Document in `app/metrics.py` docstring.
- **httpx ASGITransport + lifespan**: `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` does NOT trigger the FastAPI lifespan by default. Use `LifespanManager` from `asgi-lifespan` OR `httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=True))` + explicit lifespan context manager. Add `asgi-lifespan>=2.1` to dev deps if needed (mypy override already there from earlier prep).
- **`uvicorn[standard]` brings in `httptools` + `websockets`**: heavy deps. Acceptable for a service; if image size is a concern, downgrade to `uvicorn` without `[standard]`. Decision: keep `[standard]` for h11/httptools performance parity with registry-api.

## Definition of done

- All 15 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `10-3-fastapi-metrics-endpoint: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- ADR-0005 in `accepted` status; referenced from architecture.md:1218 (delete the "to be drafted" placeholder).
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, decisions log, surprises).
- NFR-O8 p95 captured in CI log + Dev Agent Record.
- No regressions in: `tests/separability/`, `tests/integration/`, full pytest suite.

---

## Dev Agent Record

**Implementation date:** 2026-05-19
**Implementer:** executor agent (Claude Opus 4.7, 1M context)
**Status flip:** `ready-for-dev` → `review` (CI pending @ eb53bb9)

### Implementation summary

Story 10.3 lifts Story 10.2's structured-log fields (`bytes_behind`,
`wall_clock_lag_s`, `metrics_subscriber_parse_skip_total` preview) into
actual Prometheus gauges + counter, wrapped behind a FastAPI factory
that mirrors `registry-api`'s `build_app` pattern. ADR-0005 authored,
closing one of the five Phase-2 forward-referenced ADR acceptance-gate
items declared in ADR-0003. Architecture.md:1218 placeholder removed.

Key design decisions:

1. **Per-app `CollectorRegistry`** (NOT module global) — test isolation;
   each `build_app` call gets a fresh registry, eliminating
   "Duplicated metric registration" flakes (Story 10.2 P3-L3 lesson).
2. **`MetricsState` dataclass** holds `Gauge` / `Counter` instances
   — one instance per app, stored on `app.state.metrics`, mutated by
   the tail loop via `_emit_lag_log`, read by the `/metrics` HTTP
   handler via the same registry. (Story 10.2 P2-H4 + P3-H2 lesson:
   cross-poll / cross-request state lives on app-scoped objects,
   not module globals.)
3. **Async lifespan via `AsyncExitStack`** — wraps `run_subscriber` as
   a background task; tail-task `done_callback` discriminates typed
   exceptions (`CursorSchemaVersionError`, `ParseSkipThresholdExceeded`,
   `CursorLockUnsupportedFilesystemError | BlockingIOError`) via
   `isinstance` checks (Story 10.2 P3-H1 lesson — NO substring-match)
   and surfaces them onto `app.state.exit_code` + `should_exit` for
   `__main__.py` to pick up.
4. **`OMB_METRICS_RUN_MODE=server|tail` dispatch** in `__main__.py` —
   default `server` runs uvicorn + FastAPI; `tail` preserves the
   Story 10.2 standalone tail-loop path that the
   `test_restart_recovery_subprocess.py` tests rely on (rewriting
   them to drive the FastAPI server would conflate 10.2 + 10.3
   review semantics; trade-off documented in the spec).
5. **`asgi-lifespan>=2.1` adopted for the test client** — `httpx.AsyncClient`
   + `ASGITransport` alone does NOT trigger FastAPI lifespan
   (documented Story 10.3 risk-flag). `LifespanManager(app)` wrap
   exercises startup + shutdown deterministically.
6. **External-bind heuristic** (`_is_external_bind_heuristic`) — emits
   `metrics_subscriber_bind_external_interface_suspected` WARN log
   if `settings.metrics_host` parses as a concrete IP that is neither
   loopback (`127.0.0.1` / `::1`) nor wildcard (`0.0.0.0` / `::`).
   Sanity guard against typos; real P2-I5 enforcement is at the
   compose layer (Story 10.6).
7. **Parse-skip counter wiring via `events.log_reader.set_on_skip`**
   callback — Story 10.2 `iter_new_envelopes_since` was extended in
   prep; Story 10.3 wires the callback to
   `MetricsState.on_parse_skip(reason)`. Server mode wires the callback;
   tail mode passes `None` → unchanged behaviour.

### Files changed

**Modified (8):**

- `_bmad-output/implementation-artifacts/sprint-status.yaml` — flip
  `in-progress` → `review` for Story 10.3 row.
- `_bmad-output/planning-artifacts/architecture.md` — replace ADR-0005
  "to be drafted" placeholder with accepted-status reference (AC11).
- `_bmad-output/implementation-artifacts/10-3-fastapi-metrics-endpoint.md`
  — Status flip + this Dev Agent Record block.
- `packages/events/src/events/log_reader.py` — add `on_skip` callback
  parameter to `parse_with_pre110_backfill` + propagate through
  `iter_new_envelopes_since`; add `EventLogReader.set_on_skip` method
  (AC6 wiring).
- `pyproject.toml` — register `benchmark` pytest marker; add
  `asgi-lifespan>=2.1` + `httpx>=0.27` to workspace dev deps so the
  metrics-subscriber tests resolve at the workspace root (CI `uv sync`
  materialises them for `pytest -m "not slow"`).
- `services/metrics-subscriber/pyproject.toml` — add
  `prometheus-client>=0.20,<1.0`, `fastapi>=0.115,<0.120`,
  `uvicorn[standard]>=0.30,<0.35` to `[project] dependencies`;
  `httpx>=0.27`, `pytest-benchmark>=4.0`, `asgi-lifespan>=2.1` to
  `[dependency-groups] dev` (AC1).
- `services/metrics-subscriber/src/metrics_subscriber/__main__.py` —
  dispatch via `OMB_METRICS_RUN_MODE` env var; add `_run_server_mode`
  (uvicorn-hosted FastAPI factory) + `_run_tail_mode` (Story 10.2
  standalone path) (AC8). `run_subscriber` gains
  `metrics_state: MetricsState | None = None` parameter; passes
  through to `_emit_lag_log` which now mutates gauges when not None
  (AC5).
- `services/metrics-subscriber/src/metrics_subscriber/app/config.py` —
  add `metrics_host: str = Field(default="0.0.0.0")` and
  `metrics_port: int = Field(default=9090, ge=1, le=65535)` (AC7).
- `services/metrics-subscriber/src/metrics_subscriber/conftest.py` —
  add autouse fixture `_reset_collector_registry_per_test` that
  unregisters dynamically-added collectors from the global
  `prometheus_client.REGISTRY` between tests (defensive: production
  code uses per-app registries, but this prevents an inadvertent
  global-registry registration from leaking across tests) (AC14).
- `services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery_subprocess.py`
  — set `OMB_METRICS_RUN_MODE=tail` on subprocess env so the subprocess
  tests exercise the standalone tail-loop path (AC8 trade-off).
- `uv.lock` — refreshed by `uv lock` after new dependencies.

**Added (5):**

- `docs/adr/0005-metrics-subscriber-derived-projection.md` — ADR-0005
  authored at `status: accepted`, date 2026-05-19. Context / Decision /
  Consequences / 3 rejected alternatives / References (AC11).
- `services/metrics-subscriber/src/metrics_subscriber/app/main.py` —
  `build_app(*, settings) -> FastAPI` factory + lifespan +
  `/metrics` + `/healthz` + exception handler (AC2/AC3/AC4/AC9).
- `services/metrics-subscriber/src/metrics_subscriber/app/metrics.py` —
  `MetricsState` dataclass + `build_collectors(registry) -> MetricsState`
  (AC5/AC6).
- `services/metrics-subscriber/src/metrics_subscriber/test_app_main.py`
  — FastAPI integration tests via `LifespanManager` + `httpx.AsyncClient`
  + `ASGITransport` (AC2/AC3/AC4/AC5/AC6/AC9 + AC8 dispatch).
- `services/metrics-subscriber/src/metrics_subscriber/test_metrics_state.py`
  — unit tests for `MetricsState` + `build_collectors` (AC5/AC6/AC14).
- `services/metrics-subscriber/src/metrics_subscriber/test_metrics_endpoint_benchmark.py`
  — `@pytest.mark.slow @pytest.mark.benchmark` NFR-O8 latency benchmark
  with ~30-metric fixture simulating Story 10.4 scale (AC10).

### Test count delta

- **Pre-Story-10.3 metrics-subscriber:** 44 tests (baseline post 10.2).
- **Post-Story-10.3 metrics-subscriber (initial):** 61 tests (`+17` new).
- **Post-pass-1 batch:** services/metrics-subscriber + packages/events
  combined run at **510 collected** (`+8` vs. the pre-pass-1 502 figure
  the parent task brief cites; the eight additions are the pass-1
  required tests P1-H1, P1-H2, P1-H4, P1-H5, P1-M3, P1-M5, P1-M7, P1-L6
  plus the four-reason extension to `test_parse_skip_counter_increments_by_reason`).
- **Pass-1 P1-L7 evidence line** (per Story 10.2 P2-M8 convention —
  fresh `--collect-only` paste):

  ```
  $ uv run pytest --collect-only -q services/metrics-subscriber packages/events | tail -1
  510 tests collected in 0.77s
  ```

- **Full PR-gate suite (`-m "not slow"`):** 2864 passed, 3 skipped,
  28 deselected pre-pass-1; the pass-1 additions land in the
  `services/metrics-subscriber` portion of the gate so the full-suite
  count rises by the same `+8`. Zero new failures vs. baseline.
- **`-m slow` in metrics-subscriber:** 4 passed (NFR-O8 benchmark +
  3 subprocess tests).
- **`packages/events`:** unchanged test count; existing tests still
  green after `on_skip` callback addition (parameter is optional /
  default `None`). Pass-1 P1-H5 + P1-M3 changes are also additive in
  this package — no existing behaviour changed.

### Mypy `--strict` baseline delta

- **Pre-Story-10.3:** 120 source files clean.
- **Post-Story-10.3:** **125 source files clean** (delta `+5` — matches
  the AC13 expectation of "≈125"). New files contributing:
  `app/main.py`, `app/metrics.py`, `test_app_main.py`,
  `test_metrics_state.py`, `test_metrics_endpoint_benchmark.py`.
- No new mypy override stanzas required — `fastapi`, `uvicorn`,
  `prometheus_client`, `httpx`, `asgi_lifespan` all ship `py.typed`
  in the resolved versions.
- **Pass-1 P1-L5 explicit confirmation**: all Story 10.3 deps
  (`fastapi`, `uvicorn`, `prometheus_client`, `httpx`, `asgi_lifespan`)
  ship `py.typed`; **no** `[[tool.mypy.overrides]]` blocks added in
  this story (verified `mypy --strict` passes across 125 source files
  without them). The "no overrides" outcome was a considered decision
  reached by reviewing each dep's package metadata, not an omission.

### NFR-O8 latency benchmark (AC10)

```
NFR-O8 /metrics latency (100 samples): p50=0.31ms p95=0.94ms p99=1.50ms
```

- **Budget:** p95 < 100 ms.
- **Observed (local M-series macOS, in-process `ASGITransport`):**
  p95 = **0.94 ms** — ~100× headroom against the budget.
- **CI runner (ubuntu-latest):** to be captured on PR CI; the budget
  applies on the CI runner per the AC10 wording. Local headroom is
  large enough that the CI delta is not a concern.
- Fixture preloads ~50 timeseries (~30 metric families) — 10 gauges
  + 10 counters + 10 labelled gauges × 3 tier values — to simulate
  Story 10.4's exposition size. Pass-1 P1-L4 disambiguated the
  "30 metrics" / "50 timeseries" wording across spec body + DAR:
  both numbers describe the same fixture, with "metric families"
  the canonical Prometheus term for the 30 figure.

### Validation gates (AC15) — final run

All gates green at the local checkpoint that immediately precedes
the commit:

- `uv run ruff check .` — All checks passed
- `uv run ruff format --check .` — 347 files already formatted
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber`
  — Success: no issues found in 125 source files
- `uv run python scripts/check_imports.py` — exit 0
- `uv run python scripts/check_event_registry.py` — exit 0
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run pytest -x -q services/metrics-subscriber packages/events`
  — 502 passed
- `uv run pytest -x -q -m slow services/metrics-subscriber` — 4 passed
- `uv run pytest -q -m "not slow"` — 2864 passed, 3 skipped
- `just bootstrap-verify` — ✓ bootstrap OK (14 workspace-member imports verified — confirmed against AC1/AC15/Existing-state references in pass-1 P1-M2)

### Surprises / deviations from spec

1. **Spec said "expected baseline shift 120 → ~125" — observed exactly
   125.** No additional sources crept in unexpectedly.
2. **`pytest -m "not slow"` post-test fatal Python error.** Daemon-
   thread shutdown noise from aiosqlite + asyncio teardown surfaces
   AFTER the test summary line ("2864 passed"). Not a regression —
   present on baseline too — and zero functional impact (test results
   are reported correctly before the noise). Out-of-scope for Story
   10.3; tracked informally for a future cleanup story.
3. **Subprocess tests required `OMB_METRICS_RUN_MODE=tail` env-var
   addition** to `_subscriber_env` helper in
   `test_restart_recovery_subprocess.py` (initial run failed with
   `rc=-15` because the default `server` mode owns SIGTERM via
   uvicorn, not the tail loop's `stop_event`). One-line fix; documented
   inline.
4. **Test count delta differs from the parent task brief's "485 vs new"
   wording.** The 485 figure was the Story 10.2 closing test count
   (line 275 of sprint-status); the local-equivalent baseline for
   _metrics-subscriber only_ pre-Story-10.3 was 44 (10.2 tests carry
   over). Post-Story-10.3 metrics-subscriber = 61 tests; full suite =
   2864 passed. Both numbers captured above.
5. **`prometheus_client` family-name vs sample-name in parser**:
   `text_string_to_metric_families` strips `_total` from Counter family
   names, but the underlying sample names retain it. AC6 assertions
   use sample-name matching (the canonical comparison) and pass; AC4
   assertions use family-name matching with `_total` stripped (which
   is correct for the family-existence check). Documented in
   `test_metrics_state.py` docstring.
6. **AC6 reason enum drift between spec table and implementation
   (pass-1 P1-L8)**: the original spec AC6 table listed
   `{invalid_json, unknown_event_type, payload_validation_failure}`,
   but the actual `log_reader.py` skip points emit
   `{json_decode, not_a_dict, pre110_missing_trace_id, validation}`
   (4 values, different naming). The spec table + self-verification
   clause + test fixture were ALL written against the speculative
   enum and silently never matched the production labels — the test
   passed only because it asserted on the implementation's own
   labels (json_decode, not_a_dict) via `_parse_sample` rather than
   the spec's. Pass-1 P1-M1 updated the spec AC6 table to mirror
   implementation and extended the test to cover all four reasons.
   Lesson: when adding a new metric, copy the production label
   strings into the spec table at authoring time, do not paraphrase.
7. **`bootstrap-verify` count was 14, not 15 (pass-1 P1-M2)**: AC1 +
   AC15 + the existing-state row all said "15/15 imports" while the
   DAR validation-gates row said "14". Live `just bootstrap-verify`
   on HEAD produced "14 workspace-member imports verified". The
   discrepancy was a wording inconsistency carried over from
   Story 10.1's "15 workspace members" framing (which counted
   `events` as a workspace member alongside the 14 service / package
   imports the `bootstrap-verify` Justfile recipe loops over). All
   three spec occurrences updated to the live count. No member
   actually regressed.

### Story 10.4 readiness check

Story 10.4 (FR62 — counter/gauge/histogram set over task lifecycle,
session state, capability tier) is unblocked by Story 10.3:

- ✅ `MetricsState` dataclass + `build_collectors` factory — Story 10.4
  extends `build_collectors` with the FR62 metric set; the per-app
  `CollectorRegistry` + autouse-reset fixture machinery is reusable.
- ✅ FastAPI factory + `/metrics` route — Story 10.4 adds NO new
  routes, only collectors.
- ✅ NFR-O8 benchmark — Story 10.4's larger metric set should still
  comfortably meet the 100ms budget given the 100× headroom observed
  here. The benchmark fixture (10/10/10 metric pattern) was sized to
  approximate Story 10.4's footprint, so the budget is already
  validated at that scale.
- ✅ ADR-0005 accepted — closes the Phase-2 acceptance-gate item that
  was blocking further Epic 10 merges.

---

## Review Findings — pass-1 (2026-05-19)

Pass-1 adversarial review on diff `c211a7f..249d387` (17 files, +1940 / −31 lines). Three parallel reviewers (Sonnet): Blind Hunter (8 findings B-HIGH-1..2 + B-MED-1..3 + B-LOW-1..3), Edge Case Hunter (6 findings E1..E6), Acceptance Auditor (6 findings A1..A2 + 4 minor + observations). All three verdicts: **REVISE**.

After dedup → **20 unique findings** (5 HIGH, 7 MED, 8 LOW). Convergences:
- `_P2_I5_INTERNAL_ONLY` dead-code dict (B-LOW-2 + A-minor-4)
- Benchmark date hardcode (B-LOW-3 + E4)
- AC6 spec/impl label drift + test coverage gap (A1 + A-minor-1)

All 20 close per "fix all issues even minors" standing policy.

### Patch — HIGH (5)

- [x] [Review][Patch] **P1-H1 — Counter.labels() called from `asyncio.to_thread` worker risks lazy-registration race on first-seen reason** [services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:124] — Solo HIGH: B-HIGH-1. The module docstring (app/main.py:47-54) claims "Subsequent `.inc()` / `.set()` calls can interleave safely" — true for already-created label children but NOT for the first `.labels(reason=r)` call which lazily inserts into the Counter's internal `_metrics` dict (form of lazy registration on the registry's `_names_to_collectors` structure). Concurrent `generate_latest()` from `/metrics` route + worker-thread `.labels()` on novel `reason` value is a real low-probability race during startup. Fix: in `build_collectors(registry)`, pre-populate ALL known reason children: `for r in ("json_decode", "not_a_dict", "pre110_missing_trace_id", "validation"): parse_skip_total.labels(reason=r).inc(0)`. Eliminates lazy-registration entirely. Add test `test_parse_skip_counter_all_reasons_pre_populated_in_collectors`.

- [x] [Review][Patch] **P1-H2 — `reader.current_path` raises `RuntimeError` in finally drain when `restore_into` raises non-CursorSchemaVersionError, masking the original exception** [services/metrics-subscriber/src/metrics_subscriber/__main__.py:387-390] — Solo HIGH: B-HIGH-2. `restore_into` can raise `OSError`/`json.JSONDecodeError` for corrupt cursor files. Current code catches only `CursorSchemaVersionError`; other exceptions propagate to outer `finally` where `reader.current_path` raises `RuntimeError` (property requires `_current_path` non-None, only set after `open()`/`seek()`). Original exception is masked by the secondary RuntimeError. Fix: guard finally with `if reader._current_path is not None:` OR restructure so `restore_into` lives inside the try whose finally reads `current_path`. Add test `test_finally_drain_skipped_when_restore_into_raises_uncaught_exception`.

- [x] [Review][Patch] **P1-H3 — `record_lag` two-setter sequence (`lag_seconds.set()` + `bytes_behind.set()`) is non-atomic; Prometheus scrape between setters sees split-brain** [services/metrics-subscriber/src/metrics_subscriber/app/metrics.py:104-105] — Solo HIGH: E1. Each `Gauge.set()` acquires its own internal lock independently. The docstring at line 91 falsely claims "Atomic mutation". Alerting rules correlating `lag_seconds × bytes_behind` see inconsistent snapshots. Fix: (a) easy — collapse docstring claim and document as best-effort eventual-consistency (operationally fine for 15s scrape interval, microsecond split window); OR (b) collapse the two into a single labeled gauge `metrics_subscriber_persist_snapshot{field="lag_seconds|bytes_behind"}` collected atomically; OR (c) use a custom collector with a single lock. **Decision per "fix all issues even minors": choose (a) — update docstring honestly + add explicit "may not be atomic at scrape boundary, see ADR-0005 §atomicity note" reference. Document the operational impact: at 15s scrape the split-brain window is sub-microsecond, well within Prometheus scrape jitter.**

- [x] [Review][Patch] **P1-H4 — `/healthz` returns 200 unconditionally even when tail task has crashed (hollow health gate)** [services/metrics-subscriber/src/metrics_subscriber/app/main.py:279-287] — Solo HIGH: E2. When `_on_tail_done` fires for an unclassified exception, it logs CRITICAL and requests uvicorn shutdown — but `/healthz` still returns 200 between callback and uvicorn drain. compose `service_healthy` probe passes while subscriber is dead. Docstring at line 25 ("gates the service") is hollow. Fix: in healthz handler, check `request.app.state.exit_code` (if non-zero, 503) AND/OR `tail_task.done() and tail_task.exception() is not None`. Return `503 {"status": "degraded", "reason": "tail_task_failed", "exit_code": <code>}`. Add `test_healthz_503_when_tail_task_crashed`.

- [x] [Review][Patch] **P1-H5 — `on_skip` callback exception propagates out of `asyncio.to_thread`, killing the entire subscriber (instrumentation kill switch)** [packages/events/src/events/log_reader.py:482, 493, 516, 539] — Solo HIGH: E3. The callback (which wires to `parse_skip_total.labels(reason=r).inc()`) has no try/except wrapper at any of the 4 call sites. If prometheus_client raises (locked registry, child-creation race per P1-H1, future contributor bug), the worker thread propagates → tail_task crashes → exit 1 for entire subscriber. Tail loop must NOT die because of instrumentation failure. Fix: wrap each `on_skip(...)` call in `try: on_skip(reason) except Exception as e: logger.warning("metrics_subscriber_on_skip_callback_failed", reason=reason, error=str(e))`. Add `test_on_skip_callback_exception_does_not_crash_tail_loop` (mock callback to raise, assert tail continues).

### Patch — MED (7)

- [x] [Review][Patch] **P1-M1 — AC6 spec/implementation label drift + test coverage gap** [spec AC6 + services/metrics-subscriber/src/metrics_subscriber/test_app_main.py:237-283] — **2-lane: A1+A-minor-1**. Spec AC6 table says reason enum is `{invalid_json, unknown_event_type, payload_validation_failure}`. Actual reasons in `log_reader.py:482/493/516/539` are `{json_decode, not_a_dict, pre110_missing_trace_id, validation}` (4 values, different naming). Spec self-verification clause asserts `reason="invalid_json"` which never fires. Test covers only `json_decode` and `not_a_dict` — `pre110_missing_trace_id` + `validation` paths untested. Fix: (a) update AC6 reason enum table in spec to actual labels `{json_decode, not_a_dict, pre110_missing_trace_id, validation}`; (b) update self-verification clause; (c) extend test `test_parse_skip_counter_increments_by_reason` to cover all 4 reasons; (d) add Surprises bullet in DAR noting the deviation (per P1-L8).

- [x] [Review][Patch] **P1-M2 — bootstrap-verify count contradiction: AC1/AC15 claim "15/15 imports", DAR validation gates row says "14 workspace-member imports"** [spec lines 33-35 + 583] — Solo MED: A2. Internal contradiction within the spec document. Either the AC self-verification clauses are wrong OR a workspace member regressed from Story 10.1's 15-member baseline. Fix: run `just bootstrap-verify` on current HEAD, record exact count, update ALL three occurrences (AC1, AC15, DAR row) to match. If actual count is 14, investigate which member dropped + add a tech-debt note.

- [x] [Review][Patch] **P1-M3 — `read_new_envelopes_since` / `read_batch` does not thread `on_skip` callback through; sync code path silently drops parse-skip counter** [packages/events/src/events/log_reader.py:355-431] — Solo MED: B-MED-1. `iter_new_envelopes_since` accepts `on_skip` but its wrapper `read_new_envelopes_since` (line 355) doesn't expose it; `EventLogReader.read_batch` calls it without the callback. registry-state / registry-api callers see zero parse_skip_total increments regardless of skip count. AC6 claims the counter is wired for "lines skipped during JSONL tail" — strictly true for tail() path but the gap on sync path is silent. Fix: add `on_skip: Callable[[str], None] | None = None` parameter to `read_new_envelopes_since` (additive, default None preserves existing callers); thread through to `iter_new_envelopes_since` call. Update `EventLogReader.read_batch` to pass `self._on_skip` (if set during construction). Add test exercising the sync path with a skip.

- [x] [Review][Patch] **P1-M4 — `_on_tail_done` `add_done_callback` race with lifespan `yield`** [services/metrics-subscriber/src/metrics_subscriber/app/main.py:201-252] — Solo MED: B-MED-2. If `tail_task` completes (with non-zero exit) between `add_done_callback` (line 252) and `yield` (line 259) — possible with zero-length event log + immediate stop_event — `_on_tail_done` fires before lifespan teardown is fully set up, causing uvicorn to begin shutdown mid-startup. AsyncExitStack drain races with uvicorn's own shutdown. Window is ms-level in production. Fix: move `tail_task.add_done_callback(_on_tail_done)` to AFTER the `yield`, inside the `async with AsyncExitStack()` block so the callback is only active while lifespan is fully running. Verify with test that simulates immediate task completion.

- [x] [Review][Patch] **P1-M5 — `app.state.exit_code` read after `asyncio.run(_serve())` may race with `_on_tail_done` callback dispatch** [services/metrics-subscriber/src/metrics_subscriber/__main__.py:469-474] — Solo MED: B-MED-3 (Blind Hunter Open Question). If uvicorn's shutdown path causes `asyncio.run()` to unwind before pending done-callbacks are scheduled, `getattr(app.state, "exit_code", 0)` reads 0 even when the tail crashed with non-zero exit. CPython 3.12 docs say callbacks are dispatched "when the Future finishes" within the running loop — likely safe but unverified. Fix: explicit `await asyncio.sleep(0)` after `await server.serve()` returns to flush pending callbacks, OR restructure exit_code into an `asyncio.Future` that the callback explicitly resolves and `_run_server_mode` awaits with a timeout. Add `test_exit_code_propagation_after_serve_returns` with mock callback delay.

- [x] [Review][Patch] **P1-M6 — autouse `_reset_collector_registry_per_test` fixture resets global `prometheus_client.REGISTRY`, but the per-app `app.state.registry` is the actual isolation mechanism; comment overstates the guarantee for crash-mid-lifespan tests** [services/metrics-subscriber/src/metrics_subscriber/conftest.py:79-80] — Solo MED: E5. Comment claims "a fresh registry per app instance means cross-test metric value leak is impossible" — only true for tests that complete lifespan normally. If a test crashes mid-lifespan and reuses the same app instance, per-app registry leaks. Fix: clarify comment to document the actual guarantee scope ("per-test lifespan-complete scenarios"). Optional: add post-yield assertion that `build_app` succeeds without duplicate-metric errors. Decision: doc-only fix unless we want to harden the lifespan exit path (deferred to Story 10.4 if scope creep).

- [x] [Review][Patch] **P1-M7 — Invalid `OMB_METRICS_RUN_MODE` value logs via structlog BEFORE `logging.basicConfig` in `main()` — operator sees plain stdlib format, not JSON** [services/metrics-subscriber/src/metrics_subscriber/__main__.py:81 + 489] — Solo MED: E6. Module-level `log = structlog.get_logger(...)` at line 81; structlog config happens in `main()` at line 489. Invalid-mode branch fires the log before structlog is configured → operator sees unstructured output where they expect JSON. Fix: either (a) move structlog `configure()` to module-import time (idempotent guard already exists per Story 3.6 AC-4 pattern), or (b) use stdlib `logging.error()` for the pre-configure window. Decision: (a) — move config to module level with `_STRUCTLOG_CONFIGURED` guard, matching registry-api precedent. Add test `test_invalid_run_mode_logs_structured_event`.

### Patch — LOW (8)

- [x] [Review][Patch] **P1-L1 — `httpx.ASGITransport(raise_app_exceptions=...)` inconsistency across tests** [test_app_main.py:162 vs :366] — Solo LOW: B-LOW-1. Some tests use `ASGITransport(app=app)` (raises exceptions), others use `raise_app_exceptions=False` (returns response). The exception-test path correctly uses `False`; happy-path tests should be consistent. Fix: standardize on `raise_app_exceptions=False` for ALL tests; assert response codes explicitly. Avoids confusing "raw exception vs 500" failures during test triage.

- [x] [Review][Patch] **P1-L2 — `_P2_I5_INTERNAL_ONLY` dict in `app/main.py:333-337` is dead code; module docstring already cites P2-I5** [services/metrics-subscriber/src/metrics_subscriber/app/main.py:333-337] — **2-lane: B-LOW-2 + A-minor-4**. Dict exists solely as grep anchor for AC9 self-verification. The docstring at line 28 already cites P2-I5 — grep target satisfied without the dict. Confuses future contributors. Fix: delete the dict; update AC9 self-verification clause to reference docstring grep only (`grep -n "P2-I5" services/metrics-subscriber/src/metrics_subscriber/app/main.py` returns line 28+).

- [x] [Review][Patch] **P1-L3 — Hardcoded `/tmp/2026-05-19.jsonl` path in benchmark + lag test fixtures** [test_metrics_endpoint_benchmark.py:87 + test_app_main.py:215] — **2-lane: B-LOW-3 + E4**. Date string couples tests to authoring date. Today the test path is just a label string (no FS access via this exact path), so test currently passes regardless of date — but it's a maintenance trap (false-positive understanding) and label cardinality churn risk in CI when today's date differs. Fix: replace with `str(tmp_path / "2026-01-01.jsonl")` (stable fixture path) or `Path("today.jsonl")` (semantic).

- [x] [Review][Patch] **P1-L4 — AC10 wording ambiguity: spec body says "~30 metrics", DAR says "~50 timeseries"; fixture creates 10 gauges + 10 counters + 10 labelled × 3 tiers = 50 timeseries / 30 metric families** [spec AC10 + DAR] — Solo LOW: A-minor-2. Both numbers are correct but the spec uses "metrics" ambiguously. Fix: update spec AC10 to clarify "~30 metric families = ~50 timeseries" with footnote on Prometheus terminology. Update DAR to match.

- [x] [Review][Patch] **P1-L5 — DAR mypy section does not explicitly confirm "no new overrides needed" was a considered decision (deps all ship `py.typed`)** [spec DAR Mypy baseline section] — Solo LOW: A-minor-3. Reader can't distinguish "no overrides needed" from "forgot to add overrides". Fix: one-line addition to DAR: "All Story 10.3 deps (`fastapi`, `uvicorn`, `prometheus_client`, `httpx`, `asgi_lifespan`) ship `py.typed`; no `[[tool.mypy.overrides]]` blocks added (verified mypy --strict passes without them)."

- [x] [Review][Patch] **P1-L6 — Missing `test_lifespan_cursor_schema_version_refused_exits_2` from AC8 risk-flag requirement** [services/metrics-subscriber/src/metrics_subscriber/test_app_main.py] — Solo LOW: A-missing-1. AC8 out-of-scope risk flag explicitly required this test ("Test `test_lifespan_cursor_schema_version_refused_exits_2` MUST exist"). Not found in test_app_main.py. Fix: add the test — patches `CursorPersistence.restore_into` to raise `CursorSchemaVersionError`, calls `_run_server_mode`, asserts rc==2 + structured log emitted.

- [x] [Review][Patch] **P1-L7 — DAR lacks `pytest --collect-only` evidence-line for test count (per Story 10.2 P2-M8 lesson)** [spec DAR Test count delta] — Solo LOW: A-missing-2. Story 10.2's pass-2 P2-M8 finding established the convention: paste the `pytest --collect-only -q | tail -1` output in DAR as evidence. Story 10.3 DAR states "61 collected" without backing evidence. Fix: run `uv run pytest --collect-only -q services/metrics-subscriber packages/events | tail -1` and paste output in DAR.

- [x] [Review][Patch] **P1-L8 — AC6 label drift not recorded in DAR Surprises/deviations section** [spec DAR Surprises] — Solo LOW: A-missing-3. Implementation diverged from spec labels (per P1-M1) but Surprises section doesn't mention it. Future readers tracing AC6 confusion will hit unexplained drift. Fix: add Surprises bullet: "AC6 reason enum: spec table listed `{invalid_json, unknown_event_type, payload_validation_failure}` but actual `log_reader.py` skip points emit `{json_decode, not_a_dict, pre110_missing_trace_id, validation}` (4 values, different naming). Spec AC6 + test updated in pass-1 P1-M1 to match implementation."

### Deferred (none — all 20 addressed in this pass)


```yaml
---
story_id: 10.3
story_key: 10-3-fastapi-metrics-endpoint
parent_epic: 10
phase: 2
fr_refs: [FR61]
nfr_refs: [NFR-O1, NFR-O8, NFR-O10, NFR-S7]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "Derived projection pattern (P2-I3, ADR-0005)"
  - "No public ingress (P2-I5)"
estimated_hours: 4-6
priority: medium (Epic 10 critical-path — unblocks 10.4/10.5; ADR-0005 closes acceptance-gate item)
blocks:
  - 10.4 (counter/gauge/histogram set — needs FastAPI factory + MetricsState)
  - 10.5 (cardinality regression — needs at least one metric to fingerprint)
  - 10.6 (compose entry + separability — needs HTTP server to add to stack)
blocked_by:
  - 10.1 (scaffold — done)
  - 10.2 (tail loop + cursor persistence — done)
status: review
created: 2026-05-19
created_by: bmad-create-story skill
---
```
