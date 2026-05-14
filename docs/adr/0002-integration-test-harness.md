# ADR-0002: Integration test harness sharing strategy

## Status

Accepted

## Context

Integration test helper code is duplicated across 10+ test files (~610 total redundant lines). Three consecutive retrospectives flagged this:

1. **Epic 5 retro** (2026-05-09): "~62 lines duplicated between scripted_worker_stub and auto_approval_stub"
2. **Epic 6 retro** (2026-05-11): "Nearly identical `_Harness` classes across 3 test files. Action: extract to conftest." — NOT DONE
3. **Epic 7 retro** (2026-05-13): "This is the third retro flagging it. Decide: shared module OR formally accept self-contained design."

### Duplication audit (3 clusters)

**Cluster 1 — ASGI harness** (~200 lines across 4 files):
Core wiring (`_db_url`, `_seed_tables`, event loop snapshot/restore, LifespanManager + ASGITransport) is byte-for-byte identical. The `_Harness` class itself is actively diverging: `test_command_injection_fuzz` adds `_RequestRecorder`, `registry_client`, and guard machinery; `test_tier3_negative` parameterizes by `actor_kind`.

**Cluster 2 — Stub helpers** (~170 lines across 3 files):
`_install_signal_handlers` is byte-for-byte identical across all 3 files. `_connect_mcp` is identical between 2 files. `_read_new_lines` shares a tail-read skeleton but has diverged: `null_orchestrator` returns typed `EventEnvelope` objects via `from_canonical_json`, while the others return raw dicts via `json.loads`.

**Cluster 3 — Docker-compose journey helpers** (~225 lines across 3 files):
192/225 lines are byte-for-byte identical. The only divergences are trivial: a single env-key string and one length guard. Strongest extraction candidates.

## Decision

**Hybrid extraction**: Extract stable, identical code into shared modules. Leave actively diverging code self-contained with documentation.

1. **Extract Docker-compose journey helpers** to `tests/integration/_compose_helpers.py`. These are the strongest candidates (192/225 lines identical, trivial parameterization for differences).

2. **Extract identical stub helpers** (`_install_signal_handlers`, `_connect_mcp`) to `tests/fixtures/_stub_helpers.py`. Leave `_read_new_lines` per-file since `null_orchestrator` has meaningfully diverged.

3. **Extract ASGI base utilities** (`_db_url`, `_seed_tables`, event loop snapshot/restore) to `tests/integration/_asgi_harness.py`. Keep `_Harness` classes per-file since they are actively diverging — the base utilities are the stable core.

4. **Document the convention**: New integration tests import shared utilities from the appropriate module. Per-file customization is allowed and expected for divergent concerns.

## Rationale

- The "third retro" signal indicates the status quo is not working. A decision (either way) is better than deferring again.
- 10 files is enough consumers to justify extraction. The pattern will grow with new journey and validation stories.
- The hybrid approach avoids the coupling risk of extracting diverging code while still eliminating the bulk of duplication.
- Docker-compose helpers are the cleanest win: nearly all identical, trivial to parameterize.

## Consequences

- Shared modules create coupling: changes to `_compose_helpers.py` affect 3 journey tests. This is acceptable — the interface is stable and well-documented.
- `_Harness` classes remain per-file. New test files should copy the pattern from the most similar existing test and import base utilities from `_asgi_harness.py`.
- `null_orchestrator` keeps its own `_read_new_envelopes_since` — it returns typed objects, not raw dicts. Documenting this divergence prevents future confusion.
- Bug fixes to shared helpers propagate automatically. This is a feature, not a risk.
