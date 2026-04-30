"""RFC 7807 problem+json error envelope + exception handlers (Story 2.9 AC-5).

Converts FastAPI's ``HTTPException``, Pydantic's ``RequestValidationError``,
and any *unhandled* ``Exception`` into ``application/problem+json`` responses
per RFC 7807.

``ProblemDetails`` is the canonical response shape. All 4xx/5xx responses from
registry-api use this envelope — consuming clients can always parse the same
JSON schema regardless of error source.

F6 note: handler signatures use ``exc: Exception`` (the type FastAPI dispatches
with) and runtime-narrow via ``isinstance``. This avoids ``# type: ignore``
on ``app.add_exception_handler`` calls under mypy --strict.
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

_PROBLEM_MEDIA_TYPE = "application/problem+json"

# Story 3.6 AC-3: HTTP methods that mutate state. Only these methods carry the
# server-generated-key nudge in the ProblemDetails extensions; non-mutating
# methods (GET / HEAD / OPTIONS) omit the field entirely so the envelope is
# not noisy for read paths.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Story 3.6 AC-3: hint payload populated when a mutation request reaches an
# error handler with a server-generated Idempotency-Key. Story 3.7's Telegram
# renderer will read these stable keys.
#
# Story 3.6 L1: ``MappingProxyType`` makes the module-level source-of-truth
# read-only; mutating the proxy raises ``TypeError`` so a caller cannot
# accidentally rebind a key on the shared object. ``_build_idempotency_
# extensions`` still returns a fresh ``dict(...)`` per request so callers
# (including Story 3.7's Telegram renderer) can mutate their own copy
# without touching the shared template. Note: the copy is shallow — the
# values are flat strings, so this is sufficient. See
# ``ProblemDetails.extensions`` docstring for the shallow-mutability caveat.
_IDEMPOTENCY_NUDGE: MappingProxyType[str, Any] = MappingProxyType(
    {
        "idempotency_key_origin": "server-generated",
        "idempotency_hint": (
            "Provide a client-generated UUIDv7 Idempotency-Key for true idempotent "
            "retries (RFC 7231 §4.2.2)."
        ),
    }
)


def _build_idempotency_extensions(request: Request) -> dict[str, Any] | None:
    """Return the AC-3 nudge dict iff the key was server-generated on a mutation.

    Defensive ``getattr(..., None)`` access — ``request.state`` may be
    incomplete if a middleware crashed before populating the flag (Story 2.9
    review F1: exception handlers must not leak state assumptions).

    Story 3.6 L1: returns a SHALLOW copy of ``_IDEMPOTENCY_NUDGE`` (the
    MappingProxyType source-of-truth is read-only — ``dict(...)`` produces
    a fresh mutable dict whose string values are themselves immutable, so
    the shallow copy is sufficient for one level of safety).
    """
    generated = getattr(request.state, "idempotency_key_generated", None)
    if generated is True and request.method.upper() in _MUTATING_METHODS:
        return dict(_IDEMPOTENCY_NUDGE)
    return None


_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}

_log = logging.getLogger("registry_api.errors")


class ProblemDetails(BaseModel):
    """RFC 7807 problem+json response shape.

    Fields:
        type:       URI reference identifying the problem type. Defaults to
                    "about:blank" when no specific problem-type URI is defined.
        title:      Short human-readable summary of the problem type.
        status:     HTTP status code (mirrors the response status).
        detail:     Human-readable explanation specific to this occurrence.
        instance:   URI reference identifying the specific occurrence (request URL).
        extensions: Story 3.6 AC-3: nested dict of platform-specific custom
                    fields per architecture.md §366–382. Optional — when None,
                    ``model_dump(exclude_none=True)`` keeps it off the wire so
                    healthy responses are not polluted with empty objects.
                    Story 3.7's Telegram renderer will read this nested dict.

                    Shallow-mutability caveat (Story 3.6 L1): although
                    ``model_config = frozen=True`` blocks reassignment of
                    the field, Pydantic v2 does NOT freeze nested dict
                    values — a downstream consumer that does
                    ``problem.extensions["foo"] = "bar"`` will succeed even
                    on an instance built from the read-only
                    ``_IDEMPOTENCY_NUDGE`` template (because
                    ``_build_idempotency_extensions`` returns a fresh
                    ``dict(...)`` shallow copy per request). Treat this
                    field as logically immutable on the wire and avoid
                    in-place mutation by route handlers / renderers.
    """

    model_config = ConfigDict(frozen=True)

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    extensions: dict[str, Any] | None = None


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Map ``HTTPException`` to RFC 7807 problem+json response.

    Registered via ``app.add_exception_handler(HTTPException, handle_http_exception)``
    in ``build_app``. Sets ``Content-Type: application/problem+json``.

    Signature accepts ``Exception`` (FastAPI's dispatch type) and runtime-narrows
    to ``HTTPException`` so the registration call type-checks under mypy --strict
    without ``# type: ignore``.
    """
    if not isinstance(exc, HTTPException):
        raise TypeError(f"expected HTTPException, got {type(exc).__name__}")
    status = exc.status_code
    title = _STATUS_TITLES.get(status, "Error")
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    problem = ProblemDetails(
        title=title,
        status=status,
        detail=detail,
        instance=str(request.url),
        extensions=_build_idempotency_extensions(request),
    )
    return JSONResponse(
        content=problem.model_dump(exclude_none=True),
        status_code=status,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Map Pydantic ``RequestValidationError`` to RFC 7807 422 problem+json.

    Pydantic v2 validation errors carry structured field-level detail in
    ``exc.errors()``. We flatten them into a readable string for the ``detail``
    field — full structured errors are in the ``errors`` key of the response
    body for programmatic consumers.

    F4: status code is 422 (Unprocessable Entity) — the canonical mapping for
    syntactically-valid but semantically-invalid request bodies. Earlier drafts
    used 400, but FastAPI's default validation status is 422 and that is the
    correct HTTP semantics for failed Pydantic validation.

    Signature: see ``handle_http_exception`` for the F6 mypy rationale.
    """
    if not isinstance(exc, RequestValidationError):
        raise TypeError(f"expected RequestValidationError, got {type(exc).__name__}")
    errors = exc.errors()
    detail = "; ".join(f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in errors)
    problem = ProblemDetails(
        title="Validation Error",
        status=422,
        detail=detail,
        instance=str(request.url),
        extensions=_build_idempotency_extensions(request),
    )
    return JSONResponse(
        content=problem.model_dump(exclude_none=True),
        status_code=422,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


async def handle_internal_error(request: Request, exc: Exception) -> JSONResponse:
    """Map any unhandled exception → RFC 7807 problem+json 500 response.

    F2+F3: Without this catch-all, FastAPI returns a plain ``text/plain``
    "Internal Server Error" 500 — which violates the AC-5 contract that *every*
    4xx/5xx response from registry-api uses the problem+json envelope. Logging
    at ``exception`` level captures the traceback for operator investigation
    while the response body intentionally surfaces no internal details.
    """
    _log.exception(
        "unhandled exception in request handler",
        extra={"path": str(request.url)},
    )
    # Story 3.6 AC-3: ``_build_idempotency_extensions`` uses ``getattr(...,
    # None)`` so a crash before ``IdempotencyKeyMiddleware`` populated
    # ``request.state.idempotency_key_generated`` returns ``None`` here
    # rather than raising AttributeError — the catch-all handler must
    # never double-fault.
    problem = ProblemDetails(
        type="about:blank",
        title="Internal Server Error",
        status=500,
        detail="An internal error occurred. The error has been logged for investigation.",
        instance=str(request.url),
        extensions=_build_idempotency_extensions(request),
    )
    return JSONResponse(
        content=problem.model_dump(exclude_none=True),
        status_code=500,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


__all__ = [
    "ProblemDetails",
    "handle_http_exception",
    "handle_internal_error",
    "handle_validation_error",
]
