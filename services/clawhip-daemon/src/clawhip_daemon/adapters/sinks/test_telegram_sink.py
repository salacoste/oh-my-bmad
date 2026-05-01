"""Unit tests for TelegramSink — Story 3.9 AC-7 / AC-9 + Story 3.10 review pass.

5 baseline tests:
1. Happy-path dispatch — task.* event with binding → send_to_thread called.
2. Skip on missing chat_id — binding has reply_to but chat_id is None.
3. Skip on missing reply_to_message_id — binding has chat_id but reply_to is None.
4. Skip non-task event — event type does not start with "task." → no dispatch.
5. Placeholder renderer output shape — text is "Task <id>: <type>" HTML-escaped.

Story 3.10 AC-10 (14 renderer tests) + review-pass additions.
"""

from __future__ import annotations

from pathlib import Path
from random import Random
from typing import Literal
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

from clawhip_daemon.adapters.sinks.telegram_sink import (
    _APPROVAL_MESSAGE_MAX_CHARS,
    _DELIVERABLE_EVENT_TYPES,
    _RENDERERS,
    TelegramSink,
    _render,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test-sink")


# Story 3.10 review M8: idempotent guard so repeat invocations of the test
# helper don't re-run the schema_registry.register side-effect for every
# helper call (11+ tests previously hit it).
_REGISTERED: bool = False


def _ensure_task_created_registered() -> None:
    """Register task.created 1.1.0 so EventEnvelope.create succeeds in tests.

    Story 3.10 review M8: module-level idempotent guard — once-per-module
    instead of once-per-helper-invocation.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        ServiceCrashedPayload,
        TaskCompletedPayload,
        TaskCreatedPayload,
    )

    _reg("task.created", "1.0.0", TaskCreatedPayload)
    _reg("task.created", "1.1.0", TaskCreatedPayload)
    _reg("task.completed", "1.0.0", TaskCompletedPayload)
    _reg("service.crashed", "1.0.0", ServiceCrashedPayload)
    _REGISTERED = True


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
    risk_class: Literal["low", "medium", "high"] | None = None,
    pre_check_results: "PreCheckResults | None" = None,  # noqa: UP037 — forward ref
    diff_summary: "DiffSummary | None" = None,  # noqa: UP037 — forward ref
    accepted_commands: list[str] | None = None,
    mono_ns: int = 4_000_000,
) -> EventEnvelope:
    """Build a task.approval_requested envelope (schema 1.1.0).

    Story 3.10 review M7: ``risk_class`` is typed ``Literal[...]`` and the
    nested optional models use direct types — drops three legacy
    ``# type: ignore[arg-type]`` markers.
    """
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
        risk_class=risk_class,
        pre_check_results=pre_check_results,
        diff_summary=diff_summary,
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


# Re-imported here so the ``_approval_envelope`` forward references resolve.
from registry_state.domain.event_types import (  # noqa: E402, IMP001 — Story 2.9 AC-16
    DiffSummary,
    PreCheckResults,
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
    # Story 3.10 review L8: line-number-based ordering rather than
    # ``result.index("Lint")`` substring search (would skew if "Lint" /
    # "Types" appears elsewhere in the message).
    lines = result.split("\n")
    lint_line = next(i for i, ln in enumerate(lines) if ln.startswith("✅ Lint:"))
    types_line = next(i for i, ln in enumerate(lines) if ln.startswith("✅ Types:"))
    unit_line = next(i for i, ln in enumerate(lines) if ln.startswith("✅ Unit:"))
    integration_line = next(i for i, ln in enumerate(lines) if ln.startswith("✅ Integration:"))
    assert lint_line < types_line < unit_line < integration_line


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
    """AC-7: HTML-escape every operator-supplied string (Story 3.5 H5 carry-forward).

    Story 3.10 review M5: separate ``<`` and ``>`` checks so a partial-
    escape regression (e.g. ``&lt;x>`` where only ``<`` is escaped) is
    caught — the prior ``"<x>" not in ...`` substring test would not.
    """
    env = _approval_envelope(
        task_id="t-<x>",
        action="rm -rf <foo>",
        justification="<b>bold</b>",
        accepted_commands=["/cmd <x>"],
    )
    result = _render(env)
    # M5: separate-character checks. Once all ``&lt;`` are stripped, no
    # bare ``<`` may remain anywhere in the result; symmetric for ``>``.
    assert "<" not in result.replace("&lt;", "")
    assert ">" not in result.replace("&gt;", "")
    # Escaped variants explicitly present.
    assert "&lt;x&gt;" in result
    assert "&lt;foo&gt;" in result
    assert "&lt;b&gt;bold&lt;/b&gt;" in result
    # Sanity: the raw payload string never appears verbatim.
    assert "<b>bold</b>" not in result


def test_render_approval_request_total_cap_drops_diff_only() -> None:
    """AC-6 + review M6/M10: sized-just-right scenario where ONLY the diff drop fires.

    Justification + commands tuned so the fully-populated message overflows
    by a small margin recoverable by dropping the diff section alone. All
    10 commands must remain; only ``Diff:`` is gone.

    Sizing rationale: cap is 2000 chars. The diff section
    ``"Diff: 5 files, +100, -50"`` plus ``\\n\\n`` separator ≈ 28 chars.
    Justification padding chosen so the FULL message lands just above 2000
    (overflow ≤ 28) — diff drop alone brings it back under.
    """
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        DiffSummary,
    )

    cmds = [f"/cmd-{i:02d}" for i in range(10)]
    diff = DiffSummary(files=5, insertions=100, deletions=50)
    # Need: full assembly > 2000 by at most ~26 chars (the diff section size
    # plus separator). Empirically (cap=2000): pad=1740 → full=2003,
    # no_diff=1977 — diff drop alone is the sufficient ladder step.
    pad = "x" * 1740
    env = _approval_envelope(
        justification=pad,
        diff_summary=diff,
        accepted_commands=cmds,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _APPROVAL_MESSAGE_MAX_CHARS
    # Mandatory sections preserved.
    assert "🔒 Approval required" in result
    assert "Action:" in result
    assert "Reason:" in result
    # Diff dropped.
    assert "Diff:" not in result
    # All 10 commands retained, no overflow line.
    for i in range(10):
        assert f"  • /cmd-{i:02d}" in result
    assert "… and " not in result


def test_render_approval_request_total_cap_drops_diff_and_commands() -> None:
    """AC-6 + review M6: scenario where diff drop is insufficient — commands trim too.

    Sizing rationale: 60-char commands × 10 = 600 chars of bullets. With
    pad=1300 + pre-check suffix + diff, full assembly is well over cap.
    Drop diff (Step 2) → still over; binary search (Step 3) picks the
    largest visible_count that fits (empirically vc=8 with 60-char cmds).
    Pre-check block remains because Step 3.5 only fires when commands
    cannot recover the overflow alone.
    """
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        DiffSummary,
        PreCheckOutcome,
        PreCheckResults,
    )

    big_just = "x" * 1300
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
    # Cap honored (M1 — 2000 chars).
    assert len(result) <= _APPROVAL_MESSAGE_MAX_CHARS
    # Mandatory sections preserved.
    assert "🔒 Approval required" in result
    assert "Action:" in result
    assert "Reason:" in result
    # Step 2 — diff dropped.
    assert "Diff:" not in result
    # Step 1 — ' (failed)' suffix dropped before the diff drop.
    assert " (failed)" not in result
    # Step 3 — commands trimmed from the bottom (binary search picks largest
    # visible_count that fits — strictly fewer than 10).
    visible_bullets = sum(1 for line in result.splitlines() if line.startswith("  • /cmd-"))
    assert 0 < visible_bullets < 10
    assert "… and " in result


def test_render_approval_request_total_cap_drops_pre_checks_before_emergency() -> None:
    """AC-6 + review H1: pre-check block dropped (Step 3.5) before emergency fallback.

    Construct an envelope where Header+Action+Reason+Pre-checks overflows
    but Header+Action+Reason alone fits — the only sufficient drop is the
    pre-check block. Verifies Step 3.5 fires in the ladder.
    """
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    # Justification just under cap so Header+Action+Reason fits but adding
    # pre-checks pushes us over.
    big_just = "x" * 1900
    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=10, total=10, status="pass"),
        types=PreCheckOutcome(passed=20, total=20, status="pass"),
        unit=PreCheckOutcome(passed=30, total=30, status="pass"),
        integration=PreCheckOutcome(passed=40, total=40, status="pass"),
    )
    env = _approval_envelope(
        justification=big_just,
        pre_check_results=pre,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _APPROVAL_MESSAGE_MAX_CHARS
    # Mandatory sections preserved.
    assert "🔒 Approval required" in result
    assert "Action:" in result
    assert "Reason:" in result
    # Pre-check block dropped.
    assert "Pre-checks:" not in result
    # Did NOT fall through to emergency one-liner.
    assert "(message body too large; see /logs" not in result


def test_render_approval_request_emergency_fallback_when_justification_too_long() -> None:
    """AC-6: justification beyond the renderer cap → emergency one-liner pointing at /logs."""
    # H3 caps justification at 10_000 chars; pick the maximum allowed value
    # which still trivially exceeds the 2000-char renderer cap (M1).
    env = _approval_envelope(
        task_id="t-00000000-0000-7000-8000-0000000000aa",
        justification="X" * 10_000,
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
    """AC-4: unknown event type → 'Task <id>: <type>' placeholder (Story 3.9 shape).

    Story 3.10 review M9: spec AC-10 names ``task.execution_started`` with
    the underscore form. The renderer dispatcher accepts any unknown type
    and falls back to the placeholder; we keep the dot-form
    ``task.execution.started`` (which IS in ``_DELIVERABLE_EVENT_TYPES`` but
    NOT in ``_RENDERERS``) as the realistic "registered upstream, no
    renderer yet" path. The placeholder string echoes whatever
    ``envelope.type`` is supplied.
    """
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


# ---------------------------------------------------------------------------
# Story 3.10 review-pass additions
# ---------------------------------------------------------------------------


def test_render_approval_request_multiline_justification_collapsed_to_single_line() -> None:
    """H11: multi-line ``justification`` is collapsed to a single line.

    Telegram messages aren't a multi-line free-form surface; mid-section
    newlines would visually conflict with the ``\\n\\n`` separator the
    renderer uses between sections.
    """
    env = _approval_envelope(justification="Line 1\nLine 2\nLine 3")
    result = _render(env)
    # The Reason: line must hold all three sub-lines as a single line —
    # verify by finding the ``Reason:`` line and asserting the join.
    reason_line = next(line for line in result.split("\n") if line.startswith("Reason: "))
    assert reason_line == "Reason: Line 1 Line 2 Line 3"


def test_render_dispatcher_warns_and_falls_back_on_payload_type_mismatch() -> None:
    """H9 + H10: when the dispatcher routes here but payload isn't typed, WARN + placeholder.

    Constructs the envelope via ``model_construct`` to bypass Pydantic
    validation and forcibly assigns a raw-dict payload (the registration-
    race scenario). Uses :func:`structlog.testing.capture_logs` because the
    clawhip-daemon test environment does not configure stdlib logging.
    """
    import structlog.testing

    rng = Random(456)
    clk = FrozenClock(mono_ns=6_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.1.0",
        type="task.approval_requested",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": "t-raw-dict-payload"},  # type: ignore[arg-type]
        request_id=rid,
    )
    with structlog.testing.capture_logs() as captured:
        result = _render(env)

    # Placeholder shape returned (Story 3.9 carry-forward).
    assert result == "Task t-raw-dict-payload: task.approval_requested"
    # H9: structured warning emitted with expected/actual fields.
    assert any(
        rec.get("event") == "renderer.payload_type_mismatch"
        and rec.get("log_level") == "warning"
        and rec.get("expected") == "TaskApprovalRequestedPayload"
        and rec.get("actual") == "dict"
        for rec in captured
    )


@pytest.mark.asyncio
async def test_handle_logs_error_and_falls_back_when_renderer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M11: an exception inside ``_render`` does not crash the sink loop.

    Patches ``_render`` to raise; asserts the handler logs an error AND
    still calls ``send_to_thread`` with the placeholder text. Uses
    :func:`structlog.testing.capture_logs` (clawhip-daemon test env does
    not configure stdlib logging).
    """
    import structlog.testing

    from clawhip_daemon.adapters.sinks import telegram_sink as ts

    rng = Random(789)
    clk = FrozenClock(mono_ns=7_000_000, now=FROZEN_EPOCH)
    task_id = new_task_id(clock=clk, rng=rng)

    outbound_mock = MagicMock()
    outbound_mock.send_to_thread = AsyncMock()
    sink = _make_sink(
        outbound=outbound_mock,
        registry_response={"chat_id": -1001, "reply_to_message_id": 42},
    )

    def _boom(_envelope: EventEnvelope) -> str:
        raise RuntimeError("synthetic renderer crash")

    monkeypatch.setattr(ts, "_render", _boom)

    env = _task_completed_envelope(task_id)

    with structlog.testing.capture_logs() as captured:
        await sink._handle(env)

    # Outbound called with placeholder text.
    outbound_mock.send_to_thread.assert_called_once()
    text = outbound_mock.send_to_thread.call_args[1]["text"]
    assert text == f"Task {task_id}: task.completed"
    # Error logged with renderer-failure event.
    assert any(
        "renderer raised" in str(rec.get("event", "")) and rec.get("log_level") == "error"
        for rec in captured
    )


def test_renderers_subset_of_deliverable_event_types() -> None:
    """M12: every entry in ``_RENDERERS`` must be present in ``_DELIVERABLE_EVENT_TYPES``.

    Stories 3.11/3.12/3.13 will add task.blocker_raised, task.completed,
    task.self_recovered renderers; this invariant prevents drift between
    the dispatcher table and the allowlist.
    """
    assert set(_RENDERERS.keys()).issubset(_DELIVERABLE_EVENT_TYPES)


def test_render_approval_request_with_pre_check_results_all_none() -> None:
    """L11: ``PreCheckResults()`` (object exists, all fields None) → section omitted."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckResults,
    )

    env = _approval_envelope(pre_check_results=PreCheckResults())
    result = _render(env)
    # Section header is absent because no individual outcomes populated.
    assert "Pre-checks:" not in result


def test_render_dispatcher_fallback_html_escapes_task_id() -> None:
    """L12: placeholder fallback HTML-escapes ``task_id`` containing < / >.

    Builds an envelope of an UNKNOWN type (not in ``_RENDERERS``) so the
    fallback path runs; payload's ``task_id`` carries angle brackets that
    must escape to ``&lt;`` / ``&gt;``.
    """
    rng = Random(202)
    clk = FrozenClock(mono_ns=8_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    _ensure_task_created_registered()
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        TaskExecutionStartedPayload,
    )

    _reg("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    # task_id with angle brackets — bypass model validation via
    # model_construct so the H3 task_id pattern doesn't fight us.
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskExecutionStartedPayload.model_construct(
            task_id="t-<x>",
            session_id="s-00000000-0000-7000-8000-000000000001",
        ),
        request_id=rid,
    )
    result = _render(env)
    assert result == "Task t-&lt;x&gt;: task.execution.started"


def test_render_dispatcher_fallback_when_payload_is_none() -> None:
    """L19: dispatcher fallback handles ``envelope.payload = None`` cleanly.

    Constructs the envelope via ``model_construct`` and assigns a None
    payload; ``_extract_task_id`` returns None which falls through to the
    ``<unknown>`` sentinel (also HTML-escaped to ``&lt;unknown&gt;``).
    """
    rng = Random(303)
    clk = FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.0.0",
        type="task.execution.started",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=None,  # type: ignore[arg-type]
        request_id=rid,
    )
    result = _render(env)
    # ``<unknown>`` sentinel HTML-escaped (L18 — magic-sentinel collision is
    # a non-issue because both real and sentinel paths escape identically).
    assert result == "Task &lt;unknown&gt;: task.execution.started"


def test_render_approval_request_with_pre_check_skipped_status() -> None:
    """M13: ``status='skipped'`` → ⏭️ emoji rendered."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        lint=PreCheckOutcome(passed=0, total=0, status="skipped"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    assert "⏭️ Lint: 0/0" in result


def test_render_approval_request_with_pre_check_error_status() -> None:
    """M13: ``status='error'`` → ⚠️ emoji rendered."""
    from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        unit=PreCheckOutcome(passed=0, total=0, status="error"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    assert "⚠️ Unit: 0/0" in result
