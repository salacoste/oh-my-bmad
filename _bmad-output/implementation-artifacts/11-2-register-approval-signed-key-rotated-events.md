# Story 11.2 — Register `task.approval_signed` + `key.rotated` event types

Status: **review** (CI pending @ &lt;new-sha&gt;)

## Story

**As** the event-spine maintainer (and Story 11.4/11.5 author)
**I want** the `task.approval_signed` and `key.rotated` event types registered at `schema_version=1.1.0` with full Pydantic constraints + contract-fixture forward-compat pairs
**so that** (a) Story 11.5's key-rotation handler has a registered event type to emit; (b) Story 11.4's `just verify-approval` recipe has a stable payload schema to parse; (c) per Epic 10 retro DD5 — `capability.denied` event type is bundled into the same registration batch so Story 10.4's deferred-preview counter `omb_capability_denied_total` has a real upstream emission path in future work.

Story 11.2 is **pure schema additions** — event-type registration + Pydantic payload models + contract-fixture forward-compat pairs. NO emission logic (deferred to Stories 11.5 and a future capability-denied story). NO middleware changes.

## Acceptance criteria

### AC1 — Bump `task.approval_signed` schema 1.0.0 → 1.1.0 (additive)

Story 11.1 D1 registered `task.approval_signed` at schema_version `1.0.0` with `TaskApprovalSignedPayload`. Story 11.2 bumps to `1.1.0` per the original spec promise.

In `services/registry-state/src/registry_state/domain/event_types.py`, add a second registration line:

```python
register("task.approval_signed", "1.0.0", TaskApprovalSignedPayload)  # Story 11.1 (existing)
register("task.approval_signed", "1.1.0", TaskApprovalSignedPayload)  # Story 11.2 — additive bump
```

Constraints:
- **Additive bump**: SAME payload class — no field additions, no field renames, no type changes. The bump documents Story 11.1 P1-H2's already-applied `Field` constraints as the canonical schema_version-1.1.0 surface.
- **NO Story 11.2 changes to `TaskApprovalSignedPayload`** — Story 11.1 P1-H2 already tightened `hmac_sha256` pattern + `task_id`/`decision_id` patterns. Don't re-tighten or modify; just register at the higher version.
- **`emitter_schema_version`** in event emissions remains at `1.1.0` (Story 11.1 already emits at the current envelope schema_version which is `1.1.0` per P2-I2). Verify the existing emission site in `services/registry-api/src/registry_api/routes/decisions.py` produces envelopes with `schema_version="1.1.0"`.

Self-verification:
- `uv run python -c "from events.schema_registry import EVENT_TYPES; assert ('task.approval_signed', '1.1.0') in [(k, v) for ... ]"` (or equivalent introspection — verify both 1.0.0 AND 1.1.0 registered).
- Test `test_task_approval_signed_registered_at_both_schema_versions` in `tests/contract/` (mirroring existing test patterns there).

### AC2 — Register `key.rotated` event type + `KeyRotatedPayload` (NEW)

In `packages/events/src/events/payloads.py`, add a new payload class:

```python
class KeyRotatedPayload(BaseModel):
    """Audit event recording that the operator's HMAC signing key has been rotated.

    Emitted exactly once per actual rotation by Story 11.5's key-rotation
    detector (compares fingerprint of current ``OPERATOR_HMAC_KEY`` against
    last-known fingerprint persisted in registry-state).

    Per FR65a: pre-rotation approvals remain verifiable ONLY via the prior
    key — operator's responsibility to retain it for audit-window duration.
    The ``key.rotated`` event records the rotation timestamp + fingerprints
    (NEVER the keys themselves — NFR-S10 isolation: keys NEVER appear in
    events/logs/snapshots/registry).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    rotated_at: datetime
    previous_key_fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    new_key_fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    actor_id: str = Field(min_length=1)  # actor that triggered rotation (typically "operator" or service-account)
```

Constraints:
- **Fingerprint = first 16 hex chars of `SHA-256(key_bytes)`** — short enough to be operator-readable in audit logs; long enough (64 bits) for collision resistance in a small key population. Spec'd, not implementation choice — Story 11.5 computes via `hashlib.sha256(key.get_secret_value().encode()).hexdigest()[:16]`.
- **NEVER store the key itself** — fingerprint is one-way. NFR-S10 isolation.
- **`previous_key_fingerprint == new_key_fingerprint` is invalid** — that's not a rotation. Reject via `model_validator(mode="after")` raising `ValueError`.
- Re-export from `packages/events/src/events/__init__.py` `__all__`.

