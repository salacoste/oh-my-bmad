---
stepsCompleted:
  - step-01-init
  - step-02-discovery
  - step-02b-vision
  - step-02c-executive-summary
  - step-03-success
  - step-04-journeys
  - step-05-domain
  - step-06-innovation
  - step-07-project-type
  - step-08-scoping
  - step-09-functional
  - step-10-nonfunctional
  - step-11-polish
  - step-12-complete
forwardCaptures:
  architecture:
    - 'Total ordering is free from single-writer + linear event log; WAL provides crash-consistency. No distributed clock or consensus needed in Phase 1.'
  testStrategy:
    - 'Deterministic UUIDv7 injection + controlled clock fixture in integration tests — lock in now to avoid flakiness in idempotency verification.'
    - 'Write-interrupt harness (pauses I/O mid-syscall, deterministic clock, byte-sequence replay) for verifying atomic file-edit recovery in Journey 3 restart scenario. Hardest single test case; must be in Phase 1 test infra.'
inputDocuments:
  - _bmad-output/planning-artifacts/product-brief.md
  - plan_draft.md
workflowType: 'prd'
documentCounts:
  briefs: 1
  research: 0
  brainstorming: 0
  projectDocs: 0
classification:
  projectType: infrastructure_platform
  projectTypeNotes: 'custom; developer_tool undersells service-boundary decomposition'
  domain: ai_agent_orchestration
  complexity: high
  projectContext: greenfield
partyModeInsights:
  blindSpots:
    - 'JTBD thesis: is Phase 1 "faster Claude Code" or "personal dev OS"? Frame before feature list.'
    - 'Event-integrity (no stdout parsing) must appear in acceptance criteria, not just principles.'
    - 'Message-design UX: approval fatigue + context starvation in Telegram summaries is a Phase 1 risk, not Phase 2 polish.'
vision:
  jtbd: 'Phase 1 ships "orchestrate Claude Code durably with remote control"; PRD written as "self-hosted personal dev OS" so later phases read as extensions, not scope creep.'
  ahaMoment: 'Stepped away for an hour, came back to the console, it remembered exactly where the task was — no reload, no mental reset. Restart survival is the infrastructural proof; return-to-flow is the felt experience.'
  coreInsight: 'Typed events + persistent task/session registry as source of truth. Every other surface (Telegram, console, worker, browser) is a replaceable client of that truth. Separability demands single-writer discipline on the registry and idempotent event handlers — without them, separability is a lie.'
  separabilityThesis: 'Human control, orchestration, runtime, and telemetry are four separable concerns from day one. Telegram/event bus/swappable CLIs/browser plane are all downstream consequences of that single idea.'
  whyNowMacro: 'MCP became infrastructure (Anthropic → Linux Foundation, cross-vendor adoption). CLI coding agents proliferated past single-vendor-lock-in. Analysts call the next infra tier "agent harness."'
  whyNowPersonal: 'Scratch-your-own-itch platform — the operator is the only user; no market case needed. Three specific blockers: lost work on host/bot restart, stdout-regex telemetry was fragile, laptop-tethered control prevented remote operation.'
  phase1ArchitecturalCommitments:
    - 'Snapshot strategy for the event log (to avoid linear-replay startup past ~10K events).'
    - 'Single-writer ownership of the task/session registry (one service owns writes; Telegram bot and console are read + command-emit clients, not direct writers).'
    - 'Idempotency contracts for Telegram/console command ingestion (command dedup by client-generated id; no duplicate task execution on network partition or bot restart).'
---

# Product Requirements Document - oh-my-bmad

**Author:** R2d2
**Date:** 2026-04-20

## Executive Summary

**Typed events and a persistent task/session registry are the only durable source of truth.** Every human surface, orchestrator, CLI runtime, and browser tool is a replaceable client of that truth — a self-hosted personal development OS for autonomous software engineering, built on that single commitment. A single operator drives the platform from Telegram (anywhere) or a local console (at the workstation). One `docker compose up` deploys the full stack — control plane, orchestrator, execution worker, typed event bus, and registries — to a VPS or a local macOS host. The operator kicks off tasks from a phone, approves risky steps, receives structured summaries, and trusts the system to survive host, container, and bot restarts without losing context. Phase 1 pairs one orchestrator (OMC) with Claude Code; future phases swap in Codex, Gemini, or others — the event spine stays untouched.

**Sessions survive restarts because the truth is not in the session.**

### What Makes This Special

Four concerns — **human control**, **orchestration**, **runtime**, and **telemetry** — are separable from day one. That single architectural idea is the product; Telegram-first control, the typed event bus (`clawhip`), and swappable CLI runtimes are downstream consequences.

Against the current landscape (Devin, OpenHands, SWE-agent, Replit Agent, ComposioHQ Agent Orchestrator, thepopebot), no competitor combines all three of these simultaneously:

1. **Telegram-first control surface** + local console parity — mobile-native, globally available, zero extra app install; competitors are web-UI- or IDE-coupled.
2. **Typed event bus instead of stdout parsing** — reliable replay, filtering, approvals, audit trail; production-shaped telemetry, not regex scraping.
3. **Designed for swappable CLI runtimes** behind a single MCP capability contract — v1 ships Claude Code; later phases add backends without orchestrator or registry changes.

**Architectural design choice (not a competitive claim):** browser automation arrives in Phase 4 as its own plane — browser sessions get their own service, risk profile, and scaling; they are not a tool call inside the coding agent's loop. Playwright/Puppeteer MCP integrations already exist; the distinction here is *separate plane with distinct policy*, not browser automation itself.

The **aha moment** is *return to flow*: the operator steps away, comes back an hour later, and the console and task registry remember exactly where they left off — no reload, no mental reset. Restart survival is the infrastructural proof; return-to-flow is the felt experience.

**Phase 1 architectural commitments** (non-negotiable, AC-bound):

- **Snapshot strategy for the event log.** Linear replay does not scale past ~10K events; periodic snapshots are a v1 concern, not a Phase 7 optimization.
- **Single-writer ownership of the task/session registry.** One service owns writes; Telegram bot and console are read + command-emit clients, never direct writers.
- **Idempotency contracts on command ingestion.** Every Telegram/console command carries a **client-generated UUIDv7 idempotency key**; processed keys are cached for **7 days**; on collision the system returns the prior result (dedupe), never double-executes, never silent-rejects. A network partition, bot restart, or retried message must never produce a duplicate task.

**Why now (macro):** MCP has consolidated into cross-vendor infrastructure (Anthropic donated the protocol to the Linux Foundation; OpenAI, Google, and Microsoft shipped adoption). CLI coding agents have proliferated past the point where binding to a single vendor is a sane default. Analysts identify "agent harness" as the next infrastructure tier.

**Why now (personal):** This is an explicitly scratch-your-own-itch platform — the operator is the only user; the market case is intentionally out of scope. What is required is that the three concrete blockers are the author's real pain: lost work on host/bot restart, stdout-regex telemetry that silently broke, and laptop-tethered control that blocked remote operation. Phase 1 exists to eliminate those three.

## Project Classification

| Field | Value |
|---|---|
| **Project Type** | `infrastructure_platform` (custom; service-boundary decomposition, not CLI-primary) |
| **Domain** | `ai_agent_orchestration` (custom; no matching CSV category) |
| **Complexity** | `high` (distributed system: six planes, durable registries, typed events, multi-runtime adapters, Docker deploy, security policy layer, cross-layer coordination in Phase 1) |
| **Project Context** | `greenfield` |

## Success Criteria

### User Success

- **Return-to-flow (aha) — two gates, both required:**
  - **(a) Display latency** — <5 s from client open to current-state rendered (p95 over 30 sample sessions).
  - **(b) Recognition check** — within 5 s of display, the operator can name the last task step and the next expected action without scrolling or extra queries (self-reported pass/fail, 30 sessions, ≥90% pass rate).
- **Overnight trust:** Submit a real multi-file feature-with-tests task from a phone at night; by morning see either (a) a merged PR with completion summary, or (b) a single blocker message with a clear `/approve`, `/retry`, or `/stop` decision waiting.
- **Remote confidence:** From any location with Telegram connectivity, start, inspect, approve, or stop a task without a laptop within arm's reach.
- **Loss-free restarts:** A host reboot, container restart, or Telegram bot crash mid-task is experienced as a momentary pause followed by automatic resumption — **never** lost work or silently-duplicated work.

### Business Success — Personal Operator ROI

The operator is the customer. Success = the platform earns its upkeep in saved time and reduced friction.

- **Unattended-task ratio:** Of overnight tasks submitted in a given week, **≥80%** complete without any human action between submission and morning summary other than `/approve` responses. If this drops below 50%, the orchestrator is undertrained, not the infra.
- **Re-work avoided:** Tasks lost to restart/crash **= 0 per month**. Top-line Phase 1 success number; anything above zero is a Sev1 regression.
- **Cognitive offload (log-verifiable):** Operator journal shows **≥3 entries/week by day 30 post-launch** where the task-completion event timestamp precedes the operator's first check of that task (verifiable via registry event timestamps vs. journal entries). Qualitative signal backed by an objective audit.

> *Engineering-health telemetry like control-surface uptime is tracked in Technical Success, not here. Uptime is a cause; ROI is the effect — don't double-count.*

### Technical Success

Non-negotiable technical proofs, each testable:

- **Event integrity.** Zero stdout-parsing regex in the task lifecycle path. Enforced by linter + code review. Every task state transition is a typed event emitted to `clawhip`. *(Forward-capture: integration tests use deterministic UUIDv7 injection + clock control.)*
- **Restart recoverability.** 100% of in-flight tasks recoverable after a forced `docker compose restart` (VPS target) and `docker stop --signal SIGKILL` (macOS target). Verified by a test script that kills the host at each lifecycle phase and asserts resumption from the last committed event.
- **Single-writer registry.** No code path outside the registry service performs registry writes. Enforced by a capability check at service boundary.
- **Idempotency contracts.** Command ingestion dedupes by client-generated UUIDv7 key with **7-day retention**. Duplicate submission returns the prior result; retry storms do not double-execute. Verified by a replay test submitting the same command 100× in parallel.
- **Snapshot scale.** Event-log replay on startup completes in **<5 s** for any session up to **10K events** via periodic snapshots. Verified by synthetic-load test.
- **Runtime decoupling.** Replacing the Claude Code worker with a scripted stub that emits canned events leaves orchestrator and registry code **unchanged and passing all tests**.
- **Operator latency.** Task-create → Telegram ack **<2.5 s p95** over **3 consecutive batches of 100 sequential submissions** (all three batches must clear threshold). Measured on same-region VPS or local host. Fast-path alternative for degraded CI network: measure to `clawhip` event emit instead of Telegram ack, threshold **<2.0 s p95**.
- **Single-command deploy.** A clean host reaches a fully functional platform in **<30 min** via `docker compose up`, verified on both a stock Ubuntu 24.04 VPS and a stock macOS 15 host.
- **Secret hygiene.** Zero plaintext secrets (Telegram bot token, GitHub tokens, Anthropic API keys, Docker secrets) in event logs, snapshots, or artifacts. Enforced by secret-scanner pre-commit hook and runtime log-sanitizer. Audit trail: every secret read emits a typed `secret.accessed` event (actor, scope, timestamp) queryable via registry. Secret rotation completes in **<5 min** via docker-compose env reload; no manual code changes required.
- **Control-surface health (internal, not user-facing).** Telegram bot + console-API availability ≥99% of wall-clock hours on the chosen deployment target, excluding planned upgrades. Health metric only — user-facing correlate lives in *Unattended-task ratio* above.

### Measurable Outcomes — Consolidated KPI Table

| # | Metric | Target | Measurement protocol |
|---|---|---|---|
| 1a | Return-to-flow display latency | <5 s from client open to current-state render | p95 over 30 sample sessions |
| 1b | Return-to-flow recognition | ≥90% of 30 sessions pass self-reported name-last-step-and-next-action within 5 s of display | Session log + self-report |
| 2 | Overnight autonomous runs | 5 consecutive multi-file feature-with-tests tasks, no intervention beyond `/approve` | Manual acceptance (brief §9) |
| 3 | Tasks lost to restart/crash | 0 per month | Registry audit log review |
| 4 | Time-to-first-task | <30 min, clean host to first completed task | Both deployment targets |
| 5 | Operator latency | <2.5 s p95 over 3×100 sequential submissions; all batches clear | Fast-path <2.0 s p95 to `clawhip` emit on degraded CI |
| 6 | Event integrity violations | 0 stdout-regex in lifecycle path | Static analysis / code review gate |
| 7 | Unattended completion rate | ≥80% of weekly overnight submissions | Weekly rolling |
| 8 | Registry startup time | <5 s up to 10K events via snapshots | Synthetic load test |
| 9 | Duplicate-task rate under retry | 0 per 100 concurrent duplicate submissions | Replay test |
| 10 | Cognitive offload (log-verifiable) | ≥3 entries/week by day 30; task completion precedes operator first-check | Registry event ts vs. operator journal |
| 11 | Secret hygiene | 0 plaintext secrets in logs/snapshots/artifacts; rotation <5 min via env reload | Secret-scanner + log-sanitizer + rotation drill |
| 12 | Control-surface health (internal) | ≥99% wall-clock availability | Uptime tracker — excluded from user KPIs |

## Product Scope

### MVP — Minimum Viable Product (Phase 1)

