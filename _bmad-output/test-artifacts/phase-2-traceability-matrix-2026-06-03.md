# Phase 2 — Requirements → Test Traceability Matrix & Quality Gate

- **Date:** 2026-06-03
- **Scope:** Phase 2 (Observability), FR53–FR71a + NFR-O7/O8/O10, NFR-S9/S10/S11, NFR-R7/R8, NFR-M4/M5
- **HEAD:** `7799af0` (Epic 12.3c merge)
- **Method:** BMad `testarch-trace` style — 6 parallel sub-agents (one per Phase-2 epic) mapped each requirement to its concrete covering test(s)/CI proof, with a coverage verdict. Verdicts re-grounded in source (file::function) by each agent.
- **Companion docs:** `epics.md` §"Phase 2 Ship-Blocker Checklist" (+ 2026-06-03 verification stamp); `sprint-status.yaml`.

## Verdict legend
- **FULL** — requirement has a dedicated, asserting test (positive + usually negative/edge).
- **PARTIAL** — covered, but a clause/linkage lacks a direct test.
- **RELEASE-TIME** — provable only by a tagged-release CI run (Sigstore/GHCR/SLSA); not unit-testable.
- **LIVE-ONLY** — needs a real external dependency (S3 bucket); operator/nightly proof.
- **NONE** — no coverage (none found in Phase 2).

---

## Epic 8 — γ Supply-chain hardening

| REQ | Verdict | Covering test / pipeline proof | Note |
|-----|---------|--------------------------------|------|
| FR53 cosign keyless sig | RELEASE-TIME | `release.yml:255-271` + `:445-459` (cosign sign --yes, OIDC keyless) | verify side `justfile:493-499` |
| FR54 SLSA-L2 provenance | RELEASE-TIME | `release.yml:186-193` + `:405-412` (attest-build-provenance) | verify `justfile:509-514` |
| FR55 CycloneDX SBOM | RELEASE-TIME | `release.yml:207-214` + `:298-308` (anchore/sbom-action + cosign attest) | verify `justfile:524-529` |
| FR56 digest pinning | FULL | `.env.example:130-142` (OMB_IMAGE_DIGEST_*) + `justfile:478-488` | 8 services, digest resolution |
| FR56a verify-images gate + signature_rejected | PARTIAL | `justfile:423-544` + `tests/integration/test_emit_signature_rejected.py` + `packages/events/.../test_deployment.py` | **GAP: gate does not auto-emit the event; linkage manual/untested E2E** |
| NFR-S9 verify before pull → reject event | PARTIAL | `justfile:493-514` (cosign+SLSA verify blocks pull) | same gate→event decoupling as FR56a |
| NFR-S11 SBOM verifiable + license-incompat blocks publish | PARTIAL | `justfile:524-529` + `release.yml:298-308` (SBOM gen/attach fail = release fail) | **GAP: license-incompatibility gate UNIMPLEMENTED (no scanner in CI/release)** |

**Epic 8:** FULL 1 · PARTIAL 3 · RELEASE-TIME 3 · NONE 0.

## Epic 9 — α trace_id propagation kernel

| REQ | Verdict | Covering test | Note |
|-----|---------|---------------|------|
| FR57 schema 1.1.0 + required trace_id (UUIDv7) | FULL | `events/test_envelope.py::test_default_schema_version_is_1_1_0`, `::test_missing_trace_id_raises_validation_error`, `TestTraceIdShape::*`; `test_schema_registry.py` | registry 1.1.0 |
| FR58 entry points bind trace_id first | FULL | registry-api `test_middleware.py` (HTTP mint/preserve); telegram `test_allowlist.py` (tg:update_id); console `test_trace_command.py`; contract `test_mcp_tool_schemas.py` (caller_trace_id) | all 4 entry points |
| FR59 worker --trace-id flag propagation | FULL | worker-wrapper `test_claude_code_runner.py` (--trace-id argv), `test_run_task.py::test_run_task_threads_same_trace_id_to_all_emissions`; orchestrator `test_omc_runner.py` (OMB_TRACE_ID) | |
| FR59a /trace + headers + cursor | FULL | registry-api `test_trace.py` (ordered, X-Trace-Truncated, after_event_id cursor, synthetic); telegram + console `test_trace_command.py` | pagination + headers tested |
| NFR-O7 complete cross-service causal chain | PARTIAL | `test_envelope.py` (non-null) + `test_trace.py` (mono_ns order, single-service) | **GAP: no E2E test threads one trace_id across multiple services + queries unified chain via GET /trace** |

