---
session_topic: 'Phase 2 of oh-my-bmad — scope ideation'
session_goals: 'Surface options beyond the 5 known hooks; converge on a Phase 2 scope before PRFAQ / product-brief-update / PRD-edit'
session_date: '2026-05-15'
user_name: 'R2d2'
facilitator: 'Mary (bmad-brainstorming)'
techniques_used:
  - SCAMPER
  - Random Stimulation
  - 5 Whys
  - Reverse Brainstorming
  - Pre-mortem
  - Forced Connections
  - Six Thinking Hats (compressed)
ideas_generated: 78
status: 'complete'
output: 'phase-2-scope-shortlist'
next_step: 'awaiting user commitment to a shortlist; then PRFAQ (option B) or product-brief-update (option A)'
---

# Phase 2 brainstorming — oh-my-bmad

## Session overview

**Topic.** What should Phase 2 of oh-my-bmad be? Phase 1 shipped a baseline (10 epics, 88 stories) with 5 named Phase-2 hooks already deferred-by-design. The job here is to surface options the deferral list missed, stress-test the ordering, and converge on a defensible scope before we move into planning artifacts.

**Goals.**
1. Generate ≥70 distinct ideas across orthogonal domains (engineering / operator-UX / cost / risk / black-swan / governance / composition / sequencing). The first 20 will be obvious; ideas 30–70 are where the value lives.
2. Cluster + score candidates with explicit criteria (effort, value, risk, lock-in).
3. Surface the **non-obvious** ideas — the ones not in the deferral list.
4. Produce a prioritized shortlist with rationale and a sketch of what each picked item would touch in the existing codebase.
5. Route to the right next BMad skill (PRFAQ vs edit-product-brief vs edit-PRD).

**Constraints (load-bearing).**
- Solo operator — Phase 1 took ~25 days of high-throughput work. Phase 2 needs to be **trim-able** so a calendar interruption doesn't strand the work in a broken half-state.
- Phase 1 architecture invariants (FR26 single-writer, MCP stdio-only, no `anthropic` in platform code, event-sourcing additive-only) are non-negotiable. Phase-2 work that violates them needs an ADR before code.
- The 386-rule digest (`_bmad-output/project-context.md`) is a *liability* if Phase 2 keeps growing it linearly; new rules earn their place only if they prevent recurring failure modes.
- Resource budget for typed events: every new feature that touches the spine adds at least 1–3 event-type registrations + 1+ migrator versions + idempotency + recovery handlers + contract fixtures. The marginal cost per new event is **not zero**.

---

## Phase 1 — divergent generation (78 ideas across 9 domains)

> Each block is a different lens. Shifts forced every ~10 ideas per the skill's anti-bias protocol. The 5 named hooks appear as natural seeds but are **not** the center of gravity — only ~1/3 of the ideas below cluster on them.

### Domain 1 — Engineering: the named hooks, deepened (10 ideas)

