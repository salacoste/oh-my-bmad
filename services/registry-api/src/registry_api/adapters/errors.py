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

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

_PROBLEM_MEDIA_TYPE = "application/problem+json"

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
        type:     URI reference identifying the problem type. Defaults to
                  "about:blank" when no specific problem-type URI is defined.
        title:    Short human-readable summary of the problem type.
        status:   HTTP status code (mirrors the response status).
        detail:   Human-readable explanation specific to this occurrence.
        instance: URI reference identifying the specific occurrence (request URL).
    """

    model_config = ConfigDict(frozen=True)

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


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
    )
    return JSONResponse(
        content=problem.model_dump(),
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
    )
    return JSONResponse(
        content=problem.model_dump(),
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
    problem = ProblemDetails(
        type="about:blank",
        title="Internal Server Error",
        status=500,
        detail="An internal error occurred. The error has been logged for investigation.",
        instance=str(request.url),
    )
    return JSONResponse(
        content=problem.model_dump(),
        status_code=500,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


__all__ = [
    "ProblemDetails",
    "handle_http_exception",
    "handle_internal_error",
    "handle_validation_error",
]
