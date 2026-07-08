# Story 132.8 — Epic 132 closure evidence

## Summary

Story 132.8 closes Epic 132 as `readiness-contract-complete_not_live_activation` only. The closure evidence is recorded in `docs/split-deployment-remote-postgres-closure-readiness.json` and enforced by:

```bash
uv run python scripts/check_split_deployment_remote_postgres_closure.py
uv run python scripts/check_split_deployment_remote_postgres_closure.py --self-test
```

This artifact is closure evidence for readiness contracts, not live activation. It does not add live split deployment, live remote Postgres activation, provisioning, production credentials, production migrations, production host mutation, DB mTLS production activation, live load execution, live restore execution, live horizontal scaling activation, or runtime audit emitters. Local/single-host SQLite defaults remain preserved.

## Evidence inventory

The closure checker requires durable Story 132.1-132.7 evidence and executes each Story 132.1-132.7 checker plus its `--self-test` before Epic 132 can be marked done:

- Story 132.1 topology contract: `docs/split-deployment-topology-readiness.json`, `scripts/check_split_deployment_topology.py`, tests, and implementation artifact.
- Story 132.2 remote Postgres readiness: `docs/remote-postgres-production-readiness.json`, `scripts/check_remote_postgres_readiness.py`, tests, and implementation artifact.
- Story 132.3 registry remote Postgres profile: `docker-compose.registry-remote-postgres.yml`, `docs/registry-remote-postgres-profile-readiness.json`, checker/tests, and implementation artifact.
- Story 132.4 worker/MCP/event-bus profile: `docker-compose.worker-mcp-event-bus-split.yml`, `docs/worker-mcp-event-bus-split-readiness.json`, checker/tests, and implementation artifact.
- Story 132.5 operator/dashboard split profile: `docker-compose.operator-dashboard-split.yml`, `docs/operator-dashboard-split-readiness.json`, checker/tests, and implementation artifact.
- Story 132.6 horizontal scaling readiness: `docs/horizontal-scaling-readiness.json`, `scripts/check_horizontal_scaling_readiness.py`, tests, and implementation artifact.
- Story 132.7 failure/load/backup/restore validation readiness: `docs/failure-load-backup-restore-readiness.json`, `scripts/check_failure_load_backup_restore_readiness.py`, tests, and implementation artifact.

## quality_gates

Final closure requires these gates:

- `local_checker.status: passed` for the Story 132.8 checker, self-test, pytest, ruff check, ruff format verification, and successful subordinate Story 132.1-132.7 checker/self-test execution.
- `ci_wiring.status: passed` for justfile and CI wiring of all Story 132.1-132.8 checkers plus self-tests.
- `code_review.status: passed` with non-leader durable code-review native subagent evidence; leader, self-attested, manual-summary, and artifact-only evidence is rejected.
- `ultraqa.status: passed` with non-leader durable UltraQA/verifier native subagent evidence; pending placeholders do not satisfy final closure.

The checker intentionally fails if `quality_gates.code_review` or `quality_gates.ultraqa` is pending, leader-authored, manually summarized, artifact-only, missing source references, missing non-leader source identity, or backed only by the quality-gate source-record JSON without matching native subagent provenance. Native subagent records must include `thread_id`, `agent_role`, `status: completed`, and `completed_at`, and must match native subagent provenance: local OMX subagent tracking plus `agent-turn-complete` logs when present, or the committed sanitized native-subagent provenance bundle when local runtime state is absent.

## Readiness domains

Closure covers topology, remote Postgres, registry profile, worker/MCP/event-bus profile, operator/dashboard profile, horizontal scaling, failure/load/backup/restore validation, and Epic 133 DB mTLS composition. DB mTLS production activation remains deferred to Epic 133 evidence and future operator activation; Epic 132 does not activate it.

## Fail-closed statements

Epic 132 closure requires explicit fail-closed statements for no live activation, no provisioning, no credentials, no production migration, no production host mutation, no DB mTLS production activation, no load execution, no restore execution, no scaling activation, no runtime audit emitter, local single-host SQLite default preservation, and a future approved activation story before any live rollout.

## Verification commands

```bash
uv run python scripts/check_split_deployment_remote_postgres_closure.py --self-test
uv run python scripts/check_split_deployment_remote_postgres_closure.py --verbose
uv run pytest -q tests/scripts/test_check_split_deployment_remote_postgres_closure.py
uv run ruff check scripts/check_split_deployment_remote_postgres_closure.py tests/scripts/test_check_split_deployment_remote_postgres_closure.py
uv run ruff format --check scripts/check_split_deployment_remote_postgres_closure.py tests/scripts/test_check_split_deployment_remote_postgres_closure.py
```
