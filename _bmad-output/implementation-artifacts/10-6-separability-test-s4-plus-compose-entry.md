# Story 10.6 — Separability test S-4 + add metrics-subscriber to compose stack

Status: **ready-for-dev**

## Story

**As** the platform operator
**I want** the `metrics-subscriber` to be a first-class service in `docker-compose.yml` (with a healthcheck that gates compose `service_healthy` probes) AND a separability test that spins the stack up WITHOUT `metrics-subscriber` to prove the rest of the platform serves identically
**so that** (a) operators get derived metrics out-of-the-box on a default `docker compose up`, (b) the Phase-2 promise of decoupled subscribers is verifiable in CI — adding `metrics-subscriber` did not introduce a new hidden dependency in any producer service (NFR-M4/M5 discipline extended; FR62a satisfied).

Story 10.6 closes Epic 10's acceptance gate. The 4 requirements:
1. ✅ `/metrics` returns documented metric set — Story 10.4 done
2. ✅ NFR-O8 benchmark (<100ms p95) verified in CI on fixed runner — Story 10.3 (p95=0.94ms; Story 10.4 re-measured at 0.65ms)
3. **Separability test S-4 green — Story 10.6 scope (this story)**
4. ✅ ADR-0005 authored and `accepted` — Story 10.3 done, Story 10.4 §Cardinality + §Deferred amendments, Story 10.5 §CI regression gate amendment

## Acceptance criteria

### AC1 — `metrics-subscriber` service entry in `docker-compose.yml`

Add a new service block to `docker-compose.yml` (root, NOT `tests/separability/docker-compose.s4.yml`) after `clawhip-daemon` (or alphabetically appropriate slot — current convention is registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon, then metrics-subscriber). Schema:

```yaml
  metrics-subscriber:
    build:
      context: .
      dockerfile: services/metrics-subscriber/Dockerfile
    image: ${OMB_IMAGE_REGISTRY:-ghcr.io/r2d2}/oh-my-bmad-metrics-subscriber:${OMB_VERSION:-dev}
    environment:
      ENV: ${ENV:-prod}
      OMB_METRICS_EVENT_LOG_DIR: /var/lib/oh-my-bmad/registry/events
      OMB_METRICS_CURSOR_PATH: /var/lib/oh-my-bmad/metrics-subscriber/cursor.json
      OMB_METRICS_POLL_INTERVAL_S: "${OMB_METRICS_POLL_INTERVAL_S:-0.5}"
      OMB_METRICS_METRICS_HOST: "0.0.0.0"
      OMB_METRICS_METRICS_PORT: "9090"
      OMB_METRICS_RUN_MODE: server
    depends_on:
      registry-state:
        condition: service_healthy
    user: "${OMB_METRICS_UID:-10003}:${OMB_METRICS_GID:-10000}"
    init: true
    restart: unless-stopped
    networks: [oh-my-bmad-net]
    volumes:
      - oh-my-bmad-data:/var/lib/oh-my-bmad
    healthcheck: *healthcheck
```

Constraints (per P2-I5 in architecture.md):
- **NO `ports:` block** — internal-only per P2-I5. Operator scrapes via SSH tunnel or co-located Prometheus container on the same docker network.
- **`depends_on: registry-state: service_healthy`** — the subscriber tails JSONL written by `registry-state`'s materializer; if `registry-state` isn't healthy, the JSONL doesn't have today's file. NO `depends_on` on `registry-api` (subscriber is decoupled per P2-I3).
- **Reuse `*healthcheck` anchor** — same `x-healthcheck` definition the other 6 services share. The Story 10.3 `/healthz` route returns `{"status": "ok"}` or `{"status": "degraded"}` (503) — the anchor's curl-based probe will see 200 from `/healthz` when the tail task is alive.
- **Verify the healthcheck anchor** at `docker-compose.yml:26` — its `test:` command must point at `/healthz` (not `/metrics`, which is a hot path under Prometheus scrape and would skew latency).

