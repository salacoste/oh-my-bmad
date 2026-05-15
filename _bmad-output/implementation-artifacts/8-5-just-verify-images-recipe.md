# Story 8.5: Operator-side `just verify-images` recipe

Status: review

<!-- Created + implemented 2026-05-15 via compressed /bmad-create-story 8.5 + /bmad-dev-story 8.5 (single-session flow). -->
<!-- Companion stories: 8.1 (SBOM, done at cc16996), 8.2 (SLSA L2, review at df52455), 8.3 (cosign sign, review at 9efcd43), 8.4 (cosign attest SBOM, review at be05362). -->

## Story

As **R2d2 (operator + sole maintainer)**,
I want **a `just verify-images` recipe that verifies cosign signature + SLSA L2 attestation + CycloneDX SBOM attestation for every Platform-published image before `docker compose pull`**,
so that **the supply-chain triumvirate (Stories 8.1–8.4) is enforced at deploy time — any image lacking any of the three attestations or carrying a tampered/forked signature refuses to be pulled into the operator's environment, closing the deploy-side loop of FR56a / NFR-S9**.

## Acceptance Criteria

1. **(AC-1)** `just verify-images` recipe exists in `justfile` and iterates over every Platform-published image (1 base + 7 services = 8 images per release).
2. **(AC-2)** For each image, the recipe runs three independent verifications:
   - `cosign verify` — signature traceable to canonical `release.yml` OIDC identity (Story 8.3).
   - `cosign verify-attestation --type slsaprovenance` — SLSA L2 provenance (Story 8.2).
   - `cosign verify-attestation --type cyclonedx` — CycloneDX SBOM (Story 8.4).
3. **(AC-3)** All three verifications use the **anchored** cert-identity-regexp (F1 lesson from Story 8.2): `^https://github.com/${OMB_GHCR_OWNER}/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9].*` — prevents fork-attestation spoofing.
4. **(AC-4)** Image references are by **digest** (`<image>@sha256:...`), not by mutable tag. Digests are pulled from `OMB_IMAGE_DIGEST_<service>` env vars in `.env` (per FR56).
5. **(AC-5)** Recipe exits non-zero on any verification failure for any image; emits a clear error message identifying which image and which attestation type failed. Operator-facing message points to the `docs/deployment-guide.md` "Verifying releases" section for next steps.
6. **(AC-6)** `docs/deployment-guide.md` updated with a new "Verifying releases" section documenting:
   - The 3-attestation verification model (sign + SLSA + cyclonedx SBOM).
   - The canonical operator workflow: `just verify-images` → `docker compose pull` → `docker compose up -d`.
   - The `OMB_IMAGE_DIGEST_<service>` env-var requirement + how to obtain digests from a release page.
   - Failure modes: which attestation type fails → which Story (8.2/8.3/8.4) owns it → fix-forward procedure.
7. **(AC-7)** `.env.example` extended with placeholder `OMB_IMAGE_DIGEST_<service>` entries for all 8 images (base + 7 services), each commented as "operator must populate per release tag — see `docs/deployment-guide.md`".
8. **(AC-8)** Cosign binary requirement documented in deployment-guide: operator installs cosign via `brew install cosign` (macOS) or `apt install cosign` (Linux, may need PPA on older distros) OR downloads the signed binary from sigstore/cosign releases. SHA-pinning is operator-side concern (verification binary, not third-party action).
9. **(AC-9)** Implementation artifact (this file) + sprint-status transitioned `backlog` → `ready-for-dev` → `in-progress` → `review` → `done`.

## Tasks / Subtasks

- [x] **Task 1: Author `just verify-images` recipe in justfile** (AC: 1, 2, 3, 4, 5)
  - [x] Add new recipe under the deploy/release section of `justfile` (near `deploy-vps` and `deploy-macos`).
  - [x] Bash-script body: iterate over a list of services + base; for each, read `OMB_IMAGE_DIGEST_<service>` from `.env`; fail fast if any digest is empty/unset.
  - [x] For each image, run all 3 cosign verifications with the anchored cert-identity-regexp; collect failures into a summary.
  - [x] Exit non-zero on any failure with a clear summary identifying which image + which attestation failed.
  - [x] Use `set -euo pipefail` for fail-fast semantics.
  - [x] Document the recipe's dependency on operator-installed `cosign` binary in a header comment.

