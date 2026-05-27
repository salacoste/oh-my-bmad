# Story 11.3.4 — Separability stack task-progression failure (nightly S-3)

Status: **review** (dev-story 2026-05-27 — S1/S2/S3 root-caused + fixed + Docker e2e 6/6
green; S4 split to Story 11.3.5 per D3. Two root causes: S3 null-orchestrator missing
required `trace_id`; S1/S2 MCP env-strip dropping `CLAWHIP_BRIDGE_*` + strict-tuple-from-JSON
`plan` reject (latent production bug fixed via BeforeValidator). Pending: AC5 nightly +
AI-1 3-lane review.)

> **CREATE-STORY CORRECTION (2026-05-27):** The 2026-05-25 backlog draft's hypotheses
> were anchored on the *production* `orchestrator-adapter` / `worker-wrapper` services.
> Source analysis shows **S1/S2/S3 do not run those services at all** — they swap in
> purpose-built test-fixture stubs (`scripted-worker-stub`, `null-orchestrator`). The
> hypothesis space below (H1–H6) is rebuilt around the **stub** architecture, which is
> what actually drives the lifecycle in the failing jobs. H2 (env-propagation) was
> already REVERTED in `a0ca050` and lived in the production services, not the stubs —
> it is reframed (D1) rather than re-investigated.

## Story

**As** the platform maintainer
**I want** the nightly `s3-separability` job (S1/S2/S3/S4) to pass — tasks posted to the
separability compose stacks must progress through the canonical lifecycle
(`task.created` → `task.plan.ready` → … → `task.completed`)
**so that** the FR35 / NFR-M5 separability invariants (worker-swappable + orchestrator-
swappable spine) regain regression protection and the nightly baseline goes fully green.

## Background — how this surfaced

Story 11.3.3 took nightly from 1/4 → 3/4 green (`cb53279`, run 26373557044):
- **Fix-A** dropped a stray `--` in the idempotency-replay step (`nightly.yml`).
- **Fix-B** defaulted the four `_compose_env` sites' `OMB_*_UID/GID` to the host
  `os.getuid()/os.getgid()` so the container writes bind-mounted audit JSONL (mode
  `0o640`, `event_log.py:506`) as a uid the host can read. This also fixed the
  crash-injection "120 s hang" (same root cause — the container couldn't write the
  bind-mount, so `/tmp/ready` was never touched).

Fix-B removed a `PermissionError` that the separability tests hit when **reading** the
bind-mounted JSONL. That error had been **masking** a deeper failure: with the read now
working, the tests reveal that **tasks never progress past `task.created`**.

The AI-1 3-lane review of 11.3.3 (`a0ca050`) found and **reverted** a bundled
`env=os.environ.copy()` in the *production* `orchestrator-adapter` + `worker-wrapper`
`adapters/mcp_clients.py` (it leaked `WORKER_ANTHROPIC_API_KEY`/`github_token` past the
`_ENV_ALLOWLIST` into clawhip-bridge). That revert clears the old "H2" suspect — see D1.

## Architecture the dev MUST understand before touching anything

The separability tests **replace** a production service with a single-file fixture stub
to prove the spine doesn't care which image fills a slot. **The production services are
NOT exercised by S1/S2/S3.** Get this wrong and you will debug the wrong process.

| Job | Compose file | Swapped slot | Stub image | Stub source | Detect path | **Emit path** |
|---|---|---|---|---|---|---|
| **S1** cold worker swap | `tests/separability/docker-compose.s1.yml` | `worker-wrapper` ← `WORKER_IMAGE` | `scripted-worker-stub:latest` | `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` | tail JSONL (`_read_new_lines`, `json.loads`) | **clawhip-bridge MCP** `emit_event` (`_emit_via_clawhip:491`) |
| **S2** midflight swap | `tests/separability/docker-compose.s2.yml` | `worker-wrapper` ← `WORKER_IMAGE` | `scripted-worker-stub:latest` (+`SCRIPTED_WORKER_EVENT_DELAY_S=0.5`) | same as S1 | same as S1 | same as S1 (clawhip-bridge MCP) |
| **S3** orchestrator swap | `tests/separability/docker-compose.test.yml` | `orchestrator-adapter` ← `ORCHESTRATOR_IMAGE` | `null-orchestrator:latest` | `tests/fixtures/null_orchestrator/null_orchestrator.py` | tail JSONL (`_read_new_envelopes_since`, `from_canonical_json`) | **direct** `EventLogWriter.append` (no MCP) |
| **S4** metrics-subscriber optional | Phase 1 = **ROOT** `docker-compose.yml`; Phase 2 = `tests/separability/docker-compose.s4.yml` | (removes metrics-subscriber in Ph2) | **real** images | `services/*` | n/a (boot-only test) | n/a |

Stub images are built by `tests/separability/_build_scripted_worker.py` and
`_build_null_orchestrator.py` — **source-SHA-cached**: each hashes its fixture source
files and re-tags a cached `sha-<hex>` image as `:latest` on a cache hit, else rebuilds.
A stale cache is a real failure mode (see H5).

