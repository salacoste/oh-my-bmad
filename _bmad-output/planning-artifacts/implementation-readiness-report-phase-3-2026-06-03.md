---
report_type: implementation_readiness
phase: 3
date: 2026-06-03
author: R2d2
workflow: bmad-check-implementation-readiness
verdict: READY
companion_documents:
  - _bmad-output/planning-artifacts/phase-3-scoping-brief.md
  - _bmad-output/planning-artifacts/phase-3-plan.md
  - _bmad-output/planning-artifacts/prd.md (Phase 3 Scope Extension)
  - _bmad-output/planning-artifacts/architecture.md (Phase 3 Architecture Extension)
  - _bmad-output/planning-artifacts/epics.md (Phase 3 Epics — MCP tooling fleet)
  - docs/adr/0009-phase-3-gate.md
  - docs/adr/0010-mcp-server-authoring.md
  - docs/adr/0011-artifact-store.md
  - docs/adr/0012-memory-wiki-store.md
---

# Phase 3 implementation-readiness report

> Verifies that Phase 3 planning artifacts (PRD amendment + architecture amendment + epics decomposition + Phase 3 gate ADR + the three forward-referenced server ADRs) are mutually aligned and ready for `bmad-sprint-planning`. Modeled after the Phase 2 report at [`implementation-readiness-report-phase-2-2026-05-15.md`](./implementation-readiness-report-phase-2-2026-05-15.md). Phase 3 is incremental and fully additive (five new optional stdio MCP servers + a tests-first hardening warm-up), so the gate focuses on additivity, cross-amendment alignment, Phase-1+2 regression-freedom, and the closure of the carried-forward gate-tooling debt surfaced by the adversarial critic review.
>
> **This report records the application of the must-fix items from the Phase-3 adversarial critic review (verdict: READY-WITH-FIXES). All three MAJOR findings and the minor/gap items were applied 2026-06-03; the verdict is upgraded to READY.**

## Step 1 — Document discovery

All Phase 3 planning artifacts present and readable:

| Artifact | Path | Status |
|---|---|---|
| Scoping brief (analyst) | `_bmad-output/planning-artifacts/phase-3-scoping-brief.md` | ✅ |
| Phase-3 plan (brainstorming convergence) | `_bmad-output/planning-artifacts/phase-3-plan.md` | ✅ |
| PRD amendment | `_bmad-output/planning-artifacts/prd.md` §"Phase 3 Scope Extension" | ✅ FR72–FR77 + NFR-O11/M8/S12 |
| Architecture amendment | `_bmad-output/planning-artifacts/architecture.md` §"Phase 3 Architecture Extension" | ✅ P3-I1/I2/I3 + recipe + per-epic wiring |
| Epics + stories | `_bmad-output/planning-artifacts/epics.md` §"Phase 3 Epics" (Epics 14–19) | ✅ |
| ADR-0009 Phase 3 gate | `docs/adr/0009-phase-3-gate.md` | ✅ `proposed` (acceptance-ready) |
| ADR-0010 MCP-server-authoring | `docs/adr/0010-mcp-server-authoring.md` | ✅ `proposed` |
| ADR-0011 artifact-store | `docs/adr/0011-artifact-store.md` | ✅ `proposed` |
| ADR-0012 memory/wiki store | `docs/adr/0012-memory-wiki-store.md` | ✅ `proposed` |
| This readiness report | `_bmad-output/planning-artifacts/implementation-readiness-report-phase-3-2026-06-03.md` | ✅ this artifact |

**Discovery verdict:** ✅ all artifacts exist and are mutually cross-referenced. Phase 3 reuses the Phase-2 planning chain shape verbatim.

---

## Step 2 — PRD amendment analysis

### Functional Requirements added (6 across the fleet)

