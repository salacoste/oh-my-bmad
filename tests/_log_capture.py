"""Log-capture harness primitives for FR43 / NFR-S1 integration tests.

This private helper module backs the ``capture_structlog`` fixture defined in
``tests/conftest.py``. It defines the captured-record types, the structured-
field whitelist, and the two assertion helpers integration tests use to verify
that runtime sanitiser middleware (``secret_hygiene.sanitizer.redact_secrets``)
is actually wired and effective.

Source-of-truth — whitelist
---------------------------
``ALLOWED_LOG_FIELDS`` below is the canonical whitelist of structured log
fields that may appear at the **top level** of an emitted event_dict during
Phase 1. The module-level constant is the single place to update when a new
domain field is added; future stories that introduce a new field MUST extend
this constant in the same commit.

Symmetry: ``secret_hygiene.scanner.SECRET_PATTERNS`` is the single source of
truth for redaction patterns; ``ALLOWED_LOG_FIELDS`` is the single source of
truth for log-field hygiene. The harness consumes the former read-only and
exposes the latter for top-level whitelist checks.

References
----------
- ``architecture.md:416`` — required-fields list (event/level/timestamp/
  request_id/service + domain-bound contextvars).
- ``packages/secret-hygiene/src/secret_hygiene/sanitizer.py:49`` —
  ``REDACTED_SENTINEL``; tests import it rather than hard-coding the literal.
- ``packages/secret-hygiene/src/secret_hygiene/sanitizer.py:111-173`` —
  ``_redact_value`` recursion semantics that ``assert_no_plaintext_secrets``
  mirrors (dict / MutableMapping / list / tuple / set / frozenset / bytes).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from secret_hygiene.scanner import SECRET_PATTERNS

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

CapturedRecord = dict[str, Any]


class CapturedLogList(list[CapturedRecord]):
    """Thin ``list`` subtype whose elements are captured structlog event dicts.

    Subclassing ``list`` (rather than aliasing) gives the fixture a stable,
    nominal type that test authors can annotate against without leaking the
    underlying list-of-dict shape into call sites.
    """


# ---------------------------------------------------------------------------
# Whitelist — single source of truth for top-level structured-field hygiene.
# Extend this set (in the same commit) when a story legitimately introduces a
# new structured log field.
# ---------------------------------------------------------------------------

ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        # Required by architecture.md:416 — every record MUST carry these.
        "event",  # short label (positional first arg to log.info(...))
        "level",  # added by structlog.stdlib.add_log_level
        "timestamp",  # added by TimeStamper
        "request_id",  # bound via contextvars at request boundary
        "service",  # bound at service startup
        # Domain-specific fields used by current Phase-1 services.
        "task_id",
        "session_id",
        "event_id",
        "actor_kind",
        "actor_id",
        "secret_name",  # NFR-S3 audit metadata (Story 2.16); never the value.
        "schema_version",
        "idempotency_key",
        "logger",  # structlog.stdlib.add_logger_name companion
        # structlog exception-rendering fields.
        "exc_info",
        "exception",
    }
)


# ---------------------------------------------------------------------------
# Internal walker — mirrors sanitizer._redact_value recursion semantics.
# Yields (dotted_path, str_value) for every str leaf reachable from the record.
# ---------------------------------------------------------------------------

# Depth bound — matches sanitizer._MAX_DEPTH spirit; prevents runaway recursion
# on accidental cycles in test-authored records.
_MAX_DEPTH: int = 32


def _walk_strings(value: Any, path: str, depth: int = 0) -> list[tuple[str, str]]:
    """Return all ``(dotted_path, str_value)`` pairs reachable from *value*.

    Recursion mirrors :func:`secret_hygiene.sanitizer._redact_value` exactly —
    same container handling (dict, MutableMapping, list, tuple, set, frozenset),
    same depth guard. Bytes are decoded with ``errors="replace"`` so binary
    payloads carrying ASCII-shaped tokens are still scanned.
    """
    if depth > _MAX_DEPTH:
        return []
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return []
        return [(path, decoded)]
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            out.extend(_walk_strings(v, sub, depth + 1))
        return out
    # Non-dict MutableMapping (e.g. ChainMap) — same iteration as dict.
    if isinstance(value, MutableMapping):
        out2: list[tuple[str, str]] = []
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            out2.extend(_walk_strings(v, sub, depth + 1))
        return out2
    if isinstance(value, list | tuple):
        out3: list[tuple[str, str]] = []
        for idx, item in enumerate(value):
            sub = f"{path}[{idx}]"
            out3.extend(_walk_strings(item, sub, depth + 1))
        return out3
    if isinstance(value, set | frozenset):
        # Sets are unordered; report a stable synthetic path. Sorting str
        # members keeps the assertion-message deterministic for sets of strs.
        out4: list[tuple[str, str]] = []
        items = sorted(value, key=lambda x: repr(x)) if value else []
        for idx, item in enumerate(items):
            sub = f"{path}{{{idx}}}"
            out4.extend(_walk_strings(item, sub, depth + 1))
        return out4
    return []


def _scan_for_secret(text: str) -> str | None:
    """Return the name of the first ``SECRET_PATTERNS`` entry that matches *text*.

    Iterates ``SECRET_PATTERNS`` (the FIVE-pattern table at
    ``scanner.py:53-61``) read-only — never re-defines patterns. Returns
    ``None`` if no pattern fires.
    """
    for pattern_name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text) is not None:
            return pattern_name
    return None


# ---------------------------------------------------------------------------
# Public assertion helpers
# ---------------------------------------------------------------------------


def assert_no_plaintext_secrets(records: CapturedLogList) -> None:
    """Raise ``AssertionError`` if any captured record contains a plaintext secret.

    Walks every record (recursing into nested dicts / MutableMappings / lists /
    tuples / sets / frozensets / bytes — exactly mirroring ``_redact_value``)
    and tests every encountered ``str`` value against ``SECRET_PATTERNS``.

    On hit, the error message follows the AC-5 contracted format::

        AssertionError: plaintext secret detected in captured log record
            pattern: {pattern_name}
            record_index: {N}
            level: {level or '?'}
            event: {event or '?'}
            offending_path: {dotted-path-from-root}
            offending_excerpt: {value[:24] + "…" if len(value) > 24 else value}
    """
    for index, record in enumerate(records):
        for path, str_value in _walk_strings(record, ""):
            hit = _scan_for_secret(str_value)
            if hit is None:
                continue
            level = record.get("level", "?")
            event = record.get("event", "?")
            excerpt = str_value if len(str_value) <= 24 else str_value[:24] + "…"
            raise AssertionError(
                "plaintext secret detected in captured log record\n"
                f"    pattern: {hit}\n"
                f"    record_index: {index}\n"
                f"    level: {level}\n"
                f"    event: {event}\n"
                f"    offending_path: {path}\n"
                f"    offending_excerpt: {excerpt}"
            )


def assert_only_whitelisted_fields(
    records: CapturedLogList,
    whitelist: frozenset[str],
) -> None:
    """Raise ``AssertionError`` on any top-level key not in *whitelist*.

    Top-level keys only — nested payload fields are domain-owned and not
    candidates for whitelist enforcement.

    On violation::

        AssertionError: unknown log field outside whitelist
            record_index: {N}
            offending_field: {key}
            level: {level or '?'}
            event: {event or '?'}
            hint: extend ALLOWED_LOG_FIELDS in tests/_log_capture.py if intentional.
    """
    for index, record in enumerate(records):
        for key in record:
            if key in whitelist:
                continue
            level = record.get("level", "?")
            event = record.get("event", "?")
            raise AssertionError(
                "unknown log field outside whitelist\n"
                f"    record_index: {index}\n"
                f"    offending_field: {key}\n"
                f"    level: {level}\n"
                f"    event: {event}\n"
                "    hint: extend ALLOWED_LOG_FIELDS in tests/_log_capture.py if intentional."
            )
