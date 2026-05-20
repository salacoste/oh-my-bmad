# Story 11.1 — HMAC signing inside `/v1/tasks/<id>/decisions` handler

Status: **review** (CI pending @ 37cbdfa)

## Story

**As** the platform operator (and future forensic auditor)
**I want** every `approval.granted` event to be accompanied by a `task.approval_signed` sibling event carrying an HMAC-SHA256 signature computed over `(task_id || action || timestamp || actor_id)` using an operator-local `OPERATOR_HMAC_KEY`
**so that** approvals are non-repudiable — the operator can offline-verify any historical approval forever via `just verify-approval <event-id>` (FR65 — Story 11.4), and the signing key never leaves the operator's host (NFR-S10 — key isolation).

Story 11.1 wires HMAC signing into the existing `/v1/tasks/<id>/decisions` `approve` handler at `services/registry-api/src/registry_api/routes/decisions.py:454`. The handler today emits `approval.granted` via `EventLogWriter`; Story 11.1 adds a paired `task.approval_signed` emission. Per Epic 11's natural sequence + retro recommendation, Story 11.1 registers the `task.approval_signed` event type **minimally** (D1 below); Story 11.2 refines with full Pydantic constraints + contract fixtures.

## Acceptance criteria

### AC1 — `OPERATOR_HMAC_KEY` loaded from `.env` via pydantic-settings

Extend the existing registry-api settings class (or add a dedicated `ApprovalSigningSettings` if no shared settings exists — verify before deciding) with:

```python
operator_hmac_key: SecretStr | None = Field(default=None, description="...")
```

Constraints:
- Use `pydantic.SecretStr` (NOT `str`) so the key is masked in logs / `repr()` / `model_dump()` by default (NFR-S10 — never appears in events/logs/snapshots).
- Field is `None`-default to allow the service to start without the key — Story 11.1 emits a structured `log.warning("approval_signing_disabled_missing_hmac_key", ...)` and skips signing rather than crashing. Operators get a clear signal to set the key.
- Env-var name: `OPERATOR_HMAC_KEY` (matches FR64 wording — NOT prefixed with `REGISTRY_API_` because the key is operator-property, not registry-api property).
- Length minimum: enforced via `Field(min_length=32)` constraint when the value is set (32 bytes / 256 bits is the HMAC-SHA256 minimum for a non-trivial keyspace). Document recommendation: 64-character hex string from `openssl rand -hex 32`.

Self-verification:
- `grep -F "operator_hmac_key" services/registry-api/src/registry_api/` returns the new field.
- Test `test_settings_operator_hmac_key_missing_logs_warning_does_not_crash` — construct settings without env var, assert no exception + warning emitted via `structlog.testing.capture_logs()`.
- Test `test_settings_operator_hmac_key_too_short_rejected` — set `OPERATOR_HMAC_KEY="short"`, assert validation error.
- Test `test_settings_operator_hmac_key_masked_in_repr` — set key to known value, call `repr(settings)`, assert key text does NOT appear (Pydantic `SecretStr` default behavior).

### AC2 — `task.approval_signed` event type registered (minimal — Story 11.2 refines)

Story 11.1 adds the minimal registration needed for Story 11.1's emission to pass the `check_event_registry.py` CI gate. Story 11.2 will refine the payload class + add contract fixtures + bump to schema_version `1.1.0`.

In `services/registry-state/src/registry_state/domain/event_types.py` (the canonical registration site per Story 3.5.2 refactor):

```python
register("task.approval_signed", "1.0.0", TaskApprovalSignedPayload)
```

And in `packages/events/src/events/payloads.py`, add a **minimal** payload model:

```python
class TaskApprovalSignedPayload(BaseModel):
    """Sibling event emitted alongside `approval.granted` carrying HMAC signature.

    Minimal Story 11.1 surface. Story 11.2 refines:
    - bumps to schema_version 1.1.0
    - tightens hmac_sha256 to 64-char hex Field constraint
    - adds contract-fixture forward-compat pair
    - sets `frozen=True, strict=True` model_config

    See ADR-0006 (to be drafted in Story 11.5) for signing/verification protocol.
    """
    model_config = ConfigDict(frozen=True, strict=True)

    task_id: str
    decision_id: str
    actor_id: str
    action: Literal["approve"]
    timestamp: datetime
    hmac_sha256: str  # Story 11.2: tighten to Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
```