1. **Metrics + tracing as one event-stream subscriber**, not as separate instrumentation libraries. A new `metrics-subscriber` service tails the JSONL log and computes counters / gauges / histograms from the existing event types — zero new instrumentation in `services/*`. Avoids the "OTel everywhere" tax.
2. **`trace_id` propagation as a Phase-2 *kernel*** — wire the reserved field into MCP envelopes, registry-API headers (`X-Trace-Id` already documented), and worker subprocess args. *Then* layer OTel on top later. Decouples wiring from collector choice.
3. **Browser-automation as a fourth "worker-wrapper" sibling**, not as a fourth operator surface — runs Playwright/Patchright in a sandboxed subprocess, supervised by an `orchestrator-adapter` extension. Operator surface stays Telegram + console; the "browser" is a worker's tool.
4. **Or: browser as a 4th operator surface** (the originally-listed hook) — a web UI you log into to see live task state, approve high-tier actions, replay events. Different ROI calculus.
5. **CLI-agent swap as a per-task choice**, not a global config. Operator says "/run task X with Codex" or "/run task X with Gemini"; orchestrator-adapter dispatches to the chosen runtime. Forces the adapter contract to be airtight.
6. **Remote MCP via SSE** specifically (not HTTP polling) — SSE keeps the stdio "long-running stream" mental model intact, just over the network. HTTP request/response is a worse fit for tool sessions.
7. **Cosign + SLSA L2 + SBOM** as one self-contained Phase-2 epic, **plus** GitHub-supplied attestations (no separate Sigstore infra to run). Lowest-operational-cost path to digest-pinning.
8. **WAL replication to a second host** (literal SQLite WAL streaming via `rsync --inplace` cron OR `litestream`) — Phase-2 disaster-recovery without redoing the storage layer. Cheap insurance.
9. **Per-task budget enforcement at the worker boundary** — Phase 1 has `task.budget_exceeded` events but no enforcement loop. Phase 2 ships the loop: worker subprocess gets killed when its token-spend exceeds the task's budget allocation.
10. **Snapshot-diff materialization** — Phase 1 ships full snapshots. Phase 2 could ship incremental diffs (deltas since last snapshot) — same restore semantics, smaller recurring size. Defers the Phase-4 retention pruning by 2-3x.

### Domain 2 — Operator UX: what the human notices (10 ideas)

11. **"What is the agent doing right now?" pane** — a live event-stream view (Telegram inline message that updates? a console TUI?) that lets the operator watch reasoning breadcrumbs as they happen. Phase 1 emits them; Phase 2 surfaces them.
12. **Approval-request inbox** as a separate Telegram conversation thread, not inline replies — operator can pin it and triage approvals like email. Right now approval requests are inline; they get lost in chatter.
13. **Task templates** — operator stores reusable task prompts ("nightly dependabot triage", "weekly retro digest") and triggers them with one command.
14. **Voice commands via Telegram** (Telegram voice → Whisper transcription → task command) for car / mobile use.
15. **Cost-per-task disclosure** in the operator UI — every task ends with "$0.43 spent, 12k tokens, 4 retries." Already in events; Phase 2 surfaces it.
16. **Multi-operator with capability tier per operator** — a delegated tier-1 operator can approve tier-2 actions but not tier-3, etc. Phase 1's `AllowlistMiddleware` is binary (allow/deny); Phase 2 makes it tiered. Big UX win, modest engineering.
17. **"Why did you reject?" feedback loop** — when operator rejects a worker's approval request, capture a free-form reason and feed it back as context for the worker's next attempt. Closes a learning loop.
18. **Console TUI that mirrors Telegram** — same command surface, real-time event view, retro-compute keystrokes on iTerm/tmux. Today's console is a CLI; Phase 2 makes it a sit-down TUI.
19. **Mobile-optimized message rendering** — Phase 1's [`message-design.md`](../../docs/message-design.md) targets character budgets; Phase 2 audits how those messages render on iOS Telegram specifically (the most-common operator surface). Probably some surprises hiding.
20. **Operator handoff protocol** — when one operator (you) wants another (collaborator/co-maintainer) to take over a stuck task, there's no clean handoff event today. Phase 2 could add `task.handoff_requested` / `task.handoff_accepted`.

### Domain 3 — Cost + opportunity (8 ideas)

