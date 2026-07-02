---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: 'complete'
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-07-02'
phase: 48
finalEpicCount: 7
finalStoryCount: 42
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/phase-46-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-46-architecture-amendment.md
  - _bmad-output/planning-artifacts/phase-46-epics.md
  - _bmad-output/planning-artifacts/phase-47-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-47-architecture-amendment.md
  - _bmad-output/planning-artifacts/phase-47-epics.md
  - docs/feature-status.md
  - docs/api-contracts.md
  - _bmad-output/implementation-artifacts/deferred-work.md
  - .omx/plans/story-125-phase-46-dashboard-task-list-expansion-planning-plan.md
  - .omx/plans/story-125-3-125-4-inventory-test-guard-plan-20260701T161454Z.md
  - .omx/plans/story-126-2-browser-full-selector-composition-implementation-plan.md
sourceNotes:
  - 'Generated from user-requested deferred/fail-closed production-readiness zones; no documents were excluded during autonomous intake.'
  - 'This artifact is additive and phase-scoped; it does not overwrite the original baseline epics.md.'
---

# oh-my-bmad — Phase 48 Production-Readiness Epic and Story Breakdown

## Overview

Phase 48 converts the remaining explicit deferred / fail-closed production-readiness zones into implementation-ready BMAD epics and stories. It builds on the shipped Phase 47 / Epic 126 baseline: dashboard browser full selector composition is green, while search/discovery runtime, hidden selector policy, automatic traversal / infinite scroll, broad dashboard rewiring, lifecycle destructive mutations, object-storage lifecycle jobs / scheduled retention, production operations, deployment changes, production credentials / GitHub write activation, split deployment / remote Postgres horizontal scaling, and DB connection mTLS remain unproductized.

The readiness threshold is: every listed zone has a production-grade implementation path with tests, auditability, rollback/disable behavior, and closure evidence, or an explicit permanent product contract proving the safe fail-closed posture is intentional and tested.

## Requirements Inventory

### Functional Requirements

- **FR395:** The operator can perform task-list search/discovery through exact searchable fields, bounded query grammar, and visible authority/freshness/provenance.
- **FR396:** Search/discovery rejects hidden selectors, row-derived selectors, URL/hash/storage/cookie selectors, arbitrary query language, and unbounded query payloads.
- **FR397:** Browser search/discovery uses only visible controls and explicit operator-triggered reads.
- **FR398:** Automatic traversal / infinite scroll, if enabled, is explicit, bounded, cancellable, observable, rate-limited, and stale-state invalidating.
- **FR399:** Dashboard modules have a current route/control/metadata inventory and are rewired only through behavior-preserving slices.
- **FR400:** Broad dashboard cleanup removes duplicated/dead wiring without changing approved route contracts, selector provenance, or mutation boundaries.
- **FR401:** Destructive lifecycle dry-run produces immutable plan hash, affected identities, replay validation, rollback/restore evidence, and risk summary.
- **FR402:** Lifecycle apply/prune/delete/rollback executes only when bound to approved dry-run plan hash and current evidence.
- **FR403:** Lifecycle mutation is idempotent, auditable, crash-resumable, and fail-closed on stale/missing/partial/mismatched evidence.
- **FR404:** Operator can inspect lifecycle mutation status and audit records without triggering mutation.
- **FR405:** Object-storage lifecycle policies define retention windows, holds/exclusions, adapter support, deletion/transition timing, and dry-run/apply modes.
- **FR406:** Scheduled retention jobs are idempotent, single-owner, lock-protected, bounded, observable, and safe under clock drift/crash/retry/eventual consistency.
- **FR407:** Retention mutates only objects proven by manifest and policy evidence; otherwise it emits fail-closed ProblemDetails/blocker events.
- **FR408:** Production operations are explicit operator workflows with preflights, dry-run where possible, audit events, and emergency disable.
- **FR409:** Production credentials are scoped, provisioned, rotated, revoked, audited, and prevented from leaking to unauthorized subprocesses/logs/events/artifacts.
- **FR410:** GitHub write activation uses scoped credentials, explicit enablement, approval-gated tools, simulation parity, and controlled real-write smoke tests.
- **FR411:** Deployment changes use profile gates, compatibility checks, backup/restore evidence, rollback steps, and post-deploy health evidence.
- **FR412:** Split deployment with remote Postgres preserves single-writer, event-log, idempotency, and capability-tier invariants.
- **FR413:** Remote Postgres production mode includes pooling, migrations, backup/restore drills, latency budgets, and failure behavior.
- **FR414:** Horizontal worker/control scaling is bounded by concurrency limits, worktree/session locks, idempotency, and audit evidence.
- **FR415:** DB connection mTLS is profile-gated with client/server certificate validation, no plaintext fallback, rotation, and safe failure behavior.
- **FR416:** DB mTLS certificate issuance, rotation, expiry, revocation, and misconfiguration are observable and testable without exposing private keys.