Register at schema_version `1.1.0` (Phase 2 envelope schema baseline) in `services/registry-state/src/registry_state/domain/event_types.py`:

```python
register("key.rotated", "1.1.0", KeyRotatedPayload)
```

Self-verification:
- `uv run python -c "from events.payloads import KeyRotatedPayload; print(KeyRotatedPayload.__name__)"` succeeds.
- `uv run python scripts/check_event_registry.py` exits 0.
- Test `test_key_rotated_rejects_same_fingerprint` — construct payload with `previous_key_fingerprint == new_key_fingerprint`; assert `ValidationError` raised.

### AC3 — Register `capability.denied` event type + `CapabilityDeniedPayload` (NEW — DD5 from Epic 10 retro)

Per Epic 10 retro recommendation: Story 11.2 is the natural place to bundle DD5 because the registration pattern is identical for `task.approval_signed` / `key.rotated` / `capability.denied` — same Pydantic + same registry + same contract-fixture mechanism. DD5's actual EMISSION is deferred to a follow-up story (likely 11.2.x or absorbed into 12.x), but the **type registration unblocks Story 10.4's deferred-preview counter** `omb_capability_denied_total` and gives operators a stable schema for future tooling.

In `packages/events/src/events/payloads.py`:

```python
class CapabilityDeniedPayload(BaseModel):
    """Audit event recording a capability-tier denial at the MCP / HTTP boundary.

    Emitted by ``TierEnforcementMiddleware`` (HTTP) or capability-handler
    decorators (MCP) when a request exceeds the actor's permitted tier.
    Story 10.4 reserved ``omb_capability_denied_total{tier, boundary}``
    counter as a preview-only metric with pre-populated zero values
    pending this event type. Emission wiring deferred (out of scope for
    11.2 — see Out-of-scope risk flags).

    Per Epic 10 retro DD5 — registered here as the natural bundling
    opportunity with other Epic 11 event type registrations.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    tier: Literal["tier1", "tier2", "tier3"]  # bounded — Story 10.4 enum
    boundary: Literal["mcp", "http"]            # bounded — Story 10.4 enum
    actor_id: str = Field(min_length=1)
    attempted_action: str = Field(min_length=1)  # e.g., "task.create", "decision.approve" — opaque string
    reason: str | None = None  # optional operator-facing explanation
```

Constraints:
- **`tier` + `boundary` MATCH Story 10.4's `_CAPABILITY_TIERS` / `_CAPABILITY_BOUNDARIES`** enums EXACTLY (3 × 2 = 6 label combinations pre-populated in `omb_capability_denied_total`). If Story 10.4 ever bumps these enums, both ends must update.
- **`actor_id` not constrained beyond `min_length=1`** — could be UUID, hostname, opaque MCP client ID. Story 6.1+ may tighten when real auth lands.
- **`attempted_action` is opaque string** — no pattern. Producers (HTTP middleware, MCP capability handler) pass whatever identifies the attempt; downstream metrics aggregate by `tier`/`boundary` only.

Register at schema_version `1.1.0` in `event_types.py`:

```python
register("capability.denied", "1.1.0", CapabilityDeniedPayload)
```

Self-verification:
- `uv run python scripts/check_event_registry.py` exits 0 (`capability.denied` recognized).
- Test `test_capability_denied_payload_tier_enum_matches_story_10_4_metrics` — import `CapabilityDeniedPayload` AND `_CAPABILITY_TIERS` (or equivalent constant from `metrics-subscriber/app/metrics.py`); assert `set(get_args(CapabilityDeniedPayload.model_fields["tier"].annotation)) == set(_CAPABILITY_TIERS)`. Same for `boundary` × `_CAPABILITY_BOUNDARIES`. **Catches future enum drift between event payload and metric labels.**

### AC4 — Contract-fixture forward-compat pairs (NEW for 2 event types)

Story 11.2's spec scope: "Contract-fixture forward-compat pair added." This refers to the convention in `tests/contract/` where each event type has a frozen JSON fixture proving the payload schema is stable across schema_version bumps.

