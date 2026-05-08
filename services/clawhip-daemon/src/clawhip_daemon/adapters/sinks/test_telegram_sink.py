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

from datetime import UTC, datetime
from pathlib import Path
from random import Random
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

# Story 3.11 review M1 / M2: payload imports promoted from inline imports
# inside ``_approval_envelope`` / ``_blocker_envelope``. Originally from
# ``registry_state.domain.event_types`` with ``# noqa: IMP001``; relocated to
# ``events`` by Story 3.5.2. ``DiffSummary`` / ``PreCheckResults`` are
# forward-ref-resolved here so ``_approval_envelope`` can use them in its
# signature without ``# noqa: UP037`` markers.
# (Other helpers — ``_task_created_envelope`` etc — keep their inline
# imports per Story 2.9's pattern; promotion scope is limited to what M1 /
# M2 explicitly call out.)
from events import (  # Story 2.9 AC-16
    FROZEN_EPOCH,
    Actor,
    DiffSummary,
    EventEnvelope,
    FrozenClock,
    PlanStep,
    PreCheckResults,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskPlanReadyPayload,
    TaskSelfRecoveredPayload,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.schema_registry import register as _reg

from clawhip_daemon.adapters.sinks.telegram_sink import (
    _APPROVAL_MESSAGE_MAX_CHARS,
    _BLOCKER_AVAILABLE_COMMANDS,
    _BLOCKER_MESSAGE_MAX_CHARS,
    _COMPLETED_MESSAGE_MAX_CHARS,
    _DELIVERABLE_EVENT_TYPES,
    _EMERGENCY_TASK_ID_MAX_CHARS,
    _RENDERERS,
    _SELF_RECOVERED_MESSAGE_MAX_CHARS,
    TelegramSink,
    _build_diff_stats_line,
    _build_pr_line,
    _render,
    _render_blocker_raised,
    _render_completed,
    _render_plan_ready,
    _render_self_recovered,
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
    from events import (  # Story 2.9 AC-16
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
    from events import TaskCreatedPayload  # noqa: I001 — Story 2.9 AC-16, inline import

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
    from events import TaskCompletedPayload  # noqa: I001 — Story 2.9 AC-16, inline import

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
    from events import ServiceCrashedPayload  # noqa: I001 — Story 2.9 AC-16, inline import

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
    # Story 3.12 — task.completed now routes through _render_completed
    # (FR9 typed renderer), replacing the Story 3.9 placeholder shape
    # ``Task <id>: task.completed``. The new shape is
    # ``✅ Task <id> complete.\n\n<summary>``.
    assert "✅ Task " in call_kwargs["text"]
    assert "complete." in call_kwargs["text"]


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
    pre_check_results: PreCheckResults | None = None,
    diff_summary: DiffSummary | None = None,
    accepted_commands: list[str] | None = None,
    mono_ns: int = 4_000_000,
) -> EventEnvelope:
    """Build a task.approval_requested envelope (schema 1.1.0).

    Story 3.10 review M7: ``risk_class`` is typed ``Literal[...]`` and the
    nested optional models use direct types — drops three legacy
    ``# type: ignore[arg-type]`` markers.

    Story 3.11 review M1 / M2: dropped the forward-ref noqas (``# noqa:
    UP037``) and the inline ``TaskApprovalRequestedPayload`` import — both
    types are now imported at top of file.
    """
    _ensure_task_created_registered()
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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

    Sizing rationale (Story 3.11 review H12 — parametric on the cap):
    The diff section ``"Diff: 5 files, +100, -50"`` plus ``\\n\\n``
    separator ≈ 28 chars. Justification padding is sized so the FULL
    message lands just above the cap (overflow ≤ 28) — diff drop alone
    brings it back under. Pad scales with the cap so future cap moves
    don't silently re-route through a different ladder step.
    """
    from events import (  # Story 2.9 AC-16
        DiffSummary,
    )

    cmds = [f"/cmd-{i:02d}" for i in range(10)]
    diff = DiffSummary(files=5, insertions=100, deletions=50)
    # Story 3.11 review H12: parametric sizing — pad scales with cap so
    # the test exercises the same ladder step regardless of cap value.
    # Empirically (cap=1900): pad=1660 → full=1923, no_diff=1897 — diff
    # drop alone is sufficient to bring the message under cap. Pad is
    # expressed as ``cap - 240`` so future cap moves keep the same
    # ladder-step coverage.
    pad = "x" * (_APPROVAL_MESSAGE_MAX_CHARS - 240)
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
    from events import (  # Story 2.9 AC-16
        DiffSummary,
        PreCheckOutcome,
        PreCheckResults,
    )

    # Story 3.11 review H12: parametric on cap so the test exercises the
    # same ladder step (diff drop + commands trim, NOT pre-check drop)
    # regardless of cap value.
    big_just = "x" * (_APPROVAL_MESSAGE_MAX_CHARS - 700)
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
    from events import (  # Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    # Justification just under cap so Header+Action+Reason fits but adding
    # pre-checks pushes us over.
    # Story 3.11 review H12: parametric on cap so the test exercises Step 3.5
    # (pre-check drop) regardless of cap value. Empirically (cap=1900):
    # pad=1800 → full=1974, no_pre_checks=1897 — Step 3.5 (pre-check drop)
    # is the sufficient ladder step.
    big_just = "x" * (_APPROVAL_MESSAGE_MAX_CHARS - 100)
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
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
    from events import (  # Story 2.9 AC-16
        PreCheckOutcome,
        PreCheckResults,
    )

    pre = PreCheckResults(
        unit=PreCheckOutcome(passed=0, total=0, status="error"),
    )
    env = _approval_envelope(pre_check_results=pre)
    result = _render(env)
    assert "⚠️ Unit: 0/0" in result


# ---------------------------------------------------------------------------
# Story 3.11 — _render_blocker_raised renderer + dispatcher tests
# (Story 3.11 review L3: count dropped from heading — drifts when tests
# are added/removed.)
# ---------------------------------------------------------------------------


# Story 3.11 review H11: idempotent guard mirroring _REGISTERED — once-per-
# module instead of once-per-helper-invocation. The 11 blocker tests all
# go through ``_blocker_envelope`` which used to re-register the schema on
# every call (matches the M8 pattern Story 3.10 fixed for approval).
_BLOCKER_REGISTERED: bool = False


def _ensure_blocker_raised_registered() -> None:
    """Register task.blocker_raised 1.1.0 — idempotent (Story 3.11 review H11)."""
    global _BLOCKER_REGISTERED
    if _BLOCKER_REGISTERED:
        return
    _reg("task.blocker_raised", "1.1.0", TaskBlockerRaisedPayload)
    _BLOCKER_REGISTERED = True


def _blocker_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000002",
    reason: str = "worker crashed mid-execution",
    blocked_since: datetime | None = None,
    last_event: str | None = None,
    last_action: str | None = None,
    mono_ns: int = 7_000_000,
) -> EventEnvelope:
    """Build a task.blocker_raised envelope (schema 1.1.0).

    Story 3.11 review M1 / M2: drops the forward-ref noqa (``# noqa:
    UP037``) and the inline ``TaskBlockerRaisedPayload`` import — both
    are now imported at top of file.

    Story 3.11 review H11: schema registration is idempotent via
    ``_ensure_blocker_raised_registered`` (matches the Story 3.10 M8
    pattern for ``_REGISTERED``).
    """
    _ensure_task_created_registered()
    _ensure_blocker_raised_registered()

    rng = Random(311)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskBlockerRaisedPayload(
        task_id=task_id,
        reason=reason,
        blocked_since=blocked_since,
        last_event=last_event,
        last_action=last_action,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_blocker_raised_minimal() -> None:
    """AC-2: only required fields → header + commands footer; optional sections absent.

    Story 3.11 review L5: footer-order assertion — the four bullets must
    appear in spec order ``/logs`` → ``/retry`` → ``/stop`` → ``/handoff``.
    """
    env = _blocker_envelope(
        task_id="t-00000000-0000-7000-8000-000000000002",
        reason="worker crashed",
    )
    result = _render(env)
    # Header line present.
    assert (
        "⛔ Task t-00000000-0000-7000-8000-000000000002 blocked. worker crashed. "
        "See /logs t-00000000-0000-7000-8000-000000000002 for detail." in result
    )
    # Available commands footer present with all 4 bullets.
    assert "Available commands:" in result
    assert "  • /logs" in result
    assert "  • /retry" in result
    assert "  • /stop" in result
    assert "  • /handoff" in result
    # Story 3.11 review L5: assert footer bullet ORDER.
    assert (
        result.index("/logs")
        < result.index("/retry")
        < result.index("/stop")
        < result.index("/handoff")
    )
    # Optional sections absent.
    assert "Blocked since:" not in result
    assert "Last event:" not in result
    assert "Last action:" not in result


def test_render_blocker_raised_with_blocked_since() -> None:
    """AC-2: blocked_since populated → ``Blocked since: <iso>`` line present."""
    env = _blocker_envelope(
        blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
    )
    result = _render(env)
    assert "Blocked since: 2026-05-01T12:00:00+00:00" in result


def test_render_blocker_raised_with_last_event() -> None:
    """AC-2: last_event populated → ``Last event: <name>`` line present."""
    env = _blocker_envelope(last_event="task.execution.started")
    result = _render(env)
    assert "Last event: task.execution.started" in result


def test_render_blocker_raised_with_last_action() -> None:
    """AC-2: last_action populated → ``Last action: <text>`` line present."""
    env = _blocker_envelope(last_action="ran pytest tests/")
    result = _render(env)
    assert "Last action: ran pytest tests/" in result


def test_render_blocker_raised_html_escapes_task_id_reason_last_event_last_action() -> None:
    """AC-4 / Story 3.5 H5 carry-forward: HTML-escape all operator-supplied strings.

    No raw ``<`` / ``>`` (only ``&lt;`` / ``&gt;``) appear anywhere in the output.

    Story 3.11 review M4 / Story 3.10 review M5 carry-forward: substring
    assertions like ``"<b>boom</b>" not in result`` fail to detect partial
    escapes (e.g. ``&lt;b>boom``). Add per-character invariants on the
    escaped output so a future renderer drift can't slip past.
    """
    env = _blocker_envelope(
        task_id="t-<x>",
        reason="<b>boom</b>",
        last_event="evt<>",
        last_action="rm -rf <foo>",
    )
    result = _render(env)
    # Sanity: no raw payload strings appear verbatim.
    assert "<b>boom</b>" not in result
    assert "<foo>" not in result
    assert "evt<>" not in result
    # Escaped forms appear.
    assert "t-&lt;x&gt;" in result
    assert "&lt;b&gt;boom&lt;/b&gt;" in result
    assert "evt&lt;&gt;" in result
    assert "rm -rf &lt;foo&gt;" in result
    # Story 3.11 review M4: per-character invariants — once ``&lt;`` /
    # ``&gt;`` are removed, no raw ``<`` / ``>`` characters remain.
    assert "<" not in result.replace("&lt;", "")
    assert ">" not in result.replace("&gt;", "")


def test_render_blocker_raised_collapses_multiline_reason_and_last_action() -> None:
    """AC-2 / Story 3.10 H11 carry-forward: ``\\n`` in reason / last_action collapsed to space.

    The header / available-commands separator (``\\n\\n``) remains intact.

    Story 3.11 review L4: ``next()`` over the split lines uses an explicit
    default + ``assert`` so a future header-prefix drift produces a clear
    failure message instead of a bare ``StopIteration``.
    """
    env = _blocker_envelope(
        reason="line1\nline2",
        last_action="step1\nstep2",
    )
    result = _render(env)
    # Reason / last_action sections are single-line.
    # Story 3.11 review L4: explicit default + assertion.
    header_line = next(
        (line for line in result.split("\n") if line.startswith("⛔ ")),
        "",
    )
    assert header_line, f"header line not found in result: {result!r}"
    assert "line1 line2" in header_line
    last_action_line = next(
        (line for line in result.split("\n") if line.startswith("Last action: ")),
        "",
    )
    assert last_action_line, f"Last action line not found in result: {result!r}"
    assert last_action_line == "Last action: step1 step2"


def test_render_blocker_raised_total_cap_drops_last_action_first() -> None:
    """AC-5: section-drop ladder Step 2 — last_action dropped first when cap exceeded.

    Sized so that the full message overflows by an amount recoverable by
    dropping ONLY ``last_action``. ``last_event`` and ``blocked_since``
    stay present.

    Story 3.11 review H12: sizing is parametric on
    ``_BLOCKER_MESSAGE_MAX_CHARS`` so future cap moves don't silently
    re-route through a different ladder step. Empirically (cap=1900):
    last_action=1700 → full=2003, no_last_action=288.
    """
    # last_action is the dominant size driver — boost it just enough to
    # overflow the cap. Parametric: ``cap - 200`` overflows by ~+100, and
    # dropping last_action wholesale leaves a ~290-char message well
    # under cap.
    big_last_action = "a" * (_BLOCKER_MESSAGE_MAX_CHARS - 200)
    env = _blocker_envelope(
        blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        last_event="task.execution.started",
        last_action=big_last_action,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS
    # Mandatory header preserved.
    assert "⛔ Task " in result
    # Available commands footer preserved.
    assert "Available commands:" in result
    # last_action dropped (Step 2).
    assert "Last action:" not in result
    # last_event and blocked_since retained.
    assert "Last event: task.execution.started" in result
    # Story 3.11 review M14: blocked_since rendered with timespec="seconds".
    assert "Blocked since: 2026-05-01T12:00:00+00:00" in result


def test_render_blocker_raised_total_cap_drops_in_spec_order() -> None:
    """AC-5: section-drop ladder Steps 2 + 3 — last_action then last_event dropped.

    Sized so that even after dropping ``last_action``, the message still
    overflows; dropping ``last_event`` brings it back under cap. Footer
    and ``blocked_since`` remain.

    Story 3.11 review H12: sizing is parametric on
    ``_BLOCKER_MESSAGE_MAX_CHARS`` so future cap moves don't silently
    re-route through a different ladder step. Empirically (cap=1900):
    reason=1650 + last_event=128 + last_action=200 → full=2231,
    no_last_action=2016 (Step 2 still over), no_last_action_no_last_event=1874
    (Step 3 fits).
    """
    env = _blocker_envelope(
        reason="r" * (_BLOCKER_MESSAGE_MAX_CHARS - 250),
        blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        last_event="x" * 128,
        last_action="a" * 200,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS
    # Mandatory header preserved.
    assert "⛔ Task " in result
    # Available commands footer preserved.
    assert "Available commands:" in result
    # last_action and last_event dropped.
    assert "Last action:" not in result
    assert "Last event:" not in result
    # blocked_since retained.
    assert "Blocked since: 2026-05-01T12:00:00+00:00" in result


def test_render_blocker_raised_emergency_fallback_when_reason_too_long() -> None:
    """AC-5: section-drop ladder Step 5 — emergency one-liner; no commands footer.

    When ``reason`` alone is large enough that even header + commands
    footer can't fit together, the renderer falls back to the one-liner
    that still embeds ``/logs <id>`` for recovery.

    Story 3.11 review H12: ``reason`` size parametric on the cap so
    future cap changes still route through Step 5. ``cap + 90`` keeps
    the size under the model boundary (max_length=2000) when cap=1900,
    while guaranteeing Steps 1-4 all overflow.

    Story 3.11 review H5: assert the final length is under the cap
    (defensive self-clamp).
    """
    # Reason scales with cap. Header overhead ~80 chars; reason of size
    # ``cap + 90`` makes Steps 1-4 (which all keep the full reason) all
    # overflow, forcing Step 5.
    reason_size = _BLOCKER_MESSAGE_MAX_CHARS + 90
    env = _blocker_envelope(
        task_id="t-00000000-0000-7000-8000-0000000000bb",
        reason="X" * reason_size,
    )
    result = _render(env)
    # One-liner shape from Step 5.
    assert result == (
        "⛔ Task t-00000000-0000-7000-8000-0000000000bb blocked. "
        "(message body too large; see /logs t-00000000-0000-7000-8000-0000000000bb)"
    )
    # No available-commands footer in emergency tier.
    assert "Available commands:" not in result
    # Story 3.11 review H5: defensive final-length self-check assertion.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS


def test_render_blocker_raised_payload_type_mismatch_logs_and_falls_back() -> None:
    """H9 + H10 carry-forward: WARN + placeholder when payload isn't a typed instance.

    Constructs the envelope via :meth:`EventEnvelope.model_construct` to
    bypass Pydantic validation and forcibly assigns a raw-dict payload
    (the registration-race scenario). Uses
    :func:`structlog.testing.capture_logs` because the clawhip-daemon
    test environment does not configure stdlib logging.
    """
    import structlog.testing

    rng = Random(789)
    clk = FrozenClock(mono_ns=8_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.1.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": "t-raw-dict-blocker"},  # type: ignore[arg-type]
        request_id=rid,
    )
    with structlog.testing.capture_logs() as captured:
        result = _render(env)

    # Placeholder shape returned (Story 3.9 carry-forward).
    assert result == "Task t-raw-dict-blocker: task.blocker_raised"
    # H9: structured warning emitted with expected/actual fields.
    assert any(
        rec.get("event") == "renderer.payload_type_mismatch"
        and rec.get("log_level") == "warning"
        and rec.get("expected") == "TaskBlockerRaisedPayload"
        and rec.get("actual") == "dict"
        for rec in captured
    )
    # Story 3.11 review L7: assert the placeholder did NOT leak any
    # blocker-renderer-specific shape (no ⛔ header, no commands footer).
    assert "⛔" not in result
    assert "Available commands:" not in result


def test_render_dispatcher_routes_blocker_to_renderer() -> None:
    """AC-3: _render(envelope) for task.blocker_raised invokes _render_blocker_raised.

    The blocker renderer's distinguishing header is ``⛔ Task ... blocked.`` —
    the placeholder fallback does not include this.

    Story 3.11 review M3 / M10: assert the dispatcher invariant
    ``_RENDERERS["task.blocker_raised"] is _render_blocker_raised`` (the
    actual routing contract) — substring-only assertions pass vacuously
    if the entry is removed but ``task.blocker_raised`` remains in
    ``_DELIVERABLE_EVENT_TYPES``.
    """
    env = _blocker_envelope(reason="explicit dispatch check")
    result = _render(env)
    assert result.startswith("⛔ Task ")
    assert "explicit dispatch check" in result
    # Story 3.11 review M3 / M10: positive identity assertion on the
    # dispatcher entry — confirms the new entry was actually added (not
    # silently absent while a different test path produces the header).
    assert _RENDERERS["task.blocker_raised"] is _render_blocker_raised
    # Sanity: the static commands tuple ordering is preserved
    # (defensive on _BLOCKER_AVAILABLE_COMMANDS contract).
    assert _BLOCKER_AVAILABLE_COMMANDS == ("/logs", "/retry", "/stop", "/handoff")


# ---------------------------------------------------------------------------
# Story 3.11 review pass — additional renderer tests
# (M9 / M11 / L8 / L11 + H1 / H4 / H6 behavior coverage)
# ---------------------------------------------------------------------------


def test_render_blocker_raised_full_payload_all_sections() -> None:
    """M11: happy-path test exercising header + all 3 optional fields + footer in one render.

    Each field contributes a distinct section; assert all 5 are present
    AND in the spec-defined order (header → blocked_since → last_event →
    last_action → Available commands).
    """
    env = _blocker_envelope(
        task_id="t-00000000-0000-7000-8000-0000000000aa",
        reason="worker crashed mid-execution",
        blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        last_event="task.execution.started",
        last_action="ran pytest tests/",
    )
    result = _render(env)
    # All five sections present.
    assert "⛔ Task t-00000000-0000-7000-8000-0000000000aa blocked." in result
    assert "Blocked since: 2026-05-01T12:00:00+00:00" in result
    assert "Last event: task.execution.started" in result
    assert "Last action: ran pytest tests/" in result
    assert "Available commands:" in result
    # Sections appear in spec order.
    assert (
        result.index("⛔ Task ")
        < result.index("Blocked since:")
        < result.index("Last event:")
        < result.index("Last action:")
        < result.index("Available commands:")
    )
    # Sanity: under cap.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS


def test_render_blocker_raised_total_cap_drops_blocked_since_at_step_4() -> None:
    """M9: section-drop ladder Step 4 — drop ``blocked_since`` after Steps 2 + 3.

    Sized so that even after dropping ``last_action`` (Step 2) and
    ``last_event`` (Step 3), the message still overflows; dropping
    ``blocked_since`` (Step 4) brings it back under cap. The header +
    commands footer remain.

    Story 3.11 review M9: covers the otherwise-untested Step 4 ladder
    transition. The narrow band where Step 4 fires is roughly
    ``cap - 220 < len(reason) <= cap - 175`` once the optional fields
    are sized to consume the remaining headroom. Empirically (cap=1900):
    reason=1700, blocked_since=tz-aware datetime, last_event=128 chars,
    last_action=200 chars: full=2281, no_la=2066, no_la_no_le=1924
    (Step 3 still over), step4=1882 (Step 4 fits).
    """
    # Reason sized so Step 3 still overflows but Step 4 (drop blocked_since)
    # brings the message under cap. Parametric on cap.
    reason_size = _BLOCKER_MESSAGE_MAX_CHARS - 200
    env = _blocker_envelope(
        reason="r" * reason_size,
        blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        last_event="x" * 128,
        last_action="a" * 200,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS
    # Header + footer preserved.
    assert "⛔ Task " in result
    assert "Available commands:" in result
    # All three optional sections dropped.
    assert "Last action:" not in result
    assert "Last event:" not in result
    assert "Blocked since:" not in result


def test_render_blocker_raised_is_not_async() -> None:
    """L8 / AC-6: renderer is ``def``, not ``async def`` — pure sync function.

    Confirms the renderer is trivially unit-testable without an event
    loop. Mirrors the AC-6 carry-forward from Story 3.10's approval
    renderer.
    """
    import inspect

    assert not inspect.iscoroutinefunction(_render_blocker_raised)


def test_render_blocker_raised_minimal_emits_no_payload_type_mismatch_warn() -> None:
    """L11: success path emits NO ``renderer.payload_type_mismatch`` WARN.

    Sibling to the H9 test that asserts the WARN IS emitted on the
    raw-dict-payload negative path. Without this positive-path assertion
    a future refactor that always logs the WARN would pass the negative
    test silently.
    """
    import structlog.testing

    env = _blocker_envelope(reason="worker crashed")
    with structlog.testing.capture_logs() as captured:
        _ = _render(env)

    assert not any(rec.get("event") == "renderer.payload_type_mismatch" for rec in captured), (
        f"unexpected payload_type_mismatch on success path: {captured!r}"
    )


def test_render_blocker_raised_strips_trailing_terminal_punctuation_in_reason() -> None:
    """H1: trailing terminal punctuation in ``reason`` is stripped before injection.

    Without this, ``reason="crashed."`` produces double punctuation in
    the header line (``"...crashed.. See /logs..."``). Story 3.11 review
    H1 fixes this by stripping ``.``/``?``/``!``/``:`` before HTML-escape.
    """
    for raw in ("crashed.", "crashed?", "crashed!", "crashed:"):
        env = _blocker_envelope(reason=raw)
        result = _render(env)
        # Header line ends with a single period (the renderer-added one),
        # not double punctuation.
        assert ". See /logs " in result
        assert ".." not in result.split(". See /logs ")[0]
        # The chosen punctuation char does not survive in the rendered
        # header before the renderer-added period.
        before_period = result.split(". See /logs ")[0]
        assert raw[-1] not in before_period.split("blocked. ")[1]


def test_render_blocker_raised_collapses_newlines_in_last_event() -> None:
    """H4: ``last_event`` newlines are collapsed too (not just ``reason`` / ``last_action``).

    A schema-valid ``last_event`` value containing ``\\n`` would
    otherwise inject a bogus section break since the renderer joins
    sections with ``\\n\\n``.
    """
    # The model boundary allows up to 128 chars; embed a newline.
    env = _blocker_envelope(last_event="evt\nattacker")
    result = _render(env)
    # Newline collapsed to space.
    assert "Last event: evt attacker" in result
    # No literal newline appears within the last_event line.
    assert "Last event: evt\nattacker" not in result


def test_render_blocker_raised_collapses_carriage_returns_in_reason_and_last_action() -> None:
    """H6 / L17: ``\\r\\n`` and bare ``\\r`` are collapsed too (not just ``\\n``).

    Pre-fix, ``reason="line1\\r\\nline2"`` produced ``"line1\\r line2"``
    after the naive ``.replace("\\n", " ")``. Story 3.11 review H6 / L17
    extracts ``_collapse_newlines`` which handles ``\\r\\n`` first, then
    bare ``\\r``, then ``\\n`` so legacy line endings collapse cleanly.
    """
    env = _blocker_envelope(
        reason="line1\r\nline2",
        last_action="step1\r\nstep2\rstep3",
    )
    result = _render(env)
    # No raw CR or LF survives in the operator-supplied content
    # (the section separator ``\n\n`` is fine — that's renderer-added).
    # Header line has no CR / LF embedded.
    header_line = next(
        (line for line in result.split("\n") if line.startswith("⛔ ")),
        "",
    )
    assert header_line, f"header line not found: {result!r}"
    assert "\r" not in header_line
    # Last action line is single-line.
    last_action_line = next(
        (line for line in result.split("\n") if line.startswith("Last action: ")),
        "",
    )
    assert last_action_line == "Last action: step1 step2 step3"


def test_render_blocker_raised_blocked_since_drops_microseconds() -> None:
    """M14: ``blocked_since`` rendered with ``timespec='seconds'`` — no microseconds.

    Without timespec="seconds", microsecond presence shifts the rendered
    length by 7 chars (``+00:00`` vs ``.123456+00:00``), perturbing the
    section-drop ladder math. The renderer locks to seconds precision.
    """
    aware = datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=UTC)
    env = _blocker_envelope(blocked_since=aware)
    result = _render(env)
    # No microsecond fragment.
    assert ".123456" not in result
    # Seconds-precision representation present.
    assert "Blocked since: 2026-05-01T12:00:00+00:00" in result


def test_render_blocker_raised_emergency_clamps_to_cap_when_task_id_oversized() -> None:
    """H2 + H5: emergency one-liner with raw ``<`` task_id — escape-then-slice safety.

    Construct an envelope via ``model_construct`` to bypass the model's
    1..64 cap on ``task_id`` and force a 65-char raw ``<`` task_id. The
    pre-fix ``task_id_esc[:64]`` slice would split mid-entity (each ``<``
    escapes to 5 chars of ``&lt;``); the post-fix
    ``html.escape(payload.task_id[:64])`` slices the RAW string first.
    Reason is sized to force Step 5.
    """
    # task_id of 65 ``<`` chars — would escape to 65*5 = 325 chars under
    # the old slice-after-escape code.
    bad_task_id = "<" * 65
    # Pydantic v2 ``model_construct`` bypasses field validators — the
    # 1..64 max_length is intentionally bypassed for this test.
    payload = TaskBlockerRaisedPayload.model_construct(
        task_id=bad_task_id,
        reason="X" * (_BLOCKER_MESSAGE_MAX_CHARS + 90),
        blocked_since=None,
        last_event=None,
        last_action=None,
    )
    rng = Random(101)
    clk = FrozenClock(mono_ns=9_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.1.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )
    result = _render(env)
    # H2: no truncated entity (no bare ``&l`` or ``&lt`` without ``;``).
    # The escape happens AFTER the slice, so the result contains exactly
    # 64 fully-formed ``&lt;`` entities per occurrence.
    # H5: result respects the cap.
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS
    # No partial-entity tokens — every ``&l`` is followed by ``t;``.
    # Use a strict structural check: stripping all complete ``&lt;`` and
    # ``&gt;`` entities should leave NO bare ``&`` chars from the
    # task_id occurrences.
    stripped = result.replace("&lt;", "").replace("&gt;", "")
    # No truncated entity fragments (e.g. ``&l`` alone) remain.
    assert "&l" not in stripped
    assert "&g" not in stripped


# ---------------------------------------------------------------------------
# Story 3.12 — _render_completed renderer + dispatcher tests
# ---------------------------------------------------------------------------


# Story 3.10 M8 / Story 3.11 H11 carry-forward: idempotent guard so
# repeat invocations of the test helper don't re-run the schema_registry
# .register side-effect on every helper call.
_COMPLETED_REGISTERED: bool = False


def _ensure_completed_registered() -> None:
    """Register task.completed 1.1.0 — idempotent (Story 3.10 M8 / 3.11 H11)."""
    global _COMPLETED_REGISTERED
    if _COMPLETED_REGISTERED:
        return
    _reg("task.completed", "1.1.0", TaskCompletedPayload)
    _COMPLETED_REGISTERED = True


def _completed_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000003",
    summary: str = "task complete",
    pr_url: str | None = None,
    pr_number: int | None = None,
    pr_branch: str | None = None,
    files_changed: int | None = None,
    lines_added: int | None = None,
    lines_removed: int | None = None,
    tests_added: int | None = None,
    ci_state: Literal["green", "red", "unknown"] | None = None,
    blockers_count: int | None = None,
    mono_ns: int = 12_000_000,
) -> EventEnvelope:
    """Build a task.completed envelope (schema 1.1.0).

    Story 3.11 review M1 / M2 carry-forward: top-of-file
    ``TaskCompletedPayload`` import (no inline import); schema
    registration is idempotent via :func:`_ensure_completed_registered`
    (Story 3.10 M8 / 3.11 H11 pattern).
    """
    _ensure_task_created_registered()
    _ensure_completed_registered()

    rng = Random(312)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskCompletedPayload(
        task_id=task_id,
        summary=summary,
        pr_url=pr_url,
        pr_number=pr_number,
        pr_branch=pr_branch,
        files_changed=files_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
        tests_added=tests_added,
        ci_state=ci_state,
        blockers_count=blockers_count,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_completed_minimal() -> None:
    """AC-2: only required fields → header + summary; optional sections absent.

    Story 3.12 review M10: structural assertions instead of fragile
    ``split("complete.")`` slicing — the prior form depended on the
    cardinality of ``"complete."`` substrings in the rendered output.
    Story 3.12 review L3: assert ``isinstance(result, str)`` so a runtime
    regression to ``None`` cannot silently break Telegram dispatch (the
    type annotation is mypy-only).
    """
    env = _completed_envelope(
        task_id="t-00000000-0000-7000-8000-000000000003",
        summary="task complete",
    )
    result = _render(env)
    # L3: runtime type guarantee (annotation alone is mypy-only).
    assert isinstance(result, str)
    # Header present.
    assert "✅ Task t-00000000-0000-7000-8000-000000000003 complete." in result
    # Summary present.
    assert "task complete" in result
    # M10: structural absence checks for each optional section. The
    # tokens here are unique to their respective renderer branches and do
    # not appear in the header or summary.
    assert "PR #" not in result
    assert "Branch:" not in result
    assert "PR:" not in result
    assert "files changed" not in result
    assert "tests added" not in result
    assert "CI:" not in result
    assert "blockers raised" not in result
    assert " — " not in result  # PR line / diff "fc+lr" separator


def test_render_completed_with_pr_full() -> None:
    """AC-2: pr_number + pr_branch + pr_url → ``PR #N: <branch> — <url>``."""
    env = _completed_envelope(
        pr_number=42,
        pr_branch="feat/foo",
        pr_url="https://github.com/example/repo/pull/42",
    )
    result = _render(env)
    assert "PR #42: feat/foo — https://github.com/example/repo/pull/42" in result


def test_render_completed_with_pr_partial() -> None:
    """AC-2: only pr_number → ``PR: #42`` form."""
    env = _completed_envelope(pr_number=42)
    result = _render(env)
    assert "PR: #42" in result
    assert "feat/" not in result
    assert "https://" not in result


def test_render_completed_with_diff_stats_full() -> None:
    """AC-2: full diff counters → ``5 files changed, 234+ / 89- lines.``."""
    env = _completed_envelope(
        files_changed=5,
        lines_added=234,
        lines_removed=89,
    )
    result = _render(env)
    assert "5 files changed, 234+ / 89- lines." in result


def test_render_completed_with_tests_added() -> None:
    """AC-2: tests_added populated → ``12 tests added.`` line."""
    env = _completed_envelope(tests_added=12)
    result = _render(env)
    assert "12 tests added." in result


@pytest.mark.parametrize(
    ("ci_state", "emoji"),
    [
        ("green", "✅"),
        ("red", "❌"),
        ("unknown", "❓"),
    ],
)
def test_render_completed_with_ci_state(
    ci_state: Literal["green", "red", "unknown"], emoji: str
) -> None:
    """AC-2: each ci_state Literal renders ``CI: <emoji> <state>``."""
    env = _completed_envelope(ci_state=ci_state)
    result = _render(env)
    assert f"CI: {emoji} {ci_state}" in result


def test_render_completed_with_blockers_count() -> None:
    """AC-2: blockers_count populated → ``2 blockers raised.`` line."""
    env = _completed_envelope(blockers_count=2)
    result = _render(env)
    assert "2 blockers raised." in result


def test_render_completed_html_escapes_task_id_summary_pr_branch_pr_url() -> None:
    """AC-4 / Story 3.5 H5 carry-forward: HTML-escape all operator-supplied strings.

    Story 3.11 review M4 carry-forward: per-character invariants on the
    escaped output so a future renderer drift cannot slip past.
    """
    env = _completed_envelope(
        task_id="t-<x>",
        summary="<b>done</b>",
        pr_branch="feat/<foo>",
        pr_url="https://example.com/?<x>=1",
    )
    result = _render(env)
    # Sanity: no raw payload strings appear verbatim.
    assert "<b>done</b>" not in result
    assert "feat/<foo>" not in result
    # Escaped forms appear.
    assert "t-&lt;x&gt;" in result
    assert "&lt;b&gt;done&lt;/b&gt;" in result
    assert "feat/&lt;foo&gt;" in result
    assert "https://example.com/?&lt;x&gt;=1" in result
    # Story 3.11 review M4: per-character invariants — once ``&lt;`` /
    # ``&gt;`` are removed, no raw ``<`` / ``>`` characters remain.
    assert "<" not in result.replace("&lt;", "")
    assert ">" not in result.replace("&gt;", "")
    # Story 3.12 review M11: extend the per-character discipline to ``&``.
    # After stripping every well-formed entity, no bare ``&`` may remain.
    stripped = (
        result.replace("&lt;", "")
        .replace("&gt;", "")
        .replace("&amp;", "")
        .replace("&quot;", "")
        .replace("&#x27;", "")
    )
    assert "&" not in stripped


def test_render_completed_collapses_multiline_summary_and_pr_branch() -> None:
    """AC-2 / Story 3.10 H11 + 3.11 H6 carry-forward: ``\\n`` collapsed to space.

    Defense-in-depth — branch names should not contain ``\\n`` per git
    ref-name rules, but if a buggy emitter slips one in, defend.
    """
    env = _completed_envelope(
        summary="line1\nline2",
        pr_branch="feat/foo\nbar",
    )
    result = _render(env)
    # Newlines collapsed to spaces in operator-supplied content.
    assert "line1 line2" in result
    assert "feat/foo bar" in result
    # No raw ``\n`` in the summary line itself (section separator
    # ``\n\n`` is renderer-added and intact).
    assert "line1\nline2" not in result
    assert "feat/foo\nbar" not in result


def test_render_completed_total_cap_drops_in_spec_order() -> None:
    """AC-5: section-drop ladder Step 2 — diff stats dropped first when cap exceeded.

    Sized so that the full message (with diff stats present) overflows
    by an amount recoverable by dropping ONLY the diff stats line. All
    other sections (PR / tests / CI / blockers / summary) stay present.

    Story 3.11 review H12 carry-forward: sizing parametric on
    ``_COMPLETED_MESSAGE_MAX_CHARS``.

    Story 3.12 review M9: previous docstring carried hand-computed section
    sizes (off by 2 chars for the 7-section / 6-separator count). Re-derive
    the size programmatically from each candidate sub-message instead so a
    future renderer tweak that shifts overhead by a few chars cannot
    silently let the test pass for the wrong reason.
    """
    big_summary = "s" * (_COMPLETED_MESSAGE_MAX_CHARS - 150)
    env = _completed_envelope(
        summary=big_summary,
        pr_number=42,
        pr_branch="feat/foo",
        files_changed=5,
        lines_added=234,
        lines_removed=89,
        tests_added=12,
        ci_state="green",
        blockers_count=2,
    )
    result = _render(env)
    # Cap honored.
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    # M9: structural sanity — the result is the Step-2 candidate exactly.
    # Step 1 (full) must overflow; Step 2 (no diff stats) must fit.
    from clawhip_daemon.adapters.sinks.telegram_sink import _assemble_completed_sections

    payload = env.payload
    assert isinstance(payload, TaskCompletedPayload)
    full = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=True,
        include_tests=True,
        include_ci_state=True,
        include_blockers=True,
        include_summary=True,
    )
    no_diff = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=False,
        include_tests=True,
        include_ci_state=True,
        include_blockers=True,
        include_summary=True,
    )
    assert len(full) > _COMPLETED_MESSAGE_MAX_CHARS
    assert len(no_diff) <= _COMPLETED_MESSAGE_MAX_CHARS
    assert result == no_diff
    # Mandatory header preserved.
    assert "✅ Task " in result
    # Summary preserved (semantically most valuable per FR9).
    assert "complete." in result
    assert big_summary in result
    # Diff stats line dropped (Step 2).
    assert "files changed" not in result
    # Other optional sections preserved.
    assert "PR #42: feat/foo" in result
    assert "12 tests added." in result
    assert "CI: ✅ green" in result
    assert "2 blockers raised." in result


