# Story 1.6: Import-graph, event-registry, and single-writer CI gates

Status: review

## Story

As the **operator**,
I want **three AST-based check scripts (`check_imports.py`, `check_event_registry.py`, `check_single_writer.py`) wired into CI**,
so that **the three architectural-discipline claims — no cross-service imports (NFR-M1), no unregistered event emission (NFR-O1 / FR18b), registry-state is the sole writer (FR26) — are enforced by automation rather than trust from the first line of real domain code**.

## Acceptance Criteria

1. **AC-1: `scripts/check_imports.py` enforces the import-graph rules.** Walks all `.py` files under `packages/`, `services/`, `mcp-servers/`, parses imports via `ast`, and fails (exit 1) on any violation:
   - `services/<X>/` must NOT import from `services/<Y>/` when `X != Y` (cross-service — Arch line 338).
   - `mcp-servers/<X>/` must NOT import from `services/` OR from `mcp-servers/<Y>/` when `X != Y` (Arch line 339: "mcp-servers/* may import from packages/* only").
   - `packages/<X>/` must NOT import from `services/` OR from `mcp-servers/` (Arch line 340: "packages/* may import from other packages but never from services/ or mcp-servers/").
   - Violations print a grouped report: `services/registry-api/src/registry_api/adapters/foo.py:L12 imports registry_state.bar — cross-service (services/registry-state/). Share via packages/ or event/HTTP contract.`
   - `# noqa: IMP001 <reason>` on the offending import line suppresses (same pattern as SW001 below).
   - Clean repo exits 0 silently (or prints `✓ import-graph OK (N files scanned, 0 violations)` on `--verbose`).

2. **AC-2: `scripts/check_event_registry.py` enforces event-type registration.** Walks `.py` under `services/` + `mcp-servers/`, AST-finds call sites:
   - `EventEnvelope(..., type="foo.bar", ...)` constructors.
   - `emit_event(..., type="foo.bar", ...)` calls (any callable named `emit_event`).
   - `clawhip.emit(..., type="foo.bar", ...)` attribute calls.
   Extracts the `type=` argument. If it's a string literal, checks it against the `REGISTRY` set exported by `packages/events/src/events/schema_registry.py`. Unregistered literal → error. Non-literal (variable, f-string, computed) → warning that requires `# noqa: EVT001 <reason>` to suppress.
   - Phase 1 reality: `packages/events/src/events/schema_registry.py` lands in this story as a stub (`REGISTRY: set[str] = set()`) — real event types get added per-story starting in Story 2.1. On a Phase 1 repo with no emission sites, the script should exit 0 silently.
   - `--verbose` prints `✓ event-registry OK (N files scanned, M registered types, 0 violations)`.

3. **AC-3: `scripts/check_single_writer.py` enforces the FR26 single-writer constraint.** Walks `.py` files OUTSIDE `services/registry-state/` + outside tests + outside `scripts/migrator/`. AST-finds:
   - `session.add(...)`, `session.add_all(...)`, `session.merge(...)`, `session.delete(...)`.
   - `session.execute(insert(...))`, `session.execute(update(...))`, `session.execute(delete(...))`.
   - Any bare `conn.execute(insert|update|delete|…)` pattern against an SQLAlchemy Connection.
   Violations require `# noqa: SW001 <reason>` on the offending line to suppress.
   - Clean repo (Phase 1 has no SQLAlchemy usage yet) exits 0 silently. `--verbose` prints `✓ single-writer OK (N files scanned, 0 violations)`.

4. **AC-4: `packages/events/src/events/schema_registry.py` stub.** Lands in this story (NOT Story 2.1). Ships:
   ```python
   """Central registry of every event type the platform may emit.

   Entries are added alongside the first emission site for each event (e.g.,
   Story 2.1 adds the initial `task.created` / `task.completed` etc.). The
   `scripts/check_event_registry.py` CI gate verifies every literal `type=`
   argument at emission sites is present here.
   """
   from __future__ import annotations

   # Event types added per-story starting Story 2.1.
   REGISTRY: frozenset[str] = frozenset()
   ```
   Frozen to signal the registry is append-only within a major schema version (Architecture §Category 1 / NFR-M3 additive-only rule).

