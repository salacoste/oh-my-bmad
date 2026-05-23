"""Story 11.2.2 tests — MCP-boundary capability.denied emission helper.

Coverage:
- `build_capability_denied_payload`: tier mapping, actor_id None fallback,
  enum-drift contract against Story 11.2.1's source-of-truth map.
- `emit_capability_denied_on_deny` decorator: happy path (no exception),
  CapabilityDenied catch + emit + re-raise, PD-1 fail-soft on emitter
  error, CancelledError re-raise discipline, non-CapabilityDenied errors
  pass through untouched.

Mirrors Story 11.2.1 PP6/PP7 discipline — tests round-trip through
Pydantic where applicable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from capabilities.emit import (
    _TIER_INT_TO_LITERAL,
    build_capability_denied_payload,
    emit_capability_denied_on_deny,
)
from capabilities.tiers import CapabilityDenied, Tier

# ---------------------------------------------------------------------------
# build_capability_denied_payload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Tier-mapping, actor_id fallback, schema shape."""

    def test_payload_shape_with_explicit_actor_id(self) -> None:
        p = build_capability_denied_payload(
            required_tier=Tier.TWO,
            boundary="mcp",
            actor_id="worker-01",
            attempted_action="task.add_note",
            reason="actor_kind 'worker' allows Tier.2 at most; action requires Tier.2",
        )
        assert p == {
            "tier": "tier2",
            "boundary": "mcp",
            "actor_id": "worker-01",
            "attempted_action": "task.add_note",
            "reason": "actor_kind 'worker' allows Tier.2 at most; action requires Tier.2",
        }

    def test_payload_actor_id_none_falls_back_to_unknown(self) -> None:
        """PP4 (Story 11.2.1 mirror): None must become "unknown" before reaching Pydantic."""
        p = build_capability_denied_payload(
            required_tier=Tier.THREE,
            boundary="mcp",
            actor_id=None,
            attempted_action="emit_event",
            reason="no_matching_approval",
        )
        assert p["actor_id"] == "unknown"

    def test_payload_actor_id_empty_string_falls_back_to_unknown(self) -> None:
        """Empty string also short-circuits via ``or "unknown"``."""
        p = build_capability_denied_payload(
            required_tier=Tier.ONE,
            boundary="mcp",
            actor_id="",
            attempted_action="task.add_note",
            reason="r",
        )
        assert p["actor_id"] == "unknown"

    @pytest.mark.parametrize(
        ("tier", "expected_literal"),
        [(Tier.ONE, "tier1"), (Tier.TWO, "tier2"), (Tier.THREE, "tier3")],
    )
    def test_tier_literal_mapping(self, tier: Tier, expected_literal: str) -> None:
        p = build_capability_denied_payload(
            required_tier=tier,
            boundary="mcp",
            actor_id="x",
            attempted_action="y",
            reason="z",
        )
        assert p["tier"] == expected_literal

    def test_tier_zero_raises_key_error(self) -> None:
        """Tier.ZERO is read-only and CANNOT generate a denial event —
        the payload schema literal Literal["tier1","tier2","tier3"]
        excludes "tier0" intentionally. KeyError here lands in the
        decorator's PD-1 swallow at runtime, but the test exists to
        document the contract: callers MUST NOT pass Tier.ZERO.
        """
        with pytest.raises(KeyError):
            build_capability_denied_payload(
                required_tier=Tier.ZERO,
                boundary="mcp",
                actor_id="x",
                attempted_action="y",
                reason="z",
            )


def test_tier_int_to_literal_covers_every_denyable_tier_member() -> None:
    """PP3 (Story 11.2.1 mirror): enum-drift contract.

    If ``Tier`` gains a new denyable member (e.g. ``Tier.FOUR``) without
    extending ``_TIER_INT_TO_LITERAL``, ``build_capability_denied_payload``
    raises KeyError → swallowed by PD-1 in the decorator → audit lost.
    This test catches drift at test time. ``Tier.ZERO`` excluded — see
    docstring on ``test_tier_zero_raises_key_error``.
    """
    denyable = {t.value for t in Tier if t != Tier.ZERO}
    assert set(_TIER_INT_TO_LITERAL.keys()) == denyable, (
        f"_TIER_INT_TO_LITERAL keys {set(_TIER_INT_TO_LITERAL)} drifted from "
        f"denyable Tier members {denyable}. Add the missing ``tierN`` literal "
        "to capabilities/emit.py AND services/registry-api/.../middleware.py "
        "(mirror discipline)."
    )


