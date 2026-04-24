# Exceptions

Documented naming-rule and convention exceptions. This file exists so the
`exceptions.md` grep-check in Architecture §Pattern Completeness remains
reviewable. Every entry explains why the exception exists and which story
introduced it.

---

## MCP-server triple-naming

MCP servers use three distinct names: directory, project name, and Python module.
This is intentional, not a typo.

| Directory (`mcp-servers/`) | Project (`pyproject.toml` `name`) | Python module |
|----------------------------|------------------------------------|---------------|
| `task-registry/` | `task-registry-mcp` | `task_registry_mcp` |
| `session-registry/` | `session-registry-mcp` | `session_registry_mcp` |
| `clawhip-bridge/` | `clawhip-bridge-mcp` | `clawhip_bridge_mcp` |

**Rationale (Story 1.2 ADR):** `uv build` derives the importable module name
from the project `name` field by replacing `-` with `_`. The `-mcp` suffix is
appended to the project name (not the directory name) so that the Python module
name is unambiguous at import time — `import task_registry_mcp` cannot be
confused with a service or package named `task_registry`. The directory omits
the `-mcp` suffix because the parent `mcp-servers/` folder already establishes
the contract type.

Derivation rule: `<dir>` → project = `<dir>-mcp` → module = `<dir>_mcp`
(with `-` → `_`).

Services and packages follow the simpler 1:1 kebab-to-snake convention
(e.g., `secret-hygiene` ↔ `secret_hygiene`).

---

## Scaffold-version tags (historical)

Stories 1.4 through 1.8 used `# SCAFFOLD VERSION — Story X replaces ...`
header comments on transient code (Dockerfiles, entrypoints) so reviewers
knew the shape was deliberately incomplete. Story 1.8 removed the last of
these tags when it landed the real multi-stage Dockerfile.base. **If you
encounter this tag in the repo today, it's stale — file a follow-up to
retire it.** The convention itself is deprecated; future stories should
reference their replacement story in commit messages rather than in-code
scaffold tags.

---

## Scaffold `__main__.py` files

Story 1.4 established a hello-world pattern for every service: touch
`/tmp/ready` (healthcheck probe), install a SIGTERM handler, then call
`signal.pause()` to keep the container alive until real logic ships.

The table below maps each service to the story that replaces its scaffold:

| Service | Replacement story |
|---------|------------------|
| `registry-api` | Story 2.9 (HTTP API + `/v1/health`) |
| `registry-state` | Stories 2.3–2.4 (SQLite schema + WAL writer) |
| `telegram-gateway` | Story 3.1 (aiogram webhook receiver) |
| `orchestrator-adapter` | Story 5.10 (OMC subprocess supervision) |
| `worker-wrapper` | Story 5.1 (worker lifecycle management) |
| `clawhip-daemon` | Story 2.8 (clawhip-bridge MCP integration) |

Until a story ships its replacement, the scaffold `__main__.py` is the
correct and expected code. Do not add business logic on top of `signal.pause()`
— wait for the owning story's implementation.

---

## Suppression tags

Story 1.6 registered three custom noqa codes for the architectural-discipline
check scripts:

| Tag | Check script | When to use |
|-----|-------------|-------------|
| `IMP001` | `check_imports.py` | A cross-layer import that is permitted by explicit ADR (e.g., a test helper importing a service internals for fixture setup). |
| `EVT001` | `check_event_registry.py` | A `type=` argument that is not a string literal — for example, when the event type is computed from a variable. The reason must name the variable and confirm it is always a registered type at runtime. |
| `SW001` | `check_single_writer.py` | A write path that deliberately bypasses the single-writer constraint (e.g., a migrator or a test fixture writer). |

Usage (inline, end of line):

```python
emit_event(type=event_type_var, payload=payload)  # noqa: EVT001 event_type_var always in REGISTRY
```

The `# noqa: EVT001` form suppresses only the EVT001 check. The reason string
(everything after the code) is mandatory — the check scripts reject bare
`# noqa: EVT001` without a reason.

---

## `.secret-hygiene-ignore` vs `.pre-commit-config.yaml` exclude

The secret-hygiene scanner uses two independent exclusion mechanisms, and both
are necessary.

- **`.pre-commit-config.yaml` `exclude:` regex** — applies when pre-commit
  invokes `secret-hygiene-precommit` as a hook. Files matching the regex are
  not passed to the hook at all.

- **`.secret-hygiene-ignore` file** — applies when `scan-secrets` or
  `secret-hygiene-precommit` is called directly (e.g., `just lint` or
  `just scan-secrets`). The scanner auto-discovers this file by walking up from
  `cwd` to the repo root. Lines in this file are treated as glob patterns to
  exclude from the full-tree walk.

**Why both?** Pre-commit manages its own file list and does not expose that
list to the subprocess it invokes. A file excluded via `exclude:` in
`.pre-commit-config.yaml` is simply never handed to `secret-hygiene-precommit`
— the hook cannot see it. But `just scan-secrets` calls the scanner directly,
bypassing pre-commit entirely, so it needs `.secret-hygiene-ignore` to apply
the same exclusions. Story 1.7's post-review fix added the dual-mechanism
rationale to the scanner docs.

---

## worker-wrapper 283 MB over AC-7 200 MB budget

Story 1.8 documented a container-size deviation: the `worker-wrapper` image
measures approximately 283 MB against the 200 MB AC-7 budget.

**Attribution:** the overage is structural, not avoidable without changing
the runtime stack:

- Python 3.12-slim base: ~151 MB
- Node.js v24 binary (required by `clawhip-bridge-mcp`): ~121 MB
- Platform code: remainder

Options (replacing Node with a lighter runtime, or moving Node to a sidecar)
were evaluated and deferred. This is a documented architecture constraint, not
a bug. The deviation is tracked in the Story 1.8 artifact; a Phase 2 hardening
story may revisit it.

---

## `OMB_IMAGE_REGISTRY=ghcr.io/r2d2` default

Story 1.4 seeded `OMB_IMAGE_REGISTRY=ghcr.io/r2d2` as the default registry
namespace in `.env.example`. Story 1.9 added a fork-note to the README.

If you have forked the repository, change this value to your own GitHub owner
namespace (`ghcr.io/<YOUR_GITHUB_OWNER>`) before pushing images or running
`just deploy-vps`. Leaving the default means compose will attempt to pull from
`ghcr.io/r2d2` for any `OMB_VERSION` that does not exist locally — which will
fail for version tags you built yourself.

---

## See also

- [Testing guide](./testing-guide.md) — suppression-tag usage in the CI gate context.
- [Operator runbook](./operator-runbook.md) — per-service scaffold state + replacement stories.
