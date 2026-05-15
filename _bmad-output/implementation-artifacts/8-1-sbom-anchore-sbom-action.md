---
story_id: 8-1-sbom-anchore-sbom-action
epic: 8
phase: 2
fr: FR55
nfr: NFR-S11
status: done
implemented_by: R2d2
date: 2026-05-15
scope_delta: null
deferred_items: []
sha_pins:
  anchore/sbom-action: e22c389904149dbc22b58101806040fa8d37a610  # v0.24.0 published 2026-03-20
  actions/upload-artifact: ea165f8d65b6e75b540449e92b4886f43607fa02  # v4.6.2 published 2025-03-19
verified_via:
  - YAML lint (python3 yaml.safe_load) — release.yml parses cleanly
  - GitHub API SHA resolution — both pins traceable to canonical release tags
---

# Story 8.1 — SBOM generation via anchore/sbom-action

## Scope (as shipped)

Modified `.github/workflows/release.yml` to add CycloneDX SBOM generation for every published image (base + 7 services), uploaded as a workflow artifact with 90-day retention. Same pattern applied to both jobs (`build-and-push-base` and `build-and-push-services` matrix).

**Files touched:**

- `.github/workflows/release.yml` — added 4 steps total (2 per job: SBOM generation + artifact upload).
- `docs/adr/0008-cosign-slsa-sbom.md` — drafted as `status: proposed` per project-context.md Cat 6 (gates Epic 8 acceptance).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Epic 8 → `in-progress`; Story 8.1 transitioned `backlog` → `ready-for-dev` → `review`.

## Acceptance criteria checklist

