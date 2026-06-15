# Story 89.3: Sessions visibility panel

Status: done

## Story

As the single operator/developer, I want the read-only dashboard Sessions panel to explain session visibility, provenance, freshness, and unavailable states from existing safe reads only, so that I can inspect session context without creating a control surface or live dashboard read contract.

## Source Context

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md`
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-epics.md`
- `.omx/plans/all-stories-one-by-one-story-89-3-plan.md`
- `.omx/specs/all-stories-one-by-one-story-89-3-test-spec.md`
- Existing MCP read resources in `mcp-servers/session-registry/src/session_registry_mcp/handlers/resources.py`:
  - `session://active`
  - `session://detail/{session_id}`
  - `session://heartbeats`

## Acceptance Criteria

- [ ] Sessions panel names existing MCP session read resources as inert visible provenance only.
- [ ] No `session://...` resource literal appears in HTML attributes, binding-like slots, links, sources, actions, data attributes, ARIA attributes, or metadata attributes.
- [ ] Resource-native row contract uses `id`, `task_id`, `worker_kind`, `worktree_path`, `status`, `started_at`, `ended_at`, and `last_heartbeat_at`.
- [ ] If visible `session_id` appears, it is explicitly a display label for resource-native `id`, not a separate resource field; the URI template placeholder alone does not satisfy this assertion.
- [ ] Derived/provenance/unavailable-only semantics (`freshness_state`, `source`, `trace_id`) are separated from resource-native fields.
- [ ] Historical and terminal session wording is explanatory only and does not imply an approved aggregate historical-session list/search/read route.
- [ ] State copy includes no active sessions, active session, historical session, terminal session outcome, heartbeat/stale warning, loading, unavailable pending dashboard read contract, empty successful read, read error, unauthorized/configuration failure, and stale data.
- [ ] Section-local wording is generic visibility/no-action copy and does not repeat exact banner-only control vocabulary.
- [ ] Existing Story 88.2 and Story 89.2 dashboard boundary tests continue to pass.

## Tasks

- [x] Create story context and update sprint status backlog → ready-for-dev → in-progress.
- [x] Add red parser tests for Story 89.3 Sessions contract.
- [x] Update `dashboard/static/index.html` Sessions section.
- [x] Run focused dashboard tests and hygiene gates.
- [x] Run full local non-slow regression.
- [x] Move story to review and complete independent review + UltraQA.
- [x] Mark done locally; commit/push/CI pending at time of this story update.

## Dev Notes

This story is static/dashboard-copy/test-only. It must not add backend routes, frontend runtime code, JavaScript, dependencies, package/lockfile changes, deployment changes, MCP server changes, registry API changes, hidden writes, background jobs, live polling, or dashboard HTTP session routes.

## Dev Agent Record

### Ralplan Consensus

- Architect final review: APPROVE/CLEAR (`019ecb3d-b56b-7490-8370-26764f401ceb`).
- Critic final review: APPROVE (`019ecb3e-66ae-76c3-8b57-3c5955bb4022`).

### Implementation Evidence

- Red: `uv run pytest tests/dashboard/test_static_shell.py -q` failed with 3 intended Story 89.3 failures before dashboard copy.
- Green focused: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 23 tests.
- Hygiene: `git diff --check`, `uv run ruff format --check .`, and `uv run ruff check .` passed after formatting `tests/dashboard/test_static_shell.py`.
- Full local: `uv run pytest -q -m "not slow"` passed 4158 tests, 8 skipped, 61 deselected.

### Review Cycle 1 Fix Evidence

- Ralplan review-fix consensus: Architect CLEAR (`019ecb4a-f864-73f3-84d7-8c53a4364654`) followed by Critic APPROVE (`019ecb4a-f97c-75d3-92be-7f4227074d1a`).
- Patch: hardened Story 89.3 tests to extract exact native and derived field clauses instead of broad substring checks.
- Green focused: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 23 tests.
- Hygiene: `git diff --check`, `uv run ruff format --check .`, `uv run ruff check .` passed.
- Full local: `uv run pytest -q -m "not slow"` passed 4158 tests, 8 skipped, 61 deselected.

### Final Review and UltraQA Evidence

- Independent code-review re-review: APPROVE, 0 issues (`019ecb51-f8ee-7f70-bef5-42f4c1c175c8`).
- Independent architect re-review: CLEAR (`019ecb51-fa23-7003-930b-4ac0f2e0c4b0`).
- UltraQA: `/tmp/story_89_3_ultraqa.py` temporary harness passed 6 static adversarial scenarios and was removed. Scenarios covered normal contract, native field-list mutation, session resource attribute injection, control-copy injection, misleading Source/meta false green, and URI-template/display-label false green.
