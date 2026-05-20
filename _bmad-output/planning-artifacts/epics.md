---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-03-create-stories-revisions
  - step-04-final-validation
workflowStatus: 'complete'
finalStoryCount: 98
finalEpicCount: 7
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/product-brief.md
  - plan_draft.md
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-04-21'
---

# oh-my-bmad — Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for **oh-my-bmad**, decomposing the PRD (56 FRs / 38 NFRs across 6 categories / 6 user journeys) and the Architecture document (11 components, 7-category implementation patterns, `uv` workspace monorepo with vendored-with-sync upstream forks) into implementable stories. No UX Design document exists for Phase 1 (platform is text-only: Telegram + Console); UX-DR section is intentionally empty.

## Requirements Inventory

### Functional Requirements

Condensed from PRD §Functional Requirements. Each FR id is authoritative; refer to PRD for full wording.

**Task Lifecycle Management (FR1–10):**
- **FR1:** Operator submits a task via free-text from Telegram or Console, optionally with repo + hint.
- **FR2:** Platform plans a submitted task, producing an operator-visible stepwise plan.
- **FR3:** Platform autonomously executes a planned task (edits, tests, commits, PR drafts).
- **FR4:** Operator retrieves full current task state in a single response (no scrollback needed).
- **FR5:** Operator retrieves an LLM-summarized log digest of a task.
- **FR6:** Operator retrieves the raw typed-event stream for debugging.
- **FR7:** Operator approves / rejects / stops / retries a task at any checkpoint, with optional free-text hint injected into next planning pass.
- **FR8:** Platform transitions tasks through explicit lifecycle states, recording each transition as a typed event.
- **FR9:** Platform emits a structured completion summary (file count, line count, test count, CI state, blockers).
- **FR10:** Platform auto-creates a PR draft on green-tests completion of a repo-mutating flow.

**Control Surfaces (FR11–17b):**
- **FR11:** Telegram Bot authenticates via allowlist; non-allowlisted senders get no response, logged as rejected.
- **FR12:** Console Client surface parity with Telegram — no capability is Telegram-only.
- **FR13:** Operator binds a Telegram thread to a task id for per-task progress streaming.
- **FR14:** Platform delivers approval requests with risk class, pre-check results, diff summary, accepted commands.
- **FR15:** Platform delivers blocker notifications with blocked-since, last event, last action, available commands.
- **FR16:** Platform delivers a proactive morning summary when a host restart occurred during an overnight task.
- **FR17:** Operator issues `/ping` health check → registry, worker, event-bus queue depth, version.
- **FR17a:** Operator queries current runtime/provider owning a task via `/agent <task-id>`.
- **FR17b:** Operator inspects agent reasoning breadcrumbs via `agent.reasoning.*` event subtype; `/logs` + `/status` surface last breadcrumb.

**Event System (FR18a–23):**
- **FR18a:** Worker and Orchestrator emit typed events to the Event Bus via a dedicated MCP surface.
- **FR18b:** Platform never interprets stdout as execution state; all state from typed events.
- **FR19:** Event Bus routes events to registered sinks (Telegram sink in Phase 1).
- **FR20:** Platform persists every event to an append-only event log with metadata to reconstruct state.
- **FR21:** Platform versions every event (`schema_version`) and rejects unknown `(event_type, schema_version)` combinations with `event.unknown_schema`.
- **FR22:** Platform executes a migrator tool that transforms old-version events into new-version events.
- **FR23:** Event Bus exposes recent event stream + route diagnostics as read-only MCP resources.

**Persistence & Recovery (FR24–30):**
- **FR24:** Registry persists task + session state surviving host, container, bot restart — zero in-flight work loss.
- **FR24a:** Platform detects service-level failure (container exit, heartbeat timeout, webhook delivery failure, `/stop`) and emits `service.crashed` / `session.heartbeat_timeout` / `sink.delivery_failed` / `task.stop_requested`.
- **FR25:** Registry captures event-log snapshots so replay meets startup budget even at elevated event counts.
- **FR26:** Registry is the sole writer to persistent task/session state; other services mutate only by appending events.
- **FR27:** Platform holds a Worker's worktree lock through a blocked task's entire waiting period.
- **FR28:** Platform dedupes control commands by client-generated idempotency key; prior result on collision; never double-executes.
- **FR29:** Platform reattaches a Worker post-restart; resumes from last committed event; emits `session.reconnecting` + `task.execution.resumed`.
- **FR30:** Worker performs file edits atomically — mid-write interruption leaves a consistent filesystem on resume.

**Runtime Execution (FR31–36):**
- **FR31:** Orchestrator drives task from plan → execution → verification → completion via MCP.
- **FR32:** Worker registers with Session Registry; emits lifecycle events; acquires exclusive worktree lock.
- **FR33:** Worker obtains task detail read-only via MCP; never writes task state directly.
- **FR34:** Platform swaps default Worker for an alternative (including scripted-stub) via single env-var change; no source/DI/MCP changes required.
- **FR35:** Platform swaps default Orchestrator for a pass-through null orchestrator via single env-var change; no source changes required.
- **FR36:** Worker participates in approval-gated flows (emits `task.awaiting_approval`, holds lock, waits on `approval.*` event).

**Policy & Security (FR37–45):**
- **FR37:** Platform classifies actions into Tiers 0–3; enforces tier at MCP + HTTP API boundary.
- **FR38:** Platform requires explicit operator approval event for Tier 3; Phase 1 gates `git push`.
- **FR39:** Platform runs a pre-commit hook blocking sensitive-path changes, worktree traversal, commit-message injection.
- **FR40:** Platform runs license-scan on every agent commit pre-push; emits `task.license_flagged` on incompatibility.
- **FR41:** Operator overrides license flag via `/approve --override license`; override is audited.
- **FR42:** Platform emits `secret.accessed` on every secret access.
- **FR43:** Platform sanitizes events, snapshots, artifacts, logs — zero plaintext secret persistence.
- **FR44:** Platform enforces per-task budget; emits `task.budget_exceeded`; halts until operator approves extension.
- **FR45:** Platform sanitizes operator-provided task input to prevent command injection into shell, git, MCP.

**Deployment & Operations (FR46–52):**
- **FR46:** Operator deploys full stack to VPS and macOS with single `docker compose up` + `.env` only.
- **FR47:** Platform meets time-to-first-task deployment budget on both targets from a clean host.
- **FR48:** Operator rotates secrets via env-var update + container reload; no source changes.
- **FR49:** Platform exposes structured JSON logs on stdout from every service, independent of event stream.
- **FR50:** Operator runs schema migrator as one-shot container for event-log evolution.
- **FR51:** Platform packages Docker images for every platform-owned service to GHCR; upstream-fork images pinned by digest.
- **FR52:** Operator upgrades by updating image tags + `docker compose up -d`; data volumes preserved.

**Total: 56 FRs (FR1–FR52 with FR17a, FR17b, FR18a, FR18b, FR24a as additions; FR18 split into FR18a+FR18b).**

### NonFunctional Requirements

Condensed from PRD §Non-Functional Requirements. Each NFR id is authoritative.

