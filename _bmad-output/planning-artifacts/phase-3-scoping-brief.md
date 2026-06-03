# Phase-3 Scoping & Readiness Brief — oh-my-bmad

> **Status:** Phase 2 shipped (v0.3.0, supply-chain-verified; all Epics 1–13 `done`). `current_phase: 2`. No Phase-3 epic/story entries exist yet — this brief is the BMad **analyst** deliverable that tees up Phase-3 planning. Read-only analysis; commits to building nothing. Authored 2026-06-03 (analyst subagent, grounded in planning docs + sprint-status + the one security finding's source).

---

## 1. Phase-3 Scope Inventory

Phase 3's documented theme is **"MCP tooling fleet / MCP-tooling baseline"** (`prd.md:753`, `product-brief.md:181`). Candidates, each with doc cite:

### A. MCP server fleet (the named Phase-3 theme)
| # | Candidate | What it is | Cite |
|---|---|---|---|
| 1 | `artifact` server (+ store) | Persisted build/run output store | `prd.md:595,753`; `product-brief.md:88,181` |
| 2 | `git` server | MCP tool surface for git ops | `prd.md:595,753`; `product-brief.md:133,181` |
| 3 | `github` server | MCP surface for GitHub (issues/PRs/reviews) | `prd.md:595,753`; `product-brief.md:133,181` |
| 4 | `build`/`verification` server | MCP-exposed build + verification | `prd.md:595,753`; `product-brief.md:133,181` |
| 5 | `memory`/`wiki` server | Persistent knowledge store | `prd.md:595,753`; `product-brief.md:133,181` |
| 6 | `workspace` server | Workspace-access MCP server | `prd.md:595` |
| 7 | `docker-pool` server | MCP over a Docker execution pool (roadmap: Phase 6) | `prd.md:595,756` |
| 8 | `db-schema` server | MCP for DB schema ops | `prd.md:595` |
| 9 | `docs-research` server | MCP for docs/research lookup | `prd.md:595` |
| 10 | `browser-automation` server | MCP for browser automation (roadmap: Phase 4) | `prd.md:595,754` |
| 11 | `telegram-control-direct` server | Direct Telegram-control MCP surface | `prd.md:595` |

> **Doc tension (→ D1):** `prd.md:595` lists all 11 as "Phase 3+"; the roadmap tables (`prd.md:753`, `product-brief.md:181`) scope Phase 3 to the **first five** (artifact, git, github, build/verification, memory/wiki). The five-server reading is the more specific/recent commitment and should anchor planning.

### B–F. Other documented candidates
| # | Candidate | What / cite |
|---|---|---|
| 12 | **Remote MCP transport (HTTP/SSE)** | Reconsider stdio-only invariant; "likely requires new auth + rate-limiting" — `architecture.md:1489`; deferred `prd.md:979` |
| 13 | **Second CLI agent (Codex/Gemini/GLM)** | trace_id + metrics built to enable head-to-head comparison — `architecture.md:1491` (roadmap puts multi-runtime at Phase 5: `product-brief.md:755`) |
| 14 | **Mutation-testing nightly gate** | Cat-4; metrics-subscriber to reveal if worth runtime — `architecture.md:1493` |
| 15 | **FR56 digest-deprecation** | Tag fallback "deprecated in Phase 3" — a commitment already made, `prd.md:990` |
| 16 | **Replay mode** | Value-gated on Phase-2 observability data — `architecture.md:1492` |

---

## 2. Dependency & Enablement Map

**Unblocked NOW by shipped Phase-2 work:**
- Second CLI agent / head-to-head (#13) ← trace_id kernel (Epic 9) + metrics-subscriber (Epic 10) — the explicit enablers (`architecture.md:1491`).
- Any new server's release/hardening ← supply-chain pipeline (Epic 8) + the just-shipped NFR-S11 license gate — new servers ship as **wheels in the signed base image** (no per-server image) and so inherit cosign/SLSA/SBOM + license gating transitively, for free.
- Mutation-testing gate (#14) ← metrics plane + existing nightly harness.
- FR56 digest-deprecation (#15) ← `OMB_IMAGE_DIGEST_*` + `just verify-images` already primary; cheapest item.

**Hard prerequisites / open ADRs before work can start:**
- Remote-MCP (#12): architectural decision + new auth/rate-limit layer; needs an ADR + a concrete remote-worker use case (`architecture.md:1489`).
- browser-automation (#10): ADR to decide surface shape (web UI vs worker tool) — `architecture.md:1490`.
- Second CLI agent (#13): which agent + phase-conflict (Phase 3 vs 5).
- Replay (#16): value-gated on production data, not capability.

---

## 3. Open Product/Architecture Decisions (resolve BEFORE planning)

- [ ] **D1 — Phase-3 scope:** five-server fleet (`prd.md:753`) or broader 11-server "Phase 3+" (`prd.md:595`)? *Determines epic count + phase boundary.*
- [ ] **D2 — Remote MCP transport:** yes/no this phase + which (HTTP/SSE/streamable)? Largest scope fork; pulls in auth + rate-limit (`architecture.md:1489`).
- [ ] **D3 — First additional CLI agent:** which (Codex/Gemini/GLM), and Phase 3 (arch) vs Phase 5 (roadmap)?
- [ ] **D4 — Browser-automation surface:** 4th operator surface (web UI) vs 4th worker tool (Playwright/Patchright)? Needs the promised ADR (`architecture.md:1490`).
- [ ] **D5 — Mutation-testing gate:** worth the runtime, per actual metrics data now available?
- [ ] **D6 — Replay mode:** has production data made its value obvious yet?
- [ ] **D7 — FR56 digest-deprecation:** execute now; is the Phase-2 cutover window complete?

---

## 4. Recommended Phase-3 Entry Point

**Pattern:** the project lands the foundational, cheapest-highest-ROI, dependency-unblocking item first (Epic 8 led Phase 2; Epic 1 led Phase 1).

**Recommendation:** lead with a **"first MCP server + server-authoring substrate" epic — the `git` server** (`github` close second).
- Highest ROI / lowest risk: domain already partly solved (Story 5.7 GitHub adapter, PR-draft), so the new work is wrapping known behavior in the established stdio-MCP + tier-authz + event-only-telemetry pattern.
- Establishes the reusable "new MCP server" recipe (like Epic 8 established the release recipe); exercises separability fixtures; surfaces test-isolation issues early at small blast radius.
- Inherits Phase-2 supply-chain + license-gate hardening for free.
- **Sidesteps the two unresolved ADRs** (D2 remote-MCP, D4 browser-automation) — git/github are stdio-only, in-pattern, decision-free.

**Tests-first warm-up (operator priority):** consider a tiny "Phase-3 hardening warm-up" epic FIRST — FR56 digest-deprecation (#15) + evaluate mutation-testing gate (#14): pure verification/CI work, near-zero feature surface, mirrors Epic-8-before-features. Then the `git` server with a `bmad-testarch-test-design`/ATDD red-phase pass per MCP tool contract.

---

## 5. BMad Planning Next-Steps (mirrors the Phase-2 chain)

1. **`bmad-brainstorming`** — resolve scope/narrative (Phase 3 has more forks D1–D7 than Phase 2). Inputs: this brief, metrics data (D5/D6), operator's second-runtime need (D3).
2. **`bmad-create-prd` (extension)** — Phase-3 FRs/NFRs (as Phase 2 added FR53–FR71a).
3. **`bmad-create-architecture` (extension)** — Phase-3 amendment + the two promised ADRs (remote-MCP D2, browser-automation D4).
4. **`bmad-create-epics-and-stories`** — decompose into epics (next epic # = **14**).
5. **`bmad-check-implementation-readiness`** — Phase-3 gate (PRD+arch accepted, `phase: 3` labels, Phase-3-gate ADR accepted, `deferred-work.md` reviewed).
6. **`bmad-sprint-planning`** — generate Phase-3 sprint-status entries.

---

## 6. Readiness Gaps (clear before Phase 3)

### Security (highest value — recommend first, on the operator's tests/verification priority)
- **G-SEC-1 — `_token_ok` is default-OPEN for unknown licenses** (`license_scan.py:118`): returns `True` for any token lacking a copyleft substring → an **unknown/unrecognized license passes as compatible**. The `"unknown-incompatible"` reason code (`license_scan.py:129`) is never triggered. **Now load-bearing:** the shipped NFR-S11 publish-gate (`scripts/check_sbom_licenses.py`) reuses this policy, so an unknown-license transitive dep would silently pass the release gate. Recommend flipping to **fail-closed (allowlist-only) + explicit operator-override** BEFORE Phase 3 multiplies dependency surface. *(Operator decision — flipping fail-closed is stricter and may block releases on legit-but-unrecognized licenses; needs an override path.)*
- **G-SEC-2 — child-env secret leak to subprocesses** (`deferred-work.md:130,133`): `claude_code_runner._spawn` + `omc_runner._spawn` build child env via `dict(os.environ)`, inheriting all parent secrets. `mcp_clients.py` already has `_ENV_ALLOWLIST` (the a0ca050 P0 area) — apply the same pattern. **Phase 3 multiplies spawn sites** (new servers, second agent) → clear before #13. Diff-audit any fix (project memory records a reintroduction incident).

### Functional / architectural carry-forwards
- **G-FN-1** — Story 2.6.X cursor-filter / monotonic-clock decision still open (`deferred-work.md:137`); more event-writers in Phase 3 makes it harder to defer.
- **G-FN-2** — nested-stdio audit deadlock; `OMB_MCP_AUDIT_EMISSION_ENABLED` forced OFF on 2 spawners (`deferred-work.md:149`). New servers = new nesting candidates.
- **G-FN-3** — unbounded MCP liveness-probe init (`deferred-work.md:153`); more servers = more unbounded init windows.

### Process
- Epic-11/12 follow-up backlog (11.2.1, 11.3.1, 11.3.2, 11.5.1, 12-3c) — "new work, not blockers"; the readiness gate should decide each: pull into Phase 3 or leave standing.

---

## Open Questions (operator)
1. Phase-3 boundary: five-server (`prd.md:753`) or 11-server "Phase 3+" (`prd.md:595`)? (D1)
2. Remote-MCP transport this phase? Concrete remote-worker use case emerged? (D2)
3. Second CLI agent — which, and Phase 3 or Phase 5? (D3)
4. Browser-automation — operator surface vs worker tool; Phase 3 or 4? (D4)
5. Mutation-testing gate and/or replay mode — does metrics data justify them now? (D5/D6)
6. Flip `_token_ok` fail-closed before Phase 3? (G-SEC-1 — now release-gating)
7. Clear the child-env secret-leak allowlist before the second-agent epic? (G-SEC-2)

**Key sources:** `architecture.md:1485–1507`; `prd.md:595,744,753,979,990`; `product-brief.md:88,133,152,181,755`; `sprint-status.yaml:39,230–332`; `deferred-work.md:130,133,137,149,153`; `license_scan.py:111–129`.
