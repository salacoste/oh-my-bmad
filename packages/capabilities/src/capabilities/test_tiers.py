"""Tests for capability-tier classification and enforcement helpers."""

from __future__ import annotations

import pytest

from capabilities import (
    CallerContext,
    CapabilityDenied,
    CapabilityOk,
    Tier,
    check_tier,
)

# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------


class TestTier:
    def test_values(self) -> None:
        assert Tier.ZERO == 0  # type: ignore[comparison-overlap]  # IntEnum value equals its int at runtime
        assert Tier.ONE == 1  # type: ignore[comparison-overlap]  # IntEnum value equals its int at runtime
        assert Tier.TWO == 2  # type: ignore[comparison-overlap]  # IntEnum value equals its int at runtime
        assert Tier.THREE == 3  # type: ignore[comparison-overlap]  # IntEnum value equals its int at runtime

    def test_ordering(self) -> None:
        assert Tier.ZERO < Tier.ONE < Tier.TWO < Tier.THREE

    def test_comparison_with_int(self) -> None:
        assert Tier.THREE > 1
        assert Tier.ONE >= 1
        assert Tier.ZERO <= 0


# ---------------------------------------------------------------------------
# CallerContext
# ---------------------------------------------------------------------------


class TestCallerContext:
    def test_frozen(self) -> None:
        ctx = CallerContext(actor_kind="worker", actor_id="w-001")
        with pytest.raises(AttributeError):
            ctx.actor_kind = "operator"  # type: ignore[misc]

    def test_task_id_optional(self) -> None:
        ctx = CallerContext(actor_kind="worker", actor_id="w-001")
        assert ctx.task_id is None

    def test_task_id_provided(self) -> None:
        ctx = CallerContext(actor_kind="worker", actor_id="w-001", task_id="t-001")
        assert ctx.task_id == "t-001"

    @pytest.mark.parametrize("kind", ["operator", "orchestrator", "worker", "system", "clawhip"])
    def test_valid_actor_kinds(self, kind: str) -> None:
        ctx = CallerContext(actor_kind=kind, actor_id="id")  # type: ignore[arg-type]  # str narrowed to Literal at runtime by CallerContext validator
        assert ctx.actor_kind == kind


# ---------------------------------------------------------------------------
# check_tier — authorized cases
# ---------------------------------------------------------------------------


_AUTHORIZED: list[tuple[str, Tier]] = [
    ("operator", Tier.ZERO),
    ("operator", Tier.ONE),
    ("operator", Tier.TWO),
    ("operator", Tier.THREE),
    ("system", Tier.ZERO),
    ("system", Tier.ONE),
    ("system", Tier.TWO),
    ("system", Tier.THREE),
    ("clawhip", Tier.ZERO),
    ("clawhip", Tier.ONE),
    ("clawhip", Tier.TWO),
    ("orchestrator", Tier.ZERO),
    ("orchestrator", Tier.ONE),
    ("orchestrator", Tier.TWO),
    ("worker", Tier.ZERO),
    ("worker", Tier.ONE),
    ("worker", Tier.TWO),
]


_DENIED: list[tuple[str, Tier]] = [
    ("clawhip", Tier.THREE),
    ("orchestrator", Tier.THREE),
    ("worker", Tier.THREE),
]


class TestCheckTierAuthorized:
    @pytest.mark.parametrize(
        "actor_kind,required_tier",
        _AUTHORIZED,
        ids=[f"{a}-T{t.value}" for a, t in _AUTHORIZED],
    )
    def test_returns_ok(self, actor_kind: str, required_tier: Tier) -> None:
        caller = CallerContext(actor_kind=actor_kind, actor_id="id-1", task_id="t-1")  # type: ignore[arg-type]  # str narrowed to Literal at runtime by CallerContext validator
        result = check_tier("test_action", caller, required_tier, has_approval=True)
        assert isinstance(result, CapabilityOk)
        assert result.action == "test_action"
        assert result.caller is caller
        assert result.tier == required_tier


class TestCheckTierDenied:
    @pytest.mark.parametrize(
        "actor_kind,required_tier",
        _DENIED,
        ids=[f"{a}-T{t.value}" for a, t in _DENIED],
    )
    def test_raises_denied(self, actor_kind: str, required_tier: Tier) -> None:
        caller = CallerContext(actor_kind=actor_kind, actor_id="id-1")  # type: ignore[arg-type]  # str narrowed to Literal at runtime by CallerContext validator
        with pytest.raises(CapabilityDenied) as exc_info:
            check_tier("git_push", caller, required_tier)
        err = exc_info.value
        assert err.action == "git_push"
        assert err.actor_kind == actor_kind
        assert err.required_tier == int(required_tier)
        assert "not authorized" in err.reason or "allows Tier" in err.reason

    def test_worker_tier3_git_push(self) -> None:
        caller = CallerContext(actor_kind="worker", actor_id="w-001", task_id="t-001")
        with pytest.raises(CapabilityDenied) as exc_info:
            check_tier("git_push", caller, Tier.THREE)
        msg = str(exc_info.value)
        assert "worker" in msg
        assert "Tier.3" in msg
        assert "Tier.2" in msg


