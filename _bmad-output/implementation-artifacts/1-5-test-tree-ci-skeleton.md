# Story 1.5: Test tree + CI skeleton

Status: done

## Story

As the **operator**,
I want **the full `tests/` directory layout, `pytest` / `ruff` / `mypy` configuration, and a GitHub Actions CI pipeline that runs on every PR**,
so that **adding real tests in later stories drops into a working harness, regression guarding starts from day one, and the `just test` / `just lint` placeholders from Story 1.4 become real**.

## Acceptance Criteria

1. **AC-1: `tests/` directory skeleton — 6 test trees + top-level fixtures.** Directory `tests/` contains:
   - `tests/separability/test_placeholder.py`
   - `tests/crash-injection/test_placeholder.py`
   - `tests/idempotency/test_placeholder.py`
   - `tests/integration/test_placeholder.py`
   - `tests/contract/test_placeholder.py`
   - `tests/migrator/test_placeholder.py`
   - `tests/conftest.py` (top-level pytest fixtures skeleton)
   - `tests/fixtures/` (starts with a `README.md` describing fixture conventions — real fixture modules arrive per-story)
   - Each `test_placeholder.py` is exactly one test function, marked `@pytest.mark.skip(reason="placeholder — real tests land in Story <N>")` plus the tree's own marker (`@pytest.mark.separability`, `@pytest.mark.crash`, `@pytest.mark.idempotency`, `@pytest.mark.integration`, `@pytest.mark.contract`, `@pytest.mark.migrator`). The body is `assert True`.

2. **AC-2: Root-level pytest configuration (`[tool.pytest.ini_options]` in `pyproject.toml`).** Registers the 6 markers (`separability`, `crash`, `idempotency`, `integration`, `contract`, `migrator`) + the `slow` marker per Architecture line 346. Sets `testpaths = ["tests", "packages", "services", "mcp-servers"]` so co-located `test_*.py` files (Architecture line 344) are discovered alongside the tree-level tests. Sets `asyncio_mode = "auto"`. Collect-ignores `upstream/` so vendored code isn't picked up.

3. **AC-3: `ruff.toml` at repo root.** Configures `ruff check` + `ruff format` per Architecture line 116:
   - Target `py312`.
   - Line length 100.
   - Select a reasonable core ruleset (`E`, `F`, `I`, `UP`, `B`, `SIM`, `N`) — explicitly enable the style guides the project needs. The custom `no-stdout-parse` rule (FR18b) is NOT in scope for Story 1.5 — deferred to Story 1.6 / 1.7.
   - Excludes `upstream/`, `.venv/`, `_bmad-output/`, `_bmad/`.
   - Per-file-ignore `tests/**` allows `B`-rule relaxation and missing docstrings.

4. **AC-4: `mypy.ini` at repo root.** Configures `mypy --strict`:
   - Python version 3.12.
   - Strict mode for `packages/` and `services/registry-*` (per Architecture line 245). Every other path gets `--ignore-errors` or an explicit relaxed section.
   - Explicit `[mypy-tests.*]` section with `ignore_errors = True` (tests are hello-world skips right now; strictness arrives per-story).
   - Explicit `[mypy-upstream.*]` section with `ignore_errors = True` (vendored code not under platform-owned strictness).

5. **AC-5: `[dependency-groups]` declares dev dependencies.** Root `pyproject.toml` gains a `[dependency-groups.dev]` list containing `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`. `uv sync --frozen --dev` installs them; plain `uv sync --frozen` (as used by `bootstrap-verify`) does NOT pull dev deps — Story 1.1's regression claim stays green. Pin versions via `uv lock`'s output, not by hardcoding.

6. **AC-6: `tests/conftest.py` skeleton.** Declares pytest_plugins/empty fixture module + a `# Real fixtures land per-story` comment. Pre-stubs the two fixtures Architecture line 347 promises will live here: `fixed_clock` (returns `datetime(2026, 1, 1, tzinfo=UTC)` — used by tests that need deterministic timestamps once `packages/events/src/events/clock.py` ships in Story 2.1) and `seeded_uuid7` (returns a prefix-reserved UUIDv7 — real impl lands with Story 2.2). Both are marked `@pytest.fixture` but raise `NotImplementedError("lands in Story 2.1/2.2")` if invoked — so the skeleton compiles but doesn't pretend to provide value yet.

