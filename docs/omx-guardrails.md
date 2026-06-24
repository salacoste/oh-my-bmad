# OMX Guardrails

## Planning/review stale-lane capacity rule

Planning/review waits are capped at 5 minutes.

On timeout, attempt one replacement lane spawn.

If replacement spawn is unavailable, record a stale/capacity incident and stop that lane cleanly.

Do not use multi_agent_v1.close_agent as recovery for a stale planning/review lane.

No unbounded waits are permitted for planning/review lanes.
