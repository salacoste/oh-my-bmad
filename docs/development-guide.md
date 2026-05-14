# Development guide (entry point)

This file is the AI-context entry into the development workflow. **Tooling quirks and rediscovered gotchas live in [development.md](./development.md) — read that first if you've been bitten by `uv sync --no-dev` or `mypy_path`.**

## Five-minute setup

```sh
# Prereqs: Docker Engine ≥ 24 + Docker Compose v2.24+ + uv ≥ 0.5 + just ≥ 1.14
#   brew install uv just                                  # macOS
#   curl -LsSf https://astral.sh/uv/install.sh | sh       # Linux (uv)
git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
uv sync --frozen --all-packages                            # NOT --no-dev — see development.md
uv run pre-commit install
just bootstrap-verify                                      # 13 workspace imports must be green
cp .env.example .env
$EDITOR .env                                               # secrets + tunnel choice
just dev                                                   # macOS overlay; Linux base compose
docker compose ps                                          # expect 6/6 Up (healthy) within 60s
```

## Daily loop

| Command | Purpose |
|---|---|
| `just bootstrap-verify` | After every `uv sync` or pull. Verifies all 13 workspace imports. |
| `just test` | PR-gate suite (`pytest -m "not slow"`). The merge bar. |
| `just test-slow` | Full matrix (nightly only). |
| `just test-contract` | After every `just sync-upstream <name>`. |
| `just test-crash` | NFR-R2 crash-injection harness. |
| `just lint` | `ruff` check + format. |
| `just scan-secrets` | Pre-commit secret-pattern scan. |
| `just check-gates` | All quality gates locally before pushing. |
| `just dev` | Bring up the Compose stack (macOS overlay or Linux base). |
| `just migrate` | Apply pending Alembic migrations. |
| `just sync-upstream <name>` | **Only** sanctioned vendoring path. Pinned SHA → `VENDORED.md`. |
| `just backup [suffix]` | Snapshot the named volume. |
| `just build` / `just build-base` | Build service / base images. |

The `justfile` is the single source of truth for operator recipes. New recipes carry a header comment with purpose + the story that added them.

## Required reading before writing code

1. [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) — the AI-agent rule digest (Cats 1–7). Treat as injected context.
2. [development.md](./development.md) — `uv sync` variants, `mypy.ini` quirk, and other rediscovered tooling gotchas.
3. [testing-guide.md](./testing-guide.md) — test-tree layout, marker taxonomy, harness usage, contract-fixture recording.
4. [exceptions.md](./exceptions.md) — naming/convention exceptions (scaffold replacement map, MCP triple-naming rule).

## `uv` discipline (the short version)

- **CI:** `uv sync --frozen` (fails if lock is stale).
- **Local fresh install:** `uv sync` (or `--all-packages` for full workspace).
- **Local dep upgrade:** `uv lock --upgrade-package <pkg>` then `uv sync`.
- **`uv.lock` merge conflicts:** `git checkout --theirs uv.lock && uv sync --frozen`. Never hand-edit; never `--ours`.

`uv sync --no-dev` is for Docker image builds only — it strips test-only deps (`asgi-lifespan`, `sniffio`) and breaks tests. Re-discovered in 10+ stories across Epics 1–3 (see [development.md](./development.md)).

## Adding a new workspace member

1. Create the directory: `services/<svc>/`, `packages/<pkg>/`, or `mcp-servers/<srv>/`.
2. Add a `pyproject.toml` with `name = "<member>"` (for mcp-servers, the name is `<dir>-mcp`).
3. Create `src/<module>/__init__.py` (exports `__version__: str`), `__main__.py` (scaffold pattern if not implementing today), and `py.typed` marker.
4. Register in root `pyproject.toml`:
   - `[tool.uv.sources]` — `<member> = { workspace = true }`
   - `[project.dependencies]` — `<member>`
5. `uv lock` + `just bootstrap-verify`.
6. Update the relevant CI gate config (`scripts/checks/check_imports.py` etc.).

The MCP triple-naming rule (`<dir>` ↔ `<dir>-mcp` ↔ `<dir>_mcp`) is owned by `uv_build`. Never rename the import root by hand. See [exceptions.md](./exceptions.md).

## Adding a new event type

