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
  teardown (real round-trip drive of the underlying generator);
  ``REDACTED_SENTINEL`` itself never matches any registered secret pattern;
  ``ALLOWED_LOG_FIELDS`` covers the architecture-required set; chain order
  pins ``redact_secrets`` strictly before the terminal capture processor;
  ``cache_logger_on_first_use is False``; ``_KEY_REDACT_SET`` membership
  for the keys this story relies on.

This is *integration scope* (real ``structlog.get_logger`` + real processor
chain). Unit-level redaction coverage lives in
``packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py`` (Story 1.7).
"""

from __future__ import annotations

import pytest
import structlog
from secret_hygiene.sanitizer import REDACTED_SENTINEL, redact_secrets
from secret_hygiene.scanner import SECRET_PATTERNS

from tests._log_capture import (
    _MAX_DEPTH,
    ALLOWED_LOG_FIELDS,
    CapturedLogList,
    assert_no_plaintext_secrets,
    assert_only_whitelisted_fields,
)
from tests.conftest import _capture_structlog_generator

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

    @pytest.fixture
    def log(self) -> structlog.stdlib.BoundLogger:
        """Class-scoped logger factory — every test in this class shares the binding."""
        return structlog.get_logger("test_log_capture")

    def test_anthropic_key_in_value_is_redacted_to_sentinel(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        log.info("auth ok", api_key=_ANTHROPIC_FIXTURE)
        assert len(capture_structlog) == 1
        rec = capture_structlog[0]
        assert rec["api_key"] == REDACTED_SENTINEL
        assert rec["event"] == "auth ok"
        # Pin presence + value of structlog-injected fields (L3).
        assert "level" in rec
        assert rec["level"] == "info"
        assert "timestamp" in rec

    def test_telegram_bot_token_in_message_is_redacted(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        # The token sits in a free-form "msg" field (not key-name redacted),
        # so this exclusively exercises value-pattern redaction.
        log.info("bot started", msg=f"token={_TELEGRAM_FIXTURE} active")
        assert len(capture_structlog) == 1
        rec = capture_structlog[0]
        # Loosened from `msg == REDACTED_SENTINEL` to a permissive substring
        # contract — the sanitizer may evolve to per-substring substitution
        # without breaking this test (M4).
        assert REDACTED_SENTINEL in rec["msg"]
        assert _TELEGRAM_FIXTURE not in rec["msg"]

    def test_github_classic_pat_in_nested_dict_is_redacted(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        log.info("creds bound", extra={"creds": {"token": _GITHUB_PAT_FIXTURE}})
        rec = capture_structlog[0]
        # 'token' is in _KEY_REDACT_SET so the sanitizer redacts the value
        # by key-name regardless of pattern.
        assert rec["extra"]["creds"]["token"] == REDACTED_SENTINEL

    def test_secret_keyed_field_with_nonsecret_value_still_redacted(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        # String value, no SECRET_PATTERNS hit — relies entirely on
        # _KEY_REDACT_SET (sanitizer.py:59-86) to redact "password" by name.
        log.info("login attempt", password="just-a-secret")
        rec = capture_structlog[0]
        assert rec["password"] == REDACTED_SENTINEL

    def test_assert_no_plaintext_secrets_passes_when_clean(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        log.info("task started", task_id="t-123")
        # Should not raise.
        assert_no_plaintext_secrets(capture_structlog)

    def test_capture_does_not_emit_to_stderr(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The terminal processor raises ``DropEvent`` so no renderer should
        # ever fire — verify nothing leaks to stderr (L14).
        log.info("silent", task_id="t-999")
        log.warning("also silent", request_id="r-1")
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_contextvar_does_not_shadow_call_kwarg(
        self,
        capture_structlog: CapturedLogList,
        log: structlog.stdlib.BoundLogger,
    ) -> None:
        # Pin the precedence contract: a contextvar-bound ``level="bogus"``
        # MUST NOT shadow the actual call's level — ``add_log_level`` writes
        # the call-site level into the event_dict (L15).
        structlog.contextvars.bind_contextvars(level="bogus")
        try:
            log.info("test")
        finally:
            structlog.contextvars.unbind_contextvars("level")
        assert len(capture_structlog) == 1
        rec = capture_structlog[0]
        assert rec["level"] == "info"


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
        # Anchor on ``Violation 1 of`` for the collect-all message format,
        # and use ``\b`` after ``leaked`` so ``leaked_token`` etc. would NOT
        # satisfy the regex (L30).
        with pytest.raises(
            AssertionError,
            match=(
                r"(?s)Violation 1 of .*plaintext secret detected.*"
                r"pattern: ANTHROPIC_API_KEY.*offending_path: leaked\b"
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
            match=(
                r"(?s)Violation 1 of .*plaintext secret detected.*"
                r"pattern: GITHUB_TOKEN_CLASSIC.*"
                r"offending_path: items\[0\]\.k\b"
            ),
        ):
            assert_no_plaintext_secrets(records)

    def test_assert_only_whitelisted_fields_fails_on_unknown_top_level(self) -> None:
        records = CapturedLogList()
        records.append({"event": "x", "level": "info", "wat": "ok"})
        with pytest.raises(
            AssertionError,
            match=(
                r"(?s)Violation 1 of .*unknown log field outside whitelist.*"
                r"offending_field: wat\b"
            ),
        ):
            assert_only_whitelisted_fields(records, ALLOWED_LOG_FIELDS)


# ===========================================================================
# Fixture-contract / harness invariants.
# ===========================================================================


@pytest.mark.integration
class TestLogCaptureFixtureContract:
    """Pin the fixture's behavioural contract for future story authors."""

    def test_fixture_round_trip_restores_config(self) -> None:
        """Drive the underlying generator manually to verify true round-trip
        restoration (H1).

        The pytest-fixture wrapper hides teardown behind the test boundary;
        driving ``_capture_structlog_generator()`` directly lets us assert
        ``before == after`` from inside a single test scope.
        """
        # 1. Set a known non-default config we expect to see restored.
        structlog.reset_defaults()
        structlog.configure(
            processors=[structlog.processors.JSONRenderer()],
            cache_logger_on_first_use=True,
        )
        # Use type-shape comparison rather than identity — deep-copying the
        # snapshot in the fixture creates equivalent-but-distinct processor
        # instances; what we care about is that the structural config came
        # back, not the same object addresses.
        before_proc_types = [type(p) for p in structlog.get_config()["processors"]]
        before_cache = structlog.get_config()["cache_logger_on_first_use"]
        # 2. Drive the fixture's underlying generator manually.
        gen = _capture_structlog_generator()
        captured = next(gen)
        try:
            structlog.get_logger("rt").info(
                "inside",
                api_key="sk-ant-FIXTURE_AAAAAAAAAA1234567890BB",
            )
            assert any(r.get("api_key") == REDACTED_SENTINEL for r in captured)
            # While the fixture is active, the chain must be the test chain,
            # not the pre-fixture chain.
            active_proc_types = [type(p) for p in structlog.get_config()["processors"]]
            assert active_proc_types != before_proc_types
        finally:
            # Drive teardown; generator must complete cleanly.
            with pytest.raises(StopIteration):
                next(gen)
        # 3. Assert restoration: same processor types, same cache flag.
        after_proc_types = [type(p) for p in structlog.get_config()["processors"]]
        after_cache = structlog.get_config()["cache_logger_on_first_use"]
        assert after_proc_types == before_proc_types
        assert after_cache == before_cache
        # Cleanup: restore pristine state for subsequent tests.
        structlog.reset_defaults()

    def test_fixture_round_trip_when_initially_unconfigured(self) -> None:
        """If structlog was UNconfigured before the fixture ran, teardown
        must leave it UNconfigured (M1's ``was_configured`` snapshot fix).
        """
        structlog.reset_defaults()
        assert not structlog.is_configured()
        gen = _capture_structlog_generator()
        next(gen)
        try:
            # Inside: fixture-installed chain is active.
            assert structlog.is_configured()
        finally:
            with pytest.raises(StopIteration):
                next(gen)
        # After: should be back to unconfigured.
        assert not structlog.is_configured()

    def test_chain_order_redact_before_capture(self, capture_structlog: CapturedLogList) -> None:
        """Pin the chain order: ``redact_secrets`` MUST appear strictly
        BEFORE the terminal capture processor (M3)."""
        cfg = structlog.get_config()
        procs = cfg["processors"]
        redact_idx = next(i for i, p in enumerate(procs) if p is redact_secrets)
        capture_idx = next(
            i for i, p in enumerate(procs) if getattr(p, "__name__", "") == "_capture"
        )
        assert redact_idx < capture_idx, (
            "redact_secrets must run before the list-capture terminal processor"
        )

    def test_chain_pins_cache_logger_on_first_use_false(
        self, capture_structlog: CapturedLogList
    ) -> None:
        """Pin ``cache_logger_on_first_use is False`` per AC-2 (L28)."""
        cfg = structlog.get_config()
        assert cfg["cache_logger_on_first_use"] is False

    def test_chain_pins_redact_secrets_by_identity(
        self, capture_structlog: CapturedLogList
    ) -> None:
        """Pin chain membership by identity, not by ``__name__`` lookup (L27)."""
        cfg = structlog.get_config()
        assert redact_secrets in cfg["processors"]

    def test_redacted_sentinel_does_not_match_any_secret_pattern(self) -> None:
        # Critical invariant: if the sentinel matched a SECRET_PATTERN regex,
        # assert_no_plaintext_secrets would fire even on a properly-redacted
        # record, making the harness unusable.
        for pattern_name, pattern in SECRET_PATTERNS.items():
            assert pattern.search(REDACTED_SENTINEL) is None, (
                f"REDACTED_SENTINEL ({REDACTED_SENTINEL!r}) matches "
                f"SECRET_PATTERNS[{pattern_name!r}] — harness is unusable."
            )

    def test_assert_no_plaintext_secrets_passes_when_sentinel_present(self) -> None:
        # Positive contract: the sentinel itself MUST NOT match any registered
        # secret pattern (L10 — moved from the negative class because this is
        # a positive harness contract, not a sanitizer-bypass simulation).
        records = CapturedLogList()
        records.append({"event": "x", "level": "info", "api_key": REDACTED_SENTINEL})
        # Should not raise.
        assert_no_plaintext_secrets(records)

    def test_allowed_log_fields_contains_architecture_required_set(self) -> None:
        # Pin the architecture.md:416 required-fields set as a non-empty
        # subset of ALLOWED_LOG_FIELDS so future edits to the whitelist
        # cannot accidentally drop a required field. Also lock the count
        # at exactly 16 so any addition forces a conscious update (L18).
        required = frozenset({"event", "level", "timestamp", "request_id", "service"})
        assert required.issubset(ALLOWED_LOG_FIELDS), (
            f"ALLOWED_LOG_FIELDS missing architecture-required fields: "
            f"{required - ALLOWED_LOG_FIELDS}"
        )
        assert len(ALLOWED_LOG_FIELDS) == 16

    def test_assert_only_whitelisted_fields_passes_with_full_whitelist(self) -> None:
        """Build a record with EVERY field in ALLOWED_LOG_FIELDS (with safe
        values) and assert the helper passes — guards against typos in the
        literal (L19)."""
        record: dict[str, object] = {f: f"val-{f}" for f in ALLOWED_LOG_FIELDS}
        records = CapturedLogList()
        records.append(record)  # type: ignore[arg-type]
        # Should not raise.
        assert_only_whitelisted_fields(records, ALLOWED_LOG_FIELDS)

    def test_assert_helpers_accept_empty_records(self) -> None:
        """Both helpers must accept an empty ``CapturedLogList`` as a no-op (L9)."""
        records = CapturedLogList()
        assert_no_plaintext_secrets(records)
        assert_only_whitelisted_fields(records, ALLOWED_LOG_FIELDS)

    def test_walker_handles_non_string_dict_keys(self) -> None:
        """Non-string dict keys (e.g. ``{1: "ok"}``) must be stringified into
        the offending_path so secrets nested under them are still surfaced (L26).
        """
        records = CapturedLogList()
        records.append(
            {
                "event": "x",
                "level": "info",
                "payload": {1: "ok", "nested": {2: _ANTHROPIC_FIXTURE}},
            }
        )
        with pytest.raises(
            AssertionError,
            match=(
                r"(?s)plaintext secret detected.*pattern: ANTHROPIC_API_KEY.*"
                r"offending_path: payload\.nested\.2\b"
            ),
        ):
            assert_no_plaintext_secrets(records)

    def test_walker_raises_on_max_depth_exceeded(self) -> None:
        """The walker MUST raise on depth overflow rather than returning empty —
        otherwise a deeply-nested secret could produce a false-clean (H3 / L32)."""
        # Build a record nested deeper than _MAX_DEPTH.
        leaf: object = _ANTHROPIC_FIXTURE
        node: dict[str, object] = {"leaf": leaf}
        for _ in range(_MAX_DEPTH + 2):
            node = {"n": node}
        records = CapturedLogList()
        records.append({"event": "x", "level": "info", "payload": node})
        with pytest.raises(AssertionError, match=r"max-depth exceeded"):
            assert_no_plaintext_secrets(records)

    def test_key_redact_set_pins_keys_this_story_relies_on(self) -> None:
        """Pin ``_KEY_REDACT_SET`` membership for the keys this story depends
        on. Future refactors that move keys out of the set will surface here
        rather than as a downstream behaviour failure (H4 / L25)."""
        from secret_hygiene.sanitizer import _KEY_REDACT_SET

        assert "password" in _KEY_REDACT_SET
        assert "api_key" in _KEY_REDACT_SET
        assert "token" in _KEY_REDACT_SET
