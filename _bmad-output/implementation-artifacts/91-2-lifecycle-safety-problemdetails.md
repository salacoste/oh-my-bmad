# Story 91.2 — Lifecycle safety copy and archive ProblemDetails states

Status: review

## Scope

Implement static, read-only lifecycle safety and archive ProblemDetails copy in `dashboard/static/index.html` under `#replay-lifecycle-readiness`. The panel renders canonical route-local ProblemDetails fields, grounded lifecycle evidence slots, fail-safe archive/lifecycle/authorization states, retention/future-work boundaries, and destructive-operation gate copy without adding any runtime controls or live wiring.

## Canonical static contract source

`dashboard/static/replay-lifecycle-contract.json` is the canonical source for the exact Story 91.2 static dashboard lists:

- `archiveProblemDetailsPassiveFields`
- `lifecycleReadinessPassiveEvidence`
- `lifecycleAndArchiveFailSafeStates`

`dashboard/static/index.html` renders those values as inert static copy, and `tests/dashboard/test_static_shell.py` loads the JSON source before asserting the HTML lists. This keeps the exact safety contract visible while reducing manual drift across tests and documentation.

## Implementation constraints

No backend/API/schema changes, no dependencies, no JavaScript, no live HTTP lookup, no replay execution, no non-read snapshot behavior, no lifecycle execution, no archive/manifest changes, no retention scheduling, no background jobs, no hidden writes, no cache warming, and no mutation/control affordances. Route and evidence names are inert visible provenance/copy only.

## Verification plan

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`
- YAML chronology parse for `backlog -> ready-for-dev -> in-progress -> review`
- `git diff --check`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pytest -q -m "not slow"`
- independent code-review APPROVE and architecture status CLEAR
- UltraQA static adversarial scenarios
- Push and CI green before done

## Local verification evidence

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q` failed 4 Story 91.2 tests against the Story 91.1 placeholder section.
- `uv run pytest tests/dashboard/test_static_shell.py -q` — 40 passed after canonical contract-source extraction.
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 49 passed.
- `git diff --check` — passed.
- `uv run ruff format --check .` — passed after formatting `tests/dashboard/test_static_shell.py`.
- `uv run ruff check .` — passed.
- `uv run pytest -q -m "not slow"` — 4184 passed, 8 skipped, 61 deselected.

Final done status still requires independent code review, architecture status CLEAR, UltraQA, push/CI, and Ultragoal reconciliation.

## Deferred follow-up

- `_bmad-output/implementation-artifacts/deferred-work.md` records Story 91.2 D1: future canonical static-dashboard lifecycle contract source. This is maintenance debt from independent architect review, not a blocker for the current static/read-only slice. Story 91.2 now uses `dashboard/static/replay-lifecycle-contract.json` as the canonical source for exact static-dashboard lists; future stories should preserve that source when evolving lifecycle/readiness copy.
