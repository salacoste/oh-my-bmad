"""POST /v1/tasks + GET /v1/tasks/{task_id} route handlers (Story 2.9 AC-2, AC-3).

Pydantic models:
  - ``CreateTaskRequest``:  request body for POST /v1/tasks.
  - ``CreateTaskResponse``: 201 response body for POST /v1/tasks.
  - ``ActorOut``:           nested actor shape in TaskResponse.
  - ``LastEventOut``:       nested last-event shape in TaskResponse.
  - ``TaskResponse``:       200 response body for GET /v1/tasks/{task_id}.

Behavior:
  POST: generate IDs, emit task.created envelope via EventLogWriter, return 201.
  GET:  query read-only SQLite via app.state.engine, return 200 or 404.

Actor identity is hardcoded ``("operator", "http-api")`` for Phase 1.
Real auth replaces this in Story 6.1+.

``next_commands`` lookup: minimal Phase 1 table. Full lifecycle logic lands
in Stories 5.x (worker lifecycle) and 6.x (approval gate).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

import cachetools
from events import TaskCreatedPayload
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_task_id, new_uuid7
from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.exceptions import HTTPException
from idempotency import IdempotencyCacheStore
from pydantic import BaseModel, ConfigDict, Field, field_validator
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Event,
    Session,
    Task,
)
from sqlalchemy import select

from registry_api.lifecycle import STATE_NEXT_COMMANDS

log = logging.getLogger("registry_api.routes.tasks")

# Mn2: Literal type for X-Idempotency-Status — keeps the OpenAPI ``enum``
# constant in sync with the runtime header value at type-check time.
IdempotencyStatus = Literal["applied", "replayed"]
TaskStatusFilter = Literal[
    "pending",
    "planning",
    "plan_ready",
    "executing",
    "blocked",
    "completed",
    "stopped",
    "failed",
]


# ---------------------------------------------------------------------------
# Side-channel response cache types (Story 2.13 review C1/C3/M6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseSlot:
    """Cached response payload for a single idempotency key (Story 2.13 C1/M6).

    A frozen dataclass replaces the prior pair of side-channel keys
    (``key`` and ``key + ":task_id"``) — the suffix scheme allowed an
    attacker submitting ``Idempotency-Key: foo:task_id`` to collide
    with the companion entry for key ``foo``. Storing both fields under
    a single key removes that collision vector entirely.

    Attributes:
        body:    Canonical JSON body bytes returned to all replay callers.
                 Byte-identity is the FR28 / NFR-R4 invariant.
        task_id: ASCII task_id (``t-<uuidv7>``) used to build the
                 ``Location`` header. Empty bytes is RESERVED for the
                 post-restart fallback path where the side-channel was
                 lost; route handler treats empty as "omit Location".
    """

    body: bytes
    task_id: bytes


# Type alias for the side-channel cache. Kept here (not in app.py) to avoid
# a circular import (app.py imports the tasks router; the router needs the
# slot type for its handler-internal annotations).
ResponseSlotCache = cachetools.TTLCache[tuple[str, str], ResponseSlot]

# UUIDv7 task-id pattern: t- prefix + standard UUIDv7 hex shape
_TASK_ID_PATTERN = r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

# Story 109.2: fixed first page for GET /v1/tasks. Story 113.2 adds only
# one finite status selector. Story 115.2 composes exactly status then limit
# in canonical query order. Story 117.2 adds exactly canonical limit then
# offset pagination. Story 120.2 adds only canonical status then limit then
# offset API-local composition, without browser traversal.
_TASK_LIST_LIMIT = 50
_TASK_LIST_OFFSET_MAX = 2_147_483_647
_TASK_STATUS_FILTER_ROUTE: Literal["GET /v1/tasks?status={task_status}"] = (
    "GET /v1/tasks?status={task_status}"
)
_TASK_LIMIT_ROUTE: Literal["GET /v1/tasks?limit={task_list_limit}"] = (
    "GET /v1/tasks?limit={task_list_limit}"
)
_TASK_STATUS_LIMIT_ROUTE: Literal["GET /v1/tasks?status={task_status}&limit={task_list_limit}"] = (
    "GET /v1/tasks?status={task_status}&limit={task_list_limit}"
)
_TASK_LIMIT_OFFSET_ROUTE: Literal[
    "GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}"
] = "GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}"
_TASK_STATUS_LIMIT_OFFSET_ROUTE: Literal[
    "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}"
] = "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}"
_TASK_STATUS_FILTER_VALUES: tuple[TaskStatusFilter, ...] = (
    "pending",
    "planning",
    "plan_ready",
    "executing",
    "blocked",
    "completed",
    "stopped",
    "failed",
)
_ALLOWED_TASK_STATUS_FILTERS: frozenset[TaskStatusFilter] = frozenset(_TASK_STATUS_FILTER_VALUES)


def _is_ascii_decimal(value: str) -> bool:
    """Return True only for non-empty ASCII decimal digits.

    ``str.isdecimal()`` accepts Unicode decimal digits such as fullwidth forms;
    the task-list limit selector is intentionally pinned to canonical ASCII
    query spelling.
    """
    return bool(value) and all("0" <= char <= "9" for char in value)


_TASKS_STATUS_RAW_RE = re.compile(rb"^status=([A-Za-z_]+)$")
_TASKS_LIMIT_RAW_RE = re.compile(rb"^limit=([0-9]{1,2})$")
_TASKS_STATUS_LIMIT_RAW_RE = re.compile(rb"^status=([A-Za-z_]+)&limit=([0-9]{1,2})$")
_TASKS_LIMIT_OFFSET_RAW_RE = re.compile(rb"^limit=([0-9]{1,2})&offset=([0-9]{1,10})$")
_TASKS_STATUS_LIMIT_OFFSET_RAW_RE = re.compile(
    rb"^status=([A-Za-z_]+)&limit=([0-9]{1,2})&offset=([0-9]{1,10})$"
)


def _has_empty_query_segment(raw_query: bytes) -> bool:
    """Reject empty raw query segments before Starlette normalizes them away."""
    if not raw_query:
        return False
    segments = raw_query.split(b"&")
    return any(segment == b"" for segment in segments)


def _matches_tasks_raw_query_contract(raw_query: bytes) -> bool:
    """Return True only for exact ASCII raw spellings approved for GET /v1/tasks.

    This byte-level gate is the intentional route contract: each approved
    selector composition must be explicitly registered here and then handled by
    the query-key branch below. It prevents framework normalization from
    broadening spelling, order, encoded-key, repeated-key, or empty-segment
    behavior as new bounded selector combinations are added.
    """
    return bool(
        _TASKS_STATUS_RAW_RE.fullmatch(raw_query)
        or _TASKS_LIMIT_RAW_RE.fullmatch(raw_query)
        or _TASKS_STATUS_LIMIT_RAW_RE.fullmatch(raw_query)
        or _TASKS_LIMIT_OFFSET_RAW_RE.fullmatch(raw_query)
        or _TASKS_STATUS_LIMIT_OFFSET_RAW_RE.fullmatch(raw_query)
    )


def _parse_task_status_selector(value: str | None) -> TaskStatusFilter:
    """Return an approved task status selector or fail closed."""
    if value not in _ALLOWED_TASK_STATUS_FILTERS:
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks status selector is not allowed",
        )
    return value


def _parse_task_limit_selector(value: str | None) -> int:
    """Return an approved task-list limit selector or fail closed."""
    if value is None or not _is_ascii_decimal(value):
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks limit selector must be an integer from 1 through 50",
        )
    selected_limit = int(value)
    if not 1 <= selected_limit <= _TASK_LIST_LIMIT:
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks limit selector must be an integer from 1 through 50",
        )
    return selected_limit


def _parse_task_offset_selector(value: str | None) -> int:
    """Return an approved task-list offset selector or fail closed."""
    if value is None or not _is_ascii_decimal(value):
        raise HTTPException(
            status_code=400,
            detail=("GET /v1/tasks offset selector must be an integer from 0 through 2147483647"),
        )
    selected_offset = int(value)
    if not 0 <= selected_offset <= _TASK_LIST_OFFSET_MAX:
        raise HTTPException(
            status_code=400,
            detail=("GET /v1/tasks offset selector must be an integer from 0 through 2147483647"),
        )
    return selected_offset


def _task_list_pagination_metadata(
    *, fetched_count: int, effective_limit: int, selected_offset: int
) -> tuple[bool, int | None]:
    """Return bounded has_more/next_offset metadata for Story 117.2.

    ``has_more`` means another page is reachable inside the approved API
    boundary, not that arbitrary database rows exist beyond the story-approved
    maximum offset. This keeps emitted ``next_offset`` values closed under the
    accepted selector domain.
    """
    if fetched_count <= effective_limit:
        return False, None
    candidate_next_offset = selected_offset + effective_limit
    if candidate_next_offset > _TASK_LIST_OFFSET_MAX:
        return False, None
    return True, candidate_next_offset


# Story 110.2: fixed first page for GET /v1/sessions. Selectors/pagination
# knobs are intentionally absent until a later story defines a separate
# contract.
_SESSION_LIST_LIMIT = 50

# Phase 1 next-commands lookup — derived from lifecycle.canonical map.
_NEXT_COMMANDS = STATE_NEXT_COMMANDS


def _next_commands_for(status: str) -> list[str]:
    """Return available commands for *status* per the Phase 1 lookup table.

    Unknown statuses produce an empty list and a warning log so an unexpected
    status string surfaces in operator dashboards instead of silently
    suppressing the workflow advance.
    """
    if status not in _NEXT_COMMANDS:
        log.warning(
            "unknown task status; returning empty next_commands",
            extra={"status": status},
        )
    return list(_NEXT_COMMANDS.get(status, []))


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """Request body for POST /v1/tasks.

    ``extra="forbid"`` rejects unknown fields with 422 (Pydantic v2 default
    maps to RequestValidationError → 422 via our handler).
    ``strict=True`` prevents silent type coercion (e.g. int title → str).
    ``frozen=True`` prevents mutation after construction.

    F7: ``title`` is bounded ``[1, 512]`` chars — empty titles are rejected
    at the API boundary (422) instead of producing meaningless task rows.

    Story 3.9 AC-3: ``chat_id`` + ``reply_to_message_id`` are optional
    Telegram-thread-binding fields (FR13). Both ``int | None`` — Telegram
    supergroup chat ids are negative so ``PositiveInt`` is wrong. ``strict=True``
    rejects string-coerced ints from JSON, matching the rest of the body.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    repo: str | None = Field(default=None, max_length=2048)
    hint: str | None = Field(default=None, min_length=1, max_length=4096)
    # Story 3.9: Telegram thread binding (FR13).
    # M13: chat_id=0 is rejected (Telegram never uses 0; returns 400 chat not found).
    # L20: explicit BigInteger bounds guard against attacker-supplied oversized ints.
    chat_id: int | None = Field(default=None, ge=-(2**63), le=(2**63) - 1)
    # M13: reply_to_message_id must be strictly positive (Telegram message IDs ≥ 1).
    reply_to_message_id: int | None = Field(default=None, gt=0)
    # Story 12.4: per-task budget policy (FR68a). Both optional — omitting them
    # means "inherit the .env default" (OMB_DEFAULT_TASK_BUDGET_*). budget_action
    # is stored + surfaced but its worker-wrapper consumption is deferred to
    # Story 12.3a (per-task delivery + awaiting_approval FSM).
    budget_token_limit: int | None = Field(default=None, gt=0)
    budget_action: Literal["failed", "awaiting_approval"] | None = None

    @field_validator("chat_id")
    @classmethod
    def _chat_id_not_zero(cls, v: int | None) -> int | None:
        if v == 0:
            raise ValueError("chat_id must not be 0 — Telegram never uses chat_id=0")
        return v


