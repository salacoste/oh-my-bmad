"""Unit tests for TelegramSink — Story 3.9 AC-7 / AC-9.

5 tests:
1. Happy-path dispatch — task.* event with binding → send_to_thread called.
2. Skip on missing chat_id — binding has reply_to but chat_id is None.
3. Skip on missing reply_to_message_id — binding has chat_id but reply_to is None.
4. Skip non-task event — event type does not start with "task." → no dispatch.
5. Placeholder renderer output shape — text is "Task <id>: <type>" HTML-escaped.
"""

from __future__ import annotations

from pathlib import Path
from random import Random
from unittest.mock import AsyncMock, MagicMock

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.schema_registry import register as _reg

from clawhip_daemon.adapters.sinks.telegram_sink import TelegramSink, _render

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-sink")


def _ensure_task_created_registered() -> None:
    """Register task.created 1.1.0 so EventEnvelope.create succeeds in tests."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        ServiceCrashedPayload,
        TaskCompletedPayload,
        TaskCreatedPayload,
    )

    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.created", "1.1.0", TaskCreatedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)
    _reg("service.crashed", "1.0.0", ServiceCrashedPayload)


def _task_created_envelope(task_id: str, *, mono_ns: int = 1_000_000) -> EventEnvelope:
    """Build a task.created envelope."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import TaskCreatedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(42)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(task_id=task_id, title="test"),
        request_id=rid,
    )


def _task_completed_envelope(task_id: str, *, mono_ns: int = 2_000_000) -> EventEnvelope:
    """Build a task.completed envelope."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import TaskCompletedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(77)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCompletedPayload(task_id=task_id, summary="done"),
        request_id=rid,
    )


def _service_crashed_envelope(*, mono_ns: int = 3_000_000) -> EventEnvelope:
    """Build a service.crashed envelope (non-task event)."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import ServiceCrashedPayload  # noqa: IMP001, I001 — Story 2.9 AC-16, inline import

    rng = Random(11)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="service.crashed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=ServiceCrashedPayload(service="worker", exit_code=1),
        request_id=rid,
    )