### NonFunctional Requirements

- **NFR-P48-1:** Bounded search/discovery preserves aggregate task-list latency budgets through row caps, query length caps, and bounded server work.
- **NFR-R48-1:** Mutation, retention, deployment, GitHub write, scaling, and mTLS stories include rollback/disable or fail-closed evidence.
- **NFR-R48-2:** Crash/retry during lifecycle mutation or scheduled retention never duplicates destructive effects or skips audit emission.
- **NFR-S48-1:** Hidden selectors and arbitrary discovery grammar remain denied unless an exact future contract names and tests them.
- **NFR-S48-2:** Production credentials never reach unauthorized subprocesses, logs, events, snapshots, dashboard payloads, or artifacts.
- **NFR-S48-3:** Destructive lifecycle and production write actions require explicit operator approval bound to exact parameters.
- **NFR-S48-4:** DB mTLS private keys and CA material are never committed; scanner coverage rejects cert/key material except approved test fixtures.
- **NFR-O48-1:** New production operations emit structured audit events with trace/request/operator identifiers and durable status records.
- **NFR-M48-1:** Each story is scoped to one production-readiness boundary and preserves Phase 47 contracts unless explicitly named.

### Additional Requirements

- Phase 47 / Epic 126 is the immediate baseline and remains unchanged by this planning artifact.
- `docs/feature-status.md` and `docs/api-contracts.md` are derivative summaries; phase PRD/architecture/epic/story artifacts are canonical for new decisions.
- Future implementation stories require a fresh sequential Ralplan gate: Architect APPROVE/CLEAR first, then Critic APPROVE/CLEAR, for the exact story boundary.
- Destructive/production stories require explicit approval, audit, rollback/disable, and negative tests.
- Split deployment, real GitHub writes, scheduled jobs, remote Postgres production mode, and DB mTLS are opt-in/profile-gated and must preserve local/default compatibility.

### UX Design Requirements

- **UX-DR48-1:** Search/discovery controls show field, query, bounded result count, authority/freshness/provenance, and explicit empty/unavailable/error states.
- **UX-DR48-2:** Traversal/infinite-scroll mode shows enabled state, read/page budget, active selectors, cancel control, and stale-state copy.
- **UX-DR48-3:** Destructive/production write controls name exact action, target, plan hash/request id, rollback status, and approval actor.
- **UX-DR48-4:** Production operation surfaces distinguish dry-run, pending approval, executing, succeeded, failed, rolled back, and disabled.
- **UX-DR48-5:** mTLS/deployment readiness diagnostics are operator-readable and never display certificate secrets or credentials.

## Implementation Gate

Every future Phase 48 runtime, destructive, production-operation, deployment, retention, scaling, GitHub write, or mTLS story must pass a fresh sequential Ralplan gate before implementation: Architect APPROVE/CLEAR first, then Critic APPROVE/CLEAR. Contract stories may create the required plan/test spec, but they do not authorize downstream execution until that gate is recorded for the exact story boundary.

## FR Coverage Map

- **FR395-FR398:** Epic 127 — search/discovery, selector provenance, and traversal productization.
- **FR399-FR400:** Epic 128 — broad dashboard rewiring cleanup.
- **FR401-FR404:** Epic 129 — destructive lifecycle mutation controls.
- **FR405-FR407:** Epic 130 — object-storage lifecycle jobs and retention.
- **FR408-FR411:** Epic 131 — production operations, deployment, credentials, and GitHub write activation.
- **FR412-FR414:** Epic 132 — split deployment and remote Postgres horizontal scaling.
- **FR415-FR416:** Epic 133 — DB connection mTLS.

## Epic List

### Epic 127: Search, Discovery, Selector Provenance, and Controlled Traversal
Prepare the bounded search/discovery implementation path from visible operator state while permanently preventing hidden selectors and unbounded traversal.
**FRs covered:** FR395, FR396, FR397, FR398.

### Epic 128: Behavior-Preserving Broad Dashboard Rewiring Cleanup
Make dashboard wiring production-maintainable through inventory, test guards, and narrow cleanup slices.
**FRs covered:** FR399, FR400.

### Epic 129: Destructive Lifecycle Mutation Controls
Prepare the approved lifecycle apply/prune/delete/rollback implementation path with dry-run plan hashes, replay validation, rollback evidence, and audit.
**FRs covered:** FR401, FR402, FR403, FR404.

### Epic 130: Object-Storage Lifecycle Jobs and Scheduled Retention
Prepare the policy-driven retention automation implementation path with dry-run/apply modes, idempotent jobs, adapter safety, and auditability.
**FRs covered:** FR405, FR406, FR407.

### Epic 131: Production Operations, Deployment Changes, Credentials, and GitHub Write Activation
Prepare the controlled production-operation implementation path through runbooks, preflights, scoped credentials, deployment control, controlled GitHub-write gates, and emergency disable.
**FRs covered:** FR408, FR409, FR410, FR411.

