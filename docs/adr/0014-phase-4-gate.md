---
id: ADR-0014
status: accepted
date: 2026-06-05
supersedes: null
---

# ADR-0014: Phase 4 gate — formally opens `phase: 4` for `main`-branch merges

## Status

**Accepted** — 2026-06-05. All acceptance criteria verified: PRD amendment authored, architecture amendment authored, ADR-0013 accepted, epics decomposed (Epic 20-22, 17 stories), implementation readiness report verdict READY. Mirrors the ADR-0003 (Phase-2 gate) and ADR-0009 (Phase-3 gate) lifecycle.

## Context

Phase 3 of oh-my-bmad shipped on 2026-06-04 as five MCP fleet servers (Epics 14–19 `done` — `git`, `github`, `verification`, `memory`, `artifact`; see [`epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"Phase 3 Ship-Blocker Checklist", gate **PASS**). ADR-0010 (MCP-server-authoring recipe) is proven across all five servers. The Phase-3 retrospective confirms browser automation as the next priority. G-SEC-2 (both halves) is fully closed. 3,739 tests passing, all CI gates green.

Phase 1's architecture explicitly deferred browser-automation to a later phase; ADR-0009 §3 deferred it to Phase 4. The following Phase-4 planning artifacts were produced via the BMad workflow (2026-06-05):

- [`_bmad-output/planning-artifacts/phase-4-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-prd-amendment.md) — browser-automation plane FR78–FR88 + NFR-B1–B5, NFR-O12, NFR-M9, NFR-S13, NFR-R9 + Phase 4 ship-blocker checklist + sequencing (Epics 20–25).
- [`_bmad-output/planning-artifacts/phase-4-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-architecture-amendment.md) — three new invariants (P4-I1 ephemerality, P4-I2 Tier-3 JS execution, P4-I3 Docker sandboxing) + Browser Worker archetype (4th archetype) + tool-surface tier mapping + `@playwright/mcp` transport integration + ADR-0010 recipe application steps 1–8.

The project-context Cat-6 workflow rule requires every epic/story carry `phase: N`, and no `phase: N` work merges to `main` until a Phase-N gate ADR is accepted and `current_phase` in `sprint-status.yaml` increments. This ADR is that gate for Phase 4.

## Decision

1. **Phase 4 will be formally open for `main`-branch merges once this ADR is accepted.** Stories carrying `phase: 4` may then transition through the normal workflow and merge via the standard PR gate. `sprint-status.yaml` increments to `current_phase: 4` at acceptance.

2. **The Phase 4 baseline scope is the browser automation plane** (operator convergence D5–D8):
   - **Epic 20 (ω browser server scaffold)** — FR78, S-10, NFR-M9 — lands **first** (ADR-0010 recipe + separability; mirrors the Epic-8/Epic-14 warm-up pattern).
   - **Epic 21 (ω-2 navigation tools)** — FR79, FR86 — second; Tier-1 read-like tools establish the Playwright subprocess integration and event emission pattern.
   - **Epic 22 (ω-3/ω-4 interaction + screenshot tools)** — FR80, FR81, NFR-B3 — third; Tier-2 tools + artifact-store integration.
   - **Epic 23 (ω-5/ω-6 JS execution + tab management)** — FR82, FR83, NFR-B5 — fourth; Tier-3 `browser_evaluate` with approval gating — most security-sensitive, lands after Tier-1/2 patterns are proven.
   - **Epic 24 (ω-7/ω-8 session isolation + origin control)** — FR84, FR85, NFR-B2 — fifth; policy layer composing with all tools above.
   - **Epic 25 (ω-10 container sandboxing)** — FR87, NFR-S13, NFR-R9 — sixth; can parallelize with Epics 21–24 but must land before Phase 4 close.

   Items rejected from Phase 4 scope (deferred per the convergence):
   - **D7** — `browser-harness` (raw CDP) as an alternative transport. Remains a future Tier-3 escape hatch.
   - Remote-MCP transport (HTTP/SSE/streamable) — carried from Phase 2 D2.
   - Second CLI agent (Codex / Gemini / GLM) — carried from Phase 3 D3, deferred to Phase 5.
   - Visual regression testing / cross-browser testing / mobile device emulation / file upload-download / network interception.

3. **Forward-referenced ADRs** are staked out; each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges:
   - **ADR-0013** — Playwright MCP as browser transport (`--caps` dual-enforcement, `--isolated` ephemerality, Docker container subprocess, origin control) — gates Epic 20.
   - **ADR-0014** — this document — Phase 4 gate.

4. **Phase-1+2+3 invariants are non-negotiable in Phase 4.** Every browser tool preserves FR26 single-writer, stdio-only MCP transport, event-only telemetry, `trace_id` propagation, tier-enforced authz, supply-chain triumvirate + fail-closed license gate + child-env allowlist, and the separability blank-command toggle pattern. A Phase-4 PR violating one is rejected at review regardless of merits.

5. **Three new Phase-4 invariants are non-negotiable:**
   - **P4-I1:** Browser sessions are ephemeral — no state leaks between tasks (`--isolated`, per-task respawn, suppressed `storage` capability).
   - **P4-I2:** `browser_evaluate` is Tier-3 with `check_tier_with_approval` — the same gate as `git push` (RCE-equivalent in browser context).
   - **P4-I3:** The Playwright subprocess runs inside a Docker container, never bare-metal on the host (seccomp, user-namespace isolation, no host network, resource limits).

6. **Phase-4 ship criterion** is the green Phase-4 Ship-Blocker Checklist (14 items in [`phase-4-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-prd-amendment.md) §"Phase 4 Ship-Blocker Checklist"). Phase 4 has not shipped until every gate there is green.