**In scope:**
- Telegram bot + allowlisted user ids.
- Local console client hitting the same application API as the bot.
- Task Registry + Session Registry services; single-writer; persistent across restarts; snapshot-aware.
- OMC (upstream fork) as the single orchestrator.
- Claude Code as the single execution backend.
- `clawhip` daemon (upstream fork) with typed event schema + one Telegram sink (compact + summary formats).
- Approval flow via text command (`/approve <task-id>`) for `git push` as the one gated risky action class.
- Three MVP-blocking MCP servers: `task-registry`, `session-registry`, `clawhip` event bridge.
- Docker images and `docker-compose.yml` for both VPS (Linux) and macOS targets.
- The three architectural commitments enforced (snapshot, single-writer, idempotency).
- Secret hygiene baseline (secret-scanner, log-sanitizer, `secret.accessed` events, env-reload rotation).

**Explicitly out:** GLM/Codex/Gemini workers, OMX, browser plane, multi-runtime handoff, Docker pool scaling, web dashboard, Telegram inline buttons, dead-session detection, scheduled jobs, artifact/git/github/memory/build MCP servers.

### Growth Features (Post-MVP, Phases 2–3)

- `clawhip` route richness: threaded Telegram topics per task, alert/summary/blocker templates, rate-limit policy, delivery dedup.
- Full approval UX: callback buttons (inline keyboards for `/approve` / `/reject` / `/stop`).
- MCP server fleet expansion: `artifact`, `git`, `github`, `build/verification`, `memory/wiki`.
- Recovery UX: runbook suggestions on blocker events, retry backoff policies.
- Additional `clawhip` sinks (local console live-feed, file-log sink).
- Automated secret rotation (currently manual env-reload).

### Vision (Phases 4–7)

See §Project Scoping → *Post-MVP Features* for the consolidated phase table covering Phases 4–7. Single source of truth; this subsection intentionally does not duplicate it.

## User Journeys

Phase 1 has **one human role** (the operator — R2d2) in multiple interaction modes, plus **one machine integration** (Claude Code worker lifecycle). No team/admin/support/API-consumer distinctions — the operator is all of them.

### Journey 1 — Primary user, happy path: "The overnight PR"

**Persona:** R2d2, solo operator. 11:47 PM, in bed with phone. A feature has been half-baked in his head for two days: *"add idempotency-key middleware to the API gateway, with tests."* He doesn't want to open a laptop.

**Opening scene.** He opens Telegram, selects the bot thread pinned at the top, and types:
`/task repo=gateway add idempotency-key middleware on POST/PUT; cover with table-driven unit tests; open a PR when green`
He hits send and puts the phone down.

**Rising action.** 3 s later the bot replies: *"Task `t-7f2a` created. Planning. Events on thread."* The thread updates over the next 20 minutes: plan ready (small summary, 4 steps), execution started (file-level deltas flow), tests running, first failure, self-fix, tests pass, commit, branch pushed, PR draft opened. R2d2 checks the thread once in the dark, sees "executing step 3/4", drops the phone.

**Climax.** 6:42 AM. Push notification: *"✅ Task `t-7f2a` complete. PR #143: `gateway/idempotency`. 3 files changed, 47 lines, 12 tests added. CI green. No blockers raised."* R2d2 opens the PR from his phone, scans the diff over coffee, leaves one comment, taps merge.

**Resolution.** Work that would have been a 90-minute desk session happened in the background while he slept. He never opened a laptop.

**Capabilities revealed:**
- Task submission from Telegram with free-text + repo + intent.
- Typed event stream to Telegram thread (plan, execute, test, commit, push, PR).
- Automatic PR creation on green tests.
- Completion summary message format (file count, line count, test count, CI state, blockers).
- Session thread binding (one Telegram topic per task).

### Journey 2 — Primary user, edge case: "The approval gate"

**Persona:** Same R2d2. 2:14 PM, in a café. Task `t-9c4e` has been running for 8 minutes — *"refactor the billing service to extract invoice-generation"*. The orchestrator has finished the code, run tests, and is about to `git push` the branch.

**Opening scene.** Phone buzzes. Message from the bot:
> 🔒 **Approval required — task `t-9c4e`**
> Action: `git push origin feature/invoice-extract`
> Risk class: repo-mutating
> Pre-push checks: ✅ lint · ✅ types · ✅ unit (47) · ✅ integration (6)
> Diff: 8 files, 312 insertions, 89 deletions
> Reply: `/approve t-9c4e` · `/reject t-9c4e <reason>` · `/stop t-9c4e`

**Rising action.** R2d2 opens the thread, scans the test summary, notices one integration test is new and one is deleted — the deleted one was for a method that got inlined. Reasonable. He types `/approve t-9c4e`.

**Climax.** 1.3 s later: *"Approved by @r2d2 at 14:14:32. Pushing."* Push completes; PR draft opens. Another 4 minutes: CI green, PR ready for review.

**Resolution.** The risky step never happened silently. The operator owns the decision; the system provides the context.

**Capabilities revealed:**
- Approval flow for one gated risky action class (`git push`) via text command.
- Pre-approval context packet (risk class + pre-check results + diff summary).
- `secret.accessed` / approval audit events emitted to registry.
- Idempotency on approval responses (double-tap `/approve` does nothing harmful).

### Journey 3 — Primary user, recovery: "The restart that nobody noticed"

**Persona:** Same R2d2. 3:02 AM. Task `t-a1b3` is mid-execution on the VPS. The VPS provider reboots the host for a security patch. He has no idea.