### Epic 132: Split Deployment and Remote Postgres Horizontal Scaling
Prepare the optional split-topology and remote Postgres production-mode implementation path while preserving core platform invariants.
**FRs covered:** FR412, FR413, FR414.

### Epic 133: DB Connection mTLS
Prepare the profile-gated Postgres mTLS implementation path with certificate lifecycle, no plaintext fallback, and safe observability.
**FRs covered:** FR415, FR416.

## Epic 127: Search, Discovery, Selector Provenance, and Controlled Traversal

Goal: The operator can find task-list records through production-grade visible search/discovery while the platform proves no hidden selector, row traversal, or unbounded infinite-scroll behavior exists outside explicit bounded controls.

### Story 127.1: Search/Discovery Product and Architecture Contract
As the operator, I want searchable fields, query grammar, privacy rules, selector provenance, and traversal boundaries explicitly decided, so that search/discovery can be implemented safely.

**Acceptance Criteria:**
**Given** Phase 47 is baseline and search/discovery is deferred
**When** Story 127.1 completes
**Then** PRD/architecture define exact fields, operators, lengths, encoding, sort/pagination composition, response metadata, privacy/redaction, and fail-closed states
**And** row-derived selectors, URL/hash/storage/cookie selectors, hidden inputs, arbitrary grammar, background prefetch, and automatic traversal are prohibited.
**Given** the contract is ready
**When** the planning gate runs
**Then** Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR is recorded.


**Story 127.1 contract detail:**
- Future search/discovery remains route-local to bodyless `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}` with optional `status`, `limit`, `offset`, and `sort` only in that canonical order after `q`.
- Search fields/operators are exactly `task_id:eq`, `title:contains|prefix`, `status:eq`, `actor_id:eq|prefix`, `last_event_type:eq`, `updated_at:gte|lte`, and `created_at:gte|lte`.
- `q` is required, raw ASCII-only, globally `1..96` bytes, with field caps `task_id/title/actor_id 1..64`, `last_event_type 1..80`, and timestamp fields exactly 20 chars in UTC `YYYY-MM-DDTHH:MM:SSZ`; full raw query strings are capped at `1..256` bytes.
- Percent encoding / percent-encoded bytes, `+`, raw spaces, controls, Unicode/non-ASCII, repeated/encoded keys, aliases, reordered keys, empty values, GET bodies, boolean DSL, regex, fuzzy search, SQL-like syntax, wildcards, nesting, multiple search fields, and arbitrary JSON fail closed.
- `field=status` plus a separate `status=` selector is fail-closed duplicate status semantics.
- Privacy boundary denies worktree/resource paths, logs, event payloads, summaries/generated text, decisions/approval text, credentials/secrets, raw JSON blobs, arbitrary metadata, and non-allowlisted fields.
- Response metadata must include selected field/op/query, selected status/limit/offset/sort when present, returned count, pagination, freshness, authority, provenance, request/trace/correlation ids, redaction state, and explicit fail-closed display state.
- Browser provenance remains visible-control-only and explicit-action-only; URL/hash/storage/cookie, hidden input, row-derived, server-provided route string, and background-derived selectors are prohibited.
- Traversal stays disabled until Story 127.4; search results cannot trigger background prefetch, infinite scroll, timers, workers, observers, retry loops, cache warming, websocket/EventSource/XMLHttpRequest side channels, or automatic next-page reads.

### Story 127.2: API-local Task Search/Discovery Runtime Boundary
As the operator, I want a bounded API-local task search/discovery route, so that I can find tasks by approved fields without weakening existing contracts.

**Acceptance Criteria:**
**Given** Story 127.1 is approved
**When** the API receives an approved bodyless GET search/discovery request
**Then** it validates exact query order, field allowlist, query length, encoding, finite domains, and no GET body
**And** it returns bounded rows plus selected search metadata, freshness, authority, provenance, request/trace/correlation, and pagination metadata.
**Given** unknown fields, arbitrary grammar, encoded/repeated keys, row-derived ids, hidden selectors, GET body, URL/hash/storage values, or unsupported composition
**When** the request is handled
**Then** it fails closed without fallback to broader search.

**Story 127.2 implementation detail:**

- API-local runtime is implemented in `services/registry-api/src/registry_api/routes/tasks.py` for bodyless `GET /v1/tasks` only.
- Accepted search shapes are exact `field`, `op`, `q` raw ASCII query prefixes with only the registered suffix families: no suffix, `status`, `limit`, `status&limit`, `limit&offset`, `status&limit&offset`, `sort`, and `status&limit&offset&sort`.
- Search uses only allowlisted fields/operators; title/actor substring and prefix filters are literal, timestamps are semantically parsed as UTC second-precision values, and `last_event_type` filters only the current `Task.last_event_id` event before pagination.
- Search responses expose only bounded task summary rows plus selected search/suffix metadata, redaction state, freshness, authority, provenance, request/trace/correlation ids, and pagination metadata.
- Browser search controls, selector provenance UX, automatic traversal, hidden selectors, prefetch/infinite scroll, adjacent route traversal, mutation, credentials, dependencies, and production operations remain deferred.

