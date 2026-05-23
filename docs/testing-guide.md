# Testing guide

How to run and add tests for the oh-my-bmad platform. This guide covers the
test-tree layout, pytest marker taxonomy, unit and integration test patterns,
contract-fixture recording workflow, and CI gate interpretation.

---

## Running tests

| Command | What it runs | When to use |
|---------|-------------|-------------|
| `just test` | `pytest -m "not slow"` | PR gate — run before every push |
| `just test-slow` | `pytest` (all markers) | Full regression; nightly CI (lands later story) |
| `just test-contract` | `pytest tests/contract` | After every `just sync-upstream <name>` |

`just test` is the minimum bar for merging. A green `just test` means all
non-slow tests pass; a green `just test-slow` means the complete matrix passes.

---

## Test-tree layout

```
tests/
  conftest.py                  # cross-cutting fixtures (fixed_clock, seeded_uuid7)
  separability/                # Story 1.5 — import-graph isolation tests
  crash-injection/             # Story 1.5 — process-crash + recovery tests
  idempotency/                 # Story 1.5 — duplicate-event idempotency tests
  integration/                 # Story 1.5 — cross-service integration tests
  contract/                    # Story 1.5 — upstream-adapter contract tests
    fixtures/
      <adapter>/               # recorded stdin/stdout/events per adapter
  migrator/                    # Story 1.5 — migrator correctness tests
```

Co-located unit tests live alongside their subject:

```
packages/<pkg>/src/<module>/test_*.py
services/<svc>/src/<module>/test_*.py
```

Architecture §line 344 establishes that co-located `test_*.py` files are the
primary unit-test location. The top-level `tests/` tree hosts cross-service
and infrastructure-level tests only.

| Test tree | Pytest marker | Owning story |
|-----------|--------------|-------------|
| `tests/separability/` | `separability` | 1.5 |
| `tests/crash-injection/` | `crash` | 1.5 |
| `tests/idempotency/` | `idempotency` | 1.5 |
| `tests/integration/` | `integration` | 1.5 |
| `tests/contract/` | `contract` | 1.5 |
| `tests/migrator/` | `migrator` | 1.5 |
| slow tests (any tree) | `slow` | 1.5 |

---

## Marker taxonomy

Story 1.5 registered all seven markers in `pyproject.toml`:

| Marker | Meaning |
|--------|---------|
| `slow` | Test takes >2 s or requires network / Docker. Excluded from `just test`. |
| `separability` | Verifies that a service/package can be imported without pulling in out-of-layer dependencies. |
| `crash` | Injects a process crash and asserts the system recovers cleanly. |
| `idempotency` | Sends a duplicate event and asserts exactly-once semantics in the output. |
| `integration` | Exercises two or more services together over the compose network. |
| `contract` | Replays a recorded upstream-adapter fixture and asserts output matches. |
| `migrator` | Runs a migration against a fixture JSONL and asserts the output schema. |

Combine markers with `-m` boolean expressions:

```sh
uv run pytest -m "crash or idempotency"
uv run pytest -m "not slow and not contract"
```

---

## Writing a unit test (co-located)

Place the test file next to the module it tests:
`packages/secret-hygiene/src/secret_hygiene/test_scanner.py` alongside
`scanner.py`. Architecture §line 344 establishes this as the primary
unit-test location.

```python
"""Unit tests for secret_hygiene.scanner."""
from __future__ import annotations
from secret_hygiene.scanner import SECRET_PATTERNS

_EXPECTED_KEYS = {
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN_CLASSIC",
    "GITHUB_TOKEN_FINE",
    "GENERIC_AWS_ACCESS_KEY",
}

def test_secret_patterns_exports_expected_keys() -> None:
    assert set(SECRET_PATTERNS.keys()) == _EXPECTED_KEYS
```

No marker needed for plain unit tests. Add `@pytest.mark.slow` only if the
test is genuinely slow (network call, Docker).

---

## Writing an integration test

Integration tests live in `tests/integration/` and exercise interactions
between two or more platform components.

