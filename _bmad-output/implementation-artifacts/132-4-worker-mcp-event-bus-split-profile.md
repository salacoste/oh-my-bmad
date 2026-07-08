# Story 132.4 — Worker/MCP/event-bus split profile

## Summary

Story 132.4 adds an opt-in worker/MCP/event-bus split profile/readiness slice.
The implementation introduces `docker-compose.worker-mcp-event-bus-split.yml`,
`docs/worker-mcp-event-bus-split-readiness.json`, and
`scripts/check_worker_mcp_event_bus_split.py` with pytest coverage. Default
single-host stdio MCP/local event-log behavior remains unchanged when the overlay
is absent.

## What changed

- Added a `worker-mcp-event-bus-split` compose overlay/profile.
- Wired `worker-wrapper` and `orchestrator-adapter` to internal HTTP MCP service
  URLs for task/session registry, git, GitHub, verification, memory, artifact,
  browser, and clawhip bridge.
- Required `MCP_AUTH_TOKEN` for spawners and `JWT_SECRET_KEY` for MCP services at
  compose interpolation time, without embedding token/secret values.
- Profile-gated the nine MCP HTTP services under the split profile, internal
  compose network only, with no host ports.
- Assigned remote event-log append authority to `clawhip-bridge-mcp` and gave it
  RW access to `/var/lib/oh-my-bmad/registry/events`; registry-state and
  registry-api authorities remain unchanged.
- Added docs/status/just/CI wiring for
  `uv run python scripts/check_worker_mcp_event_bus_split.py` and `--self-test`.

## Operator boundary

This story does not activate live split deployment, create production
credentials, expose host ports, add an external event-bus broker, add external
worker/MCP hosts, mutate production hosts, change registry authority, or add a
runtime production audit emitter.

## Verification commands

```bash
uv run python scripts/check_worker_mcp_event_bus_split.py --self-test
uv run python scripts/check_worker_mcp_event_bus_split.py --verbose
uv run pytest -q tests/scripts/test_check_worker_mcp_event_bus_split.py
```

## Follow-up sequence

Epic 132 remains in progress. Story 132.5 is still the next backlog slice for the
operator/dashboard split profile; Stories 132.6-132.8 remain backlog until
implemented and verified.
