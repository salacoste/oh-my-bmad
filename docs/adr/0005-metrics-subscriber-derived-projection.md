---
id: ADR-0005
status: accepted
date: 2026-05-19
supersedes: null
---

# ADR-0005: metrics-subscriber as a derived projection of the event log

## Status

**Accepted** — 2026-05-19. Supersedes the "to be drafted" placeholder
at [`_bmad-output/planning-artifacts/architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 2 Architecture
Extension" §"Epic 10 wiring" (line 1218 pre-deletion).

This ADR closes one of the five Phase-2 forward-referenced ADR
acceptance-gate items declared in [ADR-0003](./0003-phase-2-gate.md)
§Decision-item-4 (ADR-0004 through ADR-0008). It MUST be `accepted`
before any further Epic 10 story merges to `main`.

## Context

Phase 1 (epics 1–7) established the **event spine** — a JSONL append-
only log of immutable, schema-versioned `EventEnvelope` records — as
the *primary* observability stream. The Phase-1 architecture pinned
this with **NFR-O1**:

> Every state change observable to the operator MUST be expressed as
> a typed event on the event spine. Instrumentation that bypasses the
> spine (stdout logs scraped by regex, side-channel metrics emitted
> directly from producer services, sidecar agents that re-derive
> state from process introspection) is forbidden.

Phase 2 (epics 8–13, opened by [ADR-0003](./0003-phase-2-gate.md))
introduces three observability surfaces operators want NOW:

1. **Metrics** — Prometheus-format counters / gauges / histograms
   over task lifecycle, session state, capability tier, lag.
2. **Traces** — request-scoped `trace_id` propagation across the
   2-process MCP bridge (Epic 9 — landed pre-Story-10.3).
3. **Approvals + budgets** — derived from the event spine; Epics 11
   and 12.

The architectural question for Epic 10 (β metrics-subscriber service,
FR60–FR62a) is: **how do metrics relate to the event spine?**

Three credible-on-paper alternatives surfaced during the Phase 2
brainstorming and architecture passes:

- **A. Inline `prometheus_client` calls in `services/*`** — every
  producer service holds its own `Counter` / `Gauge` instances and
  exposes them via a sidecar `/metrics` route. Familiar OTel-style
  pattern.
- **B. OpenTelemetry SDK in every service** — same as (A) but with
  the OTel SDK as the wrapper, exported to an OTLP collector.
- **C. Sidecar `tail -F` on stdout** — a separate process tails each
  service's stdout log and scrapes structured-log lines via regex,
  exposing the result as Prometheus metrics.

All three violate NFR-O1 (the spine ceases to be the primary
observability source) and / or NFR-O10 (instrumentation surface
appears in producer services). None were chosen.

## Decision

**Metrics + traces are derived projections of the event log, not
parallel instrumentation paths.** Specifically:

1. **A new workspace member — `services/metrics-subscriber`** —
   tails the JSONL event log read-only via the shared
   `events.log_reader.EventLogReader` (extracted in Story 10.2 AC1
   per **P2-I1**: cross-service code shared via `packages/`, never
   `services/*→services/*` imports).

2. **The metrics-subscriber computes Prometheus metrics by reading
   the event spine and projecting envelope fields into gauges /
   counters / histograms.** It exposes the result on an HTTP
   `/metrics` endpoint reachable ONLY on the docker-compose
   internal network (P2-I5 — Story 10.6 enforces at the compose
   layer; Story 10.3 documents in code).

3. **No `services/*` code (other than `metrics-subscriber` itself)
   emits Prometheus metrics directly.** No `prometheus_client`
   import outside `services/metrics-subscriber/`. No OTel SDK in
   producer services. The `check_imports.py` gate enforces the
   read-only-subscriber rule.

4. **Cursor durability is mandatory** — the subscriber persists its
   log cursor (`cursor.json` in the named volume) so a restart
   resumes from the exact offset. Without cursor durability,
   subscriber restarts would replay the entire day's event log and
   double-count counters (NFR-R2 violation). Story 10.2 ships the
   `CursorPersistence` machinery with the **exactly-once at
   envelope level** invariant.

5. **Cardinality discipline is bounded at the projection layer.**
   The metrics-subscriber's label namespace is a closed enum
   (task status, session phase, capability tier, actor kind). Raw
   `task_id` / `event_id` / free-text breadcrumbs are NEVER lifted
   into Prometheus labels — they live in the event spine only.
   Story 10.5 will add a cardinality regression test.

The pattern is named **"derived projection"** throughout the
architecture, FR60, and the code docstrings:

- "Derived" — the metrics are computed from the spine, not emitted
  by producers.
- "Projection" — like a relational view, the metrics surface is a
  read-only transformation of the underlying source of truth.

## Consequences

### Positive

1. **NFR-O1 + NFR-O10 are structurally enforced**, not just
   socially. The `check_imports.py` gate fails the build if a
   producer service imports `prometheus_client`. Operators cannot
   accidentally add inline instrumentation that bypasses the spine.

2. **Single source of truth.** The spine carries every state change;
   metrics are computed from it. Discrepancies between "what the log
   says happened" and "what Prometheus shows" become impossible by
   construction (subject to the cursor-lag observability gauges).

3. **Forecloses the "OTel-everywhere" anti-pattern.** Adding OTel
   instrumentation to producer services is a one-way decision that's
   hard to walk back once dashboards depend on it. The derived-
   projection pattern keeps the door open for switching the metrics
   exposition format (Prometheus → OTLP → something else) without
   touching producer code.

4. **Adds zero changes to existing services.** Story 10.2 + 10.3
   introduce the new workspace member; the existing producer
   services (`registry-state`, `registry-api`, `worker-wrapper`,
   etc.) remain bit-identical. Separability-S-4 (Story 10.6) will
   verify that removing `metrics-subscriber` from the compose stack
   leaves every other service starting + serving traffic
   identically.

### Negative

1. **Metrics granularity is bounded by event-log granularity** — by
   design. If a state transition is not on the spine, it cannot
   appear as a metric. This is a feature, not a bug: it forces
   producer services to express observable state changes as typed
   events first, which is exactly what NFR-O1 mandates.

2. **Cursor durability is required.** The subscriber's restart-
   recovery story is complex (Story 10.2 invested ~3 review passes
   on cursor semantics, day-rollover, concurrent-start refusal, and
   the exit-code matrix). This complexity is paid once in
   `metrics-subscriber` rather than scattered across every producer
   service.

3. **Some lag between event emission and metric visibility** —
   bounded by `OMB_METRICS_POLL_INTERVAL_S` (default 0.5s) and the
   `persist_every_n_events` cadence (default 1000). The
   `metrics_subscriber_lag_seconds` gauge (Story 10.3 AC5) is
   itself a derived metric so operators can alert on excessive lag.

4. **Wall-clock-skew sensitivity** — `lag_seconds` is computed from
   `envelope.emitted_at` (UTC datetime, set by the writer process)
   vs `datetime.now(UTC)` (the subscriber's wall clock). Deployments
   require NTP / chrony; unsynchronised clocks produce negative or
   inflated lag values. Operators should configure clock-sync
   monitoring (Story 10.5 cardinality discipline tests will check
   that the gauge stays within reasonable bounds).

## Alternatives rejected

### Alternative A — Inline `prometheus_client.Counter` calls in producer services

**Rejected.** Creates two sources of truth (the spine + the metric
registry) which can drift. Operators ask "why does Prometheus show 7
tasks completed when the event log shows 8?" and the answer is "race
between the inline `Counter.inc()` and the `EventEnvelope` append —
one path failed silently". The derived-projection pattern eliminates
this class of bug at the architecture level.

### Alternative B — OpenTelemetry SDK in every service

**Rejected.** Same drift risk as (A) plus a heavier instrumentation
surface (OTel collectors, exporters, sampling config) in every
producer process. Phase 1's NFR-O1 specifically pinned the spine as
the primary stream; introducing the OTel SDK in producer services
would violate that pin without delivering observable benefits over
the derived-projection pattern. OTel-as-export-format remains a
future option at the metrics-subscriber boundary (an OTLP exporter
could replace `prometheus_client.generate_latest` without touching
producer code).

### Alternative C — Sidecar `tail -F` on stdout

**Rejected.** NFR-O1 specifically bans stdout-parsing regex — the
spine is the structured event surface, not stdout. A `tail -F`
sidecar would necessarily parse stdout log lines via regex, which
is brittle (log format changes silently break dashboards), un-typed
(no `pydantic` validation), and circular (we'd be re-deriving the
same structured envelopes the spine already carries).

## References

### PRD

- [`_bmad-output/planning-artifacts/prd.md`](../../_bmad-output/planning-artifacts/prd.md)
  §"Phase 2 Scope Extension":
  - **FR60** — β metrics-subscriber tails event spine, exposes
    Prometheus-format metrics.
  - **FR61** — internal-only Prometheus exposition (`/metrics` on
    docker-compose internal network).
  - **FR62a** — counter / gauge / histogram set over task lifecycle,
    session state, capability tier.
  - **NFR-O1** — event spine is primary observability stream.
  - **NFR-O8** — `/metrics` p95 < 100 ms.
  - **NFR-O10** — derived projection; no instrumentation in producer
    services.

### Architecture

- [`_bmad-output/planning-artifacts/architecture.md`](../../_bmad-output/planning-artifacts/architecture.md)
  §"Phase 2 Architecture Extension":
  - **P2-I1** — read-only subscriber rule (shared code lives in
    `packages/`).
  - **P2-I3** — metrics + traces as derived projections.
  - **P2-I5** — no public ingress for the metrics surface.

### Stories

- [`_bmad-output/implementation-artifacts/10-1-metrics-subscriber-scaffold.md`](../../_bmad-output/implementation-artifacts/10-1-metrics-subscriber-scaffold.md)
  — scaffold workspace member.
- [`_bmad-output/implementation-artifacts/10-2-metrics-subscriber-cursor-and-tail-loop.md`](../../_bmad-output/implementation-artifacts/10-2-metrics-subscriber-cursor-and-tail-loop.md)
  — tail loop + cursor persistence (exactly-once envelope semantics).
- [`_bmad-output/implementation-artifacts/10-3-fastapi-metrics-endpoint.md`](../../_bmad-output/implementation-artifacts/10-3-fastapi-metrics-endpoint.md)
  — FastAPI `/metrics` surface (this ADR's owning story).

### Related ADRs

- [ADR-0003](./0003-phase-2-gate.md) — Phase 2 gate; declares this
  ADR as a Phase-2 acceptance-gate item.
