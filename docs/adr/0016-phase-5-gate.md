---
id: ADR-0016
status: accepted
date: 2026-06-07
supersedes: null
---

# ADR-0016: Phase 5 gate — formally opens `phase: 5` for `main`-branch merges

## Status

**Accepted** — 2026-06-07. All acceptance criteria verified: PRD amendment authored, architecture amendment authored, ADR-0015 accepted, epics decomposed (Epic 26-29), implementation readiness report verdict CONDITIONALLY-READY with blockers resolved. Mirrors the ADR-0009 (Phase-3 gate) and ADR-0014 (Phase-4 gate) lifecycle.

## Context

Phase 4 of oh-my-bmad shipped on 2026-06-06 as the browser automation plane (Epics 20–22 `done` — 15 browser tools, 6 event types, 3 tiers; see [`epics.md`](../../_bmad-output/planning-artifacts/epics.md) §"Phase 4 Ship-Blocker Checklist", gate **PASS**). ADR-0013 (Playwright MCP transport) and ADR-0014 (Phase-4 gate) are both accepted. G-SEC-2 is fully closed (both halves). 3,662+ tests passing, all CI gates green. The Phase-4 retrospective confirms multi-runtime support as the next priority, with Codex CLI convergence identified as the concrete second runtime.

Phase 1's architecture explicitly deferred a second CLI agent to a later phase; ADR-0014 §2 deferred it to Phase 5. The following Phase-5 planning artifacts were produced via the BMad workflow (2026-06-06):

- [`_bmad-output/planning-artifacts/phase-5-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-prd-amendment.md) — multi-runtime plane FR89–FR98 + NFR-R10, NFR-O13, NFR-M10, NFR-S14 + Phase 5 ship-blocker checklist + sequencing (Epics 26–29).
- [`_bmad-output/planning-artifacts/phase-5-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-architecture-amendment.md) — three new invariants (P5-I1 credential isolation, P5-I2 trace_id continuity, P5-I3 budget per-runtime) + Multi-Runtime Worker archetype (5th archetype) + `RuntimeAdapter` protocol + factory function + credential isolation architecture + ADR-0010 recipe extension (step 9).

The implementation-readiness report ([`implementation-readiness-report-phase-5-2026-06-07.md`](../../_bmad-output/planning-artifacts/implementation-readiness-report-phase-5-2026-06-07.md), verdict **CONDITIONALLY-READY**) confirmed that the codebase is architecturally ready and the architecture's `RuntimeAdapter` protocol maps cleanly to the existing `ClaudeCodeRunner` structure. Its five blocking conditions have been addressed: epic numbering resolved (26–29 per PRD continuity), ADR-0015 authored and accepted, ADR-0016 authored (this document), epics decomposed, and Codex CLI binary pinning plan established.

The project-context Cat-6 workflow rule requires every epic/story carry `phase: N`, and no `phase: N` work merges to `main` until a Phase-N gate ADR is accepted and `current_phase` in `sprint-status.yaml` increments. This ADR is that gate for Phase 5.

## Decision

1. **Phase 5 will be formally open for `main`-branch merges once this ADR is accepted.** Stories carrying `phase: 5` may then transition through the normal workflow and merge via the standard PR gate. `sprint-status.yaml` increments to `current_phase: 5` at acceptance.

