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
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import cachetools
import httpx
import pydantic
import structlog
from events import EventEnvelope, from_canonical_json
from pydantic import BaseModel, ConfigDict, Field

# TODO(packages/events refactor): cross-service IMP001 noqa cluster grows with
# each renderer story (3.10, 3.11, 3.12, 3.13). Consider moving payload models
# to packages/events/ in a future story to eliminate the noqa cluster (Story
# 3.10 review L5).
from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
    PreCheckResults,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
)

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
# Renderers (Story 3.10 — first message-template + dispatcher)
# ---------------------------------------------------------------------------

# Story 3.10 AC-6: length-safety caps for the approval-request renderer.
#
# Review-pass H7: per-bullet cap is the WHOLE LINE cap (prefix + escaped text)
# so the on-the-wire bullet never exceeds 200 chars. The text body cap is
# derived by subtracting the prefix length.
#
# Review-pass M1: ``_APPROVAL_MESSAGE_MAX_CHARS = 2000`` (tightened from
# 3500). Telegram's 4096-char limit counts UTF-16 code units, NOT codepoints.
# Surrogate-pair-heavy messages (emoji clusters, math symbols, CJK
# extensions) can double their UTF-16 length vs. Python's ``len()``. Capping
# at 2000 codepoints leaves headroom for worst-case UTF-16 expansion
# (≈4000 code units) without exceeding Telegram's wire limit.
#
# Story 3.11 review H3 / L15: tightened from 2000 → 1900 codepoints.
# A 2000-codepoint cap admitted pathological UTF-16 expansions (2000
# plane-1 emoji = 4000 UTF-16 units, plus header/footer/entities push
# over the 4096-unit wire limit). 1900 leaves an extra 5% headroom.
# **Parity invariant:** ``_APPROVAL_MESSAGE_MAX_CHARS`` and
# ``_BLOCKER_MESSAGE_MAX_CHARS`` MUST move together — future renderers
# inherit the same cap; if you change one, change both.
_BULLET_PREFIX: str = "  • "
_APPROVAL_BULLET_MAX_CHARS: int = 200
_APPROVAL_BULLET_TEXT_MAX_CHARS: int = _APPROVAL_BULLET_MAX_CHARS - len(_BULLET_PREFIX)
_APPROVAL_MAX_COMMANDS: int = 10
_APPROVAL_MESSAGE_MAX_CHARS: int = 1900

# Story 3.10 AC-5: ordered pre-check field iteration (lint → types → unit →
# integration). Field name → Capitalized display label.
_PRE_CHECK_FIELDS: tuple[tuple[str, str], ...] = (
    ("lint", "Lint"),
    ("types", "Types"),
    ("unit", "Unit"),
    ("integration", "Integration"),
)


def _extract_task_id(envelope: EventEnvelope) -> str | None:
    """Best-effort extract of ``task_id`` from an envelope's payload.

    Mirrors the inline extraction in ``_handle`` but is reusable from the
    dispatcher fallback so the placeholder string preserves Story 3.9's
    ``Task <id>: <type>`` shape even when no typed renderer is registered.

    Story 3.10 review L2: emit a structured WARN when the payload exposes a
    ``task_id`` attribute / key whose runtime type is not ``str`` — silent
    ``None`` returns mask schema-registry drift in production.
    """
    payload = envelope.payload
    if hasattr(payload, "task_id"):
        raw = getattr(payload, "task_id", None)
        if isinstance(raw, str):
            return raw
        if raw is not None:
            _log.warning(
                "renderer.task_id_non_string",
                event_type=envelope.type,
                actual_type=type(raw).__name__,
            )
        return None
    if isinstance(payload, dict):
        v = payload.get("task_id")
        if isinstance(v, str):
            return v
        if v is not None:
            _log.warning(
                "renderer.task_id_non_string",
                event_type=envelope.type,
                actual_type=type(v).__name__,
            )
        return None
    return None


