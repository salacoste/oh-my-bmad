"""Pre-1.1.0 trace_id back-fill helper (Story 9.7 pass-2 TH-B5).

Single source of truth for the back-fill rule applied by BOTH:

* :mod:`scripts.migrator.cli` — offline file-level rewrite
  (``v1.0.0`` → ``v1.0.1``).
* :mod:`registry_state.adapters.event_log` — online subscriber-side
  injection on JSONL read (``_parse_with_pre110_backfill``).

Before this helper, the two paths had independent back-fill rules that
could subtly diverge (e.g. one accepting an ``e-<uuidv7>`` request_id
while the other rejected it). Determinism contract: same input
envelope dict produces the same output via either caller.

Story 9.7 pass-2 TH-B2 (Q6 decision a): accepts ``e-<uuidv7>`` shaped
request_ids by stripping the ``e-`` prefix before assigning to
``trace_id``. Pre-9.1 envelopes used ``e-`` prefixes for request_id
that look identical to event_id shapes; the bare UUIDv7 is what the
Story-9.1 validator expects.
"""

from __future__ import annotations

import re
from typing import Any

from events.envelope import is_valid_trace_id

# Match either bare UUIDv7 OR ``e-<uuidv7>`` (the pre-9.1 request_id shape).
# Kept in sync with packages/events/src/events/envelope.py canonical patterns.
_BACKFILL_UUIDV7_RE = re.compile(
    r"\A(?:e-)?[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


def backfill_trace_id_from_request_id(envelope_dict: dict[str, Any]) -> dict[str, Any] | None:
    """Inject ``trace_id`` derived from ``request_id`` when missing/invalid.

    Returns:
        A NEW dict with ``trace_id`` populated when back-fill succeeded.
        Returns the input dict unchanged (same object) when ``trace_id`` is
        already valid AND ``schema_version`` is explicit. Returns ``None``
        when neither ``trace_id`` nor ``request_id`` can produce a valid
        bare UUIDv7 (caller decides whether to skip-with-warning or raise).

    The back-fill is deterministic: same input ``request_id`` always
    produces the same ``trace_id`` so the subscriber + migrator paths
    converge on the same value.

    Story 9.7 pass-2 TM-E7: also ensures ``schema_version`` is present on
    the returned dict. Pre-1.1.0 JSONL records lacking an explicit
    ``schema_version`` field would otherwise silently be upgraded to the
    EventEnvelope default (``"1.1.0"``), falsifying provenance. We tag
    them ``"1.0.0"`` (the historical default) on back-fill so the wire
    label matches the on-disk record's true origin.
    """
    existing_trace = envelope_dict.get("trace_id")
    has_valid_trace = isinstance(existing_trace, str) and is_valid_trace_id(existing_trace)
    has_schema_version = isinstance(envelope_dict.get("schema_version"), str)

    if has_valid_trace and has_schema_version:
        return envelope_dict

    new_dict = dict(envelope_dict)

    if not has_valid_trace:
        raw_request_id = envelope_dict.get("request_id")
        if not isinstance(raw_request_id, str) or not raw_request_id:
            return None
        if not _BACKFILL_UUIDV7_RE.match(raw_request_id):
            # The Story 9.1 ``tg:<update_id>`` shape is also a valid trace_id
            # but is NOT a valid request_id shape in any historical envelope,
            # so we don't need to handle it here.
            return None
        # Strip ``e-`` prefix if present (Q6 decision a): pre-9.1 request_id
        # shape was ``e-<uuidv7>``; the bare UUIDv7 is what the Story-9.1
        # validator accepts as a trace_id.
        bare_uuid = raw_request_id.removeprefix("e-")
        if not is_valid_trace_id(bare_uuid):
            return None
        new_dict["trace_id"] = bare_uuid

    if not has_schema_version:
        # Pre-1.1.0 records had no explicit schema_version; the historical
        # default was ``"1.0.0"`` (the value at the time those records were
        # written). Tag accordingly so provenance survives the back-fill.
        new_dict["schema_version"] = "1.0.0"

    return new_dict


__all__ = ["backfill_trace_id_from_request_id"]
