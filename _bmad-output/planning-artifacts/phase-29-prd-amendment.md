# Phase 29 PRD Amendment — Aggregate/Session/Digest Route Selection Planning

## Summary

Phase 29 opens the next dashboard route-family planning branch after Phase 28 closed Snapshot Creation authorization. Phase 29 selects exactly one remaining high-risk family for future runtime consideration:

- **Selected family:** aggregate/session/digest
- **Selected exact future candidate surface:** `GET /v1/tasks/{task_id}/logs/digest`

Story 108.1 is docs/status-only. It does not add runtime behavior, dashboard JavaScript/HTML behavior, browser network calls, backend/API route changes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, task-list/search/discovery, aggregate task listing, session list/detail, digest streaming, mutation/control behavior, cache warming, LLM/browser-side generation, broad dashboard wiring, or production operations.

## Problem

The dashboard has narrow runtime boundaries for health/readiness, task detail, event/transition, trace, history/replay, lifecycle/snapshot listing, and snapshot creation authorization. The remaining dashboard backlog includes two higher-risk families: `aggregate/session/digest` and `task-list/search/discovery`. Both can accidentally broaden into hidden selectors, discovery/listing, generated data, or external-service behavior. Phase 29 starts with a planning gate so only one route family and one exact future surface are considered before any runtime work.

## Goals

- Formally open Phase 29 / Epic 108 as planning-only work.
- Select exactly the `aggregate/session/digest` family from the remaining route-family backlog.
- Within that family, select exactly `GET /v1/tasks/{task_id}/logs/digest` as the future candidate surface.
- Keep aggregate/session list/detail and digest streaming fail-closed.
- Preserve task-list/search/discovery as a separate future family.
- Require a later tests-first runtime story before any browser/runtime digest wiring.
- Require future runtime work to prove visible task_id selector discipline, GET-only behavior, bounded digest display, no hidden generation, no stream, no aggregate/session traversal, no discovery/listing, degraded-state handling, source/provenance/freshness visibility, review, QA, push, and CI evidence.

## Out of scope for Story 108.1

- Runtime implementation or dashboard JavaScript/HTML behavior changes.
- Browser network calls.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- `/v1/tasks` as aggregate/list/read, `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, aggregate/session traversal, generated live data, browser-side LLM prompt construction or summarization, cache warming, polling/timers/background jobs, automatic refresh, mutation/control surfaces, dependencies, lockfiles, deployment, CI, services, MCP, production credentials, or production operations.

## Functional requirements

- **FR243 — Phase 29 route-family scope.** The repository records Phase 29 as the planning gate for the next dashboard route-family branch after Snapshot Creation authorization.
- **FR244 — Selected family.** Story 108.1 selects exactly `aggregate/session/digest` and does not select `task-list/search/discovery`.
- **FR245 — Exact future candidate.** Story 108.1 selects exactly `GET /v1/tasks/{task_id}/logs/digest` as the only future runtime candidate in this phase.
- **FR246 — Separate implementation story.** Any digest runtime/dashboard/browser use requires a later separately approved tests-first Story 108.2.
- **FR247 — Visible task selector only.** Future runtime work must use an explicit visible task_id as the only selector. Query/hash/storage/session/list/search/discovery/aggregate-derived selectors remain forbidden.
- **FR248 — No digest-stream or generated-data drift.** Future work must not use `/v1/tasks/{task_id}/logs/digest/stream`, browser-side LLM summarization, prompt construction, hidden generated data, cache warming, polling, background refresh, or external provider calls from the browser.
- **FR249 — Aggregate/session remain deferred.** `/v1/tasks` aggregate/list reads, `/v1/sessions`, and `/v1/sessions/{session_id}` remain `needs-separate-contract` or unavailable until separately planned.
- **FR250 — No behavior change in Story 108.1.** Story 108.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S45 — Digest fail-closed safety.** Missing task_id, missing digest, no configured digest provider, unavailable provider, timeout, non-2xx response, invalid response, empty digest, stale digest, unauthorized, backend-unavailable, or ambiguous authority states must render non-authoritative/unavailable copy in future runtime work without auto-retry.
- **NFR-S46 — No hidden generation or side effects.** Future runtime tests must fail on browser-side LLM calls, prompt construction, generated-data synthesis, cache warming, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, automatic refresh, POST/PUT/PATCH/DELETE, aggregate/session traversal, or discovery/search/listing.
- **NFR-O33 — Digest provenance and freshness.** Future displayed digest values must expose source route, visible task_id, retrieved-at/completed-at, freshness, authority/provenance, request/trace/correlation id when available, and degraded-state metadata.
- **NFR-M29 — Tests-first maintainability.** Future runtime implementation must add boundary tests before or with any runtime wiring and keep existing dashboard runtime suites green.

## Acceptance criteria

1. Phase 29 PRD, architecture, and epics artifacts exist and define aggregate/session/digest route-selection planning scope.
2. Story 108.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 29`, opens Epic 108 / Story 108.1, preserves Epic 107 done, and records newest-first audit evidence.
4. Story 108.1 explicitly excludes runtime implementation, digest streaming, aggregate/session list/detail, task-list/search/discovery, broad dashboard wiring, backend/API expansion, generated live data, browser-side LLM generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and test/runtime code changes.
5. Follow-on Phase 29 epics sequence docs/status opening first, exact digest-read runtime-boundary tests/implementation second, final closure third.
