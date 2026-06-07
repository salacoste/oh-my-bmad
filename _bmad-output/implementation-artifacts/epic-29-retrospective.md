# Epic 29 Retrospective — Per-Runtime Budget Tracking + Fleet Integration

**Date:** 2026-06-07
**Phase:** 5 (Multi-Runtime Plane)
**Epic:** 29 — Budget Tracking + Fleet Integration
**Stories:** 29.1 (per-runtime budget tracking), 29.2 (handoff rejection), 29.3 (fleet smoke), 29.4 (metrics label)
**Status:** DONE

## Summary

Epic 29 closes the Phase 5 multi-runtime plane by wiring budget tracking, fleet-level integration testing, and per-runtime metrics. All 4 stories shipped in a single commit (3247469) with 67 tests passing.

## What Went Well

- **Schema evolution discipline held.** `TaskCompletedPayload` bumped to 1.3.0 (additive `tokens_consumed_by_runtime` field) and budget payloads bumped to 1.2.0 (additive `runtime` field). All registrations valid, zero schema migration pain.
- **Fleet smoke test with graceful skip.** `test_codex_fleet_smoke.py` exercises the full MCP fleet (task-registry, git-mcp, verification-mcp) with Codex runtime, but gracefully skips when the codex binary or `OPENAI_API_KEY` are unavailable. No CI dependency on external tools.
- **Per-runtime metrics label is bounded.** The `runtime` label uses a bounded enum (`claude-code`, `codex`, `unknown`) — cardinality baseline unchanged at 66. Cleanup handler iterates over all runtime variants to prevent label-leak.
- **Single-commit delivery.** All 4 stories landed atomically, avoiding intermediate broken states.

## What Could Improve

- **No live Codex binary validation.** The fleet smoke test skips when codex is absent. The first real Codex execution will be the real validation — the adapter is structurally sound but runtime-untested with a real `codex exec` subprocess.
- **Budget accounting uses heterogeneous token models.** Claude Code uses `num_turns` as proxy; Codex uses `input_tokens + output_tokens`. The budget comparison across runtimes is approximate, not exact. Documented in `_accumulate_runtime_tokens` but worth noting for operators.

## Lessons Learned

| ID | Lesson | Applicability |
|----|--------|---------------|
| L1 | Bounded enum labels for multi-tenant dimensions prevent cardinality explosions at registration time, not just at cleanup time. | All future multi-value dimensions (runtimes, regions, etc.) |
| L2 | Graceful-skip integration tests validate structure without external dependencies — ship structure first, validate runtime later. | Fleet smoke tests for optional runtimes |
| L3 | Additive schema bumps (1.x.0) are painless when every field is optional with a sensible default. | All event schema evolution |

## Carry-Forward Items

None — Epic 29 is the final epic in Phase 5. No deferred work from this epic.
