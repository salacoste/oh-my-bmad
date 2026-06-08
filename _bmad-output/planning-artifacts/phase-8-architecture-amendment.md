## Phase 8 Architecture Amendment -- Platform Hardening & Debt Resolution

> **Amendment added:** 2026-06-08.
>
> **Companion documents:**
> - PRD amendment: see [`phase-8-prd-amendment.md`](./phase-8-prd-amendment.md) (FR108-FR121, NFR-O17-O18, NFR-S17-S18, NFR-R13-R14, NFR-M13-M14).
> - API versioning: see [`docs/adr/0021-api-versioning-strategy.md`](../../docs/adr/0021-api-versioning.md) (proposed) -- additive-only within v1.
> - Prior amendments: [`phase-6-architecture-amendment.md`](./phase-6-architecture-amendment.md) (P6-I1 through P6-I5), [`phase-7-architecture-amendment.md`](./phase-7-architecture-amendment.md) (if applicable).

**Theme.** The debt resolution closure -- resolve all 19 GATED items in `deferred-work.md`, close 7 WONTDO items with rationale, and optionally ship 2 feature candidates (FC-P6-1 auto-scaling, FC-P6-2 Gemini schema enforcement). Phase 8 does not introduce new architectural archetypes, new services, or new invariant classes. It closes the open loop.

### Preserved invariants (Phase 1 through Phase 7 carry forward)

All prior invariants stand unchanged. As they apply to the new surface:

- **FR26 single-writer (P2-I1).** Per-server env scoping does not create new DB writers. Each MCP server's env isolation is a subprocess-configuration concern, not a persistence concern.
- **MCP transport stdio-only (P2-I4).** Auto-scaling uses Docker Compose CLI (out-of-band), not MCP. No new transport surfaces.
- **Credential isolation (P5-I1, P6-I5).** Per-server env scoping (FR115) strengthens the existing credential isolation by ensuring each server receives only its own allowlisted vars. No server gains access to another server's credentials.
- **Event-driven state transitions (P6-I3).** Auto-scaling events (`pool.scaled`) are emitted through the normal event spine. No direct state mutations.
- **Runtime adapter protocol (ADR-0015).** Gemini schema enforcement is adapter-internal. The protocol interface does not change.

### New invariants (Phase 8)

Phase 8 introduces **no new invariant classes**. This is deliberate -- Phase 8 is a closure phase, not a feature phase.

The one discipline rule that Phase 8 formalizes:

| # | Invariant | Why |
|---|---|---|
| **P8-I2** | **Closure is the deliverable -- zero open GATED items is the success criterion.** A phase that ships features but leaves GATED items open has failed. | The entire purpose of Phase 8 is to empty the deferred-work backlog. Feature candidates are optional; debt closure is mandatory. |

### ADR-0021: API Versioning Strategy

**Location:** `docs/adr/0021-api-versioning-strategy.md` (proposed, gates Epic 41)

**Decision:** The `/v1/` HTTP API follows additive-only evolution. Permitted changes:

1. New optional response fields with sensible defaults.
2. New endpoints under `/v1/`.
3. Addition of `response_model` to existing endpoints (OpenAPI schema enrichment, not wire-change).
4. New query parameters with backward-compatible defaults.

**Prohibited changes within `/v1/`:**

1. Removing or renaming an existing response field.
2. Changing a field's type.
3. Removing an endpoint.
4. Changing the semantics of an existing parameter.

**Breaking changes require `/v2/`:** any change that violates the above rules must be introduced under a new API version prefix with a migration guide and a deprecation timeline for `/v1/` endpoints.

**Resolves:** GATED-ARCH D6 from Story 7.5.6 ("Adding response_model would break wire contract; requires API versioning decision").

### Events composite index migration

**Location:** Alembic migration (next in sequence after existing chain)

**Index:** `ix_events_task_id_mono_ns` on `events(task_id, emitted_at_monotonic_ns)`

**Purpose:** The `GET /v1/tasks/{id}/events?after=` endpoint performs a range query on events for a specific task. Without a composite index, the query uses a table scan or the primary key index, which degrades linearly with event count. The composite index enables an index-only scan for the common pagination pattern.

**Migration details:**

```python
# Alembic migration
def upgrade() -> None:
    op.create_index(
        "ix_events_task_id_mono_ns",
        "events",
        ["task_id", "emitted_at_monotonic_ns"],
        unique=False,
    )

def downgrade() -> None:
    op.drop_index("ix_events_task_id_mono_ns", table_name="events")
```

**Postgres note:** For production deployments with large event tables, the migration may use `CREATE INDEX CONCURRENTLY` to avoid locking. The Alembic migration should detect the backend and use the appropriate strategy.

