"""Tests for WorkerSettings defaults and env overrides.

Story 9.6 review pass-1 H6 / M9 / M12: ambient trace_id env vars cleared per
test; invalid-shape parametrization covers CRLF / NULL / ZWJ / RTL override /
embedded whitespace / lowercase prefix / surrogate-like / overflow / negative;
``resolve_trace_id`` caching verified by patching ``new_uuid7`` and asserting
``call_count == 1`` (mocking the eager-resolve path in ``model_post_init``).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import structlog.testing
from events.envelope import is_valid_trace_id
from events.ids import new_uuid7

from worker_wrapper.app.config import WorkerSettings

# Story 9.4 / 9.5 lesson: keep DeprecationWarnings strict at the test-module
# boundary so any new callsite-warning ingress is caught immediately.
pytestmark = pytest.mark.filterwarnings("error::DeprecationWarning")

# Story 9.6 review pass-2 PH5 — the autouse env-cleaning fixture lives in
# ``worker_wrapper/conftest.py`` so we no longer duplicate it here.


class TestWorkerSettingsDefaults:
    def test_task_registry_defaults(self) -> None:
        s = WorkerSettings()
        assert s.task_registry_command == "python"
        assert s.task_registry_args == ["-m", "task_registry_mcp"]

    def test_session_registry_defaults(self) -> None:
        s = WorkerSettings()
        assert s.session_registry_command == "python"
        assert s.session_registry_args == ["-m", "session_registry_mcp"]

    def test_clawhip_bridge_defaults(self) -> None:
        s = WorkerSettings()
        assert s.clawhip_bridge_command == "python"
        assert s.clawhip_bridge_args == ["-m", "clawhip_bridge_mcp"]

    def test_registry_db_path_default_empty(self) -> None:
        s = WorkerSettings()
        assert s.registry_db_path == ""

    def test_ready_file_path_default(self) -> None:
        s = WorkerSettings()
        assert s.ready_file_path == ""


class TestWorkerSettingsEnvOverrides:
    def test_task_registry_command_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_REGISTRY_COMMAND": "/usr/bin/python3"}):
            s = WorkerSettings()
            assert s.task_registry_command == "/usr/bin/python3"

    def test_task_registry_args_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_TASK_REGISTRY_ARGS": '["-m", "custom_mcp"]'}):
            s = WorkerSettings()
            assert s.task_registry_args == ["-m", "custom_mcp"]

    def test_clawhip_bridge_command_override(self) -> None:
        with patch.dict(os.environ, {"WORKER_CLAWHIP_BRIDGE_COMMAND": "node"}):
            s = WorkerSettings()
            assert s.clawhip_bridge_command == "node"
            assert s.session_registry_command == "python"


# ---------------------------------------------------------------------------
# Story 9.6 / FR59 — trace_id propagation
# ---------------------------------------------------------------------------


class TestWorkerSettingsTraceId:
    """trace_id field + resolve_trace_id behaviour (Story 9.6 AC1, AC2, AC5)."""

    def test_settings_accepts_valid_uuidv7_trace_id(self) -> None:
        """AC1: a bare UUIDv7 from WORKER_TRACE_ID is accepted verbatim."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        assert s.trace_id == tid
        assert s.trace_id is not None
        assert is_valid_trace_id(s.trace_id) is True

    def test_settings_accepts_valid_tg_form_trace_id(self) -> None:
        """AC1: ``tg:<update_id>`` Telegram-derived form is accepted."""
        with patch.dict(os.environ, {"WORKER_TRACE_ID": "tg:42"}):
            s = WorkerSettings()
        assert s.trace_id == "tg:42"

    def test_settings_rejects_invalid_trace_id_with_warning(self) -> None:
        """AC2: invalid shape → WARNING log + field reset to None (no crash)."""
        with (
            structlog.testing.capture_logs() as cap,
            patch.dict(os.environ, {"WORKER_TRACE_ID": "bad-format"}),
        ):
            s = WorkerSettings()
        assert s.trace_id is None
        # WARNING about the invalid trace_id must be present.
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        # Review pass-1 H8: preview uses repr() — should escape into a quoted form.
        assert warnings[0]["value_preview"] == repr("bad-format")

    def test_settings_silent_when_trace_id_absent(self) -> None:
        """AC2: absent env var → no warning, trace_id is None silently."""
        # _clean_trace_id_env fixture already cleared the env vars.
        with structlog.testing.capture_logs() as cap:
            s = WorkerSettings()
        assert s.trace_id is None
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert warnings == []

    def test_settings_logs_warning_for_empty_string(self) -> None:
        """Story 9.6 review pass-1 M2: empty string is "present-but-invalid"
        (spawner bug), so logs a WARNING — not silent like absent."""
        with (
            structlog.testing.capture_logs() as cap,
            patch.dict(os.environ, {"WORKER_TRACE_ID": ""}),
        ):
            s = WorkerSettings()
        assert s.trace_id is None
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert len(warnings) == 1
        assert warnings[0]["reason"] == "empty_string"

    # Story 9.6 review pass-1 M9 — parametrize across the full invalid-shape
    # corpus. Every entry must produce a WARNING event and reset trace_id to
    # None. NB: values are passed via the constructor (not the env var) because
    # POSIX environ rejects embedded NULL bytes; the validator path is the
    # same regardless of source (AliasChoices accepts kw or env).
    @pytest.mark.parametrize(
        "raw_value",
        [
            "tg:42\r\n",  # CRLF tail (smuggling)
            "tg:42\n",  # bare LF
            "tg:42\r",  # bare CR
            "tg:42\x00",  # NULL byte
            "tg:42‍",  # zero-width joiner
            "tg:42‮",  # RTL override
            "tg:42 ",  # trailing space
            " tg:42",  # leading space
            "tg: 42",  # embedded space
            "TG:42",  # lowercase canonical only (uppercase prefix rejected)
            "tg:42\t",  # embedded tab
            "tg:0",  # zero update_id (rejected by Story 9.1)
            "tg:" + "9" * 20,  # > int64 max (overflow)
            "tg:-1",  # negative
            "tg:01",  # leading zero
            "tg:",  # empty update_id
            "not-a-uuid-or-tg",  # arbitrary garbage
            "01917e5c-a7d1-7000-8abc-XXXXXXXXXXXX",  # malformed UUIDv7 (hex)
        ],
    )
    def test_settings_rejects_invalid_shape_with_warning(self, raw_value: str) -> None:
        with structlog.testing.capture_logs() as cap:
            s = WorkerSettings(trace_id=raw_value)
        assert s.trace_id is None
        warnings = [
            entry
            for entry in cap
            if entry.get("event") == "worker_trace_id_invalid_will_mint_fresh"
        ]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        # Review pass-1 H8: preview is repr()-escaped — control chars / RTL /
        # ZWJ etc. become escape sequences, not literal bytes in the log line.
        # Review pass-2 PM7 — assert the SHAPE of the preview (no smuggled
        # control chars) rather than just its open-quote.  A regression that
        # strips ``repr()`` and uses raw ``value[:80]`` would have passed
        # ``preview.startswith("'")`` on alphanumeric inputs.
        preview = warnings[0]["value_preview"]
        assert isinstance(preview, str)
        assert "\r" not in preview
        assert "\n" not in preview
        assert "\x00" not in preview

    # Review pass-2 PM2 — ``test_settings_rejects_trace_id_with_crlf`` was
    # removed: it is fully covered by the parametrized
    # ``test_settings_rejects_invalid_trace_id_with_warning`` corpus above,
    # and the legacy version had ``capture_logs()`` with no assertion on the
    # captured records (M9 was added to fix that asymmetry).

    def test_resolve_trace_id_returns_set_value(self) -> None:
        """AC5: when a valid trace_id is supplied, resolve returns it verbatim."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        assert s.resolve_trace_id() == tid

    def test_resolve_trace_id_mints_uuidv7_when_absent(self) -> None:
        """AC2/AC5: absent input → fresh UUIDv7 minted on demand."""
        s = WorkerSettings()
        resolved = s.resolve_trace_id()
        assert is_valid_trace_id(resolved) is True

    def test_resolve_trace_id_caches_minted_value(self) -> None:
        """AC5: minted ONCE per WorkerSettings instance (per-invocation singleton)."""
        s = WorkerSettings()
        first = s.resolve_trace_id()
        second = s.resolve_trace_id()
        third = s.resolve_trace_id()
        assert first == second == third

    def test_resolve_trace_id_caches_supplied_value(self) -> None:
        """AC5: supplied-value path also caches (no re-validation cost)."""
        tid = new_uuid7()
        with patch.dict(os.environ, {"WORKER_TRACE_ID": tid}):
            s = WorkerSettings()
        first = s.resolve_trace_id()
        second = s.resolve_trace_id()
        assert first == second == tid

    def test_resolve_trace_id_calls_new_uuid7_once_when_absent(self) -> None:
        """Story 9.6 review pass-1 M12: when no trace_id is supplied, the
        eager-resolve in ``model_post_init`` calls ``new_uuid7`` exactly
        once — even across repeated ``resolve_trace_id()`` reads."""
        with patch(
            "worker_wrapper.app.config.new_uuid7",
            wraps=new_uuid7,
        ) as spy:
            s = WorkerSettings()
            first = s.resolve_trace_id()
            second = s.resolve_trace_id()
            third = s.resolve_trace_id()
        assert spy.call_count == 1
        assert first == second == third

    # Story 9.6 review pass-1 M7 — AliasChoices accepts three env var names.
    def test_trace_id_accepts_omb_worker_trace_id_alias(self) -> None:
        tid = new_uuid7()
        with patch.dict(os.environ, {"OMB_WORKER_TRACE_ID": tid}, clear=False):
            s = WorkerSettings()
        assert s.trace_id == tid

    def test_trace_id_accepts_omb_trace_id_alias(self) -> None:
        tid = new_uuid7()
        with patch.dict(os.environ, {"OMB_TRACE_ID": tid}, clear=False):
            s = WorkerSettings()
        assert s.trace_id == tid

    # Story 9.6 review pass-2 PM10 — alias-priority is canonical-wins.
    def test_alias_priority_canonical_wins_when_multiple_set(self) -> None:
        """When all three alias env vars are set with distinct valid UUIDs,
        the canonical ``WORKER_TRACE_ID`` wins per ``AliasChoices`` order."""
        a = new_uuid7()
        b = new_uuid7()
        c = new_uuid7()
        # Sanity: distinct values so the assertion is meaningful.
        assert a != b != c
        with patch.dict(
            os.environ,
            {
                "WORKER_TRACE_ID": a,
                "OMB_WORKER_TRACE_ID": b,
                "OMB_TRACE_ID": c,
            },
            clear=False,
        ):
            s = WorkerSettings()
        assert s.trace_id == a

    # Story 9.6 review pass-2 PH1 — empty canonical env var no longer
    # blackholes a valid fallback alias.  Previously ``AliasChoices`` picked
    # ``WORKER_TRACE_ID=""`` (alias-first-wins on presence) which then
    # failed shape validation and silently dropped to None.
    def test_empty_worker_trace_id_falls_through_to_omb_trace_id(self) -> None:
        valid = new_uuid7()
        with patch.dict(
            os.environ,
            {
                "WORKER_TRACE_ID": "",  # spawner shell idiom ${VAR:-}
                "OMB_TRACE_ID": valid,
            },
            clear=False,
        ):
            s = WorkerSettings()
        assert s.trace_id == valid


class TestWorkerEmitTraceIdFlag:
    """Story 9.6 review pass-1 H2 — feature flag default-OFF behaviour."""

    def test_flag_defaults_off(self) -> None:
        s = WorkerSettings()
        assert s.emit_trace_id_flag is False

    def test_flag_enabled_via_env(self) -> None:
        with patch.dict(os.environ, {"WORKER_EMIT_TRACE_ID_FLAG": "1"}):
            s = WorkerSettings()
        assert s.emit_trace_id_flag is True

    def test_flag_enabled_via_constructor(self) -> None:
        s = WorkerSettings(emit_trace_id_flag=True)
        assert s.emit_trace_id_flag is True

    # Story 9.6 review pass-2 PH7 — backwards-compat alias also accepted.
    def test_flag_enabled_via_legacy_double_prefix_alias(self) -> None:
        """``WORKER_WORKER_EMIT_TRACE_ID_FLAG`` is kept as a backwards-compat
        alias so deployments that already export the legacy name keep
        working through PH7's rename."""
        with patch.dict(
            os.environ,
            {"WORKER_WORKER_EMIT_TRACE_ID_FLAG": "1"},
            clear=False,
        ):
            s = WorkerSettings()
        assert s.emit_trace_id_flag is True