class CreateTaskResponse(BaseModel):
    """201 Created response body for POST /v1/tasks."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    event_id: str
    created_at: datetime


class ActorOut(BaseModel):
    """Nested actor shape in TaskResponse."""

    model_config = ConfigDict(frozen=True)

    kind: str
    id: str


class LastEventOut(BaseModel):
    """Nested last-event shape in TaskResponse."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: str
    emitted_at: datetime
    summary: str | None = None


class WorktreeLockOut(BaseModel):
    """Nested worktree-lock state in TaskResponse (Story 7.1 / FR4)."""

    model_config = ConfigDict(frozen=True)

    held: bool
    by_session_id: str | None = None
    acquired_at: datetime | None = None


class TaskResponse(BaseModel):
    """200 OK response body for GET /v1/tasks/{task_id}.

    Story 7.1: enriched with ``state_since``, ``current_step``, ``total_steps``,
    ``last_agent_action``, ``worktree_lock``, and ``available_commands`` to
    support full state reconstitution in a single response (FR4).
    """

    model_config = ConfigDict(frozen=True, strict=True)

    task_id: str
    status: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    state_since: datetime
    actor: ActorOut
    last_event: LastEventOut | None
    current_step: int | None = None
    total_steps: int | None = None
    last_agent_action: str | None = None
    worktree_lock: WorktreeLockOut
    available_commands: list[str]
    next_commands: list[str]  # deprecated — use available_commands instead
    # Story 3.9: Telegram thread binding (FR13).
    chat_id: int | None = Field(default=None, ge=-(2**63), le=(2**63) - 1)
    reply_to_message_id: int | None = Field(default=None, gt=0)
    hint: str | None = None
    # Story 12.4: per-task budget policy (FR68a). Surfaces the effective stored
    # values; NULL = "inherit the .env default".
    budget_token_limit: int | None = Field(default=None, gt=0)
    budget_action: Literal["failed", "awaiting_approval"] | None = None

    @field_validator("chat_id")
    @classmethod
    def _chat_id_not_zero(cls, v: int | None) -> int | None:
        if v == 0:
            raise ValueError("chat_id must not be 0")
        return v


