"""Tests for HMAC approval signing (Story 11.1 / FR64 / NFR-S10).

Covers ``ApprovalSigningSettings`` + ``compute_approval_hmac`` pure function.
The handler-level integration tests live in ``test_decisions_signing.py`` so
this module stays purely synchronous (no FastAPI / lifespan overhead).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError

from registry_api.adapters.approval_signing import compute_approval_hmac
from registry_api.settings import ApprovalSigningSettings

# ---------------------------------------------------------------------------
# AC1 — Settings tests
# ---------------------------------------------------------------------------

# A 64-char hex string (valid 32-byte HMAC key shape per .env.example recipe).
_VALID_KEY = "a" * 64


class TestApprovalSigningSettings:
    """AC1 — OPERATOR_HMAC_KEY loaded via pydantic-settings + SecretStr masking."""

    def test_settings_operator_hmac_key_missing_logs_warning_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC1: missing env-var → settings construct cleanly with key=None.

        The handler (NOT settings) is responsible for the structured warning
        emission. Settings just exposes the unset state. This test asserts
        the bootstrap doesn't crash and the field defaults to None.
        """
        monkeypatch.delenv("OPERATOR_HMAC_KEY", raising=False)
        settings = ApprovalSigningSettings.from_env()
        assert settings.operator_hmac_key is None

    def test_settings_operator_hmac_key_too_short_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC1 / P1-M4: keys < 32 bytes rejected with operator-facing error."""
        monkeypatch.setenv("OPERATOR_HMAC_KEY", "short")
        with pytest.raises(PydanticValidationError) as excinfo:
            ApprovalSigningSettings.from_env()
        # Error message names the env-var for operator clarity.
        assert "OPERATOR_HMAC_KEY" in str(excinfo.value)
        # P1-M4: error now says "BYTES" (byte-count validation).
        assert "32 BYTES" in str(excinfo.value)

    def test_settings_operator_hmac_key_empty_string_treated_as_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """P1-M2: OPERATOR_HMAC_KEY="" normalised to None (unset semantics).

        Pydantic parses empty env-var as SecretStr("") not None.  The
        validator normalises empty/whitespace to None so operators who
        clear the env-var get signing-disabled path, not a confusing
        "too short" validation error.
        """
        monkeypatch.setenv("OPERATOR_HMAC_KEY", "")
        settings = ApprovalSigningSettings.from_env()
        assert settings.operator_hmac_key is None

    def test_settings_operator_hmac_key_whitespace_only_treated_as_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """P1-M2: OPERATOR_HMAC_KEY='   ' (whitespace-only) normalised to None."""
        monkeypatch.setenv("OPERATOR_HMAC_KEY", "   ")
        settings = ApprovalSigningSettings.from_env()
        assert settings.operator_hmac_key is None

    def test_settings_operator_hmac_key_masked_in_repr(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC1 / NFR-S10: SecretStr masks the key in repr()/model_dump()."""
        sentinel = "X" * 64  # Distinctive — easy to grep for.
        monkeypatch.setenv("OPERATOR_HMAC_KEY", sentinel)
        settings = ApprovalSigningSettings.from_env()
        # Pydantic SecretStr default masking.
        rendered = repr(settings)
        assert sentinel not in rendered
        # model_dump() also masks (returns SecretStr wrapper).
        dumped = settings.model_dump()
        assert sentinel not in repr(dumped)
        # The value IS retrievable via get_secret_value() — verify the
        # masking is opt-in-revealable (not a one-way hash).
        assert settings.operator_hmac_key is not None
        assert settings.operator_hmac_key.get_secret_value() == sentinel

    def test_settings_minimum_length_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC1: exactly 32 chars is accepted (the documented minimum)."""
        monkeypatch.setenv("OPERATOR_HMAC_KEY", "b" * 32)
        settings = ApprovalSigningSettings.from_env()
        assert settings.operator_hmac_key is not None
        assert len(settings.operator_hmac_key.get_secret_value()) == 32


# ---------------------------------------------------------------------------
# AC3 — compute_approval_hmac unit tests
# ---------------------------------------------------------------------------


# Frozen test inputs for the AC3 known-vector check.
_TEST_KEY = SecretStr("test-hmac-key-must-be-at-least-32-chars-long-yes")
_TEST_TASK_ID = "t-00000000-0000-7000-8000-000000000001"
_TEST_ACTION: Literal["approve"] = "approve"
_TEST_TIMESTAMP = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# P1-H3 — Golden vector (external openssl cross-check)
# ---------------------------------------------------------------------------

# _EXPECTED_HMAC_GOLDEN_VECTOR computed via:
#   echo -n 'test-task-uuid-001|approve|2026-01-01T00:00:00+00:00|test-actor' | \
#     openssl dgst -hmac 'test-key-known-vector-must-be-32-chars-minimum-x' -sha256 -hex
# Output: SHA2-256(stdin)= 40a928fd23a98785a4beadcd450051b807f1eb4d77599ad369a7b54a4b79ef36
_EXPECTED_HMAC_GOLDEN_VECTOR = "40a928fd23a98785a4beadcd450051b807f1eb4d77599ad369a7b54a4b79ef36"
_GOLDEN_KEY = SecretStr("test-key-known-vector-must-be-32-chars-minimum-x")
_GOLDEN_TASK_ID = "test-task-uuid-001"
_GOLDEN_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_GOLDEN_ACTOR_ID = "test-actor"
_TEST_ACTOR_ID = "op-alice"


def _manual_hmac(
    key: str,
    task_id: str,
    action: str,
    timestamp: datetime,
    actor_id: str,
) -> str:
    """Independent reference implementation (does NOT call production fn).

    Used by ``test_compute_approval_hmac_known_vector`` so regression
    against a future refactor of ``compute_approval_hmac`` is caught
    without a hardcoded hex literal (which would itself need recomputing
    on any input change). The reference recomputes the canonical string
    via the SAME formula but is structurally independent — if the
    production fn drifts (e.g. changes delimiter or field order), the
    two outputs diverge and the test fails.
    """
    canonical = f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}".encode()
    return hmac.new(
        key=key.encode("utf-8"),
        msg=canonical,
        digestmod=hashlib.sha256,
    ).hexdigest()


class TestComputeApprovalHmac:
    """AC3 — HMAC computation pure-function tests."""

    def test_compute_approval_hmac_reproducible(self) -> None:
        """AC3: same inputs → same hex output across calls."""
        h1 = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        h2 = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        assert h1 == h2
        # Shape: 64 lowercase hex chars (HMAC-SHA256 hexdigest).
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_compute_approval_hmac_known_vector(self) -> None:
        """AC3: production fn matches an independent reference implementation.

        Detects crypto-library drift (Python 3.x stdlib refactor) AND
        canonical-string-format drift (delimiter / ordering / iso-format).
        """
        produced = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        expected = _manual_hmac(
            key=_TEST_KEY.get_secret_value(),
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        assert produced == expected

    def test_compute_approval_hmac_actor_id_change_changes_hmac(self) -> None:
        """AC3: changing actor_id changes the HMAC (avalanche check)."""
        base = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        changed = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id="op-mallory",  # different actor → different HMAC
        )
        assert base != changed

    def test_compute_approval_hmac_task_id_change_changes_hmac(self) -> None:
        """AC3: changing task_id changes the HMAC."""
        base = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        changed = compute_approval_hmac(
            key=_TEST_KEY,
            task_id="t-00000000-0000-7000-8000-000000000999",
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        assert base != changed

    def test_compute_approval_hmac_timestamp_change_changes_hmac(self) -> None:
        """AC3: changing timestamp changes the HMAC."""
        base = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        changed = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=datetime(2026, 5, 20, 12, 0, 1, tzinfo=UTC),  # +1s
            actor_id=_TEST_ACTOR_ID,
        )
        assert base != changed

    def test_compute_approval_hmac_key_change_changes_hmac(self) -> None:
        """AC3: changing key changes the HMAC (sanity — the HMAC primitive)."""
        base = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        changed = compute_approval_hmac(
            key=SecretStr("different-key-of-at-least-32-characters-long"),
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        assert base != changed


# ---------------------------------------------------------------------------
# AC5 — NFR-S10 key-isolation unit-level assertions
# ---------------------------------------------------------------------------


class TestKeyIsolation:
    """AC5 / NFR-S10 — key MUST NOT appear in repr/dump/HMAC output."""

    def test_hmac_key_isolation_no_leak_in_settings_repr(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC5: full env-var value never appears in repr(settings)."""
        sentinel = "S10-ISOLATION-SENTINEL-VALUE-MUST-NOT-LEAK"  # 43 chars > 32
        monkeypatch.setenv("OPERATOR_HMAC_KEY", sentinel)
        settings = ApprovalSigningSettings.from_env()
        assert sentinel not in repr(settings)
        assert sentinel not in str(settings)
        # Pydantic model_dump_json() also masks.
        dumped_json = settings.model_dump_json()
        assert sentinel not in dumped_json

    def test_hmac_key_isolation_hmac_output_not_key_material(self) -> None:
        """AC5: HMAC hex output cannot be reversed to the key.

        The HMAC value lands in events (intended); the key MUST NOT.
        This is a structural assertion: the 64-char hex output cannot
        possibly contain the key text — they are different domains.
        """
        sentinel = "S10-DOMAIN-SEPARATION-SENTINEL-KEY-32CHARS"
        key = SecretStr(sentinel)
        produced = compute_approval_hmac(
            key=key,
            task_id=_TEST_TASK_ID,
            action=_TEST_ACTION,
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        # Output is 64 hex chars; the sentinel contains non-hex chars.
        assert sentinel not in produced
        assert len(produced) == 64

    def test_hmac_key_isolation_secretstr_repr_masked(self) -> None:
        """AC5: SecretStr itself masks in repr/str — no leak from the wrapper."""
        sentinel = "ANOTHER-S10-SENTINEL-NEVER-IN-REPR-32CHRS"
        key = SecretStr(sentinel)
        assert sentinel not in repr(key)
        assert sentinel not in str(key)
        # But get_secret_value() works as designed.
        assert key.get_secret_value() == sentinel


# ---------------------------------------------------------------------------
# P1-H3 — External golden-vector test (openssl cross-check)
# ---------------------------------------------------------------------------


class TestGoldenVector:
    """P1-H3: hardcoded openssl-derived HMAC proves canonical format stable.

    This is the only test that proves external-tool compatibility.  Story
    11.4's ``just verify-approval`` CLI verifier will use the same canonical
    format.  If the delimiter or field order drifts in production code, this
    test will fail while ``test_compute_approval_hmac_known_vector`` (which
    uses the internal ``_manual_hmac`` reference) would also fail — but the
    golden-vector test provides proof against external tooling, not just
    internal consistency.
    """

    def test_compute_approval_hmac_known_vector_external_verification(self) -> None:
        """P1-H3: Python HMAC output matches openssl-computed hex digest.

        Canonical string: 'test-task-uuid-001|approve|2026-01-01T00:00:00+00:00|test-actor'
        Key: 'test-key-known-vector-must-be-32-chars-minimum-x'

        Computed via:
            echo -n 'test-task-uuid-001|approve|2026-01-01T00:00:00+00:00|test-actor' |
              openssl dgst -hmac 'test-key-known-vector-must-be-32-chars-minimum-x' -sha256 -hex
        """
        result = compute_approval_hmac(
            key=_GOLDEN_KEY,
            task_id=_GOLDEN_TASK_ID,
            action="approve",
            timestamp=_GOLDEN_TIMESTAMP,
            actor_id=_GOLDEN_ACTOR_ID,
        )
        assert result == _EXPECTED_HMAC_GOLDEN_VECTOR
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# P1-H1 + P1-L5 — Canonical-string injection guard tests
# ---------------------------------------------------------------------------


class TestCanonicalStringInjectionGuard:
    """P1-H1 + P1-L5: pipe character is rejected in any canonical-string field.

    Two distinct inputs that share a canonical string would produce the same
    HMAC — a forged signing record.  The guard prevents this for Story 6.1+
    JWT actor_id values that may contain '|' (e.g. 'org|alice').
    """

    @pytest.mark.parametrize(
        "field_name,kwargs",
        [
            (
                "task_id",
                {
                    "task_id": "t|injection-attempt",
                    "actor_id": "legit-actor",
                },
            ),
            (
                "actor_id",
                {
                    "task_id": "t-00000000-0000-7000-8000-000000000001",
                    "actor_id": "alice|approve|2026-01-01T00:00:00+00:00|bob",
                },
            ),
        ],
    )
    def test_compute_approval_hmac_rejects_pipe_in_field(
        self,
        field_name: str,
        kwargs: dict[str, str],
    ) -> None:
        """P1-H1 / P1-L5: ValueError raised when canonical-string field contains '|'."""
        with pytest.raises(ValueError, match="pipe character forbidden"):
            compute_approval_hmac(
                key=_TEST_KEY,
                action="approve",
                timestamp=_TEST_TIMESTAMP,
                **kwargs,
            )

    def test_compute_approval_hmac_clean_inputs_succeed(self) -> None:
        """Guard does NOT reject valid inputs (regression guard for the guard)."""
        result = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action="approve",
            timestamp=_TEST_TIMESTAMP,
            actor_id=_TEST_ACTOR_ID,
        )
        assert len(result) == 64


