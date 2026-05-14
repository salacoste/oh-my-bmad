# Story 7.5.7: Integration test harness decision

Status: done

## Story

As **a project maintainer**,
I want **a formal decision and implementation for the duplicated integration test harness code across 10+ test files**,
So that **future test authors have a single, documented pattern to follow instead of copy-pasting ~610 lines of boilerplate**.

This is the **third consecutive retrospective** flagging integration test harness duplication (Epic 5 retro → Epic 6 retro → Epic 7 retro). The deferred-work tracker entry from 5.18 (D1) noted "~62 lines duplicated between auto_approval_stub and scripted_worker_stub." The Epic 6 retro action item "Extract integration test harness to conftest" was NOT DONE. The Epic 7 retro escalated: "decide: shared module OR formally accept self-contained design."

This story resolves the decision and implements the chosen approach.

## Acceptance Criteria

1. **AC-1: Decision documented** — An ADR (Architecture Decision Record) at `docs/adr/adr-integration-test-harness.md` records the decision: either (a) extract shared harness to `tests/integration/conftest.py` + `tests/fixtures/conftest.py`, or (b) formally accept self-contained design with documented rationale. The ADR includes context, options considered, decision, and consequences.
2. **AC-2: Implementation matches decision** — If option (a) chosen: extract shared harness helpers to conftest, refactor at least 2 existing test files to use the shared module, verify all tests pass. If option (b) chosen: add documentation annotations to duplicated code explaining the intentional self-contained design, add a section to developer docs explaining the convention.
3. **AC-3: Convention documented** — Regardless of decision, a brief convention section is added to the project's developer documentation stating the pattern to follow for new integration tests.
4. **AC-4: No regressions** — All existing tests pass. `uv run ruff check` clean on modified files.

## Tasks / Subtasks

