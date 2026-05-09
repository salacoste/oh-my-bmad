"""Centralized RFC 7807 error rendering for console-cli commands (Story 4.5)."""

from __future__ import annotations

import json
import sys
from typing import NoReturn

import httpx

EXIT_ERROR = 1
EXIT_VALIDATION = 2
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5

_STATUS_TO_EXIT: dict[int, int] = {
    422: EXIT_VALIDATION,
    404: EXIT_NOT_FOUND,
    409: EXIT_CONFLICT,
}


def exit_code_for_status(status_code: int) -> int:
    return _STATUS_TO_EXIT.get(status_code, EXIT_ERROR)


def _format_value(value: object) -> str:
    """Format an extension value for display — primitive types as-is, nested via JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, (int, float)) or value is None:
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def render_http_error(exc: httpx.HTTPStatusError) -> NoReturn:
    """Render RFC 7807 error to stderr and exit with mapped code."""
    code = exit_code_for_status(exc.response.status_code)
    title: str | None = None
    detail: str | None = None
    extensions: dict[str, object] | None = None

    try:
        raw: object = exc.response.json()
    except (ValueError, UnicodeDecodeError):
        raw = None

    if isinstance(raw, dict):
        body: dict[str, object] = raw
        candidate_title = body.get("title")
        if isinstance(candidate_title, str) and candidate_title:
            title = candidate_title
        candidate_detail = body.get("detail")
        if isinstance(candidate_detail, str) and candidate_detail:
            detail = candidate_detail
        candidate_ext = body.get("extensions")
        if isinstance(candidate_ext, dict):
            extensions = candidate_ext

    if title is None and detail is None:
        title = f"HTTP {exc.response.status_code}"
        detail = (
            exc.response.content[:800].decode(errors="replace").strip()[:200]
            or exc.response.reason_phrase
        )

    parts = [f"Error: {title or f'HTTP {exc.response.status_code}'}"]
    if detail:
        parts[0] += f" -- {detail}"
    if extensions:
        for key, value in extensions.items():
            parts.append(f"  {key}: {_format_value(value)}")

    print("\n".join(parts), file=sys.stderr)
    raise SystemExit(code) from None
