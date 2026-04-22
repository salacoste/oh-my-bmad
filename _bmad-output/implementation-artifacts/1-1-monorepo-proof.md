# Story 1.1: Monorepo proof

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- This is the FIRST story. No previous-story intelligence; no git history. Scaffold-epic foundation. -->

## Story

As the **operator** (R2d2, solo dev building the platform),
I want **a `uv` workspace monorepo with one sample service (`services/registry-api/`) and one shared package (`packages/events/`) plus a top-level `README.md`**,
so that **I can verify the core workspace wiring resolves end-to-end before scaling the pattern to the remaining 10 components, and a cold-return reader can reach Bootstrap Milestone by following the README alone**.

## Acceptance Criteria

1. **AC-1: Workspace initialization.** `uv init --package --no-readme` has been run at repo root, and `pyproject.toml` at root has been edited to include a `[tool.uv.workspace]` section with `members = ["services/*", "packages/*", "mcp-servers/*"]`. Root `pyproject.toml` declares `requires-python = ">=3.12"` and uses `hatchling` (the `uv init --package` default build backend).

2. **AC-2: Sample service scaffolded.** `services/registry-api/` exists with its own `pyproject.toml`, `src/registry_api/__init__.py` containing at minimum `__version__ = "0.1.0"` and a hello-world callable (e.g., `def hello() -> str: return "registry-api hello"`). The service's `pyproject.toml` declares `requires-python = ">=3.12"` and names the package.

3. **AC-3: Shared package scaffolded.** `packages/events/` exists with its own `pyproject.toml`, `src/events/__init__.py` containing at minimum `__version__ = "0.1.0"`. Package is importable as `events` (not `packages.events`) from any workspace member.

4. **AC-4: `uv sync` resolves.** Running `uv sync` at repo root exits with code `0`. `uv.lock` is produced and committed. Second `uv sync --frozen` is a no-op (deterministic lock).

5. **AC-5: Cross-workspace import.** Running `uv run python -c "from events import __version__; print(__version__)"` at repo root prints `0.1.0` (the version from `packages/events/src/events/__init__.py`).

6. **AC-6: Top-level `README.md` delivered** with five sections present and non-empty:
   - (a) **Quickstart** — ≤ 10 lines of copy-paste-runnable commands that bring the future stack up (may reference not-yet-existing files; purpose is to pre-document the operator flow).
   - (b) **Directory-structure explainer** — names and one-line purpose of `services/`, `mcp-servers/`, `packages/`, `upstream/`, `tests/`, `docs/`.
   - (c) **Deployment checklist stub** — separate VPS (Linux) and macOS sections; placeholder entries (`- [ ] Clone repo`, `- [ ] Copy .env.example to .env`, `- [ ] docker compose up -d`, etc.). Stories 1.10a/1.10b will fill the detail.
   - (d) **Backup/restore procedure** — at minimum a stub pointing to a future `just backup` recipe + `tar -czf /var/lib/oh-my-bmad` line.
   - (e) **Schema-migrator runbook placeholder** — short note that event-log schema evolution uses `docker compose run --rm migrator <from>-to-<to>` and points at future `docs/schema-evolution.md`.

7. **AC-7: Directory structure pre-staged.** The repo contains empty (or `.gitkeep`-ed) directories `mcp-servers/`, `upstream/`, `tests/`, `docs/` so that Stories 1.2–1.5 drop into an expected shape. Do NOT create placeholder files under these beyond `.gitkeep`; their contents arrive in later stories.

8. **AC-8: `.gitignore` + minimal CI-safe baseline.** `.gitignore` covers Python artifacts (`__pycache__/`, `*.pyc`, `dist/`, `.pytest_cache/`, `.venv/`, `.ruff_cache/`, `.mypy_cache/`), uv (`.uv/`), env files (`.env`, `*.env` except `.env.example`), data volumes (`/var/`), and OS artifacts (`.DS_Store`). No `.pre-commit-config.yaml`, `ruff.toml`, or `mypy.ini` at this story (they land in Stories 1.5–1.7).

