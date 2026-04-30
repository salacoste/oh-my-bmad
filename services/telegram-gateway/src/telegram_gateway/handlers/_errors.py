"""Shared HTTP-error formatting for all telegram-gateway command handlers.

Story 3.4 M4 promoted ``format_http_error`` here from ``task_command.py`` so
``approve_command.py`` / ``ping_command.py`` / future decision-command
handlers can share a single renderer.

Story 3.7 refactor: dispatch by RFC 7807 ``type`` slug rather than HTTP
status code. Each problem class has a dedicated private ``_format_*`` helper
≤15 LoC; legacy status-based fallback preserves back-compat for endpoints
that still ship ``type=about:blank`` (or unknown slugs).

Slug duplication
----------------
The five problem-type slug constants are deliberately duplicated from
``services/registry-api/src/registry_api/adapters/errors.py`` (Story 3.7
AC-1 alternative). A contract test in ``app/test_rate_limit.py`` pins all
five slugs against the registry-api catalog by source-text inspection
(Story 3.7 review H2). This avoids the cross-service import noqa
proliferation flagged by Story 3.6 review N7 — each service owns its own
constants and the contract is tested at the wire level.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

# Story 3.7 AC-1 alternative: duplicate slug constants on the gateway side.
# Pinned to registry-api's catalog by contract test (Story 3.7 AC-4 in
# ``app/test_rate_limit.py``); review H2 parametrizes the contract across
# all five slugs.
_PROBLEM_TYPE_VALIDATION = "/errors/validation"
_PROBLEM_TYPE_NOT_FOUND = "/errors/not-found"
_PROBLEM_TYPE_IDEMPOTENCY_COLLISION = "/errors/idempotency-collision"
_PROBLEM_TYPE_RATE_LIMITED = "/errors/rate-limited"
_PROBLEM_TYPE_INTERNAL = "/errors/internal"
_PROBLEM_TYPE_DEFAULT = "about:blank"

# Story 3.7 AC-10: shared with Story 3.3 H2 backstop. Existing ``"⚠️ Internal
# error. Logs captured."`` strings hardcoded in ``task_command.py`` /
# ``approve_command.py`` / ``ping_command.py`` are intentionally left in place
# for back-compat — the renderer reuses this constant so any future drift is
# centralized here. Pinned by a contract test (Story 3.7 review L16) that
# greps the literal in those three call sites.
_INTERNAL_ERROR_MESSAGE = "⚠️ Internal error. Logs captured."

# Story 3.7 AC-6: cap validation field-list bullets so a 200-field validation
# error cannot blow Telegram's 4096-char message limit.
_VALIDATION_FIELD_CAP = 5

# Story 3.7 review H3: per-bullet truncation defends against a single
# pattern_mismatch error with a 4000-char regex echoed in ``msg``.
_VALIDATION_BULLET_MAX_CHARS = 200

# Story 3.7 review H3: total-message safety cap. Telegram's sendMessage limit
# is 4096 chars; we leave 500 chars headroom for prefix + suffix + truncation
# notice.
_VALIDATION_MESSAGE_MAX_CHARS = 3500

# Story 3.7 AC-8: detect a task-id-shaped substring in a not-found ``detail``
# string so the renderer can surface ``Task t-<id> not found.`` when the
# registry hands back a specific id. ``\b`` word boundaries (review M7)
# avoid matching mid-token in arbitrary detail prose.
_TASK_ID_PATTERN = re.compile(
    r"\bt-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)

_log = logging.getLogger("telegram_gateway.handlers._errors")

__all__ = ["_INTERNAL_ERROR_MESSAGE", "format_http_error"]


def format_http_error(
    exc: httpx.HTTPStatusError,
    *,
    command_label: str = "Task",
) -> str:
    """Surface RFC 7807 error details as a human-readable Telegram reply.

    Args:
        exc:           The ``httpx.HTTPStatusError`` to format.
        command_label: Human-readable label for the failing operation used in
                       the reply prefix.  Defaults to ``"Task"`` (preserves
                       existing ``/task`` and ``/approve`` behaviour).
                       Pass ``"Health check"`` for ``/ping`` 4xx replies so the
                       message reads ``"⚠️ Health check failed: …"`` rather than
                       the semantically wrong ``"⚠️ Task rejected: …"``.

    Story 3.7 dispatch: parse the envelope once, route by ``type`` slug to a
    dedicated renderer. Unknown slugs (or ``about:blank``) fall through to
    the legacy status-code-based path for back-compat (review L13: legacy
    fallback handles 401/403/4xx/5xx for envelopes without a recognized
    ``type`` slug).

    Note (review L14): ``_format_rate_limited`` and ``_format_internal_error``
    are label-agnostic by design (AC-9, AC-10) — the rendered messages do
    not include ``command_label``. All other renderers honor the label/verb.
    """
    status = exc.response.status_code
    try:
        body = exc.response.json()
        if not isinstance(body, dict):
            # Story 3.7 review L2: log non-dict bodies for observability so
            # malformed upstream envelopes are not silently swallowed.
            _log.warning(
                "non-dict response body, falling back to legacy status path",
                extra={"body_type": type(body).__name__, "status": status},
            )
            body = {}
        problem_type = body.get("type", _PROBLEM_TYPE_DEFAULT)
        # Story 3.7 review L3: type-guard — non-string ``type`` falls through
        # to the legacy status path rather than equality-comparing to slugs.
        if not isinstance(problem_type, str):
            problem_type = _PROBLEM_TYPE_DEFAULT
        detail = body.get("detail")
        extensions = body.get("extensions") or {}
        if not isinstance(extensions, dict):
            extensions = {}
    except Exception:  # noqa: BLE001 — body may not be JSON (e.g., proxy 413)
        body = {}
        problem_type = _PROBLEM_TYPE_DEFAULT
        detail = None
        extensions = {}

    if problem_type == _PROBLEM_TYPE_VALIDATION:
        return _format_validation_error(extensions, detail, command_label)
    if problem_type == _PROBLEM_TYPE_IDEMPOTENCY_COLLISION:
        return _format_idempotency_collision(body, command_label)
    if problem_type == _PROBLEM_TYPE_NOT_FOUND:
        return _format_not_found(detail, extensions, command_label)
    if problem_type == _PROBLEM_TYPE_RATE_LIMITED:
        return _format_rate_limited(detail, extensions)
    if problem_type == _PROBLEM_TYPE_INTERNAL:
        return _format_internal_error()

    # Fallback: status-code-based legacy path. The 409 branch reuses
    # ``_format_idempotency_collision`` so back-compat behaviour (rendering a
    # top-level ``task_id`` from envelopes that don't ship the slug yet) is
    # preserved without duplicating the renderer.
    if status == 409:
        return _format_idempotency_collision(body, command_label)
    return _format_legacy_status(status, detail, command_label)


def _verb(command_label: str) -> str:
    """Story 3.5 H2: ``"Task" → "rejected"``; non-default → ``"failed"``."""
    return "rejected" if command_label == "Task" else "failed"


def _format_validation_error(
    extensions: dict[str, Any],
    detail: Any,
    command_label: str,
) -> str:
    """AC-6: bullet list of per-field errors; cap at 5 fields with ``… and N more``.

    Story 3.7 review M1: ``command_label`` is HTML-escaped uniformly across
    renderers. Review H4: reads from ``extensions["validation_errors"]``
    (renamed from ``"errors"`` to avoid future namespace collisions).
    Review H3: bullets are truncated at 200 chars and the total message at
    3500 chars to avoid Telegram's 4096-char sendMessage limit.
    Review M2: when all entries are non-dict (filtered out), falls through
    to the flat ``detail`` rendering. Overflow count uses ``len(bullets)``
    so skipped non-dicts count toward "more".
    Review M5: multi-line ``msg`` whitespace collapsed to single spaces.
    """
    verb = _verb(command_label)
    label_safe = html.escape(command_label)
    errors = extensions.get("validation_errors")
    if not isinstance(errors, list) or not errors:
        # Fallback: flat ``detail`` rendering when structured list is absent.
        if detail is not None:
            return f"⚠️ {label_safe} {verb}: {html.escape(str(detail))}"
        return f"⚠️ {label_safe} {verb}: invalid request"
    head = errors[:_VALIDATION_FIELD_CAP]
    bullets: list[str] = []
    for entry in head:
        if not isinstance(entry, dict):
            continue
        loc_raw = entry.get("loc") or []
        # Review M6: accept tuple OR list (Pydantic returns tuple natively).
        if isinstance(loc_raw, (list, tuple)):
            loc_parts = [html.escape(str(p)) for p in loc_raw]
        else:
            loc_parts = []
        # Review M5: ``.split()`` (no args) collapses any whitespace
        # including ``\n``/``\t``/multiple spaces into single spaces so a
        # multi-line ``msg`` does not visually merge with subsequent bullets.
        msg = " ".join(html.escape(str(entry.get("msg", ""))).split())
        bullet = f"• {' → '.join(loc_parts)}: {msg}"
        # Review H3: per-bullet truncation guards against a single huge
        # ``msg`` blowing the message limit.
        if len(bullet) > _VALIDATION_BULLET_MAX_CHARS:
            bullet = bullet[: _VALIDATION_BULLET_MAX_CHARS - 1] + "…"
        bullets.append(bullet)
    if not bullets:
        # Review M2: all head entries were non-dict; fall through to flat detail.
        if detail is not None:
            return f"⚠️ {label_safe} {verb}: {html.escape(str(detail))}"
        return f"⚠️ {label_safe} {verb}: invalid request"
    # Review M2: overflow counts skipped non-dicts toward "more".
    overflow = max(0, len(errors) - len(bullets))
    suffix = f"\n… and {overflow} more" if overflow > 0 else ""
    result = f"⚠️ {label_safe} {verb}: invalid request\n" + "\n".join(bullets) + suffix
    # Review H3: total-message safety cap.
    if len(result) > _VALIDATION_MESSAGE_MAX_CHARS:
        result = (
            result[:_VALIDATION_MESSAGE_MAX_CHARS] + "\n… (output truncated to fit message limit)"
        )
    return result


def _format_idempotency_collision(body: dict[str, Any], command_label: str) -> str:
    """AC-7: prefer ``extensions.task_id``; fall back to top-level ``task_id``.

    Story 3.7 review M3: ``command_label`` is wired into the prefix so a
    ``/ping`` 409 reads ``"⚠️ Health check failed: Duplicate idempotency
    key — …"``. Review M8: type-guards ensure non-string ``task_id`` values
    fall through to the no-task-id branch; a debug log fires when both
    extensions and body carry ``task_id`` and they disagree.
    """
    verb = _verb(command_label)
    label_safe = html.escape(command_label)
    extensions = body.get("extensions") or {}
    ext_task_id = ""
    if isinstance(extensions, dict):
        candidate = extensions.get("task_id", "")
        if isinstance(candidate, str):
            ext_task_id = candidate
    body_task_id = ""
    body_candidate = body.get("task_id", "")
    if isinstance(body_candidate, str):
        body_task_id = body_candidate
    # Review M8: precedence is extensions first, body second; log when both
    # are present and disagree so operators can debug spec drift.
    if ext_task_id and body_task_id and ext_task_id != body_task_id:
        _log.debug(
            "idempotency-collision: extensions.task_id and body.task_id disagree",
            extra={"ext_task_id": ext_task_id, "body_task_id": body_task_id},
        )
    task_id_raw = ext_task_id or body_task_id
    if task_id_raw:
        task_id_safe = html.escape(task_id_raw)
        return (
            f"⚠️ {label_safe} {verb}: Duplicate idempotency key — "
            f"another instance already submitted this message. "
            f"Stored result: {task_id_safe}."
        )
    return (
        f"⚠️ {label_safe} {verb}: Duplicate idempotency key — "
        f"another instance already submitted this message."
    )


def _format_not_found(
    detail: Any,
    extensions: dict[str, Any],
    command_label: str,
) -> str:
    """AC-8: surface a task-id from ``extensions`` (preferred) or ``detail``.

    Story 3.7 review M7: prefer ``extensions.task_id`` (parity with the
    idempotency-collision path) so a detail like ``"Cannot find dependency
    t-aaa for task t-bbb"`` does not surface the wrong id. Falls back to a
    bounded regex on ``detail`` when ``extensions.task_id`` is absent.
    Review L6: the regex confines matches to hex chars + dashes so
    ``html.escape`` on the matched task-id is a no-op (dropped).
    """
    # Review M7: prefer extensions.task_id (parity with idempotency-collision).
    task_id_safe = ""
    if isinstance(extensions, dict):
        candidate = extensions.get("task_id", "")
        if isinstance(candidate, str) and _TASK_ID_PATTERN.fullmatch(candidate):
            task_id_safe = candidate
    if not task_id_safe and detail is not None:
        match = _TASK_ID_PATTERN.search(str(detail))
        if match is not None:
            # Regex confines to hex chars + dashes; html.escape would be a no-op.
            task_id_safe = match.group(0)
    if task_id_safe:
        return f"⚠️ {html.escape(command_label)} {_verb(command_label)}: {task_id_safe} not found."
    return f"⚠️ {html.escape(command_label)} not found."


def _format_rate_limited(detail: Any, extensions: dict[str, Any]) -> str:
    """AC-9: surface ``extensions.retry_after_seconds`` when present.

    Story 3.7 review M4: type-guard ``retry_after_seconds`` so non-numeric
    or non-positive values fall through to the generic ``Retry shortly.``
    message rather than rendering nonsense like ``Retry in [1, 2, 3]s.``.
    """
    retry_after = extensions.get("retry_after_seconds")
    # Reject ``bool`` (subclass of int) and only accept positive numerics.
    if (
        isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and retry_after > 0
    ):
        return f"⚠️ Rate limit exceeded. Retry in {retry_after}s."
    # detail intentionally unused per AC-9 — generic message is the spec output.
    _ = detail
    return "⚠️ Rate limit exceeded. Retry shortly."


def _format_internal_error() -> str:
    """AC-10: shared with Story 3.3 H2 backstop."""
    return _INTERNAL_ERROR_MESSAGE


def _format_legacy_status(status: int, detail: Any, command_label: str) -> str:
    """Back-compat: status-code-based path for ``about:blank`` / unknown slugs.

    Preserves the pre-Story-3.7 behaviour for endpoints that still ship
    ``type=about:blank`` (or any unknown slug) so existing tests / handlers
    that don't migrate to the catalog continue to render as before.

    Story 3.7 review M1: ``command_label`` is HTML-escaped uniformly with
    the dispatched renderers. Review L1: explicit empty-string guard on
    ``detail_str`` so a 422 with a list of non-dicts (e.g. ``[123, "x"]``)
    falls back to the ``HTTP <status>`` shape rather than a trailing colon.
    """
    if status in (401, 403):
        return "⚠️ Not authorized. Contact your administrator."

    if 400 <= status < 500:
        verb = _verb(command_label)
        label_safe = html.escape(command_label)
        if detail is not None:
            # FastAPI 422 returns detail as a list of dicts:
            # ``[{"loc": [...], "msg": "..."}]``.
            if isinstance(detail, list):
                msgs = [d.get("msg", "") for d in detail if isinstance(d, dict)]
                detail_str = "; ".join(m for m in msgs if m) or str(detail)
            else:
                detail_str = str(detail)
            # Review L1: safety guard — empty detail_str falls back to HTTP <status>.
            if not detail_str:
                detail_str = f"HTTP {status}"
            return f"⚠️ {label_safe} {verb}: {html.escape(detail_str)}"
        return f"⚠️ {label_safe} {verb}: HTTP {status}"

    # 5xx — transient registry error.
    return f"⚠️ Registry unavailable: HTTP {status}. Retry in a moment."
