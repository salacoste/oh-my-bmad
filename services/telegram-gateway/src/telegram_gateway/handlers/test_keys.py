"""Regression tests for the ``\\A`` / ``\\Z`` regex anchors in ``_keys.py``.

Story 9.2 pass-2 review N3: the pass-1 batch tightened the ``UUIDV7_BARE_RE``
and ``TASK_ID_PATTERN`` regex anchors from ``^...$`` to ``\\A...\\Z`` (B3) so
that a hostile value of ``<valid-uuid>\\n<garbage>`` cannot slip past the
match boundary — Python's ``$`` matches before a trailing ``\\n`` while
``\\Z`` matches only at the absolute end of the input. Without these
regression tests, a future contributor reverting to ``^/$`` would silently
re-open the bypass.

Named ``test_keys.py`` (not ``test__keys.py``) per the same convention as
the sibling ``test_errors_rfc7807.py`` — pytest plugins choke on leading
double-underscore module names.
"""

from __future__ import annotations

from telegram_gateway.handlers._keys import TASK_ID_PATTERN, UUIDV7_BARE_RE


class TestUuidV7BareReTrailingNewline:
    """Pass-2 N3: ``UUIDV7_BARE_RE`` must reject trailing-newline payloads."""

    def test_rejects_uuid_with_trailing_newline_and_garbage(self) -> None:
        """``<valid-uuid>\\n<garbage>`` must NOT match (``\\A/\\Z`` regression)."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        hostile = f"{valid_uuid}\nattacker-payload"
        assert UUIDV7_BARE_RE.match(hostile) is None, (
            f"regex MUST reject trailing newline payload; got match on: {hostile!r}"
        )

    def test_rejects_uuid_with_bare_trailing_newline(self) -> None:
        """``<valid-uuid>\\n`` must NOT match — even a single trailing newline."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        hostile = f"{valid_uuid}\n"
        assert UUIDV7_BARE_RE.match(hostile) is None, (
            f"regex MUST reject single trailing newline; got match on: {hostile!r}"
        )

    def test_accepts_clean_uuid(self) -> None:
        """Sanity: a clean UUIDv7 (no trailing garbage) still matches."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        assert UUIDV7_BARE_RE.match(valid_uuid) is not None, (
            f"regex MUST accept clean UUIDv7; got no match on: {valid_uuid!r}"
        )


class TestTaskIdPatternTrailingNewline:
    """Pass-2 N3: ``TASK_ID_PATTERN`` must reject trailing-newline payloads."""

    def test_rejects_task_id_with_trailing_newline_and_garbage(self) -> None:
        """``t-<valid-uuid>\\n<garbage>`` must NOT match."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        hostile = f"t-{valid_uuid}\nattacker-payload"
        assert TASK_ID_PATTERN.match(hostile) is None, (
            f"regex MUST reject trailing newline payload; got match on: {hostile!r}"
        )

    def test_rejects_task_id_with_bare_trailing_newline(self) -> None:
        """``t-<valid-uuid>\\n`` must NOT match — even a single trailing newline."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        hostile = f"t-{valid_uuid}\n"
        assert TASK_ID_PATTERN.match(hostile) is None, (
            f"regex MUST reject single trailing newline; got match on: {hostile!r}"
        )

    def test_accepts_clean_task_id(self) -> None:
        """Sanity: a clean t-<uuidv7> still matches."""
        valid_uuid = "01917e5c-a7d1-7000-8000-000000000000"
        clean = f"t-{valid_uuid}"
        assert TASK_ID_PATTERN.match(clean) is not None, (
            f"regex MUST accept clean t-<uuidv7>; got no match on: {clean!r}"
        )
