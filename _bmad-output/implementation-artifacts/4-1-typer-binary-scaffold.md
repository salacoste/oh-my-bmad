# Story 4.1: Typer binary scaffold + entrypoint

Status: ready-for-dev

## Story

As **the operator**,
I want `services/console-cli/` packaged as a Typer-based CLI binary invokable via `docker compose exec console oh-my-bmad-cli`,
so that **every command I can run from Telegram has a local console counterpart**.

This is the foundation story for Epic 4. It creates the Typer app scaffold, wires the entry point, configures dependencies, and establishes the directory structure that Stories 4.2–4.6 will build on. No command logic is implemented — only stub commands that show help text.

## Acceptance Criteria

1. **AC-1: `--help` exits 0 with subcommand list** — Running `uv run python -m console_cli --help` (or `oh-my-bmad-cli --help` after install) exits 0 and prints a help menu listing all planned subcommands: `task`, `status`, `logs`, `approve`, `reject`, `stop`, `retry`, `ping`, `agent`, `events`.

2. **AC-2: Stub commands registered** — Each subcommand is a Typer `@app.command()` stub that prints "Not yet implemented" and exits 0. All 10 commands are registered.

3. **AC-3: `__main__.py` entry point works** — `python -m console_cli --help` works. The old hello-world/signal-pause scaffold is replaced with the Typer app invocation. Structlog is wired using the idempotent `_STRUCTLOG_CONFIGURED` sentinel pattern (same as telegram-gateway).

4. **AC-4: `pyproject.toml` dependencies declared** — `typer>=0.21.0`, `structlog`, `httpx`, workspace deps on `events` and `secret-hygiene`. `[project.scripts]` entry `oh-my-bmad-cli = "console_cli.__main__:app"` added. Version bumped to `0.2.0`.

5. **AC-5: Directory structure matches architecture** — The source tree follows the architecture spec:
   ```
   src/console_cli/
     __init__.py          # __version__ = "0.2.0"
     __main__.py          # structlog wiring + Typer app invocation
     app/
       __init__.py
       main.py            # Typer app factory, registers all sub-Typer groups
       config.py          # ConsoleSettings (pydantic-settings) with REGISTRY_API_BASE_URL
     adapters/
       __init__.py
       registry_api_client.py  # placeholder — httpx.AsyncClient factory
     commands/
       __init__.py
       task.py            # stub
       status.py          # stub
       logs.py            # stub
       approve.py         # stub
       reject.py          # stub
       stop.py            # stub
       retry.py           # stub
       ping.py            # stub
       agent.py           # stub
       events.py          # stub
   ```

6. **AC-6: Import-graph rules pass** — `scripts/check_imports.py` passes. Console-cli imports from `packages/` only (events, secret-hygiene), never from `services/telegram-gateway` or `services/registry-api`.

