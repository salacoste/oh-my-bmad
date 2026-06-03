---
id: ADR-0010
status: proposed
date: 2026-06-03
supersedes: null
---

# ADR-0010: MCP-server-authoring pattern — the canonical recipe for every Phase-3 fleet server

## Status

**Proposed** — 2026-06-03. Transitions to **accepted** before Epic 15's first story merges to `main` (per [ADR-0009](./0009-phase-3-gate.md) §3). Mirrors the ADR-0004..0008 lifecycle: proposed in the architecture amendment, accepted as its owning epic begins. Gates **Epic 15**; reused verbatim by **Epics 16–19**.

## Context

Phase 3's scope (ADR-0009; [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md)) is five new stdio MCP tool servers (`git`, `github`, `verification`, `memory`, `artifact` — FR72–FR76). The platform already runs **three** stdio MCP servers — `clawhip-bridge`, `task-registry`, `session-registry` (`mcp-servers/*`) — built across Stories 2.8/5.8/5.9 and hardened through Epics 6 (tiers), 9 (trace_id), and 11 (audit emission). These three encode a consistent, battle-tested authoring shape, but that shape lives only as convention across three files; it has never been written down as a contract.

Adding five servers without a written recipe risks divergence: an untiered tool (authz hole), an ambient `trace_id` (correlation gap), a broad-secret env leak (the `ANTHROPIC_API_KEY`/`GITHUB_TOKEN`/`OPERATOR_HMAC_KEY` leak reverted twice — see `mcp-servers/.../adapters/clawhip_client.py` and the worker-wrapper `_ENV_ALLOWLIST` comment at `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py:26-28`), or a server accidentally modeled as a standalone compose service (new public surface, redundant supply-chain). This ADR makes the recipe canonical so Epic 15 implements it once and Epics 16–19 reuse it.

The reference implementations cited throughout:
- `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py` — stdio factory, lifespan recovery, `TIER_MAP`, `_emit` with `trace_id`, `validate_caller_trace_id`.
- `mcp-servers/task-registry/src/task_registry_mcp/app/main.py` — read-only SQLite (`read_only=True`), clawhip-bridge client spawn + `EmitterHolder` for FR26-routed audit.
- `mcp-servers/task-registry/src/task_registry_mcp/handlers/tools.py` — `@mcp.tool()` + `_maybe_wrap` audit decorator + `check_tier`.
- `packages/capabilities/src/capabilities/tiers.py` — `check_tier` / `check_tier_with_approval`.
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — `_ENV_ALLOWLIST` + `StdioServerParameters` spawn.
- `Dockerfile.base` (`:38` `COPY mcp-servers/`, `:41` `uv sync --all-packages --no-editable`) + `.github/workflows/release.yml` (`:347-354` matrix omits mcp-servers).

## Decision