- [x] **Task 1: Audit duplicated code** (AC: #1)
  - [x] Catalog the three duplication clusters:
    1. **ASGI harness** (`_Harness` + `_build_harness` + `_db_url` + `_seed_tables`): ~200 lines across 4-5 files in `tests/integration/`
    2. **Stub helpers** (`_read_new_lines`, `_connect_mcp`, `_install_signal_handlers`): ~170 lines across 3 files in `tests/fixtures/`
    3. **Docker-compose journey helpers** (`_compose_env`, `_compose_cmd`, `_wait_for_all_healthy`, `_resolve_registry_api_port`): ~240 lines across 3 files in `tests/integration/`
  - [x] For each cluster, assess: how stable is the pattern, how likely to diverge, how many future consumers.
  - [x] Record findings for ADR context section.

- [x] **Task 2: Write ADR** (AC: #1, #3)
  - [x] Create `docs/adr/0002-integration-test-harness.md` with:
    - Context (3-retro history, ~610 duplicated lines, 10 files)
    - Option A: Extract to shared conftest modules
    - Option B: Accept self-contained design (status quo with documentation)
    - Decision and rationale
    - Consequences
  - [x] Add convention reference to project developer docs.

- [x] **Task 3: Implement decision** (AC: #2)
  - [x] Extract Docker-compose journey helpers to `tests/integration/_compose_helpers.py`.
  - [x] Refactor `test_journey_3_recovery.py` and `test_journey_6_stale_blocker.py` to use shared module.
  - [x] Verify ruff check clean and tests pass.

- [x] **Task 4: Run full regression suite** (AC: #4)
  - [x] `uv run ruff check` — all checks passed.
  - [x] `uv run pytest -m "not slow"` — 2179 passed, 20 pre-existing failures (unrelated services), 48 integration tests all pass.

## Dev Notes

### Origin and Context

Three consecutive retrospectives flagged the same issue:

1. **Epic 5 retro** (2026-05-09): "~62 lines duplicated code between scripted_worker_stub and auto_approval_stub — intentional fixture independence; extracting shared code would cross fixture boundaries."
2. **Epic 6 retro** (2026-05-11): "Integration test harness duplication (6.12, 6.13, 6.14) — Nearly identical `_Harness` classes across 3 test files. Scope constraint prevented consolidation. Action: extract to conftest." Action NOT DONE.
3. **Epic 7 retro** (2026-05-13): "Integration test harness duplication (systemic) — deferred across Epic 6 AND Epic 7 retros. This is a systemic issue, not a discipline issue. Action: resolve — decide: shared module OR formally accept self-contained design. This is the third retro flagging it."

### Key Files (exact paths + duplication clusters)

**Cluster 1: ASGI harness** (~200 redundant lines)
| File | Lines | What's duplicated |
|------|-------|-------------------|
| `tests/integration/test_license_scan.py` | 85-197 | `_Harness` class, `_db_url`, `_seed_tables`, LifespanManager + ASGITransport wiring |
| `tests/integration/test_tier3_negative.py` | 120-240 | Same as above |
| `tests/integration/test_decision_interleaving.py` | 145-209 | Same as above |
| `tests/integration/test_command_injection_fuzz.py` | 515-600 | Same as above |
| `tests/idempotency/test_100x_replay.py` | varies | Same pattern, wraps in pytest_asyncio |

**Cluster 2: Stub helpers** (~170 redundant lines)
| File | Lines | What's duplicated |
|------|-------|-------------------|
| `tests/fixtures/auto_approval_stub.py` | 55, 81, 184 | `_read_new_lines`, `_connect_mcp`, `_install_signal_handlers` |
| `tests/fixtures/scripted_worker_stub.py` | 509, 469, 697 | Same functions |
| `tests/fixtures/null_orchestrator.py` | 476 | `_install_signal_handlers` |

**Cluster 3: Docker-compose journey helpers** (~240 redundant lines)
| File | What's duplicated |
|------|-------------------|
| `tests/integration/test_journey_1_overnight.py` | `_compose_env`, `_compose_cmd`, `_wait_for_all_healthy`, `_resolve_registry_api_port` |
| `tests/integration/test_journey_3_recovery.py` | Same functions |
| `tests/integration/test_journey_6_stale_blocker.py` | Same functions |

### Architecture Compliance

- **Decision scope**: This is a process/architecture decision, not a feature. The ADR format is the right tool because it records context (3-retro history) that would otherwise be lost.
- **Two valid options**:
  - **Option A (Extract)**: Pro: DRY, single source of truth, easier to evolve harness. Con: Shared conftest creates coupling; changing shared helpers may break tests in non-obvious ways; harder to reason about fixture scope.
  - **Option B (Accept)**: Pro: Self-contained tests are easier to understand in isolation; no coupling risk; test failures are local. Con: Duplication means bug fixes must be applied N times; new tests copy existing patterns mechanically.
- **ADR location**: `docs/adr/` — consistent with project convention for architecture decisions (see `docs/adr/adr-from-user-none.md` from story 3.5.5).
- **Convention docs**: Add to `RENDERER_CONVENTIONS.md` (already exists) or create `docs/dev-testing.md` depending on scope.

### Previous Story Intelligence (7.5.1–7.5.6)

- **Commit style**: `docs(tests): resolve integration test harness duplication decision (Story 7.5.7)`.
- **This is a process/documentation story**: No production code changes. The "implementation" is either a shared conftest module (if Option A) or documentation annotations (if Option B). Tests should not break.
- **Test scope**: After any refactoring, run the full integration test suite: `uv run pytest tests/integration/ -x -q`. The ASGI harness tests (~4 files) and journey tests (~3 files) are the primary regression targets.
- **Pre-existing test failures**: Two formally excluded tests from story 3.5.4: crash-injection (4 tests) and separability (1 test). These are not related to the harness work and should continue to be excluded.

### Decision Guidance

The dev agent should make a reasoned choice between the two options. Key factors:

1. **Stability of the harness pattern**: The ASGI + LifespanManager + ASGITransport pattern has been stable across 5+ stories (6.12, 6.13, 6.14, 3.8, 2.13). It's unlikely to change significantly.
2. **Number of consumers**: 10 files is enough to justify extraction. The pattern will likely grow (new journey tests, new validation stories).
3. **Coupling risk**: Shared conftest means changes propagate to all consumers. This is manageable with good docstrings and a clear interface.
4. **The "third retro" signal**: Three consecutive retros independently flagging the same issue suggests the status quo is not working. A decision (either way) is better than deferring again.

### References

- [Source: epic-7-retro-2026-05-13.md — "Integration test harness duplication (systemic)", action item 1]
- [Source: epic-6-retro-2026-05-11.md — "Extract integration test harness to conftest", NOT DONE]
- [Source: epic-5-retro-2026-05-09.md — "~62 lines duplicated code between scripted_worker_stub and auto_approval_stub"]
- [Source: deferred-work.md — D1 (story 5.18)]
- [Source: tests/integration/test_license_scan.py — lines 85-197]
- [Source: tests/integration/test_tier3_negative.py — lines 120-240]
- [Source: tests/integration/test_decision_interleaving.py — lines 145-209]
- [Source: tests/integration/test_command_injection_fuzz.py — lines 515-600]
- [Source: tests/fixtures/auto_approval_stub.py — duplicated helpers]
- [Source: tests/fixtures/scripted_worker_stub.py — duplicated helpers]
- [Source: docs/adr/adr-from-user-none.md — existing ADR pattern]

## Dev Agent Record

### Implementation Plan

Hybrid extraction (ADR-0002 Decision #1-4):
1. Audit 3 duplication clusters via parallel explore agents.
2. Write ADR at `docs/adr/0002-integration-test-harness.md`.
3. Extract Cluster 3 (Docker-compose helpers — strongest candidates, 192/225 lines identical) to `tests/integration/_compose_helpers.py`.
4. Refactor journey_3 and journey_6 to use shared module.
5. Document convention in `docs/testing-guide.md`.
6. Run regression suite.

### Debug Log References

- Ruff I001 import ordering: `uv run ruff check --fix` auto-split multi-import into 4 separate import-from statements. This is standard ruff behavior.
- `json` import needed in test bodies (used by `_wait_for_container_exit`, `_read_jsonl_envelopes`) — not part of extracted helpers.
- `min_services=4` parameter in journey_6's `_wait_for_all_healthy` wrapper is journey-specific.

### Completion Notes

- Chose hybrid extraction over full extraction or full acceptance.
- Only extracted Docker-compose helpers (Cluster 3) — strongest candidates (192/225 lines identical, trivial parameterization).
- Clusters 1 (ASGI) and 2 (Stubs) documented in ADR as future extraction targets but left self-contained due to active divergence.
- 20 pre-existing test failures in registry-api/registry-state/worker-wrapper — unrelated to this story.
- All 48 integration tests pass. All ruff checks pass.

### File List

| Action | Path |
|--------|------|
| Created | `docs/adr/0002-integration-test-harness.md` |
| Created | `tests/integration/_compose_helpers.py` |
| Modified | `tests/integration/test_journey_3_recovery.py` |
| Modified | `tests/integration/test_journey_6_stale_blocker.py` |
| Modified | `docs/testing-guide.md` |

## Change Log

- 2026-05-13: Story created from Epic 7 retrospective action item. Status: backlog.
- 2026-05-14: Comprehensive story created with duplication audit and ADR guidance. Status: ready-for-dev.
- 2026-05-14: Implementation complete — ADR written, Cluster 3 extracted, 2 journey tests refactored, convention documented. Status: review.
- 2026-05-14: Code review — 3 patches applied, 6 deferred, 4 dismissed. Status: done.

## Review Findings

### Applied Patches

- [x] [Review][Patch] Testing-guide references nonexistent `_stub_helpers.py` and `_asgi_harness.py` with present tense ["can be imported from"] — changed to conditional tense ("should be imported from... once extracted") [`docs/testing-guide.md:279`]
- [x] [Review][Patch] Journey 3 `_wait_for_all_healthy` missing `min_services=4` — added to wrapper closure [`test_journey_3_recovery.py:86`]
- [x] [Review][Patch] Testing-guide Docker Compose table missing `data_dir_key` kwarg mention — added to `compose_env` row [`docs/testing-guide.md:258`]
- [x] [Review][Patch] `compose_env` has noisy UID/GID params always passed as constants — added defaults `container_uid=10002`, `container_gid=10000` [`_compose_helpers.py:33`]
- [x] [Review][Patch] `compose_cmd` doesn't validate compose_file exists — added `is_file()` check with clear error [`_compose_helpers.py:58`]
- [x] [Review][Patch] `__all__` not defined — added to module [`_compose_helpers.py:25`]
- [x] [Review][Patch] `resolve_registry_api_port` hardcodes port 8080 without documenting assumption — added docstring note [`_compose_helpers.py:139`]

### Deferred (pre-existing or scope limitation)

- [x] [Review][Defer] Journey 1 not migrated to use shared module — deferred, scope (AC-2 says "at least 2 test files", 2 done)
- [x] [Review][Defer] `_wait_for_socket`, `_read_jsonl_envelopes`, `_poll_for_event`, `_wait_for_container_exit` still copy-pasted — deferred, scope (ADR decision #1 scoped to compose helpers)
- [x] [Review][Defer] `wait_for_all_healthy` silently loops on compose ps failure — deferred, pre-existing behavior
- [x] [Review][Defer] `wait_for_all_healthy` doesn't distinguish unhealthy vs not-started — deferred, pre-existing behavior
- [x] [Review][Defer] ADR-0002 adds `Rationale` section not in ADR-0001 — deferred, style preference
- [x] [Review][Defer] Wrapper indirection is intentional closure pattern — dismissed, design choice
