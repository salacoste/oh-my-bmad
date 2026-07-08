# Story 132.5 — Operator/dashboard split profile

## Summary

Story 132.5 adds an opt-in operator/dashboard split profile/readiness slice. The
implementation introduces `docker-compose.operator-dashboard-split.yml`,
`docs/operator-dashboard-split-readiness.json`, and
`scripts/check_operator_dashboard_split.py` with pytest coverage. Default
single-host/local behavior remains unchanged when the overlay is absent.

## What changed

- Added an `operator-dashboard-split` compose overlay/profile scoped only to
  actual compose services: `telegram-gateway` and `clawhip-daemon`.
- Kept `console-cli` as host-side operator invocation and required checker
  evidence that it is not a compose service.
- Kept `dashboard/static` as a static/browser asset boundary with future ingress
  readiness only; no dashboard compose service or live serving path is added.
- Required placeholder-only `OPERATOR_DASHBOARD_AUTH_TOKEN` at compose
  interpolation time for future ingress/proxy evidence, without adding runtime
  auth enforcement or embedding a token value.
- Added a readiness contract covering Story 132.3/132.4 prerequisites,
  Telegram/console/dashboard ingress boundaries, auth boundary preservation,
  health/readiness evidence, trace propagation, version compatibility, browser
  payload/log secret hygiene, and overclaim prevention.
- Added docs/status/just/CI wiring for
  `uv run python scripts/check_operator_dashboard_split.py` and `--self-test`.

## Operator boundary

This story does not activate live split deployment, create production
credentials, expose host ports, configure an external host, activate a reverse
proxy or tunnel, add runtime auth enforcement, add a dashboard compose service,
add a console compose service, mutate production hosts, change registry/worker
/MCP/event-bus authority, or add a runtime production audit emitter.

## Verification commands

```bash
uv run python scripts/check_operator_dashboard_split.py --self-test
uv run python scripts/check_operator_dashboard_split.py --verbose
uv run pytest -q tests/scripts/test_check_operator_dashboard_split.py
```

## Follow-up sequence

Epic 132 remains in progress. Story 132.6 horizontal scaling, Story 132.7
failure/load/backup/restore validation, and Story 132.8 closure evidence remain
backlog until implemented and verified.
