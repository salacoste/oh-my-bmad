# Phase 19 UX Design Specification — Read-Only Web Dashboard

**Author:** R2d2  
**Date:** 2026-06-13  
**Scope:** Phase 19 / P19-ROWD  
**Status:** Planning-only; no dashboard implementation

## Executive Summary

Phase 19 defines the UX for a read-only web dashboard that helps the single oh-my-bmad operator understand current platform state without switching between Telegram, console commands, API curls, logs, and BMAD artifacts. The dashboard is an operator visibility surface, not a control surface.

The first dashboard slice is intentionally non-destructive and non-mutating. It displays existing task, session, event, trace, replay, archive, lifecycle-readiness, and health/projection information through existing safe read surfaces only; any new read contract requires a separate future architecture amendment before implementation. It does not introduce frontend code, backend routes, API schemas, dependencies, deployment changes, credentials, approval controls, lifecycle apply controls, or any runtime behavior.

## User Context and Jobs-to-Be-Done

### Primary user

The primary user is the single operator/developer running oh-my-bmad. This operator is technically comfortable, understands task/session/event concepts, and needs high-confidence situational awareness while the system runs autonomous coding workflows.

### Jobs

1. **See what needs attention.** The operator wants a fast overview of failed, blocked, stale, or otherwise attention-worthy tasks and sessions.
2. **Understand one task.** The operator wants to inspect a task's state, current or last session, terminal outcome, trace id, and related event timeline.
3. **Trace what happened.** The operator wants to follow ordered events and provenance without reading raw JSONL logs.
4. **Check replay and lifecycle readiness.** The operator wants to see archive/replay/lifecycle-readiness evidence as information only, with no ability to apply or prune.
5. **Handle uncertainty safely.** The operator wants the UI to clearly distinguish empty, loading, stale, invalid, unavailable, and partial data.

## Design Principles

1. **Read-only by construction.** The UI contains no controls or indirect flows that mutate task, session, approval, budget, lifecycle, archive, deployment, credential, cache, job, or runtime state. Read-only means no side effects, including no cache-warming writes, not merely no mutating HTTP method.
2. **Attention before detail.** The overview surfaces failed/stale/blocked tasks, heartbeat issues, archive/replay configuration errors, and unavailable projections before normal detail.
3. **Provenance visible.** Each important datum labels its source category: registry projection, event log, replay/task-history response, metrics projection, or BMAD artifact.
4. **Fail-safe uncertainty.** Partial, stale, unavailable, or invalid data appears as bounded uncertainty, not as authoritative healthy state.
5. **Lifecycle safety language.** Lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, and credentialed lifecycle terms are informational only and paired with explicit "not available in this dashboard" language.
6. **Existing contracts first.** UX planning assumes existing read surfaces before proposing any new backend/API contract.

## Information Architecture

### 1. Overview

Purpose: fast situational awareness.

Content:
- task counts by state;
- attention cards for failed tasks, blocked tasks, stale warnings, heartbeat issues, archive/replay configuration errors, and unavailable projections;
- persistent read-only banner;
- links into filtered task list and task detail screens.

### 2. Tasks

Purpose: browse and filter tasks.

Content:
- task id;
- status;
- current or last session;
- trace id;
- last event summary;
- stale/heartbeat state;
- terminal outcome summary;
- source/provenance label.

### 3. Task Detail

Purpose: understand one task.

Content:
- task summary and status;
- session panel;
- event timeline;
- trace correlation;
- replay/task-history status;
- relevant attention or failure states;
- no mutation controls.

### 4. Sessions

Purpose: inspect worker/session lifecycle.

Content:
- active sessions;
- historical sessions;
- heartbeat/stale indicators;
- terminal session outcome;
- worker/runtime provenance where available.

### 5. Replay and Lifecycle Visibility

Purpose: inspect replay/archive/lifecycle-readiness information without mutation.

Content:
- archive manifest configuration state;
- replay validation state;
- known archive ProblemDetails categories;
- lifecycle dry-run/readiness evidence if available through existing safe read surfaces;
- explicit no-apply/no-prune messaging.

### 6. System Health and Projections

Purpose: show projection availability and confidence.

Content:
- registry projection health;
- event-spine read status;
- metrics projection availability;
- stale/partial/unavailable projection indicators.

### 7. Audit and Help

Purpose: explain dashboard boundaries.

Content:
- read-only guarantee;
- data source explanations;
- lifecycle safety warnings;
- references to operator runbook, ADR-0025, and Phase 18/19 planning artifacts.

## Screen Inventory

| Screen | Purpose | Required states | Mutation controls allowed? |
|---|---|---|---|
| Dashboard overview | Fast situational awareness | loading, no tasks, healthy, attention needed, stale data, backend unavailable | No |
| Task list | Browse/filter tasks | empty, populated, filter no-results, stale data, read error | No |
| Task detail | Understand one task | task missing, active, completed, failed, cancelled, blocked/stale, replay data unavailable | No |
| Event timeline | Inspect causal sequence | loading, empty, event parse/read error, trace mismatch, truncated/large result | No |
| Sessions | Inspect worker/session lifecycle | no active sessions, active, terminal, stale/heartbeat warning, read error | No |
| Replay/lifecycle | Inspect validation/readiness | no archive config, valid archive config, invalid config ProblemDetails, dry-run evidence present/absent | No |
| Health/provenance | Explain source projections | metrics unavailable, registry unavailable, event log unavailable, partial data | No |