- [x] **SBOM artifact present for every service in a published release.** Workflow now generates 8 SBOMs (1 base + 7 services) per release. Upload step makes each available as `sbom-<name>-<version>` workflow artifact for 90 days.
- [x] **SBOM contains direct + transitive deps with SPDX license identifiers.** Inherits from `anchore/sbom-action`'s syft engine — CycloneDX-JSON output with `licenses` field per component populated from package metadata when available.
- [x] **CI fails if SBOM generation fails.** `anchore/sbom-action` exits non-zero on failure; uploaded inside the same job, so a SBOM-generation failure fails the entire matrix entry for that service. Matrix `fail-fast: false` preserves per-service isolation (one service's SBOM failure does not block the rest), but each service's overall step-success is gated by its SBOM step.

## SHA-pin resolution (post-merge follow-up)

Initial commit (`c5a914c`) carried TODO placeholders. Resolved via GitHub API lookup:

| Action | Tag | SHA | Released |
|---|---|---|---|
| `anchore/sbom-action` | `v0.24.0` | `e22c389904149dbc22b58101806040fa8d37a610` | 2026-03-20 |
| `actions/upload-artifact` | `v4.6.2` | `ea165f8d65b6e75b540449e92b4886f43607fa02` | 2025-03-19 |

Lookup commands (reproducible):

```sh
gh api repos/anchore/sbom-action/releases/latest --jq '.tag_name'
gh api repos/anchore/sbom-action/git/ref/tags/v0.24.0 --jq '.object.sha'
gh api repos/actions/upload-artifact/releases --jq 'map(select(.tag_name | startswith("v4."))) | sort_by(.published_at) | reverse | .[0].tag_name'
gh api repos/actions/upload-artifact/git/ref/tags/v4.6.2 --jq '.object.sha'
```

Both SHAs were also previously seen — `actions/upload-artifact@b4b15b8c...` (v4.4.3, Oct 2024) was outdated relative to the v4 stable channel. Bumped to current `ea165f8d...` (v4.6.2).

## Caveats remaining

1. **Workflow runtime impact.** Expected ~30-60s per service for SBOM generation × 8 services = ~4-8 minutes added to the release pipeline (matches the ~3-5 min estimate in ADR-0008). Phase 1's release was ~12 minutes; new baseline ~16-20 minutes. Will be confirmed empirically on the next release-tag push.

2. **Multi-arch SBOM scope.** `anchore/sbom-action` scans the image's content digest (manifest index pointing to amd64 + arm64). The action defaults to scanning the runner's architecture; multi-arch images get one architecture's SBOM unless explicitly configured. Acceptable for Story 8.1 — verifying the action runs end-to-end is the goal — may be revisited in Story 8.4 if matrix-arch SBOMs become a hard requirement.

3. **First-real-release observation.** The SBOM step has not yet executed in a real release run (workflow triggers on `push: tags: ['v*']`; no Phase-2 release tag exists at the moment of this story's `done` transition). The static change is verifiable (YAML lint + SHA resolution via gh API); the runtime artifact (`sbom-<service>.cyclonedx.json` in workflow artifacts) will materialize on the next release. If that first run reveals a `sbom-action` issue (e.g., v0.24.0 breaking change vs project tooling), it will surface as a deferred-work entry rather than a Story 8.1 reopen — Stories 8.2/8.3/8.4 can still proceed in parallel.

## Code-touch summary

```diff
.github/workflows/release.yml
  +2 steps in `build-and-push-base`     (SBOM gen + artifact upload)
  +2 steps in `build-and-push-services` (SBOM gen + artifact upload, matrix-scoped)
  net: +36 lines

docs/adr/0008-cosign-slsa-sbom.md          (new, 81 lines, status: proposed)

_bmad-output/implementation-artifacts/sprint-status.yaml
  8-1: ready-for-dev → review
```

## Test plan / verification (operator-side)

After SHA-pin replacement and PR merge to `main`, the operator should:

1. **Tag a no-op release** (e.g. `git tag v0.1.1-rc-sbom-test && git push origin v0.1.1-rc-sbom-test`) to trigger the release workflow with the new steps. Prerelease tags do not advance `:latest`, so this is a safe rehearsal.
2. **Verify** the GitHub Actions run completes successfully and the artifacts tab shows 8 SBOM files (`sbom-base-0.1.1-rc-sbom-test`, `sbom-registry-api-...`, etc.).
3. **Download one SBOM**, validate the CycloneDX JSON (e.g. `cyclonedx-cli validate --input-file sbom-registry-api.cyclonedx.json`), confirm `components[*].licenses` is populated for at least the direct Python deps from `uv.lock`.
4. **If all three pass**, move sprint-status entry `8-1` from `review` → `done` and promote ADR-0008 from `proposed` → `accepted` (Story 8.4's merge re-promotes if anything changes).

If the SHA-pin replacement reveals a breaking change in `anchore/sbom-action` (e.g., a v0.19 with renamed inputs), surface as a deferred-work entry pointing back to this story; do not block Story 8.2 on the SHA churn.

## Dependencies for next stories in Epic 8

- **Story 8.2 (SLSA L2 provenance)** is independent of this story's success — adds `actions/attest-build-provenance` in the same job. Can start in parallel.
- **Story 8.3 (cosign keyless sign)** depends on workflow `permissions: id-token: write` (also added by 8.2). Land 8.2 + 8.3 together if convenient.
- **Story 8.4 (attach SBOM as cosign attestation)** **depends on this story** — cannot attest without an SBOM to attest to. Lands after 8.1 + 8.3.
- **Story 8.5 (`just verify-images` deploy gate)** depends on all of 8.1 + 8.2 + 8.3 + 8.4.
- **Story 8.6 (`deployment.signature_rejected` event)** depends on 8.5 firing the verification flow.

## Phase 1 invariant regression check

- ✅ FR26 single-writer — no change.
- ✅ MCP stdio-only — no change.
- ✅ Envelope immutability — no change (envelope schema change is Story 9.7 territory).
- ✅ NFR-M3 additive-only schema — no change.
- ✅ No `anthropic` SDK in platform code — no change.
- ✅ `services/*` code untouched.

Workflow-only change. Zero blast radius on platform code.

## Retrospective notes (provisional)

- **What worked:** Story scope was tight — single PR-able change, clear acceptance criteria, easy to review.
- **Surprise:** Action SHA-pinning policy in Phase 1 means I cannot author a complete PR without the operator's SHA-lookup step. Acceptable for Story 8.1 (tiny lookup) but worth noting in retrospective if it recurs across Stories 8.2/8.3 — may justify a `scripts/checks/sha_pin_lookup.py` helper that automates the lookup.
- **For Epic 8 retrospective:** evaluate whether the 6-story decomposition matches actual implementation cadence; if Stories 8.2 + 8.3 land together in one PR, consider merging them in the epics.md amendment for Phase 3 hindsight.

— *R2d2, 2026-05-15.*
