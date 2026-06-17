# Phase 20 Architecture Amendment — Read-Only Dashboard Live-Read Contracts

## Decision Summary

Phase 20 may proceed from product planning into architecture planning for **read-only dashboard live-read contracts**. The dashboard remains an operator visibility surface, not a control plane. This amendment authorizes design constraints and future implementation boundaries only. It is docs/status-only and does not authorize live dashboard wiring, frontend live wiring, digest integration, backend route implementation, API schema changes, dependency changes, deployment changes, CI changes, runtime behavior changes, or mutation/control surfaces.

The architectural rule is **read-only by effect**. A route being `GET` is necessary but not sufficient: dashboard reads must not write, dispatch jobs, warm caches through writes, create snapshots, mutate manifests, enqueue lifecycle work, or expose controls.

## Inputs

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-19-epics.md`
- `.omx/plans/phase-20-readonly-dashboard-live-read-contracts-plan.md`
- `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-test-spec.md`
- `.omx/specs/phase-20-readonly-dashboard-live-read-contracts-consensus-handoff.md`

## Existing safe-read candidates

The first future live-read stories should consume only existing safe read candidates that remain approved by story-level tests. The route and panel names below are **candidate/provisional planning labels**, not a pre-approved final backend or UI shape; follow-on stories may narrow, rename, or reject them when contract tests and architecture evidence require it:

| Dashboard need | Candidate source | Phase 20 stance |
|---|---|---|
| Task detail | `GET /v1/tasks/{task_id}` | Candidate safe read for task detail panel. |
| Task events | `GET /v1/tasks/{task_id}/events` | Candidate safe read for timeline panel. |
| Task transitions | `GET /v1/tasks/{task_id}/transitions` | Candidate safe read for transition summaries. |
| Trace correlation | `GET /v1/trace/{trace_id}` | Candidate safe read for trace panel. |
| Task history | `GET /v1/tasks/{task_id}/history` | Candidate safe read for history/replay visibility, including fail-safe archive errors. |
| Replay state | `GET /v1/events/replay` | Candidate read for replay state visibility. |
| Replay validation | `GET /v1/events/replay/validate` | Candidate read for validation visibility only. |
| Snapshot listing | `GET /v1/events/replay/snapshots` | Candidate listing only; snapshot creation remains forbidden. |
| Health | `GET /v1/health` | Candidate health/stale signal read. |
| Logs digest | `GET /v1/tasks/{task_id}/logs/digest` | Excluded from first implementation story; non-core due to possible LLM/external-service dependency and latency. |
| Aggregate task/session lists | No approved Phase 20 contract yet | Must render unavailable/needs-contract until separately approved. |

## Architecture Decisions

### Decision 1 — First Phase 20 story is docs/status-only

The first Phase 20 execution story must create/reconcile PRD, architecture, epics, story/status evidence, and stale `current_phase` metadata before any implementation work.

Consequences:

- No runtime, dashboard, test, API, CI, dependency, script, deployment, or lockfile changes in the first story.
- Any later live wiring requires separately approved stories.
- Phase 20 status must distinguish planning authorization from implementation authorization.

### Decision 2 — Contract tests precede live wiring

The first implementation artifact after planning must be contract tests and static/effect guards, not UI wiring.

Required future tests:

- route/method allowlist;
- forbidden mutation/control vocabulary;
- static import/grep checks for writers, lifecycle helpers, snapshot creation, job dispatch, idempotency writes, cache-warming write paths;
- provenance/freshness assertions;
- unavailable/needs-contract aggregate states;
- degraded/error-state distinctions.

### Decision 3 — Aggregate/session reads are unavailable until separately approved

A live overview or session list may not synthesize authoritative state from unsafe discovery, mutating operations, side-effectful reads, logs scraping, or event-spine guesses. Until a safe aggregate/session-list GET contract is separately approved, the UI must show explicit unavailable/needs-contract copy.

Any future aggregate/session-list GET contract must prove:

- no hidden writes;
- no background dispatch;
- no cache-warming write/read side effects;
- no mutation route reachability;
- clear pagination/freshness semantics;
- clear provenance to registry/event/read source.

### Decision 4 — Logs digest is non-core and excluded from first implementation

Logs digest is a useful existing read, but it may involve LLM/external-service dependency and latency. It must stay out of the first implementation story unless later architecture explicitly justifies it and tests degraded/external-service behavior.

### Decision 5 — Destructive lifecycle remains a separate high-risk lane

Phase 20 does not approve destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled retention, object-storage lifecycle jobs, credentialed production operation, or any dashboard control that triggers those behaviors.

Phase 17/18 destructive lifecycle gates remain authoritative.

### Decision 6 — Provenance/freshness are required interface fields

A future dashboard live-read adapter must expose enough metadata for the UI to avoid false authority:

- source route/category;
- retrieved-at or emitted-at timestamp when available;
- freshness/staleness status;
- task/session/event/trace reference where applicable;
- unavailable/partial/invalid state category when data is not authoritative.

## Forbidden Surfaces

Future Phase 20 stories must not add or expose:

- approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credential entry, token minting, or production operation controls;
- frontend forms/buttons/links that trigger state transitions;
- POST, PUT, PATCH, DELETE dashboard API calls;
- registry/event-log writers, idempotency-cache writes, lifecycle apply/prune helpers, snapshot creation, archive mutation, job dispatch, or cache-warming writes;
- public share/export, OAuth, external hosting, multi-user auth, or credential storage;
- new dependencies or deployment surfaces without later amendment.

## Allowed future implementation order

This amendment is planning-only. Later implementation requires separate approved stories and review gates. Later stories may proceed only in this order, with contract tests first before live UI wiring:

1. Docs/status planning story: PRD, architecture, epics, sprint status, story artifact.
2. Contract-test story: route/method/effect allowlists and unavailable/provenance/stale-state assertions, using candidate/provisional route labels until tests prove the stable surface.
3. Adapter-boundary story: minimal read adapter boundary if approved by tests and architecture; names remain provisional until then.
4. Narrow panel stories: one candidate/provisional panel family at a time using existing safe reads.
5. Final quality gate: no-mutation, accessibility/responsiveness, provenance/freshness, review, UltraQA, CI.

## Verification Strategy for Future Stories

Future implementation stories must include:

1. Allowed-file diff checks matching story scope.
2. Route/method allowlist tests proving only approved reads are reachable.
3. No-hidden-write scans proving dashboard code cannot import/call writer, lifecycle, snapshot creation, background job, cache-warming, or mutation helpers.
4. Provenance/freshness tests for every displayed live value.
5. Unavailable/needs-contract tests for aggregate/session-list panels until a contract is approved.
6. Digest exclusion tests for the first implementation story.
7. Error/stale/degraded-state tests proving invalid/partial reads do not render as healthy.
8. Independent code-reviewer and architect review before completion.

## Handoff to Epics and Stories

The next BMAD artifact is `_bmad-output/planning-artifacts/phase-20-epics.md`. It should decompose Phase 20 into small, auditable stories that preserve the docs/status-first and contract-tests-before-wiring order.
