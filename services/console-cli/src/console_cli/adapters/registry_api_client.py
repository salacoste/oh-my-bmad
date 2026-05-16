"""RegistryAPIClient — httpx-based client for registry-api endpoints (Stories 4.2–4.4).

Wraps POST /v1/tasks, GET /v1/tasks/{task_id},
GET /v1/tasks/{task_id}/logs/digest, POST /v1/tasks/{task_id}/decisions,
GET /v1/health, and GET /v1/tasks/{task_id}/events.

The client creates a fresh ``httpx.AsyncClient`` per method call via
``async with``. This is the CLI pattern: each invocation is short-lived,
so no connection pool or lifespan is needed (contrast with telegram-gateway's
long-lived lifespan-owned client).

Architecture boundary
---------------------
Response models are redefined here as ``*Local`` frozen Pydantic models,
mirroring the telegram-gateway pattern. The cross-service contract is
HTTP/JSON (architecture.md:231), not shared Python objects.
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime
from typing import Literal

import httpx
from events.envelope import is_valid_trace_id  # noqa: IMP001
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Pass-2 S6: header constants live in ``app.headers`` (commands depend
# on ``app/`` not ``adapters/``). Re-exported here as module-level names
# for backwards-compatibility with pre-pass-2 imports that landed in
# commit 653e2a9 (pass-1 R13). Listed in ``__all__`` below.
from console_cli.app.headers import REQUEST_ID_HEADER, TRACE_ID_HEADER

# Default timeout for CLI HTTP calls — prevents indefinite hangs on stalled connections.
_DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# TASK_ID_PATTERN — local definition (cannot import from telegram-gateway).
# Validates the "t-<uuidv7>" task-id format per architecture naming rules.
TASK_ID_PATTERN: re.Pattern[str] = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class CreateTaskResponseLocal(BaseModel):
    """Local mirror of registry-api's CreateTaskResponse."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    event_id: str
    created_at: datetime
    idempotency_status: Literal["applied", "replayed"] = "applied"


class ActorLocal(BaseModel):
    """Local mirror of registry-api's ActorOut."""

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=128)


class LastEventLocal(BaseModel):
    """Local mirror of registry-api's LastEventOut."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    emitted_at: datetime


class TaskResponseLocal(BaseModel):
    """Local mirror of registry-api's TaskResponse."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=2000)
    created_at: datetime
    updated_at: datetime
    actor: ActorLocal
    last_event: LastEventLocal | None = None
    next_commands: list[str] = Field(max_length=20)


class LogsDigestResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual logs/digest response.

    Forward-compatible shape — Story 7.3 owns the server-side endpoint.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=20_000)
    truncated: bool = False
    line_count: int = Field(ge=1, le=20)


class DecisionResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual DecisionResponse (Story 6.4).

    Forward-compatible shape — Story 6.4 owns POST /v1/tasks/{id}/decisions.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    decision_id: str
    action: Literal["approve", "reject", "stop", "retry"]
    decided_at: datetime
    idempotency_status: Literal["applied", "replayed"] = "applied"


class HealthResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual GET /v1/health response.

    FR17 fields: registry status, worker status, clawhip queue depth, version.
    Forward-compatible shape — server endpoint not yet implemented.

    Uses str fields rather than Literal because the server-side contract
    is not finalised. ``extra="ignore"`` drops unknown future fields.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    registry_status: str = Field(min_length=1, max_length=64)
    worker_status: str = Field(min_length=1, max_length=64)
    clawhip_queue_depth: int = Field(ge=0, le=1_000_000)
    version: str = Field(min_length=1, max_length=200)


class TaskEventsResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual GET /v1/tasks/{id}/events response.

    Forward-compatible shape — Story 7.5 owns the server-side endpoint.
    Each event is a raw dict (JSON passthrough — no per-event validation).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    events: list[dict[str, object]] = Field(default_factory=list)


class RegistryResponseError(httpx.HTTPError):
    """Raised when registry-api returns a 2xx response with a malformed body."""


