# Story 2.16: secret.accessed audit event emission

Status: done

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

#### Review-fix pass (2026-04-28)

- **High (7)**: H1 added `__get_pydantic_core_schema__` so pydantic `model_dump`/`model_dump_json` redact via `<REDACTED:secret_name>` instead of leaking `_value`; H2 wrapped `loop.create_task` in `try/except RuntimeError` (closed-loop) with WARNING log + value still returned; H3/H4 `from_env` now raises `ValueError` for empty-string and `None` env-var resolutions (fail-fast UX); H5 introduced `_UNCONFIGURED_ACTOR` sentinel and a per-instance once-flag warning fired from `.value` when bare `MySettings()` is read without `from_env`; H6 added `flush_pending_emissions(timeout)` module helper + re-export for graceful service shutdown; H7 swapped the test-time conditional registry lookup for a deterministic `_LocalSecretAccessedPayload` binding with `contextlib.suppress(ValueError)`-wrapped registration (xdist-safe).
- **Medium (10)**: M1 `_safe_emit` now lets `KeyboardInterrupt`/`SystemExit`/`asyncio.CancelledError` propagate (broad `except Exception` retained for the rest); M2 pre-validator handles `AliasChoices(...)` (string members enumerated, non-string members like `AliasPath` skipped); M3 introduced `_AUDITED_FIELD_MARKER` sentinel — foreign code with the dict key but no marker is no longer wrapped; M4 `_UNSET` sentinel distinguishes "no default" (required) from `default=None` (literal None default, optional field); M5 replaced 3-yield `_drain` with deterministic `asyncio.gather(*_live_emission_tasks)` helper; M6 switched operational paths to stdlib `logging.getLogger` so `caplog` captures records (test now asserts WARNING record); M7 emission-failure test now asserts ERROR record presence + message contents; M8 `__pydantic_init_subclass__` enforces `secret_name` uniqueness across audited fields (fail-fast at class definition); M10 `ContextVar`-based re-entrant emission guard prevents unbounded fan-out when an emit callable itself reads an `AuditedSecret`; M11 `_live_emission_tasks` is now a `weakref.WeakSet` (auto-cleans GC'd tasks); M13 module + `from_env` docstrings document the `model_validator(mode='after')` rewrap caveat; M14 added 100-coroutine concurrent-read stress test asserting distinct envelopes + unique event IDs.
- **Low (14)**: L2 docstring clarifies why the factory return is `Any` (matches Pydantic's own `Field()` to keep `x: AuditedSecret = audited_secret_field(...)` mypy-clean for users); L3 stale `# type: ignore` paragraph removed; L5 `format(s, "")` assertion locks down the `__format__` path; L6/L19/L20 `_validate_secret_name` regex `[A-Za-z0-9._-]{1,128}` enforced at both `AuditedSecret.__init__` and `audited_secret_field` declaration; L7 added `AuditedSecret.unwrap_for_rewrap()` (named, documented internal API; `# noqa: SLF001` removed); L9 redundant `Awaitable` import dropped from test file; L10 `SettingsConfigDict` comment clarifies `BaseSettings` defaults to `extra="ignore"`; L15 direct-ctor empty-string allowed test added; L16 `from_env(**overrides)` forwards through to `cls(**overrides)`; L17 done-callback retrieves `task.exception()` defensively before discard; L18 pre-validator detects multi-key conflict and raises `ValueError` (wrapped by Pydantic into `ValidationError`); L23 `contextlib.suppress(ValueError)` around `register(...)` for xdist race + canonical-class coexistence; L24 `clock=None` default monotonicity test added; L25 GC-anchor invariant test (force `gc.collect()` mid-flight, then flush).
- **Touched files**: production `audited_secret.py` (rewritten), `__init__.py` (re-exports `flush_pending_emissions`); tests `test_audited_secret.py` (rewritten with 28 net-new tests); story doc + sprint-status. **Zero** changes to `event_types.py`, registry-api, registry-state, clawhip-bridge, worker-wrapper sources (separability honored). `pyproject.toml` / `uv.lock` unchanged. `secret_hygiene.__version__` stays at `0.2.0` per directive.
- **Verification**: `just lint` 8/8 green; `just check-gates-self-test` 3/3 green; `just bootstrap-verify` shows `secret_hygiene 0.2.0`; full test suite **582 passed / 2 skipped** (was 558; +24 net, exceeds +13 minimum). The single remaining `tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged` failure observed pre-commit is transitive — the test inspects `git diff HEAD~1..HEAD` which currently captures Story 2.16's own `event_types.py` touch on commit 669327b; once the review-fix commit lands, HEAD~1 becomes 669327b and the diff returns clean (no spine touches in this commit).
- **Judgment calls**: (a) H1 implemented via `pydantic_core.core_schema.no_info_plain_validator_function` + `plain_serializer_function_ser_schema(when_used="always")`; the validator is identity (passthrough) since pydantic-settings constructs `AuditedSecret` instances via the pre-validator path, never via cold-start string→AuditedSecret coercion. (b) M1 ordered the explicit `except (KeyboardInterrupt, SystemExit, asyncio.CancelledError): raise` BEFORE the broad `except Exception` (per Python control-flow propagation contract); the broad `except Exception` does not catch the critical control-flow exceptions anyway, so the explicit form is purely documentary but kept for clarity. (c) L2 reverted to `Any` return after observing that Pydantic's own `Field()` returns `Any` for the same `x: SomeType = Field(...)` model-body idiom — returning `FieldInfo` would force every downstream call site (including services adopting `AuditedBaseSettings`) to add `# type: ignore[assignment]`, a worse outcome than the original "Any" annotation; the docstring now explicitly explains the choice. (d) M8 used `__pydantic_init_subclass__` (not stdlib `__init_subclass__`) because Pydantic's metaclass populates `model_fields` AFTER `type.__new__` calls the standard hook.

### File List

**New (2):**
- `packages/secret-hygiene/src/secret_hygiene/audited_secret.py`
- `packages/secret-hygiene/src/secret_hygiene/test_audited_secret.py`

**Modified (4):**
- `services/registry-state/src/registry_state/domain/event_types.py` — `SecretAccessedPayload` + 2 register() calls + `__all__` update + `Literal` import.
- `packages/secret-hygiene/src/secret_hygiene/__init__.py` — re-exports `AuditedSecret`, `AuditedBaseSettings`, `audited_secret_field`, `flush_pending_emissions` (review-fix added the helper); bumped `__version__` to `0.2.0`.
- `packages/secret-hygiene/pyproject.toml` — added `events` + `pydantic-settings>=2.5,<3.0` deps; bumped version 0.1.0 → 0.2.0; updated description.
- `uv.lock` — regenerated for the new dependency tree.

**Review-fix added public surface:**
- `flush_pending_emissions(timeout: float = 1.0)` — module-level async helper for graceful service shutdown (drain in-flight emissions). Re-exported from `secret_hygiene/__init__.py`.
- `AuditedSecret.unwrap_for_rewrap() -> str` — internal-only typed accessor used by `AuditedBaseSettings.from_env` to rewrap placeholder wrappers without `# noqa: SLF001` private-attr access.
- `_AUDITED_FIELD_MARKER`, `_UNCONFIGURED_ACTOR`, `_UNSET` — module-private sentinels (single-underscore prefix; not exported).

### Review Findings

Three-layer adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) against commit `669327b` produced 7 High / 10 Med / 14 Low after deduplication. All classified as `[Patch]` per user directive ("fix all issues even minors").

**High severity**

- [x] [Review][Patch] H1: `model_dump()` / `model_dump_json()` leaks secret value (only `__repr__` overridden) [packages/secret-hygiene/src/secret_hygiene/audited_secret.py]
- [x] [Review][Patch] H2: Closed-loop `loop.create_task(...)` raises `RuntimeError` and breaks `.value` "always returns" invariant [audited_secret.py:_schedule_emission]
- [x] [Review][Patch] H3: Empty-string env-var (`""`) silently wrapped as a "valid" secret with audit trail [audited_secret.py:from_env]
- [x] [Review][Patch] H4: `from_env` walk wraps `None` as literal `"None"` string when raw_value is `None` [audited_secret.py:from_env]
- [x] [Review][Patch] H5: Bare `MySettings()` (without `from_env`) silently emits nothing on `.value` reads — placeholder actor sentinel must trigger loud warning [audited_secret.py:AuditedBaseSettings]
- [x] [Review][Patch] H6: No `flush_pending_emissions()` helper — in-flight emission tasks dropped at service shutdown [audited_secret.py]
- [x] [Review][Patch] H7: Test-time conditional schema lookup creates non-deterministic class binding under different test orderings / xdist [test_audited_secret.py:903-950]

**Medium severity**

- [x] [Review][Patch] M1: `_safe_emit` catches `Exception`, not `BaseException` — `CancelledError` / `KeyboardInterrupt` / `SystemExit` propagate inconsistently [audited_secret.py:_safe_emit]
- [x] [Review][Patch] M2: Pre-validator only handles `isinstance(alias, str)` — `AliasChoices` / `AliasPath` ignored [audited_secret.py:_wrap_audited_fields]
- [x] [Review][Patch] M3: `from_env` field walk over-eager — any field with `audited_secret_name` extra wraps; tighten via sentinel marker class or annotation check [audited_secret.py:from_env]
- [x] [Review][Patch] M4: `audited_secret_field(default=None)` confusing — `None` means "required", no way to specify literal `None` default; use `_UNSET` sentinel [audited_secret.py:audited_secret_field]
- [x] [Review][Patch] M5: `_drain()` test helper uses 3 yields with mismatched comment — replace with deterministic `await asyncio.gather(*_live_emission_tasks)` [test_audited_secret.py:_drain]
- [x] [Review][Patch] M6: `test_no_loop_skips_emission_with_warning` does NOT actually assert the WARNING (AC-5 deviation); switch the no-loop log path to stdlib `logging` and assert via `caplog` [audited_secret.py + test_audited_secret.py]
- [x] [Review][Patch] M7: `test_emission_failure_does_not_propagate` sets `caplog.at_level(ERROR)` but never asserts a record was captured [test_audited_secret.py]
- [x] [Review][Patch] M8: Two audited fields sharing the same `secret_name` produce indistinguishable audit events — validate uniqueness [audited_secret.py:AuditedBaseSettings.__init_subclass__]
- [x] [Review][Patch] M10: Re-entrant secret read inside `emit` causes unbounded task fan-out — add `ContextVar` guard [audited_secret.py:_schedule_emission]
- [x] [Review][Patch] M11: `_live_emission_tasks` not loop-scoped — multi-loop processes leak; switch to `weakref.WeakSet` [audited_secret.py]
- [x] [Review][Patch] M13: `from_env` rewrap bypasses subclass `model_validator(mode="after")` — document limitation in docstring [audited_secret.py:from_env]
- [x] [Review][Patch] M14: No test for concurrent `.value` reads from many coroutines (stress) [test_audited_secret.py]

**Low severity**

- [x] [Review][Patch] L2: `audited_secret_field` returns `Any` — tighten to `pydantic.fields.FieldInfo` [audited_secret.py:audited_secret_field]
- [x] [Review][Patch] L3: Stale `# type: ignore` paragraph in `AuditedBaseSettings` docstring (no such ignores exist) [audited_secret.py]
- [x] [Review][Patch] L5: `test_repr_does_not_leak_via_format` redundant `!s` branch (since `__str__ = __repr__`); replace with a `__format__`/`format(s, "")` test [test_audited_secret.py]
- [x] [Review][Patch] L6: No negative test asserting `_build_envelope` rejects invalid `secret_name` [test_audited_secret.py]
- [x] [Review][Patch] L7: Private-attr access `raw_value._value` from `from_env` — add `unwrap_for_rewrap()` method [audited_secret.py]
- [x] [Review][Patch] L9: `Awaitable` redundant import in test [test_audited_secret.py]
- [x] [Review][Patch] L10: Misleading `extra="forbid"` comment in `SettingsConfigDict` block — `BaseSettings` defaults to `ignore` [audited_secret.py]
- [x] [Review][Patch] L15: Add test for direct `AuditedSecret(value="", ...)` construction (empty allowed in direct ctor; rejected only by from_env) [test_audited_secret.py]
- [x] [Review][Patch] L16: `from_env()` doesn't forward `**overrides` to `cls(**overrides)` [audited_secret.py:from_env]
- [x] [Review][Patch] L17: Done-callback should call `task.exception()` defensively to mark exception retrieved [audited_secret.py:_schedule_emission]
- [x] [Review][Patch] L18: Pre-validator silently uses first match when both `field_name` and `alias` are in input — detect collision and raise [audited_secret.py:_wrap_audited_fields]
- [x] [Review][Patch] L19: `secret_name` accepts non-ASCII / control chars — enforce `[A-Za-z0-9._-]+` [audited_secret.py:SecretAccessedPayload + AuditedSecret] (emit-side; payload model is in registry-state — apply at AuditedSecret construction)
- [x] [Review][Patch] L20: `audited_secret_field` doesn't validate `secret_name` length / emptiness at field-creation (fail-fast UX) [audited_secret.py:audited_secret_field]
- [x] [Review][Patch] L23: xdist race possible on schema registration — wrap in `try/except ValueError` [test_audited_secret.py]
- [x] [Review][Patch] L24: No test for `clock=None` default branch (real `SystemClock` monotonicity) [test_audited_secret.py]
- [x] [Review][Patch] L25: No test for `_live_emission_tasks` reference-anchor invariant (`gc.collect` between schedule + drain) [test_audited_secret.py]

**Dismissed (not patched)**

- [x] [Review][Defer] AC-2 `__slots__` additive `_clock` parameter — purely additive; spec snippet uses `...` ellipsis. No fix.
- [x] [Review][Defer] AC-12 future check_event_registry scanning `.create()` — speculative future change, out of scope.
- [x] [Review][Defer] L26 `SecretAccessedPayload` 1.0.1 registration "dead" — matches Story 2.14 additive-version pattern, intentional.
- [x] [Review][Defer] AC-1 `EVENT_TYPES` count comment — vacuously satisfied (computed collection), not a code defect.
- [x] [Review][Defer] sprint-status `last_updated` regression observed in pre-fix snapshot — was an external file edit, not the commit's responsibility.

## Change Log

| Date       | Story | Change                                                                                                                | Author            |
|------------|-------|-----------------------------------------------------------------------------------------------------------------------|-------------------|
| 2026-04-27 | 2.16  | secret.accessed audit event type + AuditedSecret wrapper + AuditedBaseSettings; FR42 / NFR-S3 infrastructure delivered | claude-opus-4-7[1m] |
| 2026-04-28 | 2.16  | review-fix pass: 7 High / 10 Med / 14 Low addressed; +24 tests; serializer redaction, sentinel-marker field walk, weakref task anchor, BaseException-aware `_safe_emit`, `flush_pending_emissions` helper, secret_name regex validation, ContextVar re-entrant guard, AliasChoices support, `_UNSET` default sentinel, deterministic test schema-binding, etc. | claude-opus-4-7[1m] |
