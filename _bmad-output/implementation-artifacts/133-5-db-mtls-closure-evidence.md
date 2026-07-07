# Story 133.5 DB mTLS closure evidence

## Summary

Story 133.5 closes the local Epic 133 DB connection mTLS readiness track. The
implemented capability is runtime-gated registry-state DB mTLS support plus an
executable production-readiness contract. This artifact does not authorize live
Postgres provisioning, production host mutation, real certificate material, or
production activation.

## Planning approvals

- Architect APPROVE/CLEAR: `_bmad-output/implementation-artifacts/133-db-mtls-architect-approval-cycle-3.md`
  records `Verdict: APPROVE/CLEAR` and confirms exact server-side Postgres
  evidence, rotation/revocation, fail-closed URL semantics, canonical secret path
  policy, and redaction coverage.
- Critic APPROVE/CLEAR: `_bmad-output/implementation-artifacts/133-db-mtls-critic-approval-cycle-3.md`
  records `Verdict: APPROVE/CLEAR` and confirms prior blockers are covered;
  server-side Postgres evidence remains operator-contract evidence unless safely
  simulated locally.

## Closure gate evidence

- enabled/disabled DB mTLS matrix: covered by targeted runtime/checker tests for
  disabled SQLite default preservation, enabled PostgreSQL+asyncpg requirement,
  unsafe `sslmode` rejection, and fail-closed non-Postgres/non-asyncpg URLs.
- rotation/revocation drill evidence: covered by local runtime-gated expiry,
  rotation-warning, CRL, old-client-cert rejection, revoked/old server rejection,
  approved-prefix CRL policy, and operator drill documentation evidence.
- failure-mode and observability evidence: covered by local sanitized diagnostic
  tests, canonical failure classes, bounded retry metadata, and no-plaintext
  fallback URL policy checks.
- secret-scanner evidence: `git ls-files -z | xargs -0 uv run secret-hygiene-precommit`
  exited 0; only warning was that `scancode-toolkit` is not installed, so the
  license scan was skipped by that tool.
- docs/status links: `docs/db-mtls-readiness.json`, `docs/operator-runbook.md`,
  `docs/production-operations.md`, `docs/feature-status.md`, this closure
  artifact, and `_bmad-output/implementation-artifacts/sprint-status.yaml` are
  cross-linked by the readiness checker.
- code-review: cycle 3 APPROVE/CLEAR recorded in `.omx/code-review/epic-133-db-mtls-code-review-cycle-3.md`,
  source thread `019f3e66-9f5e-7bf1-a9d6-1548fa05aba2`; no P0/P1 blockers.
- UltraQA: initial UltraQA failed on closure/status/checker contradictions
  (`.omx/tmp/epic-133-autopilot-rework-qa.json`, source thread
  `019f3e6b-3132-7543-93f5-e92872d35c2e`). This closure rework removes those
  contradictions by replacing stale placeholders with evidence, closing sprint
  status, correcting the feature-status table, and adding checker regressions.
- command-output: cycle-2/rework evidence recorded targeted pytest `75 passed`,
  readiness checker passed, checker self-test passed, Ruff check passed, Ruff
  format check passed, mypy strict passed, `just check-gates-self-test` passed,
  `just check-gates` passed, and isolated mTLS import
  `uv run --isolated --no-dev --package mtls python -c "import mtls.db; print('ok')"`
  printed `ok`.
- split-deployment/remote Postgres composition: still fail-closed for live split
  deployment/remote Postgres activation; Epic 132 remains a static topology
  contract, and Epic 133 requires operator server evidence before production DB
  mTLS activation.

## Closure status

- docs: concrete evidence recorded above and linked from feature/runbook/ops docs.
- CI: just/CI checker wiring exists; local `just check-gates` and
  `just check-gates-self-test` passed in prior evidence.
- checker: readiness checker and self-test passed; this rework adds regression
  enforcement for stale closure placeholders, sprint-status closure state, and
  feature-status DB mTLS table drift.
- scanner: secret-hygiene precommit exited 0 with scancode-toolkit warning only.
- code-review: cycle 3 APPROVE/CLEAR recorded with artifact and source thread.
- UltraQA: prior UltraQA failure is satisfied by this closure/status/checker
  enforcement rework and the verification commands recorded for this change.


## Closure rework verification

- `uv run pytest tests/scripts/test_check_db_mtls_readiness.py -q` → `29 passed, 29 warnings`.
- `uv run python scripts/check_db_mtls_readiness.py && uv run python scripts/check_db_mtls_readiness.py --self-test` → checker passed; self-test printed `DB mTLS readiness self-test passed`.
- `uv run ruff check scripts/check_db_mtls_readiness.py tests/scripts/test_check_db_mtls_readiness.py` → `All checks passed!`.
- `uv run ruff format --check .` → `625 files already formatted`.
- `uv run pytest packages/mtls/tests/test_db_mtls.py services/registry-state/src/registry_state/test_postgres_mtls.py tests/scripts/test_check_db_mtls_readiness.py -q` → `79 passed, 86 warnings`.

## Static artifact links

- Contract: `docs/db-mtls-readiness.json`
- Checker: `scripts/check_db_mtls_readiness.py`
- Story 133.1 artifact: `_bmad-output/implementation-artifacts/133-1-db-mtls-static-readiness-contract.md`
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml`
- Code-review cycle 3: `.omx/code-review/epic-133-db-mtls-code-review-cycle-3.md`
- UltraQA failure state: `.omx/tmp/epic-133-autopilot-rework-qa.json`

## Non-activation statement

Runtime-gated registry-state DB mTLS code is present, but live Postgres provisioning,
production host mutation, real certificate material, plaintext fallback, or
production deployment/profile activation is not authorized by this artifact.
