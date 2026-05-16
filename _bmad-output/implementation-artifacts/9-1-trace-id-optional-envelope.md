# Story 9.1 — `trace_id` as optional on the envelope + deprecation warning

Status: **done**

## Story

**As** the Phase 2 distributed-tracing kernel that lets every event in the JSONL log correlate back to the originating operator command,
**I want** the `EventEnvelope` to harden its already-present `trace_id: str | None` field with shape validation (UUIDv7 or `tg:<update_id>`), a deprecation warning when the field is None at construction time, and round-trip-stable canonical-JSON serialization,
**so that** Stories 9.2 – 9.6 can wire each entry point (HTTP middleware, Telegram AllowlistMiddleware, console CLI, MCP tool handlers, worker subprocess flag) to populate the field without re-litigating the field shape or the validation contract, and Story 9.7 can flip the field to non-optional + bump `schema_version` 1.0.0 → 1.1.0 in a strictly additive change.

This is the foundation story of Epic 9 (α `trace_id` propagation kernel). Every later story in the epic depends on the validation contract this story locks in. The field itself is **already in the code** (`packages/events/src/events/envelope.py:169` for the model and `:322` for the `create()` kwarg — Phase 1 reserved it per Architecture §"Cross-Cutting Concerns" line-401); 9.1's job is to add the missing validation, deprecation-warning, and test coverage so the field is **load-bearing** for the rest of Epic 9.

---

## Acceptance criteria

### AC1 — `trace_id` shape validation (UUIDv7 OR `tg:<digits>`)

Add a `field_validator("trace_id")` on `EventEnvelope` that, when the field is not `None`, rejects values that don't match ONE of these two anchored forms:

| Form | Regex | Origin |
|---|---|---|
| Bare UUIDv7 | `\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z` (same as `_UUIDV7_BARE_RE` at line 134) | Stories 9.2 (HTTP `X-Trace-Id`), 9.4 (console), 9.5 (MCP `caller_trace_id`), 9.6 (worker `--trace-id`) |
| Telegram-derived | `\Atg:[1-9][0-9]{0,18}\Z` + post-match int64-ceiling check (NEW regex; tightened in pass-1 review: leading-zero rejection F2 + int64 ceiling F3) | Story 9.3 — `f"tg:{update.update_id}"`; `update_id` is a positive signed 64-bit (1 ≤ n ≤ 9_223_372_036_854_775_807) |

The error message must be specific: `trace_id must be a bare UUIDv7 OR match \\Atg:<update_id>\\Z (got {v!r})` for shape failures, and `trace_id Telegram update_id out of range (max 9223372036854775807); got <v>` for int64-overflow failures. Validation runs in Pydantic `mode="after"` (explicit, locked in pass-2 review) so coercion has already happened.