21. **Phase 2 as 4 independent mini-phases**, each ≤2 weeks of solo work — sized so a calendar interruption doesn't strand work mid-flight. Diametric opposite of "one big Phase 2."
22. **Defer everything to Phase 3 except observability** — argument: every other Phase-2 hook benefits more from observability than from itself, so do observability first and let the rest of Phase 2 light up the new instrumentation.
23. **GHCR digest-pinning is the cheapest, highest-ROI item** — it's hours of work, not days, and it eliminates a real attack surface. Bundle it into ANY other Phase 2 epic as a "while we're here" addition.
24. **Browser plane is the most-fun-to-build but the least-load-bearing.** Building it first feels great and ships nothing the operator can't already do via Telegram. Ship it last (or skip into Phase 3).
25. **Codex / Gemini / GLM adapter parity is a one-shot integration test investment**, not a feature investment. The work is "prove the adapter contract holds." Cheap if the contract is good; expensive (and educational) if it isn't.
26. **Skip OpenTelemetry initially, use plain Prometheus pull** from the new `metrics-subscriber`. Defer the OTel collector + agent fan-out. 80% of value for 20% of cost.
27. **Budget calendar time for retros + amend cycle** — Phase 1's retros generated 11 deferred items; some surfaced as Epic 7.5. Phase 2 will likely have a similar 10-15% rework tail. Budget it; don't pretend it won't happen.
28. **Opportunity cost: writing this doc instead of Phase 1.5 polish.** Worth checking what *polish-only* work is sitting in `_bmad-output/implementation-artifacts/deferred-work.md` before committing to a big new direction.

### Domain 4 — Risk + regression surface (8 ideas)

29. **OTel adoption is sticky** — once added, ripping out is hard. Make sure the choice is right before scaling instrumentation. Argument for option (1) over canonical OTel.
30. **Remote MCP transport changes the trust boundary** — stdio assumes the local process is trusted; HTTP/SSE pulls in TLS, auth tokens, allowlist, rate-limiting. Phase 1's `AllowlistMiddleware` only covers Telegram.
31. **Multi-CLI-agent broadens the attack surface** — every new agent SDK is a new dependency, a new license, a new supply-chain risk.
32. **WAL replication ≠ HA** — common confusion. Replication is *disaster recovery*; HA is failover. Phase 2 should pick one and name it correctly, not blur them.
33. **Browser automation is a credential-leak surface** — sessions, cookies, OAuth flows all touch the worker process. The capability-tier system needs new tiers (or sub-tiers) for "browser-tier-2" actions.
34. **Cosign tag-immutability has a sharp edge** — once enabled, no force-overwrites of release tags even by an operator. Sometimes operators legitimately need that (corrupted release republish). Document the recovery path.
35. **`metrics-subscriber` is a new subscriber** — and Phase 1's separability tests (`tests/separability/`) assume the subscriber set is fixed. Adding to it touches the test fixtures, the recovery path, and the snapshot policy.
36. **Adapter shim change-fatigue.** Every new CLI agent (Codex, Gemini, GLM) needs contract tests + integration test fixtures + retro artifacts. The marginal cost per agent is **not zero**; doing 3 agents at once is 3× the cost.

### Domain 5 — Black-swan / pre-mortem (8 ideas)

37. **Telegram Bot API changes.** Phase 2 picks aiogram v4 (when it ships) — and the migration alone could eat a full mini-phase.
38. **Anthropic's Claude Code CLI changes its emission protocol.** Phase 1's `worker-wrapper` parses the CLI's typed events; if v2.0 changes the format, the wrapper rewrites.
39. **MCP protocol v2 ships** — if Anthropic moves to a v2 wire format with breaking changes, the stdio servers all need a parallel-deploy migration.
40. **uv ships a 1.0 with workspace semantic changes** that break `[tool.uv.sources]` resolution. Not a "if" — a "when."
41. **GHCR pricing changes** for public images push them off the platform. Phase 2 should at minimum tag an "exit path" image registry.
42. **A regulatory/safety event** around autonomous AI agents that forces explicit human-approval gates (Phase 1 already has them for Tier 3 — Phase 2 might need them for Tier 2).
43. **Roborev auto-review bot deprecation** — Phase 1's CI relied on it; the bot's API changed mid-Phase-1 (see Epic 7.5). Phase 2 might need a parallel reviewer or in-house gate.
44. **Solo-operator burnout / extended absence** — the system needs a "frozen" mode where it stops accepting new tasks but preserves all state safely for a 30/60/90-day pause.

