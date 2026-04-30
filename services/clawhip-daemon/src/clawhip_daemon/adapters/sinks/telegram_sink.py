"""TelegramSink — event-log subscriber that dispatches outbound Telegram messages.

Story 3.9 AC-7: subscribes to the JSONL event log; for each ``task.*`` event
looks up the Telegram-thread binding from registry-api via HTTP GET, then
calls :class:`~clawhip_daemon.adapters.telegram_outbound.TelegramOutbound`
to deliver a placeholder message to the operator's originating chat thread.

Design notes:

* **HTTP-only lookup** (Story 3.6 review N7): reads ``(chat_id,
  reply_to_message_id)`` via ``GET /v1/tasks/{id}`` — NO direct ORM import
  from ``registry_state``.

* **Offset-0 startup** (Phase 1 simplification): reads from byte offset 0
  on every restart.  No position file.  Resume-after-restart semantics
  land in Story 7.x.

* **Placeholder renderer** (AC-7): returns ``f"Task {task_id}: {event_type}"``
  HTML-escaped.  Stories 3.10–3.13 replace this with proper templates.

* **Allowlist filter** (L15): only ``_DELIVERABLE_EVENT_TYPES`` are forwarded;
  future internal heartbeat / monitoring events are silently skipped.

* **JSONL reader** is implemented inline — clawhip-daemon only imports from
  ``packages/events`` (``from_canonical_json``) to stay within the
  import-graph rules (NFR-M1).

* **H4 eventual-consistency**: on 404 the sink retries once after 200 ms to
  tolerate the race window between event emission and materializer commit.

* **H5 consecutive lookup failure tracking**: after N>5 consecutive transient
  failures a WARN is emitted so operators are alerted.

* **M7 binding cache**: ``cachetools.TTLCache`` avoids N+1 GET per event for
  immutable bindings (set once at task.created, never updated).

* **M15 DI adapters**: ``EventLogReader`` and ``RegistryAPIReadClient`` are
  thin wrapper classes injected into ``TelegramSink`` so future stories can
  swap readers without touching TelegramSink internals.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import re
import time
from pathlib import Path
from typing import Any

import cachetools
import httpx
import pydantic
import structlog
from events import EventEnvelope, from_canonical_json
from pydantic import BaseModel, ConfigDict, Field

from clawhip_daemon.adapters.telegram_outbound import TelegramOutbound

_log = structlog.get_logger("clawhip_daemon.adapters.sinks.telegram_sink")

# Poll interval in seconds — 100ms matches registry-state subscriber.
_POLL_INTERVAL_S: float = 0.1

# H4: delay before 404 retry (eventual-consistency window).
_LOOKUP_RETRY_DELAY_S: float = 0.2

# H5: consecutive transient lookup failure threshold before WARN emission.
_LOOKUP_FAILURE_WARN_THRESHOLD: int = 5

# M6: only filenames matching YYYY-MM-DD.jsonl are processed.
_LOG_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")

# L15: positive allowlist of event types to deliver. Future internal-only
# event types (e.g. task.internal.heartbeat) will NOT match and are skipped.
_DELIVERABLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.created",
        "task.planning.started",
        "task.plan.ready",
        "task.execution.started",
        "task.blocker_raised",
        "task.summary_emitted",
        "task.approval_requested",
        "task.completed",
    }
)


# ---------------------------------------------------------------------------
# M4: typed Pydantic model for registry-api binding response
# ---------------------------------------------------------------------------


class _TaskBindingResponse(BaseModel):
    """Typed parse of GET /v1/tasks/{id} fields needed by TelegramSink.

    ``extra="ignore"`` so new fields added to TaskResponse in future stories
    do not break the sink (M4).
    """

    model_config = ConfigDict(extra="ignore")

    chat_id: int | None = Field(default=None)
    reply_to_message_id: int | None = Field(default=None)


# ---------------------------------------------------------------------------
# M15: EventLogReader — thin adapter over the JSONL file scan
# ---------------------------------------------------------------------------


class EventLogReader:
    """Reads newly-appended envelopes from a directory of YYYY-MM-DD.jsonl files.

    M15 DI adapter: ``TelegramSink`` takes an instance so future stories can
    swap in a different reader (e.g. a network-streamed log reader) without
    touching the sink's dispatch logic.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._offsets: dict[str, int] = {}
        # M5: track last-seen time per filename to prune stale offset keys.
        self._last_seen: dict[str, float] = {}

    async def read_new_envelopes(self) -> list[EventEnvelope]:
        """Return all newly-appended envelopes across all log files.

        Offsets are advanced so successive calls return only new data.
        """
        return await _scan_all_files(self._base_dir, self._offsets, self._last_seen)


