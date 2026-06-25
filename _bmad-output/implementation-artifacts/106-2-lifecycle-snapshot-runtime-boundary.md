# Story 106.2 — Lifecycle / Snapshot Runtime Boundary

## Status

Done locally — tests-first runtime boundary implemented, reviewed, and UltraQA-verified on 2026-06-24.

Story 106.3 remains the future final closure lane for push/remote-CI/final Epic 106 status closure evidence.

## Runtime boundary implemented

- Added `dashboard/static/lifecycle-snapshot.js` as the single lifecycle/snapshot runtime module.
- Added the `Lifecycle / snapshot readiness` panel in `dashboard/static/index.html`.
- Runtime fetch boundary is exactly `GET /v1/events/replay/snapshots` through one body-free `fetch(ROUTE, { method: "GET" })` call.
- Output is bounded to snapshot-list metadata only: `snapshot_id`, `sequence_number`, `timestamp`, and `size_bytes`.
- Freshness is rendered only from returned `retrieved_at`; row timestamps are not reused as panel freshness.
- Lifecycle evidence remains passive display/provenance data. Missing, failed, stale, invalid, missing-rollback, and unverifiable evidence renders non-authoritative degraded states.
- Unknown backend `display_state` values are clamped to `invalid`; raw backend strings are not rendered.
- `archive_manifest_validation` must equal `valid archive manifest` before evidence can become authoritative.

## Explicit non-authorization

This story does not authorize `POST /v1/events/replay/snapshots`, snapshot creation, snapshot deletion, snapshot mutation, lifecycle apply/prune/rollback, archive/manifest mutation, replay execution, task-list/search/discovery, aggregate/session/digest reads, generated live data, polling/timers/storage/workers/websocket/xhr, background jobs, cache warming, backend/API expansion, dependency changes, services, MCP changes, or dashboard controls.

## Tests-first evidence

- Red phase: `uv run pytest -q tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py` failed 11/11 before implementation because the lifecycle/snapshot runtime module and panel targets were absent.
- Green after implementation: `uv run pytest -q tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py` passed 13/13 after review fixes.
- Targeted boundary/static/live-read suite: `126 passed, 2 warnings`.
- All dashboard tests: `175 passed, 2 warnings`.
- Syntax/static checks:
  - `node --check dashboard/static/lifecycle-snapshot.js` passed.
  - `uv run python -m py_compile tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_static_shell.py` passed.
  - `uv run ruff check tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_static_shell.py` passed.
  - `uv run mypy tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py` passed.
  - `git diff --check` passed.

## Review evidence

- Code-reviewer lane initially returned `REQUEST CHANGES` for two HIGH findings:
  1. unbounded `display_state` could render raw backend strings;
  2. substring validation could treat `invalid`/`not valid`/`unvalidated` archive-manifest evidence as authoritative.
- Both findings were fixed and covered by regression tests.
- Code-reviewer re-review returned `APPROVE` with 0 remaining issues.
- Architect lane initially returned `WATCH` for evidence-source visibility. The shell copy was updated to explicitly declare optional `window.LIFECYCLE_SNAPSHOT_EVIDENCE` and non-authoritative fail-closed behavior; architect re-check returned `CLEAR`.
- Final code-review synthesis: `APPROVE` after code-reviewer re-review `APPROVE` and architect re-check `CLEAR`.

## UltraQA evidence

See `.omx/ultraqa/story-106-2-lifecycle-snapshot-runtime-boundary-report.md`.

UltraQA Cycle 1 used the existing dynamic Node/pytest harnesses for malformed payloads, hidden selector/prompt-injection-style decoys, stale/missing lifecycle evidence, raw snapshot leakage attempts, unbounded backend state strings, misleading authority strings, absent freshness, unauthorized/backend/network failures, and shared dashboard boundary regression. No temporary harness files were needed.

## Follow-up scope note

- Passive lifecycle evidence is consumed from `window.LIFECYCLE_SNAPSHOT_EVIDENCE` / global test injection. Without that object, the panel safely renders missing lifecycle evidence as non-authoritative. If a future story wants production-owned passive evidence hydration, it must add an explicit narrow source without creating lifecycle actions, operator gates, snapshot creation, replay execution, archive/manifest mutation, route discovery, polling, or controls.