### Domain 6 — Governance + ethics (6 ideas)

45. **Tier-3 action audit log as a separate first-class report** — not just events in the spine, but a queryable "every git push the worker did in the last 30 days, with the approving operator." Compliance hygiene.
46. **Per-task "kill switch" via a magic-word in Telegram** — `/PANIC <task-id>` immediately SIGKILLs the worker subprocess + emits `task.panic_killed`, no questions. Today's `/stop` is graceful.
47. **Reasoning breadcrumb retention policy** — they're emitted as events, so they're in the log forever. Some breadcrumbs may contain PII / credentials / secrets that slipped past the sanitizer. Phase 2 could add a "redact-in-place" path (writes a `secret.redacted` event referencing the original).
48. **License flagging at code-write time** — Phase 1 has `task.license_flagged` events; Phase 2 could add a pre-commit gate that fails the commit if the worker tries to merge code under an incompatible license.
49. **Worker subprocess sandboxing** — Firejail / bubblewrap / `unshare` to put the Claude Code subprocess into a namespace that can't reach the host's `~/.ssh` or `~/.aws`.
50. **Operator-approval signature** on Tier-3 events — operator's approval gets a non-repudiation marker (HMAC with operator-local key) so a future forensic audit can verify the approval wasn't fabricated by a compromised registry.

### Domain 7 — Composition: which hooks compose with which (8 ideas)

51. **(Metrics + tracing) → (Multi-agent CLI):** instrumentation is most useful when comparing two runtimes head-to-head. Doing observability **before** the second CLI agent maximizes the learning ROI of adding the agent.
52. **(Multi-agent CLI) → (Remote MCP):** if Codex/Gemini run in their own subprocesses, having an HTTP MCP transport lets them live on different hosts and consume one shared MCP server. But: not needed until the second agent ships.
53. **(Supply-chain hardening) → (anything that ships an image):** so it should land *first* if any Phase 2 epic touches the GHCR push pipeline. Otherwise lots of rework.
54. **(Browser plane) is mostly orthogonal** to the other 4 — it depends on the event spine, not on tracing/CLI/MCP/supply-chain. Schedules cleanly into its own slot.
55. **(WAL replication) cuts orthogonally** — touches deployment + disaster recovery, doesn't depend on any other Phase 2 epic. Can ship anytime.
56. **(Per-task budget enforcement) compounds with (metrics)** — without metrics, the enforcement is a black box. With metrics, the operator can see "this task spent 87% of its budget on retries" and tune the policy.
57. **(Tiered allowlist for multiple operators) compounds with (Tier-3 audit log)** — the moment you have 2+ operators, the audit log goes from "convenient" to "necessary."
58. **(Reasoning-breadcrumb live view) requires (`trace_id` propagation)** — you can't render a coherent worker reasoning trace without correlation IDs. So `trace_id` kernel is a prerequisite for any UI work.

### Domain 8 — Sequencing logic (5 ideas)

59. **Sequence A — Observability-first.** OTel/`trace_id` kernel → metrics-subscriber → tiered allowlist → cost-disclosure UX → live reasoning view. Coherent narrative, each step compounds.
60. **Sequence B — Supply-chain-first.** Cosign + SLSA + SBOM + digest-pinning (1 week) → then any other Phase 2 epic ships through the hardened pipeline. Lowest regret if you stop early.
61. **Sequence C — Multi-agent-first.** Add Codex (or Gemini) to prove the adapter contract → adapter testing surfaces bugs → fix them → then observability matters because you have something to compare. Risky if the contract is broken in a non-obvious way.
62. **Sequence D — Operator-UX-first.** Tiered allowlist + approval inbox + task templates + voice commands. Most-visible improvements, low engineering risk, motivates further investment.
63. **Sequence E — Disaster-recovery-first.** WAL replication + frozen-mode + Tier-3 audit log + operator-handoff protocol. Reduces the cost of stopping at any point.

