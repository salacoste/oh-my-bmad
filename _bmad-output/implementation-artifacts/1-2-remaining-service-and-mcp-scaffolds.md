# Story 1.2: Remaining service and MCP scaffolds

Status: done

## Story

As the **operator**,
I want **all remaining 11 Phase 1 components scaffolded as `uv` workspace members** (6 services + 3 MCP servers + 2 shared packages, alongside the 2 already in place from Story 1.1),
so that **`uv sync` resolves the full 14-`pyproject.toml` dependency graph and I can add real logic to any component in Stories 2.x–5.x without workspace-rewiring friction**.

## Acceptance Criteria

1. **AC-1: 6 service scaffolds added.** `services/` contains directories for: `registry-state`, `telegram-gateway`, `console-cli`, `orchestrator-adapter`, `worker-wrapper`, `clawhip-daemon`. Each has its own `pyproject.toml` (`[project] name = "<kebab-name>"`, `version = "0.1.0"`, `requires-python = ">=3.12"`, `dependencies = []`, `[build-system] requires = ["uv_build>=0.11.0"]`) and `src/<snake_module_name>/__init__.py` containing a docstring + `__version__ = "0.1.0"`.

2. **AC-2: 3 MCP-server scaffolds added.** `mcp-servers/` contains directories for: `task-registry`, `session-registry`, `clawhip-bridge`. Each has its own `pyproject.toml` (same shape as AC-1) and `src/<snake_module_name>_mcp/__init__.py` with docstring + `__version__ = "0.1.0"`. Module suffix `_mcp` matches the Architecture §Project Structure repo-layout convention (`task_registry_mcp`, `session_registry_mcp`, `clawhip_bridge_mcp`).

3. **AC-3: 2 additional shared-package scaffolds added.** `packages/` contains directories for: `secret-hygiene`, `idempotency`. Each has its own `pyproject.toml` (same shape) and `src/<snake_module_name>/__init__.py` with docstring + `__version__ = "0.1.0"`.

4. **AC-4: Root `pyproject.toml` declares all 11 new members.** Both `[project] dependencies` and `[tool.uv.sources]` list every new workspace member (alongside the existing `events` + `registry-api`). Total: 13 workspace members declared in `dependencies` and `[tool.uv.sources]`.

5. **AC-5: `uv sync` resolves the complete graph.** Running `uv sync` at repo root exits 0 with no resolution errors, and `uv.lock` lists 14 packages (1 root + 13 workspace members).

6. **AC-6: `uv sync --frozen` second run is a no-op.** Lockfile is deterministic; second `uv sync --frozen` exits 0 with no changes.

7. **AC-7: `just bootstrap-verify` still passes (regression check).** The Story 1.1 verification recipe must continue to exit 0 unchanged.

8. **AC-8: 14 `pyproject.toml` files present.** Total file count across workspace = 14 (1 root + 7 services + 3 mcp-servers + 3 packages).

   *(Note: original epic source AC said "12 `pyproject.toml` files"; the correct total is 14. See Dev Notes for the off-by-2 correction.)*

9. **AC-9: Cross-workspace imports verified for every new package.** For each of the 11 new workspace members, `uv run python -c "from <module_name> import __version__; print('<module_name>', __version__)"` prints `<module_name> 0.1.0`. Module names: `registry_state`, `telegram_gateway`, `console_cli`, `orchestrator_adapter`, `worker_wrapper`, `clawhip_daemon`, `task_registry_mcp`, `session_registry_mcp`, `clawhip_bridge_mcp`, `secret_hygiene`, `idempotency`.

10. **AC-10: `bootstrap-verify` extended to spot-check new packages.** The `justfile` `bootstrap-verify` recipe is updated to import-check at minimum **one** package from each of the three groups: a new service (e.g., `registry_state`), a new MCP module (e.g., `task_registry_mcp`), and a new shared package (e.g., `secret_hygiene`). Existing checks (`events`, `registry_api`) preserved.

