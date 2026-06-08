"""RegistryAPIClient — httpx-based client for registry-api endpoints (Story 3.3 AC-1, AC-2).

Wraps POST /v1/tasks, POST /v1/tasks/{id}/decisions, GET /v1/health, and
GET /v1/tasks/{id}.
The client holds a pre-built ``httpx.AsyncClient`` that is constructed ONCE at
lifespan startup (Story 3.1 H4 cache-once pattern) and reused across all handler
invocations. Never construct a new ``AsyncClient`` per-request.

Architecture boundary
---------------------
``CreateTaskResponseLocal``, ``DecisionResponseLocal``, ``HealthResponseLocal``,
``TaskResponseLocal``, ``ActorLocal``, and ``LastEventLocal`` are redefined here
rather than imported from ``registry_api.routes.*``.  This keeps the transport
boundary clean: the cross-service contract is HTTP/JSON (architecture.md:231),
not shared Python objects.  See AC-2 doc-comment for details.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from telegram_gateway.handlers._keys import TASK_ID_PATTERN

# Story 9.3 pass-2 review Q9: WARN when a caller passes an empty-string
# ``trace_id`` (distinct from ``None`` which means "don't forward").
_log = logging.getLogger("telegram_gateway.handlers.registry_client")


class CreateTaskResponseLocal(BaseModel):
    """Local mirror of registry-api's CreateTaskResponse (Story 2.9).

    Redefined here to avoid a services→services import (architecture.md:231 keeps
    the cross-service contract as HTTP/JSON, not shared Python objects).
    Source-of-truth for field layout: services/registry-api/src/registry_api/routes/tasks.py
    class CreateTaskResponse. Review-time validation: field names must match registry-api's
    serialised JSON keys. Payload models were migrated to packages/events/
    by Story 3.5.2; response DTOs remain local until count justifies a shared module.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    event_id: str
    created_at: datetime
    # Derived from response body first, then X-Idempotency-Status header (M3 fix).
    # "applied" = first successful submission; "replayed" = Telegram retry
    # deduplicated by registry-api (FR28). Defaults to "applied" when absent.
    idempotency_status: Literal["applied", "replayed"] = "applied"


class DecisionResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual DecisionResponse (Story 6.4 owns server-side).

    Forward-compatible shape pinned by 3.4's mocked tests; review-time validation
    must align with 6.4's POST /v1/tasks/{id}/decisions response when that endpoint lands.
    Source-of-truth: services/registry-api/src/registry_api/routes/tasks.py (Story 6.4).
    Architecture note: local redefinition keeps cross-service contract as HTTP/JSON
    (architecture.md:231) — same decision as CreateTaskResponseLocal (Story 3.3 AC-2).

    TODO(story-6.4): verify field names match Story 6.4's serialised JSON keys.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str
    decision_id: str  # "d-<uuidv7>" per FR7 audit trail
    action: Literal["approve", "reject", "stop", "retry"]
    decided_at: datetime
    # Derived from response body first, then X-Idempotency-Status header (M3 fix).
    # "applied" = first successful submission; "replayed" = Telegram retry
    # deduplicated by registry-api (FR28). Defaults to "applied" when absent.
    idempotency_status: Literal["applied", "replayed"] = "applied"


