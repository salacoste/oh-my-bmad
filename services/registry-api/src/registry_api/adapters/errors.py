"""RFC 7807 problem+json error envelope + exception handlers (Story 2.9 AC-5).

Converts FastAPI's ``HTTPException`` and Pydantic's ``RequestValidationError``
into ``application/problem+json`` responses per RFC 7807.

``ProblemDetails`` is the canonical response shape. All 4xx/5xx responses from
registry-api use this envelope — consuming clients can always parse the same
JSON schema regardless of error source.
"""

from __future__ import annotations

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


async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Map ``HTTPException`` to RFC 7807 problem+json response.

    Registered via ``app.add_exception_handler(HTTPException, handle_http_exception)``
    in ``build_app``. Sets ``Content-Type: application/problem+json``.
    """
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


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map Pydantic ``RequestValidationError`` to RFC 7807 400 problem+json.

    Pydantic v2 validation errors carry structured field-level detail in
    ``exc.errors()``. We flatten them into a readable string for the ``detail``
    field — full structured errors are in the ``errors`` key of the response
    body for programmatic consumers.

    Registered via ``app.add_exception_handler(RequestValidationError, ...)``
    in ``build_app``.
    """
    errors = exc.errors()
    detail = "; ".join(f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in errors)
    problem = ProblemDetails(
        title="Validation Error",
        status=400,
        detail=detail,
        instance=str(request.url),
    )
    return JSONResponse(
        content=problem.model_dump(),
        status_code=400,
        media_type=_PROBLEM_MEDIA_TYPE,
    )


__all__ = [
    "ProblemDetails",
    "handle_http_exception",
    "handle_validation_error",
]
