# Story 11.3.9 — `/v1/health` returns REAL signals (not placeholders) — DB-reachable + worker liveness + queue depth

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** `GET /v1/health` to return REAL liveness signals (DB reachable,
worker recently executing, clawhip queue depth) instead of the static
``registry_status="ok"`` / ``worker_status="unknown"`` / ``clawhip_queue_depth=0``
placeholders that Story 11.3.7 shipped as the AC5 stop-gap,
**so that** the telegram-gateway `/ping` command (and any future external
monitoring hook) shows actionable health — green = whole spine is alive,
warning = a specific subsystem is degraded — rather than always showing
"unknown / 0" regardless of actual cluster state.

## Background — what Story 11.3.7 left as a stop-gap

Story 11.3.7's AC5 added `services/registry-api/src/registry_api/routes/health.py`
with this shape (verbatim, lines ~75-81):

```python
return HealthResponse(
    registry_status="ok",        # static — does not probe SQLite
    worker_status="unknown",     # placeholder — registry-api can't see workers
    clawhip_queue_depth=0,       # placeholder — registry-api can't see queue
    version=__version__,
)
```

The route's own docstring (lines 13-18) acknowledges this:

> The placeholder values for `worker_status` and `clawhip_queue_depth`
> reflect that registry-api itself does not (yet) track worker liveness or
> queue depth — those signals live in the worker-wrapper + clawhip-daemon
> services respectively. FR17 / a future platform-observability story is
> expected to expand this endpoint to query a shared status registry (or
> add a sibling `/v1/ready` readiness probe with a cheap `SELECT 1`).

This story IS that "future platform-observability story" — Story 11.3.9 is
the 2nd of the 3-story Epic-11.3 close-out tail (11.3.8 events-perm →
**11.3.9 /v1/health real signals** → 11.3.10 MCP-init flake fix).

The `HealthResponseLocal` mirror in
`services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:82-112`
already carries a `TODO(story-TBD)` pointing at exactly this work
(lines 97-101), and uses **permissive `str` typing** (not `Literal`) for
`registry_status`/`worker_status` precisely so this story can extend the
state vocabulary without a client-side breaking change.

## Acceptance Criteria

1. **AC1 — `registry_status` is the result of a real probe.**
   `registry-api` queries `registry-state`'s SQLite store with a cheap
   `SELECT 1` (or equivalent) per request and maps the result to:
   - `"ok"` — the query returned in `<200ms` (the NFR-R8 5s p99 budget
     ÷25 — generous headroom for a single-row sanity check).
   - `"degraded"` — the query failed (timeout, connection error,
     `OperationalError`) but the HTTP route itself can still return 200.
     The route MUST NOT return non-200 for a degraded backend — a 5xx
     would make Telegram's `/ping` show "couldn't reach registry"
     instead of the actual degraded signal.
   - The probe is BUDGETED at `200ms` with `asyncio.wait_for(...)` so a
     hung SQLite (e.g. lock contention) doesn't blow the route's own
     budget. On budget exhaustion → `"degraded"`.

2. **AC2 — `worker_status` reflects recent worker activity.**
   `registry-api` queries `registry-state`'s materialised view of recent
   events (last 60s window) and maps to:
   - `"ok"` — at least 1 `task.execution.started` OR `task.execution.completed`
     OR `worker.heartbeat` event in the window.
   - `"idle"` — no worker events in the window, but the registry IS reachable
     (the platform is up, just no in-flight work).
   - `"unknown"` — registry-state unreachable (paired with `registry_status="degraded"`).
   The 60s window is configurable via env `OMB_HEALTH_WORKER_WINDOW_S`
   (default 60, range 5-3600). Stored as a constant in `routes/health.py`
   with a literal default and read from settings only when set.

