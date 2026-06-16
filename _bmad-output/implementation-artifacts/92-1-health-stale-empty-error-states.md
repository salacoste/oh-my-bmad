# Story 92.1 — Health, stale, empty, and error states

Status: review

## Scope

Implement static, read-only health, stale, empty, missing, unauthorized, forbidden, degraded, unavailable-read, and ProblemDetails state copy in `dashboard/static/index.html` under `#health`. The panel renders inert `GET /v1/health` provenance, aria-labeled metadata/state/ProblemDetails lists, unapproved metrics/provenance unavailable copy, and external-remediation copy without live wiring or mutation/control affordances.

## Implementation constraints

No backend/API/schema changes, no dependencies, no JavaScript, no live HTTP lookup, no health refresh, no polling, no background checks, no hidden writes, no cache warming, no metrics/provenance reads beyond the approved static `GET /v1/health` provenance, and no production operation controls. Route names are inert visible provenance only.

## Verification plan

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`
- YAML chronology parse for Story 92.1 lifecycle
- `git diff --check`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy tests/dashboard/test_static_shell.py`
- `uv run pytest -q -m "not slow"`
- independent code-review APPROVE and implementation architecture CLEAR
- UltraQA static adversarial scenarios
- Push and CI green before done

## Local verification evidence

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q` failed 5 Story 92.1 tests against the Story 88.1 health placeholder.
- `uv run pytest tests/dashboard/test_static_shell.py -q` — 45 passed.
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 54 passed.
- YAML chronology parse — passed for backlog -> ready-for-dev -> in-progress -> review.
- `git diff --check` — passed.
- `uv run ruff format --check .` — passed.
- `uv run ruff check .` — passed.
- `uv run mypy tests/dashboard/test_static_shell.py` — passed.
- `uv run pytest -q -m "not slow"` — 4189 passed, 8 skipped, 61 deselected.
- AI slop cleaner scoped audit — passed/no-op; report `.omx/specs/autopilot-story-92-1-ai-slop-cleaner-report.md`.
- Post-cleaner `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 54 passed.
- Post-cleaner `git diff --check`, `ruff format --check`, `ruff check`, and `mypy tests/dashboard/test_static_shell.py` — passed.
- Post-cleaner `uv run pytest -q -m "not slow"` — 4189 passed, 8 skipped, 61 deselected.

## Closure evidence

- Independent code-review: APPROVE (`019ecdb1-4226-7960-84a6-d4e40641e434`).
- Independent implementation architect review: CLEAR (`019ecdb1-4336-7290-a0bc-70c50c6f447b`).
- Review synthesis: `.omx/specs/autopilot-story-92-1-code-review-final.md`.
- UltraQA: passed 33 adversarial static assertions; report `.omx/specs/autopilot-story-92-1-ultraqa-report.md`.
- Commit/push/CI: pending.
