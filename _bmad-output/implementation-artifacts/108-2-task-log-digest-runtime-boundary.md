# Story 108.2 — Task Log Digest Runtime Boundary

## Status

Done — tests-first runtime implementation, code review, adversarial QA, strict typecheck, lint, dashboard regression, full non-slow PR-gate CI-equivalent, push, and remote CI passed.

## Selected surface

- `GET /v1/tasks/{task_id}/logs/digest` only.

## Scope delivered

- Dashboard visible task log digest panel:
  - `dashboard/static/index.html`
  - Exposes a visible `task_id` source and bounded digest status/metadata targets.
  - Documents the route-local boundary and no-stream/no-browser-generation exclusions.
- Dashboard runtime:
  - `dashboard/static/task-log-digest.js`
  - Reads only the visible `#task-log-digest-task-id-source` text.
  - Builds exactly `/v1/tasks/${encodeURIComponent(task_id)}/logs/digest`.
  - Performs exactly one body-free `GET` fetch for the selected task id.
  - Renders returned backend digest/summary text and bounded metadata with `textContent`.
  - Treats malformed JSON, unknown display/freshness states, missing task id, mismatched task id, missing digest, missing server freshness, backend failures, network failures, and unauthorized responses as non-authoritative/fail-closed states.
- Live-read contract promotion:
  - `dashboard/live_read_adapter.py`
  - Promotes only `/v1/tasks/{task_id}/logs/digest` to an approved digest read contract.
  - Keeps `/v1/tasks/{task_id}/logs/digest/stream` excluded.
  - Keeps `/v1/tasks` aggregate and `/v1/sessions` session list unavailable/needs-contract.
- Tests:
  - Adds `tests/dashboard/test_task_log_digest_runtime_boundary.py` for route/method/source/runtime behavior and exclusions.
  - Updates existing dashboard boundary/contract/static tests to allow only the new digest script/GET route while preserving aggregate/session/digest-stream/search/discovery exclusions.

## Non-authorization / exclusions

This story does not authorize digest streaming, aggregate task list reads, session list/detail reads, task-list/search/discovery, browser-side LLM generation, browser-side summarization, generated live data, cache warming, background refresh, polling/timers/retry loops, workers/service workers, storage/cache persistence, mutation/control behavior, backend/API expansion, services/MCP/dependencies/lockfiles/CI/deployment changes, production credentials, or production operations.

## Red-test evidence

Tests were written before implementation. Initial Story 108.2 focused runtime suite failed as expected because the digest panel, runtime script, and approved digest read contract did not yet exist:

- Command: `uv run pytest tests/dashboard/test_task_log_digest_runtime_boundary.py`
- Result before implementation: `9 failed`.
- Expected failure shape: missing `task-log-digest.js`, missing panel element ids, missing visible task id source, no digest runtime behavior, and route/allowlist assertions failing.

## Green verification / CI evidence

- Focused Story 108.2 + state/adapter suite:
  - `uv run pytest tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_adapter.py`
  - Result: `28 passed, 2 warnings in 1.02s`.
- Dashboard regression suite:
  - `uv run pytest tests/dashboard`
  - Result: `189 passed, 2 warnings in 6.67s`.
- Lint/static/syntax/type checks:
  - `uv run ruff check dashboard tests/dashboard`
  - Result: `All checks passed!`.
  - `node --check dashboard/static/task-log-digest.js`
  - Result: passed.
  - `uv run mypy --strict --explicit-package-bases dashboard tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_fixture_contracts.py`
  - Result: `Success: no issues found in 5 source files`.
  - `git diff --check`
  - Result: passed.
  - YAML parse check for `_bmad-output/implementation-artifacts/sprint-status.yaml`
  - Result: passed.
- Full local PR-gate CI-equivalent:
  - `uv run pytest -m "not slow"`
  - Result: `4333 passed, 8 skipped, 61 deselected, 24 warnings in 155.70s (0:02:35)`.
- Remote CI after push:
  - Commit: `a835db97b62a005891a0b3e4ce920fc64c0215da` (`a835db9 feat(dashboard): add task log digest boundary`).
  - GitHub Actions `ci` run `28205787033`: status `completed`, conclusion `success`.
  - Jobs `Registry-state tests (Postgres service container)` and `PR gate (ruff + mypy + pytest)` both succeeded.
  - URL: `https://github.com/salacoste/oh-my-bmad/actions/runs/28205787033`.

## Review and QA evidence

- RALPLAN architect review: APPROVE / WATCH, no required plan changes.
- RALPLAN critic review: APPROVE, no required plan changes.
- Initial code-review findings were fixed before final approval:
  - Removed browser-generated freshness fallback and required server freshness for authoritative digest rendering.
  - Added adapter/runtime state taxonomy for provider unavailable and empty digest states.
  - Clamped backend `display_state` and `freshness_state` to allowed contract values.
  - Classified malformed JSON as `invalid` / non-authoritative rather than backend unavailable.
- Final code review: `.omx/specs/story-108-2-task-log-digest-runtime-boundary-code-review.md`
  - Verdict: `APPROVE`; reviewer subagent `019f00f6-43c7-7703-b1fb-7d1bf6a2edff`.
  - Reviewer validation: `git diff --check -- dashboard/static/task-log-digest.js dashboard/live_read_adapter.py dashboard/static/index.html tests/dashboard` passed; `uv run pytest -q tests/dashboard` -> `189 passed, 2 warnings`; targeted scoped suite -> `176 passed, 2 warnings`.
- UltraQA/adversarial QA: `.omx/ultraqa/story-108-2-task-log-digest-runtime-boundary-report.md`
  - Verdict: `PASS`; QA subagent `019f00f9-6674-7251-948c-ffbcb73d0f20`.
  - QA confirmed no digest stream, aggregate/session list, task-list/search/discovery, browser LLM generation, storage/cache warming, or background refresh markers; adapter keeps digest stream excluded and aggregate/session unavailable.

## Evidence checklist

- Context snapshot: `.omx/context/story-108-2-task-log-digest-runtime-boundary-20260625T221438Z.md`
- Deep interview: `.omx/interviews/story-108-2-task-log-digest-runtime-boundary-deep-interview.md`
- RALPLAN: `.omx/specs/story-108-2-task-log-digest-runtime-boundary-ralplan.md`
- Test spec: `.omx/specs/story-108-2-task-log-digest-runtime-boundary-test-spec.md`
- Red tests: complete.
- Implementation: complete.
- Verification: complete locally.
- Code review: `.omx/specs/story-108-2-task-log-digest-runtime-boundary-code-review.md` — `APPROVE`.
- UltraQA: `.omx/ultraqa/story-108-2-task-log-digest-runtime-boundary-report.md` — `PASS`.

Generated: 2026-06-26T00:50:00+03:00
Updated: 2026-06-26T02:10:00+03:00
