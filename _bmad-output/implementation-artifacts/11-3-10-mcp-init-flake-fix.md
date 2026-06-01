# Story 11.3.10 — orchestrator-adapter + worker-wrapper MCP-init flake fix (healthcheck budget ≥ legitimate init ceiling)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

**As** the platform operator,
**I want** the `orchestrator-adapter` and `worker-wrapper` services to
reliably reach `Up (healthy)` on a fresh `docker compose up` instead of
getting stuck in an MCP-init timeout/restart cycle that leaves the stack
at "5/7 healthy",
**so that** the ROOT compose comes up fully green on first boot (and the
nightly `s3-separability` S-4 sub-test passes without the intermittent
`TimeoutError` that Stories 11.3.6/11.3.7 documented but deferred).

## Background — the flake, as discovered across Stories 11.3.6 / 11.3.7

This is the **3rd and final story of the Epic-11.3 close-out tail**
(11.3.8 events-perm → 11.3.9 /v1/health real signals → **11.3.10
MCP-init flake**).

During Story 11.3.7's Task 7 (ROOT compose full bring-up verify) the
stack repeatedly reached **5/7 healthy** — `clawhip-daemon`,
`metrics-subscriber`, `registry-api`, `registry-state`,
`telegram-gateway` all `healthy` — then **`orchestrator-adapter` +
`worker-wrapper`** failed to converge, timing out after the 180s S-4
healthcheck budget. (Verbatim, `11-3-7-root-compose-full-bringup.md:454-457`:
*"FAILED with `TimeoutError` after 180s — `orchestrator-adapter` +
`worker-wrapper` stuck in MCP-init restart loop … Each MCP-init attempt
times out at ~30s."*) Labelled **H7f** in the deferred-work trail; flagged
as pre-existing macOS-Docker-Desktop subprocess-spawn timing, NOT caused
by 11.3.7's diff.

### Root-cause analysis (this story's job to confirm + fix)

The MCP-init path is identical (byte-for-byte mirror, contract-enforced)
in both services:

- `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py`
- `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py`

Sequence (orchestrator line numbers; worker mirrors):
- `_INIT_TIMEOUT: float = 30.0` (orch line 19 / worker line 16) — per-server cap.
- `MCPClientGroup.__aenter__` connects **3 MCP servers sequentially**
  (`task-registry`, `session-registry`, `clawhip-bridge`).
- Per server `_connect()`:
  `read, write = await stack.enter_async_context(stdio_client(params))`
  → `session = await stack.enter_async_context(ClientSession(read, write))`
  → `await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT)`
  (orch line 151 / worker line 156 — **where the `TimeoutError` fires**).
- Only AFTER all 3 inits succeed does the service touch `/tmp/ready`
  (`WORKER_READY_FILE_PATH: /tmp/ready`, compose line 230; registry-state
  precedent `app/main.py:277-291`).

**The latent arithmetic bug (NOT macOS-specific):** the shared
healthcheck anchor `docker-compose.yml:26-31` is
`start_period: 10s, interval: 5s, retries: 12` → a ~**70s** window before
Docker marks the container `unhealthy`. But the **legitimate worst-case
MCP-init ceiling is 3 × `_INIT_TIMEOUT` = 90s** (sequential). So even a
healthy-but-slow boot where each `session.initialize()` runs toward its
30s cap exceeds the 70s healthcheck window → marked `unhealthy` while
still legitimately initializing. macOS Docker Desktop's slower subprocess
spawn just makes the slow path the common path. **The healthcheck budget
is shorter than the code's own worst-case init time** — that is the
primary, environment-independent defect.

### Why a partial workaround already exists

Story 11.3.6 (H7f) set `OMB_MCP_AUDIT_EMISSION_ENABLED: "0"` on the two
spawners to break a *nested-stdio deadlock* in task-registry /
session-registry audit emission. That fixed the deadlock but NOT the
timing-budget mismatch above (per `deferred-work.md` H7f entry, deep-fix
deferred to this story).

## Acceptance Criteria

1. **AC1 — Nightly evidence gathered FIRST (gates the fix choice).**
   Before changing anything, run the nightly workflow and inspect the
   `s3-separability` job (which runs Phase-1 S-4 on `ubuntu-latest`):
   ```bash
   gh workflow run nightly.yml --ref epic-11.3.10
   # watch the run; capture the s3-separability job result + logs
   gh run watch <run-id>
   gh run view <run-id> --log --job <s3-separability-job-id> > /tmp/nightly-s4.log
   ```
   Record in Dev Agent Record:
   - Did `s3-separability` PASS or FAIL on CI Linux?
   - If it reached "5/7 healthy" then timed out → the flake reproduces on
     Linux (deeper than macOS-only).
   - If it passed → the flake is macOS-Docker-Desktop-local, and the
     compose-budget fix (AC2) is the complete fix; the code path is sound.
   This AC may require user authorisation to push + run the workflow
   (mirrors Story 11.3.7's AC8 deferral). HALT-and-ask if push is not
   pre-authorised.

2. **AC2 — Healthcheck budget ≥ legitimate init ceiling (primary fix; compose-only, NO P0 touch).**
   Give `orchestrator-adapter` + `worker-wrapper` a healthcheck window
   that comfortably exceeds the 90s worst-case sequential MCP-init
   ceiling. Preferred mechanism: a dedicated healthcheck anchor (e.g.
   `x-healthcheck-mcp`) with `start_period` raised to **≥ 100s** (90s
   ceiling + headroom) while keeping `test -f /tmp/ready`, `interval: 5s`,
   `timeout: 3s`, `retries: 12`. Apply it to both services' `healthcheck:`
   keys (currently both use the shared `*healthcheck` anchor at
   compose lines 203 + 244).
   - Rationale comment MUST cite: 3 × `_INIT_TIMEOUT` (30s) = 90s
     sequential ceiling; the old 70s window (`start_period 10s + 12×5s`)
     was shorter than the code's own legitimate init time.
   - `start_period` during which a failing healthcheck does NOT count
     toward `retries` is exactly the Docker primitive for "service is
     legitimately still starting" — this is the correct lever.
   - Do NOT touch the shared `*healthcheck` anchor used by other services
     (registry-state, clawhip-daemon) — add a sibling anchor so the blast
     radius is the 2 MCP-spawner services only.

3. **AC3 — `_INIT_TIMEOUT` / parallel-init change is GATED on AC1 Linux-failure evidence + mandatory P0 review.**
   ONLY if AC1 shows the flake still reproduces on CI Linux AFTER the AC2
   budget fix would not help (i.e. an individual `session.initialize()`
   genuinely exceeds 30s on Linux, not just the aggregate window), may the
   `mcp_clients.py` files be touched — and then ONLY the timeout constant
   and/or sequential→parallel init structure, NEVER the env-handling.
   This is the **a0ca050 P0 security area**; if touched:
   - **MANDATORY P0 diff-audit:** NO `os.environ.copy()` / `dict(os.environ)`
     reintroduced; the `_ENV_ALLOWLIST` mechanism (orch lines 37-77 /
     worker lines 34-74) stays byte-identical in intent.
   - The orch ⇄ worker `mcp_clients.py` **byte-identical mirror** must be
     preserved (contract test `tests/integration/test_clawhip_client_env_allowlist_mirror.py`
     — or the Story-11.3.6 mirror test — MUST stay green; change BOTH files
     identically).
   - **AI-1 3-lane review (Blind + Edge + Acceptance) is mandatory** for
     any `mcp_clients.py` diff (security-sensitive), per the same gate
     Stories 11.3.6/11.3.7 applied.
   - If AC1 shows Linux is clean, **DO NOT touch `mcp_clients.py` at all** —
     AC2 is the complete fix and AC3 is explicitly out of scope.

4. **AC4 — Restart-loop interaction documented.** Both services ALREADY
   declare `restart: unless-stopped` (confirmed:
   `docker-compose.yml` orchestrator-adapter + worker-wrapper blocks). So
   a timed-out MCP-init container DOES loop — which is exactly the
   "restart loop" the prior-story notes describe: each restart re-attempts
   the 3×30s init from scratch, and on a slow host none of the attempts
   finishes inside the ~70s healthcheck window before the next failure, so
   the service oscillates `starting → unhealthy → restart` and never
   latches `healthy`. AC2's `start_period` bump fixes this by giving a
   single attempt enough budget to complete and touch `/tmp/ready` before
   the healthcheck can mark it unhealthy. No restart-policy CHANGE is
   needed; just document in Dev Agent Record that `restart: unless-stopped`
   is present and that the start_period bump (not a restart-policy tweak)
   is the correct lever.

5. **AC5 — Docker repro confirmation (macOS local).** Reproduce the
   fresh-boot path on the same host Story 11.3.7 Task 7 used:
   ```bash
   docker compose down -v --remove-orphans
   just build-base && docker compose build orchestrator-adapter worker-wrapper
   env TELEGRAM_BOT_TOKEN=0:dummytesttoken TELEGRAM_SKIP_WEBHOOK_SET=1 \
       REGISTRY_STATE_AUTO_CREATE_SCHEMA=1 docker compose up -d
   # Wait; assert ALL 7 reach healthy (not 5/7) within the new budget.
   docker compose ps --format json
   ```
   Record the before/after: pre-fix 5/7 + timeout; post-fix 7/7 healthy.
   If the macOS host still flakes after AC2 (init genuinely >30s/server),
   that is the AC3-gating evidence — capture it.

6. **AC6 — Validation gates green:**
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy --strict packages/ services/ scripts/ mcp-servers/   # 240=baseline (0-new)
   uv run python scripts/check_imports.py && uv run python scripts/check_event_registry.py && uv run python scripts/check_single_writer.py
   uv run pytest -x -q -m "not slow"   # regression no new fails
   # If AC3 was triggered (mcp_clients.py touched): the env-allowlist
   # mirror contract test MUST pass.
   ```

7. **AC7 — Code review.**
   - If the change is **compose-only (AC2/AC4)** → `/code-review` default
     effort is sufficient (infra config, small blast radius).
   - If **AC3 was triggered** (`mcp_clients.py` touched) → **AI-1 3-lane
     review is MANDATORY** (security-sensitive P0 area) in addition to the
     default review.

8. **AC8 — Nightly green (closes the Epic-11.3 tail).** Final nightly run
   shows `s3-separability` (and all 4 jobs) PASS. This is the headline
   acceptance for the whole tail: the ROOT compose comes up fully green.
   May be DEFERRED to user authorisation (push + `gh workflow run`),
   mirroring 11.3.7 AC8 — record the run id when executed.

## Tasks / Subtasks

- [ ] **Task 1 — Gather nightly evidence** (AC1)
  - [ ] (If push pre-authorised) push `epic-11.3.10`, `gh workflow run nightly.yml --ref epic-11.3.10`.
  - [ ] Capture `s3-separability` result + logs; classify: Linux-clean vs Linux-flakes.
  - [ ] Record verdict in Dev Agent Record (drives whether AC3 is in scope).
- [ ] **Task 2 — Add `x-healthcheck-mcp` anchor + apply to the 2 spawners** (AC2)
  - [ ] In `docker-compose.yml`, add a sibling anchor near lines 26-46:
        `x-healthcheck-mcp: &healthcheck_mcp` = copy of `*healthcheck`
        with `start_period: 100s` (≥ 90s ceiling + headroom) and a
        rationale comment citing 3 × `_INIT_TIMEOUT`.
  - [ ] Point `orchestrator-adapter.healthcheck` (line 203) and
        `worker-wrapper.healthcheck` (line 244) at `*healthcheck_mcp`.
  - [ ] Leave the shared `*healthcheck` untouched for the file-ready
        services that init fast (registry-state, clawhip-daemon).
- [ ] **Task 3 — Document restart-loop interaction** (AC4)
  - [ ] Confirm `restart: unless-stopped` on both blocks (already present);
        document that the start_period bump — not a restart-policy change —
        is the fix lever, and why (a looping container still can't latch
        healthy if no single attempt fits the window).
- [ ] **Task 4 — macOS Docker repro** (AC5)
  - [ ] Run the AC5 fixture; assert 7/7 healthy post-fix; paste
        `docker compose ps` before/after into Dev Agent Record.
  - [ ] If still flaking → capture per-server init timing as AC3 evidence.
- [ ] **Task 5 — (CONDITIONAL, only if AC1+AC5 prove Linux/per-server >30s) `mcp_clients.py` fix** (AC3)
  - [ ] Change BOTH mirror files identically (timeout bump and/or
        sequential→`asyncio.gather` parallel init). NO env-handling change.
  - [ ] P0 diff-audit: confirm no `os.environ.copy()` / `dict(os.environ)`;
        `_ENV_ALLOWLIST` intact; mirror byte-identical.
  - [ ] Run the env-allowlist mirror contract test → green.
- [ ] **Task 6 — Validation gates** (AC6).
- [ ] **Task 7 — Code review** (AC7): default effort if compose-only;
      AI-1 3-lane MANDATORY if Task 5 ran.
- [ ] **Task 8 — Nightly green** (AC8): may defer to user authorisation.

## Dev Notes

### Source map (file:line guardrails)

- **Healthcheck anchors:** `docker-compose.yml:26-31` (`*healthcheck`,
  file-ready), `:37-46` (`*healthcheck_http`, socket). Add
  `*healthcheck_mcp` alongside.
- **orchestrator-adapter service block:** `docker-compose.yml:157-203`
  (healthcheck at 203). **worker-wrapper:** `:205-244` (healthcheck at
  244, `WORKER_READY_FILE_PATH: /tmp/ready` at 230).
- **MCP-init (P0 — touch ONLY under AC3):**
  - `services/orchestrator-adapter/.../adapters/mcp_clients.py` —
    `_INIT_TIMEOUT=30.0` (line 19), `_connect` init at line 151,
    `_ENV_ALLOWLIST` lines 37-77.
  - `services/worker-wrapper/.../adapters/mcp_clients.py` — mirror:
    `_INIT_TIMEOUT=30.0` (line 16), init at line 156, allowlist 34-74.
- **`/tmp/ready` precedent:** `registry-state/.../app/main.py:277-291`
  (touch) + `:373-394` (unlink on shutdown). worker touches after
  `start_session` completes.
- **Nightly workflow:** `.github/workflows/nightly.yml` (~20K) — 4 jobs;
  `s3-separability` on `ubuntu-latest`, cron `0 3 * * *`,
  `workflow_dispatch` enabled. This is the AC1/AC8 evidence source.
- **Prior workaround:** `OMB_MCP_AUDIT_EMISSION_ENABLED: "0"` on both
  spawners (Story 11.3.6 H7f) — keep as-is; it fixes the nested-stdio
  deadlock, orthogonal to this timing fix.

### Constraints

- **`mcp_clients.py` is the a0ca050 P0 security area.** Default posture:
  **DO NOT TOUCH.** Only AC3 (gated on hard Linux/per-server evidence)
  may, and then under mandatory P0 diff-audit + AI-1 3-lane review + the
  env-allowlist mirror contract test, with NO `os.environ.copy()` /
  `dict(os.environ)` and the orch⇄worker mirror kept byte-identical.
  [[diff-audit-delegated-security-work]]
- **Prefer the compose-only fix (AC2).** The healthcheck-budget mismatch
  (70s window < 90s init ceiling) is a real, environment-independent
  defect that AC2 fixes without any code change. Lead with it.
- **No new dependencies; no new event emission** — pure infra/timing fix.
- **Don't perturb the other 5 services' healthchecks** — scope the new
  anchor to the 2 MCP spawners.
- **Nightly may need user push authorisation** (AC1/AC8) — HALT-and-ask
  if not pre-authorised, mirroring 11.3.7 AC8.

### Project Structure Notes

- The fix is additive: a new compose anchor + two `healthcheck:` key
  re-points. No file moves, no deletions.
- If AC3 triggers, the change is symmetric across the two mirror files
  only; everything else is config.

### References

- [Source: `11-3-7-root-compose-full-bringup.md:454-463` — H7f flake
  discovery: 5/7 healthy → `TimeoutError` after 180s; "Each MCP-init
  attempt times out at ~30s"; root cause = subprocess spawn timing.]
- [Source: `11-3-7-root-compose-full-bringup.md:25` — H7f
  `OMB_MCP_AUDIT_EMISSION_ENABLED:"0"` nested-stdio deadlock workaround.]
- [Source: `services/orchestrator-adapter/.../adapters/mcp_clients.py:19,151`
  — `_INIT_TIMEOUT=30.0`; sequential 3-server init; `asyncio.wait_for`
  timeout origin.]
- [Source: `docker-compose.yml:26-31` — shared `*healthcheck`
  (start_period 10s + 12×5s = ~70s window < 90s init ceiling).]
- [Source: `.github/workflows/nightly.yml` — `s3-separability` job =
  AC1/AC8 CI-Linux evidence source.]
- [Source: memory [[diff-audit-delegated-security-work]] — a delegated
  debugger once reintroduced the reverted P0 secret leak; always
  diff-audit; MCP env = allowlist only.]

## Previous-story intelligence

- **Story 11.3.6 (H7f)** landed the `AUDIT_EMISSION=0` nested-stdio
  deadlock workaround and explicitly deferred the timing deep-fix here.
- **Story 11.3.7 (Task 7 / AC8)** is where the flake was repeatedly
  observed (5/7 healthy → timeout) and where nightly verification was
  deferred to user authorisation — this story inherits that deferral
  pattern for AC1/AC8.
- **Stories 11.3.8 + 11.3.9** (the first two tail stories) both held the
  line on "NO `mcp_clients.py` touched"; this story keeps that as the
  default and only relaxes it under the AC3 evidence gate + P0 review.
- **macOS-vs-Linux split is the crux:** if nightly (Linux) is green, the
  flake is host-local and AC2 fully closes it; the heavy AC3 path stays
  out of scope. Gather evidence before reaching for the P0 file.

## Git intelligence summary

Last commits on this lineage:

- `035d217` (epic-11.3.9) — /v1/health real signals (Story 11.3.9)
- `7f1a51f` (epic-11.3.9) — file Story 11.3.9
- `fde786e` (epic-11.3.8) — events/ dir 0o2775 ensure_shared_dir
- `808c24a` (epic-11.3.8) — file Story 11.3.8

Story 11.3.10 branches off `epic-11.3.9` so the chain stays linear:
11.3.7 → 11.5.1 → 12.1.1 → 11.3.8 → 11.3.9 → **11.3.10**. Branch name
`epic-11.3.10`. This is the LAST story of the Epic-11.3 close-out tail.

## Frontmatter

```yaml
---
story_id: 11.3.10
story_key: 11-3-10-mcp-init-flake-fix
parent_epic: 11
phase: 2
fr_refs: [FR35]
nfr_refs: [NFR-M5]
arch_refs:
  - "Story 11.3.6 H7f — OMB_MCP_AUDIT_EMISSION_ENABLED=0 nested-stdio deadlock workaround; timing deep-fix deferred here"
  - "Story 11.3.7 Task 7 — 5/7 healthy → TimeoutError after 180s; orchestrator-adapter + worker-wrapper MCP-init flake; nightly deferred"
  - "mcp_clients.py _INIT_TIMEOUT=30.0 × 3 sequential servers = 90s init ceiling vs ~70s healthcheck window (the latent budget mismatch)"
  - "a0ca050 P0 — MCP env = allowlist only; mcp_clients.py touch gated on AI-1 3-lane review"
  - ".github/workflows/nightly.yml s3-separability — CI-Linux evidence source"
estimated_complexity: SMALL (compose-only AC2) → MEDIUM (if AC3 P0 path triggers)
priority: MEDIUM (last red item of the Epic-11.3 close-out tail; ROOT compose 7/7-green on fresh boot)
blocks: []
unblocks:
  - Fresh ROOT-compose boot reaches 7/7 healthy (not 5/7) without MCP-init timeout
  - Nightly s3-separability (S-4 Phase 1) goes green
  - Closes the 3-story Epic-11.3 close-out tail
---
```

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Definition of Done

- `orchestrator-adapter` + `worker-wrapper` reach `Up (healthy)` on a
  fresh ROOT `docker compose up` (7/7, not 5/7) — verified by the AC5
  macOS repro and the AC8 nightly.
- AC1 nightly evidence recorded (Linux-clean vs Linux-flakes) and it
  drove the fix choice (compose-only vs P0-gated).
- Primary fix is the compose `x-healthcheck-mcp` start_period bump
  (≥100s) scoped to the 2 spawners; the shared `*healthcheck` anchor and
  the other 5 services are untouched.
- `mcp_clients.py` touched ONLY if AC1+AC5 proved a per-server >30s
  Linux failure — and if so, under P0 diff-audit (no `os.environ.copy()`;
  `_ENV_ALLOWLIST` intact; orch⇄worker byte-identical) + AI-1 3-lane
  review + green env-allowlist mirror contract test.
- Validation gates green: ruff/format clean, mypy 240=baseline 0-new,
  discipline 0, regression sweep no new fails.
- Code review discharged (default effort for compose-only; AI-1 3-lane
  MANDATORY if the P0 file was touched).
- `sprint-status.yaml` flips `11-3-10-mcp-init-flake-fix`:
  backlog → ready-for-dev → in-progress → review → done (after
  epic-11.3.9 in dependency order).
- Epic-11.3 close-out tail is COMPLETE (11.3.8 + 11.3.9 + 11.3.10 all done).
