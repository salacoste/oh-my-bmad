"""Tests for RFC 7807 error rendering (Story 4.5)."""

from __future__ import annotations

import httpx
import pytest

from console_cli.adapters.error_renderer import (
    EXIT_CONFLICT,
    EXIT_ERROR,
    EXIT_NOT_FOUND,
    EXIT_VALIDATION,
    _format_value,
    exit_code_for_status,
    render_http_error,
)


def _make_http_error(
    status: int,
    json_body: dict[str, object] | None = None,
    text: str = "",
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://registry-api:8080/v1/tasks/t-test")
    response = httpx.Response(
        status,
        json=json_body,
        text=text if json_body is None else None,
        request=request,
    )
    return httpx.HTTPStatusError(
        message=f"HTTP {status}",
        request=request,
        response=response,
    )


# --- exit_code_for_status ---


def test_exit_code_422() -> None:
    assert exit_code_for_status(422) == EXIT_VALIDATION


def test_exit_code_404() -> None:
    assert exit_code_for_status(404) == EXIT_NOT_FOUND


def test_exit_code_409() -> None:
    assert exit_code_for_status(409) == EXIT_CONFLICT


def test_exit_code_400() -> None:
    assert exit_code_for_status(400) == EXIT_ERROR


def test_exit_code_500() -> None:
    assert exit_code_for_status(500) == EXIT_ERROR


def test_exit_code_503() -> None:
    assert exit_code_for_status(503) == EXIT_ERROR


# --- render_http_error ---


def test_render_rfc7807_full() -> None:
    exc = _make_http_error(
        409,
        json_body={
            "type": "/errors/idempotency-collision",
            "title": "Duplicate idempotency key",
            "status": 409,
            "detail": "A decision with this key was already recorded.",
            "instance": "/v1/tasks",
            "extensions": {
                "task_id": "t-0192a1b5-1234",
                "idempotency_key": "ik-abc",
            },
        },
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_CONFLICT


def test_render_rfc7807_no_extensions() -> None:
    exc = _make_http_error(
        422,
        json_body={
            "type": "/errors/validation",
            "title": "Validation failed",
            "detail": "title is required",
        },
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_VALIDATION


def test_render_rfc7807_title_only() -> None:
    exc = _make_http_error(
        404,
        json_body={
            "type": "/errors/not-found",
            "title": "Not Found",
        },
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_NOT_FOUND


def test_render_plain_text_fallback() -> None:
    exc = _make_http_error(500, text="Internal Server Error")
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_ERROR


def test_render_non_json_fallback() -> None:
    request = httpx.Request("GET", "http://registry-api:8080/v1/tasks/t-test")
    response = httpx.Response(
        500,
        content=b"<html>error</html>",
        headers={"content-type": "text/html"},
        request=request,
    )
    exc = httpx.HTTPStatusError(
        message="HTTP 500",
        request=request,
        response=response,
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_ERROR


def test_render_stderr_output(capsys: pytest.CaptureFixture[str]) -> None:
    exc = _make_http_error(
        422,
        json_body={
            "type": "/errors/validation",
            "title": "Validation failed",
            "detail": "title is required",
            "extensions": {"field": "title"},
        },
    )
    with pytest.raises(SystemExit):
        render_http_error(exc)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: Validation failed -- title is required" in captured.err
    assert "field: title" in captured.err


def test_render_stdout_empty_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    exc = _make_http_error(404, json_body={"title": "Not Found", "detail": "task not found"})
    with pytest.raises(SystemExit):
        render_http_error(exc)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error:" in captured.err


def test_render_list_body_falls_back(capsys: pytest.CaptureFixture[str]) -> None:
    """CRITICAL: non-dict JSON body (list) must not crash — falls back to HTTP status."""
    request = httpx.Request("GET", "http://registry-api:8080/v1/tasks/t-test")
    response = httpx.Response(
        500,
        json=["error", "details"],
        request=request,
    )
    exc = httpx.HTTPStatusError(
        message="HTTP 500",
        request=request,
        response=response,
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_ERROR
    captured = capsys.readouterr()
    assert "Error: HTTP 500" in captured.err


def test_render_empty_string_title_falls_back(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty-string title treated as absent — falls back to HTTP status."""
    exc = _make_http_error(
        422,
        json_body={"title": "", "detail": ""},
    )
    with pytest.raises(SystemExit) as info:
        render_http_error(exc)
    assert info.value.code == EXIT_VALIDATION
    captured = capsys.readouterr()
    assert "Error: HTTP 422" in captured.err


def test_render_nested_extensions_formatted_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Nested extension values render as JSON, not Python repr."""
    exc = _make_http_error(
        409,
        json_body={
            "title": "Conflict",
            "detail": "State conflict",
            "extensions": {
                "task_id": "t-abc",
                "context": {"step": 3, "reason": "locked"},
            },
        },
    )
    with pytest.raises(SystemExit):
        render_http_error(exc)
    captured = capsys.readouterr()
    assert 'context: {"step": 3, "reason": "locked"}' in captured.err
    assert "task_id: t-abc" in captured.err


def test_format_value_non_serializable() -> None:
    """Non-JSON-serializable values (set, bytes) fall back to repr."""
    result = _format_value({1, 2})
    assert "{" in result and "1" in result


def test_format_value_str() -> None:
    assert _format_value("hello") == "hello"


def test_format_value_int() -> None:
    assert _format_value(42) == "42"


def test_format_value_none() -> None:
    assert _format_value(None) == "None"


def test_format_value_bool_json_style() -> None:
    """Bool renders as JSON true/false, not Python True/False."""
    assert _format_value(True) == "true"
    assert _format_value(False) == "false"