**Performance (NFR-P1–P5):**
- **NFR-P1:** Return-to-flow display <5 s p95 over 30 sessions (KPI #1a).
- **NFR-P2:** Operator latency <2.5 s p95 over 3×100 sequential submissions; fast-path <2.0 s p95 to `clawhip` emit (KPI #5).
- **NFR-P3:** Registry startup replay <5 s for up to 10K events via snapshots (KPI #8).
- **NFR-P4:** Time-to-first-task <30 min from clean host on both targets (KPI #4).
- **NFR-P5:** Per-task budget enforcement within 5 s of ceiling; no loop exceeds ceiling by >10%.

**Reliability (NFR-R1–R6):**
- **NFR-R1:** Restart recoverability 100% at every lifecycle phase (KPI #2).
- **NFR-R2:** Zero tasks lost to restart/crash per month; CI synthetic-crash harness + monthly audit (KPI #3).
- **NFR-R3:** Control-surface availability ≥99% wall-clock (KPI #12).
- **NFR-R4:** Zero duplicate task executions per 100 concurrent duplicate submissions (KPI #9).
- **NFR-R5:** Service-level failures detected + typed event emitted within 60 s.
- **NFR-R6:** Unattended completion rate ≥80% of weekly overnight submissions (KPI #7).

**Security (NFR-S1–S8):**
- **NFR-S1:** Zero plaintext secrets in logs/snapshots/artifacts; scanner + sanitizer enforced (KPI #11).
- **NFR-S2:** Secret rotation <5 min via env-var update + container reload; no source changes.
- **NFR-S3:** Every Tier-3 action, secret access, and operator decision emits an audit typed event.
- **NFR-S4:** Non-allowlisted Telegram ids get no response; rejection is a typed event.
- **NFR-S5:** Operator task-input command-injection prevented; fuzz test covers null bytes, shell metacharacters, nested quoting, traversal, ANSI escapes, git refname injection.
- **NFR-S6:** Tier-3 action without matching approval event → `permission_denied` (negative-test verified).
- **NFR-S7:** Phase 1 trust boundary = docker-compose network; external ingress limited to Telegram webhook + SSH.
- **NFR-S8:** License scan on every agent commit; incompatibility blocks approval with reason code; `--override license` audit event.

**Observability (NFR-O1–O6):**
- **NFR-O1:** Zero stdout-parsing regex in task lifecycle path; ruff rule enforced (KPI #6).
- **NFR-O2:** Structured JSON stdout logs from every service, independent of events.
- **NFR-O3:** Full task history reconstructable from event log; `/logs` LLM-digest; raw events available.
- **NFR-O4:** `/ping` returns registry + worker + event-bus + version in <2 s single response.
- **NFR-O5:** Unknown `(event_type, schema_version)` halts ingestion + `event.unknown_schema`; silent drops are P0.
- **NFR-O6:** `agent.reasoning.*` events pass through the same secret sanitizer; redacted-stub fallback on failed sanitization.

**Maintainability (NFR-M1–M7):**
- **NFR-M1:** OMC/`clawhip` integrated only via adapter shims; no source vendored from them; general utility vendoring logged in `VENDORED.md`; dependency-graph CI check.
- **NFR-M2:** No auto-upgrade of upstream forks; version bumps gated by behavioral-contract integration tests in `tests/contract/`.
- **NFR-M3:** Within a major schema version, additive changes only; breaking changes require migrator container + Platform downtime.
- **NFR-M4:** Worker swap via single env-var change; Orchestrator + Registry code unchanged (S-1 test).
- **NFR-M5:** Orchestrator swap via single env-var change; Registry + Event Bus + Worker unchanged (S-3 test).
- **NFR-M6:** Every story cites ≥1 FR id; fits ≤1 operator-day; decompose or defer if not.
- **NFR-M7:** README contains quickstart, directory guide, deploy checklist, backup/restore, migrator runbook.

**Data-Volume Scalability (NFR-SC1–SC3):**
- **NFR-SC1:** Replay performance holds as event log grows (via snapshots).
- **NFR-SC2:** 10 GB data volume suffices for ≥6 months of typical activity + weekly snapshot + monthly log rotation.
- **NFR-SC3:** Phase 1 supports one active task per worker; multi-task parallelism is Phase 6.

**Total: 38 NFRs across 6 categories** (Integration refers to Domain + Project-Type sections, no separate NFRs).

### Additional Requirements

Extracted from Architecture. **Non-negotiable implementation constraints** that bind every story beyond the raw FRs/NFRs:

**Scaffold (blocks all other work):**
- No monolithic starter applies; per-component bootstrap in a `uv` workspace monorepo.
- **Scaffold epic (5 sequenced stories)** must land before any implementation:
  1. Monorepo proof — `uv` workspace root + `services/registry-api/` + `packages/events/` hello-world + top-level `README.md` with quickstart + directory guide + deploy checklist + backup/restore + migrator runbook.
  2. Remaining service + MCP scaffolds — 5 more services + 3 MCP servers + 2 more shared packages; all 12 `pyproject.toml` files resolve via `uv sync --all-packages`.
  3. Upstream vendoring — OMC + `clawhip` copied into `upstream/`; `VENDORED.md` manifest; `just sync-upstream` recipe; **scaffold `scripts/migrator/` with Dockerfile + trivial v1.0.0 → v1.0.1 additive-upgrade path** (per validation fix; machinery pre-built before first real schema bump).
  4. Compose + env + justfile — `docker-compose.yml`, `docker-compose.macos.yml`, `.env.example` (documents tunnel-first TLS options: Cloudflare Tunnel default, ngrok, bring-your-own), `justfile` (`dev`, `test`, `lint`, `scenarios`, `sync-upstream`, `backup`, `test-contract`).
  5. Test tree + CI skeleton — `tests/{separability,crash-injection,idempotency,integration,contract,migrator}/` each with one `@pytest.mark.skip` placeholder; `.github/workflows/ci.yml` running `uv sync --frozen && ruff check && ruff format --check && mypy --strict && pytest -m "not slow"`.

**Infrastructure baseline:**
- Phase 1 deployment: 5 Docker containers (telegram-gateway, registry-api, registry-state, orchestrator-adapter, worker-wrapper, clawhip-daemon) + optional console gateway. Single-target deploy (VPS *or* macOS, not split).
- Base image `python:3.12-slim-bookworm` (not Alpine); Node.js inside worker wrapper uses `node:lts-bookworm-slim`. Multi-stage Dockerfile via shared `Dockerfile.base`.
- TLS on Telegram webhook ingress: **not bundled**; operator chooses Cloudflare Tunnel (default) / ngrok / BYO reverse proxy.

**CI gates (all must go green before merge):**
- `ruff check` with custom `no-stdout-parse` rule across `services/**` + `mcp-servers/**`.
- `ruff format --check`.
- `mypy --strict` on `packages/**` and `services/registry-*`; relaxed at adapter shim boundaries.
- `scripts/check_imports.py` — no cross-service imports; domain layer has no IO-lib imports; packages never import from services/mcp-servers.
- `scripts/check_event_registry.py` — AST check every `emit_event(type=...)` literal exists in `packages/events/schema_registry.py`.
- `scripts/check_single_writer.py` — only `services/registry-state/**` may mutate registry state.
- Pre-commit secret-scanner blocks commits containing secret patterns.

**Test trees (required before MVP ship):**
- Unit tests colocated (`test_*.py` next to module).
- `tests/separability/` — S-1 cold worker swap, S-2 mid-flight swap, S-3 orchestrator pass-through.
- `tests/crash-injection/` — kill-host-at-each-phase harness (NFR-R2).
- `tests/idempotency/` — 100× concurrent replay + UUIDv7 fixture.
- `tests/integration/` — Journey 1, Journey 3, approval flow, license scan, command-injection fuzz (Hypothesis), log-capture, resume-after-approval (HIGH-RISK file), `test_decision_interleaving.py` (Hypothesis property test for async `/approve`+`/retry`+`/stop` interleaving, per validation fix).
- `tests/contract/` — OMC, `clawhip`, Anthropic, Telegram, GitHub adapter behavioral pins; gate `just sync-upstream`.
- `tests/migrator/` — `test_v1_0_0_to_v1_0_1.py` synthetic additive upgrade.
- Test harnesses: deterministic UUIDv7 injection, controlled clock, write-interrupt harness for file-edit atomicity, log-capture harness for secret redaction.

**Architectural commitments with enforcement:**
- **Immutable event envelope** — Pydantic v2 frozen model (`ConfigDict(frozen=True)`) in `packages/events/envelope.py`; fields: `event_id` (UUIDv7), `schema_version`, `type`, `emitted_at`, `emitted_at_monotonic_ns`, `actor`, `payload`, `parent_event_id?`, `trace_id?` (reserved, unused Phase 1), `request_id`.
- **Event log is sole mutation path** — orchestrator + workers + registry-api emit events via `clawhip-bridge` MCP server; `registry-state` is the lone subscriber that materializes state.
- **Read-only SQLite connection from `registry-api`** — mode=ro SQLAlchemy engine factory.
- **Idempotency** — UUIDv7 keys, 7-day cache via `cachetools.TTLCache` + SQLite durability; collision returns prior result verbatim.
- **RFC 7807 error envelope** — `application/problem+json` with `extensions` object for platform-specific fields.
- **Structlog JSON** — every log record carries `request_id`, `service`, `level`, `timestamp`, `event` (via `bind_contextvars`); log-sanitizer strips secret patterns.
- **Tenacity retries** — external API calls max 3× exp-backoff at adapter boundary; no retries in business logic.
- **Timeouts** — every outbound HTTP call has explicit `timeout=` (default 10 s); uvicorn entrypoints set `--timeout-keep-alive=10 --timeout-graceful-shutdown=30`.

**Upstream-fork governance:**
- Vendored-with-sync under `upstream/omc/` and `upstream/clawhip/`; `VENDORED.md` records commit SHAs; `just sync-upstream <name>` updates + runs contract tests; no git submodules.
- Phase 1 upstreams: OMC, `clawhip`. Phase 4 adds `browser-harness`. Phase 5 adds OMX + `claw-code`.

**HTTP API:**
- Versioned `/v1/`; additive-only until v2.
- Endpoints: `POST /v1/tasks`, `GET /v1/tasks/{id}`, `GET /v1/tasks/{id}/events`, `GET /v1/tasks/{id}/logs/digest`, `POST /v1/tasks/{id}/decisions`, `GET /v1/sessions/{id}`, `GET /v1/health`.
- FastAPI middlewares (ordered): request-id + idempotency-key extractor → log-sanitizer → rate limiter (Telegram webhook only, 10 req/s burst 20).

**MCP surface (Phase 1):**
- `task-registry` MCP server — resources: task list / detail / approval queue / blockers; tools: `task.add_note`, `task.attach_artifact`, `task.emit_event`.
- `session-registry` MCP server — resources: active sessions / worker metadata / heartbeats; tools: `session.heartbeat`, `session.register`, `session.close`.
- `clawhip-bridge` MCP server — resources: recent event stream (read-only), route diagnostics; tools: `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` (all append-only).

**Documentation (NFR-M7 non-negotiable):**
- Top-level `README.md` with quickstart, directory-structure explainer, deploy checklist (VPS + macOS), backup/restore procedure, migrator runbook — ships with scaffold story #1.
- `docs/operator-runbook.md`, `docs/schema-evolution.md`, `docs/deployment/{vps,macos}.md`, `docs/backup-restore.md`, `docs/exceptions.md`, `docs/testing-guide.md` (contract-fixture workflow).

**Minimum viable path to Bootstrap Milestone** (first end-to-end Journey 1 run executed by the platform, with the task being a real Phase 1 story; from Architecture §Implementation Handoff):
1. `packages/events/` (envelope + schema registry)
2. `services/registry-state/` (writer + subscriber + materializer + idempotency)
3. `mcp-servers/clawhip-bridge/` (event emission)
4. `services/registry-api/` (HTTP `/v1/tasks` + read paths)
5. `services/telegram-gateway/` (bot + minimum commands)
6. `services/worker-wrapper/` (Claude Code lifecycle + approval-gated push)

The other 5 components are MVP-required but not strictly Bootstrap-required; they can land after Bootstrap is hit.

**High-risk files** (extra care, pair review, explicit integration tests before merge):
- `services/worker-wrapper/domain/lifecycle.py` — resume-after-approval state machine (couples FR28 + FR29 + FR30 + FR36).
- `services/registry-state/domain/recovery.py` — snapshot replay idempotent across partial replay.
- `packages/events/envelope.py` — used everywhere; a bug corrupts every event.

### UX Design Requirements

**None.** Phase 1 has no GUI, no visual design spec. The two control surfaces (Telegram bot + local console CLI) are text-only. Message design (templates for approval request, blocker, completion, self-recovered, status reconstitution) is owned by `services/telegram-gateway/domain/message_templates.py` as code, not as a separate UX artifact. Operator-facing dashboard / web surface is Phase 7.

*Optional sidecar deliverable:* a focused `docs/message-design.md` within the Telegram-gateway epic specifying exact message templates, character budgets, markdown safety conventions, emoji discipline, `/status` one-message-reconstitution schema — see §6 in the Telegram gateway epic (added in Step 2).

### FR Coverage Map

| FR | Epic | Note |
|---|---|---|
| FR1 | E2 | task submission via HTTP API |
| FR2 | E5 | orchestrator produces plan |
| FR3 | E5 | autonomous execution |
| FR4 | E7 | `/status` reconstituted state |
| FR5 | E7 | `/logs` LLM digest |
| FR6 | E7 | raw event stream |
| FR7 | E6 | approve/reject/stop/retry |
| FR8 | E2 | lifecycle states + typed events |
| FR9 | E5 | completion summary |
| FR10 | E5 | PR draft auto-creation |
| FR11 | E3 | Telegram allowlist |
| FR12 | E4 | console parity |
| FR13 | E3 | thread binding |
| FR14 | E3 | approval messages |
| FR15 | E3 | blocker messages |
| FR16 | E3 | self-recovered summary |
| FR17 | E3 | `/ping` |
| FR17a | E3 | `/agent <task-id>` |
| FR17b | E3 + E5 | reasoning breadcrumbs surface in E3; emission in E5 |
| FR18a | E2 | typed event emission |
| FR18b | E2 | no stdout parsing (ruff rule) |
| FR19 | E2 | event routing to sinks |
| FR20 | E2 | append-only log |
| FR21 | E2 | schema versioning |
| FR22 | E2 | migrator execution (scaffolding in E1) |
| FR23 | E2 | event resources via MCP |
| FR24 | E2 | state survives restart |
| FR24a | E2 | failure detection + events |
| FR25 | E2 | snapshot strategy |
| FR26 | E2 | single writer |
| FR27 | E7 | worktree lock through blocker |
| FR28 | E2 | idempotency dedupe |
| FR29 | E2 | reattach after restart |
| FR30 | E7 | atomic file edits (coupled with E5 worker) |
| FR31 | E5 | orchestrator drives task |
| FR32 | E5 | worker registration |
| FR33 | E5 | worker reads task detail read-only |
| FR34 | E5 | worker swap proof — CI test is AC on E5 worker-lifecycle story |
| FR35 | E2 | orchestrator swap proof — CI test is AC on E2 event-spine-e2e story |
| FR36 | E6 | worker approval participation |
| FR37 | E6 | capability tiers |
| FR38 | E6 | Tier 3 approval |
| FR39 | E6 | pre-commit hook |
| FR40 | E6 | license scan |
| FR41 | E6 | license override |
| FR42 | E2 | `secret.accessed` events |
| FR43 | E2 | sanitization of events/snapshots |
| FR44 | E6 | budget exceeded |
| FR45 | E3 | input sanitization at ingress |
| FR46 | E1 | single-command deploy |
| FR47 | E1 | time-to-first-task budget |
| FR48 | E1 | env-var rotation |
| FR49 | E1 | structured JSON logs baseline (usage across all epics) |
| FR50 | E2 | migrator as one-shot container |
| FR51 | E1 | GHCR image publishing |
| FR52 | E1 | upgrade flow |

**100% FR coverage confirmed — 56 FRs mapped across 7 epics, zero orphans.**

### NFR Coverage Summary

- **E1:** NFR-M1, M2, M7, P4, S2, S7, SC2, O2
- **E2:** NFR-P3, R1, R2, R4, R5, S1, M3, **M5**, O1, O5, SC1
- **E3:** NFR-P1, P2, R3, S4, S5, O4
- **E4:** inherits E3 norms (no unique NFRs)
- **E5:** NFR-P5, R6, **M4**, O6, SC3
- **E6:** NFR-S3, S6, S8 (plus P5 enforcement point)
- **E7:** NFR-O3
- **Cross-cutting (every story):** NFR-M6 (≤1 operator-day per story, cite ≥1 FR)

**38 NFRs covered across 7 epics; zero orphans.**

## Epic List

Dependency graph:

```
E1 (scaffold)
  │
  ▼
E2 (event spine)  ────────────────┐
  │                               │
  ├──→ E3 (Telegram)               │
  ├──→ E4 (Console CLI)            │
  │                                │
  ▼                                ▼
E5 (autonomous exec) ────→ E6 (approval & policy) ────→ E7 (recon & recovery UX)
```

**Each epic is standalone:**
- E1 does not require any future epic.
- E2 delivers testable event-spine behavior against a stub worker; does not require E3/E4/E5.
- E3 works with a stub worker (uses E2's HTTP API); does not require E5.
- E4 parallel to E3; does not require E3.
- E5 uses E3 or E4 for task submission; delivers end-to-end execution against a real runtime.
- E6 adds safety gates to E5's flow; does not require E7.
- E7 delivers richer UX; no downstream epic dependencies.

**Bootstrap Milestone delivery** = E1 + E2 + E3 (minimum subset: `/task` + `/approve` + `/ping`) + E5 + E6. E4 optional for bootstrap; E7 is post-bootstrap polish.

**Separability thesis (formerly Epic 8)** is delivered as CI evidence by acceptance criteria on earlier stories — the thesis ships as test results, not as a dedicated epic:
- **S-1 cold worker swap** → AC on E5 "worker wrapper lifecycle" story (FR34, NFR-M4).
- **S-2 mid-flight worker swap** → AC on E5 "resume-after-approval" HIGH-RISK story (FR34, NFR-M4).
- **S-3 orchestrator pass-through** → AC on E2 "event-spine end-to-end with stub" story (FR35, NFR-M5).
- Env-var-driven worker/orchestrator image override → part of E1 scaffold (compose story).

---

### Epic 1: Scaffold & Deployability

*Operator can clone the repo, add `.env`, run one command, and watch a healthy (if empty) platform come up on VPS or macOS. The deployment surface is proven before any domain logic ships.*

**FRs covered:** FR46, FR47, FR48, FR49, FR51, FR52
**NFRs:** NFR-M1, NFR-M2, NFR-M7, NFR-P4, NFR-S2, NFR-S7, NFR-SC2, NFR-O2
**Additional:** the full 5-story scaffold epic from Architecture (monorepo proof → service + MCP scaffolds → upstream vendoring with pre-built migrator skeleton → compose + env + justfile → test tree + CI skeleton); CI gates (`ruff`, `ruff format`, `mypy --strict`, `scripts/check_imports.py`, `scripts/check_event_registry.py`, `scripts/check_single_writer.py`, pre-commit secret-scanner); `Dockerfile.base` multi-stage build; `docker-compose.yml` + `docker-compose.macos.yml`; tunnel-first TLS documentation (Cloudflare Tunnel default, ngrok, BYO reverse proxy); `VENDORED.md` manifest with `just sync-upstream`; top-level `README.md` (NFR-M7 checklist); operator documentation set under `docs/`; `tests/contract/` tree with recorded-fixture workflow guide.

*Standalone value: deployable hello-world stack proves the `docker compose up <30 min` KPI end-to-end. Operator has a working (empty) platform they can show off.*

---

### Epic 2: Event Spine & Registry

*Operator can submit a trivial task via HTTP API (or the test harness) through the event spine; state survives a forced restart; every mutation goes through typed events; the separability thesis is proved at the orchestrator layer via the S-3 test.*

**FRs covered:** FR1, FR8, FR18a, FR18b, FR19, FR20, FR21, FR22, FR23, FR24, FR24a, FR25, FR26, FR28, FR29, FR35, FR42, FR43, FR50
**NFRs:** NFR-P3, NFR-R1, NFR-R2, NFR-R4, NFR-R5, NFR-S1, NFR-M3, NFR-M5, NFR-O1, NFR-O5, NFR-SC1
**Additional:** `packages/events/` (immutable envelope + schema registry + canonical serializer + UUIDv7 ids + injectable clock); `services/registry-state/` single-writer with snapshot-aware recovery (HIGH-RISK file); `mcp-servers/clawhip-bridge/` append-only emission surface; `services/registry-api/` HTTP skeleton (POST /v1/tasks, GET /v1/tasks/{id}); `clawhip-daemon` wrapping vendored upstream with telegram-sink stub; idempotency cache (`cachetools.TTLCache` + SQLite durability); `check_event_registry.py` and `check_single_writer.py` active; synthetic-crash-injection harness; write-interrupt harness; idempotency 100× replay test; migrator integration test (v1.0.0→v1.0.1 additive); **S-3 separability test** (orchestrator swap pass-through) as an acceptance criterion on the event-spine-end-to-end story.

*Standalone value: `POST /v1/tasks` + forced-restart + replay passes. Proves the architectural spine independent of any runtime. Journey 3 (restart recovery) acceptance is reachable at the end of this epic.*

---

### Epic 3: Telegram Control Plane

*Operator drives the platform from Telegram — submits tasks, watches progress, gets status, receives approval/blocker/completion/self-recovered messages, runs `/ping` and `/agent`.*

**FRs covered:** FR11, FR13, FR14, FR15, FR16, FR17, FR17a, FR17b, FR45
**NFRs:** NFR-P1, NFR-P2, NFR-R3, NFR-S4, NFR-S5, NFR-O4
**Additional:** `services/telegram-gateway/` with `aiogram` v3 handlers; allowlist middleware; full command surface (`/task`, `/status`, `/logs`, `/approve`, `/reject`, `/stop`, `/retry`, `/ping`, `/agent`); message templates (approval, blocker, completion, self-recovered); Telegram sink in `clawhip-daemon`; RFC 7807 error handling on the app API; FastAPI middlewares (request-id + idempotency extractor + log-sanitizer + webhook rate limiter); command-injection fuzz test (Hypothesis); optional sidecar `docs/message-design.md` covering character budgets, markdown safety, emoji discipline, `/status` reconstitution schema.

**Story sequencing note (per Winston's party-mode finding):** deliver **`/task` + `/approve` + `/ping`** (minimum Bootstrap set) as the first three stories in this epic. `/status`, `/logs`, `/retry`, `/agent`, `/stop`, `/reject`, and the self-recovered summary ship after Bootstrap is hit. `/retry` with hint injection and the `/logs` LLM-digest adapter live in E7 (factored out there); this epic delivers the Telegram surface *layer* for those commands, but their business logic ships with E7.

*Standalone value: Telegram becomes a real operator surface against a stub worker; Journey 1 submit-and-watch is partially demonstrable (plan + execute + completion-summary messages flow) once E5 lands.*

---

### Epic 3.5: Tech-Debt Sweep (Epic 3 Retrospective)

*Tech debt, bug backports, and process documentation accumulated during Epic 3 but deferred to keep story scope clean. Completing these before Epic 4 prevents debt propagation.*

**FRs covered:** N/A (maintenance)
**NFRs:** N/A
**Additional:** Backport `approve_command.py` double-@ bug; refactor payload models to `packages/events/`; extract shared task-id helper; resolve pre-existing test failures; document dev-tooling quirks and architectural decisions; create handler template checklist; fix `check_imports.py` multi-tag noqa regex.

*Standalone value: clean codebase for Epic 4; prevents console-cli from inheriting telegram-gateway bugs; eliminates accumulated `# noqa: IMP001` cluster.*

---

### Epic 4: Console CLI Parity

*Operator at the Mac can run `oh-my-bmad-cli task …` / `status` / `logs` / `approve` / `retry` / `stop` / `ping` / `agent` / `events --follow` with 1:1 parity to Telegram.*

**FRs covered:** FR12
**NFRs:** inherits E3 reliability + latency norms; no unique NFRs.
**Additional:** `services/console-cli/` Typer binary; reuses `domain/commands.py` logic from `telegram-gateway`; exposed via `docker compose exec console oh-my-bmad-cli <...>`; `--follow` live event tail for debugging; same error-envelope rendering as Telegram (RFC 7807 → text).

*Standalone value: desk-side flow; proves surface parity is real, not just claimed. Parallelizable with E3.*

---

### Epic 5: Autonomous Task Execution

*Operator's submitted task is planned, executed, tested, and committed by the Claude Code worker end-to-end; completion summary + PR draft arrive. Runtime swappability is proved by the S-1 and S-2 tests.*

**FRs covered:** FR2, FR3, FR9, FR10, FR17b (reasoning emission), FR31, FR32, FR33, FR34
**NFRs:** NFR-P5, NFR-R6, NFR-M4, NFR-O6, NFR-SC3
**Additional:** `services/worker-wrapper/` with Claude Code CLI supervision; lifecycle state machine (HIGH-RISK file — `domain/lifecycle.py`); worktree locking; `agent.reasoning.*` breadcrumb emission with secret-sanitized payloads; atomic-edit primitive; GitHub API adapter (PR draft creation + tenacity retries); `services/orchestrator-adapter/` wrapping vendored OMC (subprocess supervision); `mcp-servers/task-registry/` + `mcp-servers/session-registry/` read-only resources + bounded-write tools; test-fixture scripted worker stub + canned events; **S-1 separability test** (cold worker swap) as AC on the worker-wrapper-lifecycle story; **S-2 separability test** (mid-flight swap) as AC on the resume-after-approval story (HIGH-RISK coupling of FR28 + FR29 + FR30 + FR36).

*Standalone value: Bootstrap Milestone becomes reachable — Journey 1 runs end-to-end (minus `git push` gating, which E6 adds). The platform-builds-its-own-features moment lives here.*

---

### Epic 6: Approval & Policy Gate

*Operator's `git push` is gated; license scan runs pre-push; approvals and overrides emit audit events; per-task budget ceilings enforce; capability tiers protect the platform.*

**FRs covered:** FR7, FR36, FR37, FR38, FR39, FR40, FR41, FR44
**NFRs:** NFR-P5 (enforcement point), NFR-S3, NFR-S6, NFR-S8
**Additional:** capability-tier enforcement at MCP handlers + HTTP API boundaries; pre-commit validation hook (sensitive paths, worktree boundary, commit-message injection); license scan integration (`scancode-toolkit` / `ORT` lightweight); approval audit events (`approval.granted`, `approval.rejected`, `tier3.action_attempted`, `tier3.action_performed`); `POST /v1/tasks/{id}/decisions` handler + decision shapes; `test_decision_interleaving.py` (Hypothesis property test for async `/approve`+`/retry`+`/stop` interleaving); tier-3 negative test; `test_license_scan.py`; secret-hygiene 3-layer enforcement (scanner + sanitizer + audit).

*Standalone value: Journey 2 approval flow works end-to-end; platform is now safe-for-real-tasks. Bootstrap Milestone is fully reachable at this epic's close.*

---

### Epic 7: Reconnaissance & Recovery UX

*Operator returning to a blocked task hours later gets full state in one message, LLM-digest logs, can `/retry` with a hint; restart-recovered overnight tasks get a proactive morning summary; atomic file-edit recovery is proven.*

**FRs covered:** FR4, FR5, FR6, FR27, FR30
**NFRs:** NFR-O3
**Additional:** `/v1/tasks/{id}` reconstituted-state handler; `/v1/tasks/{id}/logs/digest` LLM digest via Anthropic adapter (`llm_digest.py`); `/v1/tasks/{id}/events` raw event tail; `/v1/tasks/{id}/decisions` with `hint=` free-text injection; worktree-lock persistence through blocker windows; atomic file-edit primitive in worker (couples with E5's lifecycle); `tests/integration/test_journey_3_recovery.py` (MVP gate) and `tests/integration/test_journey_6_stale_blocker.py`; proactive self-recovered morning-summary logic in `clawhip-daemon`.

*Standalone value: Journey 3 + Journey 6 now have dedicated UX beyond what Epic 3's templates cover. Operator trust builds on visible resilience and context reconstitution.*

---

## Epic 1: Scaffold & Deployability

**Epic Goal:** Operator clones the repo, sets `.env`, runs `docker compose up`, and watches a healthy (if behaviorally empty) platform come up on VPS or macOS in under 30 minutes — proving the deployment surface before any domain logic ships.

### Story 1.1: Monorepo proof

As the operator,
I want a `uv` workspace monorepo with one sample service and one shared package,
So that I can verify the core workspace wiring resolves end-to-end before scaling the pattern.

**Acceptance Criteria:**

**Given** an empty repo
**When** I run `uv init --package --no-readme`, add `[tool.uv.workspace]` with `members=["services/*","packages/*","mcp-servers/*"]`, create `services/registry-api/` and `packages/events/` with minimal `pyproject.toml` + hello-world `__init__.py`
**Then** `uv sync` exits 0 and `uv run python -c "from events import __version__; print(__version__)"` prints the version string.

**And Given** the repo has no README
**When** the story is complete
**Then** a top-level `README.md` exists containing (a) 10-line quickstart, (b) directory-structure explainer naming `services/`, `mcp-servers/`, `packages/`, `upstream/`, `tests/`, `docs/`, (c) deployment checklist stub for VPS + macOS, (d) backup/restore procedure, (e) schema-migrator runbook placeholder.

*Cites: FR46, FR49, NFR-M7.*

### Story 1.2: Remaining service and MCP scaffolds

As the operator,
I want all 11 Phase 1 components scaffolded as uv workspace members,
So that `uv sync --all-packages` resolves the full dependency graph and I can add logic to any component without workspace-rewiring friction.

**Acceptance Criteria:**

**Given** scaffold story 1.1 is complete
**When** I run scaffold story 1.2
**Then** the tree contains `services/{registry-state, telegram-gateway, console-cli, orchestrator-adapter, worker-wrapper, clawhip-daemon}`, `mcp-servers/{task-registry, session-registry, clawhip-bridge}`, and `packages/{secret_hygiene, idempotency}` — each with minimal `pyproject.toml` + hello-world entrypoint.

**And When** I run `uv sync --all-packages`
**Then** all 12 `pyproject.toml` files resolve with no conflicts and `uv.lock` is deterministic (second run is a no-op).

*Cites: FR46, NFR-M1, NFR-M7.*

### Story 1.3: Upstream vendoring + migrator scaffold

As the operator,
I want OMC and `clawhip` vendored under `upstream/` with a sync recipe, plus a pre-built migrator skeleton,
So that upstream-fork governance is explicit and the schema-migrator machinery exists before the first real schema bump.

**Acceptance Criteria:**

**Given** the two upstream repos need tracking
**When** I run `just sync-upstream omc` (and then `clawhip`)
**Then** `upstream/omc/` and `upstream/clawhip/` contain the upstream source at a pinned commit SHA, and `VENDORED.md` records each fork's source URL + SHA + sync date.

**And Given** the migrator script does not yet exist
**When** story 1.3 completes
**Then** `scripts/migrator/` contains a `Dockerfile` + `src/migrator/__main__.py` implementing a trivial v1.0.0 → v1.0.1 additive-upgrade path, and `docker compose run --rm migrator v1.0.0-to-v1.0.1` runs successfully against an empty event log.

*Cites: FR22, FR50, FR51, NFR-M1, NFR-M2, NFR-M3.*

### Story 1.4: Compose + env + justfile

As the operator,
I want `docker-compose.yml` + `docker-compose.macos.yml` + `.env.example` + a `justfile` with operator recipes,
So that `docker compose up` brings up the (empty) stack on either deployment target and common dev/test/ops flows are one command away.

**Acceptance Criteria:**

**Given** all services are scaffolded with hello-world entrypoints
**When** I run `docker compose -f docker-compose.yml up -d` on Ubuntu 24.04
**Then** 5 containers (registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon) start cleanly and every service's healthcheck reports healthy within 60 s.

**And Given** I am on macOS 15
**When** I run `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d`
**Then** the same 5 containers start cleanly with macOS-specific volume-mount paths.

**And When** I inspect `.env.example`
**Then** every required env var is documented (`TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `TG_ALLOWLIST_USER_IDS`, `REGISTRY_DB_PATH`, `ENV`, `TUNNEL_MODE`) and the tunnel-first TLS options (Cloudflare Tunnel, ngrok, BYO proxy) are explained in comments.

**And When** I run `just --list`
**Then** recipes `dev`, `test`, `test-slow`, `test-contract`, `lint`, `scenarios`, `sync-upstream`, `backup`, `build`, `deploy-vps`, `deploy-macos` are present and each does what its name implies.

*Cites: FR46, FR47, FR48, FR52, NFR-P4, NFR-S2, NFR-S7, NFR-SC2.*

### Story 1.5: Test tree + CI skeleton

As the operator,
I want the full test-tree layout and a GitHub Actions CI pipeline that runs on every PR,
So that adding real tests in later stories drops into a working harness and regression guarding starts from day one.

**Acceptance Criteria:**

**Given** `tests/` does not yet exist
**When** story 1.5 completes
**Then** `tests/{separability, crash-injection, idempotency, integration, contract, migrator}/` each contain one placeholder `test_*.py` marked `@pytest.mark.skip("placeholder")`, and `tests/conftest.py` + `tests/fixtures/` exist.

**And When** I push a commit to a branch and open a PR
**Then** `.github/workflows/ci.yml` runs `uv sync --frozen && ruff check && ruff format --check && mypy --strict packages/ services/registry-* && pytest -m "not slow"` on a representative-spec runner and reports pass/fail on the PR.

**And When** the CI job completes successfully
**Then** PR checks show green status for every step.

*Cites: FR47, NFR-M7.*

### Story 1.6: Import-graph, event-registry, and single-writer CI gates

As the operator,
I want `scripts/check_imports.py`, `scripts/check_event_registry.py`, and `scripts/check_single_writer.py` wired into CI,
So that the three architectural discipline claims (no cross-service imports, no unregistered event emission, registry-state is sole writer) are enforced by automation rather than trust.

**Acceptance Criteria:**

**Given** a file in `services/registry-api/` imports from `services/registry-state/`
**When** CI runs `scripts/check_imports.py`
**Then** the check exits non-zero with a violation message naming the offending import and PR status turns red.

**And Given** `services/worker-wrapper/adapters/clawhip_client.py` calls `emit_event(type="new.type", ...)` and `"new.type"` is not in `packages/events/schema_registry.py`
**When** CI runs `scripts/check_event_registry.py`
**Then** the check exits non-zero with a violation message citing the call site and the missing registry entry.

**And Given** a file outside `services/registry-state/` calls `session.add(...)` or `session.execute(insert/update/delete/...)` against a SQLAlchemy session bound to the registry DB
**When** CI runs `scripts/check_single_writer.py`
**Then** the check exits non-zero unless the offending line carries a `# noqa: SW001 <reason>` inline comment.

**And When** all three checks run on a clean main branch
**Then** all three exit 0.

*Cites: NFR-M1, NFR-O1, FR18b, FR26.*

### Story 1.7: Secret-scanner pre-commit hook + sanitizer library

As the operator,
I want a pre-commit hook that blocks commits containing secret patterns and a runtime `structlog` processor that redacts secrets before log emission,
So that plaintext secrets cannot leak through source control or observability.

**Acceptance Criteria:**

**Given** `.pre-commit-config.yaml` wires a secret-scanner hook
**When** I attempt to commit a file containing a pattern matching `ANTHROPIC_API_KEY=sk-ant-*` or `TELEGRAM_BOT_TOKEN=[0-9]+:AA*`
**Then** the commit is blocked with a descriptive error naming the file and the matched pattern class.

**And Given** `packages/secret_hygiene/sanitizer.py` exports a structlog processor
**When** a service logs `log.info("auth ok", api_key="sk-ant-abc123")`
**Then** the emitted JSON log record contains `"api_key": "***REDACTED***"` (or similar sentinel) — never the original value.

**And When** a test captures log output via the log-capture fixture
**Then** the captured record can be asserted against using an exact string-match on `"***REDACTED***"`.

*Cites: FR43, NFR-S1.*

### Story 1.8: Dockerfile.base + multi-stage builds per service

As the operator,
I want a shared `Dockerfile.base` multi-stage template and a per-service `Dockerfile` override for each Python service,
So that all services build consistently and the final runtime image stays under 200 MB.

**Acceptance Criteria:**

**Given** `Dockerfile.base` exists with stage-1 (`uv sync --frozen --no-dev --all-packages` into `/opt/venv`) and stage-2 (`python:3.12-slim-bookworm` + `/opt/venv` + per-service entrypoint)
**When** I run `docker build -f services/registry-api/Dockerfile .`
**Then** the resulting image runs the registry-api service and its size is ≤ 200 MB.

**And When** I repeat the build for every Python service (`registry-state`, `telegram-gateway`, `console-cli`, `orchestrator-adapter`, `clawhip-daemon`) and the Node-inclusive `worker-wrapper`
**Then** each image builds successfully and the `worker-wrapper` image includes both `python:3.12-slim-bookworm` and `node:lts-bookworm-slim` layers via multi-stage composition.

*Cites: FR46, FR51.*

### Story 1.9: GHCR image publishing on git tag

As the operator,
I want `.github/workflows/release.yml` to build and publish multi-arch Docker images to GHCR on git tag push,
So that deploying a new version is a `docker compose pull && up -d` away — no manual image building on the VPS.

**Acceptance Criteria:**

**Given** I push a git tag `v0.1.0`
**When** `release.yml` runs
**Then** it builds `linux/amd64` + `linux/arm64` images for every platform-owned service and pushes them to `ghcr.io/<owner>/oh-my-bmad-<service>:0.1.0` and `:latest`.

**And Given** the release workflow completes successfully
**When** I run `docker compose pull` on a deployment host
**Then** all service images update to the new tag without manual image rebuild steps.

*Cites: FR51, FR52.*

### Story 1.10a: Deployment quickstart docs (Bootstrap-blocker)

As the operator,
I want `docs/deployment/vps.md` + `docs/deployment/macos.md` + the quickstart extension of the top-level README,
So that a cold return to the project reaches Bootstrap Milestone without out-of-doc guessing.

**Acceptance Criteria:**

**Given** the operator has never seen the project before
**When** they follow `docs/deployment/vps.md` (or `docs/deployment/macos.md`) step-by-step
**Then** they reach first-completed-task within 30 min with no out-of-doc guesses required.

**And Given** the top-level README's quickstart
**When** the operator copy-pastes the 10 commands
**Then** the stack is up and the first `/ping` succeeds.

*Cites: NFR-M7 (quickstart + deploy checklist subsets).*

### Story 1.10b: Full operator documentation set (MVP-ship-blocker, post-Bootstrap)

As the operator (and any future collaborator),
I want `docs/operator-runbook.md` + `docs/schema-evolution.md` + `docs/exceptions.md` + `docs/testing-guide.md` + `docs/backup-restore.md` + `docs/message-design.md` delivered,
So that a full runnable, recoverable, maintainable, debuggable documentation set exists before MVP ship.

**Acceptance Criteria:**

**Given** a running platform with active tasks
**When** the operator follows `docs/backup-restore.md` (`just backup` + manual rsync to off-host target)
**Then** they can restore the `/var/lib/oh-my-bmad/` volume on a fresh host and resume operations with no event loss.

**And When** the operator runs `just test-contract`
**Then** `docs/testing-guide.md` describes the contract-fixture recording workflow in enough detail for them to add a new adapter contract test without asking.

**And When** the operator needs to evolve the event schema
**Then** `docs/schema-evolution.md` walks them through additive changes + migrator runbook.

**And When** the operator references `docs/message-design.md`
**Then** every Telegram message template has an example rendering, a character budget, a field list, and a rationale.

*Cites: NFR-M7 (full set).*

---

## Epic 2: Event Spine & Registry

**Epic Goal:** Operator submits a trivial task via HTTP API (or test harness); every state transition is a typed event; state survives forced restart; the event spine is the only mutation path; the S-3 separability test proves orchestrator-layer swappability.

### Story 2.1: Event envelope + schema registry + canonical serializer

As a platform service,
I want an immutable `EventEnvelope` Pydantic v2 model (frozen, with full field set) + a central schema registry + a canonical JSON serializer,
So that every event across every service has a single shared shape that cannot be mutated after construction and replays deterministically.

**Acceptance Criteria:**

**Given** `packages/events/envelope.py` defines `EventEnvelope` as `ConfigDict(frozen=True)` with fields `event_id`, `schema_version`, `type`, `emitted_at`, `emitted_at_monotonic_ns`, `actor`, `payload`, `parent_event_id?`, `trace_id?`, `request_id`
**When** code attempts to mutate a constructed envelope (e.g., `env.payload = {...}`)
**Then** Pydantic raises a validation error.

**And Given** `packages/events/schema_registry.py` defines a `(event_type, schema_version) → payload_model` table
**When** `EventEnvelope.create(type="task.created", payload={...})` is called and `"task.created"` is not in the registry
**Then** a `EventSchemaUnknown` typed exception is raised.

**And When** the same envelope is serialized twice via `canonical.py`
**Then** both outputs are byte-identical (sorted keys, no whitespace, UTF-8).

*Cites: FR18a, FR20, FR21, NFR-O5.*

### Story 2.2: UUIDv7 + injectable clock

As a platform service,
I want `packages/events/ids.py` exporting UUIDv7 generation with prefixed-id helpers, and `packages/events/clock.py` exporting an injectable clock,
So that all task/session/event IDs are time-ordered and tests can control time deterministically.

**Acceptance Criteria:**

**Given** `ids.py` exports `new_task_id()`, `new_session_id()`, `new_event_id()`, `new_idempotency_key()`
**When** I call `new_task_id()`
**Then** the result matches `^t-[0-9a-f-]{36}$` and sorts lexicographically by creation time.

**And Given** `clock.py` exports `Clock` protocol with `now()` + `monotonic_ns()` and a `FrozenClock(ts)` test double
**When** a test injects `FrozenClock(42)` into envelope construction
**Then** `envelope.emitted_at_monotonic_ns == 42`.

*Cites: NFR-O1 (determinism), NFR-M6 (test fixtures).*

### Story 2.3: Registry-state SQLite schema + initial Alembic migration

As a platform service,
I want `services/registry-state/` with a SQLAlchemy 2.x async schema for tasks, sessions, events, idempotency_cache, and an Alembic initial migration,
So that registry state has a deterministic on-disk representation that can evolve safely.

**Acceptance Criteria:**

**Given** the service starts with an empty data volume
**When** Alembic runs `upgrade head`
**Then** SQLite contains tables `tasks`, `sessions`, `events`, `idempotency_cache`, `snapshots` with the correct columns + `ix_events_task_id_emitted_at` + related indexes.

**And When** the service restarts against an already-migrated database
**Then** Alembic detects head and is a no-op.

*Cites: FR24, FR28.*

### Story 2.4: Event-log append writer (JSONL)

As `registry-state`,
I want to append every received event to a per-day JSONL file under `/var/lib/oh-my-bmad/registry/events/YYYY-MM-DD.jsonl`,
So that the append-only event log is the durable source of truth regardless of what SQLite holds.

**Acceptance Criteria:**

**Given** the write path receives `EventEnvelope` instances
**When** `event_log.append(env)` is called
**Then** the canonical JSON serialization is appended to the current-day file with `fsync` after every write, terminated by `\n`.

**And Given** the service crashes mid-write
**When** it restarts
**Then** the event log file contains only complete JSONL lines (no half-written records).

*Cites: FR20, NFR-O5.*

### Story 2.5: Event-log subscriber + state materializer

As `registry-state`,
I want a subscriber loop that reads the event log and materializes state into SQLite,
So that the derived state is always recomputable from the log and the single-writer discipline is preserved.

**Acceptance Criteria:**

**Given** the event log contains events `task.created` → `task.planning.started` → `task.plan.ready`
**When** the materializer replays the log from start
**Then** `tasks` has one row with state `planning` and the latest event id recorded.

**And Given** the subscriber is running and new events are appended
**When** a `task.execution.started` event is appended
**Then** the `tasks` row transitions to `executing` within 1 s.

**And When** `scripts/check_single_writer.py` runs
**Then** no other service contains code that writes to any of these tables — the subscriber in `registry-state` is the sole writer.

*Cites: FR8, FR26, FR24a.*

### Story 2.6: Snapshot capture and replay on startup

As `registry-state`,
I want to snapshot materialized state every N events (configurable default 1000) into a `snapshots` table row, and on startup replay from the latest snapshot + subsequent events only,
So that startup time stays under 5 s even after 10K events.

**Acceptance Criteria:**

**Given** an event log containing 10K events + 10 snapshots spaced every 1000 events
**When** the service starts with an empty SQLite
**Then** startup replay completes in <5 s on the reference runner and the resulting task/session state matches a full replay-from-zero byte-for-byte.

**And Given** the latest snapshot is at event #9000 and subsequent events are #9001–#10000
**When** replay runs
**Then** only events #9001–#10000 are re-materialized (verified via instrumentation counter).

*Cites: FR25, NFR-P3, NFR-SC1.*

### Story 2.7: Idempotency cache

As `registry-state`,
I want `packages/idempotency/cache.py` combining `cachetools.TTLCache` (in-process) with a SQLite `idempotency_cache` table for durability,
So that idempotent command submissions survive restart and 100× replay never double-executes.

**Acceptance Criteria:**

**Given** a `POST /v1/tasks` with `Idempotency-Key: uuid-X`
**When** it is received twice back-to-back
**Then** the first call creates the task; the second returns the stored prior response (status + body) without side-effects.

**And Given** the service restarts after handling `uuid-X` but before the 7-day TTL expires
**When** the same key is resubmitted
**Then** the stored response is returned from the SQLite-backed cache.

**And When** 100 concurrent duplicate submissions arrive
**Then** `tasks` contains exactly one row for that key and all 100 responses are byte-identical.

*Cites: FR28, NFR-R4.*

### Story 2.8: clawhip-bridge MCP server (append-only emission)

As orchestrator / worker / registry-api,
I want `mcp-servers/clawhip-bridge/` exposing `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` as append-only MCP tools + `recent_events` as a read-only resource,
So that every component has a single canonical path to emit events into the spine.

**Acceptance Criteria:**

**Given** an MCP client calls `clawhip-bridge.emit_event(type="task.created", payload={...})`
**When** the bridge processes the call
**Then** the event is (a) validated against the envelope model, (b) passed to the event-log writer, (c) returned to the caller with the assigned `event_id`.

**And Given** the client attempts to call any tool that would modify a prior event
**Then** no such tool exists on the server — the full tool surface is strictly append-only.

**And Given** the client reads the `recent_events` MCP resource with `limit=50`
**Then** it receives the 50 most-recent events from the current-day log file.

*Cites: FR18a, FR19, FR23, FR26.*

### Story 2.9: Registry-api HTTP skeleton (`POST /v1/tasks` + `GET /v1/tasks/{id}`)

As the operator (via Telegram or console),
I want `POST /v1/tasks` to create a task and `GET /v1/tasks/{id}` to return its current materialized state,
So that the platform has an HTTP ingress + read surface that other services (bot, CLI) can consume.

**Acceptance Criteria:**

**Given** the body is a valid `CreateTaskRequest` with `Idempotency-Key`
**When** `POST /v1/tasks` is called
**Then** the handler emits `task.created` via `clawhip-bridge`, materializer creates the row, and the response is `201 Created` with `{"task_id": "t-..."}`.

**And Given** a materialized task exists for id `t-0001`
**When** `GET /v1/tasks/t-0001` is called
**Then** the response is `200 OK` with task state, last event summary, last agent action summary, and available next commands — all in a single response (no scrollback required).

**And Given** `scripts/check_single_writer.py` runs against `services/registry-api/`
**Then** it exits 0 (registry-api does not write registry state directly).

*Cites: FR1, FR4, FR8, FR26.*

### Story 2.10: Failure-detection typed events

As the platform,
I want `registry-state` to emit `service.crashed`, `session.heartbeat_timeout`, `sink.delivery_failed`, `task.stop_requested` typed events on detection of those conditions,
So that recovery paths are driven by explicit signals rather than implicit timers.

**Acceptance Criteria:**

**Given** a worker container exits with a non-zero code
**When** the supervising process detects the exit
**Then** a `service.crashed` event is emitted within 60 s with `{"service": "...", "exit_code": N}`.

**And Given** a worker heartbeat is overdue by more than 2× its configured interval
**Then** a `session.heartbeat_timeout` event is emitted.

**And Given** the Telegram sink's outbound delivery fails 3× consecutively
**Then** a `sink.delivery_failed` event is emitted.

*Cites: FR24a, NFR-R5.*

### Story 2.11: Synthetic crash-injection harness

As a CI pipeline,
I want a harness that launches the stack, triggers tasks, kills the host at each lifecycle phase, restarts, and asserts state-reconstruction with zero duplicate events,
So that NFR-R2 (zero tasks lost) is continuously verified instead of audit-log-reviewed manually.

**Acceptance Criteria:**

**Given** the harness runs in CI
**When** a task is in state `planning` / `executing` / `awaiting_approval` / `verifying`
**Then** the harness (a) `docker compose stop --timeout 1`, (b) `docker compose up -d`, (c) asserts the task resumes from the last committed event with zero duplicate events in the log.

**And When** all four lifecycle phases are exercised
**Then** the harness exits 0 and produces a summary artifact.

*Cites: FR24, NFR-R1, NFR-R2.*

### Story 2.12: Write-interrupt harness + atomic-edit verification

As a CI pipeline,
I want a harness that pauses a file-edit mid-syscall, kills the process, restarts, and verifies the file is either fully written or untouched — never partial,
So that the atomicity claim for worker file edits (FR30) is testable deterministically.

**Acceptance Criteria:**

**Given** the worker-wrapper's atomic-edit primitive from `packages/` is under test
**When** the harness interrupts the write between byte N and N+1
**Then** the target file's post-restart state is either identical to the pre-edit content or identical to the fully-edited content — never a mixed byte sequence.

**And When** the harness runs 100 randomized interruption points
**Then** 100/100 runs satisfy the invariant.

*Cites: FR30, NFR-R2.*

### Story 2.13: Idempotency 100× replay test

As a CI pipeline,
I want a test that submits the same `POST /v1/tasks` command 100 times concurrently and asserts exactly one task row and 100 byte-identical responses,
So that NFR-R4 (zero duplicate executions under retry storm) is regression-proof.

**Acceptance Criteria:**

**Given** a fresh deployment
**When** 100 concurrent `POST /v1/tasks` calls are issued with the same `Idempotency-Key`
**Then** the `tasks` table contains exactly 1 row and all 100 HTTP responses have byte-identical bodies and `201` status.

**And When** the test runs 10 times in CI
**Then** 10/10 runs pass (no flakiness).

*Cites: FR28, NFR-R4.*

### Story 2.14: Migrator integration test (v1.0.0 → v1.0.1 additive)

As a CI pipeline,
I want a test that runs the migrator scaffold on a synthetic v1.0.0 event log and verifies the migrated log is v1.0.1-compliant,
So that the schema-evolution machinery is exercised before any real schema bump.

**Acceptance Criteria:**

**Given** a fixture event log with 100 v1.0.0 events
**When** `docker compose run --rm migrator v1.0.0-to-v1.0.1` runs
**Then** the migrated log contains 100 v1.0.1-shaped events, the original log is archived with suffix `.v1.0.0.archive`, and a fresh `registry-state` reading the migrated log materializes identical state.

*Cites: FR22, NFR-M3.*

### Story 2.15: S-3 separability test — orchestrator pass-through

As a CI pipeline,
I want `tests/separability/test_s3_orchestrator_swap.py` that replaces OMC with a null-orchestrator stub and runs a canned task end-to-end,
So that the orchestrator-layer swappability claim (NFR-M5, FR35) is a CI-verified fact.

**Acceptance Criteria:**

**Given** `tests/fixtures/null_orchestrator.py` emits `task.planning.started` → `task.plan.ready` → `task.execution.requested` for any submitted task
**When** the compose stack is booted with `ORCHESTRATOR_IMAGE=null-orchestrator:latest`
**Then** a `POST /v1/tasks` run completes via `task.completed` without any source changes to `registry-state`, `registry-api`, `clawhip-bridge`, or `worker-wrapper`.

**And When** the test runs
**Then** it asserts the platform's spine code was not modified (git diff check passes) and the task completes successfully.

*Cites: FR35, NFR-M5.*

### Story 2.16: secret.accessed audit event emission

As the platform,
I want `secret.accessed` typed events emitted on every read of a configured secret (Telegram bot token, GitHub PAT, Anthropic API key, Docker registry credentials),
So that secret access has an audit trail queryable from the registry.

**Acceptance Criteria:**

**Given** a service reads `settings.anthropic_api_key` through `pydantic-settings`
**When** the read happens
**Then** a `secret.accessed` event with `{"secret_name": "anthropic_api_key", "actor": {"kind": "service", "id": "worker-wrapper"}, "scope": "read"}` is emitted — without including the secret value in the payload.

*Cites: FR42, NFR-S3.*

### Story 2.17: Log-capture harness + NFR-S1 redaction test

As a CI pipeline,
I want a pytest fixture that captures the platform's structlog JSON output and a test that asserts no plaintext secret value ever appears in captured records,
So that the runtime sanitizer's correctness is continuously verified (per Murat's Phase 1 test-infra commitment).

**Acceptance Criteria:**

**Given** the log-capture fixture is wired into the test suite
**When** integration tests exercise code paths that log messages containing secret-shaped fields
**Then** captured records contain `"***REDACTED***"` for those fields and the test asserts the exact whitelist of allowed fields.

**And Given** a plaintext secret pattern appears in any captured record
**When** the test runs
**Then** it fails with a specific error naming the offending log record.

*Cites: FR43, NFR-S1.*

---

## Epic 3: Telegram Control Plane

**Epic Goal:** Operator drives the platform from Telegram — submits tasks, watches progress, gets structured status/blocker/completion/self-recovered messages, approves risky steps. First three stories (`/task`, `/approve`, `/ping`) are the Bootstrap Minimum Subset; remaining stories land after Bootstrap Milestone.

### Story 3.1: aiogram v3 bootstrap + webhook config

As the operator,
I want `services/telegram-gateway/` wired with `aiogram` v3 async dispatcher + FastAPI webhook endpoint + `pydantic-settings` config,
So that the Telegram bot is reachable from Telegram servers via a public URL.

**Acceptance Criteria:**

**Given** `.env` contains `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_URL`
**When** the service starts
**Then** it registers the webhook with Telegram and logs `"Webhook set · ready"`.

**And When** a test sends a synthetic update to the webhook endpoint
**Then** the dispatcher receives it and returns `200` within 500 ms.

*Cites: FR11 (enabling), NFR-R3.*

### Story 3.2: Allowlist middleware + rejection event

As the operator,
I want every inbound Telegram update checked against `TG_ALLOWLIST_USER_IDS`; non-allowlisted senders receive no response and the rejection is recorded as a typed event,
So that FR11 is enforced at the ingress with an audit trail.

**Acceptance Criteria:**

**Given** `TG_ALLOWLIST_USER_IDS=[12345]`
**When** a message arrives from user id 67890
**Then** the bot returns no response to Telegram; a `telegram.rejected` typed event is emitted containing `{"user_id": 67890, "reason": "not_in_allowlist"}`; the rejecter never sees confirmation.

**And When** a message arrives from user id 12345
**Then** the middleware passes through to command dispatch.

*Cites: FR11, NFR-S4.*

### Story 3.3: `/task` command (Bootstrap Minimum #1)

As the operator,
I want to send `/task <description>` and have the bot create the task through `POST /v1/tasks`,
So that I can kick off autonomous work from my phone.

**Acceptance Criteria:**

**Given** I am an allowlisted user
**When** I send `/task add rate-limit header to gateway`
**Then** the bot (a) calls `POST /v1/tasks` with `Idempotency-Key` generated from Telegram `message_id`, (b) replies within 3 s with `Task <task-id> created. Planning. Events on thread.` including the new `t-…` id.

**And Given** the same Telegram `message_id` is delivered twice (Telegram retry)
**When** both deliveries hit the bot
**Then** both result in the same task_id (idempotency honored) and the operator sees one creation message.

*Cites: FR1, FR28, NFR-P2.*

### Story 3.4: `/approve` command (Bootstrap Minimum #2)

As the operator,
I want to send `/approve <task-id>` and have the bot grant the pending approval,
So that I can unblock a `git push` from my phone.

**Acceptance Criteria:**

**Given** a task is in state `awaiting_approval`
**When** I send `/approve t-0001`
**Then** the bot calls `POST /v1/tasks/t-0001/decisions` with `{"action": "approve"}` and replies within 3 s with `Approved by @r2d2 at <ts>. Pushing.`.

**And Given** a task is not in `awaiting_approval`
**When** I send `/approve t-0001`
**Then** the bot replies with an RFC 7807-rendered error saying `Task is in state <X>; cannot approve`.

*Cites: FR7, NFR-P2.*

### Story 3.5: `/ping` command (Bootstrap Minimum #3)

As the operator,
I want `/ping` to return a one-line platform health summary,
So that I can check the stack from anywhere.

**Acceptance Criteria:**

**Given** the stack is running
**When** I send `/ping`
**Then** the bot calls `GET /v1/health` and replies within 2 s with `pong · registry: healthy · worker: idle · clawhip: <N> events queued · version: <vX.Y.Z>`.

*Cites: FR17, NFR-O4.*

### Story 3.6: FastAPI middleware stack (request-id + idempotency + log-sanitizer + webhook rate limiter)

As the platform,
I want the four middlewares wired on `services/registry-api/` (and the webhook rate limiter scoped to the Telegram webhook route only),
So that cross-cutting concerns are enforced uniformly without per-handler code.

**Acceptance Criteria:**

**Given** a request arrives without `X-Request-ID`
**When** the request-id middleware processes it
**Then** a UUIDv7 is generated and attached to `request.state.request_id`, echoed in the response header, and bound into structlog context.

**And Given** a request arrives without `Idempotency-Key` to a mutation endpoint
**When** the idempotency middleware processes it
**Then** a UUIDv7 is generated server-side and the response echoes it in a header; the operator is nudged via the error-envelope extensions that client-generated keys are preferred.

**And Given** 30 requests/s burst against the Telegram webhook endpoint
**When** the rate limiter processes them
**Then** the first 20 pass (burst) and subsequent requests receive `429 Too Many Requests` until the bucket refills at 10 req/s.

*Cites: FR28, NFR-S1, NFR-S7.*

### Story 3.7: RFC 7807 error envelope + Telegram rendering

As the operator,
I want every API error returned as `application/problem+json` per RFC 7807, and the Telegram bot renders these errors as human-readable messages,
So that failures communicate actionable reasons, not opaque status codes.

**Acceptance Criteria:**

**Given** a `POST /v1/tasks` with an invalid body
**When** the handler raises a Pydantic `ValidationError`
**Then** the response is `422` with body `{"type": "/errors/validation", "title": "Invalid request", "status": 422, "detail": "...", "instance": "/v1/tasks", "extensions": {...}}`.

**And When** the Telegram bot receives such a response while processing `/task`
**Then** it replies with a formatted message naming the specific fields that failed — not a raw JSON dump.

*Cites: (implementation of already-locked decision from Architecture §Core Architectural Decisions — Category 3).*

### Story 3.8: Command-injection fuzz test (Hypothesis)

As a CI pipeline,
I want a Hypothesis-based fuzz test that generates random operator inputs (null bytes, shell metacharacters, nested quoting, directory traversal, ANSI escapes, git ref-name injection) and asserts no injection reaches shell/git/MCP call sites,
So that NFR-S5 is continuously verified.

**Acceptance Criteria:**

**Given** the fuzz test is wired into CI
**When** it runs 10,000 generated inputs against `/task` and `/retry hint=`
**Then** no input results in an unescaped shell or git invocation and no MCP call is made with non-sanitized payload fields.

*Cites: FR45, NFR-S5.*

### Story 3.9: Task thread binding + message delivery routing

As the operator,
I want every progress event for a task to deliver to the same Telegram thread,
So that I can follow a task's lifecycle in one conversation instead of a global feed.

**Acceptance Criteria:**

**Given** a task was created from chat_id `C` and message_id `M`
**When** the platform emits progress events for that task
**Then** the Telegram sink routes the outbound messages to chat_id `C`, replying to message_id `M` (creating a topic/thread binding).

*Cites: FR13.*

### Story 3.10: Approval-request message template

As the operator,
I want approval-required messages to include risk class, pre-check results, diff summary, and the exact commands accepted,
So that I can decide `/approve` / `/reject` without scrolling or context-switching.

**Acceptance Criteria:**

**Given** a task reaches `task.awaiting_approval` for a `git push` step
**When** the telegram-sink renders the outbound message
**Then** the message contains: `🔒 Approval required — task <id>`, action line, risk class, pre-check results (lint/types/unit/integration with counts and ✅/❌), diff summary (`N files, +X, -Y`), accepted commands line — all in a single Telegram message under 4096 characters.

*Cites: FR14.*

### Story 3.11: Blocker notification template

As the operator,
I want blocker messages to include blocked-since timestamp, last event, last agent action, and the enumerated available commands,
So that I know what to do next without querying the registry manually.

**Acceptance Criteria:**

**Given** a task emits `task.blocked`
**When** the telegram-sink renders the outbound message
**Then** the message contains `⛔ Task <id> blocked. <reason>. See /logs <id> for detail.` plus a compact list of available commands (`/logs`, `/retry`, `/stop`, `/handoff`).

*Cites: FR15.*

### Story 3.12: Completion summary template

As the operator,
I want completion messages to show file count, line count, test count, CI state, and blockers encountered,
So that I can scan a morning summary in one glance.

**Acceptance Criteria:**

**Given** a task emits `task.completed`
**When** the telegram-sink renders the outbound message
**Then** the message contains `✅ Task <id> complete. PR #<N>: <branch>. <files> files changed, <lines> lines, <tests> tests added. CI green. <blockers> blockers raised.`.

*Cites: FR9.*

### Story 3.13: Self-recovered summary template

As the operator,
I want a proactive morning Telegram message whenever the host self-recovered from a restart during an overnight task,
So that I earn confidence from visible resilience rather than silent resilience.

**Acceptance Criteria:**

**Given** a task's event log contains a `session.reconnecting` + `task.execution.resumed` pair emitted between 00:00 and the next morning's completion summary
**When** the morning completion summary fires
**Then** a second compact message `🛠️ Self-recovered from host restart at <ts>. <N> events replayed in <ms>. Zero intervention required.` is emitted alongside.

*Cites: FR16.*

### Story 3.14: `/status` command (Telegram surface)

As the operator,
I want `/status <task-id>` to render whatever `GET /v1/tasks/{id}` returns in a single human-friendly Telegram message,
So that reconnaissance after a blocker doesn't require scrollback.

**Acceptance Criteria:**

**Given** a task exists in any state (and Story 2.9 has delivered the basic endpoint)
**When** I send `/status t-0001`
**Then** the bot calls `GET /v1/tasks/t-0001` and replies with a single message rendering every available field from the response.

**And Note:** this story delivers the Telegram surface and basic rendering; when E7 Story 7.1 enhances the endpoint to return the full reconstituted state (state + since-timestamp, current step, last event, last agent action, worktree lock state, available commands), this command's output automatically becomes richer without further surface changes.

*Cites: FR4 (full business logic in E7; this story delivers the Telegram surface + basic rendering).*

### Story 3.15: `/logs` command (Telegram surface)

As the operator,
I want `/logs <task-id>` to render the LLM-digest response from `GET /v1/tasks/{id}/logs/digest`,
So that I get actionable context in a single message.

**Acceptance Criteria:**

**Given** a task exists and Story 7.3 has delivered the digest endpoint
**When** I send `/logs t-0001`
**Then** the bot calls `GET /v1/tasks/t-0001/logs/digest` and replies with a ≤20-line summary.

**And Note:** Until Story 7.3 lands, this command returns a placeholder message explaining that the digest is not yet available — callers should run `oh-my-bmad-cli events t-0001` for the raw stream. Once Story 7.3 completes, this command's output becomes the real LLM digest without further changes to this Telegram surface.

*Cites: FR5 (full business logic in E7; this story delivers the Telegram surface + placeholder behavior).*

### Story 3.16: `/stop` command

As the operator,
I want `/stop <task-id>` to halt a running task and release its worktree lock,
So that I can kill work that's gone sideways.

**Acceptance Criteria:**

**Given** a task is in state `executing` / `awaiting_approval` / `verifying`
**When** I send `/stop t-0001`
**Then** the bot calls `POST /v1/tasks/t-0001/decisions {action:stop}`, the task transitions to `stopped`, the worktree lock releases, and the bot confirms within 3 s.

*Cites: FR7.*

### Story 3.17: `/reject` command

As the operator,
I want `/reject <task-id> <reason>` to explicitly reject a pending approval with a recorded reason,
So that reject is distinct from stop and auditable.

**Acceptance Criteria:**

**Given** a task is in `awaiting_approval`
**When** I send `/reject t-0001 "push before review"`
**Then** the bot calls `POST /v1/tasks/t-0001/decisions {action:reject, reason:"..."}`, an `approval.rejected` event is emitted with the reason string, and the task transitions to `rejected`/`stopped` per approval semantics.

*Cites: FR7.*

### Story 3.18: `/retry` command (Telegram surface, hint passthrough)

As the operator,
I want `/retry <task-id> hint="..."` to resume a blocked task with my clarifying hint injected into the orchestrator's next plan,
So that I can course-correct without re-submitting a full task.

**Acceptance Criteria:**

**Given** a task is in `blocked`
**When** I send `/retry t-0001 hint="rate limit must be per-user, not per-IP"`
**Then** the bot calls `POST /v1/tasks/t-0001/decisions {action:retry, hint:"..."}`, the task transitions to `planning` with the hint carried in the event payload, and the bot confirms.

*Cites: FR7 (Telegram surface; hint-injection business logic in E7).*

### Story 3.19: `/agent` command

As the operator,
I want `/agent <task-id>` to report which runtime/provider owns the task,
So that I future-proof Phase 5 multi-runtime — and in Phase 1 know that Claude Code is the one runtime.

**Acceptance Criteria:**

**Given** a task is active
**When** I send `/agent t-0001`
**Then** the bot replies with `Task t-0001: runtime=claude-code, worker_id=w-..., session_id=s-...`.

*Cites: FR17a.*

### Story 3.20: Optional sidecar — `docs/message-design.md`

As the operator,
I want a single reference doc specifying Telegram message templates, character budgets, markdown safety conventions, emoji discipline, and the `/status` reconstitution schema,
So that message-design choices are documented and reviewable outside of code.

**Acceptance Criteria:**

**Given** stories 3.10–3.15 are complete
**When** `docs/message-design.md` lands
**Then** each message template has an example rendering, a character budget, a field list, and a rationale paragraph.

*Cites: none directly (supporting infra for E3's templates).*

---

## Epic 4: Console CLI Parity

**Epic Goal:** Operator at the Mac runs `oh-my-bmad-cli <command>` with 1:1 parity to the Telegram surface, plus a live event tail for debugging. Parallelizable with E3 after E2's Registry API exists.

### Story 4.1: Typer binary scaffold + entrypoint

As the operator,
I want `services/console-cli/` packaged as a Typer-based CLI binary invokable via `docker compose exec console oh-my-bmad-cli`,
So that every command I can run from Telegram has a local console counterpart.

**Acceptance Criteria:**

**Given** the console-cli container is running
**When** I run `docker compose exec console oh-my-bmad-cli --help`
**Then** the command exits 0 and prints a help menu listing all subcommands.

**And Given** the domain logic lives in `services/telegram-gateway/domain/commands.py`
**When** `services/console-cli/` is implemented
**Then** it imports and reuses that logic rather than duplicating it — the domain layer is shared via the service's `adapters/` seam, not by cross-service import (staying within the import-graph rules via an intermediate package if needed).

*Cites: FR12.*

### Story 4.2: `task`, `status`, `logs` commands

As the operator at the Mac,
I want `oh-my-bmad-cli task`, `status`, `logs` to perform the same actions as their Telegram counterparts,
So that desk-side workflows don't require switching to my phone.

**Acceptance Criteria:**

**Given** the registry-api is reachable on the docker network
**When** I run `oh-my-bmad-cli task "add idempotency middleware" --repo gateway`
**Then** the CLI calls `POST /v1/tasks` and prints `Task t-... created. Planning.`.

**And When** I run `oh-my-bmad-cli status t-0001`
**Then** it prints the same one-message state reconstitution the Telegram `/status` renders.

**And When** I run `oh-my-bmad-cli logs t-0001`
**Then** it prints the LLM-digest output.

*Cites: FR12, FR4, FR5.*

### Story 4.3: `approve`, `reject`, `stop`, `retry`, `ping`, `agent` commands

As the operator at the Mac,
I want the operator-decision commands + health/ownership commands available from the CLI,
So that full surface parity holds.

**Acceptance Criteria:**

**Given** a pending approval exists
**When** I run `oh-my-bmad-cli approve t-0001`
**Then** the CLI calls `POST /v1/tasks/t-0001/decisions {action:approve}` and prints the confirmation.

**And Similarly** `reject <id> <reason>`, `stop <id>`, `retry <id> --hint "..."` produce the right side-effects.

**And When** I run `oh-my-bmad-cli ping`
**Then** the health line is printed within 2 s.

**And When** I run `oh-my-bmad-cli agent t-0001`
**Then** the runtime/provider name + worker/session ids are printed.

*Cites: FR12, FR7, FR17, FR17a.*

### Story 4.4: `events --follow` live tail

As the operator debugging locally,
I want `oh-my-bmad-cli events <task-id> --follow` to stream the raw typed event stream to my console in real time,
So that I can inspect platform behavior without leaving the terminal.

**Acceptance Criteria:**

**Given** a task is active
**When** I run `oh-my-bmad-cli events t-0001 --follow`
**Then** each new event for that task prints as a single JSON line within 1 s of emission; pressing Ctrl+C exits cleanly.

*Cites: FR6, FR12.*

### Story 4.5: Error rendering (RFC 7807 → text)

As the operator,
I want RFC 7807 error responses rendered as readable console text with exit codes,
So that scripting against the CLI is possible.

**Acceptance Criteria:**

**Given** the API returns `422` with a RFC 7807 body
**When** the CLI receives it
**Then** stdout is empty, stderr prints `Error: <title> — <detail>` with per-extension context lines, and the CLI exits with code `2` (validation) / `4` (not found) / `5` (conflict) per a documented mapping.

*Cites: FR12 (parity of error handling with Telegram).*

### Story 4.6: `docker compose exec console` wrapper + host-side symlink

As the operator,
I want a `just cli <args...>` recipe that wraps `docker compose exec console oh-my-bmad-cli <args...>` and a documented host-side symlink or shell function,
So that I can type `bm task "…"` from my Mac terminal without typing the full docker command.

**Acceptance Criteria:**

**Given** the stack is running
**When** I run `just cli task "do something"`
**Then** it is equivalent to `docker compose exec console oh-my-bmad-cli task "do something"`.

**And Given** `docs/deployment/macos.md` documents a shell function or alias
**When** the operator follows those instructions
**Then** they can alias `bm` (or similar) to the `just cli` recipe for terse desk-side use.

*Cites: FR12.*

---

## Epic 5: Autonomous Task Execution

**Epic Goal:** Operator's submitted task is planned, executed, tested, committed by the Claude Code worker end-to-end; completion summary + PR draft arrive. S-1 (cold) and S-2 (mid-flight) separability tests pass in CI.

**Story ordering note (per Final Validation):** `task-registry` + `session-registry` MCP servers (originally numbered 5.8 and 5.9) are implemented **first** in this epic — before the worker-wrapper scaffold — because Story 5.3 (worker-wrapper) wires clients to them. Stories 5.8 and 5.9 remain at their original numbers in the document below for traceability; but the **implementation order** is 5.8 → 5.9 → 5.1 → 5.2 → 5.3 → 5.4 → ... Treat the numbered order as a documentation index, not a strict sequencing. All other within-epic dependencies are linear by number.

### Story 5.1: Worker-wrapper service scaffold + MCP client integration

As the platform,
I want `services/worker-wrapper/` scaffolded with MCP clients to `task-registry`, `session-registry`, and `clawhip-bridge`,
So that the worker has a wired-up surface to read task detail, emit events, and register sessions.

**Acceptance Criteria:**

**Given** Stories 5.8 and 5.9 have landed (MCP servers available) and Story 2.8 has landed (clawhip-bridge available)
**When** the worker-wrapper service starts
**Then** it connects to all three MCP servers over stdio, each handshake succeeds, and the worker can call at least one resource/tool on each (verified with a connectivity test).

*Cites: FR32, FR33 (enabling). **Implementation prerequisite:** Stories 5.8 and 5.9.*

### Story 5.2: Session lifecycle emission (started / heartbeat / finished)

As the platform,
I want the worker to register itself with the session registry on startup and emit `session.started`, periodic `session.heartbeat`, and `session.finished` typed events,
So that session state is observable and heartbeat timeouts trigger failure detection (FR24a).

**Acceptance Criteria:**

**Given** the worker starts
**When** session init completes
**Then** a `session.started` event is emitted with `{session_id, worker_id, task_id?}`.

**And Given** the worker is running
**When** 30 s pass
**Then** a `session.heartbeat` event is emitted within the following 5 s.

**And Given** the worker shuts down gracefully
**Then** `session.finished` is emitted before the process exits.

*Cites: FR32, NFR-R5.*

### Story 5.3: Exclusive worktree lock acquisition + release

As the platform,
I want the worker to acquire an exclusive lock on its assigned worktree at session start and release it on `session.finished` / `task.stopped`,
So that two workers can never mutate the same worktree concurrently (FR27, NFR-SC3).

**Acceptance Criteria:**

**Given** worktree `<path>` has no lock
**When** a worker acquires the lock
**Then** the lock file (`<path>/.oh-my-bmad.lock`) contains the session id + acquired-at ts and a second worker attempting the same lock receives `WorktreeLockHeld`.

**And Given** a task enters `blocked`
**When** the worker transitions
**Then** the lock is **retained** (not released) per FR27 — operator `/stop` or `/retry` is required to release.

**And Given** the worker exits ungracefully
**When** a new worker starts against the same worktree after the session is marked failed
**Then** the stale lock is cleanable via a documented recovery procedure (not silently stolen).

*Cites: FR27, FR32, NFR-SC3.*

### Story 5.4: Claude Code CLI subprocess supervision + event extraction

As the platform,
I want the worker to spawn `claude-code` as a subprocess, feed it the task + context, and extract meaningful actions via the Claude Code SDK (not stdout parsing) to emit as typed events,
So that Claude Code's execution is integrated without violating NFR-O1.

**Acceptance Criteria:**

**Given** the worker starts a task
**When** it invokes `claude-code` via the SDK
**Then** every observed action (`file.edited`, `test.run`, `commit.created`, etc.) is emitted as a typed event via `clawhip-bridge` — no `subprocess.check_output().decode()` usage anywhere in the call path.

**And When** `scripts/check_imports.py` or the custom `ruff no-stdout-parse` rule runs against the worker-wrapper
**Then** they exit 0.

*Cites: FR3, FR18a, FR18b, NFR-O1.*

### Story 5.5: `agent.reasoning.*` breadcrumb emission with sanitizer integration

As the operator,
I want the worker to emit `agent.reasoning.*` typed events (planning rationale, retry justifications, rejected hypotheses, tool-call arguments) passed through the secret sanitizer,
So that `/logs` and `/status` can surface *why* the agent did what it did, not just what it did (FR17b, NFR-O6).

**Acceptance Criteria:**

**Given** Claude Code produces a planning rationale
**When** the worker emits `agent.reasoning.plan_drafted`
**Then** the payload's text fields pass through the sanitizer first; if sanitization cannot safely redact (e.g., secret patterns detected), the payload is replaced with `{reason: "sensitive_content_suppressed"}` and the event is still emitted (not dropped).

**And When** `/status t-0001` is called
**Then** the response includes the last `agent.reasoning.*` breadcrumb in human-readable form.

*Cites: FR17b, NFR-O6.*

### Story 5.6: Atomic file-edit primitive

As the worker,
I want a `packages/events` or `services/worker-wrapper/domain/atomic_edit.py` primitive that performs file edits atomically (write to tmp file, fsync, rename),
So that a mid-write host interruption leaves the filesystem in a consistent state (FR30).

**Acceptance Criteria:**

**Given** the primitive performs `atomic_write(target_path, content)`
**When** the write is interrupted mid-syscall (verified by the write-interrupt harness from Story 2.12)
**Then** `target_path` post-restart is either the full old content or the full new content — never partial.

*Cites: FR30, NFR-R2.*

### Story 5.7: GitHub API adapter (PR draft + retries)

As the platform,
I want a GitHub adapter in `services/worker-wrapper/adapters/github_client.py` that creates PR drafts, adds commits/branches, and uses `tenacity` 3× exponential-backoff retries,
So that PR creation is reliable on flaky networks without infinite retry loops.

**Acceptance Criteria:**

**Given** `GITHUB_TOKEN` is set in env
**When** the adapter creates a PR draft
**Then** it calls `POST /repos/{owner}/{repo}/pulls` with `draft: true` and correct metadata; on 5xx/timeout, retries 3× with exp-backoff + jitter; total timeout per call ≤ 10 s.

**And Given** `scripts/check_imports.py` runs
**Then** no `requests` sync-client import exists; only `aiohttp`-style async.

*Cites: FR10.*

### Story 5.8: `task-registry` MCP server (read surfaces)

As orchestrator and worker,
I want `mcp-servers/task-registry/` exposing `task list`, `task detail`, `approval queue`, `blockers` as read-only resources and `task.add_note`, `task.attach_artifact`, `task.emit_event` as bounded-write tools,
So that agents have a structured read-only view of task state plus a narrow write surface scoped by capability tier.

**Acceptance Criteria:**

**Given** the MCP server is running
**When** a client reads the `task/detail/{id}` resource
**Then** the response matches the registry's materialized state.

**And When** a Tier-0 client attempts to call `task.attach_artifact`
**Then** the capability check rejects with `CapabilityDenied`.

*Cites: FR33 (read surface), FR37.*

### Story 5.9: `session-registry` MCP server (read + bounded-write)

As the worker,
I want `mcp-servers/session-registry/` exposing `active sessions`, `worker metadata`, `heartbeats` as resources and `session.heartbeat`, `session.register`, `session.close` as tools,
So that the worker lifecycle has a structured surface.

**Acceptance Criteria:**

**Given** the MCP server is running
**When** a worker calls `session.register(...)`
**Then** a `session.started` typed event flows through `clawhip-bridge` to the event log.

*Cites: FR32 (emission surface).*

### Story 5.10: Orchestrator-adapter: OMC subprocess supervision

As the platform,
I want `services/orchestrator-adapter/` to supervise the vendored OMC subprocess via `adapters/omc_runner.py` and translate between OMC's task model and the platform's typed events via `domain/task_dispatch.py`,
So that OMC's orchestration logic drives platform tasks without leaking OMC specifics into registry or worker code.

**Acceptance Criteria:**

**Given** OMC is vendored at `upstream/omc/` with a known contract
**When** the orchestrator-adapter receives a `task.created` event
**Then** it drives OMC to produce a plan and emits `task.planning.started` → `task.plan.ready` typed events.

**And Given** `scripts/check_imports.py` runs
**Then** no `services/` file outside `orchestrator-adapter/` imports OMC directly.

*Cites: FR31, NFR-M1.*

### Story 5.11: Task plan emission (FR2)

As the operator,
I want the platform to produce a stepwise plan before execution and emit `task.plan.ready` with the plan summary,
So that I see what the agent intends to do before it acts.

**Acceptance Criteria:**

**Given** a new task is in `planning`
**When** OMC completes plan generation
**Then** `task.plan.ready` is emitted with `{plan: [{step, description}...], estimated_steps: N}`.

**And When** the Telegram sink renders the outbound
**Then** the operator sees `Plan ready, <N> steps: 1) …, 2) …, 3) …, 4) …`.

*Cites: FR2.*

### Story 5.12: Task execution driver (FR3 + FR31)

As the platform,
I want the orchestrator-adapter → worker-wrapper handoff to drive execution from `task.execution.requested` through per-step events to `task.completed`,
So that a planned task actually runs end-to-end.

**Acceptance Criteria:**

**Given** a plan is ready
**When** the operator's implicit consent (plan auto-approval in Phase 1 for non-Tier-3 actions) allows execution to start
**Then** the orchestrator emits `task.execution.requested` → worker subscribes → per-step events flow → `task.completed` emits with final state.

*Cites: FR3, FR31.*

### Story 5.13: Completion summary payload emission (FR9)

As the operator,
I want the `task.completed` event's payload to include structured file count, line count, test count, CI state, blockers-encountered counters,
So that the telegram-sink's completion template has all fields it needs.

**Acceptance Criteria:**

**Given** a task finishes cleanly
**When** `task.completed` is emitted
**Then** the payload contains `{files_changed: N, lines_added: X, lines_removed: Y, tests_added: T, ci_state: "green"|"red"|"unknown", blockers_count: B, pr_url?: "..."}`.

*Cites: FR9.*

### Story 5.14: PR draft auto-creation on green tests (FR10)

As the operator,
I want the worker to call `github_client.create_pr_draft(...)` when the task reaches green-tests state and completes a repo-mutating flow,
So that the PR is waiting for me (and approval is gated for `git push` via E6).

**Acceptance Criteria:**

**Given** tests pass and the task is ready to push
**When** (and only when) `git push` approval has been granted
**Then** the worker creates a PR draft via the GitHub adapter and attaches the PR URL to the completion event's payload.

*Cites: FR10 (coupled with E6 FR38).*

### Story 5.15: Per-task budget enforcement (FR44 coupling)

As the platform,
I want the worker to track per-task compute/token budget and emit `task.budget_exceeded` if the configured ceiling is reached,
So that cost-loop bugs cannot run the operator's API bill into the ground.

**Acceptance Criteria:**

**Given** the per-task budget is 50,000 tokens
**When** the worker's cumulative token count crosses the ceiling
**Then** `task.budget_exceeded` is emitted within 5 s of the crossing and execution halts pending operator extension-approval.

**And When** the final token count is measured
**Then** it is ≤ 1.1× the ceiling (no more than 10% over).

*Cites: FR44, NFR-P5.*

### Story 5.16: S-1 separability test — cold worker swap

As a CI pipeline,
I want `tests/separability/test_s1_cold_worker_swap.py` to replace the Claude Code worker with a scripted-stub worker via env-var override and prove orchestrator + registry code is unchanged,
So that FR34 / NFR-M4 is verified as a fact, not a claim.

**Acceptance Criteria:**

**Given** `tests/fixtures/scripted_worker_stub.py` emits canned lifecycle events
**When** `docker compose` is started with `WORKER_IMAGE=scripted-worker-stub:latest`
**Then** a canned task runs end-to-end to `task.completed` with zero source changes to `services/registry-*`, `mcp-servers/*`, `services/orchestrator-adapter/` (verified by a git-diff assertion scoped to those paths).

*Cites: FR34, NFR-M4.*

### Story 5.17a (HIGH-RISK): Resume-after-approval state machine (FSM + unit tests)

As the worker,
I want `services/worker-wrapper/domain/lifecycle.py` defining a deterministic FSM with states `running` → `awaiting_approval` → `paused` → `resumed` → `completed`/`failed` and transitions driven exclusively by input events,
So that the state machine has an isolated, unit-tested core before it's coupled to cross-restart + idempotency concerns (pair-reviewed HIGH-RISK file).

**Acceptance Criteria:**

**Given** the FSM receives input event sequence `[running, task.awaiting_approval, approval.granted]`
**When** transitions are applied
**Then** the final state is `resumed` and the transition log is deterministic (same input = same state + same transition trace every time).

**And Given** invalid transitions (e.g., `approval.granted` from `completed`)
**When** they are fed into the FSM
**Then** the FSM rejects with a typed `InvalidTransition` exception and the rejection is audited.

**And Given** the unit test suite
**When** CI runs
**Then** every state × input-event combination has explicit coverage and transition-table coverage is 100%.

*Cites: FR36. HIGH-RISK — pair review required; this story lands the core FSM in isolation.*

### Story 5.17b (HIGH-RISK): Cross-restart approval handling + exactly-once guarantees + `test_resume_after_approval.py`

As the operator,
I want the FSM from 5.17a plugged into the idempotency cache (FR28) + reattach path (FR29) + atomic-edit primitive (FR30) + GitHub-adapter idempotency passthrough, and an integration test asserting the combined path handles (a) restart-during-awaiting-approval (approval arrives before or after restart) and (b) retry-storm-on-`/approve` (10 rapid approvals processed exactly once),
So that Journey 2 + Journey 3 stand under real failure conditions.

**Acceptance Criteria:**

**Given** a task in `awaiting_approval` and the worker restarts
**When** `approval.granted` arrives either before or after the restart
**Then** the task resumes and the gated action (`git push` + PR creation) executes exactly once — verified by absence of duplicate events in the log.

**And Given** 10 `/approve` decisions arrive within 1 s (retry storm)
**When** all are processed
**Then** exactly one `approval.granted` audit event is recorded and exactly one gated action executes.

**And Given** `tests/integration/test_resume_after_approval.py` covers both cases
**When** CI runs on merge
**Then** the test passes green.

*Cites: FR28, FR29, FR30, FR36. HIGH-RISK — pair review required.*

### Story 5.17c: S-2 separability test — mid-flight worker swap

As a CI pipeline,
I want `tests/separability/test_s2_midflight_swap.py` that kills the real worker mid-task and hands off to the scripted-stub worker via env-var override,
So that FR34 / NFR-M4 is proved under motion — not just cold-swap interface compatibility (5.16).

**Acceptance Criteria:**

**Given** a real worker is processing a task and has emitted some progress events
**When** the test kills the worker via SIGKILL and restarts compose with `WORKER_IMAGE=scripted-worker-stub:latest`
**Then** the stub worker picks up the session by id, resumes from the last committed event, and drives the task to `task.completed` with zero state corruption and zero event loss.

**And When** the test runs in CI
**Then** it passes green and is listed in the MVP Ship-Blocker Checklist.

*Cites: FR34, NFR-M4, NFR-R2.*

### Story 5.18: Journey 1 integration test (MVP gate — two-phase)

As a CI pipeline,
I want `tests/integration/test_journey_1_overnight.py` that exercises Journey 1 end-to-end with a real Claude Code worker against a test repo,
So that the MVP gate is continuously verified.

**Acceptance Criteria (Phase 1 of this story — lands at end of Epic 5):**

**Given** Epic 5 is complete and Epic 6 is not yet complete
**When** the test runs with an auto-approval stub fixture replacing the Tier-3 gate
**Then** plan emitted → execution runs → tests go green → auto-approval stub grants → push + PR draft emitted → `task.completed` summary recorded.

**Acceptance Criteria (Phase 2 — re-enabled when Epic 6 completes):**

**Given** Epic 6 is complete
**When** the test runs with the real approval flow (operator-decision endpoint + tier enforcement + license scan)
**Then** the same end-to-end flow succeeds using the real approval mechanism instead of the stub.

**And When** CI runs on merge after both phases are in place
**Then** this test passes green and is the MVP ship-blocker gate for Journey 1.

*Cites: FR1, FR2, FR3, FR7, FR9, FR10, FR18a, FR31, NFR-R6. **Cross-epic coupling note:** full Journey 1 requires E6's approval flow; Phase 1 of this test (auto-approval stub) lets Story 5.18 complete within E5's boundary.*

---

## Epic 6: Approval & Policy Gate

**Epic Goal:** Operator's `git push` is gated; license scan runs pre-push; approvals and overrides emit audit events; per-task budget ceilings enforce; capability tiers protect the platform. After this epic, the platform is safe-for-real-tasks.

### Story 6.1: Capability-tier enforcement helpers

As the platform,
I want `packages/` to provide tier-classification and tier-check helpers,
So that every MCP handler and HTTP endpoint can enforce Tier 0–3 access uniformly.

**Acceptance Criteria:**

**Given** `packages/secret_hygiene/` (or a new `packages/capabilities/`) exports `Tier` enum + `check_tier(action, caller, required_tier)` helper
**When** a handler calls `check_tier(action="git_push", caller=worker_ctx, required_tier=Tier.THREE)`
**Then** the helper returns `CapabilityOk` only if an approval event exists for that action + task; otherwise raises `CapabilityDenied`.

*Cites: FR37.*

### Story 6.2: Tier enforcement at MCP handler boundaries

As the platform,
I want every MCP tool handler to call the tier-check helper before executing a mutating action,
So that Tier-3 actions cannot be triggered through the MCP surface without an approval event.

**Acceptance Criteria:**

**Given** a client attempts to call a Tier-2 tool as a Tier-0 caller
**When** the handler runs
**Then** it returns `CapabilityDenied` before any side-effect runs.

**And When** a negative test attempts Tier-3 action without a matching approval event
**Then** the attempt is logged as `tier3.action_attempted` with `{accepted: false, reason: "no_matching_approval"}` and the action is not performed.

*Cites: FR37, FR38, NFR-S6.*

### Story 6.3: Tier enforcement in HTTP API middleware

As the platform,
I want a FastAPI middleware that enforces tier on state-mutating endpoints where relevant (e.g., `/v1/tasks/{id}/decisions` requires operator-authenticated caller),
So that the HTTP ingress respects the same tier model as MCP.

**Acceptance Criteria:**

**Given** an incoming request to `/v1/tasks/{id}/decisions`
**When** the middleware processes it
**Then** it verifies caller identity + attaches tier context to `request.state` before handler dispatch.

*Cites: FR37.*

### Story 6.4: `POST /v1/tasks/{id}/decisions` handler + payload shapes

As the operator,
I want `POST /v1/tasks/{id}/decisions` to accept `{action: approve|reject|stop|retry, reason?, hint?, override?}` payloads,
So that Telegram and Console can route all operator decisions through a single endpoint.

**Acceptance Criteria:**

**Given** a task is `awaiting_approval`
**When** the handler receives `{"action": "approve"}`
**Then** `approval.granted` is emitted and the task transitions out of approval-wait.

**And When** the handler receives `{"action": "reject", "reason": "..."}`
**Then** `approval.rejected` is emitted.

**And When** the handler receives `{"action": "retry", "hint": "..."}`
**Then** `task.retry_requested` is emitted with the hint in the payload.

**And When** the handler receives `{"action": "approve", "override": "license"}`
**Then** an `approval.granted` event + a `tier3.license_override` audit event are both emitted.

*Cites: FR7, FR41, NFR-S3.*

### Story 6.5: Approval audit events

As the operator,
I want every operator decision (`approve`, `reject`, `stop`, `retry`) emitted as a typed audit event with actor, scope, timestamp, reason,
So that NFR-S3 (auditability) is enforced on the control plane.

**Acceptance Criteria:**

**Given** the operator sends `/approve t-0001`
**When** the decision is processed
**Then** `approval.granted` is emitted with `{task_id, actor: {kind:"operator", id:"..."}, decided_at, request_id}`.

**And Similarly** `approval.rejected` / `task.stop_requested` / `task.retry_requested` events include equivalent actor/audit fields.

*Cites: FR7, NFR-S3.*

### Story 6.6: `tier3.action_attempted` + `tier3.action_performed` audit events

As the platform,
I want every Tier-3 action attempt and performance emitted as typed audit events,
So that an audit reviewer can reconstruct every sensitive operation.

**Acceptance Criteria:**

**Given** the worker attempts `git push`
**When** it requests the gated action
**Then** `tier3.action_attempted` is emitted with `{task_id, action: "git_push", accepted: false, reason: "awaiting_approval"}`.

**And After** operator approval
**When** the worker actually performs the `git push`
**Then** `tier3.action_performed` is emitted with `{task_id, action: "git_push", performed_at, actor, approval_event_id}`.

*Cites: FR38, NFR-S3.*

### Story 6.7: Worker approval-wait state (FR36 coupling with E5 lifecycle)

As the worker,
I want to emit `task.awaiting_approval` when a Tier-3 action is reached, hold my worktree lock, sleep on a conditional wait, and resume on `approval.granted` or terminate on `approval.rejected`,
So that FR36 is coded up in the lifecycle state machine (couples with Story 5.17 HIGH-RISK).

**Acceptance Criteria:**

**Given** the worker reaches a `git push` step
**When** pre-push checks pass
**Then** the worker emits `task.awaiting_approval` with action/risk/diff summary context, retains the worktree lock, and awaits an approval-class event.

**And Given** an `approval.granted` event arrives (whether before or after a worker restart — covered by Story 5.17 S-2 test)
**When** the worker observes it
**Then** the push executes exactly once and emits `tier3.action_performed` + the post-push lifecycle events.

*Cites: FR36 (coupled with FR28 idempotency + FR29 reattach).*

### Story 6.8: Pre-commit validation hook (FR39)

As the platform,
I want `packages/secret_hygiene/` to provide a pre-commit hook that blocks (a) changes to `.env*`/`secrets/`/`*.pem`/`*.key`/`*.credentials*`, (b) worktree-boundary violations (writes outside the assigned worktree), (c) commit-message injection patterns (null bytes, command substitution),
So that Tier-3 stays reserved for truly irreversible operations and cheap-to-catch violations are caught at commit time.

**Acceptance Criteria:**

**Given** an agent attempts to commit `.env`
**When** the pre-commit hook runs
**Then** the commit is blocked with `Refusing to commit sensitive path: .env`.

**And Given** an agent attempts to commit a file outside the assigned worktree
**When** the hook runs
**Then** it blocks with a clear message naming the violated boundary.

*Cites: FR39.*

### Story 6.9: License-scan integration (scancode-toolkit / ORT)

As the platform,
I want `packages/secret_hygiene/license_scan.py` wrapping `scancode-toolkit` (lightweight mode) or `ORT` and invoked on every agent-generated commit pre-push,
So that license-incompatible snippets (e.g., GPL into permissive-licensed projects) are detected before they ship.

**Acceptance Criteria:**

**Given** a diff introduces a file containing a GPL-licensed snippet
**When** the license scan runs
**Then** it returns a structured finding `{file, license_detected, incompatible_with_repo_license, reason_code}`.

**And Given** a diff introduces only permissively-licensed content
**When** the scan runs
**Then** it returns no findings and execution proceeds.

*Cites: FR40, NFR-S8.*

### Story 6.10: `task.license_flagged` event + approval-gate block + `/approve --override license`

As the operator,
I want license-incompatibility findings to emit `task.license_flagged`, block the approval gate with a specific reason code, and allow `/approve --override license` (which emits an audit event) to proceed with deliberate override,
So that the operator is never silently bypassed or unable to override in a real emergency.

**Acceptance Criteria:**

**Given** license-scan finds an incompatibility
**When** the approval gate processes the pending push
**Then** `task.license_flagged` is emitted with `{reason_code, file_list, detected_licenses}`; the approval message to the operator includes the license-flag block; default `/approve` is refused with `approval_blocked_by: license_flag`.

**And Given** the operator sends `/approve t-0001 --override license`
**When** the decision is processed
**Then** `approval.granted` + `tier3.license_override` audit events are both emitted; the push proceeds.

*Cites: FR40, FR41, NFR-S8.*

### Story 6.11: Budget-exceeded enforcement + event (FR44 handling)

As the operator,
I want `task.budget_exceeded` events to halt autonomous work and require operator approval to extend the budget,
So that cost-loop bugs don't run up unbounded bills.

**Acceptance Criteria:**

**Given** a task emits `task.budget_exceeded`
**When** the event is materialized
**Then** the task transitions to `blocked` with `blocker_reason: "budget_exceeded"` and the telegram-sink delivers a blocker message to the operator.

**And Given** the operator sends `/approve t-0001 --override budget`
**When** the decision is processed
**Then** the budget ceiling is raised per a documented policy (e.g., ×2 or +50%), an audit event fires, and the task resumes.

*Cites: FR44, NFR-P5.*

### Story 6.12: `test_decision_interleaving.py` Hypothesis property test

As a CI pipeline,
I want a Hypothesis-based property test in `tests/integration/test_decision_interleaving.py` that generates randomized interleavings of `/approve`, `/retry`, `/stop` against a running task and asserts the worker lifecycle converges on a single consistent outcome regardless of arrival order,
So that the class of decision-race bugs doesn't lurk through Phase 1.

**Acceptance Criteria:**

**Given** the test runs 1000 randomized interleavings
**When** all interleavings complete
**Then** in every run the task's final state is deterministic given the arrival-set (not the order), no duplicate gated actions are performed, and no events are lost.

*Cites: FR7, FR28 (race-safety under retry storms), NFR-R4.*

### Story 6.13: `test_license_scan.py`

As a CI pipeline,
I want an integration test that seeds a repo with a GPL-licensed file, runs an autonomous task that would commit that file, and asserts the license-scan blocks the approval gate with the correct reason code,
So that FR40 is continuously verified.

**Acceptance Criteria:**

**Given** a test fixture repo with a known GPL snippet staged for commit
**When** the test runs the Phase 1 autonomous-task flow
**Then** `task.license_flagged` is emitted, the approval message includes the license-block reason, and the test harness's default `/approve` is refused.

**And Given** the harness then sends `/approve --override license`
**When** the decision is processed
**Then** the push proceeds and both the license-flag event and the override event are recorded.

*Cites: FR40, FR41.*

### Story 6.14: Tier-3 negative test

As a CI pipeline,
I want a negative test that attempts a Tier-3 action without a matching approval event and asserts it fails with `permission_denied`,
So that NFR-S6 is regression-proof.

**Acceptance Criteria:**

**Given** no approval event exists for a task
**When** the test invokes the Tier-3 action path (via test seam)
**Then** the attempt is rejected with `CapabilityDenied` / `permission_denied`, `tier3.action_attempted` fires with `accepted: false`, and no side-effect occurs.

*Cites: FR38, NFR-S6.*

---

## Epic 7: Reconnaissance & Recovery UX

**Epic Goal:** Operator returning to a blocked task hours later gets full state in one message, LLM-digest logs, can `/retry` with hint; restart-recovered overnight tasks get a proactive morning summary; atomic file-edit recovery is proven. Delivers Journey 3 + Journey 6 fully.

### Story 7.1: `GET /v1/tasks/{id}` reconstituted-state handler (FR4)

As the operator,
I want the registry-api's `GET /v1/tasks/{id}` endpoint to return a single response with state, current step (x of y), last event, last agent action, worktree lock state, and enumerated available commands,
So that `/status` calls on any surface reconstitute full context without scrollback.

**Acceptance Criteria:**

**Given** a task is in `blocked`
**When** `GET /v1/tasks/{id}` is called
**Then** the JSON response contains `{state, state_since, current_step, total_steps, last_event: {type, ts, summary}, last_agent_action, worktree_lock: {held, by_session_id?, acquired_at?}, available_commands: [...]}`.

**And Given** the response is rendered by Telegram `/status`
**When** the operator reads it
**Then** it fits in a single Telegram message (≤ 4096 chars) without truncation.

*Cites: FR4.*

### Story 7.2: Telegram `/status` business logic

As the operator,
I want Telegram `/status <task-id>` to call `GET /v1/tasks/{id}` and render the response via the message-template function,
So that the Telegram surface story (3.14) has real business logic backing it.

**Acceptance Criteria:**

**Given** Story 3.14 delivered the Telegram surface
**When** this story completes
**Then** `/status` returns substantive state content (not a placeholder) and `test_journey_6_stale_blocker.py` exercises this flow.

*Cites: FR4.*

### Story 7.3: `GET /v1/tasks/{id}/logs/digest` LLM adapter (FR5)

As the operator,
I want a `services/registry-api/adapters/llm_digest.py` that calls Anthropic API with a summarization prompt over the task's recent events and returns a human-readable digest,
So that `/logs` surfaces actionable context rather than raw event dumps.

**Acceptance Criteria:**

**Given** a task has >20 events
**When** `GET /v1/tasks/{id}/logs/digest` is called
**Then** the handler (a) pulls recent events from the event log, (b) passes them to the digest adapter with a bounded prompt, (c) returns a ≤20-line summary naming key transitions, blockers, and the agent's last decision.

**And Given** the adapter's prompt + context exceed the model's token budget
**When** the handler runs
**Then** it degrades gracefully (truncates older events, adds a `"truncated": true` marker) rather than failing.

*Cites: FR5.*

### Story 7.4: Telegram `/logs` business logic

As the operator,
I want Telegram `/logs <task-id>` to call `GET /v1/tasks/{id}/logs/digest` and return the digest text,
So that the Telegram surface story (3.15) has real business logic backing it.

**Acceptance Criteria:**

**Given** Story 3.15 delivered the Telegram surface
**When** this story completes
**Then** `/logs` returns LLM-digest content and `test_journey_6_stale_blocker.py` exercises the happy path.

*Cites: FR5.*

### Story 7.5: `GET /v1/tasks/{id}/events` raw event tail

As the operator debugging,
I want `GET /v1/tasks/{id}/events?since=<ts>&limit=N` to return raw typed events,
So that `oh-my-bmad-cli events --follow` and debugging workflows have structured data access.

**Acceptance Criteria:**

**Given** a task has events recorded
**When** `GET /v1/tasks/t-0001/events?since=2026-04-22T00:00:00Z&limit=50` is called
**Then** the response is a JSON array of envelope objects matching the filter, ordered by `emitted_at`.

*Cites: FR6.*

### Story 7.6: `/retry` with hint-injection into orchestrator context

As the operator,
I want `/retry t-0001 hint="..."` to resume a blocked task with my clarifying hint injected into the orchestrator's next planning pass,
So that I can course-correct without re-submitting a full task.

**Acceptance Criteria:**

**Given** a task is in `blocked`
**When** `POST /v1/tasks/t-0001/decisions {action:retry, hint:"rate limit must be per-user"}` is processed
**Then** `task.retry_requested` is emitted with the hint in payload, the orchestrator's next plan input includes the hint as a first-class context field, and the task transitions back to `planning`.

**And When** the next `task.plan.ready` fires
**Then** the new plan reflects the hint (verified by the integration test, which asserts a hint-relevant fragment in the plan text).

*Cites: FR7, FR5, FR6 (reconnaissance coupling).*

### Story 7.7: Worktree-lock persistence through blocker window (FR27)

As the operator,
I want the worktree lock to be held for the entire duration of a `blocked` state — released only on `/stop` or `/retry`,
So that stopping by returns hours later the task is still recoverable.

**Acceptance Criteria:**

**Given** a task enters `blocked` at time T
**When** the operator returns at T + 6 hours
**Then** the worktree lock is still held (verified via `/status` lock-state field) and `/retry` can resume without lock-contention errors.

**And Given** the operator sends `/stop`
**When** the decision is processed
**Then** the lock is released as part of the `task.stopped` event chain.

*Cites: FR27.*

### Story 7.8: Proactive self-recovered morning summary

As the operator,
I want `clawhip-daemon` to emit a proactive `🛠️ Self-recovered from host restart at <ts>...` Telegram message whenever an overnight task experienced a host restart (detected via `session.reconnecting` + `task.execution.resumed` pair between midnight and the completion summary),
So that hidden resilience becomes visible trust.

**Acceptance Criteria:**

**Given** a task completes in the morning and its event log contains a `session.reconnecting` + `task.execution.resumed` pair timestamped overnight
**When** the completion summary is delivered
**Then** a second compact self-recovered message is delivered immediately after, with the restart timestamp, events-replayed count, and replay duration.

*Cites: FR16.*

### Story 7.9: Journey 3 recovery integration test (MVP gate)

As a CI pipeline,
I want `tests/integration/test_journey_3_recovery.py` that launches a task, kills the host mid-execution, restarts, and asserts full resumption with both the execution summary and the self-recovered summary landing,
So that Journey 3 acceptance is continuously verified.

**Acceptance Criteria:**

**Given** a task is mid-execution
**When** the test triggers `docker compose stop --timeout 1` and then `up -d`
**Then** the task resumes from the last committed event, reaches `task.completed`, and both the completion summary + the self-recovered summary are emitted via the Telegram sink fake.

**And When** CI runs on merge
**Then** this test passes green as part of the MVP ship checklist.

*Cites: FR16, FR24, FR29, NFR-R1, NFR-R2.*

### Story 7.10: Journey 6 stale-blocker integration test

As a CI pipeline,
I want `tests/integration/test_journey_6_stale_blocker.py` that puts a task into `blocked` via a deliberately-failing test, leaves it for a simulated 6-hour window (mocked clock), and verifies the operator can `/status` → `/logs` → `/retry hint="…"` → task resumes to completion with the hint honored,
So that Journey 6 acceptance is continuously verified.

**Acceptance Criteria:**

**Given** the test harness forces a blocker on a running task
**When** the test simulates a time-skip (via the injectable clock)
**Then** `/status` still returns the lock-held + available-commands response, `/logs` returns a coherent digest, `/retry` with a hint resumes the task, and the final plan reflects the hint.

**And When** CI runs on merge
**Then** this test passes green.

*Cites: FR4, FR5, FR7, FR27.*

---

### Epic 7.5: Tech-Debt Sweep (Epic 7 Retrospective)

*Tech debt, bug fixes, and process improvements accumulated across Epics 3–7 but deferred to keep story scope clean. Completing these prevents debt growth and resolves the 3-retro integration test harness deferral.*

**FRs covered:** N/A (maintenance)
**NFRs:** N/A
**Additional:** Rate-limiter allowlist layering fix; session bulk close + compound index; rate-limiter contract documentation + dynamic Retry-After; configurable Anthropic model + digest hardening; worktree lock TOCTOU fix; events endpoint truncation + trace_id; integration test harness decision; cross-renderer validator consistency.

*Standalone value: addresses all HIGH/MEDIUM deferred items from 7 stories; resolves the integration test harness duplication flagged in 3 consecutive retros; hardens the rate-limiter and session lifecycle for production load.*

---

## MVP Ship-Blocker Checklist

The tests and stories below must be green/complete before Phase 1 can be claimed as shipped. Journey gates are user-observable; separability + harness tests are architectural-claim proofs; docs are maintenance prerequisites.

### Journey gates (user-observable)

- **Story 5.18** — `tests/integration/test_journey_1_overnight.py` — Journey 1 Overnight PR runs end-to-end with a real Claude Code worker.
- **Story 7.9** — `tests/integration/test_journey_3_recovery.py` — Journey 3 Restart Recovery survives kill-mid-task + delivers self-recovered summary.

### Separability claim proofs

- **Story 5.16** — `tests/separability/test_s1_cold_worker_swap.py` — FR34 / NFR-M4 cold swap.
- **Story 5.17c** — `tests/separability/test_s2_midflight_swap.py` — FR34 / NFR-M4 mid-flight swap.
- **Story 2.15** — `tests/separability/test_s3_orchestrator_swap.py` — FR35 / NFR-M5 orchestrator pass-through.

### Continuous-verification harnesses

- **Story 2.11** — synthetic crash-injection harness — NFR-R2 zero-tasks-lost.
- **Story 2.12** — write-interrupt harness — FR30 atomic-edit invariant.
- **Story 2.13** — idempotency 100× concurrent replay — FR28 / NFR-R4.
- **Story 2.14** — migrator v1.0.0 → v1.0.1 integration test — FR22 / NFR-M3.
- **Story 2.17** — log-capture + NFR-S1 secret-redaction verification.
- **Story 3.8** — Hypothesis command-injection fuzz — NFR-S5.
- **Story 6.12** — `test_decision_interleaving.py` Hypothesis property test — FR7 / NFR-R4.
- **Story 6.13** — `test_license_scan.py` — FR40 / NFR-S8.
- **Story 6.14** — Tier-3 negative test — FR38 / NFR-S6.

### Documentation

- **Story 1.10b** — full operator documentation set — NFR-M7.

### Principle

If any item above is not green/complete, Phase 1 has not shipped. The three architectural commitments (snapshot / single-writer / idempotency) and the three separability tests are the spine; their absence from green CI invalidates the MVP claim regardless of what else is complete.

---

## Phase 2 Epics — Observability Phase

> **Amendment added:** 2026-05-15. Decomposes FR53–FR71a from [`prd.md`](./prd.md) §"Phase 2 Scope Extension" into six epics, aligned with the architecture amendment in [`architecture.md`](./architecture.md) §"Phase 2 Architecture Extension".
>
> **Selected via:** [`phase-2-brainstorming.md`](./phase-2-brainstorming.md) (Narrative I — Observability Phase).
>
> **Phase label:** every story below carries `phase: 2` in `sprint-status.yaml`; cannot merge to `main` until ADR-0003 (Phase 2 gate) is `accepted`.

### Phase 2 Epic Summary

| Epic | Item | FRs | Stories | Effort | Order |
|---|---|---|---|---|---|
| **Epic 8** | γ Supply-chain hardening | FR53–FR56a | 6 | ~3 days | 1 — lands first to harden every later release |
| **Epic 9** | α `trace_id` propagation kernel | FR57–FR59a | 7 | ~1 week | 2 — unblocks every later epic |
| **Epic 10** | β `metrics-subscriber` service | FR60–FR62a | 6 | ~1 week | 3 — consumes α's trace_id |
| **Epic 11** | ξ Approval inbox + HMAC signature | FR63–FR65a | 5 | ~1 week | 4 — compounds with α |
| **Epic 12** | κ Per-task budget enforcement | FR66–FR68a | 4 | ~1 week | 5 — composes with β |
| **Epic 13** | δ litestream WAL replication | FR69–FR71a | 4 | ~3 days | 6 — orthogonal; ships in parallel with 11/12 if convenient |

**Total: ~32 stories, 6–8 weeks of solo-operator work.**

---

## Epic 8: Supply-chain hardening (γ)

**Goal.** Every Platform-published Docker image is verifiable end-to-end: cosign keyless signature against GitHub OIDC + SLSA L2 provenance attestation + CycloneDX SBOM attestation. Operator deploys refuse to pull unverified images. (FR53–FR56a, NFR-S9, NFR-S11.)

**Why first.** Cheapest by far (~3 days), lands before any other Phase 2 epic so that *every* later release ships through the hardened pipeline.

**Dependencies.** Phase 1's release.yml + GHCR setup (already in place from Story 1.9).

### Story 8.1: SBOM generation via anchore/sbom-action in release.yml
- **FR:** FR55. Generates CycloneDX SBOM for every published image.
- **Scope:** Add `anchore/sbom-action` step to the matrix build in `.github/workflows/release.yml`. SHA-pin the action.
- **AC:** SBOM artifact present for every service in a published release; contains direct + transitive deps with SPDX license identifiers; CI fails if SBOM generation fails.

### Story 8.2: SLSA L2 provenance attestation
- **FR:** FR54.
- **Scope:** `actions/attest-build-provenance` with `push-to-registry: true`. Verifies via `cosign verify-attestation --type slsaprovenance`.
- **AC:** `cosign verify-attestation --type slsaprovenance` succeeds for every published image; attestation traceable to specific commit SHA + workflow run ID.

### Story 8.3: Cosign keyless signing of every published image
- **FR:** FR53.
- **Scope:** `sigstore/cosign-installer` + `cosign sign --yes` against the published image digest. Configure workflow with `permissions: id-token: write` for OIDC. SHA-pin all sigstore actions.
- **AC:** `cosign verify` succeeds for every released image; signature certificate identity matches the GitHub Actions OIDC issuer.

### Story 8.4: Attach SBOM as cosign attestation
- **FR:** FR55, NFR-S11.
- **Scope:** `cosign attest --yes --predicate sbom-<service>.cyclonedx.json --type cyclonedx <image>@<digest>`.
- **AC:** `cosign verify-attestation --type cyclonedx <image>@<digest>` returns the SBOM matching Story 8.1's artifact.

### Story 8.5: Operator-side `just verify-images` recipe
- **FR:** FR56a, NFR-S9.
- **Scope:** New `justfile` recipe iterating `OMB_IMAGE_DIGEST_<service>` env vars, running `cosign verify` + `cosign verify-attestation --type slsaprovenance` for each. Failure refuses `docker compose pull`.
- **AC:** `just verify-images` exits non-zero on any mismatch; deploy procedure in `docs/deployment-guide.md` updated to require it before `docker compose pull`.

### Story 8.6: `deployment.signature_rejected` event + one-shot CLI helper
- **FR:** FR56a; new event type.
- **Scope:** Register `deployment.signature_rejected` at schema_version 1.1.0. Add `scripts/emit_signature_rejected.py` operator runs after verify failure to append the rejection to the event log even when the Platform stack is not running.
- **AC:** Helper writes a well-formed envelope to the JSONL log; `just verify-approval` (Epic 11) can read it in the audit trail.

### Epic 8 acceptance gate
- All Phase 2 baseline images carry valid cosign + SLSA + SBOM attestations.
- `just verify-images` against the latest release passes; against a tampered image fails with a specific reason.
- ADR-0008 (`docs/adr/0008-cosign-slsa-sbom.md`) authored and `accepted`.

---

## Epic 9: trace_id propagation kernel (α)

**Goal.** Every event emitted in Phase 2+ carries a `trace_id: UUIDv7` that correlates the complete causal chain of a single operator command across every service. (FR57–FR59a, NFR-O7.)

**Why second.** Every downstream Phase 2 epic depends on this being live.

**Dependencies.** Phase 1's envelope (`packages/events`), schema registry, `EventLogReader`/`EventLogWriter`. Epic 8.

### Story 9.1: Add `trace_id` as optional on the envelope
- **FR:** FR57 (partial — optional phase).
- **Scope:** `packages/events/src/events/envelope.py` — add `trace_id: str | None = None` with a deprecation warning if absent. Schema registry retains `1.0.0` as canonical.
- **AC:** Existing envelopes still parse; new envelopes with `trace_id` round-trip via canonical JSON.

### Story 9.2: registry-api middleware pulls `X-Trace-Id` header
- **FR:** FR58 (HTTP).
- **Scope:** Extend `IdempotencyMiddleware` (or sibling `TraceIdMiddleware`) in `services/registry-api/src/registry_api/adapters/middleware.py`. Mint `new_uuid7(clock=...)` if absent; log at WARNING.
- **AC:** Every `request.state` has `trace_id` set before any handler runs; response echoes `X-Trace-Id` header.

### Story 9.3: telegram-gateway AllowlistMiddleware derives `tg:{update_id}`
- **FR:** FR58 (Telegram).
- **Scope:** Extend `AllowlistMiddleware` to bind `trace_id = f"tg:{update.update_id}"` to structlog context before any handler.
- **AC:** Replaying the same Telegram `update_id` produces the same `trace_id` (deterministic); composes with FR28 idempotency.

### Story 9.4: console-cli mints at command entry
- **FR:** FR58 (console).
- **Scope:** `services/console-cli/src/console_cli/app/` command-entry hook mints `new_request_id(clock=...)` and threads as `X-Trace-Id` in the command envelope.
- **AC:** Every console command carries an explicit `trace_id`; `oh-my-bmad trace <trace-id>` returns at least one event per command.

### Story 9.5: MCP tool handlers take `caller_trace_id` as explicit input
- **FR:** FR58 (MCP).
- **Scope:** Extend Pydantic input models in `mcp-servers/{task-registry,session-registry,clawhip-bridge}` to take `caller_trace_id: str`. Tools propagate to downstream client calls. Schema round-trip tests in `tests/contract/` updated.
- **AC:** Tool invocations without `caller_trace_id` fail validation; with it, value appears in every event the tool emits.

### Story 9.6: worker-wrapper passes `--trace-id` CLI flag to Claude Code
- **FR:** FR59.
- **Scope:** `services/worker-wrapper/src/worker_wrapper/app/` adds `--trace-id <uuid>` to subprocess argv. Worker emits via `clawhip-bridge.emit_*` tools which now require `caller_trace_id` (Story 9.5).
- **AC:** Every event emitted by the worker for a task carries the same `trace_id` as the inbound operator command.

### Story 9.7: Schema bump 1.0.0 → 1.1.0 + migrator backfill + `/trace` operator query
- **FRs:** FR57 (completion), FR59a, NFR-O7.
- **Scope:** Flip envelope to `trace_id: str` (non-optional). Bump `schema_version` to `1.1.0`. Add `events.trace_id` column + non-unique index to `registry-state` schema. Backfill historical events via migrator container per ADR-0004. Add `/trace <trace-id>` to Telegram and `oh-my-bmad trace <trace-id>` to console; both query `events` by `trace_id`. CI gate (`scripts/checks/check_trace_id_required.py`) AST-scans every `EventEnvelope.create(...)` callsite.
- **AC:** Every new event has `trace_id` populated; `tests/replay/` still passes against migrated log; `/trace <trace-id>` returns chronologically-ordered causal chain across all services.

### Epic 9 acceptance gate
- Every new event in CI carries `trace_id`.
- `EventLogReader` accepts both 1.0.0 and 1.1.0 envelopes during 1-month consumer-compat window.
- `tests/separability/`, `tests/crash-injection/`, `tests/idempotency/`, `tests/contract/` all green.
- ADR-0004 authored and `accepted`.

---

## Epic 10: metrics-subscriber service (β)

**Goal.** A new workspace member that tails the JSONL event log read-only and computes Prometheus-format metrics — without injecting instrumentation into any existing service. (FR60–FR62a, NFR-O8, NFR-O10.)

**Why third.** Consumes Epic 9's `trace_id`. Establishes the "derived metrics, not parallel instrumentation" pattern that preserves Phase 1's NFR-O1.

**Dependencies.** Epic 8, Epic 9.

### Story 10.1: Scaffold `services/metrics-subscriber/` workspace member
- **FR:** FR60 (scaffold).
- **Scope:** uv-workspace member; `pyproject.toml` with `name = "metrics-subscriber"`; standard `src/metrics_subscriber/` layout; `py.typed`; scaffold `__main__.py`. Add to root `pyproject.toml` `[tool.uv.sources]` + `[project.dependencies]`. Update `just bootstrap-verify` (14 → 15 imports).
- **AC:** `just bootstrap-verify` green; `python -m metrics_subscriber` succeeds with the scaffold pattern.

### Story 10.2: Tail loop + cursor persistence
- **FR:** FR60 (tail).
- **Scope:** Lifespan task opens JSONL via `EventLogReader`, persists cursor to `oh-my-bmad-data/metrics-subscriber/cursor.json` every 1000 events, resumes from cursor on restart.
- **AC:** Restart-recovery test confirms subscriber resumes from last persisted cursor; no events processed twice; lag observable.

### Story 10.3: FastAPI `/metrics` endpoint (Prometheus exposition)
- **FR:** FR61.
- **Scope:** `prometheus_client` exposition; reachable on docker-compose-network only (no public ingress). Lifespan wires the tail task. NFR-O8 latency target (<100ms p95) verified by CI benchmark on fixed runner.
- **AC:** `curl http://metrics-subscriber:9090/metrics` returns valid Prometheus text format.

### Story 10.4: Core counter + gauge + histogram set
- **FR:** FR62.
- **Scope:** Implement the metrics enumerated in FR62: task counters by status, session counters by phase, idempotency-cache hit rate, capability-tier deny counts, `secret.accessed` counts, event-log append rate (1m/5m/1h), per-task token-spend gauges.
- **AC:** Each metric verified by integration test emitting controlled events and asserting resulting metric values.

### Story 10.5: Cardinality discipline + regression test
- **FR:** FR62 (labels); NFR-O8 (cardinality bound).
- **Scope:** Restrict labels to bounded enums. Add `tests/integration/test_metrics_cardinality.py` — emit 10K events with varied task_ids, assert label-set cardinality stays bounded. Fails CI if high-cardinality label sneaks in.
- **AC:** Cardinality test passes for baseline metric set; deliberately violating in fixture fails CI.

### Story 10.6: Separability test S-4 + add to compose stack
- **FRs:** FR62a; NFR-M4/M5 discipline.
- **Scope:** Add `metrics-subscriber` to `docker-compose.yml` with `condition: service_healthy` on registry-state. Add `tests/separability/test_metrics_subscriber_optional.py` — spin up with `OMB_METRICS_DISABLED=1`, assert rest of stack starts + serves identically.
- **AC:** Stack reaches 7/7 healthy with subscriber enabled; 6/6 with it disabled; both pass `bootstrap-verify`.

### Epic 10 acceptance gate
- ✅ `/metrics` returns documented metric set — Story 10.4 done (full FR62 set, ~51 timeseries).
- ✅ NFR-O8 benchmark (<100ms p95) verified in CI on fixed runner — Story 10.3 done (p95=0.94ms; refined to 0.65ms in Story 10.4).
- ✅ Separability test S-4 green — Story 10.6 done (this story closes Epic 10).
- ✅ ADR-0005 authored and `accepted` — Story 10.3 (initial) + Story 10.4 (§Cardinality + §Deferred amendments) + Story 10.5 (§CI regression gate amendment).

---

## Epic 11: Approval-request inbox + HMAC signature (ξ)

**Goal.** Operator approvals are non-repudiable — every `approval.granted` event carries an HMAC-SHA256 signed locally with `OPERATOR_HMAC_KEY`, offline-verifiable forever. Approval-request notifications consolidate into a pinned Telegram thread. (FR63–FR65a, NFR-S10.)

**Why fourth.** Composes with Epic 9 (`trace_id` correlates inbox messages back to originating task). Most operator-visible Phase 2 win.

**Dependencies.** Epic 9, Epic 10.

### Story 11.1: HMAC signing inside `/v1/tasks/<id>/decisions` handler
- **FR:** FR64.
- **Scope:** Load `OPERATOR_HMAC_KEY` from `.env` at startup via `pydantic-settings`. On every `approval.granted` emission, compute `HMAC-SHA256(key, task_id || action || timestamp || actor_id)` and emit sibling `task.approval_signed` event with the HMAC.
- **AC:** Every test approval produces a paired `task.approval_signed` event; HMAC value reproducible against the key.

### Story 11.2: Register `task.approval_signed` + `key.rotated` event types
- **FR:** FR64, FR65a; new event types.
- **Scope:** Register both at `schema_version=1.1.0` in `packages/events/src/events/schema_registry.py`. Add Pydantic payload classes with `frozen=True, strict=True`. Contract-fixture forward-compat pair added.
- **AC:** Schema-registry tests pass; payload validates with HMAC as 64-char hex.

### Story 11.3: `/approvals` Telegram command opens pinned thread
- **FR:** FR63.
- **Scope:** New `/approvals` handler in `services/telegram-gateway/.../handlers/`. Pinned-thread state stored in registry-state (one row per operator). Subsequent `task.approval_requested` events deliver to pinned thread with link-back to originating task thread.
- **AC:** Replay test: 10 approval requests for 10 different tasks arrive in pinned thread; each has a working link back to original.

### Story 11.4: `just verify-approval` offline recipe
- **FR:** FR65.
- **Scope:** Recipe reads single event by `event_id` from JSONL log, recomputes HMAC using operator's local `OPERATOR_HMAC_KEY`, prints structured match/mismatch. Works against frozen log copy with Platform stack not running.
- **AC:** Verifies fresh approval; deliberately corrupting HMAC produces clear mismatch with reason pointing to next investigation step.

### Story 11.5: Key rotation flow + `key.rotated` emission
- **FR:** FR65a.
- **Scope:** Operator updates `OPERATOR_HMAC_KEY` in `.env`, restarts stack. registry-api detects change (compares key fingerprint with last-known) and emits `key.rotated`. Pre-rotation approvals verifiable only against prior key (operator retains it for audit duration).
- **AC:** Rotation emits exactly one `key.rotated` per actual rotation; post-rotation approvals verify against new key only.

### Epic 11 acceptance gate
- Offline `just verify-approval` works against simulated 1-month-old approval.
- `OPERATOR_HMAC_KEY` grep-checked to never appear in any event/log/snapshot (`tests/integration/test_hmac_key_isolation.py`).
- ADR-0006 authored and `accepted`.

---

## Epic 12: Per-task budget enforcement loop (κ)

**Goal.** When `task.budget_exceeded` fires, `worker-wrapper` SIGTERMs the Claude Code subprocess within 5 seconds, emits `task.budget_enforcement_triggered`, transitions task per operator-declared policy. (FR66–FR68a, NFR-R8.)

**Why fifth.** Composes with Epic 10 — without metrics-subscriber observing enforcement frequency, operator has no signal for tuning policy.

**Dependencies.** Epic 9, Epic 10. `task.budget_exceeded` exists from Phase 1 (FR44).

### Story 12.1: Budget supervisor module in worker-wrapper
- **FR:** FR66.
- **Scope:** New `services/worker-wrapper/src/worker_wrapper/domain/budget_supervisor.py` — lifespan task subscribed to `task.budget_exceeded` for active task. On receipt: `subprocess.terminate()` → wait ≤5s → `subprocess.kill()` if still alive.
- **AC:** Integration test: emit a `task.budget_exceeded` event; supervisor terminates subprocess; total time from event-emit to subprocess-exit < 5s p99 (NFR-R8).

### Story 12.2: Emit `task.budget_enforcement_triggered` event
- **FR:** FR67; new event type.
- **Scope:** Register at `schema_version=1.1.0`. After subprocess termination, supervisor emits event with: budget_threshold, actual_spend, action_taken, post_trigger_transition (from per-task policy).
- **AC:** Event present in log after every enforcement; metrics-subscriber counts it.

### Story 12.3: `/approve --override budget` + `budget.override` event
- **FR:** FR68; new event type.
- **Scope:** Extend `/approve` Telegram handler (and console-cli equivalent) to accept `--override budget`. Emit `budget.override` audit event with the budget-delta granted. Update task's budget envelope.
- **AC:** Override before 5-second grace extends budget and prevents enforcement; override after termination requires `/retry` (documented sharp edge).

### Story 12.4: Per-task budget policy storage + default policy in `.env`
- **FR:** FR68a.
- **Scope:** Store per-task budget (token-ceiling, dollar-ceiling, action-on-exceed) on `task` row at submission. Add `OMB_DEFAULT_TASK_BUDGET_TOKENS` and `OMB_DEFAULT_TASK_BUDGET_ACTION` to `.env.example`. Update `pydantic-settings` model.
- **AC:** Tasks without explicit budget inherit defaults; tasks with explicit budget override defaults; `tests/integration/test_budget_policy_inheritance.py` verifies both paths.

### Epic 12 acceptance gate
- NFR-R8 latency verified by integration test on fixed runner.
- metrics-subscriber exposes `task_budget_enforcement_triggered_total` and `budget_override_total` counters.
- Budget policy documented in `docs/operator-runbook.md`.

---

## Epic 13: litestream WAL replication (δ)

**Goal.** Optional sidecar replicates SQLite WAL stream to operator-configured S3-compatible endpoint, enabling cross-host disaster recovery. **Replication ≠ HA** — explicitly framed in ADR. (FR69–FR71a, NFR-R7.)

**Why sixth (orthogonal).** Doesn't depend on any other Phase 2 epic; can ship in parallel with Epic 11 or 12.

**Dependencies.** Phase 1's compose stack + named volume + `just backup`.

### Story 13.1: litestream sidecar in docker-compose
- **FR:** FR69.
- **Scope:** Add `litestream` service to `docker-compose.yml` mounting `oh-my-bmad-data` shared-read. Sidecar **disabled by default**; activated only when `OMB_LITESTREAM_CONFIG_PATH` is set.
- **AC:** Stack starts to 6/6 (or 7/7 with metrics) healthy without sidecar; 7/7 (or 8/8) with sidecar enabled.

### Story 13.2: litestream config template + S3-compatible target docs
- **FR:** FR70.
- **Scope:** Ship `litestream.yml.example` with one config per supported target (S3, B2, R2, MinIO). Add `OMB_LITESTREAM_CONFIG_PATH` to `.env.example` with explicit credential-placement comment. Operator-runbook section added.
- **AC:** Operator can copy `litestream.yml.example` → `litestream.yml`, fill credentials, sidecar replicates within 1 minute of start.

### Story 13.3: `just restore-from-litestream <bucket>/<key>` recipe
- **FR:** FR71.
- **Scope:** Recipe: stop stack → recreate volume → `litestream restore` → start stack → `just bootstrap-verify`. Documented in `docs/backup-restore.md`.
- **AC:** End-to-end restore drill on fresh host produces a `bootstrap-verify`-passing volume. Drill runs in `nightly.yml`.

### Story 13.4: `just litestream-lag-check` recipe + `replication.lagging` event
- **FR:** NFR-R7; new event type.
- **Scope:** Recipe queries litestream's `/metrics` for replication lag. Emits `replication.lagging` (registered at schema_version=1.1.0) if lag exceeds 30s for >5 minutes.
- **AC:** Synthetic-delay test: blocking outbound S3 for 6 minutes produces exactly one `replication.lagging` event; restoring connectivity stops further emissions.

### Epic 13 acceptance gate
- Nightly restore drill in `nightly.yml` green.
- NFR-R7 verified manually + by recipe.
- ADR-0007 authored and `accepted` — must include the explicit "replication ≠ HA" framing.

---

## Phase 2 Ship-Blocker Checklist

Mirrors Phase 1 checklist. **Phase 2 has not shipped until every item below is green.**

### Architectural commitments (P2-I1–P2-I6, per architecture.md amendment)
- [ ] FR26 single-writer unchanged (every Phase 2 addition is read-only subscriber).
- [ ] Envelope `schema_version` bumped 1.0.0 → 1.1.0 additively; v1.0.0 envelopes still parseable for 1 month post-Epic 9.
- [ ] No instrumentation added to `services/*` — metrics-subscriber is the only metrics surface.
- [ ] MCP transport stdio-only (no `mcp.server.sse` / `streamable_http` imports anywhere).
- [ ] No new public-network ingress.
- [ ] Cosign + SLSA L2 + CycloneDX SBOM verified on every Phase 2 release.

### Per-epic gates
- [ ] **Epic 8** — `just verify-images` green against tagged Phase 2 release.
- [ ] **Epic 9** — Every new event in CI carries `trace_id`; `/trace <id>` returns coherent chains.
- [ ] **Epic 10** — `/metrics` p95 <100ms; cardinality test green; separability S-4 green.
- [ ] **Epic 11** — `just verify-approval` works offline against 1-month-old approval; HMAC key isolation test green.
- [ ] **Epic 12** — Budget enforcement p99 <5s on fixed runner; counters exposed.
- [ ] **Epic 13** — Nightly restore drill green; replication lag <30s p95.

### Phase 1 invariants regression-free
- [ ] `tests/separability/` (S-1 through S-4) all green at every Phase 2 epic boundary.
- [ ] `tests/crash-injection/` all green.
- [ ] `tests/idempotency/` all green (100× concurrent retry test for trace_id-extended envelope).
- [ ] `tests/contract/` all green incl. forward-compat fixtures for the 6 new event types.
- [ ] `tests/arch/` (single-writer, separability, transport, no-anthropic-outside-worker) all green.
- [ ] `tests/replay/` byte-for-byte equivalence holds after `trace_id` migration.

### New ADRs accepted
- [ ] **ADR-0003** — Phase 2 gate (formally opens `phase: 2` for `main`).
- [ ] **ADR-0004** — `trace_id` propagation policy + cutover plan.
- [ ] **ADR-0005** — metrics-subscriber as derived projection (forecloses OTel-everywhere).
- [ ] **ADR-0006** — operator HMAC non-repudiation + key rotation.
- [ ] **ADR-0007** — litestream WAL replication (read-only sidecar; replication ≠ HA).
- [ ] **ADR-0008** — cosign keyless + SLSA L2 + CycloneDX SBOM triumvirate.

### Documentation
- [ ] `docs/operator-runbook.md` extended with metrics scraping + litestream restore drill + budget tuning + HMAC verification recipes.
- [ ] `docs/explanations/` gains 1-2 new deep-dives (likely: trace-id propagation OR supply-chain pipeline OR HMAC signing flow).
- [ ] `_bmad-output/project-context.md` updated with Phase 2 additions to Cat 3 (litestream + metrics-subscriber framework rules) and Cat 7 (2-3 new high-frequency gotchas from Phase 2 retros).

### Principle

If any item above is not green/complete, **Phase 2 has not shipped**. The Phase 2 architectural invariants (P2-I1–P2-I6) and the `trace_id` correlation contract (NFR-O7) are the spine; their absence from green CI invalidates the Phase 2 claim regardless of what else is complete.

— *Amendment by R2d2, 2026-05-15, via the BMad `bmad-create-epics-and-stories` workflow (extension mode).*