def test_render_completed_emergency_fallback_when_summary_too_long() -> None:
    """AC-5: section-drop ladder Step 7 — emergency one-liner; summary dropped.

    When ``summary`` alone is large enough that even header + summary
    can't fit together, the renderer falls back to the one-liner that
    still embeds ``/logs <id>`` for recovery.

    Story 3.11 review H12 carry-forward: ``summary`` size parametric on
    the cap so future cap changes still route through Step 7.
    ``cap + 90`` keeps size under the model boundary (max_length=2000)
    when cap=1900 while guaranteeing Steps 1-6 all overflow.

    Story 3.11 review H5 carry-forward: assert the final length is
    under the cap (defensive self-clamp).
    """
    summary_size = _COMPLETED_MESSAGE_MAX_CHARS + 90
    env = _completed_envelope(
        task_id="t-00000000-0000-7000-8000-0000000000cc",
        summary="X" * summary_size,
    )
    result = _render(env)
    # One-liner shape from Step 7.
    assert result == (
        "✅ Task t-00000000-0000-7000-8000-0000000000cc complete. "
        "(message body too large; see /logs t-00000000-0000-7000-8000-0000000000cc)"
    )
    # Story 3.11 review H5: defensive final-length self-check assertion.
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS


def test_render_completed_payload_type_mismatch_logs_and_falls_back() -> None:
    """H9 + H10 carry-forward: WARN + placeholder when payload isn't a typed instance.

    Constructs the envelope via :meth:`EventEnvelope.model_construct` to
    bypass Pydantic validation and forcibly assigns a raw-dict payload
    (the registration-race scenario).
    """
    import structlog.testing

    rng = Random(789)
    clk = FrozenClock(mono_ns=13_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.1.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": "t-raw-dict-completed"},  # type: ignore[arg-type]
        request_id=rid,
    )
    with structlog.testing.capture_logs() as captured:
        result = _render(env)

    # Placeholder shape returned (Story 3.9 carry-forward).
    assert result == "Task t-raw-dict-completed: task.completed"
    # H9: structured warning emitted with expected/actual fields.
    assert any(
        rec.get("event") == "renderer.payload_type_mismatch"
        and rec.get("log_level") == "warning"
        and rec.get("expected") == "TaskCompletedPayload"
        and rec.get("actual") == "dict"
        for rec in captured
    )
    # Story 3.11 review L7 carry-forward: assert the placeholder did
    # NOT leak any completed-renderer-specific shape.
    assert "✅" not in result
    assert "complete." not in result