| FR | Capability | Owning Epic |
|---|---|---|
| FR72 | `git` MCP server (status/diff/log/branch Tier-1; add/commit Tier-2; push/history-rewrite Tier-3-gated; worktree-bound) | Epic 15 |
| FR73 | `github` MCP server (issues/PRs/reviews/comments; reads Tier-1; writes Tier-3-gated; scoped credential closes G-SEC-2) | Epic 16 |
| FR74 | `verification` MCP server (build+test recipes → structured result; worktree-sandboxed; Tier-2) | Epic 17 |
| FR75 | `memory`/`wiki` MCP server (FS + SQLite FTS5; read/search Tier-1, write Tier-2; own store file) | Epic 18 |
| FR76 | `artifact` MCP server + store (content-addressed local-FS; get/list Tier-1, put Tier-2, delete Tier-3-gated; retention) | Epic 19 |
| FR77 | digest-deprecation execution (digest-pinned references become the sole deploy path) | Epic 14 |

**FR completeness assessment:** PASS. Every Phase-3 capability in the architecture amendment has exactly one owning FR + epic.

### Non-Functional Requirements added (3)

| NFR | Target | Verified by |
|---|---|---|
| NFR-O11 | Nightly mutation-testing gate over platform-owned packages; published score; threshold-enforced | Epic 14 (14.2 baseline, 14.3 threshold) |
| NFR-M8 | Fleet separability — each server an optional swappable stdio member (toggle spawn config, no source change elsewhere) | S-5…S-9 separability tests (Epics 15–19) |
| NFR-S12 | Fleet supply-chain + tier-authz — servers ship as wheels in the signed base image (no per-server image / matrix row); every destructive tool Tier-3-gated with a negative denial test | base-image `just verify-images` + per-server Tier-3-denial tests |

**NFR completeness assessment:** PASS. Each NFR has a measurable target and a verification mechanism. NFR-S12 was a stale-language hotspot ("per-server image") — corrected to the wheels-in-base-image transitive model in this pass (see Step 6, minor fixes).

### Phase 1 + 2 invariants explicitly preserved

ADR-0009 §Decision item 4 makes the preserved spine non-negotiable: FR26 single-writer, stdio-only MCP transport, event-only telemetry (no instrumentation outside `metrics-subscriber`), `trace_id` propagation, tier-enforced authz, and the supply-chain triumvirate + fail-closed license gate + child-env allowlist.

**Preservation assessment:** PASS.

---

## Step 3 — Architecture amendment analysis

### Phase 3 architectural invariants (P3-I1 through P3-I3)

| ID | Invariant | Enforcement |
|---|---|---|
| P3-I1 | Every MCP tool declares a capability tier (`TIER_MAP` entry); untiered tool = build-time failure | `scripts/check_tier_declarations.py` AST gate — **built in Epic 15 / Story 15.2a** (see MAJOR-1 disposition) |
| P3-I2 | A store-owning server uses an isolated file; never a second writer of the registry DB / JSONL log | `memory` (FTS5 own DB) + `artifact` (content-addressed FS) own per-server subtrees; `scripts/check_single_writer.py` |
| P3-I3 | Servers ship as wheels in the base image and run as stdio subprocesses — never standalone compose services | no `services/*` Dockerfile / compose entry / `release.yml` matrix row; `scripts/check_mcp_transport.py` |

**Invariant assessment:** PASS. Each invariant has a mechanical enforcement, not just a documentation rule. P3-I1's enforcement gate was a forward-reference with no owning story — now closed (MAJOR-1).

### The MCP-server-authoring recipe (8 steps)

The architecture amendment documents the canonical 8-step recipe (workspace layout, `build_server` factory, tool+tier wrapping, event emission with `trace_id`, child-env allowlist, `__main__` entrypoint, transitive supply-chain, separability entry) plus the G-FN-2 nested-stdio precondition. Epic 15 implements it end-to-end; Epics 16–19 reuse it verbatim. ADR-0010 is its decision record.

**Recipe assessment:** PASS. Recipe step 4 (event-type registration) was mis-described as a single-location edit — corrected to the two-location payload-model + service-side-`register()` model that avoids the `events → registry_state` circular import (MAJOR-3).

