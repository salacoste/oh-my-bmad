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