def test_render_dispatcher_routes_completed_to_renderer() -> None:
    """AC-3: _render(envelope) for task.completed invokes _render_completed.

    Story 3.11 review M3 / M10 carry-forward: positive identity
    assertion on the dispatcher entry — substring assertions pass
    vacuously if the entry is removed but ``task.completed`` remains
    in ``_DELIVERABLE_EVENT_TYPES``.

    Story 3.12 review L2: tighten the substring shape — ``startswith("✅
    Task ")`` rules in the typed renderer; ``not startswith("Task ")``
    rules out the placeholder fallback shape (``Task <id>: <type>``)
    that bare-``Task`` would also match.
    """
    env = _completed_envelope(summary="explicit dispatch check")
    result = _render(env)
    assert result.startswith("✅ Task ")
    # L2: rule out the placeholder fallback shape ``Task <id>: <type>``.
    assert not result.startswith("Task ")
    assert "explicit dispatch check" in result
    # Story 3.11 review M3 / M10: positive identity assertion on the
    # dispatcher entry — confirms the new entry was actually added.
    assert _RENDERERS["task.completed"] is _render_completed
    # Sanity: task.completed is in the deliverable allowlist (Story 3.9 L15).
    assert "task.completed" in _DELIVERABLE_EVENT_TYPES