### ADRs forward-referenced (and their owning epic)

| ADR | Topic | Owning Epic | Status |
|---|---|---|---|
| ADR-0009 | Phase 3 gate | (this gate) | `proposed` → acceptance-ready |
| ADR-0010 | MCP-server-authoring pattern | Epic 15 (reused 16–19) | `proposed` |
| ADR-0011 | artifact-store design | Epic 19 | `proposed` |
| ADR-0012 | memory/wiki store | Epic 18 | `proposed` |

**ADR readiness:** all four exist as `proposed`. With the must-fix items applied, the gate ADRs (0009–0012) may now be moved to `accepted`.

---

## Step 4 — Epic coverage validation (FR/NFR-to-story matrix)

### FR → Epic coverage matrix

| FR / NFR | Owning Epic | Key stories | Coverage |
|---|---|---|---|
| FR72 (`git` server) | Epic 15 | 15.1 ATDD · 15.2 scaffold · **15.2a tier-declaration AST gate** · 15.3 read · 15.4 mutating+events · 15.5 allowlist+S-5 · 15.6 supply-chain+ADR-0010 | ✅ PASS |
| FR73 (`github` server) | Epic 16 | 16.1–16.6 (scaffold · read · write Tier-3 · scoped credential · S-6) | ✅ PASS |
| FR74 (`verification` server) | Epic 17 | 17.1–17.5 (scaffold · run tools sandboxed · structured result+events · S-7) | ✅ PASS |
| FR75 (`memory`/`wiki` server) | Epic 18 | 18.1–18.5 (store schema FTS5 own-file · read/search · write+events · S-8 · ADR-0012) | ✅ PASS |
| FR76 (`artifact` server+store) | Epic 19 | 19.1–19.5 (content-addressed store+retention · put/get/list · events · S-9 · ADR-0011) | ✅ PASS |
| FR77 (digest-deprecation) | Epic 14 | 14.1 | ✅ PASS |
| NFR-O11 (mutation gate) | Epic 14 | 14.2 baseline · 14.3 threshold | ✅ PASS |
| NFR-M8 (fleet separability) | Epics 15–19 | S-5…S-9 (one per server) | ✅ PASS |
| NFR-S12 (fleet supply-chain + authz) | Epics 15–19 | per-server supply-chain story + Tier-3-denial negative tests | ✅ PASS |

**Coverage statistics:** 6 FRs + 3 NFRs (FR72–FR77 + NFR-O11/M8/S12) — **9/9 covered, all PASS, 0 unmapped.** Two gate-tooling stories were added in this pass (Story 14.5 `check_trace_id_required.py`; Story 15.2a `check_tier_declarations.py`) to close referenced-but-unbuilt gates.

**Coverage assessment:** PASS.

---

## Step 5 — UX alignment

**Phase 3 has no UI work.** The new servers are stdio MCP tool surfaces consumed by the worker/orchestrator; operator-facing additions are text/runbook only (digest-only deploy procedure, mutation-gate score location, per-server operator notes). All surfaces inherit Phase 1's message-design discipline.

**UX alignment assessment:** PASS (trivial — no new operator UI surface).

---

## Step 6 — Critic-review findings + applied dispositions

The adversarial Phase-3 implementation-readiness critic returned **READY-WITH-FIXES** with three MAJOR findings + several minor/gap items. All were applied 2026-06-03. Dispositions:

### MAJOR-1 — P3-I1 untiered-tool AST gate referenced but never built, no owning story

**Evidence (critic):** `architecture.md` P3-I1 and the epics ship-blocker reference "the P3-I1 untiered-tool AST gate" as a gate, but `scripts/check_tier_declarations.py` does not exist and no story builds it.

