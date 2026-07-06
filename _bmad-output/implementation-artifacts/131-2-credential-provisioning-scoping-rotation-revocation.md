# Story 131.2 — credential provisioning, scoping, rotation, and revocation

Story 131.2 implements an executable production credential readiness contract. It
adds no real secret values and does not activate real GitHub writes, deployment
mutations, command surfaces, lifecycle/retention jobs, or runtime production
audit emitters.

## Implemented scope

- `docs/production-credential-inventory.json` records the provisioned credential
  contract for `GITHUB_MCP_SCOPED_TOKEN`:
  - scope and env location;
  - authorized subprocess (`github`) and allowlist sources;
  - broad forbidden env vars, including `GITHUB_TOKEN`;
  - rotation and revocation procedures;
  - scanner coverage;
  - metadata-only `secret.accessed` behavior required before future activation.
- `scripts/check_production_credentials.py` validates the inventory and repo
  bindings:
  - inventory version/story/activation state;
  - required credential fields;
  - no real-looking credential values in the inventory;
  - `GITHUB_MCP_SCOPED_TOKEN` appears in worker/orchestrator MCP allowlists and
    only in `_SERVER_REQUIRED_ENV["github"]`;
  - `GITHUB_TOKEN` and the scoped token do not reach generic agent/runtime
    allowlists;
  - forbidden broad production env vars do not reach MCP subprocess allowlists;
  - docs refs and scanner coverage exist.
- `tests/scripts/test_check_production_credentials.py` covers the checker,
  self-test, live tree, broad-token rejection, and non-github server leakage.
- CI and local gates now run the credential checker:
  - `.github/workflows/ci.yml` adds `Check production credentials (Story 131.2)`;
  - `just lint`, `just check-gates`, and `just check-gates-self-test` include the
    new gate/self-test.
- `docs/operator-runbook.md`, `docs/production-operations.md`,
  `docs/feature-status.md`, and sprint status record the Story 131.2 boundary.

## Non-goals / fail-closed boundaries

Story 131.2 does **not**:

- commit or generate real credential values;
- enable any GitHub MCP production-write flag;
- change GitHub write simulation behavior;
- add deployment mutation behavior or environment-changing commands;
- add dashboard/Telegram/console production operation controls;
- implement runtime `secret.accessed` emission for github-mcp token reads;
- reclassify Story 131.3+ work as complete.

## Verification evidence

Initial local verification:

```bash
uv run python scripts/check_production_credentials.py --verbose
uv run python scripts/check_production_credentials.py --self-test
uv run pytest tests/scripts/test_check_production_credentials.py
```

Expected result: credential readiness OK, self-test OK, and focused pytest green.

Broader verification for the PR should include:

```bash
git diff --check
uv run python scripts/check_no_secrets.py
uv run python scripts/check_no_secrets.py --self-test
uv run python scripts/check_production_credentials.py --verbose
uv run python scripts/check_production_credentials.py --self-test
uv run pytest tests/scripts/test_check_production_credentials.py
uv run ruff check scripts/check_production_credentials.py tests/scripts/test_check_production_credentials.py
uv run ruff format --check scripts/check_production_credentials.py tests/scripts/test_check_production_credentials.py
```

## Next story boundary

Story 131.3 may activate controlled GitHub writes only after it supplies the
separate dry-run, approval, scoped-token evidence, emergency-disable,
rate-limit, real-write smoke-test, and audit gates defined by
`docs/production-operations.md`. Story 131.2 is a readiness/checker slice only.
