# Story 2.1: Event envelope + schema registry + canonical serializer

Status: review

## Story

As a **platform service**,
I want **an immutable `EventEnvelope` Pydantic v2 model + a central schema registry + a canonical JSON serializer + a typed-error hierarchy**,
so that **every event across every service has a single shared shape that cannot be mutated after construction and replays deterministically — this is the first real platform code after 11 stories of scaffold, and the architecture's explicitly-flagged high-risk file (`packages/events/envelope.py`: used everywhere, bug corrupts every event)**.

## Acceptance Criteria

1. **AC-1: `packages/events/src/events/envelope.py`** — `EventEnvelope` Pydantic v2 model with `ConfigDict(frozen=True, strict=True)` and full field set:
   - `event_id: str` — UUIDv7 with `e-` prefix (Story 2.2 lands the generator; Story 2.1 validates shape via regex `^e-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).
   - `schema_version: str` — semver pattern `^[0-9]+\.[0-9]+\.[0-9]+$`.
   - `type: str` — dotted lowercase past-tense `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$` (min 2 dotted segments).
   - `emitted_at: datetime` — UTC-aware; `timezone.utc` enforced by validator.
   - `emitted_at_monotonic_ns: int` — nanoseconds (≥ 0).
   - `actor: Actor` — nested frozen model with `kind: Literal["operator", "orchestrator", "worker", "system", "clawhip"]` + `id: str`.
   - `payload: BaseModel | dict[str, Any]` — polymorphic per `(type, schema_version)` via schema registry (see AC-2).
   - `parent_event_id: str | None` — optional; same UUIDv7 shape as `event_id`.
   - `trace_id: str | None` — optional; Phase-1 always `None` per Architecture line 232 (Phase-2 propagates).
   - `request_id: str` — UUIDv7 **without** prefix (raw, per Architecture line 309).
   - Mutation attempt (`env.payload = {...}`) raises Pydantic `ValidationError` / `FrozenInstanceError`.

2. **AC-2: `packages/events/src/events/schema_registry.py`** — upgrade Story-1.6's stub from `REGISTRY: frozenset[str]` to the real `(event_type, schema_version) → payload_model` mapping. Exports:
   - `REGISTRY: dict[tuple[str, str], type[BaseModel]]` — starts EMPTY. Per-story additions populate it (Story 2.4 adds `task.created`, etc.).
   - `EVENT_TYPES: frozenset[str]` — convenience cache of type-names only (any version). Rebuilt from `REGISTRY` at module-load via a `_rebuild_types_cache()` helper. Required by `scripts/check_event_registry.py` which only sees emission-site `type="..."` literals, not versions.
   - `register(event_type: str, schema_version: str, payload_model: type[BaseModel]) -> None` — explicit registration function (used by per-event-type stories); raises if `(type, version)` already bound to a different model.

3. **AC-3: `packages/events/src/events/canonical.py`** — canonical JSON serializer. Exports:
   - `to_canonical_json(envelope: EventEnvelope) -> bytes` — deterministic byte-identical output per Architecture line 359: `sort_keys=True`, `separators=(",", ":")`, UTC Z-suffix ISO 8601 timestamps (millisecond precision per line 360), NaN/Inf disallowed (`allow_nan=False`), UTF-8 encoded.
   - `from_canonical_json(data: bytes) -> EventEnvelope` — round-trip parser using `EventEnvelope.model_validate_json`.
   - Serializing the same envelope twice returns byte-identical results (tested).
   - `allow_nan=False` causes `CanonicalSerializationError` on any NaN/Inf in payload (numeric JSON non-compliance guard).

4. **AC-4: `packages/events/src/events/errors.py`** — typed exception hierarchy:
   - `EventsError(Exception)` — base class.
   - `EventSchemaUnknown(EventsError)` — raised when `EventEnvelope(type=..., schema_version=...)` hits an unregistered `(type, version)` combo. Carries `.event_type`, `.schema_version`, `.registered_types` attrs for downstream `event.unknown_schema` emission (Architecture line 329, PRD FR21 + NFR-O5).
   - `EventValidationError(EventsError)` — wraps Pydantic `ValidationError` for platform-facing error messages.
   - `CanonicalSerializationError(EventsError)` — raised from `canonical.to_canonical_json()` on NaN/Inf / non-serializable payload.
   - All exceptions have clear `str()` output (no raw Pydantic noise).

5. **AC-5: `EventEnvelope.create()` class method** — factory that enforces registry membership:
   ```python
   @classmethod
   def create(cls, type: str, schema_version: str, payload: dict | BaseModel, ...) -> EventEnvelope:
       if (type, schema_version) not in REGISTRY:
           raise EventSchemaUnknown(type, schema_version, EVENT_TYPES)
       # Validate payload against registered model
       model_cls = REGISTRY[(type, schema_version)]
       validated = model_cls.model_validate(payload) if isinstance(payload, dict) else payload
       return cls(type=type, schema_version=schema_version, payload=validated, ...)
   ```
   The direct `EventEnvelope(...)` constructor also validates field shapes but does NOT enforce registry membership (allows construction from canonical-JSON replay + test fixtures via `model_validate_json` without registry round-tripping). AC-2's `REGISTRY` enforcement is ONLY at the `create()` factory — documented explicitly.

6. **AC-6: Co-located unit tests** under `packages/events/src/events/` (Architecture line 344 pattern):
   - `test_envelope.py` — field validators: UUIDv7 shape, datetime UTC enforcement, type-name regex, schema_version regex, actor Literal enum, frozen mutation rejection, round-trip via `model_validate_json`. ~15 tests.
   - `test_canonical.py` — byte-identical serialization of identical envelopes; NaN/Inf rejection; UTC Z-suffix formatting; round-trip `to_canonical_json → from_canonical_json`; sort-keys determinism; key-order stability across Python dict-ordering changes. ~10 tests.
   - `test_schema_registry.py` — `register()` accepts new pairs, rejects duplicate with different model, accepts same-model re-registration (idempotent). `EVENT_TYPES` rebuilds after mutation. ~8 tests.
   - `test_errors.py` — exception classes + `.event_type` / `.registered_types` attrs on `EventSchemaUnknown`; `str()` formatting. ~6 tests.
   - Total: ~40 new tests.

7. **AC-7: `packages/events/pyproject.toml`** — add `pydantic>=2.8` to `[project.dependencies]` (current-stable v2 line with `ConfigDict(frozen=True, strict=True)` support). Regenerate `uv.lock`.

8. **AC-8: `packages/events/src/events/__init__.py`** — re-export the new public surface:
   ```python
   from events.envelope import EventEnvelope, Actor
   from events.schema_registry import REGISTRY, EVENT_TYPES, register
   from events.canonical import to_canonical_json, from_canonical_json
   from events.errors import (
       EventsError,
       EventSchemaUnknown,
       EventValidationError,
       CanonicalSerializationError,
   )
   __version__ = "0.2.0"  # bump from 0.1.0 per first real feature
   ```

9. **AC-9: `scripts/check_event_registry.py` compatibility** — Story 1.6's scanner currently does `from events.schema_registry import REGISTRY` and tests `literal_type in REGISTRY`. With REGISTRY now a `dict[tuple, type]`, `in REGISTRY` checks tuple-keys (not event-type strings). Fix: scanner imports `EVENT_TYPES` instead and tests `literal_type in EVENT_TYPES`. Similarly, the fixture file `scripts/checks/fixtures/events/clean/registry.py` updated to export `EVENT_TYPES = frozenset({"task.created"})` instead of `REGISTRY`. Both changes ship in Story 2.1 as necessary Story-1.6 compatibility.

10. **AC-10: `packages/events/src/events/schema_registry.py` docstring documents the populate-per-story pattern.** Headline sentence: "REGISTRY starts empty; every future story that defines a new event type extends it via `register()` in that story's own test setup OR in a dedicated `packages/events/src/events/types/<event_domain>.py` submodule." The exact split (in-registry vs separate domain-submodule) is deferred — Story 2.1 establishes the `register()` API + empty starting state, and the first real event type lands in Story 2.4 (`task.created` event-log-writer).

11. **AC-11: Regression — all prior story gates green.** `just bootstrap-verify` 13/13 (now includes pydantic indirect dep — expect still 0 leak since pydantic is a runtime dep of `events`, not a dev dep). `just test` count bumps from 75 to ~115 (40 new envelope/canonical/registry/errors tests). `just lint` all 7 sub-commands green. `just migrator-test-additive` 3/3. `just check-gates-self-test` 3/3 (event-registry self-test still passes with the updated scanner).

12. **AC-12: Scan-secrets clean.** New source files + tests use angle-bracket placeholders or clearly-non-matching example values (e.g., `"sk-ant-EXAMPLE"` fails the `{20,200}` length bound).

13. **AC-13: mypy-strict pass.** `mypy --strict` on `packages/events/` must pass. Pydantic v2 has excellent typing — but `EventEnvelope.payload: BaseModel | dict[str, Any]` union requires careful annotation. Use `ClassVar` for class-level constants; avoid `TypeVar` unless justified.

14. **AC-14: Atomic commit.** Single commit titled `feat(events): story 2.1 — EventEnvelope + schema registry + canonical serializer · FR18a FR20 FR21 NFR-O5`. The `feat(events)` prefix — not `chore(scaffold)` — marks this as the first real feature commit of the project.

## Tasks / Subtasks

- [x] **Task 1: `errors.py`** (AC: #4)
  - [x] 4 exception classes with `__init__` preserving context attributes.
  - [x] `str()` overrides for each.

- [x] **Task 2: `schema_registry.py` upgrade** (AC: #2, #9, #10)
  - [x] Replace `REGISTRY: frozenset[str]` stub with `REGISTRY: dict[tuple[str, str], type[BaseModel]]`.
  - [x] Add `EVENT_TYPES: frozenset[str]` cache + `_rebuild_types_cache()`.
  - [x] Add `register(event_type, schema_version, payload_model)` function with dedup-by-identity + raise-on-conflict-model.
  - [x] Docstring documents populate-per-story pattern.

- [x] **Task 3: `envelope.py`** (AC: #1, #5)
  - [x] `Actor` frozen model: `kind: Literal[...]`, `id: str`.
  - [x] `EventEnvelope` frozen model: all 10 fields + field validators.
  - [x] UUIDv7-shape regex for event_id + parent_event_id.
  - [x] `emitted_at` UTC-awareness validator (reject naive datetimes or `tzinfo != UTC`).
  - [x] `type` regex validator.
  - [x] `schema_version` regex validator.
  - [x] `create()` factory method enforcing registry membership via `EventSchemaUnknown`.

- [x] **Task 4: `canonical.py`** (AC: #3)
  - [x] `to_canonical_json(envelope) -> bytes` using `envelope.model_dump_json(...)` with `sort_keys=True`-equivalent behavior (Pydantic v2's `model_dump_json()` doesn't support sort_keys directly — use `model_dump()` + `json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False, default=<datetime-to-iso-Z>)`).
  - [x] Datetime serializer: `2026-04-21T03:02:17.412Z` format (ms precision, `Z` suffix — not `+00:00`).
  - [x] `from_canonical_json(data: bytes) -> EventEnvelope` — `EventEnvelope.model_validate_json(data)`.
  - [x] Wrap any `json.dumps` `ValueError` (NaN/Inf) in `CanonicalSerializationError`.

- [x] **Task 5: `scripts/check_event_registry.py` compatibility** (AC: #9)
  - [x] Change `from events.schema_registry import REGISTRY` to `from events.schema_registry import EVENT_TYPES`.
  - [x] Change `literal_type in REGISTRY` to `literal_type in EVENT_TYPES`.
  - [x] Update fixture at `scripts/checks/fixtures/events/clean/registry.py` to export `EVENT_TYPES = frozenset({"task.created"})` instead of `REGISTRY = frozenset({"task.created"})`.
  - [x] Run `just check-gates-self-test` — event-registry self-test passes.

- [x] **Task 6: `__init__.py` re-exports** (AC: #8)
  - [x] Import + re-export per AC-8 list.
  - [x] Bump `__version__` to `0.2.0`.
  - [x] Regression: `bootstrap-verify` still prints `events 0.2.0` (not 0.1.0).

- [x] **Task 7: `pyproject.toml` — add pydantic dep** (AC: #7)
  - [x] `dependencies = ["pydantic>=2.8"]` in `packages/events/pyproject.toml`.
  - [x] Run `uv lock` — commit the refreshed `uv.lock`.

- [x] **Task 8: Co-located unit tests** (AC: #6)
  - [x] `test_envelope.py` — 15 tests per AC-6 list.
  - [x] `test_canonical.py` — 10 tests.
  - [x] `test_schema_registry.py` — 8 tests.
  - [x] `test_errors.py` — 6 tests.
  - [x] All tests pass via `uv run pytest packages/events/`.

- [x] **Task 9: mypy-strict sanity** (AC: #13)
  - [x] `uv run mypy --strict packages/events/` → no errors.
  - [x] Fix any annotations surfacing (Pydantic v2 payload-union typing is the likely tricky spot).

- [x] **Task 10: Regression + atomic commit** (AC: #11, #12, #14)
  - [x] `just bootstrap-verify` → 13/13 (note: `events 0.2.0` now).
  - [x] `just test` → ~115 passed + 6 skipped.
  - [x] `just lint` → all 7 sub-commands green.
  - [x] `just migrator-test-additive` → 3/3.
  - [x] `just check-gates-self-test` → 3/3.
  - [x] Single atomic commit per AC-14 title.

## Dev Notes

### Architecture patterns for this story

- **Frozen envelope** (Architecture line 401). Pydantic v2's `ConfigDict(frozen=True)` + `strict=True` rejects coercion + post-construction mutation. Strict mode also means `"1"` (string) won't auto-cast to `1` (int) — all fields must be the right type from the start.
- **Canonical JSON** (Architecture line 359). `sort_keys=True` + `separators=(",", ":")` + no whitespace + UTF-8 + `allow_nan=False`. Two identical envelopes MUST serialize to identical bytes — this is THE replay-determinism guarantee the whole platform rests on.
- **`(type, schema_version)` keyed registry** (PRD line 670). Separator choice: tuple, not string concatenation. Keeps versions strictly separated.
- **Typed event names** (Architecture line 327-329). Past tense, dotted, lowercase, min 2 segments. Enforced at field-validator level.
- **Actor is a Literal-typed kind + id** (Architecture line 393). Literal enum keeps the 5 canonical actor types tight: operator / orchestrator / worker / system / clawhip.
- **UUIDv7 shape validation only** (Story 2.2 lands real generation). Story 2.1 uses regex validation so real generators (`new_event_id()`, `new_task_id()` etc.) work — but Story 2.1 doesn't ship those generators yet. Tests use hard-coded UUIDv7-shaped literals.

### What this story does NOT do

- `packages/events/src/events/ids.py` — UUIDv7 generator. **Story 2.2.**
- `packages/events/src/events/clock.py` — injectable clock. **Story 2.2.**
- Per-event-type payload models (e.g., `TaskCreatedPayload`). Each lands in the story that owns the event type — Story 2.4 owns `task.created`, Story 3.x owns command events, etc.
- `event.unknown_schema` runtime emission — that's Story 2.4's event-log writer responsibility (Story 2.1 only defines the exception type).
- Registry HTTP API endpoints — Story 2.9.
- SQLAlchemy schema for events table — Story 2.3.
- Event log writer (JSONL append) — Story 2.4.
- Snapshot capture + replay — Story 2.6.

### Source tree components to touch

```
oh-my-bmad/
├── packages/events/
│   ├── pyproject.toml                         # Task 7 MODIFIED (+ pydantic dep)
│   └── src/events/
│       ├── __init__.py                        # Task 6 MODIFIED (re-exports + version bump)
│       ├── envelope.py                        # Task 3 NEW
│       ├── schema_registry.py                 # Task 2 MODIFIED (stub → real)
│       ├── canonical.py                       # Task 4 NEW
│       ├── errors.py                          # Task 1 NEW
│       ├── test_envelope.py                   # Task 8 NEW
│       ├── test_canonical.py                  # Task 8 NEW
│       ├── test_schema_registry.py            # Task 8 NEW
│       └── test_errors.py                     # Task 8 NEW
├── scripts/
│   ├── check_event_registry.py                # Task 5 MODIFIED (REGISTRY → EVENT_TYPES)
│   └── checks/fixtures/events/clean/registry.py  # Task 5 MODIFIED (REGISTRY → EVENT_TYPES)
└── uv.lock                                    # regenerated (pydantic added)
```

**Files: 4 new + 4 modified + `uv.lock` regen. First real feature commit.**

### Pydantic v2 specifics

- `ConfigDict(frozen=True, strict=True, extra="forbid", populate_by_name=True)` — full lockdown.
- Field-level `@field_validator` for regex + timezone checks.
- `model_dump(mode="json")` returns JSON-serializable dict — combine with `json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False)` for canonical bytes.
- `model_validate_json(bytes)` for parsing — includes all regex validators.
- `model_config` vs Config class — always use `ConfigDict`.

### Canonical datetime serialization

Pydantic v2 default ISO format uses `+00:00` suffix. Architecture line 360 requires `Z`. Override:

```python
def _datetime_to_iso_z(dt: datetime) -> str:
    """Serialize UTC-aware datetime to ISO 8601 with millisecond precision + Z suffix."""
    if dt.tzinfo != timezone.utc:
        raise ValueError(f"datetime must be UTC-aware, got {dt.tzinfo}")
    # Strip microseconds beyond millisecond precision
    ms = dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
    # isoformat() with timespec="milliseconds" produces YYYY-MM-DDTHH:MM:SS.sss+00:00
    # Replace +00:00 → Z
    return ms.isoformat(timespec="milliseconds").replace("+00:00", "Z")
