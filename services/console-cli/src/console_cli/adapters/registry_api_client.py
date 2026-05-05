"""RegistryAPIClient — httpx-based client for registry-api endpoints (Story 4.2).

Wraps POST /v1/tasks, GET /v1/tasks/{task_id}, and
GET /v1/tasks/{task_id}/logs/digest.

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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
            headers["X-Request-ID"] = request_id

        body: dict[str, str] = {"title": title}
        if repo is not None:
            body["repo"] = repo
        if hint is not None:
            body["hint"] = hint

        async with httpx.AsyncClient(base_url=self._base_url) as client:
            response = await client.post("/v1/tasks", json=body, headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
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
    ) -> TaskResponseLocal:
        """GET /v1/tasks/{task_id} — retrieve task state.

        Returns TaskResponseLocal on HTTP 200.
        Raises ValueError if task_id doesn't match TASK_ID_PATTERN.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id

        async with httpx.AsyncClient(base_url=self._base_url) as client:
            response = await client.get(f"/v1/tasks/{task_id}", headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
            return TaskResponseLocal.model_validate(data)
        except (
            _json.JSONDecodeError,
            KeyError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_logs_digest(
        self,
        *,
        task_id: str,
        request_id: str | None = None,
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
            headers["X-Request-ID"] = request_id

        async with httpx.AsyncClient(base_url=self._base_url) as client:
            response = await client.get(f"/v1/tasks/{task_id}/logs/digest", headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
            return LogsDigestResponseLocal.model_validate(data)
        except (
            _json.JSONDecodeError,
            KeyError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc


__all__ = [
    "CreateTaskResponseLocal",
    "TaskResponseLocal",
    "LogsDigestResponseLocal",
    "ActorLocal",
    "LastEventLocal",
    "RegistryAPIClient",
    "RegistryResponseError",
    "TASK_ID_PATTERN",
]