### Story 127.3: Browser Search/Discovery Controls from Visible Operator State
As the operator, I want dashboard search/discovery controls that use only visible selector state, so that browser search is explicit and auditable.

**Acceptance Criteria:**
**Given** the API route exists
**When** I enter permitted visible controls and trigger search
**Then** the browser issues exactly one explicit GET with `credentials: "omit"`, no body, and canonical query shape
**And** result state shows selected field/query/status/limit/offset/sort, row count, freshness, authority, provenance, and request/trace/correlation evidence.
**Given** a selector is hidden, stale, malformed, response-mismatched, storage-derived, or row-derived
**When** search is attempted
**Then** the browser renders fail-closed state and starts no automatic traversal.

### Story 127.4: Explicit Bounded Traversal / Infinite-Scroll Mode
As the operator, I want any multi-page traversal to be explicit, bounded, cancellable, and observable, so that convenience does not become hidden automation.

**Acceptance Criteria:**
**Given** search/discovery and manual pagination exist
**When** I enable traversal mode
**Then** the UI shows enabled state, read/page budget, rate limit, selector tuple, cancel control, and stale-state copy
**And** traversal stops on budget exhaustion, selector edit, stale/non-authoritative response, network error, or cancel.
**Given** traversal is not explicitly enabled
**When** `has_more` or `next_offset` is returned
**Then** no automatic next read, prefetch, timer, worker, observer, websocket/EventSource/XHR side channel, retry loop, or cache warming runs.

**Story 127.4 implementation detail:**

- Dashboard runtime is implemented in `dashboard/static/aggregate-task-list.js` and markup in `dashboard/static/index.html`; no backend/API route behavior changes.
- Traversal controls are visible-only: `aggregate-task-list-traversal-budget-control`, `aggregate-task-list-traversal-rate-control`, `aggregate-task-list-traversal-enable`, `aggregate-task-list-traversal-cancel`, and `aggregate-task-list-traversal-state`.
- Traversal availability is derived only from a healthy authoritative search response with `has_more=true` and numeric `next_offset`; search pagination never enables manual previous/next.
- Enable validates the visible budget/rate controls and exact unchanged search selector tuple; each traversal page updates the visible offset control and reuses the same canonical raw search route with no body and omitted credentials.
- Traversal stops on budget exhaustion, no next offset, visible stop control, selector edit/mismatch, stale/non-authoritative/malformed response, unauthorized/backend/network failure, or hidden/invalid traversal controls.
- Disabled mode remains inert: no automatic next read, prefetch, timer, worker, observer, web-socket/event-source/XMLHttpRequest side channel, repeated-attempt loop, cache warming, hidden selector, row-derived selector, URL/hash/storage/cookie selector, broad dashboard wiring, mutation, dependency, credential, deployment, or production-operation expansion.

### Story 127.5: Search/Discovery and Traversal Closure Evidence
As the operator, I want proof that search/discovery and traversal are production-ready or intentionally disabled, so that this formerly deferred area can close.

**Acceptance Criteria:**
**Given** Stories 127.1-127.4 are complete
**When** closure validation runs
**Then** API/dashboard tests, hidden-selector negative tests, traversal budget/cancel tests, forbidden-marker tests, code-review APPROVE/CLEAR, UltraQA PASS, and CI evidence are recorded
**And** docs distinguish implemented contracts from still-forbidden arbitrary discovery.

## Epic 128: Behavior-Preserving Broad Dashboard Rewiring Cleanup

Goal: The dashboard can be maintained as production UI without hidden coupling, dead wiring, or accidental broad behavior changes.

### Story 128.1: Dashboard Wiring Inventory and Cleanup Contract Refresh
As the operator, I want a current dashboard module/control/route inventory and cleanup contract, so that cleanup starts from facts rather than broad assumptions.

**Acceptance Criteria:**
**Given** Story 125.4 inventory exists
**When** Story 128.1 completes
**Then** it refreshes all dashboard modules, script order, DOM ids, route literals, selector sources, metadata targets, live/dead/deferred classification, and owner story/phase
**And** inventory tests fail on drift.
**Given** the contract is ready
**When** the planning gate runs
**Then** Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR is recorded.

### Story 128.2: Aggregate Task-list Read-State Helper Seed
As the operator, I want the aggregate task-list panel to seed shared read-state helpers, so that extraction starts with the most-tested surface.

**Acceptance Criteria:**
**Given** duplicate aggregate task-list read-state/fail-closed logic exists
**When** helper extraction lands
**Then** it is limited to aggregate task-list files/tests
**And** selector-free/status/limit/offset/sort/search/traversal contracts remain unchanged.