# ---------------------------------------------------------------------------
# M15: RegistryAPIReadClient — thin adapter over the HTTP binding lookup
# ---------------------------------------------------------------------------


class RegistryAPIReadClient:
    """Thin wrapper for GET /v1/tasks/{id} binding lookups.

    M15 DI adapter: injected into ``TelegramSink`` so future stories can swap
    in a different client (e.g. a gRPC registry reader) without touching the
    sink.
    """

    def __init__(self, *, registry_api_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = registry_api_url.rstrip("/")
        self._http_client = http_client

    async def get_task_binding(
        self,
        task_id: str,
        *,
        request_id: str | None = None,
    ) -> tuple[int | None, int | None]:
        """GET /v1/tasks/{task_id} and return (chat_id, reply_to_message_id).

        H4: on 404 retries once after _LOOKUP_RETRY_DELAY_S ms (eventual-
        consistency race window between event emission and materializer commit).
        M17: distinguishes 404 (legitimate skip) from 5xx/transport (transient
        error that increments the H5 consecutive failure counter).

        Returns:
            ``(chat_id, reply_to_message_id)`` or ``(None, None)`` if the task
            has no Telegram binding (pre-3.9 task or non-Telegram origin).

        Raises:
            httpx.HTTPError: on transient 5xx / transport errors (caller
                handles and increments consecutive failure counter).
        """
        headers: dict[str, str] = {}
        if request_id is not None:
            headers["X-Request-ID"] = request_id

        url = f"{self._base_url}/v1/tasks/{task_id}"
        response = await self._http_client.get(url, headers=headers)

        if response.status_code == 404:
            # H4: retry once after delay to handle eventual-consistency window.
            await asyncio.sleep(_LOOKUP_RETRY_DELAY_S)
            response = await self._http_client.get(url, headers=headers)
            if response.status_code == 404:
                # Truly missing or non-Telegram task — skip silently.
                return None, None

        response.raise_for_status()  # M17: let 5xx propagate to caller

        # M4: type-safe parse of the response.
        try:
            binding = _TaskBindingResponse.model_validate(response.json())
        except pydantic.ValidationError:
            _log.warning(
                "telegram_sink: registry-api binding response parse failed",
                task_id=task_id,
            )
            return None, None

        return binding.chat_id, binding.reply_to_message_id


# ---------------------------------------------------------------------------
# JSONL reader helpers (no cross-service imports)
# ---------------------------------------------------------------------------


def _read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]:
    """Read complete ``\\n``-terminated envelopes from *path* starting at *offset*.

    H10: uses explicit trailer detection instead of ``rstrip(b"\\r\\n")``
    (byte-set strip) to avoid over-stripping payloads that end with ``\\r``.
    H3: JSONL parse errors are caught per-line; the bad line is skipped and
    the offset is advanced past it so the sink doesn't stall permanently.

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
                # H10: explicit trailer strip (not byte-set rstrip).
                if raw.endswith(b"\r\n"):
                    stripped = raw[:-2]
                elif raw.endswith(b"\n"):
                    stripped = raw[:-1]
                else:
                    stripped = raw
                # H3: wrap parse — bad line is logged + skipped, offset advanced.
                try:
                    envelope = from_canonical_json(stripped)
                    envelopes.append(envelope)
                except Exception as exc:  # noqa: BLE001 — corrupted JSONL line; advance past it
                    _log.warning(
                        "telegram_sink: malformed JSONL line; advancing past",
                        path=str(path),
                        offset=last_complete_end,
                        exc=str(exc),
                    )
                last_complete_end += len(raw)
    except OSError:
        # File disappeared between exists() check and open() (TOCTOU).
        return offset, []
    return last_complete_end, envelopes


async def _scan_all_files(
    base_dir: Path,
    offsets: dict[str, int],
    last_seen: dict[str, float],
) -> list[EventEnvelope]:
    """Scan every ``YYYY-MM-DD.jsonl`` in *base_dir* for newly-appended envelopes.

    M6: applies regex filter so non-conforming files are skipped.
    M5: prunes offset keys for files not seen within the last 7 days.
    Iterates in lexicographic (= chronological) order.
    """
    collected: list[EventEnvelope] = []
    if not base_dir.exists():
        return collected

    now = time.time()
    prune_age_s = 7 * 86400  # 7 days

    for path in sorted(
        (p for p in base_dir.glob("*.jsonl") if _LOG_FILE_RE.match(p.name)),
        key=lambda p: p.name,
    ):
        last_seen[path.name] = now
        prior = offsets.get(path.name, 0)
        new_offset, envelopes = await asyncio.to_thread(_read_new_envelopes_since, path, prior)
        offsets[path.name] = new_offset
        collected.extend(envelopes)

    # M5: prune stale offset entries.
    stale_keys = [k for k, ts in last_seen.items() if now - ts > prune_age_s]
    for k in stale_keys:
        offsets.pop(k, None)
        last_seen.pop(k, None)

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

    M15: constructor takes ``EventLogReader`` and ``RegistryAPIReadClient``
    as injected adapters.  For back-compat the legacy kwargs
    (``base_dir``, ``registry_api_url``, ``http_client``) are still accepted
    and used to construct the adapters implicitly.

    Args:
        base_dir:           Root directory containing ``YYYY-MM-DD.jsonl`` event logs.
                            Used when ``log_reader`` is not supplied.
        registry_api_url:   Base URL for registry-api.  Used when
                            ``registry_client`` is not supplied.
        http_client:        ``httpx.AsyncClient`` for registry-api lookups.
                            Used when ``registry_client`` is not supplied.
        outbound:           :class:`TelegramOutbound` for Telegram delivery.
        log_reader:         Injected ``EventLogReader`` (M15).  When supplied,
                            ``base_dir`` is ignored.
        registry_client:    Injected ``RegistryAPIReadClient`` (M15).  When
                            supplied, ``registry_api_url`` / ``http_client``
                            are ignored.
        poll_interval_s:    How long to sleep between tail-loop iterations.
    """

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        registry_api_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        outbound: TelegramOutbound,
        log_reader: EventLogReader | None = None,
        registry_client: RegistryAPIReadClient | None = None,
        poll_interval_s: float = _POLL_INTERVAL_S,
        # L4: clock parameter removed — was stored but never used; kept as
        # **kwargs for backward-compat if callers still pass clock=.
        **_kwargs: Any,
    ) -> None:
        # Build adapters from legacy kwargs if not injected directly (M15).
        if log_reader is not None:
            self._log_reader = log_reader
        else:
            if base_dir is None:
                raise TypeError("TelegramSink requires either log_reader or base_dir")
            self._log_reader = EventLogReader(base_dir)

        if registry_client is not None:
            self._registry_client = registry_client
        else:
            if registry_api_url is None or http_client is None:
                raise TypeError(
                    "TelegramSink requires either registry_client or "
                    "(registry_api_url + http_client)"
                )
            self._registry_client = RegistryAPIReadClient(
                registry_api_url=registry_api_url,
                http_client=http_client,
            )

        self._outbound = outbound
        self._poll_interval_s = poll_interval_s
        # M7: TTLCache for immutable bindings (set-once at task.created).
        self._binding_cache: cachetools.TTLCache[str, tuple[int | None, int | None]] = (
            cachetools.TTLCache(maxsize=1000, ttl=3600)
        )
        # H5: consecutive transient lookup failure counter.
        self._consecutive_lookup_failures: int = 0

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

        _log.info("telegram_sink started")

        while not stop.is_set():
            envelopes = await self._log_reader.read_new_envelopes()
            for envelope in envelopes:
                # L16: check stop between dispatches to avoid long shutdown delays.
                if stop.is_set():
                    break
                await self._handle(envelope)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_s)

        _log.info("telegram_sink stopped")

    async def _handle(self, envelope: EventEnvelope) -> None:
        """Process a single envelope: skip non-deliverable events, lookup binding, dispatch."""
        # L15: positive allowlist replaces the too-broad startswith("task.") check.
        if envelope.type not in _DELIVERABLE_EVENT_TYPES:
            return

        # Extract task_id from payload (all task.* payloads carry task_id).
        payload = envelope.payload
        task_id: str | None
        if hasattr(payload, "task_id"):
            raw = getattr(payload, "task_id", None)
            task_id = raw if isinstance(raw, str) else None
        else:
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

        # M7: check cache before HTTP lookup.
        if task_id in self._binding_cache:
            chat_id, reply_to_message_id = self._binding_cache[task_id]
        else:
            chat_id, reply_to_message_id = await self._lookup_binding(
                task_id, request_id=envelope.request_id
            )
            self._binding_cache[task_id] = (chat_id, reply_to_message_id)

        if chat_id is None or reply_to_message_id is None:
            # Pre-3.9 task or non-Telegram task — skip silently.
            return

        text = _render(task_id, envelope.type)
        await self._outbound.send_to_thread(
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            text=text,
        )

    async def _lookup_binding(
        self,
        task_id: str,
        *,
        request_id: str | None = None,
    ) -> tuple[int | None, int | None]:
        """Lookup binding via RegistryAPIReadClient with H5 failure tracking.

        M17: distinguishes 404 (legitimate) from 5xx/transport (transient).
        H5: increments consecutive transient failure counter; emits WARN at
        threshold.
        """
        try:
            # M8: propagate request_id for distributed tracing.
            result = await self._registry_client.get_task_binding(task_id, request_id=request_id)
            # Success — reset consecutive failure counter (H5).
            self._consecutive_lookup_failures = 0
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Legitimate: task does not exist or has no binding.
                self._consecutive_lookup_failures = 0
                return None, None
            # Transient 5xx — increment counter.
            self._consecutive_lookup_failures += 1
            self._emit_lookup_failure_warn(task_id, exc)
            return None, None
        except httpx.HTTPError as exc:
            # Transport / network error — transient.
            self._consecutive_lookup_failures += 1
            self._emit_lookup_failure_warn(task_id, exc)
            return None, None
        except Exception as exc:  # noqa: BLE001 — best-effort, never crash the loop
            self._consecutive_lookup_failures += 1
            self._emit_lookup_failure_warn(task_id, exc)
            return None, None

    def _emit_lookup_failure_warn(self, task_id: str, exc: Exception) -> None:
        """Log a structured warning on registry-api lookup failure (H5)."""
        _log.warning(
            "telegram_sink: registry-api lookup failed",
            task_id=task_id,
            exc_type=type(exc).__name__,
            exc=str(exc),
            consecutive_lookup_failures=self._consecutive_lookup_failures,
        )
        if self._consecutive_lookup_failures > _LOOKUP_FAILURE_WARN_THRESHOLD:
            _log.warning(
                "telegram_sink: consecutive lookup failures exceed threshold — "
                "registry-api may be unreachable",
                threshold=_LOOKUP_FAILURE_WARN_THRESHOLD,
                consecutive_lookup_failures=self._consecutive_lookup_failures,
            )


__all__ = ["EventLogReader", "RegistryAPIReadClient", "TelegramSink"]