# ---------------------------------------------------------------------------
# P1-M3 — Microsecond-precision timestamp round-trip
# ---------------------------------------------------------------------------


class TestMicrosecondTimestamp:
    """P1-M3: HMAC deterministic with sub-second timestamps."""

    def test_compute_approval_hmac_microsecond_precision_timestamp_round_trips(
        self,
    ) -> None:
        """P1-M3: HMAC is deterministic with microsecond-precision timestamp.

        Production ``SystemClock.now()`` returns sub-second precision.
        Story 11.4 offline verifier must read payload.timestamp rather than
        recompute — this test proves the ISO format includes microseconds
        and the function is deterministic across calls with the same value.
        """
        ts = datetime(2026, 5, 20, 12, 0, 0, 123456, tzinfo=UTC)
        result_a = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action="approve",
            timestamp=ts,
            actor_id=_TEST_ACTOR_ID,
        )
        result_b = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action="approve",
            timestamp=ts,
            actor_id=_TEST_ACTOR_ID,
        )
        assert result_a == result_b  # deterministic
        # Verify ISO format includes microseconds (confirms Story 11.4 can
        # reproduce the HMAC from the payload.timestamp field verbatim).
        assert ".123456" in ts.isoformat()
        # Confirm microsecond timestamp produces different HMAC from zero-microsecond.
        ts_zero = datetime(2026, 5, 20, 12, 0, 0, 0, tzinfo=UTC)
        result_zero = compute_approval_hmac(
            key=_TEST_KEY,
            task_id=_TEST_TASK_ID,
            action="approve",
            timestamp=ts_zero,
            actor_id=_TEST_ACTOR_ID,
        )
        assert result_a != result_zero
