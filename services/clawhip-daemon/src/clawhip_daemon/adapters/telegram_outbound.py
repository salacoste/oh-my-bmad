"""TelegramOutbound — direct httpx client for Telegram Bot API ``sendMessage``.

Story 3.9 AC-6 / AC-16: clawhip-daemon delivers outbound progress events to
Telegram via a direct ``httpx`` call to
``https://api.telegram.org/bot<TOKEN>/sendMessage``.  No ``aiogram`` in this
service (rationale: adding aiogram for one method imports ~30 MB of dispatcher
framework; 20 LoC of direct httpx is cleaner — architecture.md:846 / AC-16).

Retry policy (architecture.md:856 LOCK):
    ``tenacity.AsyncRetrying`` — 3 attempts, exponential backoff with jitter,
    ``wait_exponential(multiplier=0.5, max=8)``.  Retries on:
    * HTTP 429 (Too Many Requests)
    * HTTP 5xx (server errors)
    * ``httpx.TransportError`` (network failures, timeouts)

Terminal failure (all 3 attempts exhausted):
    Logs an ``ERROR`` via structlog and returns — NEVER raises to the caller.
    A ``sink.delivery_failed`` event would normally be emitted via
    ``clawhip-bridge`` MCP (architecture.md:846) but that channel is not yet
    wired in Phase 1 (no EventLogWriter in clawhip-daemon; clawhip-daemon is
    not the single writer).  Deviation from AC-6 is documented in completion
    notes: ``emit`` is accepted as a parameter but defaults to ``None``; callers
    that eventually wire the bridge pass their callback.

Bot-token cache-once (Story 2.16 carry-forward):
    The constructor reads ``AuditedSecret.value`` EXACTLY ONCE and stores the
    plain-text token.  Per-call reads would saturate the audit trail with
    ``secret.accessed`` events on every outbound message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import structlog
import tenacity
from secret_hygiene.audited_secret import AuditedSecret
from tenacity import stop_after_attempt, wait_exponential
from tenacity.wait import wait_random

_log = structlog.get_logger("clawhip_daemon.adapters.telegram_outbound")

# Jitter ceiling (seconds) added on top of wait_exponential. Avoids
# thundering-herd on transient 429 bursts.
_JITTER_MAX_S: float = 1.0

# Telegram API base URL — extracted as a module constant so tests can
# override it without patching the constructor.
_TELEGRAM_BASE_URL = "https://api.telegram.org"


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors that warrant a retry attempt.

    Retries on:
    * ``httpx.TransportError`` — network/timeout errors (DNS, TCP reset, etc.)
    * ``httpx.HTTPStatusError`` with status 429 or 5xx — rate-limit / server
      errors.  4xx other than 429 are permanent (misconfigured payload) and
      should NOT be retried.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        sc = exc.response.status_code
        return sc == 429 or sc >= 500
    return False


class TelegramOutbound:
    """Async Telegram Bot API ``sendMessage`` adapter with 3× exp-backoff retry.

    Instantiate once at service startup; reuse across all ``send_to_thread``
    calls (the underlying ``http_client`` holds the TLS session).

    Args:
        bot_token:   ``AuditedSecret`` wrapping ``TELEGRAM_BOT_TOKEN``.  The
                     constructor reads ``.value`` once and discards the wrapper
                     so subsequent calls do not emit audit events per-message.
        http_client: Lifespan-owned ``httpx.AsyncClient``.  Must NOT be
                     constructed per-call — TLS session reuse requires a
                     long-lived client.
        emit:        Optional async callable invoked on terminal failure to
                     emit a ``sink.delivery_failed`` event.  Defaults to
                     ``None`` (no-op) for Phase 1 because clawhip-daemon does
                     not yet have an ``EventLogWriter`` (single-writer rule —
                     architecture.md:846).
    """

    def __init__(
        self,
        *,
        bot_token: AuditedSecret,
        http_client: httpx.AsyncClient,
        emit: Callable[[object], Awaitable[None]] | None = None,
    ) -> None:
        # Cache-once: read AuditedSecret.value exactly once at construction.
        # Storing the raw string avoids one secret.accessed audit event per
        # outbound message (Story 2.16 carry-forward).
        self._token: str = bot_token.value
        self._http_client = http_client
        self._emit = emit

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
                                 ``parse_mode="HTML"`` so the bot honours
                                 Story 3.1's HTML-mode default.

        Returns:
            ``None`` always — terminal failures are logged + optionally
            emitted as ``sink.delivery_failed`` events; they are NEVER
            re-raised to the caller.
        """
        url = f"{_TELEGRAM_BASE_URL}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "reply_to_message_id": reply_to_message_id,
            "text": text,
            "parse_mode": "HTML",
        }

        last_exc: BaseException | None = None
        try:
            async for attempt in tenacity.AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, max=8) + wait_random(0, _JITTER_MAX_S),
                retry=tenacity.retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    response = await self._http_client.post(url, json=payload)
                    response.raise_for_status()
        except BaseException as exc:
            last_exc = exc
            _log.error(
                "telegram sendMessage terminal failure — all retries exhausted",
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            # Best-effort sink.delivery_failed emission (AC-6 deviation note:
            # wiring the real emit callback is deferred until clawhip-bridge
            # MCP is available; for Phase 1 pass emit=None at construction).
            if self._emit is not None:
                try:
                    await self._emit(
                        {
                            "sink_name": "telegram",
                            "consecutive_failures": 1,
                            "last_error": _safe_truncate(str(exc), 4096),
                        }
                    )
                except Exception:  # noqa: BLE001 — emit failure must not surface
                    _log.warning(
                        "sink.delivery_failed emit itself failed; swallowed",
                        exc_type=type(last_exc).__name__ if last_exc else "unknown",
                    )


def _safe_truncate(s: str, max_chars: int) -> str:
    """Truncate *s* to *max_chars* characters (for SinkDeliveryFailedPayload.last_error)."""
    return s[:max_chars] if len(s) > max_chars else s


__all__ = ["TelegramOutbound"]