**Disposition (FIXED):** **Built in Epic 15** as a fleet-wide deliverable.
- `epics.md` — added **Story 15.2a** to build `scripts/check_tier_declarations.py` (AST-walk `mcp-servers/**/handlers/tools.py`, assert every `@mcp.tool()` name has a `TIER_MAP` entry; `--self-test`; wired into `ci.yml`); referenced in the Epic-15 acceptance gate.
- `epics.md` Phase-3 ship-blocker (P3-I1 item + arch-gates item) — clarified the gate is **built in Epic 15, Story 15.2a** (not pre-existing).
- `architecture.md` P3-I1 (~line 1537) + Phase-3 CI-gate additions — noted the gate is introduced in Epic 15.
- `docs/adr/0010-mcp-server-authoring.md` §Decision item 3 — references the new gate as the mechanical P3-I1 enforcement, built in Story 15.2a.

### MAJOR-2 — `check_trace_id_required.py` referenced as a ship-blocker gate but doesn't exist (Phase-2 carryover debt)

**Evidence (critic):** the ship-blocker checklist cites `check_trace_id_required.py` AST-scanning every `EventEnvelope.create(...)` callsite, but the gate was deferred at Phase-2 Story 9.7 and never landed.

**Disposition (FIXED): BUILD it in Epic 14.**
- `epics.md` — added **Story 14.5** to build `scripts/checks/check_trace_id_required.py` (AST-scan every `EventEnvelope.create(...)` callsite requires a `trace_id=` kwarg; `--self-test`; wired into `ci.yml`); fits the Epic-14 tests-first hardening theme; closes the Phase-2 Story-9.7 deferral; added to the Epic-14 acceptance gate.
- `epics.md` Phase-3 ship-blocker (`trace_id` item) — updated so the gate is an **Epic-14, Story 14.5** deliverable (closes the Story-9.7 deferral; did not pre-exist).

### MAJOR-3 — event-type registration mis-described (circular-import trap)