7. **AC-7: `.github/workflows/ci.yml`.** Runs on `push` to any branch and `pull_request` to `main`. Single job `pr-gate` on `ubuntu-24.04`:
   - `actions/checkout@v4`
   - `astral-sh/setup-uv@v3` (or equivalent; pin the version) with `enable-cache: true`
   - Installs Python 3.12 via `uv python install` if not present on the runner
   - `uv sync --frozen --dev`
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `uv run mypy --strict packages/ services/registry-api services/registry-state` (per Architecture line 245; other services stricter-later)
   - `uv run pytest -m "not slow"` (discovers all 6 tree placeholders — they're all `@pytest.mark.skip` so pytest exits 0 with "N skipped" report)
   - Step `if: failure()` uploads `pytest.log` as artifact for debugging (optional but cheap)

8. **AC-8: Justfile: replace `test` / `test-slow` / `test-contract` / `lint` / `scenarios` placeholder bodies with real commands.** Story 1.4 shipped `@echo "pytest lands in Story 1.5"` bodies — those are replaced as follows:
   - `test` → `uv run pytest -m "not slow"`.
   - `test-slow` → `uv run pytest` (full matrix, no marker filter).
   - `test-contract` → `uv run pytest tests/contract` (per Architecture line 922).
   - `lint` → `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict packages/ services/registry-api services/registry-state`.
   - `scenarios` → placeholder preserved with updated message: `@echo "scenario harness lands across Stories 2.11 / 2.12 / 5.18"` — the scenario layer truly is later work; this story doesn't fake it.

9. **AC-9: `just test` on the bare checkout exits 0.** Runs the placeholder tree; pytest reports `6 skipped` (one per tree) plus whatever co-located tests exist (there are zero today). Exit code 0. No warnings about unknown markers (AC-2 registered them).

10. **AC-10: `just lint` on the bare checkout exits 0.** `ruff check` + `ruff format --check` + `mypy --strict` against the 4 strict paths (`packages/events`, `packages/secret_hygiene`, `packages/idempotency`, `services/registry-api`, `services/registry-state`) all exit 0. Per-package tests + hello-world entrypoints are type-safe enough to satisfy strict mypy — if not, add the minimum annotations required (likely already fine given Story 1.1/1.4 code shape).

11. **AC-11: Regression — `just bootstrap-verify` + `just migrator-test-additive` stay green.** `bootstrap-verify` runs `uv sync --frozen` (no `--dev`) so the dev group doesn't pollute the import-only smoke test. `migrator-test-additive` is independent of the test tree and should pass unchanged.

12. **AC-12: CI dry-run via `act` (optional) or visual verification.** The `ci.yml` workflow is syntactically valid (YAML parse + `gh workflow view` exit 0 on local inspection); a real CI trigger lands once this commit is pushed + a PR opened. Document in Completion Notes whether a real CI run was exercised.

13. **AC-13: Atomic commit.** All new files (ruff.toml, mypy.ini, 6× `test_placeholder.py`, `tests/conftest.py`, `tests/fixtures/README.md`, `.github/workflows/ci.yml`) + modifications (root `pyproject.toml` adds `[dependency-groups]` and `[tool.pytest.ini_options]`, `justfile` flips 5 placeholder bodies) land in a single commit titled `chore(scaffold): story 1.5 — test tree + CI skeleton · FR47 NFR-M7`. Docs-only follow-up commits permitted per the established precedent.

## Tasks / Subtasks

- [x] **Task 1: Root pyproject.toml — add dependency-groups + pytest config** (AC: #2, #5)
  - [x] `[dependency-groups.dev]` with pytest/pytest-asyncio/hypothesis/ruff/mypy.
  - [x] `[tool.pytest.ini_options]` with testpaths + asyncio_mode + all 7 markers + norecursedirs for upstream/ + _bmad*.
  - [x] `uv lock` regenerated; dev deps recorded (pytest 9.0.3, pytest-asyncio 1.3.0, hypothesis 6.152.1, ruff 0.15.11, mypy 1.20.2).

- [x] **Task 2: `ruff.toml`** (AC: #3)
  - [x] Config per AC-3 spec + 2 additions documented in deviations: (a) global `ignore = ["E501"]` because scaffold module docstrings are deliberately long reference prose; (b) `extend-exclude` covers 7 AI-tool skill dotdirs at repo root (`.agent`, `.agents`, `.claude`, `.cursor`, `.gemini`, `.opencode`, `.pi`, `.omc`) that ship here but aren't project code.
  - [x] `ruff check .` exit 0; `ruff format --check .` exit 0 on 44 files.

- [x] **Task 3: `mypy.ini`** (AC: #4)
  - [x] Config per AC-4 spec. Extra excludes added: `.venv/`, `scripts/migrator/` (it's a script, not platform code), `node_modules/`.
  - [x] `mypy --strict packages/ services/registry-api services/registry-state` → "Success: no issues found in 7 source files". Zero annotation fixes needed — Story 1.1/1.4 scaffold code was already strict-clean.

- [x] **Task 4: Create 6× `tests/<tree>/test_placeholder.py`** (AC: #1)
  - [x] All 6 files present with tree marker + `@pytest.mark.skip` + owning-story reference in docstring.
  - [x] Per-tree `conftest.py` stubs present (6 files).
  - [x] **Deviation: `__init__.py` files added** to `tests/` and each of the 6 subtrees. The spec said "no `__init__.py` needed" but that only holds when test filenames are unique across trees; with 6 identically-named `test_placeholder.py` files pytest's rootdir-based module disambiguation conflicts and throws `ImportPathMismatchError`. Package mode (via `__init__.py`) resolves the collision cleanly.

- [x] **Task 5: `tests/conftest.py` + `tests/fixtures/README.md`** (AC: #6)
  - [x] `tests/conftest.py` ships `fixed_clock` + `seeded_uuid7` stub fixtures that raise `NotImplementedError` pointing at Stories 2.1/2.2. `FROZEN_EPOCH` constant reserved for future test use.
  - [x] `tests/fixtures/README.md` documents scope rules.

- [x] **Task 6: `.github/workflows/ci.yml`** (AC: #7)
  - [x] Written per AC-7 recipe.
  - [x] YAML-validates (`python3 -c "import yaml; yaml.safe_load(...)"` exit 0).
  - [x] Top-of-file comment references Story 1.9 (release.yml) and the deferred nightly.yml.

- [x] **Task 7: Justfile recipe bodies** (AC: #8)
  - [x] 5 placeholder bodies replaced: `test` → `uv run pytest -m "not slow"`; `test-slow` → `uv run pytest`; `test-contract` → `uv run pytest tests/contract`; `lint` → three-step ruff+mypy chain; `scenarios` comment updated to point at Stories 2.11 / 2.12 / 5.18.
  - [x] Every other recipe preserved verbatim.

- [x] **Task 8: Local verification** (AC: #9, #10, #11, #12)
  - [x] `uv sync --frozen --dev` succeeds (28 packages in 0.86 s).
  - [x] `just test` → "6 skipped in 0.11s", exit 0.
  - [x] `just lint` → all 3 sub-commands green ("All checks passed!" + "44 files already formatted" + "no issues found in 7 source files").
  - [x] `just bootstrap-verify` → "✓ bootstrap OK (13 workspace-member imports verified)" — `uv sync --frozen` (no `--dev`) means dev deps don't leak into the smoke path.
  - [x] `just migrator-test-additive` → "3 events, all v1.0.1 with extensions" green.
  - [x] `ci.yml` YAML-valid; real CI trigger deferred until push to remote (no GitHub remote configured yet per Story 1.1 open question).

- [x] **Task 9: Commit atomically** (AC: #13)
  - [x] Scaffold commit `6d03c0b` (36 files changed, 587 insertions, 27 deletions). AC-12's commit-title cites FR47 + NFR-M7.

## Dev Notes

### Architecture patterns for this story

- **Test framework: `pytest` + `pytest-asyncio` + `hypothesis`** (Architecture line 114). No alternative harnesses. `hypothesis` goes in the dev dep group even though no property tests ship in this story — it'll be the fuzz-test library Stories 2.x / 6.12 use.
- **Test tree layout** (Architecture lines 344–347 + 173–177):
  - Co-located `test_*.py` beside modules for fast unit tests.
  - Top-level `tests/{separability,crash-injection,idempotency,integration,contract,migrator}/` for cross-service tests.
  - Top-level `conftest.py` for cross-cutting fixtures (clock, UUIDv7); per-tree `conftest.py` for tree-specific fixtures.
- **Linting / typing** (Architecture line 116): `ruff` (lint + format — one tool, replaces black / isort / flake8); `mypy --strict` on `packages/` + `services/registry-*`. Upstream-adapter shim boundaries are deliberately relaxed (Story 1.5 doesn't touch those adapter boundaries; later adapter stories own `# type: ignore[attr-defined]` or `Protocol` seams.)
- **CI (GitHub Actions)** (Architecture line 245): single `ci.yml` runs `uv sync --frozen` → `ruff check` → `ruff format --check` → `mypy --strict` → `pytest -m "not slow"` on every PR. `release.yml` is Story 1.9.
- **Version pinning policy** (Architecture line 180): pytest / ruff / mypy versions are NOT pre-committed in the architecture doc — they land in `uv.lock` at this story and update only via the NFR-M2 behavioral-contract gate. Accept `uv lock`'s chosen current-stable versions; only pin explicitly if the lock's choice is known-broken.
- **Marker hygiene**: registered markers prevent pytest's `PytestUnknownMarkWarning` noise. The 7-marker set (`separability`, `crash`, `idempotency`, `integration`, `contract`, `migrator`, `slow`) is comprehensive for Phase 1; new markers require updating this story file's AC-2 list + re-registering.

### What this story does NOT do

- Custom ruff rule for FR18b (no-stdout-parsing) — deferred to Story 1.6 / 1.7. AC-3 explicitly excludes it from scope.
- `scripts/check_imports.py` / `check_event_registry.py` / `check_single_writer.py` — Story 1.6 owns these.
- Pre-commit hook wiring (`.pre-commit-config.yaml`) — Story 1.7.
- `.github/workflows/release.yml` (GHCR publishing) — Story 1.9.
- `.github/workflows/nightly.yml` (full slow-matrix) — deferred; the PR-gate on `not slow` is the only CI surface Phase 1 commits to initially.
- Real tests in any tree — each tree's placeholder test exists only so the CI discovers a green baseline; the real tests land per-story.
- Log-capture fixture (NFR-O2 / Story 2.17) — stubbed only.
- Event-envelope fixtures, UUIDv7 fixture (Stories 2.1 / 2.2) — stubbed only.

### Source tree components to touch

```
oh-my-bmad/
├── pyproject.toml                           # Task 1 — MODIFIED (add [dependency-groups.dev] + [tool.pytest.ini_options])
├── uv.lock                                  # regenerated by `uv lock`
├── ruff.toml                                # Task 2 NEW
├── mypy.ini                                 # Task 3 NEW
├── justfile                                 # Task 7 MODIFIED (5 recipe bodies)
├── tests/                                   # Task 4–5 NEW tree
│   ├── conftest.py
│   ├── fixtures/
│   │   └── README.md
│   ├── separability/
│   │   ├── conftest.py
│   │   └── test_placeholder.py
│   ├── crash-injection/
│   │   ├── conftest.py
│   │   └── test_placeholder.py
│   ├── idempotency/
│   │   ├── conftest.py
│   │   └── test_placeholder.py
│   ├── integration/
│   │   ├── conftest.py
│   │   └── test_placeholder.py
│   ├── contract/
│   │   ├── conftest.py
│   │   └── test_placeholder.py
│   └── migrator/
│       ├── conftest.py
│       └── test_placeholder.py
└── .github/
    └── workflows/
        └── ci.yml                           # Task 6 NEW
```

**Files: ~17 new + 2 modified (pyproject.toml, justfile) + `uv.lock` regen.**

Note: `tests/crash-injection/` has a hyphen in the path. Python module names can't use hyphens, but pytest discovers files by path and doesn't require importable package names — so `tests/crash-injection/test_placeholder.py` works as long as it doesn't have `__init__.py`. Keep these trees as "rootdir-discovered test files", NOT importable packages (matches Architecture line 173's layout).

### `pyproject.toml` dependency-groups sketch

```toml
[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "hypothesis",
    "ruff",
    "mypy",
]

[tool.pytest.ini_options]
testpaths = ["tests", "packages", "services", "mcp-servers"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers"
markers = [
    "slow: exclude from PR-gate CI; runs on merge / nightly",
    "separability: S-1/S-2/S-3 adapter-swap tests",
    "crash: NFR-R2 crash-injection harness",
    "idempotency: UUIDv7-key replay tests",
    "integration: cross-service journey tests",
    "contract: upstream-fork behavioral contracts",
    "migrator: event-log schema migrator tests",
]
norecursedirs = ["upstream", "_bmad", "_bmad-output", ".venv", ".uv"]
```

### `ruff.toml` sketch

```toml
target-version = "py312"
line-length = 100

extend-exclude = [
    "upstream",
    ".venv",
    "_bmad",
    "_bmad-output",
]

[lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "UP",   # pyupgrade
    "B",    # flake8-bugbear
    "SIM",  # flake8-simplify
    "N",    # pep8-naming
]

[lint.per-file-ignores]
"tests/**" = ["B", "N", "D"]
"scripts/**" = ["N"]

[format]
# Defaults are fine; just stamp.
```

### `mypy.ini` sketch

```ini
[mypy]
python_version = 3.12
strict = True
explicit_package_bases = True
exclude = (?x)(
      ^upstream/
    | ^_bmad/
    | ^_bmad-output/
    | ^\.venv/
    | ^scripts/migrator/
)

[mypy-tests.*]
ignore_errors = True

[mypy-upstream.*]
ignore_errors = True

# Relaxed at adapter shim boundaries — lands in Stories 2.8 / 5.10.
[mypy-services.orchestrator_adapter.adapters.*]
ignore_errors = True

[mypy-services.worker_wrapper.adapters.*]
ignore_errors = True
```

### `tests/conftest.py` sketch

```python
"""Top-level pytest fixtures — cross-cutting fixtures (clock, UUIDv7) live here.

Real fixture bodies arrive per-story:
- `fixed_clock` — Story 2.1 (events.clock injectable).
- `seeded_uuid7` — Story 2.2 (events.ids prefix-reserved UUIDv7 generator).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fixed_clock():
    raise NotImplementedError("lands in Story 2.1 (packages/events/src/events/clock.py)")


@pytest.fixture
def seeded_uuid7():
    raise NotImplementedError("lands in Story 2.2 (packages/events/src/events/ids.py)")
```

### `test_placeholder.py` sketch (per tree)

```python
"""Placeholder test — <tree>.

Real tests land in <Story N>. Marker + skip-reason exist so CI passes on a
bare tree and the test-discovery surface is locked in from day one.
"""
from __future__ import annotations

import pytest


@pytest.mark.<tree-marker>
@pytest.mark.skip(reason="placeholder — real tests land in Story <N>")
def test_placeholder() -> None:
    assert True
```

### `.github/workflows/ci.yml` sketch

```yaml
name: ci
on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]

# release.yml (GHCR multi-arch publish) lands in Story 1.9.
# nightly.yml (full slow-matrix: separability + crash-injection + idempotency replay) deferred.

jobs:
  pr-gate:
    name: PR gate — ruff + mypy + pytest
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - name: Install Python 3.12
        run: uv python install 3.12
      - name: Sync workspace (incl. dev deps)
        run: uv sync --frozen --dev
      - name: ruff check
        run: uv run ruff check .
      - name: ruff format --check
        run: uv run ruff format --check .
      - name: mypy --strict (packages + registry services)
        run: uv run mypy --strict packages/ services/registry-api services/registry-state
      - name: pytest (not slow)
        run: uv run pytest -m "not slow"
```

### Testing standards for this story

No "real" tests exist yet — the whole point of Story 1.5 is the harness. Verification is:

1. `uv sync --frozen --dev` succeeds cleanly.
2. `just test` → exit 0 with "6 skipped" (or "6 skipped, 0 passed").
3. `just lint` → exit 0 across ruff + ruff-format + mypy.
4. `just bootstrap-verify` → still 13/13 green (no dev deps leaked into the smoke path).
5. `just migrator-test-additive` → still green.
6. `.github/workflows/ci.yml` parses as YAML; workflow shows up in `gh workflow list` after push.

### Project Structure Notes

#### Alignment with unified project structure

- All test-config files live at top level (Architecture line 910).
- `tests/` tree mirrors Architecture §Repo Layout (line 173–177).
- `ruff.toml` + `mypy.ini` sibling to `pyproject.toml` + `justfile` — one-tool-one-config discipline.
- CI workflow directory `.github/workflows/` — standard GitHub Actions location.

#### Detected variances

- **`test_placeholder.py` with `@pytest.mark.skip` contradicts "tests must fail first" TDD ideology**, but this story is explicitly scaffolding — real tests arrive per-story with their own red-green-refactor cycles. The skip + marker combo ensures CI is green on a bare repo without pretending the real test exists.
- **`uv lock` regenerates version pins** — versions aren't specified in this story. If `uv` picks versions that break the scaffold (e.g., a pytest release that removes `@pytest.mark.skip`), document in Completion Notes which versions landed and why.
- **`mypy --strict` against `services/registry-api` and `services/registry-state`** — current Story 1.4 `__main__.py` uses `NoReturn`, `FrameType | None`, explicit type hints — likely clean. If strict mode surfaces gaps, the fix is minimum annotations, not a refactor.
- **`crash-injection/` with hyphen** — pytest path-based discovery handles this; no `__init__.py` needed. If a future story tries to `import tests.crash_injection.helpers`, that story renames the directory OR uses `pathlib`-based fixture paths. Document the non-importability in Completion Notes.

### Previous Story Intelligence (Story 1.4)

Carry-forward learnings from `1-4-compose-env-justfile.md`:

- **Justfile additive-only discipline.** Story 1.4 preserved Stories 1.1–1.3's recipes verbatim and added 10 new ones. Story 1.5 preserves the 13 existing ones and REPLACES 5 placeholder bodies (`test`, `test-slow`, `test-contract`, `lint`, `scenarios`). The 3 existing review-fixed recipes (`backup`, `dev`, `deploy-vps`, `deploy-macos`) + Stories 1.1/1.2/1.3 recipes (`bootstrap-verify`, `sync-upstream`, `migrator-test-additive`, `build`) are untouched.
- **Review-fix → docs-finalize commit cadence.** Pattern remains: scaffold commit first, then (after 3-layer review) fix commit, then docs commit flipping to `done`. Story 1.5 follows it.
- **`.env.example` vs `.env` discipline.** Story 1.4 made `env_file required: false` in compose — no compose-level env is required for Story 1.5's test/CI path since CI doesn't bring up the stack.
- **Non-root container pattern.** Story 1.4 codified `USER <service>` + UID ranges. Story 1.5 doesn't add new containers, so this carries forward without change.
- **Atomic commit + deviation documentation.** Story 1.4's review discovered several "skipped deviations" — Story 1.5 documents the same style (custom ruff rule, pre-commit, nightly CI, release CI) explicitly out-of-scope.

### Git Intelligence (recent commits)

- `df29fe0 docs(story-1-4): finalize + mark done`
- `9862a4b chore(scaffold): apply story 1.4 code-review fixes · all severities`
- `51189a2 docs(story-1-4): finalize story file + mark review`
- `4146529 chore(scaffold): story 1.4 — compose + env + justfile · FR46 FR48 FR52 NFR-P4 NFR-S2`
- `17740d6 docs(story-1-3): finalize + mark done`

Epic 1 cadence: 4 stories done, each shipped scaffold → review → fix → finalize in 4 commits. Story 1.5 follows the same rhythm.

### Latest Tech Information

- **`uv 0.11.*`** — stable `[dependency-groups]` support (PEP 735). No need for `[project.optional-dependencies]` or legacy `[tool.uv.dev-dependencies]`.
- **`astral-sh/setup-uv@v3`** — GitHub Action installs `uv`, caches the global package cache, optionally installs Python. Preferred over hand-rolled `curl | sh` for CI reproducibility.
- **`pytest 8.x`** — `asyncio_mode = "auto"` removes per-test `@pytest.mark.asyncio` decoration (Architecture doesn't specify sync-vs-async test style; auto is the least-surprise default).
- **`ruff 0.6+`** — the "lint + format — one tool" era. Single `ruff.toml` drives both; no separate `black.toml` / `.flake8` / `pyproject.toml:tool.isort` needed.
- **`mypy 1.11+`** — `strict = True` in `mypy.ini` enables the full strict profile without per-flag enumeration.

### References

- `epics.md` §Epic 1 / Story 1.5 (lines 524–542) — source ACs.
- `architecture.md` lines 104 (Step 5 scaffold intent), 114–118 (testing + linting stack), 173–177 (test tree layout), 245 (CI workflow definition), 344–347 (test discovery + marker taxonomy), 910–922 (top-level configuration + `just test` contract), 955 (custom ruff rule deferred elsewhere).
- `prd.md` FR47 (time-to-first-task budget), NFR-M7 (README + runbook documentation completeness — Story 1.10 owns the README-side; Story 1.5 owns the CI-side of this NFR).
- `1-4-compose-env-justfile.md` — justfile placeholder recipes this story replaces.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — mechanical configuration work with one non-trivial surface (mypy strictness across existing scaffold). No Opus reasoning required unless strict mypy surfaces unexpected type-graph issues.

### Debug Log References

_Placeholder._

### Completion Notes List

**Implementation summary**

- `pyproject.toml` extended with PEP 735 `[dependency-groups.dev]` (pytest + pytest-asyncio + hypothesis + ruff + mypy) and `[tool.pytest.ini_options]` registering the 7-marker taxonomy; `uv.lock` regenerated.
- `ruff.toml` + `mypy.ini` land at repo root per Architecture line 910's top-level-config discipline. Ruff selects `E/F/I/UP/B/SIM/N`; mypy strict on `packages/` + `services/registry-*` with explicit relaxations for adapter shim boundaries (Stories 2.8/5.10).
- `tests/` tree: 6 subtrees each with placeholder + per-tree conftest + `__init__.py` for pytest package-mode disambiguation; top-level `tests/conftest.py` ships stub `fixed_clock` + `seeded_uuid7` fixtures that raise `NotImplementedError` pointing at Stories 2.1/2.2.
- `.github/workflows/ci.yml`: single `pr-gate` job on `ubuntu-24.04` with `actions/checkout@v4` + `astral-sh/setup-uv@v3` (cache enabled) + `uv python install 3.12` + `uv sync --frozen --dev` + ruff check + ruff format --check + mypy --strict + pytest -m "not slow". Top-of-file comment defers `release.yml` to Story 1.9 and `nightly.yml` to later work.
- `justfile`: 5 placeholder bodies replaced with real commands (`test`, `test-slow`, `test-contract`, `lint`, `scenarios`); 8 existing recipes preserved verbatim.

**Locked dev-dep versions (uv.lock)**

| Package | Version |
|---|---|
| pytest | 9.0.3 |
| pytest-asyncio | 1.3.0 |
| hypothesis | 6.152.1 |
| ruff | 0.15.11 |
| mypy | 1.20.2 |

`uv lock` picked current-stable versions per Architecture line 180's deferred-pinning policy. No known-broken releases detected.

**Scaffold-code fixes required to pass ruff/mypy**

Zero mypy fixes. Strict mode against `packages/` + `services/registry-api` + `services/registry-state` returned "Success: no issues found in 7 source files" on first invocation — Story 1.1/1.4 annotation discipline was already strict-clean.

`ruff format` cosmetic changes (pure whitespace / line-wrap collapse — no logic change; verified by `migrator-test-additive` regression staying green):
- 7 `__main__.py` files gain one blank line after module docstring (PEP 8 canonical).
- `scripts/migrator/src/migrator/__main__.py` collapses multi-line argument lists that fit within the 100-char line limit.
- `scripts/migrator/tests/assert_migrated.py` gains one blank line after module docstring.
- `scripts/sync_upstream.py` gains one blank line after module docstring + one `# noqa: SIM102` on an intentionally-readable three-branch guard chain (`first.startswith("`") and first.endswith("`") and first[1:-1] == name`).

**AC-by-AC evidence**

- **AC-1** ✓ — 6 test trees with placeholder + conftest; top-level conftest + fixtures/README present.
- **AC-2** ✓ — `[tool.pytest.ini_options]` registers 7 markers; pytest emits no `PytestUnknownMarkWarning`.
- **AC-3** ✓ — `ruff check .` + `ruff format --check .` both exit 0.
- **AC-4** ✓ — `mypy --strict packages/ services/registry-api services/registry-state` → "Success: no issues found in 7 source files".
- **AC-5** ✓ — `[dependency-groups.dev]` present; `uv sync --frozen --dev` completes; `uv.lock` records all 5 dev deps + transitive closure.
- **AC-6** ✓ — `tests/conftest.py` ships stub fixtures + `FROZEN_EPOCH` constant.
- **AC-7** ✓ — `.github/workflows/ci.yml` YAML-valid + 6-step pr-gate job present.
- **AC-8** ✓ — 5 justfile recipe bodies replaced; `just --list` shows all 14 recipes (default + 13 named).
- **AC-9** ✓ — `just test` → "6 skipped in 0.11s", exit 0.
- **AC-10** ✓ — `just lint` → all 3 sub-commands green.
- **AC-11** ✓ — `bootstrap-verify` 13/13; `migrator-test-additive` 3/3.
- **AC-12** ✓ — `ci.yml` YAML-valid locally. Real CI run deferred until the git remote is configured (Story 1.1 open question still pending).
- **AC-13** ✓ — atomic scaffold commit `6d03c0b` (36 files, 587+/27-).

**Deviations (documented)**

1. `__init__.py` files added to `tests/` + 6 subtrees. The spec's "no `__init__.py` needed" note was scoped to unique-basename layouts; with all 6 trees carrying `test_placeholder.py`, pytest's rootdir-based import system conflicts without package mode. Zero-line `__init__.py` files resolve the collision cleanly.
2. `ruff.toml` gains global `ignore = ["E501"]`. The scaffold ships long reference-text module docstrings across ~20 files; rewrapping each at 100 chars is cosmetic noise that obscures code review. `ruff format`'s line-length handling still applies to executable code — docstrings are prose. Acceptable for Phase 1 scaffold.
3. `ruff.toml` `extend-exclude` adds 8 AI-tool skill dotdirs at repo root (`.agent`, `.agents`, `.claude`, `.cursor`, `.gemini`, `.opencode`, `.pi`, `.omc`). These ship with the repo but contain vendored harness scripts, not project code. Without these excludes, ruff reported 1860 errors across code not owned by this project.
4. `mypy.ini` excludes `scripts/migrator/` entirely. The migrator is a standalone operator script (Story 1.3), not a workspace member; its strict-mode posture belongs with whatever Story promotes it to a real test target (Story 2.14) if needed.
5. `scripts/sync_upstream.py` gets one `# noqa: SIM102` inline suppression. The three-branch guard (`startswith + endswith + slice-equals`) reads more naturally than the merged-single-if ruff would prefer.

**Regression risk for Stories 1.6+**

- None. Story 1.6 (import-graph / event-registry / single-writer checks) will add 3 new CI steps to `.github/workflows/ci.yml` + 3 new scripts under `scripts/`. Story 1.7 (pre-commit hook) will wire a sibling `.pre-commit-config.yaml`. Story 1.9 (release) will add `release.yml` next to the already-present `ci.yml`. All additive; zero Story 1.5 refactor risk.

### File List

**New (22):**

- `ruff.toml`
- `mypy.ini`
- `.github/workflows/ci.yml`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/fixtures/README.md`
- `tests/separability/__init__.py` + `conftest.py` + `test_placeholder.py`
- `tests/crash-injection/__init__.py` + `conftest.py` + `test_placeholder.py`
- `tests/idempotency/__init__.py` + `conftest.py` + `test_placeholder.py`
- `tests/integration/__init__.py` + `conftest.py` + `test_placeholder.py`
- `tests/contract/__init__.py` + `conftest.py` + `test_placeholder.py`
- `tests/migrator/__init__.py` + `conftest.py` + `test_placeholder.py`

**Modified (13):**

- `pyproject.toml` (additive: `[dependency-groups]` + `[tool.pytest.ini_options]`)
- `justfile` (5 recipe bodies: `test`, `test-slow`, `test-contract`, `lint`, `scenarios`)
- `uv.lock` (regenerated — dev deps + transitive closure)
- 7 `services/*/src/*/__main__.py` — `ruff format` whitespace only
- `scripts/migrator/src/migrator/__main__.py` — `ruff format` whitespace + multi-line collapse
- `scripts/migrator/tests/assert_migrated.py` — `ruff format` whitespace only
- `scripts/sync_upstream.py` — `ruff format` whitespace + one `# noqa: SIM102`

**Deleted (0).**

### Change Log

- **2026-04-23:** Story 1.5 implemented. 22 new + 13 modified files; atomic scaffold commit `6d03c0b`. Verification: `just test` (6 skipped), `just lint` (all 3 sub-commands green), `just bootstrap-verify` (13/13 imports), `just migrator-test-additive` (3/3 events); `ci.yml` YAML-valid. Status: `ready-for-dev` → `in-progress` → `review`.
- **2026-04-23 (review):** 3-layer adversarial review on `6d03c0b` surfaced 2 CRITICAL + 6 HIGH + 8 MEDIUM + 5 LOW findings. Applied across 14 files in commit `0ea617e`:
  - **CRITICAL — `bootstrap-verify` leaked dev deps.** PEP 735 auto-activates `[dependency-groups.dev]` unless `--no-dev` is explicit. `uv sync --frozen` AND subsequent `uv run` calls both needed the flag. Fix: `--no-dev` added to `uv sync` and to every `uv run python -c` line in the recipe. Empirical: `uv pip list | grep -cE "^(pytest|ruff|mypy|hypothesis)"` = **0** on fresh venv after bootstrap-verify (was 4 before).
  - **CRITICAL — `pytest .` collected from AI-tool dotdirs** (12 errors). Expanded `norecursedirs` to include all 8 dotdirs (`.agent`, `.agents`, `.claude`, `.cursor`, `.gemini`, `.opencode`, `.pi`, `.omc`) + `.git`, `.tmp`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`. Before: `50 tests collected, 12 errors`. After: `6 tests collected`, zero errors.
  - **HIGH — `tests/subtree/__init__.py` violated spec's "NOT importable packages" rule AND created invalid module name `tests.crash-injection`.** Deleted all 7 `__init__.py` files; switched to `--import-mode=importlib` in `addopts`. Basename collision across 6 trees resolves cleanly without package mode.
  - **HIGH — `ci.yml` missing `permissions:` block** → inherited repo-default `GITHUB_TOKEN`. Added `permissions: contents: read` (least-privilege for a read-only PR gate).
  - **HIGH — `ci.yml` missing `concurrency:` group.** Added `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}`.
  - **HIGH — `ci.yml` triggered duplicate runs on push+PR.** Narrowed `push: branches: [main]` so non-main pushes only trigger via `pull_request`.
  - **HIGH — `uv python install 3.12` unpinned + cache-bypassing.** Consolidated into `astral-sh/setup-uv@v3` with `python-version: "3.12"` input.
  - **HIGH — `mypy.ini` adapter-boundary stanzas had wrong module-name prefix** (`services.orchestrator_adapter.adapters.*` never matched because mypy resolves by distribution module, not file path). Corrected to `orchestrator_adapter.*` + `worker_wrapper.*`; added `clawhip_daemon.adapters.*` + `telegram_gateway.adapters.*` for Stories 2.8 / 3.1.
  - **MEDIUM — `asyncio_mode = "auto"` would hijack every async def in the workspace** once real code lands. Switched to `"strict"` — requires explicit `@pytest.mark.asyncio`.
  - **MEDIUM — `ruff.toml` global `ignore = ["E501"]` disabled line-length everywhere.** Removed global; added narrow per-file-ignores for files with deliberately-long module docstrings (`services/*/src/*/__main__.py`, `*/src/*/__init__.py`, `tests/**`, `scripts/**`). Net: `ruff check .` now enforces E501 on all executable code — still passes with zero new findings.
  - **MEDIUM — `tests/conftest.py` `FROZEN_EPOCH` was tz-naive** violating AC-6 + Architecture §Format Patterns line 360 ("ISO 8601 UTC"). Added `from datetime import UTC` + `tzinfo=UTC`.
  - **MEDIUM — `mypy.ini` excluded `scripts/migrator/`** while the migrator is CI-exercised. Removed exclude so the path is discoverable when future stories point mypy at it.
  - **MEDIUM — `ruff.toml` extend-exclude omitted `node_modules` + `.tmp/`.** Added + `.agent-os/` for forward compatibility with another AI-tool dotdir.
  - **MEDIUM — `tests/**` per-file-ignore missing `D`.** Pre-emptively added for when Story 1.6/1.7 may select pydocstyle.
  - **MEDIUM — `scripts/migrator/tests/**` not in per-file-ignores.** Added with same rule-relaxations as `tests/**`.
  - **MEDIUM — `tests/.gitkeep` leftover.** Removed.
  - **LOW — dev-dep version floors** not added; accepted (uv.lock is the reproducibility anchor).
  - **LOW — `just lint` mypy target hard-coded**; accepted (concrete list matches today's strict-scope; revisit when `services/registry-foo` lands).
  - **Skipped (not defects):** `astral-sh/setup-uv@v3` floating major tag — inline comment notes "consider SHA-pinning when the project adopts dependabot"; FR47/NFR-M7 citation critique — scaffold commit title can't be rewritten without force-push; spec line 409 explicitly claims the CI-side of NFR-M7 and this story's CI harness is genuinely what enables the operator-runbook regression layer.
  - Live verification post-fix: `just bootstrap-verify` 13/13 + 0 dev deps; `just test` 6 skipped; `just lint` all 3 green; `just migrator-test-additive` 3/3; `pytest --collect-only .` 6/0; `ci.yml` YAML-valid.
- **2026-04-23 (finalize):** Completion Notes expanded with review-fix summary. Status `review` → `done`.