### Story 128.3: Task Detail, Event Timeline, and Trace Cleanup Slice
As the operator, I want task detail, event timeline, and trace panels cleaned up in one bounded read-only slice, so that task-inspection surfaces remain maintainable.

**Acceptance Criteria:**
**Given** inventory identifies cleanup candidates in these panels
**When** this slice lands
**Then** it names exact files, DOM ids, route contracts, and removed wiring
**And** tests prove task detail, event timeline, and trace failure/empty/stale states remain unchanged.

### Story 128.4: History/Replay and Lifecycle/Snapshot Cleanup Slice
As the operator, I want history/replay and lifecycle/snapshot panels cleaned up in a bounded slice, so that replay/lifecycle visibility remains read-safe.

**Acceptance Criteria:**
**Given** inventory identifies cleanup candidates in these panels
**When** this slice lands
**Then** it preserves archive ProblemDetails, lifecycle readiness copy, replay validation visibility, and snapshot boundaries
**And** no apply/prune/delete/rollback, retention job, archive mutation, or hidden traversal appears.

### Story 128.5: Session and Digest Panel Cleanup Slice
As the operator, I want session and digest panels cleaned up in a bounded slice, so that session/digest visibility remains production-maintainable.

**Acceptance Criteria:**
**Given** inventory identifies cleanup candidates in session or digest panels
**When** this slice lands
**Then** it preserves session list/detail and digest/digest-stream route contracts, provider-unavailable states, and visible id provenance
**And** no hidden selector, generated browser summary, automatic traversal, side channel, or mutation control is introduced.

### Story 128.6: Dashboard Shared Helper Expansion After Panel Slices
As the operator, I want shared helpers expanded only after bounded panel slices prove behavior preservation, so that common code does not become broad rewrite.

**Acceptance Criteria:**
**Given** Stories 128.2-128.5 are complete
**When** helper expansion lands
**Then** it extracts only duplicated read-state/fail-closed rendering patterns proven equivalent by tests
**And** it does not change routes, selector provenance, copy meaning, or mutation boundaries.

### Story 128.7: Dashboard Rewiring Production Closure
As the operator, I want broad dashboard rewiring no longer deferred, so that dashboard maintenance is production-ready.

**Acceptance Criteria:**
**Given** Stories 128.1-128.6 are complete
**When** closure validation runs
**Then** inventory tests, forbidden markers, accessibility smoke checks, code-review APPROVE/CLEAR, UltraQA PASS, and CI evidence are recorded
**And** docs mark broad dashboard rewiring cleanup complete while preserving future-feature gates.

## Epic 129: Destructive Lifecycle Mutation Controls

Goal: Destructive lifecycle operations become approval-bound, auditable, rollback-aware production capabilities.

### Story 129.1: Destructive Lifecycle Plan-Hash Authorization Contract
As the operator, I want destructive lifecycle actions bound to exact dry-run plans and rollback evidence, so that no mutation executes from ambiguous intent.

**Acceptance Criteria:**
**Given** existing replay/lifecycle readiness contracts
**When** Story 129.1 completes
**Then** artifacts define dry-run plan hash, affected identities, replay validation, rollback proof, operator identity, approval event, expiry, stale evidence rules, and failure modes
**And** apply/prune/delete/truncate/move/rewrite/chmod/archive/manifest mutations remain blocked until Architect APPROVE/CLEAR then Critic APPROVE/CLEAR is recorded.

### Story 129.2: Lifecycle Dry-run Plan Generation and Evidence Store
As the operator, I want destructive lifecycle dry-runs to produce durable evidence, so that exact impact is reviewable before approval.

**Acceptance Criteria:**
**Given** an eligible lifecycle target
**When** dry-run executes
**Then** it records plan hash, target set, expected mutations, replay validation, rollback prerequisites, risk summary, expiry, trace ids, and audit event
**And** no destructive mutation occurs.

### Story 129.3: Approval-Gated Lifecycle Apply/Prune/Delete Execution
As the operator, I want approved lifecycle mutations to execute only against exact dry-run evidence, so that destructive work is controlled.

**Acceptance Criteria:**
**Given** a current dry-run plan hash and explicit approval
**When** apply/prune/delete executes
**Then** executor revalidates plan hash, target identities, replay state, rollback evidence, approval, locks, and idempotency keys
**And** stale/missing/mismatched evidence fails closed without partial mutation.

### Story 129.4: Lifecycle Rollback/Restore Execution and Verification
As the operator, I want rollback/restore executable and verified after destructive lifecycle operations, so that production data loss is recoverable within scope.

**Acceptance Criteria:**
**Given** completed or partially failed lifecycle mutation
**When** rollback/restore is approved
**Then** system validates prerequisites, restores from evidence, verifies replay consistency, emits audit events, and reports residual risk
**And** unsupported rollback states fail closed with clear copy.