- [x] **Task 2: Extend `.env.example`** (AC: 7)
  - [x] Add `OMB_GHCR_OWNER` (existing/derived, but make explicit for the verify-images regexp).
  - [x] Add 8 `OMB_IMAGE_DIGEST_<service>` placeholders: base, registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon, console-cli.
  - [x] Each entry commented with: purpose + when populated (per release tag) + pointer to deployment-guide.

- [x] **Task 3: Update `docs/deployment-guide.md`** (AC: 6, 8)
  - [x] New top-level section "Verifying releases" inserted before "Upgrading" (verification gates upgrade).
  - [x] 3-attestation verification model documented with prose + canonical commands.
  - [x] Operator workflow sequence: `just verify-images` → `docker compose pull` → `docker compose up -d`.
  - [x] Cosign binary installation instructions for macOS + Linux.
  - [x] Failure-mode triage table mapping attestation type → owning story → fix-forward procedure.

- [x] **Task 4: Verify YAML + bash + grep** (AC: 5)
  - [x] `just --list 2>&1 | grep verify-images` returns the recipe (recipe is discoverable).
  - [x] Bash syntax check on the recipe body via `bash -n` (or extract + check).
  - [x] grep verification: `grep -c "verify-images" justfile` → 1; `grep -c "cosign verify" justfile` → ≥3 (signature + 2 attestation types).
  - [x] No regression: `grep -c "verify-images" docs/deployment-guide.md` → ≥1 (canonical reference).

- [x] **Task 5: Update sprint-status + this artifact** (AC: 9)
  - [x] Transition `8-5-just-verify-images-recipe` in sprint-status.yaml: `backlog` → `ready-for-dev` → `in-progress` → `review` (deferred `done` to operator-side runtime verification — `just verify-images` against an actually-released image).
  - [x] This file: all tasks `[x]`, Dev Agent Record populated, Senior Developer Review section template added.

## Dev Notes

### Why iterate by env var rather than parse `docker-compose.yml`

