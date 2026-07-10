# Story 134.2 — Split Deployment Activation Smoke Evidence Package

## Summary

Story 134.2 is complete locally as a docs/status/static-checker slice for a future/operator-gated split-deployment activation smoke evidence package.

This story is not activation evidence, not activation proof, and not a production go/no-go decision. It defines the fail-closed evidence contract that a later operator-run activation story must satisfy before any split-deployment activation claim can be considered.

## Scope delivered

- Added `docs/split-deployment-activation-smoke-evidence.json` as the Story 134.2 machine-readable future evidence contract.
- Added `scripts/check_split_deployment_activation_smoke_evidence.py` to validate contract shape, docs/status wiring, just/CI wiring, redaction hygiene, forbidden overclaim language, readiness-as-proof rejection, and no-live-activation boundaries.
- Added `tests/scripts/test_check_split_deployment_activation_smoke_evidence.py` with fail-closed regressions for missing domains, activation flags, overclaim language, readiness-as-proof language, wiring drift, status drift, and secret-like material.
- Wired the checker into `just lint`, `just check-gates`, `just check-gates-self-test`, and CI static/self-test gates.
- Updated status docs to mark Story 134.2 complete locally while Epic 134 remains in progress and Stories 134.3-134.6 remain future/operator-gated planning.

## Required future evidence domains

The contract requires future, redacted, timestamped evidence for:

1. service placement;
2. network boundaries;
3. `registry-state` single-writer authority;
4. event-log append authority;
5. MCP boundary;
6. operator/dashboard ingress boundary;
7. health/readiness smoke checks;
8. rollback and emergency-disable handling.

Readiness artifacts remain prerequisites only and are not proof activation occurred.

## Safety boundary

No live split deployment activation, external load-balancer activation, host-port change, production host mutation, compose/profile activation, provisioning, migration execution, credential handling, certificate material handling, plaintext fallback, runtime behavior change, deployment config change, dependency change, lockfile change, or production-state change is performed by this story.

## Local verification commands

Run before review/merge:

```bash
python -m json.tool docs/split-deployment-activation-smoke-evidence.json >/dev/null
uv run python scripts/check_split_deployment_activation_smoke_evidence.py --self-test
uv run python scripts/check_split_deployment_activation_smoke_evidence.py
uv run python scripts/check_controlled_activation_evidence.py
uv run pytest tests/scripts/test_check_split_deployment_activation_smoke_evidence.py tests/scripts/test_check_controlled_activation_evidence.py
uv run ruff check scripts/check_split_deployment_activation_smoke_evidence.py tests/scripts/test_check_split_deployment_activation_smoke_evidence.py
uv run ruff format --check scripts/check_split_deployment_activation_smoke_evidence.py tests/scripts/test_check_split_deployment_activation_smoke_evidence.py
```

## CI expectation

CI must run both:

- `uv run python scripts/check_split_deployment_activation_smoke_evidence.py`
- `uv run python scripts/check_split_deployment_activation_smoke_evidence.py --self-test`

The checker reports that this is not activation proof.