**SQLite note:** SQLite's `CREATE INDEX` is online (does not block reads). No special handling needed.

**Resolves:** GATED-ARCH D1 from Story 7.5.6 ("Low urgency at current scale; requires Alembic migration. Add ix_events_task_id_mono_ns when query latency warrants").

### Per-server env scoping

**Context:** GATED-P0 from Story 16.5 ("Per-server env scoping -- defense-in-depth; scoped token reaches all MCP children").

**Current state:** Phase 5 introduced per-runtime allowlists (`_CHILD_ENV_ALLOWLIST` + `_CHILD_ENV_PREFIXES`) for the worker subprocess. Phase 6 extended this to the Gemini adapter. Phase 3 applied allowlists to MCP servers via the child-env discipline. However, the allowlist enforcement is per-spawner, not centrally governed.

**Phase 8 resolution:** Formalize the per-server allowlist as a config-driven registry. Each MCP server declares its allowlist in its server configuration (or code). The MCP spawner (`mcp_clients.py`) enforces the allowlist centrally rather than relying on each server to self-enforce.

**Architecture:**

```python
# Per-server env scope configuration
_SERVER_ENV_SCOPES: dict[str, frozenset[str]] = {
    "git-mcp": frozenset({"PATH", "HOME", "USER", "LANG", "GIT_*", "OMB_*"}),
    "github-mcp": frozenset({"PATH", "HOME", "USER", "LANG", "GITHUB_MCP_SCOPED_TOKEN", "GITHUB_SCOPED_TOKEN_REPO", "OMB_*"}),
    "verification-mcp": frozenset({"PATH", "HOME", "USER", "LANG", "OMB_*"}),
    "memory-mcp": frozenset({"PATH", "HOME", "USER", "LANG", "OMB_*"}),
    "artifact-mcp": frozenset({"PATH", "HOME", "USER", "LANG", "OMB_*"}),
}
```

The spawner builds each server's child env by intersecting the parent env with the server's scope. This is defense-in-depth: even if a server's code changes, the spawner enforces the boundary at the process level.

### Worker pool auto-scaling control loop (FC-P6-1)

**Context:** Phase 6 introduced the worker pool with manual scaling via `docker compose up --scale worker-wrapper=N`. FC-P6-1 automates this.

**Architecture:**

```
┌─────────────────────────────────────┐
│  orchestrator-adapter               │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ AutoscaleController          │   │
│  │                             │   │
│  │ - poll_interval: 30s       │   │
│  │ - up_threshold: 3 tasks    │   │
│  │ - down_threshold: 2 idle   │   │
│  │ - min_workers: 1           │   │
│  │ - max_workers: 5           │   │
│  │                             │   │
│  │ poll():                     │   │
│  │   queue_depth = count(QUEUED)│   │
│  │   idle_count = count(idle)  │   │
│  │   if queue > up_threshold:  │   │
│  │     scale_up()              │   │
│  │   elif idle > down_thresh:  │   │
│  │     scale_down()            │   │
│  └─────────────────────────────┘   │
│                                     │
│  scale_up():                        │
│    new_count = min(current+1, max)  │
│    docker compose up --scale ...    │
│    emit pool.scaled event           │
│                                     │
│  scale_down():                      │
│    wait for idle worker to drain    │
│    new_count = max(current-1, min)  │
│    docker compose up --scale ...    │
│    emit pool.scaled event           │
└─────────────────────────────────────┘
```

**Key design decisions:**

1. **Poll-based, not event-driven.** The controller polls the task registry at a fixed interval. Event-driven scaling (reacting to `task.queued` events) is more responsive but adds coupling between the event spine and Docker Compose CLI. Poll-based is simpler and adequate for single-operator scale.

2. **Docker Compose CLI, not Docker API.** The controller invokes `docker compose` as a subprocess rather than using the Docker Engine API directly. This is consistent with the platform's deployment model (Compose-native) and avoids a new dependency on `docker-py`.

3. **Scale-down drains first.** When scaling down, the controller identifies the most idle worker and waits for its active task to complete before removing it. No SIGKILL mid-task.

4. **Bounded by min/max.** `WORKER_AUTOSCALE_MIN` prevents scaling to zero (at least 1 worker always running). `WORKER_AUTOSCALE_MAX` caps resource usage.

5. **Disabled by default.** `WORKER_AUTOSCALE_ENABLED` defaults to `false`. Operators who prefer manual scaling are unaffected.

### Gemini structured output schema enforcement (FC-P6-2)

**Context:** The Gemini adapter (Phase 6) parses JSONL output from the Gemini CLI. FC-P6-2 adds optional schema validation to enforce structured output.

**Architecture:**