### Story 129.5: Dashboard and API Lifecycle Mutation Visibility
As the operator, I want to inspect lifecycle mutation states without accidental mutation, so that operations are transparent.

**Acceptance Criteria:**
**Given** lifecycle records exist
**When** I open dashboard/API reads
**Then** I can see dry-run status, plan hash, affected counts, approval, execution, rollback readiness, audit trail, and ProblemDetails
**And** read-only inspection never triggers mutation.

### Story 129.6: Destructive Lifecycle Closure Evidence
As the operator, I want destructive lifecycle mutation no longer deferred, so that lifecycle management is production-ready.

**Acceptance Criteria:**
**Given** Stories 129.1-129.5 are complete
**When** closure validation runs
**Then** property tests, crash/retry tests, approval-bypass negatives, rollback drills, audit checks, code-review, UltraQA, and CI evidence are recorded
**And** docs list supported and unsupported mutation classes.

## Epic 130: Object-Storage Lifecycle Jobs and Scheduled Retention

Goal: Retention and object-storage lifecycle automation are policy-driven, scheduled, idempotent, auditable, and safe under production failure modes.

### Story 130.1: Retention Policy and Object-Storage Adapter Contract
As the operator, I want retention policy and adapter behavior specified before scheduled deletion exists, so that automation cannot delete outside policy.

**Acceptance Criteria:**
**Given** object-storage lifecycle remains future work
**When** Story 130.1 completes
**Then** artifacts define retention windows, holds, object identity schema, adapter support, dry-run/apply modes, clock semantics, eventual consistency, and fail-closed conditions
**And** scheduled jobs remain disabled until Architect APPROVE/CLEAR then Critic APPROVE/CLEAR is recorded.

### Story 130.2: Object-Storage Lifecycle Dry-run and Manifest Validation
As the operator, I want retention dry-runs to validate manifests and planned object changes, so that policy impact is reviewable.

**Acceptance Criteria:**
**Given** manifests and retention policy
**When** dry-run executes
**Then** it validates checksums, keys, duplicates/overlap, exemptions, holds, and planned transition/delete actions
**And** invalid evidence fails closed without storage mutation.

### Story 130.3: Scheduled Retention Job Runner
As the operator, I want scheduled retention jobs to run safely without manual cron scripting, so that lifecycle automation is controlled.

**Acceptance Criteria:**
**Given** retention policy is enabled
**When** scheduler triggers
**Then** one lock-protected job runs with bounded concurrency, idempotency key, retry/backoff, trace id, dry-run/apply mode, and audit events
**And** crash/retry resumes safely without duplicate destructive effects.

### Story 130.4: Retention Apply, Deletion/Transition Audit, and Recovery Evidence
As the operator, I want object lifecycle changes auditable and recoverable where policy allows, so that retention is production-safe.

**Acceptance Criteria:**
**Given** approved retention apply job
**When** objects are transitioned or deleted
**Then** each action records object identity, manifest evidence, policy basis, adapter response, trace id, and rollback/recovery status
**And** partial failure blocks further destructive work until review or safe retry.

### Story 130.5: Retention Observability and Closure
As the operator, I want lifecycle jobs visible in health/readiness and status docs, so that scheduled retention is operable.

**Acceptance Criteria:**
**Given** scheduled jobs are implemented
**When** readiness/status is queried
**Then** it reports enabled state, last/next run, failures, skipped protected objects, audit count, and degraded states without secrets
**And** closure records tests, retention drills, code-review, QA, and CI evidence.

## Epic 131: Production Operations, Deployment Changes, Credentials, and GitHub Write Activation

Goal: Real production operation is explicit, scoped, auditable, and reversible.

### Story 131.1: Production Operations Runbook and Preflight Contract
As the operator, I want production operations defined as runbook-backed preflighted workflows, so that changes are repeatable and auditable.

**Acceptance Criteria:**
**Given** production operations remain deferred
**When** Story 131.1 completes
**Then** artifacts define operation classes, preflights, dry-run requirements, approval levels, audit schema, emergency disable, rollback, and docs ownership
**And** unsupported production operations remain blocked until Architect APPROVE/CLEAR then Critic APPROVE/CLEAR is recorded.

### Story 131.2: Credential Provisioning, Scoping, Rotation, and Revocation
As the operator, I want scoped credentials managed safely, so that write activation and deployment do not leak broad secrets.

**Acceptance Criteria:**
**Given** production credentials are provisioned
**When** credential checks run
**Then** each credential has scope, env location, subprocess allowlist, rotation, revocation, scanner coverage, and `secret.accessed` audit behavior
**And** broad tokens cannot reach unauthorized processes, logs, snapshots, dashboard payloads, or artifacts.

