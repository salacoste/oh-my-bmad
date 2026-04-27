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

Whitelist case-sensitivity
--------------------------
``ALLOWED_LOG_FIELDS`` membership is case-SENSITIVE — emitters MUST use
lowercase field names. The sanitizer's ``_KEY_REDACT_SET`` uses ``.casefold()``,
but the whitelist deliberately does not, to surface accidental case drift
(e.g. ``"Request_Id"`` from header propagation) as test failures rather than
silently allowing them.

Coverage gap — custom-object leak channel
-----------------------------------------
Custom objects whose ``__repr__`` / ``__str__`` embed secrets are NOT scanned —
the walker only enters str / bytes / dict / list / tuple / set / frozenset
leaves. Services emitting non-primitive objects via structlog MUST register a
processor that redacts in ``__repr__`` or stringifies before emission. Tracked
as a future enhancement.

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

from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest

# Defensive import guard: a contributor running ``tests/unit/...`` without the
# secret-hygiene venv would otherwise fail at module collection. ``importorskip``
# downgrades that to a clean skip.
pytest.importorskip("secret_hygiene")

from secret_hygiene.scanner import SECRET_PATTERNS  # noqa: E402

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

# Depth bound — aligned with sanitizer._MAX_DEPTH (= 20). On overflow the
# walker raises rather than returning ``[]``, so a deeply-nested record cannot
# silently produce a false-clean scan.
_MAX_DEPTH: int = 20


def _walk_strings(value: Any, path: str, depth: int = 0) -> Iterator[tuple[str, str]]:
    """Yield all ``(dotted_path, str_value)`` pairs reachable from *value*.

    Recursion is at least as thorough as
    :func:`secret_hygiene.sanitizer._redact_value`: the walker descends into
    set/frozenset members of any type, while the sanitizer only inspects str
    members of sets — the asymmetry favours detection. Same depth guard
    (``_MAX_DEPTH = 20``); on overflow we raise ``AssertionError`` rather than
    returning empty so a deeply-nested secret cannot produce a false-clean.
    """
    if depth > _MAX_DEPTH:
        raise AssertionError(
            "max-depth exceeded while walking captured record at "
            f"path={path!r} depth={depth}; refusing to give a false-clean — "
            "flatten the record or split the test"
        )
    if isinstance(value, str):
        yield (path, value)
        return
    if isinstance(value, bytes):
        # ``errors="replace"`` cannot raise — no try/except wrapper needed.
        yield (path, value.decode("utf-8", errors="replace"))
        return
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            yield from _walk_strings(v, sub, depth + 1)
        return
    # Non-dict MutableMapping (e.g. ChainMap) — same iteration as dict.
    if isinstance(value, MutableMapping):
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            yield from _walk_strings(v, sub, depth + 1)
        return
    if isinstance(value, list | tuple):
        for idx, item in enumerate(value):
            sub = f"{path}[{idx}]"
            yield from _walk_strings(item, sub, depth + 1)
        return
    if isinstance(value, set | frozenset):
        # Sets are unordered; render path as ``path.<set:idx>`` (unambiguous
        # vs literal-key ``.idx`` and vs list ``[idx]``). Sort members by
        # ``repr()`` for deterministic ordering across runs.
        items = sorted(value, key=lambda x: repr(x)) if value else []
        for idx, item in enumerate(items):
            sub = f"{path}.<set:{idx}>"
            yield from _walk_strings(item, sub, depth + 1)
        return
    return


def _scan_for_secret(text: str) -> list[str]:
    """Return ALL ``SECRET_PATTERNS`` entry names that match *text*, sorted.

    Iterates ``SECRET_PATTERNS`` (the FIVE-pattern table at
    ``scanner.py:53-61``) read-only — never re-defines patterns. Returns an
    empty list if no pattern fires; otherwise an alphabetically-sorted list
    of matching pattern names, so error messages are deterministic when
    multiple patterns hit the same string.
    """
    hits = [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text) is not None]
    return sorted(hits)


# ---------------------------------------------------------------------------
# Public assertion helpers
# ---------------------------------------------------------------------------


def _format_secret_violation(
    *,
    index: int,
    record: CapturedRecord,
    path: str,
    str_value: str,
    hits: list[str],
) -> str:
    """Build the AC-5 contracted violation block (no secret material echoed)."""
    level = record.get("level") or "?"
    event = record.get("event") or "?"
    pattern_str = ",".join(hits)
    return (
        "plaintext secret detected in captured log record\n"
        f"    pattern: {pattern_str}\n"
        f"    record_index: {index}\n"
        f"    level: {level}\n"
        f"    event: {event}\n"
        f"    offending_path: {path}\n"
        f"    offending_value_length: {len(str_value)}"
    )


def _format_whitelist_violation(
    *,
    index: int,
    record: CapturedRecord,
    key: str,
) -> str:
    """Build the AC-6 contracted whitelist-violation block."""
    level = record.get("level") or "?"
    event = record.get("event") or "?"
    return (
        "unknown log field outside whitelist\n"
        f"    record_index: {index}\n"
        f"    offending_field: {key}\n"
        f"    level: {level}\n"
        f"    event: {event}\n"
        "    hint: extend ALLOWED_LOG_FIELDS in tests/_log_capture.py if intentional."
    )


def assert_no_plaintext_secrets(records: CapturedLogList) -> None:
    """Raise ``AssertionError`` if any captured record contains a plaintext secret.

    Walks every record (recursing into nested dicts / MutableMappings / lists /
    tuples / sets / frozensets / bytes — exactly mirroring ``_redact_value``)
    and tests every encountered ``str`` value against ``SECRET_PATTERNS``.

    Collects ALL violations across ALL records and raises a single
    ``AssertionError`` whose message lists each violation block, separated by
    blank lines and prefixed with ``Violation N of M:``. The harness itself
    NEVER echoes the offending secret value — only its length is reported, so
    that test failure output cannot itself violate NFR-S1.
    """
    violations: list[str] = []
    for index, record in enumerate(records):
        for path, str_value in _walk_strings(record, ""):
            hits = _scan_for_secret(str_value)
            if not hits:
                continue
            violations.append(
                _format_secret_violation(
                    index=index,
                    record=record,
                    path=path,
                    str_value=str_value,
                    hits=hits,
                )
            )
    if not violations:
        return
    total = len(violations)
    blocks = [f"Violation {i + 1} of {total}:\n{v}" for i, v in enumerate(violations)]
    raise AssertionError("\n\n".join(blocks))


def assert_only_whitelisted_fields(
    records: CapturedLogList,
    whitelist: frozenset[str],
) -> None:
    """Raise ``AssertionError`` on any top-level key not in *whitelist*.

    Top-level keys only — nested payload fields are domain-owned and not
    candidates for whitelist enforcement.

    Whitelist comparison is case-SENSITIVE — emitters MUST use lowercase field
    names. See module docstring for rationale.

    Collects ALL violations across ALL records and raises a single
    ``AssertionError`` whose message lists each violation block, separated by
    blank lines and prefixed with ``Violation N of M:``.
    """
    violations: list[str] = []
    for index, record in enumerate(records):
        for key in record:
            if key in whitelist:
                continue
            violations.append(
                _format_whitelist_violation(
                    index=index,
                    record=record,
                    key=key,
                )
            )
    if not violations:
        return
    total = len(violations)
    blocks = [f"Violation {i + 1} of {total}:\n{v}" for i, v in enumerate(violations)]
    raise AssertionError("\n\n".join(blocks))
