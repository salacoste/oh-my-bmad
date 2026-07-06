# Story 131.4 — Deployment Change Control and Rollback Readiness

Date: 2026-07-06

## Scope

Story 131.4 adds a static/readiness-only deployment change control contract for
Epic 131. It documents and checks the existing digest-pinned deployment profile,
rollback/backup expectations, production credential dependency, and fail-closed
preflight evidence required before any future live deployment-affecting story.

## Added artifacts

- `docs/deployment-change-readiness.json` — machine-readable deployment change
  readiness contract.
- `scripts/check_deployment_change_readiness.py` — static checker for the
  contract, justfile deployment recipes, digest compose overlay, and docs.
- `tests/scripts/test_check_deployment_change_readiness.py` — self-test and
  negative-drift coverage for the checker.

## Safety boundary

This story does not run docker compose, execute migrations, read or provision
credentials, contact hosts, create production command surfaces, emit runtime
production audit events, or mutate deployments. Existing deployment recipes
remain unchanged except for static CI/check-gate validation of the documented
readiness boundary.

## Readiness evidence pinned by the checker

- `deploy-vps-digest` and `deploy-macos-digest` continue to depend on
  `verify-images`.
- `docker-compose.digest.yml` keeps first-party service images pinned through
  fail-loud `OMB_IMAGE_DIGEST_*` substitutions and `build: !reset null`.
- Tag-based deploy recipes remain documented as deprecated for production.
- `verify-images` retains cosign signature, SLSA provenance, SBOM, `.env`, owner,
  and digest-format gates.
- Backup, restore, migration, rollback, health, and destructive restore
  confirmation documentation remains discoverable from deployment/runbook docs.

## Verification commands

- `uv run --python 3.12 python scripts/check_deployment_change_readiness.py --verbose`
- `uv run --python 3.12 python scripts/check_deployment_change_readiness.py --self-test`
- `uv run --python 3.12 pytest tests/scripts/test_check_deployment_change_readiness.py -q`