class TaskListLastEventOut(BaseModel):
    """Bounded last-event shape for GET /v1/tasks summary rows.

    Story 109.2 intentionally omits payload, summary, request_id,
    parent_event_id, session_id, and route/action links from the aggregate list.
    Operators can request a task-detail or event-timeline route explicitly when
    they need those richer fields.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    id: str
    type: str
    emitted_at: datetime
    trace_id: str | None = None


class TaskSummaryOut(BaseModel):
    """One bounded task summary row for GET /v1/tasks."""

    model_config = ConfigDict(frozen=True, strict=True)

    task_id: str
    status: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    state_since: datetime
    actor: ActorOut
    last_event: TaskListLastEventOut | None


class TaskListResponse(BaseModel):
    """200 OK response body for unfiltered GET /v1/tasks.

    This is a fixed, selector-free, first-page aggregate boundary. It carries
    route/source/freshness/provenance metadata and only bounded summary rows.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/tasks"]
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int
    returned_count: int
    has_more: bool
    next_offset: None = None
    items: list[TaskSummaryOut]


class TaskStatusFilteredListResponse(BaseModel):
    """200 OK response body for GET /v1/tasks?status={task_status}.

    Story 113.2 permits exactly one finite lifecycle status selector and no
    other query/body selector, pagination, sorting, search, traversal, or
    mutation affordance. Row shape remains identical to the aggregate list.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/tasks?status={task_status}"]
    selected_status: TaskStatusFilter
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int
    returned_count: int
    has_more: bool
    next_offset: None = None
    items: list[TaskSummaryOut]


class TaskLimitSelectedListResponse(BaseModel):
    """200 OK response body for GET /v1/tasks?limit={task_list_limit}.

    Story 114.2 permits exactly one bounded first-page row-count selector and
    no status composition, pagination traversal, sorting, search, hidden
    selector, adjacent route traversal, or mutation affordance. Row shape and
    ordering remain identical to the aggregate list.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/tasks?limit={task_list_limit}"]
    selected_limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    returned_count: int
    has_more: bool
    next_offset: None = None
    items: list[TaskSummaryOut]


class TaskStatusLimitSelectedListResponse(BaseModel):
    """200 OK response body for canonical GET /v1/tasks?status=...&limit=....

    Story 115.2 composes exactly the approved finite status selector and the
    approved bounded first-page limit selector. Only canonical ``status`` then
    ``limit`` query order is accepted; no other selector composition, query
    order, traversal, sorting, search, adjacent route, or mutation affordance
    is introduced.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/tasks?status={task_status}&limit={task_list_limit}"]
    selected_status: TaskStatusFilter
    selected_limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    returned_count: int
    has_more: bool
    next_offset: None = None
    items: list[TaskSummaryOut]


class TaskLimitOffsetSelectedListResponse(BaseModel):
    """200 OK response body for canonical GET /v1/tasks?limit=...&offset=....

    Story 117.2 permits exactly the approved bounded limit selector followed
    by the approved bounded offset selector. The route remains API-local:
    no status composition, browser pagination controls, sorting, search,
    hidden discovery, adjacent traversal, or mutation affordance is introduced.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}"]
    selected_limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    selected_offset: int = Field(ge=0, le=_TASK_LIST_OFFSET_MAX)
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    returned_count: int
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, le=_TASK_LIST_OFFSET_MAX)
    items: list[TaskSummaryOut]


class TaskStatusLimitOffsetSelectedListResponse(BaseModel):
    """200 OK response body for canonical GET /v1/tasks?status=...&limit=...&offset=....

    Story 120.2 composes exactly the approved finite status selector, bounded
    limit selector, and bounded offset selector in canonical order. The route
    remains API-local: no dashboard/browser consumption, status+offset without
    limit, sorting, search, hidden discovery, adjacent traversal, or mutation
    affordance is introduced.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal[
        "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}"
    ]
    selected_status: TaskStatusFilter
    selected_limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    selected_offset: int = Field(ge=0, le=_TASK_LIST_OFFSET_MAX)
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state task summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int = Field(ge=1, le=_TASK_LIST_LIMIT)
    returned_count: int
    has_more: bool
    next_offset: int | None = Field(default=None, ge=0, le=_TASK_LIST_OFFSET_MAX)
    items: list[TaskSummaryOut]


class SessionSummaryOut(BaseModel):
    """One bounded session summary row for GET /v1/sessions.

    Story 110.2 intentionally omits raw ``worktree_path`` and every adjacent
    traversal/control hint. ``session_id`` and ``task_id`` are display text
    only; no links/selectors are returned by the API.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    session_id: str
    task_id: str
    worker_kind: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    last_heartbeat_at: datetime | None
    heartbeat_state: Literal["ended", "observed", "missing"]


