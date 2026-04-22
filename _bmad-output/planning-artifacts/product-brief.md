# Product Brief: Autonomous Multi-Agent Development Platform

**Working title:** `oh-my-bmad` (platform-level project)
**Author:** R2d2
**Date:** 2026-04-20
**Status:** Draft v1 — north-star vision with Phase 1 MVP definition
**Classification:** Personal operator platform, self-hosted, Docker-deployable (VPS or local macOS)

---

## 1. Executive Summary

A self-hosted **autonomous software-development platform** that lets a single operator run, supervise, and redirect AI coding agents from **Telegram** (remote) or **local console** (at the workstation). The platform is not another AI coding agent — it is the **orchestration and event layer** above interchangeable CLI agents, starting with Claude Code and designed to absorb Codex, Gemini, GLM, and a browser-automation runtime as they come online in later phases. One `docker compose up` on a VPS or macOS host brings up the full control plane, execution worker, event bus, and registries.

The wager: treat **typed events and a persistent task registry as the source of truth**, and keep Telegram and console as pure human control surfaces. That decoupling is what makes sessions resumable, runtimes swappable later, and the whole thing operable from a phone.

---

## 2. Problem

Autonomous coding agents have arrived (Claude Code, Codex CLI, Gemini CLI, Aider, OpenHands). Each is strong inside its own session but weak at everything around it:

- **No durable state.** Restart the host, container, or bot — the task context evaporates.
- **Stdout-shaped telemetry.** Orchestrators parse raw terminal output; failures, approvals, and progress are fragile regex, not typed events.
- **Vendor-coupled harnesses.** Devin, Replit Agent, and OpenHands each bind you to one model/runtime; switching costs are high.
- **No native remote control.** Web UIs and IDE embeds require a laptop in front of you. A developer who wants to kick off a task from the subway, approve a risky step from a cafe, or triage a blocker from bed has no good option.
- **Browser automation mixed with code execution.** Letting the same agent loop that writes code also drive a live browser creates risk-profile and scaling conflicts.

The gap is an **operator-grade harness**: durable, event-driven, runtime-agnostic, messaging-app-native, and safe by default.

---

## 3. Product Vision

> An operating system for personal autonomous development. Telegram is the human interface; the event bus is the nervous system; the registries are long-term memory; one CLI agent is the v1 muscle, and more are swapped in later without replanning the spine.

One operator, from a phone or a console, can:

1. Submit an engineering task.
2. Watch it get planned, executed, verified, and committed autonomously.
3. Be pinged only for blockers, approvals, and completion summaries.
4. Restart or redeploy the host without losing in-flight work.

---

## 4. Target Audience

**Primary user:** the operator (one person — the author). Solo-developer autonomous workflows on personal projects.

**Use cases in scope for v1:**

- Overnight autonomous task runs kicked off from Telegram, reviewed in the morning.
- Desk-side interactive workflows via local console when the operator is at the Mac.
- Approval-gated risky actions (e.g. `git push` to protected branches) — never silent.

**Explicitly not in scope (audience-wise):** team/enterprise use, multi-tenant deployments, public SaaS. Design choices favor the single-operator case; multi-user generalization is a later question.

---

## 5. Key Differentiators

Based on a survey of the current landscape (Devin, OpenHands, SWE-agent, Replit Agent, ComposioHQ Agent Orchestrator, thepopebot), no competitor combines all four of these simultaneously:

| # | Property | Why it matters |
|---|----------|---------------|
| 1 | **Telegram-first control surface** + local console fallback | Mobile-native, globally available, zero extra app install; console mode covers desk-side flow. Competitors are web-UI- or IDE-coupled. |
| 2 | **Typed event bus (`clawhip`)** instead of stdout parsing | Reliable replay, filtering, approvals, and audit trail. Production-shaped telemetry, not regex scraping. |
| 3 | **Designed for swappable CLI agent runtimes** behind one capability contract | v1 ships Claude Code. Later phases plug in Codex, Gemini, GLM, and `claw-code` without touching orchestration or registry code. |
| 4 | **Browser automation designed as a separate plane** (from Phase 4) | Browser sessions get their own service, risk profile, policy, and scaling — not a tool call inside the coding agent's loop. |