**Evidence (critic):** recipe step 4 said "register the new event types additively in `registry-state` `domain/event_types.py`", implying a single-location edit — but the payload model lives in `packages/events/payloads.py` and only the `register()` side-effect stays service-side to avoid the `events → registry_state` circular import (documented in that file's module docstring). A naive single-location edit would reintroduce the circular import.

**Disposition (FIXED):** corrected to the two-location model everywhere it appears.
- `architecture.md` recipe step 4 (~line 1572) — corrected to: define the payload model in `packages/events/payloads.py`; add the `register()` call in `registry-state/domain/event_types.py` (service-side to avoid the circular import — citing the module docstring's import chain); `scripts/check_event_registry.py` validates the type string against `packages/events/schema_registry.py`.
- `epics.md` — propagated the corrected two-location wording to the five `*.4` stories (15.4, 16.4, 17.4, 18.4, 19.4).

**Verification of accuracy:** the corrected wording was checked against the live source — `services/registry-state/src/registry_state/domain/event_types.py` (module docstring states the exact `events.__init__ → registry_state.__init__ → registry_state.adapters.event_log → events.EventEnvelope` chain), `packages/events/src/events/payloads.py`, and `packages/events/src/events/schema_registry.py` all exist as described.

### Minor fixes applied

| Item | Disposition (FIXED) | Where applied |
|---|---|---|
| Packaging "images" → "wheels in the base image" (per-server-image language) | Reworded to the transitive/base-image model (no per-server image; verify-images covers the base image) | `phase-3-plan.md:22,36,43,54,82,84`; `phase-3-scoping-brief.md:43`; `docs/adr/0009-phase-3-gate.md:69,76` (Consequences + Servers-first alternative) |
| Directory naming — align to verified convention `mcp-servers/<name>/` (package `<name>-mcp`) | Fixed DIRECTORY refs `mcp-servers/git-mcp/` → `mcp-servers/git/` (and github/verification/memory/artifact); kept package names (`name = "git-mcp"`), `src/<name>_mcp/` dirs, and `oh-my-bmad-data/<name>-mcp/` data subtrees unchanged; PRD already correct, untouched | `architecture.md` (tree block + Epics 15–19 wiring); `epics.md` (Epic 15–19 goals + scaffold scopes) |
| ADR-0009:34 stale "Epic 17 (υ `build`/`verification` ...)" | Renamed to `verification` (match arch/epics) | `docs/adr/0009-phase-3-gate.md:34` |
| ADR-0009 ship-blocker pointer "§7 (to be promoted...)" | Noted the promotion happened — `epics.md` now carries the canonical ship-blocker checklist | `docs/adr/0009-phase-3-gate.md:51` |

**Post-fix verification:** grep confirms **zero** remaining `mcp-servers/<name>-mcp/` DIRECTORY refs in `architecture.md` + `epics.md` (package names `git-mcp` etc. retained); the only remaining "server image" mentions in the four reworded spots are the corrected text that explicitly negates per-server images ("no per-server image"); recipe step 4 reads the corrected two-location wording.

---

## Step 7 — Deferred-work dispositions (12-3c, 11.3.3)

The scoping brief flagged the Epic-11/12 follow-up backlog (incl. 12-3c) for explicit disposition. Recorded in `docs/adr/0009-phase-3-gate.md` §"Deferred-work dispositions":

- **12-3c (budget-override new-ceiling enforcement, FR68).** **Already merged / done** — landed in `main` at commit `c07694e` (`fix(epic-12.3c): critic-lane review fixes + mark 12-3c done`), preceded by `ece88a2` (`feat(epic-12.3c): ... FR68 — Option A + persist`). **No Phase-3 disposition needed** — it is closed Phase-2 work, not Phase-3 backlog.
- **11.3.3 (deferred nightly-red root cause, carried from Story 11.3.2).** **Disposition: folded into Epic 14.** The mutation-gate/nightly work (Stories 14.2/14.3) requires a green nightly to be meaningful (a red nightly would mask mutation-score regressions). **Epic 14 must confirm the nightly is green before its gate passes** — added to the Epic-14 acceptance gate in `epics.md` and to ADR-0009's disposition block. Resolving 11.3.3 is therefore a precondition of the Epic-14 gate, not a standing-deferred item.

**Disposition assessment:** PASS — both items explicitly dispositioned.

---

## Step 8 — Cross-amendment alignment matrix

| Claim | PRD | Architecture | Epics | ADR |
|---|---|---|---|---|
| 6 epics in Phase 3 (Epics 14–19) | ✅ | ✅ per-epic wiring | ✅ Epic summary table | ✅ ADR-0009 §Decision 2 |
| Servers ship as wheels in the signed base image (no per-server image / matrix row) | ✅ NFR-S12 | ✅ P3-I3 + recipe step 7 | ✅ ship-blocker P3-I3 | ✅ ADR-0010 §Decision 7 |
| Every MCP tool declares a tier; untiered = build-time failure (gate built in Epic 15) | ✅ implicit (NFR-S12 authz) | ✅ P3-I1 (gate noted Epic 15) | ✅ Story 15.2a + ship-blocker | ✅ ADR-0010 §Decision 3 |
| Every emitted event carries `trace_id` (gate built in Epic 14) | ✅ NFR-O7 (preserved) | ✅ recipe step 4 | ✅ Story 14.5 + ship-blocker | ✅ ADR-0010 §Decision 4 |
| Event types registered in two locations (payloads.py + service-side register) | — | ✅ recipe step 4 | ✅ the five `*.4` stories | ✅ ADR-0010 §Decision 5 |
| Store-owning servers use isolated files (P3-I2) | ✅ FR75/FR76 | ✅ P3-I2 | ✅ Epic 18/19 + ship-blocker | ✅ ADR-0011/0012 |
| Fleet separability S-5…S-9 (toggle spawn config) | ✅ NFR-M8 | ✅ recipe step 8 | ✅ per-server S-entry | ✅ ADR-0010 §Decision 8 |
| Directory `mcp-servers/<name>/`, package `<name>-mcp` | ✅ FR72–76 | ✅ (fixed this pass) | ✅ (fixed this pass) | — |
| 12-3c done / 11.3.3 folded into Epic 14 | — | — | ✅ Epic-14 gate (nightly green) | ✅ ADR-0009 dispositions |

**Cross-amendment alignment:** PASS. Every load-bearing claim appears consistently across the amendments after this pass; the three drift hazards the critic flagged (gate non-existence, registration mis-description, packaging/directory wording) are resolved.

---

## Step 9 — Gap analysis

| Gap | Severity (pre-fix) | Resolution |
|---|---|---|
| P3-I1 tier-declaration gate referenced but unbuilt / unowned | MAJOR | FIXED — Story 15.2a builds `scripts/check_tier_declarations.py` |
| `check_trace_id_required.py` referenced but unbuilt (Phase-2 carryover) | MAJOR | FIXED — Story 14.5 builds it; closes Story-9.7 deferral |
| Event-type registration mis-described (circular-import trap) | MAJOR | FIXED — two-location wording in arch recipe + the five `*.4` stories |
| Per-server "image" packaging language | MINOR | FIXED — wheels-in-base-image transitive model |
| Directory naming drift (`mcp-servers/<name>-mcp/`) | MINOR | FIXED — aligned to `mcp-servers/<name>/`; package names retained |
| ADR-0009 stale Epic-17 name + un-promoted ship-blocker pointer | MINOR | FIXED |
| 12-3c / 11.3.3 disposition missing | GAP | FIXED — 12-3c done (no action); 11.3.3 folded into Epic-14 nightly-green gate |

**No HIGH or CRITICAL gaps remain.** All three MAJOR findings, the minor fixes, and the two dispositions are applied. Phase 3 is **ready to implement**.

---

## Step 10 — Verdict

> **Verdict: READY.**
>
> All must-fix items from the Phase-3 adversarial critic review (verdict READY-WITH-FIXES) were applied 2026-06-03. The three MAJOR findings (P3-I1 gate now built in Epic 15 / Story 15.2a; `check_trace_id_required.py` now built in Epic 14 / Story 14.5; event-type registration corrected to the two-location circular-import-safe model), the minor fixes (packaging language, directory naming, ADR-0009 stale refs), and the deferred-work dispositions (12-3c done; 11.3.3 folded into the Epic-14 nightly-green gate) are all in place.
>
> Phase 3 planning artifacts (PRD amendment + architecture amendment + epics decomposition + ADR-0009 Phase-3 gate + ADR-0010/0011/0012) are mutually aligned, fully additive relative to Phase 1+2, and decomposed across Epics 14–19 with **100% coverage of FR72–FR77 + NFR-O11/M8/S12 (9/9 PASS)** and a clean dependency order (Epic 14 first; Epic 15 establishes the recipe; Epics 16–19 parallelize after).
>
> **The gate ADRs (0009–0012) may now be moved to `accepted`.** The next BMad workflow step is **`bmad-sprint-planning`** to populate `sprint-status.yaml` with the Phase-3 stories (Epics 14–19) and increment `current_phase` to `3` at ADR-0009 acceptance.

### Recommendations for sprint planning

1. **Open Epic 14 first.** Pure verification/CI, zero feature surface; lands the digest-only deploy gate, the mutation harness, **and the two new AST gates** (`check_trace_id_required.py` Story 14.5; the tier-declaration gate is Epic 15's Story 15.2a) before any server ships.
2. **Confirm the nightly is green as part of the Epic-14 gate** (11.3.3 disposition) — a red nightly masks mutation-score regressions.
3. **Epic 15 is the recipe-establishing epic** — its review checklist (incl. the Story-15.2a tier gate) becomes the per-server gate reused by Epics 16–19.
4. **ADR-0010 must be `accepted` before Story 15.2 merges**; ADR-0011 before Epic 19's first story; ADR-0012 before Epic 18's first story.
5. **Epics 16–19 parallelize after Epic 15**; recommended serial order github → verification → memory → artifact (operator-visibility + reuse of the github scoped-credential work).

— *Report by R2d2, 2026-06-03 (Phase-3 critic-review fixes applied; verdict upgraded READY-WITH-FIXES → READY).*
