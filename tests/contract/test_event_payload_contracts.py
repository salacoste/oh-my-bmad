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
from events import CapabilityDeniedPayload, EventEnvelope, KeyRotatedPayload
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
    # Deep-equal payload field-by-field. ``env.payload`` is a frozen dict
    # view of the JSON object; compare against the original parsed dict.
    expected_payload = json.loads(blob)["payload"]
    assert dict(env.payload) == expected_payload  # type: ignore[arg-type]


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
    assert dict(env.payload) == expected_payload  # type: ignore[arg-type]
    # Fingerprints differ (D3 invariant honored in the fixture).
    assert (
        env.payload["previous_key_fingerprint"]  # type: ignore[index]
        != env.payload["new_key_fingerprint"]  # type: ignore[index]
    )


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
    assert dict(env.payload) == expected_payload  # type: ignore[arg-type]


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