Supporting properties: **MCP as the capability contract** (Anthropic's protocol, donated to the Linux Foundation, adopted across OpenAI / Google / Microsoft tooling), **persistent task/session registries** that survive restarts, and **Docker-first deployment** to either a VPS or local macOS with one command.

---

## 6. Architecture at a Glance

Six logical planes, loosely coupled. **Bold = v1 scope. Grey = later phases.**

```
           ┌─────────────────────────────────┐
           │ ★ Telegram bot  |  ★ Console    │  ← Control plane
           └──────────┬──────────────────────┘
                      │ application API (no direct shell)
                      ▼
           ┌─────────────────────────────────┐
           │ ★ Task Registry                 │  ← Persistence plane
           │ ★ Session Registry              │
           │   (Artifact store — Phase 3)    │
           └──────────┬──────────────────────┘
                      │
           ┌──────────┴──────────┐
           ▼                     ▼
   ┌──────────────┐      ┌──────────────┐
   │ ★ OMC        │      │ ★ clawhip    │    ← Orchestration + Event planes
   │ orchestrator │◄────►│ event bus    │
   └──────┬───────┘      └──────┬───────┘
          │                     │
          ▼                     ▼
   ┌──────────────────┐   ┌──────────────────┐
   │ ★ Claude Code    │   │   Browser        │
   │   worker         │   │   Automation     │
   │                  │   │   Server         │
   │ (Codex / Gemini  │   │   (Phase 4)      │
   │  / GLM / claw-   │   │                  │
   │  code — Phase 5) │   │                  │
   └──────────────────┘   └──────────────────┘
              ↑ Execution plane       ↑ Browser plane
```

**Core principle:** human control, orchestration, runtime, and telemetry are four separate concerns that must stay separable. Any coupling between them is a regression.

---

## 7. Phase 1 Scope (MVP — v1)

**Definition of done:** the operator can

1. Run **one command** (`docker compose up`) on either a same-region VPS or a local macOS host.
2. Send `/task <description>` from Telegram **or** the equivalent command via local console.
3. The system plans, executes, and verifies the task using **OMC as the single orchestrator** over a **single Claude Code worker**.
4. Progress events flow through `clawhip` and land in Telegram / console as structured status updates.
5. When the task completes, the operator gets a summary with links to generated artifacts / PR.
6. Kill the host or bot mid-task, restart it, and the **task state and session are recoverable** from the registry.

**In scope for v1:**

- Telegram bot with allowlisted user ids.
- Local console client hitting the same application API.
- Task Registry + Session Registry (persistent; survives restart).
- OMC as single orchestrator owner; Claude Code as single execution backend.
- `clawhip` daemon with typed event schema and one Telegram sink (compact + summary formats).
- Basic approval flow via text command (`/approve <task-id>`) for `git push` as the one gated risky action class.
- **Three MVP-blocking MCP servers only:** `task-registry`, `session-registry`, `clawhip` event bridge. Workspace access uses Claude Code's native file tools; `artifact`, `git`, `github`, `memory`, `build` servers are Phase 3.
- Docker images and `docker-compose.yml` for both VPS (Linux) and macOS deployment targets.

**What "works" looks like in one sentence:** *one operator can kick off a real coding task from their phone at night, wake up to a structured summary with a merged PR, and have the system survive a VPS reboot in between.*

---

## 8. Non-Goals for v1

Explicitly deferred:

- GLM adapter / Codex worker / Gemini worker / `claw-code` Rust runtime.
- OMX (Codex-first orchestration mode).
- Remote browser workers and the Browser Automation Plane in general.
- Multi-runtime handoff protocol.
- Dynamic Docker execution-pool scaling.
- Multi-user access, team features, web dashboard.
- Telegram inline-button UX (keep to text commands first).
- Advanced recovery loops, dead-session detection, scheduled jobs.
- The artifact, git, github, memory/wiki, build, docker-pool, telegram-control MCP servers (Phase 3+).

Cutting these from v1 is the point of v1. They come back in their own phases once the spine is proven.

---

## 9. Success Criteria (falsifiable)

| Axis | Target for v1 |
|------|--------------|
| **Time-to-first-task** | < 30 min from a clean host to the first completed task. |
| **Autonomous-run proof** | 5 consecutive overnight runs, each a **multi-file feature task with tests**, completed with **no human action other than `/approve` responses**. Reading Telegram messages does not count as intervention. |
| **Durability** | 100% of in-flight tasks recoverable after a forced `docker compose restart`, verified by test script that kills the host mid-task. |
| **Event integrity** | Zero stdout-parsing regex anywhere in the task lifecycle path; every state transition emits a typed event. Enforced by code review / linter. |
| **Runtime decoupling proof** | Swap the Claude Code worker for a scripted stub that returns canned events; orchestrator and registry code must run unchanged. |
| **Operator latency** | Task creation → first Telegram ack < 2 s on same-region VPS **or** local host. Long-haul remote deployments out of scope. |

---

*End of executive brief. Sections below are appendix material supporting PRD handoff.*

---

## Appendix A — Roadmap Summary

| Phase | Theme | Unlocks |
|-------|-------|---------|
| **Phase 1** (v1 / this brief) | Control plane baseline | Telegram + console + OMC + registries + Claude Code + `clawhip` + Docker deploy. |
| **Phase 2** | Event plane maturity | Full `clawhip` routes, alerts, threaded chats, richer approval flows. |
| **Phase 3** | MCP/tooling baseline | Artifact, git, github, build/verification, memory/wiki servers. |
| **Phase 4** | Browser automation plane | `browser-harness`-backed server, live + remote modes, browser-specialist profile. |
| **Phase 5** | Multi-runtime | OMX + Codex + Gemini + GLM + `claw-code` adapters; handoff protocol. |
| **Phase 6** | Server execution pool | Dockerized worker pool, remote browsers, isolated worktrees, verification workers. |
| **Phase 7** | Reliability | Recovery loops, stale detection, runbooks, operator dashboards. |

## Appendix B — Dependencies

**Upstream projects to fork / extend** (build path (a) — upstream-first):

- [`oh-my-claudecode` (OMC)](https://github.com/Yeachan-Heo/oh-my-claudecode) — primary orchestrator (v1).
- [`clawhip`](https://github.com/Yeachan-Heo/clawhip) — event bus (v1).
- [`oh-my-codex` (OMX)](https://github.com/Yeachan-Heo/oh-my-codex) — Codex-first mode (Phase 5).
- [`claw-code`](https://github.com/ultraworkers/claw-code) — experimental Rust runtime (Phase 5).
- [`browser-harness`](https://github.com/browser-use/browser-harness) — browser backend (Phase 4).

**External contracts:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex CLI](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [Model Context Protocol](https://modelcontextprotocol.io/), Telegram Bot API.

**Local reference implementations** (prior art on this machine; inspiration only, not v1 dependencies): `agent-orchestrator`, `hive`, `ai-maestro`, `codex-lb`, `Antigravity-Manager`, `mem0` / `OpenMemory`, `opencode`.

## Appendix C — Risks

1. **Upstream velocity.** Five core forks are external; breaking upstream changes could dominate maintenance. *Mitigation:* pin versions, adapter interfaces, upstream contributions.
2. **Registry is a single point of failure.** Task/session corruption nukes in-flight work. *Mitigation:* append-only event log + periodic snapshots before anything fancier.
3. **Telegram as sole remote surface.** Outages or account bans block the operator. *Mitigation:* console mode is always the local fallback; a web surface is a later-phase addition.
4. **Scope ambition.** 7 phases; only Phase 1 must ship. *Mitigation:* enforce §8 non-goals hard.
5. **Single-operator assumption hardens the design.** Later multi-user support may require retrofits. *Accepted:* not a v1 problem.

## Appendix D — Open Questions for PRD

- Registry storage choice: SQLite vs. Postgres vs. embedded KV?
- Local console protocol: shared HTTP API with the bot, or direct registry client?
- Event schema versioning strategy.
- Minimum approval UX for v1: text-only `/approve <task-id>` (current plan) vs. Telegram callback buttons.

---

## One-Sentence Summary

*A self-hosted, Docker-deployable personal autonomous development platform where Telegram and a local console drive a Claude Code worker through a typed event bus, backed by a persistent task registry that survives restarts — architected so additional CLI agents and a dedicated browser plane can be added later without changing the spine.*
