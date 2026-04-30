"""TelegramSink — event-log subscriber that dispatches outbound Telegram messages.

Story 3.9 AC-7: subscribes to the JSONL event log; for each ``task.*`` event
looks up the Telegram-thread binding from registry-api via HTTP GET, then
calls :class:`~clawhip_daemon.adapters.telegram_outbound.TelegramOutbound`
to deliver a placeholder message to the operator's originating chat thread.

Design notes:

* **HTTP-only lookup** (Story 3.6 review N7): reads ``(chat_id,
  reply_to_message_id)`` via ``GET /v1/tasks/{id}`` — NO direct ORM import
  from ``registry_state``.  The slight latency (~few ms) is acceptable at
  Phase-1 solo-operator scale.

* **Offset-0 startup** (Phase 1 simplification): reads from byte offset 0
  on every restart.  No position file.  Telegram dedupes by content within
  a short window.  Resume-after-restart semantics land in Story 7.x.

* **Placeholder renderer** (AC-7): returns ``f"Task {task_id}: {event_type}"``
  HTML-escaped.  Stories 3.10–3.13 replace this with proper templates; the
  dispatch table (``_render``) is structured for easy per-type extension.

* **JSONL reader** is implemented inline — clawhip-daemon only imports from
  ``packages/events`` (``from_canonical_json``) to stay within the
  import-graph rules (NFR-M1).  The file-tail pattern mirrors
  ``registry_state.app.main._scan_new_envelopes`` but without the
  cross-service import.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
from pathlib import Path
from typing import Any

import httpx
import structlog
from events import EventEnvelope, from_canonical_json
from events.clock import Clock, SystemClock

from clawhip_daemon.adapters.telegram_outbound import TelegramOutbound

_log = structlog.get_logger("clawhip_daemon.adapters.sinks.telegram_sink")

# Poll interval in seconds — 100ms matches registry-state subscriber.
_POLL_INTERVAL_S: float = 0.1


# ---------------------------------------------------------------------------
# JSONL reader helpers (no cross-service imports)
# ---------------------------------------------------------------------------


def _read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]:
    """Read complete ``\\n``-terminated envelopes from *path* starting at *offset*.

    Mirrors ``registry_state.adapters.event_log._read_new_envelopes_since``
    without importing from that service.  Trailing partial lines are NOT
    consumed (offset stays at last complete newline so the next call picks
    them up once complete).

    Returns:
        ``(new_offset, envelopes)``
    """
    if not path.exists():
        return offset, []
    envelopes: list[EventEnvelope] = []
    last_complete_end = offset
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            while True:
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break  # trailing partial line — leave for next poll
                envelopes.append(from_canonical_json(raw.rstrip(b"\r\n")))
                last_complete_end += len(raw)
    except OSError:
        # File disappeared between exists() check and open() (TOCTOU).
        return offset, []
    return last_complete_end, envelopes


async def _scan_all_files(base_dir: Path, offsets: dict[str, int]) -> list[EventEnvelope]:
    """Scan every ``*.jsonl`` in *base_dir* for newly-appended envelopes.

    Iterates in lexicographic (= chronological) order.  Reads are offloaded
    to the thread executor so the asyncio loop stays responsive.
    """
    collected: list[EventEnvelope] = []
    if not base_dir.exists():
        return collected
    for path in sorted(base_dir.glob("*.jsonl")):
        prior = offsets.get(path.name, 0)
        new_offset, envelopes = await asyncio.to_thread(_read_new_envelopes_since, path, prior)
        offsets[path.name] = new_offset
        collected.extend(envelopes)
    return collected


# ---------------------------------------------------------------------------
# Placeholder renderer (Stories 3.10–3.13 replace per-type entries)
# ---------------------------------------------------------------------------


def _render(task_id: str, event_type: str) -> str:
    """Placeholder renderer: ``f"Task {task_id}: {event_type}"`` (HTML-escaped).

    Both fields are HTML-escaped so any future task_id / type string that
    somehow contains ``<`` / ``>`` / ``&`` does not break Telegram's HTML
    parser (Story 3.5 H5 carry-forward).
    """
    return f"Task {html.escape(task_id)}: {html.escape(event_type)}"


# ---------------------------------------------------------------------------
# TelegramSink
# ---------------------------------------------------------------------------


class TelegramSink:
    """Subscriber loop: tail JSONL event log → lookup binding → dispatch via TelegramOutbound.

    Args:
        base_dir:           Root directory containing ``YYYY-MM-DD.jsonl`` event logs.
        registry_api_url:   Base URL for registry-api (e.g. ``http://registry-api:8080``).
        http_client:        Lifespan-owned ``httpx.AsyncClient`` for registry-api lookups.
        outbound:           :class:`TelegramOutbound` for Telegram ``sendMessage`` delivery.
        clock:              Injected clock; reserved for future timed-poll extensions.
        poll_interval_s:    How long to sleep between tail-loop iterations (default 100ms).
    """

    def __init__(
        self,
        *,
        base_dir: Path,
        registry_api_url: str,
        http_client: httpx.AsyncClient,
        outbound: TelegramOutbound,
        clock: Clock | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._base_dir = base_dir
        self._registry_api_url = registry_api_url.rstrip("/")
        self._http_client = http_client
        self._outbound = outbound
        self._clock = clock or SystemClock()
        self._poll_interval_s = poll_interval_s

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Subscribe to the event log and dispatch outbound messages indefinitely.

        Reads from offset 0 on startup (Phase 1 — no position file).  Tails
        ALL ``*.jsonl`` files in date order so events written just before
        UTC-midnight rollover are not missed.

        Args:
            stop_event: Optional ``asyncio.Event``; set it to request a clean
                        shutdown.  Omit (or pass ``None``) in production —
                        ``main()`` installs SIGTERM → ``stop_event.set()``.
        """
        stop = stop_event if stop_event is not None else asyncio.Event()
        offsets: dict[str, int] = {}

        _log.info("telegram_sink started", base_dir=str(self._base_dir))

        while not stop.is_set():
            envelopes = await _scan_all_files(self._base_dir, offsets)
            for envelope in envelopes:
                await self._handle(envelope)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_s)

        _log.info("telegram_sink stopped")

    async def _handle(self, envelope: EventEnvelope) -> None:
        """Process a single envelope: skip non-task events, lookup binding, dispatch."""
        if not envelope.type.startswith("task."):
            return

        # Extract task_id from payload (all task.* payloads carry task_id).
        payload = envelope.payload
        task_id: str | None
        if hasattr(payload, "task_id"):
            raw = getattr(payload, "task_id", None)
            task_id = raw if isinstance(raw, str) else None
        else:
            # Payload may be a dict (from from_canonical_json deserialization
            # of unknown event versions).
            if isinstance(payload, dict):
                v = payload.get("task_id")
                task_id = v if isinstance(v, str) else None
            else:
                task_id = None

        if task_id is None:
            _log.warning(
                "telegram_sink: task.* event missing task_id — skipping",
                event_type=envelope.type,
                event_id=envelope.event_id,
            )
            return

        # HTTP lookup: GET /v1/tasks/{task_id}
        chat_id, reply_to_message_id = await self._lookup_binding(task_id)

        if chat_id is None or reply_to_message_id is None:
            # Pre-3.9 task or non-Telegram task — skip silently.
            return

        text = _render(task_id, envelope.type)
        await self._outbound.send_to_thread(
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            text=text,
        )

    async def _lookup_binding(self, task_id: str) -> tuple[int | None, int | None]:
        """GET /v1/tasks/{task_id} and return (chat_id, reply_to_message_id).

        Returns ``(None, None)`` on any failure (404, network error, etc.).
        """
        try:
            response = await self._http_client.get(f"{self._registry_api_url}/v1/tasks/{task_id}")
            if response.status_code == 404:
                return None, None
            response.raise_for_status()
            data: Any = response.json()
            chat_id_raw = data.get("chat_id")
            reply_raw = data.get("reply_to_message_id")
            chat_id = chat_id_raw if isinstance(chat_id_raw, int) else None
            reply_to = reply_raw if isinstance(reply_raw, int) else None
            return chat_id, reply_to
        except httpx.HTTPError as exc:
            _log.warning(
                "telegram_sink: registry-api lookup failed",
                task_id=task_id,
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            return None, None
        except Exception as exc:  # noqa: BLE001 — best-effort, never crash the loop
            _log.warning(
                "telegram_sink: unexpected error during binding lookup",
                task_id=task_id,
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            return None, None


__all__ = ["TelegramSink"]
