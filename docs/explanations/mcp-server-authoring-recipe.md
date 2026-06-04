# The MCP-server-authoring recipe, end to end

> Companion to **ADR-0010**. ADR-0010 is the *decision* ("every Phase-3 fleet server is authored
> this way"); this is the *explanation* — how the eight steps fit together, where each lives in the
> code, and why the shape is the shape. Five servers (`git`, `github`, `verification`, `memory`,
> `artifact` — Epics 15–19) were built to it; the consistency is not an accident, it is mechanically
> enforced.

## In one breath

A fleet MCP server is a **stdio subprocess**, spawned by `worker-wrapper`, that exposes a handful of
**tier-gated tools**, takes an **explicit `caller_trace_id`** on every call, routes any **spine
events** through the one FR26 writer, receives **only allowlisted (never secret) env**, ships **in
the base image** (no new container), and is **optional** (the worker runs fine without it). Eight
steps; each one has a build-time gate or a contract test that fails the build if you skip it.

## The picture

```
  worker-wrapper (MCPClientGroup)
    │  spawns, if WORKER_<X>_COMMAND is non-blank, with env = _ENV_ALLOWLIST ∩ os.environ
    ▼
  python -m <x>_mcp   ── __main__.py: read+validate env, exit 2 if a REQUIRED var is missing
    │
    ▼
  build_server(*, ...) -> FastMCP        (server.py — synchronous factory, I/O deferred to lifespan)
    │  TIER_MAP: {tool -> Tier}          (handlers/tools.py)
    │  register_tools(...)
    ▼
  @mcp.tool(name="x.do")                 every tool:
    async def x_do(*, caller_trace_id, ...):
        validate_caller_trace_id(caller_trace_id)        # step 4 — explicit, shape-checked
        check_tier("x.do", _caller(), TIER_MAP["x.do"])  # step 3 — Tier 0..2
        # ...or check_tier_with_approval(...) for Tier-3
        result = <do the bounded thing>
        await _emit_x_event("x.done", payload, caller_trace_id=...)  # step 5 — through FR26 writer
        return result
```

## Step 1 — stdio transport only (P2-I4)

`FastMCP(name).run()` on stdio. No HTTP/SSE/streamable transport — a fleet server is a *tool*, not a
network service, so it has no public surface (P2-I5). Enforced by `scripts/check_mcp_transport.py`
(exit 0 = no `mcp.server.sse` / `streamable_http` import anywhere). The server is reachable only by
its parent over the pipe it was spawned on.

## Step 2 — a synchronous `build_server` factory, I/O deferred to the lifespan

`build_server(*, ...) -> FastMCP` (e.g. `mcp-servers/git/src/git_mcp/server.py`) is **synchronous and
side-effect-free**: it wires config into closures and returns the server. All real I/O — recovery,
spawning the clawhip-bridge emitter client, opening a store — happens in a FastMCP **lifespan**
async-context. Config is injected at the boundary; the factory never reads `os.environ`. Startup
failures fail loud in the lifespan; mid-request failures fail soft (a tool returns a structured
error, never crashes the server). The `__main__.py` is the only place env is read, and it **exits 2**
the moment a REQUIRED var is missing — so a half-configured server never half-runs.

## Step 3 — every tool declares a capability tier (P3-I1)

A module-level `TIER_MAP: dict[str, Tier]` maps each tool name to a tier; each handler calls
`check_tier` (Tier-0..2) or `check_tier_with_approval` (Tier-3) **before any side effect**.

```
# packages/capabilities/src/capabilities/tiers.py — the kernel
Tier.ONE    read-only          → check_tier
Tier.TWO    mutate, no external → check_tier
Tier.THREE  destructive/external → check_tier_with_approval(approval_lookup=...)
```

Tier-3 tools (`git push`/`rebase`, every `github` write, `artifact delete`) are **denied without a
matching `approval.granted` event** and ship a *negative test* proving the denial. They are also
wrapped by `emit_capability_denied_on_deny` so a denial emits a `capability.denied` audit. This
invariant is **mechanically enforced** by `scripts/check_tier_declarations.py` — a build-time AST
gate (built in Epic 15, Story 15.2a) that fails the build if any `@mcp.tool()` lacks a `TIER_MAP`
entry referenced via `check_tier`. (See also `capability-tiers.md`.)

## Step 4 — `trace_id` is an explicit, shape-validated input

Every tool takes keyword-only required `caller_trace_id`, validated *first* by the **byte-identical**
`validate_caller_trace_id` helper, then threaded into the event envelope. Never ambient, never read
from env. "Byte-identical" is literal: `tests/contract/test_mcp_tool_schemas.py` AST-compares the
function body across **all eight stdio servers** (the three Phase-2 registry/bridge servers + the
five fleet servers) and fails on any drift — because the import-graph rule (Story 5.8) forbids
mcp-servers from sharing code, so the helper is *copied* per server and the contract test is what
keeps the copies honest. (See `trace-id-propagation.md`.)

## Step 5 — mutating spine events route through the single FR26 writer

A fleet server never writes the registry DB or the JSONL log directly. New event types
(`git.committed`, `github.pr.created`, `verification.completed`, `memory.written`, `artifact.stored`/
`artifact.deleted`) are emitted through `clawhip-bridge` (in-process or via a spawned stdio client +
`EmitterHolder`). Registration is **two-location, additive, born at schema `1.1.0`**: a payload model
in `packages/events/payloads.py` *and* a `register(...)` call in
`services/registry-state/.../domain/event_types.py` (kept service-side to avoid the
`events → registry_state` circular import). `scripts/check_event_registry.py` fails the build if a
`type="x.*"` literal is emitted without being registered, and `check_trace_id_required.py` fails it if
any envelope is created without a `trace_id`. **Event payloads carry metadata only** — never a stored
artifact's bytes, never a memory document's body, never a credential. (See `event-spine.md`.)

## Step 6 — child-env allowlist, never `os.environ.copy()`

Each server's REQUIRED vars are added to the **byte-identical** `_ENV_ALLOWLIST` frozensets in
`worker-wrapper` and `orchestrator-adapter`. The spawner forwards `{k: v for k,v in os.environ if k
in _ENV_ALLOWLIST}` — an explicit allowlist, *never* `os.environ.copy()` / `dict(os.environ)`. This is
the **a0ca050 P0** lesson: a full env-copy leaked `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` /
`OPERATOR_HMAC_KEY` into subprocesses (reverted twice). The forbidden-secret set is contract-enforced
(`test_clawhip_client_env_allowlist_mirror.py`). The **one** credential in the whole fleet allowlist
is `github`'s `GITHUB_MCP_SCOPED_TOKEN` — a deliberately *narrowly-named, repo-scoped* token (ADR-0010
§6); the broad `GITHUB_TOKEN` stays forbidden. Store-path / actor-identity / worktree-root vars are
non-secret. Because this is the P0 area, allowlist changes are authored in the main implementation
context (not delegated) and credential additions get an independent security-review pass.