Every Phase-3 fleet server is authored to the eight-step pattern documented in [`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 3 Architecture Extension" → "The MCP-server-authoring pattern". The load-bearing decisions:

1. **stdio transport only.** `FastMCP(name).run()` on stdio (P2-I4). No HTTP/SSE/streamable transport.

2. **Synchronous `build_server(*, ...) -> FastMCP` factory** with all I/O (recovery, emitter-client spawn) deferred to a FastMCP lifespan async-context; config injected at the boundary, never `os.environ` read inside the factory. Startup failures fail loud in the lifespan; mid-request failures fail soft per PD-1.

3. **Every tool declares a tier (P3-I1).** A module-level `TIER_MAP: dict[str, Tier]` maps each tool name to its tier; each handler calls `check_tier` (Tier-0..2) or `check_tier_with_approval` with an `approval_lookup` (Tier-3) before any side effect. Destructive tools are `Tier.THREE`; each ships a negative test proving `CapabilityDenied` without a matching `approval.granted` event. Tier-3 handlers are additionally wrapped by `emit_capability_denied_on_deny` so denials emit a `capability.denied` audit through the FR26 writer.

4. **`trace_id` is an explicit, shape-validated input.** Every tool takes keyword-only required `caller_trace_id`, validated first by the byte-identical `validate_caller_trace_id` helper, threaded into `EventEnvelope.create(trace_id=...)`. Never ambient, never env-backchannel.

5. **Mutating spine events route through the single FR26 writer** (clawhip-bridge `EventLogWriter.append`), in-process or via a spawned clawhip-bridge stdio client + `EmitterHolder`. New event types are registered additively in `registry-state` `domain/event_types.py`.

6. **Child-env allowlist, never `os.environ.copy()`.** Each server's REQUIRED vars are added to the byte-identical `_ENV_ALLOWLIST` frozensets in `worker-wrapper` and `orchestrator-adapter`. No broad secret is ever added; scoped credentials (e.g. `github`'s) use new, narrowly-named vars.

7. **Ships in the base image, not as a compose service (P3-I3).** Added to the `members = ["mcp-servers/*"]` workspace glob; built into the base via existing `COPY mcp-servers/` + `uv sync --all-packages`; spawned as a stdio subprocess by the worker/orchestrator. No `services/*` Dockerfile, no compose entry, no `release.yml` matrix row. Supply-chain (cosign/SLSA/CycloneDX + fail-closed license gate) is inherited transitively from the base image (NFR-S12).

8. **A new separability entry (S-5…S-9)** that toggles the server's *spawn command* (not a compose service) and proves the member is optional: tools listed+callable when spawned; all other servers + worker still function when absent (NFR-M8).

9. **G-FN-2 nested-stdio audit deadlock is resolved in this recipe** before the first Tier-3 tool ships. Because every destructive tool may spawn a clawhip-bridge stdio child to emit its denial audit, the nested-stdio path becomes five-fold more common; the Epic-14.4 G-FN-2 disposition is folded in here as a recipe precondition.

## Consequences

- **Epic 15 (`git`) is the reference implementation;** its review checklist becomes the per-server gate for 16–19.
- **The contract tests expand to all eight servers:** `validate_caller_trace_id` byte-identical body + `_ENV_ALLOWLIST` mirror.
- **No supply-chain matrix growth.** Five new servers add zero `release.yml` matrix rows; the base image's attestations cover them. This keeps the release pipeline flat.
- **The mutation gate (NFR-O11) protects this recipe's kernel.** A surviving mutant in `tiers.py` `check_tier` is a fleet-wide authz hole; Epic 14's mutation gate targets exactly these packages.
- **Code duplication is accepted, by constraint.** The import-graph rule (Story 5.8) forbids mcp-servers sharing code directly; `validate_caller_trace_id` and the clawhip-client adapter are copied byte-identically and guarded by contract tests. This ADR ratifies that trade-off rather than introducing a shared mcp-servers package.

## Alternatives considered

- **A shared `mcp-server-kit` package the five servers import.** Rejected for now — the Story-5.8 import-graph constraint blocks mcp-servers from sharing code, and the existing three servers already duplicate-with-contract-test. Revisiting this is a legitimate Phase-3.5 tech-debt item (a `packages/mcp-kit` that mcp-servers may import), but pulling it into Epic 15 would re-litigate a settled constraint mid-recipe. Tracked, not adopted.
- **Model each server as a standalone compose service + image.** Rejected (P3-I3) — contradicts the existing packaging (no existing MCP server has a Dockerfile or matrix row), adds public surface against P2-I5, and creates a redundant five-row supply-chain matrix. Servers are subprocess tools, not network services.
- **Ambient `trace_id` via env var into the subprocess.** Rejected — violates the Story-9.5 explicit-input contract (`server.py:1167`-equivalent: "propagation is explicit, not ambient"); a mis-wired client could spoof another actor's trace context.

## Linked artifacts

- [`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 3 Architecture Extension" — the eight-step recipe + P3-I1/I2/I3.
- [`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension" — FR72–FR77 + NFR-O11/M8/S12.
- [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) — Epics 14–19 + G-FN dispositions.
- Reference code: `mcp-servers/clawhip-bridge/.../server.py`, `mcp-servers/task-registry/.../app/main.py` + `handlers/tools.py`, `packages/capabilities/.../tiers.py`, `services/worker-wrapper/.../adapters/mcp_clients.py`, `Dockerfile.base`, `.github/workflows/release.yml`.

— *R2d2, 2026-06-03 (proposed; via the BMad Phase-3 planning chain).*
