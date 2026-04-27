# Story 2.15: S-3 separability test — orchestrator pass-through

Status: review

## Story

As **the CI pipeline (and operators verifying FR35 / NFR-M5)**,
I want **(a) a `tests/fixtures/null-orchestrator/` package with a Python script + Dockerfile that subscribes to the JSONL event log, detects each `task.created` event, and emits the canonical task lifecycle (`task.planning.started` → `task.plan.ready` → `task.execution.started` → `task.completed`); (b) `ORCHESTRATOR_IMAGE` env-var indirection in `docker-compose.yml` so swapping the orchestrator requires only a single env-var change; (c) `tests/separability/test_s3_orchestrator_swap.py` that boots the compose stack with `ORCHESTRATOR_IMAGE=null-orchestrator:latest`, POSTs a task to registry-api, waits for the task to reach `completed`, and asserts no spine source code (registry-state, registry-api, clawhip-bridge, worker-wrapper) was modified to make the test pass**,
so that **the orchestrator-layer swappability claim (FR35: "no changes required to Registry, Event Bus, or Worker source code") and NFR-M5 ("single-env-var change") become CI-verified facts rather than asserted via belief**.

## Acceptance Criteria

1. **AC-1: Compose env-var indirection** — `docker-compose.yml`:
   - The `orchestrator-adapter` service's `image:` field becomes:
     ```yaml
     image: ${ORCHESTRATOR_IMAGE:-${OMB_IMAGE_REGISTRY:-ghcr.io/r2d2}/oh-my-bmad-orchestrator-adapter:${OMB_VERSION:-dev}}
     ```
   - Default behavior unchanged (real orchestrator-adapter image when `ORCHESTRATOR_IMAGE` is unset).
   - Override via `ORCHESTRATOR_IMAGE=null-orchestrator:latest` (or any other compatible image).
   - The `build:` directive remains so `docker compose build` still tags the local default image. The `null-orchestrator:latest` image is built separately by the test harness (see AC-3).