## Core Flows

### Flow 1 — Overview to Task Detail

1. Operator opens dashboard overview.
2. Overview shows task counts and attention cards.
3. Operator selects a failed/stale/blocked task card or a task count.
4. Dashboard opens task detail with task status, session panel, trace id, and timeline.
5. Operator reads status and provenance; no mutation action is available.

### Flow 2 — Task Filtering and No-Results

1. Operator opens task list.
2. Operator filters by status, trace id, session, stale state, or terminal outcome.
3. If matches exist, the list updates with source/provenance labels.
4. If no matches exist, the no-results state explains the filter scope and offers only non-mutating filter reset or adjustment.

### Flow 3 — Event Timeline Inspection

1. Operator opens a task detail screen.
2. Operator scans the event timeline ordered by event order or monotonic event time where available.
3. Timeline entries show event type, source category, trace relationship, and summary.
4. Missing, truncated, parse-error, or trace-mismatch states appear as explicit uncertainty.
5. UI does not expose raw mutation controls or event replay triggers.

### Flow 4 — Replay and Lifecycle-Readiness Inspection

1. Operator opens Replay/lifecycle screen from task detail or navigation.
2. UI shows archive manifest state, replay validation state, and lifecycle-readiness evidence where safely available.
3. Invalid archive configuration maps to recognizable ProblemDetails categories.
4. Apply/prune/delete/truncate/move/rewrite/chmod/archive mutation controls are absent.
5. Lifecycle safety copy states that destructive apply remains gated by Phase 18 follow-on architecture, tests, and reviews.

### Flow 5 — Stale or Error-State Recovery Guidance

1. Operator sees stale data, backend unavailable, metrics unavailable, invalid archive config, or partial projection state.
2. UI labels the affected data source and the confidence level.
3. UI gives read-only diagnostic guidance such as checking registry-api availability, archive manifest configuration, or operator runbook references.
4. UI does not attempt repair, retry mutation, credential entry, lifecycle apply, or operational changes.

## Empty, Loading, Error, and Stale States

### Empty states

- No tasks: explain that no task records are visible from the current read source.
- No sessions: explain that no active or historical sessions match the current filter.
- No replay/lifecycle evidence: explain that no safe read evidence is currently available; do not imply failure or mutation.

### Loading states

- Use bounded loading labels per panel.
- Keep source labels visible when known.
- Avoid global loading that hides already-known safe data.

### Error states

- Registry unavailable: show read failure and source category.
- Event log unavailable: show timeline unavailable without hiding task metadata.
- Archive invalid: show ProblemDetails category and read-only guidance.
- Metrics unavailable: show projection unavailable without implying system failure.

### Stale states

- Display stale timestamp or freshness cue where available.
- Mark stale panels independently so one stale projection does not make all dashboard data appear invalid.
- Never present stale data as a green/healthy authoritative state.

## Read-Only and Lifecycle Safety Guardrails

- Persistent dashboard-level label: "Read-only visibility surface".
- No forms or buttons for approve, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credential entry, or production operation.
- Disabled-looking controls are avoided unless they are clearly explanatory; unavailable destructive actions should usually be text, not faux controls.
- Lifecycle panels must say that apply/prune is not available in this dashboard.
- Dashboard copy must not instruct operators to bypass Telegram/console approval gates.
- **Implementation gate criteria for future architecture/test design:** Dashboard implementation cannot start from this UX slice. A future implementation story may start only after a separate architecture/story/test-design gate proves that dashboard data comes from existing safe read surfaces or separately approved future read contract; only allowlisted read routes/read methods are reachable; no mutation routes are reachable from the dashboard; no background-job dispatch can be triggered by dashboard reads; no hidden writes behind read endpoints are present; and no cache-warming writes/read-side effects occur. This UX planning artifact records criteria only; it is not completed architecture approval.

## Accessibility and Responsiveness Notes

- Attention states must not rely on color alone; pair color with icons/text labels.
- Timeline and task-list rows need readable keyboard focus order in future implementation.
- Tables should collapse into card/list layouts for narrow screens without hiding status, provenance, or attention labels.
- Error and stale states should use concise language suitable for quick operator scanning.
- Read-only status should remain visible in responsive layouts.
- Long event summaries should wrap or truncate with non-mutating expansion affordances only.

## Implementation Prerequisites and Follow-On Gates

Before any dashboard implementation begins, BMAD must produce and approve:

1. Architecture amendment defining frontend/backend boundaries, auth assumptions, data freshness, read-only enforcement, and data-source contracts.
2. Epics/stories decomposed into testable read-only slices.
3. Test design for effect-based no-mutation guarantees, route/method allowlists, no mutation routes, no background-job dispatch, no hidden writes behind read endpoints, no cache-warming writes, source-provenance display, empty/loading/error/stale states, and responsive accessibility checks.
4. Independent review confirming no approval, lifecycle mutation, control-plane mutation, credentialed operation, hidden write, background-job dispatch, cache-warming write, or runtime/deployment side effect is introduced.
5. Final implementation verification plan that preserves Phase 18 destructive lifecycle apply gates.

## Non-Goals

- Frontend implementation.
- Backend route or API schema implementation.
- Runtime, package, MCP, service, deployment, dependency, lockfile, or CI changes.
- Approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credential entry, or production operation controls.
- Public sharing, replay export, multi-user auth, OAuth, or external dashboard hosting.