class TestCheckTierBoundary:
    def test_unknown_actor_kind_raises_denied(self) -> None:
        caller = CallerContext(actor_kind="bogus", actor_id="x")  # type: ignore[arg-type]  # str narrowed to Literal at runtime by CallerContext validator
        with pytest.raises(CapabilityDenied, match="unknown actor_kind"):
            check_tier("test_action", caller, Tier.ONE)

    def test_tier_zero_always_allowed(self) -> None:
        for kind in ("operator", "system", "clawhip", "orchestrator", "worker"):
            caller = CallerContext(actor_kind=kind, actor_id="id")
            result = check_tier("read", caller, Tier.ZERO)
            assert isinstance(result, CapabilityOk)

    def test_capability_denied_is_events_error(self) -> None:
        from events.errors import EventsError

        caller = CallerContext(actor_kind="worker", actor_id="w-001")
        with pytest.raises(EventsError):
            check_tier("git_push", caller, Tier.THREE)


class TestCheckTierApproval:
    """Tier-3 approval gate via has_approval parameter (Story 6.2)."""

    def test_tier3_denied_without_approval(self) -> None:
        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-1")
        with pytest.raises(CapabilityDenied) as exc_info:
            check_tier("git_push", caller, Tier.THREE, has_approval=False)
        assert "no_matching_approval" in exc_info.value.reason

    def test_tier3_allowed_with_approval(self) -> None:
        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-1")
        result = check_tier("git_push", caller, Tier.THREE, has_approval=True)
        assert isinstance(result, CapabilityOk)
        assert result.tier == Tier.THREE

    def test_tier2_ignores_has_approval_false(self) -> None:
        caller = CallerContext(actor_kind="worker", actor_id="w-1")
        result = check_tier("branch_create", caller, Tier.TWO, has_approval=False)
        assert isinstance(result, CapabilityOk)

    def test_tier1_ignores_has_approval_default(self) -> None:
        caller = CallerContext(actor_kind="worker", actor_id="w-1")
        result = check_tier("add_note", caller, Tier.ONE)
        assert isinstance(result, CapabilityOk)


class TestCheckTierWithApproval:
    """check_tier_with_approval async wrapper (Story 6.2)."""

    @pytest.mark.asyncio
    async def test_tier3_denied_when_lookup_returns_false(self) -> None:
        from unittest.mock import AsyncMock

        from capabilities import check_tier_with_approval

        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-1")
        lookup = AsyncMock(return_value=False)
        with pytest.raises(CapabilityDenied, match="no_matching_approval"):
            await check_tier_with_approval(
                "git_push",
                caller,
                Tier.THREE,
                approval_lookup=lookup,
            )
        lookup.assert_awaited_once_with("t-1", "git_push")

    @pytest.mark.asyncio
    async def test_tier3_allowed_when_lookup_returns_true(self) -> None:
        from unittest.mock import AsyncMock

        from capabilities import check_tier_with_approval

        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-1")
        lookup = AsyncMock(return_value=True)
        result = await check_tier_with_approval(
            "git_push",
            caller,
            Tier.THREE,
            approval_lookup=lookup,
        )
        assert isinstance(result, CapabilityOk)
        assert result.tier == Tier.THREE

    @pytest.mark.asyncio
    async def test_tier1_skips_approval_lookup(self) -> None:
        from unittest.mock import AsyncMock

        from capabilities import check_tier_with_approval

        caller = CallerContext(actor_kind="worker", actor_id="w-1")
        lookup = AsyncMock(return_value=False)
        result = await check_tier_with_approval(
            "add_note",
            caller,
            Tier.ONE,
            approval_lookup=lookup,
        )
        assert isinstance(result, CapabilityOk)
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_actor_denied_before_approval_lookup(self) -> None:
        from unittest.mock import AsyncMock

        from capabilities import check_tier_with_approval

        caller = CallerContext(actor_kind="worker", actor_id="w-1")
        lookup = AsyncMock()
        with pytest.raises(CapabilityDenied):
            await check_tier_with_approval(
                "git_push",
                caller,
                Tier.THREE,
                approval_lookup=lookup,
            )
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tier3_raises_when_lookup_is_none(self) -> None:
        """Tier-3 with approval_lookup=None raises ValueError — callers
        must supply a lookup for Tier-3 actions."""
        from capabilities import check_tier_with_approval

        caller = CallerContext(actor_kind="operator", actor_id="op-1", task_id="t-1")
        with pytest.raises(ValueError, match="approval_lookup is required"):
            await check_tier_with_approval(
                "git_push",
                caller,
                Tier.THREE,
                approval_lookup=None,
            )
