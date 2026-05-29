"""GET /v1/health liveness probe (Story 11.3.7 / AC5).

Wire-shape contract MUST match
:class:`telegram_gateway.handlers.registry_client.HealthResponseLocal`
(declared at ``services/telegram-gateway/.../registry_client.py:82-112``):
``registry_status``, ``worker_status``, ``clawhip_queue_depth``, ``version``.

This is a **liveness** probe — it confirms the HTTP server is serving + the
process is healthy enough to answer requests. It does NOT exercise the
registry-state SQLite store, the event-log writer, or any downstream
dependency, so a green response only proves "registry-api is up", not
"the spine is healthy".

The placeholder values for ``worker_status`` and ``clawhip_queue_depth``
reflect that registry-api itself does not (yet) track worker liveness or
queue depth — those signals live in the worker-wrapper + clawhip-daemon
services respectively. FR17 / a future platform-observability story is
expected to expand this endpoint to query a shared status registry (or
add a sibling ``/v1/ready`` readiness probe with a cheap ``SELECT 1``).

Until then this returns a stable, schema-compliant shape so:
  * the S-4 separability test's ``urlopen('/v1/health')`` probe sees 200
  * the telegram-gateway ``/ping`` command can parse the response cleanly
    via its typed ``HealthResponseLocal`` without ``ValidationError``
  * the route follows the codebase's ``routes/*.py`` + ``include_router``
    convention (matches ``digest.py``, ``events.py``, ``tasks.py``, etc.)
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from registry_api._version import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    """200 OK response body for GET /v1/health.

    Wire contract MUST match ``HealthResponseLocal`` in
    ``telegram_gateway/handlers/registry_client.py``: same field names +
    same Field constraints so the typed client parses without
    ``ValidationError``.
    """

    model_config = ConfigDict(frozen=True)

    # H1 (mirror of client constraint): str, not Literal — server contract
    # not yet finalised; if registry-api adds "warning"/"maintenance"/etc.
    # states, the client's permissive str typing forwards them verbatim.
    registry_status: str = Field(min_length=1, max_length=64)
    worker_status: str = Field(min_length=1, max_length=64)
    # L4: defensive upper bound prevents absurdly large queue depths.
    clawhip_queue_depth: int = Field(ge=0, le=1_000_000)
    # M11: defensive upper bound prevents overlong version strings exceeding
    # Telegram's 4096-char message limit when rendered in /ping reply.
    version: str = Field(min_length=1, max_length=200)


@router.get(
    "/health",
    status_code=200,
    response_model=HealthResponse,
    tags=["meta"],
)
async def get_health() -> HealthResponse:
    """Liveness probe — returns 200 when registry-api is serving.

    Phase 1 placeholders for ``worker_status`` + ``clawhip_queue_depth``:
    registry-api does not track these signals directly; FR17 expansion
    will source them from the registry-state subscriber's materialised
    status view (or a sibling /v1/ready endpoint).
    """
    return HealthResponse(
        registry_status="ok",
        worker_status="unknown",
        clawhip_queue_depth=0,
        version=__version__,
    )
