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


def backfill_trace_id_from_request_id(
    envelope_dict: dict[str, Any],
    *,
    caller_label: str = "migrator-v1_0_0-to-v1_0_1",
) -> dict[str, Any] | None:
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

    Args:
        envelope_dict: Parsed JSONL record being back-filled.
        caller_label: Story 9.8 D6 (Epic 9 retro) — provenance label
            written to ``envelope.extensions["trace_id_synthetic_source"]``
            when this helper actually back-fills a trace_id. Defaults to
            ``"migrator-v1_0_0-to-v1_0_1"`` (the original caller); the
            subscriber back-fill path passes
            ``"subscriber-pre110-replay"``. The materializer later lifts
            this label to the new ``events.trace_id_synthetic_source``
            column so operators inspecting /trace can distinguish real
            (operator-originated) traces from synthetic ones. NOT applied
            when the no-op path returns the input dict unchanged — an
            already-valid trace_id is authoritative.

    Story 9.7 pass-2 TM-E7 / pass-3 UH-5: also ensures ``schema_version`` is
    present AND historically valid on the returned dict. Pre-1.1.0 JSONL
    records lacking an explicit ``schema_version`` field would otherwise
    silently be upgraded to the EventEnvelope default (``"1.1.0"``),
    falsifying provenance. Pass-3 UH-5 tightens the check from
    ``isinstance(_, str)`` (which accepted ``""``) and ``in {"1.0.0",
    "1.0.1", "1.1.0"}`` (only those three are known wire labels). Anything
    else — empty string, ``None``, ``"garbage"``, missing key — triggers
    tagging as ``"1.0.0"`` (the historical default) on back-fill so the
    wire label matches the on-disk record's true origin and downstream
    validation surfaces real garbage rather than silently coercing it.
    """
    existing_trace = envelope_dict.get("trace_id")
    has_valid_trace = isinstance(existing_trace, str) and is_valid_trace_id(existing_trace)
    # Pass-3 UH-5: strict whitelist of historically-known wire labels.
    # An empty string, null, or garbage value would otherwise pass the prior
    # ``isinstance(_, str)`` check (or silently upgrade null → "1.0.0").
    sv = envelope_dict.get("schema_version")
    has_valid_schema_version = isinstance(sv, str) and sv in ("1.0.0", "1.0.1", "1.1.0")

    if has_valid_trace and has_valid_schema_version:
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
        # Story 9.8 D6 (Epic 9 retro): mark the synthetic origin so the
        # materializer can route it to events.trace_id_synthetic_source.
        # ``envelope.extensions`` is the reserved forward-compatible
        # metadata channel (envelope.py docstring: "Reserved for
        # forward-compatible per-event metadata e.g. trace_id_synthetic_source").
        # We mutate the existing dict (or create an empty one) rather than
        # overwriting so callers that already populated other extensions
        # keys keep them.
        existing_ext = new_dict.get("extensions")
        ext: dict[str, Any] = dict(existing_ext) if isinstance(existing_ext, dict) else {}
        ext["trace_id_synthetic_source"] = caller_label
        new_dict["extensions"] = ext

    if not has_valid_schema_version:
        # Pre-1.1.0 records had no explicit schema_version; the historical
        # default was ``"1.0.0"`` (the value at the time those records were
        # written). Tag accordingly so provenance survives the back-fill.
        # Pass-3 UH-5: also overwrite empty-string / null / garbage values
        # so they're tagged as the historical default rather than passed
        # through to fail later validation with a less-actionable error.
        new_dict["schema_version"] = "1.0.0"

    return new_dict


__all__ = ["backfill_trace_id_from_request_id"]
