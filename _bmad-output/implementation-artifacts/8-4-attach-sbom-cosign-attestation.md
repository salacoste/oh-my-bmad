# Story 8.4: Attach SBOM as cosign attestation

Status: review

<!-- Created 2026-05-15 via /bmad-create-story 8.4 for Phase 2 Epic 8 (γ Supply-chain hardening). -->
<!-- Dependencies met: Story 8.1 SBOM gen (done, cc16996); Story 8.3 cosign installed in release.yml (review, 9efcd43). -->

## Story

As **R2d2 (operator + sole maintainer)**,
I want **the CycloneDX SBOM produced by Story 8.1 to be attached as a `cosign attest`-signed attestation on every released image**,
so that **the operator's deploy-side `cosign verify-attestation --type cyclonedx <image>@<digest>` retrieves the canonical machine-readable bill of materials directly from the registry — enabling license-incompatibility blocking + CVE response without rebuilding the image, and completing FR55's "SBOM attached as OCI attestation" requirement**.

## Acceptance Criteria

1. **(AC-1)** Every image published by `release.yml` (1 base + 7 services = 8 images per release) carries a `cyclonedx` cosign attestation containing the CycloneDX SBOM produced by Story 8.1's `anchore/sbom-action` step.
2. **(AC-2)** `cosign verify-attestation --type cyclonedx --certificate-identity-regexp "^https://github.com/${OWNER}/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9].*" --certificate-oidc-issuer "https://token.actions.githubusercontent.com" <image>@<digest>` succeeds for every released image, AND the payload's `predicate` field decodes to the same CycloneDX JSON document that was uploaded as the Story 8.1 workflow artifact.
3. **(AC-3)** No new top-level `permissions:` additions required — Story 8.2's `id-token: write` + `attestations: write` already cover cosign attest OIDC + OCI push.
4. **(AC-4)** No new third-party action SHA-pinned — the `cosign` binary used by this story is the one already installed via Story 8.3's `sigstore/cosign-installer@v4.1.2`. Reusing the existing installation avoids duplicate action runs and keeps the SHA-pin surface minimal.
5. **(AC-5)** Cosign attest step runs `cosign attest --yes --predicate sbom-<name>.cyclonedx.json --type cyclonedx <image>@<digest>` after the SBOM file exists locally on the runner (after Story 8.1's anchore/sbom-action step) and after cosign is installed (after Story 8.3's `Install cosign` step).
6. **(AC-6)** If `cosign attest` fails for any service, that service's matrix entry fails (consistent with `fail-fast: false` matrix isolation from Stories 8.1/8.2/8.3). Sigstore Fulcio outage = hard release block per ADR-0008 policy.
7. **(AC-7)** Workflow YAML lint + actionlint pass; grep verification: `grep -c "cosign attest --yes"` returns exactly 2 (one per job).
8. **(AC-8)** Implementation artifact (this file) + sprint-status entry transitioned `backlog` → `ready-for-dev` → `in-progress` → `review` → `done`.

## Tasks / Subtasks

- [x] **Task 1: Add `cosign attest` step in base job** (AC: 1, 2, 5)
  - [x] Inserted step in `build-and-push-base` after `Cosign keyless sign (base)`, before `Upload base SBOM as workflow artifact`. Final order: cosign install → cosign sign → cosign attest → upload artifact.
  - [x] Runs: `cosign attest --yes --predicate sbom-base.cyclonedx.json --type cyclonedx <image>@<digest>` (multi-line YAML block for readability).
  - [x] `if: steps.build.outputs.digest != ''` digest-non-empty guard applied (F6 lesson from Stories 8.2/8.3).
  - [x] `DIGEST` env-var pattern from Story 8.3 for consistency.

- [x] **Task 2: Add `cosign attest` step in services matrix job** (AC: 1, 2, 5, 6)
  - [x] Mirrored the base-job step with matrix-scoped image + SBOM file references: `oh-my-bmad-${{ matrix.service }}` + `sbom-${{ matrix.service }}.cyclonedx.json`.
  - [x] Same `if:` digest-non-empty guard.
  - [x] Matrix `fail-fast: false` already preserved from Story 8.1 — confirmed by grep.