Self-verification:
- `docker compose config --services` lists `metrics-subscriber`.
- `docker compose config metrics-subscriber` shows the resolved schema.
- `grep -F "metrics-subscriber" docker-compose.yml` returns ≥ 1 match.

### AC2 — `services/metrics-subscriber/Dockerfile` (NEW)

Create `services/metrics-subscriber/Dockerfile` mirroring the existing pattern (look at `services/registry-state/Dockerfile` or `services/registry-api/Dockerfile`). Multi-stage:

1. **builder stage**: `python:3.12-slim-bookworm` base, install `uv`, copy workspace, `uv sync --frozen --no-dev --package metrics-subscriber`.
2. **runtime stage**: distroless or slim base, copy the virtualenv from builder, `USER 10003:10000`, `ENTRYPOINT ["python", "-m", "metrics_subscriber"]`.

Reference: `services/registry-api/Dockerfile` — same layout (FastAPI + uvicorn). Adapt: only metrics-subscriber's deps (`prometheus-client`, `fastapi`, `uvicorn[standard]`, `events` workspace package).

Self-verification:
- `test -f services/metrics-subscriber/Dockerfile`.
- `docker build -t test-metrics-subscriber:dev -f services/metrics-subscriber/Dockerfile .` succeeds locally.
- Container starts when given `OMB_METRICS_EVENT_LOG_DIR` pointing to a valid (possibly empty) directory.

### AC3 — `OMB_METRICS_UID`/`OMB_METRICS_GID` UID convention + `.env.example` extension

Each compose service runs as a dedicated non-root user (pattern from `OMB_S3_UID:-10002`, etc.). Add:
- `OMB_METRICS_UID=10003` (next free UID after the existing assignments)
- `OMB_METRICS_GID=10000` (shared `oh-my-bmad` group)

Update `.env.example` (or the project's environment template) with the new UID/GID defaults + documentation comment.

Self-verification:
- `grep -F "OMB_METRICS_UID" .env.example` returns the new line.
- `docker compose config metrics-subscriber | grep user:` shows `10003:10000` resolved.

### AC4 — Separability overlay `tests/separability/docker-compose.s4.yml` (NEW)

Mirror `tests/separability/docker-compose.s1.yml` pattern (108 lines). Two interpretation paths considered:

| Path | Mechanism | Test invocation |
|---|---|---|
| **A. Overlay removes service** | `docker-compose.s4.yml` defines a stack WITHOUT `metrics-subscriber` (only the 6 baseline services). | `docker compose -f tests/separability/docker-compose.s4.yml up` |
| **B. Profile-based disable** | Add `profiles: ["metrics"]` to root `docker-compose.yml` `metrics-subscriber` block; default `docker compose up` activates no profiles → subscriber NOT started. Activated via `COMPOSE_PROFILES=metrics` or `--profile metrics`. | `COMPOSE_PROFILES= docker compose up` (no metrics) vs `COMPOSE_PROFILES=metrics docker compose up` |

**Decision (D1): use Path A (overlay file).** Rationale: matches S-1/S-2/S-3 convention; the epic wording "spin up with OMB_METRICS_DISABLED=1" implies env-var driven, but Path A is cleaner Docker semantics (one config file, no env var pollution). The root `docker-compose.yml` ships metrics-subscriber ENABLED-by-default per Epic 10 goal (operators get derived metrics out-of-the-box). Path B's profile gating is reserved for the future Epic 13 litestream sidecar (which IS opt-in).

The S-4 overlay file:
```yaml
# tests/separability/docker-compose.s4.yml
# Story 10.6 — S-4 separability harness (FR62a / NFR-M4/M5 discipline).
#
# Mirrors the baseline 6-service stack from docker-compose.yml but
# EXCLUDES metrics-subscriber. Used by test_s4_metrics_subscriber_optional.py
# to prove that removing the subscriber does not affect the rest of the
# stack's startup, healthcheck, or end-to-end behavior (NFR-M4/M5).

services:
  registry-state: # ... (copy from docker-compose.yml)
  registry-api:   # ... (copy)
  telegram-gateway: # ... (copy)
  orchestrator-adapter: # ... (copy)
  worker-wrapper: # ... (copy)
  clawhip-daemon: # ... (copy)

  # NO metrics-subscriber — that's the point of this overlay.

networks:
  oh-my-bmad-net: # ... (copy)

volumes:
  oh-my-bmad-data: # ... (copy)
```

Constraints:
- The overlay MUST be self-contained (no `extends:` magic) — keeps the diff readable when comparing to root compose.
- The overlay MUST use the SAME image references (`${OMB_IMAGE_REGISTRY:-ghcr.io/r2d2}/...`) so CI doesn't rebuild.
- Add a leading comment block explaining the file's purpose (mirror S-1's style).

