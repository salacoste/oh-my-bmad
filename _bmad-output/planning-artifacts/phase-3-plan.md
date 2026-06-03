# Phase-3 Plan — MCP Tooling Fleet

> **Status:** DRAFT planning artifact (brainstorming-convergence output, 2026-06-03). Authored after Phase 2 shipped (v0.3.0) + the Phase-3 readiness hardening (G-SEC-1 license-gate fail-closed, G-SEC-2 child-env allowlist — both merged, main green). This is the input to the BMad `create-prd` (extension) → `create-architecture` (extension) → `create-epics-and-stories` chain; FR/NFR numbers and story breakdowns here are PROPOSED and finalized in those steps. Companion: `phase-3-scoping-brief.md`.

## 1. Overview

**Theme:** the MCP tooling fleet — give the worker/orchestrator a set of first-class, stdio-only, tier-authz'd MCP tool servers, built on the Phase-2 spine (event-only telemetry, trace_id, supply-chain pipeline).

**Resolved scope (operator brainstorming decisions, 2026-06-03):**
- **D1 — IN:** exactly five servers — `git`, `github`, `build`/`verification`, `memory`/`wiki`, `artifact`.
- **D2 — OUT (deferred):** remote-MCP transport (HTTP/SSE). MCP stays **stdio-only** this phase (no auth/rate-limit sub-project). The deferred-ADR stays deferred.
- **D3 — OUT (deferred to Phase 5):** second CLI agent (Codex/Gemini/GLM). Single Claude Code runtime this phase.
- **D4 — entry point:** a **tests-first hardening warm-up epic FIRST** (FR56 digest-deprecation + mutation-testing nightly gate), then `git` (recipe-establishing), then the rest.
- **Also OUT (Phase 3+/4/6):** workspace, docker-pool, db-schema, docs-research, browser-automation, telegram-control-direct servers; replay mode (value-gated).

**Preserved invariants (carry from Phase 2 — non-negotiable):**
- **FR26 single-writer** — new servers are read-only consumers OR route mutations through the existing registry write path; none becomes a second DB writer.
- **MCP transport stdio-only** (P2-I4) — every new server is stdio; no `mcp.server.sse`/`streamable_http`.
- **Event-only telemetry** (NFR-O1/O10) — servers emit typed events on the spine; metrics remain derived in metrics-subscriber; NO per-server instrumentation.
- **trace_id propagation** (NFR-O7) — every new server stamps/propagates trace_id.
- **Tier-enforced authz** (Epic 6) — destructive tools (git push, github writes, artifact deletes) are Tier-3 gated through the approval flow.
- **Supply-chain** (Epic 8 + G-SEC-1/2) — each new server ships as a **wheel in the signed base image** (no per-server image); it inherits cosign/SLSA/SBOM signing transitively, passes the (now fail-closed) license gate, and uses the child-env allowlist.

## 2. Proposed FRs / NFRs (PRD-extension sketch — finalize in create-prd)

Numbering continues from FR71a → **FR72+**. Each is crisp + testable, mirroring the Phase-2 FR style.

