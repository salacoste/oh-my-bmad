# Story 10.3 — FastAPI `/metrics` endpoint (Prometheus exposition)

Status: **ready-for-dev**

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
- `just bootstrap-verify` green (still 15/15 imports).
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
| `metrics_subscriber_parse_skip_total` | Counter | `reason` | Lines skipped during JSONL tail. `reason` enum: `invalid_json`, `unknown_event_type`, `payload_validation_failure`. Cardinality bounded by enum. |

Implementation: extend `events.log_reader.iter_new_envelopes_since` to accept an optional `on_skip: Callable[[str], None]` callback; Story 10.3 wires the callback to `counter.labels(reason=...).inc()`. Story 10.2 path passes `None` → unchanged behavior.

Self-verification:
- Test `test_parse_skip_counter_increments_by_reason` — write 3 garbage + 2 unknown-type lines, assert `parse_skip_total{reason="invalid_json"} == 3` AND `{reason="unknown_event_type"} == 2`.

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
- Add a check in `app/main.py` startup that emits `log.warning("metrics_subscriber_bind_external_interface_suspected", host=settings.metrics_host)` if `settings.metrics_host` is set to a non-loopback / non-`0.0.0.0` / non-`::` value (heuristic: an explicit external IP suggests misconfiguration).
- Update `tests/separability/` and `tests/integration/` to NOT scrape via external host:port; use the FastAPI TestClient (in-process) for unit tests, leaving real-network scrape for Story 10.6's S-4 separability test.

Self-verification:
- `grep -rn "P2-I5" services/metrics-subscriber/src/metrics_subscriber/app/` finds the docstring.
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
        p95 = sorted(latencies)[95]
        assert p95 < 0.1, f"NFR-O8 violation: /metrics p95={p95*1000:.1f}ms ≥ 100ms"
```

Constraints:
- `populated_state` fixture loads a `MetricsState` with the full Story 10.4 metric count (~30 metrics) to ensure the benchmark reflects realistic exposition size — even though Story 10.4 isn't done yet, populate via direct `MetricsState` mutation in the fixture.
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
- `just bootstrap-verify` — green (15/15 imports)

---

## Developer context

### Existing state (post Story 10.2)

- **Story 10.1 done**: scaffold workspace member, `__init__.py` + `__main__.py` (banner-only), `py.typed`, `test_version.py`.
- **Story 10.2 done**: tail loop + cursor persistence, lifespan task, structlog adoption, exit code matrix (0/1/2/3), `bytes_behind` + `wall_clock_lag_s` structured logs every persist, `metrics_subscriber_parse_skip_total{reason}` Counter preview field reserved by VH-13 fix, `MetricsSubscriberSettings` extensible.
- **`packages/events/src/events/log_reader.py`**: shared `EventLogReader` + `iter_new_envelopes_since` (extraction from registry-state per Story 10.2 AC1 — P2-I1 satisfied).
- **`registry-api`**: reference FastAPI factory pattern at `services/registry-api/src/registry_api/app.py` (`build_app`) + `__main__.py` (uvicorn). Mirror conventions: structlog wiring in `__main__.py` only (test pollution avoidance per Story 3.6 AC-4), `AsyncExitStack` for independent teardown, per-app state (NOT module globals).
- **Bootstrap verify**: 15/15 imports (Story 10.1 added metrics-subscriber).
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

## Frontmatter

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
status: ready-for-dev
created: 2026-05-19
created_by: bmad-create-story skill
---
```