def _truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, appending ``…`` when shortened.

    Story 3.10 review L3 / L10: defensive handling of degenerate ``limit``
    values — ``limit <= 0`` returns the empty string (cannot fit any
    content); ``limit == 1`` returns the ``…`` ellipsis (single
    truncation marker).
    """
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


def _collapse_newlines(text: str) -> str:
    """Collapse all line-break sequences (``\\r\\n``, ``\\r``, ``\\n``) to single spaces.

    Story 3.11 review H6 / L17 / M12: extracted helper so renderers don't
    diverge stylistically (``"\\n"`` vs ``chr(10)``). Order matters:
    ``\\r\\n`` is collapsed first so the trailing ``\\n`` doesn't decay
    a CRLF to ``" \\n"`` → ``"  "``. Bare ``\\r`` is collapsed next so
    legacy Mac line endings don't leak into HTML-escaped output. Finally
    bare ``\\n`` (the common case) is collapsed.

    The function is total — call sites do not pre-validate. Used on both
    operator-supplied free-form text (``reason``, ``last_action``) and
    registry-controlled strings (``last_event``, defense-in-depth) before
    HTML-escaping.
    """
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


# Story 3.10 review M13: status → emoji map. Widened from binary
# pass/fail to also cover ``"skipped"`` (env unavailable) and ``"error"``
# (the check itself crashed). Defensive ``"❓"`` fallback covers any future
# status values that ship before this map is updated.
_PRE_CHECK_STATUS_EMOJI: Mapping[str, str] = MappingProxyType(
    {
        "pass": "✅",
        "fail": "❌",
        "skipped": "⏭️",
        "error": "⚠️",
    }
)


def _render_pre_check_block(
    results: PreCheckResults | None, *, include_failed_suffix: bool = True
) -> str | None:
    """Render the pre-check block; return ``None`` if no pre-check is populated.

    Iterates fields in the spec order (lint, types, unit, integration) and
    skips ``None`` slots. The ``include_failed_suffix`` flag is the lever the
    total-cap section-drop strategy pulls (AC-6) when the message is too long.

    Story 3.10 review M4: signature decoupled from the whole payload — takes
    just ``PreCheckResults | None`` so unit tests can construct the block
    directly without a full envelope. Pre-check fields skipped when ``None``.
    """
    if results is None:
        return None
    lines: list[str] = []
    for attr, label in _PRE_CHECK_FIELDS:
        outcome = getattr(results, attr)
        if outcome is None:
            continue
        emoji = _PRE_CHECK_STATUS_EMOJI.get(outcome.status, "❓")
        line = f"{emoji} {label}: {outcome.passed}/{outcome.total}"
        if include_failed_suffix and outcome.status == "fail":
            line += " (failed)"
        lines.append(line)
    if not lines:
        return None
    return "Pre-checks:\n" + "\n".join(lines)


def _render_diff_summary(payload: TaskApprovalRequestedPayload) -> str | None:
    """Render ``Diff: <files> files, +<I>, -<D>`` or ``None`` if absent."""
    diff = payload.diff_summary
    if diff is None:
        return None
    return f"Diff: {diff.files} files, +{diff.insertions}, -{diff.deletions}"


def _build_command_bullets(cmds: list[str], visible_count: int) -> list[str]:
    """Format ``visible_count`` commands as bullet lines (HTML-escaped + truncated).

    Story 3.10 review H8: extracted from ``_render_accepted_commands`` and
    the section-drop ladder so both call sites share the same bullet logic.
    Future stories (3.11/3.12/3.13) that tweak bullet formatting touch a
    single helper.

    Story 3.10 review H7: per-bullet cap is the WHOLE-LINE cap. Since the
    line is ``_BULLET_PREFIX + escaped_text``, the text body cap is
    ``_APPROVAL_BULLET_TEXT_MAX_CHARS = _APPROVAL_BULLET_MAX_CHARS -
    len(_BULLET_PREFIX)``.
    """
    bullets: list[str] = []
    for cmd in cmds[:visible_count]:
        escaped = html.escape(cmd)
        if len(escaped) > _APPROVAL_BULLET_TEXT_MAX_CHARS:
            escaped = _truncate(escaped, _APPROVAL_BULLET_TEXT_MAX_CHARS)
        bullets.append(f"{_BULLET_PREFIX}{escaped}")
    return bullets


def _build_overflow_line(omitted: int) -> str | None:
    """Build the ``  • … and N more`` overflow indicator, or ``None`` if N == 0."""
    if omitted <= 0:
        return None
    return f"{_BULLET_PREFIX}… and {omitted} more"


def _build_commands_section(
    payload: TaskApprovalRequestedPayload, visible_count: int
) -> str | None:
    """Build the full ``Accepted commands:`` section text, or ``None`` when empty.

    Story 3.10 review L4 / H8: used by both ``_render_accepted_commands``
    (initial render) and the section-drop ladder Step 3 (progressive trim).
    """
    cmds = payload.accepted_commands or []
    if visible_count <= 0 or len(cmds) == 0:
        return None
    bullets = _build_command_bullets(cmds, visible_count)
    omitted = len(cmds) - visible_count
    overflow = _build_overflow_line(omitted)
    if overflow is not None:
        bullets.append(overflow)
    return "Accepted commands:\n" + "\n".join(bullets)


def _render_accepted_commands(payload: TaskApprovalRequestedPayload) -> str | None:
    """Render the accepted-commands bullet list, applying the 10-cmd / 200-char caps.

    Returns ``None`` when the list is absent or empty (Story 3.10 AC-5 / L16:
    omit the section in either case — caller filters via the ``None``
    return).
    """
    cmds = payload.accepted_commands
    if cmds is None or len(cmds) == 0:
        return None
    visible_count = min(len(cmds), _APPROVAL_MAX_COMMANDS)
    return _build_commands_section(payload, visible_count)


def _assemble_approval_sections(
    payload: TaskApprovalRequestedPayload,
    *,
    include_failed_suffix: bool,
    include_pre_checks: bool = True,
    include_diff: bool,
    commands_section: str | None,
) -> str:
    """Join the approval-request sections in spec order with ``\\n\\n`` separators.

    Header / Action / Reason are mandatory; the other sections honor the
    boolean / Optional levers the section-drop strategy in
    :func:`_render_approval_request` flips on overflow.

    Story 3.10 review H1: ``include_pre_checks`` adds a lever the ladder
    pulls between Step 3 (commands trim) and the emergency one-liner —
    drops the pre-check block wholesale before falling back to ``/logs``.

    Story 3.10 review H11: multi-line ``justification`` is collapsed to a
    single line (``\\n`` → space) before HTML-escaping so the section
    separator (``\\n\\n``) remains visually unambiguous.

    Story 3.10 review M14: ``risk_class`` is HTML-escaped defensively. Today
    the value is bound by ``Literal["low","medium","high"]`` so the escape
    is a no-op; if the model is ever relaxed to a bare ``str`` the renderer
    is already drift-safe.
    """
    task_id_esc = html.escape(payload.task_id)
    action_esc = html.escape(payload.action)
    # H11: collapse newlines so the section separator stays unambiguous.
    # Story 3.11 review L17: ``_collapse_newlines`` covers ``\r\n`` / ``\r``
    # / ``\n`` uniformly — bare ``\r`` from legacy Mac line endings would
    # otherwise survive a naive ``.replace("\n", " ")``.
    justification_esc = html.escape(_collapse_newlines(payload.justification))

    sections: list[str] = [
        f"🔒 Approval required — task {task_id_esc}",
        f"Action: {action_esc}",
        f"Reason: {justification_esc}",
    ]
    if payload.risk_class is not None:
        # M14: defense-in-depth escape (no-op for current Literal values).
        sections.append(f"Risk: {html.escape(payload.risk_class)}")
    if include_pre_checks:
        pre_check_block = _render_pre_check_block(
            payload.pre_check_results, include_failed_suffix=include_failed_suffix
        )
        if pre_check_block is not None:
            sections.append(pre_check_block)
    if include_diff:
        diff_section = _render_diff_summary(payload)
        if diff_section is not None:
            sections.append(diff_section)
    if commands_section is not None:
        sections.append(commands_section)
    return "\n\n".join(sections)


#: Story 3.10 review H2: defense-in-depth cap on ``task_id`` length used by
#: the emergency one-liner fallback. The model boundary (H3) already caps
#: ``task_id`` at 64 chars, so this is a no-op for valid inputs; defends
#: against any path that bypasses Pydantic validation (e.g.
#: ``EventEnvelope.model_construct``).
_EMERGENCY_TASK_ID_MAX_CHARS: int = 64


def _largest_visible_count_under_cap(
    payload: TaskApprovalRequestedPayload,
    *,
    initial_count: int,
) -> tuple[int, str | None]:
    """Binary-search for the largest ``visible_count`` whose assembled message fits.

    Story 3.10 review M3: replaces the O(N²) linear-scan trim loop with an
    O(log N) binary search. Returns ``(visible_count, assembled_text)``
    where ``visible_count`` is the largest count that fits, or ``-1`` if no
    count (including 0) fits. Caller is responsible for falling through to
    Step 3.5 (drop pre-checks) when the negative sentinel is returned.

    Note: monotonicity holds for this assembly — fewer commands → strictly
    shorter message — so binary search is valid.
    """
    lo, hi = 0, initial_count
    best_count = -1
    best_text: str | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        section = _build_commands_section(payload, mid) if mid > 0 else None
        candidate = _assemble_approval_sections(
            payload,
            include_failed_suffix=False,
            include_diff=False,
            commands_section=section,
        )
        if len(candidate) <= _APPROVAL_MESSAGE_MAX_CHARS:
            best_count = mid
            best_text = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best_count, best_text


def _render_approval_request(envelope: EventEnvelope) -> str:
    """Render the FR14 approval-request message — Story 3.10 AC-5/AC-6/AC-7.

    Sections (in order, with optional ones omitted on ``None``):

      1. Header — ``🔒 Approval required — task <task_id>``
      2. Action — ``Action: <action>``
      3. Reason — ``Reason: <justification>``
      4. Risk — ``Risk: <low|medium|high>``  (omitted if ``risk_class is None``)
      5. Pre-checks — multi-line block (omitted if ``pre_check_results is None``)
      6. Diff — ``Diff: N files, +I, -D``  (omitted if ``diff_summary is None``)
      7. Accepted commands — bullet list, 10-cmd cap with ``… and N more``
         overflow line (omitted if list is None or empty)

    HTML-escapes ``task_id``, ``action``, ``justification``, and each accepted
    command (Story 3.5 H5 carry-forward).

    Length safety (AC-6 + review-pass H1):

    * per-bullet 200-char cap (whole-line; H7 fixed prefix-math).
    * total 2000-char cap (M1 — UTF-16 surrogate-pair safety; tightened
      from 3500 codepoints).
    * On overflow, sections are dropped in order:

        Step 1: ``(failed)`` suffix.
        Step 2: diff summary.
        Step 3: accepted commands (binary-searched per M3).
        Step 3.5: pre-check block (H1).
        Step 4: emergency one-liner.

    Story 3.10 review H9: when the dispatcher routes here but the runtime
    payload is not a typed ``TaskApprovalRequestedPayload`` (registration
    race / version drift), a structured WARN is emitted before falling back
    to the placeholder. SRE has a signal in production.
    """
    payload = envelope.payload
    if not isinstance(payload, TaskApprovalRequestedPayload):
        # Defensive: dispatcher only routes here for task.approval_requested,
        # but if the envelope was built from a raw dict (registration race
        # or a future version-drift scenario), fall back to the placeholder
        # shape. Story 3.10 review H9: log a structured warning so SRE can
        # detect schema-registry registration races / version drift.
        _log.warning(
            "renderer.payload_type_mismatch",
            event_type=envelope.type,
            expected="TaskApprovalRequestedPayload",
            actual=type(payload).__name__,
        )
        task_id = _extract_task_id(envelope) or "<unknown>"
        return f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"

    commands_section = _render_accepted_commands(payload)

    # Fast path: assemble fully-populated message; if under cap, return it.
    text = _assemble_approval_sections(
        payload,
        include_failed_suffix=True,
        include_diff=True,
        commands_section=commands_section,
    )
    if len(text) <= _APPROVAL_MESSAGE_MAX_CHARS:
        return text

    # AC-6 section-drop ladder.
    # Step 1: drop the ` (failed)` suffix from pre-check lines.
    text = _assemble_approval_sections(
        payload,
        include_failed_suffix=False,
        include_diff=True,
        commands_section=commands_section,
    )
    if len(text) <= _APPROVAL_MESSAGE_MAX_CHARS:
        return text

    # Step 2: drop the diff summary section.
    text = _assemble_approval_sections(
        payload,
        include_failed_suffix=False,
        include_diff=False,
        commands_section=commands_section,
    )
    if len(text) <= _APPROVAL_MESSAGE_MAX_CHARS:
        return text

    # Step 3 (review M3): binary-search for the largest ``visible_count``
    # whose assembled message fits under the cap. O(log N) replaces the
    # prior O(N²) linear-scan + per-iteration string concat.
    cmds = payload.accepted_commands or []
    initial_count = min(len(cmds), _APPROVAL_MAX_COMMANDS)
    if initial_count > 0:
        best_count, best_text = _largest_visible_count_under_cap(
            payload, initial_count=initial_count
        )
        if best_text is not None:
            return best_text
        # Even with 0 commands the message overflows — fall through to
        # Step 3.5 / Step 4.
    else:
        # No commands to trim; rebuild without the section to verify the
        # overflow source is something else (pre-checks or justification).
        text = _assemble_approval_sections(
            payload,
            include_failed_suffix=False,
            include_diff=False,
            commands_section=None,
        )
        if len(text) <= _APPROVAL_MESSAGE_MAX_CHARS:
            return text

    # Step 3.5 (review H1): drop the pre-check block before the emergency
    # one-liner. A populated pre-check block (~60-160 chars) is a frequent
    # contributor to overflow; the operator can recover via /logs <id>.
    text = _assemble_approval_sections(
        payload,
        include_failed_suffix=False,
        include_pre_checks=False,
        include_diff=False,
        commands_section=None,
    )
    if len(text) <= _APPROVAL_MESSAGE_MAX_CHARS:
        return text

    # Step 4: emergency one-liner fallback — even Header+Action+Reason are too
    # large (typically `justification` is enormous). Operator can `/logs <id>`.
    # Review H2: defense-in-depth cap on ``task_id`` (the model boundary
    # already caps at 64 chars; this is the same ceiling for any path that
    # bypasses Pydantic validation).
    #
    # Story 3.11 review H2 (retroactive 3.10 fix): cap on the RAW ``task_id``
    # BEFORE ``html.escape`` — slicing the escaped string can split an HTML
    # entity mid-token (e.g. 64 raw ``<`` chars escape to 64 × 5 = 320 chars
    # of ``&lt;``; slicing the escaped form at 64 lands inside ``&lt;`` and
    # produces invalid markup like ``...&lt;&lt;&l``).
    #
    # Story 3.12 review H1 (retroactive 3.10/3.11/3.12 fix): collapse newlines
    # BEFORE the slice + escape so a ``task_id`` containing ``\n`` cannot
    # smuggle a real newline into the supposedly single-line emergency
    # message. ``_collapse_newlines`` is idempotent on already-clean input.
    # _EMERGENCY_TASK_ID_MAX_CHARS = 64 — Story 3.10 H2 carry-forward (matches
    # the model-boundary ``max_length=64`` ceiling on ``task_id``).
    task_id_safe = _collapse_newlines(payload.task_id)
    task_id_capped = task_id_safe[:_EMERGENCY_TASK_ID_MAX_CHARS]
    task_id_for_fallback = html.escape(task_id_capped)
    result = (
        f"🔒 Approval required — task {task_id_for_fallback}"
        f"\n\n(message body too large; see /logs {task_id_for_fallback})"
    )
    # Story 3.11 review H5 carry-forward: defensive final length self-check.
    # Header overhead + 64-char escaped ``task_id`` × 2 occurrences is well
    # under the cap in practice; the clamp is here so a future header tweak
    # cannot silently breach the wire limit.
    if len(result) > _APPROVAL_MESSAGE_MAX_CHARS:
        result = result[:_APPROVAL_MESSAGE_MAX_CHARS]
    return result


# ---------------------------------------------------------------------------
# Renderer (Story 3.11 — blocker-notification template, FR15)
# ---------------------------------------------------------------------------

#: Story 3.11 AC-2 — static recovery commands listed in the blocker
#: notification footer. Platform-defined (not operator-supplied) so they
#: live as a renderer-side constant; if a future story makes the list
#: dynamic per-task it can promote this to a payload field.
_BLOCKER_AVAILABLE_COMMANDS: tuple[str, ...] = ("/logs", "/retry", "/stop", "/handoff")

#: Story 3.11 review M13: cache the ``list(...)`` form module-side so
#: ``_assemble_blocker_sections`` doesn't allocate a fresh list per render
#: call. ``_build_command_bullets`` mutates nothing on its input — passing
#: the shared list is safe.
_BLOCKER_COMMANDS_LIST: list[str] = list(_BLOCKER_AVAILABLE_COMMANDS)

#: Story 3.11 AC-5 — same codepoint cap as approvals (Story 3.10 M1 UTF-16
#: surrogate-pair safety carry-forward). Telegram's 4096-char limit counts
#: UTF-16 code units; capping at 1900 codepoints leaves headroom for
#: worst-case expansion plus header/footer/entity overhead.
#:
#: Story 3.11 review H3 / L15: tightened from 2000 → 1900 in lockstep with
#: ``_APPROVAL_MESSAGE_MAX_CHARS``. **Parity invariant** — both caps move
#: together; future renderers (3.12 / 3.13) inherit the same value.
_BLOCKER_MESSAGE_MAX_CHARS: int = 1900


def _assemble_blocker_sections(
    payload: TaskBlockerRaisedPayload,
    *,
    include_blocked_since: bool,
    include_last_event: bool,
    include_last_action: bool,
    include_commands_footer: bool,
) -> str:
    """Join the blocker sections in spec order with ``{nl}{nl}`` separators.

    Header is mandatory; the optional context fields and the commands
    footer honor boolean levers the section-drop ladder in
    :func:`_render_blocker_raised` flips on overflow.

    Story 3.11 AC-4: HTML-escape ``task_id``, ``reason`` (after newline
    collapse), ``last_event``, ``last_action`` (after newline collapse).

    Story 3.10 review H11 carry-forward + Story 3.11 review H6 / L17 / M12:
    multi-line ``reason`` / ``last_event`` / ``last_action`` are collapsed
    via :func:`_collapse_newlines` (handles ``\\r\\n`` / ``\\r`` / ``\\n``
    uniformly) before HTML-escaping so the section separator
    (``{nl}{nl}``) remains visually unambiguous.

    Story 3.11 review H1: trailing terminal punctuation (``.``/``?``/``!``/``:``)
    is stripped from the collapsed ``reason`` BEFORE HTML-escaping so the
    header line doesn't render double punctuation
    (e.g. ``reason="crashed."`` produced ``"...crashed.. See /logs..."``
    pre-fix; post-fix renders cleanly as ``"...crashed. See /logs..."``).
    """
    task_id_esc = html.escape(payload.task_id)
    # H6 / L17 / M12: collapse newlines uniformly (``\r\n`` / ``\r`` / ``\n``)
    # so the section separator stays unambiguous and bare-CR legacy line
    # endings don't survive into HTML output.
    # H1: strip trailing terminal punctuation from the raw reason before
    # escape so the header doesn't render double punctuation.
    reason_collapsed = _collapse_newlines(payload.reason).rstrip(".?!:")
    reason_esc = html.escape(reason_collapsed)

    sections: list[str] = [
        f"⛔ Task {task_id_esc} blocked. {reason_esc}. See /logs {task_id_esc} for detail.",
    ]
    if include_blocked_since and payload.blocked_since is not None:
        # blocked_since is internal (datetime → ASCII via .isoformat());
        # no escape needed (AC-4).
        # Story 3.11 review M14: lock format to ``timespec="seconds"`` so
        # microsecond presence/absence doesn't shift the ladder math by
        # 7 chars (``+00:00`` vs ``.123456+00:00``).
        sections.append(f"Blocked since: {payload.blocked_since.isoformat(timespec='seconds')}")
    if include_last_event and payload.last_event is not None:
        # M14 carry-forward: defense-in-depth escape on the registry-
        # controlled string. Story 3.11 review H4: collapse newlines
        # too — schema permits 128 chars including line breaks; an
        # emitter shipping ``last_event="evt\n\nattacker"`` would
        # otherwise inject a bogus section break.
        sections.append(f"Last event: {html.escape(_collapse_newlines(payload.last_event))}")
    if include_last_action and payload.last_action is not None:
        # H11 carry-forward: collapse newlines before escape (M12 — use
        # the same helper as ``reason`` / ``last_event`` for consistency).
        sections.append(f"Last action: {html.escape(_collapse_newlines(payload.last_action))}")
    if include_commands_footer:
        # Story 3.11 review M13: pass the cached list (allocated once at
        # module scope) instead of allocating a fresh list per call.
        bullets = _build_command_bullets(_BLOCKER_COMMANDS_LIST, len(_BLOCKER_AVAILABLE_COMMANDS))
        sections.append("Available commands:\n" + "\n".join(bullets))
    return "\n\n".join(sections)


def _render_blocker_raised(envelope: EventEnvelope) -> str:
    """Render the FR15 blocker-notification message — Story 3.11 AC-2/AC-4/AC-5.

    Sections (in order, with optional ones omitted on ``None``):

      1. Header (always) — ``⛔ Task {task_id} blocked. {reason}. See /logs {task_id} for detail.``
      2. Blocked since (omitted if ``blocked_since is None``).
      3. Last event (omitted if ``last_event is None``).
      4. Last action (omitted if ``last_action is None``).
      5. Available commands footer (always except emergency fallback).

    Length safety (AC-5) — section-drop ladder:

      Step 1: full message — return if under cap.
      Step 2: drop ``last_action``.
      Step 3: drop ``last_event``.
      Step 4: drop ``blocked_since``.
      Step 5: emergency one-liner —
        ``⛔ Task {task_id} blocked. (message body too large; see /logs {task_id})``
        with the available-commands footer dropped.

    Story 3.10 review H9 carry-forward: when the dispatcher routes here
    but the runtime payload is not a typed
    :class:`TaskBlockerRaisedPayload` (registration race / version drift),
    a structured WARN is emitted before falling back to the placeholder.

    Story 3.11 review L12: non-string ``last_event`` / ``last_action``
    (model_construct-bypass scenarios) would raise inside ``html.escape``;
    the existing ``try/except`` block in :meth:`TelegramSink._handle`
    (Story 3.10 review M11) catches the exception and falls back to the
    placeholder shape — the renderer itself does not duplicate that
    isolation per-field.
    """
    payload = envelope.payload
    if not isinstance(payload, TaskBlockerRaisedPayload):
        # H9 carry-forward: structured warning so SRE can detect schema-
        # registry registration races / version drift.
        _log.warning(
            "renderer.payload_type_mismatch",
            event_type=envelope.type,
            expected="TaskBlockerRaisedPayload",
            actual=type(payload).__name__,
        )
        task_id = _extract_task_id(envelope) or "<unknown>"
        return f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"

    # Step 1: full message.
    text = _assemble_blocker_sections(
        payload,
        include_blocked_since=True,
        include_last_event=True,
        include_last_action=True,
        include_commands_footer=True,
    )
    if len(text) <= _BLOCKER_MESSAGE_MAX_CHARS:
        return text

    # Step 2: drop last_action.
    text = _assemble_blocker_sections(
        payload,
        include_blocked_since=True,
        include_last_event=True,
        include_last_action=False,
        include_commands_footer=True,
    )
    if len(text) <= _BLOCKER_MESSAGE_MAX_CHARS:
        return text

    # Step 3: drop last_event.
    text = _assemble_blocker_sections(
        payload,
        include_blocked_since=True,
        include_last_event=False,
        include_last_action=False,
        include_commands_footer=True,
    )
    if len(text) <= _BLOCKER_MESSAGE_MAX_CHARS:
        return text

    # Step 4: drop blocked_since (header + commands footer only).
    # Story 3.11 review L10: ``include_blocked_since=False`` is the
    # ladder-driven lever; the renderer also short-circuits with
    # ``payload.blocked_since is not None`` inside the helper. The two
    # gates are intentionally independent — the boolean is the lever the
    # ladder pulls; the ``is not None`` check is the per-field omission.
    # Story 3.11 review L18: Step 4 fires in a narrow band (roughly
    # ``1820 < len(reason) <= 1844`` with all 3 optional fields populated)
    # — it is NOT dead code; tests cover the transition explicitly.
    text = _assemble_blocker_sections(
        payload,
        include_blocked_since=False,
        include_last_event=False,
        include_last_action=False,
        include_commands_footer=True,
    )
    if len(text) <= _BLOCKER_MESSAGE_MAX_CHARS:
        return text

    # Step 5: emergency one-liner — header + commands footer can't fit
    # together (typically because ``reason`` is pathologically long).
    # Operator can recover via /logs <id>.
    # Story 3.10 review H2 carry-forward + Story 3.11 review H2: cap on
    # the RAW ``task_id`` BEFORE ``html.escape`` so a 64-char raw ``<``
    # task_id (which escapes to 64 × 5 = 320 chars of ``&lt;``) cannot
    # split an HTML entity mid-token when sliced at 64.
    #
    # Story 3.12 review H1 (retroactive 3.11 fix): collapse newlines BEFORE
    # the slice + escape so a ``task_id`` containing ``\n`` cannot smuggle
    # a real newline into the supposedly single-line emergency message.
    # _EMERGENCY_TASK_ID_MAX_CHARS = 64 — Story 3.10 H2 carry-forward
    # (matches the model-boundary ``max_length=64`` ceiling on ``task_id``).
    task_id_safe = _collapse_newlines(payload.task_id)
    task_id_capped = task_id_safe[:_EMERGENCY_TASK_ID_MAX_CHARS]
    task_id_for_fallback = html.escape(task_id_capped)
    result = (
        f"⛔ Task {task_id_for_fallback} blocked. "
        f"(message body too large; see /logs {task_id_for_fallback})"
    )
    # Story 3.11 review H5: defensive final length self-check. The header
    # overhead + 64-char escaped ``task_id`` × 2 occurrences sits well
    # under the cap in practice; the clamp guards against future header
    # tweaks silently breaching the wire limit.
    if len(result) > _BLOCKER_MESSAGE_MAX_CHARS:
        result = result[:_BLOCKER_MESSAGE_MAX_CHARS]
    return result


# ---------------------------------------------------------------------------
# Renderer (Story 3.12 — completion-summary template, FR9)
# ---------------------------------------------------------------------------

#: Story 3.12 AC-5 — same codepoint cap as approvals / blockers (Story 3.10
#: M1 + Story 3.11 H3 / L15 UTF-16 surrogate-pair safety carry-forward).
#: **Parity invariant** — ``_APPROVAL_MESSAGE_MAX_CHARS`` /
#: ``_BLOCKER_MESSAGE_MAX_CHARS`` / ``_COMPLETED_MESSAGE_MAX_CHARS`` MUST
#: move together; future renderers (3.13) inherit the same value.
_COMPLETED_MESSAGE_MAX_CHARS: int = 1900

#: Story 3.12 AC-2 — ci_state → emoji map. Pattern matches
#: :data:`_PRE_CHECK_STATUS_EMOJI` (Story 3.10 review M13). Defensive
#: ``"❓"`` fallback covers any future status values that ship before this
#: map is updated. ``MappingProxyType`` matches the read-only convention
#: (Story 3.6 review L1 carry-forward).
#:
#: Story 3.12 review L8: the defensive fallback emoji is ``"❔"`` (white
#: question mark ornament) — distinct from the explicit ``"unknown" → "❓"``
#: (red question mark) entry. If a future Literal value is added without
#: also updating this map, the visible emoji difference signals a drift
#: (rendered ``CI: ❔ <value>``) and the ``ci_state_drift`` WARN below
#: provides the SRE-actionable signal. Co-locating the structural map
#: with the drift-detection log keeps the contract self-documenting.
_CI_STATE_EMOJI: Mapping[str, str] = MappingProxyType(
    {
        "green": "✅",
        "red": "❌",
        "unknown": "❓",
    }
)
_CI_STATE_FALLBACK_EMOJI: str = "❔"


def _build_pr_line(payload: TaskCompletedPayload) -> str | None:
    """Build the PR line — one of seven shapes based on which fields are present.

    Returns ``None`` when ``pr_number``, ``pr_branch``, and ``pr_url`` are
    all ``None``. Otherwise composes the most-informative shape per
    Story 3.12 AC-2 spec table.

    Story 3.12 AC-4: ``pr_branch`` and ``pr_url`` are HTML-escaped after
    newline collapse (defense-in-depth — branch names should not contain
    ``\\n`` per git ref-name rules, but if a buggy emitter slips one in,
    defend); ``pr_number`` is integer (no escape).

    Story 3.12 review M5: when ``pr_branch`` or ``pr_url`` collapses to a
    whitespace-only string (e.g. ``"\\n\\n\\n"`` passes ``min_length=1``
    on raw input but ``_collapse_newlines`` converts it to ``"   "``), the
    field is treated as None-equivalent so the PR line doesn't render with
    trailing whitespace gaps (e.g. ``"PR #42:    "``).
    """
    pr_number = payload.pr_number
    pr_branch = payload.pr_branch
    pr_url = payload.pr_url
    if pr_number is None and pr_branch is None and pr_url is None:
        return None
    branch_esc: str | None = None
    if pr_branch is not None:
        collapsed = _collapse_newlines(pr_branch)
        # M5: empty-after-collapse short-circuit so a newlines-only branch
        # name (which slipped past ``min_length=1`` on raw input) doesn't
        # render as a whitespace-only token.
        if collapsed.strip() != "":
            branch_esc = html.escape(collapsed)
    url_esc: str | None = None
    if pr_url is not None:
        collapsed_url = _collapse_newlines(pr_url)
        if collapsed_url.strip() != "":
            url_esc = html.escape(collapsed_url)
    # Recompute "all None" after collapse in case both string fields became
    # whitespace-only — fall back to None when only pr_number is left, OR
    # to the pr_number-only branch.
    if pr_number is None and branch_esc is None and url_esc is None:
        return None
    if pr_number is not None and branch_esc is not None and url_esc is not None:
        return f"PR #{pr_number}: {branch_esc} — {url_esc}"
    if pr_number is not None and branch_esc is not None:
        return f"PR #{pr_number}: {branch_esc}"
    if pr_number is not None and url_esc is not None:
        return f"PR #{pr_number}: {url_esc}"
    if branch_esc is not None and url_esc is not None:
        return f"{branch_esc} — {url_esc}"
    if url_esc is not None:
        return f"PR: {url_esc}"
    if branch_esc is not None:
        return f"Branch: {branch_esc}"
    # Only pr_number remains. Story 3.12 review M13: the early-return guard
    # above + the post-collapse recompute together guarantee ``pr_number is
    # not None`` here. The assert documents the structural invariant so a
    # future field addition cannot silently slip past the branch ladder.
    assert pr_number is not None, "exhausted PR-line branches"
    return f"PR: #{pr_number}"


def _build_diff_stats_line(payload: TaskCompletedPayload) -> str | None:
    """Build the diff-stats line, or ``None`` when no counters populated.

    Composes one of seven shapes based on which combination of
    ``files_changed`` / ``lines_added`` / ``lines_removed`` is present.

    Story 3.12 review H2: explicit handling of the (fc+la-no-lr) and
    (fc+lr-no-la) combinations — previously these fell through to the
    ``fc-only`` branch causing silent data loss for the populated
    line-counter.

    Story 3.12 review M1: zero-counter omission — ``files_changed=0`` /
    ``lines_added=0`` / ``lines_removed=0`` are treated as "no activity to
    report" and the section is omitted (returns ``None`` if all populated
    fields are zero). Distinguishes "field absent" from "zero activity"
    cleanly while not rendering a misleading ``"0 files changed."`` line.
    """
    fc = payload.files_changed
    la = payload.lines_added
    lr = payload.lines_removed
    # M1: treat 0 as "do not render" — collapse zero into None semantically.
    fc_v = fc if fc is not None and fc > 0 else None
    la_v = la if la is not None and la > 0 else None
    lr_v = lr if lr is not None and lr > 0 else None
    if fc_v is None and la_v is None and lr_v is None:
        return None
    if fc_v is not None and la_v is not None and lr_v is not None:
        return f"{fc_v} files changed, {la_v}+ / {lr_v}- lines."
    # H2: explicit (fc+la) and (fc+lr) branches before the fc-only fall-through.
    if fc_v is not None and la_v is not None:
        return f"{fc_v} files changed, {la_v}+ lines."
    if fc_v is not None and lr_v is not None:
        return f"{fc_v} files changed, {lr_v}- lines."
    if fc_v is not None:
        return f"{fc_v} files changed."
    if la_v is not None and lr_v is not None:
        return f"{la_v}+ / {lr_v}- lines."
    if la_v is not None:
        return f"{la_v} lines added."
    # Only lines_removed.
    return f"{lr_v} lines removed."


def _assemble_completed_sections(
    payload: TaskCompletedPayload,
    *,
    include_pr: bool,
    include_diff_stats: bool,
    include_tests: bool,
    include_ci_state: bool,
    include_blockers: bool,
    include_summary: bool,
) -> str:
    """Join the completion sections in spec order with ``\\n\\n`` separators.

    Header is mandatory; the optional sections honor boolean levers the
    section-drop ladder in :func:`_render_completed` flips on overflow.

    Story 3.12 AC-4: HTML-escape ``task_id`` and ``summary`` (after
    newline collapse). PR line composition delegates to
    :func:`_build_pr_line` which handles ``pr_branch`` / ``pr_url``
    escaping. Integer counters need no escape; ``ci_state`` is bound by
    ``Literal[...]`` but is HTML-escaped defensively (Story 3.10 review
    M14 — no-op on current Literals, drift-safe).
    """
    task_id_esc = html.escape(payload.task_id)

    sections: list[str] = [
        f"✅ Task {task_id_esc} complete.",
    ]
    if include_pr:
        pr_line = _build_pr_line(payload)
        if pr_line is not None:
            sections.append(pr_line)
    if include_diff_stats:
        diff_line = _build_diff_stats_line(payload)
        if diff_line is not None:
            sections.append(diff_line)
    if include_tests and payload.tests_added is not None and payload.tests_added > 0:
        # Story 3.12 review M1: ``tests_added=0`` is "no tests added in this
        # change", semantically equivalent to "field absent" for renderer
        # purposes. Omitting is more honest than rendering ``"0 tests added."``
        # which conflates "no activity" with "field absent" / pre-3.12
        # back-compat events.
        sections.append(f"{payload.tests_added} tests added.")
    if include_ci_state and payload.ci_state is not None:
        # Story 3.10 review M14 carry-forward: defense-in-depth escape on
        # the Literal-bound value (no-op for current values, drift-safe —
        # Story 3.12 review L9: the runtime cost is intentionally retained
        # so a model_construct-bypass payload carrying an unmapped string
        # still renders safely without HTML-injection risk).
        # Story 3.12 review M8: log a structured WARN when the runtime
        # ``ci_state`` falls outside the Literal-bound set. Combined with the
        # distinct ``_CI_STATE_FALLBACK_EMOJI`` (L8), SRE has both a visual
        # signal and a log signal for schema-registry drift / pre-3.12
        # emitters that ship a new state literal before this map updates.
        if payload.ci_state not in _CI_STATE_EMOJI:
            _log.warning(
                "renderer.ci_state_drift",
                value=payload.ci_state,
                expected=list(_CI_STATE_EMOJI.keys()),
            )
        emoji = _CI_STATE_EMOJI.get(payload.ci_state, _CI_STATE_FALLBACK_EMOJI)
        sections.append(f"CI: {emoji} {html.escape(payload.ci_state)}")
    if include_blockers and payload.blockers_count is not None and payload.blockers_count > 0:
        # Story 3.12 review M1: ``blockers_count=0`` is "no blockers raised",
        # which is the *normal* completion path. Omitting the section is more
        # informative than ``"0 blockers raised."``.
        sections.append(f"{payload.blockers_count} blockers raised.")
    if include_summary:
        # H11 / L17 / M12 carry-forward: collapse newlines uniformly so the
        # section separator (``\n\n``) stays unambiguous.
        summary_esc = html.escape(_collapse_newlines(payload.summary))
        sections.append(summary_esc)
    return "\n\n".join(sections)


def _render_completed(envelope: EventEnvelope) -> str:
    """Render the FR9 completion-summary message — Story 3.12 AC-2/AC-4/AC-5.

    Sections (in order, with optional ones omitted on ``None``):

      1. Header (always) — ``✅ Task <task_id> complete.``
      2. PR line (one of seven forms; omitted when all 3 PR fields ``None``).
      3. Diff stats line (one of five forms; omitted when all 3 diff
         counters ``None``).
      4. Tests line — ``<N> tests added.`` (omitted if ``tests_added`` ``None``).
      5. CI state line — ``CI: <emoji> <state>`` (omitted if ``ci_state`` ``None``).
      6. Blockers line — ``<N> blockers raised.`` (omitted if
         ``blockers_count`` ``None``).
      7. Summary (always when present) — operator-supplied free-form text.

    Length safety (AC-5) — section-drop ladder:

      Step 1: full message — return if under cap.
      Step 2: drop diff stats (longest counter line).
      Step 3: drop blockers count.
      Step 4: drop tests count.
      Step 5: drop CI state.
      Step 6: drop PR line.
      Step 7: emergency one-liner —
        ``✅ Task {task_id} complete. (message body too large; see /logs {task_id})``
        with the summary dropped. Operator can recover via ``/logs <id>``.

    The summary line is dropped LAST among optional fields because it's the
    operator-supplied human-readable description — semantically the most
    valuable field per FR9's intent.

    Story 3.10 review H9 carry-forward: when the dispatcher routes here
    but the runtime payload is not a typed
    :class:`TaskCompletedPayload` (registration race / version drift),
    a structured WARN is emitted before falling back to the placeholder.
    """
    payload = envelope.payload
    if not isinstance(payload, TaskCompletedPayload):
        # H9 carry-forward: structured warning so SRE can detect schema-
        # registry registration races / version drift.
        _log.warning(
            "renderer.payload_type_mismatch",
            event_type=envelope.type,
            expected="TaskCompletedPayload",
            actual=type(payload).__name__,
        )
        task_id = _extract_task_id(envelope) or "<unknown>"
        return f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"

    # Step 1: full message.
    text = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=True,
        include_tests=True,
        include_ci_state=True,
        include_blockers=True,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 2: drop diff stats (typically the longest counter line).
    text = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=False,
        include_tests=True,
        include_ci_state=True,
        include_blockers=True,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 3: drop blockers count.
    text = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=False,
        include_tests=True,
        include_ci_state=True,
        include_blockers=False,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 4: drop tests count.
    text = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=False,
        include_tests=False,
        include_ci_state=True,
        include_blockers=False,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 5: drop CI state.
    text = _assemble_completed_sections(
        payload,
        include_pr=True,
        include_diff_stats=False,
        include_tests=False,
        include_ci_state=False,
        include_blockers=False,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 6: drop PR line — header + summary only.
    text = _assemble_completed_sections(
        payload,
        include_pr=False,
        include_diff_stats=False,
        include_tests=False,
        include_ci_state=False,
        include_blockers=False,
        include_summary=True,
    )
    if len(text) <= _COMPLETED_MESSAGE_MAX_CHARS:
        return text

    # Step 7: emergency one-liner — even header + summary can't fit
    # together (typically because ``summary`` is pathologically long).
    # Operator can recover via /logs <id>.
    # Story 3.10 review H2 / Story 3.11 review H2 carry-forward: cap on
    # the RAW ``task_id`` BEFORE ``html.escape`` so a 64-char raw ``<``
    # task_id (which escapes to 64 × 5 = 320 chars of ``&lt;``) cannot
    # split an HTML entity mid-token when sliced at 64.
    #
    # Story 3.12 review H1: collapse newlines BEFORE the slice + escape so
    # a ``task_id`` containing ``\n`` cannot smuggle a real newline into the
    # supposedly single-line emergency message. ``_collapse_newlines`` is
    # idempotent on already-clean input.
    # _EMERGENCY_TASK_ID_MAX_CHARS = 64 — Story 3.10 H2 carry-forward (L14:
    # matches the model-boundary ``max_length=64`` ceiling on ``task_id``).
    task_id_safe = _collapse_newlines(payload.task_id)
    task_id_capped = task_id_safe[:_EMERGENCY_TASK_ID_MAX_CHARS]
    task_id_for_fallback = html.escape(task_id_capped)
    result = (
        f"✅ Task {task_id_for_fallback} complete. "
        f"(message body too large; see /logs {task_id_for_fallback})"
    )
    # Story 3.11 review H5 carry-forward: defensive final length self-check.
    # Header overhead + 64-char escaped ``task_id`` × 2 occurrences sits
    # well under the cap in practice; the clamp guards against future
    # header tweaks silently breaching the wire limit.
    if len(result) > _COMPLETED_MESSAGE_MAX_CHARS:
        result = result[:_COMPLETED_MESSAGE_MAX_CHARS]
    return result


# ---------------------------------------------------------------------------
# Renderer dispatcher (Story 3.10 AC-4)
# ---------------------------------------------------------------------------

_RenderFn = Callable[[EventEnvelope], str]

# Story 3.10 AC-4 / AC-15 + Story 3.11 AC-3 + Story 3.12 AC-3: read-only
# dispatch table (MappingProxyType — Story 3.6 review L1 + Story 3.7 H4
# carry-forward). Story 3.13 will add the task.self_recovered entry.
#
# Story 3.10 review M2: annotation tightened from ``Mapping[...]`` to
# ``MappingProxyType[...]`` so mypy enforces immutability at the type level
# (matches the runtime guarantee).
#
# Story 3.11 review L2: dict-literal insertion order is irrelevant — the
# dispatcher in ``_render`` does an O(1) ``.get(envelope.type)`` lookup by
# key, NOT a sequential scan. Entries can be added in any order.
_RENDERERS: MappingProxyType[str, _RenderFn] = MappingProxyType(
    {
        "task.approval_requested": _render_approval_request,
        "task.blocker_raised": _render_blocker_raised,
        "task.completed": _render_completed,
    }
)


def _render(envelope: EventEnvelope) -> str:
    """Dispatch by event-type to the registered renderer; fall back to placeholder.

    Story 3.10 AC-4 replaces Story 3.9's bare ``_render(task_id, event_type)``
    with an envelope-typed dispatcher. Event types not yet templated (e.g.
    ``task.execution_started``) preserve Story 3.9's placeholder shape
    (``Task <id>: <type>`` HTML-escaped — Story 3.5 H5 carry-forward).

    Story 3.10 review L1: ``envelope.type`` is registry-controlled (strings
    are gated by ``_DELIVERABLE_EVENT_TYPES`` and the schema registry). The
    ``html.escape`` call is preserved as defense-in-depth (no-op for any
    valid registered type).
    """
    renderer = _RENDERERS.get(envelope.type)
    if renderer is not None:
        return renderer(envelope)
    task_id = _extract_task_id(envelope) or "<unknown>"
    # L1: envelope.type is registry-controlled; escape preserved as
    # defense-in-depth (no-op).
    return f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"


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

        # Story 3.10 review M11: wrap the renderer call so an unexpected
        # payload-shape exception (e.g. UTF surrogate edge case in
        # ``html.escape``, or a future-version model evolution) cannot crash
        # the sink loop. Fall back to the placeholder shape and log.
        try:
            text = _render(envelope)
        except Exception as exc:  # noqa: BLE001 — best-effort, never crash the sink loop
            _log.error(
                "telegram_sink: renderer raised; falling back to placeholder",
                event_type=envelope.type,
                event_id=envelope.event_id,
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            text = f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"
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