```

Used as the custom serializer in `to_canonical_json` — pass `default=_default_encoder` to `json.dumps` where the encoder handles datetime (and UUIDs if they're not already strings).

### Schema-registry populate-per-story pattern

When Story 2.4 adds `task.created`, it will:
1. Define `TaskCreatedPayload(BaseModel)` in `services/registry-state/src/registry_state/events/task_created.py` (or wherever the domain lives).
2. Call `register("task.created", "1.0.0", TaskCreatedPayload)` at module-import or in a dedicated `registrations` module.
3. Story 2.1's `schema_registry.py` stays generic — it holds the registry but doesn't know which types will populate it.

This keeps Story 2.1's scope tight (infrastructure only) + avoids back-and-forth when each event-type story lands.

### Previous Story Intelligence

- **Story 1.6** check_event_registry.py currently reads `REGISTRY`. AC-9 fixes the scanner + fixture to read `EVENT_TYPES`. This is a **mandatory coupled change** — without it, Story 1.6's gate breaks.
- **Story 1.7** secret-hygiene scanner — Story 2.1's tests use dummy tokens (e.g., `"sk-ant-EXAMPLE"` with 7 chars, below the `{20,200}` length threshold) + dummy UUIDs (`"e-01HY..."` literal strings) — no real secrets.
- **Story 1.8** mypy-strict scope (`packages/` + `services/registry-*`) — Story 2.1 lives in `packages/events/`, so `mypy --strict` fires on every new file. Plan for it.
- **Story 1.10b testing-guide.md** — the "writing a unit test" example in this doc now has real referent: Story 2.1 ships 40 co-located tests following exactly that pattern.
- **Story 1.10b message-design.md** — cites `fixed_clock` fixture "arrives Story 2.1/2.2". Story 2.1 doesn't ship the fixture (that's 2.2); docstring clarity important.

### Git Intelligence

- `93f072a docs(story-1-10b): finalize + mark done · Epic 1 closed`
- `429f53c docs(story-1-10b): apply code-review fixes · all severities`
- `ddbf9f3 docs(story-1-10b): finalize story file + mark review`
- `a8efa01 docs(story-1-10b): full operator documentation set · NFR-M7`

Cadence: docs story → 4 commits. Story 2.1 is the first feature commit — expect 4-commit cadence (scaffold → review → fix → finalize) per established pattern.

### Latest Tech Information

- **Pydantic 2.8+**: current stable with `ConfigDict(frozen=True, strict=True)` + `model_validate_json` + `model_dump(mode="json")`. Frozen + strict is the full lockdown.
- **`datetime.isoformat(timespec="milliseconds")`**: Python 3.6+.
- **`json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False)`**: stdlib; `allow_nan=False` raises on NaN/Inf — necessary for strict JSON compliance.
- **`mypy --strict` + Pydantic 2**: `model_config` typed as `ConfigDict`; `Field(...)` usage returns `Any` in strict mode — annotate fields explicitly via `: <Type> = Field(...)`.

### References

- `epics.md` §Epic 2 / Story 2.1 (lines 672–691) — ACs source.
- `architecture.md` lines 232 (trace_id reserved Phase-1), 259 (blocking dependency), 291 (PascalCase classes), 308-310 (UUIDv7 everywhere), 327-331 (event naming), 343-347 (co-located tests), 355-364 (JSON format), 384-401 (envelope shape).
- `prd.md` FR18a (emission via MCP — line 837), FR20 (persist to event log — line 840), FR21 (schema_version + unknown_schema — line 841), NFR-O5 (event-schema integrity — line 936).
- `1-6-ci-gates-imports-events-single-writer.md` — stub `REGISTRY: frozenset[str]` that Story 2.1 upgrades.
- `1-7-secret-scanner-sanitizer.md` — scan-secrets conventions for test fixtures.
- `1-10b-full-operator-docs.md` — `docs/schema-evolution.md` describes how future stories extend the registry.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Opus 4.7** — this is the highest-risk file in the project ("bug corrupts every event"). Pydantic v2 typing + canonical serialization determinism + frozen-model invariants reward deep reasoning. Not a mechanical scaffold task.

### Debug Log References

_Placeholder._

### Completion Notes List

_To be filled. Per AC pass/fail + evidence. Record actual pydantic version locked + actual test count delta + any mypy-strict quirks._

### File List

_To be filled. Expected: 4 new + 4 modified + uv.lock regen._

### Change Log

- **2026-04-24:** Story 2.1 implemented — **first real feature commit** of the project. Atomic scaffold commit `37fede8` (15 files changed, 1150+/21-). Pydantic 2.13.3 locked; `events 0.2.0` on bootstrap. Platform test count 75 → 132 (+57 new tests across errors/registry/envelope/canonical). mypy --strict scope grew 14 → 21 source files; all green. Deviations documented in commit: `mypy_path` config (first multi-file package surfaced the gap); `model_dump(mode="python")` for canonical (JSON mode coerces NaN); dict-first union payload; `# noqa: N818` on EventSchemaUnknown. Status: `ready-for-dev` → `in-progress` → `review`.