# ---------------------------------------------------------------------------
# Story 3.12 review-pass additions (H1, H3-H10, M4-M12, L3, L7)
# ---------------------------------------------------------------------------


# H1 retroactive — emergency-tier task_id newline-collapse for all 3 renderers.


def test_render_completed_emergency_collapses_newline_in_task_id() -> None:
    """H1: emergency-tier ``task_id`` containing ``\\n`` does NOT smuggle a newline.

    Slice-before-escape (Story 3.11 H2) operates on the RAW task_id; if
    that raw string contained ``\\n``, the escaped output would still
    embed the literal newline byte. ``_collapse_newlines`` runs FIRST
    so the emergency one-liner stays a single line as advertised.
    """
    # task_id with embedded \n; max_length=64 so 6 chars + \n + 6 chars fits.
    big_summary = "X" * (_COMPLETED_MESSAGE_MAX_CHARS + 90)
    env = _completed_envelope(
        task_id="t-aa\nbbcc",
        summary=big_summary,
    )
    result = _render(env)
    # Emergency one-liner shape — must be a single line (no embedded \n
    # except the one between the two halves of the f-string... wait, the
    # 3.12 emergency message uses a single space, not \n\n. Either way the
    # task_id segment must not contain \n). The completed emergency form
    # is: "✅ Task <id> complete. (message body too large; see /logs <id>)"
    # — single-line by construction.
    assert "\n" not in result
    # The original task_id had \n collapsed to space.
    assert "t-aa bbcc" in result
    # Cap honored.
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS


def test_render_blocker_raised_emergency_collapses_newline_in_task_id() -> None:
    """H1 retroactive 3.11: blocker emergency-tier collapses ``\\n`` in task_id."""
    _ensure_task_created_registered()
    _ensure_blocker_raised_registered()
    rng = Random(311)
    clk = FrozenClock(mono_ns=14_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskBlockerRaisedPayload(
        task_id="t-aa\nbbcc",
        reason="X" * (_BLOCKER_MESSAGE_MAX_CHARS + 90),
    )
    env = EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.blocker_raised",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )
    result = _render(env)
    # Emergency one-liner is single-line by construction.
    assert "\n" not in result
    # Newline collapsed to space.
    assert "t-aa bbcc" in result
    assert len(result) <= _BLOCKER_MESSAGE_MAX_CHARS


def test_render_approval_request_emergency_collapses_newline_in_task_id() -> None:
    """H1 retroactive 3.10: approval emergency-tier collapses ``\\n`` in task_id.

    Approval emergency message uses ``\\n\\n`` between header and the
    "(message body too large...)" tail — assert the one-liner contains
    exactly that single separator and no extra newlines from a smuggled
    task_id.
    """
    env = _approval_envelope(
        task_id="t-aa\nbbcc",
        # justification long enough to overflow even after dropping all
        # optional sections + pre_checks.
        justification="J" * (_APPROVAL_MESSAGE_MAX_CHARS + 90),
    )
    result = _render(env)
    # The approval emergency one-liner contains exactly ONE ``\n\n``
    # separator added by the renderer. A smuggled \n in task_id would
    # bump the count.
    assert result.count("\n") == 2
    assert "t-aa bbcc" in result
    assert len(result) <= _APPROVAL_MESSAGE_MAX_CHARS


# H3 — section-drop ladder Steps 3-6 boundary tests.


def _completed_step_sizes(payload: TaskCompletedPayload) -> dict[int, int]:
    """Compute the assembled length at each ladder step for a given payload.

    Used by the H3/H4 boundary tests to size summaries dynamically so the
    test stays correct under future renderer overhead changes.
    """
    from clawhip_daemon.adapters.sinks.telegram_sink import _assemble_completed_sections

    flags = [
        # (include_pr, include_diff, include_tests, include_ci, include_blockers, include_summary)
        (True, True, True, True, True, True),  # Step 1: full
        (True, False, True, True, True, True),  # Step 2: no diff
        (True, False, True, True, False, True),  # Step 3: no blockers
        (True, False, False, True, False, True),  # Step 4: no tests
        (True, False, False, False, False, True),  # Step 5: no CI
        (False, False, False, False, False, True),  # Step 6: no PR
    ]
    sizes: dict[int, int] = {}
    for i, (pr, df, ts, ci, bl, sm) in enumerate(flags, start=1):
        text = _assemble_completed_sections(
            payload,
            include_pr=pr,
            include_diff_stats=df,
            include_tests=ts,
            include_ci_state=ci,
            include_blockers=bl,
            include_summary=sm,
        )
        sizes[i] = len(text)
    return sizes


def _build_step_boundary_payload(target_step: int) -> TaskCompletedPayload:
    """Build a payload whose section-drop ladder lands exactly on *target_step*.

    Iteratively increase the summary size until step (target_step - 1)
    overflows AND step target_step fits.
    """
    cap = _COMPLETED_MESSAGE_MAX_CHARS
    # Search summary length so step (target_step - 1) overflows by at
    # least 1 char and step target_step is at most cap.
    # Binary search range: 0 .. cap - 1.
    base_kwargs = {
        "task_id": "t-00000000-0000-7000-8000-000000000003",
        "pr_number": 42,
        "pr_branch": "feat/foo",
        "files_changed": 5,
        "lines_added": 234,
        "lines_removed": 89,
        "tests_added": 12,
        "ci_state": "green" if target_step >= 5 else "green",
        "blockers_count": 2,
    }
    # Brute search: find the smallest summary such that the renderer
    # picks `target_step` (i.e. step `target_step - 1` overflows but step
    # `target_step` fits).
    for size in range(1, cap):
        payload = TaskCompletedPayload(summary="s" * size, **base_kwargs)
        sizes = _completed_step_sizes(payload)
        prev = sizes.get(target_step - 1, cap + 1)
        cur = sizes[target_step]
        if prev > cap and cur <= cap:
            return payload
    raise RuntimeError(f"could not find boundary payload for step {target_step}")


def test_render_completed_total_cap_drops_blockers_at_step_3() -> None:
    """H3: Step 3 (drop blockers_count) — Steps 1+2 overflow; Step 3 fits."""
    payload = _build_step_boundary_payload(target_step=3)
    env = _completed_envelope(
        task_id=payload.task_id,
        summary=payload.summary,
        pr_number=payload.pr_number,
        pr_branch=payload.pr_branch,
        files_changed=payload.files_changed,
        lines_added=payload.lines_added,
        lines_removed=payload.lines_removed,
        tests_added=payload.tests_added,
        ci_state=payload.ci_state,
        blockers_count=payload.blockers_count,
    )
    result = _render(env)
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    # Step-3 candidate: header + PR + tests + CI + summary preserved;
    # diff + blockers dropped.
    assert "✅ Task " in result
    assert payload.summary in result
    assert "PR #42: feat/foo" in result
    assert "12 tests added." in result
    assert "CI: ✅ green" in result
    assert "files changed" not in result
    assert "blockers raised" not in result


def test_render_completed_total_cap_drops_tests_at_step_4() -> None:
    """H3: Step 4 (drop tests_added) — Steps 1-3 overflow; Step 4 fits."""
    payload = _build_step_boundary_payload(target_step=4)
    env = _completed_envelope(
        task_id=payload.task_id,
        summary=payload.summary,
        pr_number=payload.pr_number,
        pr_branch=payload.pr_branch,
        files_changed=payload.files_changed,
        lines_added=payload.lines_added,
        lines_removed=payload.lines_removed,
        tests_added=payload.tests_added,
        ci_state=payload.ci_state,
        blockers_count=payload.blockers_count,
    )
    result = _render(env)
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    assert "✅ Task " in result
    assert payload.summary in result
    assert "PR #42: feat/foo" in result
    assert "CI: ✅ green" in result
    assert "tests added" not in result
    assert "files changed" not in result
    assert "blockers raised" not in result


def test_render_completed_total_cap_drops_ci_at_step_5() -> None:
    """H3: Step 5 (drop ci_state) — Steps 1-4 overflow; Step 5 fits."""
    payload = _build_step_boundary_payload(target_step=5)
    env = _completed_envelope(
        task_id=payload.task_id,
        summary=payload.summary,
        pr_number=payload.pr_number,
        pr_branch=payload.pr_branch,
        files_changed=payload.files_changed,
        lines_added=payload.lines_added,
        lines_removed=payload.lines_removed,
        tests_added=payload.tests_added,
        ci_state=payload.ci_state,
        blockers_count=payload.blockers_count,
    )
    result = _render(env)
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    assert "✅ Task " in result
    assert payload.summary in result
    assert "PR #42: feat/foo" in result
    assert "CI:" not in result
    assert "tests added" not in result
    assert "files changed" not in result
    assert "blockers raised" not in result


def test_render_completed_total_cap_drops_pr_at_step_6() -> None:
    """H3: Step 6 (drop PR line) — Steps 1-5 overflow; Step 6 fits.

    Header + summary survive; PR / CI / counts all dropped.
    """
    payload = _build_step_boundary_payload(target_step=6)
    env = _completed_envelope(
        task_id=payload.task_id,
        summary=payload.summary,
        pr_number=payload.pr_number,
        pr_branch=payload.pr_branch,
        files_changed=payload.files_changed,
        lines_added=payload.lines_added,
        lines_removed=payload.lines_removed,
        tests_added=payload.tests_added,
        ci_state=payload.ci_state,
        blockers_count=payload.blockers_count,
    )
    result = _render(env)
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    assert "✅ Task " in result
    assert payload.summary in result
    # Step 6 drops PR — but header + summary still under cap.
    assert "PR #" not in result
    assert "CI:" not in result
    assert "tests added" not in result
    assert "files changed" not in result
    assert "blockers raised" not in result
    # Sanity: not the emergency one-liner.
    assert "(message body too large" not in result


# H4 — Step 6 → Step 7 boundary.


def test_render_completed_step_6_fits_at_exact_threshold() -> None:
    """H4: header + ``\\n\\n`` + summary == cap exactly takes Step 6, not Step 7.

    Computes the exact summary size that makes Step 6 (header + summary)
    land precisely at the cap, then verifies the renderer chose Step 6
    (header + summary visible) and NOT Step 7 (emergency one-liner).
    """
    task_id = "t-00000000-0000-7000-8000-000000000003"
    header = f"✅ Task {task_id} complete."
    # header + "\n\n" + summary == cap → summary length = cap - len(header) - 2.
    summary_len = _COMPLETED_MESSAGE_MAX_CHARS - len(header) - 2
    summary = "s" * summary_len
    env = _completed_envelope(task_id=task_id, summary=summary)
    result = _render(env)
    assert len(result) == _COMPLETED_MESSAGE_MAX_CHARS
    assert result == f"{header}\n\n{summary}"
    # NOT the emergency one-liner.
    assert "(message body too large" not in result


# H5 — UTF-16 surrogate-pair length safety.


def test_render_completed_utf16_surrogate_pair_safety() -> None:
    """H5: 4-byte UTF-8 emoji at the cap boundary stays under Telegram's UTF-16 limit.

    Telegram counts message length in UTF-16 code units (cap = 4096).
    Each 😀 (U+1F600) is one Python codepoint but TWO UTF-16 code units
    (surrogate pair). The 1900-codepoint cap defends against pathological
    emoji inputs by leaving headroom: 1900 codepoints × ≤2 UTF-16 units =
    ≤ 3800 units, well under 4096.
    """
    summary = "😀" * (_COMPLETED_MESSAGE_MAX_CHARS // 2)
    env = _completed_envelope(summary=summary)
    result = _render(env)
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    # UTF-16 length under Telegram's 4096-code-unit limit.
    utf16_units = len(result.encode("utf-16-le")) // 2
    assert utf16_units <= 4096


# H6 — emergency-clamp test for completed renderer.


def test_render_completed_emergency_clamps_to_cap_when_task_id_oversized() -> None:
    """H6: an oversized task_id passing through the emergency tier is clamped to cap.

    Story 3.11 H5 carry-forward — defensive final-length self-check on
    the emergency tier. Constructs a model_construct-bypass payload with
    a 64-char ``<`` task_id (escapes to 64 × 5 = 320 chars per occurrence,
    × 2 occurrences = 640 + header overhead). Final length must be ≤ cap
    AND must not split mid-entity (no trailing ``&l`` / ``&lt`` / ``&am``).
    """
    # Build a payload via model_construct so we can inject a task_id that
    # would otherwise hit max_length=64 — but our 64 raw ``<`` chars is
    # exactly at the model boundary too.
    rng = Random(312)
    clk = FrozenClock(mono_ns=15_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    raw_task_id = "<" * _EMERGENCY_TASK_ID_MAX_CHARS  # 64 ``<``
    payload = TaskCompletedPayload.model_construct(
        task_id=raw_task_id,
        summary="X" * (_COMPLETED_MESSAGE_MAX_CHARS + 90),
    )
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.1.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )
    result = _render(env)
    # Final length under cap.
    assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS
    # Must not end mid-entity.
    assert not result.endswith("&")
    assert not result.endswith("&l")
    assert not result.endswith("&lt")
    assert not result.endswith("&am")
    assert not result.endswith("&amp")


# H8 — pure-def invariant.


def test_render_completed_is_not_async() -> None:
    """H8 / Story 3.11 L8 carry-forward: AC-6 — renderer is ``def``, not ``async def``."""
    import inspect

    assert inspect.iscoroutinefunction(_render_completed) is False


# H9 — happy-path no-WARN assertion.


def test_render_completed_minimal_emits_no_payload_type_mismatch_warn() -> None:
    """H9 / Story 3.11 L11 carry-forward: typed payload happy-path emits no WARN.

    Wraps a minimal ``_render_completed`` call with
    ``structlog.testing.capture_logs()`` and asserts that no
    ``renderer.payload_type_mismatch`` event was logged.
    """
    import structlog.testing

    env = _completed_envelope(summary="task complete")
    with structlog.testing.capture_logs() as captured:
        result = _render(env)

    assert "✅ Task " in result
    assert not any(rec.get("event") == "renderer.payload_type_mismatch" for rec in captured)


# H10 — parametrized PR-line forms (7) + diff-stats forms (7).


@pytest.mark.parametrize(
    ("pr_number", "pr_branch", "pr_url", "expected_substring"),
    [
        (
            42,
            "feat/foo",
            "https://example.com/pr/42",
            "PR #42: feat/foo — https://example.com/pr/42",
        ),
        (42, "feat/foo", None, "PR #42: feat/foo"),
        (42, None, "https://example.com/pr/42", "PR #42: https://example.com/pr/42"),
        (None, "feat/foo", "https://example.com/pr/42", "feat/foo — https://example.com/pr/42"),
        (None, None, "https://example.com/pr/42", "PR: https://example.com/pr/42"),
        (None, "feat/foo", None, "Branch: feat/foo"),
        (42, None, None, "PR: #42"),
    ],
)
def test_build_pr_line_forms(
    pr_number: int | None,
    pr_branch: str | None,
    pr_url: str | None,
    expected_substring: str,
) -> None:
    """H10: all 7 PR-line forms render correctly.

    Story 3.11 review M3 carry-forward: parametrized over the full
    branch table so a future field addition cannot silently change
    one form's output without flagging.
    """
    payload = TaskCompletedPayload(
        task_id="t-00000000-0000-7000-8000-000000000003",
        summary="x",
        pr_number=pr_number,
        pr_branch=pr_branch,
        pr_url=pr_url,
    )
    result = _build_pr_line(payload)
    assert result == expected_substring


def test_build_pr_line_returns_none_when_all_pr_fields_absent() -> None:
    """H10: ``pr_number=pr_branch=pr_url=None`` yields ``None`` (no PR section)."""
    payload = TaskCompletedPayload(
        task_id="t-00000000-0000-7000-8000-000000000003",
        summary="x",
    )
    assert _build_pr_line(payload) is None


@pytest.mark.parametrize(
    ("fc", "la", "lr", "expected"),
    [
        # All three.
        (5, 234, 89, "5 files changed, 234+ / 89- lines."),
        # H2: explicit fc+la branch (was silent data loss before).
        (5, 234, None, "5 files changed, 234+ lines."),
        # H2: explicit fc+lr branch (was silent data loss before).
        (5, None, 89, "5 files changed, 89- lines."),
        # fc only.
        (5, None, None, "5 files changed."),
        # la+lr.
        (None, 234, 89, "234+ / 89- lines."),
        # la only.
        (None, 234, None, "234 lines added."),
        # lr only.
        (None, None, 89, "89 lines removed."),
    ],
)
def test_build_diff_stats_line_forms(
    fc: int | None, la: int | None, lr: int | None, expected: str
) -> None:
    """H10: all 7 diff-stats forms render correctly.

    Story 3.12 review H2: previously the ``fc+la-no-lr`` and ``fc+lr-no-la``
    combinations silently dropped the line counter and rendered as
    ``fc files changed.`` only. The fix surfaces both data points.
    """
    payload = TaskCompletedPayload(
        task_id="t-00000000-0000-7000-8000-000000000003",
        summary="x",
        files_changed=fc,
        lines_added=la,
        lines_removed=lr,
    )
    assert _build_diff_stats_line(payload) == expected


def test_build_diff_stats_line_returns_none_when_all_counters_absent() -> None:
    """H10: all-None counters → ``None`` (no diff section)."""
    payload = TaskCompletedPayload(
        task_id="t-00000000-0000-7000-8000-000000000003",
        summary="x",
    )
    assert _build_diff_stats_line(payload) is None


# M1 — zero-counter omission semantics.


def test_render_completed_omits_zero_tests_added() -> None:
    """M1: ``tests_added=0`` does NOT render ``"0 tests added."`` line."""
    env = _completed_envelope(tests_added=0)
    result = _render(env)
    assert "tests added" not in result


def test_render_completed_omits_zero_blockers_count() -> None:
    """M1: ``blockers_count=0`` does NOT render ``"0 blockers raised."`` line."""
    env = _completed_envelope(blockers_count=0)
    result = _render(env)
    assert "blockers raised" not in result


def test_render_completed_omits_zero_diff_stats() -> None:
    """M1: all-zero diff counters → diff stats line omitted entirely."""
    env = _completed_envelope(files_changed=0, lines_added=0, lines_removed=0)
    result = _render(env)
    assert "files changed" not in result
    assert "lines" not in result.replace("complete.", "")  # avoid matching "complete."'s 'l'


# M4 — _collapse_newlines covers \r\n / \r.


def test_render_completed_collapses_crlf_in_summary() -> None:
    """M4: ``\\r\\n`` in summary collapses uniformly with ``\\n``."""
    env = _completed_envelope(summary="line1\r\nline2")
    result = _render(env)
    # Newlines collapsed; separator preserved between sections only.
    assert "line1\r\nline2" not in result
    assert "line1" in result and "line2" in result
    # No raw \r remains.
    assert "\r" not in result


def test_render_completed_collapses_bare_cr_in_summary() -> None:
    """M4: bare ``\\r`` (legacy Mac line endings) collapses uniformly."""
    env = _completed_envelope(summary="line1\rline2")
    result = _render(env)
    assert "line1\rline2" not in result
    assert "\r" not in result
    assert "line1" in result and "line2" in result


# M5 — pr_branch / pr_url containing only newlines.


def test_render_completed_omits_pr_line_when_pr_branch_collapses_to_empty() -> None:
    """M5: ``pr_branch="\\n\\n\\n"`` collapses to whitespace → field treated as empty.

    With only ``pr_branch`` populated and it being newlines-only, after
    collapse there is no informative content. The renderer should NOT
    render a malformed ``"Branch:    "`` line.
    """
    env = _completed_envelope(pr_branch="\n\n\n")
    result = _render(env)
    # No PR / Branch line should appear (collapsed-to-empty).
    assert "Branch:" not in result
    assert "PR:" not in result
    assert "PR #" not in result


# M7 already lives in test_event_types.py.


# M11 covered by the per-character ``&`` invariant added inline above.


# M12 — wire-format v1.0.0 back-compat E2E through dispatcher.


def test_task_completed_v1_0_0_envelope_renders_through_dispatcher() -> None:
    """M12: a v1.0.0-shaped raw envelope dict deserializes + dispatches cleanly.

    Constructs an envelope with ``schema_version="1.0.0"`` and the
    minimal v1.0 payload shape (``{task_id, summary, pr_url}``), routes
    through ``_render(envelope)``, and asserts the typed renderer
    produces a sensible output (header + summary + PR line).
    """
    _ensure_task_created_registered()
    _ensure_completed_registered()
    rng = Random(312)
    clk = FrozenClock(mono_ns=16_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    # v1.0 payload shape — only the 3 original fields, no FR9 counters.
    payload = TaskCompletedPayload(
        task_id="t-00000000-0000-7000-8000-0000000000aa",
        summary="back-compat ok",
        pr_url="https://example.com/pr/1",
    )
    env = EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )
    result = _render(env)
    assert "✅ Task t-00000000-0000-7000-8000-0000000000aa complete." in result
    assert "back-compat ok" in result
    assert "PR: https://example.com/pr/1" in result


# L7 — _DELIVERABLE_EVENT_TYPES pre-existence (Story 3.9 L15).


def test_task_completed_already_in_deliverable_event_types_per_story_3_9() -> None:
    """L7: ``task.completed`` is in ``_DELIVERABLE_EVENT_TYPES`` independent of dispatcher.

    Story 3.9 L15 added ``task.completed`` to the allowlist; the
    Story 3.10 / 3.11 / 3.12 dispatcher invariant
    ``_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES`` becomes vacuous if the
    allowlist is ever auto-derived from the dispatcher. This test
    asserts the membership pre-exists.
    """
    assert "task.completed" in _DELIVERABLE_EVENT_TYPES
    # Independent of whether the dispatcher routes it.
    # (The Story 3.9 placeholder shape would also have this membership
    # without any renderer registration.)


# M6 strengthening lives in tests/integration/test_task_thread_binding.py.


# ---------------------------------------------------------------------------
# Story 3.13 — _render_self_recovered renderer + dispatcher tests (8 tests)
# ---------------------------------------------------------------------------


# Story 3.10 M8 / Story 3.11 H11 / Story 3.12 carry-forward: idempotent
# guard so repeat invocations of the test helper don't re-run the
# schema_registry.register side-effect on every helper call.
_SELF_RECOVERED_REGISTERED: bool = False


def _ensure_self_recovered_registered() -> None:
    """Register task.self_recovered 1.0.0 — idempotent (Story 3.11 H11 pattern)."""
    global _SELF_RECOVERED_REGISTERED
    if _SELF_RECOVERED_REGISTERED:
        return
    _reg("task.self_recovered", "1.0.0", TaskSelfRecoveredPayload)
    _SELF_RECOVERED_REGISTERED = True


def _self_recovered_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000004",
    recovered_at: datetime = datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
    events_replayed: int = 142,
    replay_duration_ms: int = 350,
    mono_ns: int = 17_000_000,
) -> EventEnvelope:
    """Build a task.self_recovered envelope (schema 1.0.0)."""
    _ensure_task_created_registered()
    _ensure_self_recovered_registered()

    rng = Random(313)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskSelfRecoveredPayload(
        task_id=task_id,
        recovered_at=recovered_at,
        events_replayed=events_replayed,
        replay_duration_ms=replay_duration_ms,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.self_recovered",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_self_recovered_minimal() -> None:
    """AC-4: populated payload → exact single-line message shape."""
    env = _self_recovered_envelope()
    result = _render(env)
    assert result == (
        "🛠️ Self-recovered from host restart at 2026-05-01T03:00:00+00:00. "
        "142 events replayed in 350 ms. "
        "Zero intervention required."
    )


def test_render_self_recovered_uses_isoformat_seconds_precision() -> None:
    """AC-4 / Story 3.12 M14 carry-forward: microseconds omitted via timespec='seconds'."""
    aware = datetime(2026, 5, 1, 3, 0, 0, 123456, tzinfo=UTC)
    env = _self_recovered_envelope(recovered_at=aware)
    result = _render(env)
    assert "2026-05-01T03:00:00+00:00" in result
    assert ".123456" not in result


@pytest.mark.parametrize(
    ("events_replayed", "expected_word"),
    [
        (0, "0 events"),
        (1, "1 event"),
        (2, "2 events"),
    ],
)
def test_render_self_recovered_pluralizes_events_correctly(
    events_replayed: int, expected_word: str
) -> None:
    """AC-4 / Story 3.12 L1 carry-forward: singular 'event' when N==1, plural otherwise."""
    env = _self_recovered_envelope(events_replayed=events_replayed)
    result = _render(env)
    assert expected_word in result


def test_render_self_recovered_handles_zero_duration() -> None:
    """AC-4 edge case: replay_duration_ms=0 renders '0 ms' (instant heartbeat restart)."""
    env = _self_recovered_envelope(events_replayed=0, replay_duration_ms=0)
    result = _render(env)
    assert "0 events replayed in 0 ms." in result


def test_render_self_recovered_payload_type_mismatch_logs_and_falls_back() -> None:
    """H9 + H10 carry-forward: WARN + placeholder when payload isn't a typed instance."""
    import structlog.testing

    rng = Random(313)
    clk = FrozenClock(mono_ns=18_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.0.0",
        type="task.self_recovered",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": "t-raw-dict-recovered"},  # type: ignore[arg-type]
        request_id=rid,
    )
    with structlog.testing.capture_logs() as captured:
        result = _render(env)

    # Placeholder shape returned.
    assert result == "Task t-raw-dict-recovered: task.self_recovered"
    # H9: structured warning emitted.
    assert any(
        rec.get("event") == "renderer.payload_type_mismatch"
        and rec.get("log_level") == "warning"
        and rec.get("expected") == "TaskSelfRecoveredPayload"
        and rec.get("actual") == "dict"
        for rec in captured
    )
    # Placeholder did NOT leak renderer-specific shape.
    assert "🛠️" not in result
    assert "Self-recovered" not in result


def test_render_self_recovered_emergency_clamp_unreachable_for_valid_inputs() -> None:
    """AC-6: maximum-sized valid payload is well under the cap.

    Story 3.11 H5 carry-forward: the defensive final-length self-clamp
    cannot fire for valid model-bound inputs, but assert it so a future
    template-text growth is caught.
    """
    env = _self_recovered_envelope(
        task_id="t" * 64,
        recovered_at=datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
        events_replayed=10**6,
        replay_duration_ms=10**9,
    )
    result = _render(env)
    assert len(result) <= _SELF_RECOVERED_MESSAGE_MAX_CHARS
    # Additional guard: worst-case is ~140 chars; assert well under 250.
    assert len(result) < 250


def test_render_dispatcher_routes_self_recovered_to_renderer() -> None:
    """AC-3 + AC-2: dispatcher identity check + allowlist membership.

    Story 3.11 M3 / M10 carry-forward: positive identity assertion on the
    dispatcher entry — confirms the new entry was actually added. Also
    asserts AC-2 allowlist membership.
    """
    # Identity check.
    assert _RENDERERS["task.self_recovered"] is _render_self_recovered
    # AC-2: task.self_recovered is in the deliverable allowlist.
    assert "task.self_recovered" in _DELIVERABLE_EVENT_TYPES


def test_renderers_subset_of_deliverable_event_types_after_3_13() -> None:
    """Story 3.10 M12 / 3.11 M10 invariant — re-verified after AC-2 grew the allowlist.

    AC-2 added ``task.self_recovered`` to ``_DELIVERABLE_EVENT_TYPES``; AC-3
    added ``task.self_recovered`` to ``_RENDERERS``. The subset invariant
    is preserved (renderer set grew by 1, allowlist set grew by 1).
    """
    assert set(_RENDERERS.keys()).issubset(_DELIVERABLE_EVENT_TYPES)


def test_render_self_recovered_singular_with_max_fields() -> None:
    """Boundary interaction: singular '1 event' form with max task_id + max duration.

    Covers the untested combination where ``events_replayed=1`` triggers the
    singular ``"event"`` branch while all other fields are at their model
    boundary maximums.
    """
    env = _self_recovered_envelope(
        task_id="t" * 64,
        recovered_at=datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
        events_replayed=1,
        replay_duration_ms=10**9,
    )
    result = _render(env)
    assert "1 event replayed in 1000000000 ms." in result
    assert len(result) < 250


def test_render_self_recovered_type_mismatch_collapses_newlines_in_task_id() -> None:
    """Story 3.12 H1 carry-forward: newlines in task_id are collapsed in fallback.

    When a ``model_construct`` bypass produces a raw-dict payload and the
    extracted ``task_id`` contains ``\\n``, the fallback must produce a
    single-line placeholder (no real newlines).
    """
    import structlog.testing

    rng = Random(313)
    clk = FrozenClock(mono_ns=19_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    env = EventEnvelope.model_construct(
        event_id=eid,
        schema_version="1.0.0",
        type="task.self_recovered",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"task_id": "t-aa\nbb\ncc"},  # type: ignore[arg-type]
        request_id=rid,
    )
    with structlog.testing.capture_logs():
        result = _render(env)

    # Newlines collapsed (not preserved as literal \n in output).
    assert "\n" not in result
    assert "t-aa bb cc" in result


# ---------------------------------------------------------------------------
# Renderer (Story 5.11 — plan-ready template, FR2)
# ---------------------------------------------------------------------------


_PLAN_READY_REGISTERED: bool = False


def _ensure_plan_ready_registered() -> None:
    """Register task.plan.ready 1.1.0 so EventEnvelope.create succeeds."""
    global _PLAN_READY_REGISTERED
    if _PLAN_READY_REGISTERED:
        return
    _reg("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
    _reg("task.plan.ready", "1.0.1", TaskPlanReadyPayload)
    _reg("task.plan.ready", "1.1.0", TaskPlanReadyPayload)
    _PLAN_READY_REGISTERED = True


def _plan_ready_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000010",
    plan_summary: str = "Plan summary text",
    steps: tuple[PlanStep, ...] = (),
    estimated_steps: int = 0,
    mono_ns: int = 10_000_000,
) -> EventEnvelope:
    """Build a task.plan.ready envelope (schema 1.1.0)."""
    _ensure_task_created_registered()
    _ensure_plan_ready_registered()

    rng = Random(511)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskPlanReadyPayload(
        task_id=task_id,
        plan_summary=plan_summary,
        plan=steps,
        estimated_steps=estimated_steps,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.1.0",
        type="task.plan.ready",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_plan_ready_basic() -> None:
    """AC-4: basic plan with 3 steps renders correctly."""
    steps = (
        PlanStep(step=1, description="Setup project"),
        PlanStep(step=2, description="Write tests"),
        PlanStep(step=3, description="Implement feature"),
    )
    env = _plan_ready_envelope(steps=steps, estimated_steps=3)
    result = _render_plan_ready(env)
    assert "Plan ready, 3 steps:" in result
    assert "1) Setup project" in result
    assert "2) Write tests" in result
    assert "3) Implement feature" in result


def test_render_plan_ready_empty_steps() -> None:
    """No steps -> header only, no step lines."""
    env = _plan_ready_envelope(steps=(), estimated_steps=0)
    result = _render_plan_ready(env)
    assert result == "Plan ready, 0 steps:"


def test_render_plan_ready_single_step() -> None:
    """Single step renders correctly."""
    steps = (PlanStep(step=1, description="Do the thing"),)
    env = _plan_ready_envelope(steps=steps, estimated_steps=1)
    result = _render_plan_ready(env)
    assert "Plan ready, 1 steps:" in result
    assert "1) Do the thing" in result


def test_render_plan_ready_html_escape() -> None:
    """Step descriptions are HTML-escaped."""
    steps = (PlanStep(step=1, description="<script>alert('xss')</script>"),)
    env = _plan_ready_envelope(steps=steps, estimated_steps=1)
    result = _render_plan_ready(env)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_render_plan_ready_newlines_collapsed() -> None:
    """Newlines in step descriptions are collapsed to spaces."""
    steps = (PlanStep(step=1, description="Line one\nLine two"),)
    env = _plan_ready_envelope(steps=steps, estimated_steps=1)
    result = _render_plan_ready(env)
    assert "Line one\nLine two" not in result
    assert "Line one Line two" in result


def test_render_plan_ready_step_truncation_overflow() -> None:
    """More than 20 steps triggers overflow indicator."""
    steps = tuple(PlanStep(step=i, description=f"Step {i}") for i in range(1, 25))
    env = _plan_ready_envelope(steps=steps, estimated_steps=24)
    result = _render_plan_ready(env)
    assert "and 4 more" in result
    # Should not show step 25 (index 24).
    assert "25)" not in result


def test_render_plan_ready_emergency_one_liner() -> None:
    """AC-5: extreme data forces section-drop ladder (Step 3: 4 steps + overflow)."""
    # 30 steps with max-length descriptions force the ladder past
    # Step 1 (20 visible → too long) and Step 2 (10 visible → too long),
    # landing at Step 3 (4 visible + overflow indicator).
    steps = tuple(PlanStep(step=i, description="Z" * 490) for i in range(1, 31))
    env = _plan_ready_envelope(steps=steps, estimated_steps=30)
    result = _render_plan_ready(env)
    # Step 3 produces header + 4 step lines + overflow.
    assert "Plan ready, 30 steps:" in result
    assert "… and 26 more" in result
    assert result.count(") ") == 4
    assert len(result) <= 1900


def test_render_plan_ready_payload_type_mismatch() -> None:
    """H9 carry-forward: wrong payload type -> placeholder + WARN."""
    # Build an envelope with a raw dict payload via model_construct to bypass
    # schema validation, so isinstance check fails inside the renderer.
    env = EventEnvelope.model_construct(
        event_id="e-test",
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=10_000_000,
        actor=_ACTOR,
        payload={"task_id": "t-test", "plan_summary": "s"},
        parent_event_id=None,
        trace_id=None,
        request_id="r-test",
        extensions=None,
    )
    import structlog.testing

    with structlog.testing.capture_logs() as captured:
        result = _render_plan_ready(env)
    # Falls back to placeholder shape.
    assert "Task t-test: task.plan.ready" in result
    # WARN log emitted for the type mismatch.
    warns = [e for e in captured if e["event"] == "renderer.payload_type_mismatch"]
    assert len(warns) == 1
    assert warns[0]["expected"] == "TaskPlanReadyPayload"


def test_render_plan_ready_dispatcher_routes() -> None:
    """task.plan.ready is registered in _RENDERERS and dispatched correctly."""
    assert "task.plan.ready" in _RENDERERS
    assert _RENDERERS["task.plan.ready"] is _render_plan_ready


def test_render_plan_ready_length_cap() -> None:
    """AC-5: output respects the 1900-char cap."""
    steps = tuple(PlanStep(step=i, description=f"Step {i}: " + "X" * 150) for i in range(1, 30))
    env = _plan_ready_envelope(steps=steps, estimated_steps=29)
    result = _render_plan_ready(env)
    assert len(result) <= 1900


def test_render_plan_ready_step2_ladder_truncation() -> None:
    """Section-drop Step 2: 15 steps with long descriptions truncate to 10."""
    # 150-char descriptions: Step 1 (15 visible) ≈ 2333 > 1900, Step 2 (10 visible) ≈ 1569 < 1900.
    steps = tuple(PlanStep(step=i, description="Y" * 150) for i in range(1, 16))
    env = _plan_ready_envelope(steps=steps, estimated_steps=15)
    result = _render_plan_ready(env)
    assert "Plan ready, 15 steps:" in result
    assert "… and 5 more" in result
    assert result.count(") ") == 10


def test_render_plan_ready_plan_summary_excluded() -> None:
    """plan_summary field is intentionally excluded from rendered output."""
    steps = (PlanStep(step=1, description="Do the thing"),)
    env = _plan_ready_envelope(
        plan_summary="A detailed summary that must NOT appear",
        steps=steps,
        estimated_steps=1,
    )
    result = _render_plan_ready(env)
    assert "A detailed summary that must NOT appear" not in result
    assert "Do the thing" in result


def test_render_plan_ready_v1_0_0_backward_compat() -> None:
    """v1.0.0 envelope (no plan/estimated_steps) renders through dispatcher."""
    _ensure_plan_ready_registered()
    rng = Random(511)
    clk = FrozenClock(mono_ns=10_000_000, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    # Construct with only v1.0.0 fields — plan and estimated_steps default.
    payload = TaskPlanReadyPayload(
        task_id="t-00000000-0000-7000-8000-000000000011",
        plan_summary="Some plan",
    )
    _ensure_task_created_registered()
    env = EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.plan.ready",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )
    result = _render(env)
    assert "Plan ready, 0 steps:" in result


# ---------------------------------------------------------------------------
# Story 5.12 — step-completed renderer tests
# ---------------------------------------------------------------------------

from events import TaskStepCompletedPayload  # noqa: E402

_STEP_COMPLETED_REGISTERED: bool = False


def _ensure_step_completed_registered() -> None:
    """Register task.step.completed 1.0.0 so EventEnvelope.create succeeds."""
    global _STEP_COMPLETED_REGISTERED
    if _STEP_COMPLETED_REGISTERED:
        return
    _reg("task.step.completed", "1.0.0", TaskStepCompletedPayload)
    _STEP_COMPLETED_REGISTERED = True


def _step_completed_envelope(
    *,
    task_id: str = "t-00000000-0000-7000-8000-000000000020",
    step: int = 1,
    description: str = "Run tests",
    output_summary: str = "All tests passed",
    mono_ns: int = 11_000_000,
) -> EventEnvelope:
    """Build a task.step.completed envelope (schema 1.0.0)."""
    _ensure_task_created_registered()
    _ensure_step_completed_registered()

    rng = Random(511)
    clk = FrozenClock(mono_ns=mono_ns, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = new_uuid7(clock=clk, rng=rng)
    payload = TaskStepCompletedPayload(
        task_id=task_id,
        step=step,
        description=description,
        output_summary=output_summary,
    )
    return EventEnvelope.create(
        event_id=eid,
        schema_version="1.0.0",
        type="task.step.completed",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        request_id=rid,
    )


def test_render_step_completed_basic() -> None:
    env = _step_completed_envelope(step=3, description="Write unit tests")
    result = _render(env)
    assert result == "Step 3 done: Write unit tests"


def test_render_step_completed_html_escape() -> None:
    env = _step_completed_envelope(description="Fix <bug> & ship")
    result = _render(env)
    assert "&lt;bug&gt;" in result
    assert "&amp;" in result


def test_render_step_completed_newlines_collapsed() -> None:
    env = _step_completed_envelope(description="Do X\nthen Y\r\nthen Z")
    result = _render(env)
    assert "\n" not in result
    assert "Do X" in result


def test_render_step_completed_description_truncation() -> None:
    long_desc = "A" * 500
    env = _step_completed_envelope(description=long_desc)
    result = _render(env)
    # Description capped at 200 chars before escape.
    assert len(result) < len(f"Step 1 done: {long_desc}")


def test_render_step_completed_payload_type_mismatch() -> None:
    """Wrong payload type → WARN logged, placeholder fallback."""
    # Use model_construct to bypass schema validation so isinstance check fails.
    env = EventEnvelope.model_construct(
        event_id="e-test",
        schema_version="1.0.0",
        type="task.step.completed",
        emitted_at=FROZEN_EPOCH,
        emitted_at_monotonic_ns=1_000_000,
        actor=_ACTOR,
        payload={"task_id": "t-mismatch", "step": 1, "description": "x", "output_summary": "y"},
        parent_event_id=None,
        trace_id=None,
        request_id="r-test",
        extensions=None,
    )
    result = _render(env)
    assert "Task" in result
    assert "task.step.completed" in result


def test_render_step_completed_dispatcher_routes() -> None:
    """task.step.completed is routed to _render_step_completed."""
    env = _step_completed_envelope(step=5, description="Deploy")
    result = _render(env)
    assert result == "Step 5 done: Deploy"


def test_render_step_completed_length_cap() -> None:
    """Even with max-length description, the result is under the cap."""
    env = _step_completed_envelope(description="B" * 500)
    result = _render(env)
    assert len(result) <= 1900
    # Renderer truncates description to 200 chars.
    assert len(result) < len(f"Step 1 done: {'B' * 500}")


def test_task_step_completed_in_deliverable_event_types() -> None:
    """task.step.completed is in the deliverable set."""
    assert "task.step.completed" in _DELIVERABLE_EVENT_TYPES