class SessionListResponse(BaseModel):
    """200 OK response body for GET /v1/sessions."""

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/sessions"]
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy", "empty-list"]
    authority_state: Literal["authoritative", "non-authoritative"]
    provenance: Literal["registry-state session summary list"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    limit: int
    returned_count: int
    has_more: bool
    next_offset: None = None
    sort: Literal["last_heartbeat_at_desc_nulls_last_started_at_desc_id_asc"]
    items: list[SessionSummaryOut]


class SessionDetailResponse(BaseModel):
    """200 OK response body for GET /v1/sessions/{session_id}."""

    model_config = ConfigDict(frozen=True, strict=True)

    route: Literal["GET /v1/sessions/{session_id}"]
    selected_session_id: str
    retrieved_at: datetime
    freshness_state: Literal["fresh"]
    display_state: Literal["healthy"]
    authority_state: Literal["authoritative"]
    provenance: Literal["registry-state session detail"]
    request_id: str
    trace_id: str | None
    correlation_id: str
    item: SessionSummaryOut


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


TaskListReadResponse = (
    TaskListResponse
    | TaskStatusFilteredListResponse
    | TaskLimitSelectedListResponse
    | TaskStatusLimitSelectedListResponse
    | TaskLimitOffsetSelectedListResponse
    | TaskStatusLimitOffsetSelectedListResponse
)


@router.get(
    "/tasks",
    status_code=200,
    response_model=TaskListReadResponse,
)
async def get_tasks(
    request: Request,
    status: Annotated[
        str | None,
        Query(
            description=(
                "Optional Story 113.2 task lifecycle status filter. Only one "
                "status query key is accepted; extra or repeated query keys fail closed."
            ),
            json_schema_extra={"enum": list(_TASK_STATUS_FILTER_VALUES)},
        ),
    ] = None,
    limit: Annotated[
        str | None,
        Query(
            description=(
                "Optional Story 114.2 bounded first-page row limit. Only one limit "
                "query key with an integer value from 1 through 50 is accepted."
            ),
            json_schema_extra={
                "anyOf": [
                    {
                        "type": "integer",
                        "maximum": _TASK_LIST_LIMIT,
                        "minimum": 1,
                    },
                    {"type": "null"},
                ],
            },
        ),
    ] = None,
    offset: Annotated[
        str | None,
        Query(
            description=(
                "Optional Story 117.2 bounded pagination offset. Only canonical "
                "limit then offset query order is accepted; the raw offset value "
                "must be an ASCII integer from 0 through 2147483647."
            ),
            json_schema_extra={
                "anyOf": [
                    {
                        "type": "integer",
                        "maximum": _TASK_LIST_OFFSET_MAX,
                        "minimum": 0,
                    },
                    {"type": "null"},
                ],
            },
        ),
    ] = None,
) -> (
    TaskListResponse
    | TaskStatusFilteredListResponse
    | TaskLimitSelectedListResponse
    | TaskStatusLimitSelectedListResponse
    | TaskLimitOffsetSelectedListResponse
    | TaskStatusLimitOffsetSelectedListResponse
):
    """GET /v1/tasks — bounded aggregate task summary list.

    Story 109.2 keeps the selector-free first page. Story 113.2 adds exactly
    one route-local status selector: ``GET /v1/tasks?status={task_status}``,
    where status is one finite lifecycle value. Story 114.2 adds exactly one
    route-local limit selector: ``GET /v1/tasks?limit={task_list_limit}``,
    where limit is an integer from 1 through 50. Story 115.2 adds only the
    canonical-order composition ``GET /v1/tasks?status=...&limit=...``.
    Story 117.2 adds only canonical ``GET /v1/tasks?limit=...&offset=...``.
    Story 120.2 adds only canonical ``GET /v1/tasks?status=...&limit=...&offset=...``.
    Repeated keys, GET body, reversed query order, hidden pagination token,
    status+offset without limit, task-detail/event/session/digest traversal,
    search, sorting, or mutation control are not accepted.
    """
    if await request.body():
        raise HTTPException(
            status_code=400,
            detail="GET /v1/tasks does not accept a request body",
        )

    selected_status: TaskStatusFilter | None = None
    selected_limit: int | None = None
    selected_offset: int | None = None
    effective_limit = _TASK_LIST_LIMIT
    if request.url.query:
        raw_query = request.scope.get("query_string", b"")
        if (
            not isinstance(raw_query, bytes)
            or _has_empty_query_segment(raw_query)
            or not _matches_tasks_raw_query_contract(raw_query)
        ):
            raise HTTPException(
                status_code=400,
                detail="GET /v1/tasks query selectors must use exact canonical ASCII spelling",
            )
        query_pairs = list(request.query_params.multi_items())
        query_keys = [key for key, _value in query_pairs]
        if query_keys == ["status"]:
            selected_status = _parse_task_status_selector(status)
        elif query_keys == ["limit"]:
            selected_limit = _parse_task_limit_selector(limit)
            effective_limit = selected_limit
        elif query_keys == ["status", "limit"]:
            selected_status = _parse_task_status_selector(status)
            selected_limit = _parse_task_limit_selector(limit)
            effective_limit = selected_limit
        elif query_keys == ["status", "limit", "offset"]:
            selected_status = _parse_task_status_selector(status)
            selected_limit = _parse_task_limit_selector(limit)
            selected_offset = _parse_task_offset_selector(offset)
            effective_limit = selected_limit
        elif query_keys == ["limit", "offset"]:
            selected_limit = _parse_task_limit_selector(limit)
            selected_offset = _parse_task_offset_selector(offset)
            effective_limit = selected_limit
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "GET /v1/tasks accepts only one status selector, one limit selector, "
                    "canonical status then limit selectors, canonical limit then "
                    "offset selectors, or canonical status then limit then offset selectors"
                ),
            )

    session_maker = request.app.state.session_maker
    clock = request.app.state.clock
    request_id: str = request.state.request_id
    trace_id: str | None = getattr(request.state, "trace_id", None)

    async with session_maker() as session:
        task_query = select(Task).order_by(Task.updated_at.desc(), Task.id.asc())
        if selected_status is not None:
            task_query = task_query.where(Task.status == selected_status)
        if selected_offset is not None:
            task_query = task_query.offset(selected_offset)
        task_result = await session.execute(task_query.limit(effective_limit + 1))
        fetched_tasks = list(task_result.scalars().all())
        if selected_offset is None:
            has_more = len(fetched_tasks) > effective_limit
            next_offset: int | None = None
        else:
            has_more, next_offset = _task_list_pagination_metadata(
                fetched_count=len(fetched_tasks),
                effective_limit=effective_limit,
                selected_offset=selected_offset,
            )
        tasks = fetched_tasks[:effective_limit]

        event_ids = [task.last_event_id for task in tasks if task.last_event_id is not None]
        events_by_id: dict[str, Event] = {}
        if event_ids:
            event_result = await session.execute(select(Event).where(Event.id.in_(event_ids)))
            events_by_id = {event.id: event for event in event_result.scalars().all()}

    items: list[TaskSummaryOut] = []
    for task in tasks:
        last_event: TaskListLastEventOut | None = None
        if task.last_event_id is not None:
            event_row = events_by_id.get(task.last_event_id)
            if event_row is not None:
                last_event = TaskListLastEventOut(
                    id=event_row.id,
                    type=event_row.type,
                    emitted_at=event_row.emitted_at,
                    trace_id=event_row.trace_id,
                )

        items.append(
            TaskSummaryOut(
                task_id=task.id,
                status=task.status,
                title=task.title,
                created_at=task.created_at,
                updated_at=task.updated_at,
                state_since=task.updated_at,
                actor=ActorOut(kind=task.actor_kind, id=task.actor_id),
                last_event=last_event,
            )
        )

    display_state: Literal["healthy", "empty-list"] = "healthy" if items else "empty-list"
    authority_state: Literal["authoritative", "non-authoritative"] = (
        "authoritative" if items else "non-authoritative"
    )

    response_kwargs = {
        "retrieved_at": clock.now(),
        "freshness_state": "fresh",
        "display_state": display_state,
        "authority_state": authority_state,
        "provenance": "registry-state task summary list",
        "request_id": request_id,
        "trace_id": trace_id,
        "correlation_id": request_id,
        "limit": effective_limit,
        "returned_count": len(items),
        "has_more": has_more,
        "next_offset": next_offset,
        "items": items,
    }
    if selected_status is not None and selected_limit is not None and selected_offset is not None:
        return TaskStatusLimitOffsetSelectedListResponse(
            route=_TASK_STATUS_LIMIT_OFFSET_ROUTE,
            selected_status=selected_status,
            selected_limit=selected_limit,
            selected_offset=selected_offset,
            **response_kwargs,
        )

    if selected_status is not None and selected_limit is not None:
        return TaskStatusLimitSelectedListResponse(
            route=_TASK_STATUS_LIMIT_ROUTE,
            selected_status=selected_status,
            selected_limit=selected_limit,
            **response_kwargs,
        )

    if selected_status is not None:
        return TaskStatusFilteredListResponse(
            route=_TASK_STATUS_FILTER_ROUTE,
            selected_status=selected_status,
            **response_kwargs,
        )

    if selected_limit is not None:
        if selected_offset is not None:
            return TaskLimitOffsetSelectedListResponse(
                route=_TASK_LIMIT_OFFSET_ROUTE,
                selected_limit=selected_limit,
                selected_offset=selected_offset,
                **response_kwargs,
            )
        return TaskLimitSelectedListResponse(
            route=_TASK_LIMIT_ROUTE,
            selected_limit=selected_limit,
            **response_kwargs,
        )

    return TaskListResponse(route="GET /v1/tasks", **response_kwargs)


