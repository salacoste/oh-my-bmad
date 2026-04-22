# VENDORED

Vendored-with-sync upstream fork manifest. Each row records the pinned commit SHA + sync date of the upstream source tree copied into `upstream/<name>/`. Updated by `just sync-upstream <name>`.

Rationale: Architecture §Starter Template Evaluation — vendoring keeps the working tree self-contained; re-syncs are explicit + auditable. See `upstream/<name>/README.md` for each fork's role.

| Fork | Upstream URL | Pinned commit SHA | Sync date (UTC) | First real use |
|---|---|---|---|---|
| `omc` | https://github.com/Yeachan-Heo/oh-my-claudecode | `0ac52cdaa093d6c41763e47055e995adaa4f8987` | 2026-04-22 | Story 5.10 (orchestrator-adapter) |
| `clawhip` | https://github.com/Yeachan-Heo/clawhip | `ff3ba32dc22a143d53bec40870d3b52b2fa11a2b` | 2026-04-22 | Story 2.8 (clawhip-bridge MCP server) |

**How to update:** run `just sync-upstream <name>`. The recipe fetches the upstream repo at its current HEAD, copies into `upstream/<name>/`, and rewrites this file's SHA + sync-date cell in-place.

**How to re-pin to a specific commit:** edit `scripts/sync_upstream.py` to accept a `--commit <sha>` argument (not in Story 1.3 scope — current recipe fetches `HEAD` only). For now, after running `just sync-upstream <name>`, the SHA recorded is whatever the upstream's HEAD was at fetch time.

## Vendored utility libraries (future — Story 1.2 carry-forward)

Per NFR-M1, general utility libraries may be vendored here if doing so is materially simpler than a package-manager dependency. None currently vendored.

| Utility | Upstream | SHA | Reason |
|---|---|---|---|
| *(none)* | — | — | — |
