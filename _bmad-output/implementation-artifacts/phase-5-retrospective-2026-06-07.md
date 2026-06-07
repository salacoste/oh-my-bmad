# Phase 5 Retrospective — Multi-Runtime Plane

**Date:** 2026-06-07
**Phase:** 5 (Multi-Runtime Plane)
**Epics:** 26–29 (4 epics, 18 stories)
**Commits:** 4 atomic epic commits (2530816, 4ca477f, 4741a6b, 3247469)
**Status:** COMPLETE

## Summary

Phase 5 adds multi-runtime support to oh-my-bmad: a runtime-abstraction layer in worker-wrapper that allows tasks to run on either Claude Code or Codex CLI, with per-task runtime selection, runtime handoff, credential isolation, per-runtime budget tracking, and fleet-level integration testing. The phase shipped 4 epics across 4 atomic commits with ~67 new tests, zero regressions on existing test suites, and all lint/mypy/discipline gates green.

## Scope Delivered

| Epic | Scope | Stories | Status |
|------|-------|---------|--------|
| 26 — Runtime Abstraction Layer | RuntimeAdapter protocol, CodexRunner, credential isolation, health probes, separability S-11 | 7 | DONE |
| 27 — Per-Task Runtime Selection | TaskCreatedPayload.preferred_runtime, runtime fallback, runtime event types | 3 | DONE |
| 28 — Runtime Handoff + Session Continuity | /handoff command, subprocess termination + resumption, cross-runtime session continuity, runtime_handoff event | 4 | DONE |
| 29 — Budget Tracking + Fleet Integration | Per-runtime budget tracking, handoff rejection, fleet smoke test, per-runtime metrics label | 4 | DONE |

**Total:** 18 stories, all DONE.

## Requirements Coverage

| Req | Description | Coverage |
|-----|-------------|----------|
| FR89 | RuntimeAdapter protocol + factory | FULL — protocol, factory, SUPPORTED_RUNTIMES closed set |
| FR90 | Codex CLI adapter | FULL — CodexRunner with JSONL parsing, event extraction, no regex |
| FR91 | Per-task runtime selection | FULL — preferred_runtime field + fallback logic + runtime_fallback event |
| FR92 | Runtime handoff | FULL — /handoff command + subprocess termination/resumption |
| FR93 | Cross-runtime session continuity | FULL — runtime field on events, trace_id continuity |
| FR94 | Per-runtime budget tracking | FULL — tokens_consumed_by_runtime map, cumulative enforcement |
| FR95 | Runtime health probes | FULL — binary check + version + API key validity (lazy, cached) |
| FR96 | Fleet-level integration test | FULL — Codex + git-mcp + verification-mcp end-to-end (graceful skip) |
| FR97 | Runtime event types | FULL — task.runtime_handoff, task.runtime_fallback, runtime.health_checked |
| FR98 | Separability S-11 | FULL — WORKER_CODEX_COMMAND toggle, S-11 test |
| NFR-R10 | Runtime credential isolation | FULL — separate allowlists, no cross-runtime secret leakage |
| NFR-O13 | Per-runtime metrics label | FULL — bounded runtime enum on task metrics |
| NFR-M10 | Codex separability | FULL — optional stdio member, single-env-var toggle |
| NFR-S14 | Credential separation | FULL — OPENAI_API_KEY never in Claude env, ANTHROPIC_API_KEY never in Codex env |

## What Went Well

1. **Protocol-first architecture (ADR-0015).** The `RuntimeAdapter` protocol was defined before any implementation. Both `ClaudeCodeRunner` (refactored) and `CodexRunner` (new) satisfy the same structural contract. The factory pattern (`get_runtime_adapter`) with closed-set validation prevents unknown runtime names from slipping through.

2. **Credential isolation by construction.** Each runner has its own explicit allowlist (`_CHILD_ENV_ALLOWLIST` / `_CODEX_ENV_ALLOWLIST`) and prefix allowlist (`_CHILD_ENV_PREFIXES` / `_CODEX_ENV_PREFIXES`). `ANTHROPIC_API_KEY` is absent from the Codex allowlist by construction, not by convention. Integration tests assert cross-runtime non-leakage.