def _make_sink(
    *,
    outbound: object | None = None,
    registry_response: dict[str, object] | None = None,
    registry_status: int = 200,
    base_dir: Path | None = None,
) -> TelegramSink:
    """Build a TelegramSink with mocked outbound + http_client."""
    import httpx

    outbound_mock = outbound or MagicMock()
    outbound_mock.send_to_thread = AsyncMock()

    resp_data = registry_response or {"chat_id": -1001, "reply_to_message_id": 42}

    async def _registry_get(url: str, **kwargs: object) -> httpx.Response:
        req = httpx.Request("GET", url)
        return httpx.Response(
            status_code=registry_status,
            json=resp_data,
            request=req,
        )

    http_client = MagicMock(spec=httpx.AsyncClient)
    http_client.get = AsyncMock(side_effect=_registry_get)

    return TelegramSink(
        base_dir=base_dir or Path("/nonexistent"),
        registry_api_url="http://registry-api:8080",
        http_client=http_client,
        outbound=outbound_mock,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. Happy-path dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_dispatches_on_task_event() -> None:
    """AC-7: task.completed event with binding → send_to_thread called with correct args."""
    rng = Random(1)
    clk = FrozenClock(mono_ns=1, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": -1001, "reply_to_message_id": 42},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_called_once()
    call_kwargs = outbound_mock.send_to_thread.call_args[1]
    assert call_kwargs["chat_id"] == -1001
    assert call_kwargs["reply_to_message_id"] == 42
    assert task_id in call_kwargs["text"]
    assert "task.completed" in call_kwargs["text"]


# ---------------------------------------------------------------------------
# 2. Skip on missing chat_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_when_chat_id_is_none() -> None:
    """AC-7: registry returns chat_id=null → send_to_thread NOT called (no binding)."""
    rng = Random(2)
    clk = FrozenClock(mono_ns=2, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": None, "reply_to_message_id": 42},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Skip on missing reply_to_message_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_when_reply_to_is_none() -> None:
    """AC-7: registry returns reply_to_message_id=null → send_to_thread NOT called."""
    rng = Random(3)
    clk = FrozenClock(mono_ns=3, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": -1001, "reply_to_message_id": None},
    )

    env = _task_completed_envelope(task_id)
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Skip non-task event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sink_skips_non_task_event() -> None:
    """AC-7: service.crashed event does not start with 'task.' → no dispatch."""
    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(outbound=outbound_mock)

    env = _service_crashed_envelope()
    await sink._handle(env)

    outbound_mock.send_to_thread.assert_not_called()


# ---------------------------------------------------------------------------
# Story 3.10 — _render_approval_request renderer + dispatcher tests (14)
# ---------------------------------------------------------------------------


def _approval_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000001",
    action: str = "merge PR #42",
    justification: str = "tests pass; reviewer approved",
    risk_class: str | None = None,
    pre_check_results: object = None,
    diff_summary: object = None,
    accepted_commands: list[str] | None = None,
    mono_ns: int = 4_000_000,
) -> EventEnvelope:
    """Build a task.approval_requested envelope (schema 1.1.0)."""
    _ensure_task_created_registered()
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        TaskApprovalRequestedPayload,
    )

    _reg("task.approval_requested", "1.1.0", TaskApprovalRequestedPayload)

    rng = Random(99)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskApprovalRequestedPayload(
        task_id=task_id,
        action=action,
        justification=justification,
        risk_class=risk_class,  # type: ignore[arg-type]
        pre_check_results=pre_check_results,  # type: ignore[arg-type]
        diff_summary=diff_summary,  # type: ignore[arg-type]
        accepted_commands=accepted_commands,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.approval_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_approval_request_minimal() -> None:
    """AC-5: only required fields → header + Action + Reason; no optional sections."""
    env = _approval_envelope()
    result = _render(env)
    assert "🔒 Approval required — task t-00000000-0000-7000-8000-000000000001" in result
    assert "Action: merge PR #42" in result
    assert "Reason: tests pass; reviewer approved" in result
    assert "Risk:" not in result
    assert "Pre-checks:" not in result
    assert "Diff:" not in result
    assert "Accepted commands:" not in result


def test_render_approval_request_with_risk_class_low() -> None:
    """AC-5: risk_class='low' → 'Risk: low' line present."""
    env = _approval_envelope(risk_class="low")
    result = _render(env)
    assert "Risk: low" in result


def test_render_approval_request_with_risk_class_medium() -> None:
    """AC-5: risk_class='medium' → 'Risk: medium' line present."""
    env = _approval_envelope(risk_class="medium")
    result = _render(env)
    assert "Risk: medium" in result


def test_render_approval_request_with_risk_class_high() -> None:
    """AC-5: risk_class='high' → 'Risk: high' line present."""
    env = _approval_envelope(risk_class="high")
    result = _render(env)
    assert "Risk: high" in result


def test_render_approval_request_with_full_pre_checks_all_pass() -> None:
    """AC-5: 4 pre-checks all-pass → 4 ✅ lines in spec order (Lint, Types, Unit, Integration)."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=142, total=142, status="pass"),
        types=PreCheckOutcome(passed=88, total=88, status="pass"),
        unit=PreCheckOutcome(passed=315, total=315, status="pass"),
        integration=PreCheckOutcome(passed=27, total=27, status="pass"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    assert "Pre-checks:" in result
    assert "✅ Lint: 142/142" in result
    assert "✅ Types: 88/88" in result
    assert "✅ Unit: 315/315" in result
    assert "✅ Integration: 27/27" in result
    # Spec order: Lint before Types before Unit before Integration.
    assert (
        result.index("Lint")
        < result.index("Types")
        < result.index("Unit")
        < result.index("Integration")
    )


def test_render_approval_request_with_pre_check_one_fail() -> None:
    """AC-5: one ❌ check carries ' (failed)' suffix."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=142, total=142, status="pass"),
        unit=PreCheckOutcome(passed=312, total=315, status="fail"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    assert "✅ Lint: 142/142" in result
    assert "❌ Unit: 312/315 (failed)" in result


def test_render_approval_request_with_partial_pre_checks() -> None:
    """AC-5: only 2 of 4 pre-check fields populated → exactly 2 lines rendered."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=10, total=10, status="pass"),
        types=PreCheckOutcome(passed=5, total=5, status="pass"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    # Pre-check block exists.
    assert "Pre-checks:" in result
    # Exactly 2 outcome lines (✅ or ❌) in the rendered string.
    outcome_line_count = sum(1 for line in result.splitlines() if line.startswith(("✅", "❌")))
    assert outcome_line_count == 2
    assert "Lint" in result
    assert "Types" in result
    assert "Unit" not in result
    assert "Integration" not in result


def test_render_approval_request_with_diff_summary() -> None:
    """AC-5: DiffSummary renders as 'Diff: N files, +I, -D'."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        DiffSummary,
    )

    env = _approval_envelope(diff_summary=DiffSummary(files=5, insertions=234, deletions=89))
    result = _render(env)
    assert "Diff: 5 files, +234, -89" in result


def test_render_approval_request_with_accepted_commands_capped_at_10() -> None:
    """AC-6: 12 commands → 10 visible bullets + '… and 2 more' overflow line."""
    cmds = [f"/cmd-{i}" for i in range(12)]
    env = _approval_envelope(accepted_commands=cmds)
    result = _render(env)
    assert "Accepted commands:" in result
    # All first 10 commands present.
    for i in range(10):
        assert f"  • /cmd-{i}" in result
    # 11th and 12th not directly listed.
    assert "  • /cmd-10" not in result
    assert "  • /cmd-11" not in result
    # Overflow indicator.
    assert "  • … and 2 more" in result


def test_render_approval_request_html_escapes_task_id_action_justification_commands() -> None:
    """AC-7: HTML-escape every operator-supplied string (Story 3.5 H5 carry-forward)."""
    env = _approval_envelope(
        task_id="t-<x>",
        action="rm -rf <foo>",
        justification="<b>bold</b>",
        accepted_commands=["/cmd <x>"],
    )
    result = _render(env)
    # Raw < / > / & gone from operator-supplied substrings.
    assert "<x>" not in result.replace("&lt;x&gt;", "")
    assert "<foo>" not in result.replace("&lt;foo&gt;", "")
    assert "<b>bold</b>" not in result
    # Escaped variants present.
    assert "&lt;x&gt;" in result
    assert "&lt;foo&gt;" in result
    assert "&lt;b&gt;bold&lt;/b&gt;" in result


def test_render_approval_request_total_cap_drops_diff_then_commands() -> None:
    """AC-6: >3500-char message → drop ' (failed)' → drop diff → drop commands."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        DiffSummary,
        PreCheckOutcome,
        PreCheckResults,
    )

    # Justification just under 3500 so Header+Action+Reason alone don't trip
    # the emergency one-liner; section-drop ladder must do the work.
    big_just = "x" * 3000
    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=1, total=2, status="fail"),
    )
    env = _approval_envelope(
        justification=big_just,
        pre_check_results=pre,
        diff_summary=DiffSummary(files=5, insertions=100, deletions=50),
        accepted_commands=[f"/cmd-{i}" * 10 for i in range(10)],
    )
    result = _render(env)
    # Length cap honored.
    assert len(result) <= 3500
    # Mandatory sections preserved.
    assert "🔒 Approval required" in result
    assert "Action:" in result
    assert "Reason:" in result
    # AC-6 ladder step 2 — diff section dropped (sits ABOVE commands in drop
    # priority).
    assert "Diff:" not in result
    # AC-6 ladder step 1 — ' (failed)' suffix dropped: pre-check line shows
    # status emoji + counts but no '(failed)' tail.
    assert " (failed)" not in result
    # AC-6 ladder step 3 — commands trimmed from the bottom (full list had 10
    # entries; result keeps strictly fewer + an overflow indicator).
    assert "… and " in result
    visible_bullets = sum(1 for line in result.splitlines() if line.startswith("  • /cmd-"))
    assert visible_bullets < 10


def test_render_approval_request_emergency_fallback_when_justification_too_long() -> None:
    """AC-6: justification = 'X' * 5000 → emergency one-liner pointing at /logs."""
    env = _approval_envelope(
        task_id="t-00000000-0000-7000-8000-0000000000aa",
        justification="X" * 5000,
    )
    result = _render(env)
    assert result == (
        "🔒 Approval required — task t-00000000-0000-7000-8000-0000000000aa"
        "\n\n(message body too large; see /logs t-00000000-0000-7000-8000-0000000000aa)"
    )


def test_render_dispatcher_routes_approval_to_renderer() -> None:
    """AC-4: _render(envelope) for task.approval_requested invokes _render_approval_request."""
    env = _approval_envelope(action="apply migration")
    result = _render(env)
    # The approval renderer's distinguishing header is '🔒 Approval required' —
    # the placeholder fallback does not include this.
    assert result.startswith("🔒 Approval required —")
    assert "Action: apply migration" in result


def test_render_dispatcher_falls_back_to_placeholder_for_unknown_type() -> None:
    """AC-4: unknown event type → 'Task <id>: <type>' placeholder (Story 3.9 shape)."""
    rng = Random(123)
    clk = FrozenClock(mono_ns=5_000_000, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)
    _ensure_task_created_registered()
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        TaskExecutionStartedPayload,
    )

    _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskExecutionStartedPayload(
            task_id=task_id,
            session_id="s-00000000-0000-7000-8000-000000000001",
        ),
        request_id=rid,
    )
    result = _render(env)
    assert result == f"Task {task_id}: task.execution.started"
