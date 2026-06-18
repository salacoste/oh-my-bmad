# Story 97.2 — Phase 20 final no-mutation, provenance, accessibility, review, and CI gate

Status: in-progress

## Scope

Final Phase 20 validation and closure for read-only dashboard live-read
contracts and wiring readiness.

## Validation coverage recorded

- Route/method/effect allowlist validation.
- Provenance/freshness/stale/error-state validation.
- Aggregate/session unavailable/needs-contract decision validation.
- Dashboard accessibility/responsiveness/read-only boundary validation.
- No runtime live API calls, no frontend scripts, no backend route expansion, no
  HTTP clients, no mutation/control behavior, no destructive lifecycle behavior,
  no dependencies, no lockfile changes, and no CI/deployment changes.

## Required local gates

- `git diff --check`
- `uv run ruff format --check tests/dashboard/test_phase20_final_validation.py`
- `uv run ruff check tests/dashboard/test_phase20_final_validation.py`
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
- Targeted dashboard suite including final validation, static shell, read-only
  boundary, live-read contract/state/adapter/panel tests.
- Full non-slow pytest.

## Review and CI evidence

Final independent code-review, architecture review, UltraQA decision, commit SHA,
push, CI URL, and CI green evidence must be recorded in the Ultragoal quality
gate after the commit that contains this artifact is pushed. This artifact stays
in-progress until those external gates exist.

## Closure rule

Sprint status may close Story 97.2 and Epic 97 only after local gates,
independent review, UltraQA pass or justified skip, push, and CI green are
available in the final Ultragoal checkpoint.