def _heartbeat_state(row: Session) -> Literal["ended", "observed", "missing"]:
    if row.ended_at is not None:
        return "ended"
    if row.last_heartbeat_at is not None:
        return "observed"
    return "missing"


@router.get(
    "/sessions",
    status_code=200,
    response_model=SessionListResponse,
)
async def get_sessions(request: Request) -> SessionListResponse:
    """GET /v1/sessions — bounded session summary list (Story 110.2).

    Route-local, selector-free read of the ``Session`` table only. It rejects
    every query string and every GET body, exposes only display-safe row fields,
    and keeps session detail/control/discovery surfaces out of scope.
    """
    if request.url.query:
        raise HTTPException(
            status_code=400,
            detail="GET /v1/sessions does not accept query selectors",
        )

    if await request.body():
        raise HTTPException(
            status_code=400,
            detail="GET /v1/sessions does not accept a request body",
        )

    session_maker = request.app.state.session_maker
    clock = request.app.state.clock
    request_id: str = request.state.request_id
    trace_id: str | None = getattr(request.state, "trace_id", None)

    async with session_maker() as db_session:
        session_result = await db_session.execute(
            select(Session)
            .order_by(
                Session.last_heartbeat_at.desc().nulls_last(),
                Session.started_at.desc(),
                Session.id.asc(),
            )
            .limit(_SESSION_LIST_LIMIT + 1)
        )
        fetched_sessions = list(session_result.scalars().all())

    has_more = len(fetched_sessions) > _SESSION_LIST_LIMIT
    sessions = fetched_sessions[:_SESSION_LIST_LIMIT]
    items = [
        SessionSummaryOut(
            session_id=row.id,
            task_id=row.task_id,
            worker_kind=row.worker_kind,
            status=row.status,
            started_at=row.started_at,
            ended_at=row.ended_at,
            last_heartbeat_at=row.last_heartbeat_at,
            heartbeat_state=_heartbeat_state(row),
        )
        for row in sessions
    ]

    display_state: Literal["healthy", "empty-list"] = "healthy" if items else "empty-list"
    authority_state: Literal["authoritative", "non-authoritative"] = (
        "authoritative" if items else "non-authoritative"
    )

    return SessionListResponse(
        route="GET /v1/sessions",
        retrieved_at=clock.now(),
        freshness_state="fresh",
        display_state=display_state,
        authority_state=authority_state,
        provenance="registry-state session summary list",
        request_id=request_id,
        trace_id=trace_id,
        correlation_id=request_id,
        limit=_SESSION_LIST_LIMIT,
        returned_count=len(items),
        has_more=has_more,
        next_offset=None,
        sort="last_heartbeat_at_desc_nulls_last_started_at_desc_id_asc",
        items=items,
    )


