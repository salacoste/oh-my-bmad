"""Contract tests for Story 11.2 — event payload schema registration + fixtures.

Story 11.2 registers three event types (``task.approval_signed`` at 1.1.0
additive bump, ``key.rotated`` NEW, ``capability.denied`` NEW per Epic 10
retro DD5) and ships frozen JSON fixtures under
``tests/contract/fixtures/`` proving the payload schemas are stable.

What the tests cover:

* **AC1 + AC4** — ``task.approval_signed`` registered at BOTH 1.0.0 (Story
  11.1 minimal registration) and 1.1.0 (this story's additive bump);
  fixture round-trips through :class:`events.EventEnvelope`.
* **AC2 + AC4** — ``key.rotated`` registered at 1.1.0;
  :class:`KeyRotatedPayload` rejects ``previous == new`` fingerprint
  (D3 cross-field invariant); fixture round-trips.
* **AC3 + AC4** — ``capability.denied`` registered at 1.1.0; fixture
  round-trips; **enum-drift contract**: payload ``tier`` / ``boundary``
  ``Literal`` values match Story 10.4's ``_CAPABILITY_TIERS`` /
  ``_CAPABILITY_BOUNDARIES`` constants in
  ``metrics-subscriber/app/metrics.py``. If either side drifts, this
  test fails — load-bearing for DD5 emission's downstream metric
  cardinality contract.

D4 outcome: tests under ``tests/`` are outside
``scripts/check_imports.py``'s ``SCAN_ROOTS`` (only
``packages/services/mcp-servers`` are scanned). The test can therefore
import directly from BOTH ``events.payloads`` AND
``metrics_subscriber.app.metrics`` without violating the import-graph
rule — no enum extraction was required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

# Side-effect import: ``registry_state.domain.event_types`` performs the
# canonical ``register()`` calls at module load. Without this import the
# schema registry would be empty and ``EventEnvelope.create`` /
# ``REGISTRY`` lookups would miss the Story 11.2 entries.
import registry_state.domain.event_types  # noqa: F401, IMP001 — test
from events import (
    CapabilityDeniedPayload,
    EventEnvelope,
    KeyRotatedPayload,
    TaskApprovalSignedPayload,
)
from events.schema_registry import REGISTRY

# D4: direct cross-service import from metrics-subscriber is permitted
# under ``tests/`` (outside ``check_imports.py`` scan roots). Keeps the
# Story 10.4 enum constants as single source of truth — the drift test
# reads them at runtime.
from metrics_subscriber.app.metrics import (  # noqa: IMP001 — test
    _CAPABILITY_BOUNDARIES,
    _CAPABILITY_TIERS,
)
from pydantic import ValidationError

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC1 — task.approval_signed registered at BOTH 1.0.0 and 1.1.0.
# ---------------------------------------------------------------------------


def test_task_approval_signed_registered_at_both_schema_versions() -> None:
    """AC1 self-verification — both 1.0.0 (Story 11.1) and 1.1.0 (Story 11.2)
    are present in REGISTRY, with the SAME payload class (additive bump)."""
    key_v100 = ("task.approval_signed", "1.0.0")
    key_v110 = ("task.approval_signed", "1.1.0")
    assert key_v100 in REGISTRY, "Story 11.1 1.0.0 registration missing"
    assert key_v110 in REGISTRY, "Story 11.2 1.1.0 additive bump missing"
    # Additive bump invariant: same payload class on both keys.
    assert REGISTRY[key_v100] is REGISTRY[key_v110], (
        "task.approval_signed must use the same payload class at 1.0.0 and "
        "1.1.0 (Story 11.2 is a pure additive bump — no field changes)"
    )


def test_task_approval_signed_fixture_parses() -> None:
    """AC4 — frozen JSON fixture round-trips through EventEnvelope."""
    blob = _load_fixture("task.approval_signed.v1.1.0.json")
    env = EventEnvelope.model_validate_json(blob)
    assert env.type == "task.approval_signed"
    assert env.schema_version == "1.1.0"
    # Deep-equal payload field-by-field. ``env.payload`` is typed
    # ``dict[str, Any] | BaseModel``; use isinstance-guarded branch to
    # support both shapes without masking type ambiguity (P1-M1).
    expected_payload = json.loads(blob)["payload"]
    payload_data = env.payload if isinstance(env.payload, dict) else env.payload.model_dump()
    assert payload_data == expected_payload


# ---------------------------------------------------------------------------
# AC2 — key.rotated registration + D3 no-op-rotation rejection + fixture.
# ---------------------------------------------------------------------------


def test_key_rotated_registered_at_1_1_0() -> None:
    """AC2 self-verification — key.rotated registered at 1.1.0."""
    assert ("key.rotated", "1.1.0") in REGISTRY
    assert REGISTRY[("key.rotated", "1.1.0")] is KeyRotatedPayload


def test_key_rotated_fixture_parses() -> None:
    """AC4 — frozen JSON fixture round-trips through EventEnvelope."""
    blob = _load_fixture("key.rotated.v1.1.0.json")
    env = EventEnvelope.model_validate_json(blob)
    assert env.type == "key.rotated"
    assert env.schema_version == "1.1.0"
    expected_payload = json.loads(blob)["payload"]
    payload_data = env.payload if isinstance(env.payload, dict) else env.payload.model_dump()
    assert payload_data == expected_payload
    # Fingerprints differ (D3 invariant honored in the fixture).
    assert payload_data["previous_key_fingerprint"] != payload_data["new_key_fingerprint"]


def test_key_rotated_rejects_same_fingerprint() -> None:
    """AC2 D3 — KeyRotatedPayload.model_validator rejects no-op rotation.

    A ``previous_key_fingerprint == new_key_fingerprint`` payload is not a
    rotation; it could be a detection bug or a replay-attack attempting to
    emit a no-op rotation event. The cross-field validator MUST reject.
    """
    from datetime import UTC, datetime

    with pytest.raises(ValidationError, match="previous_key_fingerprint"):
        KeyRotatedPayload(
            rotated_at=datetime(2026, 1, 1, tzinfo=UTC),
            previous_key_fingerprint="0123456789abcdef",
            new_key_fingerprint="0123456789abcdef",
            actor_id="operator",
        )


# ---------------------------------------------------------------------------
# AC3 — capability.denied registration + fixture + enum-drift contract.
# ---------------------------------------------------------------------------


def test_capability_denied_registered_at_1_1_0() -> None:
    """AC3 self-verification — capability.denied registered at 1.1.0."""
    assert ("capability.denied", "1.1.0") in REGISTRY
    assert REGISTRY[("capability.denied", "1.1.0")] is CapabilityDeniedPayload


def test_capability_denied_fixture_parses() -> None:
    """AC4 — frozen JSON fixture round-trips through EventEnvelope."""
    blob = _load_fixture("capability.denied.v1.1.0.json")
    env = EventEnvelope.model_validate_json(blob)
    assert env.type == "capability.denied"
    assert env.schema_version == "1.1.0"
    expected_payload = json.loads(blob)["payload"]
    payload_data = env.payload if isinstance(env.payload, dict) else env.payload.model_dump()
    assert payload_data == expected_payload


def test_capability_denied_payload_tier_enum_matches_story_10_4_metrics() -> None:
    """AC3 D4 — enum-drift contract test (load-bearing for DD5).

    ``CapabilityDeniedPayload.tier`` / ``boundary`` use ``Literal[...]``
    bounded enums that MUST match Story 10.4's
    ``_CAPABILITY_TIERS`` / ``_CAPABILITY_BOUNDARIES`` constants — the
    counter ``omb_capability_denied_total{tier,boundary}`` is
    pre-populated with one zero-value sample per combination, so any
    drift between the payload schema and the metric label values would
    break cardinality on emission.

    Failure mode: a future story bumps the payload tier enum (e.g.
    adds ``tier4``) without updating ``_CAPABILITY_TIERS`` — this test
    fails immediately at CI.
    """
    payload_tiers = set(get_args(CapabilityDeniedPayload.model_fields["tier"].annotation))
    payload_boundaries = set(get_args(CapabilityDeniedPayload.model_fields["boundary"].annotation))
    assert payload_tiers == set(_CAPABILITY_TIERS), (
        f"CapabilityDeniedPayload.tier Literal values "
        f"{payload_tiers!r} drifted from Story 10.4 "
        f"_CAPABILITY_TIERS {set(_CAPABILITY_TIERS)!r}. Sync both ends."
    )
    assert payload_boundaries == set(_CAPABILITY_BOUNDARIES), (
        f"CapabilityDeniedPayload.boundary Literal values "
        f"{payload_boundaries!r} drifted from Story 10.4 "
        f"_CAPABILITY_BOUNDARIES {set(_CAPABILITY_BOUNDARIES)!r}. "
        "Sync both ends."
    )


# ---------------------------------------------------------------------------
# Pass-1 review additions — P1-H1 (actor_id cap) + P1-L5 (missing tests).
# ---------------------------------------------------------------------------


_ACTOR_ID_OVERSIZED = "a" * 129  # one over the 128-char invariant.


def _construct_for_actor_id(payload_cls: type, actor_id: str) -> object:
    """Build a minimal-valid instance of *payload_cls* with the given *actor_id*.

    Each Story 11.x payload class has distinct required fields; this helper
    centralises the construction logic for the parametrised P1-H1 test so
    the asserted invariant (``ValidationError`` on oversized actor_id) is
    isolated from per-class shape boilerplate.
    """
    from datetime import UTC, datetime

    if payload_cls is TaskApprovalSignedPayload:
        return payload_cls(
            task_id="t-00000000-0000-7000-8000-000000000001",
            decision_id="d-00000000-0000-7000-8000-000000000002",
            actor_id=actor_id,
            action="approve",
            timestamp=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
            hmac_sha256="0" * 64,
        )
    if payload_cls is KeyRotatedPayload:
        return payload_cls(
            rotated_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
            previous_key_fingerprint="0123456789abcdef",
            new_key_fingerprint="fedcba9876543210",
            actor_id=actor_id,
        )
    if payload_cls is CapabilityDeniedPayload:
        return payload_cls(
            tier="tier1",
            boundary="http",
            actor_id=actor_id,
            attempted_action="task.create",
        )
    raise AssertionError(f"unhandled payload class in test helper: {payload_cls!r}")


@pytest.mark.parametrize(
    "payload_cls",
    [TaskApprovalSignedPayload, KeyRotatedPayload, CapabilityDeniedPayload],
)
def test_actor_id_rejects_oversized_string(payload_cls: type) -> None:
    """Pass-1 P1-H1 — every ``actor_id`` field enforces ``max_length=128``.

    Codebase-wide invariant (see ``payloads.py`` module docstring).
    Closes Story 11.1 inheritance gap on ``TaskApprovalSignedPayload`` +
    applies same cap to Story 11.2's new ``KeyRotatedPayload`` and
    ``CapabilityDeniedPayload``. Protects append-only audit log from
    bloat-injection via unbounded service-account identifiers.
    """
    with pytest.raises(ValidationError, match="actor_id"):
        _construct_for_actor_id(payload_cls, _ACTOR_ID_OVERSIZED)


def test_key_rotated_rejects_naive_datetime() -> None:
    """Pass-1 P1-L5 — ``KeyRotatedPayload.rotated_at`` is :class:`AwareDatetime`.

    Pydantic ``AwareDatetime`` rejects naive ``datetime`` values (no
    ``tzinfo``). Audit-log timestamps MUST carry timezone info — naive
    timestamps break cross-deployment ordering invariants when JSONL files
    move between hosts in different local timezones.
    """
    from datetime import datetime

    with pytest.raises(ValidationError, match=r"(?i)tz|timezone|aware|naive"):
        KeyRotatedPayload(
            rotated_at=datetime(2026, 5, 20, 12, 0, 0),  # noqa: DTZ001 — naive on purpose
            previous_key_fingerprint="0123456789abcdef",
            new_key_fingerprint="fedcba9876543210",
            actor_id="operator",
        )


# ---------------------------------------------------------------------------
# Story 11.3 — approval.inbox_opened registration + fixture + payload contract
# ---------------------------------------------------------------------------


def test_approval_inbox_opened_registered_at_1_1_0() -> None:
    """Story 11.3 AC3 — approval.inbox_opened registered at schema_version 1.1.0."""
    from events import ApprovalInboxOpenedPayload

    assert ("approval.inbox_opened", "1.1.0") in REGISTRY
    assert REGISTRY[("approval.inbox_opened", "1.1.0")] is ApprovalInboxOpenedPayload


def test_approval_inbox_opened_fixture_parses() -> None:
    """Story 11.3 AC3 — frozen JSON fixture round-trips through EventEnvelope."""
    blob = _load_fixture("approval.inbox_opened.v1.1.0.json")
    env = EventEnvelope.model_validate_json(blob)
    assert env.type == "approval.inbox_opened"
    assert env.schema_version == "1.1.0"
    expected_payload = json.loads(blob)["payload"]
    payload_data = env.payload if isinstance(env.payload, dict) else env.payload.model_dump()
    assert payload_data == expected_payload


def test_approval_inbox_opened_rejects_negative_thread_id() -> None:
    """Story 11.3 AC3 — ``inbox_thread_id`` must be ``>= 1`` (Telegram contract).

    Telegram Forum-Topic ``message_thread_id`` is always positive int64. A
    negative value would either be a deserialization bug or an attempt to
    inject a sentinel value the downstream router would misinterpret.
    """
    from datetime import UTC, datetime

    from events import ApprovalInboxOpenedPayload

    with pytest.raises(ValidationError, match="inbox_thread_id"):
        ApprovalInboxOpenedPayload(
            operator_chat_id=-1001234567890,
            inbox_thread_id=-1,  # invalid — must be >= 1
            opened_at=datetime.now(UTC),
            opened_by_actor_id="operator",
        )


def test_approval_inbox_opened_rejects_zero_thread_id() -> None:
    """Story 11.3 AC3 — ``inbox_thread_id == 0`` is invalid (Telegram contract).

    Telegram thread_ids are >= 1; zero is sometimes used as a sentinel
    meaning "no thread" / "general" but it cannot be a valid pinned-inbox
    target — the row would route approval requests to the chat's general
    thread instead of a dedicated Forum-Topic.
    """
    from datetime import UTC, datetime

    from events import ApprovalInboxOpenedPayload

    with pytest.raises(ValidationError, match="inbox_thread_id"):
        ApprovalInboxOpenedPayload(
            operator_chat_id=-1001234567890,
            inbox_thread_id=0,  # invalid — must be >= 1
            opened_at=datetime.now(UTC),
            opened_by_actor_id="operator",
        )


def test_capability_denied_with_reason_none_round_trips() -> None:
    """Pass-1 P1-L5 — ``CapabilityDeniedPayload.reason`` is optional.

    The field defaults to ``None``; explicit ``reason=None`` construction
    must validate cleanly. Locks the optionality contract — a future
    pattern-tightening migration that accidentally drops ``None`` from
    the allowed values would fail this test immediately.
    """
    payload = CapabilityDeniedPayload(
        tier="tier1",
        boundary="mcp",
        actor_id="operator",
        attempted_action="task.create",
        reason=None,
    )
    assert payload.reason is None
    # Round-trip through model_dump → model_validate to confirm the
    # nullable shape survives the serialization boundary.
    round_tripped = CapabilityDeniedPayload.model_validate(payload.model_dump())
    assert round_tripped.reason is None
