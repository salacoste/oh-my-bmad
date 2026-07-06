# Story 131.3 — GitHub write activation readiness

Story 131.3 records and enforces the controlled GitHub write activation contract
without enabling real GitHub writes. This is a static/readiness slice only: no
runtime env flag is read, no credential value is added, no GitHub API mutation is
performed, and github-mcp writes remain simulated by default.

## Implemented scope

- `docs/github-write-activation-readiness.json` records the activation-readiness
  contract for github-mcp write tools, including scoped credential dependency,
  repo authority, approval, simulation parity, smoke-test/cleanup expectations,
  rate-limit handling, audit evidence, emergency disable, and out-of-scope
  fail-closed behavior.
- `scripts/check_github_write_activation.py` validates current repo bindings:
  - activation remains `deferred_fail_closed` and `static_readiness_only`;
  - `GITHUB_MCP_WRITE_ENABLED` is documented as a future operator gate only and
    is not read by runtime code;
  - `GitHubWriteClient` still defaults `simulate=True`;
  - `build_server` does not pass `simulate=False`;
  - every write tool in `TIER_MAP` is `Tier.THREE`;
  - each write handler validates `caller_trace_id`, uses
    `check_tier_with_approval`, binds `approval_lookup=approval_lookup`, guards
    owner/repo before invoking the write client, and emits/result-describes the
    expected `github.*` event;
  - rate-limit retry evidence remains present in the REST adapter.
- `tests/scripts/test_check_github_write_activation.py` covers the checker,
  self-test, live tree, simulate-disable regression, tier downgrade, runtime flag
  read, and missing-evidence failures.
- CI/local gates run the checker and self-test.

## Non-goals / fail-closed boundaries

Story 131.3 does **not**:

- enable real GitHub writes;
- read or act on a production write-enable env flag;
- provision, store, rotate, or revoke credentials beyond the Story 131.2 static
  credential contract;
- perform a live GitHub issue, PR, review, comment, branch, label, merge, or
  repository mutation;
- add deployment changes, command surfaces, dashboards, Telegram/console controls,
  scheduled jobs, or retention/lifecycle behavior;
- claim Story 131.4+ production operation work is complete.

## Verification evidence

Expected focused verification:

```bash
uv run python scripts/check_github_write_activation.py --verbose
uv run python scripts/check_github_write_activation.py --self-test
uv run pytest tests/scripts/test_check_github_write_activation.py
uv run ruff check scripts/check_github_write_activation.py tests/scripts/test_check_github_write_activation.py
uv run ruff format --check scripts/check_github_write_activation.py tests/scripts/test_check_github_write_activation.py
git diff --check
```

## Next story boundary

A later approved activation story may add real-write enablement only after it has
operator/repo/security approval, scoped credential evidence, fresh dry-run proof,
real-write smoke/cleanup evidence, emergency-disable proof, and runtime audit
verification for exactly one named repository. Until then, github-mcp write tools
remain simulated/default-denied for production activation.