**Both detect paths read the same `task.created` envelope** that `registry-api` appends on
`POST /v1/tasks`. **The emit paths differ** (MCP vs direct write). Two structurally
different stubs stalling at the *same* point most strongly implicates the **shared input**
(the `task.created` envelope / the shared read primitive `read_log_lines`), not two
independent emit bugs — but the dev MUST confirm, not assume (AI-11 refutation discipline).

## Observed failure (nightly run 26373557044 @ `cb53279`)

```
S1: Failed: task 't-019e5bf5-...' did not reach task.completed within 60.0s; types seen=['task.created']
S2: TimeoutError: event 'task.plan.ready' for task 't-019e5bf6-...' not seen within 30.0s; types seen=['task.created']
S3: Failed: task 't-019e5bf6-...' did not reach task.completed within 30.0s; types seen=['task.created']
S4: Failed: Phase 1 compose up failed (rc=1); ... dependency failed to start: container omb-registry-state is unhealthy
```

S1/S2/S3: stack boots, `task.created` is emitted + materialized, but **no** stub-emitted
lifecycle event ever appears. S4 fails earlier and differently — `registry-state` never
goes healthy under the **ROOT** compose (Phase 1), so it is almost certainly a **separate
root cause** from the S1/S2/S3 task-progression stall.

## Hypotheses to investigate (H1–H6)

> Per Epic 11 retro **AI-11**: a REFUTED verdict needs *specific* evidence
> ("`read_log_lines` parsed the envelope and returned task_id=X" ), not "didn't seem to be it".

### H1 — shared read/detect path rejects the Phase-2 `task.created` envelope (PRIME suspect for S1/S2/S3)

Both stubs detect `task.created` by tailing JSONL. The null-orchestrator uses
`from_canonical_json` (`null_orchestrator.py:234`) and `read_log_lines`
(`:182`, via `registry_state.adapters.event_log`); the scripted-worker uses raw
`json.loads` for the live tail (`scripted_worker_stub.py:540`) but `read_log_lines` for the
startup scan (`:572`). If a Phase-2 change to the envelope (schema_version `1.1.0`,
required `trace_id`, new/renamed payload fields, canonical-encoding change) makes
`from_canonical_json`/`read_log_lines` **raise or skip** the `task.created` line, the
null-orchestrator silently never detects it. The scripted-worker's tolerant `json.loads`
path *should* still see it — so if **S1/S2 also stall**, H1 alone can't explain S1/S2 and
must combine with H2/H3.
**Probe:** in a local S3 repro, exec into the null-orchestrator and run
`python -c "from registry_state.adapters.event_log import read_log_lines; [print(e.type, getattr(e.payload,'task_id',e.payload)) for e in read_log_lines(<dated.jsonl>)]"`.
Confirm `task.created` is returned with a string `task_id`. Check the container's stderr
for `from_canonical_json` exceptions.

### H2 — scripted-worker's clawhip-bridge MCP emit fails (S1/S2 emit path)

The scripted-worker emits **only** via clawhip-bridge MCP (`emit_event`). Its
`_connect_mcp` (`scripted_worker_stub.py:483`) spawns clawhip-bridge as a stdio subprocess
with **`StdioServerParameters(command=command, args=args)` — no `env=`** (the same env-less
shape the services were reverted to in `a0ca050`). The compose sets
`CLAWHIP_BRIDGE_LOG_DIR`/`CLAWHIP_BRIDGE_ACTOR_KIND`/`CLAWHIP_BRIDGE_ACTOR_ID` on the
*container*, but with `env=None` the MCP SDK gives the subprocess only its **default-safe
env subset** — those `CLAWHIP_BRIDGE_*` vars are **not** inherited. clawhip-bridge then
falls back to its own defaults (which may write to a different dir, or assume a different
actor identity, or fail). If `session.started` (emitted first, `:629`) already fails, the
whole emit path is dead. **NOTE:** this is the stub's *own* connect — it was NOT touched by
the `a0ca050` revert (that touched `services/*`). Determine whether clawhip-bridge needs the
allowlisted `CLAWHIP_BRIDGE_*` vars propagated (via the `_ENV_ALLOWLIST` pattern, **never**
`os.environ.copy()`).
**Probe:** `docker compose -f docker-compose.s1.yml logs worker-wrapper` — look for
`mcp_client_connected`, `session_started`, `emitted`, or a clawhip-bridge stderr traceback.
Inspect where clawhip-bridge actually writes (is it the bind-mounted `events/` dir?).

### H3 — clawhip-bridge subprocess can't write the event log (S1/S2 emit path, write layer)

Even if MCP connects, clawhip-bridge (the subprocess) must append to the bind-mounted
`events/` dir. Fix-B set the **worker-wrapper container** to run as host-uid, but the
clawhip-bridge subprocess inherits that uid. If the dir/file perms or `CLAWHIP_BRIDGE_LOG_DIR`
resolution land it writing somewhere the host test can't see (or can't write at all), no
lifecycle events appear. Relationship to H2: H2 = "doesn't get the env var"; H3 = "has the
right target but can't write it".
**Probe:** after a local S1 run, `ls -la <OMB_S1_DATA_DIR>/registry/events/`; check whether
any clawhip-bridge-actor envelopes landed; check container stderr for `PermissionError`.