Constraints:
- **DO NOT** register at schema_version `1.1.0` — that's explicitly Story 11.2's scope. Story 11.1 ships at `1.0.0` so 11.2's bump remains additive.
- Re-export from `packages/events/src/events/__init__.py` via `__all__` for consumer access (Story 11.4's `just verify-approval` recipe will import).

Self-verification:
- `uv run python -c "from events.payloads import TaskApprovalSignedPayload; print(TaskApprovalSignedPayload.__name__)"` succeeds.
- `uv run python scripts/check_event_registry.py` exits 0 — `task.approval_signed` recognized as registered.

### AC3 — HMAC computation function

New module `services/registry-api/src/registry_api/adapters/approval_signing.py`:

```python
import hmac
import hashlib
from datetime import datetime
from pydantic import SecretStr

def compute_approval_hmac(
    *,
    key: SecretStr,
    task_id: str,
    action: Literal["approve"],
    timestamp: datetime,
    actor_id: str,
) -> str:
    """Compute HMAC-SHA256 over the canonical signing payload.

    Canonical signing string: f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}"
    (pipe-delimited; ISO-8601 UTC timestamp; deterministic ordering per FR64).

    Returns: 64-character lowercase hex string.
    """
    canonical = f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}".encode()
    return hmac.new(
        key=key.get_secret_value().encode("utf-8"),
        msg=canonical,
        digestmod=hashlib.sha256,
    ).hexdigest()
```

Constraints:
- **Pure function** — no logging, no I/O, no side effects. Story 11.4's `just verify-approval` will re-import this same function for offline verification (D3 below).
- **Canonical delimiter `|`** chosen because none of the input fields can contain `|` (task_id is UUID-shaped, action is enum, timestamp is ISO-8601, actor_id is allowlist-validated). If future actor IDs gain pipes, this needs revisit — document in docstring.
- Returns hex (NOT base64) because hex is operator-readable in events / logs / `just verify-approval` output. 64 hex chars = 32 bytes = HMAC-SHA256 output size.

Self-verification:
- Unit test `test_compute_approval_hmac_reproducible` — same inputs → same hex output across calls.
- Unit test `test_compute_approval_hmac_known_vector` — assert against an RFC-4231-style test vector (manually computed or via Python `hmac` standalone) to detect future cryptographic library drift.
- Unit test `test_compute_approval_hmac_actor_id_change_changes_hmac` — changing any single input changes output (avalanche check).

### AC4 — `/v1/tasks/<id>/decisions` handler emits paired `task.approval_signed`

At `services/registry-api/src/registry_api/routes/decisions.py:454` (`_build_event` function), the current code returns `("approval.granted", ApprovalGrantedPayload(...))` for the `approve` action. Story 11.1 extends:

```python
# Schematic — final design via implementation
if body.action == "approve":
    timestamp = clock.now()  # or whatever the existing pattern is
    approval_payload = ApprovalGrantedPayload(
        task_id=task_id, decision_id=decision_id, actor_id=actor_id, override=body.override,
    )
    yield ("approval.granted", approval_payload)

    if settings.operator_hmac_key is not None:
        signed_payload = TaskApprovalSignedPayload(
            task_id=task_id,
            decision_id=decision_id,
            actor_id=actor_id,
            action="approve",
            timestamp=timestamp,
            hmac_sha256=compute_approval_hmac(
                key=settings.operator_hmac_key,
                task_id=task_id,
                action="approve",
                timestamp=timestamp,
                actor_id=actor_id,
            ),
        )
        yield ("task.approval_signed", signed_payload)
    else:
        log.warning(
            "approval_signing_disabled_missing_hmac_key",
            task_id=task_id, decision_id=decision_id,
        )
```

Constraints:
- **Timestamp source-of-truth**: use the SAME timestamp for both `approval.granted.emitted_at` AND the HMAC signing input. Avoids `now()` drift between sibling event emissions. This requires either (a) inverting the current emission flow to compute timestamp BEFORE calling `_build_event`, OR (b) passing the timestamp into `_build_event` as a parameter. Choose (b) — smaller blast radius.
- **Both events share the same `trace_id`** so the sibling pair is operator-correlatable (Story 9.6 trace_id propagation kernel guarantees this when emitted within the same handler scope).
- **Ordering**: `approval.granted` MUST be appended to the event log BEFORE `task.approval_signed`. This matters for `materializer` ordering AND for `just verify-approval` (Story 11.4) which expects to find the signed event AFTER the granted event in the log. Implementation: two separate `EventLogWriter.append()` calls in order.
- **Missing-key behavior**: when `operator_hmac_key is None`, the handler MUST still successfully approve (emit `approval.granted`); it just SKIPS the signed emission and logs a structured warning. NFR-S10 isolation is preserved (no error reveals key state to clients).
- **`override` payload field**: NOT included in the HMAC signing input. Per FR64 wording: "computed over `(task_id || action || timestamp || actor_id)`" — `override` is a separate boolean and is NOT part of the canonical signing string. Document this decision in `compute_approval_hmac` docstring.

Self-verification:
- Integration test `test_approve_handler_emits_paired_signed_event` — POST to `/v1/tasks/<id>/decisions` with action=approve, OPERATOR_HMAC_KEY set; assert event log contains BOTH `approval.granted` AND `task.approval_signed` events with same `task_id`, same `trace_id`, same `timestamp`, and HMAC value matches manual recomputation.
- Integration test `test_approve_handler_skips_signed_event_when_no_key` — same POST without env var; assert `approval.granted` is emitted, `task.approval_signed` is NOT, and `approval_signing_disabled_missing_hmac_key` warning logged.
- Integration test `test_approve_handler_reject_action_does_not_emit_signed_event` — POST with action=reject; assert NO `task.approval_signed` emitted (FR64 says approve-only).

### AC5 — `OPERATOR_HMAC_KEY` never appears in events, logs, snapshots, or registry

NFR-S10 isolation requirement. The key MUST NOT leak through:
- Event payloads (only the HMAC value lands in events, never the key itself)
- Structured logs (Pydantic `SecretStr` default behavior masks; verify in tests)
- Database (registry-state never stores the key — only events that reference the HMAC value)
- Snapshots / replays
- `/healthz` / `/metrics` endpoints
- Error responses (RFC-7807 problem+json format)
- Stack traces (Python's default exception formatting respects `SecretStr.__str__` masking)

Self-verification:
- Test `test_hmac_key_isolation_no_leak_in_events` — set `OPERATOR_HMAC_KEY="known-test-key-12345-abc..."`, emit 10 approval events, `grep` the JSONL log for the key string, assert ZERO matches.
- Test `test_hmac_key_isolation_no_leak_in_logs` — same setup with `structlog.testing.capture_logs()`, assert no log entry contains the key string.
- Test `test_hmac_key_isolation_no_leak_in_settings_repr` — `repr(settings)` does NOT contain the key text.
- **Story 11.5 will add `tests/integration/test_hmac_key_isolation.py`** as the canonical CI gate (Epic 11 acceptance gate item); Story 11.1 ships the per-unit assertions.

### AC6 — Structured logging discipline

When signing happens / is skipped, emit structured events under standard schema:

```python
# Signing successful:
log.info("approval_signed",
    task_id=task_id, decision_id=decision_id, actor_id=actor_id,
    hmac_sha256_prefix=hmac_value[:8],  # first 8 hex chars = 32 bits of entropy — enough for correlation, useless for forgery
)

# Signing skipped due to missing key:
log.warning("approval_signing_disabled_missing_hmac_key",
    task_id=task_id, decision_id=decision_id,
)
```

Constraints:
- **NEVER log the full HMAC value at INFO** — even though HMAC is not the key, logging full 64-char hex bloats operator logs. 8-char prefix is sufficient for grep correlation against events.
- Use existing structlog logger (no new logger instance — follow registry-api convention).

Self-verification:
- Test `test_approval_signed_log_event_emitted_with_8char_prefix` asserts the prefix length + truncation.

### AC7 — Mypy --strict baseline extension

New module `services/registry-api/.../adapters/approval_signing.py` + minor edits in `routes/decisions.py` + new payload class in `packages/events/`. Expected: **126 → ~128** source files.

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the new count and exit 0.

### AC8 — Validation gates

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run python scripts/check_imports.py` — exit 0 (no `services/*→services/*` imports)
- `uv run python scripts/check_event_registry.py` — exit 0 (`task.approval_signed` recognized)
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run python scripts/check_secrets.py` (or equivalent — verify pre-existing) — exit 0
- `uv run pytest -x -q services/registry-api packages/events` — all green
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14/14 imports)

---

## Developer context

### Existing state (post Epic 10)

- **Epic 10 done** — metrics-subscriber service shipped; no Epic 10 surface touched by Story 11.1.
- **Epic 9 done** — `trace_id` propagation kernel; both sibling events (`approval.granted` + `task.approval_signed`) emitted in the same handler scope share `trace_id`.
- **`services/registry-api/src/registry_api/routes/decisions.py`**: existing handler at line 454 `_build_event` returns `(event_type, payload)` tuple. **MUST refactor to return iterable of tuples** (or invert flow) to emit 2 events for the `approve` action.
- **`ApprovalGrantedPayload`** registered at schema_version `1.0.0` and `1.1.0` (line 242-243 of `services/registry-state/src/registry_state/domain/event_types.py`); Story 11.1 does NOT modify this.
- **`EventLogWriter`** is the canonical append API; registry-api uses it directly (NOT via clawhip-bridge MCP — see `services/registry-api/src/registry_api/app.py` lifespan).
- **No `OPERATOR_HMAC_KEY` currently in `.env.example`** — Story 11.1 adds the line + documentation comment.
- **Mypy `--strict` baseline:** 126 source files post Epic 10.

### Architecture compliance

- **FR64** — HMAC signing of `approval.granted` events with operator-local key.
- **NFR-S10** — key isolation: never persisted in events/logs/snapshots/registry. AC5 + Story 11.5's CI gate enforces.
- **Single-writer rule (FR26)** — registry-state remains sole writer of state; Story 11.1 only emits events via `EventLogWriter`, doesn't write to SQLite.
- **P2-I1 / P2-I3** — derived projection (Epic 10 boundary) unaffected; HMAC events flow into the event log naturally.
- **ADR-0006** — to be drafted in Story 11.5 (key rotation + verification protocol). Story 11.1 cites it as future reference.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `pydantic` | already pinned (≥2.5) | `SecretStr` for HMAC key field masking. |
| `pydantic-settings` | already pinned | Existing pattern for `.env` loading. |
| Python `hmac` + `hashlib` | stdlib | NO third-party crypto deps. HMAC-SHA256 is stdlib. |
| `events` workspace package | already wired | `EventEnvelope`, payload models. |
| no new deps | — | Story 11.1 introduces ZERO new third-party dependencies. |

### File-structure requirements

```
services/registry-api/src/registry_api/
├── adapters/
│   └── approval_signing.py            # NEW: compute_approval_hmac() pure function
├── routes/
│   └── decisions.py                   # MODIFY: _build_event extended to emit paired signed event
└── settings.py (or app/config.py)     # MODIFY: add operator_hmac_key field

packages/events/src/events/
├── payloads.py                        # MODIFY: add TaskApprovalSignedPayload (minimal — 11.2 refines)
└── __init__.py                        # MODIFY: re-export TaskApprovalSignedPayload

services/registry-state/src/registry_state/domain/event_types.py   # MODIFY: register("task.approval_signed", "1.0.0", ...)

.env.example                           # MODIFY: add OPERATOR_HMAC_KEY=<64-char hex> with docs comment

services/registry-api/src/registry_api/
├── test_approval_signing.py           # NEW: unit tests for compute_approval_hmac
├── test_decisions_signing.py          # NEW: integration tests for handler emission
```

### Testing requirements

- **Pyramid:**
  - Unit tests for `compute_approval_hmac` (synchronous, no FastAPI overhead)
  - Integration tests for handler via `httpx.AsyncClient` (lifespan exercise — mirror existing `test_decisions.py` pattern)
  - Settings tests for `OPERATOR_HMAC_KEY` field via `pydantic_settings` direct instantiation
- **Test isolation:** each test constructs its own settings (no module-global key); use `monkeypatch.setenv` for env-var control.
- **HMAC test vector:** include at least ONE manually-computed RFC-4231-style test vector to detect future crypto-library drift.
- **NFR-S10 isolation:** AC5 self-verification tests prove key never leaks. Story 11.5 will add the canonical CI gate `tests/integration/test_hmac_key_isolation.py`; Story 11.1 ships unit-level assertions.

### Previous-story intelligence

#### From Epic 10 retro (just closed)

- **AI-2 — Spec values MUST cite canonical source.** Story 11.1 cites `services/registry-api/src/registry_api/routes/decisions.py:454` for the integration point; cites `events.envelope.ActorKind` if any actor-kind validation is added (none in Story 11.1 — `actor_id` is opaque string).
- **AI-3 — Decisions block mandatory pre-implementation.** Story 11.1 resolves D1-D5 below.
- **AI-4 — No hardcoded dates.** Tests use `datetime.now(UTC)` or explicit fixture dates passed via parameters.

#### Lessons from Epic 9 retro

- **AG-1: numbered-phase executor briefs** — Story 11.1's implementation has clear phases (settings + payload registration + HMAC fn + handler wiring + tests).
- **AG-2: empirical "no X anywhere" claims** — AC5 isolation tests use full-tree `grep` to assert key absence.

### Trade-off notes

- **Story 11.1 registers `task.approval_signed` at schema_version `1.0.0`; Story 11.2 bumps to `1.1.0`.** Could have deferred ALL registration to Story 11.2 + used `# noqa: EVT001` in Story 11.1 — rejected because that'd leave the event-registry CI gate unhappy. Minimal registration in 11.1 + refinement in 11.2 is the cleanest sequence. Per retro DD5 opportunity, Story 11.2 also bundles `capability.denied` registration.
- **`OPERATOR_HMAC_KEY=None` → skip signing + warn (NOT crash).** Operators may deploy without the key (initial setup, testing). The handler MUST still approve — just without the signed sibling. This is a deliberate safety trade-off vs. "fail-closed". Rationale: approvals are operational primitives; signing is audit/forensic. Better to ship approvals unsigned than to break the approval path on missing-key.
- **Canonical signing string uses `|` delimiter, NOT JSON.** Stable across Python versions, language-agnostic for future verification tools, no field-ordering ambiguity. JSON would require canonical-JSON serialization which adds complexity.
- **`override` field excluded from HMAC input** per FR64 wording. Document explicitly to prevent future "let's add override to the HMAC" drift.
- **Story 11.1 emits HMAC as separate event (NOT extending `ApprovalGrantedPayload`).** Rejected `ApprovalGrantedPayload.hmac_sha256` field — would require schema_version bump on existing event type + churn all materializers. Sibling-event pattern is cleaner.

### Non-goals (do NOT do in 11.1)

- **`schema_version="1.1.0"` for `task.approval_signed`** → Story 11.2 scope (with full Pydantic constraints + contract fixtures).
- **`key.rotated` event type** → Story 11.2 scope (registers) + Story 11.5 scope (emits).
- **`/approvals` Telegram command** → Story 11.3 scope.
- **`just verify-approval` recipe** → Story 11.4 scope.
- **Key rotation flow** → Story 11.5 scope.
- **ADR-0006 authoring** → Story 11.5 scope (the rotation flow finalizes the design).
- **`tests/integration/test_hmac_key_isolation.py`** canonical CI gate → Story 11.5 / Epic 11 acceptance scope.
- **`capability.denied` event registration (DD5 from Epic 10 retro)** → Story 11.2 opportunity (per retro recommendation).
- **Rejection signing** — only `approve` action is signed per FR64. `reject` / `stop` / `retry` are NOT signed.

## Out-of-scope risk flags

- **`OPERATOR_HMAC_KEY` rotation handling**: Story 11.1 reads the key ONCE at startup via settings. Hot-reload is NOT supported. If operator changes `.env` mid-run, signing continues with the OLD key until restart. Story 11.5 formalizes the rotation flow with `key.rotated` event emission. Document in `app/main.py` lifespan startup comment.
- **HMAC timing-attack resistance**: `hmac.compare_digest` is required for verification (Story 11.4's `just verify-approval` scope). Story 11.1 only PRODUCES HMACs (no comparison), so timing-attack surface is zero here. Story 11.4 will use `compare_digest` for verification — flagged for future story author.
- **Pydantic `SecretStr` introspection**: `SecretStr.get_secret_value()` returns the underlying str. ANY future code path that calls this MUST be reviewed for log/event leak. Story 11.1's only call site is inside `compute_approval_hmac` which is pure-function — never logs.
- **Event-ordering invariant**: `approval.granted` MUST be appended BEFORE `task.approval_signed`. If `EventLogWriter.append` is async + the handler `await`s both, ensure no interleaving. Document the ordering in the handler's docstring.
- **`actor_id` allowlist validation**: `actor_id` is canonical-form-checked by existing middleware before reaching `_build_event`. The HMAC computation receives the validated value. If a future middleware change weakens validation, HMAC inputs become attacker-controllable — flag for Story 11.5 ADR-0006.

## Decisions (resolved before implementation)

- **D1 — Story 11.1 registers `task.approval_signed` minimally at schema_version `1.0.0`; Story 11.2 refines.** Rejected `# noqa: EVT001` defer because event-registry CI gate would fail. Per Epic 11 natural sequence + retro recommendation.
- **D2 — `OPERATOR_HMAC_KEY=None` → skip signing + log warning (NOT crash).** Safety trade-off documented above.
- **D3 — `compute_approval_hmac` is a pure function in `adapters/approval_signing.py`.** Story 11.4's `just verify-approval` recipe re-imports the same function for offline verification — single source of truth.
- **D4 — Canonical signing string: `f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}"`.** Pipe-delimited, ISO-8601 UTC timestamp, deterministic ordering. No JSON, no `override` field.
- **D5 — HMAC value logged at INFO with 8-char prefix only.** Bloat avoidance; correlation sufficient at 32 bits of entropy.

## Definition of done

- All 8 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `11-1-hmac-signing-decisions-handler: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- `OPERATOR_HMAC_KEY` documented in `.env.example` with 64-char hex example + `openssl rand -hex 32` instruction.
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, surprises/deviations).
- No regressions in: `tests/separability/`, `tests/integration/`, full pytest suite.
- HMAC computation tested against ≥ 1 manually-computed test vector.

---

## Frontmatter

```yaml
---
story_id: 11.1
story_key: 11-1-hmac-signing-decisions-handler
parent_epic: 11
phase: 2
fr_refs: [FR64]
nfr_refs: [NFR-S10]
arch_refs:
  - "Single-writer rule (FR26) — registry-state remains sole state writer"
  - "Trace-id propagation kernel (Epic 9) — paired events share trace_id"
  - "ADR-0006 (to be drafted in Story 11.5) — HMAC signing/verification protocol"
estimated_hours: 3-5
priority: high (Epic 11 critical-path foundation — Stories 11.2-11.5 build on this)
blocks:
  - 11.2 (refines TaskApprovalSignedPayload + registers key.rotated)
  - 11.3 (Telegram /approvals — uses approval events stream)
  - 11.4 (just verify-approval — re-imports compute_approval_hmac)
  - 11.5 (key rotation flow + ADR-0006)
blocked_by:
  - Epic 9 done (trace_id kernel — sibling events share trace_id)
  - Epic 10 done (no actual dependency, but Epic 11 starts after Epic 10 per project sequence)
status: review
created: 2026-05-20
created_by: bmad-create-story skill
---
```

## Tasks/Subtasks

- [x] **AC1** — `OPERATOR_HMAC_KEY` loaded via `pydantic-settings` `SecretStr` field. NEW module `services/registry-api/src/registry_api/settings.py` exposes `ApprovalSigningSettings` (D2 decision — see DAR rationale). `.env.example` documents `openssl rand -hex 32` recipe + NFR-S10 isolation contract. Field validator enforces `min_length=32` when key is set; `None` is permitted (handler logs warning + skips signing).
- [x] **AC2** — `TaskApprovalSignedPayload` added to `packages/events/src/events/payloads.py` (frozen + strict + extra="forbid") with fields `task_id: str`, `decision_id: str`, `actor_id: str`, `action: Literal["approve"]`, `timestamp: AwareDatetime`, `hmac_sha256: str`. Registered at `schema_version="1.0.0"` in `services/registry-state/src/registry_state/domain/event_types.py::ensure_registered()`. Re-exported through both packages' `__all__` blocks.
- [x] **AC3** — `compute_approval_hmac` pure function in `services/registry-api/src/registry_api/adapters/approval_signing.py`. Canonical signing string `f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}"` per FR64 / D4. Returns 64-char lowercase hex digest of HMAC-SHA256. No logging, no I/O. Story 11.4 `just verify-approval` will re-import this function (single source of truth per D3).
- [x] **AC4** — `routes/decisions.py::_build_event` extended to return `(primary_pair, signed_pair_or_none)`. Handler appends `approval.granted` first, then `task.approval_signed` sibling sharing the SAME `decided_at` + `trace_id` + `parent_event_id` reference. Only `action="approve"` produces a signed sibling; reject/stop/retry never sign (per FR64 wording).
- [x] **AC5** — NFR-S10 isolation enforced through three vectors: (a) Pydantic `SecretStr` default masking in `repr()` / `model_dump()` / `model_dump_json()` (3 unit tests); (b) HMAC hex output is structurally separable from key material (1 unit test); (c) `get_secret_value()` called EXACTLY ONCE inside the pure HMAC function (verified by inspection — only call site in the diff). Story 11.5 will add the canonical full-tree `tests/integration/test_hmac_key_isolation.py` CI gate.
- [x] **AC6** — Structured INFO log emitted post-append: `approval_signed task_id=<...> decision_id=<...> actor_id=<...> hmac_sha256_prefix=<first-8-chars>` (D5 — 32 bits of entropy sufficient for operator correlation; full HMAC stays in the event payload). Missing-key path logs `approval_signing_disabled_missing_hmac_key` at WARNING (D2 safety trade-off).
- [x] **AC7** — Mypy `--strict` baseline extended from 126 → 130 source files (settings.py + approval_signing.py + test_approval_signing.py + test_decisions_signing.py — all 4 new source files clean under `--strict`). Existing 126 files unchanged.
- [x] **AC8** — All validation gates green locally:
  - `uv run pytest -q services/registry-api packages/events services/registry-state` → **941 passed** (baseline 921, +20 from Story 11.1's 13 unit + 7 integration tests).
  - `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` → **Success: no issues found in 130 source files**.
  - `uv run python scripts/check_imports.py` → exit 0.
  - `uv run python scripts/check_event_registry.py` → exit 0.
  - `uv run python scripts/check_single_writer.py` → exit 0.
  - `git ls-files -z | xargs -0 uv run secret-hygiene-precommit` → exit 0.

## Dev Agent Record

### Implementation summary

4 new modules + 4 modified files closing Story 11.1's 8 ACs:

1. **`services/registry-api/src/registry_api/settings.py`** (NEW, 116 lines) — `ApprovalSigningSettings` `BaseSettings` subclass. First `pydantic-settings` consumer in `registry-api` (per D2 decision — see surprises below).
2. **`services/registry-api/src/registry_api/adapters/approval_signing.py`** (NEW, 94 lines) — pure function `compute_approval_hmac`. Story 11.4 re-imports this for `just verify-approval` (single source of truth per D3).
3. **`services/registry-api/src/registry_api/test_approval_signing.py`** (NEW, 296 lines) — 13 unit tests covering settings (4), HMAC fn (6 — incl. RFC-4231-style known-vector check), key isolation (3 — NFR-S10 self-verification).
4. **`services/registry-api/src/registry_api/test_decisions_signing.py`** (NEW, 7 integration tests) — handler-level assertions: paired-event emission, ordering invariant, missing-key skip path, prefix-only log assertion, NFR-S10 isolation in event log + DB.
5. **`packages/events/src/events/payloads.py`** (MODIFIED, +30 lines) — `TaskApprovalSignedPayload` minimal model.
6. **`services/registry-state/src/registry_state/domain/event_types.py`** (MODIFIED, +7 lines) — registers `task.approval_signed` at `schema_version="1.0.0"`.
7. **`services/registry-api/src/registry_api/routes/decisions.py`** (MODIFIED, +137 lines) — `_build_event` extended to return primary + optional signed pair; handler appends both with shared `decided_at` + `trace_id`; emits structured INFO log post-append with 8-char prefix only.
8. **`services/registry-api/src/registry_api/app.py`** (MODIFIED, +22 lines) — `build_app` accepts optional `signing_settings` (tests inject explicit instances); production path constructs via `ApprovalSigningSettings.from_env()`.
9. **`.env.example`** (MODIFIED, +15 lines) — `OPERATOR_HMAC_KEY` block with `openssl rand -hex 32` recipe + NFR-S10 isolation contract.
10. **`services/registry-api/pyproject.toml`** (MODIFIED, +4 lines) — `pydantic-settings>=2.0` dependency (first consumer in registry-api).
11. **`uv.lock`** (MODIFIED, +2 lines) — auto-resolved `pydantic-settings` dep.
12. **`_bmad-output/implementation-artifacts/sprint-status.yaml`** (MODIFIED) — `11-1-hmac-signing-decisions-handler: ready-for-dev → review`.

### Files changed

```
services/registry-api/src/registry_api/settings.py                                  (NEW)
services/registry-api/src/registry_api/adapters/approval_signing.py                 (NEW)
services/registry-api/src/registry_api/test_approval_signing.py                     (NEW)
services/registry-api/src/registry_api/test_decisions_signing.py                    (NEW)
packages/events/src/events/payloads.py                                              (MODIFIED)
services/registry-state/src/registry_state/domain/event_types.py                    (MODIFIED)
services/registry-api/src/registry_api/routes/decisions.py                          (MODIFIED)
services/registry-api/src/registry_api/app.py                                       (MODIFIED)
.env.example                                                                        (MODIFIED)
services/registry-api/pyproject.toml                                                (MODIFIED)
uv.lock                                                                             (MODIFIED — pydantic-settings only)
_bmad-output/implementation-artifacts/sprint-status.yaml                            (MODIFIED — review flip)
_bmad-output/implementation-artifacts/11-1-hmac-signing-decisions-handler.md        (MODIFIED — Status + Tasks/Subtasks + DAR)
```

### Test count delta

```
$ uv run pytest --collect-only -q services/registry-api packages/events services/registry-state | tail -1
941 tests collected in 1.31s
```

Baseline (HEAD `21cc2b4` with our diff stashed via `git stash -u`): **921 tests**.
Post Story 11.1: **941 tests** → **+20 tests** (13 unit in `test_approval_signing.py` + 7 integration in `test_decisions_signing.py`).

### Mypy baseline delta

```
$ uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -1
Success: no issues found in 130 source files
```

**126 → 130** (+4 new source files: `settings.py`, `adapters/approval_signing.py`, `test_approval_signing.py`, `test_decisions_signing.py`). Spec AC7 predicted "+1 or +2 file" — actual is +4 because test files in `services/registry-api/src/registry_api/` (co-located with source) are inside `--strict` scope (NOT in `tests/` wildcard exclusion). Tests are clean under `--strict`. All existing 126 files unchanged.

### Settings class choice (D2 — rationale)

The spec listed three options for the settings home: extend a shared `RegistryApiSettings`, create a dedicated `ApprovalSigningSettings`, or use a plain `os.environ` read. **Chose option B — dedicated NEW module `services/registry-api/src/registry_api/settings.py`** with `ApprovalSigningSettings` for these reasons:

1. **No shared settings class existed in registry-api.** All prior configuration (e.g. `ANTHROPIC_API_KEY`, `db_url`, `clock`, `actor_kind`) is threaded through `build_app(...)` keyword arguments + direct `os.environ` reads in `app.py`. There is no `RegistryApiSettings` to extend.
2. **Pydantic-settings convention favors per-concern classes.** Story 2.16's `AuditedSecret` precedent and `telegram-gateway`'s `BaseSettings` usage both create concern-scoped settings classes rather than a monolithic one. Following the established pattern minimizes reviewer surprise.
3. **NFR-S10 blast radius.** A dedicated class keeps the HMAC-key surface narrow — only `_build_event` and the handler's `app.state.signing_settings` accessor touch it. No risk of accidentally exposing the key through an unrelated settings field.
4. **Story 11.5 rotation forward-compat.** When Story 11.5 adds the `key.rotated` audit event flow, the settings class is the natural home for `previous_key` field (overlap window). Keeping the surface small now makes that extension surgical.

`pydantic-settings>=2.0` was added as a `registry-api` dependency (first consumer; previously transitive only). uv.lock diff confirmed: ONLY `pydantic-settings` added — no surprise transitive deps.

### Surprises / deviations from spec

1. **Mypy delta +4 instead of +1/+2.** Test files `test_approval_signing.py` and `test_decisions_signing.py` are co-located with source under `services/registry-api/src/registry_api/` (matches the `test_decisions.py` precedent). Co-located test files ARE inside `--strict` scope — they are NOT excluded by `mypy.ini`'s `[mypy-tests.*]` wildcard (which only matches the top-level `tests/` directory). All 4 new files are clean under `--strict`; no `# type: ignore` added.
2. **`_build_event` signature became keyword-only.** Original signature took 4 positional args; the new version requires 6 args including `decided_at` + `signing_settings`. Converted to keyword-only (`*,`) to prevent argument-order mistakes at the (single) call site in the handler. Self-contained refactor — no callers elsewhere.
3. **`app.py` accepts `signing_settings` as optional `build_app` kwarg.** Production path constructs via `.from_env()` inside the lifespan; tests inject explicit instances to avoid env-var coupling. This mirrors the existing `clock` injection pattern.
4. **Event-ordering safety: same envelope `parent_event_id`.** The signed sibling carries `parent_event_id=<approval.granted.event_id>` so downstream verification (`just verify-approval`, Story 11.4) can walk the parent linkage directly without scanning for the sibling. Not strictly required by FR64 wording but obviously useful — flagged for Story 11.4 to lean on this.
5. **No new dependencies beyond `pydantic-settings`.** HMAC-SHA256 uses Python stdlib `hmac` + `hashlib`. Confirmed: `uv.lock` diff shows ONLY `pydantic-settings` added (no surprise transitive crypto deps).

### Story 11.2 readiness check

Story 11.2 lifts directly from this story (no rework expected):

- **`TaskApprovalSignedPayload` schema_version bump `1.0.0 → 1.1.0`** — Story 11.2 will tighten field constraints:
  - `task_id` / `decision_id` / `actor_id` pattern constraints (UUIDv7 shape, prefix validation).
  - `hmac_sha256` to `Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")`.
  - Contract-fixture forward-compat pair (per ADR-0001 additive-evolution rule).
- **`key.rotated` event type registration** — Story 11.2 adds the rotation audit event (Story 11.5 will emit it).
- **`capability.denied` event type registration (DD5 from Epic 10 retro)** — opportunistic bundle.

No blockers identified. Story 11.2 can begin as soon as Story 11.1 CI is green + sprint-status flips to `done`.

### Validation gates run locally

| Gate | Result |
|---|---|
| `uv run pytest -q services/registry-api packages/events services/registry-state` | **941 passed** (1 hypothesis dir warning — unrelated) |
| `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` | **Success: no issues found in 130 source files** |
| `uv run python scripts/check_imports.py` | exit 0 |
| `uv run python scripts/check_event_registry.py` | exit 0 |
| `uv run python scripts/check_single_writer.py` | exit 0 |
| `git ls-files -z \| xargs -0 uv run secret-hygiene-precommit` | exit 0 |
| `uv run secret-hygiene-precommit <4 new files>` | exit 0 |