Look at `tests/contract/test_mcp_tool_schemas.py` for the existing pattern. Mirror for:

1. **`task.approval_signed`** — minimal JSON fixture matching `TaskApprovalSignedPayload`'s constraint set (Story 11.1 P1-H2). Test asserts `TaskApprovalSignedPayload.model_validate(json.loads(fixture))` succeeds.

2. **`key.rotated`** — minimal JSON fixture matching `KeyRotatedPayload`. Test asserts validation succeeds.

3. **`capability.denied`** — minimal JSON fixture matching `CapabilityDeniedPayload`. Test asserts validation succeeds.

Constraints:
- Fixtures stored in `tests/contract/fixtures/` (NEW directory if doesn't exist) as `<event_type>.v<schema_version>.json` (e.g., `task.approval_signed.v1.1.0.json`).
- Each fixture is a complete envelope JSON (NOT just payload) — exercises the full `EventEnvelope` parsing path including `event_id`, `trace_id`, `emitted_at`, `actor`, `payload` nesting.
- Forward-compat test: load fixture, parse via `EventEnvelope.model_validate_json(...)`, assert envelope `type` + `schema_version` match expectation; payload field-by-field deep-equal.

Self-verification:
- 3 fixture files exist in `tests/contract/fixtures/` (or chosen location).
- `uv run pytest -x -q tests/contract/test_event_payload_contracts.py` (NEW file) — all green.

### AC5 — Schema-registry tests pass

Existing `scripts/check_event_registry.py` enforces that every event type emitted somewhere in the codebase is registered. Story 11.2 ADDS event types without removing any. The check MUST remain green.

Self-verification:
- `uv run python scripts/check_event_registry.py` exits 0.
- `uv run python scripts/check_imports.py` exits 0 (no `services/*→services/*` violations from new test files).
- `uv run python scripts/check_single_writer.py` exits 0.

### AC6 — Mypy --strict baseline extension

3 new payload classes + 1 new test file + 3 new JSON fixtures. Expected baseline shift: **130 → ~131** source files (JSON fixtures are NOT mypy-scoped; only the new `test_event_payload_contracts.py` if co-located in mypy-strict directory).

Self-verification:
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber 2>&1 | tail -2` reports the new count and exit 0.

### AC7 — Validation gates

- `uv run ruff check .` — clean
- `uv run ruff format --check .` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run python scripts/check_imports.py` — exit 0
- `uv run python scripts/check_event_registry.py` — exit 0 (3 new types recognized, all emissions covered)
- `uv run python scripts/check_single_writer.py` — exit 0
- `uv run pytest -x -q packages/events services/registry-state services/registry-api tests/contract` — all green
- `uv run pytest -x -q -m "not slow"` — full suite, no regressions
- `just bootstrap-verify` — green (14/14 imports)

---

## Developer context

### Existing state (post Story 11.1)

- **Story 11.1 done** — `task.approval_signed` registered at schema_version `1.0.0` with `TaskApprovalSignedPayload` (frozen, strict, Field constraints applied per Story 11.1 P1-H2).
- **`TaskApprovalSignedPayload` already has Field constraints** — Story 11.1 P1-H2 tightened `hmac_sha256` (64-char hex), `task_id` / `decision_id` (no-pipe pattern). Story 11.2 does NOT modify the payload class.
- **`CapabilityDenied` exception** exists in registry-api (`adapters/middleware.py:446` + `adapters/errors.py:290`) — currently returns 403 response but emits NO event. Story 11.2 registers the event type; emission is deferred.
- **`omb_capability_denied_total{tier, boundary}` counter** (Story 10.4) pre-populated with 6 zero-value combinations (tier1/tier2/tier3 × mcp/http). DD5 carries the event emission to a future story.
- **`tests/contract/`** has 1 existing test (`test_mcp_tool_schemas.py`) + 1 helper (`_trace_id_vectors.py`). NO existing fixtures directory.
- **Mypy `--strict` baseline:** 130 source files post Story 11.1.

### Architecture compliance

- **FR64** — Story 11.2 finalizes `task.approval_signed` schema_version 1.1.0 (additive bump from Story 11.1's minimal 1.0.0).
- **FR65a** — `key.rotated` event type registered (Story 11.5 emits).
- **NFR-S10** — key isolation preserved: `KeyRotatedPayload` stores FINGERPRINTS (one-way hash), never key bytes.
- **P2-I2** — envelope schema_version bumps to 1.1.0 for the whole phase. Both new types registered at 1.1.0.
- **DD5** (Epic 10 retro) — `capability.denied` event type bundled per retro recommendation.
- **NFR-O1** preserved — event spine remains primary observability; counter (Story 10.4) is a derived projection.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `pydantic` | already pinned (≥2.5) | `ConfigDict`, `Field`, `model_validator` for cross-field validation. |
| `events` workspace package | already wired | `BaseModel`, `EventEnvelope`. |
| no new deps | — | Story 11.2 introduces ZERO new third-party dependencies. |

### File-structure requirements

```
packages/events/src/events/
├── payloads.py                    # MODIFY: add KeyRotatedPayload + CapabilityDeniedPayload
└── __init__.py                    # MODIFY: re-export both new classes

services/registry-state/src/registry_state/domain/event_types.py   # MODIFY: 3 new register() calls

tests/contract/
├── fixtures/                       # NEW directory
│   ├── task.approval_signed.v1.1.0.json   # NEW
│   ├── key.rotated.v1.1.0.json            # NEW
│   └── capability.denied.v1.1.0.json      # NEW
└── test_event_payload_contracts.py # NEW: forward-compat tests
```

### Testing requirements

- **Pyramid:** unit tests for each new payload class (synchronous Pydantic validation) + contract tests parsing each fixture file via `EventEnvelope.model_validate_json(...)`.
- **Test isolation:** no shared state; each test parses its own fixture file.
- **Enum-drift catch:** AC3's test asserts `CapabilityDeniedPayload.tier` Literal values match Story 10.4's `_CAPABILITY_TIERS` constant. If either drifts, this test fails. **Load-bearing for Epic 10's DD5 contract.**
- **No `pragma: no cover`** on operational paths.

### Previous-story intelligence

#### From Story 11.1 (just closed)

- **`TaskApprovalSignedPayload` Field constraints already applied** (P1-H2). Story 11.2 does NOT re-tighten — pure schema_version bump.
- **`SecretStr` discipline** — `KeyRotatedPayload` does NOT contain a `SecretStr` field (fingerprints are public-safe). NFR-S10 isolation preserved by NOT storing key bytes.
- **Pipe-injection guard pattern** (P1-H1) — n/a for `key.rotated`/`capability.denied` (no canonical-string signing).

#### From Epic 10 retro

- **DD5 — capability.denied event type registration**: this story IS the natural bundling opportunity per retro recommendation. Registers the type; emission deferred.
- **AI-2 — Spec values MUST cite canonical source**: `tier` + `boundary` enum values cited as matching `metrics-subscriber/app/metrics.py:_CAPABILITY_TIERS` / `_CAPABILITY_BOUNDARIES`. AC3's drift-detection test enforces.
- **AI-3 — Decisions block mandatory pre-implementation**: this story has D1-D4 below.
- **AI-4 — No hardcoded dates**: fixture JSON files use `1970-01-01T00:00:00+00:00` or similar epoch-style placeholder (NOT today's date).

### Trade-off notes

- **Bundle DD5 in Story 11.2 vs. separate story**: chose bundle. Reason: registration pattern is identical for all 3 event types; bundling preserves cadence (1-pass closure). Separate story would split a 30-line schema addition across 2 ceremonies.
- **Fingerprint = 16 hex chars (64 bits)**: chose 16. Alternative was full 64-char SHA-256 hex (256 bits). 64 bits is more than enough for a single-operator key population; 16 chars is operator-readable in audit logs. Trade-off: brute-force collision search on 64 bits is computationally trivial but operationally irrelevant (no signing oracle exposes the fingerprint).
- **`KeyRotatedPayload` `previous_key_fingerprint == new_key_fingerprint` rejection**: chose to reject via `model_validator`. Alternative was Story 11.5 detecting and not-emitting. Rejecting at the model level catches BOTH detection bugs AND replay attacks where an attacker tries to emit a no-op rotation event.
- **`CapabilityDeniedPayload.attempted_action` opaque string**: chose `min_length=1` only (no pattern). Alternative was enum. Rejected because MCP actions are ad-hoc strings (`task.create`, `decision.approve`, etc.) — enforcing an enum would force schema_version bumps every time MCP adds a method.
- **No emission of capability.denied / key.rotated in this story**: chose to defer. Reason: emission requires middleware changes (capability.denied) or detector logic (key.rotated). Pure type registration is small + low-risk; emission stories build on this.

### Lessons from prior reviews to apply

- **Pydantic `Field(pattern=...)`** for hex / enum-like patterns — established Story 11.1 P1-H2.
- **`model_validator(mode="after")`** for cross-field invariants like `previous_key_fingerprint != new_key_fingerprint`.
- **`Literal[...]` for bounded enums** — `tier`, `boundary`, `action`. Matches Story 10.4's bounded-enum-discipline.
- **Re-export from `packages/events/src/events/__init__.py`** so consumers (Story 11.4, 11.5, future capability-emission story) import via `from events import KeyRotatedPayload` not `from events.payloads import KeyRotatedPayload`.
- **Spec self-verification clauses must execute against actual code** (Epic 10 P1-M1).

### Non-goals (do NOT do in 11.2)

- **Emit `key.rotated` event** → Story 11.5 (key rotation detector + fingerprint persistence).
- **Emit `capability.denied` event** → DD5 follow-up (likely Story 11.2.x or absorbed into Epic 12). Requires middleware change.
- **Wire `omb_capability_denied_total` counter to actual increments** → DD5 follow-up (depends on emission).
- **Story 11.4's `just verify-approval` recipe** → Story 11.4 scope.
- **ADR-0006** → Story 11.5 scope (rotation flow finalizes the design).
- **Fingerprint computation logic** → Story 11.5 scope (Story 11.2 just registers the schema field; Story 11.5 computes + persists).
- **Modify `TaskApprovalSignedPayload`** — Story 11.1 P1-H2 already set Field constraints. Story 11.2 is additive registration only.

## Out-of-scope risk flags

- **DD5 emission gap**: registering `capability.denied` event type WITHOUT wiring emission creates a window where Story 10.4's preview counter remains at 0 indefinitely. Document this in Dev Agent Record's "Story 11.2.x readiness check" so the next story knows DD5 is half-done.
- **`KeyRotatedPayload` cross-field validator**: Pydantic v2's `model_validator(mode="after")` runs AFTER all field validators. If the fingerprint pattern Field validator fails first, the cross-field check never runs. Test both validation paths separately.
- **Contract fixtures use `event_id` placeholders**: if fixtures hardcode a specific UUIDv7, the timestamp portion of the UUID may drift relative to `emitted_at`. Use clearly-fake UUIDs (e.g., `00000000-0000-7000-8000-000000000001`) to avoid "fake but plausible" confusion.
- **Capability.denied tier × boundary enum drift**: AC3's drift-detection test imports from `metrics_subscriber.app.metrics`. This creates a `services/registry-state` → `services/metrics-subscriber` import path. **Verify `scripts/check_imports.py` allows this OR refactor**: move the enum constants to `packages/events/` (or a new shared `packages/cap_tiers/`) so both the payload (in `packages/events/`) and the counter (in `services/metrics-subscriber/`) import from a shared package. **DECISION: see D4 below.**
- **`actor_id` constraint mismatch with Story 11.1**: Story 11.1's `TaskApprovalSignedPayload.actor_id` has `Field(pattern=r"^[a-zA-Z0-9_:.-]+$")` (no-pipe). Story 11.2's `KeyRotatedPayload.actor_id` + `CapabilityDeniedPayload.actor_id` have ONLY `min_length=1` (no pattern). This is intentional — different events have different actor_id constraints (rotation actor can be richer; capability denial actor may be opaque MCP client ID). Document the divergence.

## Decisions (resolved before implementation)

- **D1 — Bundle DD5 (`capability.denied`) into Story 11.2.** Per Epic 10 retro recommendation. Registration pattern identical; preserves cadence; defers EMISSION (out of scope).
- **D2 — Fingerprint format: 16 hex chars (64 bits)**, computed as `SHA-256(key_bytes)[:16]`. Operator-readable, collision-safe for single-operator population.
- **D3 — `KeyRotatedPayload` rejects `previous_fingerprint == new_fingerprint`** via `model_validator(mode="after")`. Catches both detection bugs and replay attacks.
- **D4 — `tier`/`boundary` enum drift test imports from `metrics_subscriber.app.metrics`**. This creates a `tests/contract/` → `services/metrics-subscriber/` import. Verify `scripts/check_imports.py` permits this (tests/ is typically outside the read-only-subscriber rule). If NOT permitted: refactor the enum constants into a shared location (`packages/events/src/events/_capability_enums.py` or similar) — Story 10.4's `_CAPABILITY_TIERS` moves there + metrics-subscriber re-imports. **First check + report; only refactor if blocked.**
- **D5 — `capability.denied` emission deferred to follow-up story (likely Story 11.2.x or Epic 12.x).** Registering schema unblocks Story 10.4's counter without committing to middleware change in this story. DAR documents the DD5 half-done state.

## Definition of done

- All 7 ACs met; self-verification commands in each AC pass.
- `sprint-status.yaml` `11-2-register-approval-signed-key-rotated-events: backlog → done` (after CI green).
- Spec Status `**done** (CI green @ <sha>)`.
- 3 new event types registered + 3 contract fixtures + 1 new test file.
- Dev Agent Record filled in (implementation summary, files changed, test count delta, mypy baseline delta, surprises/deviations, D4 outcome, Story 11.4/11.5 readiness check).
- DD5 half-done state documented (event registered; emission deferred — what story closes it?).
- No regressions in existing tests.

---

## Tasks/Subtasks

- [x] **AC1** — Bump `task.approval_signed` 1.0.0 → 1.1.0 (additive registration in `event_types.py`)
- [x] **AC2** — Register `key.rotated` 1.1.0 + add `KeyRotatedPayload` to `packages/events/src/events/payloads.py` (D3 cross-field validator rejecting same-fingerprint)
- [x] **AC3** — Register `capability.denied` 1.1.0 + add `CapabilityDeniedPayload` (DD5 bundling; bounded `tier` / `boundary` `Literal`s)
- [x] **AC4** — Contract-fixture forward-compat pairs (3 JSON fixtures under `tests/contract/fixtures/` + `test_event_payload_contracts.py` with 8 tests)
- [x] **AC5** — Schema-registry checks remain green (`check_event_registry.py`, `check_imports.py`, `check_single_writer.py`)
- [x] **AC6** — Mypy `--strict` baseline preserved (130 source files, JSON fixtures non-mypy-scoped, contract test file under `tests/` outside strict scope)
- [x] **AC7** — Validation gates green (ruff/format/mypy/imports/registry/single-writer/`pytest -m "not slow"` 2944 passed/3 skipped/0 failed/`just bootstrap-verify` 14/14 imports)

## Dev Agent Record

### Implementation summary

Story 11.2 is a pure additive schema-registration story. No middleware, no emitters, no envelope changes. The work landed in three layers:

1. **Pydantic models** — added `KeyRotatedPayload` and `CapabilityDeniedPayload` to `packages/events/src/events/payloads.py` per D2/D3 (fingerprint format + no-op-rotation reject) and AC3 (bounded `Literal` enums for `tier` / `boundary`).
2. **Registry registration** — added 3 `register()` calls in `services/registry-state/src/registry_state/domain/event_types.py` (`task.approval_signed` 1.1.0 additive bump alongside Story 11.1's 1.0.0; new `key.rotated` 1.1.0; new `capability.denied` 1.1.0).
3. **Contract tests + fixtures** — created `tests/contract/fixtures/` with 3 frozen JSON envelope fixtures and `tests/contract/test_event_payload_contracts.py` with 8 tests covering registration, fixture round-trip, D3 cross-field reject, and the AC3 enum-drift contract (load-bearing for DD5).

### Files changed

| Path | Change |
|---|---|
| `packages/events/src/events/payloads.py` | +2 payload classes, 2 lines added to `__all__` |
| `packages/events/src/events/__init__.py` | Comment-only — `KeyRotatedPayload` + `CapabilityDeniedPayload` flow through the existing `from events.payloads import *` star-import (re-export already covered by `*_payloads_all` at the bottom of `__all__`) |
| `services/registry-state/src/registry_state/domain/event_types.py` | +3 `register()` calls + 2 import additions in `__all__` |
| `tests/contract/test_event_payload_contracts.py` | NEW — 8 tests (registration × 3, fixture round-trip × 3, D3 cross-field reject × 1, AC3 enum-drift contract × 1) |
| `tests/contract/fixtures/task.approval_signed.v1.1.0.json` | NEW envelope fixture |
| `tests/contract/fixtures/key.rotated.v1.1.0.json` | NEW envelope fixture |
| `tests/contract/fixtures/capability.denied.v1.1.0.json` | NEW envelope fixture |
| `_bmad-output/implementation-artifacts/11-2-register-approval-signed-key-rotated-events.md` | Status `ready-for-dev` → `review` + Tasks/Subtasks + Dev Agent Record (this section) |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Annotation `in-progress` → `review` for `11-2-register-approval-signed-key-rotated-events` |

### Test count delta

```
$ uv run pytest --collect-only -q packages/events services/registry-state services/registry-api tests/contract
# pre-11.2 (HEAD 005c512):   980 tests collected
# post-11.2:                 988 tests collected   (+8)
```

The +8 comes from `tests/contract/test_event_payload_contracts.py`. Full suite `pytest -m "not slow"` shows 2944 passed / 3 skipped (matching CI invariants).

### Mypy baseline delta

```
$ uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber
Success: no issues found in 130 source files
```

**130 → 130 (no delta).** The new contract test file lives under `tests/contract/` which is outside the mypy `--strict` scope (`packages/`, `services/registry-api`, `services/registry-state`, `services/metrics-subscriber`). The 3 JSON fixtures are not Python source files. The 2 new payload classes are added to existing `payloads.py` which is already in the strict-scope count — they don't change the file count.

### D4 outcome

**OPTION C — import allowed as-is.** Verified `scripts/check_imports.py`:

* `SCAN_ROOTS` is `[packages/, services/, mcp-servers/]` — `tests/` is **not** scanned.
* `EXTRA_SKIP = {"tests", "scripts", "migrator", "fixtures"}` is additionally applied within those roots.

Therefore `tests/contract/test_event_payload_contracts.py` may directly import from BOTH `events.payloads` AND `metrics_subscriber.app.metrics` without violating the cross-service import rule. **No enum extraction or refactor was needed.** The enum-drift contract test (`test_capability_denied_payload_tier_enum_matches_story_10_4_metrics`) reads `_CAPABILITY_TIERS` / `_CAPABILITY_BOUNDARIES` from `metrics_subscriber.app.metrics` at runtime and asserts against `get_args(CapabilityDeniedPayload.model_fields["tier"].annotation)` etc. — keeping Story 10.4 as the single source of truth.

Note for future reviewers: if Story 11.2.x emission lands and the metric label values need to be referenced from inside a non-test service (e.g. the emitting middleware in `services/registry-api/`), this OPTION C arrangement is no longer sufficient — a follow-up will need to extract the enum constants to a shared location (`packages/events/src/events/_capability_enums.py` was the originally-floated location). D4 OPTION C is good for the test-only, non-emitting state shipped in Story 11.2.

### Surprises / deviations from spec

* **`KeyRotatedPayload.rotated_at` uses `AwareDatetime`, not raw `datetime`.** Spec AC2 sample code uses `datetime`. Switched to `pydantic.AwareDatetime` to match the codebase convention (Stories 2.10 / 3.11 H8 / 5.2 — all timestamp payload fields reject naive datetimes at the payload boundary). Strictly additive over the spec.
* **`CapabilityDeniedPayload.reason` gained explicit bounds** (`min_length=1, max_length=4096`). Spec only said `str | None = None`. Added bounds to match the sibling `Tier3ActionAttemptedPayload.reason` cap and to defend producers from emitting empty strings that render as a useless `Reason:` label with no value.
* **`__init__.py` re-export is implicit, not explicit.** Spec AC2 says "Re-export from `__init__.py` `__all__`". The existing star-import (`from events.payloads import *`) followed by `*_payloads_all` at the bottom of the public `__all__` already covers the two new classes. No explicit per-class import was needed; added a comment block explaining the indirection so future readers can verify the re-export contract without grepping for the names.
* **17 pre-existing test failures in partial spec command (`packages/events + services/registry-api` co-run).** The spec command `uv run pytest -x -q packages/events services/registry-state services/registry-api tests/contract` triggers a registration-isolation issue when packages/events tests run before services/registry-api (the `EventEnvelope.create` call in registry-api routes/tasks.py fails because `task.created` 1.1.0 isn't registered in that pytest session). **Pre-existing on HEAD 005c512 — Story 11.2 introduces ZERO new failures.** Verified by `git stash + same command`. The CI gate (`pytest -m "not slow"` single invocation from project root) passes cleanly (2944/2944) because the per-service conftest registration chain initializes correctly when all tests are collected together. Not blocking for Story 11.2 finalization — flagging as a latent technical-debt item for a future CI hygiene story.

### Story 11.4 readiness check

`TaskApprovalSignedPayload` is now registered at schema_version `1.1.0` per AC1. The frozen JSON fixture `tests/contract/fixtures/task.approval_signed.v1.1.0.json` gives Story 11.4's `just verify-approval` recipe a stable parsing target. Story 11.1 P1-H2 Field constraints (HMAC 64-hex, no-pipe IDs) are preserved unchanged. **Story 11.4 unblocked.**

### Story 11.5 readiness check

`KeyRotatedPayload` registered at schema_version `1.1.0` with:

* `previous_key_fingerprint` / `new_key_fingerprint`: exactly 16 lowercase hex chars (D2: `SHA-256(key_bytes)[:16]`)
* D3 cross-field reject of `previous == new`
* `AwareDatetime` on `rotated_at`
* NFR-S10 isolation — fingerprints only, never key bytes

Story 11.5's key-rotation detector implements the fingerprint computation, persistence, and emission. **Story 11.5 unblocked on the schema side.** ADR-0006 is still Story 11.5's scope.

### DD5 half-done state

Story 11.2 registers `capability.denied` at 1.1.0 and ships:

* `CapabilityDeniedPayload` Pydantic model (bounded `Literal` enums for `tier` and `boundary`)
* Schema-registry entry
* Contract-fixture forward-compat pair
* Enum-drift contract test against Story 10.4's `omb_capability_denied_total{tier, boundary}` label values

**Still deferred (DD5 half-done):**

* Actual emission of `capability.denied` events from the HTTP `TierEnforcementMiddleware` (`services/registry-api/src/registry_api/adapters/middleware.py:446`)
* Actual emission from MCP capability handlers
* Wiring `omb_capability_denied_total` counter increments to the new emissions

**Recommended next story:** Either a dedicated `Story 11.2.1 — capability.denied emission` (cleanest) OR fold into Epic 12 scope when the MCP capability handler work lands. Until that story closes, the `omb_capability_denied_total{tier=*, boundary=*}` counter remains at 0 indefinitely (pre-populated zero samples from Story 10.4 still satisfy the preview-counter contract; no operational regression).

### Validation evidence

```
ruff check .                                            All checks passed!
ruff format --check .                                   355 files already formatted
mypy --strict packages/ services/{registry-api,registry-state,metrics-subscriber}
                                                        Success: no issues found in 130 source files
scripts/check_imports.py                                exit 0
scripts/check_event_registry.py                         exit 0
scripts/check_single_writer.py                          exit 0
pytest -m "not slow" (CI-equivalent)                    2944 passed, 3 skipped, 30 deselected
just bootstrap-verify                                   ✓ bootstrap OK (14 workspace-member imports verified)
```

---

## Frontmatter

```yaml
---
story_id: 11.2
story_key: 11-2-register-approval-signed-key-rotated-events
parent_epic: 11
phase: 2
fr_refs: [FR64, FR65a]
nfr_refs: [NFR-S10]
arch_refs:
  - "Envelope schema_version 1.1.0 (P2-I2 — Phase 2 envelope bump)"
  - "Bounded-enum cardinality discipline (Story 10.4 + ADR-0005 §Cardinality)"
estimated_hours: 2-3
priority: high (unblocks Story 11.4 verifier schema + Story 11.5 key-rotation emission; closes Epic 10 retro DD5)
blocks:
  - 11.4 (just verify-approval — needs frozen payload schema at 1.1.0)
  - 11.5 (key rotation emission — needs registered key.rotated event type)
  - DD5 follow-up story (capability.denied emission — needs registered event type)
blocked_by:
  - 11.1 (TaskApprovalSignedPayload already exists at 1.0.0 with Field constraints)
status: ready-for-dev
created: 2026-05-20
created_by: bmad-create-story skill
---
```