@router.get(
    "/sessions/{session_id}",
    status_code=200,
    response_model=SessionDetailResponse,
)
async def get_session_detail(
    request: Request,
    session_id: str = Path(..., min_length=1, max_length=256),
) -> SessionDetailResponse:
    """GET /v1/sessions/{session_id} — bounded session detail (Story 111.2).

    Route-local, selector-free read of one ``Session`` table row only. The
    visible path parameter is the only selector; query strings and GET bodies
    are rejected before lookup. No task/event/log/path payloads or controls are
    returned.
    """
    if request.url.query:
        raise HTTPException(
            status_code=400,
            detail="GET /v1/sessions/{session_id} does not accept query selectors",
        )

    if await request.body():
        raise HTTPException(
            status_code=400,
            detail="GET /v1/sessions/{session_id} does not accept a request body",
        )

    session_maker = request.app.state.session_maker
    clock = request.app.state.clock
    request_id: str = request.state.request_id
    trace_id: str | None = getattr(request.state, "trace_id", None)

    async with session_maker() as db_session:
        result = await db_session.execute(select(Session).where(Session.id == session_id))
        row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="session not found")

    item = SessionSummaryOut(
        session_id=row.id,
        task_id=row.task_id,
        worker_kind=row.worker_kind,
        status=row.status,
        started_at=row.started_at,
        ended_at=row.ended_at,
        last_heartbeat_at=row.last_heartbeat_at,
        heartbeat_state=_heartbeat_state(row),
    )

    return SessionDetailResponse(
        route="GET /v1/sessions/{session_id}",
        selected_session_id=session_id,
        retrieved_at=clock.now(),
        freshness_state="fresh",
        display_state="healthy",
        authority_state="authoritative",
        provenance="registry-state session detail",
        request_id=request_id,
        trace_id=trace_id,
        correlation_id=request_id,
        item=item,
    )


