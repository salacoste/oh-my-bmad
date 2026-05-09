# Story 4.6: Console wrapper / symlink (host-side `just cli` recipe)

Status: review

## Story

As the operator,
I want a `just cli <args...>` recipe that wraps `docker run --rm` with the console-cli image and a documented host-side alias,
so that I can type `bm task "..."` from my Mac terminal without typing the full docker command.

## Acceptance Criteria

1. **AC-1: `just cli` recipe** — Given the stack is running and the console-cli image is built, when I run `just cli task "do something"`, then it is equivalent to `docker run --rm --network <compose-network> oh-my-bmad-console-cli:local task "do something"` and the CLI output appears on the host terminal.

2. **AC-2: Shell alias documentation** — Given `docs/deployment/macos.md` has a new "Console CLI" section, when the operator follows the alias instructions, then they can type `bm task "..."` (or similar) for terse desk-side use.

3. **AC-3: Network connectivity** — The CLI container joins the compose project's default network so that `http://registry-api:8080` (the default `ConsoleSettings.registry_api_base_url`) resolves correctly. No config overrides or port exposures needed.

4. **AC-4: Image build dependency** — The `just cli` recipe depends on `build` (or at minimum the console-cli image existing). If the image is missing, the recipe prints a clear error message directing the operator to `just build`.

5. **AC-5: Passthrough exit codes** — The CLI container's exit code propagates to the host. `just cli status t-nonexistent` exits with code 4 on the host, just as `oh-my-bmad-cli` would inside the container.

6. **AC-6: No regressions** — `just lint` 9/9 green. `just test` unchanged. No existing recipes broken.

7. **AC-7: Atomic commit** — title: `feat(console-cli): add just cli wrapper and shell alias docs · E4`

## Tasks / Subtasks

