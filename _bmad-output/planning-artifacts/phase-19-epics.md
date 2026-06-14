# Phase 19 Epics — Read-Only Web Dashboard

Phase 19 remains **planning-only** and **no dashboard implementation** is completed in this slice. This artifact decomposes future implementation work for a read-only web dashboard, updates BMAD planning readiness, and preserves the rule that every future dashboard story must use existing safe read surfaces or separately approved future read contract decisions. If a required read does not exist safely, the UI must show an explicit unavailable state until a separate approved read contract exists.

This artifact does not add frontend implementation, backend route/API implementation, runtime behavior, dependencies, deployment changes, CI changes, approval/retry/cancel/budget override/apply/prune/delete/truncate/move/rewrite/chmod/archive mutation/manifest mutation/scheduled job/credentialed lifecycle/production operation controls.

## Requirements traceability

- **FR169 — read-only dashboard scope:** future UI work must expose visibility only, never mutation/control actions.
- **FR170 — task/session visibility:** future UI work must display task and session state from existing safe read surfaces or explicit unavailable states.
- **FR171 — event/trace visibility:** future UI work must show event timelines, transitions, trace correlation, and provenance-first evidence.
- **FR172 — replay/lifecycle visibility:** future UI work must surface replay validation, task history, archive/lifecycle readiness, and fail-closed ProblemDetails without apply/prune controls.
- **FR173 — safe error/empty states:** future UI work must render loading, empty, stale, unavailable, unauthorized, and error states without hidden writes or background jobs.
- **FR174 — no behavior change in this planning slice:** this artifact and sprint-status update are planning/status only and do not alter runtime behavior.
- **NFR-S27 — read-only by construction:** only allowlisted read routes/read methods may be reachable.
- **NFR-O24 — provenance-first display:** every material value must show source route, timestamp, trace/event/session reference, and freshness when available.
- **NFR-M20 — existing contract reuse:** existing safe read surfaces are preferred; new read contracts require separate approval.
- **NFR-R20 — fail-safe visibility:** missing/unsafe reads degrade to explicit unavailable states instead of synthetic or mutating behavior.
- **NFR-S28 — lifecycle gate preservation:** destructive lifecycle gates remain outside dashboard scope.

## Standard per-story safety guardrail

Every future implementation story in this artifact must preserve this exact rule: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Epic 88 — Read-only dashboard shell and boundary enforcement

### Story 88.1: Dashboard static shell and read-only banner

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: create the future dashboard shell, navigation, read-only banner, provenance affordance placeholders, and unavailable-state containers without connecting mutation or control surfaces.
- Governing FR/NFR: FR169, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface for static assets/configuration only; explicit unavailable state for panels whose safe read source is not yet approved; separately approved future read contract required for any new aggregate read.
- Acceptance criteria:
  - The shell visibly states that the dashboard is read-only and that unsafe or unavailable reads render explicit unavailable states.
  - Navigation exposes only visibility panels for tasks, sessions, events, traces, replay/lifecycle readiness, health, and Audit and Help.
  - No buttons, forms, menus, or links imply approval/retry/cancel/budget override/apply/prune/delete/truncate/move/rewrite/chmod/archive mutation/manifest mutation/scheduled job/credentialed lifecycle/production operation.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 88.2: Route/method allowlist and no-mutation guard tests

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: define and test a route/method allowlist so the future dashboard can call only approved read routes and cannot reach write/control routes.
- Governing FR/NFR: FR169, FR174, NFR-S27, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface inventory from the architecture amendment; separately approved future read contract required before adding any new dashboard read; explicit unavailable state for unapproved reads.
- Acceptance criteria:
  - Tests fail if the dashboard client invokes POST, PUT, PATCH, DELETE, or any unapproved method/route.
  - Tests prove no hidden write, cache-warming write/read-side effect, or background-job dispatch occurs during page load, refresh, polling, or error recovery.
  - Tests enumerate the forbidden control vocabulary so future UI affordances cannot smuggle in mutation semantics.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Epic 89 — Task/session overview and task detail visibility