2. **The Phase 5 baseline scope is the multi-runtime plane** (operator convergence D1–D5):
   - **Epic 26 (runtime abstraction + Codex adapter + S-11)** — FR89, FR90, FR95, FR98, NFR-M10 — lands **first** (runtime dispatch table + Codex runner + separability; the runtime abstraction must land before every later feature is born under it).
   - **Epic 27 (per-task runtime selection)** — FR91, FR97 — second; `TaskCreatedPayload` extension + dispatch wiring + health-check fallback. Depends on Epic 26's abstraction.
   - **Epic 28 (runtime handoff + session continuity)** — FR92, FR93, P5-I2 — third; the most complex epic: subprocess termination + resumption prompt + event continuity + trace_id preservation across handoffs. Depends on Epic 27's per-task selection.
   - **Epic 29 (budget per-runtime + fleet smoke test)** — FR94, FR96, NFR-R10, NFR-S14 — fourth; per-runtime budget accounting + end-to-end fleet integration test exercising Codex + git-mcp + verification-mcp + event spine. Can partially parallelize with Epic 28 (budget tracking is independent of handoff).

   Items rejected from Phase 5 scope (deferred per the convergence):
   - **Gemini/GLM adapters** — deferred to Phase 6+. The runtime-abstraction layer accommodates them, but no implementation in Phase 5.
   - **Remote MCP transport** (HTTP/SSE/streamable) — carried from Phase 2 D2; stays deferred (Phase 6).
   - **Multi-task parallelism** — running multiple tasks concurrently on different runtimes. Deferred to Phase 6.
   - **Postgres upgrade, Web dashboard, Docker-in-Docker CI, runtime-specific MCP server fleets, cross-runtime tool result sharing.**

3. **Forward-referenced ADRs** are staked out; each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges:
   - **ADR-0015** — Multi-runtime adapter protocol (`RuntimeAdapter` protocol, factory function, credential isolation architecture, per-runtime allowlists, output-parsing contract, kill-semantics contract) — gates Epic 26.
   - **ADR-0016** — this document — Phase 5 gate.

4. **Phase-1+2+3+4 invariants are non-negotiable in Phase 5.** Every runtime adapter preserves FR26 single-writer, stdio-only MCP transport, event-only telemetry, `trace_id` propagation, tier-enforced authz, supply-chain triumvirate + fail-closed license gate + child-env allowlist, budget-supervisor discipline, and the separability blank-command toggle pattern. A Phase-5 PR violating one is rejected at review regardless of merits.

5. **Three new Phase-5 invariants are non-negotiable:**
   - **P5-I1:** Runtime credential isolation — each runtime's API key is injected into its own subprocess env only. `ANTHROPIC_API_KEY` appears only in `ClaudeCodeRunner`'s allowlist; `OPENAI_API_KEY` appears only in `CodexRunner`'s allowlist. Neither key is sourced from the parent `os.environ`. The CI-gate is a negative test asserting cross-runtime key absence.
   - **P5-I2:** Trace_id continuity across handoffs — the same `trace_id` spans the entire task lifecycle regardless of runtime changes. All events from both runtimes carry the same `trace_id`. The `task.runtime_handoff` event links the two runtime segments under one trace.
   - **P5-I3:** Budget accounting per-runtime — token consumption is tracked separately per runtime; the budget limit applies to the cumulative total. Handoff is rejected if the cumulative budget is already exceeded.