- [x] **Task 1: Add `cli` recipe to justfile** (AC: #1, #3, #4, #5)
  - [x] Add a `cli *ARGS` recipe to the justfile after the existing `build` recipe
  - [x] Recipe body: `docker run --rm --network $(docker compose ls -q | head -1)_default oh-my-bmad-console-cli:local {{ARGS}}`
  - [x] Handle the case where no compose project is running — print "Error: stack not running. Run `just dev` first." to stderr and exit 1
  - [x] Verify exit code passthrough works (docker run --rm propagates exit codes)

- [x] **Task 2: Update `docs/deployment/macos.md`** (AC: #2)
  - [x] Add a new "## Console CLI" section after the "## Verify" section
  - [x] Document the `just cli` recipe with examples (`just cli task "build auth"`, `just cli status t-xxx`, `just cli events t-xxx --follow`)
  - [x] Document the shell alias: `alias bm='just cli'` for bash/zsh with installation instructions (`echo "alias bm='just cli'" >> ~/.zshrc`)
  - [x] Note that the default config (`http://registry-api:8080`) works because the CLI container joins the compose network

- [x] **Task 3: Verification + commit** (AC: #6, #7)
  - [x] `just lint` ruff + format green (mypy scope doesn't include console-cli per lint recipe)
  - [x] `just test` no regressions — 115 console-cli tests pass
  - [x] Verify `just cli --help` works when stack is running (manual or smoke test note)
  - [x] Version bump to `0.7.0` in `__init__.py` and `pyproject.toml`
  - [ ] Atomic commit

## Dev Notes

### What already exists

| File | Current state | What to change |
|---|---|---|
| `justfile` | Has 30+ recipes but NO `cli` recipe | Add `cli *ARGS` recipe |
| `services/console-cli/Dockerfile` | `ENTRYPOINT ["python", "-m", "console_cli"]` — ready for `docker run` | No change needed |
| `services/console-cli/pyproject.toml` | Entry point: `oh-my-bmad-cli = "console_cli.__main__:main"` | Version bump |
| `docs/deployment/macos.md` | No mention of console CLI or aliases | Add "Console CLI" section |
| `services/console-cli/src/console_cli/app/config.py` | `registry_api_base_url` defaults to `http://registry-api:8080` | No change needed — works inside docker network |

### Why `docker run --rm` instead of `docker compose exec`

The AC mentions `docker compose exec console` but there is no `console` service in `docker-compose.yml` (and shouldn't be — the CLI is ephemeral, not a long-running service). `docker run --rm` is the correct approach because:
- The console-cli Dockerfile already has the right `ENTRYPOINT`
- `docker run --rm` cleans up after each invocation
- No compose service needed — the CLI connects via `--network`
- This matches the Dockerfile comment: "invoked via `docker run --rm`"

### Network discovery

Docker Compose creates a network named `{project}_default` where `{project}` is the directory name (or `COMPOSE_PROJECT_NAME`). For this project it's typically `oh-my-bmad_default`. The recipe uses `docker compose ls -q | head -1` to dynamically discover the project name, then appends `_default`.

Alternative: use `docker compose run --rm` with an ephemeral service definition in compose — but that's heavier and requires compose changes.

### Exit code passthrough

`docker run --rm` propagates the container's exit code to the host. Since `render_http_error` raises `SystemExit(code)` and the `__main__.py` entrypoint lets it propagate, exit codes (0, 1, 2, 4, 5) pass through correctly.

### Just recipe pattern

```just
# Console CLI wrapper — runs oh-my-bmad-cli inside a ephemeral container
# on the compose network. Requires `just build` (for the image) and
# `just dev` (for the stack). Example: just cli task "build auth module"
cli *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    project=$(docker compose ls -q 2>/dev/null | head -1)
    if [ -z "${project}" ]; then
        echo "Error: stack not running. Run \`just dev\` first." >&2
        exit 1
    fi
    network="${project}_default"
    exec docker run --rm --network "${network}" oh-my-bmad-console-cli:local {{ARGS}}
```

### Shell alias documentation

```markdown
## Console CLI

oh-my-bmad ships a local CLI with full command-surface parity to the Telegram
bot. Run it via the `just cli` recipe:

    just cli task "build the auth module"
    just cli status t-0192a1b5-...
    just cli events t-0192a1b5-... --follow

For terse desk-side use, add a shell alias:

    echo "alias bm='just cli'" >> ~/.zshrc   # or ~/.bashrc
    source ~/.zshrc

    bm task "build auth module"
    bm status t-0192a1b5-...
    bm ping
```

### Key patterns from Stories 4.1–4.5

1. **Version bump** — both `__init__.py` and `pyproject.toml` get the same version
2. **Commit message format** — `feat(console-cli): <description> · E4`
3. **Lint gates** — `just lint` 9/9 is the gatekeeper
4. **No Python code changes** — this story is purely DevOps (justfile + docs)

### Import-graph rules

No Python code changes in this story — just justfile and documentation.

### File List

| File | Change |
|---|---|
| `justfile` | Modified — add `cli *ARGS` recipe |
| `docs/deployment/macos.md` | Modified — add "Console CLI" section |
| `services/console-cli/pyproject.toml` | Modified — version bump 0.7.0 |
| `services/console-cli/src/console_cli/__init__.py` | Modified — version bump |
| `_bmad-output/implementation-artifacts/4-6-console-wrapper-symlink.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip |

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines ~1402-1419 — Story 4.6 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 658-669 — console-cli directory structure]
- [Source: `services/console-cli/Dockerfile` — ENTRYPOINT and docker run comment]
- [Source: `docs/deployment/macos.md` — existing deployment docs to extend]
- [Source: `justfile` — existing recipe patterns and `build` recipe]
- [Source: `_bmad-output/implementation-artifacts/4-5-error-rendering-cli.md` — previous story patterns]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

N/A

### Completion Notes List

1. **Task 1** — Added `cli *ARGS` recipe to justfile (lines 262–275). Uses `#!/usr/bin/env bash` shebang recipe for proper error handling. Discovers compose project via `docker compose ls -q | grep -F "$(basename "$PWD")" | head -1` (filters to this project only). Network name uses `_oh-my-bmad-net` suffix matching docker-compose.yml's named network. Includes image existence guard (`docker image inspect`) with clear error directing to `just build`. SIGPIPE-safe via `|| true`. Uses `exec` to hand off to docker run, preserving exit code passthrough.
2. **Task 2** — Added "## Console CLI" section to `docs/deployment/macos.md` (lines 199–268). Includes: usage examples for all CLI commands, shell alias instructions (`alias bm='just cli'`) for bash/zsh with reload steps, and exit code table (0/1/2/4/5) for scripting.
3. **Task 3** — Version bumped to 0.7.0 in `pyproject.toml` and `__init__.py`. Ruff check + format pass. 115 console-cli tests pass. Pre-existing mypy errors in test files (dict variance, type-arg) from Stories 4.3-4.5 are outside lint recipe scope.
4. **Code review fixes** — 4 findings from 3-layer review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Fixed: (a) wrong network name `_default` → `_oh-my-bmad-net`, (b) added image existence guard for AC-4, (c) multi-project filter via `grep -F "$(basename "$PWD")"`, (d) SIGPIPE suppression with `|| true`.

### Change Summary

| File | Change |
|---|---|
| `justfile` | Added `cli *ARGS` recipe (lines 262–271) |
| `docs/deployment/macos.md` | Added "## Console CLI" section (lines 199–268) |
| `services/console-cli/pyproject.toml` | Version bump 0.6.0 → 0.7.0 |
| `services/console-cli/src/console_cli/__init__.py` | Version bump 0.6.0 → 0.7.0 |

### File List
