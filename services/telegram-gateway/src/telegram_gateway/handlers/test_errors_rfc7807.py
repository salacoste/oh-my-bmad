"""Tests for ``format_http_error`` problem-type dispatch (Story 3.7 AC-5/6/7/8/9/10/11).

Co-located with ``_errors.py``. Named ``test_errors_rfc7807.py`` (not
``test__errors.py``) because leading underscores in module names cause
pytest collection issues with some plugins; the convention is to name
test files after the feature being tested.

11 tests covering AC-11's distribution:
  AC-5/AC-6 routing + per-helper coverage:
    - test_format_http_error_routes_validation_to_field_renderer
    - test_format_http_error_validation_caps_field_list_at_5
    - test_format_http_error_validation_html_escapes_field_names
  AC-5/AC-7 idempotency:
    - test_format_http_error_routes_idempotency_collision_with_extensions_task_id
    - test_format_http_error_routes_idempotency_collision_with_top_level_task_id
  AC-5/AC-8 not-found:
    - test_format_http_error_routes_not_found
  AC-5/AC-9 rate-limited:
    - test_format_http_error_routes_rate_limited_with_retry_after_seconds
    - test_format_http_error_routes_rate_limited_without_retry_after_seconds
  AC-5/AC-10 internal:
    - test_format_http_error_routes_internal_error
  AC-5 fallback:
    - test_format_http_error_falls_back_to_legacy_status_when_type_unknown
  Story 3.5 H2 / back-compat carry-forward:
    - test_format_http_error_preserves_command_label_verbs_per_problem_type
    - test_format_http_error_5xx_unchanged
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from telegram_gateway.handlers._errors import format_http_error


def _make_status_error(
    status: int,
    body: dict[str, Any] | str | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.HTTPStatusError:
    """Construct a realistic ``httpx.HTTPStatusError`` carrying a JSON envelope.

    Story 3.6 H1 carry-forward: tests must exercise the actual code path with
    realistic envelope JSON, not mock the helper functions directly.
    """
    if body is None:
        content = b""
    elif isinstance(body, str):
        content = body.encode()
    else:
        content = json.dumps(body).encode()
    request = httpx.Request("POST", "http://registry-api:8080/v1/tasks")
    response = httpx.Response(
        status_code=status,
        content=content,
        headers=headers or {"content-type": "application/problem+json"},
        request=request,
    )
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


# ---------------------------------------------------------------------------
# AC-5 / AC-6: validation renderer
# ---------------------------------------------------------------------------


def test_format_http_error_routes_validation_to_field_renderer() -> None:
    """AC-5/AC-6: ``type=/errors/validation`` routes to bullet-list renderer."""
    body = {
        "type": "/errors/validation",
        "title": "Validation Error",
        "status": 422,
        "detail": "body -> title: field required",
        "extensions": {
            "errors": [
                {"loc": ["body", "title"], "msg": "field required", "type": "missing"},
                {
                    "loc": ["body", "priority"],
                    "msg": "input should be 'low', 'medium' or 'high'",
                    "type": "literal_error",
                },
            ]
        },
    }
    exc = _make_status_error(422, body)
    result = format_http_error(exc)
    assert result.startswith("⚠️ Task rejected: invalid request")
    assert "• body → title: field required" in result
    assert "• body → priority: input should be" in result


def test_format_http_error_validation_caps_field_list_at_5() -> None:
    """AC-6: 7-field validation error → 5 bullets + ``… and 2 more`` suffix."""
    errors = [{"loc": ["body", f"f{i}"], "msg": f"err{i}", "type": "missing"} for i in range(7)]
    body = {
        "type": "/errors/validation",
        "status": 422,
        "extensions": {"errors": errors},
    }
    exc = _make_status_error(422, body)
    result = format_http_error(exc)
    # Exactly 5 bullets present.
    assert result.count("•") == 5
    # Overflow suffix names the remaining count.
    assert "… and 2 more" in result
    # Last two field names are NOT in the rendered output.
    assert "f5" not in result
    assert "f6" not in result


def test_format_http_error_validation_html_escapes_field_names() -> None:
    """AC-6 / Story 3.5 H5: field names containing HTML are escaped."""
    body = {
        "type": "/errors/validation",
        "status": 422,
        "extensions": {
            "errors": [
                {"loc": ["body", "<script>"], "msg": "bad<x>", "type": "missing"},
            ]
        },
    }
    exc = _make_status_error(422, body)
    result = format_http_error(exc)
    assert "&lt;script&gt;" in result
    assert "<script>" not in result
    assert "bad&lt;x&gt;" in result


# ---------------------------------------------------------------------------
# AC-5 / AC-7: idempotency-collision renderer
# ---------------------------------------------------------------------------


def test_format_http_error_routes_idempotency_collision_with_extensions_task_id() -> None:
    """AC-7: prefer ``extensions.task_id`` over top-level ``task_id``."""
    body = {
        "type": "/errors/idempotency-collision",
        "status": 409,
        "extensions": {"task_id": "t-from-ext"},
    }
    exc = _make_status_error(409, body)
    result = format_http_error(exc)
    assert "Duplicate idempotency key" in result
    assert "t-from-ext" in result


def test_format_http_error_routes_idempotency_collision_with_top_level_task_id() -> None:
    """AC-7: back-compat — top-level ``task_id`` still renders when extensions absent."""
    body = {
        "type": "/errors/idempotency-collision",
        "status": 409,
        "task_id": "t-top-level",
    }
    exc = _make_status_error(409, body)
    result = format_http_error(exc)
    assert "Duplicate idempotency key" in result
    assert "t-top-level" in result


# ---------------------------------------------------------------------------
# AC-5 / AC-8: not-found renderer
# ---------------------------------------------------------------------------


def test_format_http_error_routes_not_found() -> None:
    """AC-8: 404 with task-id-shaped detail surfaces ``Task t-<id> not found.``."""
    task_id = "t-01234567-89ab-7cde-8123-456789abcdef"
    body = {
        "type": "/errors/not-found",
        "status": 404,
        "detail": f"Task {task_id} does not exist",
    }
    exc = _make_status_error(404, body)
    result = format_http_error(exc)
    assert result == f"⚠️ Task {task_id} not found."


# ---------------------------------------------------------------------------
# AC-5 / AC-9: rate-limited renderer
# ---------------------------------------------------------------------------


def test_format_http_error_routes_rate_limited_with_retry_after_seconds() -> None:
    """AC-9: ``extensions.retry_after_seconds`` populates the ``Retry in Ns.`` hint."""
    body = {
        "type": "/errors/rate-limited",
        "status": 429,
        "detail": "Webhook rate limit exceeded; retry after refill.",
        "extensions": {"retry_after_seconds": 1},
    }
    exc = _make_status_error(429, body)
    result = format_http_error(exc)
    assert result == "⚠️ Rate limit exceeded. Retry in 1s."


def test_format_http_error_routes_rate_limited_without_retry_after_seconds() -> None:
    """AC-9 fallback: missing ``retry_after_seconds`` → generic ``Retry shortly.``."""
    body = {
        "type": "/errors/rate-limited",
        "status": 429,
        "detail": "Webhook rate limit exceeded; retry after refill.",
    }
    exc = _make_status_error(429, body)
    result = format_http_error(exc)
    assert result == "⚠️ Rate limit exceeded. Retry shortly."


# ---------------------------------------------------------------------------
# AC-5 / AC-10: internal-error renderer
# ---------------------------------------------------------------------------


def test_format_http_error_routes_internal_error() -> None:
    """AC-10: 500 with ``type=/errors/internal`` returns the fixed message."""
    body = {
        "type": "/errors/internal",
        "status": 500,
        "detail": "An internal error occurred. The error has been logged for investigation.",
    }
    exc = _make_status_error(500, body)
    result = format_http_error(exc)
    assert result == "⚠️ Internal error. Logs captured."


# ---------------------------------------------------------------------------
# AC-5: fallback to legacy status path
# ---------------------------------------------------------------------------


def test_format_http_error_falls_back_to_legacy_status_when_type_unknown() -> None:
    """AC-5 fallback: ``type=about:blank`` or unknown slug → status-code branches.

    Asserts the existing 422 string-detail path is preserved when the envelope
    does not carry a known catalog slug — back-compat for endpoints that
    haven't migrated.
    """
    # ``about:blank`` → legacy
    body_blank: dict[str, Any] = {
        "type": "about:blank",
        "status": 422,
        "detail": "title too long",
    }
    exc_blank = _make_status_error(422, body_blank)
    assert format_http_error(exc_blank) == "⚠️ Task rejected: title too long"

    # Unknown slug → legacy
    body_unknown: dict[str, Any] = {
        "type": "/errors/something-new",
        "status": 422,
        "detail": "title too long",
    }
    exc_unknown = _make_status_error(422, body_unknown)
    assert format_http_error(exc_unknown) == "⚠️ Task rejected: title too long"


# ---------------------------------------------------------------------------
# Story 3.5 H2 carry-forward + back-compat
# ---------------------------------------------------------------------------


def test_format_http_error_preserves_command_label_verbs_per_problem_type() -> None:
    """Story 3.5 H2: non-default ``command_label`` produces ``failed`` not ``rejected``.

    Validation, idempotency-collision, and not-found renderers all use the
    verb-aware label; rate-limited and internal renderers are label-agnostic
    by design (their messages are fixed-shape).
    """
    # Validation
    val_body = {
        "type": "/errors/validation",
        "status": 422,
        "extensions": {"errors": [{"loc": ["body", "x"], "msg": "y", "type": "missing"}]},
    }
    val_exc = _make_status_error(422, val_body)
    assert "Health check failed" in format_http_error(val_exc, command_label="Health check")
    assert "Health check rejected" not in format_http_error(val_exc, command_label="Health check")

    # Not-found (no task-id in detail → uses the generic ``{label} not found.``)
    nf_body = {"type": "/errors/not-found", "status": 404, "detail": "no match"}
    nf_exc = _make_status_error(404, nf_body)
    assert format_http_error(nf_exc, command_label="Health check") == "⚠️ Health check not found."


def test_format_http_error_5xx_unchanged() -> None:
    """Back-compat: 5xx (502/503/504) returns the existing ``Registry unavailable`` string."""
    for status in (502, 503, 504):
        exc = _make_status_error(status, "")
        result = format_http_error(exc)
        assert result == f"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."