## Step 7 — ships in the base image, supply-chain inherited

Added to the `members = ["mcp-servers/*"]` workspace glob; built into the base image via the existing
`Dockerfile.base` `COPY mcp-servers/` + `uv sync --all-packages`. **No `services/*` Dockerfile, no
compose entry, no `release.yml` matrix row (P3-I3).** Supply-chain — cosign + SLSA-L2 + CycloneDX
SBOM + the fail-closed license gate — is inherited transitively from the one signed base image. The
five fleet servers added **zero** new `release.yml` matrix rows and **zero** new third-party
dependencies (each uses only `mcp`, already present; the stores use stdlib `sqlite3`/`hashlib`, so even
FTS5 and content-addressing cost nothing).

## Step 8 — a separability entry that proves the server is optional (NFR-M8)

Each server is gated on a non-blank `settings.<x>_command` (default `""` → OFF) and gets a
`tests/separability/test_s{5..9}_<x>_optional.py` that boots a **real subprocess** in two states:

- **SPAWNED** — set the command + the REQUIRED env → the server is the live Nth `MCPClientGroup`
  member, its tools are listed, and a tool is callable *end-to-end through the stdio boundary* (e.g.
  memory does a write→read round-trip; artifact does a binary put→get; verification runs a recipe).
- **ABSENT** — blank command → the three core MCP members still initialize and a scripted task
  completes, proving the worker doesn't need this server.

This is the Phase-3 *spawn-composition* model (S-5…S-9) — distinct from the Phase-1/2 *compose-toggle*
model (S-1…S-4, which toggle a Docker service). A fleet server has no container to toggle; the seam
is its spawn command. A fresh boot with none of the five enabled behaves exactly as Phase 2.

## The three archetypes the recipe spans

The same eight steps produced three structurally different servers, which is the real proof the recipe
generalizes:

| Archetype | Servers | What it does | Tier shape |
|---|---|---|---|
| **subprocess-sandbox** | `git`, `verification` | `create_subprocess_exec` confined to the worktree (cwd-pinned, secret-free env, timeout, realpath containment) | 1/2/3, 2 |
| **REST-client** | `github` | aiohttp + tenacity to the GitHub API under a scoped token | 1/3 |
| **own-store** | `memory`, `artifact` | a private file/subtree on `oh-my-bmad-data` (FTS5 DB; content-addressed FS), never the registry DB (P3-I2) | 1/2, 1/2/3 |

## Why this shape

Phase 3 adds **tools, not new trust boundaries**. Every step exists to keep that true: stdio-only (no
new ingress), tier-gated (no unauthorized side effect), explicit trace_id (no correlation gap),
FR26-routed events (no second writer), allowlisted env (no leaked secret), base-image (no new
supply-chain surface), separable (no new hard dependency). The gates (`check_tier_declarations`,
`check_event_registry`, `check_trace_id_required`, `check_single_writer`, `check_mcp_transport`,
`check_imports`, `check_no_subprocess`) plus the contract tests (`validate_caller_trace_id`-identity,
`_ENV_ALLOWLIST`-mirror) are what make "authored to the recipe" a CI fact rather than a code-review
hope. Skip a step and the build is red.

## Where to look next

- **ADR-0010** (`docs/adr/0010-mcp-server-authoring.md`) — the decision + the G-FN-2 nested-stdio
  precondition.
- `docs/explanations/capability-tiers.md` — the tier kernel (step 3).
- `docs/explanations/event-spine.md` + `trace-id-propagation.md` — steps 4 + 5.
- Reference implementation: `mcp-servers/git/` (the recipe's first build); the per-epic
  retrospectives `_bmad-output/implementation-artifacts/epic-{15..19}-retro-2026-06-04.md` for the
  lessons each archetype taught.
