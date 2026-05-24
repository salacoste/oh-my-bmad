# Story 11.3.4 — Separability stack task-progression failure (nightly S-3)

Status: **backlog** (filed 2026-05-25 as Path B split from Story 11.3.3 per D1)

## Story

**As** the platform maintainer
**I want** the nightly `S-3 separability` job (S1/S2/S3/S4) to pass — tasks posted to
the full 5-service compose stack must progress through the canonical lifecycle
(`task.created` → `task.plan.ready` → … → `task.completed`)
**so that** the FR35 / NFR-M5 separability invariants (worker-swappable + orchestrator-
swappable spine) regain regression protection and the nightly baseline is fully green.

## Background — how this surfaced

Story 11.3.3 fixed two of the three original nightly failures (Fix-A idempotency-replay
`--` bug; Fix-B bind-mount uid/gid). Fix-B also fixed the crash-injection 120s hang.
Nightly run 26373557044 @ `cb53279` then went **3/4 green**.

Fix-B eliminated a `PermissionError` that the separability tests hit when reading the
bind-mounted audit JSONL. That error had been **masking** a deeper failure: with the
read now working, the tests reveal that **tasks never progress past `task.created`**.

## Observed failure (nightly run 26373557044)

```
S1: Failed: task 't-019e5bf5-...' did not reach task.completed within 60.0s; types seen=['task.created']
S2: TimeoutError: event 'task.plan.ready' for task 't-019e5bf6-...' not seen within 30.0s; types seen=['task.created']
S3: Failed: task 't-019e5bf6-...' did not reach task.completed within 30.0s; types seen=['task.created']
S4: Failed: Phase 1 compose up failed (rc=1); ... dependency failed to start: container omb-registry-state is unhealthy
```

- **S1/S2/S3** (use Fix-B'd test composes `docker-compose.s1/s2.yml` + `docker-compose.test.yml`):
  stack boots, `task.created` is emitted and materialized, but the orchestrator-adapter /
  worker-wrapper never advance the task. No `task.plan.ready`, no `task.completed`.
- **S4** (uses ROOT `docker-compose.yml` + `docker-compose.s4.yml`; named volume, NOT a
  Fix-B test-compose): `registry-state` reports unhealthy during `compose up -d`. Because
  S4 uses the root compose (uid 10002 + named volume), Fix-B's `_compose_env` uid override
  does NOT apply here — S4 may need its own uid/healthcheck treatment OR the named-volume
  ownership initialized correctly.

## Hypotheses to investigate (H1–H4)

### H1 — orchestrator-adapter not consuming `task.created` (most likely for S1/S2/S3)

The orchestrator-adapter tails the event log (or polls registry-api) for `task.created`
and drives planning. If it isn't seeing the event or isn't wired in the separability
compose, the task stalls. **Probe:** `docker compose logs orchestrator-adapter` in a
local S1 repro; check whether it logs receipt of `task.created`.

### H2 — MCP stdio env-propagation regression

Story 11.3.3 bundled a change (orchestrator-adapter + worker-wrapper `mcp_clients.py`)
that added `env=os.environ.copy()` to `StdioServerParameters`. Before, the MCP subprocess
got an empty env; now it inherits the parent. Verify this didn't break the MCP handshake
(e.g. a var that the subprocess now sees and mis-parses). **Probe:** diff behavior with
and without the env propagation in a local separability run. NOTE: this change shipped in
`f6b4b89` — if S-3 was already failing at task-progression BEFORE that commit (it was
masked by PermissionError, so unknown), this may be unrelated.

### H3 — worker-wrapper can't reach registry-api / MCP servers in the compose network

The worker-wrapper needs to connect to task-registry + session-registry MCP servers and
registry-api. A network/healthcheck/ordering issue could leave it unable to claim the task.
**Probe:** `docker compose logs worker-wrapper`; check for connection errors.

### H4 — S4 registry-state unhealthy under root compose (named-volume ownership)

S4 boots the ROOT compose with a named Docker volume. The volume is initialized by Docker
(root-owned) and registry-state runs as uid 10002. If uid 10002 can't write to the
named-volume mount point, the lifespan fails before `/tmp/ready` — same class as Fix-B but
for the named-volume path rather than the bind-mount. **Probe:** local S4 repro; inspect
the named volume's ownership + registry-state logs (AC2 `REGISTRY_STATE_LIFESPAN_TRACE`
is already wired and can be enabled in the root compose for the probe).

## Acceptance criteria (draft — refine at create-story time)

- **AC1** — Local repro of at least S1 (`just test-separability` or direct compose) that
  reproduces the `task.created`-stall on a dev machine, with orchestrator-adapter +
  worker-wrapper logs captured.
- **AC2** — Root cause confirmed via probe evidence (H1–H4 verdicts: CONFIRMED / REFUTED).
- **AC3** — Fix applied; S1/S2/S3 reach `task.completed` and S4 registry-state becomes
  healthy. Re-run nightly → `S-3 separability` job PASS.
- **AC4** — No regression in the other 3 nightly jobs (idempotency / migrator / crash-injection
  stay green) and no regression in the PR-gate `ci` workflow.
- **AC5** — Decide whether the bundled `mcp_clients.py` env-propagation (H2) stays, is
  reverted, or is narrowed; document the decision.

## Constraints

- **Epic 11 retro addendum AI-1 mandate APPLIES** — cross-cutting (multi-service stack) →
  3-lane review at pass-1.
- **FR26 single-writer invariant preserved.**
- **NFR-R1 / NFR-R2 (crash-injection) must stay green** — Fix-B fixed it; don't regress.
- Do NOT revert Story 11.3.3's Fix-A / Fix-B / AC2 — they are correct and verified.

## Frontmatter

```yaml
---
story_id: 11.3.4
story_key: 11-3-4-separability-task-progression
parent_epic: 11
phase: 2
fr_refs: [FR35, NFR-M5]
nfr_refs: [NFR-M5]
arch_refs:
  - "Story 11.3.3 — nightly diagnosis (Fix-A + Fix-B took nightly to 3/4 green; this is the 4th, unmasked failure)"
  - "Story 2.15 / 5.18 — separability harness origin (FR35 single-env-var orchestrator/worker swap)"
  - "Story 11.3.3 bundled mcp_clients.py env-propagation (H2 candidate)"
estimated_complexity: MEDIUM
priority: medium (nightly is 3/4 green; no production impact; S-3 separability is the last red job)
blocks: []
---
```

## Definition of Done

- S-3 separability nightly job PASS (all of S1/S2/S3/S4).
- Root cause documented with probe evidence.
- Other 3 nightly jobs + PR-gate `ci` remain green.
- AI-1 3-lane review complete; findings batch-applied per standing policy.
- `sprint-status.yaml` `11-3-4-...: backlog → done`.
