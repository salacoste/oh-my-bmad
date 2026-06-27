# Story 112.2 — Digest Stream Runtime/API Contract Boundary

## Status

Done — exact digest stream runtime/API boundary completed after Autopilot code-review cycle 6 APPROVE/CLEAR, verifier PASS/CLEAR, local non-slow gate, push to `main`, and remote CI run `28291210521` success for commit `4614313637059d81ddb5d705dedede91661f0116`.

## Implemented exact surface

- API/runtime route: `GET /v1/tasks/{task_id}/logs/digest/stream`.
- Stream transport/framing: `application/x-ndjson`; each frame is one JSON object followed by `\n`.
- Frame types: exactly `open`, `chunk`, and `final` for successful streams.
- Degraded provider-unavailable streams remain non-authoritative: final frames use `display_state: "provider-unavailable"` and `authority_state: "non-authoritative"` while carrying only bounded unavailable-summary text.
- Browser transport: additive `dashboard/static/digest-stream.js` using `fetch()` plus `ReadableStream.getReader()` plus `TextDecoder`, with one bounded `AbortController` timeout.
- Selector: visible `task_id` text only, percent-encoded as one path segment.

## Boundaries preserved

- Existing non-streaming `GET /v1/tasks/{task_id}/logs/digest` remains supported and independent.
- No fallback from stream to non-streaming digest.
- No task-list/search/discovery, task detail, history, trace, replay, session traversal, broad dashboard wiring, generated live data, browser-side LLM summarization/generation, EventSource, WebSocket, XMLHttpRequest, workers, polling, automatic reconnect loops, storage writes, mutation/control behavior, dependencies, lockfiles, services/MCP changes, CI/deployment file changes, production credentials, or production operations.
- Stream chunks are bounded digest-stream text/metadata only; raw event payloads, raw logs, prompts, provider internals, filesystem/resource paths, hrefs/URLs, joined task/session/event data, and control hints fail closed.

## Changed files

- `services/registry-api/src/registry_api/routes/digest.py`
- `services/registry-api/src/registry_api/test_digest.py`
- `dashboard/static/digest-stream.js`
- `dashboard/static/index.html`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_digest_stream_runtime_boundary.py`
- Dashboard/live-read route inventory and guardrail tests updated for the additive exact stream route.
- `docs/api-contracts.md`
- `docs/feature-status.md`

## Verification evidence

- `uv run pytest -q services/registry-api/src/registry_api/test_digest.py -k 'DigestStreamBoundary or WireContract or TestDigestFallback or TestDigestHappyPath or TestDigestNotFound'` — 12 passed.
- `uv run pytest -q tests/dashboard/test_digest_stream_runtime_boundary.py` — 7 passed.
- `uv run pytest -q tests/dashboard/test_read_only_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py` — 69 passed.
- Review-finding repair checks:
  - `uv run pytest -q services/registry-api/src/registry_api/test_digest.py -k 'provider_unavailable or DigestStreamBoundary'` — 5 passed.
  - `uv run pytest -q tests/dashboard/test_digest_stream_runtime_boundary.py -k 'runtime_behavior_maps_success_and_failures'` — 1 passed.
  - `uv run pytest -q services/registry-api/src/registry_api/test_digest.py tests/dashboard/test_digest_stream_runtime_boundary.py tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py` — 56 passed.
- `uv run pytest -q tests/dashboard/test_digest_stream_runtime_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_read_only_boundary.py` — 44 passed.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state`, project static check scripts and self-tests, `python -m py_compile dashboard/live_read_adapter.py services/registry-api/src/registry_api/routes/digest.py`, `node --check dashboard/static/digest-stream.js`, and `git diff --check` — passed.
- `uv run pytest -m "not slow"` — 4373 passed, 8 skipped, 61 deselected, 37 warnings.

## Review/QA note

Autopilot RALPLAN consensus for implementation was recorded before coding. Code-review cycle 6 returned APPROVE/CLEAR after server-side and browser-side fail-closed hardening plus strict dashboard frame envelope validation. Verifier cycle 1 and 2 blocked only stale BMAD wording; verifier cycle 3 returned PASS/CLEAR. Implementation commit `4614313637059d81ddb5d705dedede91661f0116` was pushed to `main`, and GitHub Actions `ci` run `28291210521` completed successfully: https://github.com/salacoste/oh-my-bmad/actions/runs/28291210521.