Self-verification:
- `test -f tests/separability/docker-compose.s4.yml`.
- `docker compose -f tests/separability/docker-compose.s4.yml config --services` lists 6 services (NO metrics-subscriber).
- `diff <(yq '.services | keys' docker-compose.yml | sort) <(yq '.services | keys' tests/separability/docker-compose.s4.yml | sort)` shows the only difference is `metrics-subscriber`.

### AC5 — `tests/separability/test_s4_metrics_subscriber_optional.py` (NEW)

Mirror `tests/separability/test_s1_cold_worker_swap.py` pattern (~400 lines). Two-phase test:

**Phase 1 — baseline stack with metrics-subscriber (uses root `docker-compose.yml`):**
1. Bring up stack via `docker compose -f docker-compose.yml up -d --wait`.
2. Wait for healthy state via `docker compose ps --format json` polling — all 7 services (6 baseline + metrics-subscriber) must reach `Up (healthy)`.
3. Hit `http://registry-api:8080/v1/health` via docker network — assert 200.
4. Hit `http://metrics-subscriber:9090/metrics` — assert 200 + Prometheus format.
5. Tear down via `docker compose down -v --remove-orphans`.

**Phase 2 — overlay stack WITHOUT metrics-subscriber (uses `tests/separability/docker-compose.s4.yml`):**
1. Bring up via `docker compose -f tests/separability/docker-compose.s4.yml up -d --wait`.
2. Wait for healthy state — 6 services must reach `Up (healthy)`; `metrics-subscriber` MUST NOT appear in `docker compose ps`.
3. Hit `http://registry-api:8080/v1/health` — assert 200 (same as Phase 1).
4. Submit a synthetic task via `POST http://registry-api:8080/v1/tasks` — assert 201 (proves the spine still serves identically without the subscriber).
5. Confirm no error in `worker-wrapper` / `clawhip-daemon` logs about missing `metrics-subscriber` (use `docker compose logs <service>` and grep).
6. Tear down.

