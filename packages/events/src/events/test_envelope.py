"""Unit tests for events.envelope — EventEnvelope field validators + create() factory.

AC-6 / Story 2.1: ~15 tests.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone
from random import Random
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from events import FrozenClock, new_event_id, new_request_id
from events.envelope import Actor, ActorKind, EventEnvelope
from events.errors import EventSchemaUnknown, EventValidationError
from events.schema_registry import register, unregister_all

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

# Deterministic test IDs via Story-2.2 generators (AC-7 replacement pattern).
_VALID_EVENT_ID = new_event_id(clock=FrozenClock(), rng=Random(42))
_VALID_REQUEST_ID = new_request_id(clock=FrozenClock(), rng=Random(43))
_VALID_EMITTED_AT = datetime(2026, 4, 21, 10, 30, 0, tzinfo=UTC)


class _TaskPayload(BaseModel):
    task_id: str


def _make_envelope(**overrides: object) -> EventEnvelope:
    """Return a valid EventEnvelope using sensible defaults."""
    kwargs: dict[str, object] = dict(
        event_id=_VALID_EVENT_ID,
        schema_version="1.0.0",
        type="task.created",
        emitted_at=_VALID_EMITTED_AT,
        emitted_at_monotonic_ns=1_000_000,
        actor=Actor(kind="system", id="test-system"),
        payload={"task_id": "abc"},
        request_id=_VALID_REQUEST_ID,
    )
    kwargs.update(overrides)
    return EventEnvelope(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    register("task.created", "1.0.0", _TaskPayload)
    yield
    unregister_all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidEnvelopeConstruction:
    def test_valid_envelope_builds(self) -> None:
        env = _make_envelope()
        assert env.event_id == _VALID_EVENT_ID
        assert env.type == "task.created"
        assert env.schema_version == "1.0.0"


class TestFrozenMutation:
    def test_mutation_raises(self) -> None:
        env = _make_envelope()
        with pytest.raises((ValidationError, TypeError)):
            env.payload = {}


class TestEventIdShape:
    def test_valid_uuidv7_with_prefix(self) -> None:
        env = _make_envelope(event_id="e-01917e5c-a7d1-7000-8abc-000000000001")
        assert env.event_id.startswith("e-")

    def test_missing_e_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="event_id"):
            _make_envelope(event_id="01917e5c-a7d1-7000-8000-000000000001")

    def test_wrong_version_nibble_rejected(self) -> None:
        # version nibble must be 7; use 4 here
        with pytest.raises(ValidationError, match="event_id"):
            _make_envelope(event_id="e-01917e5c-a7d1-4000-8000-000000000001")

    def test_valid_parent_event_id(self) -> None:
        env = _make_envelope(parent_event_id="e-01917e5c-a7d1-7000-8000-000000000099")
        assert env.parent_event_id is not None

    def test_invalid_parent_event_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="parent_event_id"):
            _make_envelope(parent_event_id="bad-id")

    def test_none_parent_event_id_accepted(self) -> None:
        env = _make_envelope(parent_event_id=None)
        assert env.parent_event_id is None


class TestRequestIdShape:
    def test_bare_uuidv7_accepted(self) -> None:
        env = _make_envelope(request_id="01917e5c-a7d1-7000-8000-000000000099")
        assert env.request_id == "01917e5c-a7d1-7000-8000-000000000099"

    def test_prefixed_request_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="request_id"):
            _make_envelope(request_id="e-01917e5c-a7d1-7000-8000-000000000099")


# Story 9.1 — trace_id field validation + deprecation warning.
# Code-review hardening pass (2026-05-16): expanded coverage across the
# 12 patch findings — newline-anchor (F1), leading-zero rejection (F2),
# int64 ceiling (F3), adversarial whitespace/case/unicode (F12), JSON
# omitted-key path (F10), and validation-vs-warning ordering (F11).
class TestTraceIdShape:
    def test_none_default_accepted(self) -> None:
        env = _make_envelope()
        assert env.trace_id is None

    def test_explicit_none_accepted(self) -> None:
        env = _make_envelope(trace_id=None)
        assert env.trace_id is None

    def test_bare_uuidv7_accepted(self) -> None:
        env = _make_envelope(trace_id="01917e5c-a7d1-7000-8abc-000000000777")
        assert env.trace_id == "01917e5c-a7d1-7000-8abc-000000000777"

    def test_telegram_form_smallest_int_accepted(self) -> None:
        env = _make_envelope(trace_id="tg:1")
        assert env.trace_id == "tg:1"

    def test_telegram_form_int64_max_accepted(self) -> None:
        # F3 boundary: signed 64-bit ceiling — exactly the int64 max.
        env = _make_envelope(trace_id="tg:9223372036854775807")
        assert env.trace_id == "tg:9223372036854775807"

    def test_telegram_form_int64_plus_one_rejected(self) -> None:
        # F3 boundary: int64 max + 1 (still 19 digits, regex passes; post-
        # match int check rejects).
        with pytest.raises(ValidationError, match="out of range"):
            _make_envelope(trace_id="tg:9223372036854775808")

    def test_telegram_form_19_nines_rejected(self) -> None:
        # F3: 19-digit max regex would accept (10^19 - 1) ≈ 9.99e18, but
        # int64 ceiling is 9.22e18. Post-match check rejects.
        with pytest.raises(ValidationError, match="out of range"):
            _make_envelope(trace_id="tg:9999999999999999999")

    def test_telegram_form_zero_rejected(self) -> None:
        # F2: Telegram update_id is always ≥ 1; tg:0 is semantically invalid.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:0")

    def test_telegram_form_leading_zero_rejected(self) -> None:
        # F2: tg:01 and tg:1 would otherwise alias to the same update_id.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:01")

    def test_telegram_form_007_rejected(self) -> None:
        # F2 corollary: prefix-zero rejection covers tg:007.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:007")

    @pytest.mark.parametrize(
        "bad_trace_id",
        [
            "tg:",  # no digits
            "tg:abc",  # all non-digits
            "tg:1a",  # mixed digit + letter
            "tg:a1",  # non-digit first
            "tg:1.5",  # decimal — has dot
            "tg:-1",  # signed
            "tg:+1",  # signed plus
        ],
        ids=["empty", "alpha", "trailing-alpha", "leading-alpha", "decimal", "neg", "plus"],
    )
    def test_telegram_form_non_canonical_digit_input_rejected(self, bad_trace_id: str) -> None:
        # Pass-2 review consolidation (blind #8): rolled the two single-case
        # tests into one parametrized matrix to cover the analogous failure
        # modes uniformly.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id=bad_trace_id)

    def test_telegram_form_20_digit_overflow_rejected(self) -> None:
        # Width-based rejection — 20 digits never fits any int64 form.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:12345678901234567890")

    def test_telegram_form_uppercase_rejected(self) -> None:
        # F12 adversarial: case sensitivity locked in.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="TG:1")

    def test_telegram_form_unicode_digit_rejected(self) -> None:
        # F12 adversarial: [0-9] (not \d) rejects Unicode digit categories.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:١")  # Arabic-Indic digit 1.

    def test_event_id_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="e-01917e5c-a7d1-7000-8abc-000000000777")

    def test_uuidv4_rejected(self) -> None:
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="01917e5c-a7d1-4000-8000-000000000777")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="")

    def test_leading_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id=" 01917e5c-a7d1-7000-8abc-000000000777")

    def test_trailing_whitespace_rejected(self) -> None:
        # F12 adversarial: trailing whitespace.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="01917e5c-a7d1-7000-8abc-000000000777 ")

    def test_trailing_newline_rejected(self) -> None:
        # F1: the key regression test. With re.match + `$`, a trailing `\n`
        # would silently pass because `$` matches before EOL. The `\Z` anchor
        # closes this. Without the fix, this test fails.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="tg:1\n")

    def test_internal_newline_rejected(self) -> None:
        # F1 corollary: internal newline must reject.
        with pytest.raises(ValidationError, match="trace_id"):
            _make_envelope(trace_id="01917e5c-a7d1-7000-8abc-00000000\n0777")


# Story 9.2 pass-2 review N13 — defensive type guard on the public helper.
class TestIsValidTraceIdDefensiveTypeGuard:
    """``is_valid_trace_id`` must return False (not raise) on non-str inputs.

    Story 9.2 pass-2 review N13: future downstream callers (Stories 9.3-9.6
    — MCP / worker / console ingresses) may pass user-decoded ``bytes`` or
    raw numeric values. Without the ``isinstance(value, str)`` gate, the
    helper would crash with ``TypeError`` inside ``re.match``. Defensive
    return-False mirrors ``parse_prefix``'s discipline.
    """

    def test_bytes_input_returns_false(self) -> None:
        from events.envelope import is_valid_trace_id

        # Bytes look like a valid trace_id but are NOT a str.
        assert is_valid_trace_id(b"tg:42") is False  # type: ignore[arg-type]

    def test_int_input_returns_false(self) -> None:
        from events.envelope import is_valid_trace_id

        assert is_valid_trace_id(42) is False  # type: ignore[arg-type]

    def test_none_input_returns_false(self) -> None:
        from events.envelope import is_valid_trace_id

        assert is_valid_trace_id(None) is False  # type: ignore[arg-type]

    def test_valid_str_still_accepted(self) -> None:
        # Sanity: the defensive gate doesn't break the str path.
        from events.envelope import is_valid_trace_id

        assert is_valid_trace_id("tg:42") is True
        assert is_valid_trace_id("01917e5c-a7d1-7000-8000-000000000000") is True


# Story 9.1 — DeprecationWarning emitted from create() when trace_id absent.
class TestTraceIdDeprecationWarning:
    def test_warning_fires_when_trace_id_absent(self) -> None:
        with pytest.warns(DeprecationWarning, match="trace_id"):
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1_000_000,
                actor=Actor(kind="system", id="test-system"),
                payload={"task_id": "abc"},
                request_id=_VALID_REQUEST_ID,
            )

    def test_warning_silent_when_trace_id_present(self) -> None:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            env = EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1_000_000,
                actor=Actor(kind="system", id="test-system"),
                payload={"task_id": "abc"},
                request_id=_VALID_REQUEST_ID,
                trace_id="01917e5c-a7d1-7000-8abc-000000000888",
            )
            assert env.trace_id == "01917e5c-a7d1-7000-8abc-000000000888"

    def test_replay_via_model_validate_json_does_not_warn(self) -> None:
        # F7: this test originally constructed via _make_envelope() (direct
        # __init__) — which never fires the warning regardless. Honest
        # replay test: produce the envelope via create() under pytest.warns
        # (so the warning IS captured during construction), serialise to
        # canonical JSON, then re-parse under simplefilter("error",
        # DeprecationWarning) and assert no warning fires on the replay.
        import warnings

        with pytest.warns(DeprecationWarning, match="trace_id"):
            env = EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1_000_000,
                actor=Actor(kind="system", id="test-system"),
                payload={"task_id": "abc"},
                request_id=_VALID_REQUEST_ID,
            )
        canonical = env.model_dump_json()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            replayed = EventEnvelope.model_validate_json(canonical)
            assert replayed.trace_id is None

    def test_invalid_non_none_trace_id_does_not_fire_deprecation_warning(self) -> None:
        # F11 (renamed per pass-2 review blind #6): the warning only fires
        # when `trace_id is None`. An invalid non-None value must raise
        # ValidationError WITHOUT emitting DeprecationWarning. Locks the
        # "warning-only-on-None" invariant.
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with pytest.raises(ValidationError, match="trace_id"):
                EventEnvelope.create(
                    event_id=_VALID_EVENT_ID,
                    schema_version="1.0.0",
                    type="task.created",
                    emitted_at=_VALID_EMITTED_AT,
                    emitted_at_monotonic_ns=1_000_000,
                    actor=Actor(kind="system", id="test-system"),
                    payload={"task_id": "abc"},
                    request_id=_VALID_REQUEST_ID,
                    trace_id="bad",
                )
            assert not any(issubclass(item.category, DeprecationWarning) for item in w), (
                "DeprecationWarning must not fire when trace_id is provided (even if invalid)"
            )


# Story 9.1 — trace_id round-trip via canonical JSON.
class TestTraceIdRoundTrip:
    """Patch F4 — AC3 / AC5 #15-#16: canonical-JSON byte-stable round-trip
    for trace_id set. The encoder is field-name-driven and alphabetical;
    these tests are the regression net that catches a future encoder
    refactor silently reordering or dropping the trace_id field.
    """

    def test_round_trip_with_none_trace_id(self) -> None:
        env = _make_envelope()
        json_bytes = env.model_dump_json().encode("utf-8")
        replayed = EventEnvelope.model_validate_json(json_bytes)
        assert replayed.trace_id is None
        assert replayed.model_dump_json().encode("utf-8") == json_bytes

    def test_round_trip_with_uuidv7_trace_id(self) -> None:
        env = _make_envelope(trace_id="01917e5c-a7d1-7000-8abc-000000000aaa")
        json_bytes = env.model_dump_json().encode("utf-8")
        replayed = EventEnvelope.model_validate_json(json_bytes)
        assert replayed.trace_id == "01917e5c-a7d1-7000-8abc-000000000aaa"
        assert replayed.model_dump_json().encode("utf-8") == json_bytes

    def test_round_trip_with_telegram_trace_id(self) -> None:
        env = _make_envelope(trace_id="tg:12345")
        json_bytes = env.model_dump_json().encode("utf-8")
        replayed = EventEnvelope.model_validate_json(json_bytes)
        assert replayed.trace_id == "tg:12345"
        assert replayed.model_dump_json().encode("utf-8") == json_bytes

    def test_model_validate_json_with_omitted_trace_id_key(self) -> None:
        # F10: a JSON blob WITHOUT the `trace_id` key (vs explicit null)
        # must still parse and yield env.trace_id is None.
        blob = (
            f'{{"event_id":"{_VALID_EVENT_ID}",'
            f'"schema_version":"1.0.0",'
            f'"type":"task.created",'
            f'"emitted_at":"2026-04-21T10:30:00.000000Z",'
            f'"emitted_at_monotonic_ns":1000000,'
            f'"actor":{{"kind":"system","id":"test-system"}},'
            f'"payload":{{"task_id":"abc"}},'
            f'"request_id":"{_VALID_REQUEST_ID}",'
            f'"extensions":{{}}}}'
        )
        env = EventEnvelope.model_validate_json(blob)
        assert env.trace_id is None

    def test_canonical_json_keys_are_alphabetical(self) -> None:
        # Pass-2 review (blind #10): the byte-equality round-trip tests
        # above use `model_dump_json()` which produces DECLARATION-order
        # keys — those tests prove idempotence, not canonicalization.
        # The true canonical encoder is `events.canonical.to_canonical_json`
        # (used by the event-log writer + sinks). This test pins the
        # alphabetical-key contract for the canonical form, including
        # the alphabetical position of `trace_id` between `schema_version`
        # and `type`.
        import json

        from events.canonical import to_canonical_json

        env = _make_envelope(trace_id="tg:12345")
        canonical_bytes = to_canonical_json(env)
        parsed = json.loads(canonical_bytes)
        keys = list(parsed.keys())
        assert keys == sorted(keys), f"Canonical JSON keys must be alphabetical; got {keys!r}"
        assert keys.index("schema_version") < keys.index("trace_id") < keys.index("type")


# Story 9.1 pass-2 — stacklevel diagnostic regression test (blind #11).
class TestTraceIdDeprecationStacklevel:
    """Pass-2 review (blind #11): `stacklevel=2` is meant to point the
    DeprecationWarning at the caller of ``EventEnvelope.create()``, not
    inside envelope.py. The deferred F-flag for wrapped factories
    (audited_secret.py:336-style helpers) acknowledges that any wrapper
    breaks this — but no test currently locks the contract. This class
    catches accidental regressions where someone forgets to bump
    stacklevel after introducing an envelope.py-internal helper.
    """

    def test_warning_points_at_caller_not_envelope_internals(self) -> None:
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1_000_000,
                actor=Actor(kind="system", id="test-system"),
                payload={"task_id": "abc"},
                request_id=_VALID_REQUEST_ID,
            )

        deprecation_records = [r for r in caught if issubclass(r.category, DeprecationWarning)]
        assert len(deprecation_records) == 1, (
            f"Expected exactly one DeprecationWarning; got {len(deprecation_records)}"
        )
        # The warning's `filename` field must be THIS test file, not
        # envelope.py. Compare basenames so the test survives path
        # normalisation differences across platforms.
        from pathlib import Path

        warning_file = Path(deprecation_records[0].filename).name
        assert warning_file == Path(__file__).name, (
            f"DeprecationWarning stacklevel must point at caller "
            f"({Path(__file__).name}); got {warning_file}"
        )


# Story 9.1 — legacy JSONL replay regression (patch F8).
class TestLegacyJsonlReplay:
    """The new trace_id validator must NOT reject 1.0.0 records emitted
    before Epic 9 wiring landed. Historical records have ``trace_id: null``
    (or omitted). Replay them through ``model_validate_json`` and confirm
    every record parses cleanly.

    The migrator's `sample_v1.0.0.jsonl` fixture is the canonical
    representative corpus. If a future fixture or production log carries
    free-form trace_id strings outside UUIDv7 / tg:<update_id>, this test
    flips red and the design must reconsider (advisory-warn on read vs
    hard-reject).
    """

    def test_migrator_fixture_corpus_parses(self) -> None:
        # Pass-2 review (blind #3): the migrator fixture is in a sibling
        # package tree. If the repo layout ever changes, skip with a
        # helpful pointer rather than a cryptic hard-fail — the test's
        # value is the regression check, not the layout assertion.
        from pathlib import Path

        fixture = (
            Path(__file__).resolve().parents[4]
            / "scripts"
            / "migrator"
            / "tests"
            / "fixtures"
            / "sample_v1.0.0.jsonl"
        )
        if not fixture.is_file():
            pytest.skip(
                f"Migrator fixture not found at {fixture} (cross-package "
                f"path may have changed); regression check skipped."
            )

        count = 0
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            env = EventEnvelope.model_validate_json(line)
            assert env.trace_id is None  # All historical records have null trace_id.
            count += 1
        assert count > 0, "Fixture corpus must contain at least one record"


class TestNaiveDatetimeRejected:
    def test_naive_datetime_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_envelope(emitted_at=datetime(2026, 4, 21, 10, 30, 0))


class TestNonUtcDatetimeRejected:
    def test_non_utc_offset_raises(self) -> None:
        non_utc = datetime(2026, 4, 21, 10, 30, 0, tzinfo=timezone(timedelta(hours=5)))
        with pytest.raises(ValidationError, match="UTC"):
            _make_envelope(emitted_at=non_utc)


class TestUtcDatetimeAccepted:
    def test_utc_datetime_accepted(self) -> None:
        env = _make_envelope(emitted_at=datetime(2026, 4, 21, 0, 0, 0, tzinfo=UTC))
        assert env.emitted_at.tzinfo is not None


class TestSchemaVersionRegex:
    def test_valid_semver(self) -> None:
        env = _make_envelope(schema_version="2.10.3")
        assert env.schema_version == "2.10.3"

    def test_missing_patch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            _make_envelope(schema_version="1.0")

    def test_non_numeric_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            _make_envelope(schema_version="v1.0.0")


class TestTypeRegex:
    def test_valid_dotted_type(self) -> None:
        register("task.updated", "1.0.0", _TaskPayload)
        env = _make_envelope(type="task.updated")
        assert env.type == "task.updated"

    def test_single_segment_rejected(self) -> None:
        with pytest.raises(ValidationError, match="type"):
            _make_envelope(type="taskcreated")

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValidationError, match="type"):
            _make_envelope(type="Task.Created")


class TestActorKindLiteral:
    def test_valid_kinds(self) -> None:
        valid_kinds: list[ActorKind] = ["operator", "orchestrator", "worker", "system", "clawhip"]
        for kind in valid_kinds:
            actor = Actor(kind=kind, id="x")
            assert actor.kind == kind

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Actor(kind="admin", id="x")  # type: ignore[arg-type]


class TestExtraFieldsForbidden:
    def test_extra_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            EventEnvelope(  # type: ignore[call-arg]
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload={"task_id": "x"},
                request_id=_VALID_REQUEST_ID,
                unknown_extra_field="oops",
            )


class TestRoundTripViaJson:
    def test_model_validate_json_roundtrip(self) -> None:
        env = _make_envelope()
        from events.canonical import to_canonical_json

        data = to_canonical_json(env)
        env2 = EventEnvelope.model_validate_json(data)
        assert env2.event_id == env.event_id
        assert env2.type == env.type
        assert env2.schema_version == env.schema_version


class TestCreateFactoryUnregistered:
    def test_raises_event_schema_unknown(self) -> None:
        with pytest.raises(EventSchemaUnknown) as exc_info:
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="unknown.event",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload={},
                request_id=_VALID_REQUEST_ID,
            )
        assert exc_info.value.event_type == "unknown.event"


class TestCreateFactoryRegistered:
    def test_create_validates_payload_against_model(self) -> None:
        env = EventEnvelope.create(
            event_id=_VALID_EVENT_ID,
            schema_version="1.0.0",
            type="task.created",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="sys"),
            payload={"task_id": "my-task"},
            request_id=_VALID_REQUEST_ID,
        )
        assert isinstance(env.payload, _TaskPayload)
        assert env.payload.task_id == "my-task"


# ---------------------------------------------------------------------------
# Regression tests — code-review follow-up for Story 2.1
# ---------------------------------------------------------------------------


class TestEventSchemaUnknownReportsLiveTypes:
    """Fix A — EVENT_TYPES was stale-bound: create() always reported empty."""

    def test_event_schema_unknown_reports_live_registered_types(self) -> None:
        """EventEnvelope.create() must report CURRENT registered types in the error."""
        from events import schema_registry as sr

        sr.unregister_all()
        sr.register("known.type", "1.0.0", _TaskPayload)
        with pytest.raises(EventSchemaUnknown) as exc_info:
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="unknown.type",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1,
                actor=Actor(kind="system", id="sys"),
                payload={},
                request_id=_VALID_REQUEST_ID,
            )
        assert "known.type" in exc_info.value.registered_types
        sr.unregister_all()


class TestPayloadImmutable:
    """Fix C — Pydantic frozen=True blocks attribute rebind but not nested mutation."""

    def test_payload_dict_setitem_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload["k"] = "changed"

    def test_payload_dict_new_key_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload["new"] = "injected"

    def test_payload_dict_delete_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            del payload["k"]

    def test_payload_dict_update_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload.update({"k": "x"})

    def test_payload_dict_clear_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload.clear()

    def test_payload_nested_dict_also_frozen(self) -> None:
        env = _make_envelope(payload={"outer": {"inner": 1}})
        payload = cast(dict[str, Any], env.payload)
        nested = cast(dict[str, Any], payload["outer"])
        with pytest.raises(TypeError):
            nested["inner"] = 99


class TestEmittedAtNormalization:
    """Fix B / Fix E — datetime is canonicalized at parse time."""

    def test_plus_00_00_input_normalized_to_utc(self) -> None:
        plus_tz = timezone(timedelta(0))  # not UTC identity, but zero offset
        env = _make_envelope(emitted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=plus_tz))
        assert env.emitted_at.tzinfo is UTC

    def test_gmt_tzinfo_normalized_to_utc(self) -> None:
        """Any zero-offset tzinfo is normalized to UTC."""
        gmt_tz = timezone(timedelta(0), "GMT")
        env = _make_envelope(emitted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=gmt_tz))
        assert env.emitted_at.tzinfo is UTC

    def test_microsecond_precision_truncated_to_millisecond(self) -> None:
        dt = datetime(2026, 4, 21, 10, 30, 0, 123456, tzinfo=UTC)
        env = _make_envelope(emitted_at=dt)
        # 123456 microseconds truncated to 123000 (= 123 ms).
        assert env.emitted_at.microsecond == 123000


class _OtherPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    unrelated_field: str


class _ExpectedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str


class TestCreateWrongBaseModelPayload:
    """Fix F — create() must validate BaseModel payloads against the registered model."""

    def test_wrong_basemodel_subclass_coerced_via_roundtrip(self) -> None:
        from events import schema_registry as sr

        sr.unregister_all()
        sr.register("typed.event", "1.0.0", _ExpectedPayload)
        other = _OtherPayload(unrelated_field="x")
        # Should either succeed via coercion or raise ValidationError — NOT silently accept.
        with pytest.raises((ValidationError, EventValidationError)):
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="typed.event",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload=other,
                request_id=_VALID_REQUEST_ID,
            )
        sr.unregister_all()


# ---------------------------------------------------------------------------
# Story 2.2 integration tests — generator output accepted by Story 2.1 validators
# ---------------------------------------------------------------------------


class TestExtensionsField:
    """Story 2.14 — additive ``extensions`` envelope-level field (FR22 / NFR-M3)."""

    def test_extensions_defaults_to_empty_dict(self) -> None:
        """v1.0.0 envelopes that omit ``extensions`` get the default ``{}``."""
        env = _make_envelope()
        assert env.extensions == {}

    def test_explicit_extensions_round_trip_via_canonical_json(self) -> None:
        """v1.0.1 envelopes with explicit ``extensions`` round-trip byte-stable."""
        from events.canonical import to_canonical_json

        env = _make_envelope(
            schema_version="1.0.1",
            extensions={"trace_id": "abc-123", "nested": {"k": "v"}},
        )
        data = to_canonical_json(env)
        env2 = EventEnvelope.model_validate_json(data)
        assert env2.extensions == {"trace_id": "abc-123", "nested": {"k": "v"}}
        # Re-serialize: byte-identical (canonical-JSON contract).
        assert to_canonical_json(env2) == data

    def test_extensions_field_frozen_post_creation(self) -> None:
        """``frozen=True`` blocks attribute rebind on the envelope."""
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises((ValidationError, TypeError)):
            env.extensions = {"k": "changed"}


class TestExtensionsImmutable:
    """Story 2.14 code-review fix M1 / M10 — ``extensions`` deep-frozen.

    Pydantic's ``frozen=True`` blocks ``env.extensions = {...}`` (covered by
    :class:`TestExtensionsField` above) but does NOT block nested mutation
    (``env.extensions["k"] = v``). The ``_freeze_and_validate_extensions``
    validator wraps the dict in :class:`_FrozenDict` so all dict-mutation
    paths raise ``TypeError``. These tests mirror :class:`TestPayloadImmutable`.
    """

    def test_extensions_dict_setitem_rejected(self) -> None:
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises(TypeError):
            env.extensions["k"] = "changed"

    def test_extensions_dict_new_key_rejected(self) -> None:
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises(TypeError):
            env.extensions["new"] = "injected"

    def test_extensions_dict_delete_rejected(self) -> None:
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises(TypeError):
            del env.extensions["k"]

    def test_extensions_dict_update_rejected(self) -> None:
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises(TypeError):
            env.extensions.update({"k": "x"})

    def test_extensions_dict_clear_rejected(self) -> None:
        env = _make_envelope(extensions={"k": "v"})
        with pytest.raises(TypeError):
            env.extensions.clear()

    def test_extensions_nested_dict_also_frozen(self) -> None:
        env = _make_envelope(extensions={"outer": {"inner": 1}})
        nested = cast(dict[str, Any], env.extensions["outer"])
        with pytest.raises(TypeError):
            nested["inner"] = 99


class TestExtensionsRejectsNonJsonSafeValues:
    """Story 2.14 code-review fix M7 — non-JSON-safe values raise at construction.

    ``dict[str, Any]`` would otherwise silently accept ``datetime``, ``set``,
    ``bytes``, ``NaN``, ``Infinity``, ``UUID``, etc., which crash later at
    canonical-JSON serialization. The ``_assert_json_safe`` validator
    rejects them eagerly so the error fires at the offending caller.
    """

    def test_nan_rejected(self) -> None:
        with pytest.raises(ValidationError, match="NaN/Infinity"):
            _make_envelope(extensions={"k": float("nan")})

    def test_infinity_rejected(self) -> None:
        with pytest.raises(ValidationError, match="NaN/Infinity"):
            _make_envelope(extensions={"k": float("inf")})

    def test_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not JSON-safe"):
            _make_envelope(extensions={"k": datetime(2026, 4, 27, tzinfo=UTC)})

    def test_bytes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not JSON-safe"):
            _make_envelope(extensions={"k": b"bytes"})

    def test_set_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not JSON-safe"):
            _make_envelope(extensions={"k": {1, 2, 3}})

    def test_nested_non_json_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not JSON-safe"):
            _make_envelope(extensions={"outer": {"inner": b"bytes"}})


class TestGeneratorIntegration:
    """AC-7 / Story 2.2: generators produce IDs that Story 2.1 validators accept."""

    def test_envelope_accepts_generator_output(self) -> None:
        """Story 2.2 generators produce IDs Story 2.1 validators accept."""
        from random import Random

        from events import FrozenClock, new_event_id, new_request_id

        clock = FrozenClock()
        rng = Random(42)
        env = EventEnvelope(
            event_id=new_event_id(clock=clock, rng=rng),
            schema_version="1.0.0",
            type="task.created",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="system", id="x"),
            payload={"task_id": "abc"},
            request_id=new_request_id(clock=clock, rng=rng),
        )
        from events.envelope import _EVENT_ID_RE, _UUIDV7_BARE_RE

        assert _EVENT_ID_RE.match(env.event_id), f"event_id {env.event_id!r} rejected by regex"
        assert _UUIDV7_BARE_RE.match(env.request_id), (
            f"request_id {env.request_id!r} rejected by regex"
        )

    def test_deterministic_generator_produces_valid_envelope(self) -> None:
        """Seeded clock+rng give a reproducible, envelope-valid event_id."""
        from random import Random

        from events import FrozenClock, new_event_id

        clock1 = FrozenClock()
        rng1 = Random(99)
        clock2 = FrozenClock()
        rng2 = Random(99)
        eid1 = new_event_id(clock=clock1, rng=rng1)
        eid2 = new_event_id(clock=clock2, rng=rng2)
        assert eid1 == eid2
        # Both should construct without ValidationError
        env = EventEnvelope(
            event_id=eid1,
            schema_version="1.0.0",
            type="task.created",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="gen-test"),
            payload={"task_id": "gen-task"},
            request_id=_VALID_REQUEST_ID,
        )
        assert env.event_id == eid2

    def test_generator_event_id_passes_envelope_regex(self) -> None:
        """new_event_id() output satisfies the _EVENT_ID_RE from envelope.py."""
        from random import Random

        from events import FrozenClock, new_event_id
        from events.envelope import _EVENT_ID_RE

        for seed in range(5):
            eid = new_event_id(clock=FrozenClock(), rng=Random(seed))
            assert _EVENT_ID_RE.match(eid), f"Seed {seed}: {eid!r} failed regex"