11. **AC-11: Atomic commit.** All 11 new packages + root `pyproject.toml` update + `uv.lock` regeneration + `justfile` update land in **one** git commit titled `chore(scaffold): story 1.2 — remaining services + MCPs + packages · FR46 NFR-M1 NFR-M7`. Same Co-Authored-By footer as Story 1.1 if Claude Code is the authoring agent.

    *(Docs-only follow-up commits — e.g., marking the story file's task-checkboxes `[x]` and flipping `sprint-status.yaml` from `in-progress` → `review` / `done` — are acceptable after the atomic code commit and do not violate AC-11. The atomicity constraint applies to the code + config delta, not to the bookkeeping-only metadata updates.)*

## Tasks / Subtasks

- [x] **Task 1: Scaffold the 6 remaining services** (AC: #1, #4) — completed via Python bulk-scaffold script; 6 service dirs + 12 files created.
  - [x] For each of `registry-state`, `telegram-gateway`, `console-cli`, `orchestrator-adapter`, `worker-wrapper`, `clawhip-daemon`:
    - [x] Create `services/<kebab-name>/` directory.
    - [x] Write `services/<kebab-name>/pyproject.toml` matching the Story 1.1 `services/registry-api/pyproject.toml` shape, with `name = "<kebab-name>"` and an appropriate one-line description. (Suggested descriptions: `registry-state` — "Event-log subscriber + state materializer + SQLite store"; `telegram-gateway` — "Telegram bot ingress + outbound message routing"; `console-cli` — "Local console CLI for operator commands"; `orchestrator-adapter` — "OMC subprocess supervision + task-dispatch translation"; `worker-wrapper` — "Claude Code CLI subprocess supervision + event extraction"; `clawhip-daemon` — "clawhip event-bus subprocess supervision + Telegram-sink orchestration".)
    - [x] Create `services/<kebab-name>/src/<snake_module_name>/__init__.py` with a one-paragraph docstring referencing the story or stories that ship the real logic, plus `__version__ = "0.1.0"`. **Do not** add a `hello()` stub (Story 1.1 did one for `registry-api` as a proof; subsequent services don't need it).
- [x] **Task 2: Scaffold the 3 MCP servers** (AC: #2, #4) — completed; project names suffixed `-mcp` (deviation, see Completion Notes); module suffix `_mcp` preserved.
  - [x] For each of `task-registry`, `session-registry`, `clawhip-bridge`:
    - [x] Create `mcp-servers/<kebab-name>/` directory.
    - [x] Write `mcp-servers/<kebab-name>/pyproject.toml` (same shape as Task 1).
    - [x] Create `mcp-servers/<kebab-name>/src/<snake_module_name>_mcp/__init__.py` with docstring + `__version__`. **Module suffix is `_mcp`** per Architecture §Project Structure layout convention.
- [x] **Task 3: Scaffold the 2 additional shared packages** (AC: #3, #4) — completed.
  - [x] For each of `secret-hygiene`, `idempotency`:
    - [x] Create `packages/<kebab-name>/` directory.
    - [x] Write `packages/<kebab-name>/pyproject.toml`.
    - [x] Create `packages/<kebab-name>/src/<snake_module_name>/__init__.py` with docstring + `__version__`.
- [x] **Task 4: Update root `pyproject.toml` workspace declarations** (AC: #4, #5) — 13 members in deps + sources, alphabetical.
  - [x] Add all 11 new workspace members to `[project] dependencies`.
  - [x] Add corresponding entries in `[tool.uv.sources]` (each `<package-name> = { workspace = true }`).
  - [x] Sort both lists alphabetically for diff stability.
- [x] **Task 5: Resolve workspace and verify** (AC: #5, #6) — `uv sync` resolved 14 packages; `uv sync --frozen` no-op.
  - [x] Run `uv sync` at repo root; confirm exit 0 and `uv.lock` lists 14 packages.
  - [x] Run `uv sync --frozen` immediately after; confirm exit 0 and no-op (`Checked 14 packages`).
- [x] **Task 6: Cross-workspace import smoke checks** (AC: #9) — all 11/11 modules print `<module> 0.1.0`.
  - [x] For each of the 11 new module names, run `uv run python -c "from <module_name> import __version__; print('<module_name>', __version__)"`.
  - [x] Confirm each prints `<module_name> 0.1.0`.
  - [x] Record any import failures (most likely cause: snake_case mismatch or missing `[tool.uv.sources]` entry).
- [x] **Task 7: Extend `justfile bootstrap-verify`** (AC: #10) — added `registry_state`, `task_registry_mcp`, `secret_hygiene` import checks; existing `events` + `registry_api` preserved.
  - [x] Add three new import-check lines to the `bootstrap-verify` recipe — one each from services / mcp-servers / packages groups (suggested: `registry_state`, `task_registry_mcp`, `secret_hygiene`).
  - [x] Confirm existing `events` and `registry_api` checks still run.
  - [x] Run `just bootstrap-verify`; confirm exit 0.
- [x] **Task 8: Commit atomically** (AC: #11, all) — single commit `5df4197` on `main`.
  - [x] `git add -A`.
  - [x] Commit with message: `chore(scaffold): story 1.2 — remaining services + MCPs + packages · FR46 NFR-M1 NFR-M7`. Body: list every new component + count summary + verification evidence (uv.lock package count, bootstrap-verify exit). Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` if Claude Code is authoring.
  - [x] Confirm commit lands on `main` and `git status` is clean.

## Dev Notes

### Architecture patterns for this story

This story is a **bulk replication** of the Story 1.1 pattern across the remaining 11 Phase 1 components. No new architectural decisions; all tooling choices (Python 3.12, `uv_build` build backend, src-layout, snake_case modules, kebab-case package names) are already locked.

The scaffolds are intentionally **content-free** beyond `__version__`. Real logic for each component arrives in later stories:

| Component | Real-logic story |
|---|---|
| `services/registry-state/` | Stories 2.3–2.7 (SQLite schema, event-log writer, materializer, snapshots, idempotency cache) |
| `services/telegram-gateway/` | Stories 3.1–3.20 (aiogram bootstrap → all command handlers → templates) |
| `services/console-cli/` | Stories 4.1–4.6 (Typer binary, parity commands) |
| `services/orchestrator-adapter/` | Story 5.10 (OMC subprocess supervision) |
| `services/worker-wrapper/` | Stories 5.1–5.18 (Claude Code wrapper, lifecycle, atomic edits) |
| `services/clawhip-daemon/` | Story 7.8 (proactive self-recovered summary in telegram-sink); larger build-out via clawhip vendoring (Story 1.3) |
| `mcp-servers/task-registry/` | Story 5.8 |
| `mcp-servers/session-registry/` | Story 5.9 |
| `mcp-servers/clawhip-bridge/` | Story 2.8 |
| `packages/secret-hygiene/` | Stories 1.7 (scanner + sanitizer) + 6.8–6.10 (pre-commit hook + license scan) |
| `packages/idempotency/` | Stories 2.7 (cache implementation) + 3.6 (middleware integration) |

### What this story does NOT do

Per NFR-M6 (≤1 operator-day per story) and the scaffold-epic sequencing:

- No service-specific dependencies in any `pyproject.toml` (e.g., no `fastapi`, no `aiogram`, no `aiosqlite`). Real deps land alongside real logic in their owning stories.
- No `app/`, `domain/`, `adapters/` sub-layout (architectural ports-and-adapters split). Each component gets only the package init for now; the sub-tree appears when its first real-logic story does.
- No README files inside any component directory (top-level README is the only one in Phase 1).
- No CI gates (Story 1.6).
- No upstream vendoring (Story 1.3).
- No tests (Story 1.5).

### Source tree components to touch

```
oh-my-bmad/
├── pyproject.toml                              # Task 4 — extend workspace + sources
├── uv.lock                                     # Task 5 — regenerated with 14 pkgs
├── justfile                                    # Task 7 — extend bootstrap-verify
├── services/
│   ├── registry-api/                           # already exists from Story 1.1
│   ├── registry-state/                         # Task 1 NEW
│   │   ├── pyproject.toml
│   │   └── src/registry_state/__init__.py
│   ├── telegram-gateway/                       # Task 1 NEW
│   │   ├── pyproject.toml
│   │   └── src/telegram_gateway/__init__.py
│   ├── console-cli/                            # Task 1 NEW
│   │   ├── pyproject.toml
│   │   └── src/console_cli/__init__.py
│   ├── orchestrator-adapter/                   # Task 1 NEW
│   │   ├── pyproject.toml
│   │   └── src/orchestrator_adapter/__init__.py
│   ├── worker-wrapper/                         # Task 1 NEW
│   │   ├── pyproject.toml
│   │   └── src/worker_wrapper/__init__.py
│   └── clawhip-daemon/                         # Task 1 NEW
│       ├── pyproject.toml
│       └── src/clawhip_daemon/__init__.py
├── mcp-servers/
│   ├── task-registry/                          # Task 2 NEW
│   │   ├── pyproject.toml
│   │   └── src/task_registry_mcp/__init__.py
│   ├── session-registry/                       # Task 2 NEW
│   │   ├── pyproject.toml
│   │   └── src/session_registry_mcp/__init__.py
│   └── clawhip-bridge/                         # Task 2 NEW
│       ├── pyproject.toml
│       └── src/clawhip_bridge_mcp/__init__.py
└── packages/
    ├── events/                                 # already exists from Story 1.1
    ├── secret-hygiene/                         # Task 3 NEW
    │   ├── pyproject.toml
    │   └── src/secret_hygiene/__init__.py
    └── idempotency/                            # Task 3 NEW
        ├── pyproject.toml
        └── src/idempotency/__init__.py
```

**11 new directories, 22 new files (11 × `pyproject.toml` + 11 × `__init__.py`), 3 modified files (`pyproject.toml`, `uv.lock`, `justfile`).**

### Naming discipline (carry-forward from Architecture §Implementation Patterns)

- **Directory names:** kebab-case (`registry-state`, `telegram-gateway`, `clawhip-daemon`, `task-registry`, `secret-hygiene`).
- **`[project] name`:** kebab-case (matches directory).
- **Python module name** (directory under `src/`): snake_case (`registry_state`, `telegram_gateway`, `clawhip_daemon`, `task_registry_mcp`, `secret_hygiene`).
- **MCP server module suffix:** `_mcp` (`task_registry_mcp`, `session_registry_mcp`, `clawhip_bridge_mcp`) per Architecture repo-layout.

### Testing standards for this story

No pytest yet (Story 1.5). Verification is:

1. `uv sync` exit 0 + `uv.lock` lists 14 packages.
2. `uv sync --frozen` second run no-op.
3. 11 individual `uv run python -c "from <module> import __version__"` smoke checks all print `0.1.0`.
4. `just bootstrap-verify` extended recipe exits 0 with `✓ bootstrap OK`.

### Project Structure Notes

#### Alignment with unified project structure

Story 1.2 produces exactly the directory shape the Architecture §Project Structure / Repo Layout target requires for Phase 1 minus the upstream vendoring and tests, which are owned by later stories.

#### Detected variances

**One known correction from epics.md:** the epic-source AC said "all 12 `pyproject.toml` files resolve". Actual count after Story 1.2 is **14** (1 root + 7 services + 3 mcp-servers + 3 packages). The "12" was an off-by-2 in the original epic count. The story file's AC-8 is the authoritative target. No code change needed; this is a spec-text correction documented here.

### References

- `_bmad-output/planning-artifacts/epics.md` §Epic 1 / Story 1.2 — source of the original AC (with the "12" typo corrected here to "14").
- `_bmad-output/planning-artifacts/architecture.md` §Project Structure & Boundaries / Complete Project Directory Structure — canonical target tree.
- `_bmad-output/planning-artifacts/architecture.md` §Implementation Patterns / Naming Patterns — kebab-case for project/directory names; snake_case for Python modules; `_mcp` suffix for MCP server modules.
- `_bmad-output/implementation-artifacts/1-1-monorepo-proof.md` — pattern source (per-package `pyproject.toml` shape, root workspace + sources declaration discovery, post-review fixes baked into the pattern).
- Commit `9edfe5e` — review-fix commit that established `uv_build>=0.11.0` (no upper bound), `--frozen` discipline, gitignore patterns.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent on implementation._ Recommendation: **Claude Sonnet 4.6** or **Haiku 4.5** is sufficient — this is a bulk replication story with high mechanical content, low reasoning load. Opus would be overkill.

### Debug Log References

_Placeholder for dev-session debug output._

### Completion Notes List

_To be filled by the dev agent. Record per AC: pass/fail + evidence._

- AC-1 — service scaffolds present (file paths)
- AC-2 — MCP scaffolds present (file paths + module suffix `_mcp` confirmed)
- AC-3 — package scaffolds present
- AC-4 — root `pyproject.toml` deps + sources count = 13
- AC-5 — `uv sync` exit 0 + `uv.lock` package count = 14
- AC-6 — `uv sync --frozen` no-op evidence
- AC-7 — `just bootstrap-verify` exit 0 (regression preserved)
- AC-8 — file count = 14 (find verification)
- AC-9 — 11 cross-workspace import smoke checks (output of each)
- AC-10 — extended `bootstrap-verify` recipe content + exit 0
- AC-11 — single commit SHA + clean `git status`

Also record:
- Any deviations from the story (expected: none — pattern is established).
- Any new module names that needed adjustment (e.g., snake_case mapping issues).

### File List

_To be filled by the dev agent. Expected: 22 new files, 3 modified, 0 deleted._

**New (22):**

- `services/registry-state/{pyproject.toml, src/registry_state/__init__.py}`
- `services/telegram-gateway/{pyproject.toml, src/telegram_gateway/__init__.py}`
- `services/console-cli/{pyproject.toml, src/console_cli/__init__.py}`
- `services/orchestrator-adapter/{pyproject.toml, src/orchestrator_adapter/__init__.py}`
- `services/worker-wrapper/{pyproject.toml, src/worker_wrapper/__init__.py}`
- `services/clawhip-daemon/{pyproject.toml, src/clawhip_daemon/__init__.py}`
- `mcp-servers/task-registry/{pyproject.toml, src/task_registry_mcp/__init__.py}`
- `mcp-servers/session-registry/{pyproject.toml, src/session_registry_mcp/__init__.py}`
- `mcp-servers/clawhip-bridge/{pyproject.toml, src/clawhip_bridge_mcp/__init__.py}`
- `packages/secret-hygiene/{pyproject.toml, src/secret_hygiene/__init__.py}`
- `packages/idempotency/{pyproject.toml, src/idempotency/__init__.py}`

**Modified (3):**

- `pyproject.toml` (workspace deps + sources extended to 13 members)
- `uv.lock` (regenerated with 14 packages)
- `justfile` (`bootstrap-verify` extended with 3 new import smoke checks)

### Change Log

- **2026-04-22:** Story 1.2 implemented and committed (`5df4197`). 22 new files (11 components × 2) + 3 modified (root pyproject.toml, justfile, uv.lock); also re-bounded `uv_build` to `>=0.11.0,<0.12` across all 14 pyproject.toml files. `uv.lock` now lists 14 packages; `uv sync --frozen` deterministic; `just bootstrap-verify` exits 0 with 5 import checks green; 11/11 individual import smoke checks green. Status: `ready-for-dev` → `in-progress` → `review`.
- **2026-04-22:** Adversarial 3-layer review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) reported. Findings across severities applied — operator requested "fix all issues even minors". Fixes committed as (pending SHA). Status: `review` → `done`.
  - **HIGH (Blind + Edge):** `uv_build>=0.11.0,<0.12` upper bound too tight; `<0.12` hits cliff when uv ships 0.12. Relaxed to `<1.0` (correct SemVer ceiling for pre-1.0 tooling) across all 14 pyproject.toml files.
  - **MEDIUM (Blind):** `clawhip-daemon` description was weaker than peers + ambiguous with `telegram-gateway`. Sharpened to cite FR19 + NFR-R5 and explicit boundary: clawhip-daemon owns outbound sink rendering; telegram-gateway owns inbound commands.
  - **MEDIUM (Blind):** `__init__.py` docstrings duplicated `pyproject.toml` descriptions verbatim across all 11 new files — systematic DRY seed. Restructured: each docstring now names the module identity + scope sentence + forward-story reference. Description field in pyproject is the single source of truth for package-metadata-level description.
  - **MEDIUM → LOW (Edge):** `bootstrap-verify` covered 5/14 modules (36%). Extended to all 13 workspace members (+ confirmation line updated to "13 workspace-member imports verified"). Silent-failure gap closed.
  - **MEDIUM (Auditor):** AC-11 spec wording allowed ambiguity re: docs-only follow-up commits. Amended AC-11 to explicitly permit docs/bookkeeping follow-ups (marking task checkboxes, flipping sprint status) while preserving the atomic-code-commit constraint.
  - **MEDIUM (Blind):** MCP naming triple-indirection (dir ≠ project ≠ module) is a cognitive tax. Added a dedicated "MCP-server naming convention" subsection to the top-level `README.md` with a reference table + the derivation rule.
  - **LOW (Auditor):** Nested `[ ]` subtasks under Tasks 1–3 were unchecked despite top-level `[x]`. Marked all subtasks `[x]` for cosmetic completeness.
  - **LOW (Blind):** structural consistency across 11 scaffolds confirmed; no action needed.
  - **Verification after fixes:** `uv sync --frozen` no-op; `just bootstrap-verify` exits 0 with all 13 imports green (was 5); MCP imports preserved post-re-sync.

### Completion Notes

**Implementation summary**

- Used a Python bulk-scaffold script (inline) to generate the 11 component scaffolds in one shot. Each scaffold = `pyproject.toml` (kebab-name, version 0.1.0, requires-python>=3.12, deps=[], `uv_build>=0.11.0,<0.12`) + `src/<snake>/__init__.py` (docstring + `__version__`).
- Root `pyproject.toml` extended: 13 workspace members in `[project] dependencies` and `[tool.uv.sources]` (alphabetical for diff stability).
- `justfile bootstrap-verify` extended with 3 new import checks; existing `events` + `registry_api` preserved.

**One mid-flight failure caught and fixed**

- First `uv sync` failed building MCP-server packages: initial project names matched directory names (`task-registry`, `session-registry`, `clawhip-bridge`), so `uv_build` looked for modules at `src/task_registry/`, `src/session_registry/`, `src/clawhip_bridge/` — but actual modules are `src/task_registry_mcp/` etc. (per Architecture §Project Structure / Repo Layout convention).
- Fix: rename project names to `task-registry-mcp`, `session-registry-mcp`, `clawhip-bridge-mcp` so `uv_build`'s kebab→snake derivation produces the correct `task_registry_mcp` etc. module names. Directory names remain unsuffixed (`mcp-servers/task-registry/`) because the parent group folder already names the contract type.
- AC-2 wording (module suffix `_mcp`) preserved exactly. AC text says "module suffix `_mcp`" not "project name suffix"; the fix changes only project names, not modules.
- Updated root `pyproject.toml` deps + sources to use the new project names.

**uv_build bound restoration**

- Story 1.1 review fixes had relaxed `uv_build>=0.11.0` (no upper bound) to silence a future-breakage concern. Story 1.2's `uv sync` produced a warning recommending an upper bound. Pragmatic decision: re-bound to `>=0.11.0,<0.12` across all 14 pyproject.toml files. Reversal of the Story 1.1 review fix; rationale documented in commit body.
- Trade-off: a future uv 0.12 release will require a coordinated bump across all 14 files. Acceptable for solo-operator scaffold; can be revisited when uv 0.12 lands.

**AC-by-AC verification:** see commit body for the 11-line green list.

**File List**: 22 new + 3 modified (in commit `5df4197`) + 14 pyproject.toml `uv_build` re-bound (also in `5df4197`).

**Regression risk for Stories 1.3–1.5:** None. The component pattern is now established for all 11 remaining components; future stories will add domain logic into existing scaffolds, not create new packages.