- **FR72 — `git` MCP server.** Stdio MCP server exposing structured git tools (status, diff, log, branch, add, commit, push). `push` (and any history-rewrite) is Tier-3 gated via the approval flow; read tools are Tier-1. Operates only within the task worktree. Emits typed events for mutating ops; carries trace_id. Separability-tested (new S-entry).
- **FR73 — `github` MCP server.** Stdio MCP server for GitHub ops (create/list issues, create/update/list PRs, request reviews, comment). Writes are Tier-3 gated. Auth via a scoped token (see G-SEC-2 follow-up — prefer fine-grained PAT/GitHub App over the broad PAT). Extends the existing PR-draft adapter (Story 5.14) into a general surface.
- **FR74 — `build`/`verification` MCP server.** Stdio MCP server to run the project's build + test/verification recipes and return structured results (pass/fail, logs, coverage). Sandboxed to the worktree. Tier-2 (runs code but no external mutation). Emits `verification.*` events.
- **FR75 — `memory`/`wiki` MCP server.** Persistent knowledge store (filesystem + SQLite FTS5, per `prd.md:557`) exposing read/search/write tools for cross-task memory. Single-writer-safe (its own store file; never the registry DB). Tier-1 read, Tier-2 write.
- **FR76 — `artifact` MCP server + store.** Persisted build/run output store (the "Artifact store — Phase 3" box, `product-brief.md:88`) with put/get/list tools and a content-addressed backing store. Tier-2 write; retention policy configurable. Emits `artifact.*` events.
- **FR77 — digest-deprecation execution (was FR56's Phase-3 commitment).** Remove the tag-based image-reference fallback; digest-pinned references (`OMB_IMAGE_DIGEST_*`) become the sole supported deploy path; update compose/docs/`just verify-images` accordingly; tag refs emit a deprecation warning then are dropped.
- **NFR-(O-series) — mutation-testing nightly gate.** A nightly mutation-testing run (e.g. `mutmut`/`cosmic-ray`) over the platform-owned packages, with a published mutation score; gate threshold TBD. Evidence-gated per `architecture.md:1493` (metrics-subscriber data now available to justify runtime).
- **NFR-(M-series) — fleet separability.** Each new server is an optional, swappable stdio member: adding/removing it requires no source change to other services; verified by new `tests/separability/` entries (S-5…S-9).
- **NFR-(S-series) — fleet supply-chain + authz.** Every new server ships as a **wheel in the signed base image** and is cosign/SLSA/SBOM-covered transitively (no per-server image, no release-matrix row), passes the fail-closed license gate + child-env allowlist; every destructive tool is Tier-3-gated (negative tests prove denial without approval).

## 3. Epic Breakdown (next epic # = 14, dependency order per D4)

| Epic | Goal | Covers | Key stories (skeleton) | Acceptance gate |
|---|---|---|---|---|
| **14 — Tests-first hardening warm-up** | Land pure verification/CI work before any feature surface (Epic-8-before-features pattern). | FR77 (digest-deprecation), mutation-gate NFR | 14.1 digest-deprecation (drop tag fallback + compose/docs/verify-images) · 14.2 mutation-testing nightly gate scaffold + baseline score · 14.3 mutation-gate threshold decision + CI wiring · 14.4 G-FN triage (decide pull-in/defer G-FN-1/2/3) | digest-only deploy verified green; mutation nightly runs + publishes score; G-FN dispositions recorded |
| **15 — `git` MCP server** (recipe-establishing) | First fleet server; establishes the reusable "new stdio MCP server" recipe (authz + telemetry + separability + supply-chain). ATDD/test-design-first per operator priority. | FR72 | 15.1 test-design/ATDD red-phase per git tool contract · 15.2 server scaffold (stdio, workspace member) · 15.3 read tools (status/diff/log) Tier-1 · 15.4 mutating tools (commit/push) Tier-3-gated + events · 15.5 separability S-5 · 15.6 supply-chain (transitive base-image inclusion + license gate) | git tools work in worktree; push Tier-3-denied without approval (negative test); S-5 green; base image carrying the server verify-images-green |
| **16 — `github` MCP server** | GitHub issues/PRs/reviews surface; scoped-token auth (G-SEC-2 follow-up). | FR73 | 16.1 ATDD · 16.2 scaffold · 16.3 read tools · 16.4 write tools Tier-3-gated · 16.5 scoped-credential design (close G-SEC-2 GITHUB_TOKEN follow-up) · 16.6 separability S-6 + supply-chain | writes Tier-3-gated; scoped creds (no broad PAT in agent env); S-6 green |
| **17 — `build`/`verification` MCP server** | Structured build+test execution surface. | FR74 | 17.1 ATDD · 17.2 scaffold · 17.3 run-build/run-tests tools (sandboxed) · 17.4 structured-result + `verification.*` events · 17.5 separability S-7 + supply-chain | build/test run + structured results; events emitted; S-7 green |
| **18 — `memory`/`wiki` MCP server** | Cross-task persistent knowledge (FS + SQLite FTS5). | FR75 | 18.1 ATDD · 18.2 store schema (FTS5, own file — single-writer-safe) · 18.3 read/search tools · 18.4 write tool Tier-2 · 18.5 separability S-8 + supply-chain | search returns relevant results; store isolated from registry DB; S-8 green |
| **19 — `artifact` MCP server + store** | Persisted build/run output store. | FR76 | 19.1 ATDD · 19.2 content-addressed store + retention · 19.3 put/get/list tools · 19.4 `artifact.*` events + retention policy · 19.5 separability S-9 + supply-chain | put/get round-trip; retention enforced; S-9 green |
| **(19.5 — Phase-3 tech-debt sweep)** | Mirror Epic 3.5/7.5/8.7 — sweep debt before phase close. | — | per-retro items | retros' HIGH/MED items cleared |

> **Artifact-store infra:** FR76's backing store may warrant its own infra sub-epic if it needs a new volume/sidecar; decide in create-architecture. Keep it local-FS/content-addressed (no new external dependency) per the project's simplicity principle (`prd.md:557`).

## 4. Sequencing Rationale & Dependencies

- **Epic 14 first (D4):** pure verification/CI, zero feature surface, satisfies the tests-first priority and de-risks the deploy path (digest-only) before the base image carrying five new servers is published.
- **Epic 15 (git) second:** establishes the MCP-server recipe at the lowest-risk, best-understood domain (git ops already partly solved); every later server reuses its scaffold/authz/separability/supply-chain pattern.
- **Epics 16–19 then largely parallelizable** once the recipe exists — github/build/memory/artifact are independent server workspaces. Recommended order github → build → memory → artifact (operator-visibility + reuse of the github scoped-cred work).
- **Each epic is independently shippable** (a new optional stdio server), so Phase 3 can release incrementally (v0.4.0 after Epic 15, etc.) rather than one big-bang.

## 5. ADRs to author

- **ADR-0009 — Phase-3 gate** (opens `phase: 3` for main; mirrors ADR-0003).
- **ADR-0010 — MCP-server-authoring pattern** (the reusable recipe: stdio + tier-authz + event-telemetry + separability + supply-chain + child-env allowlist). Drafted alongside Epic 15.
- **ADR-0011 — artifact-store design** (content-addressed local store; retention; FR26-safe).
- **ADR-0012 — memory/wiki store** (SQLite FTS5; own file; isolation from registry DB).
- **DEFERRED (stay deferred):** remote-MCP transport ADR (D2), browser-automation surface ADR (D4 of the brief). Note in ADR-0009 that they remain non-decisions.

## 6. Readiness Pre-reqs

- ✅ **G-SEC-1** (license gate fail-closed) — merged.
- ✅ **G-SEC-2** (child-env allowlist) — merged; **GITHUB_TOKEN scoped-credential follow-up folds into Epic 16** (FR73 scoped auth).
- ⏳ **G-FN-1/2/3** — decide per Epic 14.4: G-FN-2 (nested-stdio audit deadlock) is **directly relevant** (more servers = more stdio nesting) → likely pull into Epic 15's recipe; G-FN-1 (cursor-filter/monotonic ADR) + G-FN-3 (unbounded MCP init) → pull-in if a new server exposes them, else keep tracked.
- **Epic-11/12 follow-up backlog** (11.2.1, 11.3.1, 11.3.2, 11.5.1, 12-3c) — `check-implementation-readiness` decides each: pull into Phase 3 or leave standing.

## 7. Phase-3 Ship-Blocker Checklist (mirror Phase-2)

**Architectural commitments (preserved):**
- [ ] FR26 single-writer unchanged (every new server read-only or via registry write path).
- [ ] MCP transport stdio-only (no sse/streamable_http in any new server).
- [ ] No instrumentation outside metrics-subscriber (servers emit events only).
- [ ] Every new event carries trace_id.
- [ ] No new public-network ingress (servers are stdio, internal).
- [ ] Cosign + SLSA + CycloneDX SBOM + fail-closed license gate covering the base image that carries all new servers (no per-server image); child-env allowlist on every new server.

**Per-epic gates:** each server — tools function in-worktree · destructive tools Tier-3-denied without approval (negative test) · separability S-entry green · base image carrying the server `just verify-images`-green. Epic 14 — digest-only deploy green · mutation nightly publishes score.

**Phase-1+2 invariants regression-free:** separability S-1…S-9 green · crash-injection green · idempotency green · contract green (incl. new event types) · arch gates green · replay equivalence holds.

**New ADRs accepted:** ADR-0009..0012.

**Principle:** if any item is not green, Phase 3 has not shipped.

## 8. Next BMad steps

1. **`bmad-create-prd` (extension)** — formalize FR72–FR77 + the NFRs (this draft is the input).
2. **`bmad-create-architecture` (extension)** — Phase-3 amendment + ADR-0009..0012; the MCP-server-authoring pattern.
3. **`bmad-create-epics-and-stories`** — decompose Epics 14–19 into dev-ready stories.
4. **`bmad-check-implementation-readiness`** — Phase-3 gate (PRD+arch accepted, `phase: 3` labels, ADR-0009 accepted, deferred-work reviewed, G-FN dispositions set).
5. **`bmad-sprint-planning`** — generate Phase-3 sprint-status entries.

— *Brainstorming convergence by R2d2 + Claude, 2026-06-03. Scope decisions D1–D4 fixed; D5 (mutation gate) folded into Epic 14, D6 (replay) deferred, D7 (digest-deprecation) = Epic 14.1.*