3. **AC3 — `clawhip_queue_depth` reflects the actual queue.**
   Source of truth: count `task.created` events with NO corresponding
   `task.execution.started` event in the same task_id, within a
   look-back window (default 300s — covers the typical worker pickup
   latency). `registry-api` reads this via a single SQL query against
   `registry-state`'s materialised view, NOT by calling clawhip-daemon
   over HTTP (separability — registry-api MUST NOT add a new outbound
   dependency on clawhip-daemon for a liveness probe; that would couple
   them and defeat the S-3 separability gate).
   - On the same `OperationalError` / timeout path that triggers
     `registry_status="degraded"`: return `clawhip_queue_depth=0` (the
     value is meaningless when the registry is degraded; pinning to 0
     avoids surfacing stale cached counts).
   - Defensive clamp to `[0, 1_000_000]` (matches `HealthResponseLocal`
     `Field(ge=0, le=1_000_000)` constraint at line 109).

4. **AC4 — Route remains a `<500ms` p95 endpoint.**
   Total budget for the whole `/v1/health` response is `500ms p95`
   (matching the NFR-R8 ÷10 conservative budget for a liveness probe
   that's called frequently by uptime monitors). Concretely:
   - AC1 SELECT 1: `200ms` budget
   - AC2 worker-events window query: `150ms` budget
   - AC3 queue-depth query: `150ms` budget
   - The 3 queries run **in parallel** via `asyncio.gather(return_exceptions=True)`
     so the total wall time is `max(200, 150, 150) = 200ms`, not their
     sum. Per-query timeout via `asyncio.wait_for`.
   - Integration test asserts wall time `<500ms` on the test host
     (with the in-process registry-state spun up) — `@pytest.mark.slow`
     budget marker for portability across slower CI runners.

5. **AC5 — Failure mode discipline:** the route NEVER raises a 5xx.
   Every failure path of the 3 probes maps to a "degraded" / "unknown"
   / `0` value in a 200 response. The only way the route returns
   non-200 is if the route itself crashes (panic-tier bug, not a
   downstream degradation). Failure-mode test:
   `tests/contract/test_health_route_failure_modes.py` (NEW) asserts:
   - SQLite locked → 200 with `registry_status="degraded"`.
   - registry-state DB file removed mid-request → 200 with
     `registry_status="degraded"`.
   - Cancelled task (asyncio.CancelledError propagates through gather)
     → 200 with all 3 fields in `"degraded"` / `"unknown"` / `0`.

6. **AC6 — `HealthResponseLocal` client untouched.**
   `services/telegram-gateway/.../registry_client.py:82-112` already uses
   permissive `str` typing for `registry_status` + `worker_status` (H1
   note at line 89-95). The new state vocabulary (`"degraded"`, `"idle"`)
   parses cleanly through the existing `extra="ignore"` + `str` schema —
   no client-side change required. The contract-parity test at
   `tests/contract/test_key_status_client_server_shape_parity.py` (Story
   11.5.1 / AC7) is the precedent; a mirror test for `/v1/health` is
   added at `tests/contract/test_health_client_server_shape_parity.py`
   (NEW) — field-name parity + Field-constraint parity (`min_length`,
   `max_length`, `ge`, `le`) — asserts the wire shape stays in lock-step.

7. **AC7 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # 240=baseline (0-new)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q services/registry-api/   # all unit/integration changes pass
   uv run pytest -x -q tests/contract/test_health_client_server_shape_parity.py
   uv run pytest -x -q -m "not slow"            # regression no new fails
   ```

8. **AC8 — `/code-review` default effort + batch-apply.** Default effort
   is right-sized: the diff is a single route + 1 helper module + 2 new
   test files. Bump to high effort only if a paranoid pass on the
   `OperationalError`/timeout semantics is desired (the `asyncio.wait_for`
   wrapping around aiosqlite is the highest-risk surface).

## Tasks / Subtasks

- [ ] **Task 1 — Add a registry-state read-only probe API** (AC1, AC2, AC3)
  - [ ] Decide the boundary: does `registry-api` open a NEW aiosqlite
        connection to the shared SQLite file (the simplest path; the
        DB file IS on the shared volume), or does it call a new
        `registry-state` HTTP endpoint?
        **Recommended:** direct aiosqlite read-only (`mode=ro`) URL.
        Reason: avoids a new outbound dependency, matches S-3 separability,
        and the file IS on the shared `omb`-group volume so the registry-api
        uid can read it. Document the decision in Dev Agent Record.
  - [ ] Create `services/registry-api/src/registry_api/probes/health_probes.py`
        (NEW): 3 functions — `probe_registry_reachable() -> bool`,
        `probe_worker_recently_active(window_s: int) -> bool`,
        `probe_queue_depth(lookback_s: int) -> int`. Each takes the
        `aiosqlite.Connection` (or a connection factory) + budget.
- [ ] **Task 2 — Refactor `routes/health.py` to run the 3 probes in parallel** (AC1-AC5)
  - [ ] Replace the static placeholder return with `asyncio.gather(
        probe_registry_reachable(...), probe_worker_recently_active(...),
        probe_queue_depth(...), return_exceptions=True)`.
  - [ ] Map each gather result to the wire shape per AC1-AC3 (degraded
        on `Exception` / `asyncio.TimeoutError`).
  - [ ] Add structured-log entry on any degraded path (`log.warning(
        "v1_health_degraded", reason=..., probe="registry"|"worker"|"queue")`)
        — observability signal so operators don't have to grep through
        request logs to see WHICH probe degraded.
- [ ] **Task 3 — Contract-parity test** (AC6)
  - [ ] Create `tests/contract/test_health_client_server_shape_parity.py`
        mirroring `test_key_status_client_server_shape_parity.py`
        (Story 11.5.1 / AC7): field names + Field constraints
        (`min_length`, `max_length`, `ge`, `le`) match between
        `HealthResponse` (server) and `HealthResponseLocal` (client).
- [ ] **Task 4 — Failure-mode test** (AC5)
  - [ ] Create `tests/contract/test_health_route_failure_modes.py`:
        - SQLite locked (simulate via `BEGIN EXCLUSIVE` on a sidecar
          connection) → assert 200 + `registry_status="degraded"`.
        - DB file removed mid-request → assert 200 + degraded.
        - `asyncio.CancelledError` mid-probe → assert 200 with degraded fields.
- [ ] **Task 5 — Latency budget test** (AC4)
  - [ ] In `tests/integration/test_health_latency.py` (NEW,
        `@pytest.mark.slow`), boot in-process registry-api with a
        seeded registry-state DB (10K events), `urlopen` `/v1/health`
        50 times, assert p95 `<500ms`.
- [ ] **Task 6 — Validation gates** (AC7); fix anything that breaks.
- [ ] **Task 7 — Code review** (AC8) at default effort; apply findings.

## Dev Notes

### Source map (file:line guardrails)

- **Current route:** `services/registry-api/src/registry_api/routes/health.py`
  — Story 11.3.7 added (placeholder values at lines 75-81). Replace the
  return body, keep the `HealthResponse` Pydantic shape unchanged.
- **Client mirror (DO NOT MUTATE schema):** `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:82-112`
  — `HealthResponseLocal`. Already permissive `str` typing per H1
  comment lines 89-95; new state values forward verbatim. The
  `TODO(story-TBD)` at lines 97-101 explicitly points at THIS story.
- **Route registration:** `services/registry-api/src/registry_api/app.py:325`
  — `include_router(...)` call. No change needed; the route URL is stable.
- **Test precedent — contract parity:** `tests/contract/test_key_status_client_server_shape_parity.py`
  (Story 11.5.1 / AC7) — copy-template for AC6.
- **Test precedent — failure modes:** `services/registry-api/src/registry_api/test_app.py`
  has SQLite-error pytest patterns; reuse the `monkeypatch.setattr` /
  `aiosqlite.Connection` mocking idiom.
- **`registry-state` SQLite path:** `OMB_REGISTRY_STATE_DB_URL` (default
  `sqlite+aiosqlite:///var/lib/oh-my-bmad/registry/state.sqlite3`). Open
  read-only by appending `?mode=ro&uri=true` to the URL.

### Constraints

- **NO `services/*/adapters/mcp_clients.py` touched** — this story is
  NOT in the a0ca050 P0 code path; the soft-warning delegation hint is
  informational only.
- **NO new `os.environ.copy()` / `dict(os.environ)`** — config still
  comes via the existing pydantic-settings pattern; the new
  `OMB_HEALTH_WORKER_WINDOW_S` and `OMB_HEALTH_QUEUE_LOOKBACK_S` env
  vars MUST be added to the existing `RegistryApiSettings` (or sibling)
  via pydantic-settings, not via direct `os.environ` reads.
- **NO new outbound HTTP dependency from `registry-api`** — direct
  aiosqlite read of the shared SQLite file (read-only mode). This
  preserves the S-3 separability gate (registry-api boots without
  worker-wrapper / clawhip-daemon present).
- **Read-only SQLite connection** — `sqlite:?mode=ro&uri=true` MUST be
  used so a panic-bug in the route can't corrupt registry-state's
  source of truth. FR26 single-writer rule is preserved (registry-state
  remains the sole writer; registry-api is read-only consumer).
- **Per-request connection lifecycle** — open + close per request
  (not a long-lived shared connection) — keeps the route's blast
  radius bounded and sidesteps aiosqlite's known event-loop binding
  issues across long-lived asyncio tasks.
- **Budgets are HARD** — every probe is wrapped in `asyncio.wait_for`
  with its individual budget; a hung probe must NOT propagate to the
  HTTP response. Per AC5, the only non-200 is a panic crash.
- **Structured logging discipline** — degraded paths emit
  `log.warning("v1_health_degraded", reason=..., probe=...)` using the
  existing `structlog` (or whatever `log` is bound to in `routes/health.py`).
  NO PII / NO HMAC-related fields in the log payload.
- **No new event emission** — `/v1/health` is a read-side probe;
  emitting events on a liveness check would explode event-log volume.
  FR24/FR25 emission gates UNAFFECTED.

### Project Structure Notes

- New helper module `services/registry-api/src/registry_api/probes/health_probes.py`
  is the canonical location for cross-route health probes. If a future
  `/v1/ready` route lands (per the AC stop-gap docstring's hint), it
  reuses the same probe functions.
- 2 new contract tests live under `tests/contract/` (alongside Story
  11.5.1's parity test) — same `@pytest.mark.contract` marker.
- 1 new integration test at `tests/integration/test_health_latency.py`
  with `@pytest.mark.slow` (latency tests need a real DB seed and are
  the slowest tier of the test suite).

### References

- [Source: `services/registry-api/src/registry_api/routes/health.py:13-18`
  — Story 11.3.7's own docstring forecasting this story.]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:97-101`
  — `TODO(story-TBD)` in `HealthResponseLocal` pointing here.]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py:89-95`
  — H1 note: permissive `str` typing precisely for forward-compat with
  new state values landed in this story.]
- [Source: FR17 (platform-observability gateway) + NFR-R8 (5s p99 budget).]
- [Source: FR26 (single-writer rule) — registry-api MUST open read-only.]
- [Source: Story 11.5.1 / AC7
  `tests/contract/test_key_status_client_server_shape_parity.py`
  — copy-template for AC6.]
- [Source: aiosqlite docs — `?mode=ro&uri=true` URL fragment for
  read-only opens.]

## Previous-story intelligence

- **Story 11.3.7 (AC5)** shipped the `/v1/health` route with intentional
  placeholders. This story is the explicit close-out — the AC stop-gap
  documented "FR17 / a future platform-observability story is expected
  to expand this endpoint", and that's exactly this.
- **Story 11.5.1 (AC2 + AC7)** established the contract-parity test
  pattern (`HealthResponseLocal` ⇄ `HealthResponse` field+constraint
  parity). Mirror that pattern verbatim for AC6.
- **Story 11.3.8 (this PR's parent)** established `ensure_shared_dir`
  for shared-volume permissions — registry-api can now reliably
  `open(state.sqlite3)` from the shared `omb`-group volume without
  hitting the same `PermissionError` that would have blocked AC1's
  read-only probe.
- **Epic 11 retro L9 mirror-identity canon** — server `HealthResponse`
  and client `HealthResponseLocal` are the 4th known mirror-identity
  surface after MCP-env allowlists, HMAC canonical-string, and the
  filesystem-helper pattern (Story 11.3.8). The contract-parity test
  is the formal enforcement.

## Git intelligence summary

Last 5 commits on this lineage:

- `fde786e` (epic-11.3.8) — events/ dir 0o2775 via ensure_shared_dir helper
- `808c24a` (epic-11.3.8) — file Story 11.3.8
- `68015ce` (epic-12.1.1) — /bmad-code-review pass-2 fixes for 12.1.1
- `4153b86` (epic-12.1.1) — /code-review default fixes for 12.1.1
- `8b8e5ce` (epic-12.1.1) — Story 12.1.1 initial impl

Story 11.3.9 branches off `epic-11.3.8` (just-pushed tip with PR #1
open against `epic-12.1.1`) so the chain remains linear:
11.3.7 → 11.5.1 → 12.1.1 → 11.3.8 → **11.3.9**.

## Frontmatter

```yaml
---
story_id: 11.3.9
story_key: 11-3-9-v1-health-real-signals
parent_epic: 11
phase: 2
fr_refs: [FR17, FR26]
nfr_refs: [NFR-R8]
arch_refs:
  - "Story 11.3.7 / AC5 — /v1/health route (routes/health.py) with placeholder values; this story's explicit close-out"
  - "Story 11.5.1 / AC7 — contract-parity test pattern (KeyStatusResponse ⇄ KeyStatusResponseLocal); mirror for HealthResponse"
  - "Story 11.3.8 — ensure_shared_dir helper + fixed shared-volume permissions; prerequisite for read-only aiosqlite open"
  - "HealthResponseLocal at telegram-gateway/handlers/registry_client.py:82-112 (permissive str typing per H1; TODO(story-TBD) at 97-101 points here)"
  - "FR26 single-writer rule — registry-api MUST open SQLite read-only"
estimated_complexity: MEDIUM
priority: MEDIUM (closes Story 11.3.7 stop-gap; platform-observability close-out for Epic 11.3 tail; not a production blocker but the placeholder responses degrade operator UX in /ping)
blocks: []
unblocks:
  - Operator /ping in Telegram shows actionable green/warning instead of always "unknown"
  - Future external uptime monitors (Pingdom, UptimeRobot) get a real signal to alert on
  - /v1/ready readiness probe (future story) reuses the same probe helpers
  - 2nd of 3-story Epic-11.3 close-out tail
---
```

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Definition of Done

- `/v1/health` returns real signals (not placeholders) for all 3 fields:
  `registry_status`, `worker_status`, `clawhip_queue_depth`.
- All 3 probes wrapped in `asyncio.wait_for` with individual budgets;
  total p95 < 500ms verified by latency test.
- Route NEVER returns 5xx for a degraded backend (per AC5).
- Contract-parity test confirms `HealthResponse` (server) and
  `HealthResponseLocal` (client) stay field+constraint identical.
- Failure-mode test covers SQLite-locked, DB-removed, and
  asyncio-cancelled paths.
- Validation gates green: ruff/format clean, mypy 240=baseline 0-new,
  discipline 0, regression sweep no new fails.
- Code-review at default effort discharged; findings batch-applied.
- `sprint-status.yaml` flips `11-3-9-v1-health-real-signals`:
  backlog → ready-for-dev → in-progress → review → done (after
  epic-11.3.8 in dependency order).
- No `mcp_clients.py` touched; no new `os.environ.copy()` /
  `dict(os.environ)`; no new outbound HTTP dep from registry-api;
  read-only SQLite open preserves FR26 single-writer rule; Epic 11
  acceptance gate (HMAC isolation grep) continues to pass.
