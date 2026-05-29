"""GET /v1/key-status — operator-facing HMAC key fingerprint surface (Story 11.5.1 / FR65a).

Reads the singleton ``KeyFingerprint`` row materialized by registry-state from
``key.rotated`` events (Story 11.5 AC2 / AC4) and returns the operator-readable
{fingerprint, rotated_at, rotated_by_actor_id} triple.

Wire-shape contract MUST match
:class:`telegram_gateway.handlers.registry_client.KeyStatusResponseLocal` AND
:class:`console_cli.adapters.registry_api_client.KeyStatusResponseLocal`.
Pinned by ``tests/contract/test_key_status_client_server_shape_parity.py``
(Story 11.5.1 AC7; mirror-identity canon L9 from Epic 11 retro addendum).

Security
--------

* The endpoint NEVER returns the raw ``OPERATOR_HMAC_KEY``. The 16-hex
  ``fingerprint`` is a one-way SHA-256 truncation (Story 11.5 AC1 +
  ADR-0006); it identifies the key without revealing it.
* GET only — read-only; no audit-event emission (FR26 single-writer rule).
* 404 with RFC 7807 problem+json when the ``KeyFingerprint`` row is absent
  (cold-start before the first ``key.rotated`` event materializes).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    KeyFingerprint,
)
from sqlalchemy import select

_log = logging.getLogger("registry_api.routes.key_status")

router = APIRouter()


class KeyStatusResponse(BaseModel):
    """200 OK response body for GET /v1/key-status.

    Wire contract MUST match ``KeyStatusResponseLocal`` in
    ``telegram_gateway/handlers/registry_client.py`` AND
    ``console_cli/adapters/registry_api_client.py``: same field names + same
    Field constraints so the typed clients parse without ``ValidationError``.
    Pinned by the cross-service parity contract test (Story 11.5.1 AC7).
    """

    model_config = ConfigDict(frozen=True)

    # Story 11.5 AC1 + ADR-0006 §Key-fingerprint: SHA-256(key_bytes)[:8] = 16
    # lowercase hex chars (64 bits). Operator-readable; collision-safe for
    # single-operator key populations. NEVER reveals the key (one-way +
    # truncated).
    fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    # UTC datetime of the most recent rotation (UTCDateTime column in
    # registry-state stores millisecond-precision UTC; FastAPI serializes
    # to ISO-Z via the default pydantic encoder).
    rotated_at: datetime
    # Story 11.2 P1-H1 codebase-wide actor_id length invariant.
    rotated_by_actor_id: str = Field(min_length=1, max_length=128)


@router.get(
    "/key-status",
    status_code=200,
    response_model=KeyStatusResponse,
    tags=["meta"],
    responses={
        404: {
            "description": (
                "KeyFingerprint singleton row not yet materialized — "
                "first key.rotated event has not been observed since "
                "registry-state bootstrap."
            ),
        },
    },
)
async def get_key_status(request: Request) -> KeyStatusResponse:
    """Return the current HMAC signing-key fingerprint + rotation provenance.

    Cold-start (no KeyFingerprint row) returns 404 problem+json — the
    operator should re-query after the next key rotation lands. Registry-api
    detects rotation at lifespan startup (Story 11.5 AC4
    ``adapters/key_rotation.py``); first detection emits one ``key.rotated``
    event which the registry-state subscriber UPSERTs into the singleton
    row, after which this endpoint returns 200.
    """
    session_maker = request.app.state.session_maker

    async with session_maker() as session:
        result = await session.execute(select(KeyFingerprint).where(KeyFingerprint.id == "current"))
        row = result.scalar_one_or_none()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "key fingerprint not yet materialized — registry-state "
                    "has not observed a key.rotated event since bootstrap. "
                    "Restart the stack to trigger key-rotation detection."
                ),
            )

    return KeyStatusResponse(
        fingerprint=row.fingerprint,
        rotated_at=row.rotated_at,
        rotated_by_actor_id=row.rotated_by_actor_id,
    )
