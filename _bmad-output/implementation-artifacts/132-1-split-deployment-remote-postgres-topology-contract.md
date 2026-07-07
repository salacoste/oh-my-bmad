# Story 132.1 — Split deployment and remote Postgres topology contract

## Summary

Story 132.1 adds a static/readiness-only contract for future split deployment and
remote Postgres topology work:

- `docs/split-deployment-topology-readiness.json`
- `scripts/check_split_deployment_topology.py`
- `tests/scripts/test_check_split_deployment_topology.py`

The contract preserves the current single-host/local default and records required
future evidence for service placement, network boundaries, remote Postgres data
authority, pooling, migrations, backups, ingress, secrets handling,
observability, unsupported topologies, rollback/fallback, and core invariants.
It explicitly defers DB mTLS to Epic 133.

## No runtime or deployment expansion

No runtime/deployment behavior was added. This story adds no compose profile or
overlay, no environment activation flag, no deployment target, no migration
runner, no service route, no Dockerfile behavior, no remote Postgres connection
code, no external host command, no credential value, no live split deployment,
and no remote Postgres activation.

## Gate

Run the checker with:

```bash
uv run python scripts/check_split_deployment_topology.py
uv run python scripts/check_split_deployment_topology.py --self-test
```

The gate validates required topology sections, docs/status references, mandatory
just/CI wiring, secret absence, overclaim prevention, and forbidden runtime
expansion surfaces.

## Verification plan

- `uv run python scripts/check_split_deployment_topology.py --self-test`
- `uv run python scripts/check_split_deployment_topology.py`
- `uv run pytest tests/scripts/test_check_split_deployment_topology.py -q`
- `uv run ruff check scripts/check_split_deployment_topology.py tests/scripts/test_check_split_deployment_topology.py`
- `uv run ruff format --check scripts/check_split_deployment_topology.py tests/scripts/test_check_split_deployment_topology.py`
- `git diff --check`
- `just check-gates`
- `just check-gates-self-test`