### Story 131.3: GitHub Write Activation from Simulation to Controlled Production
As the operator, I want the controlled path from GitHub-write simulation to real writes defined and verified, so that PR/issue operations are not activated without scoped evidence.

**Acceptance Criteria:**
**Given** GitHub write tools default to simulation
**When** a future implementation story enables production activation for a controlled repo
**Then** scoped credentials, repo authority, approval gates, simulation parity, real-write smoke tests, rate-limit handling, audit events, and emergency disable are verified
**And** out-of-scope writes fail closed.

### Story 131.4: Deployment Change Control and Rollback Profiles
As the operator, I want deployment changes profile-gated and rollback-compatible, so that rollout does not silently break production.

**Acceptance Criteria:**
**Given** a deployment-affecting change
**When** it is staged
**Then** preflight validates config, image tags/digests, migrations, backups, readiness, secrets, compatibility, and rollback steps
**And** post-deploy health evidence is recorded.

### Story 131.5: Production Operation Command Surface and Audit Dashboard
As the operator, I want production operations visible and controllable from approved surfaces, so that I can inspect, approve, stop, or disable workflows.

**Acceptance Criteria:**
**Given** production operation records exist
**When** I use console/Telegram/dashboard reads
**Then** I can inspect preflight, approval, execution, failure, rollback, disable, and audit state
**And** controls require explicit approval and never expose credentials.

### Story 131.6: Production Operations Closure Evidence
As the operator, I want production ops, deployment changes, credentials, and GitHub writes ready to be reclassified after evidence, so that operational deferrals are closed only when verified.

**Acceptance Criteria:**
**Given** Stories 131.1-131.5 are complete
**When** closure validation runs
**Then** credential leak canaries, scoped-token tests, controlled real-write smoke, deployment rollback drill, docs review, code-review, UltraQA, and CI evidence are recorded
**And** docs distinguish enabled operations from unsupported ones.

## Epic 132: Split Deployment and Remote Postgres Horizontal Scaling

Goal: The platform can run beyond a single compose-local topology while preserving single-writer, event-log, idempotency, and capability invariants.

### Story 132.1: Split Deployment and Remote Postgres Topology Contract
As the operator, I want target split topology specified, so that scaling has clear boundaries and rollback paths.

**Acceptance Criteria:**
**Given** current docs retain single-backend framing
**When** Story 132.1 completes
**Then** architecture defines service placement, network boundaries, remote Postgres authority, pooling, migrations, backups, ingress, secrets, observability, and unsupported topologies
**And** single-host compose remains default until Architect APPROVE/CLEAR then Critic APPROVE/CLEAR is recorded for split work.

### Story 132.2: Remote Postgres Production Mode and Migration Strategy
As the operator, I want remote Postgres to be a tested production persistence option, so that data can move beyond local volumes safely.

**Acceptance Criteria:**
**Given** remote Postgres config is provided
**When** registry services start
**Then** they use bounded pools, remote-compatible migrations, credential redaction, SSL policy, backup/restore hooks, and readiness checks
**And** SQLite/local dev compatibility remains unchanged.

### Story 132.3: Registry and Remote Postgres Deployment Profile
As the operator, I want registry API/state and remote Postgres profile separated first, so that data authority is safe before other services split.

**Acceptance Criteria:**
**Given** split topology is selected
**When** registry/Postgres profile is applied
**Then** config separates registry API/state from remote Postgres with pools, migrations, backup/restore hooks, secret references, and readiness checks
**And** local single-host compose remains unchanged by default.

### Story 132.4: Worker, MCP, and Event-Bus Deployment Profile
As the operator, I want worker, MCP, and event-bus services split in a bounded profile, so that execution scaling does not break event or capability invariants.

**Acceptance Criteria:**
**Given** registry/Postgres profile is validated
**When** worker/MCP/event-bus split config is applied
**Then** connectivity, capability tiers, event emission, locks, and version compatibility are validated
**And** unsupported placements fail preflight.

### Story 132.5: Operator Surface and Dashboard Deployment Profile
As the operator, I want Telegram, console, and dashboard surfaces split in a bounded profile, so that operator access can scale without weakening auth/readiness.

**Acceptance Criteria:**
**Given** core split profiles are validated
**When** operator-surface split config is applied
**Then** Telegram/console/dashboard ingress, auth, health/readiness, trace propagation, and version compatibility are validated
**And** no production credential or secret appears in browser payloads or logs.

### Story 132.6: Horizontal Worker and Control-Plane Scaling
As the operator, I want bounded horizontal scaling where safe, so that production load can increase without duplicate work or state corruption.

**Acceptance Criteria:**
**Given** multiple worker/control instances are configured
**When** tasks execute
**Then** locks, idempotency, single-writer mutation, capability tiers, and event ordering prevent duplicates or direct state writes
**And** scale limits and unsupported combinations are enforced.

### Story 132.7: Split Deployment Failure, Load, Backup, and Restore Validation
As the operator, I want split deployment validated under failure and load, so that production scale works beyond the happy path.