7. **Incremental release.** Each browser epic is independently mergeable (the browser server is an optional stdio member toggled by `WORKER_BROWSER_COMMAND`), so Phase 4 releases incrementally rather than big-bang.

## Acceptance criteria

This ADR transitions `proposed → accepted` only when all criteria are met:

- [ ] Phase-4 brainstorming convergence recorded (D5–D8 in the PRD amendment).
- [ ] PRD extension authored (`phase-4-prd-amendment.md` — FR78–FR88 + NFR-B1–B5, NFR-O12, NFR-M9, NFR-S13, NFR-R9).
- [ ] Phase-4 **architecture amendment** authored (`phase-4-architecture-amendment.md` — P4-I1–I3 + Browser Worker archetype + tool-surface tier mapping + ADR-0010 recipe steps 1–8 + fleet integration).
- [ ] ADR-0013 (`docs/adr/0013-playwright-mcp-transport.md`) authored and `status: accepted` — formally resolves the browser-automation surface deferred in ADR-0009.
- [ ] Phase-4 **epics/stories** decomposed (`bmad-create-epics-and-stories` — Epics 20–25 + stories + per-epic acceptance gates + Phase-4 ship-blocker checklist promoted into `epics.md`).
- [ ] Phase-4 **implementation-readiness report** confirms the PRD + architecture + epics are aligned and the deferred-work backlog is reviewed (verdict **READY**).

### Prerequisites (verified before acceptance)

- [ ] G-SEC-2 fully closed (both halves — confirmed: 3,739 tests passing, CI green).
- [ ] ADR-0013 accepted (Playwright MCP transport decision).
- [ ] Architecture amendment accepted (P4-I1–I3, Browser Worker archetype, fleet integration).

## Consequences

- **Implementation order is Epic 20 first** (scaffold + separability) — the ADR-0010 recipe setup de-risks the fleet integration before any browser-specific logic ships. Mirrors the Epic-8 (supply-chain) and Epic-14 (digest-deprecation) warm-up patterns.
- **The PR-required-checks list expands** as each browser epic ships its CI gates (separability S-10, Tier-3-denial negative tests, P4-I1 ephemerality negative test, P4-I3 container-spawn assertion, `browser.*` event cardinality, Playwright image digest pinning).
- **Phase 1+2+3 stay operational throughout** — Phase 4 is fully additive (new optional stdio member toggled by blank-command pattern).
- **`main` carries mixed `phase: 3`-`done` and `phase: 4`-`in-progress` work**; the `phase:` label is the canonical distinguishing field. No long-lived phase branch.
- **Phase 5+ scope is not pre-decided** — second CLI agent (Phase 5) and remaining deferred items each require their own gate ADR + planning chain.
- **A retrospective is required at every Phase-4 epic boundary** (project-context Cat 6), landing in `_bmad-output/implementation-artifacts/epic-<n>-retro-<date>.md`.

## Alternatives considered

- **Broader scope including `browser-harness` (raw CDP).** Rejected (D7) — `@playwright/mcp` provides structured accessibility-tree output ideal for LLM consumption; raw CDP is an unnecessary complexity for Phase 4 and remains a future Tier-3 escape hatch.
- **Skip container sandboxing (run Playwright bare-metal on host).** Rejected — a browser subprocess with `file://` access and arbitrary JS execution is too privileged to run unsandboxed. Docker's default seccomp + user-namespace isolation is a minimum blast-radius boundary (P4-I3).
- **Include remote-MCP transport for browser server.** Rejected — no concrete remote-browser use case has emerged; the browser server stays stdio-only like the rest of the fleet. Remote transport stays deferred until justified.
- **Ship Phase 4 as a long-lived feature branch.** Rejected (same reasoning as ADR-0003) — `main` is the only long-lived branch; long-lived branches accumulate merge conflicts proportionally to phase duration.

## Linked artifacts

- [`phase-4-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-prd-amendment.md) — FR78–FR88 + NFRs + ship-blocker checklist.
- [`phase-4-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-architecture-amendment.md) — P4-I1–I3 + Browser Worker archetype + ADR-0010 recipe application.
- ADR-0013 — Playwright MCP transport (to be authored; gates Epic 20).
- ADR-0010 — MCP-server-authoring recipe (reused for browser-mcp).

— *R2d2, 2026-06-05 (proposed; via the BMad Phase-4 planning chain).*