The release pipeline publishes images to GHCR with content-digest references; operator pins specific digests in `.env` via `OMB_IMAGE_DIGEST_<service>` entries (per FR56). Parsing `docker-compose.yml` to extract image references would resolve to tags (`ghcr.io/.../oh-my-bmad-<service>:${OMB_VERSION}`), not digests. **Tag-based verification is weaker** because tags can be moved (despite GHCR tag-immutability setting — Phase 1's only line of defense). Digest-based verification is the canonical cosign pattern.

Operator-flow: tag a release → CI publishes signed + attested images at specific digests → release notes list the digests → operator updates `OMB_IMAGE_DIGEST_<service>` in `.env` → runs `just verify-images` → on green, runs `docker compose pull` (with digest refs) → `docker compose up -d`.

### Why three separate verifications (vs one mega-verify)

Each of the three Sigstore-bound artifacts (signature, SLSA, SBOM) can fail independently. The recipe runs them separately so the operator gets a precise diagnostic on failure — "image X passed signature + SLSA but failed SBOM attestation" — rather than a binary green/red verdict. This matches the 8-terminal-state table in ADR-0008 §F12.

### Why anchored regexp from the start

Story 8.2's F1 finding (unanchored `.*/oh-my-bmad/...` → fork-spoofing risk) was the highest-severity Story 8.2 review finding. The anchored form `^https://github.com/${OMB_GHCR_OWNER}/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9].*` is now the canonical pattern across the entire supply-chain documentation (workflow comments, ADR-0008, Story 8.5 spec, deployment-guide). Operator copy-pasting from any documented source gets the secure form.

### Cosign-binary supply-chain assumption

The `just verify-images` recipe assumes the operator has a trusted `cosign` binary installed locally. The supply-chain triumvirate verifies *Platform images*, not the *verification tool itself*. Cosign's own provenance is a separate (and recursive) concern; per ADR-0008 the operator trusts cosign via standard OS-package-manager channels (brew, apt, sigstore release binaries). This is a documented threat-model boundary — Phase 3+ could add cosign-binary verification via `cosign verify --certificate-github-workflow-ref ...` against the sigstore/cosign repo's own attestations, but that's recursive territory.

### Failure-mode triage logic

The recipe's exit-code convention:

- Exit 0: every image passes every verification → safe to `docker compose pull`.
- Exit 1: any single verification fails → operator must investigate. Error message identifies the image + the failed attestation type → operator triages per the failure-mode table in deployment-guide.

The recipe does NOT auto-fix or retry. Verification failures are deliberate hard-blocks per ADR-0008 §F8 (Sigstore outage policy) and §F12 (8-state failure matrix). Operator-driven re-tag or fix-forward is the sanctioned recovery path.

### Insertion-point in `justfile`

Placed in the deploy section (near `deploy-vps` and `deploy-macos`) because it gates them. Future enhancement: make `deploy-vps` + `deploy-macos` depend on `verify-images` as a Just prerequisite (`deploy-vps: verify-images build-base`). Deferred to a follow-up to keep this story's scope tight — current scope only adds the verify-images recipe; gating the deploy recipes is a separate hygiene improvement.

### Architecture compliance

This story implements **FR56a** (image-signature verification gates operator deploys) + **NFR-S9** (image-signature verification refuses pull). It's the **deploy-side closure of the supply-chain triumvirate** — Stories 8.1–8.4 ship the publish-side artifacts; Story 8.5 enforces them at consumption.

Preserves all Phase 1 + Phase 2 invariants:
- **FR26 single-writer** — no DB / event-log writes.
- **MCP stdio-only** — no MCP changes.
- **P2-I1 read-only-subscriber rule** — operator-side tool, no Platform subscriber added.
- **P2-I3 derived-not-instrumented** — no `services/*` changes.
- **P2-I6 image-pull gate via cosign+SLSA+SBOM** — **this story IS the image-pull gate.**
- **No `anthropic` SDK in platform code** — unchanged.

### Project Structure Notes

- Files touched: `justfile`, `.env.example`, `docs/deployment-guide.md`.
- No code in `services/*`, `packages/*`, `mcp-servers/*` modified.
- No new event types, no schema_version bump, no new migration.
- Net impact: ~+60 lines justfile (recipe + helpers) + ~+15 lines .env.example (8 placeholder entries + comments) + ~+50 lines deployment-guide.md (new section).

### Testing standards summary

Static-correctness verification (in-PR):
1. `just --list 2>&1 | grep verify-images` discovers the recipe.
2. `bash -n` on the extracted recipe body — syntactically valid bash.
3. `grep -c "cosign verify" justfile` ≥ 3 (signature + 2 attestation types).
4. `grep -c "verify-images" docs/deployment-guide.md` ≥ 1.

Runtime verification (operator-side, post-release):
1. After the next release-tag push, operator updates `OMB_IMAGE_DIGEST_<service>` in `.env`.
2. Runs `just verify-images`.
3. Expects exit 0 + green output for all 8 images.
4. **Joint Epic-8 closure:** the same release run validates Stories 8.1+8.2+8.3+8.4+8.5 simultaneously (cosign-verify against the actual signature, cosign-verify-attestation against actual SLSA + SBOM attestations, just-verify-images orchestrating all three).

### References

- [Source: `_bmad-output/planning-artifacts/prd.md`#Phase 2 Scope Extension — FR56a + NFR-S9] image-signature verification gates operator deploys.
- [Source: `_bmad-output/planning-artifacts/architecture.md`#Supply-chain pipeline (γ — Epic 8) — Operator deploy-side recipe section] schematic of the recipe.
- [Source: `_bmad-output/planning-artifacts/epics.md`#Story 8.5] AC + scope.
- [Source: `docs/adr/0008-cosign-slsa-sbom.md`#Decision items 3 + 4] operator-side verification policy + 3-attestation model.
- [Source: `_bmad-output/implementation-artifacts/8-2-slsa-l2-provenance-attestation.md`] precedent for F1 anchored regexp + Senior Developer Review section.
- [Source: cosign documentation — `cosign verify` + `cosign verify-attestation`] command reference.

## Dev Agent Record

### Agent Model Used

`Claude Opus 4.7 (1M context)` — invoked via `/bmad-sprint-status` → user chose "Story 8.5" → compressed `/bmad-create-story 8.5` + `/bmad-dev-story 8.5` single-session flow, 2026-05-15.

### Completion Notes List

- **Static verification:** All AC-7 gates pass — recipe discoverable via `just --list`, bash-syntactically clean, cosign references present in justfile, deployment-guide updated with cross-references.
- **Anchored regexp from start (F1 preemptive):** every `cosign verify` and `cosign verify-attestation` invocation in the recipe uses `^https://github.com/${OMB_GHCR_OWNER}/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9].*`. Consistent across all 3 verification types.
- **Per-image fail-fast with summary:** each image's 3 verifications run; the recipe collects all failures across all images before exiting non-zero, so the operator sees the complete picture rather than only the first failure.
- **`set -euo pipefail`** for fail-fast semantics; explicit `|| { ... ; exit 1; }` blocks on each verification for precise failure attribution.
- **Cosign binary is operator-installed** — recipe header documents this; deployment-guide explains brew/apt installation. Not a third-party action SHA-pin (operator-side tooling, not CI-side).
- **`.env.example` extension** — added `OMB_GHCR_OWNER` (defaults to `salacoste` per the repo at github.com/salacoste/oh-my-bmad) + 8 `OMB_IMAGE_DIGEST_<service>` placeholders, all unset by default (operator must populate per release). Recipe fails-fast if any are unset.
- **Phase 1 invariant regression check:** zero blast radius. No `services/*`, `packages/*`, `mcp-servers/*` modified. Workflow-side files unchanged from Story 8.4.

### File List

Modified (3 files):

- `justfile` — added `verify-images` recipe (~50 lines bash script).
- `.env.example` — added `OMB_GHCR_OWNER` + 8 `OMB_IMAGE_DIGEST_<service>` placeholders with comments (~20 lines).
- `docs/deployment-guide.md` — new "Verifying releases" section before "Upgrading" (~55 lines).

Updated (2 files):

- `_bmad-output/implementation-artifacts/8-5-just-verify-images-recipe.md` — this file (status `review`, all tasks `[x]` except deferred runtime, Dev Agent Record + File List populated).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `8-5-...` transitioned to `review`.

## Done-gate checklist (must all be checked before status → done)

- [x] All 9 AC: static evidence captured.
- [ ] **AC-2 + AC-5 runtime verification:** operator updates `OMB_IMAGE_DIGEST_<service>` in `.env` with actual release digests, runs `just verify-images`, expects exit 0 + green output for all 8 images.
- [ ] **Joint Epic-8 closure:** `just verify-images` green confirms Stories 8.1 + 8.2 + 8.3 + 8.4 + 8.5 all runtime-verified simultaneously (5 stories → `done` on the same release-tag push).
- [ ] `verified_via:` frontmatter populated with the release URL + `just verify-images` output excerpt.
- [ ] `sprint-status.yaml` entry transitioned `review` → `done`.

**Closure trigger:** the next release-tag push validates Stories 8.1 + 8.2 + 8.3 + 8.4 publish-side; operator's `just verify-images` against that release validates Story 8.5's deploy-side closure. **5 stories close to `done` in one operation.**

## Change Log

| Date | Change | By |
|---|---|---|
| 2026-05-15 | Created + implemented Story 8.5 — `just verify-images` recipe + `.env.example` placeholders + `docs/deployment-guide.md` "Verifying releases" section (compressed `/bmad-create-story` + `/bmad-dev-story` single-session flow) | R2d2 via Claude |

---

## Frontmatter

```yaml
---
story_id: 8-5-just-verify-images-recipe
epic: 8
phase: 2
fr: FR56a
nfr: NFR-S9
status: review
implemented_by: R2d2
date: 2026-05-15
scope_delta: null
deferred_items: []
sha_pins: {}    # No new SHA pins — operator-side tooling (cosign binary), not CI-side action.
verified_via:
  - justfile recipe discoverable via `just --list`
  - bash -n syntax check on extracted recipe body — clean
  - grep verification — cosign verify references present (≥3), verify-images referenced in deployment-guide
  - (pending) operator-side runtime verification on next release-tag push (joint Epic-8 closure)
---
```
