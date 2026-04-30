"""TelegramOutbound — direct httpx client for Telegram Bot API ``sendMessage``.

Story 3.9 AC-6 / AC-16: clawhip-daemon delivers outbound progress events to
Telegram via a direct ``httpx`` call to
``https://api.telegram.org/bot<TOKEN>/sendMessage``.  No ``aiogram`` in this
service (rationale: adding aiogram for one method imports ~30 MB of dispatcher
framework; 20 LoC of direct httpx is cleaner — architecture.md:846 / AC-16).

Retry policy (architecture.md:856 LOCK):
    ``tenacity.AsyncRetrying`` — 3 attempts, exponential backoff with jitter,
    ``wait_exponential(multiplier=0.5, max=8)``.  Retries on:
    * HTTP 429 (Too Many Requests) — honouring ``Retry-After`` header (H6)
    * HTTP 5xx (server errors)
    * ``httpx.TransportError`` (network failures, timeouts)

Terminal failure (all 3 attempts exhausted):
    Logs an ``ERROR`` via structlog and returns — NEVER raises to the caller.
    A ``sink.delivery_failed`` event is emitted via the ``emit`` callback
    (H8 scaffold: defaults to structlog WARN + in-memory deque for Phase 1;
    real clawhip-bridge wiring lands in a future story).

Bot-token handling (H1 / Story 2.16 carry-forward):
    ``self._secret: AuditedSecret`` is kept as a field.  ``.value`` is read
    ONCE per ``send_to_thread`` call (not cached at construction) so the audit
    trail records each outbound message access.  The token value is scrubbed
    from all structlog output before logging to prevent URL leaks (H1).
"""

from __future__ import annotations

import os
import re
from collections import deque
from collections.abc import Awaitable, Callable

import httpx
import structlog
import tenacity
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed per AC-16
    SinkDeliveryFailedPayload,
)
from secret_hygiene.audited_secret import AuditedSecret
from tenacity import stop_after_attempt, wait_exponential, wait_random

_log = structlog.get_logger("clawhip_daemon.adapters.telegram_outbound")

# Jitter ceiling (seconds) added on top of wait_exponential. Avoids
# thundering-herd on transient 429 bursts.
_JITTER_MAX_S: float = 1.0