7. **AC-7: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict` on the new code.

8. **AC-8: Tests for scaffold** — At minimum: a test that imports the app, a test that `--help` exits 0, a test that each stub command runs without error.

9. **AC-9: `just test` no regressions** — Existing test count unchanged (1161 passed, 5 skipped, 14 deselected). New tests increase the count.

10. **AC-10: Atomic commit** — title: `feat(console-cli): add Typer binary scaffold and entrypoint · E4`

## Tasks / Subtasks

- [ ] **Task 1: Update `pyproject.toml` dependencies** (AC: #4)
  - [ ] Add `typer>=0.21.0` to dependencies
  - [ ] Add `structlog`, `httpx` to dependencies
  - [ ] Add workspace deps: `events`, `secret-hygiene` with `[tool.uv.sources]` entries
  - [ ] Add `[project.scripts]` entry: `oh-my-bmad-cli = "console_cli.__main__:app"`
  - [ ] Add `[dependency-groups] dev = ["pytest", "pytest-asyncio"]`
  - [ ] Add `[tool.uv.build-backend] source-exclude` for test files (same pattern as telegram-gateway)
  - [ ] Bump version to `0.2.0`
  - [ ] Run `uv sync --frozen --all-packages` to verify lock resolves

- [ ] **Task 2: Create directory structure** (AC: #5)
  - [ ] Create `src/console_cli/app/`, `src/console_cli/adapters/`, `src/console_cli/commands/` with `__init__.py` files
  - [ ] Create `src/console_cli/app/config.py` with `ConsoleSettings(BaseSettings)` — `REGISTRY_API_BASE_URL: str = "http://registry-api:8000"`
  - [ ] Create `src/console_cli/adapters/registry_api_client.py` with a placeholder `RegistryAPIClient` class (httpx.AsyncClient factory)

- [ ] **Task 3: Implement Typer app** (AC: #1, #2, #3)
  - [ ] Create `src/console_cli/app/main.py` — `app = typer.Typer(name="oh-my-bmad-cli", help="Operator CLI for oh-my-bmad platform")` with `no_args_is_help=True`
  - [ ] Create 10 stub command files in `src/console_cli/commands/`, each with `@app.command()` that prints "Not yet implemented — see Story 4.2/4.3/4.4"
  - [ ] Register all command modules in `main.py` by importing and calling `app.command()` on each
  - [ ] Rewrite `src/console_cli/__main__.py` — replace hello-world scaffold with structlog wiring + `app()` invocation
  - [ ] Update `src/console_cli/__init__.py` — bump `__version__` to `"0.2.0"`, update docstring

- [ ] **Task 4: Wire structlog** (AC: #3)
  - [ ] Implement `_configure_structlog()` in `__main__.py` using the idempotent `_STRUCTLOG_CONFIGURED` sentinel pattern
  - [ ] Use the same processor chain as telegram-gateway: `merge_contextvars`, `add_log_level`, `add_logger_name`, `ExtraAdder`, `TimeStamper(fmt="iso", utc=True)`, `redact_secrets` (from `secret_hygiene`), `JSONRenderer`
  - [ ] Bridge stdlib logging through `ProcessorFormatter` (same canonical pattern)

- [ ] **Task 5: Write tests** (AC: #8)
  - [ ] Create `src/console_cli/test_main.py` — test that importing the app succeeds
  - [ ] Test `--help` exits 0 via `typer.testing.CliRunner`
  - [ ] Test each stub command runs without error (parametrized)
  - [ ] Create `src/console_cli/test_config.py` — test `ConsoleSettings` defaults

- [ ] **Task 6: Verification + commit** (AC: #6, #7, #9, #10)
  - [ ] `uv run python -m console_cli --help` — exits 0 with subcommand list
  - [ ] `scripts/check_imports.py` — passes (no cross-service imports)
  - [ ] `just lint` 9/9 green
  - [ ] `just test` — no regressions, new tests counted
  - [ ] Atomic commit

## Dev Notes

### What already exists

The console-cli scaffold was created in Story 1.2 (monorepo proof) and Story 1.8 (Dockerfile). The following files already exist and need modification:

| File | Current state | What to change |
|---|---|---|
| `services/console-cli/pyproject.toml` | v0.1.0, empty deps | Add all deps, scripts entry, bump to v0.2.0 |
| `services/console-cli/src/console_cli/__init__.py` | v0.1.0 docstring | Bump version, update docstring |
| `services/console-cli/src/console_cli/__main__.py` | Hello-world signal.pause() | Replace with structlog + Typer app |
| `services/console-cli/Dockerfile` | Entrypoint `python -m console_cli` | No changes needed (already correct) |

### Typer framework notes

- **Version**: Use `typer>=0.21.0`. The project uses Python 3.12 so version pinning to 0.24.x is fine.
- **`add_typer` requires explicit `name=`** since Typer 0.14.0. Always pass the name parameter.
- **`no_args_is_help=True`** on the root Typer app so bare invocation shows help.
- **`typer.testing.CliRunner`** for testing — it invokes the app in-process without subprocess overhead.
- **Rich is included by default** in modern Typer. Help output is automatically formatted. No need to import Rich separately for basic usage.
- **The `[project.scripts]` entry** must point to a `typer.Typer()` instance, not a function: `oh-my-bmad-cli = "console_cli.__main__:app"`. Typer handles calling it via Click's `standalone_mode`.

### Import-graph rules (CRITICAL)

`scripts/check_imports.py` enforces these rules. Console-cli MUST:
- Import from `packages/` (events, secret-hygiene, idempotency) — ALLOWED
- Import from `console_cli` own modules — ALLOWED
- Import from `services/telegram-gateway/`, `services/registry-api/`, etc. — FORBIDDEN
- Communication with registry-api is HTTP-only via httpx, NOT via Python imports
- Domain logic reuse: console-cli must NOT import `telegram_gateway.domain.commands`. It reuses the same Pydantic models from `packages/events/` and replicates the HTTP client pattern locally.

The `domain/commands.py` directory from the architecture spec is intentionally NOT created in this story. The architecture says it should "1:1 mirror telegram_gateway/domain/commands.py" — but since cross-service imports are forbidden, the actual approach is:
- Shared Pydantic models live in `packages/events/` (moved there in Story 3.5.2)
- HTTP client calls go through `adapters/registry_api_client.py`
- Command handlers in `commands/` use the HTTP client + shared models
- This achieves "reuses that logic" via the adapters seam without violating import rules

### Structlog wiring pattern

Follow the EXACT same pattern as `services/telegram-gateway/src/telegram_gateway/__main__.py`:
1. `_STRUCTLOG_CONFIGURED: bool = False` module-level sentinel
2. `_configure_structlog()` function checks sentinel, configures if not yet done
3. Processor chain: `merge_contextvars → add_log_level → add_logger_name → ExtraAdder → TimeStamper → redact_secrets → JSONRenderer`
4. Bridge stdlib logging via `ProcessorFormatter` on root handler
5. Called from `main()` before any other work

### Config pattern

Follow `services/telegram-gateway/src/telegram_gateway/app/config.py`:
- `ConsoleSettings(AuditedBaseSettings)` from `secret_hygiene` (or plain `BaseSettings` if AuditedBaseSettings isn't accessible)
- `REGISTRY_API_BASE_URL: str = "http://registry-api:8000"` with `validation_alias="REGISTRY_API_BASE_URL"`
- The settings class is instantiated in `main()` and passed to the registry client

### Testing with Typer

```python
from typer.testing import CliRunner
from console_cli.app.main import app

