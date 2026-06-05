# Data models

Two surfaces: (1) the event envelope and the typed payload catalog (`packages/events`), which is the platform's source of truth; (2) the SQLite materialization in `registry-state`, which is a derived projection over the event log. The log is authoritative; the DB is a query optimization.

## Event envelope

Every event flows through this immutable envelope (Pydantic `frozen=True, strict=True`):

| Field | Type | Notes |
|---|---|---|
| `event_id` | UUIDv7 string | from `events.new_uuid7()`; never `uuid.uuid4()`. |
| `schema_version` | string (semver) | per `(event_type, schema_version)` pair registered in `events.REGISTRY`. |
| `type` | string | the event-type identifier (e.g. `task.completed`). |
| `emitted_at` | datetime (UTC, aware) | from `events.FrozenClock`; never `datetime.utcnow()`. |
| `emitted_at_monotonic_ns` | int | from `time.monotonic_ns()` for ordering across clock skew. |
| `actor` | `Actor` | typed actor (Telegram chat id, console user, worker, etc.). |
| `payload` | `*Payload` | one of the typed payloads below. |
| `parent_event_id` | UUIDv7 string \| None | trace-context propagation. Never fabricated when absent. |
| `trace_id` | string (UUIDv7 or `tg:<update_id>`) | Required since schema 1.1.0 (Story 9.7 / FR57-FR59a). Accepts bare UUIDv7 or Telegram-derived `tg:<update_id>`. |

Once emitted, the envelope is **immutable** — not by the materializer, not by the bridge, not by sinks. Mutation invalidates the S-2 separability test (atomic visibility) and is rejected by static analysis + crash-injection fixtures.

## Event-type catalog (`packages/events/src/events/`)

Grouped by lifecycle area. Every entry is a `*Payload` Pydantic class with `frozen=True, strict=True` and tuple/frozenset collection fields.

### Task lifecycle
`TaskCreatedPayload`, `TaskPlanningStartedPayload`, `TaskPlanReadyPayload`, `TaskExecutionStartedPayload`, `TaskStepCompletedPayload`, `TaskBlockerRaisedPayload`, `TaskSummaryEmittedPayload`, `TaskApprovalRequestedPayload`, `TaskCompletedPayload`, `TaskExecutionResumedPayload`, `TaskStopRequestedPayload`, `TaskSelfRecoveredPayload`, `TaskRetryRequestedPayload`, `TaskLicenseFlaggedPayload`

### Decisions & approvals
`ApprovalGrantedPayload`, `ApprovalRejectedPayload`

### Sessions
`SessionStartedPayload`, `SessionHeartbeatPayload`, `SessionFinishedPayload`, `SessionHeartbeatTimeoutPayload`, `SessionReconnectingPayload`

### Operators & budget
`LicenseOverridePayload`, `BudgetOverridePayload`, `TaskBudgetExceededPayload`

### Failures & monitoring
`ServiceCrashedPayload`, `SinkDeliveryFailedPayload`, `TelegramRejectedPayload`

### Security & audit
`SecretAccessedPayload`, `Tier3ActionAttemptedPayload`, `Tier3ActionPerformedPayload`

### Agent / worker
`AgentReasoningBreadcrumbPayload`, `FileEditedPayload`

### Security & signing (Phase 2 — Epic 10/11)
`TaskApprovalSignedPayload`, `KeyRotatedPayload`, `CapabilityDeniedPayload`, `ApprovalInboxOpenedPayload`

### Replication monitoring (Phase 2 — Epic 13)
`ReplicationLaggingPayload` — emitted by `scripts/check_replication_lag.py` when the litestream sidecar stalls (`replication.lagging`, NFR-R7). Born at 1.0.0 + 1.1.0 (two-location registration in `packages/events/src/events/types/replication.py`).

### Deployment security (Phase 2 — Epic 8)
`DeploymentSignatureRejectedPayload` — emitted when `just verify-images` fails a cosign check (`deployment.signature_rejected`, FR56a, NFR-S9). Born at 1.0.0 + 1.1.0 (two-location registration in `packages/events/src/events/types/deployment.py`).

### Budget enforcement (Phase 2 — Epic 12)
`TaskBudgetEnforcementTriggeredPayload` — the ACTION-RECORD event emitted after SIGTERM of the Claude Code subprocess (`task.budget_enforcement_triggered`, FR67). `BudgetOverridePayload` — emitted alongside `approval.granted` when the operator overrides a budget block (`tier3.budget_override` / `budget.override` alias, FR68). Both registered at 1.0.0 + 1.1.0 (two-location).

### Git mutations (Phase 3 — Epic 15)
`GitCommittedPayload` (`git.committed`), `GitPushedPayload` (`git.pushed`) — emitted by git-mcp after Tier-2 commit / Tier-3 push. Born at 1.1.0 (no v1.0.0 predecessor; two-location registration).

Planned Phase 3 read/query events (not yet registered; born 1.1.0, two-location):
`git.status_queried`, `git.diff_queried`, `git.log_queried`, `git.branch_created`, `git.commit_created`, `git.push_completed`, `git.history_rewritten`.

### GitHub writes (Phase 3 — Epic 16)
`GithubIssueCreatedPayload` (`github.issue.created`), `GithubIssueUpdatedPayload` (`github.issue.updated`), `GithubPrCreatedPayload` (`github.pr.created`), `GithubPrUpdatedPayload` (`github.pr.updated`), `GithubReviewRequestedPayload` (`github.review.requested`), `GithubCommentCreatedPayload` (`github.comment.created`) — emitted by github-mcp after Tier-3 writes gated by `approval.granted`. Born at 1.1.0 (no v1.0.0 predecessor; two-location registration). Default `simulate=True` in Phase 1 (no scoped credential).