**Epic 9:** FULL 4 · PARTIAL 1 · NONE 0.

## Epic 10 — β metrics-subscriber

| REQ | Verdict | Covering test | Note |
|-----|---------|---------------|------|
| FR60 read-only JSONL subscriber, no other-service instrumentation | FULL | `tests/separability/test_s4_metrics_subscriber_optional.py` + `metrics-subscriber/test_app_main.py`; enforced by `check_single_writer.py` + `check_imports.py --self-test` | |
| FR61 /metrics internal-only | FULL | `test_app_main.py::test_metrics_endpoint_returns_valid_exposition` + external-bind heuristic tests; no `ports:` in compose | |
| FR62 counters/gauges/histograms | FULL | `test_metrics_state.py::*` + `test_metrics_integration.py` | cardinality pinned at 64 |
| FR62a S-4 separability | FULL | `test_s4_metrics_subscriber_optional.py` (2-phase overlay) | |
| NFR-O8 /metrics p95 <100ms | FULL | `test_metrics_endpoint_benchmark.py::test_metrics_endpoint_p95_under_100ms` (asserts p95<0.1s) | @slow benchmark |
| NFR-O10 derived projection, no other-service calls | FULL | `tests/integration/test_metrics_cardinality.py` + `check_imports.py --self-test` (IMP001 fixture) + S-4 | static + runtime |

**Epic 10:** FULL 6 · PARTIAL 0 · NONE 0. (No gaps.)

## Epic 11 — ξ Approval inbox + HMAC

| REQ | Verdict | Covering test | Note |
|-----|---------|---------------|------|
| FR63 /approvals pinned inbox routing | FULL | registry-api `test_approvals.py::TestPostInbox::*` + `tests/integration/test_journey_approval_inbox.py` (10-event replay) | link-back asserted |
| FR64 approval.granted → task.approval_signed (HMAC) | FULL | `test_decisions_signing.py` + `test_approval_signing.py::TestGoldenVector` (openssl external vector) | reject/stop not signed |
| FR65 offline verify-approval | FULL | `tests/integration/test_verify_approval_offline_recipe.py::*` (match→0, mismatch→1, ≥3 steps) | no FastAPI/SQLAlchemy import |
| FR65a key rotation + prior-key verify | FULL | `test_key_rotation.py` (exactly-one key.rotated, idempotent) + offline recipe `--key-file` test | |
| NFR-S10 key isolation, offline | FULL | `test_hmac_key_isolation.py` (canary grep: JSONL/SQLite/snapshot/structlog clean) | **Note: @slow + importorskip-gated — only enforced in slow CI** |

**Epic 11:** FULL 5 · PARTIAL 0 · NONE 0.

## Epic 12 — κ Per-task budget enforcement