2. **AC-2: `tests/fixtures/null-orchestrator/` package** — new directory containing:

   - **`null_orchestrator.py`** — main script with `if __name__ == "__main__": asyncio.run(main())`. Behavior:
     1. Reads `EVENT_LOG_DIR` env var (default `/var/lib/oh-my-bmad/registry/events`).
     2. Tails the most recent `*.jsonl` file (Story 2.4's pattern: `_read_new_envelopes_since` polling at 100ms intervals).
     3. For each new envelope of type `task.created`, extracts `task_id` and emits four follow-up events to the same day's JSONL file via `EventLogWriter.append`:
        - `task.planning.started(task_id=...)`
        - `task.plan.ready(task_id=..., plan_summary="null-orchestrator pass-through")`
        - `task.execution.started(task_id=..., session_id=<new>)`
        - `task.completed(task_id=..., summary="null-orchestrator completion", pr_url=None)`
     4. Touches `/tmp/ready` after the first poll iteration so the compose healthcheck passes.
     5. Logs each emit to stdout (operator visibility).
     6. Stops on SIGTERM/SIGINT (clean shutdown).

   - **`Dockerfile`** — multi-stage build (mirrors `scripts/migrator/Dockerfile` pattern):
     - Stage 1: `python:3.12-slim-bookworm` + `uv` to sync workspace deps (events, registry-state for `EventLogWriter`).
     - Stage 2: slim runtime with `/opt/venv` + null_orchestrator.py + non-root `null` user.
     - ENTRYPOINT: `["python", "-m", "null_orchestrator"]` OR `["python", "/app/null_orchestrator.py"]`.

   - **`pyproject.toml`** — minimal package definition with deps `events>=0.3.0`, `registry-state>=0.5.0` (for `EventLogWriter`).

   - **`__init__.py`** + **`__main__.py`** entrypoint shim if invoking via `python -m null_orchestrator`.

3. **AC-3: Build helper** — `tests/separability/build_null_orchestrator.sh` (or a Python helper):
   - One-shot script that runs `docker build -t null-orchestrator:latest tests/fixtures/null-orchestrator/`.
   - Idempotent: skip rebuild if image already exists with current source SHA tag.
   - Invoked by the test fixture before booting the compose stack.

4. **AC-4: New integration test** — `tests/separability/test_s3_orchestrator_swap.py`:

   **`test_orchestrator_swap_with_null_orchestrator_completes_task_end_to_end`** — the AC-headline test:
   - Marked `@pytest.mark.separability @pytest.mark.slow`.
   - Skip if Docker unavailable (reuse `skip_if_no_docker` pattern; replicate in `tests/separability/conftest.py`).
   - Build the null-orchestrator image (via the AC-3 helper).
   - Boot the compose stack with `ORCHESTRATOR_IMAGE=null-orchestrator:latest` + bind-mounted host data dir (mirrors Story 2.11's pattern).
   - Wait for `registry-api` + `registry-state` healthchecks to flip green.
   - POST `/v1/tasks` with `{"title": "s3-separability-test"}`.
   - Poll `GET /v1/tasks/{id}` (or query the SQLite DB read-only) at 1s intervals; assert `tasks.status == "completed"` within 30s.
   - Assert the JSONL log contains all 5 expected events for the task: `task.created` + the 4 emitted by null orchestrator.
   - Tear down the compose stack.

   **`test_spine_source_code_unchanged`** — git-diff assertion:
   - Marked `@pytest.mark.separability` (NOT slow — runs fast as a subprocess).
   - Run `git diff --name-only HEAD -- 'services/registry-state/src/' 'services/registry-api/src/' 'mcp-servers/clawhip-bridge/src/' 'services/worker-wrapper/src/'`.
   - Assert output is empty (i.e., no working-tree changes to spine source code paths).
   - Note: this asserts the WORKING TREE has no spine modifications. The test is meant to be run against a clean checkout post-implementation; it verifies the story's implementation didn't touch spine code. CI runs it against the merged commit; local devs running with uncommitted spine changes will see it fail (expected — surface accidental coupling early).
   - Add a comment explaining: this test does NOT assert "no spine commits between this story and HEAD" — that would require a baseline-commit ref. It asserts "this PR/branch's diff doesn't touch spine source under src/". Compose YAML changes are config, not source — exempt.

5. **AC-5: `tests/separability/docker-compose.test.yml` overlay**:
   - Bind-mount `oh-my-bmad-data` to `${OMB_S3_DATA_DIR:?}:/var/lib/oh-my-bmad` (host directory under pytest's `tmp_path`).
   - Drop services NOT needed for this test: `telegram-gateway`, `clawhip-daemon`, `worker-wrapper` (the null orchestrator handles task lifecycle alone).
   - Keep: `registry-api`, `registry-state`, `orchestrator-adapter` (which becomes null-orchestrator via `ORCHESTRATOR_IMAGE`).
   - Mirrors the Story 2.11 crash-injection pattern.

6. **AC-6: `null_orchestrator.py` event emission contract**:
   - All envelopes use `Actor(kind="orchestrator", id="null-orchestrator")`.
   - `request_id` synthesized via `new_request_id(clock=SystemClock())`.
   - `parent_event_id` = the `task.created` envelope's `event_id` (chains causally for audit).
   - `emitted_at` / `emitted_at_monotonic_ns` from `SystemClock` (production semantics; deterministic seeding not required for this test).
   - `schema_version="1.0.0"` for all 4 emitted types (per registry registrations from Stories 2.5 + 2.10).

7. **AC-7: Idempotency under restart** — if the null orchestrator restarts mid-task (e.g., crashes between emitting `plan.ready` and `execution.started`), it MUST NOT re-emit events for tasks already partially processed. **Fix**: maintain an in-memory set of task_ids already processed; persist nothing (restart starts fresh). On restart, the orchestrator sees only NEW `task.created` events (those whose `event_id` isn't in any prior `task.planning.started` event in the log). Implement via:
   - On startup, scan the existing JSONL log; build a set of `task_id`s that already have `task.planning.started` events.
   - For each new `task.created` event during tailing, skip if `task_id` is in the set; otherwise emit + add to the set.

   Document this as a Phase-1 best-effort behavior — production orchestrators (Story 5.10+) will use proper task-state queries instead of log-scanning.

8. **AC-8: justfile recipe** — add `test-separability`:
   ```
   test-separability *ARGS="":
       uv run pytest -m separability -v tests/separability/ {{ARGS}}
   ```
   Document in justfile help that this exercises FR34 + FR35 (S-1, S-2, S-3) — Story 2.15 lands S-3; S-1 + S-2 are Stories 5.16 + 5.17c (later).

9. **AC-9: Nightly CI integration** — extend `.github/workflows/nightly.yml` with `s3-separability` job:
   - Runs on `ubuntu-latest`, timeout 20 minutes.
   - Installs Docker + just + uv + Python.
   - Builds the platform's base image: `just build-base`.
   - Runs `just test-separability`.
   - Uploads JUnit XML artifact (matching Story 2.13 pattern).
   - Path filter additions: `tests/separability/**`, `tests/fixtures/null-orchestrator/**`.

10. **AC-10: Delete `tests/separability/test_placeholder.py`** — per Story 2.11/2.12/2.13/2.14 precedent.

11. **AC-11: mypy --strict clean** — extend `lint` recipe's second mypy invocation to include `tests/separability` AND `tests/fixtures/null-orchestrator` (the latter contains `null_orchestrator.py`):
    ```
    uv run mypy --strict --explicit-package-bases tests/crash-injection tests/idempotency tests/migrator tests/separability tests/fixtures/null-orchestrator
    ```
    Add `[mypy-tests.separability.*] ignore_errors = False` override to `mypy.ini` (matching the existing Stories 2.11/2.13/2.14 pattern). The fixtures dir doesn't need `[mypy-tests.fixtures.*]` if it's covered via `--explicit-package-bases` directly, but verify the directory naming (hyphen `null-orchestrator` may cause module-name issues).

12. **AC-12: All architectural gates green**:
    - `check_event_registry`: null_orchestrator emits 4 string literals (`task.planning.started`, etc.); the gate scans `services/` and `packages/` not `tests/fixtures/`, so likely vacuously green. Verify.
    - `check_single_writer`: null_orchestrator writes JSONL only (no SQLite); gate stays green.
    - `check_imports`: null-orchestrator imports from `events` + `registry-state` workspace packages — verify the import graph allows `tests/fixtures/null-orchestrator/` → `services/`. If the gate flags it, exempt the path.

13. **AC-13: Regression** — `just test` count unchanged (the new tests are `@pytest.mark.separability @pytest.mark.slow` — excluded from regular `just test`). `just test-separability` → 2 passed (1 end-to-end + 1 git-diff check) when Docker is available; both SKIP when Docker unavailable. `just lint` 8/8 green. Spine source code untouched (verify via `git diff` after implementation).

14. **AC-14: Atomic commit** titled `feat(separability): story 2.15 — S-3 orchestrator pass-through swap test · FR35 NFR-M5`.

## Tasks / Subtasks

- [ ] **Task 1: Compose env-var indirection** (AC: #1)
  - [ ] Update `docker-compose.yml` `orchestrator-adapter.image` to use `${ORCHESTRATOR_IMAGE:-...}` indirection.
  - [ ] Verify default behavior (no env var) resolves to the existing image tag.

- [ ] **Task 2: Null orchestrator package** (AC: #2, #6, #7)
  - [ ] Create `tests/fixtures/null-orchestrator/` directory with `pyproject.toml`, `Dockerfile`, `null_orchestrator.py`, `__init__.py`, `__main__.py`.
  - [ ] Implement the JSONL-tailing + 4-event-emission pattern.
  - [ ] Implement the in-memory dedupe per AC-7.
  - [ ] Touch `/tmp/ready` for healthcheck after first poll.

- [ ] **Task 3: Build helper + compose overlay** (AC: #3, #5)
  - [ ] Add `tests/separability/build_null_orchestrator.sh` (or Python helper) for image build.
  - [ ] Create `tests/separability/docker-compose.test.yml` overlay (bind-mount + service subset).

- [ ] **Task 4: Integration test + git-diff test** (AC: #4)
  - [ ] Create `tests/separability/test_s3_orchestrator_swap.py` with 2 tests.
  - [ ] Replicate `skip_if_no_docker` in `tests/separability/conftest.py` (don't import cross-tree — small enough to duplicate).
  - [ ] Use the established `subprocess.run(["docker", "compose", "-p", project, ...])` pattern from Story 2.11.

- [ ] **Task 5: justfile + CI + mypy** (AC: #8, #9, #11)
  - [ ] Add `test-separability` recipe.
  - [ ] Extend lint recipe's second mypy invocation.
  - [ ] Add `[mypy-tests.separability.*] ignore_errors = False` to `mypy.ini`.
  - [ ] Add `s3-separability` job to `.github/workflows/nightly.yml` with path filters.

- [ ] **Task 6: Cleanup + regression + commit** (AC: #10, #12, #13, #14)
  - [ ] Delete `tests/separability/test_placeholder.py`.
  - [ ] Verify spine source untouched (`git diff` against the 4 spine paths must be empty when ONLY this story's commits are considered).
  - [ ] `just test` count unchanged.
  - [ ] `just lint` 8/8 green.
  - [ ] `just test-separability` → 2 passed locally with Docker.
  - [ ] `just check-gates-self-test` 3/3.
  - [ ] Single atomic commit per AC-14.

## Dev Notes

### Architecture context

- **`tests/separability/test_s3_orchestrator_swap.py`** is named in Architecture line 741 as the FR35 / NFR-M5 enforcement point.
- **FR35** (PRD line 862): "Platform can swap the default Orchestrator for an alternative Orchestrator implementation (including a pass-through null orchestrator) via a single configuration change, with no changes required to Registry, Event Bus, or Worker source code, DI wiring, or MCP server definitions."
- **NFR-M5** (PRD): "Orchestrator decoupling: replacing the default Orchestrator with a pass-through null implementation requires a single-env-var change and no source-code modification to Registry, Event Bus, or Worker."
- **Architecture line 911**: "FR34 / FR35 (swappable runtime/orchestrator) | Single env-var image override | S-1 / S-3 CI tests".

### Why the spec's `task.execution.requested` is renamed to `task.execution.started`

The spec (epics.md line 940) lists 3 emitted events: `task.planning.started → task.plan.ready → task.execution.requested`. But `task.execution.requested` is NOT a registered event type — only `task.execution.started` is (Stories 2.5 + 2.10 registered all 12 task types). This is a spec typo. The story uses the actual registered type. Document in Spec Amendments.

### Why the null orchestrator emits 4 events, not 3

Spec lists 3 (planning.started, plan.ready, execution.requested) but says "task completes via task.completed". Without a worker, no one emits `task.completed`. Two options:
- **Option A (chosen)**: null orchestrator also emits `task.completed` after `task.execution.started`. This satisfies the spec's "task completes" assertion. Renders the orchestrator a "pass-through" that fakes the entire lifecycle.
- **Option B (rejected)**: add a "scripted-stub worker" fixture that responds to `task.execution.started` with `task.completed`. More moving parts; higher complexity.

Option A is the simplest interpretation of "pass-through null orchestrator" — the orchestrator pretends to orchestrate by emitting the canonical lifecycle events without doing any real work.

### JSONL tailing vs MCP subscription

The spec doesn't mandate how the null orchestrator detects `task.created` events. Two options:
- **JSONL tailing** (chosen): poll the day's `*.jsonl` file at 100ms intervals (matches Story 2.4's `_read_new_envelopes_since` pattern). Simple, no MCP plumbing.
- **clawhip-bridge MCP subscription** (rejected): would require MCP client code in the null orchestrator. Significant complexity for marginal benefit.

JSONL tailing is the pragmatic choice — the null orchestrator is a test fixture, not production infrastructure.

### Compose service subset (test overlay)

The full compose stack has 6 services. For S-3 we need:
- `registry-state` (replays events into SQLite)
- `registry-api` (POST /v1/tasks creates task.created)
- `orchestrator-adapter` (replaced by null-orchestrator)

NOT needed:
- `telegram-gateway` (no operator interaction)
- `clawhip-daemon` (no Telegram sink)
- `worker-wrapper` (null orchestrator fakes worker emissions)

The overlay drops these to keep the test focused. Mirrors Story 2.11's pattern.

### Why the git-diff assertion isn't a CI gate

The git-diff test asserts the WORKING TREE has no spine modifications. This works against:
- A fresh checkout of the merged commit (CI nightly): asserts the commit's diff against HEAD doesn't touch spine paths (vacuously true in CI).
- A local dev branch with this story's commits: asserts the developer didn't accidentally modify spine code.

It does NOT assert "the implementation never modified spine code at any point in history" — that would require a baseline-commit ref. The assertion's intent is: catch accidental coupling DURING DEVELOPMENT. Once merged, the assertion stays as a sentinel — any future PR that modifies spine paths AND breaks separability semantics would surface here.

### `tests/fixtures/null-orchestrator/` directory naming

Hyphen in directory names breaks Python package imports. Two options:
- **Hyphen in directory** (chosen): the directory is NOT a Python package; it contains a flat script. Docker build invokes `python null_orchestrator.py`. Mypy uses `--explicit-package-bases`.
- **Underscore in directory**: `null_orchestrator/` as a proper Python package. Cleaner imports but breaks the convention of `tests/fixtures/<name>/` being a flat fixture dir.

Hyphen is consistent with `tests/crash-injection/` pattern. The `pyproject.toml` inside declares the package name `null-orchestrator` (with hyphen) — pyproject names are PEP 503-normalized and accept hyphens.

### `Actor.kind = "orchestrator"`

The existing `ActorKind` Literal includes `"orchestrator"` (verified via Story 2.10's review where the canonical set was identified: `operator | orchestrator | worker | system | clawhip`). The null orchestrator's emitted events use `kind="orchestrator"` correctly.

### Test runtime budget

Each test boot involves: docker compose build (cached after first run) + 3-service startup + healthcheck wait + task lifecycle (4 emits over ~400ms-1s) + assertion polls. Budget: ≤30s under nightly CI. If the runtime exceeds this materially, document and consider parallelizing across S-1/S-2/S-3 in the future.

### What this story does NOT do

- **Does NOT implement S-1 (worker-swap test) or S-2 (mid-flight worker swap)** — those are Stories 5.16 + 5.17c (Epic 5).
- **Does NOT implement a real orchestrator-adapter** — Story 5.10 (`orchestrator-adapter-omc-supervision`) handles that.
- **Does NOT implement clawhip-bridge MCP subscription for the null orchestrator** — JSONL tailing is sufficient for this test.
- **Does NOT exercise approval/blocker/decision flows** — pure happy-path lifecycle.
- **Does NOT verify the null orchestrator handles concurrent task.created events robustly** — single-task path only. Concurrency tests can be added later if needed.

### Previous Story Intelligence

- **Story 2.11** established the `tests/crash-injection/` Docker compose pattern (compose project name, bind-mount overlay, healthcheck poll, skip_if_no_docker fixture).
- **Story 2.12** established the per-test test-tree mypy strict fix pattern (`[mypy-tests.X.*] ignore_errors = False`).
- **Story 2.13** established the `--junitxml` artifact upload + parametrized-flakiness-signal pattern in nightly.yml.
- **Story 2.14** established the `mypy_path` POSIX-only single-line pattern + the `migrator-test-additive` recipe-in-CI pattern (relevant: this story's test should similarly run in CI).

### File List (predicted)

**New (8):**
- `tests/fixtures/null-orchestrator/pyproject.toml`
- `tests/fixtures/null-orchestrator/Dockerfile`
- `tests/fixtures/null-orchestrator/null_orchestrator.py`
- `tests/fixtures/null-orchestrator/__init__.py`
- `tests/fixtures/null-orchestrator/__main__.py`
- `tests/separability/build_null_orchestrator.sh` (or Python helper)
- `tests/separability/docker-compose.test.yml`
- `tests/separability/test_s3_orchestrator_swap.py`

**Modified (5):**
- `docker-compose.yml` — `ORCHESTRATOR_IMAGE` env-var indirection.
- `tests/separability/conftest.py` — add `skip_if_no_docker` fixture.
- `justfile` — `test-separability` recipe; lint mypy invocation extended.
- `mypy.ini` — `[mypy-tests.separability.*] ignore_errors = False` override; `mypy_path` may need to add `tests/fixtures/null-orchestrator` if the script imports cross-tree (it imports `events` + `registry-state` which are already on `mypy_path`).
- `.github/workflows/nightly.yml` — new `s3-separability` job + path filters.

**Deleted (1):**
- `tests/separability/test_placeholder.py`

### References

- `epics.md` Story 2.15 (lines 932–947).
- `architecture.md` line 741 — `test_s3_orchestrator_swap.py` filename mandate.
- `architecture.md` line 911 — env-var image override pattern for FR34/FR35.
- `prd.md` FR35, NFR-M5 — orchestrator decoupling requirements.
- `prd.md` line 474 — S-3 separability test description.
- `services/orchestrator-adapter/pyproject.toml` — current scaffold (Story 1.2).
- `services/orchestrator-adapter/Dockerfile` — base image to mimic for null orchestrator.
- `tests/crash-injection/_compose.py` (Story 2.11) — compose orchestration helper pattern.
- `tests/crash-injection/conftest.py` — `skip_if_no_docker` fixture pattern.
- `tests/migrator/test_migrator_compose.py` (Story 2.14) — Docker-test pattern (DEFERRED there but referenced for shape).
- `services/registry-state/src/registry_state/adapters/event_log.py` — `EventLogWriter` for the null orchestrator's emission.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