```python
# adapters/gemini_runner.py -- schema enforcement extension

class GeminiSchemaValidationError(Exception):
    """Raised when Gemini CLI output fails schema validation."""
    def __init__(self, errors: list[str], record_index: int):
        self.errors = errors
        self.record_index = record_index
        super().__init__(
            f"Schema validation failed at record {record_index}: "
            + "; ".join(errors)
        )

class GeminiRunner(RuntimeAdapter):
    def __init__(self, settings):
        self._schema = None
        if settings.gemini_output_schema:
            with open(settings.gemini_output_schema) as f:
                self._schema = json.load(f)

    def parse_output(self, raw: str) -> list[dict]:
        records = []
        for i, line in enumerate(raw.splitlines()):
            record = json.loads(line)
            if self._schema:
                errors = _validate_against_schema(record, self._schema)
                if errors:
                    raise GeminiSchemaValidationError(errors, i)
            records.append(record)
        return records
```

**Key design decisions:**

1. **Adapter-internal.** Schema validation is entirely within `GeminiRunner`. The `RuntimeAdapter` protocol does not change. Other adapters (Claude, Codex) are unaffected.

2. **Config-gated.** `GEMINI_OUTPUT_SCHEMA` env var points to a JSON Schema file. Absent the var, no validation occurs (backward compatible).

3. **Fail-fast on invalid output.** The first record that fails validation raises `GeminiSchemaValidationError` with the record index and validation details. The error is handled by the worker's error handling path (same as any runtime error).

4. **Uses `jsonschema` (if already in deps) or a lightweight validator.** If `jsonschema` is not already a dependency, a minimal validator (recursive type/required check) is implemented to avoid adding a new dependency without ADR.

### Per-epic wiring decisions

**Epic 41 -- API contract formalization.** ADR-0021 is the primary deliverable. The composite index migration is a standalone Alembic migration. State machine GATED closures are documentation-only updates to deferred-work.md.

**Epic 42 -- Operator configuration surface.** Config-gated GitHub write tools use the existing `settings` pattern (Pydantic BaseSettings). Scoped token authority model extends the existing `GITHUB_MCP_SCOPED_TOKEN` pattern. Input sanitization boundary is a documentation deliverable (runbook entry). Docker healthcheck hardening modifies the entrypoint script.

**Epic 43 -- Security defense-in-depth.** Per-server env scoping modifies `mcp_clients.py` to enforce allowlists at spawn time. WONTDO closures are documentation-only. Module resolution path fix is a test infrastructure change.

**Epic 44 -- Optional feature candidates.** Auto-scaling adds `AutoscaleController` to the orchestrator-adapter. Gemini schema enforcement extends `GeminiRunner`. Both are config-gated and disabled by default.

**Epic 45 -- Deferred work backlog closure.** Lock protocol TOCTOU acceptance is a documentation + runbook deliverable. Final audit is a verification pass over deferred-work.md.

### Forward-referenced ADRs (proposed; each gates its epic)

- **ADR-0021** -- API Versioning Strategy (additive-only within v1). **Gates Epic 41.** `docs/adr/0021-api-versioning-strategy.md`.

### Phase 8 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 41:** ADR-0021 accepted; composite index migration runs on both backends; downgrade path works; state machine GATED items updated in deferred-work.md.
- **Epic 42:** `GITHUB_TOOLS_LIVE_MODE` controls live/simulate; scoped token derives from config; input sanitization boundary documented; healthcheck removes `/tmp/ready` on start.
- **Epic 43:** Per-server env scoping negative tests pass for all MCP servers; WONTDO items have rationale; integration tests pass regardless of CWD.
- **Epic 44 (if shipped):** Auto-scaling emits `pool.scaled` events; scale-down drains worker; min/max bounds enforced. Gemini schema validation raises `GeminiSchemaValidationError` on invalid output; absent config = no validation.
- **Epic 45:** `grep -c 'GATED' deferred-work.md` returns 0; all items in terminal state.

### Acceptance checklist

- [ ] Architecture amendment (this section) accepted; P8-I2 invariant explicitly stated.
- [ ] ADR-0021 (`docs/adr/0021-api-versioning-strategy.md`) accepted -- API versioning strategy.
- [ ] `bmad-create-epics-and-stories` has decomposed the scope into Epics 41-45 stories.
- [ ] Each Phase 8 epic has its `phase: 8` label set in `sprint-status.yaml`.
- [ ] `deferred-work.md` has zero GATED items after Epic 45 completion.
- [ ] Phase 8 retrospective produced with final debt count.

-- *Amendment by R2d2, 2026-06-08, via the BMad `bmad-create-architecture` workflow (amendment mode).*