class HealthResponseLocal(BaseModel):
    """Local mirror of registry-api's eventual GET /v1/health response.

    FR17 fields: registry status, worker status, clawhip queue depth, platform version.
    Forward-compatible shape pinned by 3.5's mocked tests; alignment with the
    eventual server-side endpoint owner (TBD — gap in current epic plan; see Dev Notes).

    H1 (permissive str typing): ``registry_status`` and ``worker_status`` use ``str``
    rather than ``Literal[...]`` because the server-side endpoint is NOT yet
    implemented.  If registry-api adds ``"warning"`` / ``"maintenance"`` /
    ``"stopped"`` / ``"offline"`` states, ``Literal`` typing would silently render
    every ``/ping`` as ``"⚠️ Registry returned an unexpected response"`` instead of
    forwarding the actual status string.  The ``extra="ignore"`` policy in
    ``model_config`` ensures unknown future fields are dropped cleanly.

    TODO(story-TBD): verify field names match the server-side GET /v1/health response
    when that endpoint lands. Most likely owner: Story 6.x middleware stack or a new
    platform-observability story between Epics 5 and 7.
    TODO(story-TBD): re-evaluate whether to narrow these to Literal once the server
    contract is finalised.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    # H1: str + Field constraints rather than Literal — server contract not yet finalised.
    registry_status: str = Field(min_length=1, max_length=64)
    worker_status: str = Field(min_length=1, max_length=64)
    # L4: defensive upper bound prevents absurdly large queue depths rendering verbatim.
    clawhip_queue_depth: int = Field(ge=0, le=1_000_000)
    # M11: defensive upper bound prevents overlong version strings exceeding Telegram's
    # 4096-char message limit when combined with the rest of the reply.
    version: str = Field(min_length=1, max_length=200)  # e.g., "v1.2.3"


class KeyStatusResponseLocal(BaseModel):
    """Local mirror of registry-api's GET /v1/key-status response (Story 11.5.1 / AC2).

    Wire contract MUST match :class:`registry_api.routes.key_status.KeyStatusResponse`
    field-for-field; pinned by ``tests/contract/test_key_status_client_server_shape_parity.py``
    (Story 11.5.1 AC7; mirror-identity canon L9 from Epic 11 retro addendum).

    The 16-hex ``fingerprint`` is a one-way SHA-256[:8] truncation per Story
    11.5 AC1 + ADR-0006 §Key-fingerprint — operator-readable; does NOT reveal
    the underlying ``OPERATOR_HMAC_KEY``.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    rotated_at: datetime
    # Story 11.2 P1-H1 codebase-wide actor_id length invariant.
    rotated_by_actor_id: str = Field(min_length=1, max_length=128)


class ActorLocal(BaseModel):
    """Local mirror of registry-api's ActorOut (Story 3.14 AC-2).

    Source-of-truth: services/registry-api/src/registry_api/routes/tasks.py ActorOut.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=128)


class LastEventLocal(BaseModel):
    """Local mirror of registry-api's LastEventOut (Story 3.14 AC-2).

    Source-of-truth: services/registry-api/src/registry_api/routes/tasks.py LastEventOut.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)
    emitted_at: datetime
    summary: str | None = None


class WorktreeLockLocal(BaseModel):
    """Local mirror of registry-api's WorktreeLockOut (Story 7.1 / FR4)."""

    model_config = ConfigDict(frozen=True)

    held: bool
    by_session_id: str | None = None
    acquired_at: datetime | None = None


