# Story 90.1: Task event timeline

Status: done

## Story

As the single operator/developer, I want the read-only dashboard Events panel to explain task event timelines, state transitions, provenance, and unavailable states from existing safe event/transition reads only, so that I can inspect the future timeline contract without creating a control surface or live dashboard read contract.

## Source Context

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md`
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-epics.md`
- `.omx/plans/autopilot-story-90-1-task-event-timeline-plan.md`
- `.omx/specs/autopilot-story-90-1-task-event-timeline-test-spec.md`
- Existing safe registry-api reads in `services/registry-api/src/registry_api/routes/events.py`:
  - `GET /v1/tasks/{task_id}/events`
  - `GET /v1/tasks/{task_id}/transitions`

## Acceptance Criteria

- [ ] Events panel names existing task event and transition GET routes as inert visible provenance only.
- [ ] Route strings do not appear in links, attributes, automatic-refresh sources, client calls, or live runtime wiring.
- [ ] Raw event envelope fields are listed separately from derived display summary wording.
- [ ] Transition fields are listed from the existing transition read contract.
- [ ] State copy distinguishes empty history, missing task, unauthorized access, stale data, route failure/read error, unavailable unsupported timeline segment, loading, and empty successful read.
- [ ] Unsupported timeline aggregation and related ProblemDetails visibility are explicitly deferred/unavailable pending a separate read contract.
- [ ] Timeline visibility cannot append events, trigger replay, create snapshots, or dispatch background jobs.
- [ ] Section-local wording does not repeat exact banner-only control vocabulary.
- [ ] Existing Story 88.2, 89.2, and 89.3 dashboard boundary tests continue to pass.

## Tasks

- [x] Create story context and update sprint status backlog → ready-for-dev → in-progress before source edits.
- [x] Add red parser tests for Story 90.1 Events contract.
- [x] Update `dashboard/static/index.html` Events section.
- [x] Run focused dashboard tests and hygiene gates.
- [x] Run full local non-slow regression.
- [x] Move story to review for independent review and UltraQA.
- [x] Complete final independent review + UltraQA after process-artifact correction.
- [x] Mark done locally after pushed CI green evidence.

## Dev Notes

This story is static/dashboard-copy/test-only. It must not add backend routes, frontend runtime code, JavaScript, dependencies, package/lockfile changes, deployment changes, MCP server changes, registry API changes, hidden writes, background jobs, live polling, event appending, replay triggering, snapshot creation, dashboard HTTP event routes, or timeline aggregation routes.

## Dev Agent Record

### Ralplan Consensus

- Architect final review: APPROVE/CLEAR (`019ecbae-698b-7733-8d06-f1f4f1538fa7`).
- Critic final review: APPROVE (`019ecbb0-b073-7630-acbe-6616330a0a8a`).

### Implementation Evidence

- Red: `uv run pytest tests/dashboard/test_static_shell.py -q` failed with 4 intended Story 90.1 failures before Events panel copy, then 2 copy/parser alignment failures, then 1 dotted-field parser failure.
- Green focused: `uv run pytest tests/dashboard/test_static_shell.py -q` passed 21 tests.
- Green focused dashboard/boundary: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 28 tests.
- Hygiene: `git diff --check`, `uv run ruff format --check .`, and `uv run ruff check .` passed after formatting `tests/dashboard/test_static_shell.py`.
- Full local: `uv run pytest -q -m "not slow"` passed 4163 tests, 8 skipped, 61 deselected.
- Mypy: not run because the implementation touched static HTML, parser tests, and BMad markdown/YAML artifacts only; no typed production Python surface changed.

### Review Cycle 1 Fix Evidence

- Architect review initially returned WATCH for prose-as-contract brittleness in Events field-list parsing.
- Patch: replaced punctuation-sensitive prose field parsing with structured static `<ul aria-label=...>` lists and HTMLParser `section_lists` capture.
- Green focused after fix: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 28 tests.
- Hygiene after fix: `git diff --check`, `uv run ruff format --check .`, `uv run ruff check .` passed.
- Full local after fix: `uv run pytest -q -m "not slow"` passed 4163 tests, 8 skipped, 61 deselected.

### Final Review, UltraQA, and CI Evidence

- Process-artifact correction: prior premature done status was reverted to review before final re-review.
- Final independent code-review: APPROVE, 0 issues (`019ecbcf-a1f5-7fb3-a925-c4d4f34a80dc`).
- Final independent architect review: CLEAR (`019ecbcf-a324-7b13-ab1d-5e4b1bdc0614`).
- UltraQA: `/tmp/story_90_1_ultraqa.py` temporary harness passed 7 static adversarial scenarios after harness false-positive corrections and was removed.
- Commit: `c15efef feat(dashboard): add task event timeline panel`.
- CI: `https://github.com/salacoste/oh-my-bmad/actions/runs/27556313447` completed successfully; Registry-state Postgres and PR gate jobs both passed.