**Acceptance Criteria:**
**Given** split topology is running
**When** drills run
**Then** tests cover database outage, network partition, pool exhaustion, worker crash, registry restart, migration rollback, backup restore, and latency budgets
**And** recovery emits audit/health evidence.

### Story 132.8: Split Deployment Closure Evidence
As the operator, I want split deployment and remote Postgres scaling ready to be reclassified after evidence, so that this topology is no longer deferred only when verified.

**Acceptance Criteria:**
**Given** Stories 132.1-132.7 are complete
**When** closure validation runs
**Then** runbooks, config examples, migration evidence, load/failure results, code-review, UltraQA, and CI/nightly evidence are recorded
**And** docs mark default single-host and optional split topology support clearly.

## Epic 133: DB Connection mTLS

Goal: Remote/Postgres database connections can require mutual TLS without breaking local defaults or leaking certificate material.

### Story 133.1: DB mTLS PKI and Rollout Contract
As the operator, I want the DB mTLS certificate and rollout model decided, so that secure transport has a controlled profile-gated implementation path without ad-hoc certificate handling.

**Acceptance Criteria:**
**Given** DB connection mTLS is deferred and any related internal-service mTLS evidence must be revalidated from canonical architecture/status artifacts
**When** Story 133.1 completes
**Then** architecture defines CA ownership, server/client certs, SAN requirements, storage, env/config keys, profile gating, rotation/expiry/revocation, no-plaintext fallback, and rollback
**And** local non-mTLS development remains default until Architect APPROVE/CLEAR then Critic APPROVE/CLEAR is recorded.

### Story 133.2: Postgres Server and Client mTLS Configuration
As the operator, I want Postgres and platform clients to enforce mTLS when enabled, so that database traffic is mutually authenticated.

**Acceptance Criteria:**
**Given** DB mTLS profile is enabled
**When** services connect to Postgres
**Then** server validates client certs, clients validate server cert/hostname, sslmode policy is enforced, private keys load only from approved secret locations, and plaintext fallback is impossible
**And** disabled profile preserves existing behavior.

### Story 133.3: Certificate Rotation, Expiry, and Revocation Flow
As the operator, I want DB mTLS certificates rotated and revoked safely, so that production security does not depend on static certs.

**Acceptance Criteria:**
**Given** certificates near expiry or revoked
**When** rotation/revocation runs
**Then** new certs are distributed, services reconnect safely, old certs are rejected, expiry warnings are emitted, and no private key material appears in logs/artifacts
**And** failed rotation leaves rollback/disable path.

### Story 133.4: DB mTLS Failure-Mode and Observability Tests
As the operator, I want handshake failures and misconfiguration obvious and safe, so that mTLS problems do not become silent outages.

**Acceptance Criteria:**
**Given** invalid CA, expired cert, wrong SAN, missing client cert, wrong permissions, or plaintext attempt
**When** services connect
**Then** readiness fails closed with sanitized diagnostics, audit events record failure class, and retries are bounded
**And** scanners reject committed key/cert material except approved fixtures.

### Story 133.5: DB mTLS Closure Evidence
As the operator, I want DB mTLS ready to be reclassified after evidence, so that database transport security is not marked production-ready until verified.

**Acceptance Criteria:**
**Given** Stories 133.1-133.4 are complete
**When** closure validation runs
**Then** mTLS enabled/disabled matrix tests, rotation drills, failure-mode tests, scanner evidence, docs, code-review, UltraQA, and CI evidence are recorded
**And** docs explain composition with split deployment and remote Postgres.

## Validation Summary

- Every user-listed deferred / fail-closed zone is covered:
  - search/discovery runtime: Epic 127
  - hidden selectors: Epic 127 hard prohibition and negative tests
  - automatic traversal / infinite scroll: Epic 127 explicit bounded mode
  - broad dashboard rewiring: Epic 128
  - lifecycle destructive mutations apply/prune/delete/rollback/etc.: Epic 129
  - object-storage lifecycle jobs / scheduled retention: Epic 130
  - production operations: Epic 131
  - deployment changes: Epics 131 and 132
  - production credentials / GitHub write activation: Epic 131
  - split deployment / remote Postgres horizontal scaling: Epic 132
  - DB connection mTLS: Epic 133
- Story order has no forward dependency within each epic: contract/inventory stories precede runtime or closure stories.
- Cross-epic recommended sequence: Epic 127 → Epic 128 → Epic 131 → Epic 129 → Epic 130 → Epic 132 → Epic 133. Epic 133 may begin after Epic 132.1 if DB mTLS must be designed in parallel with split topology.
- All stories preserve the standing safety posture: fail closed by default, exact allowlists, visible provenance, explicit approval for destructive/production actions, secret hygiene, auditability, rollback/disable paths, and review/QA/CI closure evidence.