runner = CliRunner()

def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "task" in result.output
```

### Previous story learnings (Epic 3.5)

- `just lint` 9/9 is the gatekeeper. Run early and often.
- `uv sync --frozen --all-packages` restores all workspace deps.
- `just test` = PR gate (`pytest -m "not slow"`). `testpaths` includes `services/`, so new tests in `services/console-cli/` WILL be discovered.
- The check_imports.py gate is strict — verify it passes before committing.
- Commit pattern: `feat(console-cli): ... · E4`

### File List

| File | Change |
|---|---|
| `services/console-cli/pyproject.toml` | Modified — deps, scripts, version bump |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `services/console-cli/src/console_cli/__main__.py` | Rewritten — structlog + Typer app |
| `services/console-cli/src/console_cli/app/__init__.py` | New |
| `services/console-cli/src/console_cli/app/main.py` | New — Typer app factory |
| `services/console-cli/src/console_cli/app/config.py` | New — ConsoleSettings |
| `services/console-cli/src/console_cli/adapters/__init__.py` | New |
| `services/console-cli/src/console_cli/adapters/registry_api_client.py` | New — placeholder |
| `services/console-cli/src/console_cli/commands/__init__.py` | New |
| `services/console-cli/src/console_cli/commands/{task,status,logs,approve,reject,stop,retry,ping,agent,events}.py` | New — 10 stub commands |
| `services/console-cli/src/console_cli/test_main.py` | New — scaffold tests |
| `services/console-cli/src/console_cli/test_config.py` | New — config tests |
| `_bmad-output/implementation-artifacts/4-1-typer-binary-scaffold.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1314-1331 — Story 4.1 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 659-669 — console-cli directory structure]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 213-214 — SSH-level auth]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 418 — print() allowed in console-cli]
- [Source: `services/telegram-gateway/src/telegram_gateway/__main__.py` — structlog wiring pattern]
- [Source: `services/telegram-gateway/src/telegram_gateway/app/config.py` — settings pattern]
- [Source: `services/telegram-gateway/src/telegram_gateway/handlers/registry_client.py` — HTTP client pattern]
- [Source: `services/console-cli/Dockerfile` — existing entrypoint]
- [Source: Typer docs — `add_typer` requires `name=` since 0.14.0]
- [Source: `docs/development.md` — uv sync variants]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