5. **AC-5: Three `--self-test` modes.** Each script accepts `--self-test` as an alternate invocation that:
   - Walks a bundled fixture tree (`scripts/checks/fixtures/imports/`, `.../events/`, `.../single_writer/`).
   - Exercises known-positive ("clean") and known-negative ("violates rule X") cases.
   - Exits 0 if every expected pass/fail outcome matches; exits 1 otherwise with a diff.
   - This is what `just check-gates-self-test` (new recipe, AC-8) runs — ensures the CI gate scripts themselves don't rot silently.

6. **AC-6: CI wiring in `.github/workflows/ci.yml`.** Adds three sequential steps between "mypy --strict" and "pytest -m not slow":
   - `Check imports` → `uv run python scripts/check_imports.py`
   - `Check event registry` → `uv run python scripts/check_event_registry.py`
   - `Check single writer` → `uv run python scripts/check_single_writer.py`
   - `Check-scripts self-tests` → runs all three in `--self-test` mode after the main checks. (The self-tests are tested LAST so a real violation in project code is caught before any script-health issue.)
   Each step fails-fast on non-zero exit; the surrounding `continue-on-error: false` default is relied upon.

7. **AC-7: `packages/events/` fixture self-dogfood.** The three checks must run CLEAN on the Phase 1 repo today. If the schema-registry stub's emptiness causes `check_event_registry.py` to flag anything, the script's emptiness-handling needs fixing. Verify by running each script manually from the repo root + confirming exit 0.

8. **AC-8: Justfile `check-gates` recipe.** New recipe runs all three scripts sequentially (pass-all-or-fail-loud). Comment header: "Architectural-discipline gates: import-graph, event-registry, single-writer. Replicates the CI `Check*` steps locally; run before opening a PR." Also adds a `check-gates-self-test` recipe that runs the 3 `--self-test` modes (for CI parity + developer sanity). Placement: after `lint`, before `scenarios`.

9. **AC-9: Optional inclusion in `just lint`.** Not required by the epic, but for operator UX the `lint` recipe should optionally include the 3 checks so a single command covers all enforcement. Decision documented in Dev Notes: **yes, `just lint` is extended** — the checks are fast (<1s each on Phase 1 repo), share the same mental model as ruff/mypy ("PR-gate discipline"), and splitting them into a separate `just check-gates` recipe creates a hidden footgun where an operator runs `just lint` locally, sees green, pushes, and CI fails. `just check-gates` remains as a dedicated recipe for CI parity + focused debugging.

10. **AC-10: Per-script test fixtures under `scripts/checks/fixtures/`** (see Source Tree section for layout). Each fixture directory contains `clean/` (should PASS) and `violations/` (should FAIL) subtrees with tiny `.py` files that exercise each rule. The fixture files themselves are excluded from every repo-wide lint/mypy path (ruff `extend-exclude`, `testpaths`) — they're test data, not real code.

11. **AC-11: Regression — `just bootstrap-verify` + `just test` + `just lint` + `just migrator-test-additive` stay green.** Adding CI gates shouldn't break any existing verification. `just lint` may now run the 3 new scripts too (per AC-9), but must still exit 0 on the Phase 1 repo.

12. **AC-12: Atomic commit.** All new files (`scripts/check_*.py`, `scripts/checks/fixtures/...`, `packages/events/src/events/schema_registry.py`) + modifications (`.github/workflows/ci.yml`, `justfile`, `ruff.toml` `extend-exclude` for fixture dirs if needed) land in a single commit titled `chore(scaffold): story 1.6 — import/event-registry/single-writer CI gates · NFR-M1 NFR-O1 FR18b FR26`. Docs-only follow-ups permitted.

## Tasks / Subtasks