| REQ | Verdict | Covering test | Note |
|-----|---------|---------------|------|
| FR66 SIGTERM ≤5s, SIGKILL +5s | FULL | `domain/test_budget_supervisor.py::*` + `tests/integration/test_budget_enforcement_latency.py::test_budget_enforcement_latency_sigkill_escalation` | real subprocess |
| FR67 budget_enforcement_triggered payload | FULL | worker-wrapper `test_run_task.py::TestRunTaskBudget*` | action_taken + post_trigger_transition asserted |
| FR68 override grace + budget.override + new-ceiling | FULL | `test_budget_supervisor.py` (grace abort/expiry) + registry-state `test_handlers.py` (new_limit persist, 12.3c) + registry-api `test_decisions.py` | **Note: "delta" implemented as fixed-multiplier, not caller-supplied delta — confirm spec intent** |
| FR68a per-task policy in envelope + .env default | FULL | `tests/integration/test_budget_policy_inheritance.py` (4 precedence tiers) + `test_config.py` | |
| NFR-R8 SIGTERM ≤5s p99 | FULL | `test_budget_enforcement_latency.py::test_budget_enforced_subprocess_exits_within_5s_e2e` (5 reps <5s) | in tests/integration/ |

**Epic 12:** FULL 5 · PARTIAL 0 · NONE 0.

## Epic 13 — δ litestream WAL replication

| REQ | Verdict | Covering test / proof | Note |
|-----|---------|-----------------------|------|
| FR69 optional litestream sidecar | LIVE-ONLY | `docker-compose.yml:431-486` (profiles:["litestream"]) | **GAP: no automated compose-config test; manual `docker compose config` only.** Live WAL→S3 = operator/nightly |
| FR70 OMB_LITESTREAM_CONFIG_PATH; absent disables | LIVE-ONLY | `docker-compose.yml:486` default + `.env.example` | **GAP: no test asserts env-default fallback / no-startup-error-when-absent** |
| FR71 restore-from-litestream recipe | FULL | `scripts/litestream-restore-drill.sh` + `nightly.yml:172-192`; `justfile:263-329` (chains bootstrap-verify) | S3-transport leg LIVE-ONLY; restore code-path CI-tested hermetically |
| FR71a replication read-only, FR26 preserved | PARTIAL | `scripts/check_replication_lag.py:271-279` (flock single-writer) + ADR-0007 | shared-read of WAL is design/ADR-only (live sidecar behavior) |
| NFR-R7 lag <30s p95; >5min → replication.lagging | FULL | `scripts/test_replication_lag_detector.py` (13 tests: debounce/stall/recovery) + `events/types/test_replication.py` | <30s threshold itself is operator-measured (LIVE) |

**Epic 13:** FULL 2 · PARTIAL 1 · LIVE-ONLY 2 · NONE 0.

---

## Aggregate

| Verdict | Count |
|---|---|
| FULL | 18 |
| PARTIAL | 4 |
| RELEASE-TIME | 3 |
| LIVE-ONLY | 2 |
| **NONE** | **0** |

No requirement is wholly uncovered. RELEASE-TIME (FR53/54/55) and LIVE-ONLY (FR69/70 transport) are inherent to their nature, not coverage failures.

## Gap closure log
- **2026-06-03:** G3 + G4 CLOSED (pure-test additions, executor-built → orchestrator-verified: both green on independent re-run, ruff clean, no production code touched).
- **2026-06-03:** G2 CLOSED (verify-images→signature_rejected wiring + hermetic test; diff-audited FR26-safe, never masks exit 1, test green on independent re-run; shellcheck + just-parse clean).
- **2026-06-03:** G1 CLOSED (operator chose implement) — publish-time license gate `scripts/check_sbom_licenses.py` reusing the FR40 policy, wired into release.yml; architect→executor→security+code review (both clean)→2 review MEDIUMs fixed. **ALL FOUR GAPS CLOSED.**

## Quality Gate Decision (final): **PASS**
All 25 Phase-2 requirements are covered (NFR-O7 + NFR-S11 now FULL after G3/G1). RELEASE-TIME items (FR53/54/55, P2-I6, Epic-8 `verify-images`) and the litestream live-S3 leg are verified on the next tagged Phase-2 release — their CI/recipe machinery is present, wired, and (for the license gate) unit-proven. No unmet clauses remain. Original gate decision (CONCERNS, at authoring) retained below for history.

## Quality Gate Decision: **CONCERNS** (original, at matrix authoring)

