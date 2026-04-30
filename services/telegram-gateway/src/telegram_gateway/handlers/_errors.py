"""Shared HTTP-error formatting for all telegram-gateway command handlers (Story 3.4 M4).

Extracted from ``task_command.py`` so that ``approve_command.py`` and future
decision-command handlers (3.16, 3.17, 3.18) can import ``format_http_error``
without coupling to a peer handler's private internals.

Previously ``_format_http_error`` lived in ``task_command.py`` with a leading
underscore.  The leading underscore is dropped here because the function is now
part of a dedicated shared module — it is intentionally public within the
``handlers`` package.
"""

from __future__ import annotations

import html

import httpx

__all__ = ["format_http_error"]


def format_http_error(
    exc: httpx.HTTPStatusError,
    *,
    command_label: str = "Task",
) -> str:
    """Surface RFC 7807 error details as a human-readable Telegram reply.

    Args:
        exc:           The ``httpx.HTTPStatusError`` to format.
        command_label: Human-readable label for the failing operation used in
                       the 4xx reply prefix.  Defaults to ``"Task"`` (preserves
                       existing ``/task`` and ``/approve`` behaviour).
                       Pass ``"Health check"`` for ``/ping`` 4xx replies so the
                       message reads ``"⚠️ Health check failed: …"`` rather than
                       the semantically wrong ``"⚠️ Task rejected: …"``.

    Differentiates:
    - 401/403: authorization error → fixed human-readable message (M2).
    - 409: idempotency collision from a concurrent bot instance.
    - 4xx other: validation / Pydantic error; parse RFC 7807 ``detail``.
      When ``detail`` is a list (FastAPI 422 shape), extracts the first
      ``"msg"`` entry.  All interpolated values are HTML-escaped (H5).
    - 5xx: registry unavailable.

    Falls back to ``"⚠️ {command_label} rejected: HTTP {status}"`` (or
    ``"⚠️ {command_label} failed: HTTP {status}"`` for non-default labels)
    when the body is not valid JSON or lacks ``detail``.
    """
    status = exc.response.status_code

    if status in (401, 403):
        return "⚠️ Not authorized. Contact your administrator."

    if status == 409:
        # Concurrent bot instance submitted the same idempotency key via
        # a different path (unusual but possible in multi-replica deploys).
        try:
            body = exc.response.json()
            task_id_raw = body.get("task_id", "")
        except Exception:  # noqa: BLE001 — best-effort body parse
            task_id_raw = ""
        if task_id_raw:
            task_id_safe = html.escape(str(task_id_raw))
            return (
                f"⚠️ Duplicate idempotency key — another instance already submitted "
                f"this message. Stored result: {task_id_safe}."
            )
        return "⚠️ Duplicate idempotency key — another instance already submitted this message."

    if 400 <= status < 500:
        # Parse RFC 7807 / FastAPI validation body for the ``detail`` field.
        try:
            body = exc.response.json()
            detail_raw = body.get("detail")
        except Exception:  # noqa: BLE001 — body may not be JSON (e.g., proxy 413)
            detail_raw = None

        # Verb selection: default label uses "rejected"; custom labels use "failed".
        verb = "rejected" if command_label == "Task" else "failed"

        if detail_raw is not None:
            # FastAPI 422 returns detail as a list of dicts: [{"loc": [...], "msg": "..."}].
            if isinstance(detail_raw, list):
                msgs = [d.get("msg", "") for d in detail_raw if isinstance(d, dict)]
                detail_str = "; ".join(m for m in msgs if m) or str(detail_raw)
            else:
                detail_str = str(detail_raw)
            return f"⚠️ {command_label} {verb}: {html.escape(detail_str)}"
        return f"⚠️ {command_label} {verb}: HTTP {status}"

    # 5xx — transient registry error.
    return f"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."