Planned Phase 3 addition (not yet registered):
`github.review.submitted` — emitted after a Tier-3 review submission.

### Verification (Phase 3 — Epic 17)
`VerificationCompletedPayload` (`verification.completed`, FR74) — emitted by verification-mcp after a Tier-2 `verification.run_build` / `verification.run_tests` recipe finishes. Payload carries `tool`, `recipe`, `passed`, `exit_code`, `coverage` — **never** logs or secrets. Born at 1.1.0 (no v1.0.0 predecessor; two-location registration).

### Memory (Phase 3 — Epic 18)
`MemoryWrittenPayload` (`memory.written`, ADR-0012) — emitted by memory-mcp after a Tier-2 `memory.write` upsert. Payload carries `key`, `title`, `body_bytes` — **never** the body content per ADR-0012 section 5. Born at 1.1.0 (no v1.0.0 predecessor; two-location registration).

### Artifacts (Phase 3 — Epic 19)
`ArtifactStoredPayload` (`artifact.stored`), `ArtifactDeletedPayload` (`artifact.deleted`, ADR-0011) — emitted by artifact-mcp. Metadata-only payloads: `hash`, `name`, `size_bytes`, `reason` — **never** the artifact content/bytes. Born at 1.1.0 (no v1.0.0 predecessor; two-location registration).

### Adding a new event type
1. Add the `*Payload` class in `packages/events/src/events/` with `frozen=True, strict=True`.
2. Register the `(event_type, schema_version)` pair in `events.REGISTRY` via `register(...)`.
3. Ship the migrator path additively (see [schema-evolution.md](./schema-evolution.md)).
4. Add a forward-compatibility fixture to `tests/contract/fixtures/` so consumers at vN can read vN+1 without corrupting known fields.
5. Update Telegram template (if user-visible) per [message-design.md](./message-design.md).

## registry-state DB schema

SQLite + WAL only in Phase 1. Connection URL fixed by `REGISTRY_STATE_DB_URL`. Migrations are date-prefixed (`YYYY-MM-DD_NNNN_<desc>.py`) under `services/registry-state/src/registry_state/migrations/versions/`.

SQLAlchemy 2.0 typed style: `DeclarativeBase`, `Mapped[T]`, `mapped_column(...)`. All sessions are `AsyncSession(expire_on_commit=False)`; the default raises `MissingGreenlet` after commit in async context. All relationships declare `lazy="raise"` and eager-load via `selectinload(...)`. Column / constraint operations require `with op.batch_alter_table(...)` (bare `op.add_column` / `op.drop_column` raises `OperationalError` on SQLite).

| Table | Purpose |
|---|---|
| `task` | Persistent task entity. Carries `status`, `actor`, Telegram thread binding (Story 3.9). |
| `session` | Session row (renamed from `Session` to avoid the SQLAlchemy clash). |
| `event` | Event-log rows materialized from JSONL into SQLite for query. The JSONL log remains authoritative. |
| `idempotency_cache` | Dedup cache for the FR28 7-day retention. TTL-swept; UUIDv7-keyed. |
| `snapshot` | Snapshot capture for recovery (Story 2.6). Bounded replay time (NFR-P3). |

### Migration rules

- **Additive within a major.** `DROP COLUMN`, `DROP TABLE`, `ALTER COLUMN (type change)`, `RENAME`, `ADD COLUMN NOT NULL` without `DEFAULT` are rejected by the migrator linter (see [testing-guide.md](./testing-guide.md)).
- Two-phase destructive plans require an ADR.
- `migrate(v_n) == migrate(migrate(v_n))` (idempotent migration) is asserted per version step.
- `alembic downgrade -1` smoke test runs in CI against a known schema state.
- A connection-open test asserts `PRAGMA journal_mode = wal` — one-liner that catches env misconfig before it becomes data loss.

### Single-writer invariant (FR26)

Only `registry-state` opens the DB for writes. No other service declares SQLAlchemy or holds an `AsyncSession`. A static-analysis test asserts only `registry-state` writer modules contain `session.add` / `session.execute(...write...)` / `commit()` call-sites. The append-only JSONL log is opened for write only by the `EventLogWriter` class in `registry-state`; everyone else reads via `EventLogReader`.

## Snapshot & replay contract

Recovery on restart replays the JSONL event log from the most recent snapshot. On SIGTERM, `registry-state` runs `PRAGMA wal_checkpoint(FULL)` then `await engine.dispose()` (8s budget). Snapshot materialization is the **only** path allowed to hold a write transaction >1s.

The replay contract is asserted under `tests/replay/`: a frozen event-log fixture replayed through the projector produces a byte-identical state snapshot. Mandatory on every projector or event-handler change.

## Idempotency

The triggering event's UUIDv7 is threaded as the `idempotency_key` into every downstream write. Re-driving the same event must produce identical outcome with zero duplicate side-effects. The 7-day cache lives in `packages/idempotency` (`IdempotencyCacheStore`) and is owned by `registry-state`; queries flow through it via `CacheHit` / `IdempotencyConflict`.

Test contract: every command handler has a `tests/idempotency/` test driving the same `(idempotency_key, payload)` twice and asserting exactly one side-effect AND identical response.

## Cross-references

- [api-contracts.md](./api-contracts.md) — HTTP endpoints + MCP tools (which events each tool emits).
- [schema-evolution.md](./schema-evolution.md) — full workflow for adding event types and shipping migrators.
- [exceptions.md](./exceptions.md) — naming exceptions (e.g. `SessionRow` vs `session` table).
- [`_bmad-output/project-context.md`](../_bmad-output/project-context.md) Cat 2 — Pydantic v2 rules; Cat 3 — SQLAlchemy / Alembic rules; Cat 4 — replay / migrator tests.
