"""POST /v1/tasks/{task_id}/decisions route handler (Story 6.4 / FR7).

Accepts operator decisions (approve/reject/stop/retry) and emits the
corresponding typed events to the JSONL event log.  Each action validates
the task's current state before emitting.

Idempotency follows the same pattern as POST /v1/tasks (Story 2.13):
the ``IdempotencyCacheStore`` deduplicates by ``(actor_id, idempotency_key)``
and the side-channel ``ResponseSlotCache`` preserves byte-identical replays.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Literal

from events import (
    ApprovalGrantedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    LicenseOverridePayload,
    TaskRetryRequestedPayload,
    TaskStopRequestedPayload,
)
from events.budget_policy import calculate_new_limit
from events.envelope import Actor, EventEnvelope
from events.ids import new_decision_id, new_event_id
from fastapi import APIRouter, Path, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from idempotency import IdempotencyCacheStore
from pydantic import BaseModel, ConfigDict, Field, model_validator
from registry_state.schema import Event, Task  # noqa: IMP001 — services→services allowed per AC-16
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_api.adapters.errors import ProblemDetails
from registry_api.lifecycle import ACTION_VALID_STATES
from registry_api.routes.tasks import ResponseSlot, ResponseSlotCache

log = logging.getLogger("registry_api.routes.decisions")

IdempotencyStatus = Literal["applied", "replayed"]

# UUIDv7 task-id pattern: t- prefix + standard UUIDv7 hex shape
_TASK_ID_PATTERN = r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

# State precondition rules (AC-3) — derived from lifecycle.canonical map.
_VALID_STATES = ACTION_VALID_STATES

_DEFAULT_TOKEN_LIMIT = 50_000

# Status code per action (AC-2): approve/reject → 202, stop/retry → 200.
_STATUS_CODE_BY_ACTION: dict[str, int] = {
    "approve": 202,
    "reject": 202,
    "stop": 200,
    "retry": 200,
}


# ---------------------------------------------------------------------------
# Pydantic request/response models (AC-1, AC-2)
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    """Request body for POST /v1/tasks/{task_id}/decisions (AC-1)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    action: Literal["approve", "reject", "stop", "retry"]
    reason: str | None = Field(default=None, max_length=4096)
    hint: str | None = Field(default=None, min_length=1, max_length=4096)
    override: Literal["license", "budget"] | None = None

    @model_validator(mode="after")
    def _override_only_on_approve(self) -> DecisionRequest:
        if self.override is not None and self.action != "approve":
            raise ValueError("override is only valid with action='approve'")
        return self