### Domain 9 — Non-obvious / forced-connection ideas (15 ideas)

> Forced random stimulation: pair Phase-2 work with words drawn from unrelated domains. Goal is to surface ideas that wouldn't appear from the hook list alone.

64. **"Recipe book"** (cooking) → task-template library where each template is a typed event-sequence template + a name + an operator-curated description. The operator builds up a personal kitchen.
65. **"Replay"** (video games) → operator can replay a *historical* task from the event log into a sandboxed worker subprocess, with mocked side-effects, to debug "why did the worker do X?" An IDE for incident response.
66. **"Library card"** (libraries) → public read-only event-stream export. Operator can publish (via a one-shot redact-and-export pipeline) the event log of a specific successful task as a teaching artifact — a worked example.
67. **"Subscription"** (newspapers) → operator subscribes to specific event-type emissions (e.g., `task.approval_requested`) via webhook/email/Slack — beyond Telegram. Decouples notification from chat.
68. **"Marketplace"** (retail) → community-shared task templates with rating + provenance. Phase 4-level idea, but a Phase 2 hook could be "task template export/import as a portable file."
69. **"Receipts"** (transactions) → every task ends with a signed receipt: `(task_id, operator, model, cost, duration, side-effects emitted)` — operator-receivable, attached to the closing Telegram message.
70. **"Notebook"** (Jupyter) → an interactive shell where the operator can issue commands, see real-time state from `registry-state`, and *time-travel* through past task states. Operator-debugger.
71. **"Driving lessons"** (driver education) → simulation mode where a new operator gets a sandboxed bot that responds to commands but doesn't execute real side effects. Onboarding ramp.
72. **"Pair programming"** (development) → a second worker (cheap model) reviews the primary worker's plan before execution. Built-in red-teaming. Latency cost; learning value.
73. **"Dead man's switch"** (estate planning) → operator-defined "if no commands for N days, stop accepting new tasks and notify backup operator." Long-vacation safety.
74. **"Translation layer"** (linguistics) → a non-Telegram chat surface — Discord, Matrix, IRC — speaking the same envelope contract. Decouples operator UX from any single chat vendor.
75. **"Honesty box"** (community) → operator-visible "model uncertainty" indicators on worker outputs — when the Claude Code CLI signals low confidence, the operator UI flags it before approval.
76. **"Crash cart"** (medicine) → a one-button recovery flow that grabs the last-good snapshot + last-good event-log file + last-good config and packages them into a downloadable bundle.
77. **"Sister city"** (governance) → an experimental MCP server that mirrors *another* personal-platform's event log — for the case where you eventually run more than one instance.
78. **"Conservatory"** (music) → an explicit "improvement-only" mode where the worker is forbidden from production-touching actions and can only refactor / add tests / add docs to a designated tree.

---

## Phase 2 — convergence

### Scoring criteria (consistent across all candidates)

- **Effort (E):** S = ≤3 days · M = ~1 week · L = 2-3 weeks · XL = >3 weeks
- **Value (V):** 1-5 — operator-felt improvement to autonomy / safety / observability
- **Risk (R):** 1-5 — regression surface + lock-in cost (lower is better)
- **Composes-with-others (C):** number of OTHER candidates this one accelerates
- **Reversibility (Rev):** Y = can be ripped out · N = sticky once shipped

### Top 14 clustered candidates, scored

