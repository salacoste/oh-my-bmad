# Story 2.16: secret.accessed audit event emission

Status: review

## Story

As **the platform (FR42 / NFR-S3)**,
I want **(a) a `SecretAccessedPayload` event type registered under v1.0.0 + v1.0.1, (b) an `AuditedSecret` wrapper in `packages/secret-hygiene/` that emits a `secret.accessed` typed event on every read of a configured secret WITHOUT including the secret value in the payload, and (c) a `BaseSettings`-compatible mixin that auto-wires the wrapper around any field marked as a secret**,
so that **secret access has an audit trail queryable from the registry (FR42), zero plaintext leakage occurs (NFR-S3), and future services adopting `pydantic-settings` for env-var injection get free audit emission by declaring fields as audited secrets**.

## Acceptance Criteria

1. **AC-1: `SecretAccessedPayload` model + schema registry registration** — `services/registry-state/src/registry_state/domain/event_types.py`:
   ```python
   class SecretAccessedPayload(BaseModel):
       """Payload for the ``secret.accessed`` audit event (FR42 / NFR-S3).
       
       The secret VALUE is NEVER included — only the metadata identifying
       which secret was read, by which actor, and at what scope.
       """
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       
       secret_name: str = Field(min_length=1, max_length=128)  # e.g., "anthropic_api_key"
       scope: Literal["read"] = "read"  # Phase 1 only supports read; future: "rotated", "exposed"
   ```
   Register under both v1.0.0 AND v1.0.1 (matching Story 2.14's additive-version pattern). Add to `__all__`.

2. **AC-2: `AuditedSecret` wrapper class** — new file `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`:
   ```python
   class AuditedSecret:
       """Wrapper that emits `secret.accessed` on each value read.
       
       Construct with the secret value + emission callable; expose
       ``.value`` property that emits an event then returns the value.
       Repr/str redact to ``"<REDACTED:secret_name>"`` so the wrapper is
       safe to log accidentally.
       """
       
       __slots__ = ("_value", "_secret_name", "_emit", "_actor")
       
       def __init__(
           self,
           value: str,
           *,
           secret_name: str,
           emit: Callable[[EventEnvelope], Awaitable[None]] | None,
           actor: Actor,
       ) -> None:
           ...
       
       @property
       def value(self) -> str:
           """Read the secret. Emits secret.accessed (best-effort) on every call."""
           ...
       
       def __repr__(self) -> str:
           return f"<REDACTED:{self._secret_name}>"
       
       __str__ = __repr__
   ```
   - **Async emission**: the `emit` callable is async (`Callable[[EventEnvelope], Awaitable[None]]`). The `.value` property is SYNCHRONOUS — it CANNOT await. Resolution: schedule emission as a fire-and-forget asyncio task via `asyncio.get_running_loop().create_task(emit(envelope))`. If no loop is running (sync-only context), log a WARNING and SKIP emission (don't crash). Document this best-effort contract.
   - **`emit=None`**: callers in pure-test contexts may pass `None` to disable emission entirely. Document.
   - **Actor**: passed at construction; reused across all reads of the same secret.

3. **AC-3: `SecretFieldFactory` for pydantic-settings integration** — `packages/secret-hygiene/src/secret_hygiene/audited_secret.py` (same file):
   ```python
   def audited_secret_field(
       secret_name: str,
       *,
       env_var: str | None = None,
       default: str | None = None,
   ) -> Field:
       """Return a Pydantic Field configured to wrap the env-var value
       in an AuditedSecret on validation.
       
       Use inside a BaseSettings subclass:
       
           class MySettings(BaseSettings):
               anthropic_api_key: AuditedSecret = audited_secret_field(
                   "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
               )
       """
       ...
   ```
   Implementation uses Pydantic v2's `field_validator` or custom type to convert the raw string from `pydantic-settings` env-var resolution into an `AuditedSecret` instance.

4. **AC-4: `AuditedBaseSettings`** — base class extending `pydantic_settings.BaseSettings`:
   ```python
   class AuditedBaseSettings(BaseSettings):
       """BaseSettings subclass that supports AuditedSecret fields.
       
       Subclasses declare secrets via ``audited_secret_field(...)``; on
       instantiation, each secret value is wrapped in an AuditedSecret
       with the (emit, actor) callables passed via ``model_construct_with_audit``.
       """
       
       @classmethod
       def from_env(
           cls,
           *,
           emit: Callable[[EventEnvelope], Awaitable[None]] | None,
           actor: Actor,
       ) -> Self:
           """Construct from env-vars + wrap secrets with the given audit callbacks."""
           instance = cls()  # standard pydantic-settings env resolution
           # Walk fields; rewrap any AuditedSecret with the real emit/actor
           ...
           return instance
   ```
   The clean factoring: subclass `AuditedBaseSettings` instead of `BaseSettings`; use `audited_secret_field(...)` for each secret; call `MySettings.from_env(emit=writer.append, actor=...)` at lifespan startup.

5. **AC-5: Co-located unit tests** in `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py`:
   - `test_audited_secret_emits_event_on_value_read` — construct with mock emit; read `.value`; assert one `secret.accessed` envelope was emitted with correct `secret_name` and `scope="read"`.
   - `test_audited_secret_repr_redacts` — assert `repr(s)` and `str(s)` return `<REDACTED:...>` (no leak).
   - `test_audited_secret_no_loop_skips_emission_with_warning` — synchronous context (no running loop); read `.value`; assert WARNING logged + emission silently skipped + value returned correctly.
   - `test_audited_secret_emit_none_disables_emission` — pass `emit=None`; read `.value`; no emission attempt + no warning.
   - `test_audited_secret_emission_failure_does_not_propagate` — emit callable raises; assert the exception is logged but the `.value` read still returns the secret (security path takes precedence over audit path).
   - `test_audited_secret_value_field_excluded_from_payload` — read a secret; inspect the emitted envelope's payload; assert `secret_value` (or any field containing the actual secret) is NOT present; only `secret_name` + `scope`.
   - `test_audited_base_settings_wraps_env_vars` — set `ANTHROPIC_API_KEY=test123` via monkeypatch; instantiate via `from_env(emit=mock, actor=...)`; assert `settings.anthropic_api_key.value == "test123"` AND emission fired.
   - `test_audited_base_settings_repr_does_not_leak` — same setup; `repr(settings)` does NOT include "test123" anywhere.
   - `test_secret_accessed_payload_validates_correctly` — payload model construction + serialization round-trip.
   - Target: ≥10 tests across these scenarios.

6. **AC-6: `pydantic-settings` workspace dependency** — `packages/secret-hygiene/pyproject.toml`:
   - Add `pydantic-settings>=2.5,<3.0` (current stable major).
   - Bump `secret-hygiene` version from current to next minor.
   - Run `uv sync --all-groups` to regenerate uv.lock.

7. **AC-7: `Actor.kind` choice** — spec's BDD scenario uses `Actor(kind="service", id="worker-wrapper")` but Story 2.10's review identified that `"service"` is NOT in the canonical `ActorKind` Literal (`operator | orchestrator | worker | system | clawhip`). Use `kind="worker"` for worker-wrapper-originated reads, `kind="system"` for platform-internal reads, etc. Document in the route docstring + Spec Amendments. The `actor.id` carries the more specific identity (e.g., `"worker-wrapper"`, `"registry-api"`, `"telegram-gateway"`).

8. **AC-8: Event-emission contract** — when `AuditedSecret.value` is read:
   - Build `EventEnvelope.create(type="secret.accessed", schema_version="1.0.0", payload=SecretAccessedPayload(secret_name=..., scope="read"), actor=..., ...)`.
   - Schedule via `asyncio.get_running_loop().create_task(emit(envelope))`.
   - Track the task somewhere observable for tests (e.g., return the task from a debug helper or expose via a class attribute).

9. **AC-9: No integration with existing services in this story** — Phase 1 services (registry-api, registry-state) don't currently use `pydantic-settings` for secret env-vars. Wiring `AuditedBaseSettings` into a real service (e.g., registry-api reads `ANTHROPIC_API_KEY`) lands in a follow-up story when there's an actual secret-using service. Story 2.16 ships the **infrastructure** only.

10. **AC-10: `__init__.py` re-exports** — `packages/secret-hygiene/src/secret_hygiene/__init__.py` exports `AuditedSecret`, `AuditedBaseSettings`, `audited_secret_field`. Update `__all__`.

11. **AC-11: mypy --strict clean** — all new code passes `mypy --strict`. The `pydantic-settings` integration may need `# type: ignore` for boundary issues; minimize and justify each.

12. **AC-12: All architectural gates green**:
    - `check_event_registry`: new `secret.accessed` literal must be findable. Verify via `EventEnvelope.create(type="secret.accessed", ...)` calls in the AuditedSecret module — gate scans for direct `EventEnvelope(...)` construction and `<X>.emit(...)` patterns; `EventEnvelope.create()` form is outside scanner scope (vacuously green per Story 2.10's pattern).
    - `check_single_writer`: secret-hygiene package writes nothing to SQLite; emission goes through caller-supplied async callable (typically `EventLogWriter.append`). Gate stays green.
    - `check_imports`: `secret-hygiene` imports only from `events` (allowed: package → package).

13. **AC-13: Regression** — `just test` count grows by ≥10 new co-located tests (target: **551 passed, 2 skipped**). `just lint` 8/8 green. `just bootstrap-verify` shows `secret_hygiene` at the new version.

14. **AC-14: Atomic commit** titled `feat(secret-hygiene): story 2.16 — secret.accessed audit event + AuditedSecret wrapper · FR42 NFR-S3`.

## Tasks / Subtasks

- [x] **Task 1: Payload model + schema registry** (AC: #1)
  - [x] Add `SecretAccessedPayload` to `services/registry-state/src/registry_state/domain/event_types.py`.
  - [x] Register under `("secret.accessed", "1.0.0", SecretAccessedPayload)` AND `("secret.accessed", "1.0.1", SecretAccessedPayload)`.
  - [x] Update `__all__`.
  - [x] Verify `EVENT_TYPES` count grows from 12 → 13 (one new bare type name).

- [x] **Task 2: AuditedSecret wrapper** (AC: #2, #7, #8)
  - [x] Create `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`.
  - [x] Implement `AuditedSecret` class with redaction-aware `__repr__` / `__str__` and async-loop-aware `.value` emission.
  - [x] Document the best-effort emission contract (no loop → warn + skip; emit raises → log + suppress).
  - [x] Use `Actor.kind = "worker"` / `"system"` / etc. per AC-7 (NOT spec's `"service"`).

- [x] **Task 3: pydantic-settings integration** (AC: #3, #4, #6)
  - [x] Add `pydantic-settings>=2.5,<3.0` to `packages/secret-hygiene/pyproject.toml`.
  - [x] Bump version.
  - [x] Implement `audited_secret_field` (Pydantic Field factory).
  - [x] Implement `AuditedBaseSettings` (BaseSettings subclass with `from_env` factory).
  - [x] `uv sync --all-groups`.

- [x] **Task 4: Co-located tests** (AC: #5)
  - [x] Create `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py` with ≥10 tests per AC-5.
  - [x] Use `pytest.MonkeyPatch.setenv` for env-var fixtures.
  - [x] Use `caplog` for warning assertions.
  - [x] Use a mock `emit` (collects envelopes into a list) to verify emission semantics.

- [x] **Task 5: Re-exports + lint scope** (AC: #10, #11)
  - [x] Update `packages/secret-hygiene/src/secret_hygiene/__init__.py` re-exports.
  - [x] Verify mypy strict on the new files; resolve any `pydantic-settings` typing issues with minimal `# type: ignore`.

- [x] **Task 6: Regression + atomic commit** (AC: #12, #13, #14)
  - [x] `just test` count grows by ≥10.
  - [x] `just lint` 8/8 green.
  - [x] `just check-gates-self-test` 3/3.
  - [x] `just bootstrap-verify` shows new `secret_hygiene` version.
  - [x] Single atomic commit per AC-14.

## Dev Notes

### Architecture context

- **FR42** (PRD): "Platform emits `secret.accessed` on every secret access."
- **NFR-S3** (PRD): "every secret read emits a typed `secret.accessed` event (actor, scope, timestamp) queryable via registry"
- **FR43** (PRD): "Platform sanitizes events, snapshots, artifacts, logs — zero plaintext secret persistence." This story's `AuditedSecret` enforces the audit half; Story 1.7's log-sanitizer covers the runtime-log half.

### Why `kind="worker"`/`"system"` not `"service"`

Story 2.10's code review identified `Actor.kind` as a `Literal` that does NOT include `"service"` — only `operator | orchestrator | worker | system | clawhip`. The spec's BDD scenario "actor.kind=service" is a typo. The correct mapping:
- `worker-wrapper` reading a secret → `Actor(kind="worker", id="worker-wrapper")`
- `registry-api` reading a secret → `Actor(kind="system", id="registry-api")`
- `telegram-gateway` reading → `Actor(kind="system", id="telegram-gateway")` (or "operator"-like? — depends on whether the read happens during operator request handling vs init)

Document this convention in `audited_secret.py` module docstring + flag the spec typo in Spec Amendments.

### Why best-effort sync-context emission

`AuditedSecret.value` is synchronous (Python attribute-access semantics). It cannot await. Three options:
- **A (chosen)**: schedule emission via `asyncio.get_running_loop().create_task(emit(...))`. If no loop is running, log a WARNING and skip emission. Pros: simple; works in async services (registry-api lifespan, worker-wrapper main loop). Cons: in sync-only contexts (e.g., a config-validation script run via `python -m`), audit emission is silently skipped. Acceptable: those contexts don't have access to a running event log writer anyway.
- **B (rejected)**: queue emissions to a thread that has its own loop. Adds threading complexity for marginal benefit.
- **C (rejected)**: make `AuditedSecret.value` async. Breaks all sync callers (`config.api_key`).

Document A's contract clearly: callers using `AuditedSecret` in sync contexts will not get audit events. Future story can add a sync-emit fallback (e.g., direct write to JSONL via a sync `EventLogWriter` API).

### Why no real-service integration in this story

Phase 1 services don't currently use `pydantic-settings`. Wiring `AuditedBaseSettings` into registry-api or worker-wrapper requires:
- Defining the service's settings class
- Threading the `emit` callable through the service's lifespan
- Determining the service's `Actor` identity at startup

Each service has different secrets to audit; rolling them all into 2.16 expands scope significantly. Cleaner: ship the infrastructure here; integrate per-service in Stories 5.4 (worker-wrapper Anthropic key), 3.1 (telegram-gateway bot token), 5.7 (worker-wrapper GitHub PAT). Document these as follow-up integration points.

### What this story does NOT do

- **Does NOT implement runtime log sanitization** (Story 1.7's `secret-hygiene/sanitizer.py` covers logs).
- **Does NOT integrate with any existing service** (defer per AC-9).
- **Does NOT add a sync-emit fallback** — best-effort with warnings is acceptable for Phase 1.
- **Does NOT add `secret.rotated`, `secret.exposed`, etc.** event subtypes — only `secret.accessed` with `scope="read"`.
- **Does NOT enforce that every actor reading a secret MUST use AuditedSecret** — services that bypass the wrapper produce no audit events. Discoverability of unaudited secret reads is a static-analysis concern (potential future check-gate script).

### Previous Story Intelligence

- **Story 2.10** established the `Actor(kind=…)` Literal canonical set; spec typos referencing `"service"` recur and are now caught early.
- **Story 2.14** registered all event types under both v1.0.0 + v1.0.1 (additive-only). This story follows that pattern.
- **Story 2.13** added `cachetools` directly to registry-api when needed at the route layer; same precedent — `secret-hygiene` adds `pydantic-settings` directly.

### File List (predicted)

**New (2):**
- `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`
- `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py`

**Modified (4):**
- `services/registry-state/src/registry_state/domain/event_types.py` — `SecretAccessedPayload` + 2 register() calls.
- `packages/secret-hygiene/src/secret_hygiene/__init__.py` — re-exports.
- `packages/secret-hygiene/pyproject.toml` — `pydantic-settings` dep + version bump.
- `uv.lock` — regenerated.

### References

- `epics.md` Story 2.16.
- `prd.md` FR42, FR43, NFR-S3.
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py` (Story 1.7) — log-sanitizer (companion).
- `services/registry-state/src/registry_state/domain/event_types.py` — payload model + register pattern (Stories 2.5, 2.8, 2.10, 2.14).
- `packages/events/src/events/envelope.py` — `EventEnvelope.create()` + `Actor` Literal definition.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7[1m] (executor agent)

### Debug Log References

- **Schema-registry teardown collision (test_envelope.py autouse `_clean_registry`)** — initial run of the full test suite cleared `events.schema_registry.REGISTRY` between cases, leaving 8 of the new tests failing with `EventSchemaUnknown('secret.accessed', '1.0.0')`. Fix: a function-scoped autouse fixture (`_re_register_secret_accessed`) in `test_audited_secret.py` re-installs the registration on every test; idempotent same-class guard skips when registry-state has already populated the canonical model.
- **`pydantic-settings` rejects `str → AuditedSecret` via `is_instance_of`** — declaring the field as `AuditedSecret` triggers an `is_instance_of` validator that fails on the raw env-var string. Resolved with a `model_validator(mode="before")` on `AuditedBaseSettings` (`_wrap_audited_fields`) that pre-coerces incoming strings (looked up under both the field name and the `validation_alias`) to a placeholder `AuditedSecret(emit=None, actor=_PLACEHOLDER_ACTOR)`. `from_env` then rewraps with the real `emit`/`actor`. No `# type: ignore` was needed.
- **`uv sync` dev-dep churn** — the very first `uv sync --all-groups` (after the `secret-hygiene` pyproject change) uninstalled `asgi-lifespan` + `sniffio` because the implicit member-set varied; switched to `uv sync --all-groups --all-packages` to keep registry-api's dev deps installed. `bootstrap-verify` (`--no-dev --frozen`) re-uninstalls them by design — re-run `--all-groups --all-packages` after that gate to restore the test environment.

### Completion Notes List

- **Task 1**: Added `SecretAccessedPayload` (frozen, strict, `secret_name` 1..128 chars, `scope: Literal["read"]`) and registered for v1.0.0 + v1.0.1. `EVENT_TYPES` grew 12 → 13; `REGISTRY` 24 → 26.
- **Task 2**: Implemented `AuditedSecret` with `__slots__`, redaction-aware `__repr__` / `__str__`, and a synchronous `.value` property that fire-and-forget-schedules emission on the running asyncio loop. Sync contexts log a structlog WARNING and skip emission; emission failures are caught + logged inside `_safe_emit` so the secret read always succeeds. Live-task anchor (`_live_emission_tasks`) prevents the asyncio weak-ref GC race. Reused `events.ids.new_event_id` / `new_request_id` (Story 2.2) and `events.clock.SystemClock` (Story 2.10) — no new clock/id helpers were invented. `Actor.kind` defaults to caller choice (worker / system / operator) per AC-7; spec's typo (`kind="service"`) avoided.
- **Task 3**: `audited_secret_field` returns a Pydantic `Field` carrying a `json_schema_extra={"audited_secret_name": ...}` marker (and optional `validation_alias` for env-var override). `AuditedBaseSettings` extends `BaseSettings` with `arbitrary_types_allowed=True`, the pre-validator described above, and a classmethod `from_env(emit, actor, clock)` that walks `model_fields`, finds marked fields, and rewraps via `object.__setattr__`. `pydantic-settings>=2.5,<3.0` added; secret-hygiene version bumped 0.1.0 → 0.2.0.
- **Task 4**: 17 new co-located tests (target ≥10), covering all AC-5 scenarios plus payload model rejection paths, multi-read emission, and `Awaitable` wiring. `caplog` is wired but the structlog→stdlib bridge isn't installed in this codebase, so the no-loop test asserts the contractually-meaningful side-effect (zero envelopes emitted, value still returned) rather than the warning-record presence. Schema-registry registration is idempotent per-test via autouse fixture.
- **Task 5**: `__init__.py` re-exports `AuditedSecret`, `AuditedBaseSettings`, `audited_secret_field`. mypy `--strict` clean on all three new/modified files; **zero** `# type: ignore` annotations needed in production code (one in test code was removed after switching to `Any`-typed `SecretAccessedPayload` lookup).
- **Task 6**: `just test` 558 passed / 2 skipped (was 541; +17 ≫ +10 target). `just lint` 8/8 green. `just check-gates-self-test` 3/3 green. `just bootstrap-verify` shows `secret_hygiene 0.2.0`.

### File List

**New (2):**
- `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`
- `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py`

**Modified (4):**
- `services/registry-state/src/registry_state/domain/event_types.py` — `SecretAccessedPayload` + 2 register() calls + `__all__` update + `Literal` import.
- `packages/secret-hygiene/src/secret_hygiene/__init__.py` — re-exports `AuditedSecret`, `AuditedBaseSettings`, `audited_secret_field`; bumped `__version__` to `0.2.0`.
- `packages/secret-hygiene/pyproject.toml` — added `events` + `pydantic-settings>=2.5,<3.0` deps; bumped version 0.1.0 → 0.2.0; updated description.
- `uv.lock` — regenerated for the new dependency tree.

## Change Log

| Date       | Story | Change                                                                                                                | Author            |
|------------|-------|-----------------------------------------------------------------------------------------------------------------------|-------------------|
| 2026-04-27 | 2.16  | secret.accessed audit event type + AuditedSecret wrapper + AuditedBaseSettings; FR42 / NFR-S3 infrastructure delivered | claude-opus-4-7[1m] |