```python
"""Integration test: event emitted by registry-state is readable by registry-api."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_event_round_trip(fixed_clock: object, seeded_uuid7: object) -> None:
    # fixed_clock and seeded_uuid7 arrive in Stories 2.1 / 2.2.
    # Until then, calling this fixture raises NotImplementedError — the test
    # is correctly skipped in Phase 1 via the `integration` marker exclusion.
    pytest.skip("EventEnvelope fixture arrives in Story 2.1")
```

The `fixed_clock` and `seeded_uuid7` fixtures are declared in `tests/conftest.py`
and currently raise `NotImplementedError`. Real implementations arrive in
Stories 2.1 and 2.2 respectively. Until then, any integration test that
requests these fixtures will fail at fixture setup — guard with `pytest.skip`
or the `@pytest.mark.integration` marker (excluded from `just test`).

---

## Schema-registry isolation in tests

The root `conftest.py` (repo root, not `tests/conftest.py`) provides a
session-scoped autouse fixture `_ensure_event_types_registered` that calls
`ensure_registered()` once per pytest session. New test modules get
registration "for free" — no boilerplate needed.

If a test deliberately mutates the registry (e.g., `unregister_all()` or
`register()` with a different class), add a **function-scoped** autouse
fixture in THAT module to restore state after each test. Do NOT add
session-scoped fixtures that conflict with the root.

```python
# In a test module that calls unregister_all():
from registry_state.domain.event_types import ensure_registered  # noqa: IMP001

@pytest.fixture(autouse=True)
def _restore_registry() -> Generator[None, None, None]:
    yield
    ensure_registered()  # restore canonical types for sibling tests
```

See Story 7e4ffec (root cause analysis) and Story 8.7.5 (consolidation).

### Snapshot/restore for fine-grained isolation

When a test file registers **additional** types beyond the canonical set (e.g.
a test-only payload model at a real event-type key), use a **snapshot/restore**
fixture instead of `unregister_all()`. This preserves other already-registered
types so sibling test files see no change.

Pattern (`packages/events/src/events/types/test_deployment.py:71`):

```python
import events.schema_registry as sr
from events.schema_registry import REGISTRY

@pytest.fixture()
def _isolated_registry() -> Generator[None, None, None]:
    """Snapshot REGISTRY around tests that mutate it."""
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    sr._rebuild_types_cache()  # noqa: SLF001 — test-only registry rebuild
```

Use this when:
- Your test registers a different class at a canonical key (e.g. to force a
  `ValueError` from `register()`).
- You need to add test-only extra versions of an existing type.
- You want to guarantee zero impact on the broader session state.

Cross-refs: `packages/events/src/events/types/test_deployment.py:71`,
`services/registry-state/src/registry_state/domain/test_failure_detection.py:73`.

### Test-only event types

When testing the **registry mechanics themselves** (registration, lookup, cache
invalidation) it is cleaner to use synthetic event type names rather than
mutating the canonical set. The isolation fixture registers a throwaway type
for the test body and restores canonical state in teardown.

Pattern (`packages/events/src/events/test_schema_registry.py`):

```python
@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    # tests register their own synthetic types via register("foo.bar", ...)
    yield
    unregister_all()
    ensure_registered()  # restore canonical set for sibling tests
```

Use this when:
- The test asserts registry semantics directly (idempotency, conflict errors,
  cache rebuild) and must start with an empty registry.
- Any co-located production code paths would be confused by extra types.

Cross-refs: `packages/events/src/events/test_schema_registry.py`,
`packages/events/src/events/test_canonical.py`,
`services/registry-state/src/registry_state/test_event_log.py`.

The `scripts/check_registry_isolation.py` CI gate enforces that every
`unregister_all()` call is paired with a restore (Story 8.7.5 PP3).

### pytest-xdist parallel workers not supported