class DecisionResponse(BaseModel):
    """Response body for POST /v1/tasks/{task_id}/decisions (AC-2)."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    decision_id: str
    action: Literal["approve", "reject", "stop", "retry"]
    decided_at: datetime
    idempotency_status: IdempotencyStatus


# ---------------------------------------------------------------------------
# License gate helper (Story 6.10)
# ---------------------------------------------------------------------------


async def _check_license_gate(
    task_id: str,
    session_maker: async_sessionmaker[AsyncSession],
) -> bool:
    """Return True if a ``task.license_flagged`` event exists for this task.

    TOCTOU note: the event log is materialized asynchronously from JSONL by
    the subscriber/materializer pipeline.  A ``task.license_flagged`` event
    emitted moments earlier may not yet be present in the SQL table when this
    query runs.  This is an accepted risk — the precommit hook provides a
    synchronous first layer of defense, and the materialization lag is
    typically <1 s.  A future story could query the JSONL directly for
    zero-gap coverage.
    """
    if not isinstance(session_maker, async_sessionmaker):
        raise TypeError(
            f"session_maker must be an async_sessionmaker, got {type(session_maker).__name__}"
        )
    async with session_maker() as session:
        result = await session.execute(
            select(Event.id)
            .where(
                Event.task_id == task_id,
                Event.type == "task.license_flagged",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Budget gate helper (Story 6.11)
# ---------------------------------------------------------------------------


async def _check_budget_gate(
    task_id: str,
    session_maker: async_sessionmaker[AsyncSession],
) -> bool:
    """Return True if a ``task.budget_exceeded`` event exists for this task.

    TOCTOU note: same accepted risk as _check_license_gate.
    """
    if not isinstance(session_maker, async_sessionmaker):
        raise TypeError(
            f"session_maker must be an async_sessionmaker, got {type(session_maker).__name__}"
        )
    async with session_maker() as session:
        result = await session.execute(
            select(Event.id)
            .where(
                Event.task_id == task_id,
                Event.type == "task.budget_exceeded",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post(
    "/tasks/{task_id}/decisions",
    description=(
        "Accept an operator decision for a task. Valid actions: approve, reject, stop, retry. "
        "Each action validates the task's current state before emitting the corresponding event. "
        "Idempotency-Key dedup is enforced: duplicate submissions return the stored response "
        "with `idempotency_status: 'replayed'`."
    ),
)
async def post_decision(
    body: DecisionRequest,
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
) -> Response:
    app = request.app
    clock = app.state.clock
    writer = app.state.writer
    idempotency_cache: IdempotencyCacheStore = app.state.idempotency_cache
    response_body_cache: ResponseSlotCache = app.state.idempotency_response_cache

    request_id: str = request.state.request_id
    trace_id: str = request.state.trace_id
    # Phase 1: actor_id hardcoded by middleware. TODO(Story 6.1+): enforce
    # ownership/role before allowing decision on task.
    actor_id: str = getattr(request.state, "actor_id", "http-api")
    idempotency_key: str = request.state.idempotency_key

    # AC-3: validate task exists and state preconditions.
    session_maker = app.state.session_maker
    async with session_maker() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    current_status = task.status
    valid_states = _VALID_STATES.get(body.action, set())
    if current_status not in valid_states:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Action '{body.action}' not allowed on task in "
                f"status '{current_status}'; requires one of {sorted(valid_states)}"
            ),
        )

    # Story 6.10 AC-1: license gate — block default approve when flagged.
    if body.action == "approve" and body.override != "license":
        license_blocked = await _check_license_gate(task_id, session_maker)
        if license_blocked:
            problem = ProblemDetails(
                type="approval_blocked_by",
                title="Approval Blocked",
                status=409,
                detail="License flag active. Use --override license to proceed.",
                instance=str(request.url),
                extensions={"reason": "license_flag"},
            )
            return JSONResponse(
                content=problem.model_dump(exclude_none=True),
                status_code=409,
                media_type="application/problem+json",
            )

    # Story 6.11 AC-2: budget gate — block default approve when budget exceeded.
    if body.action == "approve" and body.override != "budget":
        budget_blocked = await _check_budget_gate(task_id, session_maker)
        if budget_blocked:
            problem = ProblemDetails(
                type="approval_blocked_by",
                title="Approval Blocked",
                status=409,
                detail="Budget exceeded. Use --override budget to extend and resume.",
                instance=str(request.url),
                extensions={"reason": "budget_exceeded"},
            )
            return JSONResponse(
                content=problem.model_dump(exclude_none=True),
                status_code=409,
                media_type="application/problem+json",
            )

    # AC-6: scoped cache key.
    cache_key = (actor_id, idempotency_key)
    factory_called: bool = False
    captured: dict[str, str] = {}

    async def _factory() -> str:
        nonlocal factory_called
        factory_called = True

        decision_id = new_decision_id(clock=clock)
        event_id = new_event_id(clock=clock)
        actor = Actor(kind="operator", id=actor_id)
        decided_at = clock.now()

        # AC-4: emit the correct event type per action.
        event_type, payload = _build_event(body, task_id, decision_id, actor_id)

        envelope = EventEnvelope.create(
            event_id=event_id,
            type=event_type,
            schema_version="1.0.0",
            emitted_at=decided_at,
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=actor,
            payload=payload,
            request_id=request_id,
            trace_id=trace_id,
            parent_event_id=None,
        )
        await writer.append(envelope)

        # AC-8: license override branch — emit second audit event.
        # Accepted risk: two sequential writer.append calls with no
        # transactional boundary.  If the first succeeds but the second
        # fails (e.g. I/O error), the approval is recorded without the
        # required license-override audit event.  A future story should
        # introduce a batch/atomic append on the writer, or a
        # compensating reconciliation mechanism.
        if body.action == "approve" and body.override == "license":
            override_event_id = new_event_id(clock=clock)
            override_payload = LicenseOverridePayload(
                task_id=task_id,
                decision_id=decision_id,
                actor_id=actor_id,
                reason="operator_license_override",
            )
            override_envelope = EventEnvelope.create(
                event_id=override_event_id,
                type="tier3.license_override",
                schema_version="1.0.0",
                emitted_at=decided_at,
                emitted_at_monotonic_ns=clock.monotonic_ns(),
                actor=actor,
                payload=override_payload,
                request_id=request_id,
                trace_id=trace_id,
                parent_event_id=event_id,
            )
            await writer.append(override_envelope)

        # Story 6.11 AC-2: budget override branch — emit audit event.
        # Same accepted risk as license override (sequential appends).
        if body.action == "approve" and body.override == "budget":
            override_event_id = new_event_id(clock=clock)
            async with session_maker() as q_session:
                be_result = await q_session.execute(
                    select(Event.payload_json)
                    .where(
                        Event.task_id == task_id,
                        Event.type == "task.budget_exceeded",
                    )
                    .order_by(desc(Event.emitted_at_monotonic_ns))
                    .limit(1)
                )
                be_row = be_result.scalar_one_or_none()
            if be_row is not None:
                try:
                    be_data = json.loads(be_row)
                    old_limit = be_data.get("token_limit", _DEFAULT_TOKEN_LIMIT)
                except (json.JSONDecodeError, TypeError):
                    log.warning(
                        "Malformed payload_json for budget_exceeded event, "
                        "using default limit (task_id=%s)",
                        task_id,
                    )
                    old_limit = _DEFAULT_TOKEN_LIMIT
            else:
                old_limit = _DEFAULT_TOKEN_LIMIT
            new_limit = calculate_new_limit(old_limit)
            budget_override_payload = BudgetOverridePayload(
                task_id=task_id,
                decision_id=decision_id,
                actor_id=actor_id,
                old_limit=old_limit,
                new_limit=new_limit,
            )
            budget_override_envelope = EventEnvelope.create(
                event_id=override_event_id,
                type="tier3.budget_override",
                schema_version="1.0.0",
                emitted_at=decided_at,
                emitted_at_monotonic_ns=clock.monotonic_ns(),
                actor=actor,
                payload=budget_override_payload,
                request_id=request_id,
                trace_id=trace_id,
                parent_event_id=event_id,
            )
            await writer.append(budget_override_envelope)

        # Build and cache the response.
        status_code = _STATUS_CODE_BY_ACTION[body.action]
        response_model = DecisionResponse(
            task_id=task_id,
            decision_id=decision_id,
            action=body.action,
            decided_at=decided_at,
            idempotency_status="applied",
        )
        body_bytes = response_model.model_dump_json().encode("utf-8")

        response_body_cache[cache_key] = ResponseSlot(
            body=body_bytes,
            task_id=task_id.encode("utf-8"),
        )
        captured["decision_id"] = decision_id
        captured["status_code"] = str(status_code)
        return event_id

    cache_hit, was_run = await idempotency_cache.get_or_run(
        f"{actor_id}:{idempotency_key}",
        request_id=request_id,
        factory=_factory,
    )

    if was_run:
        if not factory_called:
            raise RuntimeError("get_or_run reported was_run=True but factory_called is False")
        slot = response_body_cache[cache_key]
        body_bytes = slot.body
        status_value: IdempotencyStatus = "applied"
        status_code = int(captured["status_code"])
    else:
        # Cache hit — use side-channel slot if present.
        slot_or_none = response_body_cache.get(cache_key)
        if slot_or_none is None:
            # Post-restart fallback: rebuild minimal response.
            fallback = DecisionResponse(
                task_id=task_id,
                decision_id="",
                action=body.action,
                decided_at=cache_hit.created_at,
                idempotency_status="replayed",
            )
            body_bytes = fallback.model_dump_json().encode("utf-8")
            status_code = _STATUS_CODE_BY_ACTION.get(body.action, 202)
        else:
            # Rebuild the response with idempotency_status="replayed" instead
            # of returning the stored bytes verbatim (which have "applied").
            stored = json.loads(slot_or_none.body)
            status_code = _STATUS_CODE_BY_ACTION.get(stored.get("action", ""), 202)
            replay_body = DecisionResponse(
                task_id=stored["task_id"],
                decision_id=stored["decision_id"],
                action=stored["action"],
                decided_at=stored["decided_at"],
                idempotency_status="replayed",
            )
            body_bytes = replay_body.model_dump_json().encode("utf-8")
        status_value = "replayed"

    headers: dict[str, str] = {"X-Idempotency-Status": status_value}

    return Response(
        content=body_bytes,
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


_DecisionPayload = (
    ApprovalGrantedPayload
    | ApprovalRejectedPayload
    | TaskStopRequestedPayload
    | TaskRetryRequestedPayload
)


def _build_event(
    body: DecisionRequest,
    task_id: str,
    decision_id: str,
    actor_id: str,
) -> tuple[str, _DecisionPayload]:
    """Return ``(event_type, payload_model)`` for the given action."""
    if body.action == "approve":
        return (
            "approval.granted",
            ApprovalGrantedPayload(
                task_id=task_id,
                decision_id=decision_id,
                actor_id=actor_id,
                override=body.override,
            ),
        )
    if body.action == "reject":
        return (
            "approval.rejected",
            ApprovalRejectedPayload(
                task_id=task_id,
                decision_id=decision_id,
                actor_id=actor_id,
                reason=body.reason,
            ),
        )
    if body.action == "stop":
        return (
            "task.stop_requested",
            TaskStopRequestedPayload(
                task_id=task_id,
                actor_id=actor_id,
            ),
        )
    # retry
    return (
        "task.retry_requested",
        TaskRetryRequestedPayload(
            task_id=task_id,
            decision_id=decision_id,
            actor_id=actor_id,
            hint=body.hint,
        ),
    )


__all__ = [
    "DecisionRequest",
    "DecisionResponse",
    "router",
]