Constraints:
- Pytest marker: `@pytest.mark.integration + @pytest.mark.slow` (matches S-1/S-2/S-3).
- Use the existing `tests/separability/conftest.py` fixtures (e.g., `compose_stack_up`, `wait_for_healthy`) — DO NOT reinvent. Look at S-1's imports.
- **`@pytest.mark.skipif(not docker_available, reason="...")`** — gracefully skip on developer machines without Docker (mirror S-1's pattern).
- Wall-clock budget per phase: 60 seconds (matches S-1's bootstrap-verify timeouts).
- Total test wall-clock budget: **~3 minutes** (two phases + drain + assertion overhead).

Self-verification:
- `uv run pytest -q tests/separability/test_s4_metrics_subscriber_optional.py -m slow` exits 0 when Docker is available.
- Assertion error messages include the failing service name + healthcheck output for debuggability.

### AC6 — `just bootstrap-verify` recipe extension

Story 10.1 added `metrics-subscriber` to `just bootstrap-verify` (14/14 imports). Story 10.6 needs to verify:
- The `Dockerfile` builds cleanly (lightweight smoke — NOT a full image tag, just a syntax check via `docker buildx build --check`).
- The compose entry resolves cleanly (`docker compose config metrics-subscriber > /dev/null` exit 0).

If the existing `just bootstrap-verify` recipe doesn't already cover compose-resolution sanity, add a step. Otherwise, no changes.

Self-verification:
- `just bootstrap-verify` exits 0; output mentions metrics-subscriber compose validation if added.

### AC7 — Epic 10 acceptance gate documentation

Update the Epic 10 epics.md acceptance gate block to mark all 4 items as `✅` with their completion stories:
- `/metrics` returns documented metric set — Story 10.4 done
- NFR-O8 benchmark (<100ms p95) verified in CI on fixed runner — Story 10.3 done (p95=0.94ms; refined to 0.65ms in Story 10.4)
- **Separability test S-4 green — Story 10.6 done** (this story)
- ADR-0005 authored and `accepted` — Story 10.3 (initial) + Story 10.4 (Cardinality + Deferred amendments) + Story 10.5 (CI regression gate amendment)

Self-verification:
- `grep -A 8 "Epic 10 acceptance gate" _bmad-output/planning-artifacts/epics.md` shows all 4 items checked off with story refs.

### AC8 — Mypy --strict baseline extension

`test_s4_metrics_subscriber_optional.py` adds ~400 lines. Like other separability tests, it lives in `tests/separability/` which is OUTSIDE the strict-mypy scope (per Story 10.5 P1-L6 — `mypy.ini` has `[mypy-tests.*] ignore_errors = True`). Expected: **126 → 126** (unchanged).

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the same count (126) and exit 0.

### AC9 — Validation gates

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0 (126 unchanged)
- `uv run python scripts/check_imports.py` — exit 0
- `uv run python scripts/check_event_registry.py` — exit 0
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run pytest -x -q services/metrics-subscriber packages/events` — all green (unchanged from Story 10.5)
- `uv run pytest -x -q tests/integration/test_metrics_cardinality.py` — all green (unchanged)
- `uv run pytest -x -q -m slow tests/separability/test_s4_metrics_subscriber_optional.py` — green (requires Docker)
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14/14 imports)
- `docker compose config` (root) — exits 0 with 7 services listed
- `docker compose -f tests/separability/docker-compose.s4.yml config` — exits 0 with 6 services listed

---

## Developer context

### Existing state (post Story 10.5)

- **Story 10.1 done**: scaffold workspace member.
- **Story 10.2 done**: tail loop + cursor persistence + exit code matrix.
- **Story 10.3 done**: FastAPI factory, `/metrics` endpoint, `/healthz` endpoint, NFR-O8 benchmark p95=0.94ms.
- **Story 10.4 done**: Full FR62 metric set (~51 timeseries), dispatch table, ADR-0005 §Cardinality + §Deferred amendments.
- **Story 10.5 done**: Cardinality regression test gate, ADR §CI gate amendment, date-rollover hotfix.
- **`docker-compose.yml`**: 6 services (registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon) + migrator (profile-gated) + `x-healthcheck: &healthcheck` anchor.
- **`tests/separability/`**: conftest.py + 3 S-tests (S-1 cold worker swap, S-2 midflight swap, S-3 orchestrator swap) + 3 docker-compose.s*.yml overlays + helper builders.
- **Mypy baseline**: 126 src files post Story 10.5.
- **No Dockerfile yet** for metrics-subscriber — Story 10.6 ships it (mirror registry-api pattern).

### Architecture compliance

- **FR62a** — separability test S-4 verifies the subscriber doesn't introduce new dependencies in producer services.
- **NFR-M4/M5** — pattern extended: removing metrics-subscriber leaves rest of stack identical (NFR-M5 style decoupling).
- **P2-I1** — read-only-subscriber rule preserved: docker-compose only adds metrics-subscriber as a peer consumer of the shared volume (registry-state remains sole writer).
- **P2-I3** — derived projection pattern: subscriber depends ONLY on `registry-state` (for healthcheck gate); no `depends_on` on `registry-api`, `worker-wrapper`, etc.
- **P2-I5** — internal-only: NO `ports:` block in the compose entry; operator scrapes via SSH tunnel.
- **NFR-O1** — observability primacy preserved: no parallel instrumentation introduced by adding the service.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `prometheus-client`, `fastapi`, `uvicorn[standard]` | already pinned (Story 10.3) | Already in `services/metrics-subscriber/pyproject.toml`. |
| no new deps | — | Story 10.6 introduces zero new third-party dependencies. |
| Docker buildx / compose v2 | runtime | Required for separability test execution. |

### File-structure requirements

```
docker-compose.yml                              # MODIFY: add metrics-subscriber service entry (AC1)
services/metrics-subscriber/Dockerfile          # NEW: multi-stage Dockerfile (AC2)
.env.example                                    # MODIFY: add OMB_METRICS_UID/GID defaults (AC3)
tests/separability/docker-compose.s4.yml        # NEW: overlay without metrics-subscriber (AC4)
tests/separability/test_s4_metrics_subscriber_optional.py   # NEW: ~400 lines (AC5)
_bmad-output/planning-artifacts/epics.md        # MINOR: tick off Epic 10 acceptance gate items (AC7)
```

### Testing requirements

- **Pyramid:** S-4 lives at the **separability suite boundary** (full Docker stack lifecycle), exercised via `docker compose up/down`. NO unit-level or integration-level tests added in this story.
- **Test isolation:** each phase brings up a fresh stack with `docker compose down -v --remove-orphans` between phases. The `tests/separability/conftest.py` already provides session-level fixtures that GUARANTEE teardown even when pytest fails — confirm before relying.
- **Docker availability:** `@pytest.mark.skipif(not docker_available, ...)` mirrors S-1's pattern. CI runners with Docker run the test; developer laptops without it skip gracefully.
- **Wall-clock:** total budget ~3 minutes (two compose lifecycles + assertions). Mark `@pytest.mark.slow`.
- **Volume cleanup:** `docker compose down -v` is critical — without `-v`, `oh-my-bmad-data` named volume persists between phases and contaminates state. Use `--remove-orphans` to evict orphaned containers from previous test runs.

### Previous-story intelligence

#### From Story 10.5 (just closed)

- **Date-rollover flake hotfix** (commit `d37f181`): 3 Story 10.2 test files were migrated from hardcoded `2026-05-19.jsonl` to `datetime.now(UTC).date()` pattern. Story 10.6's separability test must NOT introduce new hardcoded dates — use `datetime.now(UTC).date()` if test envelopes are emitted.
- **Cardinality regression gate** (Story 10.5 P1-H1 lesson): the `/metrics` scrape in Phase 1 should sanity-check baseline cardinality ≤ 52 (not the load-bearing 51 because of cursor-offset path child). Add a smoke assertion only — full regression is Story 10.5's job.

#### From Story 10.4 / 10.3 retros

- **Per-app `CollectorRegistry`** (Story 10.3 P1-H1): not relevant to Story 10.6 (Docker compose doesn't construct app instances; uvicorn does).
- **`get_args(ActorKind)` drift-proof derivation** (Story 10.4 P1-H2): if separability test emits envelopes, use canonical `Actor(kind="system", ...)`.
- **Exit code matrix Q6 (0/1/2/3)** (Story 10.2 pass-2): the compose `restart: unless-stopped` policy means the subscriber will retry on exit codes 2/3 (cursor schema refused / corrupt region). Document this in the compose entry's comment if behavior diverges from expectations.

#### From Epic 10 trajectory

- 10.1 (low) → no review
- 10.2 (high) → **3-pass review** (70 findings)
- 10.3 (medium) → 1-pass (20)
- 10.4 (medium-high) → 1-pass (15)
- 10.5 (medium) → 1-pass (17) + hotfix
- **10.6 estimate: low-medium complexity** (mostly YAML + 1 Dockerfile + 1 test file, no algorithmic novelty). Expect 1-pass review.

### Trade-off notes

- **Overlay file (Path A) vs `profiles:` (Path B) for "disable" mechanism**: chose Path A. Reason: matches S-1/S-2/S-3 convention; root compose remains simple (no `profiles:` annotation on the subscriber); the epic's "OMB_METRICS_DISABLED=1" wording is satisfied by "test uses different compose file" (semantic equivalence).
- **`depends_on: registry-state` healthcheck dependency**: chose explicit. Alternative was no `depends_on` (subscriber retries until JSONL file appears). Reason: cleaner startup ordering for operator demos; subscriber's first request to `/metrics` returns valid output even on cold-start.
- **Internal-only port (no `ports:` block)**: matches P2-I5. Operator scrape pattern is SSH-tunneled `curl` or co-located Prometheus container; documented in `app/main.py` docstring + ADR-0005.
- **Healthcheck via `/healthz` not `/metrics`**: rationale in AC1. `/metrics` is a hot path under Prometheus scrape and would skew benchmark numbers; `/healthz` is purpose-built for health probes.
- **No `OMB_METRICS_DISABLED=1` env var introduced**: redundant with Path A overlay. Adding a runtime env-var check inside `__main__.py` to bail before starting uvicorn would mean MORE code, MORE testing surface; the compose-file approach is purely declarative and matches the rest of the codebase's patterns.

### Lessons from prior reviews to apply

- **No hardcoded dates in any test file** (Story 10.5 hotfix lesson) — use `datetime.now(UTC).date()` if dates are needed.
- **Typed exception classes; no substring-match exception discrimination** (Story 10.2 P3-H1) — n/a for Story 10.6 (no Python exception flow added).
- **Per-finding individual checkboxes; no aggregated** (Epic 9 AI-3) — applies to review batch IF surfaced.
- **Spec self-verification clauses MUST match implementation** (Story 10.3 P1-M1, Story 10.4 P1-H2) — every AC has a self-verification block; cross-check after impl.
- **Sprint-status flip `in-progress → review` in the same commit as spec Status flip** (Story 10.4 P1-H5) — Story 10.5 applied this correctly; Story 10.6 must do the same.
- **DAR includes `pytest --collect-only` evidence-line + actual mypy file count** (Story 10.2 P2-M8) — n/a for compose-only changes, but if test file is added, include count.
- **No `pragma: no cover` on operational error paths** (Story 10.2 P3-M6) — n/a (no Python production code added).

### Non-goals (do NOT do in 10.6)

- **Add metrics-subscriber to operator runbook / deployment docs** → out of Phase 2 ops docs scope.
- **Add a Grafana dashboard JSON** → ops docs scope, not project scope.
- **Add a Prometheus container to docker-compose** → operator-side concern; we ship the `/metrics` endpoint, not the scraper.
- **Modify any of the existing 6 services' compose entries** → P2-I1 + FR62a discipline: subscriber addition must not require touching producer services.
- **Add docker-compose entries for the litestream sidecar** → Epic 13 scope.
- **Add `OMB_METRICS_DISABLED=1` env-var support in `__main__.py`** → redundant with Path A overlay (D1).
- **Performance benchmark in compose-up CI** → Story 10.3's NFR-O8 benchmark already covers this (in-process via `httpx.ASGITransport`).

## Out-of-scope risk flags

- **Docker buildx availability on CI**: `services/metrics-subscriber/Dockerfile` builds in the CI image. If CI uses an older Docker image without `buildx`, fall back to legacy `docker build`. Verify via `docker buildx version` in CI workflow.
- **`oh-my-bmad-data` named-volume contamination**: between Phase 1 and Phase 2, `down -v` MUST execute even on assertion failure. Use try/finally pattern or pytest's `request.addfinalizer`.
- **Race between subscriber's first scrape and `service_healthy` probe**: the healthcheck's `interval` + `retries` must be tolerant of the subscriber's startup time (tail loop initialization + first cursor restore). If `*healthcheck` anchor's default interval is too aggressive, override per-service.
- **`docker compose ps --format json`** output schema varies by compose version. Pin the parsing to Compose v2.20+ semantics (current convention in tests/separability/).
- **CI runner Docker socket access**: GitHub Actions runners need Docker available. Verify the CI workflow has `services: docker-in-docker` or runs on `ubuntu-latest` which includes Docker by default.

## Decisions (resolved before implementation)

- **D1 — Path A (overlay file) for separability disable.** Matches S-1/S-2/S-3 convention; cleaner Docker semantics; no env-var pollution; satisfies epic's "OMB_METRICS_DISABLED=1" wording via semantic equivalence.
- **D2 — `metrics-subscriber` default-ON in root `docker-compose.yml`** (no `profiles:` annotation). Epic 10 goal is "operators get derived metrics out-of-the-box".
- **D3 — Healthcheck via `/healthz` not `/metrics`.** `/healthz` is purpose-built; `/metrics` is hot path under Prometheus scrape.
- **D4 — `depends_on: registry-state: service_healthy`** ONLY. NO `depends_on` on other services (P2-I3 derived projection — subscriber's only dependency is JSONL file availability gated by registry-state's healthcheck).
- **D5 — UID 10003, GID 10000.** Next free UID after existing service assignments; shared `oh-my-bmad` group.
- **D6 — Wall-clock budget: 3 minutes total** for S-4 test (60s per phase + drain + assertions). Mark `@pytest.mark.slow` so it runs in the slow CI gate, not the inner loop.

## Definition of done

- All 9 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `10-6-separability-test-s4-plus-compose-entry: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- Epic 10 acceptance gate documentation updated (AC7) — all 4 items ticked with story refs.
- `epics.md` epic-10 acceptance gate block updated; consider marking `epic-10` status `done` once all 6 stories are complete (Story 10.6 IS the last).
- Dev Agent Record filled in (implementation summary, files changed, mypy baseline delta, S-4 wall-clock measurement, surprises/deviations).
- No regressions in: `tests/separability/test_s1*.py`, `tests/separability/test_s2*.py`, `tests/separability/test_s3*.py`, full pytest suite.
- `docker compose -f docker-compose.yml up -d --wait` reaches 7/7 healthy locally (or in CI with Docker).
- `docker compose -f tests/separability/docker-compose.s4.yml up -d --wait` reaches 6/6 healthy locally.

---

## Frontmatter

```yaml
---
story_id: 10.6
story_key: 10-6-separability-test-s4-plus-compose-entry
parent_epic: 10
phase: 2
fr_refs: [FR62a]
nfr_refs: [NFR-M4, NFR-M5]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "Derived projection pattern (P2-I3, ADR-0005)"
  - "No public ingress (P2-I5)"
estimated_hours: 3-5
priority: high (CLOSES Epic 10 — all 4 acceptance-gate items finalized by this story)
blocks:
  - epic-10-retrospective (optional — opens once 10.6 closes)
  - epic-11 (HMAC + approval inbox — next epic in sequence)
blocked_by:
  - 10.3 (FastAPI factory + /healthz — done)
  - 10.4 (full FR62 metric set — done)
  - 10.5 (cardinality regression test — done)
status: ready-for-dev
created: 2026-05-20
created_by: bmad-create-story skill
---
```