# L11: env-var override for staging/test environments.
_TELEGRAM_BASE_URL = os.environ.get("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

# Telegram message text length cap in bytes (L8/L9).
_TELEGRAM_MAX_TEXT_BYTES = 4096

# L18: token format regex validated at boot.
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

# H8 scaffold: in-memory deque captures sink.delivery_failed payloads until
# clawhip-bridge MCP is wired.  Tests assert on this deque.
_delivery_failed_deque: deque[SinkDeliveryFailedPayload] = deque(maxlen=1000)


class TelegramApiError(Exception):
    """Raised when Telegram returns HTTP 200 with ``ok: false`` (M2).

    Retried when ``error_code == 429`` (rate-limit); terminal otherwise.
    """

    def __init__(self, *, error_code: int | None, description: str | None) -> None:
        self.error_code = error_code
        self.description = description
        super().__init__(f"Telegram ok:false error_code={error_code} description={description!r}")


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors that warrant a retry attempt.

    Retries on:
    * ``httpx.TransportError`` — network/timeout errors (DNS, TCP reset, etc.)
    * ``httpx.HTTPStatusError`` with status 429 or 5xx — rate-limit / server
      errors.  4xx other than 429 are permanent and should NOT be retried.
    * ``TelegramApiError`` with ``error_code == 429`` — ok:false rate-limit.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        sc = exc.response.status_code
        return sc == 429 or sc >= 500
    if isinstance(exc, TelegramApiError):
        return exc.error_code == 429
    return False


def _retry_after_wait(retry_state: tenacity.RetryCallState) -> float:
    """Custom wait callable that honours Telegram's ``Retry-After`` header (H6).

    Parses ``Retry-After`` from the HTTP header first, then falls back to
    ``parameters.retry_after`` in the JSON body, then to exponential+jitter.
    """
    base_wait = wait_exponential(multiplier=0.5, max=8) + wait_random(0, _JITTER_MAX_S)
    if retry_state.outcome is None or not retry_state.outcome.failed:
        return base_wait(retry_state)
    exc = retry_state.outcome.exception()
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        # Prefer Retry-After header value.
        retry_after_header = exc.response.headers.get("Retry-After")
        if retry_after_header is not None:
            try:
                return float(retry_after_header)
            except ValueError:
                pass
        # Fall back to JSON parameters.retry_after.
        try:
            params = exc.response.json().get("parameters", {})
            return float(params.get("retry_after", 1))
        except Exception:  # noqa: BLE001
            pass
    return base_wait(retry_state)


class TelegramOutbound:
    """Async Telegram Bot API ``sendMessage`` adapter with 3× exp-backoff retry.

    Instantiate once at service startup; reuse across all ``send_to_thread``
    calls (the underlying ``http_client`` holds the TLS session).

    Args:
        bot_token:   ``AuditedSecret`` wrapping ``TELEGRAM_BOT_TOKEN``.
                     ``.value`` is read once per send call so the audit trail
                     records each outbound message access (H1 / Story 2.16).
        http_client: Lifespan-owned ``httpx.AsyncClient``.  Must NOT be
                     constructed per-call — TLS session reuse requires a
                     long-lived client.
        emit:        Async callable invoked on terminal failure to emit a
                     ``sink.delivery_failed`` payload.  When ``None`` (default),
                     the H8 scaffold logs via structlog WARN and appends to
                     ``_delivery_failed_deque`` for Phase 1 observability.
    """

    def __init__(
        self,
        *,
        bot_token: AuditedSecret,
        http_client: httpx.AsyncClient,
        emit: Callable[[SinkDeliveryFailedPayload], Awaitable[None]] | None = None,
    ) -> None:
        # H1: keep the AuditedSecret wrapper — read .value per send call.
        self._secret: AuditedSecret = bot_token
        self._http_client = http_client
        self._emit = emit
        # M3: running counter of consecutive terminal failures; resets on success.
        self._consecutive_failures: int = 0

    async def send_to_thread(
        self,
        *,
        chat_id: int,
        reply_to_message_id: int,
        text: str,
    ) -> None:
        """POST ``sendMessage`` to Telegram with 3× exponential-backoff retry.

        Args:
            chat_id:             Telegram chat id (negative for supergroups).
            reply_to_message_id: Message id of the originating ``/task``
                                 message; Telegram threads replies to it.
            text:                Already-HTML-escaped message body.  The
                                 adapter passes it through verbatim and sets
                                 ``parse_mode="HTML"``.

        Returns:
            ``None`` always — terminal failures are logged + emitted as
            ``sink.delivery_failed`` events; they are NEVER re-raised.
        """
        # L8/L9: pre-flight text validation — empty or overlong text is dropped
        # here to save a round-trip on malformed renders.
        if not text:
            _log.warning(
                "telegram sendMessage skipped — empty text",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
            )
            return
        if len(text.encode("utf-8")) > _TELEGRAM_MAX_TEXT_BYTES:
            _log.warning(
                "telegram sendMessage skipped — text exceeds 4096 bytes",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text_bytes=len(text.encode("utf-8")),
            )
            return

        # H1: read token per call (audited); scrub from exception strings below.
        token = self._secret.value
        url = f"{_TELEGRAM_BASE_URL}/bot{token}/sendMessage"
        req_payload = {
            "chat_id": chat_id,
            "reply_to_message_id": reply_to_message_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            async for attempt in tenacity.AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=_retry_after_wait,
                retry=tenacity.retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    response = await self._http_client.post(url, json=req_payload)
                    response.raise_for_status()
                    # M2: Telegram may return HTTP 200 with ok:false.
                    body = response.json()
                    if not body.get("ok", True):
                        raise TelegramApiError(
                            error_code=body.get("error_code"),
                            description=body.get("description"),
                        )
            # Success — reset consecutive failure counter (M3).
            self._consecutive_failures = 0
        except Exception as exc:  # H2: Exception (not BaseException) lets CancelledError propagate
            self._consecutive_failures += 1
            # H1: scrub the token from the exception string before logging.
            exc_str = str(exc).replace(token, "***REDACTED***")
            _log.error(
                "telegram sendMessage terminal failure — all retries exhausted",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                exc_type=type(exc).__name__,
                exc=exc_str,
                consecutive_failures=self._consecutive_failures,
            )
            # H8 / AC-6: emit sink.delivery_failed.
            failed_payload = SinkDeliveryFailedPayload(
                sink_name="telegram",
                consecutive_failures=self._consecutive_failures,
                last_error=_safe_truncate_bytes(exc_str, _TELEGRAM_MAX_TEXT_BYTES),
            )
            if self._emit is not None:
                try:
                    await self._emit(failed_payload)
                except Exception:  # noqa: BLE001 — emit failure must not surface
                    _log.warning(
                        "sink.delivery_failed emit itself failed; swallowed",
                        exc_type=type(exc).__name__,
                    )
            else:
                # H8 scaffold: structlog WARN + deque (Phase 1 stand-in).
                _log.warning(
                    "sink.delivery_failed (scaffold — clawhip-bridge not yet wired)",
                    sink_name="telegram",
                    consecutive_failures=self._consecutive_failures,
                    last_error=failed_payload.last_error,
                )
                _delivery_failed_deque.append(failed_payload)


def _safe_truncate_bytes(s: str, max_bytes: int) -> str:
    """Truncate *s* so its UTF-8 encoding fits within *max_bytes* (L10).

    Character-count truncation is wrong for multi-byte codepoints (a 4-byte
    emoji produces 4× expected bytes).  Encode → slice → decode with
    ``errors="ignore"`` drops any trailing truncated multi-byte sequence.
    """
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


__all__ = ["TelegramApiError", "TelegramOutbound", "_delivery_failed_deque"]