**Opening scene (from the operator's POV).** He sleeps.

**Rising action (from the system's POV).** `docker compose restart` on host boot. Registry loads the latest snapshot (87 events old), replays 134 events to reach current state (<3 s). Session Registry marks session `s-8de2` as `recovering`; `clawhip` emits `session.reconnecting` and `task.execution.resumed`. Claude Code worker reattaches to its assigned worktree via the session id. Execution continues from the last committed event — a file edit that was mid-write is reapplied idempotently (the worker's scratch file was atomic). The Telegram thread receives a compact update: *"ℹ️ Session reconnected after host restart. Task `t-a1b3` continuing from step 4/6."*

**Climax.** 6:30 AM. Task completes normally. Morning summary arrives as if nothing happened — with one extra line in the event timeline: *"03:02:14 host restart · 03:02:17 session reconnected · 03:02:18 execution resumed."*

**Resolution.** 7:15 AM, alongside the normal task completion summary, a second compact message arrives:
> 🛠️ Self-recovered from host restart at 03:02:14. 134 events replayed in 2.8 s. Zero intervention required.

R2d2 doesn't need the detail, but he earns confidence from seeing it surfaced. Hidden resilience becomes visible trust.

**Capabilities revealed:**
- Snapshot-aware registry replay on startup.
- Session Registry reconnection protocol tied to session id + worktree path.
- Idempotent file-edit operations at the worker layer (no double-apply on resume).
- `session.reconnecting` / `task.execution.resumed` typed events.
- Compact informational message template for recovery events.
- **Proactive morning "self-recovered" summary** whenever a restart occurred overnight — surfaces resilience rather than hiding it.

### Journey 4 — Admin mode (same operator, different hat): "First deploy"

**Persona:** Same R2d2, Saturday afternoon, sitting at his Mac. New VPS is provisioned. He wants the platform running on it by evening.

**Opening scene.** He SSHes into the VPS, clones the repo, copies `.env.example` to `.env`, pastes in his Telegram bot token, GitHub PAT, Anthropic API key, and his allowlisted Telegram user id. He runs `docker compose up -d`.

**Rising action.** Five containers come up: registry, orchestrator (OMC), worker (Claude Code), `clawhip` daemon, Telegram bot. The bot process logs *"Webhook set · allowlisted 1 user · ready."* He opens Telegram, messages `/ping` to the bot. Reply in 0.4 s: *"pong · registry: healthy · worker: idle · clawhip: 0 events queued · version: 0.1.0"*.

**Climax.** He sends a trivial first task: `/task echo hello world to the log`. Task completes in 6 s. He checks the registry via `/status t-0001`: full event timeline.

**Resolution.** Total elapsed time from `git clone` to first completed task: 17 minutes. The platform survives a `docker compose restart` test — he verifies by killing the worker mid-task and confirming the next task resumes clean. He closes the laptop.

**Capabilities revealed:**
- `docker-compose.yml` for VPS (Linux) and macOS targets.
- Environment-variable-only secret configuration (no code changes for rotation).
- `/ping` health command returning registry, worker, `clawhip`, version info.
- Allowlist enforcement at bot ingress.
- Time-to-first-task <30 min budget (KPI #4) verified live.

### Journey 5 — Machine integration: "Claude Code worker lifecycle"

**Persona:** The `claude-code-worker` process. Starts when orchestrator spawns it for task `t-c4d7`.

**Opening scene.** Orchestrator publishes `session.start_requested` with task id and worktree path to `clawhip`. Worker container boots; reads its session id from env; registers with Session Registry (`session.started` event); acquires an exclusive lock on the worktree.

**Rising action.** Worker requests the `task-registry` MCP server for task detail (read-only), the `clawhip` event bridge for event emission, and the Claude Code harness for execution. For every meaningful action — `plan.drafted`, `file.edited`, `test.run`, `commit.created` — it emits a typed event. It **does not parse any stdout for system state**; every state transition is a typed emission.

**Climax.** A `git push` step triggers the approval gate. Worker pauses, emits `task.awaiting_approval`, holds its lock, sleeps on a conditional wait. On approval event, worker wakes and continues.

**Resolution.** Worker completes, emits `session.finished`, releases the lock, exits cleanly. Registry marks session as terminated. The worker's entire contribution is auditable through the event stream — no stdout archaeology required.

**Capabilities revealed:**
- Worker ↔ Session Registry registration + heartbeat + lifecycle events.
- Worker ↔ MCP: read-only task detail, event bridge, session state.
- Worker awaits on typed events, not polling.
- Event-only telemetry contract (no stdout interpretation).
- Exclusive worktree locking; graceful shutdown.

### Journey 6 — Primary user, reconnaissance: "The stale blocker"

**Persona:** Same R2d2. 10:03 AM, gym. Submits `/task add rate-limit header` from Telegram. 40 minutes later the bot messages: *"⛔ Task `t-5e9c` blocked. Tests failing. See `/logs t-5e9c` for detail."* R2d2 glances, keeps lifting, forgets.

**Rising action.** 4:17 PM, he remembers. Opens the thread. Sends `/status t-5e9c`. Reply (one message):
> *"State: `blocked` since 10:41. Step 3/5. Last event: `test.failed` (2 unit tests, `middleware_rate_limit_test.py`). Last agent action: edit to `server/middleware/rate.py:87`. Worktree held. Worker idle. Available commands: `/logs`, `/retry`, `/stop`, `/handoff`."*

One screen, no scrolling.

**Climax.** He sends `/logs t-5e9c`. Gets a summary — not raw logs — naming the two failing assertions and the agent's last attempted fix. Decides the agent misread the intent. Sends `/retry t-5e9c hint="rate limit must be per-user, not per-IP"`.

**Resolution.** Task resumes with operator's clarifying hint injected into orchestrator context. 11 minutes later: PR open, green.

**Capabilities revealed:**
- Telegram `/status <task-id>` returns **reconstituted full context** in one message — never scrollback-dependent.
- Telegram `/logs <task-id>` returns an **LLM-summarized digest**, not raw event dump.
- Blocker messages include the "available commands" affordance so operator knows every next option.
- `/retry` accepts a free-text `hint=` that gets injected into orchestrator's next planning pass.
- Worktree lock is **held for a blocked task** until operator decides (`/retry`, `/stop`, `/handoff`).
- Operator can take hours to return; no session timeouts.

### Journey Requirements Summary

Each journey reveals distinct capabilities; consolidated:

| Capability cluster | Revealed by | Phase |
|---|---|---|
| Telegram bot command surface (`/task`, `/status`, `/approve`, `/reject`, `/stop`, `/retry`, `/logs`, `/ping`) | J1, J2, J4, J6 | MVP |
| Local console command surface (parity with bot) | Implied by brief | MVP |
| Typed event stream + Telegram sink templates (plan, execute, test, commit, push, PR, completion, approval, recovery, blocker) | J1, J2, J3, J6 | MVP |
| Approval flow (risk-class-gated, pre-context packet, audit event) | J2 | MVP |
| Session Registry lifecycle + reconnect protocol | J3, J5 | MVP |
| Task Registry + snapshot replay | J1, J3, J4 | MVP |
| Idempotent file operations + idempotent approval responses | J2, J3 | MVP |
| Allowlist enforcement + env-based secret config + rotation | J4 | MVP |
| Worker ↔ MCP integration (task-registry, session-registry, clawhip bridge) | J5 | MVP |
| Event-only telemetry (no stdout parsing) | J5 | MVP (enforced) |
| PR draft auto-creation on green tests | J1 | MVP |
| Structured completion summary (files/lines/tests/CI) | J1 | MVP |
| Exclusive worktree locking (held through blocker windows) | J5, J6 | MVP |
| Context reconstitution on `/status` (one-message full state) | J6 | MVP |
| LLM-digest `/logs` output (not raw dump) | J6 | MVP |
| `/retry` with operator hint injection | J6 | MVP |
| Proactive "self-recovered" morning summary on overnight restart | J3 | MVP |
| Inline Telegram buttons, richer approval UX | *(noted absent)* | Phase 2 |
| Additional risky-action classes beyond `git push` | *(noted absent)* | Phase 2–3 |
| `/handoff` to another runtime | *(noted absent)* | Phase 5 |

## Domain-Specific Requirements

### Compliance & Regulatory

**Not applicable in the traditional sense.** This is a self-hosted personal platform; there are no regulators, auditors, or certification bodies. The project does not touch PHI, PII beyond the operator's own, PCI data, or SOX-scope workflows.

Two soft compliance concerns that the operator owns personally:

- **Upstream project licenses.** The five upstream forks (OMC, OMX, `clawhip`, `claw-code`, `browser-harness`) have their own licenses; the platform must respect them and preserve attribution. If any upstream changes to a non-permissive license, Phase 1 adapters must be swap-ready.
- **Third-party API Terms of Service.** Anthropic, OpenAI, Google, GitHub, and Telegram all have terms that prohibit certain automated patterns. The platform must not (a) resell API capacity, (b) impersonate human users outside the allowlisted operator, or (c) run unattended bulk-generation that violates per-provider fair-use.

### Technical Constraints (domain-driven)

These are technical constraints that arise **because** this is an AI agent orchestration platform, not general software:

- **Capability model discipline (`plan_draft.md` §15).** Tools, resources, and prompts must be treated as three distinct affordances.

  > **Default posture — the single design principle behind the four-tier model:**
  > *Maximum context as resources. Minimum actions as tools. Risky actions only via approval.*

  Agent tool sprawl is the single biggest domain-specific anti-pattern; Phase 1 enforces three access tiers for the execution backend:
  - **Tier 0 — Read-only core:** workspace read, search, task/session registry read, event read.
  - **Tier 1 — Bounded write:** write/edit within assigned worktree, run local tests, artifact write.
  - **Tier 2 — Repo mutation:** git commit, branch create, PR draft. Subject to approval policy.
  - **Tier 3 — High-risk (Phase 1 supports only `git push`):** requires explicit approval event.
- **Pre-commit validation layer.** Every agent-made commit passes a hook-based check: no changes to `.env*`, `secrets/`, `*.pem`, `*.key`, `*.credentials*`; no file-path traversal outside the assigned worktree; no commit-message injection (null bytes, command substitution). Failure blocks the commit locally before any Tier-2/Tier-3 path is reached. Enforced in Phase 1 via a git `pre-commit` hook installed by the worker on session start. This is the cheapest layer to catch sensitive-file commits; Tier-3 stays reserved for truly irreversible operations (`git push`).
- **Event schema stability.** In an event-driven system, schema churn breaks replay. Phase 1 commits to a **versioned event schema** with additive-only changes; breaking changes require a migration path and registry-side shim.
- **Worktree isolation per task.** No two workers may write to the same worktree. Enforced via exclusive-lock acquisition at session start.
- **Provider abstraction boundary.** The platform must never hard-code the quirks of a specific CLI agent. Adapters expose a single `runTask(provider, prompt, context, budget, tools)` contract; any provider-specific behavior lives inside the adapter, not leaking into orchestration or registry code.
- **Context limits are first-class.** Every agent invocation has a provider-specific token budget; the orchestrator must plan around it (chunking, summarization, retry on context overflow) rather than fail silently.

### Integration Requirements

**Required external systems (Phase 1):**

| System | Integration mode | Scope |
|---|---|---|
| Anthropic Claude API | via Claude Code CLI | Primary execution backend |
| Telegram Bot API | HTTP webhook + polling fallback | Control surface |
| GitHub | PAT-authenticated REST API | PR creation, branch ops |
| Docker / Docker Compose | Local daemon socket | Deployment + container lifecycle |
| Git | Local binary + CLI | Source control operations |
| MCP protocol | stdio transport | Internal tool contracts |

**Required internal contracts:**

- **MCP as the capability contract** — every tool and resource exposed to agents must be defined via MCP, not ad-hoc function calls. This is the Phase 1 non-negotiable that enables Phase 5 runtime swapping.
- **Typed event schema** — protobuf or JSON Schema (decision for Architecture step). One source of truth for all cross-service communication.
- **Upstream fork boundary** — OMC, `clawhip`, and (Phase 4+) `browser-harness` are integrated as pinned upstream forks with adapter layers, never vendored or inlined.

**Deferred (Phase 2+):** Additional MCP servers (artifact, git, github, memory, build), Codex/Gemini/GLM APIs, browser automation via CDP, web dashboards.

### Risk Mitigations

Domain-specific risks that don't appear in generic software PRDs:

| # | Risk | Mitigation | Phase |
|---|---|---|---|
| 1 | **Agent gets stuck in a cost loop** (retries blow API budget) | Per-task token/cost cap; orchestrator emits `task.budget_exceeded` and halts; approval to extend. | MVP |
| 2 | **Agent emits unsafe shell commands** | Tier-3 classification; `git push` gated; destructive commands (`rm -rf`, database drops, prod deploys) never in Phase 1 tool surface. | MVP |
| 3 | **Prompt injection via untrusted file content** | Worker treats file content as data, not instructions. Prompt templates isolate role boundaries. No `eval`-style content promotion. | MVP |
| 4 | **Secret leakage in events/logs** | Already covered as a Technical Success metric (secret hygiene). Log sanitizer + secret-scanner + `secret.accessed` audit events. | MVP |
| 5 | **Upstream fork drift** | Pinned versions; adapter layer; contribute fixes upstream; integration test suite verifies adapter contracts on each upgrade. | MVP |
| 6 | **Schema churn breaks replay** | Versioned event schema; additive-only changes; migrations required for breaking changes; replay-integrity test on CI. | MVP |
| 7 | **Context window overflow** | Orchestrator-level chunking, summarization budgets, context eviction policy; per-provider limits configurable. | MVP |
| 8 | **Upstream API TOS drift** | **Not a testable mitigation.** Quarterly policy review: crawl Anthropic / OpenAI / Google / GitHub / Telegram TOS changelogs; review for behaviors the platform assumes (rate limits, automation clauses, user-attribution requirements); update adapter behavior if drift detected. Reminder tracked as a recurring scheduled task in the registry itself (dogfood) once scheduled jobs land in Phase 7; meanwhile a calendar reminder suffices. | Ongoing |
| 9 | **Concurrent task contention** | Exclusive worktree locking; single-writer registry; Phase 1 supports one task-per-worker. Multi-task parallel is Phase 6. | MVP (single-task) |
| 10 | **Malicious git operations via crafted task input** | Input sanitization on `/task` free-text; approval required for any history-rewriting op; force-push never in Phase 1. | MVP |
| 11 | **License contamination — agent generates code pulling in a license-incompatible snippet** (e.g., GPL fragment into a permissive-licensed project) | License-scan step runs on every agent-generated commit pre-push (tool: `scancode-toolkit` or `ORT` lightweight mode). Detected incompatibility emits `task.license_flagged` event and blocks the approval gate with a specific reason. Operator can override with explicit `/approve --override license` (emits audit event). | MVP |

## Innovation & Novel Patterns

### Detected Innovation Areas

The platform is genuinely novel along three axes. Each is a specific bet against the dominant design in the space.

**1. Separability of concerns as the product, not a feature.**
Mainstream agent platforms (Devin, OpenHands, Replit Agent) converge four concerns — human interface, orchestration, runtime, telemetry — into a single tightly coupled experience. This platform inverts that: the *point* is that those four concerns are independent clients of a shared source of truth (the registry + event bus). Telegram-first control, swappable CLI runtimes, and the future browser plane are downstream consequences of the inversion, not features added to a monolith. No surveyed competitor ships this inversion as its architectural identity.

**2. Messaging-app as primary control plane for autonomous dev work.**
Telegram as a *control surface* — not as a notification channel on top of a web UI — is rare in professional developer tooling. The closest precedent is `thepopebot` (Telegram + GitHub Actions trigger), but that treats chat as a pipeline trigger; it doesn't reconstruct task context, attach approvals, or provide LLM-digest `/logs`. The innovation is industrialized chatops for autonomous development: command surface parity between console and phone, approval gates with pre-context packets, LLM-summarized status that doesn't require scrollback.

**3. Typed events as the sole durable state — applied to the agent space.**
In mature distributed systems (Temporal, event-sourced services), typed-events-as-ground-truth is table stakes. In the current agent-orchestration space, no surveyed competitor has adopted it: Devin, OpenHands, SWE-agent, Replit Agent, and ComposioHQ all rely on stdout observability and in-memory state. The innovation is not the pattern itself but its first-principled application to autonomous CLI-agent coordination — `no stdout parsing` is enforceable, session resumption is a first-class registry feature (not a recovery hack), and `/status` / `/logs` on any surface reconstitute state from the same ground truth.

Secondary novel design choices (not differentiators against the market, but not-common-practice):

- **MCP capability tiers as a governance discipline, not a tool surface** (Tier 0/1/2/3 access model, pre-commit validation, approval-gated Tier 3).
- **Aha moment framed as *return-to-flow*, not restart-survival** — the UX target is the felt experience of continuity, not the infrastructural proof (see Success Criteria KPIs 1a + 1b).
- **Phase 1 ships a single-operator, scratch-your-own-itch platform** — the *absence* of a market-fit narrative is itself an innovation in how this PRD is structured.

### Market Context & Competitive Landscape

From prior research (embedded in Executive Summary §5); summarized here:

| Competitor | Overlaps | Does NOT have |
|---|---|---|
| **Devin (Cognition)** | Fully autonomous SWE, cloud VM runtime | Telegram control, typed event bus, swappable CLI backends, self-hosted |
| **OpenHands (All Hands AI)** | Multi-agent, OSS, sandboxed execution | Messaging-app control, dedicated browser plane, typed event bus, swappable CLI backends |
| **SWE-agent (Princeton)** | Minimal footprint, Agent-Computer Interface | Orchestration platform, persistent registry, remote control, multi-runtime |
| **Replit Agent 4** | End-to-end autonomous, continuous runtime | Self-hosted, Telegram control, swappable model backends |
| **ComposioHQ Agent Orchestrator** | Agent-agnostic, runtime-agnostic, tracker-agnostic | Telegram control, typed event bus, dedicated browser plane, MCP-as-capability-contract |
| **thepopebot (GH Actions + Telegram)** | Telegram trigger | Persistent registry, typed events, swappable CLI backends, Docker deploy |

**Category framing:** The market is still naming this tier ("autonomous software engineering", "agentic coding platform", "AI agent orchestration", "agent harness"). "Agent harness" is the term that best describes this platform's identity — not an agent, not an IDE, but the supervision layer above heterogeneous CLI agents. Analyst quote worth keeping: *"2025 was the year of agents; 2026 is the year of harnesses."*

**Validating market signals:**

- MCP adoption across OpenAI, Google, Microsoft; Anthropic's donation to the Linux Foundation in Dec 2025 → MCP-as-capability-contract is a defensible architectural bet.
- CLI-agent proliferation (Claude Code, Codex, Gemini CLI, Aider, Goose, Plandex, OpenCode, SWE-agent, and others) → single-vendor orchestration is a bad bet.
- Docker-first remote execution is standard (AWS remote SWE reference architecture; Docker's sandbox-for-agents work) → the deployment model is not exotic.
- Gartner recorded 1,445% surge in multi-agent system inquiries Q1/2024 → Q2/2025 → the category exists.

### Validation Approach

Each innovation axis has Phase 1 validation tests baked into Success Criteria:

| Innovation claim | How we know it's real (not theater) |
|---|---|
| **Separability of concerns** | Three-test suite (see below) exercising runtime + orchestration axes; human-control axis covered by shipping Telegram + console as independent clients of the same application API; telemetry-as-consequence proven by event-integrity enforcement. |
| **Messaging-app as primary control** | Remote confidence (§User Success) + operator latency metric (<2.5s p95, §KPI #5) + Journey 6 reconnaissance capabilities (`/status` one-message reconstitution, LLM-digest `/logs`). If the phone-only flow fails once without operator going to a laptop, the claim is broken. |
| **Typed events as sole durable state** | Event integrity (§Technical Success, §KPI #6): zero stdout-regex in lifecycle path, enforced by linter + code review. Restart recoverability (§KPI #2) verifies resumability from registry alone. |
| **Aha — return-to-flow** | Dual-gate KPI #1a (display latency <5s p95) + #1b (recognition check ≥90% pass). |

**Separability proof — three-test suite (Phase 1 CI):**

| # | Axis tested | Test |
|---|---|---|
| S-1 | **Runtime swap (cold)** | Replace Claude Code worker with scripted-stub worker that emits canned events. Orchestrator + registry code pass all tests unchanged. Baseline interface-compatibility test. |
| S-2 | **Runtime swap (mid-flight)** | Start a task with a real worker; mid-flight, kill it and hand off to the scripted stub. Registry resumes, events flow, no state corruption, no event loss. Single-writer + snapshot + idempotency all exercised together under motion. |
| S-3 | **Orchestration swap (pass-through)** | Replace OMC with a "null orchestrator" that just forwards events and issues trivial commands. Registry, event bus, and worker keep functioning; a canned task completes. Proves orchestration logic is not embedded in any lower layer. |

The fourth axis (human control) is covered **operationally, not via unit test**: both Telegram bot and local console ship in Phase 1 as independent clients of the same application API. Each surface proves the other is swappable by existing alongside it.

**Validation philosophy:** the PRD avoids claims that can't be converted to a failing test or a measurable user experience. Innovation theater (claims that sound novel but aren't testable) is the single biggest risk in this section.

### Risk Mitigation

Innovation-specific risks (complementing the domain risk table in §Domain):

| Risk | Mitigation |
|---|---|
| **"Separability" never gets stress-tested** because Phase 1 only has one of each concern (one orchestrator, one runtime, one sink) | Ship the three-test separability suite (S-1, S-2, S-3) in Phase 1 CI. Without all three, separability is architectural posture, not architectural fact. |
| **Telegram as primary surface becomes a liability** (outages, bans, quota) | Console parity from day one; `clawhip` sink abstraction allows adding an additional delivery channel (email, webhook, local log tail) in Phase 2 without changes to orchestration. |
| **Typed events get bypassed under schedule pressure** (someone adds a `subprocess.check_output().decode()` parse) | Linter rule; code review gate; `task.state_change_untyped` bug class tracked as P0. |
| **Category framing loses momentum** (the "agent harness" tier gets absorbed into IDE-embedded agents instead) | The platform is scratch-your-own-itch; category adoption is not a success condition. The absence of a market-fit dependency is itself a mitigation. |
| **Innovation is rejected by the operator** (the operator prefers a laptop to a phone; the aha moment doesn't land) | Personal-operator-ROI KPIs (§Business Success) make the platform falsifiable as a tool *for this specific operator*. If the operator stops using it, the platform failed even if everything shipped. |

## Infrastructure Platform — Specific Requirements

### Project-Type Overview

**Category:** Self-hosted infrastructure platform for AI agent orchestration. Deployable as a service stack (multi-container) rather than a library, CLI, or SaaS. The operator owns the hardware (VPS) or the host (macOS) and the full data plane.

**Shape:** 5 containers minimum in Phase 1 — Telegram bot, registry service, orchestrator (OMC), execution worker (Claude Code), `clawhip` daemon. A 6th container is the console gateway (if split from the registry HTTP API). All wired via an internal docker-compose network; external ingress only for Telegram webhook + operator SSH.

### Technical Architecture Considerations

#### Service Topology (Phase 1 default)

```
┌────────────────────┐      ┌────────────────────┐
│  Telegram bot      │      │  Console client    │
│  (telegram-gateway)│      │  (CLI binary)      │
└─────────┬──────────┘      └─────────┬──────────┘
          │ HTTP/JSON (app API)       │
          └────────────┬──────────────┘
                       ▼
          ┌────────────────────────────┐
          │  Registry service          │
          │  — Task Registry           │  ← single writer
          │  — Session Registry        │     source of truth
          │  — Event log + snapshots   │
          │  — Application HTTP API    │
          └──────────┬─────────────────┘
                     │ MCP (stdio)
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │  OMC     │ │ Worker   │ │ clawhip  │
  │orchestr. │ │ (Claude  │ │ event    │
  │          │ │  Code)   │ │ bus +    │
  │          │ │          │ │ Telegram │
  │          │ │          │ │ sink     │
  └──────────┘ └──────────┘ └──────────┘
```

**Event-log append is the only mutation path to registry state.** Clients (orchestrator, worker) can emit events via the `clawhip-bridge` MCP server, which writes to the event log *only*. The registry service owns a single subscriber process that reads from the event log, interprets events, and materializes derived state (task rows, session rows, snapshots). No client — including orchestrator, worker, Telegram bot, or console — ever writes to registry tables directly. The `clawhip` bridge itself has append-only semantics; it cannot read or modify prior events. Enforced at the service boundary by a capability check + integration test: *attempt a direct registry table write from a non-registry container → must fail with `permission_denied`.*

This is what makes "single-writer discipline" load-bearing, not decorative.

#### Service Stack — Language & Runtime Defaults (with rationale)

Soft defaults; Architecture step may revise. Each must be justified not just by familiarity but by ecosystem + operational cost.

| Service | Default language | Rationale |
|---|---|---|
| Telegram bot + console gateway | Python 3.12 (FastAPI + `aiogram` v3) | First-class Anthropic SDK, mature async HTTP story. Same runtime as worker reduces cognitive load. |
| Registry service | Python 3.12 (FastAPI) + SQLite | SQLite + WAL mode gives single-writer semantics for free. Embedded DB matches the single-operator deployment profile. Upgradeable to Postgres in Phase 6+ without app changes via SQLAlchemy. |
| Orchestrator (OMC adapter) | Upstream OMC language (Python/TypeScript) behind an adapter shim | OMC is an upstream fork; no language choice is made here. Adapter wraps it in the platform's service contract. |
| Worker (Claude Code CLI) | Node.js (the CLI's runtime) + Python wrapper emitting typed events | Wrapper owns MCP bridging + event emission; the CLI stays pristine. |
| `clawhip` event bus | Upstream `clawhip` language (likely Go or Rust) | Same pattern as OMC — upstream, adapted via MCP bridge. |

**Single stack principle for Phase 1:** Python everywhere the platform owns the code; upstream forks keep their own languages. Resist the urge to rewrite anything upstream.

#### Storage — Data Choices

| Concern | Phase 1 choice | Rationale |
|---|---|---|
| Task + Session registry | SQLite with WAL | Simple single-writer, zero ops, file-copyable for backup. |
| Event log | Append-only JSONL file per day, rolled up into SQLite table on snapshot | Easy to inspect; cheap replay; snapshots keep startup under 5 s (§KPI #8). |
| Artifacts (PR descriptions, summaries, screenshots in Phase 4) | Flat filesystem under `/var/lib/oh-my-bmad/artifacts/{task-id}/` | Trivially backupable with rsync; no DB blob bloat. |
| Secret storage | `.env` file + env-var injection at container start | Covered by secret-hygiene KPI #11; no vault needed in Phase 1. |
| Memory / wiki (Phase 3) | Filesystem + SQLite FTS5 | Same simplicity principle. |

**Upgrade path to Postgres:** SQLAlchemy ORM means swap is primarily a deployment concern, not a code rewrite. Scheduled for Phase 6 only if operator deploys to shared infrastructure.

### API Surface

Three distinct surfaces, each with its own contract:

#### 1. Application HTTP API (internal)

JSON-over-HTTP; served by the Registry service. Consumed by Telegram bot, console client, and internal health checks only. Not intended for public consumption in Phase 1.

Core endpoints:

| Verb | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tasks` | Create task (idempotent via `Idempotency-Key` header). |
| `GET` | `/v1/tasks/{id}` | Reconstituted task state for `/status` (one-response full context). |
| `GET` | `/v1/tasks/{id}/events` | Raw event stream (for debugging). |
| `GET` | `/v1/tasks/{id}/logs/digest` | LLM-summarized digest (for `/logs` command). |
| `POST` | `/v1/tasks/{id}/decisions` | Operator decision: `approve`, `reject`, `stop`, `retry` (with optional hint). |
| `GET` | `/v1/sessions/{id}` | Session state for debugging. |
| `GET` | `/v1/health` | Used by `/ping` command. |

Versioned under `/v1/` from day one. Additive-only changes until v2.

#### 2. MCP Surface (tool / resource contracts)

Phase 1 ships **three** MCP servers, all served over stdio from the Registry service container:

| MCP server | Resources (read) | Tools (write) |
|---|---|---|
| `task-registry` | task list, task detail, approval queue, blockers | `task.add_note`, `task.attach_artifact`, `task.emit_event` |
| `session-registry` | active sessions, worker metadata, heartbeats | `session.heartbeat`, `session.register`, `session.close` |
| `clawhip-bridge` | recent event stream (read-only), route diagnostics | `emit_event`, `emit_blocker`, `emit_summary`, `emit_approval_request`, `emit_completion` — **all append-only; cannot mutate prior events** |

MCP server authorization is tier-enforced: workers get Tier 0/1 read + bounded write; orchestrator gets Tier 2; operator (via application API) gets Tier 3 via approval path.

Deferred to Phase 3+: `workspace`, `artifact`, `git`, `github`, `build`, `memory`/`wiki`, `docker-pool`, `db-schema`, `docs-research`, `browser-automation`, `telegram-control-direct`.

#### 3. CLI (Console) Surface

Single binary `oh-my-bmad-cli` (packaged with the platform). Subcommands mirror Telegram commands 1-to-1 for surface parity:

```
oh-my-bmad-cli task <description> [--repo] [--hint]
oh-my-bmad-cli status <task-id>
oh-my-bmad-cli logs <task-id>
oh-my-bmad-cli approve <task-id> [--override license]
oh-my-bmad-cli reject <task-id> <reason>
oh-my-bmad-cli stop <task-id>
oh-my-bmad-cli retry <task-id> [--hint]
oh-my-bmad-cli ping
oh-my-bmad-cli events <task-id> [--follow]        # raw event tail for debugging
```

`--follow` on `events` is the local equivalent of a live Telegram thread. No operator-facing feature that exists on Telegram is absent from the CLI (parity principle).

### Installation Methods

Phase 1 supports exactly two deployment targets. Others are Phase 2+.

| Target | Primary install command | Prereqs |
|---|---|---|
| **VPS (Linux)** | `git clone … && cp .env.example .env && $EDITOR .env && docker compose up -d` | Docker Engine ≥ 24, Docker Compose v2, ~2 GB RAM baseline, outbound internet to Anthropic/Telegram/GitHub. |
| **Local macOS** | Same command; `docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d` | Docker Desktop or Colima ≥ 0.6, macOS 15+. |

Both targets validated by the time-to-first-task <30 min KPI (#4).

**What the installer DOES provide:** docker images (published to GHCR), `.env.example` with every required variable commented, health check script, and a 10-line README quickstart.

**What the installer does NOT provide in Phase 1:** Kubernetes manifests, Helm chart, systemd units, package-manager-native install (apt/brew), cloud-provider templates, TLS termination, reverse proxy config. All deferred.

**Recommended v1 topology — single-target deployment.** Pick one host (a same-region VPS *or* local macOS) and run all five Phase 1 containers on it. The split topology (operator on macOS, execution on VPS) is listed in the plan draft as "Personal-first" and is attractive in principle, but introduces cross-host networking, auth, and Docker-orchestration complexity that is **not justified for Phase 1 MVP**. Split deployment is a Phase 6 concern, unlocked by the server execution pool. Until Phase 6, single-target is the honest recommendation; the plan draft's larger topology remains the long-term target.

### Code Examples / Quickstart

A running example that ships in the repo:

```bash
# 1. Clone + configure
git clone https://github.com/<user>/oh-my-bmad.git
cd oh-my-bmad
cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, GITHUB_TOKEN, TG_ALLOWLIST_USER_IDS

# 2. Deploy
docker compose up -d
docker compose logs -f telegram-gateway  # watch for "Webhook set · ready"

# 3. First task (from Telegram)
# /task echo hello world

# 4. First task (from console)
docker compose exec console oh-my-bmad-cli task "echo hello world"
docker compose exec console oh-my-bmad-cli status t-0001

# 5. Clean shutdown + restart recovery test
docker compose stop --timeout 1                 # kill mid-task
docker compose up -d                            # verify task resumes
```

**CI validation is split into two independent tests to avoid cross-KPI masking:**

- **Test A — Restart recovery (KPI #2).** Deterministic scenario, controlled fixture, no timing threshold; asserts 100% of in-flight task state recovers after forced `docker compose stop --timeout 1` + `up -d` at each lifecycle phase. Failure mode protected against: timing luck hiding state-corruption bugs.
- **Test B — Time-to-first-task (KPI #4).** Timed scenario under synthetic background load (concurrent API calls, simulated disk latency); asserts <30 min from clean host to first completed task. Run on a representative-spec CI runner, not an unloaded developer laptop. Failure mode protected against: fast unloaded hardware masking production latency regressions.

The combined "quickstart journey" above remains a manual smoke-test runbook for humans, but **KPIs #2 and #4 are measured by the two split tests, not the combined flow.**

### Migration / Upgrade Guide (for registry schema evolution)

Event-sourced systems break badly on schema churn. Phase 1 commits:

- **Every event has a `schema_version` field** (semver, starts at `1.0.0`).
- **Additive-only changes** within a major version (new event types, new optional fields on existing types).
- **Breaking changes require a migrator** that reads old events and emits new ones into a fresh event log; old log kept as archive.
- **Registry service keeps a handler matrix** keyed by `(event_type, schema_version)`. Unknown combinations emit `event.unknown_schema` and halt ingestion.
- **Migrator runs as a one-shot container** (`docker compose run --rm migrator v1.0.0-to-v2.0.0`), never in the hot path.

Phase 1 ships with v1.0.0 schema; migration machinery scaffolded but only exercised by a test that upgrades from v1.0.0 → a synthetic v1.0.1 (additive).

### Implementation Considerations

Items that are decisions the PRD flags (some locked, some deferred to Architecture step):

1. **Registry storage final choice.** SQLite proposed; alternatives (Postgres, RocksDB, LMDB) remain open until load-test data is available. *(Deferred.)*
2. **Event serialization format.** JSON proposed for v1.0.0. Protobuf/CBOR options revisited if event volume justifies the operational cost in Phase 2+. *(Deferred.)*
3. **Telegram bot library: `aiogram` v3 (locked).** Chosen for async-native design, middleware composability for approval/auth layering, cleaner webhook handling, and first-class support for topic-based chats (needed for Phase 2 threaded summaries). `python-telegram-bot` considered and rejected as less aligned with FastAPI's async idioms. Locked at PRD stage so story decomposition can begin.
4. **MCP transport.** stdio proposed for Phase 1 (simplest); HTTP transport is an option if remote MCP servers land in Phase 6. *(Deferred.)*
5. **Logging + tracing stack.** Structured JSON logs to stdout; no metrics/tracing infra in Phase 1 (secret-hygiene KPI #11 covers sanitization). *(Locked.)*
6. **Identity / auth inside the platform.** Phase 1 trusts docker-network boundary; no mTLS between services until remote-worker support (Phase 6). *(Locked.)*
7. **Upgrade discipline.** Rolling `docker compose up -d` on image tag bump; data-volume preserved; migrator container run explicitly when schema version changes. Documented in the README. *(Locked.)*

Items skipped per CSV (`skip_sections`: `visual_design`, `store_compliance`):

- No visual design spec (text-based UX; Telegram message templates are content, not visual design).
- No store-compliance concerns (not distributed through app stores or package registries in Phase 1).

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**MVP approach: Platform-proof MVP.**

Of the four standard MVP philosophies (problem-solving, experience, platform, revenue), this project is explicitly a **platform-proof MVP**. Phase 1 exists to prove the *architectural thesis* — that separability of concerns + typed events + persistent registry is a viable spine — not to prove a market, not to build a brand, not to scale.

Why the alternatives don't fit:
- **Problem-solving MVP** would mean "just orchestrate Claude Code better" — too narrow; doesn't prove separability.
- **Experience MVP** would mean "polish the Telegram UX" — premature; UX is downstream of the spine.
- **Revenue MVP** is categorically N/A — this is explicitly a scratch-your-own-itch platform (§Executive Summary, *Why now — personal*).

**MVP gate = Journeys 1 + 3.**

Overnight PR (J1) exercises every platform commitment under real async pressure (snapshot, single-writer, idempotency, event-only telemetry, PR creation, completion summary). Restart recovery (J3) exercises the spine's hardest claim (sessions survive restarts). **Passing J1 + J3 end-to-end, by the bootstrapped platform executing a real Phase 1 story, is the MVP ship criterion.**

Journeys 2, 4, 5, 6 are **Phase 1 stretch**: design coverage is required (stories decomposed, ACs written, tests defined), end-to-end demonstration is preferred but not a ship blocker. If the operator's capacity runs out after J1 + J3 are proven, Phase 1 has shipped — even if J2/J4/J5/J6 remain partially complete.

**The MVP succeeds if and only if:**
1. Phase 1 ships the three architectural commitments enforced (snapshot, single-writer, idempotency — §Technical Success).
2. The three-test separability suite (S-1, S-2, S-3) passes in CI (§Innovation — Validation).
3. Journeys 1 and 3 complete end-to-end, executed by the platform on a real Phase 1 story.

If any of those three fail, the MVP is not done — even if everything else ships.

### Resource Requirements

**Team size: 1 human (the operator) + AI coding agents used for implementation.**

The operator is building a platform whose premise is autonomous AI coding assistance; the platform itself will be built with that same assistance. Implications:

- **No delegation to external engineers in Phase 1.** No contractor lane. No freelance budget.
- **Every story must be executable by an LLM-paired solo operator** in a reasonable session (rule of thumb: ≤ 1 working day of operator attention per story, regardless of total agent compute time).
- **No domain expertise is purchased** — the operator is the only expert on what "good" means for this operator.
- **Feedback loop is self-contained:** the operator submits stories to the *future* version of the platform being built. (Bootstrapping is explicitly part of Phase 1 — once enough of the platform runs to orchestrate Claude Code durably, the operator can use it to build the remaining Phase 1 features. This is not a gimmick; it is the fastest known validation loop.)

**Compute budget:** bounded by Anthropic API spend. Operator accepts the cost as R&D for personal infrastructure. No separate infrastructure team or observability tooling budget in Phase 1.

**Dependency on upstream forks:** If OMC or `clawhip` upstream velocity stalls, the operator absorbs the maintenance burden directly. No external rescue path.

### MVP Feature Set (Phase 1)

**Already defined** in §Product Scope — MVP section. Single source of truth for what is in/out is §Product Scope; this section does not duplicate, only reinforces.

**Journey gate (repeated for prominence):** J1 + J3 required; J2 + J4 + J5 + J6 stretch.

**Must-have capabilities** (cross-referenced with §Journey Requirements Summary): Telegram bot, console parity, typed event stream with Telegram sink, approval flow for `git push`, Session + Task Registry with snapshot replay, idempotent operations, worker ↔ MCP integration, event-only telemetry enforcement, PR draft auto-creation, structured completion summary, `/status` reconstitution, LLM-digest `/logs`, `/retry` with hint, worktree locking, proactive self-recovered summary.

**Explicitly out for MVP** (also in §Product Scope): GLM/Codex/Gemini/OMX/`claw-code`, browser plane, multi-runtime handoff, Docker pool scaling, web dashboard, Telegram inline buttons, additional MCP servers (artifact/git/github/memory/build), dead-session detection, scheduled jobs, `/handoff`.

### Post-MVP Features

**Already defined** in §Product Scope — Growth + Vision. Summary table for cross-reference:

| Phase | Theme | Target unlock |
|---|---|---|
| **Phase 2** | Event plane maturity | Threaded Telegram topics per task, richer approval flows, inline keyboards, additional `clawhip` sinks. |
| **Phase 3** | MCP tooling fleet | `artifact`, `git`, `github`, `build/verification`, `memory/wiki` servers. |
| **Phase 4** | Browser automation plane | `browser-harness`-backed Browser Automation Server, live + remote modes. |
| **Phase 5** | Multi-runtime | OMX, Codex/Gemini/GLM/`claw-code` adapters, runtime handoff. |
| **Phase 6** | Server execution pool | Docker worker pool, isolated worktrees, verification workers, remote browsers, Postgres upgrade path. |
| **Phase 7** | Reliability & operator tooling | Recovery loops, dead-session detection, stale alerting, operator dashboards, scheduled jobs, web surface. |

### Risk-Based Scoping Protection

The biggest threat to this project is not any single technical challenge — it is **scope creep under solo-operator conditions**. With no other humans to apply scope discipline, the operator must be protected from themselves. This section names the enforcement mechanisms.

#### Technical Risks

| Risk | Mitigation |
|---|---|
| **Upstream velocity stalls** (OMC or `clawhip` abandoned) | Adapter layer already mandated (§Infrastructure Platform). Fork boundary is explicit. If an upstream goes unmaintained for 90 days on a load-bearing feature, the adapter's shape allows a local reimplementation without touching the registry or orchestrator code. |
| **Upstream semantic drift** (not just API-shape drift) — e.g., OMC task model changes from pull-based to push-based, or `clawhip` alters delivery-ordering guarantees | Adapters absorb API shape changes but **cannot** absorb behavioral contract changes. Mitigation: aggressive version pinning (no auto-upgrade of upstream forks); weekly upstream-changelog review; integration test suite captures current behavioral contracts so any upstream bump that breaks them fails CI loudly before deploy. |
| **The three architectural commitments prove impossible to enforce under real workloads** (snapshot, single-writer, idempotency) | Each has a specific CI test (§Technical Success). If the test cannot be made green on a representative load, Phase 1 slips; it does not ship weaker. |
| **Registry becomes the bottleneck or corruption surface** | Append-only event log + daily JSONL files + SQLite WAL means backup/restore is trivial. Worst-case recovery path: human inspects the event log, truncates to a known-good offset, restarts. Documented in README. |
| **Claude Code CLI becomes unavailable or changes incompatibly** | Worker wrapper is an adapter boundary; the CLI is not called directly from the orchestrator. A scripted-stub worker is shipped as part of the separability test suite and can stand in as a degraded runtime. |

#### Market Risks

Most "market risk" categories don't apply — no market to risk. One narrow concern:

| Risk | Mitigation |
|---|---|
| **Upstream API TOS drift** (Anthropic / OpenAI / Google / Telegram / GitHub change terms incompatibly) | Already tracked as Risk #8 in Domain Requirements. Quarterly manual review; no auto-mitigation. |
| **MCP as capability contract loses industry momentum** | The bet is on MCP-the-pattern, not MCP-the-Anthropic-project. If MCP forks or fragments, the platform's adapter boundaries are positioned to translate to whatever consolidates next. |

#### Resource Risks (solo-operator specific)

| Risk | Mitigation |
|---|---|
| **Operator capacity drops** (day job demands, health, motivation) | Scope contracts, not expands. Phase 1 is the *only* phase that must ship. Phases 2–7 are explicitly optional; if the operator abandons everything post-Phase-1, the MVP still stands alone. |
| **Story complexity exceeds 1-day-operator-attention rule** | Story is rejected or decomposed. If decomposition fails, the feature is cut or moved to a later phase. |
| **Operator scope creep** (i.e., *"while I'm in there, let me also…"*) | §Non-goals enforced hard. Every scope-expansion request must be written as a separate post-Phase-1 issue. The PRD is the scope contract; the operator signs it with their own name. |
| **Bootstrap deadlock** (Phase 1 can't be built with Phase 1's tools because Phase 1 isn't built yet) | Acknowledged explicitly: the pre-bootstrap segment of Phase 1 stories is bootstrapped manually (traditional solo development with Claude Code in interactive mode, no orchestrator). From the bootstrap moment onward, remaining stories run through the platform itself. Dogfooding is the acceptance test of readiness. |
| **API spend exceeds operator budget during heavy development** | Per-task budget caps already in Domain Risk #1. Operator sets a monthly ceiling; `budget_exceeded` events halt autonomous work until the operator approves extension. |

### Bootstrap Milestone — when does the platform start building itself?

Bootstrap is **not** measured by story count (there is no denominator until stories are decomposed; any percentage would be invention). Bootstrap is a **binary observable checkpoint**:

> **Bootstrap complete** = the first end-to-end Journey 1 run executed *by the platform*, where the task being executed is itself a real Phase 1 story:
> 1. The operator submits a Phase 1 story as a `/task` from Telegram (not from a dev shell).
> 2. The in-progress platform plans, executes, verifies, and commits the story with only `/approve` interventions.
> 3. A merged PR results.
> 4. Bonus credit: the operator force-reboots the host mid-execution and confirms the task resumes to completion (proving J3 alongside J1).
>
> No percentage, no estimate. Either the platform can build one of its own features through its own control plane, or it can't. This binary checkpoint is the single most motivating milestone in the project, and it reshapes the remaining Phase 1 velocity when it lands.

## Functional Requirements

This section is the **capability contract** for the entire product. Any feature not listed here will not ship unless explicitly added here via an amendment. Downstream epics, stories, and acceptance criteria must cite at least one FR id.

*Actor conventions: **Operator** (the human user), **Platform** (the overall system), **Registry** (task + session registry service), **Orchestrator** (OMC or its adapter), **Worker** (Claude Code worker or its wrapper), **Event Bus** (`clawhip`), **Telegram Bot** / **Console Client** (control surfaces). "Can" = must-support capability.*

### Task Lifecycle Management

- **FR1.** Operator can submit a task via free-text description from Telegram or Console Client, optionally including a repository target and a free-text hint.
- **FR2.** Platform can plan a submitted task, producing a stepwise plan visible to the Operator before execution begins.
- **FR3.** Platform can execute a planned task autonomously, performing file edits, running tests, committing changes, and opening pull-request drafts.
- **FR4.** Operator can retrieve the full current state of any task in a single response, including current step, last event, last agent action, and available next commands — without relying on chat scrollback.
- **FR5.** Operator can retrieve an LLM-summarized digest of a task's recent events (not a raw log dump).
- **FR6.** Operator can retrieve the raw typed event stream of a task for debugging.
- **FR7.** Operator can approve, reject, stop, or retry a task at any approval or blocker checkpoint, with an optional free-text hint injected into the orchestrator's next planning pass.
- **FR8.** Platform can transition tasks through explicit lifecycle states (`created`, `queued`, `planning`, `awaiting_approval`, `executing`, `verifying`, `blocked`, `completed`, `failed`, `stopped`) and record each transition as a typed event.
- **FR9.** Platform can emit a structured completion summary on task completion, containing file count, line count, test count, CI state, and blockers encountered.
- **FR10.** Platform can auto-create a pull-request draft when a task reaches a green-tests state and completes a repo-mutating flow.

### Control Surfaces — Telegram and Console

- **FR11.** Telegram Bot can authenticate incoming messages against an allowlist of Telegram user ids; non-allowlisted senders receive no response and are logged as rejected.
- **FR12.** Console Client can perform every task-lifecycle command available via Telegram (full surface parity); no operator capability is Telegram-only.
- **FR13.** Operator can bind a Telegram thread to a task id such that subsequent progress events for that task deliver to the same thread.
- **FR14.** Platform can deliver approval requests as discrete messages containing risk class, pre-check results, diff summary, and the exact commands accepted.
- **FR15.** Platform can deliver blocker notifications containing the blocked-since timestamp, last event, last agent action, and the enumerated list of available operator commands.
- **FR16.** Platform can deliver a proactive morning summary message whenever a host restart occurred during an overnight task, stating timestamp, events replayed, and replay duration.
- **FR17.** Operator can issue a health-check command (`/ping`) that returns registry status, worker status, event-bus queue depth, and platform version.
- **FR17a.** Operator can query the current runtime/provider owning a specific task via `/agent <task-id>`. Phase 1 returns a single provider (Claude Code); the capability exists to future-proof Phase 5 multi-runtime.
- **FR17b.** Operator can inspect the agent's reasoning breadcrumbs for a task — emitted planning rationale, retry justifications, rejected hypotheses, and tool-call arguments — via a structured event subtype (`agent.reasoning.*`) that flows through the same typed event stream as lifecycle events. The `/logs` digest and `/status` reconstituted state must surface at least the last reasoning breadcrumb in human-readable form. Phase 1 supports reasoning capture for the Claude Code worker; other runtimes bind this capability via their adapters in later phases.

### Event System

- **FR18a.** Worker and Orchestrator can emit typed events into the Event Bus via a dedicated MCP surface.
- **FR18b.** Platform must not interpret direct stdout from any service as a source of execution state; all state transitions are read from typed events on the Event Bus. Enforced by linter + code review (see §Technical Success).
- **FR19.** Event Bus can route emitted events to registered sinks, including the Telegram sink in Phase 1.
- **FR20.** Platform can persist every emitted event into an append-only event log with sufficient metadata to reconstruct task and session state.
- **FR21.** Platform can version every event with a `schema_version` field and can refuse ingestion of events with unknown `(event_type, schema_version)` combinations, emitting `event.unknown_schema`.
- **FR22.** Platform can execute a migrator tool that reads an old-version event log and emits equivalent new-version events into a fresh log, archiving the original.
- **FR23.** Event Bus can expose the recent event stream and route diagnostics as a read-only resource via MCP.

### Persistence and Recovery

- **FR24.** Registry can persist task and session state such that the state survives host restart, container restart, and Telegram bot process restart with zero loss of in-flight work.
- **FR24a.** Platform can detect service-level failure via explicit signals: (a) Docker container exit, (b) Worker heartbeat timeout beyond a configured interval, (c) Telegram bot webhook delivery failure threshold, (d) explicit operator `/stop`. On detection, Platform can trigger the appropriate recovery path (container restart, session reconnect, sink failover, or graceful halt) and emit the corresponding typed event (`service.crashed`, `session.heartbeat_timeout`, `sink.delivery_failed`, `task.stop_requested`).
- **FR25.** Registry can periodically capture event-log snapshots so that startup replay of any session completes within the system's startup budget, even at elevated event counts.
- **FR26.** Registry is the sole writer to persistent task and session state; no other service can mutate that state except by appending events to the event log.
- **FR27.** Platform can hold a Worker's worktree lock through a blocked task's entire waiting period, releasing only on operator `/stop` or `/retry` resolution.
- **FR28.** Platform can dedupe incoming control commands by a client-generated idempotency key, returning the prior result on collision and never producing duplicate task execution on retry or network partition.
- **FR29.** Platform can reattach a Worker to its assigned session and worktree after a mid-execution restart, resuming from the last committed event, and emit `session.reconnecting` and `task.execution.resumed` events.
- **FR30.** Worker can perform file edits atomically such that a mid-write host interruption leaves the filesystem in a consistent state on resume.

### Runtime Execution

- **FR31.** Orchestrator can drive a task from plan through execution through verification through completion, delegating atomic work steps to the Worker via MCP contracts.
- **FR32.** Worker can register itself with the Session Registry on startup, emit lifecycle events (`session.started`, `session.heartbeat`, `session.finished`), and acquire an exclusive worktree lock.
- **FR33.** Worker can obtain task detail as a read-only MCP resource from the Task Registry; Worker does not write task state directly.
- **FR34.** Platform can swap the default Worker for an alternative Worker implementation (including a scripted-stub Worker that emits canned events) via a single configuration change — one environment variable naming the worker image — with no changes required to Orchestrator or Registry source code, DI wiring, or MCP server definitions. The swapped Worker must satisfy the same MCP surface contract (`task-registry` read, `session-registry` heartbeat, `clawhip-bridge` emit).
- **FR35.** Platform can swap the default Orchestrator for an alternative Orchestrator implementation (including a pass-through null orchestrator) via a single configuration change, with no changes required to Registry, Event Bus, or Worker source code, DI wiring, or MCP server definitions.
- **FR36.** Worker can participate in approval-gated flows by emitting `task.awaiting_approval`, holding its lock, sleeping on a conditional wait, and resuming on an `approval.*` event.

### Policy and Security

- **FR37.** Platform can classify actions into capability tiers (Tier 0 read-only, Tier 1 bounded write, Tier 2 repo mutation, Tier 3 high-risk) and enforce tier access at the MCP surface boundary.
- **FR38.** Platform can require an explicit operator approval event for any Tier 3 action before it is performed; Phase 1 supports at minimum `git push` as a gated Tier 3 action class.
- **FR39.** Platform can run a pre-commit validation hook on every Worker-authored commit that blocks changes to sensitive paths (`.env*`, `secrets/`, `*.pem`, `*.key`, `*.credentials*`), worktree-boundary violations, and commit-message injection patterns.
- **FR40.** Platform can run a license-scan step on every agent-generated commit before any push; on license-incompatibility detection, the Platform emits `task.license_flagged` and blocks the approval gate with a specific reason code.
- **FR41.** Operator can override a license flag with an explicit `/approve --override license` command; the override is recorded as an auditable event.
- **FR42.** Platform can emit a `secret.accessed` event for every access to a configured secret, recording actor, scope, and timestamp.
- **FR43.** Platform can sanitize typed events, snapshots, artifacts, and logs such that no plaintext secret value is ever persisted.
- **FR44.** Platform can enforce a per-task compute/token budget and emit `task.budget_exceeded` when reached, halting further autonomous work until the Operator approves an extension.
- **FR45.** Platform can sanitize operator-provided task input to prevent command injection into shell, git, or MCP surfaces.

### Deployment and Operations

- **FR46.** Operator can deploy the full Platform stack to a Linux VPS host and a macOS host using a single `docker compose up` command with an `.env` file as the only per-host configuration.
- **FR47.** Platform can complete time-to-first-task within the system's deployment budget, from a clean host, on both deployment targets.
- **FR48.** Operator can rotate secrets (bot tokens, API keys) via environment-variable update and container reload; no source-code change is required for rotation.
- **FR49.** Platform can expose structured JSON logs on stdout from every service, independent of the application event stream.
- **FR50.** Operator can run a schema migrator as a one-shot container command to evolve the event-log schema between major versions; the running Platform is shut down during migration.
- **FR51.** Platform can package Docker images for every Platform-owned service and publish them to a registry; upstream-fork images are pinned by digest.
- **FR52.** Operator can upgrade the Platform by updating image tags in the compose file and running `docker compose up -d`; persistent data volumes are preserved across upgrades.

### Completeness / Altitude Record

- Every capability in §Journey Requirements Summary maps to at least one FR.
- Every item in §MVP Feature Set maps to at least one FR.
- Every architectural commitment (snapshot, single-writer, idempotency) appears as an FR (FR25, FR26, FR28).
- Every separability test (S-1, S-2, S-3) is supported by a matching capability FR (FR34, FR35).
- No FR mentions a specific framework, DB engine, language, message format, or file path — all are capability-level, not implementation-level.
- **56 FRs across 7 capability areas.** Slight over on FR count (target was 20–50) because this is a genuinely high-complexity infrastructure project; capability-area count of 7 is within the 5–8 target.

**Traceability contract:** every downstream epic, story, and acceptance criterion must cite at least one FR id. Any capability not in this list will not ship unless explicitly added here via an amendment PR.

## Non-Functional Requirements

Selective: categories that don't apply (Accessibility, growth-sense Scalability) are omitted. Where a Success Criteria KPI already defines a target, the NFR cites the KPI rather than restating.

### Performance

- **NFR-P1.** Return-to-flow display latency: <5 s p95 from client open to current-state display, over 30 sample sessions. (Traces KPI #1a.)
- **NFR-P2.** Operator latency: <2.5 s p95 task-create → Telegram ack over 3×100 sequential submissions; all three batches must clear threshold. Fast-path alternative on degraded CI network: <2.0 s p95 to `clawhip` event emit. (Traces KPI #5.)
- **NFR-P3.** Registry startup replay: <5 s for any session of up to 10K events via periodic snapshots. (Traces KPI #8.)
- **NFR-P4.** Time-to-first-task: <30 min from clean Linux VPS host or macOS host to first completed task via `docker compose up` on representative-spec CI runner. (Traces KPI #4.)
- **NFR-P5.** Per-task compute budget: platform must enforce and emit `task.budget_exceeded` within 5 s of a configured token or dollar ceiling being reached. No cost loop exceeds the ceiling by more than 10%.

### Reliability

- **NFR-R1.** Restart recoverability: 100% of in-flight tasks recoverable after forced `docker compose restart` (Linux) or `docker stop --signal SIGKILL` (macOS) — verified by test script that kills the host at each task lifecycle phase. (Traces KPI #2.)
- **NFR-R2.** Re-work avoided: zero tasks lost to restart or crash per calendar month. Continuously verified in CI via a **synthetic-crash-injection harness** that kills the host at each lifecycle phase, replays the event log, and asserts 100% task-state reconstruction with no duplicate events. Monthly production audit-log review confirms the CI result held in the wild. Any non-zero count (CI failure or production audit finding) is a Sev1 regression. (Traces KPI #3.)
- **NFR-R3.** Control-surface health: Telegram bot + console API availability ≥99% of wall-clock hours on the chosen deployment target, excluding planned upgrades. (Traces KPI #12.)
- **NFR-R4.** Duplicate-task rate under retry storm: 0 duplicate executions per 100 concurrent duplicate submissions of the same command (idempotency replay test). (Traces KPI #9.)
- **NFR-R5.** Failure detection: Service-level failures (container exit, worker heartbeat timeout, webhook delivery failure) must be detected and emit the corresponding typed event within 60 s of the underlying condition. (FR24a.)
- **NFR-R6.** Unattended completion rate: ≥80% of weekly overnight submissions complete without any human action beyond `/approve` responses, measured weekly rolling. (Traces KPI #7.)

### Security

- **NFR-S1.** Secret hygiene: zero plaintext secret values persisted in event logs, snapshots, or artifact storage. Enforced by secret-scanner pre-commit hook + runtime log sanitizer. (Traces KPI #11, FR42, FR43.)
- **NFR-S2.** Secret rotation: all configured secrets (Telegram bot token, GitHub PAT, Anthropic API key, Docker registry credentials) rotatable in <5 min via `.env` update + `docker compose up -d`, without source-code changes. (FR48.)
- **NFR-S3.** Auditability: every Tier-3 action, every secret access, and every operator decision is emitted as a typed event with actor, scope, and timestamp, queryable via the registry. (FR37, FR38, FR42.)
- **NFR-S4.** Allowlist enforcement: non-allowlisted Telegram user ids receive no response from the bot. The rejection itself is recorded as a typed event. (FR11.)
- **NFR-S5.** Command injection prevention: operator-supplied free-text in task submissions cannot escape into shell, git, or MCP invocation contexts. Verified by a fuzz-test suite covering at minimum: null bytes, shell metacharacters, nested quoting, directory traversal sequences, ANSI escapes, Git reference-name injection. (FR45.)
- **NFR-S6.** Capability-tier enforcement: Tier-3 actions cannot be executed without a matching approval event; verified by negative-test that asserts `permission_denied` on an attempt to trigger a Tier-3 action without approval. (FR37, FR38.)
- **NFR-S7.** Network trust boundary: services inside the docker-compose network communicate without mTLS in Phase 1; external ingress is limited to Telegram webhook + SSH. No public-network exposure of the registry HTTP API, MCP transports, or database ports.
- **NFR-S8.** License contamination prevention: license-scan must run on every agent-generated commit before push; incompatibility blocks the approval gate with a specific reason code. Override requires explicit `--override license` flag producing a distinct audit event. (FR40, FR41.)

### Observability

- **NFR-O1.** Every task state transition emits a typed event — zero stdout-parsing regex in the task lifecycle path, enforced by linter + code review gate. (Traces KPI #6, FR18b.)
- **NFR-O2.** Structured JSON logs on stdout from every service, independent of the application event stream, so that container-level log aggregation works without interfering with event-driven state. (FR49.)
- **NFR-O3.** Operator can reconstruct full task history from the event log at any time; operator can obtain an LLM-summarized digest via `/logs`. Raw event stream retrieval also available for debugging. (FR5, FR6.)
- **NFR-O4.** Health check command (`/ping`) returns registry status, worker status, event-bus queue depth, and platform version in a single response within 2 s. (FR17.)
- **NFR-O5.** Event schema integrity: any `(event_type, schema_version)` combination not registered with the Platform halts ingestion and emits `event.unknown_schema`. Silent event drops are a P0 bug class. (FR21.)
- **NFR-O6.** Agent-reasoning breadcrumbs emitted as `agent.reasoning.*` events must be non-sensitive by default: no secret values, no raw file contents beyond context snippets, no embedded credentials. They pass through the same runtime log-sanitizer as all other events (NFR-S1). If a breadcrumb cannot be sanitized safely, it is replaced with a redaction stub citing `reason=sensitive_content_suppressed`.

### Maintainability

- **NFR-M1.** Upstream fork boundary: OMC and `clawhip` integrated only via adapter shims. No source from those specific projects is vendored into registry, orchestrator, or worker code. General utility libraries (small, single-purpose, permissively licensed) may be vendored if doing so is materially simpler than a package-manager dependency — vendoring is logged in a `VENDORED.md` manifest reviewed on every Phase transition. Dependency-graph CI check enforces the upstream-fork ban specifically.
- **NFR-M2.** Upstream version pinning: no auto-upgrade of upstream forks; version bumps are explicit and gated by a behavioral-contract integration test suite that fails CI on semantic drift.
- **NFR-M3.** Event schema evolution: within a major schema version, only additive changes are permitted (new event types, new optional fields). Breaking changes require a migrator container (`docker compose run --rm migrator <from>-to-<to>`) and explicit Platform downtime.
- **NFR-M4.** Runtime decoupling: replacing the default Worker with a scripted-stub requires a single-env-var change and no source-code modification to Orchestrator or Registry. Verified by separability test S-1. (FR34.)
- **NFR-M5.** Orchestrator decoupling: replacing the default Orchestrator with a pass-through null implementation requires a single-env-var change and no source-code modification to Registry, Event Bus, or Worker. Verified by separability test S-3. (FR35.)
- **NFR-M6.** Story decomposition discipline: every story must cite ≥1 FR id and must fit within ≤1 working day of operator attention (regardless of total agent compute time). Stories exceeding this bound must be decomposed or deferred.
- **NFR-M7.** Documentation: the operator-facing README contains a 10-line quickstart, a deployment checklist for both targets, a backup/restore procedure for the event log + registry, and a runbook for the schema migrator.

### Data-Volume Scalability

(Scalability *in the solo-operator personal-infrastructure sense* — not growth / concurrency / multi-tenant, which are out of scope. Concerns: event-log growth, snapshot cadence, disk footprint over months of use.)

- **NFR-SC1.** Event-log volume: registry startup-replay performance (NFR-P3) holds as the event log grows, via periodic snapshots that cap replay work per session.
- **NFR-SC2.** Disk footprint: platform can run on a 10 GB data volume for at least 6 months of typical operator activity, assuming weekly snapshot + monthly event-log rotation to compressed cold archive. Actual footprint monitored via `/ping` extensions in Phase 2.
- **NFR-SC3.** Single-task concurrency: Phase 1 supports one active task per worker instance; multi-task parallelism is explicitly deferred to Phase 6. This is both an NFR (we will not support it) and a documented scope boundary.

### Integration (refers to existing sections)

No new NFRs here. Integration requirements live in §Domain Requirements (external systems table, required internal contracts) and §Infrastructure Platform (API Surface — HTTP, MCP, CLI). Enforcement: adapter boundaries are mandatory (NFR-M1), schema stability is mandatory (NFR-M3), MCP is the sole capability contract for tool exposure (FR37).

---

## Phase 2 Scope Extension — Observability Phase

> **Amendment added:** 2026-05-15.
>
> **Selected via:** structured brainstorming session — see [`phase-2-brainstorming.md`](./phase-2-brainstorming.md) for the 78-idea exploration across 9 orthogonal domains, the 14-candidate scoring matrix, and the three-narrative convergence. The Observability Phase narrative was chosen for **lowest regret + compound value**; it closes Phase 1's explicitly-acknowledged largest gap (metrics + distributed tracing) and is structured as 6 small-to-medium epics so a calendar interruption never strands the work in a broken half-state.

### Phase 2 Vision

> *Phase 1 shipped a system whose state I can rebuild from the log but whose live behavior I can't yet see. Phase 2 fixes that, so every later phase ships into a stack I can actually reason about.*

### Phase 2 Architectural Commitments (all additive)

All Phase 2 additions preserve Phase 1's architectural invariants. Specifically:

- **Single-writer (FR26) unchanged.** Every Phase 2 service is a *read-only subscriber* of the event log (`metrics-subscriber`, litestream replication, the budget-enforcement supervisor); `registry-state` remains the sole writer of persisted state.
- **Envelope schema bumps 1.0.0 → 1.1.0 additively.** Adds a non-optional `trace_id: UUIDv7` field to every emitted event. No existing fields are modified, renamed, or removed; consumers at v1.0.0 can still parse v1.1.0 envelopes (ignoring the unknown field) for the duration of the cutover window.
- **MCP transport remains stdio-only.** Remote-MCP (HTTP/SSE) is explicitly **deferred to Phase 3** — Phase 2 changes nothing about MCP transport.
- **No `anthropic` SDK in platform code.** The worker-wrapper boundary is the only place that touches the Claude Code subprocess; Phase 2 does not change this.
- **Additive-only schema evolution (NFR-M3).** All new event types are additions; no migrators required for the schema_version bump (the additive `trace_id` field is the only change).

### Phase 2 Functional Requirements

#### γ — Supply-chain hardening (Epic 8)

- **FR53.** Every Platform-published Docker image carries a cosign keyless signature attesting to its provenance via the build's GitHub Actions OIDC identity. Signatures are stored as OCI artifacts alongside the image in GHCR.
- **FR54.** Every Platform-published Docker image carries a SLSA Build Level 2 provenance attestation generated by GitHub Actions, traceable to a specific commit SHA + workflow run. The attestation is verifiable with `cosign verify-attestation`.
- **FR55.** Every Platform-published Docker image carries a CycloneDX SBOM attached as an OCI attestation, listing direct + transitive dependencies with pinned versions and SPDX license identifiers. The SBOM is generated by `anchore/sbom-action` during the release workflow.
- **FR56.** Operator deployment paths resolve image references by **digest** (`<image>@sha256:...`) rather than mutable tags (`:latest`, `:v0.X.Y`). Phase 2 adds `OMB_IMAGE_DIGEST_<service>` environment variables to `.env.example`; tag-based references remain supported as a fallback during the Phase 2 cutover and are deprecated in Phase 3.
- **FR56a.** A `cosign verify` step in the operator deployment recipe (`just verify-images`) fails the deploy if any image signature or attestation does not verify against the expected GitHub Actions OIDC issuer; verification failure emits a `deployment.signature_rejected` event.

#### α — `trace_id` propagation kernel (Epic 9)

- **FR57.** Event envelope `schema_version` increments from `1.0.0` to `1.1.0`, retaining all existing fields and adding a non-optional `trace_id: UUIDv7` field on every emitted event. The schema-registry update is the canonical record of the bump.
- **FR58.** Every Platform entry point binds a `trace_id` to the structlog context *before* any business logic runs:
  - **registry-api** pulls from `X-Trace-Id` header; mints a fresh UUIDv7 if absent and logs at WARNING.
  - **telegram-gateway** `AllowlistMiddleware` injects `trace_id = f"tg:{update_id}"` (deterministic per inbound update).
  - **console-cli** mints `new_request_id(clock=...)` at command entry and threads it into the command envelope.
  - **MCP tool handlers** receive `caller_trace_id` as an explicit input field (not ambient context) and propagate it when calling downstream clients.
- **FR59.** The Claude Code worker subprocess receives its `trace_id` via a `--trace-id` CLI flag, propagated by `worker-wrapper` into every event the worker emits through the MCP bridge (`clawhip-bridge.emit_*` tools).
- **FR59a.** Operator can query the complete causal chain for any historical command via `/trace <trace-id>` (Telegram) or `oh-my-bmad trace <trace-id>` (console), returning every event that carried that `trace_id` across all services. The `GET /v1/trace/{trace_id}` response includes an `X-Trace-Truncated: true` header when more rows exist past the returned page, and an `X-Trace-Has-Synthetic: true` header when any returned event carries a back-filled synthetic `trace_id` (Story 9.7 pass-1 Q1 decision: migrator back-fill sets `trace_id = request_id` for pre-1.1.0 events; synthetic traces have inherent collision risk across retried requests). The `?limit=` (default 500, max 2000) and `?after_event_id=` cursor params enable paginated walks over long chains.

#### β — `metrics-subscriber` service (Epic 10)

- **FR60.** Platform ships a new workspace member `services/metrics-subscriber/` that tails the JSONL event log as a read-only subscriber (using the same `EventLogReader` discipline as `telegram-gateway` and `console-cli`) and computes Prometheus-format counters, gauges, and histograms from existing event types — **without adding any instrumentation code to any other service**.
- **FR61.** The `metrics-subscriber` exposes a `/metrics` HTTP endpoint serving Prometheus text exposition format. The endpoint is **internal-only** (reachable only inside the docker-compose network); it is NOT exposed via public ingress. Operator may scrape via SSH-tunneled `curl` or via an operator-deployed Prometheus instance on the same host.
- **FR62.** At minimum, the `metrics-subscriber` computes:
  - Task counters by status (`pending`, `planning`, `executing`, `awaiting_approval`, `completed`, `failed`, `stopped`).
  - Session counters by lifecycle phase (`started`, `heartbeat`, `finished`, `heartbeat_timeout`, `reconnecting`).
  - Idempotency-cache hit rate (`CacheHit` vs `factory ran`).
  - Capability-tier deny counts by tier + boundary.
  - `secret.accessed` event counts by actor.
  - Event-log append rate (events/sec) windowed over 1m / 5m / 1h.
  - Per-task token-spend gauges from `task.budget_*` events.
- **FR62a.** The `metrics-subscriber` is a new entry in `tests/separability/` — adding it does not require modifying any existing service's source code; verified by separability test S-4 (added in Phase 2).

#### ξ — Approval-request inbox thread + non-repudiation signature (Epic 11)

- **FR63.** Operator can open a pinned "approval inbox" Telegram thread via the `/approvals` command. After the inbox exists, subsequent `task.approval_requested` events deliver into this thread instead of the originating task thread, with a link back to the task thread for context.
- **FR64.** Every `approval.granted` event is accompanied by a `task.approval_signed` event carrying an HMAC-SHA256 signature computed over `(task_id || action || timestamp || actor_id)` using an operator-local key from `OPERATOR_HMAC_KEY` in `.env`. The signing key never leaves the operator's host; the HMAC value travels with the event.
- **FR65.** Operator (or future forensic auditor) can verify the HMAC of any historical approval offline via `just verify-approval <event-id>`, comparing the stored HMAC against a fresh recomputation using the local key. Mismatch indicates either key rotation since the approval or tampering — both produce a structured error pointing to the next investigation step.
- **FR65a.** Key rotation: operator can rotate `OPERATOR_HMAC_KEY` via `.env` update; a `key.rotated` audit event records the rotation timestamp. Pre-rotation approvals remain verifiable only via the prior key (operator's responsibility to retain it for audit-window duration).

#### κ — Per-task budget enforcement loop (Epic 12)

- **FR66.** `worker-wrapper` subscribes to `task.budget_exceeded` events for the task currently executing in its supervised Claude Code subprocess. On receipt, `worker-wrapper` sends `SIGTERM` to the subprocess within **5 seconds**; if the subprocess does not exit within an additional 5 seconds, `SIGKILL` follows.
- **FR67.** Platform emits a `task.budget_enforcement_triggered` event containing: the budget threshold (token / dollar), the actual spend at trigger, the action taken (`subprocess_terminated`), and the post-trigger task transition (`failed` or `awaiting_approval`, per per-task policy declared at submission).
- **FR68.** Operator can override a budget-exceeded enforcement before its 5-second grace window via `/approve --override budget <task-id>`. The override extends the budget by a operator-specified delta and emits a `budget.override` audit event. Overriding after termination requires explicit task retry via `/retry`.
- **FR68a.** Budget policy (token-ceiling, dollar-ceiling, action-on-exceed) is declared per-task at submission and stored in the task envelope; default policy is operator-configurable via `.env`.

#### δ — WAL replication via litestream sidecar (Epic 13)

- **FR69.** Platform supports an optional `litestream` sidecar container in `docker-compose.yml` that replicates the `oh-my-bmad-data` named volume's SQLite WAL stream to an operator-configured S3-compatible endpoint (AWS S3, Backblaze B2, Cloudflare R2, self-hosted MinIO).
- **FR70.** Operator configures replication target via `OMB_LITESTREAM_CONFIG_PATH` in `.env`, pointing to a litestream YAML configuration file mounted into the sidecar. Absent configuration disables the sidecar entirely (no replication, no startup error — the platform stack runs without it).
- **FR71.** Operator can restore the registry state from a litestream snapshot via `just restore-from-litestream <bucket>/<key>`, producing a fresh `oh-my-bmad-data` volume that passes `just bootstrap-verify`. The recipe orchestrates: stop stack → recreate volume → run litestream restore → start stack → verify health.
- **FR71a.** Replication is read-only from the stack's perspective: the litestream sidecar opens the WAL file in shared-read mode; the single-writer invariant (FR26) is preserved because `registry-state` remains the sole DB writer.

### Phase 2 Non-Functional Requirements

#### Observability (extends §Observability — supersedes the Phase-2 deferral in NFR-O1's neighborhood)

- **NFR-O7.** `trace_id` correlation: every event emitted in Phase 2 and beyond carries a non-null `trace_id`. An operator query for "all events with `trace_id=X`" returns the complete causal chain across registry-api, registry-state, telegram-gateway, worker-wrapper, and the three MCP servers, ordered by `emitted_at_monotonic_ns`.
- **NFR-O8.** Metrics-derived observability: the `metrics-subscriber`'s `/metrics` endpoint completes in <100ms p95 for a single Prometheus scrape. Metrics are derived from the event log tail only; no instrumentation is injected into existing services. Verified by a CI scrape benchmark at fixed runner size.
- **NFR-O9.** Live reasoning-breadcrumb view: operator can request the live reasoning-breadcrumb stream for an in-flight task via `/breadcrumbs <task-id>` and receive sanitizer-cleared breadcrumbs within 2s of emission. Breadcrumbs respect the same `secret-hygiene` sanitizer that protects log lines (NFR-S1, NFR-O6).
- **NFR-O10.** **Phase-2 observability primacy retained.** Typed events on the event spine remain the **primary** observability stream (NFR-O1 stands). Metrics + traces are *derived* projections, not parallel instrumentation paths. The `metrics-subscriber` ingests the event log; it does not call into any other service for data. (This explicitly forbids the "OTel everywhere" anti-pattern.)

#### Security (extends §Security)

- **NFR-S9.** Image-signature verification: operator deployment paths verify cosign signature + SLSA-L2 attestation against the GitHub Actions OIDC issuer before pulling an image. Verification failure refuses the pull and emits a `deployment.signature_rejected` event. (FR53, FR54, FR56a.)
- **NFR-S10.** Non-repudiation key isolation: the operator-local `OPERATOR_HMAC_KEY` (FR64) is stored only in the `.env` file and the running process environment. The key is never persisted in events, logs, snapshots, or the registry. HMAC verification of historical approvals is **offline-capable** — does not require Platform services to be running.
- **NFR-S11.** SBOM attestation: every released image's CycloneDX SBOM is verifiable via `cosign verify-attestation`; CI rejects any release where SBOM generation or attachment failed. License-incompatibility detection on the SBOM blocks the release pipeline before publish (extends FR40 from build-time to publish-time).

#### Reliability (extends §Reliability)

- **NFR-R7.** Replication freshness: when the litestream sidecar is configured, replication lag from local WAL write to remote object-store materialization is <30 seconds p95, measured by an operator-driven `just litestream-lag-check` recipe. Lag exceeding this threshold for >5 minutes emits a `replication.lagging` event.
- **NFR-R8.** Budget enforcement latency: `task.budget_exceeded` event → Claude Code subprocess SIGTERM completion within 5 seconds p99 (FR66). Verified by a budget-enforcement integration test in `tests/integration/`.

### Phase 2 Out-of-Scope (deferred to Phase 3+)

Per the brainstorming convergence (see [`phase-2-brainstorming.md`](./phase-2-brainstorming.md) §Rejected):

- **η Remote-MCP transports** (HTTP/SSE). No remote-worker use case in Phase 2 horizon; stdio remains canonical.
- **θ / ι Browser-automation plane** (either as a 4th worker tool or as a 4th operator surface). High effort, modest marginal value over Telegram + console for the operator's current workflow.
- **ζ Second CLI agent** (Codex / Gemini / GLM via the orchestrator-adapter shim). Adapter-contract validation is valuable but not pressing; deferred until a concrete second-runtime use case emerges.
- **μ Historical replay mode** (debug a past task in a sandboxed worker with mocked side effects). Zero composition with Phase 2 items; revisit in Phase 3 once observability is in place.
- **Translation-layer chat surfaces** (Discord / Matrix). Second-vendor chat would double maintenance load without changing agent behavior.

### Phase 2 Sequencing

| Order | Epic | Item | Effort | Why this order |
|---|---|---|---|---|
| 1 | **Epic 8** | γ Supply-chain hardening (FR53–FR56a) | ~3 days | Cheapest, highest-ROI; lands first so every later Phase-2 release ships through a hardened pipeline. |
| 2 | **Epic 9** | α `trace_id` propagation kernel (FR57–FR59a) | ~1 week | Unblocks every later epic that correlates events; envelope schema bump must precede subscribers that read the new field. |
| 3 | **Epic 10** | β `metrics-subscriber` service (FR60–FR62a) | ~1 week | Consumes α's `trace_id`; first new subscriber since Phase 1 — exercises separability test fixtures + recovery cursor. |
| 4 | **Epic 11** | ξ Approval inbox + signature (FR63–FR65a) | ~1 week | Most operator-visible UX; compounds with α (trace-id in inbox messages). |
| 5 | **Epic 12** | κ Budget enforcement (FR66–FR68a) | ~1 week | Composes with β — without metrics, enforcement is a black box. |
| 6 | **Epic 13** | δ WAL replication via litestream (FR69–FR71a) | ~3 days | Orthogonal; ships in parallel with any other epic. |

**Total estimated effort:** 6–8 weeks of solo-operator work (≈ Phase 1's 25-day cadence × 1.5).

### Phase 2 Success Criteria Extensions

Phase 2 success means **at minimum:**

1. **All FR53–FR71a implemented** and verified via the BMad workflow (sprint planning → create-story → validate-story → dev-story → code-review → testarch-automate/trace/nfr → retrospective per epic).
2. **NFR-O7 verified** — `/trace <trace-id>` returns coherent causal chains for 10 randomly-selected historical tasks (post-Epic 9 baseline).
3. **NFR-S9 verified** — `just verify-images` passes for the Phase 2 release; a deliberate signature tampering test fails the verification step.
4. **NFR-R7 verified** — `just litestream-lag-check` reports <30s lag for an end-to-end restore drill on a fresh host.
5. **Phase 2 retrospective produced** (per epic) following the Cat-6 "three falsifiable outputs" rule: wrong-assumption, single-process-change, deferred-item triage.
6. **Phase 1 invariants regression-free** — `tests/separability/`, `tests/crash-injection/`, `tests/idempotency/`, `tests/contract/`, `tests/arch/` all green at every Phase 2 epic boundary.

### Amendment Traceability

- **Brainstorming source:** [`phase-2-brainstorming.md`](./phase-2-brainstorming.md) (78 ideas, scored, sequenced, Narrative I selected).
- **Architecture impact:** future `architecture.md` extension will document the wiring of `trace_id` through the envelope, the new `metrics-subscriber` workspace member, the litestream sidecar topology, and the HMAC signing flow.
- **Implementation-readiness gate:** before Phase 2 implementation begins, `bmad-check-implementation-readiness` must validate that this PRD amendment + a Phase 2 architecture amendment + a Phase 2 epics/stories decomposition are aligned. Phase 2 sprint planning cannot start until the readiness report passes.
- **Phase boundary discipline:** every Phase 2 epic and story carries `phase: 2` in `sprint-status.yaml`. No `phase: 2` work merges to `main` until a Phase-2 gate ADR (`docs/adr/0003-phase-2-gate.md`, to be authored) is accepted.

— *Amendment by R2d2, 2026-05-15, via the BMad `bmad-edit-prd` workflow.*

---

## Phase 3 Scope Extension

> **Status:** Phase-3 PRD extension. Formalizes the operator-brainstorming convergence of 2026-06-03 (decisions D1–D4, captured in [`phase-3-plan.md`](./phase-3-plan.md), grounded in [`phase-3-scoping-brief.md`](./phase-3-scoping-brief.md)). Authored after Phase 2 shipped (v0.3.0, all Epics 1–13 `done`) and the Phase-3 readiness hardening landed (G-SEC-1 license-gate fail-closed, G-SEC-2 child-env allowlist — both merged, main green). FR/NFR numbering continues the canonical series (FR71a → FR72; NFR-O10 → O11; NFR-M7 → M8; NFR-S11 → S12).

**Theme:** the **MCP tooling fleet** — give the worker/orchestrator a set of first-class, stdio-only, tier-authz'd MCP tool servers, built on the Phase-2 spine (event-only telemetry, `trace_id`, supply-chain pipeline). Phase 3 lands **exactly five** new servers — `git`, `github`, `build`/`verification`, `memory`/`wiki`, `artifact` (D1) — plus a tests-first hardening warm-up that executes the FR56 digest-deprecation commitment and adds a mutation-testing gate.

**Resolved scope (operator brainstorming, D1–D4):**

- **D1 (IN).** Five servers only — `git`, `github`, `build`/`verification`, `memory`/`wiki`, `artifact`. The narrower five-server reading (roadmap) is the canonical Phase-3 boundary; the broader 11-server "Phase 3+" list stays a later-phase backlog.
- **D2 (OUT, deferred).** Remote-MCP transport (HTTP/SSE/streamable). MCP stays **stdio-only** this phase; no auth/rate-limit sub-project. The remote-MCP ADR remains a non-decision.
- **D3 (OUT, deferred to Phase 5).** Second CLI agent (Codex/Gemini/GLM). A single Claude Code runtime this phase.
- **D4 (entry point).** A **tests-first hardening warm-up epic FIRST** (FR77 digest-deprecation + the mutation-testing nightly gate), then the recipe-establishing `git` server, then the remaining four.

**Preserved invariants (carry from Phases 1–2 — non-negotiable):**

- **Single-writer (FR26) unchanged.** Every new server is a *read-only consumer* of the event log OR routes any mutation through the existing `registry-state` write path; none becomes a second DB writer. The `memory`/`wiki` and `artifact` stores are isolated backing stores (their own files), never the registry DB.
- **MCP transport remains stdio-only.** Every new server is a stdio MCP server; no `mcp.server.sse` / `streamable_http` is introduced. Remote-MCP stays deferred (D2).
- **Event-only telemetry (NFR-O1/O10) unchanged.** New servers emit typed events on the event spine; metrics remain *derived* in `metrics-subscriber`. No per-server instrumentation.
- **`trace_id` propagation (NFR-O7) unchanged.** Every new server stamps/propagates `trace_id` on every event it emits.
- **Tier-enforced authz (Epic 6) unchanged.** Every destructive tool (git `push`/history-rewrite, GitHub writes, `artifact` deletes) is **Tier-3 gated** through the existing approval flow.
- **Supply-chain (Epic 8 + G-SEC-1/2) unchanged.** New MCP servers ship as **wheels inside the base image** (`Dockerfile.base` `COPY mcp-servers/` + `uv sync --all-packages`) and are spawned as stdio subprocesses — they are NOT separate compose services or release-matrix images (mirrors the existing `clawhip-bridge`/`task-registry`/`session-registry`). Their supply-chain is therefore **inherited transitively** from the (cosign/SLSA-L2/CycloneDX-SBOM-signed, fail-closed-license-gated) base image; no new per-server signed image is introduced. Each runs under the child-env allowlist.

### Phase 3 Functional Requirements

#### σ — `git` MCP server (Epic 15)

- **FR72.** Platform ships a stdio MCP server `mcp-servers/git/` exposing structured git tools (`status`, `diff`, `log`, `branch`, `add`, `commit`, `push`). Read tools (`status`, `diff`, `log`, `branch`) are **Tier-1**; mutating tools (`add`, `commit`) are **Tier-2**; `push` and any history-rewrite are **Tier-3 gated** through the approval flow (negative test proves denial without approval). The server operates only within the active task worktree (no access outside it), emits typed `git.*` events for every mutating op, carries `trace_id`, and is a new separability entry (S-5). The server adds no instrumentation to any other service.

#### τ — `github` MCP server (Epic 16)

- **FR73.** Platform ships a stdio MCP server `mcp-servers/github/` for GitHub operations (create/list issues; create/update/list PRs; request reviews; comment). Read tools are **Tier-1**; all write tools are **Tier-3 gated** through the approval flow. Authentication uses a **scoped credential** (fine-grained PAT or GitHub App installation token, narrowed to the target repo) supplied via the child-env allowlist — never the broad inherited PAT (closes the G-SEC-2 `GITHUB_TOKEN` follow-up). The server generalizes the existing PR-draft adapter (Story 5.14) into a full GitHub surface, emits `github.*` events with `trace_id`, and is a new separability entry (S-6).

#### υ — `build`/`verification` MCP server (Epic 17)

- **FR74.** Platform ships a stdio MCP server `mcp-servers/verification/` that runs the project's build + test/verification recipes and returns structured results (pass/fail, captured logs, coverage summary). Execution is sandboxed to the active worktree. Tools are **Tier-2** (they run project code but perform no external mutation). The server emits `verification.*` events carrying the recipe invoked, exit status, and `trace_id`, and is a new separability entry (S-7). It adds no instrumentation to any other service.

#### φ — `memory`/`wiki` MCP server (Epic 18)

- **FR75.** Platform ships a stdio MCP server `mcp-servers/memory/` providing a persistent cross-task knowledge store backed by the filesystem + SQLite FTS5, exposing `read`/`search`/`write` tools. The store is single-writer-safe — it owns its own store file and **never** writes the registry DB (FR26 preserved). Read/search tools are **Tier-1**; the `write` tool is **Tier-2**. The server emits `memory.*` events with `trace_id` and is a new separability entry (S-8).

#### χ — `artifact` MCP server + store (Epic 19)

- **FR76.** Platform ships a stdio MCP server `mcp-servers/artifact/` plus a persisted build/run-output store (the "Artifact store — Phase 3" surface), exposing `put`/`get`/`list` tools over a **content-addressed**, local-FS backing store (no new external dependency, per the project's simplicity principle). `put` is **Tier-2** (`delete`, if exposed, is **Tier-3 gated**); `get`/`list` are **Tier-1**. Retention policy is operator-configurable. The server emits `artifact.*` events with `trace_id` and is a new separability entry (S-9).

#### ψ — Digest-deprecation execution (Epic 14)

- **FR77.** Platform executes the Phase-3 clause of **FR56**: the tag-based image-reference fallback (`:latest`, `:v0.X.Y`) is removed, and digest-pinned references (`OMB_IMAGE_DIGEST_<service>`, `<image>@sha256:...`) become the **sole** supported deploy path. `docker-compose.yml`, the operator deployment docs, and `just verify-images` are updated to digest-only resolution; any remaining tag-based reference emits a deprecation warning during the cutover window and is then dropped. Verified by a digest-only deploy passing `just verify-images` + `just bootstrap-verify` green.

### Phase 3 Non-Functional Requirements

- **NFR-O11.** Mutation-testing nightly gate: a nightly mutation-testing run (e.g. `mutmut` / `cosmic-ray`) executes over the platform-owned packages and publishes a mutation score. The runtime is evidence-justified by the now-available `metrics-subscriber` data. The gate threshold is set per the Epic-14 threshold decision; once set, a mutation score below threshold fails the nightly gate. Verified by the nightly CI run publishing a score artifact and enforcing the threshold.
- **NFR-M8.** Fleet separability: each of the five new MCP servers (FR72–FR76) is an **optional, swappable stdio member** — disabling it is a single change to the **MCP-client spawn configuration** (the server is a spawned subprocess, NOT a compose service, so this differs structurally from S-4 which toggles a compose service), with **no source-code modification** to any other service. Verified by new separability tests **S-5 (`git`), S-6 (`github`), S-7 (`build`/`verification`), S-8 (`memory`/`wiki`), S-9 (`artifact`)** in `tests/separability/`, continuing the S-1…S-4 series.
- **NFR-S12.** Fleet supply-chain + tier-authz: the new servers inherit supply-chain coverage **transitively from the signed base image** (they are wheels in it, not separate images) — cosign keyless signature + SLSA-L2 provenance + CycloneDX SBOM + fail-closed license gate (G-SEC-1) + child-env allowlist (G-SEC-2), with no new per-server image or release-matrix row. Every destructive tool across the fleet (git `push`/history-rewrite, GitHub writes, `artifact` `delete`) is **Tier-3 gated**; for each, a **negative test proves the tool is denied without an approval grant**. Verified by `just verify-images` green on the base image + per-server Tier-3-denial integration tests + per-server separability (NFR-M8).

### Phase 3 Out-of-Scope (deferred)

Per the operator-brainstorming convergence (D2–D4):

- **Remote-MCP transport** (HTTP/SSE/streamable) and its attendant auth + rate-limiting layer — deferred (D2); the remote-MCP ADR stays deferred.
- **Browser-automation plane** (4th worker tool or 4th operator surface) — deferred (roadmap: Phase 4); the browser-automation surface ADR stays deferred.
- **Second CLI agent** (Codex / Gemini / GLM via the orchestrator-adapter shim) — deferred to **Phase 5** (D3).
- Also out for Phase 3: the `workspace`, `docker-pool`, `db-schema`, `docs-research`, and `telegram-control-direct` servers, and historical replay mode (value-gated).

- **Phase boundary discipline:** every Phase 3 epic and story carries `phase: 3` in `sprint-status.yaml`. No `phase: 3` work merges to `main` until a Phase-3 gate ADR (`docs/adr/0009-phase-3-gate.md`) is accepted.

— *Amendment by R2d2, 2026-06-03, via the BMad `bmad-create-prd` workflow (Phase-3 extension; brainstorming convergence D1–D4).*
