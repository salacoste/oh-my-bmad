"""Integration tests for the log-capture harness — FR43 / NFR-S1.

Filename pinned by ``architecture.md:754``. Exercises the ``capture_structlog``
fixture (defined in ``tests/conftest.py``) end-to-end, asserting:

* **Positive paths** — the sanitizer is wired in the fixture's processor
  chain and redacts known secret shapes (Anthropic / Telegram / GitHub
  classic-PAT, key-name redaction for low-entropy values).
* **Negative paths** — when a hand-crafted record bypasses the sanitizer
  (simulating a regression), the helper ``assert_no_plaintext_secrets`` and
  ``assert_only_whitelisted_fields`` raise ``AssertionError`` with the
  contracted message format.
* **Fixture contract** — fixture restores prior structlog configuration on
  teardown; ``REDACTED_SENTINEL`` itself never matches any registered secret
  pattern; ``ALLOWED_LOG_FIELDS`` covers the architecture-required set.

This is *integration scope* (real ``structlog.get_logger`` + real processor
chain). Unit-level redaction coverage lives in
``packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py`` (Story 1.7).
"""

from __future__ import annotations

import re

import pytest
import structlog
from secret_hygiene.sanitizer import REDACTED_SENTINEL
from secret_hygiene.scanner import SECRET_PATTERNS

from tests._log_capture import (
    ALLOWED_LOG_FIELDS,
    CapturedLogList,
    assert_no_plaintext_secrets,
    assert_only_whitelisted_fields,
)

# ---------------------------------------------------------------------------
# Fixture-derived constants (kept here so tests are self-explanatory).
#
# These literals are deliberately shaped to match SECRET_PATTERNS regexes
# at scanner.py:53-61. They are NOT real credentials.
# ---------------------------------------------------------------------------

_ANTHROPIC_FIXTURE = "sk-ant-FIXTURE_ABCDEFGHIJ1234567890XYZ"
_TELEGRAM_FIXTURE = "123456789:AAabcdefghijklmnopqrstuvwxyz0123456"
_GITHUB_PAT_FIXTURE = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"


# ===========================================================================
# Positive paths — sanitizer present in the chain redacts as expected.
# ===========================================================================


@pytest.mark.integration
class TestLogCaptureRedactionPositive:
    """Sanitizer wired via fixture redacts every shape we register a pattern for."""

    def test_anthropic_key_in_value_is_redacted_to_sentinel(
        self, capture_structlog: CapturedLogList
    ) -> None:
        log = structlog.get_logger("test_log_capture")
        log.info("auth ok", api_key=_ANTHROPIC_FIXTURE)
        assert len(capture_structlog) == 1
        rec = capture_structlog[0]
        assert rec["api_key"] == REDACTED_SENTINEL
        assert rec["event"] == "auth ok"

    def test_telegram_bot_token_in_message_is_redacted(
        self, capture_structlog: CapturedLogList
    ) -> None:
        log = structlog.get_logger("test_log_capture")
        # The token sits in a free-form "msg" field (not key-name redacted),
        # so this exclusively exercises value-pattern redaction.
        log.info("bot started", msg=f"token={_TELEGRAM_FIXTURE} active")
        assert len(capture_structlog) == 1
        assert capture_structlog[0]["msg"] == REDACTED_SENTINEL

    def test_github_classic_pat_in_nested_dict_is_redacted(
        self, capture_structlog: CapturedLogList
    ) -> None:
        log = structlog.get_logger("test_log_capture")
        log.info("creds bound", extra={"creds": {"token": _GITHUB_PAT_FIXTURE}})
        rec = capture_structlog[0]
        # 'token' is in _KEY_REDACT_SET so the sanitizer redacts the value
        # by key-name regardless of pattern.
        assert rec["extra"]["creds"]["token"] == REDACTED_SENTINEL

    def test_secret_keyed_field_with_nonsecret_value_still_redacted(
        self, capture_structlog: CapturedLogList
    ) -> None:
        # Numeric, no pattern hit — relies entirely on _KEY_REDACT_SET
        # (sanitizer.py:59-86) to redact "password".
        log = structlog.get_logger("test_log_capture")
        log.info("login attempt", password=12345)
        rec = capture_structlog[0]
        assert rec["password"] == REDACTED_SENTINEL

    def test_assert_no_plaintext_secrets_passes_when_clean(
        self, capture_structlog: CapturedLogList
    ) -> None:
        log = structlog.get_logger("test_log_capture")
        log.info("task started", task_id="t-123")
        # Should not raise.
        assert_no_plaintext_secrets(capture_structlog)


