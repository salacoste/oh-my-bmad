# Story 100.2 — Live-Read Wiring Decision Gate

## Status
Done — docs/status decision-gate implementation.

## Decision outcome
`conditional-ready`

This is non-authorizing planning evidence only. Story 100.2 does **not** implement live browser/API wiring and does **not** authorize wiring inside this story.

## Decision
Phase 21 is conditionally ready to draft a later, separately scoped, narrow live HTTP read story, provided that later story re-proves the runtime boundary with browser/e2e or equivalent runtime tests before implementation is considered complete.

The later live-read story must choose one narrow approved GET route family or read-only subset from the existing approved Phase 21 categories. Story 100.2 intentionally does not choose or implement that route.

## Evidence used
- Story 99.1 established pure presentation-model/read-state contract coverage.
- Story 99.2 established fixture/snapshot rendering contracts and fail-closed probes.
- Story 100.1 added inert committed static fixture-backed rendering and parser-based drift tests.
- Fresh Story 100.2 Ralplan reviews completed in order:
  - Architect `APPROVE` / `CLEAR`.
  - Critic `APPROVE`.

## Mandatory requirements for the future live-read story
A future live browser/API story must include, before it can be marked done:

1. Browser/e2e or equivalent runtime-boundary tests proving only approved GET routes are reachable.
2. No mutation/control affordance and no POST/PUT/PATCH/DELETE dashboard calls.
3. No aggregate overview, session-list, or digest live contract.
4. Stale, partial, invalid, unauthorized, backend-unavailable, and needs-contract states render bounded non-authoritative copy.
5. Provenance, source route/category, source identifiers, freshness/timestamp, and authority metadata remain visible.
6. Existing static/fixture rendering tests remain green as regression coverage.
7. Independent code-reviewer `APPROVE` and architect `CLEAR` for the later live story.
8. Push and GitHub Actions CI green before Phase 21 can claim live-read runtime completion.

## Explicit non-authorization
Story 100.2 adds no live wiring and authorizes none of the following inside this story:

- Browser `fetch`, XHR, WebSocket, EventSource, polling, frontend script, or hidden HTTP client.
- Backend/API route expansion.
- Aggregate/session live contract.
- Digest integration.
- Mutation/control/destructive lifecycle affordance.
- Dependency, lockfile, deployment, or CI change.

## Changed-file scope
Story 100.2 implementation scope is limited to:

- `_bmad-output/implementation-artifacts/100-2-live-read-wiring-decision-gate.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review evidence remains under `.omx/`.

## Verification plan
- Markdown/content checks verify the explicit `conditional-ready` outcome, separate future story requirement, mandatory future live-story tests, and forbidden-surface denials.
- Sprint-status YAML parses and records Story 100.2 lifecycle without implying live wiring is complete.
- Changed-file allowlist remains docs/status only.
- `git diff --check` passes.
- Final AI slop cleanup and independent review gates must pass before the Ultragoal aggregate goal is completed.

## Remaining risk
`conditional-ready` can be misread as authorization. The mitigation is repeated explicit wording in this artifact and sprint status: a separate future story is required before any live browser/API wiring, and that later story must re-prove route scope and runtime-boundary tests.

## AI slop cleanup report
Scope: `_bmad-output/implementation-artifacts/100-2-live-read-wiring-decision-gate.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml`.

Behavior lock:
- Markdown/content checks passed for explicit `conditional-ready` outcome, non-authorization wording, separate future story requirement, mandatory future live-story tests, and forbidden-surface denials.
- Sprint-status YAML parse and lifecycle assertions passed.
- Changed-file allowlist passed with exactly the Story 100.2 artifact and sprint status.
- `git diff --check` passed.

Cleanup plan:
1. Keep scope docs/status-only.
2. Inventory fallback/slop signals.
3. Avoid speculative refactor or new abstractions.
4. Preserve explicit non-authorization wording even if repetitive, because it mitigates the main Critic risk.

Fallback/slop findings:
- No masking fallback, temporary workaround, swallowed error, silent default, broad compatibility shim, or alternate execution path found.
- Repeated no-live-wiring and no-authorization wording is intentional safety evidence, not duplication to remove.

Passes completed:
- Fallback-like code resolution gate: no masking fallback found.
- Dead code deletion: N/A for docs/status artifact.
- Duplicate removal: no removal; repeated guardrail copy is intentional.
- Naming/error handling cleanup: N/A.
- Test reinforcement: content/YAML/allowlist checks serve as the regression lock for this docs/status story.

Quality gates:
- Regression/content checks: PASS.
- YAML parse: PASS.
- Changed-file allowlist: PASS.
- Static/security scan: PASS for forbidden runtime-surface denials by changed-file scope.
- `git diff --check`: PASS.

Remaining risks:
- The phrase `conditional-ready` can still be misread outside context. This artifact deliberately repeats that it is non-authorizing and requires a separate future story before any live browser/API wiring.
