# Story 101.1 — Health/Readiness Live-Read Runtime Boundary Planning

## Status
Done — docs/status/test-spec-first planning for the first post-Phase-21 live-read runtime boundary.

## Selected first route family
Story 101.1 selects exactly one first live-read route family:

- Health/readiness: `GET /v1/health`

This route is the initial boundary target because it is an approved GET/readiness route, has no route identifiers, and avoids aggregate/session/digest risks while proving the first dashboard runtime-boundary pattern.

## Scope
Story 101.1 is planning/status/test-spec-first. It records the runtime-boundary requirements that must be satisfied before any Health/readiness live browser/API wiring can be considered complete.

This story does **not** implement broad live dashboard wiring. It does not add browser runtime calls, backend/API routes, runtime JavaScript, dependencies, lockfiles, deployment, CI changes, generated live data, service changes, MCP server changes, or test-code changes.

## Phase status
Phase 21 remains closed as Dashboard Live-Read Rendering Readiness complete.

Story 101.1 opens the next status branch for Health/readiness runtime-boundary planning without reopening Phase 21 or claiming live runtime completion.

## Mandatory tests for a later Health/readiness runtime implementation
A later implementation story that wires Health/readiness live runtime behavior must prove all of the following before it can be marked done:

1. Dashboard runtime can reach only the approved `GET /v1/health` route for this slice.
2. No POST, PUT, PATCH, or DELETE dashboard calls exist.
3. No aggregate overview, session-list, or digest live contract is introduced.
4. No forms, buttons, inputs, operator controls, mutation/control vocabulary, or destructive lifecycle affordance is introduced.
5. No hidden HTTP client exists beyond the explicitly approved health-read mechanism.
6. Stale, unavailable, backend-unavailable, invalid/parse-failure, and unauthorized-like responses render bounded non-authoritative copy.
7. Provenance, freshness/timestamp, and authority metadata remain visible.
8. Existing static, fixture, and read-only regression tests remain green.
9. Independent code-reviewer `APPROVE` and architect `CLEAR` gates pass.
10. UltraQA passes, or a skip is explicitly justified only for a docs-only/trivially non-runtime diff.

## Explicit non-authorization
Story 101.1 authorizes none of the following:

- Broad live dashboard runtime wiring.
- Task detail, event timeline/transitions, trace, history, or replay live wiring.
- Aggregate overview or session-list live contract.
- Digest or digest stream integration.
- Mutation/control/destructive lifecycle affordance.
- POST/PUT/PATCH/DELETE dashboard calls.
- Backend/API route expansion.
- Dependency, lockfile, deployment, CI, service, MCP, generated live-data, runtime, browser script, or test-code change.

## Changed-file scope
Story 101.1 implementation scope is limited to docs/status files:

- `_bmad-output/implementation-artifacts/101-1-health-readiness-live-read-runtime-boundary.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review/goal evidence remains under `.omx/`.

## Verification plan

- Markdown/content checks verify selected route, planning/status/test-spec-first wording, Phase 21 closure preservation, mandatory future runtime-boundary tests, and explicit forbidden-surface denials.
- Sprint-status YAML parses and records Story 101.1 lifecycle newest-first without reopening Phase 21.
- Changed-file allowlist remains docs/status only, excluding `.omx/` workflow-state artifacts.
- `git diff --check` passes.
- AI slop cleanup report records no fallback/masking/slop findings.
- Independent code-reviewer and architect final gates must pass before completion.

## AI slop cleanup report

Scope: `_bmad-output/implementation-artifacts/101-1-health-readiness-live-read-runtime-boundary.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml`.

Behavior lock:
- Content checks verify exact Health/readiness `GET /v1/health` selection, Phase 21 closure preservation, planning/status/test-spec-first scope, future runtime-boundary test list, and forbidden-surface denials.
- Sprint-status YAML parse and lifecycle checks pass.
- Changed-file allowlist passes for docs/status implementation files, excluding `.omx/` workflow-state artifacts.
- `git diff --check` passes.

Cleanup plan:
1. Keep scope docs/status-only.
2. Preserve explicit no-live-wiring and no-authorization wording even if repetitive.
3. Avoid speculative abstraction, runtime helper, hidden fallback, or test-code change.
4. Rerun post-cleaner verification.

Fallback/slop findings:
- No masking fallback, broad compatibility shim, swallowed error, silent default, speculative runtime path, hidden HTTP client, or new dependency found.
- Repeated guardrail wording is intentional risk mitigation, not duplication to remove.

Quality gates:
- Content checks: PASS.
- YAML parse/lifecycle checks: PASS.
- Changed-file allowlist: PASS.
- `git diff --check`: PASS.

Remaining risk:
- The phrase runtime-boundary planning can be misread as runtime implementation. The mitigation is repeated explicit wording that Story 101.1 does not implement live browser/API wiring and only selects Health/readiness `GET /v1/health` as the future first runtime-boundary target.
