# Story 8.7.5 — Centralize `ensure_registered()` autouse fixture in `tests/conftest.py`

Status: **review** (CI pending @ pre-commit)

## Story

**As** a Platform engineer writing tests that touch the schema registry
**I want** a single session-scoped autouse fixture in `tests/conftest.py` that calls `ensure_registered()` once per test session
**so that** I don't have to copy-paste 4-10 lines of "fixture that re-registers event types" boilerplate into every new test module — and so that future test authors can't FORGET to add it (causing `EventSchemaUnknown` cascade failures like the 16-failure incident that Story 7e4ffec hotfixed via per-file pattern).

Story 8.7.5 is a **plumbing-only** test-infrastructure improvement carried over from Epic 8.7 retrospective debt. Three moving parts:

1. **Centralize `ensure_registered()` autouse fixture** in a new (or existing) top-level `tests/conftest.py`.
2. **Remove per-file redundant fixtures** from the 10 test modules that currently re-implement the pattern locally.
3. **Verify cleanup-test isolation** — tests that call `unregister_all()` (e.g., `packages/events/test_canonical.py`, `test_schema_registry.py`) MUST still work correctly because the session-scoped autouse runs once per session, NOT once per test — so any test that `unregister_all()`s during its body leaves the registry empty for subsequent tests until something re-fires `ensure_registered()`.

This story validates Epic 11 retro L6 (test-fixture realism + plumbing patterns) and removes a recurring boilerplate burden flagged by Story 11.4 PP14, Story 11.5 fixtures, and Story 12.1 PP14/PP29/PP36.

## Background

Per the original Story 7e4ffec (commit message): The root cause of the 16-test failure cascade was:

1. `packages/secret-hygiene/test_audited_secret.py` registers a LOCAL `_LocalSecretAccessedPayload` class at module-import — succeeds.
2. `packages/events/test_canonical.py` + `test_schema_registry.py` autouse fixtures call `unregister_all()` between tests, wiping the registry.
3. `services/registry-state/domain/event_types.py`'s MODULE-LEVEL `register()` calls only run ONCE per process. After step 2 they don't re-fire — the registry stays empty for all `approval.*`, `task.stop_requested`, etc.

Story 7e4ffec's hotfix: make `event_types.py` **re-registerable** by exposing `ensure_registered()` and inviting test fixtures to call it. The hotfix was per-file — every test module that depends on registered events copied the fixture boilerplate:

```python
@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    ensure_registered()
```

This per-file pattern doesn't scale:
- **10 files currently have this fixture** (audited via `grep -rln "ensure_registered" --include="*.py"`).
- New test modules MUST remember to add it OR fail with `EventSchemaUnknown` mid-suite (intermittent, depending on which sibling test runs first).
- The fixture body is identical across all 10 files — pure boilerplate.

Story 8.7.5 centralizes via a session-scoped autouse fixture in `tests/conftest.py` (or equivalent root-level conftest). After this lands, future test authors get registration "for free" — no boilerplate, no forgotten fixture.

## Acceptance criteria

### AC1 — Centralized session-scoped autouse fixture

Add (or update if exists) a top-level conftest at the repo root or a strategically-placed conftest that pytest discovers for ALL test modules. Two candidate locations (audit during impl):

**Option A:** `tests/conftest.py` — already where most cross-cutting fixtures live; auto-discovered for `tests/**/*.py` modules but NOT for `services/**/test_*.py` or `packages/**/test_*.py`. Insufficient alone.

**Option B (RECOMMENDED):** `conftest.py` at REPO ROOT. Pytest discovers this for ALL tests regardless of location. Used by other monorepos with the same need.

**Option C:** Per-service `conftest.py` in each `services/*/src/` and `packages/*/src/` — closer to test modules but multiplies files.

Resolution: **Option B (root `conftest.py`)** — single source of truth; least surface area.

Implementation:

```python
# conftest.py (repo root)
"""Root-level pytest conftest for the oh-my-bmad monorepo.

Centralizes session-scoped fixtures that ALL tests across the repo
(services/, packages/, tests/) need. Currently:

- ``_ensure_event_types_registered`` — session-scoped autouse that calls
  ``services.registry_state.domain.event_types.ensure_registered()`` exactly
  once at session start. Replaces the per-file fixture pattern introduced
  by Story 7e4ffec; this is the Story 8.7.5 consolidation.
"""
from __future__ import annotations

import pytest

# Story 8.7.5 — register all event types once per session.
# NOTE: this import is cross-service (root conftest imports from services/) but
# is the canonical location for the registry — same pattern as the per-file
# fixtures we're replacing.
from registry_state.domain.event_types import ensure_registered  # noqa: IMP001


@pytest.fixture(scope="session", autouse=True)
def _ensure_event_types_registered() -> None:
    """Story 8.7.5 — call ensure_registered() exactly once per test session.

    Tests that `unregister_all()` mid-run (e.g., packages/events/test_canonical.py)
    are responsible for restoring registry state at function-scope; this
    session-scoped fixture does NOT auto-restore between tests.
    """
    ensure_registered()
```

Self-verification:
- `grep -nE "^@pytest.fixture\(scope=\"session\", autouse=True\)" conftest.py` returns the fixture decorator.
- `grep -nE "from registry_state.domain.event_types import ensure_registered" conftest.py` returns one line.
- Pytest discovers the conftest for all test paths: `uv run pytest --co -q tests/ services/ packages/ | head -5` shows tests collected from all 3 trees.

### AC2 — Remove redundant per-file fixtures from 10 test modules

Audit the following files for the per-file `ensure_registered()` autouse pattern and DELETE the fixture (now redundant):

```
tests/integration/test_hmac_key_isolation.py
tests/integration/test_verify_approval_offline_recipe.py
packages/events/src/events/test_log_reader.py
services/registry-api/src/registry_api/test_decisions.py
services/registry-state/src/registry_state/domain/test_handlers.py
services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery.py
services/registry-api/src/registry_api/test_decisions_signing.py
services/metrics-subscriber/src/metrics_subscriber/test_day_rollover.py
services/registry-api/src/registry_api/test_approvals.py
services/registry-api/src/registry_api/test_key_rotation.py
services/worker-wrapper/src/worker_wrapper/domain/test_budget_supervisor.py
tests/integration/test_budget_enforcement_latency.py
```