### H4 — null-orchestrator direct write fails OR dedupe over-suppresses (S3 emit path)

The null-orchestrator writes via `EventLogWriter(base_dir).append` (`:380`,`:291`). Two
sub-modes: (a) the write fails (perm/path); (b) `_scan_processed_task_ids` (`:153`)
wrongly classifies the task as already-processed and skips it. (b) is unlikely on a fresh
per-test tmp dir but must be ruled out (e.g. if a prior partial run left lifecycle events).
**Probe:** `docker compose -f docker-compose.test.yml logs orchestrator-adapter` — expect
`startup: N task_id(s) already processed` then `emitted task.planning.started ...`. If
startup logs N>0 on a fresh run → (b). If no `emitted` line and no error → detection (H1).
If `emitted` logs but no file lines → write (a).

### H5 — stale stub image (cache poisoning)

`_build_*` re-tag a `sha-<hex>` cached image as `:latest`. If a fixture-source edit didn't
change the hashed file set, or a prior broken build got cached, the nightly may boot a
**stale** stub. `_build_scripted_worker.build_if_missing(force=...)` supports `--force`;
`_build_null_orchestrator` does not. Confirm the nightly/test actually rebuilds current
source.
**Probe:** check the SHA tag vs current source hash; force-rebuild both stubs locally and
re-run; diff behavior.

### H6 — S4 `registry-state` unhealthy under ROOT compose (SEPARATE root cause)

