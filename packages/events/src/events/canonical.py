"""Canonical JSON serializer for EventEnvelope — deterministic byte output.

Architecture §line-359 mandate: canonical form = sorted keys, no whitespace,
UTF-8, ``allow_nan=False``, UTC Z-suffix ISO 8601 timestamps with millisecond
precision. Two identical envelopes MUST serialize to byte-identical output —
this is the replay-determinism guarantee the whole platform rests on.

Pydantic v2's ``model_dump_json()`` does not expose ``sort_keys``. Implementation:
``model_dump(mode="json")`` → dict → ``json.dumps(sort_keys=True, ...)``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from events.envelope import EventEnvelope
from events.errors import CanonicalSerializationError


def _default_encoder(obj: Any) -> Any:
    """JSON encoder for types stdlib json doesn't handle."""
    if isinstance(obj, datetime):
        return _datetime_to_iso_z(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _datetime_to_iso_z(dt: datetime) -> str:
    """UTC-aware datetime → ISO 8601 with millisecond precision + Z suffix.

    Architecture §line-360: ``2026-04-21T10:30:00.123Z``. Not ``+00:00``.
    """
    if dt.utcoffset() != timedelta(0):
        raise CanonicalSerializationError(f"datetime must be UTC; got offset {dt.utcoffset()}")
    # Truncate to millisecond precision (drop sub-ms microseconds).
    ms_dt = dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
    iso = ms_dt.isoformat(timespec="milliseconds")
    # isoformat outputs "...+00:00"; replace with "Z".
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def to_canonical_json(envelope: EventEnvelope) -> bytes:
    """Serialize an envelope to canonical JSON bytes.

    Deterministic: sorted keys, no whitespace, ``allow_nan=False``.

    Uses ``model_dump(mode="python")`` so datetime objects are preserved as
    Python ``datetime`` instances (enabling millisecond truncation + Z suffix
    in ``_default_encoder``) and NaN/Inf floats remain as Python floats
    (enabling ``allow_nan=False`` to raise rather than silently coerce to
    ``null`` as Pydantic's ``mode="json"`` would).
    """
    data = envelope.model_dump(mode="python", by_alias=False)
    try:
        text = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_default_encoder,
        )
    except (ValueError, TypeError) as exc:
        raise CanonicalSerializationError(str(exc)) from exc
    return text.encode("utf-8")


def _normalize_iso_z(obj: Any) -> None:
    """Walk a JSON-dumpable dict/list in place, rewriting +00:00 → Z on ISO strings."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.endswith("+00:00") and "T" in v:
                obj[k] = v[:-6] + "Z"
            else:
                _normalize_iso_z(v)
    elif isinstance(obj, list):
        for item in obj:
            _normalize_iso_z(item)


def from_canonical_json(data: bytes) -> EventEnvelope:
    """Parse canonical JSON bytes back into an EventEnvelope.

    Uses Pydantic's ``model_validate_json`` which runs all field validators
    including the regex + UTC checks.
    """
    return EventEnvelope.model_validate_json(data)
