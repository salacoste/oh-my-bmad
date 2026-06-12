# Story 78.1 — `/v1/tasks/{task_id}/history` archive opt-in

Status: done

## Summary

The registry API task-history route is archive-aware when archive manifest configuration is present, and remains hot-log-only by default when no manifest is configured.

## Acceptance evidence

- Archive-only task history returns 200 when `REPLAY_ARCHIVE_MANIFEST` points to a valid `lifecycle-manifest.json`.
- No archive manifest configured preserves previous hot-only 404 behavior for archive-only tasks.
- Invalid archive configuration fails closed with route-local RFC 7807 ProblemDetails using `replay_archive_config_error`.
- The route remains read-only and does not add destructive lifecycle operations.

## Verification

- `uv run pytest services/registry-api/src/registry_api/routes/test_replay.py::TestGetTaskHistory -q` → 9 passed, 1 warning.
- Broader replay/history suite → 85 passed, 1 warning.
- `uv run python scripts/check_single_writer.py` → passed.