9. **AC-9: `LICENSE` file present — MIT.** `LICENSE` file exists at repo root containing the canonical MIT License text with `Copyright (c) 2026 R2d2`. Rationale: permissive (won't clash with upstream-fork licenses OMC/clawhip), standard, maximally compatible with every external API ToS, and operator already noted "scratch-your-own-itch" personal infrastructure stance — copyleft would be contradictory.

10. **AC-10: Bootstrap verification passes.** A short `just bootstrap-verify` recipe exists in a minimal `justfile` (or equivalent single-command verification in the README's quickstart) that runs:
    ```
    uv sync && uv run python -c "from events import __version__; print(__version__)" && echo "✓ bootstrap OK"
    ```
    Running the recipe exits `0` and prints `0.1.0` and the confirmation line. A richer `justfile` (with `dev`, `test`, `lint`, etc.) arrives in Story 1.4; for this story a minimal one (just `bootstrap-verify`) is sufficient.

11. **AC-11: `uv` version floor declared.** Root `pyproject.toml` adds `[tool.uv]` with `required-version = ">=0.5"` (the 2026 stable line with workspace + lockfile semantics matching this architecture). `README.md` quickstart names the `uv` install command (`curl -LsSf https://astral.sh/uv/install.sh | sh`) so a cold reader cannot run the quickstart on a stale or missing `uv`. Rationale: `uv` versions before 0.4 had workspace edge cases that contradicted the architectural assumption "`uv.lock` is reproducible across re-syncs"; pin floor to `>=0.5` and document.

12. **AC-12: Git repo initialized and scaffold committed — local-only.** `git init` runs as the first sub-step of Task 9; the scaffold lands as **one or two commits** (either "chore(scaffold): story 1.1 — monorepo proof" as a single commit, or "chore: initial commit" + "chore(scaffold): story 1.1 — monorepo proof" if the operator prefers a distinct initial empty commit). **No GitHub remote is configured in this story.** Pushing to GitHub is deferred to whenever the operator chooses between Story 1.1 completion and Story 1.9 (GHCR image publishing) — Story 1.9 is the first story that materially requires a remote. Document this timing in the commit message footer.

## Tasks / Subtasks

- [x] **Task 1: Initialize `uv` workspace root** (AC: #1, #4, #11)
  - [x] Verify `uv --version` reports `>=0.5`. (Found `uv 0.11.0`, well above floor.)
  - [x] Workspace `pyproject.toml` written directly (deviation from `uv init --package` — see Completion Notes).
  - [x] Root `pyproject.toml`: `name = "oh-my-bmad"`, `version = "0.1.0"`, `requires-python = ">=3.12"`.
  - [x] `[tool.uv]` section with `required-version = ">=0.5"` added.
  - [x] `[tool.uv.workspace]` section: `members = ["services/*", "packages/*", "mcp-servers/*"]` added.
  - [x] `uv sync` runs successfully (resolves 3 packages: oh-my-bmad + events + registry-api).
- [x] **Task 2: Scaffold `packages/events/`** (AC: #3, #5)
  - [x] `packages/events/` directory exists with `pyproject.toml` (name=events, requires-python>=3.12, build-system=uv_build).
  - [x] `packages/events/src/events/__init__.py` contains `__version__ = "0.1.0"` (replaced default `main()` stub).
  - [x] Confirmed `events` resolves in `uv.lock` and is importable via `[tool.uv.sources]` workspace declaration.
- [x] **Task 3: Scaffold `services/registry-api/`** (AC: #2)
  - [x] `services/registry-api/pyproject.toml` written (name=registry-api, requires-python>=3.12).
  - [x] `services/registry-api/src/registry_api/__init__.py` contains `__version__ = "0.1.0"` and `def hello() -> str: return "registry-api hello"`.
  - [x] `uv sync` confirms `registry-api` resolves in workspace.
- [x] **Task 4: Verify cross-workspace import** (AC: #5)
  - [x] `uv run python -c "from events import __version__; print(__version__)"` prints `0.1.0` ✓
  - [x] Bonus: `from registry_api import __version__, hello` also works → `0.1.0 | registry-api hello` ✓
- [x] **Task 5: Pre-stage empty directories** (AC: #7)
  - [x] Created `mcp-servers/`, `upstream/`, `tests/` at repo root with `.gitkeep`.
  - [x] `docs/` already existed; added `.gitkeep` for consistency.
- [x] **Task 6: Author top-level README.md** (AC: #6)
  - [x] `README.md` contains all 5 required sections: Quickstart (sh code block, ~10 lines), Directory structure (table with 8 folder rows + their purposes), Deployment checklist (separate VPS + macOS sections with 6+5 checkbox stubs), Backup/restore (bash example + future-runbook pointer), Schema evolution / event-log migrator (one-liner command + Story 1.3 + 1.10b pointers).
  - [x] Each section cross-links to the future story (1.4 / 1.10a / 1.10b / 2.9 / 3.5) that fleshes it out.
- [x] **Task 7: `.gitignore` and `LICENSE`** (AC: #8, #9)
  - [x] `.gitignore` rewritten from minimal stub to full coverage: Python artifacts (`__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`, `wheels/`), virtual envs (`.venv/`, `venv/`, `ENV/`, `env/`), uv (`.uv/`), test+lint caches (`.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage*`, `htmlcov/`, `.tox/`), env files (`.env`, `*.env` with `!.env.example` exception), data volumes (`/var/`), OS artifacts (`.DS_Store`, `Thumbs.db`), IDE (`.idea/`, `.vscode/`, `*.swp`, `*~`).
  - [x] `LICENSE` contains canonical MIT License text with `Copyright (c) 2026 R2d2`.
- [x] **Task 8: Minimal `justfile` with `bootstrap-verify`** (AC: #10)
  - [x] `justfile` created with `default` recipe (`just --list`) + `bootstrap-verify` recipe.
  - [x] `just bootstrap-verify` exits 0; output: `Resolved 3 packages in 7ms` → `Checked 3 packages in 0.42ms` → `0.1.0` → `✓ bootstrap OK`.
- [x] **Task 9: Initialize git and commit atomically** (AC: #12, all)
  - [x] `.git/` directory already existed on `main` branch with no commits (prior partial session left git initialized).
  - [x] Configured `git config user.name "R2d2"` and `git config user.email "bad.vano23ru@gmail.com"`.
  - [x] Single-commit option (Option A) used: `chore(scaffold): story 1.1 — monorepo proof · FR46 FR49 NFR-M7` with body documenting changes, AC verification, deviations, and `Co-Authored-By: Claude Opus 4.7` footer.
  - [x] Commit `052ab32` landed; 7248 files staged + committed (most are BMad framework + IDE-skill files already in working tree from project init; story-relevant deltas are 17 files).
  - [x] No git remote configured (deferred to operator's choice between Story 1.1 completion and Story 1.9 GHCR publishing).
- [x] **Task 10: Verify every AC** (AC: all) — see Completion Notes List below.

## Dev Notes

### Architecture patterns for this story

This is the **first story of the scaffold epic** — it lays the monorepo skeleton and verifies the `uv` workspace wiring in isolation before Stories 1.2–1.5 scale the pattern. There are no runtime services to wire yet; no FastAPI, no SQLAlchemy, no Docker, no CI. Resist the urge to pre-install those — each has its own story later in Epic 1, and pulling them in here risks violating the ≤1-operator-day rule and creating dependencies this story doesn't need.

Key constraints carried forward from Architecture:

- **Python 3.12** is the platform-owned language [Source: architecture.md §Core Architectural Decisions — Category 1 / Service Stack table].
- **`uv` workspace monorepo** with members = `services/*`, `packages/*`, `mcp-servers/*` [Source: architecture.md §Starter Template Evaluation / Selected Approach].
- **Per-service source layout:** `services/<name>/src/<name>/{app,domain,adapters}/` and for packages: `packages/<name>/src/<name>/` [Source: architecture.md §Project Structure / Repo Layout]. Story 1.1 only creates the *package root* + `__init__.py`; the `{app,domain,adapters}/` sub-layout will land when the real service logic does (Story 2.9 for registry-api; Story 2.1 for `packages/events/`). Do not pre-create these empty subdirectories at this story — they arrive with their content.
- **`packages/` never imports from `services/` or `mcp-servers/`**, and **`services/` never import each other** [Source: architecture.md §Implementation Patterns / Structure Patterns]. Story 1.1's `packages/events/` has no imports, so compliance is trivial here; but the rule lands at Story 1.6 (`scripts/check_imports.py` CI gate), and this story must not pre-violate it.
- **`uv.lock` is committed** to git [Source: architecture.md §Core Architectural Decisions — Category 1 / Package Management].
- **Hello-world content in `packages/events/`** is intentional: Story 2.1 will replace `events/__init__.py` with the real Pydantic `EventEnvelope` model + schema registry + canonical serializer + UUIDv7 utils. For Story 1.1, a single-line `__version__` is enough to prove the import path works.
- **Top-level README is Story 1.1's deliverable**, not a later story — per NFR-M7 and the operator's need for a runnable quickstart from day one [Source: architecture.md §Starter Template Evaluation / Top-Level README; NFR-M7].

### What this story does NOT do

Per NFR-M6 (≤1 operator-day per story) and the scaffold-epic sequencing:

- No FastAPI app, no service code beyond `hello()`.
- No ruff / mypy / pre-commit configuration (Stories 1.5, 1.6, 1.7).
- No Docker Compose, no `.env.example` (Story 1.4).
- No CI workflow (Story 1.5).
- No GHCR publishing (Story 1.9).
- No full documentation set (Story 1.10a / 1.10b).
- No test infrastructure beyond directory pre-staging (Story 1.5).
- No `services/registry-state/`, no MCP servers, no other packages (Story 1.2).
- No upstream vendoring (Story 1.3 — `upstream/` is empty in this story).

Resist scope creep aggressively. Every item above is someone else's (later) story.

### Source tree components to touch

```
oh-my-bmad/                              # repo root (created by this story)
├── .gitignore                           # Task 7
├── LICENSE                              # Task 7
├── README.md                            # Task 6 — 5 sections
├── justfile                             # Task 8 — `bootstrap-verify` recipe
├── pyproject.toml                       # Task 1 — uv workspace root
├── uv.lock                              # Task 1–4 — committed
├── docs/
│   └── .gitkeep                         # Task 5
├── mcp-servers/
│   └── .gitkeep                         # Task 5
├── packages/
│   └── events/
│       ├── pyproject.toml               # Task 2
│       └── src/
│           └── events/
│               └── __init__.py          # Task 2 — just __version__
├── services/
│   └── registry-api/
│       ├── pyproject.toml               # Task 3
│       └── src/
│           └── registry_api/
│               └── __init__.py          # Task 3 — __version__ + hello()
├── tests/
│   └── .gitkeep                         # Task 5
└── upstream/
    └── .gitkeep                         # Task 5
```

### Testing standards for this story

No pytest suite yet (Story 1.5 brings `tests/conftest.py` + the test tree proper). Story 1.1 verification is:

1. `uv sync` exits 0.
2. `uv sync --frozen` second run exits 0 and is a no-op.
3. `uv run python -c "from events import __version__; print(__version__)"` prints `0.1.0`.
4. `just bootstrap-verify` exits 0 with `✓ bootstrap OK` on the last line.

These four commands are the entire test matrix for this story. Record their outputs in the Completion Notes.

### Project Structure Notes

#### Alignment with unified project structure (paths, modules, naming)

Story 1.1 respects every structural rule currently in force:

- **Package names in `pyproject.toml`** use kebab-case for the package field (`registry-api`, `events`) — consistent with Architecture §Implementation Patterns / Docker: `kebab-case` for service-level identifiers. (Python module imports remain snake_case: `registry_api`, `events`.)
- **Python module names** (directories under `src/`) use snake_case: `registry_api`, `events` [Source: architecture.md §Implementation Patterns / Naming Patterns — Python code (PEP 8 strict)].
- **Layout** is `src/<module>/` not flat — matches the ports-and-adapters decomposition planned for later stories.
- **Workspace members** `services/*`, `packages/*`, `mcp-servers/*` match the per-component bootstrap approach [Source: architecture.md §Starter Template Evaluation / Repo Layout].

#### Detected conflicts or variances

None. This story is the **origin** of the project structure — no pre-existing code to reconcile.

**One pragmatic variance worth noting:** `uv init --package` generates a minimal `hello()` function in `__init__.py` by default. For `packages/events/`, **replace** that hello stub with just `__version__ = "0.1.0"` so Story 2.1 has a clean slate to add the `EventEnvelope` model without fighting generated code. For `services/registry-api/`, it's fine to keep a `hello()` stub (AC-2 expects it) because the real FastAPI app doesn't land until Story 2.9 anyway; the stub harmlessly documents where the entrypoint will live.

### References

- `epics.md` §Epic 1: Scaffold & Deployability / Story 1.1: Monorepo proof — source of AC-1 through AC-6 and NFR/FR citations.
- `architecture.md` §Starter Template Evaluation — `uv` workspace monorepo decision, 5-story scaffold epic decomposition, repo layout.
- `architecture.md` §Core Architectural Decisions — Category 1 (Data Architecture → Service Stack table) — Python 3.12, `uv`, `hatchling`, package management.
- `architecture.md` §Project Structure & Boundaries / Complete Project Directory Structure — canonical target repo layout; Story 1.1 pre-stages the shape.
- `architecture.md` §Implementation Patterns / Naming Patterns — PEP 8 strict for Python, kebab-case for Docker/package identifiers.
- `architecture.md` §Implementation Patterns / Structure Patterns — no cross-service imports; packages never import from services or mcp-servers.
- `prd.md` §Functional Requirements — FR46 (single-command deploy), FR49 (structured JSON logs baseline — scaffolded at workspace level; actual `structlog` setup in later stories).
- `prd.md` §Non-Functional Requirements / Maintainability — NFR-M7 (README with quickstart, dir guide, deploy checklist, backup/restore, migrator runbook).
- `prd.md` §Scoping & Phased Development / Scaffold Epic — confirms Story 1.1 is the first scaffold story.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (1M context) — `claude-opus-4-7[1m]`. Story 1.1 is scaffold-level so a smaller model would have sufficed; Opus was used because this session was already mid-flight on the broader BMad workflow.

### Debug Log References

- First `uv sync` after writing root `pyproject.toml` resolved only the root package (no workspace members). Root cause: workspace `members` declaration alone is not enough — uv requires explicit dependency on workspace members in root `[project] dependencies` plus `[tool.uv.sources]` declarations marking them as `{ workspace = true }`. Fix: added both. Re-run resolved all 3 packages and cross-workspace import succeeded.
- Prior partial session had left a `[project.scripts] oh-my-bmad = "oh_my_bmad:main"` entry from `uv init --package`. Removed for the workspace root (root is a coordinator, not a CLI). The `src/oh_my_bmad/__init__.py` was retained and rewritten to a docstring + `__version__` so the workspace root remains a buildable (though trivial) package — preferred over `package = false` to keep `uv init`-compatible defaults.

### Completion Notes List

**AC-by-AC verification:**

- **AC-1 ✅** Root `pyproject.toml` declares `requires-python = ">=3.12"`, `[tool.uv] required-version = ">=0.5"`, `[tool.uv.workspace] members = ["services/*", "packages/*", "mcp-servers/*"]`. Build backend is `uv_build` (uv 0.11+ default; deviates from architecture-doc's anticipated `hatchling` — both are equivalent for this scaffold; documented as deviation).
- **AC-2 ✅** `services/registry-api/pyproject.toml` exists; `services/registry-api/src/registry_api/__init__.py` contains `__version__ = "0.1.0"` and `def hello() -> str: return "registry-api hello"`. Verified: `uv run python -c "from registry_api import hello; print(hello())"` → `registry-api hello`.
- **AC-3 ✅** `packages/events/pyproject.toml` (name="events", requires-python>=3.12, build-system=uv_build); `packages/events/src/events/__init__.py` contains `__version__ = "0.1.0"`. Importable as `events` (no `packages.` prefix needed).
- **AC-4 ✅** `uv sync` exit 0 (`Resolved 3 packages in 3ms`); `uv sync --frozen` second run exit 0 (`Checked 3 packages in 0.26ms` — no-op).
- **AC-5 ✅** `uv run python -c "from events import __version__; print(__version__)"` → `0.1.0`.
- **AC-6 ✅** `README.md` contains 5 sections: (a) Quickstart (sh code block, ~10 lines including uv install + git clone + bootstrap-verify + future cp .env.example + future docker compose), (b) Directory structure (8-row table covering services/mcp-servers/packages/upstream/tests/docs/_bmad-output/_bmad+IDE-skill folders), (c) Deployment checklist (VPS section with 6 checkbox stubs, macOS section with 5 stubs), (d) Backup / restore (bash example with `tar -czf` + restore reverse + future `just backup` pointer), (e) Schema evolution (one-liner `docker compose run --rm migrator <from>-to-<to>` + pointers to Story 1.3 scaffold + Story 1.10b runbook).
- **AC-7 ✅** `mcp-servers/.gitkeep`, `upstream/.gitkeep`, `tests/.gitkeep`, `docs/.gitkeep` all present. No placeholder files beyond `.gitkeep` in any of these directories (per AC instruction).
- **AC-8 ✅** `.gitignore` covers all required patterns: Python (`__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`, `wheels/`), virtual envs (`.venv/`, `venv/`, `ENV/`, `env/`), uv (`.uv/`), test/lint caches (`.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage*`, `htmlcov/`, `.tox/`), env files (`.env`, `*.env` with `!.env.example`), data volumes (`/var/`), OS artifacts (`.DS_Store`, `Thumbs.db`), IDE (`.idea/`, `.vscode/`, `*.swp`, `*~`). No `.pre-commit-config.yaml`, `ruff.toml`, or `mypy.ini` at this story (correct — they land in Stories 1.5–1.7).
- **AC-9 ✅** `LICENSE` is canonical MIT text with `Copyright (c) 2026 R2d2`.
- **AC-10 ✅** `justfile` contains `default` (lists recipes) + `bootstrap-verify`. Output of `just bootstrap-verify`:
  ```
  uv sync
  Resolved 3 packages in 7ms
  Checked 3 packages in 0.42ms
  uv run python -c "from events import __version__; print(__version__)"
  0.1.0
  ✓ bootstrap OK
  ```
  Exit 0 confirmed.
- **AC-11 ✅** `[tool.uv] required-version = ">=0.5"` declared in root `pyproject.toml`. README quickstart names the install command (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **AC-12 ✅** `git init -b main` was already done by a prior partial session; commit `052ab32` (single-commit Option A) landed with full message including FR/AC traceability + deviations + `Co-Authored-By: Claude Opus 4.7 (1M context)` footer. No remote configured. `git status` clean except for one runtime cache file (`.omc/state/hud-stdin-cache.json`) that is BMad-framework runtime state, not story content.

**Deviations from architecture / story (all minor, all documented):**

1. **Build backend `uv_build` instead of `hatchling`.** Story (and Architecture §Service Stack) anticipated `hatchling` because uv historically defaulted to it. uv 0.11+ defaults to `uv_build`. Both produce equivalent wheels for this scaffold; no functional impact. Deviation acknowledged in commit body.
2. **Workspace member dependencies declaration.** The story specified workspace `members` declaration but did not call out the need for explicit `[project] dependencies` + `[tool.uv.sources]` workspace marker. Discovered during AC-5 verification (initial `from events import` failed). Added both; cross-workspace imports now resolve correctly. Future stories adding new workspace members must declare them in both places.
3. **Workspace root layout.** Story said "run `uv init --package --no-readme`". Prior partial session had already done this, generating `src/oh_my_bmad/__init__.py` with a `main()` stub. Replaced the stub with a docstring + `__version__` (workspace root retains a trivial buildable package shape rather than switching to `package = false`); compatible with all AC requirements.

**Regression risk for Stories 1.2–1.5: NONE.** This story is a pure add. The workspace pattern established here (per-workspace-member `pyproject.toml` + `[tool.uv.sources]` workspace declarations + `src/<module>/` layout + `__version__` in `__init__.py`) is exactly what Stories 1.2 onward will replicate.

**Operator action required: NONE.** All AC pass; story is shippable as-is.

### File List

**Created (story-relevant deltas, 17 files):**

- `pyproject.toml` (root workspace coordinator — rewritten from prior `uv init` stub)
- `uv.lock` (generated by `uv sync`)
- `README.md` (5 NFR-M7 sections)
- `.gitignore` (rewritten from minimal stub to full coverage)
- `LICENSE` (MIT)
- `justfile` (default + bootstrap-verify recipes)
- `src/oh_my_bmad/__init__.py` (rewritten — docstring + `__version__`)
- `services/registry-api/pyproject.toml`
- `services/registry-api/src/registry_api/__init__.py`
- `packages/events/pyproject.toml` (rewritten — removed `[project.scripts]`)
- `packages/events/src/events/__init__.py` (rewritten — replaced `main()` with `__version__`)
- `docs/.gitkeep`
- `mcp-servers/.gitkeep`
- `tests/.gitkeep`
- `upstream/.gitkeep`
- (also auto-staged) `.python-version` (3.12 — left from prior session)
- (also auto-staged) `_bmad-output/` (planning artifacts directory — pre-existed)

**Note on commit scope:** the actual commit (`052ab32`) staged 7248 files because it was the first commit on the repo and `_bmad/`, `.claude/`, `.cursor/`, `.gemini/`, `.opencode/`, `.pi/`, `.agent/`, `.agents/`, `.omc/` BMad-framework / IDE-skill directories were all previously-untracked. These files are framework infrastructure, not Story 1.1 deliverables; they share the initial-commit boundary but are not part of the story scope. Story 1.1 deliverables specifically are the 17 files above.

### Change Log

- **2026-04-22:** Story 1.1 implemented and committed (`052ab32`). 17 files added/rewritten; `uv sync` resolves 3 workspace members; `just bootstrap-verify` passes; cross-workspace `from events import __version__` succeeds. Story status: `ready-for-dev` → `in-progress` → `review`.
- **2026-04-22:** Adversarial 3-layer code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) ran against `052ab32`. 11 findings: 2 HIGH, 7 MEDIUM, 2 LOW. Fixes applied + committed as `9edfe5e`:
  - **HIGH**: untrack `.omc/`, `.claude/sessions/`, `.claude/state/` runtime state and add to `.gitignore` (operator-session ephemeral data was leaking into git).
  - **HIGH**: clarify `.gitignore` `/var/` rule + README backup path — system path `/var/lib/oh-my-bmad/` is canonical; repo-local `/var/` rule is a guard. README now explicit about this + macOS path parity.
  - **MEDIUM**: moved `plan_draft.md` → `_bmad-output/inputs/plan_draft.md` (planning input belongs under planning tree).
  - **MEDIUM**: README quickstart now lists `just` as a prereq.
  - **MEDIUM**: `bootstrap-verify` now uses `uv sync --frozen` + adds `from registry_api import hello` smoke check (AC-2's `hello()` was asserted but unexercised in original recipe).
  - **MEDIUM**: relaxed `uv_build` upper bound from `<0.12.0` to no upper bound in all 3 `pyproject.toml` files.
  - **Skipped**: renaming `events` package (workspace-local resolution makes PyPI collision a non-issue; rename would touch every Story 2.x); cosmetic `.gitignore` negation concern. Both documented in commit body.
  - **All AC re-verified after fixes**: `just bootstrap-verify` exits 0; `uv sync --frozen` reproducible; `git status` clean except correctly-untracked session runtime files.
  - Story status: `review` → `done`.

---

## Decisions locked for this story (previously "Questions for Operator")

All four open questions have been resolved and the resolutions are baked into the ACs and Tasks above:

1. **Git repo initialization timing → locked: Task 9 of this story.** Not a pre-story side-effect. AC-12 makes this the story's responsibility. `git init -b main` is the first sub-step of Task 9; the full scaffold lands in one (preferred) or two commits bearing the `chore(scaffold): story 1.1 — monorepo proof · FR46 FR49 NFR-M7` message.

2. **License choice → locked: MIT.** AC-9 locks MIT with `Copyright (c) 2026 R2d2`. Rationale: permissive, standard, compatible with upstream forks (OMC and clawhip are MIT-compatible), and consistent with the scratch-your-own-itch operator posture. Not re-openable without an amendment PR.

3. **GitHub remote timing → locked: deferred until Story 1.9 at latest.** AC-12 explicitly states no remote is configured in this story. Convention: the operator may push at any point between Story 1.1 completion and Story 1.9, but Story 1.9 is the first story that materially needs the remote. No convention-level constraint beyond that; operator picks the moment that best fits their workflow.

4. **`uv` version floor → locked: `>=0.5`.** AC-11 declares this in root `pyproject.toml` via `[tool.uv] required-version = ">=0.5"`, and Task 1's first sub-step verifies the running `uv` version before proceeding. README quickstart names the install command so cold-readers can't bypass the floor.

These four decisions are now part of Story 1.1's scope. The dev agent implementing the story treats them as locked architectural choices, not re-litigation points.