pytest-xdist parallel execution (`-n auto`) is **not currently supported**. The
session-scoped `_ensure_event_types_registered` fixture in the root `conftest.py`
creates per-worker registry state, but `unregister_all()`-based fixtures may
behave unexpectedly under parallel execution because workers share the process-
global `REGISTRY` dict only within a single worker process — cross-worker
ordering is non-deterministic.

If xdist support is needed, file a follow-up Story. In the meantime, all CI
runs use the default sequential mode.

See also: root `conftest.py` comment and `scripts/check_registry_isolation.py`.

---

## Recording a contract fixture

Contract tests pin the observed I/O behavior of an upstream-fork adapter. When
vendored upstream source changes (via `just sync-upstream`), re-running
`just test-contract` detects drift against the recorded fixture.

### Workflow

1. **Identify the adapter under test.** Current adapters:
   - `services/orchestrator-adapter/adapters/omc_subprocess.py` (Story 5.10)
   - `services/worker-wrapper/adapters/clawhip_client.py` (Story 2.8)

2. **Record real subprocess I/O** into a fixture file under
   `tests/contract/fixtures/<adapter-name>/<test-case-name>.json`.
   The fixture captures: stdin/MCP tool call args, stdout/MCP tool result,
   and platform events emitted.

3. **Write the contract test** that loads the fixture and replays it:
   ```python
   import json
   from pathlib import Path
   import pytest

   FIXTURES = Path("tests/contract/fixtures/omc_subprocess")

   @pytest.mark.contract
   def test_task_plan_committed_replay() -> None:
       fixture = json.loads((FIXTURES / "task_plan_committed.json").read_text())
       pytest.skip("omc_subprocess adapter arrives in Story 5.10")
   ```

4. **Verify playback:** `just test-contract`. All contract tests must pass
   before merging a `just sync-upstream` bump.

5. **After a `just sync-upstream <name>` bump:** re-run `just test-contract`
   before merging. Contract drift fails the PR gate. Update the fixture to
   reflect new behavior, then commit the upstream bump and fixture together.

---

## CI gates

`just lint` runs seven sub-commands: `ruff check`, `ruff format --check`,
`mypy --strict`, then four architectural-discipline gates:

| Gate script | What it checks | Violation tag |
|-------------|---------------|--------------|
| `scripts/check_imports.py` | No cross-layer imports (e.g., a service importing another service's internals) | `IMP001` |
| `scripts/check_event_registry.py` | Every `type=` literal at an emission site is present in `REGISTRY` | `EVT001` |
| `scripts/check_single_writer.py` | Only the designated writer package appends to the event log | `SW001` |
| `secret-hygiene-precommit` (via `git ls-files`) | No secret patterns in tracked files | — |

### Reading a violation report

```
IMP001 services/registry-api/src/registry_api/routes.py:12
  illegal import: from registry_state.writer import append
  registry-api must not import registry-state internals (single-writer rule)
```

The report names the file, line number, import, and the rule violated.

### Suppressing with noqa

Only suppress after confirming the violation is a legitimate exception:

```python
from registry_state.writer import append  # noqa: IMP001 test fixture needs direct writer access
```

The reason string after the tag is mandatory. See
[Exceptions](./exceptions.md#suppression-tags) for the full tag reference.

### Running the self-tests

```sh
just check-gates-self-test
# Expected: 3/3 gate self-tests pass (fixture-based detection logic verified)
```

---

## Fixtures

`tests/conftest.py` declares two cross-cutting fixtures:

| Fixture | Status | Arrives |
|---------|--------|---------|
| `fixed_clock` | `NotImplementedError` | Story 2.1 — `packages/events/src/events/clock.py` |
| `seeded_uuid7` | `NotImplementedError` | Story 2.2 — `packages/events/src/events/ids.py` |

A helper constant `FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)` is
exported from `conftest.py` for tests that need a deterministic timestamp
before `fixed_clock` lands.

---

## See also

- [Exceptions](./exceptions.md) — suppression-tag reference + noqa conventions.
- [Operator runbook](./operator-runbook.md) — running the full regression suite after a production incident.
- [ADR-0002: Integration test harness sharing](./adr/0002-integration-test-harness.md) — shared helper strategy.

---

## Integration test harness conventions

Shared Docker Compose and ASGI harness helpers live in dedicated modules.
New integration tests should import from these modules rather than copy-pasting
boilerplate from existing tests. See [ADR-0002](./adr/0002-integration-test-harness.md) for the full decision context.

### Docker Compose journey tests

Import from `tests/integration/_compose_helpers.py`:

| Helper | Purpose |
|--------|---------|
| `compose_env` | Build the `os.environ` dict with journey-specific env vars (accepts `data_dir_key` kwarg, e.g. `"OMB_J1_DATA_DIR"`) |
| `compose_cmd` | Build a `docker compose` command line |
| `wait_for_all_healthy` | Poll `docker compose ps` until all services report healthy |
| `resolve_registry_api_port` | Resolve the host port for the registry-api service |

Each test file wraps these with thin closures that bind the module-level
constants (`_COMPOSE_FILE`, `_WORKER_TAG`, etc.). Example:

```python
from tests.integration._compose_helpers import compose_cmd as _shared_compose_cmd

_COMPOSE_FILE = Path(__file__).parent / "docker-compose.j3.yml"

def _compose_cmd(project: str, *args: str) -> list[str]:
    return _shared_compose_cmd(project, _COMPOSE_FILE, *args)
```

### ASGI harness tests

The ASGI + LifespanManager + ASGITransport wiring pattern is stable across
test files, but the `_Harness` class is actively diverging (different fields
per test). Import base utilities (`_db_url`, `_seed_tables`, event loop
management) from `tests/integration/_asgi_harness.py` when extracting. Keep
`_Harness` classes per-file — they are test-specific.

### Stub fixture helpers

`_install_signal_handlers` and `_connect_mcp` are identical across stub files
and should be imported from `tests/fixtures/_stub_helpers.py` once extracted.
`_read_new_lines` is intentionally per-file because `null_orchestrator` returns
typed `EventEnvelope` objects while other stubs return raw dicts.

### When to keep code self-contained

A test file should keep helper code local (not shared) when:
- The helper returns a different type or has diverged semantics
- The test needs per-file customization that would require complex parameterization
- The helper is used by only one test file

## Module-scoped asyncio loops (Story 8.7.6)

The repo's `pytest-asyncio` default fixture loop scope is `"module"` (set in
`pyproject.toml`). This reduces aiosqlite daemon-thread accumulation
dramatically and was the root-fix for the exit-134 SIGABRT shim retired in
Story 8.7.6.

**If your `@pytest_asyncio.fixture` spawns background tasks** (`LifespanManager`,
`asyncio.Queue` listeners, `asyncio.create_task`, etc.), add
`loop_scope="function"` to the decorator. Otherwise the fixture's background
state may leak across tests in the same module:

```python
@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 — per-test loop
async def app_client(test_settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    async with LifespanManager(build_app(test_settings)) as manager:
        async with AsyncClient(...) as client:
            yield client
```

**Symptoms of missing override:**
- Tests pass in isolation, fail when running the full module
- `RuntimeError: Loop is closed` at test setup
- `ResourceWarning: unclosed event loop` at session end
- Background task accumulation across same-module tests

**Auxiliary defenses (already in place):**
- Repo-root `conftest.py` `pytest_sessionfinish` hook drains aiosqlite worker
  threads at session end (Story 8.7.6 PP1/PP3/PP6) — uses defensive
  `_is_aiosqlite_worker` matcher resilient to upstream thread-naming changes.
- Repo-root `conftest.py` `_assert_no_leaked_tasks_after_test` autouse warns
  if a test leaks pending `asyncio.Task` instances (Story 8.7.6 PP7).
- `tests/integration/test_aiosqlite_drain.py` smoke-tests the basic clean-
  exit invariant in a subprocess (Story 8.7.6 PP11).

See spec line 59 + Story 8.7.6 PP2 audit for the canonical list of
`registry-api` fixtures requiring the override.