6. **Phase-5 ship criterion** is the green Phase-5 Ship-Blocker Checklist (14 items in [`phase-5-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-prd-amendment.md) §"Phase 5 Ship-Blocker Checklist"). Phase 5 has not shipped until every gate there is green.

7. **Incremental release.** The runtime abstraction is an internal worker-wrapper refactoring — no new MCP fleet member. The Codex runner is an optional stdio subprocess toggled by `WORKER_CODEX_COMMAND` (separability S-11, blank-command pattern), so Phase 5 releases incrementally rather than big-bang. Existing deployments without `WORKER_RUNTIME` set continue using Claude Code without code changes.

## Acceptance criteria

This ADR transitions `proposed → accepted` only when all criteria are met:

- [x] Phase-5 brainstorming convergence recorded (D1–D5 in the PRD amendment).
- [x] PRD extension authored (`phase-5-prd-amendment.md` — FR89–FR98 + NFR-R10, NFR-O13, NFR-M10, NFR-S14).
- [x] Phase-5 **architecture amendment** authored (`phase-5-architecture-amendment.md` — P5-I1–I3 + Multi-Runtime Worker archetype + `RuntimeAdapter` protocol + factory function + credential isolation architecture + ADR-0010 recipe extension step 9).
- [x] ADR-0015 (`docs/adr/0015-multi-runtime-adapter.md`) authored and `status: accepted` — formally defines the runtime adapter protocol, factory function, and credential isolation architecture.
- [x] Phase-5 **epics/stories** decomposed (Epic 26–29 + stories + per-epic acceptance gates + Phase-5 ship-blocker checklist promoted into `epics.md`). Epic numbering uses 26–29 (continuity from Phase 4's Epics 20–22), not the architecture amendment's 23–25.
- [x] Phase-5 **implementation-readiness report** confirms the PRD + architecture + epics are aligned, the deferred-work backlog is reviewed, and the codebase is architecturally ready (verdict **CONDITIONALLY-READY** with blockers resolved).
- [x] Deferred work reviewed — no deferred items block Phase 5. The fleet-level integration test gap is explicitly addressed by FR96/Epic 29.
- [x] Codex CLI binary pinning plan established — `codex` binary will be pinned in `Dockerfile.base` with verified checksum (same supply-chain discipline as Playwright Docker image pinning per Phase 4), landing before FR90 implementation.

## Consequences

- **Implementation order is Epic 26 first** (runtime abstraction + Codex adapter + S-11) — the abstraction layer must land before every later feature is born under it. Mirrors the Epic-8 (supply-chain) and Epic-14 (digest-deprecation) warm-up patterns.
- **The PR-required-checks list expands** as each Phase-5 epic ships its CI gates (separability S-11, P5-I1 credential isolation negative test, P5-I2 output-parsing contract test, P5-I3 kill-semantics contract test, `runtime` label cardinality ratchet, factory completeness test, Codex binary digest pinning).
- **Phase 1–4 stay operational throughout** — Phase 5 is fully additive (internal worker-wrapper refactoring; no new MCP fleet member; existing Claude Code path unchanged).
- **`main` carries mixed `phase: 4`-`done` and `phase: 5`-`in-progress` work**; the `phase:` label is the canonical distinguishing field. No long-lived phase branch.
- **Phase 6+ scope is not pre-decided** — Gemini/GLM adapters, remote MCP transport, and multi-task parallelism each require their own gate ADR + planning chain.
- **A retrospective is required at every Phase-5 epic boundary** (project-context Cat 6), landing in `_bmad-output/implementation-artifacts/epic-<n>-retro-<date>.md`.

## Alternatives considered

- **Broader scope including Gemini/GLM adapters.** Rejected (D4) — the runtime-abstraction layer is designed to accommodate additional runtimes, but implementing more than one new adapter in Phase 5 would dilute focus. Codex is the concrete second runtime; others follow the same pattern in later phases.
- **Runtime-router microservice.** Rejected (D1) — a settings field + dispatch table is the lightest viable abstraction for 2 runtimes. Adding a microservice is over-engineering (YAGNI). If/when the platform reaches 5+ runtimes, a router can be extracted then.
- **Interactive Codex mode (`codex` REPL).** Rejected (D2) — `codex exec` (non-interactive, single-shot) mirrors the `claude -p` pattern. Interactive mode would require PTY management and is not needed.
- **Docker wrapping for Codex subprocess.** Rejected (D4) — Codex has built-in OS-level sandboxing (Seatbelt/Landlock) stronger than Claude Code's process model. Docker wrapping adds latency without security benefit.
- **Ship Phase 5 as a long-lived feature branch.** Rejected (same reasoning as ADR-0003 and ADR-0014) — `main` is the only long-lived branch; long-lived branches accumulate merge conflicts proportionally to phase duration.

## Linked artifacts

- [`phase-5-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-prd-amendment.md) — FR89–FR98 + NFRs + ship-blocker checklist.
- [`phase-5-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-5-architecture-amendment.md) — P5-I1–I3 + Multi-Runtime Worker archetype + `RuntimeAdapter` protocol + ADR-0010 recipe extension.
- [`implementation-readiness-report-phase-5-2026-06-07.md`](../../_bmad-output/planning-artifacts/implementation-readiness-report-phase-5-2026-06-07.md) — readiness assessment (verdict CONDITIONALLY-READY, blockers resolved).
- ADR-0015 — Multi-runtime adapter protocol.
- ADR-0010 — MCP-server-authoring recipe (extended for runtime adapter step 9).
- ADR-0014 — Phase 4 gate (precedent for this document's structure).

— *R2d2, 2026-06-07 (proposed; via the BMad Phase-5 planning chain).*