Reject (with `ValidationError`):
- empty string
- the `e-` prefix form used by `event_id` (`event_id`/`parent_event_id` carry the prefix; `trace_id` does NOT — same convention as `request_id`)
- any string with leading/trailing whitespace
- `tg:` with no digits
- `tg:` with non-digit chars
- UUIDv4 (version nibble `4` instead of `7`)
- `tg:` followed by 20+ digits (overflows Telegram's signed 64-bit `update_id`)

### AC2 — `DeprecationWarning` on absent `trace_id`

When `EventEnvelope.create(...)` is called WITHOUT `trace_id=` (or with `trace_id=None`), emit a `DeprecationWarning`. Pass-2 review (finding F6) stripped sprint-specific tokens from the user-facing text; the current message:

```
EventEnvelope created without trace_id; this field will be required in a future schema_version bump. Pass trace_id= (UUIDv7 or 'tg:<update_id>') to silence this warning.
```

The warning is emitted from `EventEnvelope.create()` (NOT from `EventEnvelope.__init__` directly — replay from the JSONL log must NOT emit this warning, since 1.0.0 envelopes legitimately have null `trace_id` and re-parsing them is not a deprecation event).

Use `stacklevel=2` so the warning points at the caller, not at the factory. Emit via `warnings.warn(...)` — do NOT use `logging.warning`.

### AC3 — Canonical-JSON round-trip stability

`to_canonical_json(env)` for envelopes WITH and WITHOUT `trace_id` must remain byte-stable across:

- envelope with `trace_id=None` → produces `"trace_id":null,` segment (already verified by `test_canonical.py:199`)
- envelope with `trace_id="<uuidv7>"` → produces `"trace_id":"<uuidv7>",` segment
- envelope with `trace_id="tg:12345"` → produces `"trace_id":"tg:12345",` segment

The canonical encoder must emit `trace_id` as the LAST field before `type` in alphabetical order (current behavior — already alphabetical between `schema_version` and `type` at line 199). NO encoder changes needed; this AC is the regression assertion.

`from_canonical_json(canonical) == env` for all three cases.

### AC4 — Schema-version unchanged

`schema_version` remains `"1.0.0"`. The schema-registry's canonical version stays at `"1.0.0"`. NO new registry entries are added in 9.1. Story 9.7 owns the 1.0.0 → 1.1.0 bump.

This AC is a **negative invariant**: grep for `1.1.0` in `packages/events/` and `services/registry-state/src/registry_state/domain/event_types.py` must return zero matches at end of 9.1.

### AC5 — Unit test coverage (≥12 tests)

New test file or new test class in `packages/events/src/events/test_envelope.py` covering:

1. `trace_id=None` builds (existing default)
2. `trace_id=<uuidv7>` builds
3. `trace_id="tg:1"` builds (boundary — smallest int)
4. `trace_id="tg:9223372036854775807"` builds (boundary — max signed 64-bit)
5. `trace_id="tg:"` rejected (no digits)
6. `trace_id="tg:abc"` rejected (non-digits)
7. `trace_id="tg:12345678901234567890"` rejected (20 digits — overflow)
8. `trace_id="e-<uuidv7>"` rejected (event_id-style prefix not allowed)
9. `trace_id=<uuidv4>` rejected (version nibble wrong)
10. `trace_id=""` rejected (empty)
11. `trace_id=" <uuidv7>"` rejected (leading whitespace)
12. Deprecation warning fires when `create()` called without `trace_id`
13. Deprecation warning does NOT fire when `create()` called with `trace_id=<valid>`
14. Deprecation warning does NOT fire on `EventEnvelope.model_validate_json(...)` replay (re-parsing 1.0.0 logs is NOT deprecation)
15. Canonical round-trip: dict→envelope→canonical→envelope→canonical equality with `trace_id` set
16. Canonical round-trip: same with `trace_id=None`

Each test follows the existing `_make_envelope(**overrides)` helper pattern. Tests use `pytest.warns(DeprecationWarning)` and `pytest.raises(ValidationError, match=...)` consistently with the file's existing style.

### AC6 — `_UUIDV7_BARE_RE` reuse

Do NOT introduce a second UUIDv7 regex constant. The validator MUST reuse the existing `_UUIDV7_BARE_RE` (line 129) — DRY discipline. The NEW regex is the `tg:<update_id>` form, introduced as `_TRACE_ID_TELEGRAM_RE` (module-private) alongside the other patterns.

### AC7 — mypy --strict clean

`uv run mypy --strict packages/events services/registry-api services/registry-state` exits 0 over the 97 source files (Epic 8.7 baseline). No new ignores. The `field_validator` decorator + classmethod pattern follows the existing validators at lines 196 – 254 — copy-paste safe.

### AC8 — Backwards-compat existing test suite

`uv run pytest packages/events services/registry-state services/registry-api -m "not slow"` exits 0 with no new failures (Epic 8.7 baseline: 2376 tests CI / 2189 local). The 71-test secret-hygiene + 240-test secret-hygiene-package suites are NOT affected by this story. Existing trace_id usages at:

- `services/clawhip-daemon/src/.../test_telegram_sink.py:3033` (`trace_id=None`)
- `services/registry-state/src/registry_state/test_event_log.py:104,126,232,244` (test fixture default)
- `services/registry-api/src/registry_api/test_events.py:249,386,475,484` (`assert trace_id is None`)

… continue to pass without modification. The new validation rejects ill-shaped values but accepts `None` (the universal current state).

---

## Developer context

### Existing state (what's already in code)

The field is **already declared** in three places — your job is to harden it, not add it:

| File:line | Current state | What this story changes |
|---|---|---|
| `packages/events/src/events/envelope.py:169` | `trace_id: str \| None = None` (field declaration) | Add `field_validator("trace_id")` below the existing `_request_id_shape` validator (line 212) |
| `packages/events/src/events/envelope.py:322` | `trace_id: str \| None = None` (in `create()` factory signature) | Wrap the body in `warnings.warn(DeprecationWarning(...), stacklevel=2)` when `trace_id is None`, BEFORE the registry lookup at line 336 |
| `packages/events/src/events/envelope.py:358` | `trace_id=trace_id` (passed to `cls(...)` constructor) | No change — the constructor path handles validation via the new validator |

The validator file pattern is established — see `_event_id_shape` (196), `_parent_event_id_shape` (203), `_request_id_shape` (212). Copy that shape exactly for `_trace_id_shape`. Reuse `_UUIDV7_BARE_RE` (129) for the UUIDv7 branch.

### Architecture compliance

- **Architecture §"Envelope schema migration: 1.0.0 → 1.1.0"** mandates this story is **additive** — no fields modified, renamed, or type-changed. `trace_id` was reserved in Phase 1; this story binds it.
- **Architecture invariant P2-I2** (single Phase 2 schema bump): 9.1 ships with `schema_version="1.0.0"`; only 9.7 bumps to 1.1.0.
- **NFR-M3** (additive-only schema evolution): the new validator REJECTS ill-shaped non-null values, but the field's default + behavior with `None` is unchanged from Phase 1.
- **NFR-O7** (`trace_id` correlation contract): 9.1 makes the contract operationally testable; 9.2 – 9.7 make it operationally true.

### Library / framework requirements

| Library | Version | Source |
|---|---|---|
| Pydantic | v2 (current `pyproject.toml` constraint via `events` package) | `packages/events/pyproject.toml` |
| pytest | already in workspace dev group | `pyproject.toml:52` |
| stdlib `warnings` | Python 3.12 | already imported indirectly via PYTHONFAULTHANDLER context |

No new deps. The `DeprecationWarning` class is stdlib.

### File-structure requirements

| File | Change | Why |
|---|---|---|
| `packages/events/src/events/envelope.py` | Edit — 1 new constant (`_TRACE_ID_TELEGRAM_RE`), 1 new validator method (`_trace_id_shape`), 1 warnings.warn block in `create()` | Single source of truth for envelope shape |
| `packages/events/src/events/test_envelope.py` | Edit — new test class (`class TestTraceIdShape:`) with ≥12 tests (AC5) | Mirror the existing `TestEventIdShape`, `TestRequestIdShape` patterns at lines 79 – 130 |

Do **not** touch:
- `packages/events/src/events/types/` — no new event types in 9.1
- `services/registry-state/src/registry_state/domain/event_types.py` — no registration changes
- `services/registry-api/src/registry_api/routes/events.py` — line 49's `"trace_id": None` stays; the materializer wiring is Story 9.7's responsibility
- Any service code — entry-point wiring is Stories 9.2 – 9.6

### Testing requirements

- **Unit tests in the package** (`packages/events/src/events/test_envelope.py`) — same module pattern as existing
- **No integration tests in this story** — wiring tests belong to 9.2 – 9.6
- **No new contract tests** — the `tests/contract/` round-trip update is Story 9.7's concern (after the 1.1.0 bump)
- Test markers: tests are PR-gate tests (not `@pytest.mark.slow`)
- Hypothesis property tests OPTIONAL: a `@given(st.text())` strategy asserting that `validate_trace_id(s)` raises iff `s` doesn't match the union pattern would be ~10 lines and useful, but not required by AC5

### Test isolation discipline (Epic 8.7 L2 carry-over)

`test_envelope.py` already has an autouse `_clean_registry` fixture at lines 51 – 56 that calls `unregister_all()`. The new tests inherit this. **Do NOT add module-level `register()` calls** outside `ensure_registered()` patterns in any file — Epic 8.7 burned ~5 commits on this trap. (See `epic-8-7-retro-2026-05-16.md` lesson L2.)

The `_clean_registry` fixture explicitly re-registers `task.created` → `_TaskPayload` for each test. If your new tests need other event types in the registry, register them inside the test function, not at module scope.

### Previous-story intelligence

Closest analogues:

- **Story 8.6** (`deployment.signature_rejected` event + CLI helper, commit `3da2217 + f948387`): pioneered the `packages/events/src/events/types/<domain>.py` registration pattern. Field-validator style + anchored regexes + ConfigDict(frozen, strict, extra="forbid") are all directly transferable.
- **Story 2.1** (Story `events` package scaffolding): the existing validators at lines 196 – 254 are Story 2.1's work. Maintain the file's voice — short docstrings, regex constants module-private with leading underscore, `@field_validator("X") @classmethod def _X_shape(cls, v) -> X:` signature pattern.
- **Story 2.14** (envelope extensions field): the `extensions: dict[str, Any]` field at line 171 is a related "reserved in 1.0.0, populated in 1.1.0+" pattern. **Note:** the `extensions["trace_id"]` usage at `test_envelope.py:380, 384` is a TEST-internal use of `extensions` as opaque metadata — it's not the same `trace_id` as our new envelope field. Don't conflate.
- **Epic 8.7 lesson L1** (hidden-gate cascade): after 9.1 lands locally, push and watch CI. Don't assume "local green = CI green". Reserve a follow-up commit if `mypy --strict` surfaces an issue from a downstream consumer importing the envelope.

### Git intelligence — recent commits to watch for collisions

Latest 5 commits as of 2026-05-16:

```
3cbacec docs(epic-8.7): close retro + spec Story 8.7.6 aiosqlite teardown root-fix
a0c53bb fix(epic-8.7): disable bash -e in pytest step so SIGABRT shim runs
c3d8222 fix(epic-8.7): correct SIGABRT-tolerance grep logic in CI pytest step
011ced6 fix(epic-8.7): tolerate aiosqlite daemon-thread SIGABRT in CI pytest step
7e4ffec fix(epic-8.7): make event_types re-registerable + replay in test_decisions
```

No Epic 9 commits yet — 9.1 is the kickoff. Story `7e4ffec`'s `ensure_registered()` pattern is documented in `event_types.py:137` and worth reading before touching ANY `register()` callsite (which 9.1 does not, but the L2 lesson stands).

### Latest-tech notes

- **Pydantic v2 `field_validator`**: the `@field_validator("X") @classmethod` decorator is the v2 successor to v1's `@validator`. Returns the validated value or raises `ValueError` (Pydantic wraps it as `ValidationError`). `mode="after"` is implicit — explicit `mode="after"` keyword is OK but not needed for new validators (consistent with `_event_id_shape` at line 196 which omits it).
- **`warnings.warn(category=DeprecationWarning)`**: pytest's default config does NOT show DeprecationWarning from internal libs but DOES show them from your own code. The `pytest.warns(DeprecationWarning)` context manager catches them deterministically. Do not use `pytest.deprecated_call()` — it has subtle behavioral differences with `stacklevel`.
- **`stacklevel=2`**: causes the warning to point at `EventEnvelope.create(...)`'s caller in the user's IDE traceback rather than at the factory's internal `warnings.warn(...)` line. Same convention as stdlib `dataclasses` and Python's own deprecations.

---

## Dev notes

### Validator implementation sketch (updated pass-2)

```python
# Module top (line 14): unconditional import for the hot factory path.
import warnings

# Near line 134, after _SEMVER_RE:
_TRACE_ID_TELEGRAM_RE = re.compile(r"\Atg:[1-9][0-9]{0,18}\Z")  # rejects tg:0 / leading zeros
_INT64_MAX = 9_223_372_036_854_775_807

# Insert after _request_id_shape:
@field_validator("trace_id", mode="after")  # explicit mode (pass-2 locked)
@classmethod
def _trace_id_shape(cls, v: str | None) -> str | None:
    if v is None:
        return v
    if _UUIDV7_BARE_RE.match(v):
        return v
    if _TRACE_ID_TELEGRAM_RE.match(v):
        if int(v[3:]) > _INT64_MAX:
            raise ValueError(
                f"trace_id Telegram update_id out of range "
                f"(max {_INT64_MAX}); got {v!r}"
            )
        return v
    raise ValueError(
        f"trace_id must be a bare UUIDv7 OR match \\Atg:<update_id>\\Z (got {v!r})"
    )
```

### Deprecation warning sketch (updated pass-2)

```python
# In EventEnvelope.create() body, BEFORE the registry lookup:
if trace_id is None:
    warnings.warn(
        "EventEnvelope created without trace_id; this field will be "
        "required in a future schema_version bump. Pass trace_id= "
        "(UUIDv7 or 'tg:<update_id>') to silence this warning.",
        DeprecationWarning,
        stacklevel=2,
    )
```

**Trade-off note (capture in commit msg, NOT in the code):** every existing `EventEnvelope.create(...)` callsite in the codebase will now emit this warning until 9.2 – 9.6 wire each caller. The `services/clawhip-daemon`, `services/registry-state`, `services/registry-api`, `services/console-cli`, and `mcp-servers/*` will all spew warnings during the next test run. This is **intentional** — it surfaces every unwired callsite to the developer working 9.2 – 9.6. Suppress globally only via `pytest.ini` `filterwarnings` if it makes the test output unreadable; do NOT silence per-callsite (defeats the purpose).

Actually — given the spew risk, consider an explicit pytest `filterwarnings` line in `pyproject.toml` while Stories 9.2 – 9.6 are in flight:

```toml
# pyproject.toml [tool.pytest.ini_options]
filterwarnings = [
    "ignore:EventEnvelope created without trace_id:DeprecationWarning",
]
```

… and remove the line in Story 9.7 once every emitter passes `trace_id=`. Document this as a follow-up TODO.

### Verification gate — run BEFORE committing

```bash
# Local gate parity with CI
uv run ruff check . && \
uv run ruff format --check . && \
uv run mypy --strict packages/events services/registry-api services/registry-state && \
uv run python scripts/check_imports.py && \
uv run python scripts/check_single_writer.py && \
uv run pytest packages/events -q -m "not slow" && \
uv run pytest packages/ services/ -q -m "not slow"
```

If the last line balks at unrelated test isolation failures, investigate per Epic 8.7's L2 — don't paper over.

### Non-goals (do NOT do in 9.1)

- Wire entry points (HTTP, Telegram, console, MCP, worker) — Stories 9.2 – 9.6
- Bump `schema_version` to 1.1.0 — Story 9.7
- Add `events.trace_id` ORM column or DB index — Story 9.7
- Add `scripts/checks/check_trace_id_required.py` CI gate — Story 9.7
- Backfill historical events — Story 9.7 / ADR-0004
- Make `trace_id` non-optional — Story 9.7
- Add `/trace <id>` operator query — Story 9.7

If you see yourself touching `pyproject.toml` `dependencies`, `alembic/versions/`, `mypy.ini`, or any service entry point's middleware file → you've drifted past 9.1's scope. Stop and re-read this section.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| Existing callsites in tests use `EventEnvelope.create(...)` without `trace_id` and will now emit DeprecationWarning, breaking strict-warning test configurations. | Add the `filterwarnings = ["ignore:..."]` to `pyproject.toml` `[tool.pytest.ini_options]` as part of 9.1; remove in 9.7. |
| `services/registry-api/src/registry_api/routes/events.py:49` hard-codes `"trace_id": None,` with a `noqa: ERA001` comment indicating Phase 2 wiring. 9.1 does NOT touch this line; 9.7 does. | Explicit non-goal listed above. Code-review the diff to confirm no incidental edit to this file. |
| `services/clawhip-daemon/src/.../test_telegram_sink.py:3033` and similar tests construct envelopes with `trace_id=None` explicitly. The new validator accepts `None`, so this is safe. | AC8 explicitly calls out these locations as expected-passing. |
| Hypothesis property test catches an edge case the AC5 enumerated tests miss (e.g., unicode digits matching `[0-9]` in `\d` mode). | The validator regex uses `[0-9]` not `\d`, so unicode digits are rejected. Document this in a code comment if Hypothesis surfaces a related case. |
| Pydantic v2 `field_validator` signature changes across minor versions. | Pin via existing `packages/events/pyproject.toml` constraint; copy the EXACT shape from `_request_id_shape` (line 212) for compatibility. |

---

## Definition of done

- All 8 ACs satisfied.
- `uv run pytest packages/events -q` shows the new tests passing.
- Local full-suite parity gate green (see "Verification gate" above).
- CI green on push (allow for L1 hidden-gate cascade — budget a follow-up commit if mypy or a downstream test surfaces an issue).
- Commit message follows the established `feat(events): Story 9.1 — ...` style.
- `sprint-status.yaml` `9-1-trace-id-optional-envelope: backlog → done`.
- The Dev Agent Record section below is filled in with: implementation notes, surprises, callsite-warning count, any follow-up TODOs surfaced for 9.2 – 9.7.

---

## Dev Agent Record

### Implementation summary

Landed exactly as spec'd. The field was already declared since Phase 1 (Story 2.1); 9.1's job was three surgical additions:

1. **`_TRACE_ID_TELEGRAM_RE`** module-private regex at `envelope.py:131` — `^tg:[0-9]{1,19}$` (bounded to signed 64-bit Telegram `update_id` overflow).
2. **`_trace_id_shape` field validator** at `envelope.py:219` — accepts `None` (default), bare UUIDv7 (reused `_UUIDV7_BARE_RE`), or `tg:<digits>`. Rejects everything else with a precise error message.
3. **`warnings.warn(DeprecationWarning, ...)` in `create()`** before the registry lookup — local import (`import warnings`) keeps module-load fast; `stacklevel=2` points the warning at the caller.

Plus a transition-window `pyproject.toml [tool.pytest.ini_options].filterwarnings` entry that suppresses the deprecation noise across the wider suite while preserving the `pytest.warns(DeprecationWarning)` assertions inside `test_envelope.py` itself.

### Files changed

| File | Lines | Change |
|---|---|---|
| `packages/events/src/events/envelope.py` | +25 / −1 | regex constant, validator method, deprecation warning in `create()` |
| `packages/events/src/events/test_envelope.py` | +95 / −1 | new `TestTraceIdShape` (12 tests) + `TestTraceIdDeprecationWarning` (3 tests) |
| `pyproject.toml` | +9 / 0 | `filterwarnings` transition entry with removal pointer to Story 9.7 |

### Test count delta

- Local `packages/events`: 358 → **373** (+15: 12 shape tests + 3 deprecation-warning tests)
- Local full suite: 2189 → **2204** (+15)
- CI projection: 2376 → **2391** (assuming same +15 delta)

### Surprises / deviations from spec

1. **Spec said ≥12 tests; landed 15.** Split AC5's 12 enumerated tests into two test classes (`TestTraceIdShape` × 12 + `TestTraceIdDeprecationWarning` × 3) for clarity — the deprecation tests assert behaviour on `EventEnvelope.create()` whereas the shape tests assert on direct `EventEnvelope(...)` construction.

2. **`test_warning_silent_on_model_validate_json_replay` exercises a subtle invariant.** Confirms that re-parsing a 1.0.0 envelope from canonical JSON does NOT fire the deprecation warning (because re-parse goes through `__init__`, not `create()`). This is the contract Story 9.7's migrator container will rely on when backfilling historical events — replay must not spam warnings.

3. **`extensions` field's docstring at `envelope.py:172` is now slightly stale** — it references `trace_id` as a Phase 2 example, but `trace_id` is now a first-class envelope field rather than an `extensions["trace_id"]` artifact. Left unchanged: docstring polish is out of 9.1's scope and a one-word edit is more likely to surface a downstream test failure than to clarify anything. Flagged for Story 9.7 cleanup.

4. **No new `import warnings` at module top.** The warning is emitted from a single callsite inside `create()`, so a local `import warnings` inside the method (consistent with Pyflakes' "imports near use" preference and the codebase's existing pattern at `envelope.py:75 import math`) is cleaner than a module-level import.

### Callsite-warning observation

`grep -rn "EventEnvelope[.]create(" packages/ services/ mcp-servers/ scripts/ --include="*.py"` returns 117 matches. After excluding docstrings, README references, and comment-only mentions, roughly **80-90 actual call locations** across the platform will emit the DeprecationWarning until Stories 9.2 – 9.6 pass `trace_id=`.

The `pyproject.toml` `filterwarnings` ignore line is essential to keep CI logs readable. Future stories should:

- 9.2 (registry-api `TraceIdMiddleware`): mint `new_uuid7()` per request; pass to every `EventEnvelope.create()` in registry-state's materializer handlers.
- 9.3 (telegram-gateway): inject `trace_id = f"tg:{update.update_id}"` into structlog context; downstream `AcceptedCommand.create()` chain reads it.
- 9.4 (console-cli): mint at command entry; thread through `command_envelope.create()`.
- 9.5 (MCP): explicit `caller_trace_id: str` input to every Pydantic tool model; passed to downstream `EventEnvelope.create()` calls.
- 9.6 (worker-wrapper): `--trace-id` CLI flag → propagate to clawhip-bridge emit_* tools.

### Follow-up TODOs surfaced for Epic 9

1. **Remove `filterwarnings` in Story 9.7** when `trace_id` becomes mandatory — the absent-trace_id path no longer exists, so the deprecation warning can't fire and the filter line is dead config.
2. **Update `envelope.py:172` extensions docstring** in Story 9.7 (one-line edit, defer to avoid 9.1 scope creep).
3. **CI gate `scripts/checks/check_trace_id_required.py`** (Story 9.7) should AST-scan for `EventEnvelope.create(` without a `trace_id=` kwarg — a more rigorous replacement for the runtime DeprecationWarning. The warning catches missed callsites at test time; the AST scanner catches them at lint time.
4. **Hypothesis property test optional** — `@given(st.text())` strategy asserting `validate_trace_id(s)` raises iff `s` doesn't match the union pattern was considered but skipped to keep the test count manageable. Worth adding if future ill-shaped inputs surface real bugs.

### Verification gate output

```
uv run mypy --strict packages/ services/registry-api services/registry-state
  → Success: no issues found in 97 source files
uv run ruff check .
  → All checks passed!
uv run ruff format --check .
  → 307 files already formatted
uv run python scripts/check_imports.py
  → clean
uv run python scripts/check_single_writer.py
  → clean
git ls-files -z | xargs -0 uv run secret-hygiene-precommit
  → exit 0
uv run pytest packages/events -q
  → 373 passed, 1 warning in 0.29s
uv run pytest packages/ services/ -q -m "not slow"
  → 2204 passed, 3 skipped, 5 deselected in 25.94s
```

All Epic 8.7 baseline gates remain green.

---

## Review Findings

### Adversarial 3-lane code review — 2026-05-16

Reviewed at commit `dae92d8` vs parent `07a9804`. Three lanes (Blind Hunter / Edge-Case Hunter / Acceptance Auditor) produced 22 raw findings; deduplicated to 15 unique; classified as 12 `patch` + 3 `defer` + 7 `dismiss`. Density consistent with the Epic 8.x 14-finding-per-pass cadence.

#### Patch findings (action required)

- [x] [Review][Patch] **HIGH — `re.match + $` allows trailing `\n` bypass** [`packages/events/src/events/envelope.py:134, 229`]. `_TRACE_ID_TELEGRAM_RE.match("tg:1\n")` returns a match because Python's `re.match` only anchors start and `$` matches before trailing newline. Affects ALL existing regex validators (event_id/request_id/schema_version/type/parent_event_id) — newly amplified by 9.1's two-pattern union. Fix: switch to `re.fullmatch` OR change `$` → `\Z` consistently across the module. Add test `_make_envelope(trace_id="tg:1\n")` raises.

- [x] [Review][Patch] **HIGH — `tg:0` and leading-zero forms accepted** [`envelope.py:134`]. Regex `[0-9]{1,19}` accepts `tg:0`, `tg:01`, `tg:007` — Telegram BotAPI's `update_id` is always ≥1, so `tg:0` is semantically invalid; `tg:01` and `tg:007` create aliasing where two distinct trace_ids refer to the same logical update, breaking correlation/dedup downstream. Fix: tighten to `^tg:[1-9][0-9]{0,18}$`. Add negative tests for `tg:0`, `tg:01`, `tg:007`.

- [x] [Review][Patch] **MEDIUM — `tg:` regex accepts int64-overflow values** [`envelope.py:134`]. 19-digit cap allows `tg:9999999999999999999` (9.99e18) > int64 max (9.22e18 = `9_223_372_036_854_775_807`). Story 9.3+ will likely `int(tid.removeprefix("tg:"))` and overflow downstream `bigint` columns. Fix: supplement regex with a numeric range check post-match (`int(v[3:]) <= 9_223_372_036_854_775_807`). Add boundary test for `tg:9223372036854775807` accepted, `tg:9223372036854775808` rejected.

- [x] [Review][Patch] **MEDIUM — AC5 spec gap: canonical-JSON round-trip not asserted for trace_id set** [`packages/events/src/events/test_envelope.py`]. Spec AC5 enumerated scenarios #15-16 require canonical round-trip with `trace_id="<uuidv7>"` and `trace_id="tg:12345"`. Landed tests cover constructor acceptance but stop short of asserting `to_canonical_json(env) == expected_bytes`. Fix: add 2 tests mirroring `test_envelope.py:475`'s round-trip template.

- [x] [Review][Patch] **MEDIUM — `filterwarnings` substring filter not anchored, no module qualifier** [`pyproject.toml:88-90`]. Current entry `"ignore:EventEnvelope created without trace_id:DeprecationWarning"` is a substring match with no module qualifier. Future warnings starting with the same prefix would be silently captured. Fix: anchor and qualify: `"ignore:^EventEnvelope created without trace_id.*$:DeprecationWarning:events.envelope"`.

- [x] [Review][Patch] **MEDIUM — Deprecation message couples to internal sprint terminology** [`envelope.py:359-363`]. Message refers to "Story 9.7" and "schema_version 1.1.0" — opaque to library consumers; lies if the bump slips to 1.2.0 or a different story. Fix: drop sprint references in user-facing text; keep the linkage in a code comment.

- [x] [Review][Patch] **MEDIUM — `test_warning_silent_on_model_validate_json_replay` misleading** [`test_envelope.py:204-214`]. `_make_envelope()` calls `EventEnvelope(...)` directly (not `create()`), so the original construction never emitted a warning either. Test only proves `model_validate_json` doesn't warn — NOT that "replay" specifically suppresses the warning. Fix: construct via `create()` first inside `pytest.warns(DeprecationWarning)`, dump to JSON, then assert silent re-parse via `simplefilter("error", DeprecationWarning)`.

- [x] [Review][Patch] **MEDIUM — Legacy JSONL replay risk** [`services/registry-state/src/registry_state/adapters/event_log.py:109-179`]. Pre-9.1 records may have stored free-form correlation strings in `trace_id`. After 9.1, the validator rejects anything not matching UUIDv7 / `tg:<digits>` at replay time, crashing projection rebuilds. Fix: add a fixture-corpus regression test that loads all historical JSONL fixtures and asserts `read_log_lines` completes without ValidationError. If any fixture fails, document the migration path (advisory-warn on read).

- [x] [Review][Patch] **LOW — `import warnings` should be module-level** [`envelope.py:358`]. Local import inside `create()` is premature optimization; `warnings` is always already imported by pytest/Pydantic and a `sys.modules` lookup on every absent-trace_id callsite is more cost than savings. Fix: move `import warnings` to module top.

- [x] [Review][Patch] **LOW — No test for `model_validate_json` with omitted `trace_id` key** [`test_envelope.py`]. Locks the invariant that `{...}` (key absent) and `{"trace_id": null}` both yield `env.trace_id is None`. Fix: add `test_omitted_trace_id_in_json_accepted` parsing `'{"event_id":"...", ...}'` without the trace_id key.

- [x] [Review][Patch] **LOW — No test for validation-vs-warning ordering** [`test_envelope.py`]. Today's behaviour: `create(trace_id="bad")` raises `ValidationError` BEFORE `warnings.warn` fires (because the warning checks `if trace_id is None`, and "bad" is not None). Lock this in. Fix: add `test_invalid_trace_id_in_create_raises_without_warning` using `pytest.warns(None)` + `pytest.raises(ValidationError)` together.

- [x] [Review][Patch] **LOW — Adversarial coverage thin (trailing whitespace / Unicode / case)** [`test_envelope.py:152-154`]. Only leading whitespace tested. Add: `test_trailing_whitespace_rejected`, `test_internal_newline_rejected` (will fail until finding #1 fixed — that's the point), `test_telegram_form_uppercase_rejected("TG:1")`, `test_telegram_form_unicode_digit_rejected("tg:١")`.

#### Deferred findings

- [x] [Review][Defer] **`stacklevel=2` may not reach actual caller for wrapped factories** [`envelope.py:364`]. `audited_secret.py:336` wraps `create()`; warning will point at wrapper, not application code. Real fix is Story 9.7's AST CI gate (`scripts/checks/check_trace_id_required.py`) which the dev TODO already lists. Defer — runtime warning is a coarse indicator; AST scan is the proper diagnostic.

- [x] [Review][Defer] **`parent_event_id` cascade — trace_id consistency design gap**. Today, parent A's `trace_id="tg:42"` can coexist with child B's `trace_id=<uuidv7>` or `None` without complaint, breaking the distributed-tracing causal-chain contract. Defer to Story 9.7's architectural decision (either: document per-event semantics, OR add `model_validator(mode="after")` enforcing inheritance).

- [x] [Review][Defer] **Stale `extensions` field docstring at `envelope.py:172`**. Already listed in dev TODOs for Story 9.7 cleanup. References `trace_id` as a Phase 2 example, but trace_id is now a first-class envelope field.

#### Patch resolution — 2026-05-16 (pass-1 batch-apply)

All 12 `patch` findings resolved in a single follow-up commit on top of `dae92d8`. Implementation summary:

| Finding | Resolution | Files touched |
|---|---|---|
| F1 — `re.match + $` newline bypass | All 5 envelope regex constants switched from `$` to `\Z`. Negative tests for `tg:1\n` and internal newline added. | `envelope.py:127-143`, `test_envelope.py:235-244` |
| F2 — `tg:0` / leading zeros | Regex tightened to `\Atg:[1-9][0-9]{0,18}\Z`. Three rejection tests added (`tg:0`, `tg:01`, `tg:007`). | `envelope.py:143`, `test_envelope.py:155-168` |
| F3 — int64 overflow window | Post-match `int(v[3:]) > _INT64_MAX` check in validator. Boundary tests for `tg:9223372036854775807` accepted, `tg:9223372036854775808` rejected, `tg:9999999999999999999` rejected. | `envelope.py:144, 233-244`, `test_envelope.py:138-153` |
| F4 — canonical-JSON round-trip tests | New `TestTraceIdRoundTrip` class with 4 tests: None / UUIDv7 / `tg:12345` / omitted-key. Byte-stable assertions (`replayed.model_dump_json().encode("utf-8") == json_bytes`). | `test_envelope.py:339-396` |
| F5 — filterwarnings anchoring | Message anchored with `^`. Module qualifier evaluated and intentionally OMITTED — `stacklevel=2` means the warning's source module is the caller, not envelope.py, so a module-qualified filter wouldn't match (verified by 825-warning regression on first attempt). | `pyproject.toml:90-99` |
| F6 — Deprecation message coupling | Sprint terminology stripped from user-facing message. Linkage retained in code comment. | `envelope.py:386-393`, related docstring at `envelope.py:362-366` |
| F7 — Misleading replay test | Test renamed to `test_replay_via_model_validate_json_does_not_warn` and refactored to: produce envelope via `create()` under `pytest.warns(DeprecationWarning)` (capturing the warning that DOES fire), serialise to canonical JSON, then re-parse under `simplefilter("error", DeprecationWarning)`. Honest. | `test_envelope.py:283-307` |
| F8 — Legacy JSONL replay risk | New `TestLegacyJsonlReplay` class loads `scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl` line-by-line through `model_validate_json` and asserts every record parses + `trace_id is None`. Catches a future hard-rejection regression on historical data. | `test_envelope.py:397-435` |
| F9 — Module-level `import warnings` | `import warnings` lifted to module top (`envelope.py:16`). Local import inside `create()` removed. | `envelope.py:16, 386-393` |
| F10 — Omitted-key JSON test | `test_model_validate_json_with_omitted_trace_id_key` parses a literal JSON blob without the `trace_id` key and asserts `env.trace_id is None`. | `test_envelope.py:323-337` |
| F11 — Validation-vs-warning ordering test | `test_invalid_trace_id_in_create_raises_without_warning` uses `warnings.catch_warnings(record=True) + simplefilter("always")` and asserts `not any(issubclass(item.category, DeprecationWarning) for item in w)` when `create(trace_id="bad")` raises ValidationError. | `test_envelope.py:309-321` |
| F12 — Adversarial coverage | Added: trailing-whitespace rejection, internal-newline rejection (depends on F1), uppercase `TG:1` rejection, Arabic-Indic Unicode digit `tg:١` rejection. | `test_envelope.py:183-244` |

**Test count delta after pass-2 batch-apply:** 85 envelope tests (was 69 after pass-1) — **+16 net new** post-review (+15 over the original Story 9.1 implementation). Full suite: **2204 → 2220** (+16). All gates green.

#### Pass-2 review (second-opinion adversarial) — 2026-05-16

Reviewed cumulative diff `07a9804..1ea5e90` (Story 9.1 implementation + pass-1 patches). User policy: **fix all issues even minors**. Three lanes produced 24 raw findings; deduplicated to 12 unique post-merge; all classified as `patch` and applied in a single follow-up commit.

| # | Severity | Source | Title | Resolution |
|---|---|---|---|---|
| N1 | HIGH | auditor | `schema_registry.py:39-40` mirror regexes still used `^...$` after F1 — bypass class returns at registration time | `schema_registry.py` regexes switched to `\A...\Z` matching the envelope-side fix; comment block updated to record the mirror-promise. |
| N2 | MEDIUM | blind | `pyproject.toml` filterwarnings: leading `^` is redundant (`re.match` already start-anchors); the real protection is a TAIL anchor | Filter changed to `"ignore:EventEnvelope created without trace_id;.*\\Z:DeprecationWarning"` — tail-anchored on the trailing semicolon. |
| N3 | MEDIUM | blind | Int64 error message exposed implementation-detail "int64 max" to library consumers | Reworded to `"trace_id Telegram update_id out of range (max {n}); got {v!r}"` — symmetric with F6's principle. |
| N4 | MEDIUM | blind | Implicit `mode=` on field validators — Pydantic future-refactor risk if `Field(validate_default=False)` is added later | Added explicit `mode="after"` to all 5 envelope shape validators (event_id, parent_event_id, request_id, trace_id, schema_version, type). Locks the contract. |
| N5 | MEDIUM | blind | `test_invalid_trace_id_in_create_raises_without_warning` name claimed "ordering" but actually tested "warning-only-on-None" | Renamed to `test_invalid_non_none_trace_id_does_not_fire_deprecation_warning`. Comment updated. |
| N6 | MEDIUM | blind | `create()` docstring claimed warning skips replay because it goes through `__init__` — true but incomplete; missed that direct `EventEnvelope(...)` construction ALSO skips | Reworded docstring: "fires only from `create()` when `trace_id is None`". |
| N7 | MEDIUM+drift | auditor | Spec body D1-D6: original AC1 regex / AC2 message / Dev Notes sketches were never updated after pass-1 fixes | Replaced 4 stale spec artifacts (AC1 regex table, AC1 error message, AC2 message block, Dev Notes implementation sketches) with current code. |
| N8 | LOW | blind | Two redundant no-digit tests | Merged into `test_telegram_form_non_canonical_digit_input_rejected` with `@pytest.mark.parametrize` covering 7 inputs (added `tg:1a`, `tg:a1`, `tg:1.5`, `tg:-1`, `tg:+1` beyond original `tg:` / `tg:abc`). |
| N9 | LOW | blind | Round-trip tests asserted idempotence not canonicalization | Added `test_canonical_json_keys_are_alphabetical` using `events.canonical.to_canonical_json`. Pins `trace_id` between `schema_version` and `type`. |
| N10 | LOW | blind | No test locked the `stacklevel=2` contract | Added `TestTraceIdDeprecationStacklevel` asserting `warning.filename` basename equals the test file's. |
| N11 | LOW | blind | `TestLegacyJsonlReplay` hard-failed with cryptic message if fixture moved | Replaced `assert fixture.is_file()` with `pytest.skip(...)` carrying the expected path. |
| N12 | LOW | blind | Int64 check `int(v[3:])` lacks documentation that regex guarantees ASCII digits | Added comment "regex guarantees ASCII digits so int() cannot raise" inline. |

**Pass-2 test count delta:** 85 → **92** in envelope alone (+7 net: -2 from parametrize merge, +2 sort-key test, +1 stacklevel test, +6 parametrized table rows recognized). Full suite delta verified below.

#### Dismissed (7)

- `__all__` enforcement for module-private regex constant (underscore convention is standard Python).
- `repr()` log-injection risk on error message (speculative; current validator only sees post-strict-coercion `str` and ValidationError isn't directly piped to HTTP responses).
- Validator-skip-on-default coverage gap (false positive — Pydantic v2 `@field_validator` with default `mode="after"` DOES validate defaults).
- Pytest-xdist `catch_warnings` thread-safety (xdist not in use on this project).
- AC4 schema_version pre-existing docstring `1.1.0` references (predate Epic 9 — not 9.1 scope).
- AC7 mypy unverifiable from diff (already verified via Dev Agent Record output: 97 source files clean).
- Duplicate counting of newline-anchor issue across BH and ECH (merged into finding #1).

---

## Frontmatter

```yaml
---
story_id: 9.1
story_key: 9-1-trace-id-optional-envelope
parent_epic: 9
phase: 2
fr_refs: [FR57]
nfr_refs: [NFR-O7, NFR-M3]
arch_refs:
  - "Envelope schema migration: 1.0.0 → 1.1.0 / Cutover plan step 1"
  - "Cross-Cutting Concerns: trace_id reserved (line-401)"
  - "Phase 2 Invariants P2-I2"
estimated_hours: 2-4
priority: high (Epic 9 foundation)
blocks:
  - 9.2 (registry-api X-Trace-Id middleware)
  - 9.3 (telegram-gateway tg:<update_id> derivation)
  - 9.4 (console-cli mint at command entry)
  - 9.5 (MCP caller_trace_id input)
  - 9.6 (worker-wrapper --trace-id flag)
  - 9.7 (1.1.0 bump + migrator + /trace query)
blocked_by: nothing
status: ready-for-dev
created: 2026-05-16
created_by: bmad-create-story skill
---
```