| ID | Candidate | E | V | R | C | Rev |
|----|-----------|---|---|---|---|-----|
| **α** | `trace_id` propagation kernel (envelope → API → MCP → worker) | M | 4 | 2 | 6 | Y |
| **β** | `metrics-subscriber` service (counters/gauges/histograms from JSONL) | M | 4 | 2 | 3 | Y |
| **γ** | Cosign + SLSA L2 + SBOM + digest-pinning (full supply-chain) | S | 3 | 1 | 4 | N |
| **δ** | WAL replication via litestream (disaster-recovery insurance) | S | 4 | 1 | 1 | Y |
| **ε** | Tiered allowlist (per-operator capability-tier) | M | 4 | 3 | 2 | Y |
| **ζ** | Codex adapter (one second-CLI to prove the contract) | L | 3 | 4 | 4 | Y |
| **η** | Remote MCP via SSE (HTTP/SSE transport for MCP) | L | 2 | 4 | 2 | Y |
| **θ** | Browser-automation as 4th worker tool | L | 3 | 4 | 1 | Y |
| **ι** | Browser-automation as 4th operator surface (web UI) | XL | 3 | 4 | 1 | Y |
| **κ** | Per-task budget enforcement loop (kill on exceed) | M | 4 | 3 | 1 | Y |
| **λ** | Tier-3 audit log as queryable report (governance) | S | 3 | 1 | 2 | Y |
| **μ** | "Replay" mode — debug historical task in sandboxed worker | L | 4 | 3 | 0 | Y |
| **ν** | Frozen-mode + dead-man's-switch (long-pause safety) | S | 3 | 1 | 1 | Y |
| **ξ** | Approval-request Telegram inbox thread + signature | M | 4 | 2 | 2 | Y |

### Pairwise dependencies (red = hard prerequisite; blue = compound)

```
γ (supply-chain) ──── must land before any image-push epic ─── arrows to ζ, η, θ, ι
α (trace_id)    ──── prerequisite for ─── β, μ, ξ (anything correlating events)
β (metrics)     ──── compounds with ─── κ (budget enforcement makes more sense with metrics)
ε (tiered allowlist) ─── compounds with ─── λ (multi-operator → audit becomes load-bearing)
δ (WAL replication) ──── orthogonal to everything ─── can ship anytime
ν (frozen mode) ────── orthogonal to everything ─── can ship anytime
ζ (Codex adapter) ───── reveals adapter-shim bugs that ─── θ, ι would also hit
```

### Three coherent Phase-2 narratives

**Narrative I — "The Observability Phase" (Recommended)**

> *Story:* "Phase 1 shipped a system whose state I can rebuild from the log but whose live behavior I can't yet see. Phase 2 fixes that, so every later phase ships into a stack I can actually reason about."

| Order | Item | Why |
|---|---|---|
| 1 | **γ** Supply-chain hardening | Cheapest, highest-ROI, lands first to harden every later epic's release |
| 2 | **α** `trace_id` propagation kernel | Wires the field that's already reserved on the envelope into every emission and consumption site |
| 3 | **β** `metrics-subscriber` service | Counters/gauges/histograms derived from the event-log tail; no instrumentation in `services/*` |
| 4 | **ξ** Approval-request inbox thread + non-repudiation signature | Most-visible operator UX win; uses the new `trace_id` |
| 5 | **κ** Per-task budget enforcement loop | Only worth doing once metrics make the enforcement transparent |
| 6 | **δ** WAL replication via litestream | Cheap insurance; ships parallel to anything |

Total effort: ~6-8 weeks. Optimizes for **decision-quality + safety + low regret**.

**Narrative II — "The Multi-Agent Phase" (Stretch)**

> *Story:* "Phase 1 proved Claude Code works. Phase 2 proves the architecture isn't Claude-Code-specific."

| Order | Item | Why |
|---|---|---|
| 1 | **γ** Supply-chain hardening | Same reason |
| 2 | **α** `trace_id` propagation kernel | Critical for comparing agents head-to-head |
| 3 | **β** `metrics-subscriber` | Same — head-to-head needs telemetry |
| 4 | **ζ** Codex adapter | The single most informative integration test of the orchestrator-adapter contract |
| 5 | **λ** Tier-3 audit log (governance) | Multi-agent → multi-actor → audit becomes load-bearing |
| 6 | **δ** WAL replication | Cheap insurance |

