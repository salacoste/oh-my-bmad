# Story 1.1: Monorepo proof

Status: in-progress

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

- [ ] **Task 1: Initialize `uv` workspace root** (AC: #1, #4, #11)
  - [ ] Verify `uv --version` reports `>=0.5`. If not, run `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `brew install uv` on macOS) before proceeding.
  - [ ] Run `uv init --package --no-readme` in the repo root directory.
  - [ ] Edit the generated root `pyproject.toml`: remove the top-level `[project]` section's unneeded keys, set `name = "oh-my-bmad"`, `version = "0.1.0"`, `requires-python = ">=3.12"`.
  - [ ] Add a `[tool.uv]` section with `required-version = ">=0.5"` (AC-11).
  - [ ] Add a `[tool.uv.workspace]` section: `members = ["services/*", "packages/*", "mcp-servers/*"]`.
  - [ ] Verify `uv sync` runs successfully (will be empty resolution at this point).
- [ ] **Task 2: Scaffold `packages/events/`** (AC: #3, #5)
  - [ ] Create `packages/events/` directory.
  - [ ] Inside, run `uv init --package --no-readme`; accept the generated `pyproject.toml` structure.
  - [ ] Edit `packages/events/pyproject.toml`: set `name = "events"`, `version = "0.1.0"`, `requires-python = ">=3.12"`. Ensure the `[build-system]` is `hatchling` and the source layout points at `src/events`.
  - [ ] Edit `packages/events/src/events/__init__.py` to contain:
    ```python
    __version__ = "0.1.0"
    ```
  - [ ] Back at repo root, run `uv sync`; confirm `packages/events` appears in `uv.lock`.
- [ ] **Task 3: Scaffold `services/registry-api/`** (AC: #2)
  - [ ] Create `services/registry-api/` directory.
  - [ ] Inside, run `uv init --package --no-readme`.
  - [ ] Edit `services/registry-api/pyproject.toml`: set `name = "registry-api"`, `version = "0.1.0"`, `requires-python = ">=3.12"`. (No runtime deps yet — FastAPI comes in a later story when HTTP endpoints land.)
  - [ ] Edit `services/registry-api/src/registry_api/__init__.py`:
    ```python
    __version__ = "0.1.0"


    def hello() -> str:
        return "registry-api hello"
    ```
  - [ ] At repo root, `uv sync`; confirm `registry-api` appears in `uv.lock`.
- [ ] **Task 4: Verify cross-workspace import** (AC: #5)
  - [ ] At repo root, run `uv run python -c "from events import __version__; print(__version__)"`.
  - [ ] Confirm it prints `0.1.0`. If it fails with `ModuleNotFoundError`, verify (a) both `pyproject.toml` files use the `src/<name>/` layout, (b) workspace members pattern in root `pyproject.toml` matches actual paths.
- [ ] **Task 5: Pre-stage empty directories** (AC: #7)
  - [ ] Create `mcp-servers/`, `upstream/`, `tests/`, `docs/` at repo root.
  - [ ] Add a `.gitkeep` file inside each so git tracks the empty directories.
- [ ] **Task 6: Author top-level README.md** (AC: #6)
  - [ ] Create `README.md` at repo root with the five sections as specified in AC-6. Do not stub-and-move-on — each section must be coherent even as a "placeholder" (the stub content should orient a cold reader).
  - [ ] Cross-link from each section to the epic or story that will flesh it out (e.g., "Full deployment details land in Story 1.10a; quickstart here is a preview.").
- [ ] **Task 7: `.gitignore` and `LICENSE`** (AC: #8, #9)
  - [ ] Write `.gitignore` with the patterns enumerated in AC-8. Use Python-standard `.gitignore` as the base; add project-specific entries for `.env`, `/var/`, `.uv/`, `.ruff_cache/`, `.mypy_cache/`.
  - [ ] Add `LICENSE` file containing the canonical MIT License text with the exact copyright line `Copyright (c) 2026 R2d2`. (MIT is locked per AC-9; do not substitute a different license without an amendment PR against this story.)
- [ ] **Task 8: Minimal `justfile` with `bootstrap-verify`** (AC: #10)
  - [ ] Create `justfile` at repo root.
  - [ ] Add exactly one recipe initially:
    ```
    bootstrap-verify:
        uv sync
        uv run python -c "from events import __version__; print(__version__)"
        @echo "✓ bootstrap OK"
    ```
  - [ ] Run `just bootstrap-verify`; confirm exit 0 and expected output.
- [ ] **Task 9: Initialize git and commit atomically** (AC: #12, all)
  - [ ] Run `git init -b main` at repo root (`main` as default branch; do not use `master`).
  - [ ] Configure `git config user.name` and `git config user.email` locally if not inherited from global config.
  - [ ] Option A (single commit, preferred): `git add -A` + commit the entire scaffold with message `chore(scaffold): story 1.1 — monorepo proof · FR46 FR49 NFR-M7`.
  - [ ] Option B (two commits, acceptable): `git commit --allow-empty -m "chore: initial commit"` first, then `git add -A && git commit -m "chore(scaffold): story 1.1 — monorepo proof · FR46 FR49 NFR-M7"`.
  - [ ] Use `Co-Authored-By: Claude <noreply@anthropic.com>` footer if Claude Code is authoring the commit on the operator's behalf.
  - [ ] **Do NOT configure a remote or push in this story.** Remote + push are deferred until the operator chooses (any time between Story 1.1 completion and Story 1.9 which first materially needs the remote for GHCR publishing). Note this in the commit body or the story's Completion Notes.
- [ ] **Task 10: Verify every AC** (AC: all)
  - [ ] Walk through AC-1 through AC-10; for each, run the verification command or inspect the file and record pass/fail in the Completion Notes at the bottom of this story file.

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

_To be filled by the dev agent on implementation._ Target model: Claude (Opus 4.7 or Sonnet 4.6 acceptable for this scaffold-level story; Haiku 4.5 also sufficient — Story 1.1 is a boilerplate exercise, not a reasoning-heavy one).

### Debug Log References

_Placeholder for dev-session debug output._

### Completion Notes List

_To be filled by the dev agent. Record for each AC:_

- AC-1 pass/fail + evidence (file paths + key lines)
- AC-2 pass/fail + evidence
- AC-3 pass/fail + evidence
- AC-4 pass/fail + `uv sync` exit code + `uv sync --frozen` exit code
- AC-5 pass/fail + actual printed output
- AC-6 pass/fail + README section check
- AC-7 pass/fail + directory listing confirming pre-staging
- AC-8 pass/fail + `.gitignore` content check
- AC-9 pass/fail + `LICENSE` content check
- AC-10 pass/fail + `just bootstrap-verify` full output

Also record:

- Any deviations from the architecture (expected: none).
- Any commands or files that required judgment calls beyond what the AC specified (document the call + rationale).
- Any regression risk introduced for Stories 1.2–1.5 (expected: none; this story is a pure add).

### File List

_To be filled by the dev agent. Expected (10 new files + 4 `.gitkeep` markers):_

- `pyproject.toml` (root workspace)
- `uv.lock`
- `README.md`
- `.gitignore`
- `LICENSE`
- `justfile`
- `packages/events/pyproject.toml`
- `packages/events/src/events/__init__.py`
- `services/registry-api/pyproject.toml`
- `services/registry-api/src/registry_api/__init__.py`
- `docs/.gitkeep`
- `mcp-servers/.gitkeep`
- `tests/.gitkeep`
- `upstream/.gitkeep`

_Total: 14 new files, 0 modified, 0 deleted._

---

## Decisions locked for this story (previously "Questions for Operator")

All four open questions have been resolved and the resolutions are baked into the ACs and Tasks above:

1. **Git repo initialization timing → locked: Task 9 of this story.** Not a pre-story side-effect. AC-12 makes this the story's responsibility. `git init -b main` is the first sub-step of Task 9; the full scaffold lands in one (preferred) or two commits bearing the `chore(scaffold): story 1.1 — monorepo proof · FR46 FR49 NFR-M7` message.

2. **License choice → locked: MIT.** AC-9 locks MIT with `Copyright (c) 2026 R2d2`. Rationale: permissive, standard, compatible with upstream forks (OMC and clawhip are MIT-compatible), and consistent with the scratch-your-own-itch operator posture. Not re-openable without an amendment PR.

3. **GitHub remote timing → locked: deferred until Story 1.9 at latest.** AC-12 explicitly states no remote is configured in this story. Convention: the operator may push at any point between Story 1.1 completion and Story 1.9, but Story 1.9 is the first story that materially needs the remote. No convention-level constraint beyond that; operator picks the moment that best fits their workflow.

4. **`uv` version floor → locked: `>=0.5`.** AC-11 declares this in root `pyproject.toml` via `[tool.uv] required-version = ">=0.5"`, and Task 1's first sub-step verifies the running `uv` version before proceeding. README quickstart names the install command so cold-readers can't bypass the floor.

These four decisions are now part of Story 1.1's scope. The dev agent implementing the story treats them as locked architectural choices, not re-litigation points.