def test_tier_int_to_literal_matches_registry_api_source_of_truth() -> None:
    """Cross-module mirror discipline: this module's ``_TIER_INT_TO_LITERAL``
    must byte-equal the one in ``services/registry-api/.../middleware.py``
    (Story 11.2.1's source-of-truth). If they drift, the HTTP boundary
    and MCP boundary emit different ``tier`` labels for the same denial.
    """
    from registry_api.adapters.middleware import (  # noqa: IMP001 — tests/* can cross services
        _TIER_INT_TO_LITERAL as _HTTP_MAP,
    )

    assert _TIER_INT_TO_LITERAL == _HTTP_MAP, (
        f"MCP-boundary map {_TIER_INT_TO_LITERAL} drifted from "
        f"HTTP-boundary map {_HTTP_MAP}. Mirror update both maps together."
    )


# ---------------------------------------------------------------------------
# emit_capability_denied_on_deny — decorator
# ---------------------------------------------------------------------------


class _RecordingEmitter:
    """Test double for ``CapabilityDeniedEmitter`` — records each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.side_effect: BaseException | None = None

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.calls.append((event_type, payload))
        if self.side_effect is not None:
            raise self.side_effect


class TestDecorator:
    """Story 11.2.2 — handler decorator wraps tier-gated MCP tools."""

    @pytest.mark.asyncio
    async def test_happy_path_no_exception(self) -> None:
        """Decorator passes through handler return value when no denial."""
        emitter = _RecordingEmitter()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
        )
        async def handler() -> dict[str, str]:
            return {"ok": "true"}

        result = await handler()
        assert result == {"ok": "true"}
        assert emitter.calls == [], "no emission on happy path"

    @pytest.mark.asyncio
    async def test_capability_denied_emits_and_reraises(self) -> None:
        """AC1: CapabilityDenied triggers emission AND re-raises (AC6 contract)."""
        emitter = _RecordingEmitter()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
            get_actor_id=lambda *_, **__: "worker-01",
        )
        async def handler() -> None:
            raise CapabilityDenied(
                action="task.add_note",
                actor_kind="worker",
                required_tier=2,
                reason="actor_kind 'worker' allows Tier.2 at most",
            )

        with pytest.raises(CapabilityDenied) as exc_info:
            await handler()

        # Original exception preserved (AC6).
        assert exc_info.value.action == "task.add_note"
        assert exc_info.value.required_tier == 2

        # Emission happened with the correct payload.
        assert len(emitter.calls) == 1
        event_type, payload = emitter.calls[0]
        assert event_type == "capability.denied"
        assert payload == {
            "tier": "tier2",
            "boundary": "mcp",
            "actor_id": "worker-01",
            "attempted_action": "task.add_note",
            "reason": "actor_kind 'worker' allows Tier.2 at most",
        }

    @pytest.mark.asyncio
    async def test_emitter_failure_does_not_block_reraise(self) -> None:
        """PD-1 fail-soft: broken emitter MUST NOT mask the original CapabilityDenied."""
        emitter = _RecordingEmitter()
        emitter.side_effect = RuntimeError("simulated clawhip-bridge unreachable")

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
        )
        async def handler() -> None:
            raise CapabilityDenied(
                action="task.add_note",
                actor_kind="worker",
                required_tier=2,
                reason="r",
            )

        with pytest.raises(CapabilityDenied):
            await handler()

        # Emitter was invoked but failed — fail-soft swallowed it.
        assert len(emitter.calls) == 1

    @pytest.mark.asyncio
    async def test_emitter_failure_logged_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """PD-1 fail-soft: emission failure must be observable via ERROR log."""
        emitter = _RecordingEmitter()
        emitter.side_effect = RuntimeError("simulated broken pipe")

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.attach_artifact",
        )
        async def handler() -> None:
            raise CapabilityDenied(
                action="task.attach_artifact",
                actor_kind="worker",
                required_tier=2,
                reason="r",
            )

        with (
            caplog.at_level(logging.ERROR, logger="capabilities.emit"),
            pytest.raises(CapabilityDenied),
        ):
            await handler()

        failure_logs = [
            rec for rec in caplog.records if rec.message == "capability_denied_emission_failed"
        ]
        assert failure_logs, "PD-1 fail-soft must log emission failure at ERROR"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_emitter(self) -> None:
        """PP1 (Story 11.2.1 mirror): CancelledError from emitter MUST propagate."""
        emitter = _RecordingEmitter()
        emitter.side_effect = asyncio.CancelledError()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
        )
        async def handler() -> None:
            raise CapabilityDenied(
                action="task.add_note",
                actor_kind="worker",
                required_tier=2,
                reason="r",
            )

        # CancelledError NOT CapabilityDenied — cancellation wins.
        with pytest.raises(asyncio.CancelledError):
            await handler()

    @pytest.mark.asyncio
    async def test_non_capability_denied_exception_passes_through(self) -> None:
        """Other exceptions are NOT caught by the decorator — pass through unchanged."""
        emitter = _RecordingEmitter()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
        )
        async def handler() -> None:
            raise ValueError("bad arg")

        with pytest.raises(ValueError, match="bad arg"):
            await handler()
        assert emitter.calls == [], "non-CapabilityDenied must not trigger emission"

    @pytest.mark.asyncio
    async def test_get_actor_id_default_yields_unknown(self) -> None:
        """Default ``get_actor_id`` returns None → payload actor_id = "unknown"."""
        emitter = _RecordingEmitter()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
        )
        async def handler(*, caller_trace_id: str) -> None:
            raise CapabilityDenied(
                action="task.add_note",
                actor_kind="worker",
                required_tier=2,
                reason="r",
            )

        with pytest.raises(CapabilityDenied):
            await handler(caller_trace_id="abc")

        assert emitter.calls[0][1]["actor_id"] == "unknown"

    @pytest.mark.asyncio
    async def test_get_actor_id_receives_handler_args(self) -> None:
        """``get_actor_id`` receives the same args/kwargs the handler did."""
        emitter = _RecordingEmitter()
        captured: dict[str, Any] = {}

        def _extract(*args: Any, **kwargs: Any) -> str:
            captured["args"] = args
            captured["kwargs"] = kwargs
            value = kwargs.get("actor_id", "fallback")
            assert isinstance(value, str)
            return value

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.add_note",
            get_actor_id=_extract,
        )
        async def handler(*, actor_id: str, payload: str) -> None:
            raise CapabilityDenied(
                action="task.add_note", actor_kind="worker", required_tier=2, reason="r"
            )

        with pytest.raises(CapabilityDenied):
            await handler(actor_id="worker-7", payload="x")

        assert captured["kwargs"] == {"actor_id": "worker-7", "payload": "x"}
        assert emitter.calls[0][1]["actor_id"] == "worker-7"

    @pytest.mark.asyncio
    async def test_payload_round_trips_through_pydantic_model(self) -> None:
        """PP6 (Story 11.2.1 mirror): emitted payload validates against the
        canonical ``CapabilityDeniedPayload`` so a field rename / type
        change fails the test instead of slipping through dict-shape only.
        """
        from events.payloads import CapabilityDeniedPayload

        emitter = _RecordingEmitter()

        @emit_capability_denied_on_deny(
            boundary="mcp",
            emitter=emitter,
            attempted_action="task.attach_artifact",
            get_actor_id=lambda *_, **__: "worker-42",
        )
        async def handler() -> None:
            raise CapabilityDenied(
                action="task.attach_artifact",
                actor_kind="worker",
                required_tier=3,
                reason="no_matching_approval",
            )

        with pytest.raises(CapabilityDenied):
            await handler()

        payload_dict = emitter.calls[0][1]
        # Round-trip — Pydantic validation must succeed.
        model = CapabilityDeniedPayload.model_validate(payload_dict)
        assert model.tier == "tier3"
        assert model.boundary == "mcp"
        assert model.actor_id == "worker-42"
        assert model.attempted_action == "task.attach_artifact"
        assert model.reason == "no_matching_approval"