### Story 89.1: Overview/task list with explicit unavailable fallback

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: render a dashboard overview and task list only when an existing safe aggregate task read exists or after a separately approved future read contract; otherwise show an explicit unavailable state explaining the missing safe aggregate read.
- Governing FR/NFR: FR169, FR170, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- Read-surface basis: Explicit unavailable state by default for aggregate task listing if no existing safe read surface is available; separately approved future read contract required for a new aggregate list; existing safe read surface only when confirmed.
- Acceptance criteria:
  - The overview does not infer or synthesize task lists from mutating operations or side-effectful discovery.
  - If no safe aggregate read exists, the panel states that the read is unavailable and links to Audit and Help guidance.
  - Any displayed task row includes provenance, timestamp/freshness, state, and a route reference.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 89.2: Task detail/session panel using `GET /v1/tasks/{task_id}`

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: display task detail, state, metadata, latest status, and related session references using existing task detail reads and no writes.
- Governing FR/NFR: FR169, FR170, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- Read-surface basis: Existing safe read surface: `GET /v1/tasks/{task_id}` for task detail; explicit unavailable state for session fields not exposed by a safe read; separately approved future read contract required for additional session aggregation.
- Acceptance criteria:
  - Task detail renders provenance for `GET /v1/tasks/{task_id}` and clearly marks stale, missing, unauthorized, or unavailable fields.
  - Related session references are displayed only if available through existing safe read surfaces.
  - The panel has no approval, retry, cancel, budget override, or lifecycle control affordance.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 89.3: Sessions visibility panel

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: explicitly cover the UX **Sessions** section by showing active/historical session metadata, heartbeat/freshness, linked tasks, provenance, and unavailable states only from safe reads.
- Governing FR/NFR: FR169, FR170, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface where session/task read resources already expose session state; explicit unavailable state if no safe dashboard-consumable session read exists; separately approved future read contract required for any new session HTTP route or aggregate session list.
- Acceptance criteria:
  - The Sessions panel explains whether data comes from an existing safe session read surface or is unavailable pending a separate read contract.
  - Session rows include task linkage, state/freshness, source, and trace/session identifiers when available.
  - No session lifecycle, credentialed lifecycle, production operation, or control-plane action appears.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Epic 90 — Event timeline and trace provenance visibility

### Story 90.1: Task event timeline

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: render task event timeline, task transitions, state changes, and related ProblemDetails using safe event/transition read routes.
- Governing FR/NFR: FR169, FR171, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- Read-surface basis: Existing safe read surface: `GET /v1/tasks/{task_id}/events` and safe transition/event reads where approved; explicit unavailable state for unsupported timeline segments; separately approved future read contract required for new timeline aggregation.
- Acceptance criteria:
  - Timeline entries show event type, timestamp, trace/task/session references, payload summary, and source route.
  - The UI distinguishes empty history, missing task, unauthorized access, stale data, and route failure.
  - Timeline refresh cannot append events, trigger replay, create snapshots, or dispatch background jobs.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 90.2: Trace correlation panel using `GET /v1/trace/{trace_id}`

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: display trace correlation details, related events, causality notes, and provenance using the existing trace read surface.
- Governing FR/NFR: FR169, FR171, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- Read-surface basis: Existing safe read surface: `GET /v1/trace/{trace_id}`; explicit unavailable state when trace data is absent or unauthorized; separately approved future read contract required for any broader trace search/list.
- Acceptance criteria:
  - The panel shows trace source, retrieved-at timestamp, linked task/event identifiers, and freshness.
  - Empty or missing trace states remain visible and fail safe without retry/cancel controls.
  - Trace display never exposes mutation routes or background replay/snapshot actions.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Epic 91 — Replay/lifecycle-readiness and archive validation visibility