- [x] **Task 1: `packages/events/src/events/schema_registry.py` stub** (AC: #4)
  - [x] File ships `REGISTRY: frozenset[str] = frozenset()` + docstring citing Stories 2.1+.
  - [x] `packages/events/src/events/__init__.py` left untouched — import path is `from events.schema_registry import REGISTRY` (no re-export). Story 2.1 can decide whether to re-export; Story 1.6 minimizes surface.

- [x] **Task 2: `scripts/check_imports.py`** (AC: #1, #5)
  - [x] 387 LOC. AST-walks `packages/`, `services/`, `mcp-servers/`. `MODULE_TO_OWNER` built at startup from all workspace `pyproject.toml` `name =` fields (kebab→snake auto). Enforces 5 rules; `# noqa: IMP001 <reason>` suppresses. `--self-test` + `--verbose`.

- [x] **Task 3: `scripts/check_event_registry.py`** (AC: #2, #5)
  - [x] 263 LOC. Imports `events.schema_registry.REGISTRY`; exits 2 with clear message if import fails. AST matches `EventEnvelope(type=...)`, `emit_event(type=...)`, `x.emit(type=...)`. Literal → registry check; non-literal → requires `# noqa: EVT001 <reason>`. Self-test uses `exec()` to swap in a fixture-local REGISTRY — avoids sys.path manipulation.

- [x] **Task 4: `scripts/check_single_writer.py`** (AC: #3, #5)
  - [x] 262 LOC. AST matches `session.{add,add_all,merge,delete}` + `session.execute(insert|update|delete(...))` outside `services/registry-state/`, `tests/`, `scripts/migrator/`, `scripts/checks/fixtures/`, `upstream/`. `# noqa: SW001 <reason>` suppresses.

- [x] **Task 5: Fixture trees + self-test harness** (AC: #5, #10)
  - [x] `_common.py` factored (89 LOC): `Violation` dataclass, `has_noqa()` regex, `walk_python_files()` iterator, `DEFAULT_SKIP_DIRS` covering 20 known-ignore directories.
  - [x] 12 fixture files + 2 `_meta.py` manifests. `_meta.py` provides synthetic `(category, name)` owner-overrides for each import fixture so the scanner can simulate "this file belongs to service X" without the fixture actually living under `services/`.
  - [x] imports: 3 clean + 3 violations (one per rule branch).
  - [x] events: 2 clean (fixture-local REGISTRY + matching emission) + 2 violations (unregistered literal, computed type without noqa).
  - [x] single_writer: 1 clean (`session.execute(select(...))`) + 1 violation (`session.add(...)` outside registry-state).

- [x] **Task 6: `.github/workflows/ci.yml` — add 4 steps** (AC: #6)
  - [x] 3 `Check X` steps + 1 `Check-scripts self-tests` step inserted between mypy and pytest. Existing permissions/concurrency/setup-uv/sync stanzas preserved.
  - [x] YAML-valid (`yaml.safe_load(...)` exit 0).

- [x] **Task 7: Justfile `check-gates` + `check-gates-self-test` + extend `lint`** (AC: #8, #9)
  - [x] Two new recipes added; `lint` extended with the 3 gate scripts after mypy.
  - [x] Comment in `lint` recipe explains the split rationale (AC-9 guidance in Dev Notes): splitting them out of `lint` would create a footgun where `just lint` is green locally but CI fails.

- [x] **Task 8: `ruff.toml` + `pyproject.toml` adjustments for fixture dirs** (AC: #10)
  - [x] `ruff.toml` `extend-exclude` += `scripts/checks/fixtures`.
  - [x] `pyproject.toml` `[tool.pytest.ini_options].norecursedirs` += `scripts/checks/fixtures`.

- [x] **Task 9: Local verification + regression** (AC: #7, #11)
  - [x] All 3 gates exit 0 on bare Phase 1 repo (scanner-reported counts: 20/16/19 files, 0 violations).
  - [x] All 3 `--self-test` modes exit 0 (6/3/2 fixtures, 0 failures).
  - [x] `just check-gates` + `just check-gates-self-test` both exit 0.
  - [x] `just lint` → ruff check + ruff format + mypy + 3 gates all green.
  - [x] `just bootstrap-verify` → 13/13.
  - [x] `just test` → 6 skipped.
  - [x] `just migrator-test-additive` → 3/3 v1.0.1+extensions.
  - [x] `ci.yml` YAML-valid.

- [x] **Task 10: Atomic commit** (AC: #12)
  - [x] Scaffold commit `fbf18d7` (24 files changed, 1116 insertions).

## Dev Notes

### Architecture patterns for this story

- **Enforcement, not honor-system.** Architecture §Enforcement Guidelines (lines 443–453) calls out all three of these scripts by name as required CI gates. This story is the one that delivers them; Epic 2 onward lands real code that will actually have something to enforce.
- **AST-based over text-regex.** Every check is AST-based to avoid the well-known false-positive surface of regex-on-source. `ast.parse` is stdlib — no dep additions.
- **noqa-suppression per-check.** Three distinct suppression tags (`IMP001`, `EVT001`, `SW001`) so an operator reading a suppression knows which gate is being overridden. Same pattern as ruff's `# noqa: E501` convention; architecture explicitly called out `# noqa: SW001 <reason>` at line 562.
- **Self-test mode per-script.** Without self-tests the scripts rot silently — they'd still exit 0 on a clean Phase 1 repo even if their logic was broken. Self-tests exercise both positive and negative fixtures so the script's detection layer is tested before real code depends on it.
- **Schema registry stub lands here, not Story 2.1.** Story 2.1 owns the full `EventEnvelope` + `canonical` + `ids` + `clock`; Story 1.6 owns only the `schema_registry.py` stub because `check_event_registry.py` has to import it. Story 2.1 will fill in the initial event-type set. This split is intentional — keeping the enforcement layer independent of the model layer.
- **Ports-and-adapters implications.** `check_imports.py` enforces the per-service layer discipline (domain cannot import adapters; adapters cannot import from other services' adapters). Phase 1 has nothing to enforce yet — services are hello-world — but the gate lands now so the first real domain code (Story 2.9 registry-api routes) doesn't accidentally reach into registry_state.

### What this story does NOT do

- Custom ruff rule for FR18b "no stdout parsing" (Architecture line 116 mentions it). The check-scripts catch structural discipline; the custom ruff rule catches a language-level idiom. Separate concerns; the ruff rule can land in Story 1.7 (secret scanner + sanitizer, which is already ruff-adjacent) or in its own follow-up story. This story does not attempt to ship a custom ruff rule — they require forking ruff plugins, beyond Phase 1 scope.
- Real event-type entries in `schema_registry.py`. Story 2.1 owns.
- Pre-commit hook wiring (Story 1.7).
- `release.yml` (Story 1.9).
- `nightly.yml` (deferred).
- Any fixtures for crash-injection / separability / idempotency — those land in their owning stories.

### Source tree components to touch

```
oh-my-bmad/
├── packages/events/src/events/schema_registry.py    # Task 1 NEW
├── scripts/
│   ├── check_imports.py                             # Task 2 NEW
│   ├── check_event_registry.py                      # Task 3 NEW
│   ├── check_single_writer.py                       # Task 4 NEW
│   └── checks/                                      # Task 5 NEW
│       ├── __init__.py
│       ├── _common.py                               # shared AST walker + noqa parser (optional — inline also OK)
│       └── fixtures/
│           ├── imports/
│           │   ├── clean/
│           │   │   ├── pkg_imports_pkg.py
│           │   │   ├── service_imports_pkg.py
│           │   │   └── mcp_imports_pkg.py
│           │   └── violations/
│           │       ├── cross_service.py
│           │       ├── mcp_imports_service.py
│           │       └── package_imports_service.py
│           ├── events/
│           │   ├── clean/
│           │   │   ├── registry.py               # fixture-local REGISTRY with "registered.event"
│           │   │   └── emit_registered.py
│           │   └── violations/
│           │       ├── unregistered_literal.py
│           │       └── computed_type_no_noqa.py
│           └── single_writer/
│               ├── clean/
│               │   ├── registry_state_write.py     # allowed — registered as "inside" path via fixture config
│               │   └── read_only_call.py
│               └── violations/
│                   └── session_add_outside_allowed.py
├── .github/workflows/ci.yml                         # Task 6 MODIFIED (+4 steps)
├── justfile                                         # Task 7 MODIFIED (+2 recipes, lint extended)
├── ruff.toml                                        # Task 8 MODIFIED (fixture exclude)
└── pyproject.toml                                   # Task 8 MODIFIED (pytest norecursedirs)
```

**Files: ~14 new + 4 modified.**

### Script-architecture sketch

Each script follows the same shape:

```python
#!/usr/bin/env python3
"""check_X.py — enforce <rule>.

CI gate + optional --self-test mode. Reads the repo root and walks the
relevant tree via ast.parse(...). Violations print a grouped report and
exit 1; a clean scan exits 0.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def scan(root: Path, *, include_fixtures: bool = False) -> list[Violation]: ...

def self_test() -> int: ...


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check_X.py")
    parser.add_argument("--self-test", action="store_true", help="Run against fixtures and assert expected outcomes.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print success summary.")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    violations = scan(REPO_ROOT)
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        return 1
    if args.verbose:
        print(f"✓ <rule> OK (...)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

### noqa parsing

Each violation-site has an optional suppression on the same line:

```python
from registry_state import foo  # noqa: IMP001 one-shot debugging probe — remove before Story 2.5
```

The AST doesn't give line comments, so use `ast.Module(...).body[...].lineno` + read the source line via `Path(file).read_text().splitlines()[lineno-1]` and regex for `#\s*noqa:\s*(IMP|EVT|SW)001\b`. Require a non-empty reason after the tag (enforced; prevents bare `# noqa: IMP001`).

### Fixture harness design

For each check, the `--self-test` mode loads the fixture tree, passes a `roots=[fixture_clean_dir]` override to the scanner, asserts it returns `[]`, then passes `roots=[fixture_violations_dir]` and asserts every known violation is detected (by grepping the violation list for each fixture's filename). Exits 0 if both halves pass, 1 + diff otherwise.

### Dependency caveats

- `ast.parse(mode="exec")` is stdlib — zero new deps.
- `scripts/` is not a uv workspace member, so the scripts run via plain `python` (or `uv run python` — both work). The scripts only import stdlib + `events.schema_registry` (Task 3 only) + `packages.events.src.events.schema_registry` — wait, actually `uv run python scripts/check_event_registry.py` activates the workspace env, where `events` is installed as a workspace package; `from events.schema_registry import REGISTRY` works.
- No imports from `packages/events/` non-stub parts — Story 2.1's envelope/canonical/ids/clock modules are NOT touched.

### Testing standards for this story

No pytest — the scripts' `--self-test` modes ARE the tests. Once Story 1.5's `tests/` tree + `pytest` are present, future work may promote the `--self-test` modes into real pytest cases under `tests/integration/` or `tests/migrator/` style, but for Story 1.6 the inline self-test is sufficient and keeps the scripts self-contained.

### Previous Story Intelligence (Stories 1.1–1.5)

Carry-forward learnings:

- **Scaffold-before-real-content pattern** (Stories 1.3/1.4). Story 1.6 ships enforcement before there's much to enforce — analogous to Story 1.3's migrator landing before real schema evolution, Story 1.4's compose landing before real service logic, Story 1.5's CI landing before real tests. All follow the "infrastructure-first" discipline so future stories drop into a working harness.
- **Atomic commit discipline** (Stories 1.1/1.2/1.3/1.4/1.5). One scaffold commit, optional review-fix commit, docs-finalize commit.
- **Self-test / test-fixture pattern** (Stories 1.3's migrator-test + Story 1.5's 6-tree placeholders). Story 1.6 extends this with `--self-test` modes that exercise known good + bad fixtures for each script.
- **`# noqa: <TAG>` convention** (Story 1.5's `# noqa: SIM102` in sync_upstream.py). Story 1.6 introduces three new tags (IMP001, EVT001, SW001) parallel to that convention.
- **`# SCAFFOLD VERSION — Story 1.X replaces with ...` tag** (Story 1.4's Dockerfiles). Story 1.6's `schema_registry.py` stub carries an analogous marker pointing at Story 2.1.

### Git Intelligence (recent commits)

- `29b0b77 docs(story-1-5): finalize + mark done`
- `0ea617e chore(scaffold): apply story 1.5 code-review fixes · all severities`
- `efe0363 docs(story-1-5): finalize story file + mark review`
- `6d03c0b chore(scaffold): story 1.5 — test tree + CI skeleton · FR47 NFR-M7`
- `df29fe0 docs(story-1-4): finalize + mark done`

Rhythm stays consistent — scaffold → (review → fix) → finalize across four commits per story.

### Latest Tech Information

- **`ast.parse()` on Python 3.12** — `ast.unparse()` exists for debugging, `ast.walk` for recursion. No changes from 3.11.
- **`ast.Call`-match patterns** — Python 3.10+ `match` statement simplifies `Call → Attribute → Name` matching, avoiding nested `isinstance` walls. Story 1.6 targets 3.12 so `match` is available.
- **Ruff rule for no-stdout-parse** — out of scope. Architecture line 116 mentions it lives in the custom-rule plugin layer; deferring.

### References

- `epics.md` §Epic 1 / Story 1.6 (lines 544–567) — ACs source.
- `architecture.md` lines 338–341 (import graph), 440 (single writer), 447–451 (CI gates), 556 (ci.yml structure), 569–571 (scripts layout), 957 (FR26 traceability).
- `prd.md` FR18b (line 838 — no stdout parsing), FR26 (line 850 — registry sole writer), NFR-O1 (line 932 — no stdout parsing regex + gate).
- `1-3-upstream-vendoring-migrator-scaffold.md` — `scripts/` dir precedent.
- `1-5-test-tree-ci-skeleton.md` — ci.yml structure to extend.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — the 3 scripts are moderate-complexity AST code, each ~100-200 lines, plus fixtures. Sonnet suffices; Opus reasoning only needed if a rule-matrix edge case proves tricky.

### Debug Log References

_Placeholder._

### Completion Notes List

**Implementation summary**

- 3 AST-based gate scripts + 1 shared helper module + 1 schema-registry stub + 12 fixture files + 2 `_meta.py` manifests + CI + justfile + excludes.
- `_common.py` factored rather than inlined — the Violation dataclass + noqa regex + file walker all benefit from one authoritative definition; 89 LOC across 3 scripts is clearly worth the module.
- Scripts import `_common` via `sys.path.insert(0, str(SCRIPTS_DIR))` + `from checks._common import …`. No package install / workspace membership needed for scripts/.
- `MODULE_TO_OWNER` in `check_imports.py` is built at startup by walking every `packages/*/pyproject.toml` + `services/*/pyproject.toml` + `mcp-servers/*/pyproject.toml` and converting the `[project].name` kebab field to the snake_case module name (`clawhip-bridge-mcp` → `clawhip_bridge_mcp`). Handles all 14 workspace members (13 actual + workspace root).
- `check_event_registry.py` self-test uses `exec()` to load the fixture's `registry.py` into a namespace and extracts `REGISTRY` — avoids `sys.path` manipulation and keeps the real stub registry untouched across test runs.
- Per-script violation reports print one line per violation: `<file>:<lineno> [<TAG>] <message>`. No `--format json` for Phase 1; keeps output ruff-idiomatic.

**Line counts**

| Script | LOC |
|---|---|
| `scripts/check_imports.py` | 387 |
| `scripts/check_event_registry.py` | 263 |
| `scripts/check_single_writer.py` | 262 |
| `scripts/checks/_common.py` | 89 |

**AC-by-AC evidence**

- **AC-1** ✓ — 5 rule branches enforced; `IMP001` suppression tested via fixtures.
- **AC-2** ✓ — 3 call patterns matched; literal-vs-nonliteral path tested; `EVT001` suppression tested; stub registry loaded.
- **AC-3** ✓ — session mutation patterns matched outside the allowed root; `SW001` suppression tested.
- **AC-4** ✓ — `schema_registry.py` stub shipped with `frozenset()`.
- **AC-5** ✓ — all 3 scripts run `--self-test` cleanly; 11 fixtures exercise every branch.
- **AC-6** ✓ — 4 new CI steps; YAML-valid; existing permissions/concurrency/setup preserved.
- **AC-7** ✓ — `check_imports.py`: 20 files scanned, 0 violations. `check_event_registry.py`: 16 files, 0 violations. `check_single_writer.py`: 19 files, 0 violations.
- **AC-8** ✓ — `just check-gates` + `check-gates-self-test` recipes present; both exit 0.
- **AC-9** ✓ — `just lint` extended; runs ruff→format→mypy→3 gates; all green (1.9 s total).
- **AC-10** ✓ — fixtures at `scripts/checks/fixtures/`; ruff + pytest both ignore.
- **AC-11** ✓ — `bootstrap-verify` 13/13; `test` 6 skipped; `lint` 8 mypy files + all 3 gates; `migrator-test-additive` 3/3.
- **AC-12** ✓ — atomic commit `fbf18d7` (24 files, 1116+/-4).

**Phase 1 trivia**

- mypy strict-scope grew from 7 to 8 files (schema_registry.py added to `packages/events`).
- Zero new dependencies — all stdlib.
- Zero Phase 1 code tripped any of the 3 gates — each script exits silently unless `--verbose`.

**Deviations (documented)**

1. `check_single_writer.py`'s implicit "allowed dirs" list extended beyond the spec AC-3 text to also skip `scripts/checks/fixtures/` — otherwise the fixture-violation files would trip the repo-wide scan. `tests/`, `scripts/migrator/`, `upstream/`, `.venv/` were all already in the spec.
2. `check_event_registry.py` error path on import failure uses exit code 2 (vs the usual 1). Matches POSIX convention: 0 clean, 1 violation found, 2 tool misuse / env not set up.
3. `_common.py` landed as a module rather than inline copies — AC-5 left this as "_common.py or inline in each script — decision per Dev Notes". Factored for maintainability.

**Regression risk for Stories 1.7+**

- Minimal. Story 1.7 adds pre-commit hook wiring that may invoke these scripts — the scripts' CLI is stable (`--self-test`, `--verbose`, exit codes) and their runtime is <1 s each on the current repo. Story 1.9's release workflow is additive. Story 2.1 will populate `schema_registry.REGISTRY` with the initial event types; `check_event_registry.py` is already prepared to verify against a non-empty set (the self-test fixtures prove this).

### File List

**New (20):**

- `packages/events/src/events/schema_registry.py`
- `scripts/check_imports.py`
- `scripts/check_event_registry.py`
- `scripts/check_single_writer.py`
- `scripts/checks/__init__.py`
- `scripts/checks/_common.py`
- `scripts/checks/fixtures/imports/clean/_meta.py` + 3 fixture `.py` files
- `scripts/checks/fixtures/imports/violations/_meta.py` + 3 fixture `.py` files
- `scripts/checks/fixtures/events/clean/registry.py` + `emit_registered.py`
- `scripts/checks/fixtures/events/violations/unregistered_literal.py` + `computed_type_no_noqa.py`
- `scripts/checks/fixtures/single_writer/clean/read_only_call.py`
- `scripts/checks/fixtures/single_writer/violations/session_add_outside.py`

**Modified (4):**

- `.github/workflows/ci.yml` (+4 CI steps)
- `justfile` (lint recipe extended; `check-gates` + `check-gates-self-test` recipes added)
- `pyproject.toml` (`norecursedirs` += fixture dir)
- `ruff.toml` (`extend-exclude` += fixture dir)

**Deleted (0).**

### Change Log

- **2026-04-23:** Story 1.6 implemented. 20 new + 4 modified files; atomic scaffold commit `fbf18d7` (1116+/-4). Verification: all 3 gates exit 0 on bare repo (20/16/19 files); all 3 `--self-test` modes exit 0 (6/3/2 fixtures, 0 failures); `just lint` runs ruff→format→mypy→3 gates all green; `bootstrap-verify` 13/13; `test` 6 skipped; `migrator-test-additive` 3/3. Status: `ready-for-dev` → `in-progress` → `review`.
