# Story 8.4: Attach SBOM as cosign attestation

Status: ready-for-dev

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

- [ ] **Task 1: Add `cosign attest` step in base job** (AC: 1, 2, 5)
  - [ ] Insert step in `build-and-push-base` AFTER the `Generate CycloneDX SBOM (base)` step but BEFORE `Upload base SBOM as workflow artifact`. Reasoning: the SBOM file must exist locally on the runner, and the attestation should be produced before the artifact upload step to keep the workflow-output ordering "all triumvirate steps before artifact upload."
  - [ ] Run: `cosign attest --yes --predicate sbom-base.cyclonedx.json --type cyclonedx ${REGISTRY}/${OWNER}/oh-my-bmad-base@${DIGEST}`.
  - [ ] Add `if: steps.build.outputs.digest != ''` digest-non-empty guard (F6 lesson applied from Story 8.2/8.3).
  - [ ] Use the `DIGEST` env-var pattern from Story 8.3 for consistency.

- [ ] **Task 2: Add `cosign attest` step in services matrix job** (AC: 1, 2, 5, 6)
  - [ ] Mirror the base-job step in `build-and-push-services`, inserted between `Generate CycloneDX SBOM (${{ matrix.service }})` and `Upload ${{ matrix.service }} SBOM as workflow artifact`.
  - [ ] Use the matrix-scoped image reference: `oh-my-bmad-${{ matrix.service }}` + matrix-scoped SBOM file: `sbom-${{ matrix.service }}.cyclonedx.json`.
  - [ ] Same `if:` digest-non-empty guard.
  - [ ] Matrix `fail-fast: false` already in place; preserves sibling isolation.

- [ ] **Task 3: Update workflow comment block** (no AC; readability hygiene)
  - [ ] Update the comment block above the cosign-installer step (added by Story 8.3) to mention that the same cosign install now also serves the SBOM-attestation step (clarifies the "install once, sign + attest" pattern for future readers).
  - [ ] Add a sentence in the same comment block documenting Story 8.4's specific attest invocation + why it lives between SBOM gen and SBOM artifact upload.

- [ ] **Task 4: Verify YAML + grep + actionlint** (AC: 7)
  - [ ] `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"` → clean.
  - [ ] `actionlint .github/workflows/release.yml` → exit 0.
  - [ ] `grep -c "cosign attest --yes"` → 2.
  - [ ] `grep -c "cosign sign --yes"` still → 2 (Story 8.3's signing steps preserved).
  - [ ] `grep -c "cyclonedx-json"` still → 2 (Story 8.1's SBOM gen preserved).

- [ ] **Task 5: Update sprint-status + this artifact** (AC: 8)
  - [ ] `8-4-attach-sbom-cosign-attestation` in sprint-status.yaml: `ready-for-dev` → `in-progress` → `review` (deferred `done` to runtime verification on next release-tag push — same closure path as Stories 8.1/8.2/8.3).
  - [ ] Append "Senior Developer Review (AI)" section template to this file post-implementation (precedent: Story 8.2's review section).

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

_(to be filled by the dev agent on implementation)_

### Debug Log References

_(to be filled if needed)_

### Completion Notes List

_(to be filled by the dev agent post-implementation)_

### File List

Expected to touch (1 file):

- `.github/workflows/release.yml` — add 2 cosign attest steps (base + matrix) + comment update on the cosign install block. No new SHA-pinned actions (reuses cosign installed via Story 8.3).

Plus 2 status-tracking updates:

- `_bmad-output/implementation-artifacts/8-4-attach-sbom-cosign-attestation.md` — this file (status `ready-for-dev` → `in-progress` → `review`, tasks → `[x]`, Dev Agent Record + File List populated, Senior Developer Review section template added).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `8-4-...` transitioned `ready-for-dev` → `in-progress` → `review`.

## Done-gate checklist (must all be checked before status → done)

- [ ] All 8 AC: static evidence captured (Tasks 1–5 all `[x]`).
- [ ] **AC-1 + AC-2 runtime verification:** prerelease tag pushed; `cosign verify-attestation --type cyclonedx` succeeds against at least the base image + 1 service.
- [ ] **Payload integrity check:** decoded attestation `predicate` field matches the SBOM artifact bytes from Story 8.1's workflow artifact.
- [ ] `verified_via:` frontmatter list populated with the release URL + cosign verify-attestation output excerpt.
- [ ] `sprint-status.yaml` entry transitioned `review` → `done`.
- [ ] **Joint Epic-8 closure:** the same release run closes Stories 8.1 + 8.2 + 8.3 + 8.4 → all four `done`. Epic 8 advances from `in-progress` to `done` once Stories 8.5 + 8.6 also land.

**Closure trigger:** the next release-tag push validates Stories 8.1 + 8.2 + 8.3 + 8.4 simultaneously (one release run, four gate clearances).

## Change Log

| Date | Change | By |
|---|---|---|
| 2026-05-15 | Created story spec via `/bmad-create-story 8.4` | R2d2 via Claude |

---

## Frontmatter

```yaml
---
story_id: 8-4-attach-sbom-cosign-attestation
epic: 8
phase: 2
fr: FR55
nfr: NFR-S11
status: ready-for-dev   # → in-progress → review → done
implemented_by: R2d2
date: 2026-05-15
scope_delta: null
deferred_items: []
sha_pins: {}            # No new SHA pins — reuses sigstore/cosign-installer@v4.1.2 from Story 8.3.
verified_via:
  - (to be populated post-implementation)
---
```