### Story 91.1: Task history and replay validation panels

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: show task history, replay validation status, replay snapshot/read evidence, and fail-closed replay states using approved read-only replay/history routes.
- Governing FR/NFR: FR169, FR172, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface: task history and approved replay validation/snapshot GET reads; explicit unavailable state for any replay action requiring write/control behavior; separately approved future read contract required for additional replay visibility.
- Acceptance criteria:
  - Replay and history panels show validation status, source route, timestamps, and artifact/provenance references.
  - The UI separates read-only validation visibility from replay execution, snapshot creation, apply, prune, and archive mutation.
  - Unsupported replay data renders unavailable-state copy rather than attempting a background job or cache warm.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 91.2: Lifecycle safety copy and archive ProblemDetails states

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: explain lifecycle-readiness state, archive validation ProblemDetails, retention/future-work boundaries, and destructive-operation gates without adding apply/prune controls.
- Governing FR/NFR: FR169, FR172, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface for ProblemDetails and lifecycle-readiness visibility where present; explicit unavailable state for unapproved lifecycle reads; separately approved future read contract required for any new lifecycle readiness endpoint.
- Acceptance criteria:
  - Lifecycle copy states that destructive lifecycle apply and scheduled retention remain future gated work.
  - Archive validation failures, missing manifests, stale replay evidence, and unauthorized states are visible and fail safe.
  - No apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation control is exposed.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Epic 92 — Health, stale/error states, accessibility/responsiveness, and final no-mutation validation

### Story 92.1: Health, stale, empty, and error states

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: display health, stale data, empty states, unauthorized/forbidden states, unavailable reads, and ProblemDetails consistently across dashboard panels.
- Governing FR/NFR: FR169, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- Read-surface basis: Existing safe read surface: `GET /v1/health` and approved read-only metrics/provenance sources; explicit unavailable state for unapproved metrics; separately approved future read contract required for any new metrics route.
- Acceptance criteria:
  - Health and error states show source route, retrieved-at timestamp, freshness, and remediation copy that does not instruct mutation from the dashboard.
  - Loading, empty, stale, missing, unauthorized, forbidden, and degraded states are visually distinct and accessible.
  - Health refresh never dispatches background checks, writes caches, warms state, or invokes production operation controls.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 92.2: Accessibility, responsiveness, and Audit and Help copy

- Status: future implementation story; not implemented in this planning-only slice.
- Scope: explicitly cover the UX **Audit and Help** section while defining accessibility, keyboard navigation, responsiveness, provenance help, route allowlist help, and unavailable-state guidance.
- Governing FR/NFR: FR169, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface for static help/read-only documentation content; explicit unavailable state for live help data if not safely readable; separately approved future read contract required for dynamic help/audit aggregation.
- Acceptance criteria:
  - Audit and Help copy explains read-only semantics, provenance, route allowlists, unavailable states, and forbidden operator controls.
  - Keyboard, screen-reader, contrast, reduced-motion, and responsive layout checks cover every panel.
  - Help content never links to or embeds mutation/control actions in the dashboard.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

### Story 92.3: Phase 19 final quality gate, review, commit, push, CI

- Status: current planning/status story for this artifact; no runtime implementation.
- Scope: verify this Phase 19 epics/status slice, run changed-file hygiene, obtain independent code-review/architecture evidence, commit, push, verify CI, and record Autopilot/Ultragoal completion evidence.
- Governing FR/NFR: FR174, NFR-S27, NFR-M20, NFR-R20, NFR-S28.
- Read-surface basis: Existing safe read surface not applicable to runtime because this is docs/status-only; explicit unavailable state and separately approved future read contract remain the rule for future implementation stories.
- Acceptance criteria:
  - Only `_bmad-output/planning-artifacts/phase-19-epics.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml` change in tracked files.
  - Sprint status adds only `87-4-phase-19-epics-and-stories` and `phase-19-epics-and-stories-complete` for this slice; it does not add `epic-88` through `epic-92` or `88-*` through `92-*` implementation backlog keys.
  - Local validation, independent review, UltraQA skip/pass evidence, commit, push, and CI evidence are recorded before completion.
- Safety guardrails: only allowlisted read routes/read methods are reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.