S4 Phase 1 boots the **ROOT** `docker-compose.yml`. Its `registry-state` never goes
healthy, so `compose up` fails before any task is posted — this is a **boot/healthcheck**
problem, not a task-progression one. Likely class: volume ownership (if root compose uses a
named volume, Fix-B's bind-mount uid override does **not** apply) or a missing
`REGISTRY_STATE_AUTO_CREATE_SCHEMA`/start_period under the production compose. The AC2
`REGISTRY_STATE_LIFESPAN_TRACE=1` instrumentation (shipped in 11.3.3, gated) can be enabled
in the root compose for the probe.
**Probe:** local `OMB_*` ROOT-compose boot; `docker inspect` the registry-state volume
ownership; enable `REGISTRY_STATE_LIFESPAN_TRACE=1` and read which lifespan phase stalls.

## Acceptance criteria

### AC1 — Local repro of the S1/S2/S3 stall

Reproduce the `task.created`-stall locally for **at least S1 and S3** (different emit
paths — covering both is what disambiguates shared-cause from independent-cause). Capture
the swapped-slot container logs (`worker-wrapper` for S1, `orchestrator-adapter` for S3)
**and** the clawhip-bridge subprocess stderr for S1. Record literal log excerpts in the Dev
Agent Record. Note the macOS-vs-Linux caveat (VirtioFS masks raw-uid perms — if a perm
hypothesis (H3) won't reproduce on macOS, say so explicitly and lean on nightly/Linux CI).

Self-verification: Dev Agent Record contains the captured logs and the exact compose
commands used.

### AC2 — Each hypothesis CONFIRMED / REFUTED with specific evidence

For **H1–H6**, record: (1) probe executed, (2) literal observation, (3) verdict. Expected
shape: **one or two** confirmed for the S1/S2/S3 stall (a shared cause, or a split
S1/S2-vs-S3 cause) **plus H6 separately** for S4. If the S1/S2/S3 cause turns out split
(different for the MCP-emit stub vs the direct-write stub), document both. Refutations must
be specific (AI-11).

### AC3 — Fix applied; lifecycle progresses; S4 healthy

- S1/S2/S3: tasks reach `task.completed`; S2 additionally observes `task.plan.ready` within
  its window.
- S4: `registry-state` reaches healthy under the ROOT compose (Phase 1) and Phase 2.
- The fix respects scope discipline: prefer touching **fixture stubs / compose / test
  harness** over production `services/*`. If a production change is genuinely required
  (e.g. clawhip-bridge env handling), it goes through the `_ENV_ALLOWLIST` pattern and is
  called out explicitly in the Dev Agent Record + flagged for the 3-lane review.

Self-verification: `just test-separability` (or the targeted S1/S2/S3/S4 pytest nodes)
passes locally to the extent the platform allows; nightly green per AC5.

### AC4 — No regression in the other nightly jobs or PR-gate CI

idempotency-replay, migrator-integration, crash-injection stay green; the PR-gate `ci`
workflow stays green. **Do NOT revert Story 11.3.3 Fix-A / Fix-B / AC2** — they are correct
and verified. **Do NOT reintroduce `env=os.environ.copy()`** anywhere (the `a0ca050` P0).

### AC5 — Nightly verification

After commit + push: `gh workflow run nightly.yml`, wait for completion, confirm **all 4
jobs PASS** (including `s3-separability`). Record the run id + conclusion in the Dev Agent
Record.

Self-verification: `gh run view <run-id> --json conclusion` → `success` for all 4 jobs.

### AC6 — Validation gates green (Phase 2 baseline)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict packages/ services/ scripts/   # tree baseline ~215 (test-file errors); no NEW errors
uv run python scripts/check_imports.py
uv run python scripts/check_event_registry.py
uv run python scripts/check_single_writer.py
uv run pytest -x -q -m "not slow"                    # ~3125 baseline; no regressions
```

All exit 0 / no new failures. If a regression test is added, the count delta should be
small (+1 to +5).

## Decisions (resolve DURING implementation)

### D1 — clawhip-bridge env propagation for the scripted-worker stub (if H2 confirmed)

**Trigger:** H2 confirmed (clawhip-bridge subprocess doesn't receive `CLAWHIP_BRIDGE_*`).
**Options:**
- (a) Pass an **allowlisted** `env=` to the stub's `StdioServerParameters` containing only
  the `CLAWHIP_BRIDGE_*` (+ `PATH`/`HOME`/`PYTHONPATH`/TLS) vars — mirror the
  `_default_env_allowlist()` pattern from `clawhip_client.py`. **NEVER** `os.environ.copy()`.
- (b) Have clawhip-bridge default its log dir / actor to the production path when the env
  var is absent (if it doesn't already), so the stub needs no env at all.
**Bias:** (a) if the subprocess genuinely needs operator-supplied identity/dir; (b) if a
default is correct and lower-risk. Either way the secret-leak invariant (no HMAC/AWS/OPENAI
keys reach clawhip-bridge) MUST hold — re-grep after the fix.

### D2 — shared-cause vs independent-cause for S1/S2 vs S3

**Trigger:** after AC2. **If shared** (H1) → one fix covers all three. **If split**
(S3=H1/H4 read/write; S1/S2=H2/H3 MCP emit) → two fixes, ideally in separate commits for
bisectability. Conservative bias: don't paper over a split with one broad change.

### D3 — S4 scope (H6) — fold in or split out

**Trigger:** after H6 verdict. If S4's fix is trivial + isolated (compose/uid/healthcheck
knob) → fold into this story. If it requires a ROOT-compose volume-ownership redesign →
file `11-3-5-s4-root-compose-healthcheck.md` and close 11.3.4 on S1/S2/S3 alone, noting S4
remains red with a tracked follow-up. Document the choice.

## Constraints

- **Epic 11 retro addendum AI-1 mandate APPLIES** — cross-cutting (multi-service compose +
  fixtures + possibly clawhip-bridge) → **3-lane adversarial review at pass-1** (Blind
  Hunter = `code-reviewer`; Edge Case Hunter + Acceptance Auditor = `general-purpose`).
  12 consecutive Epic 11 L1 validations entering this story; the streak protects nothing.
- **`_ENV_ALLOWLIST` security contract preserved** — the clawhip-bridge subprocess must
  receive **no** HMAC keys, AWS creds, OPENAI key, `WORKER_ANTHROPIC_API_KEY`, or
  `github_token`. Do **NOT** reintroduce `env=os.environ.copy()` in any MCP client adapter
  or stub. If env propagation is needed, use the allowlist pattern (`clawhip_client.py:88`).
- **FR26 single-writer invariant preserved** (the S-3 stack's test-fixture multi-writer to
  one JSONL is an explicitly-scoped Phase-1 exception; do not extend it).
- **Do NOT revert 11.3.3 Fix-A / Fix-B / AC2.**
- **NFR-R1 / NFR-R2 (crash-injection) must stay green** — Fix-B fixed it; don't regress.
- **AI-6** (BaseException-leak audit): if you touch any `try/finally` in stub or service
  lifespan, audit acquisition-inside-try discipline.
- **AI-7** (test-realism): any regression test added must fail against a known-buggy
  substitute implementation.
- Prefer fixture/compose/harness changes over production `services/*`; flag any production
  touch explicitly for the review.

## Dev Notes

### Source map (file:line guardrails)

- **S1/S2 stub:** `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py`
  - detect: `_read_new_lines` (`:521`, raw `json.loads`), startup scan `_scan_emitted_events`
    (`:561`, uses `read_log_lines`)
  - MCP connect: `_connect_mcp` (`:476`) — **env-less** `StdioServerParameters` at `:483`
  - emit: `_emit_via_clawhip` (`:491`, `emit_event` tool, `caller_trace_id` required since 9.5)
  - main loop: `run_scripted_worker` (`:591`); `session.started` first (`:629`), then ready, then tail
  - scenario: `simple_green` (`:122`) → planning.started→plan.ready→execution.started→step.completed→completed
- **S3 stub:** `tests/fixtures/null_orchestrator/null_orchestrator.py`
  - detect: `_read_new_envelopes_since` (`:198`, `from_canonical_json`), startup scan
    `_scan_processed_task_ids` (`:153`, `read_log_lines`)
  - emit: `_emit_lifecycle_for_task` (`:240`) → **direct** `EventLogWriter.append`
  - main loop: `run_null_orchestrator` (`:353`); dedupe set built at `:377`
- **shared read primitive:** `registry_state.adapters.event_log.read_log_lines` +
  `events.canonical.from_canonical_json` — H1 lives here.
- **`task.created` producer:** `registry-api` `POST /v1/tasks` → `EventLogWriter.append`
  (audit JSONL at mode `0o640`, `event_log.py:506`).
- **stub builders:** `tests/separability/_build_scripted_worker.py` (`--force` supported),
  `_build_null_orchestrator.py` (no force) — H5.
- **production MCP adapters (reverted, DO NOT re-add env):**
  `services/orchestrator-adapter/.../adapters/mcp_clients.py:76`,
  `services/worker-wrapper/.../adapters/mcp_clients.py:81` — both env-less per `a0ca050`.
- **allowlist canon:** `mcp-servers/task-registry/.../adapters/clawhip_client.py` —
  `_ENV_ALLOWLIST` frozenset + `_default_env_allowlist()` (`:88`).
- **compose files:** `tests/separability/docker-compose.{s1,s2,test,s4}.yml`; ROOT
  `docker-compose.yml` (S4 Phase 1).
- **tests:** `tests/separability/test_s{1,2,3}_*.py` (uid guards at the Fix-B sites),
  `test_s4_metrics_subscriber_optional.py`.

### Why the original draft's H1/H3 were wrong

The 2026-05-25 draft hypothesized the production `orchestrator-adapter` "not consuming
task.created" (H1) and the production `worker-wrapper` "can't reach MCP" (H3). Source
analysis confirms **neither production service runs in S1/S2/S3** — the `WORKER_IMAGE` /
`ORCHESTRATOR_IMAGE` overrides replace them with the stubs above. The explore pass that
flagged "worker-wrapper has no consume loop" is *true of the production service* but
*irrelevant to S1/S2/S3*, which never boot it. Debug the **stubs**.

### Testing standards

`tests/separability/conftest.py` + the per-test fixtures build the stub images and export
`OMB_S{1,2,3,4}_DATA_DIR` / `OMB_*_UID/GID` before `docker compose up`. Reuse those; don't
hand-roll compose invocations that skip the uid exports (you'll re-introduce the Fix-B perm
failure). Honor the Windows-safe `os.getuid() if hasattr(os, "getuid") else _CONTAINER_UID`
guard added in `a0ca050`.

### References

- [Source: 11-3-3-nightly-deeper-diagnosis.md — Fix-A/B/AC2 + AI-1 review findings]
- [Source: epic-11-retro-addendum-2026-05-24.md — AI-1/AI-6/AI-7/AI-11 mandates]
- [Source: tests/fixtures/{scripted_worker_stub,null_orchestrator}/*.py — stub emit paths]
- [Source: tests/separability/docker-compose.{s1,s2,test,s4}.yml — slot swaps]
- [Source: clawhip_client.py — `_ENV_ALLOWLIST` security contract]

## Frontmatter

```yaml
---
story_id: 11.3.4
story_key: 11-3-4-separability-task-progression
parent_epic: 11
phase: 2
fr_refs: [FR35, NFR-M5, FR34, NFR-M4]
nfr_refs: [NFR-M5, NFR-M4]
arch_refs:
  - "Story 11.3.3 — nightly diagnosis (Fix-A + Fix-B → 3/4 green; this is the unmasked 4th failure)"
  - "Story 2.15 — S-3 null-orchestrator fixture (orchestrator slot swap)"
  - "Story 5.16 / 5.17c — S-1/S-2 scripted-worker-stub fixture (worker slot swap)"
  - "a0ca050 — env=os.environ.copy() reverted in production MCP adapters (clears old H2)"
  - "clawhip_client.py — _ENV_ALLOWLIST security contract"
estimated_complexity: MEDIUM
priority: medium (nightly 3/4 green; no production impact; s3-separability is the last red job)
blocks: []
unblocks:
  - Fully-green nightly baseline for Epic 12+ regression detection
---
```

## Tasks / Subtasks

- [x] AC1 — Repro + capture: root cause established by static/local-probe AND confirmed by
      background-debugger Docker e2e (S1/S2/S3 = 6/6 green). macOS/VirtioFS caveat noted.
- [x] AC2 — H1–H6 verdicts (AI-11-specific evidence):
  - [x] H1 shared read/detect — REFUTED (detection works; imports resolve; 1.0.0 registered; crash is in emit)
  - [x] H2 scripted-worker clawhip MCP emit — CONFIRMED (TWO bugs: MCP env-strip drops `CLAWHIP_BRIDGE_*` → bridge exits; strict-tuple-from-JSON `plan` reject)
  - [x] H3 clawhip subprocess env/write — CONFIRMED as env-strip (ACTOR_KIND missing); log-dir-specifically REFUTED (var was present)
  - [x] H4 null-orchestrator direct write/dedupe — REFUTED (crash is pre-write; trace_id TypeError)
  - [x] H5 stale stub image — addressed (debugger force-rebuilt both stubs)
  - [x] H6 S4 registry-state unhealthy under ROOT compose — CONFIRMED separate cause → split to Story 11.3.5 (D3)
- [x] AC3 — Fixes applied; S1/S2/S3 reach task.completed (6/6 green); S4 split per D3
  - [x] S3: null-orchestrator trace_id propagation + regression test
  - [x] S1/S2: `_clawhip_env()` allowlist (connect) + `TaskPlanReadyPayload.plan` BeforeValidator (emit) + 5+3 regression tests
- [x] AC4 — No regression: 3006 non-slow pass; events 446; clawhip-emit consumers 189; no `os.environ.copy()`; Fix-A/B/AC2 untouched; SPINE_PATHS sentinel preserved (debugger exclusion reverted)
- [ ] AC5 — `gh workflow run nightly.yml` → S1/S2/S3 green (S4 tracked in 11.3.5) — POST-COMMIT
- [x] AC6 — Gates green: ruff/format clean; mypy 0 new errors (2 pre-existing baseline); discipline scripts exit 0; 6 new regression tests pass
- [ ] AI-1 — 3-lane adversarial review at pass-1 — the `/bmad-code-review` step (post dev-story)

## Definition of Done

- `s3-separability` nightly job PASS (S1/S2/S3 reach `task.completed`; S4 healthy) — OR S4
  split to a tracked follow-up per D3 with S1/S2/S3 green.
- H1–H6 verdicts recorded with specific evidence in the Dev Agent Record.
- Other 3 nightly jobs + PR-gate `ci` remain green.
- `_ENV_ALLOWLIST` secret-leak invariant re-verified after any clawhip-bridge change.
- AI-1 3-lane review complete; findings batch-applied per standing policy.
- `sprint-status.yaml` `11-3-4-...: ready-for-dev → … → done`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7[1m] (dev-story 2026-05-27)

### Code-Review Findings (`/code-review` high-recall pass, 2026-05-27)

7-angle recall review (correctness + cleanup + altitude finders → verify). 2 findings
CONFIRMED + applied; the rest REFUTED with evidence.

- [x] **[Applied] `_connect_mcp` genericity** — the generic helper hardcoded
  `env=_clawhip_env()`. Added an `env: dict|None=None` keyword param and moved
  `_clawhip_env()` to the clawhip call site, so a future second MCP server supplies its
  own allowlist instead of silently inheriting clawhip's.
- [x] **[Applied] allowlist-copy documentation** — documented that `_CLAWHIP_ENV_ALLOWLIST`
  is a *deliberate* copy (the stub must not import from `mcp-servers/*` / `services/*` —
  the separability contract it proves), deliberately narrower than the production
  `_ENV_ALLOWLIST` (mirrors only what clawhip-bridge `__main__` reads), preventing a future
  "DRY it via import" that would break the separability proof.
- [Refuted] "OS basics (LOGNAME/USER/SHELL) dropped by explicit env" — the MCP SDK
  **merges** `{**get_default_environment(), **server.env}` (verified at
  `mcp/client/stdio/__init__.py:127`), so OS basics are still supplied.
- [Refuted] "missing TLS/`REGISTRY_EVENTS_DIR` breaks clawhip-bridge" — clawhip-bridge
  `__main__` reads only `CLAWHIP_BRIDGE_ACTOR_KIND/ACTOR_ID` (required) + `LOG_DIR`
  (optional); it does no network I/O. The minimal allowlist is sufficient and intentional.
- [Refuted] "generalize the tuple coercion to `EventEnvelope.create`/the MCP boundary" —
  a boundary-level coercion would over-broadly weaken `strict=True` for *all* payloads; the
  per-field `BeforeValidator` is the correctly surgical depth (and `plan` is the only
  tuple-typed payload field today). Noted: tuple-typing any future payload field requires
  the same `mode="before"` coercion.

Post-review gates: ruff/format clean; mypy 0 new errors (2 pre-existing baseline); the
fixes are behavior-preserving (same `_clawhip_env()` reaches clawhip) so the 6/6 Docker
e2e result stands.

> **Review-type note:** this was the built-in `/code-review` (recall pass). The Epic 11
> retro **AI-1 3-lane adversarial mandate** (Blind/Edge/Acceptance via `/bmad-code-review`)
> remains the canonical review for the story's DoD.

#### `/code-review` pass 2 (2026-05-27) — 2 fixes applied

Re-run on the post-pass-1 state (fresh scrutiny of the test files + the `_connect_mcp`
refactor). 1 strong + 1 minor applied; the rest refuted against verified repo facts.

- [x] **[Applied] stub allowlist drift-binding contract test** — the stub's
  `_CLAWHIP_ENV_ALLOWLIST` is a third, independent copy of the clawhip-bridge required-var
  surface with NO guard binding it to the canonical set (the production pair has the
  byte-identical mirror test, but the stub can't be in it — separability). Added
  `test_scripted_worker_stub_allowlist_contains_required_clawhip_vars` to
  `tests/contract/test_clawhip_client_env_allowlist_mirror.py` (a contract test may legally
  cross into `tests/fixtures/*`). Prevents a future clawhip-bridge required-var addition from
  silently re-introducing the S-1/S-2 `task.created` stall.
- [x] **[Applied] dead import shim removed** — `test_null_orchestrator_emit.py` used a
  `try/except ImportError` around `new_task_id`; verified `from events import new_task_id`
  is canonical, so simplified to a direct import.
- [Refuted] "secret list should reuse `secret_hygiene.SECRET_PATTERNS`" — the existing
  `test_allowlist_contains_no_secret_shaped_names` substring guard (rejects any
  `KEY/TOKEN/SECRET/...`-named var) is *more* robust than enumerating names; kept.
- [Refuted] "drop PYTHONPATH (trust surface)" — can't confirm the container's import
  reliance without a Docker re-verify; removing risks regressing the just-verified-green
  fix. Retained.
- [Refuted] "comment block excessive" — the rationale (SDK-merge, deliberate-copy,
  separability) was explicitly *requested* in pass 1; kept.
- [Refuted] "generalize the tuple coercion" — confirmed `plan` is the **only** tuple-typed
  field in the entire events package; the per-field validator is provably complete.
- [Refuted] "pre-fix raises ValidationError, docstring says TypeError" — verified by direct
  probe: omitting the required keyword-only `trace_id` param of `EventEnvelope.create()`
  raises **`TypeError`**; the docstring is correct.

Post-pass-2 gates: 106 new/touched tests pass; ruff/format clean; `check_imports` exit 0.

### Debug Log References

**Static + local-probe diagnosis (no Docker needed for root-cause; all probes via `uv run python`).**

**S3 (null-orchestrator) — root cause CONFIRMED (two-part, single fix):**
- `EventEnvelope.create()` made `trace_id` a **required** kwarg in Story 9.7 (`envelope.py:406`).
  The null-orchestrator's `_emit_lifecycle_for_task` called `create()` **without** `trace_id`
  on all 4 lifecycle events → `TypeError: ... missing 1 required keyword-only argument:
  'trace_id'` on the first detected `task.created` → the orchestrator process crashes after
  it already touched `/tmp/ready` (healthy), so the stack boots but the task stalls at
  `task.created`. Reproduced deterministically:
  `EventEnvelope.create(... no trace_id ...)` → `TypeError`.
- Verified `schema_version="1.0.0"` is still registered for all 4 lifecycle types
  (`['1.0.0','1.0.1','1.1.0']`) and that the null-orch import surface DOES trigger
  `ensure_registered()` (via `registry_state.adapters.event_log`), so 1.0.0 is fine and no
  schema bump is needed.
- **FIX APPLIED:** propagate the originating envelope's trace_id —
  `trace_id=task_created_env.trace_id` on all 4 `create()` calls
  (`tests/fixtures/null_orchestrator/null_orchestrator.py`). H4 (write/dedupe) REFUTED — the
  crash is pre-write; H1 (read path) REFUTED for S3 — detection works, the crash is in emit.

**S1/S2 (scripted-worker) — root cause CONFIRMED, and it is a LATENT PRODUCTION BUG:**
- The scripted-worker emits via clawhip-bridge MCP `emit_event`. clawhip-bridge calls
  `EventEnvelope.create(payload=<dict from JSON wire>)` → `model_validate` (strict).
- `TaskPlanReadyPayload` is `ConfigDict(frozen=True, strict=True, extra="forbid")` with
  `plan: tuple[PlanStep, ...]` (`payloads.py:107,111`). **strict=True does NOT coerce
  list→tuple.** MCP delivers payloads as **JSON (arrays = lists, never tuples)**, so a
  `task.plan.ready` carrying a populated `plan` **fails validation** at emit:
  `Input should be a valid tuple [type=tuple_type]`. Reproduced: `model_validate({...,
  "plan":[{...}]})` → FAIL; `{...,"plan":({...},)}` → OK.
- **This is not fixture-only:** the real `orchestrator-adapter` ALSO emits `task.plan.ready`
  via clawhip with `build_plan_ready_payload` (`main.py:254-257`). Any plan-bearing
  `task.plan.ready` over the clawhip JSON boundary is rejected. PR-gate `ci` never exercises
  the stdio-JSON round-trip with a populated plan, so it stayed green — separability nightly
  is exactly the harness that surfaces it.
- Read-side is safe: `from_canonical_json` returns a `_FrozenDict` payload (no strict
  re-validation), so accepting a list at emit and writing it does not break materialization.
- **Open runtime question (needs Docker to fully close):** nightly shows S1 `types
  seen=['task.created']` — i.e. NOT even `task.planning.started` (which validates fine).
  Two non-exclusive explanations remain: (a) the scripted-worker's env-less clawhip-bridge
  subprocess writes events to a dir other than the bind-mounted `EVENT_LOG_DIR` the test
  inspects (H2/H3), so NOTHING from the worker is visible; (b) snapshot timing. The tuple
  bug is confirmed regardless, but whether it is the *sole* S1/S2 cause needs a local repro.

**Fix-location decision for S1/S2 (D-new, pending):** the correct minimal fix is a
`field_validator(mode="before")` (or `BeforeValidator`) on tuple-typed payload fields that
coerces list→tuple — preserves the frozen-tuple output + `extra="forbid"`, only loosens
*input* shape for the JSON boundary. This is a shared-`packages/events` (production) change
touching the deliberate `strict=True` contract → AI-1 3-lane review applies. Surfacing to
operator before modifying the project-wide invariant (per story constraint "flag any
production touch").

### Docker e2e confirmation (background debugger, 2026-05-27) — S1/S2/S3 = 6/6 GREEN

The debugger ran the actual separability pytest nodes (force-rebuilding both stub images)
and confirmed all of S1/S2/S3 pass. It also uncovered the *true* S1/S2 connect-time
blocker, which my tuple bug alone did not explain (`types seen=['task.created']` = nothing
from the worker landed at all):

- **H2/H3 CONFIRMED (the real S1/S2 blocker):** `StdioServerParameters(env=None)` makes the
  MCP SDK call `get_default_environment()` (`mcp/client/stdio/__init__.py:51`), which on
  POSIX forwards ONLY `HOME/LOGNAME/PATH/SHELL/TERM/USER`. So the scripted-worker's
  clawhip-bridge subprocess never received `CLAWHIP_BRIDGE_ACTOR_KIND/ACTOR_ID/LOG_DIR` →
  clawhip-bridge exited at startup (`CLAWHIP_BRIDGE_ACTOR_KIND is required`) → MCP
  `Connection closed` before any event was emitted. The log-dir-specifically hypothesis was
  REFUTED (the var WAS in the container env; the missing one that crashed it was ACTOR_KIND).
  **FIX:** `_clawhip_env()` allowlist forwards only `CLAWHIP_BRIDGE_*` + OS basics to the
  subprocess (no secrets, no `os.environ.copy()`).
- **Tuple bug (my fix) is complementary and still required:** once the worker connects, the
  `simple_green` `task.plan.ready` carries a populated `plan` (list over JSON) — the
  `BeforeValidator` is what lets clawhip-bridge accept it. Both fixes are needed for green.

**Debugger over-reach corrected by dev:** the debugger had added a permanent
`SPINE_PATHS` exclusion for `orchestrator-adapter/.../mcp_clients.py` to test_s1/test_s2.
That was an artifact of running the sentinel mid-story (HEAD=a0ca050, whose `HEAD~1..HEAD`
window captures the 11.3.3 security revert). **REVERTED** — once 11.3.4 is its own commit,
`git diff HEAD~1 HEAD -- *SPINE_PATHS` is empty (verified: none of 11.3.4's files touch a
worker-facing spine path). Keeping the exclusion would have permanently weakened the
separability/security sentinel against future accidental orchestrator-adapter spine edits.

### D3 resolution — S4 SPLIT to follow-up Story 11.3.5

S4 (`test_s4_metrics_subscriber_optional.py`, Phase 1 = ROOT `docker-compose.yml`) fails
with `registry-state unhealthy` — a **boot/healthcheck** failure under the ROOT compose's
**named volume** (Fix-B's bind-mount uid override does not apply there). This is a distinct
root cause from the worker/orchestrator *swap-fixture* task-progression bug 11.3.4 targets.
Per D3's conservative bias + the DoD OR-clause, S4 is split to **`11-3-5-s4-root-compose-
healthcheck.md`** (backlog). 11.3.4 closes on S1/S2/S3 green.

### Completion Notes List

- [x] S3 root cause confirmed + fix applied (null-orchestrator `trace_id` propagation) + regression test.
- [x] S1/S2 root cause: TWO complementary bugs — (1) MCP env-strip dropping `CLAWHIP_BRIDGE_*`
      (fix: `_clawhip_env()` allowlist), (2) strict-tuple-from-JSON `plan` reject (fix:
      `BeforeValidator`). Both fixed; S1/S2/S3 = 6/6 green locally (debugger Docker e2e).
- [x] Reverted the debugger's over-broad SPINE_PATHS exclusion; verified sentinel passes post-commit.
- [x] Added clawhip-env security regression tests (secrets never forwarded — AI-8 lineage).
- [x] AC6 gates green: ruff/format clean; mypy 0 new errors (2 pre-existing baseline confirmed via stash);
      discipline scripts exit 0; broad regression **3006 passed, 0 fail** (Docker dirs excluded; run separately).
- [x] D3: S4 split to Story 11.3.5 (separate ROOT-compose named-volume healthcheck cause).
- [ ] AC5 nightly verification + AI-1 3-lane review — post-commit / code-review step.

### File List

- `tests/fixtures/null_orchestrator/null_orchestrator.py` (modified — trace_id propagation, S3 fix)
- `tests/fixtures/null_orchestrator/test_null_orchestrator_emit.py` (new — S3 regression test)
- `packages/events/src/events/payloads.py` (modified — `TaskPlanReadyPayload.plan` list→tuple BeforeValidator, S1/S2 production fix)
- `packages/events/src/events/test_payload_validators.py` (modified — 5 plan-coercion regression tests)
- `tests/fixtures/scripted_worker_stub/scripted_worker_stub.py` (modified — `_clawhip_env()` allowlist for the clawhip-bridge subprocess, S1/S2 connect fix)
- `tests/fixtures/scripted_worker_stub/test_clawhip_env_allowlist.py` (new — clawhip-env security regression tests)
- `tests/separability/test_s1_cold_worker_swap.py` (no net change — debugger exclusion reverted)
- `tests/separability/test_s2_midflight_swap.py` (no net change — debugger exclusion reverted)
