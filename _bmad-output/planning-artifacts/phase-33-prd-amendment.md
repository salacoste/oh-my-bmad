# Phase 33 PRD Amendment — Digest Stream Route Selection Planning

## Summary

Phase 33 opens the next dashboard route-selection planning branch after Phase 32 / Epic 111 closed the exact session-detail runtime/API boundary. Phase 33 selects exactly one remaining deferred surface for future consideration:

- **Selected family:** task log digest continuation
- **Selected exact future candidate surface:** `GET /v1/tasks/{task_id}/logs/digest/stream`

Story 112.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, non-digest task-list/search/discovery, broad dashboard live wiring, browser-side LLM generation/summarization, cache warming, polling/background refresh, mutation/control behavior, production credentials, or production operations.

## Problem

The non-streaming task log digest route is already implemented as a bounded dashboard runtime boundary. Operators may eventually need a live digest stream to observe digest progress or incremental provider output, but streaming is riskier than a single bounded JSON read because it can introduce long-lived connections, EventSource/WebSocket side channels, retry loops, background behavior, partial output authority confusion, provider leakage, browser-side summarization, and broad live wiring.

Phase 33 therefore starts with a planning gate that selects the exact stream candidate while keeping every runtime/API decision deferred to a later tests-first story.

## Goals

- Formally open Phase 33 / Epic 112 as planning-first work.
- Select the task log digest continuation branch after the completed non-streaming digest and session-detail boundaries.
- Within that branch, select exactly `GET /v1/tasks/{task_id}/logs/digest/stream` as the only future candidate in this phase.
- Keep broader task-list/search/discovery, broad dashboard wiring, generated live data, browser-side LLM generation/summarization, mutation/control behavior, cache warming/background jobs, production credentials, and production operations fail-closed.
- Require a later tests-first Story 112.2 before any browser/runtime/API contract work.
- Require future runtime/API work to prove visible `task_id` selector discipline, read-only GET behavior, bounded stream event contract, stream termination/error semantics, no hidden adjacent-route traversal, no provider/prompt/raw-log leakage, fail-closed degraded states, review, QA, push, and CI evidence.

## Out of scope for Story 112.1

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, task-list/search/discovery, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating the existing non-streaming digest route as an approved streaming route.
- Inferring EventSource, WebSocket, NDJSON, chunked JSON, retry, or background transport authorization from the stream label.

## Functional requirements

- **FR276 — Phase 33 route-family scope.** The repository records Phase 33 as the planning gate for the next dashboard route-family branch after Phase 32 / Epic 111 closure.
- **FR277 — Selected family continuation.** Story 112.1 selects exactly task log digest continuation and does not select broader task-list/search/discovery, broad dashboard wiring, generated live data, or production operations.
- **FR278 — Exact future candidate.** Story 112.1 selects exactly `GET /v1/tasks/{task_id}/logs/digest/stream` as the only future candidate in this phase.
- **FR279 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/tasks/{task_id}/logs/digest/stream` requires a later separately approved tests-first Story 112.2.
- **FR280 — Visible task_id only.** Future runtime work may use only an explicit visible operator-provided `task_id` path parameter. It must not use query strings, request bodies, hashes, storage, cookies, hidden fields, generated selectors, or row-derived attributes to choose the stream target.
- **FR281 — Bounded stream output only.** Future stream output may expose only bounded server-returned digest-progress/status chunks and final digest metadata. It must omit raw logs, event payloads, prompts, provider internals, token streams unrelated to the approved digest, filesystem paths, hrefs/URLs, generated browser summaries, and control hints.
- **FR282 — Stream lifecycle discipline.** Future runtime work must define connect, partial, final, error, cancellation/close, timeout/heartbeat, and stale-state behavior before implementation.
- **FR283 — Adjacent surfaces remain deferred.** Task-list/search/discovery beyond approved exact reads, broad dashboard wiring, hidden traversal, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.
- **FR284 — No behavior change in Story 112.1.** Story 112.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S53 — Streaming fail-closed safety.** Missing route contract, missing/invalid visible `task_id`, backend/provider unavailable, unauthorized, timeout, non-2xx, invalid event/chunk framing, malformed partial/final payload, unexpected keys, stale/ambiguous freshness, excessive chunk volume, over-broad payload, raw log/prompt/provider/path leakage, or stream interruption must render non-authoritative/unavailable copy in future runtime work without automatic retry.
- **NFR-S54 — No side-channel expansion.** Future tests must fail on WebSocket, XMLHttpRequest fallback, workers/service workers, storage writes, browser-side LLM/prompt generation, POST/PUT/PATCH/DELETE, automatic retry loops, automatic refresh, hidden selectors, adjacent-route calls, and mutation/control affordances unless a later story explicitly authorizes one exact mechanism.
- **NFR-O37 — Stream provenance and freshness.** Future displayed stream state must expose source route, selected visible `task_id`, connection/opened-at, last-event-at or retrieved-at, final freshness/staleness, authority/provenance, request/trace/correlation id where available, and degraded-state copy.
- **NFR-M33 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard/API runtime suites green.

## Acceptance criteria

1. Phase 33 PRD, architecture, and epics artifacts exist and define digest-stream route-selection planning scope.
2. Story 112.1 artifact records selected family continuation, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 33`, keeps Epic 111 done, opens Epic 112, marks Story 112.1 done with repaired sequential Architect/Critic consensus evidence, and leaves Story 112.2/112.3 backlog.
4. Story 112.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, EventSource/WebSocket/transport decisions, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
5. Follow-on Phase 33 epics sequence docs/status opening first, exact digest-stream runtime/API contract boundary second, final closure third.

Generated: 2026-06-26T22:03:46Z
