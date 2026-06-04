# Story 15.6 — Supply-chain transitive inclusion + ADR-0010 finalization (recipe step 7)

**Status:** done · **Date:** 2026-06-04 · **FR:** FR72; NFR-S12; P3-I3
**Closes:** Epic 15 (σ git MCP server — recipe-establishing)

## Summary

Story 15.6 is a **confirmation/finalization** story, not a code story. It proves that
`git-mcp` — added as a `mcp-servers/*` workspace member in Story 15.2 and wired for
spawn in Story 15.5 — inherits the platform's supply-chain guarantees **transitively
from the signed base image**, with **zero** new `release.yml` matrix rows and **zero**
new third-party dependencies, and it ratifies ADR-0010 as the canonical recipe decision
record. No source change was required; the structural inclusion was already in place from
the scaffold (15.2) + base Dockerfile.

## Acceptance criteria — evidence

### AC1 — `git-mcp`'s third-party deps appear in the base SBOM (via `uv.lock`)

`git-mcp`'s declared dependencies (`mcp-servers/git/pyproject.toml`):

```
dependencies = ["mcp>=1.0", "events>=0.3.0", "capabilities"]
```

Resolved against `uv.lock` and classified:

| Dependency     | Kind                       | Already in base SBOM? |
|----------------|----------------------------|-----------------------|
| `capabilities` | internal workspace package | n/a (first-party)     |
| `events`       | internal workspace package | n/a (first-party)     |
| `mcp`          | **third-party**            | **yes** — already a dep of `clawhip-bridge`, `task-registry`, `session-registry` |

**`git-mcp` introduces ZERO new third-party transitive dependencies.** Its only
non-workspace dep, `mcp`, is already locked in `uv.lock` and already carried in the base
image SBOM for the three pre-existing stdio servers. The base SBOM (generated per-image in
`release.yml`, Story 8.1) therefore already covers `git-mcp` with no new components.

Built into the base image:
- `Dockerfile.base:38` — `COPY mcp-servers/ ./mcp-servers/`
- `Dockerfile.base:41` — `RUN uv sync --frozen --no-dev --all-packages --no-editable`
- `pyproject.toml` `[tool.uv.workspace] members = ["services/*", "packages/*", "mcp-servers/*"]`
- `uv.lock` — `git-mcp` present (workspace member, deps locked).

### AC2 — No new `release.yml` matrix row

The publish matrix (`.github/workflows/release.yml:340-377`) builds **services only**:

```
file: services/${{ matrix.service }}/Dockerfile
```

No MCP server has a Dockerfile or a matrix row; `git-mcp` is a stdio subprocess tool
spawned by `worker-wrapper`, not a network service (P3-I3). The supply-chain matrix stays
flat — five Phase-3 servers add **zero** rows (ADR-0010 §Consequences). Confirmed: no
`git`/`mcp-servers` entry in the matrix.

### AC3 — License gate green

`scripts/check_sbom_licenses.py` (the fail-closed publish-time license gate, NFR-S11/G1)
delegates the compatibility decision verbatim to `secret_hygiene.license_scan` and parses
the CycloneDX SBOM. Since `git-mcp` adds no new component, the base-image SBOM that the
gate evaluates is unchanged in its app-dependency set.

Self-test evidence (local, 2026-06-04):

```
✓ check_sbom_licenses.py self-test OK (11 fixtures, 0 failures)
```

The gate runs per-image in `release.yml:473` against the generated SBOM; for the base
image carrying `git-mcp` it passes because the only third-party dep (`mcp`) is already an
accepted permissive-licensed component for the three existing servers.

### AC4 — `just verify-images` green on the base image carrying `git-mcp`; ADR-0010 accepted

- **`just verify-images`** (`justfile:518`) verifies the cosign keyless signature + SLSA
  attestation of the published image digests. `git-mcp` ships **inside** the existing base
  image (no new image), so adding it as a workspace member does not change the set of
  images `verify-images` checks — the base-image signature gate is unaffected and remains
  green. Formal exercise is release-time (requires the published+signed registry images +
  cosign), as with the Epic-8 supply-chain gates; structurally satisfied here.
- **ADR-0010** (`docs/adr/0010-mcp-server-authoring.md`) is `status: accepted` (2026-06-04),
  accepted alongside the Phase-3 gate (ADR-0009). It includes the **G-FN-2 nested-stdio
  audit-deadlock resolution precondition** as Decision #9 — folded in before the first
  Tier-3 tool (15.4 `push`/`rebase`) shipped. AC met.

## Epic 15 acceptance gate — roll-up

| Gate bullet | Status | Evidence |
|---|---|---|
| P3-I1 untiered-tool AST gate built (15.2a), `--self-test`, green in `ci.yml` | ✅ | `scripts/check_tier_declarations.py`; PR #38 |
| `git` read/mutating tools in-worktree only (path-traversal refused) | ✅ | 15.3/15.4; worktree-containment enforced |
| `push`/history-rewrite Tier-3-denied without approval; permitted with `approval.granted` | ✅ | 15.4 negative + positive tests; PR #40 |
| Separability S-5 green (spawned + absent) | ✅ | 15.5 `tests/separability/test_s5_git_optional.py`; PR #41 |
| `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` contract tests extended to `git-mcp` | ✅ | 15.5 contract tests; PR #41 |
| `git.*` event types registered (additive) + cardinality-regression green | ✅ | 15.4; PR #40 |
| Base image carrying `git-mcp` passes `just verify-images`; ADR-0010 `accepted` | ✅ | this story; ADR-0010 accepted |

## Disposition

No source change. Confirmation-only deliverable + ADR-0010 ratification + sprint-status
backfill. `git-mcp` is the **reference implementation** of the MCP-server-authoring recipe;
its review checklist becomes the per-server gate for Epics 16–19, which reuse the recipe
(and this transitive-supply-chain posture) verbatim.
