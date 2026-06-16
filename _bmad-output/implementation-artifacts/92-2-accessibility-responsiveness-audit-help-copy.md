# Story 92.2 — Accessibility, responsiveness, and Audit and Help copy

Status: review

## Scope

Implement static, read-only Audit and Help guidance in `dashboard/static/index.html` plus static accessibility/responsiveness semantics. The dashboard now explains route allowlists, provenance-first display, unavailable states, forbidden operator controls, keyboard navigation, screen-reader landmarks, labeled lists, contrast/readability, reduced motion, responsive layout, and external runbooks/control planes without adding live wiring or control affordances.

## Implementation constraints

No backend/API/schema changes, no dependencies, no JavaScript, no live HTTP lookup, no polling, no automatic refresh, no hidden writes, no cache warming, no forms/buttons/inputs, no `/v1/` links, and no approval/retry/cancel/budget override/apply/prune/delete/truncate/move/rewrite/chmod/archive mutation/manifest mutation/scheduled job/credentialed lifecycle/production operation controls.

## Verification plan

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`
- YAML chronology parse for Story 92.2 lifecycle
- `git diff --check`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy tests/dashboard/test_static_shell.py`
- `uv run pytest -q -m "not slow"`
- independent code-review APPROVE and implementation architecture CLEAR
- UltraQA static adversarial scenarios
- Push and CI green before done

## Local verification evidence

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q` failed 6 expected Story 92.2 assertions against placeholder Audit/Help sections and stale Story 92.1 deferral copy.
- Green focused: `uv run pytest tests/dashboard/test_static_shell.py -q` — 51 passed.
- Focused boundary: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 60 passed.
- `uv run ruff format tests/dashboard/test_static_shell.py` — 1 file reformatted after red/green test addition.
- `uv run ruff format --check .` — passed.
- `uv run ruff check .` — passed.
- `uv run mypy tests/dashboard/test_static_shell.py` — passed.

## Closure evidence

Completed local gates: full non-slow regression, ai-slop-cleaner no-op audit, independent code-review APPROVE, and implementation architecture CLEAR. UltraQA, commit/push/CI, final BMad done transition, and Ultragoal completion checkpoint remain pending.

Story 92.3 final quality gate remains pending; this Story 92.2 slice only covers static accessibility/responsiveness plus Audit and Help copy.