Core functional requirements are well-tested (18 FULL, 0 NONE). The gate is **CONCERNS** (not PASS) because of four addressable gaps, one of which is an *unimplemented* NFR clause:

| # | Gap | Severity | Type | Status |
|---|-----|----------|------|--------|
| G1 | **NFR-S11 license-incompatibility gate unimplemented** — no license scanner blocks publish in CI/release | HIGH | feature | ✅ **CLOSED 2026-06-03** (operator chose: implement) — new `scripts/check_sbom_licenses.py` parses the CycloneDX SBOM and fails the release before cosign-sign on any license incompatible with the **reused** `secret_hygiene.license_scan` policy (no fork — "extends FR40"). Wired into both `release.yml` jobs (after SBOM gen, before sign), + ci.yml/justfile self-test. 11 unit tests + 8 self-test fixtures; fail-closed on parse error AND empty/missing components. architect-designed → executor-built → **security-review SAFE-TO-MERGE + code-review APPROVE-WITH-NITS** (both 0 crit/high) → 2 review MEDIUMs fixed (fail-closed empty components; multi-license test). NFR-S11 now **FULL** (license logic implemented+tested; release-time SBOM-gen exercises it on the next tag). |
| G2 | **FR56a/NFR-S9 gate→event linkage** — `just verify-images` failure doesn't auto-emit `deployment.signature_rejected` | MEDIUM | wiring + test | ✅ **CLOSED 2026-06-03** — `verify-images` now best-effort emits one `deployment.signature_rejected` per failed cosign check via the existing flock-defended helper (FR26-safe; never masks `exit 1`; opt-out `OMB_SKIP_REJECTION_EVENT=1`; exit-3/uv-missing → warning). Test: `tests/integration/test_verify_images_emits_rejection.py` (stubbed failing cosign → asserts 24 events + exit 1; opt-out → 0 events + exit 1). **Residual:** rejection event only records when `OMB_VERSION` is a valid `v<semver>` (payload contract); unset → best-effort-skip + warning (normal release workflow sets it). `operator_id` defaults to `op-verify-images` to satisfy the `^op-` payload regex. |
| G3 | **NFR-O7 cross-service trace E2E** — no integration test threading one trace_id across services + unified `/trace` query | MEDIUM | test | ✅ **CLOSED 2026-06-03** — `tests/integration/test_trace_cross_service.py` (2 tests): real EventLogWriter→materializer→registry-api `/trace`, 3 distinct service actors (registry-api/worker-wrapper/orchestrator-adapter) on one trace_id, mono_ns-ordered, + negative-control isolation. NFR-O7 now **FULL**. |
| G4 | **FR69/FR70 compose-config test** — sidecar profile-gating + env-default-disable only checked manually | LOW | test | ✅ **CLOSED 2026-06-03** — `tests/test_litestream_compose_profile_gating.py` (5 tests): asserts `profiles:["litestream"]` (off-by-default), RW data mount, `${OMB_LITESTREAM_CONFIG_PATH:-./litestream.yml}` default, and default-stack-unchanged invariant (declared=9, default-active=7, +litestream=8). FR69/FR70 now have automated config coverage. |

**Minor notes (not gating):** NFR-S10 isolation tests are `@slow`+importorskip-gated (verified only in slow CI); FR68 "delta" is a fixed multiplier vs caller-supplied delta (confirm spec intent).

**Recommendation:** G3 and G4 are pure-test additions and can be closed immediately. G2 is a small wiring+test fix. G1 requires an operator decision: implement a license-gate (e.g. grype/trivy/scancode in release.yml) or formally waive via an ADR amendment deferring it to Phase 3. None of the four blocks the *functional* Phase-2 claim, but G1 leaves an NFR-S11 clause unmet — it must be implemented or explicitly waived before NFR-S11 can be marked green.

— *Generated 2026-06-03 via BMad testarch-trace method (6-agent fan-out), orchestrated from the session loop.*