(NOTE: Story 11.4 PP14 + Story 11.5 + Story 12.1 PP14/PP29 added the SNAPSHOT/RESTORE pattern — those are DIFFERENT from plain `ensure_registered()` and MUST be preserved. Audit each file carefully:
- **Plain `ensure_registered()` autouse fixture** → DELETE (now covered by root conftest)
- **Snapshot/restore fixture** (calls `REGISTRY.copy()` then restores) → KEEP (these handle the specific case where the test deliberately mutates and restores)

For each touched file, verify tests still pass after fixture removal.

Self-verification:
- `grep -rln "@pytest.fixture(autouse=True)" --include="*.py" | xargs grep -l "ensure_registered()" | wc -l` should be 0 or only the centralized conftest after fix (and any snapshot/restore fixtures).
- All ~10 affected files lose 4-6 lines each (~50 LOC total cleanup).
- Run `uv run pytest -x -q services/registry-api services/registry-state services/metrics-subscriber services/worker-wrapper packages/events tests/integration tests/contract` — same pass count as before (no regressions).

### AC3 — Verify cleanup-test isolation still works

The following files call `unregister_all()` or directly mutate `REGISTRY`:

```
packages/events/src/events/test_canonical.py
packages/events/src/events/test_schema_registry.py
packages/events/src/events/test_envelope.py
packages/events/src/events/types/test_deployment.py
packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py
tests/contract/test_event_payload_contracts.py (snapshot/restore — Story 11.4 PP14)
```

After AC1 lands, the session-scoped autouse fires ONCE at session start. If a test in `test_canonical.py` (for example) calls `unregister_all()` in its body, the registry is empty for subsequent tests in the same session until something re-fires `ensure_registered()`.

Audit each file:
- **If the file has a function-scoped autouse fixture that calls `ensure_registered()` after `unregister_all()`** → KEEP IT. Function-scope ensures every test gets a fresh registry. The new root session-scope fixture is additive.
- **If the file ONLY calls `unregister_all()` in a single test body without function-scoped restoration** → ADD a small `_restore_registry` autouse function-scope fixture in that specific file. Document why.

Self-verification:
- Run `uv run pytest -x -q packages/events services/registry-api services/registry-state` in random order (use `--random-order` plugin if available, OR run each test class in isolation via `-k`). All pass.
- Story 7e4ffec's regression test (16 failures) does NOT come back — verify by running the specific tests that originally failed (`test_decisions.py::test_*` per the 7e4ffec commit message).

### AC4 — Documentation update

Add a one-paragraph note to `docs/dev-tooling.md` (or equivalent — verify location during impl) under "Test patterns":

```markdown
### Schema-registry isolation in tests

The root `conftest.py` provides a session-scoped autouse fixture
`_ensure_event_types_registered` that calls `ensure_registered()` once
per pytest session. New test modules get registration "for free" — no
boilerplate.

If a test deliberately mutates the registry (e.g., `unregister_all()` or
`register()` with a different class), add a **function-scoped** autouse
fixture in THAT module to restore state after each test. Do NOT add
session-scoped fixtures that conflict with the root.

See Story 7e4ffec (root cause analysis) and Story 8.7.5 (consolidation).
```

If `docs/dev-tooling.md` doesn't exist, find the canonical doc for test patterns (e.g., `_bmad-output/implementation-artifacts/...` or `docs/testing.md`) and add there.

Self-verification:
- `grep -rn "ensure_registered\|Story 8.7.5" docs/` returns the new note.

### AC5 — Validation gates

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ services/ scripts/
uv run python scripts/check_imports.py
uv run python scripts/check_event_registry.py
uv run python scripts/check_single_writer.py
uv run pytest -x -q -m "not slow"
just bootstrap-verify
```

All exit 0. Test count expected: **same** as baseline 3087 (no tests added or removed). Mypy unchanged.

## Decisions (resolve BEFORE implementation)

### D1 — conftest location: repo-root vs tests/

**Resolved per spec AC1: repo-root `conftest.py`.** Single source of truth; pytest discovers for all tests regardless of subtree.

### D2 — session scope vs function scope for the centralized fixture

**Options:**
- (a) Session-scoped autouse — fires ONCE per session; cheap; matches the original `7e4ffec` semantics.
- (b) Function-scoped autouse — fires before EVERY test; defensive but adds N × `ensure_registered()` calls per session (cost should be sub-millisecond per call but multiplies).
- (c) Module-scoped autouse — fires once per test module; compromise.

**Resolved: (a) session-scoped.** `ensure_registered()` is idempotent (per Story 7e4ffec). Calling it once at session start is sufficient. Tests that DELIBERATELY mutate registry restore via function-scoped fixtures in their own files (AC3).

### D3 — Preserve snapshot/restore fixtures vs unify them

Story 11.4 PP14 + Story 11.5 + Story 12.1 PP14/PP29 added per-file SNAPSHOT/RESTORE fixtures (different from plain `ensure_registered()`). These exist because those tests register ADDITIONAL classes beyond the defaults.

**Resolved: PRESERVE.** Snapshot/restore is correct for tests that mutate registry; the root fixture handles the BASE case (ensure defaults registered) but doesn't conflict. Document the layered pattern in AC4.

### D4 — Cross-service import cost in root conftest

Root `conftest.py` will `from registry_state.domain.event_types import ensure_registered`. This is a cross-service import (root → services/). Same shape as the per-file pattern it replaces (those files already do this import).

**Resolved: Accept.** Add `# noqa: IMP001` if `check_imports.py` flags it. Document in conftest docstring that this is the canonical location for cross-service test fixtures.

### D5 — Handle 7e4ffec's `secret-hygiene` corner case

`packages/secret-hygiene/test_audited_secret.py` registers `_LocalSecretAccessedPayload` at module-import time, polluting the registry for sibling tests. Story 7e4ffec made `event_types.py` re-registerable to recover; the per-file fixture in affected files calls `ensure_registered()` to overwrite the local class.

**Resolved:** The session-scoped root fixture calls `ensure_registered()` ONCE at session-start, BEFORE `secret-hygiene` tests have imported. If `secret-hygiene` tests run FIRST and pollute the registry, subsequent tests don't get a re-registration. **Mitigation:** add a function-scoped autouse fixture in `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py` that calls `ensure_registered()` in teardown to restore canonical state.

Self-verification: run `uv run pytest -x -q packages/secret-hygiene tests/integration/test_hmac_key_isolation.py` in that order; assert no `EventSchemaUnknown` errors.

## Constraints

- **No production code changes.** Test infrastructure ONLY. Confined to `conftest.py` (new) + test fixture removals + optional docs.
- **`check_single_writer.py` MUST remain exit 0** — no SQLite writes in conftest.
- **Test count must remain stable** — this story is plumbing; no new tests, no removed tests.
- **structlog discipline** — N/A (conftest has no log calls).
- **No backwards-compatibility shims** — the per-file fixtures being removed are pure boilerplate; deletion is clean.

## Frontmatter

```yaml
---
story_id: 8.7.5
story_key: 8-7-5-test-isolation-conftest
parent_epic: 8.7
phase: 2
fr_refs: []  # test infra; no FR
nfr_refs: []
arch_refs:
  - "Story 7e4ffec hotfix — root-cause analysis for ensure_registered() pattern"
  - "Story 11.4 PP14 — snapshot/restore fixture pattern (preserved alongside this consolidation)"
  - "Epic 11 retro L6 — test-fixture realism lesson"
estimated_complexity: LOW
priority: medium (clears recurring boilerplate; unblocks future cross-cutting story test authors)
blocks: []
review_cadence: 1-PASS (plumbing-only; no subprocess/HMAC/event-log/lifespan)
---
```

## Context

- **Phase:** 2
- **Direct deps (must be `done`):** Story 7e4ffec (`event_types.ensure_registered()` exists), Story 2.1 (schema_registry primitives).
- **Test count baseline:** 3087 non-slow (Story 12.1 pass-2 close)
- **Mypy --strict baseline:** unchanged expected
- **Estimated +tests:** 0 (this is plumbing; existing tests verify)
- **Estimated complexity:** LOW. New root `conftest.py` + ~10 file cleanups + ~5 line doc note. **1-pass review expected** — does NOT match Epic 11 retro L1 criteria (no subprocess, no HMAC, no cross-service contracts beyond the conftest import).

## Definition of Done

- All 5 ACs met; self-verification commands pass.
- `sprint-status.yaml` `8-7-5-test-isolation-conftest: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- ZERO test count regression (3087 in, 3087 out).
- Mypy --strict UNCHANGED.
- 10 affected test files have redundant fixtures removed.
- Documentation note added.
- Story 7e4ffec's original 16-failure scenario verified absent (run the original failing tests in random order).

## Tasks / Subtasks

- [x] **T1 (AC1)** Create `/conftest.py` at repo root with session-scoped autouse `_ensure_event_types_registered` fixture
- [x] **T2 (AC2a)** Remove plain `ensure_registered()` autouse from `services/registry-api/src/registry_api/test_decisions.py`
- [x] **T2b (AC2b)** Remove plain `ensure_registered()` autouse from `services/registry-api/src/registry_api/test_decisions_signing.py`
- [x] **T2c (AC2c)** Remove plain `ensure_registered()` autouse from `services/registry-api/src/registry_api/test_approvals.py`
- [x] **T2d (AC2 audit)** Confirm all other AC2-listed files are SNAPSHOT/RESTORE or test-only-type fixtures — no changes needed
- [x] **T3a (AC3)** Add `ensure_registered()` teardown to `packages/events/src/events/test_canonical.py` _clean_registry fixture
- [x] **T3b (AC3)** Add `ensure_registered()` teardown to `packages/events/src/events/test_schema_registry.py` _clean_registry fixture
- [x] **T3c (AC3)** Add `ensure_registered()` teardown to `packages/events/src/events/test_envelope.py` _clean_registry fixture
- [x] **T3d (AC3/D5)** Add function-scoped teardown to `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py` calling `ensure_registered()` to restore canonical state after `unregister_all()`
- [x] **T4 (AC4)** Add schema-registry isolation note to `docs/testing-guide.md`
- [x] **T5 (AC5)** Run all validation gates and confirm 3087 test count + mypy unchanged

## Dev Agent Record

### Files Added
- `conftest.py` (repo root) — session-scoped autouse `_ensure_event_types_registered` fixture; Story 8.7.5 centralization

### Files Modified (redundant fixture removals — AC2)
- `services/registry-api/src/registry_api/test_decisions.py` — removed plain `_ensure_event_types_registered` autouse + orphaned `ensure_registered` import
- `services/registry-api/src/registry_api/test_decisions_signing.py` — removed plain `_ensure_event_types_registered` autouse + orphaned `ensure_registered` import
- `services/registry-api/src/registry_api/test_approvals.py` — removed plain `_ensure_event_types_registered` autouse + orphaned `ensure_registered` import

### Files Modified (cleanup isolation — AC3)
- `packages/events/src/events/test_canonical.py` — added `ensure_registered()` in `_clean_registry` teardown + import with `# noqa: IMP001`
- `packages/events/src/events/test_schema_registry.py` — added `ensure_registered()` in `_clean_registry` teardown + import with `# noqa: IMP001`
- `packages/events/src/events/test_envelope.py` — added `ensure_registered()` in `_clean_registry` teardown + import with `# noqa: IMP001`
- `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py` — added `ensure_registered()` in `_re_register_secret_accessed` teardown (spec D5) + import with `# noqa: IMP001`

### Files Modified (documentation — AC4)
- `docs/testing-guide.md` — added "Schema-registry isolation in tests" section

### Files Audited but UNCHANGED (snapshot/restore preserved — spec D3)
- `services/worker-wrapper/src/worker_wrapper/domain/test_budget_supervisor.py` — KEEP: `_isolated_registry` is snapshot+restore (PP14/PP29/PP36), not plain `ensure_registered()`
- `tests/integration/test_budget_enforcement_latency.py` — KEEP: mirror of above; snapshot+restore pattern
- `tests/contract/test_event_payload_contracts.py` — KEEP: no autouse; only REGISTRY lookups in assertions
- `tests/integration/test_hmac_key_isolation.py` — KEEP: no autouse; inline `ensure_registered()` calls inside pytest-asyncio fixtures serving the app lifespan
- `tests/integration/test_verify_approval_offline_recipe.py` — KEEP: no autouse; one inline call in test body within `finally` cleanup
- `packages/events/src/events/test_log_reader.py` — KEEP: autouse `_isolated_registry` registers a test-only type (not `ensure_registered()`)
- `services/registry-state/src/registry_state/domain/test_handlers.py` — KEEP: autouse registers individual types manually (not calling `ensure_registered()`); two inline calls in test bodies
- `services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery.py` — KEEP: autouse `_isolated_registry` registers test-only types
- `services/metrics-subscriber/src/metrics_subscriber/test_day_rollover.py` — KEEP: autouse `_isolated_registry` registers test-only types
- `services/registry-api/src/registry_api/test_key_rotation.py` — KEEP: no autouse, no `ensure_registered()` at all

### Validation Gate Results
- `ruff check . && ruff format --check .` → clean (0 errors)
- `mypy --strict packages/ services/ scripts/` → 212 errors in 47 files (unchanged from baseline; 0 new errors from our changes)
- `python scripts/check_imports.py` → exit 0 (noqa on `from` line of all 4 cross-boundary imports)
- `python scripts/check_event_registry.py` → exit 0
- `python scripts/check_single_writer.py` → exit 0
- `pytest -x -q -m "not slow"` → 3086 passed, 3 skipped, 34 deselected (+ 1 pre-existing failure in `test_journey_1_overnight_pr` — `ModuleNotFoundError: _build_scripted_worker`, confirmed baseline-identical via `git stash` check)
- `just bootstrap-verify` → ✓ bootstrap OK (14 workspace-member imports verified)
- Story 7e4ffec regression: `pytest -x services/registry-api/src/registry_api/test_decisions.py` → 29 passed ✓

### Test Count Delta
- Baseline: 3087 (3086 passing + 1 pre-existing broken journey test)
- After: 3087 (3086 passing + 1 pre-existing broken journey test)
- Delta: 0 (plumbing only — no tests added or removed) ✓
