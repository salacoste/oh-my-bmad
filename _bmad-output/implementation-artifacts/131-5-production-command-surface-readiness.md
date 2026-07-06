# Story 131.5 — Production Command Surface and Audit Dashboard Readiness

Date: 2026-07-06

## Scope

Story 131.5 adds a static/readiness-only contract for future production operation
inspection, approval, stop, disable, rollback, and audit-dashboard surfaces. It
keeps the current repo limited to existing task lifecycle console/Telegram
commands and existing dashboard read/status surfaces.

## Added artifacts

- `docs/production-command-surface-readiness.json` — machine-readable command
  surface readiness contract.
- `scripts/check_production_command_surface.py` — static checker for the
  contract, existing console/Telegram command registrations, dashboard static
  assets, registry API routes, and docs.
- `tests/scripts/test_check_production_command_surface.py` — self-test and
  negative-drift coverage for production-operation command/control tokens.

## Safety boundary

This story does not add console production operation commands, Telegram
production operation commands, dashboard production operation controls, registry
API production operation mutation endpoints, credential rendering, or runtime
production audit emitters. It only records the evidence future live surfaces must
satisfy before implementation.

## Readiness evidence pinned by the checker

- Existing console commands stay scoped to task lifecycle and diagnostic reads.
- Existing Telegram handlers stay scoped to task lifecycle and diagnostic reads.
- Dashboard static assets do not expose production operation approve/stop/disable
  controls.
- Registry API routes do not add production operation mutation endpoints.
- Docs keep production operation command surfaces deferred until a later approved
  implementation story.

## Verification commands

- `uv run --python 3.12 python scripts/check_production_command_surface.py --verbose`
- `uv run --python 3.12 python scripts/check_production_command_surface.py --self-test`
- `uv run --python 3.12 pytest tests/scripts/test_check_production_command_surface.py -q`
