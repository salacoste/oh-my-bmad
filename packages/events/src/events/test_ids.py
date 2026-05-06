"""Unit tests for events.ids — UUIDv7 generators + prefixed-ID helpers.

AC-6 / Story 2.2: ~15 tests.
"""

from __future__ import annotations

import re
from random import Random

from events.clock import FrozenClock, TickingClock
from events.ids import (
    new_event_id,
    new_idempotency_key,
    new_request_id,
    new_session_id,
    new_task_id,
    new_uuid7,
    new_worker_id,
    parse_prefix,
)

_UUID7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class TestUuid7Shape:
    def test_matches_canonical_regex(self) -> None:
        uid = new_uuid7()
        assert _UUID7_RE.match(uid), f"UUID7 shape mismatch: {uid!r}"

    def test_version_nibble_is_7(self) -> None:
        uid = new_uuid7()
        # 13th hex char (0-indexed: position 14 after removing 2 hyphens at 8 and 13)
        # canonical form: xxxxxxxx-xxxx-7xxx-xxxx-xxxxxxxxxxxx
        assert uid[14] == "7", f"Version nibble wrong: {uid!r}"

    def test_variant_nibble_in_89ab(self) -> None:
        uid = new_uuid7()
        # 17th hex char: xxxxxxxx-xxxx-7xxx-Nxxx-xxxxxxxxxxxx (N is at index 19)
        assert uid[19] in "89ab", f"Variant nibble wrong: {uid!r}"

    def test_deterministic_with_fixed_clock_and_rng(self) -> None:
        clock = FrozenClock()
        rng = Random(42)
        a = new_uuid7(clock=clock, rng=rng)
        clock2 = FrozenClock()
        rng2 = Random(42)
        b = new_uuid7(clock=clock2, rng=rng2)
        assert a == b

    def test_different_rng_seeds_produce_different_output(self) -> None:
        clock = FrozenClock()
        a = new_uuid7(clock=clock, rng=Random(1))
        clock2 = FrozenClock()
        b = new_uuid7(clock=clock2, rng=Random(2))
        assert a != b

    def test_timestamp_bits_round_trip(self) -> None:
        clock = FrozenClock()
        uid = new_uuid7(clock=clock)
        # Extract first 48 bits: first 8 hex chars + first 4 of second group (12 chars total)
        hex_ts = uid.replace("-", "")[:12]
        extracted_ms = int(hex_ts, 16)
        expected_ms = int(clock.now().timestamp() * 1000)
        assert extracted_ms == expected_ms

    def test_time_ordering_with_ticking_clock(self) -> None:
        tc = TickingClock()
        rng = Random(42)
        ids = [new_uuid7(clock=tc, rng=rng) for _ in range(100)]
        assert ids == sorted(ids), "UUIDv7 lex order must match insertion order"
        assert len(set(ids)) == len(ids), "duplicate UUIDs generated"
        # Adjacent UUIDs must differ by exactly 1 ms in their first-48-bit timestamp.
        timestamps = [int(uid.replace("-", "")[:12], 16) for uid in ids]
        deltas = [b - a for a, b in zip(timestamps[:-1], timestamps[1:], strict=True)]
        assert all(d == 1 for d in deltas), f"expected 1-ms deltas; got {set(deltas)}"


class TestPrefixedGenerators:
    def test_new_task_id_prefix(self) -> None:
        assert new_task_id().startswith("t-")

    def test_new_session_id_prefix(self) -> None:
        assert new_session_id().startswith("s-")

    def test_new_event_id_prefix(self) -> None:
        assert new_event_id().startswith("e-")

    def test_new_idempotency_key_no_prefix(self) -> None:
        key = new_idempotency_key()
        # Bare UUIDv7 — second char is not "-"
        assert key[1] != "-"
        assert _UUID7_RE.match(key)

    def test_new_request_id_no_prefix(self) -> None:
        rid = new_request_id()
        assert rid[1] != "-"
        assert _UUID7_RE.match(rid)

    def test_new_event_id_matches_envelope_regex(self) -> None:
        from events.envelope import _EVENT_ID_RE

        eid = new_event_id(clock=FrozenClock(), rng=Random(42))
        assert _EVENT_ID_RE.match(eid), f"event_id {eid!r} rejected by envelope regex"


class TestParsePrefix:
    def test_task_id_round_trip(self) -> None:
        tid = new_task_id(clock=FrozenClock(), rng=Random(42))
        result = parse_prefix(tid)
        assert result is not None
        prefix, core = result
        assert prefix == "t"
        assert len(core) == 36

    def test_event_id_round_trip(self) -> None:
        eid = new_event_id(clock=FrozenClock(), rng=Random(42))
        result = parse_prefix(eid)
        assert result is not None
        prefix, core = result
        assert prefix == "e"

    def test_bare_uuid_returns_none(self) -> None:
        bare = new_uuid7(clock=FrozenClock(), rng=Random(42))
        assert parse_prefix(bare) is None

    def test_unknown_prefix_returns_none(self) -> None:
        # "x-" is not a recognized prefix
        fake = "x-" + new_uuid7(clock=FrozenClock(), rng=Random(42))
        assert parse_prefix(fake) is None


class TestParsePrefixHardening:
    def test_rejects_non_uuid_core(self) -> None:
        assert parse_prefix("e-" + "x" * 36) is None
        assert parse_prefix("t-not-a-valid-uuid-at-all-just-garbage") is None

    def test_rejects_short_or_empty(self) -> None:
        assert parse_prefix("") is None
        assert parse_prefix("e") is None
        assert parse_prefix("e-") is None

    def test_rejects_non_str(self) -> None:
        assert parse_prefix(None) is None  # type: ignore[arg-type]
        assert parse_prefix(42) is None  # type: ignore[arg-type]
        assert parse_prefix(b"e-xxx") is None  # type: ignore[arg-type]

    def test_rejects_unknown_prefix(self) -> None:
        from random import Random

        from events.clock import FrozenClock
        from events.ids import new_uuid7

        uid = new_uuid7(clock=FrozenClock(), rng=Random(42))
        assert parse_prefix("x-" + uid) is None

    def test_recognizes_w_prefix(self) -> None:
        wid = new_worker_id(clock=FrozenClock(), rng=Random(42))
        result = parse_prefix(wid)
        assert result is not None
        assert result[0] == "w"


class TestWorkerId:
    def test_has_w_prefix(self) -> None:
        assert new_worker_id().startswith("w-")

    def test_uuidv7_core_matches_regex(self) -> None:
        wid = new_worker_id()
        core = wid[2:]
        assert _UUID7_RE.match(core)

    def test_deterministic_with_injected_clock_rng(self) -> None:
        a = new_worker_id(clock=FrozenClock(), rng=Random(42))
        b = new_worker_id(clock=FrozenClock(), rng=Random(42))
        assert a == b