@router.post(
    "/tasks",
    status_code=201,
    description=(
        "Create a task by appending a `task.created` envelope to the event log. "
        "Returns 201 immediately after durable append; the materializer "
        "(registry-state subscriber) applies the event to SQLite asynchronously. "
        "The `Location` response header points to the GET endpoint for the new task — "
        "clients SHOULD poll it with exponential backoff because GET may return 404 "
        "for ~100–200ms after this 201 response (eventual consistency). "
        "Idempotency-Key dedup is enforced at the route level (Story 2.13): "
        "duplicate same-key submissions return 201 with byte-identical body and "
        "`X-Idempotency-Status: replayed`; first attempts return 201 with "
        "`X-Idempotency-Status: applied`. Errors during the first attempt are "
        "NOT cached — subsequent same-key submissions will retry the factory "
        "until one succeeds. "
        "POST-RESTART DEGRADED MODE (Story 2.13 C2): when the in-process "
        "side-channel response cache is empty (typically after a process "
        "restart) but the durable SQLite cache still has the entry, the "
        "replay returns a minimal body without `task_id` and OMITS the "
        "`Location` header. A `Warning: 199` header signals the degraded "
        "response. Clients should re-derive the task_id from the original "
        "201 response they already received, OR poll the GET endpoint they "
        "previously discovered."
    ),
    responses={
        201: {
            "description": "Task created (or replayed from idempotency cache).",
            "model": CreateTaskResponse,
            "headers": {
                "Location": {
                    "schema": {"type": "string"},
                    "description": (
                        "GET endpoint for the new task. OMITTED when the response "
                        "is a post-restart degraded replay (see route description "
                        "and `Warning: 199` header)."
                    ),
                },
                "X-Idempotency-Status": {
                    "schema": {"type": "string", "enum": ["applied", "replayed"]},
                    "description": (
                        "`applied` — first call with this key; factory ran. "
                        "`replayed` — cache hit; factory NOT run; cached body returned."
                    ),
                },
                "Idempotency-Key": {"schema": {"type": "string"}},
                "Warning": {
                    "schema": {"type": "string"},
                    "description": (
                        "RFC 7234 `Warning: 199 oh-my-bmad ...` header set ONLY "
                        "on post-restart degraded replays where the response body "
                        "and Location are reconstructed without task_id."
                    ),
                },
            },
        },
    },
)
async def post_tasks(
    body: CreateTaskRequest,
    request: Request,
) -> Response:
    """Create a task by emitting ``task.created`` to the JSONL event log.

    EVENTUAL CONSISTENCY: Returns 201 immediately after the event is durably
    appended to the log. The materializer (separate process — registry-state
    subscriber) applies the event to SQLite asynchronously, typically within
    100-200ms. Clients querying GET /v1/tasks/{task_id} immediately after
    receiving 201 may see a 404 until the materializer catches up. Use the
    Location header to retry with exponential backoff.

    The Location header points to the GET endpoint for the new task. Clients
    SHOULD poll Location until 200 returns (or 5+ seconds, then surface the
    error to the operator).

    Phase 1 actor identity is read from ``request.state.actor_id`` (set by
    ``ActorIdMiddleware`` on every request — currently hardcoded to ``"http-api"``;
    real auth lands in Story 6.1+).

    Idempotency semantics (Story 2.13 — FR28 / NFR-R4):
      - First call with key K: factory runs, emits ``task.created``, returns
        201 with body B and ``X-Idempotency-Status: applied``. The cache stores
        ``(K → result_event_id)`` durably (SQLite, 7-day TTL); body B is
        captured in an in-process side-channel keyed by K so replays return
        byte-identical bytes.
      - Concurrent same-K calls: per-key asyncio.Lock in
        ``IdempotencyCacheStore.get_or_run`` serializes them; the factory
        runs EXACTLY ONCE; losers receive 201 with the SAME body B and
        ``X-Idempotency-Status: replayed``.
      - Subsequent same-K calls (post-completion): cache hit → 201 with
        body B and ``X-Idempotency-Status: replayed``; no event re-emitted.
      - Errors during first attempt: NOT cached; subsequent same-K
        submissions retry the factory.

    Architecture line 318 references 409 for "idempotency collision returning
    prior result"; Story 2.13 returns 201 instead, matching the NFR-R4 spec
    literally ("all 100 responses 201") and simplifying client logic — the
    ``X-Idempotency-Status: replayed`` header conveys the dedup signal that
    a 409 would otherwise carry.

    Route-level wiring (vs middleware-level): keeps the dedup tied to the
    single endpoint that needs it; non-mutating endpoints (GET) carry no
    ``X-Idempotency-Status`` header. A future story can hoist this into a
    generic middleware once additional mutating endpoints land.
    """
    app = request.app
    clock = app.state.clock
    writer = app.state.writer
    idempotency_cache: IdempotencyCacheStore = app.state.idempotency_cache
    response_body_cache: ResponseSlotCache = app.state.idempotency_response_cache

    idempotency_key: str = request.state.idempotency_key
    request_id: str = request.state.request_id
    # Story 9.2 pass-2 review N1: explicit conditional avoids Python's eager
    # default-arg evaluation in ``getattr(obj, name, default)`` — the prior
    # ``getattr(request.state, "trace_id", new_uuid7(clock=clock))`` would
    # mint a fresh UUID on EVERY request even when ``trace_id`` was already
    # set (wasted work + clock churn). It also silently masked the genuine
    # bug class "TraceIdMiddleware accidentally omitted from build_app".
    # We now emit an ERROR log when the fallback fires so the misconfig is
    # surfaced loudly instead of being papered over.
    trace_id_val = getattr(request.state, "trace_id", None)
    if trace_id_val is None:
        log.error("TraceIdMiddleware missing from stack — minting standalone trace_id")
        trace_id: str = new_uuid7(clock=clock)
    else:
        trace_id = trace_id_val
    actor_id: str = getattr(request.state, "actor_id", "http-api")

    # Story 6.3 AC-6: scoped cache key prevents cross-actor cache leakage.
    # Phase 1 hardcodes actor_id="http-api" so this is transparent, but the
    # tuple form is correct for when real auth lands.
    cache_key = (actor_id, idempotency_key)

    # Closure flag — distinguishes cache-miss (factory ran) from cache-hit
    # (factory skipped). ``IdempotencyCacheStore.get_or_run`` returns
    # ``(CacheHit, was_run: bool)``; we ALSO use this flag as a defensive
    # invariant check post-call.
    factory_called: bool = False
    # Mn1: store the task_id as ``str`` to avoid the bytes encode/decode
    # round-trip that the original implementation did. The slot's
    # ``task_id`` is bytes for hashing-friendly storage; we decode once at
    # construction.
    captured_task_id: dict[str, str] = {}

    async def _factory() -> str:
        """Factory closure invoked by ``IdempotencyCacheStore.get_or_run``.

        Population safety (review C1): this closure runs UNDER the per-key
        ``asyncio.Lock`` inside ``IdempotencyCacheStore``. The slot write
        on the last line is therefore protected by the same lock that
        serializes loser callers; loser callers cannot observe a
        half-populated cache.
        """
        nonlocal factory_called
        factory_called = True

        task_id = new_task_id(clock=clock)
        event_id = new_event_id(clock=clock)

        payload = TaskCreatedPayload(
            task_id=task_id,
            title=body.title,
            repo=body.repo,
            hint=body.hint,
            chat_id=body.chat_id,
            reply_to_message_id=body.reply_to_message_id,
            budget_token_limit=body.budget_token_limit,
            budget_action=body.budget_action,
        )

        # actor_id from JwtAuthMiddleware (JWT-validated when JWT_SECRET_KEY is
        # configured; Phase 1 X-Actor-Id fallback otherwise).
        actor = Actor(kind="operator", id=request.state.actor_id)

        # Story 12.4 AC2/AC3: schema_version bumped to 1.2.0 — emit the
        # additive-minor version that recognises budget_token_limit /
        # budget_action (on top of 3.9's chat_id / reply_to_message_id).
        # Earlier versions stay registered (back-compat) but new emissions
        # always use 1.2.0.
        envelope = EventEnvelope.create(
            event_id=event_id,
            type="task.created",
            schema_version="1.2.0",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=actor,
            payload=payload,
            request_id=request_id,
            trace_id=trace_id,
            parent_event_id=None,
        )

        await writer.append(envelope)

        # Build the 201 body NOW so we can both (a) cache it for byte-identical
        # replays and (b) recover the task_id in the outer scope via the slot.
        response_model = CreateTaskResponse(
            task_id=task_id,
            event_id=event_id,
            created_at=envelope.emitted_at,
        )
        body_bytes = response_model.model_dump_json().encode("utf-8")

        # Review C1: write the slot INSIDE the factory so it's covered by
        # ``IdempotencyCacheStore.get_or_run``'s per-key lock. Loser callers
        # for the same key cannot observe an empty cache + fall into the
        # post-restart fallback path while we hold the lock.
        # Review M6: single key (no companion ``:task_id`` suffix) so a
        # malicious ``Idempotency-Key: foo:task_id`` cannot collide with the
        # entry for key ``foo``.
        response_body_cache[cache_key] = ResponseSlot(
            body=body_bytes,
            task_id=task_id.encode("utf-8"),
        )
        captured_task_id["value"] = task_id
        return event_id

    cache_hit, was_run = await idempotency_cache.get_or_run(
        f"{actor_id}\x00{idempotency_key}",
        request_id=request_id,
        factory=_factory,
    )

    # Mn6: replace ``assert`` with explicit ``raise`` — production code
    # cannot rely on assertions (stripped under ``python -O``).
    degraded_post_restart = False
    if was_run:
        if not factory_called:
            raise RuntimeError(
                "get_or_run reported was_run=True but factory_called is False — "
                "side-channel cache invariant violated"
            )
        slot = response_body_cache[cache_key]
        body_bytes = slot.body
        task_id_str = captured_task_id["value"]
        status_value: IdempotencyStatus = "applied"
    else:
        # Cache hit. Use the side-channel slot if present (typical case
        # within the same process); otherwise rebuild a minimal body from
        # the cached event_id (post-restart, body-cache empty — see Dev
        # Notes for the trade-off).
        slot_or_none = response_body_cache.get(cache_key)
        if slot_or_none is None:
            # Post-restart fallback: rebuild a minimal body. The task_id
            # cannot be recovered without a JSONL replay, so this branch
            # is intentionally conservative — it returns the result_event_id
            # only, with task_id="" and OMITS the Location header (review
            # C2: empty task_id otherwise produced a malformed
            # ``Location: /v1/tasks/`` URL). Clients should re-derive
            # task_id from the original 201 they previously received.
            # Story 3.6 may add a JSONL-backed body cache; out of scope.
            fallback = CreateTaskResponse(
                task_id="",
                event_id=cache_hit.result_event_id,
                created_at=cache_hit.created_at,
            )
            body_bytes = fallback.model_dump_json().encode("utf-8")
            task_id_str = ""
            degraded_post_restart = True
        else:
            body_bytes = slot_or_none.body
            task_id_str = slot_or_none.task_id.decode("utf-8")
        status_value = "replayed"

    headers: dict[str, str] = {"X-Idempotency-Status": status_value}
    if degraded_post_restart:
        # RFC 7234 §5.5 — 199 "Miscellaneous warning" with the agent name
        # so operators tracing a degraded reply have a non-noisy signal.
        headers["Warning"] = (
            '199 oh-my-bmad "idempotency-replay served from cross-process cache; Location omitted"'
        )
    elif task_id_str:
        # Mn3: URL-encode the task_id even though current task_ids are
        # ASCII URL-safe — defensive.
        headers["Location"] = f"/v1/tasks/{quote(task_id_str, safe='')}"

    return Response(
        content=body_bytes,
        status_code=201,
        media_type="application/json",
        headers=headers,
    )