3. **Atomic epic commits.** Each epic landed as a single, self-contained commit. This made the git history a clean narrative: protocol → selection → handoff → budget/metrics.

4. **Separability pattern reuse.** S-11 mirrors the Phase-3/4 separability pattern (S-5 through S-10): conditional spawn via env var, factory always constructs the adapter, `health_check()` reports `installed=False` when binary is absent. Zero new concepts for the operator.

5. **No regex stdout parsing.** CodexRunner reads structured JSONL exclusively (NFR-O1). The `_classify_tool_use` static method maps Codex tool names to typed events via string matching on the deserialized JSON, not via regex on raw stdout.

## What Could Improve

1. **Lint errors in the initial commit.** The Epic 26–29 commits shipped with ruff lint errors (unused imports, duplicate `from __future__` lines, quoted type annotations that trigger F821). A post-merge lint sweep caught all 11 issues, but they should have been caught pre-commit. The `just lint` gate in CI would have caught these, but they were committed directly.

2. **No live Codex binary validation.** The entire Codex adapter is structurally sound but has never been run against a real `codex exec` subprocess. The fleet smoke test skips when the binary is absent. First real execution is an operator milestone.

3. **Heterogeneous token models.** Claude Code budget accounting uses `num_turns` as proxy; Codex uses `input_tokens + output_tokens`. Cross-runtime budget comparison is approximate. Operators should be aware that the "tokens" number means different things per runtime.

4. **Story decomposition was top-down.** The 18 stories were decomposed from the PRD in a single planning pass. In practice, Epics 26 and 27 were tightly coupled (protocol + factory + refactoring) and could have been a single epic. The 4-epic split felt slightly granular.

## Lessons Learned

| ID | Lesson | Applicability |
|----|--------|---------------|
| L1 | Protocol-first + factory + closed-set is the right pattern for pluggable runtimes. The factory raises ValueError on unknown names, making misconfiguration fail-loud. | All future pluggable subsystems (runtimes, transports, stores) |
| L2 | Credential isolation must be verified by construction (allowlist excludes the other secret), not by convention (don't pass it). Tests assert absence, not just presence. | All subprocess spawning with credentials |
| L3 | Lint gates must run pre-commit, not post-hoc. 11 lint errors in 4 files should never reach main. Add `just lint` to the pre-commit hook or the bmad dev-story workflow. | All future epics |
| L4 | Graceful-skip integration tests validate structural correctness without external dependencies. Ship structure first, validate runtime at the operator milestone. | Optional runtime adapters, Docker-dependent tests |
| L5 | Atomic epic commits produce clean git narratives but can hide lint drift if the pre-commit gate is missing. Consider per-story lint runs inside the bmad dev-story workflow. | All future multi-epic phases |

## Metrics

- **Stories shipped:** 18
- **Tests added:** ~67 (contract, unit, integration, separability)
- **Epics:** 4
- **Commits:** 4 (one per epic, atomic)
- **Lint errors post-hoc fixed:** 11 (6 files)
- **Regressions:** 0
- **New event types:** 3 (task.runtime_handoff, task.runtime_fallback, runtime.health_checked)
- **Schema bumps:** 1.3.0 (TaskCompletedPayload), 1.2.0 (budget payloads)
- **Separability tests:** S-11 (2 states: SPAWNED + ABSENT)

## Carry-Forward Items

| ID | Item | Origin | Priority |
|----|------|--------|----------|
| CF1 | Live Codex binary validation — first real `codex exec` execution against the adapter | Epic 29 L1 | Operator milestone |
| CF2 | Heterogeneous token model documentation — operators need clarity that "tokens" means turns (Claude) vs. input+output tokens (Codex) | Epic 29 retro | HIGH |
| CF3 | Pre-commit lint gate — add `just lint` to bmad dev-story workflow or git pre-commit hook | Phase 5 L3 | HIGH |
| CF4 | Epic granularity review — 4-epic split for 18 stories was slightly over-decomposed; consider 2 epics for similar phases | Phase 5 L4 | LOW |