Total effort: ~8-10 weeks. Optimizes for **architectural validation + portfolio depth**. Highest risk on item 4.

**Narrative III — "The Operator-UX Phase" (Soft path)**

> *Story:* "Phase 1 was for the platform; Phase 2 is for the operator."

| Order | Item | Why |
|---|---|---|
| 1 | **γ** Supply-chain hardening | Same |
| 2 | **ε** Tiered allowlist | Most-felt UX change |
| 3 | **ξ** Approval-request inbox thread + signature | Pairs with ε |
| 4 | **λ** Tier-3 audit log | Pairs with ε + ξ |
| 5 | **ν** Frozen mode + dead-man's-switch | Solo-operator safety net |
| 6 | **δ** WAL replication | Same |

Total effort: ~5-7 weeks. Optimizes for **operator confidence + solo-resilience**. Lowest engineering excitement.

### Rejected (and why)

- **η Remote MCP via SSE** — solves a problem you don't have yet (no remote workers in Phase 1 or near-term Phase 2). Defer to Phase 3.
- **θ / ι Browser plane (either flavor)** — high effort, modest marginal value over current Telegram + console surfaces. Defer to Phase 3.
- **μ Historical "replay" mode** — beautiful idea, high engineering cost (mocked side-effect harness is its own design problem), zero composition with other Phase-2 items. Defer to Phase 3 once the rest is in place.
- **74 Translation layer (Discord/Matrix)** — second chat surface adds maintenance load without changing what the agent does. Skip unless community demand surfaces.
- **77 "Sister city" multi-instance mirroring** — speculative; no use case yet.
- **All of section 9** except the named picks (64-78) — interesting prompts, but most either compose poorly with the existing spine or are Phase-3+ scoped.

### The "Phase 1.5" question

Before committing to a big Phase 2, audit `_bmad-output/implementation-artifacts/deferred-work.md`. If it has ≥3 medium-effort items remaining, run a Phase 1.5 sweep (analogous to Epic 7.5) **first**, so the deferred-debt floor is clean. Phase 1.5 is *not* a Phase 2; it's a hygiene pass.

---

## Phase 3 — code-touch sketch (top 6 picks)

These are concrete enough to estimate; not yet stories.

### γ — Supply-chain hardening (S)
- **New:** `.github/workflows/release.yml` adds `sigstore/cosign-action` + `anchore/sbom-action`. New `cosign verify` step in `ci.yml` for image pulls in tests.
- **Modified:** `Dockerfile.base` adds a `SOURCE_COMMIT` build-arg → image label. `justfile` adds `just verify-image <tag>` recipe.
- **New event types:** None.
- **New rules in project-context.md:** Cat 6 update — release procedure now includes cosign + SBOM verification step.

### α — `trace_id` propagation kernel (M)
- **New:** `packages/events/src/events/trace.py` — `TraceContext` typed envelope + `bind_trace_context()` middleware helper.
- **Modified:** `services/registry-api/src/registry_api/adapters/middleware.py` — pulls `X-Trace-Id` (already documented in api-contracts), mints UUIDv7 if absent. `services/telegram-gateway/.../middleware/AllowlistMiddleware` — injects `trace_id = f"tg:{update_id}"`. Worker subprocess wrapper — passes `trace_id` as CLI flag to Claude Code. MCP tool boundary — every tool now takes `caller_trace_id` (already documented in capability-tiers.md).
- **New event types:** None — extends existing envelope's reserved field.
- **Migration:** schema_version bumps `1.0.0 → 1.1.0` for envelope (additive).