@router.get(
    "/tasks/{task_id}",
    status_code=200,
    response_model=TaskResponse,
)
async def get_task_by_id(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
) -> TaskResponse:
    """GET /v1/tasks/{task_id} — return full reconstituted state (FR4).

    Returns 200 with ``TaskResponse`` on success, 404 (problem+json) if the
    task does not exist. The engine is read-only (Story 2.3 ``create_engine``
    with ``read_only=True``) — write attempts raise ``OperationalError``.

    Story 7.1: enriched response includes ``state_since``, ``current_step``,
    ``total_steps``, ``last_agent_action``, ``worktree_lock``, and
    ``available_commands`` for full state reconstitution.
    """
    session_maker = request.app.state.session_maker

    async with session_maker() as session:
        task_result = await session.execute(select(Task).where(Task.id == task_id))
        task = task_result.scalar_one_or_none()

        if task is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Last event with optional summary.
        last_event: LastEventOut | None = None
        if task.last_event_id is not None:
            event_result = await session.execute(
                select(Event).where(Event.id == task.last_event_id)
            )
            event_row = event_result.scalar_one_or_none()
            if event_row is not None:
                payload_summary: str | None = None
                try:
                    payload_data = json.loads(event_row.payload_json)
                    if isinstance(payload_data, dict):
                        payload_summary = payload_data.get("reason")
                        if payload_summary is None:
                            payload_summary = payload_data.get("description")
                except (json.JSONDecodeError, AttributeError):
                    log.debug(
                        "Failed to extract summary from event %s",
                        event_row.id,
                        exc_info=True,
                    )
                last_event = LastEventOut(
                    id=event_row.id,
                    type=event_row.type,
                    emitted_at=event_row.emitted_at,
                    summary=payload_summary,
                )

        # Worktree lock state derived from sessions table.
        # Skip query for statuses that never have active sessions.
        latest_session = None
        if task.status in ("executing", "blocked", "idle", "active"):
            session_result = await session.execute(
                select(Session)
                .where(Session.task_id == task_id)
                .order_by(Session.started_at.desc())
                .limit(1)
            )
            latest_session = session_result.scalar_one_or_none()
        lock_held = (
            latest_session is not None
            and latest_session.status in ("active", "idle")
            and latest_session.worktree_path is not None
        )
        worktree_lock = WorktreeLockOut(
            held=lock_held,
            by_session_id=latest_session.id if lock_held else None,  # type: ignore[union-attr]  # None case unreachable; FastAPI dependency guarantees presence
            acquired_at=latest_session.started_at if lock_held else None,  # type: ignore[union-attr]  # None case unreachable; FastAPI dependency guarantees presence
        )

        commands = _next_commands_for(task.status)

    return TaskResponse(
        task_id=task.id,
        status=task.status,
        title=task.title,
        created_at=task.created_at,
        updated_at=task.updated_at,
        state_since=task.updated_at,
        actor=ActorOut(kind=task.actor_kind, id=task.actor_id),
        last_event=last_event,
        current_step=task.current_step,
        total_steps=task.total_steps,
        last_agent_action=task.last_agent_action,
        worktree_lock=worktree_lock,
        available_commands=commands,
        next_commands=commands,  # deprecated: use available_commands (Story 7.1)
        chat_id=task.chat_id,
        reply_to_message_id=task.reply_to_message_id,
        hint=task.hint,
        budget_token_limit=task.budget_token_limit,
        budget_action=task.budget_action,
    )


__all__ = [
    "ActorOut",
    "CreateTaskRequest",
    "CreateTaskResponse",
    "LastEventOut",
    "TaskResponse",
    "TaskLimitSelectedListResponse",
    "TaskListLastEventOut",
    "TaskListReadResponse",
    "TaskListResponse",
    "TaskSummaryOut",
    "SessionDetailResponse",
    "SessionListResponse",
    "SessionSummaryOut",
    "WorktreeLockOut",
    "router",
]
