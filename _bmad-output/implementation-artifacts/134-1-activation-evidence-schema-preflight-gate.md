# Story 134.1 — Activation evidence schema preflight gate

## Summary

Story 134.1 is complete locally as a docs/status/static-checker slice for Phase 51 / Epic 134. It adds `docs/controlled-activation-evidence.json` plus an executable static preflight checker for future controlled production activation evidence packages.

This story is not activation evidence, not activation proof, and not a production go/no-go decision. It defines the fail-closed evidence contract that later Stories 134.2-134.6 must satisfy before any future/operator-gated activation claim can be considered.

## Scope

- Added a versioned Story 134.1 JSON contract for future controlled activation evidence packages.
- Added a static checker and tests for contract structure, docs/status wiring, just/CI wiring, and scoped unsafe-language scans.
- Updated status docs to mark Story 134.1 complete locally while Epic 134 remains in progress.
- Preserved Phase 51 as future/operator-gated/no-live-activation planning.

## Contract

`docs/controlled-activation-evidence.json` defines required future evidence package fields for:

- operator and security approval references;
- UTC change window;
- exact target environment, service, and version binding;
- readiness prerequisites as prerequisites only, not proof activation occurred;
- activation intent;
- smoke scope;
- rollback owner and rollback plan reference;
- emergency-disable owner and emergency-disable plan reference;
- evidence retention;
- generated and expires timestamps;
- trace correlation;
- redaction report reference and redaction statement;
- independent reviewer reference with self-attestation rejected.

The contract requires fail-closed stale/missing/malformed evidence handling and forbids plaintext secrets, credential values, private key content, certificate material, unredacted DSNs, plaintext fallback, and activation overclaims.

## Checker

`scripts/check_controlled_activation_evidence.py` validates:

- contract schema/version/required fields;
- fail-closed staleness policy;
- redaction and secret-hygiene rules;
- future story references and docs/status references;
- justfile and CI checker/self-test wiring;
- Story 134.1 done status and Epic 134 in-progress status;
- scoped docs/status text for secret-like values, activation overclaims, readiness-as-proof language, self-attestation acceptance, and plaintext fallback allowances.

The sprint-status text scan includes canonical `current_phase`, the Epic 134 / Story 134.1 region, and full relevant Story 134.1/Epic 134 audit entries while avoiding unrelated historical audit false positives. Future `--evidence` packages fail closed on stale/future/malformed UTC timestamps, malformed change windows, weak semantic fields, secret-like material, and unsafe activation language.

## No live activation boundaries

No live activation is performed or claimed. This story does not add or change runtime code, deployment behavior, production host state, credentials, certificate material, lockfiles, dependencies, migrations, compose/profile production activation, provisioning, smoke execution, rollback execution, emergency-disable execution, or plaintext fallback.

Readiness artifacts remain prerequisites only and are not proof activation occurred.

## Verification commands

```bash
python -m json.tool docs/controlled-activation-evidence.json >/dev/null
uv run python scripts/check_controlled_activation_evidence.py --self-test
uv run python scripts/check_controlled_activation_evidence.py
uv run python scripts/check_controlled_activation_evidence.py --evidence .omx/tmp/story-134-1-valid-future-evidence.json
uv run pytest -p no:cacheprovider tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py
uv run pytest tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py
uv run ruff check scripts/check_controlled_activation_evidence.py tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py
git diff --check -- .github/workflows/ci.yml justfile docs/feature-status.md docs/project-overview.md _bmad-output/implementation-artifacts/sprint-status.yaml _bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md docs/controlled-activation-evidence.json scripts/check_controlled_activation_evidence.py tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py
```

## Local verification result
- `python -m json.tool docs/controlled-activation-evidence.json >/dev/null` — exit 0.
- `uv run python scripts/check_controlled_activation_evidence.py --self-test` — exit 0.
- `uv run python scripts/check_controlled_activation_evidence.py` — exit 0; contract/status checks passed.
- `uv run python scripts/check_controlled_activation_evidence.py --evidence .omx/tmp/story-134-1-valid-future-evidence.json` — exit 0; package shape checks passed and output states not activation proof.
- `uv run pytest -p no:cacheprovider tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py` — 367 passed, 367 warnings.
- `uv run pytest tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py` — 367 passed, 367 warnings.
- `uv run ruff check scripts/check_controlled_activation_evidence.py tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py` — all checks passed.
- `git diff --check -- .github/workflows/ci.yml justfile docs/feature-status.md docs/project-overview.md _bmad-output/implementation-artifacts/sprint-status.yaml _bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md docs/controlled-activation-evidence.json scripts/check_controlled_activation_evidence.py tests/scripts/test_check_controlled_activation_evidence.py tests/scripts/test_check_db_mtls_readiness.py` — exit 0.
- `uv run pytest tests/scripts/test_check_db_mtls_readiness.py -q` — 33 passed, 33 warnings.
- `uv run pytest tests/scripts/test_check_db_mtls_readiness.py tests/scripts/test_check_controlled_activation_evidence.py -q` — 367 passed, 367 warnings.
- `uv run pytest tests/scripts -q` — 646 passed, 646 warnings.
- `uv run pytest -m "not slow"` — broader baseline run completed with Story 134.1/scripts green, but repository-wide suite still has unrelated failures in unchanged contract/dashboard/spawn-site/worker-wrapper config areas.