class TaskResponseLocal(BaseModel):
    """Local mirror of registry-api's TaskResponse (Story 3.14 AC-2, 7.1).

    Source-of-truth: services/registry-api/src/registry_api/routes/tasks.py TaskResponse.
    Fields ``chat_id`` and ``reply_to_message_id`` are internal routing fields
    persisted by registry-api (Story 3.9) — they are NOT rendered in the
    Telegram /status reply but are carried here for potential future use.
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=2000)
    created_at: datetime
    updated_at: datetime
    state_since: datetime | None = None
    actor: ActorLocal
    last_event: LastEventLocal | None = None
    current_step: int | None = None
    total_steps: int | None = None
    last_agent_action: str | None = None
    worktree_lock: WorktreeLockLocal | None = None
    available_commands: list[str] = Field(default_factory=list, max_length=20)
    next_commands: list[str] = Field(
        default_factory=list,
        max_length=20,
    )  # deprecated — use available_commands
    chat_id: int | None = None
    reply_to_message_id: int | None = None


class LogsDigestResponseLocal(BaseModel):
    """Local mirror of registry-api's logs/digest response (Story 3.15 AC-2 / 7.3).

    Shape pinned by 3.15's mocked tests and verified against Story 7.3's
    ``LogsDigestResponse`` in ``routes/digest.py`` — field names match exactly.

    Source-of-truth: services/registry-api/src/registry_api/routes/digest.py (Story 7.3).
    Architecture note: local redefinition keeps cross-service contract as HTTP/JSON
    (architecture.md:231) — same decision as CreateTaskResponseLocal (Story 3.3 AC-2).
    """

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=20_000)
    truncated: bool = False
    line_count: int = Field(ge=1, le=20)


class OpenInboxResponseLocal(BaseModel):
    """Local mirror of registry-api's ``OpenInboxResponse`` (Story 11.3 / FR63).

    Returned by ``POST /v1/approvals/inbox``. Source-of-truth:
    ``services/registry-api/src/registry_api/routes/approvals.py``
    (Story 11.3). Architecture note: local redefinition keeps the
    cross-service contract as HTTP/JSON.
    """

    model_config = ConfigDict(frozen=True)

    operator_chat_id: int
    inbox_thread_id: int
    opened_at: datetime
    event_id: str
    idempotency_status: Literal["applied", "replayed"] = "applied"


class InboxStateResponseLocal(BaseModel):
    """Local mirror of registry-api's ``InboxStateResponse`` (Story 11.3 / FR63).

    Returned by ``GET /v1/approvals/inbox/{operator_chat_id}``. Source-of-
    truth: ``services/registry-api/src/registry_api/routes/approvals.py``.
    """

    model_config = ConfigDict(frozen=True)

    operator_chat_id: int
    inbox_thread_id: int
    opened_at: datetime
    opened_by_actor_id: str


class RegistryResponseError(httpx.HTTPError):
    """Raised when registry-api returns a 2xx response with an unexpected/malformed body.

    Subclasses ``httpx.HTTPError`` so that handler catch-blocks for
    ``httpx.HTTPError`` still capture it, but handlers can also catch
    ``RegistryResponseError`` *before* the generic ``httpx.HTTPError``
    branch to produce a more specific reply:
    ``"⚠️ Registry returned an unexpected response. Logs captured."``

    This distinguishes a malformed-200 body (bug in registry-api) from a
    transient network failure (ReadTimeout etc.) — both previously rendered
    as ``"⚠️ Could not reach registry"`` (H1).
    """


class RegistryAPIClient:
    """httpx-based client for registry-api endpoints used by Telegram handlers.

    Wraps POST /v1/tasks and POST /v1/tasks/{id}/decisions. Constructor takes a
    pre-built AsyncClient (lifespan-owned, reusable across requests — Story 3.1 H4
    cache-once pattern).
    """

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        """Initialise with an already-built long-lived AsyncClient.

        Args:
            http_client: Lifespan-owned ``httpx.AsyncClient``.  NEVER pass a
                         per-request client — that would leave dangling connections
                         and defeat the TLS session-reuse benefit.  The client's
                         ``base_url`` must already be set to the registry-api base
                         URL (e.g. ``http://registry-api:8080``) at construction.
        """
        self._http_client = http_client

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Expose the underlying AsyncClient (for identity-check tests)."""
        return self._http_client

    async def create_task(
        self,
        *,
        description: str,
        idempotency_key: str,
        operator_actor_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        chat_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> CreateTaskResponseLocal:
        """POST /v1/tasks and return a typed local response model.

        Args:
            description:         Task title / free-text description (maps to
                                 ``CreateTaskRequest.title`` in registry-api).
            idempotency_key:     Deterministic key derived from ``(chat_id, message_id)``
                                 so Telegram retries map to the same task (FR28).
            operator_actor_id:   Telegram user id of the operator (Phase 1 actor hint;
                                 real auth lands in Story 6.1+).
            request_id:          UUIDv7 request correlation id (architecture.md:313).
                                 Forwarded as ``X-Request-ID``; generated by the caller.
            chat_id:             Story 3.9 AC-5 — Telegram chat id (negative for
                                 supergroups). Forwarded as ``CreateTaskRequest.chat_id``
                                 so registry-api persists the binding for the
                                 outbound TelegramSink (FR13).
            reply_to_message_id: Story 3.9 AC-5 — Telegram message id of the
                                 originating ``/task`` message; the sink replies
                                 to it on every progress event so the operator
                                 sees a single threaded conversation per task.

        Returns:
            :class:`CreateTaskResponseLocal` on HTTP 201.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses with the raw httpx
                ``Response`` attached so callers can inspect status and RFC 7807
                body (architecture.md:228).
            RegistryResponseError: On 2xx responses with a malformed/unexpected body.
            httpx.HTTPError:       On network / timeout errors.
        """
        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key,
            # Phase 1: pass the operator Telegram id as a hint header.
            # Story 6.1 replaces this with a proper auth token.
            "X-Actor-Id": operator_actor_id,
        }
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id so registry-api's
        # TraceIdMiddleware (Story 9.2) preserves rather than re-mints.
        # Pass-2 review Q9: tighten the truthy check to ``is not None and
        # != ""`` so a future caller passing ``trace_id=0`` (int) is no
        # longer silently dropped (truthy ``0`` is False). Empty-string
        # remains skipped but now emits a WARNING — sending ``X-Trace-Id: ``
        # would have produced a registry-api WARNING + UUIDv7 mint anyway;
        # surfacing it at the producer side fixes it faster.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        # Story 3.9 AC-5: omit chat_id / reply_to_message_id from the JSON
        # body when they are None so registry-api's CreateTaskRequest
        # ``extra="forbid"`` rejection does not fire on legacy callers and
        # so the wire shape stays minimal for non-Telegram callers.
        body: dict[str, str | int] = {"title": description}
        if chat_id is not None:
            body["chat_id"] = chat_id
        if reply_to_message_id is not None:
            body["reply_to_message_id"] = reply_to_message_id

        response = await self._http_client.post(
            "/v1/tasks",
            json=body,
            headers=headers,
        )
        response.raise_for_status()

        # H2 / H1: wrap body parsing so shape failures route into handle_task's
        # RegistryResponseError catch (before the generic httpx.HTTPError branch).
        try:
            data = response.json()
            # M3: prefer body field, fall back to header.
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
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            # L6: ValueError included for datetime parse edge-cases.
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def submit_decision(
        self,
        *,
        task_id: str,
        action: Literal["approve", "reject", "stop", "retry"],
        idempotency_key: str,
        operator_actor_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
        hint: str | None = None,
        override: Literal["license", "budget"] | None = None,
    ) -> DecisionResponseLocal:
        """POST /v1/tasks/{task_id}/decisions and return a typed local response model.

        Args:
            task_id:           The "t-<uuidv7>" task identifier.
            action:            Decision action: "approve", "reject", "stop", or "retry".
            idempotency_key:   Deterministic key derived from ``(chat_id, message_id)``
                               so Telegram retries map to the same decision (FR28).
            operator_actor_id: Telegram user id of the operator (Phase 1 actor hint).
            request_id:        UUIDv7 request correlation id (architecture.md:313).
                               Forwarded as ``X-Request-ID``; generated by the caller.
            hint:              Optional free-text hint for the orchestrator's next planning
                               pass (FR7). Omitted from the POST body when None.
            override:          Optional override flag (Stories 6.10/6.11). Valid values:
                               ``"license"``, ``"budget"``. Omitted from POST body when None.

        Returns:
            :class:`DecisionResponseLocal` on HTTP 2xx.

        Raises:
            ValueError:        If ``task_id`` does not match TASK_ID_PATTERN.
            httpx.HTTPStatusError: On non-2xx responses with the raw httpx
                ``Response`` attached so callers can inspect status and RFC 7807
                body (architecture.md:228).
            RegistryResponseError: On 2xx responses with a malformed/unexpected body.
            httpx.HTTPError:       On network / timeout errors.

        Note:
            POST /v1/tasks/{id}/decisions does NOT exist server-side yet.
            Story 6.4 owns the implementation. Until then a live call returns
            404. Tests mock the transport layer so they are runnable today.
            TODO(story-6.4): verify DecisionResponseLocal field names match
            Story 6.4's serialised JSON keys when that endpoint lands.
        """
        # M11: validate task_id shape before making any HTTP call.
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key,
            "X-Actor-Id": operator_actor_id,
        }
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id so registry-api's
        # TraceIdMiddleware (Story 9.2) preserves rather than re-mints.
        # Pass-2 review Q9: tighten the truthy check to ``is not None and
        # != ""`` so a future caller passing ``trace_id=0`` (int) is no
        # longer silently dropped (truthy ``0`` is False). Empty-string
        # remains skipped but now emits a WARNING — sending ``X-Trace-Id: ``
        # would have produced a registry-api WARNING + UUIDv7 mint anyway;
        # surfacing it at the producer side fixes it faster.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        # Omit hint key entirely when None (forward-compat with Story 3.18).
        body: dict[str, str] = {"action": action}
        if hint is not None:
            body["hint"] = hint
        if override is not None:
            body["override"] = override

        response = await self._http_client.post(
            f"/v1/tasks/{task_id}/decisions",
            json=body,
            headers=headers,
        )
        response.raise_for_status()

        # H2 / H1: wrap body parsing so shape failures route into handle_approve's
        # RegistryResponseError catch (before the generic httpx.HTTPError branch).
        try:
            data = response.json()
            # M3: prefer body field, fall back to header.
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
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            # L6: ValueError included for datetime parse edge-cases.
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_platform_health(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> HealthResponseLocal:
        """GET /v1/health and return a typed local response model.

        No request body. No Idempotency-Key header — GET is idempotent by HTTP
        semantics (RFC 7231 §4.2.2); Telegram retries safely re-fetch the health
        summary without duplication concerns. This is the FIRST handler in the bot
        that omits an idempotency key; document the reason explicitly.

        Args:
            request_id: UUIDv7 request correlation id. Forwarded as X-Request-ID.

        Returns:
            HealthResponseLocal on HTTP 2xx.

        Raises:
            RegistryResponseError: On 2xx with malformed/unexpected body (Story 3.4 H1).
            httpx.HTTPStatusError: On non-2xx responses.
            httpx.HTTPError:       On network / timeout errors.

        Note:
            GET /v1/health does NOT exist server-side yet. No story owner has been
            assigned (gap in epic plan). Until then a live call returns 404.
            Tests mock the transport layer and are runnable today.
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id through to registry-api.
        # Pass-2 review Q9: ``is not None and != ""`` so ``trace_id=0`` (int)
        # is not silently dropped; empty-string emits a WARNING at the
        # producer site rather than at the registry-api receive site.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        response = await self._http_client.get(
            "/v1/health",
            headers=headers,
        )
        response.raise_for_status()

        # M1: use model_validate to match the CreateTaskResponseLocal /
        # DecisionResponseLocal pattern — avoids manual key extraction.
        # H1: wrap body parsing so shape failures raise RegistryResponseError
        # (before the generic httpx.HTTPError branch in handle_ping).
        try:
            data = response.json()
            return HealthResponseLocal.model_validate(data)
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            # ValueError included for edge-cases (e.g. unexpected json type).
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_key_status(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> KeyStatusResponseLocal:
        """GET /v1/key-status and return the typed local response model (Story 11.5.1).

        No request body. No Idempotency-Key header — GET is idempotent by HTTP
        semantics (same as :meth:`get_platform_health`). Telegram retries
        safely re-fetch the key fingerprint without duplication concerns.

        Args:
            request_id: UUIDv7 request correlation id. Forwarded as X-Request-ID.
            trace_id:   UUIDv7 trace id (Story 9.3 / FR58). Forwarded as
                        X-Trace-Id when non-empty.

        Returns:
            KeyStatusResponseLocal on HTTP 2xx.

        Raises:
            RegistryResponseError: On 2xx with malformed/unexpected body.
            httpx.HTTPStatusError: On non-2xx responses (404 = cold-start;
                                   the handler renders an operator-readable
                                   "key not yet materialized" reply).
            httpx.HTTPError:       On network / timeout errors.
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id through to registry-api.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        response = await self._http_client.get("/v1/key-status", headers=headers)
        response.raise_for_status()

        try:
            data = response.json()
            return KeyStatusResponseLocal.model_validate(data)
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_task(
        self,
        *,
        task_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> TaskResponseLocal:
        """GET /v1/tasks/{task_id} and return a typed local response model.

        No Idempotency-Key header — GET is idempotent by HTTP semantics
        (same as get_platform_health). Telegram retries safely re-fetch
        without duplication concerns.

        Args:
            task_id:     The "t-<uuidv7>" task identifier.
            request_id:  UUIDv7 request correlation id. Forwarded as X-Request-ID.

        Returns:
            TaskResponseLocal on HTTP 2xx.

        Raises:
            ValueError:           If ``task_id`` does not match TASK_ID_PATTERN.
            httpx.HTTPStatusError: On non-2xx responses (e.g. 404 if task not found).
            RegistryResponseError: On 2xx with malformed/unexpected body.
            httpx.HTTPError:       On network / timeout errors.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id through to registry-api.
        # Pass-2 review Q9: ``is not None and != ""`` so ``trace_id=0`` (int)
        # is not silently dropped; empty-string emits a WARNING at the
        # producer site rather than at the registry-api receive site.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        response = await self._http_client.get(
            f"/v1/tasks/{task_id}",
            headers=headers,
        )
        response.raise_for_status()

        try:
            data = response.json()
            return TaskResponseLocal.model_validate(data)
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_logs_digest(
        self,
        *,
        task_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> LogsDigestResponseLocal:
        """GET /v1/tasks/{task_id}/logs/digest and return a typed local response model.

        No Idempotency-Key header — GET is idempotent by HTTP semantics
        (same as get_platform_health and get_task).

        Args:
            task_id:     The "t-<uuidv7>" task identifier.
            request_id:  UUIDv7 request correlation id. Forwarded as X-Request-ID.

        Returns:
            LogsDigestResponseLocal on HTTP 2xx.

        Raises:
            ValueError:           If ``task_id`` does not match TASK_ID_PATTERN.
            httpx.HTTPStatusError: On non-2xx responses (e.g. 404 if endpoint
                not deployed yet or task not found).
            RegistryResponseError: On 2xx with malformed/unexpected body.
            httpx.HTTPError:       On network / timeout errors.

        Note:
            GET /v1/tasks/{id}/logs/digest is implemented in Story 7.3.
            Returns 404 when the task has no events. Tests mock the transport
            layer to avoid requiring a live registry-api instance.
        """
        if not TASK_ID_PATTERN.match(task_id):
            raise ValueError(f"Invalid task_id (does not match TASK_ID_PATTERN): {task_id!r}")

        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        # Story 9.3 (FR58): forward derived trace_id through to registry-api.
        # Pass-2 review Q9: ``is not None and != ""`` so ``trace_id=0`` (int)
        # is not silently dropped; empty-string emits a WARNING at the
        # producer site rather than at the registry-api receive site.
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id
        elif trace_id == "":
            _log.warning(
                "empty trace_id supplied to registry_client; "
                "correlation will break (X-Trace-Id header omitted)"
            )

        response = await self._http_client.get(
            f"/v1/tasks/{task_id}/logs/digest",
            headers=headers,
        )
        response.raise_for_status()

        try:
            data = response.json()
            return LogsDigestResponseLocal.model_validate(data)
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_trace(
        self,
        *,
        trace_id: str,
        request_id: str | None = None,
    ) -> list[dict[str, object]]:
        """GET /v1/trace/{trace_id} — all events in the causal chain (FR59a / Story 9.7).

        Returns a list of raw event dicts ordered by emitted_at_monotonic_ns.
        Raises RegistryResponseError on malformed responses or non-2xx HTTP.
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id

        response = await self._http_client.get(
            f"/v1/trace/{quote(trace_id, safe='')}",
            headers=headers,
        )
        response.raise_for_status()

        try:
            data = response.json()
            if not isinstance(data, list):
                raise RegistryResponseError(
                    f"expected JSON array from /v1/trace, got {type(data).__name__}"
                )
            return data
        except (_json.JSONDecodeError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def open_inbox(
        self,
        *,
        operator_chat_id: int,
        inbox_thread_id: int,
        idempotency_key: str,
        operator_actor_id: str,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> OpenInboxResponseLocal:
        """POST /v1/approvals/inbox — emit ``approval.inbox_opened`` (Story 11.3).

        Used by the ``/approvals`` Telegram handler after aiogram successfully
        creates a Forum-Topic in the operator's chat. registry-api appends
        the event to JSONL; the registry-state materializer UPSERTs the
        ``approval_inbox`` row asynchronously.

        FR26 single-writer compliance: telegram-gateway never writes
        SQLite directly. The HTTP POST is the only state-mutation surface.

        Returns:
            :class:`OpenInboxResponseLocal` on HTTP 201.
        """
        headers: dict[str, str] = {
            "Idempotency-Key": idempotency_key,
            "X-Actor-Id": operator_actor_id,
        }
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        if trace_id is not None and trace_id != "":
            headers["X-Trace-Id"] = trace_id

        response = await self._http_client.post(
            "/v1/approvals/inbox",
            json={
                "operator_chat_id": operator_chat_id,
                "inbox_thread_id": inbox_thread_id,
            },
            headers=headers,
        )
        response.raise_for_status()

        try:
            data = response.json()
            # Story 11.3 review P25: HTTP idempotency convention treats the
            # response HEADER as authoritative — the body field is a mirror
            # of it. Read the header first; fall back to the body only when
            # the header is absent.
            raw_status = response.headers.get("X-Idempotency-Status") or data.get(
                "idempotency_status", "applied"
            )
            idempotency_status: Literal["applied", "replayed"] = (
                "replayed" if raw_status == "replayed" else "applied"
            )
            return OpenInboxResponseLocal(
                operator_chat_id=data["operator_chat_id"],
                inbox_thread_id=data["inbox_thread_id"],
                opened_at=data["opened_at"],
                event_id=data["event_id"],
                idempotency_status=idempotency_status,
            )
        except (_json.JSONDecodeError, KeyError, ValidationError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc

    async def get_pinned_inbox(
        self,
        *,
        operator_chat_id: int,
        request_id: str | None = None,
    ) -> InboxStateResponseLocal | None:
        """GET /v1/approvals/inbox/{operator_chat_id} — return row or None on 404.

        Used by ``/approvals`` to detect "operator already has an inbox open"
        before attempting to create a new Forum-Topic. Returns ``None`` on
        404 (no inbox yet) so callers branch cleanly.
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id

        response = await self._http_client.get(
            f"/v1/approvals/inbox/{operator_chat_id}",
            headers=headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()

        try:
            data = response.json()
            return InboxStateResponseLocal.model_validate(data)
        except (_json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise RegistryResponseError(f"registry-api returned malformed body: {exc}") from exc


__all__ = [
    "ActorLocal",
    "CreateTaskResponseLocal",
    "DecisionResponseLocal",
    "HealthResponseLocal",
    "InboxStateResponseLocal",
    "LastEventLocal",
    "LogsDigestResponseLocal",
    "OpenInboxResponseLocal",
    "RegistryAPIClient",
    "RegistryResponseError",
    "TaskResponseLocal",
]