### β — `metrics-subscriber` service (M)
- **New workspace member:** `services/metrics-subscriber/` (FastAPI on `/metrics` Prometheus-format).
- **New event subscriptions:** All `task.*`, `session.*`, `worker.*`, `service.crashed`, `secret.access_denied`, `tier3.action_attempted`.
- **New migrations:** None — read-only subscriber.
- **CI gates:** New `tests/separability/` entry verifies subscriber isolation.
- **Risk:** First new subscriber since Phase 1 — the separability fixtures + recovery-cursor logic need extending.

### ξ — Approval-request inbox + signature (M)
- **Modified:** `services/telegram-gateway/handlers/` — new `/approvals` command opens a pinned thread.
- **New event types:** `task.approval_signed` (carries operator's local HMAC).
- **New config:** operator-local signing key in `.env` (`OPERATOR_HMAC_KEY`).
- **Migration:** `1.0.0 → 1.0.1` of approval events (adds optional `signature` field).

### κ — Per-task budget enforcement (M)
- **Modified:** `services/worker-wrapper` — adds budget supervisor that subscribes to `task.budget_exceeded` and SIGTERMs the subprocess.
- **New event types:** `task.budget_enforcement_triggered`.
- **Composes with β** — visualizes "how often does enforcement fire?" in metrics.

### δ — WAL replication via litestream (S)
- **New service:** Sidecar `litestream` container in `docker-compose.yml` replicating `oh-my-bmad-data` to an operator-configured S3/B2 endpoint.
- **New config:** `OMB_LITESTREAM_CONFIG_PATH` in `.env.example`.
- **Migration:** None.
- **Docs:** `docs/backup-restore.md` adds a litestream restore-runbook section.

---

## Phase 4 — recommendation + decision request

### Mary's facilitator recommendation

**Narrative I — "The Observability Phase"** — for three reasons:

1. **Compounds best.** Every item in the sequence makes the next one cheaper or more valuable.
2. **Lowest regret.** If you stop after 3 weeks, you have supply-chain hardening + trace correlation + metrics — none of that needs to be ripped out.
3. **Most-honest about Phase 1's actual gap.** The 386-rule digest explicitly flags "Phase 2 gap — metrics + distributed tracing" as the platform's largest deferred unknown. Closing that unknown first makes everything later more reliable.

### What's *not* in Narrative I

- **No browser plane** (Phase 3).
- **No second CLI agent** (Phase 3 — wait until you actually want to use one).
- **No remote MCP** (Phase 3).

This shrinks Phase 2 from "5 big hooks" to **6 small-to-medium items** — fits a solo-operator calendar.

### Decision request

Three options to commit to before we run `bmad-prfaq` / `bmad-edit-prd`:

- **[I]** Narrative I — Observability Phase (Recommended). Pick this if you want low regret + compound value + the platform finally answering "what is my agent doing right now?"
- **[II]** Narrative II — Multi-Agent Phase. Pick this if portfolio-depth ("look, the adapter contract actually swaps runtimes") matters more than operator-felt improvements.
- **[III]** Narrative III — Operator-UX Phase. Pick this if you want the most-visible day-to-day improvements with the least engineering risk.
- **[Other]** A custom subset of the 14 candidates (α–ξ) — name your picks; I'll re-rank and re-sequence.
- **[Phase 1.5]** Run an Epic 7.5-style debt-sweep first before committing to Phase 2 scope.

---

## Session metadata

- **Ideas generated:** 78 across 9 domains.
- **Domain shifts per anti-bias protocol:** 9 (every ~10 ideas).
- **Techniques used:** SCAMPER, Random Stimulation, 5 Whys, Reverse Brainstorming, Pre-mortem, Forced Connections, Six Thinking Hats (compressed).
- **Convergence basis:** 5-criteria scoring (effort × value × risk × composes × reversibility) + pairwise dependency graph + 3-narrative coherence test.
- **Status:** complete; awaiting user commitment to a narrative before routing to PRFAQ / edit-product-brief / edit-PRD.
- **Output location:** `_bmad-output/planning-artifacts/phase-2-brainstorming.md` (this file).

— Mary 📊