1. Add the `*Payload` class in `packages/events/src/events/` with `frozen=True, strict=True`.
2. Register the `(event_type, schema_version)` pair via `events.REGISTRY`.
3. Ship the migrator path additively — see [schema-evolution.md](./schema-evolution.md).
4. Add a forward-compatibility fixture under `tests/contract/fixtures/`.
5. Update the Telegram template if the event is user-visible — see [message-design.md](./message-design.md).
6. Add idempotency test under `tests/idempotency/` for any command that triggers the event.

## Adding a new HTTP endpoint

1. Place the route file at `services/registry-api/src/registry_api/v1/<resource>.py`.
2. Router owns `/v1`; route owns `/<resource>`. **Never double-prefix.**
3. Pydantic v2 models in/out with `model_config = ConfigDict(extra="forbid")`. Set `response_model_exclude_unset=True`.
4. POST creates return **201**, never 200.
5. `Depends(get_db)` returns `AsyncSession(expire_on_commit=False)`. Sync `Session` in async handlers blocks the loop.
6. Bind `trace_id` + `parent_event_id` to structlog context at the middleware layer, before business logic.
7. Mutating handlers emit exactly one typed `*.requested` event with `parent_event_id` set before returning 2xx.
8. Errors flow through the registered exception handler — never let an exception escape to FastAPI's default 500.
9. Add a `tests/integration/` test using `httpx.AsyncClient + asgi-lifespan` (NOT `TestClient`).

## Adding a new MCP tool

1. Pick the right server: `task-registry`, `session-registry`, or `clawhip-bridge` (the last is mutation-only — most additions go elsewhere).
2. Pydantic-validated input model + pydantic-modelled output. One source of truth — never duplicate the schema in both the tool registration and a separate Pydantic model.
3. Tool errors raise `ToolError(...)`. Never `raise ValueError`.
4. The capability-tier middleware runs at the boundary; the handler body is logic-only.
5. All I/O flows through injected clients — no direct DB access, no `anthropic` imports (this rule applies doubly for MCP).
6. Side-effect tools must be idempotent by design (MCP clients retry on timeout).
7. Add **three** mandatory tests at the boundary: deny-path, default-deny, escalation (see [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 4).
8. Record contract fixtures under `tests/contract/fixtures/<adapter>/`.

## Commit + PR discipline

- **Rebase**, not merge. `git pull --rebase` daily.
- `--force-with-lease` only. `--force` is banned.
- `--amend` is allowed pre-push on a personal branch; banned post-push.
- Imperative-mood commit subject (`add`, `fix`, `update`), ≤72 chars.
- Conventional-commit prefixes recommended (`feat:`, `fix:`, `chore:`, `refactor:`, `test:`, `docs:`, `ci:`).
- AI-assisted commits use the strict GitHub form: `Co-Authored-By: Name <email>` — capital A, capital B, single space after colon, angle brackets required.
- `[skip ci]` and `--no-verify` are banned.
- PRs require ≥1 human reviewer. Hard cap = 800 meaningful LOC excluding generated files, migrations, lock files, fixtures.
- See [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 6 for the full workflow ruleset.

## When something breaks

- **PR-gate red.** Run `just test` locally. If green locally and red on CI, check seed: failed runs reproduce via `pytest -p randomly --randomly-seed=<logged-seed>`.
- **`MissingGreenlet` after commit.** `AsyncSession(expire_on_commit=False)` is mandatory. See [development.md](./development.md).
- **`ImportError` after sync.** You likely ran `uv sync --no-dev`. Run `uv sync --frozen --all-packages` instead.
- **Mass-deletion PR rejected by CI.** Add an issue reference (`#NNNN`) to the commit body or use the `del:` prefix.
- **`uv.lock` conflict.** `git checkout --theirs uv.lock && uv sync --frozen`. Never `--ours`.
- **Anything else.** Follow the "When in doubt" rules in `_bmad-output/project-context.md` Cat 7 §"When in doubt". If unresolved, emit a structured `BLOCKED` event and surface to a human.

## Cross-references

- [development.md](./development.md) — tooling quirks (the file YOU read when stuck).
- [testing-guide.md](./testing-guide.md) — test harness usage.
- [exceptions.md](./exceptions.md) — naming exceptions + scaffold-replacement map.
- [operator-runbook.md](./operator-runbook.md) — operating the system (different audience).
- [deployment-guide.md](./deployment-guide.md) — deployment entry point.
- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) — full rule digest (Cats 1–7).