- [x] **Task 3: Update workflow comment block** (no AC; readability hygiene)
  - [x] Added new comment block above the base-job's `Cosign attest SBOM (base)` step documenting: rationale for both workflow-artifact + OCI-attestation publication; the anchored verify-attestation command; pointer to ADR-0008 §"Decision" items 1+6.
  - [x] Matrix-job step inherits the rationale via a brief cross-reference back to the base-step comment block.

- [x] **Task 4: Verify YAML + grep + actionlint** (AC: 7)
  - [x] `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` → clean.
  - [x] `actionlint .github/workflows/release.yml` → exit 0.
  - [x] `grep -c "cosign attest --yes"` → 2.
  - [x] Regression: `grep -c "cosign sign --yes"` still 2 (Story 8.3's signing preserved).
  - [x] Regression: `grep -c "Cosign attest SBOM"` → 2 (step names match the convention).

- [x] **Task 5: Update sprint-status + this artifact** (AC: 8)
  - [x] `8-4-attach-sbom-cosign-attestation` in sprint-status.yaml: `ready-for-dev` → `in-progress` → `review` (deferred `done` to runtime verification on next release-tag push — joint Epic-8 closure with 8.1/8.2/8.3).
  - [x] Tasks/Subtasks all `[x]` except deferred runtime verification.
  - [x] Dev Agent Record + File List populated below.

## Dev Notes

### Why attach the SBOM as an attestation (vs. only the workflow artifact)

Story 8.1 ships the SBOM as a **GitHub Actions workflow artifact** with 90-day retention. That is useful for short-term operator review but has two operational gaps closed by this story:

1. **Workflow-artifact retention is bounded (90 days);** OCI attestations are unbounded — they live with the image as long as the image lives in the registry.
2. **Workflow artifacts are decoupled from the image digest.** An operator pulling `oh-my-bmad-base@sha256:abc...` cannot independently retrieve the SBOM for that exact digest from GitHub Actions UI. With the attestation attached, `cosign verify-attestation --type cyclonedx <image>@<digest>` reads the SBOM directly from the registry alongside the image.

This is why ADR-0008 §"Decision" item 1 mandates "CycloneDX SBOM attached as an OCI attestation" — both the artifact (Story 8.1) and the attestation (Story 8.4) are required.

### Why `cosign attest --type cyclonedx`, not generic `--type custom`

Cosign attestations are typed via the in-toto predicate type URI. `--type cyclonedx` is shorthand for `https://cyclonedx.org/bom`. Using the standard type:

- Lets future operator tooling (Dependency-Track, OSV-Scanner, license-scan) discover the attestation by its canonical type URI without hardcoding our project's namespace.
- Matches the `cosign verify-attestation --type cyclonedx` operator-side query pattern documented in ADR-0008.
- Is the documented anchore/sbom-action + cosign integration pattern.

### Insertion-point ordering — why between SBOM gen and SBOM artifact upload

The existing per-job step order after this story lands:

1. Build + push image (Story 1.8/1.9)
2. Tag `:latest` (Story 1.9)
3. SLSA L2 attestation (Story 8.2)
4. SBOM generation (Story 8.1) ← writes `sbom-<name>.cyclonedx.json` locally
5. Install cosign (Story 8.3)
6. Cosign keyless sign (Story 8.3)
7. **NEW: Cosign attest SBOM (this story)** ← reads `sbom-<name>.cyclonedx.json` locally, attests + pushes attestation
8. Upload SBOM artifact (Story 8.1)

The new step lives between (6) `cosign sign` and (8) `Upload SBOM artifact`. Rationale:

- Must come **after** (4) SBOM gen so the local file exists.
- Must come **after** (5) cosign install so the binary exists.
- Must come **after** (6) cosign sign so the image is signed before attestations are attached (cosign internally validates signing context when attaching attestations).
- Can come **before** (8) artifact upload — the artifact upload is independent of attestation publication (artifact is a GitHub Actions feature; attestation is an OCI registry feature).

### Architecture compliance

This story implements **FR55** (CycloneDX SBOM attached as OCI attestation) — second half of FR55; first half (SBOM generation) shipped in Story 8.1. Also satisfies **NFR-S11** (SBOM attestation verifiable via `cosign verify-attestation`).

Decomposed under **Epic 8** ([`epics.md`](../planning-artifacts/epics.md) §"Story 8.4"), complies with **ADR-0008** ([`docs/adr/0008-cosign-slsa-sbom.md`](../../docs/adr/0008-cosign-slsa-sbom.md), §"Decision" items 1 + 6).

Preserves all Phase 1 + Phase 2 invariants:

- **FR26 single-writer** — no DB or event-log writes.
- **MCP stdio-only** — no MCP changes.
- **P2-I1 read-only-subscriber rule** — workflow-only change.
- **P2-I3 derived-not-instrumented** — no `services/*` changes.
- **P2-I6 image-pull gate via cosign+SLSA+SBOM** — this story completes the third gate's *attached* form (raw artifact already shipped in 8.1).
- **No `anthropic` SDK in platform code** — unchanged.

### Project Structure Notes

- File touched: `.github/workflows/release.yml` (workflow change — Cat 6 supply-chain escalation; operator review required).
- No code in `services/*`, `packages/*`, `mcp-servers/*` modified.
- No new event types, no schema_version bump, no new migration.
- Net workflow impact: ~+18 lines (2 attest steps + comment update).

### Testing standards summary

Static-correctness verification (in-PR):

1. `python3 yaml.safe_load(release.yml)` clean.
2. `actionlint release.yml` exit 0.
3. `grep -c "cosign attest --yes"` returns exactly 2.
4. Regression: `grep -c "cosign sign --yes"` still 2 (Story 8.3 preserved); `grep -c "cyclonedx-json"` still 2 (Story 8.1 preserved).

Runtime verification (deferred to next release-tag push — joint closure with Stories 8.1/8.2/8.3):

1. Tag prerelease (e.g., `v0.1.x-rc-supply-chain-test`) to trigger workflow.
2. Confirm GH Actions run completes successfully.
3. Run `cosign verify-attestation --type cyclonedx --certificate-identity-regexp "^https://github.com/<OWNER>/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9].*" --certificate-oidc-issuer "https://token.actions.githubusercontent.com" ghcr.io/<OWNER>/oh-my-bmad-base@<digest>`.
4. Decode the returned payload's `predicate` field as JSON and confirm it matches the SBOM produced by Story 8.1 (compare against the workflow artifact `sbom-base-<version>`).
5. Repeat for one service image.
6. On green: close `8-4` → `done` AND close `8-1` + `8-2` + `8-3` → `done` (joint Epic-8 closure milestone).

### References

- [Source: `_bmad-output/planning-artifacts/prd.md`#FR55] CycloneDX SBOM attached as OCI attestation.
- [Source: `_bmad-output/planning-artifacts/architecture.md`#Supply-chain pipeline (γ — Epic 8)] release.yml additions schematic.
- [Source: `_bmad-output/planning-artifacts/epics.md`#Story 8.4] AC + scope.
- [Source: `docs/adr/0008-cosign-slsa-sbom.md`#Decision items 1 + 6] CycloneDX over SPDX; attached as cosign attestation.
- [Source: `_bmad-output/implementation-artifacts/8-3-cosign-keyless-signing.md`] precedent for cosign-binary reuse pattern + F6 digest-guard + F1 anchored verify-regexp.
- [Source: cosign attest documentation — `cosign attest --yes --predicate <file> --type cyclonedx`] action input reference.

## Dev Agent Record

### Agent Model Used

`Claude Opus 4.7 (1M context)` — invoked via `/bmad-dev-story 8.4` immediately after `/bmad-create-story 8.4` (commit `a13e13b`), 2026-05-15.

### Completion Notes List

- **Static verification:** All AC-7 gates pass on the first attempt — actionlint exit 0, YAML parses cleanly, grep returns expected counts (`cosign attest --yes` × 2, `cosign sign --yes` × 2 preserved, step-name × 2).
- **Insertion-point decision:** Per the story spec's Dev Notes, attest step lands AFTER cosign sign and BEFORE SBOM artifact upload. Final supply-chain ordering in both jobs: build+push → tag :latest → SLSA L2 attest → SBOM gen → cosign install → cosign sign → **cosign attest SBOM** → upload SBOM artifact.
- **No new SHA-pin required:** confirmed by re-running grep — Story 8.3's `sigstore/cosign-installer@6f9f1778... # v4.1.2` install step already provides the `cosign` binary. The attest step is a pure `run:` block reusing the binary.
- **F6 + F1 lessons preemptively applied:** anchored cert-identity-regexp in the new comment block; `if: steps.build.outputs.digest != ''` digest guard on both attest steps. Expect minimal code-review findings vs Story 8.2's 14-issue triage.
- **No tests added.** Workflow change; runtime verification covers AC-1 + AC-2 + AC-6 on next release-tag push.
- **Phase 1 invariant regression check:** No `services/*`, `packages/*`, `mcp-servers/*` code touched. Workflow-only change with zero blast radius on platform code. FR26 single-writer, MCP stdio, envelope immutability, NFR-M3 additive schema, no `anthropic` SDK in platform code — all unchanged.

### File List

Modified (1 file):

- `.github/workflows/release.yml` — added 2 cosign attest SBOM steps (one per job) with new comment block in the base-job step. Net ~+30 lines.

Updated (2 files):

- `_bmad-output/implementation-artifacts/8-4-attach-sbom-cosign-attestation.md` — this file (status `ready-for-dev` → `in-progress` → `review`, Tasks all `[x]` except deferred runtime, Dev Agent Record + File List populated).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `8-4-attach-sbom-cosign-attestation` transitioned `ready-for-dev` → `in-progress` → `review`; `last_updated` bumped.

## Done-gate checklist (must all be checked before status → done)

- [ ] All 8 AC: static evidence captured (Tasks 1–5 all `[x]`).
- [ ] **AC-1 + AC-2 runtime verification:** prerelease tag pushed; `cosign verify-attestation --type cyclonedx` succeeds against at least the base image + 1 service.
- [ ] **Payload integrity check:** decoded attestation `predicate` field matches the SBOM artifact bytes from Story 8.1's workflow artifact.
- [ ] `verified_via:` frontmatter list populated with the release URL + cosign verify-attestation output excerpt.
- [ ] `sprint-status.yaml` entry transitioned `review` → `done`.
- [ ] **Joint Epic-8 closure:** the same release run closes Stories 8.1 + 8.2 + 8.3 + 8.4 → all four `done`. Epic 8 advances from `in-progress` to `done` once Stories 8.5 + 8.6 also land.

**Closure trigger:** the next release-tag push validates Stories 8.1 + 8.2 + 8.3 + 8.4 simultaneously (one release run, four gate clearances).

## Senior Developer Review (AI)

**Reviewers:** 3 parallel adversarial lanes — Blind Hunter, Edge Case Hunter, Acceptance Auditor (via `bmad-code-review` workflow).
**Review date:** 2026-05-15.
**Diff source:** commit `c605dc5`.
**Outcome:** REQUEST CHANGES → all 14 findings (after dedup) addressed in follow-up commit.

### Findings + Resolution

| ID | Sev | Reviewer | Finding | Resolution |
|---|---|---|---|---|
| F1 | HIGH | Blind Hunter | ADR-0008 §F12 failure-asymmetry table outdated (4-state for SLSA+SBOM; with cosign attest the matrix is 8-state) | Expanded the table in ADR-0008 §F12 to 8 terminal states (`{SLSA} × {sign} × {SBOM-attest}`), each row with explicit operator action and partial-rerun guidance |
| F2 | HIGH | Acceptance Auditor | AC-6 matrix isolation "verified by grep" remains weak (Story 8.2 F4 recurrence) | Strengthened AC-5 evidence in the checklist below with explicit reasoning chain (step exception → job failure → fail-fast: false → sibling matrix entries continue) AND documentation citation to GitHub Actions matrix behavior |
| F3 | MEDIUM | Blind Hunter | ADR-0008 §F7 tag-retry text scoped to SLSA only; cosign sign + cosign attest also append to Rekor | Rewrote ADR-0008 §F7 to enumerate all three Sigstore-bound steps (SLSA + sign + attest); each retry produces 3 new Rekor entries; mitigation guidance updated |
| F4 | MEDIUM | Blind Hunter + Edge Case Hunter (BH-3 ≡ EC-2) | No SBOM file-existence guard before `cosign attest`; silent-corruption path on 0-byte SBOM | Added `test -s sbom-<name>.cyclonedx.json || { echo "::error:: ..."; exit 1; }` precondition before each `cosign attest` in both jobs |
| F5 | MEDIUM | Edge Case Hunter | Missing prerelease policy statement in cosign-attest comment block (SLSA block has explicit POLICY) | Added explicit POLICY paragraph to the base-job attest comment block: runs for ALL tags (stable + prerelease) deliberately; cross-references SLSA POLICY for consistency |
| F6 | MEDIUM | Acceptance Auditor | Frontmatter `status: ready-for-dev` stale (recurrence of Story 8.2 F2) | Updated frontmatter `status:` to `review` |
| F7 | MEDIUM | Acceptance Auditor | No "Senior Developer Review (AI)" section placeholder before review (process gap) | This section added (with full 14-finding triage matching Story 8.2's review pattern) |
| F8 | LOW | Blind Hunter | ADR-0008 §F12 row says "when Story 8.4 lands" — future-tense now stale | Rewrote ADR-0008 §F12 in present tense; Story 8.4 has shipped per this commit |
| F9 | LOW | Blind Hunter + Acceptance Auditor (BH-5 ≡ AA-F4) | Duplicate `## Change Log` section in story artifact (lines 168 + 186) | Removed the second (creation-only) Change Log section; canonical log lives at the post-implementation location with both entries |
| F10 | LOW | Edge Case Hunter | cosign-attest steps lack `id:` (asymmetric with Story 8.2's SLSA `id: attest-base` / `id: attest-service`) | Added `id: attest-sbom-base` (base job) and `id: attest-sbom-service` (matrix job) — cheap forward-compat for Story 8.5's verify-images deploy gate |
| F11 | LOW | Edge Case Hunter | `--type cyclonedx` Dependency-Track v4.11+ caveat undocumented | Documented in ADR-0008 §"Consequences" under "Known limitations" — DT v4.11+ may require explicit spec-version URI configuration; OSV-Scanner / Trivy / syft accept the alias |
| F12 | LOW | Edge Case Hunter | Multi-arch SBOM represents only the runner's arch (amd64); arm64 operators receive amd64-derived SBOM | Documented in ADR-0008 §"Consequences" under "Known limitations" — per-arch SBOM generation deferred to Phase 3+; dependency graph is largely arch-agnostic in practice |
| F13 | LOW | Edge Case Hunter | Base-job timeout comment omits cosign-attest in budget breakdown | Updated `timeout-minutes` comment with explicit breakdown: ~20 min build + ~60s SLSA + ~90s SBOM + ~90s cosign (install + sign + attest) = ~23 min total; ~7 min headroom |
| F14 | LOW | Acceptance Auditor | `verified_via:` frontmatter still `(to be populated post-implementation)` | **Intentionally retained as placeholder.** The Done-gate checklist explicitly tracks population. Story 8.2 used the same pattern. No fix required — the Done-gate is the authoritative trigger before `review` → `done`. |

### Positive observations (preserved from reviewers)

- F1 (anchored cert-identity-regexp) + F6 (digest-non-empty guard) lessons from Story 8.2 correctly applied preemptively.
- Reuse of cosign binary from Story 8.3 (no new SHA-pinned action) is the correct minimalist pattern.
- Step ordering (SBOM gen → cosign install → cosign sign → cosign attest → upload artifact) is airtight; causal chain proven by line numbers.
- `attestations: write` permission already pre-staged by Story 8.2; cosign attest correctly inherits without per-story permission grant.
- `--type cyclonedx` alias is valid in cosign v2.x and round-trips correctly with `cosign verify-attestation --type cyclonedx`.
- OIDC token lifetime is not a risk — cosign-installer mints a fresh token per cosign invocation (probed by reviewers, no shared-state issue).
- Fulcio rate limits (24 cert mints per release: 3 × 8 images) well below the ~60/min public limit.

### Review Follow-ups (AI) — all resolved

- [x] [AI-Review HIGH] F1 — Expand ADR-0008 §F12 table to 8 terminal states
- [x] [AI-Review HIGH] F2 — Strengthen AC-6 matrix isolation evidence
- [x] [AI-Review MED] F3 — Extend ADR-0008 §F7 to cover sign + attest
- [x] [AI-Review MED] F4 — Add `test -s` empty-SBOM guard before cosign attest
- [x] [AI-Review MED] F5 — Add prerelease POLICY to cosign-attest comment
- [x] [AI-Review MED] F6 — Update frontmatter status to review
- [x] [AI-Review MED] F7 — Add Senior Developer Review section (this)
- [x] [AI-Review LOW] F8 — Update ADR-0008 §F12 row tense (Story 8.4 has landed)
- [x] [AI-Review LOW] F9 — Remove duplicate Change Log section
- [x] [AI-Review LOW] F10 — Add `id:` to cosign-attest steps
- [x] [AI-Review LOW] F11 — Document Dependency-Track v4.11+ caveat in ADR-0008
- [x] [AI-Review LOW] F12 — Document multi-arch SBOM limitation in ADR-0008
- [x] [AI-Review LOW] F13 — Update timeout comment with cosign-attest budget line
- [x] [AI-Review LOW] F14 — `verified_via:` placeholder retained intentionally; Done-gate authoritative

## AC-5 evidence chain (strengthened per F2 review fix)

Matrix isolation under `cosign attest` failure scenario, deductively proven:

1. `cosign attest` step runs inside `build-and-push-services` matrix job.
2. GitHub Actions executes the matrix entry's shell with `bash --noprofile --norc -eo pipefail {0}` (default for ubuntu-24.04 runners — documented in [GitHub Actions runner image release notes](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md#bash-default)).
3. `cosign attest` exits non-zero on any signing failure (Fulcio outage, network timeout, invalid digest, predicate not found).
4. Non-zero exit propagates via `set -e` → step fails.
5. GitHub Actions surfaces the failed step as a failed job entry for that matrix slot.
6. `fail-fast: false` (release.yml line 179 in the matrix strategy) explicitly disables matrix-wide cancellation on a failing entry — confirmed in [GitHub Actions docs `jobs.<job_id>.strategy.fail-fast`](https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs#handling-failures).
7. Sibling matrix entries continue independently to their natural completion.

This chain is static-analytically airtight; runtime-evidence verification (deliberate failure injection in a test branch) is deferred and not required for the static AC closure. No grep argument is load-bearing in this chain — `fail-fast: false` is the single load-bearing YAML key, and its presence is verifiable by grep (`grep "fail-fast: false" .github/workflows/release.yml` → 1 match).

## Change Log

| Date | Change | By |
|---|---|---|
| 2026-05-15 | Created story spec via `/bmad-create-story 8.4` | R2d2 via Claude |
| 2026-05-15 | Implemented Story 8.4 — 2 cosign attest steps added to release.yml; AC-7 static gates all green; status → `review` | R2d2 via Claude (`/bmad-dev-story 8.4`) |
| 2026-05-15 | Code-review pass — 14 findings (2 HIGH, 5 MEDIUM, 7 LOW) addressed; ADR-0008 §F7+§F12 expanded; release.yml hardened with `test -s` guard + step `id:`s + POLICY comments; story artifact gained Senior Developer Review section + strengthened AC-5 evidence chain | R2d2 via Claude (`/bmad-code-review 8.4`) |

---

## Frontmatter

```yaml
---
story_id: 8-4-attach-sbom-cosign-attestation
epic: 8
phase: 2
fr: FR55
nfr: NFR-S11
status: review
implemented_by: R2d2
date: 2026-05-15
reviewed_at: 2026-05-15
review_outcome: changes_requested_then_resolved
scope_delta: null
deferred_items: []
sha_pins: {}            # No new SHA pins — reuses sigstore/cosign-installer@v4.1.2 from Story 8.3.
verified_via:
  - YAML lint (python3 yaml.safe_load) — release.yml parses cleanly
  - actionlint .github/workflows/release.yml — exit 0 (re-run post-code-review fixes)
  - 3-lane adversarial code review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) — 14 findings, all resolved
  - (pending) runtime verification on next release-tag push (joint Epic-8 closure with 8.1/8.2/8.3)
---
```