class RegistryAPIClient:
    """Async HTTP client for registry-api — per-invocation httpx.AsyncClient.

    Each method creates a fresh ``AsyncClient`` via ``async with`` for the
    duration of a single HTTP call. This suits the CLI's short-lived
    invocation model (one command = one process).
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def create_task(
        self,
        *,
        title: str,
        idempotency_key: str,
        actor_id: str = "console",
        request_id: str | None = None,
        trace_id: str | None = None,
        repo: str | None = None,
        hint: str | None = None,
    ) -> CreateTaskResponseLocal:
        """POST /v1/tasks — create a new task.

        Returns CreateTaskResponseLocal on HTTP 201.
        Raises httpx.HTTPStatusError on non-2xx, RegistryResponseError on
        malformed 2xx body, httpx.HTTPError on network errors.
        """
        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key,
            "X-Actor-Id": actor_id,
        }
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        # Pass-2 S1: tighten the R1 type-safe guard further — reject not
        # just ``bytes``/``int``/empty-string, but ALSO whitespace-only,
        # NUL-bearing, and CRLF-injection values (``"uuidv7\r\nX-Evil: 1"``).
        # Delegating to ``is_valid_trace_id`` keeps the shape contract in
        # one place (events.envelope, Story 9.1) so future ingresses
        # (Stories 9.5/9.6/etc.) get the same hardening for free.
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        body: dict[str, str] = {"title": title}
        if repo is not None:
            body["repo"] = repo
        if hint is not None:
            body["hint"] = hint

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.post("/v1/tasks", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        try:
            raw_status = data.get("idempotency_status") or response.headers.get(
                "X-Idempotency-Status", "applied"
            )
            idempotency_status: Literal["applied", "replayed"] = (
                "replayed" if raw_status == "replayed" else "applied"
            )
            return CreateTaskResponseLocal(
                task_id=data["task_id"],
                event_id=data["event_id"],
                created_at=data["created_at"],
                idempotency_status=idempotency_status,
            )
        except (
            _json.JSONDecodeError,
            KeyError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_task(
        self,
        *,
        task_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> TaskResponseLocal:
        """GET /v1/tasks/{task_id} — retrieve task state.

        Returns TaskResponseLocal on HTTP 200.
        Raises ValueError if task_id doesn't match TASK_ID_PATTERN.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {}
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(f"/v1/tasks/{task_id}", headers=headers)
            response.raise_for_status()
            data = response.json()

        try:
            return TaskResponseLocal.model_validate(data)
        except (
            _json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_logs_digest(
        self,
        *,
        task_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> LogsDigestResponseLocal:
        """GET /v1/tasks/{task_id}/logs/digest — retrieve LLM-digest output.

        Note: Server-side endpoint not yet implemented (Story 7.3).
        Live calls return 404. Tests mock the transport layer.

        Returns LogsDigestResponseLocal on HTTP 200.
        Raises ValueError if task_id doesn't match TASK_ID_PATTERN.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {}
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(f"/v1/tasks/{task_id}/logs/digest", headers=headers)
            response.raise_for_status()
            data = response.json()

        try:
            return LogsDigestResponseLocal.model_validate(data)
        except (
            _json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def submit_decision(
        self,
        *,
        task_id: str,
        action: Literal["approve", "reject", "stop", "retry"],
        idempotency_key: str,
        actor_id: str = "console",
        request_id: str | None = None,
        trace_id: str | None = None,
        hint: str | None = None,
    ) -> DecisionResponseLocal:
        """POST /v1/tasks/{task_id}/decisions — submit an operator decision.

        Note: Server-side endpoint not yet implemented (Story 6.4).
        Live calls return 404. Tests mock the transport layer.

        Returns DecisionResponseLocal on HTTP 2xx.
        Raises ValueError if task_id doesn't match TASK_ID_PATTERN.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key,
            "X-Actor-Id": actor_id,
        }
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        body: dict[str, str] = {"action": action}
        if hint is not None:
            body["hint"] = hint

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.post(
                f"/v1/tasks/{task_id}/decisions",
                json=body,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        try:
            raw_status = data.get("idempotency_status") or response.headers.get(
                "X-Idempotency-Status", "applied"
            )
            idempotency_status: Literal["applied", "replayed"] = (
                "replayed" if raw_status == "replayed" else "applied"
            )
            return DecisionResponseLocal(
                task_id=data["task_id"],
                decision_id=data["decision_id"],
                action=data["action"],
                decided_at=data["decided_at"],
                idempotency_status=idempotency_status,
            )
        except (
            _json.JSONDecodeError,
            KeyError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_platform_health(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> HealthResponseLocal:
        """GET /v1/health — platform health summary.

        No Idempotency-Key header — GET is idempotent by HTTP semantics.

        Note: Server-side endpoint not yet implemented. Live calls return 404.
        Tests mock the transport layer.

        Returns HealthResponseLocal on HTTP 2xx.
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get("/v1/health", headers=headers)
            response.raise_for_status()
            data = response.json()

        try:
            return HealthResponseLocal.model_validate(data)
        except (
            _json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_task_events(
        self,
        *,
        task_id: str,
        since: str | None = None,
        limit: int = 100,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> TaskEventsResponseLocal:
        """GET /v1/tasks/{task_id}/events — raw event stream for debugging.

        Returns TaskEventsResponseLocal on HTTP 2xx.
        Raises ValueError if task_id doesn't match TASK_ID_PATTERN.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        params: dict[str, str | int] = {"limit": limit}
        if since is not None:
            params["since"] = since

        headers: dict[str, str] = {}
        if request_id is not None:
            headers[REQUEST_ID_HEADER] = request_id
        if isinstance(trace_id, str) and is_valid_trace_id(trace_id):
            headers[TRACE_ID_HEADER] = trace_id

        async with httpx.AsyncClient(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(
                f"/v1/tasks/{task_id}/events",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        try:
            return TaskEventsResponseLocal.model_validate({"events": data})
        except (
            _json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"malformed body: {exc}") from exc


__all__ = [
    # Pass-2 S7: ASCII-strict sort (RUF022) — uppercase constants first,
    # then alphabetical PascalCase exports.
    "REQUEST_ID_HEADER",
    "TASK_ID_PATTERN",
    "TRACE_ID_HEADER",
    "ActorLocal",
    "CreateTaskResponseLocal",
    "DecisionResponseLocal",
    "HealthResponseLocal",
    "LastEventLocal",
    "LogsDigestResponseLocal",
    "RegistryAPIClient",
    "RegistryResponseError",
    "TaskEventsResponseLocal",
    "TaskResponseLocal",
]