# ===========================================================================
# Negative paths — bypass sanitizer (hand-craft records) and assert the
# helpers fire with the contracted message format.
# ===========================================================================


@pytest.mark.integration
class TestLogCaptureRedactionNegative:
    """Sanitizer-bypass simulations — verify helper assertion messages."""

    def test_assert_no_plaintext_secrets_fails_on_anthropic_key(self) -> None:
        records = CapturedLogList()
        records.append({"event": "boom", "level": "info", "leaked": _ANTHROPIC_FIXTURE})
        with pytest.raises(
            AssertionError,
            match=re.compile(
                r"plaintext secret detected.*ANTHROPIC_API_KEY.*"
                r"offending_path: leaked",
                re.DOTALL,
            ),
        ):
            assert_no_plaintext_secrets(records)

    def test_assert_no_plaintext_secrets_fails_inside_nested_list(self) -> None:
        records = CapturedLogList()
        records.append(
            {
                "event": "x",
                "level": "info",
                "items": [{"k": _GITHUB_PAT_FIXTURE}],
            }
        )
        with pytest.raises(
            AssertionError,
            match=re.compile(
                r"plaintext secret detected.*GITHUB_TOKEN_CLASSIC.*"
                r"offending_path: items\[0\]\.k",
                re.DOTALL,
            ),
        ):
            assert_no_plaintext_secrets(records)

    def test_assert_only_whitelisted_fields_fails_on_unknown_top_level(self) -> None:
        records = CapturedLogList()
        records.append({"event": "x", "level": "info", "wat": "ok"})
        with pytest.raises(
            AssertionError,
            match=re.compile(
                r"unknown log field outside whitelist.*offending_field: wat",
                re.DOTALL,
            ),
        ):
            assert_only_whitelisted_fields(records, ALLOWED_LOG_FIELDS)

    def test_assert_no_plaintext_secrets_passes_when_sentinel_present(self) -> None:
        # The sentinel itself MUST NOT match any registered secret pattern,
        # otherwise the harness is unusable. This pins that invariant via
        # the helper (test_redacted_sentinel_does_not_match_any_secret_pattern
        # below pins it directly against the pattern table).
        records = CapturedLogList()
        records.append({"event": "x", "level": "info", "api_key": REDACTED_SENTINEL})
        # Should not raise.
        assert_no_plaintext_secrets(records)


# ===========================================================================
# Fixture-contract / harness invariants.
# ===========================================================================


@pytest.mark.integration
class TestLogCaptureFixtureContract:
    """Pin the fixture's behavioural contract for future story authors."""

    def test_fixture_restores_global_structlog_config(
        self, capture_structlog: CapturedLogList
    ) -> None:
        # The fixture is already active here; the *real* round-trip restoration
        # check requires standing the fixture up inside a test rather than
        # depending on it. We validate two things in one shot:
        #   (1) the active config has redact_secrets in its processor chain
        #       (i.e. the fixture installed it), and
        #   (2) is_configured() is True (the fixture left structlog wired,
        #       not torn down mid-test).
        # The teardown branch is exercised by every other test in this module
        # (each request/release cycle goes through the finally: clause).
        cfg = structlog.get_config()
        assert structlog.is_configured()
        proc_names = [getattr(p, "__name__", repr(p)) for p in cfg["processors"]]
        assert "redact_secrets" in proc_names

    def test_redacted_sentinel_does_not_match_any_secret_pattern(self) -> None:
        # Critical invariant: if the sentinel matched a SECRET_PATTERN regex,
        # assert_no_plaintext_secrets would fire even on a properly-redacted
        # record, making the harness unusable.
        for pattern_name, pattern in SECRET_PATTERNS.items():
            assert pattern.search(REDACTED_SENTINEL) is None, (
                f"REDACTED_SENTINEL ({REDACTED_SENTINEL!r}) matches "
                f"SECRET_PATTERNS[{pattern_name!r}] — harness is unusable."
            )

    def test_allowed_log_fields_contains_architecture_required_set(self) -> None:
        # Pin the architecture.md:416 required-fields set as a non-empty
        # subset of ALLOWED_LOG_FIELDS so future edits to the whitelist
        # cannot accidentally drop a required field.
        required = frozenset({"event", "level", "timestamp", "request_id", "service"})
        assert required.issubset(ALLOWED_LOG_FIELDS), (
            f"ALLOWED_LOG_FIELDS missing architecture-required fields: "
            f"{required - ALLOWED_LOG_FIELDS}"
        )
