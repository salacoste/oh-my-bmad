# Development guide

Practical notes for working in the oh-my-bmad monorepo. Covers tooling quirks,
environment setup, and common pitfalls discovered across Epics 1-3.

---

## `uv sync` variants

The project uses [uv](https://docs.astral.sh/uv/) for dependency management.
Multiple `uv sync` variants exist for different contexts:

| Command | What it does | When to use |
|---------|-------------|-------------|
| `uv sync --frozen --no-dev` | Production-only deps; no dev tools | Docker image builds (Dockerfile.base) |
| `uv sync --frozen --all-packages` | All workspace packages + dev deps | Day-to-day development, running tests |
| `uv sync --all-groups --all-packages` | Everything including optional groups | Full local setup |

### The `--no-dev` quirk

`uv sync --no-dev` strips dev-only dependencies from the virtual environment.
Several test dependencies (e.g. `asgi-lifespan`, `sniffio`) are in the
`[dependency-groups.dev]` section of the root `pyproject.toml`. Running
`uv sync --no-dev` removes them, causing `ImportError` in tests that import
these packages.

This was re-discovered in 10+ stories across Epics 1-3 before being documented
here (Epic 3 retrospective, Challenge #2).

**Fix:** Run `uv sync --frozen --all-packages` to restore all dependencies.

---

## Troubleshooting

### Tests fail with `ImportError` after `uv sync`

**Symptom:** `ModuleNotFoundError: No module named 'asgi_lifespan'` (or similar)
after running `uv sync --no-dev`.

**Cause:** Dev-only dependencies were stripped from the venv.

**Fix:**
```
uv sync --frozen --all-packages
```

### `just test` vs `just test-slow`

`just test` runs `pytest -m "not slow"` — the PR gate. This excludes
Docker-dependent slow tests (crash-injection, separability). Run
`just test-slow` for the full matrix. See [testing guide](testing-guide.md).

### Lint gates

`just lint` runs 9 checks: ruff check, ruff format, mypy (2 passes),
check_imports, check_event_registry, check_single_writer, check_no_subprocess,
and secret-hygiene. All 9 must pass before merge.

---

## Project structure

```
oh-my-bmad/
├── packages/           # Shared libraries (events, idempotency, secret-hygiene)
├── services/           # Microservices (registry-state, registry-api, telegram-gateway, worker-wrapper)
├── mcp-servers/        # MCP servers (clawhip-bridge)
├── src/                # Root package (oh-my-bmad)
├── tests/              # Cross-cutting integration tests
├── scripts/            # CI gate scripts
├── docs/               # Project documentation
├── justfile            # Task runner recipes
├── pyproject.toml      # Workspace root config
└── uv.lock             # Locked dependency tree
```

### Key conventions

- **Monorepo workspace:** All packages/services share a single `uv.lock`.
- **Test tree:** Co-located per Architecture, with cross-cutting tests in `tests/`.
- **Import rules:** Enforced by `scripts/check_imports.py`. Services may import
  from `packages/` but not from each other. See
  [architecture document](../_bmad-output/planning-artifacts/architecture.md).
- **Event schema registry:** All event type registrations live in
  `services/registry-state/src/registry_state/domain/event_types.py`.
- **Single writer:** Only `registry-state` writes SQLite. JSONL event log
  appending is allowed from external callers. Enforced by
  `scripts/check_single_writer.py`.
