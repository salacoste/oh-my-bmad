"""Unit tests for :mod:`clawhip_daemon.app.main` lifecycle.

Story 11.3.7 / AC3 / H7d — verifies the /tmp/ready touch+unlink convention
(mirror of registry-state Story 2.11) wired into ``run()``:

* ``Path("/tmp/ready").touch()`` is called AFTER ``build_app`` returns the
  wired components.
* ``Path("/tmp/ready").unlink(missing_ok=True)`` is called when
  ``await sink.run(...)`` returns (in the ``finally:`` block).
* Touch precedes unlink (FIFO order — the healthcheck signal is created
  before it is destroyed).

The test stubs ``build_app`` + the sink to keep the harness hermetic
(no real httpx clients, no real telegram_sink.EventLogReader). It also
records ``Path.touch`` / ``Path.unlink`` calls scoped to ``"/tmp/ready"``
so the production /tmp on the host running the test is not mutated by
unrelated touches inside the wired-up sink (defensive — the stub sink
doesn't touch /tmp, but the recorder only fires on the healthcheck path
so other Path uses pass through untouched).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from events import SystemClock
from events.envelope import Actor
from secret_hygiene import AuditedSecret

from clawhip_daemon.app.main import run

_BOT_TOKEN_RAW: str = "0:dummytesttoken"
_REGISTRY_URL: str = "http://registry-api.test.invalid:8080"


def _make_bot_token() -> AuditedSecret:
    """Return a placeholder :class:`AuditedSecret` with ``emit=None``.

    Matches the seed-instance pattern used by ``main()`` before
    :py:meth:`AuditedBaseSettings.from_env` re-wraps the value with the
    real emit callback. ``emit=None`` means no audit envelopes fire when
    ``.value`` is read inside the test — keeps assertions focused.
    """
    return AuditedSecret(
        _BOT_TOKEN_RAW,
        secret_name="telegram_bot_token",
        emit=None,
        actor=Actor(kind="system", id="clawhip-daemon-test"),
        clock=SystemClock(),
    )


@pytest.mark.asyncio
async def test_run_touches_ready_before_sink_and_unlinks_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3: /tmp/ready is touched after build_app, unlinked after sink.run.

    Order assertion pins:
      1. touch (BEFORE awaiting sink.run)
      2. <sink runs, returns when stop_event is set>
      3. unlink (in finally)
    """
    # Record (verb, str(path)) only when the path is the healthcheck file.
    ready_calls: list[str] = []

    real_touch = Path.touch
    real_unlink = Path.unlink

    def recording_touch(self: Path, *args: Any, **kwargs: Any) -> Any:
        if str(self) == "/tmp/ready":  # noqa: S108 — same constant as production
            ready_calls.append("touch")
            return None
        return real_touch(self, *args, **kwargs)

    def recording_unlink(self: Path, *args: Any, **kwargs: Any) -> Any:
        if str(self) == "/tmp/ready":  # noqa: S108
            ready_calls.append("unlink")
            return None
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", recording_touch)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    # Stub build_app so we don't construct real httpx clients / sink.
    aclose_calls: list[str] = []

    class _FakeClient:
        async def aclose(self) -> None:
            aclose_calls.append("aclose")

    class _FakeSink:
        def __init__(self) -> None:
            self.sink_run_started: bool = False

        async def run(self, *, stop_event: asyncio.Event) -> None:
            # Touch must have happened BEFORE sink.run is awaited.
            self.sink_run_started = True
            assert ready_calls == ["touch"], (
                f"sink.run started but /tmp/ready not touched first; "
                f"recorded so far: {ready_calls!r}"
            )
            # Stop immediately so the test returns quickly; the production
            # path is identical (await sink.run until stop_event fires).
            stop_event.set()
            # Yield once so the awaiting code can observe the set.
            await asyncio.sleep(0)

    fake_sink = _FakeSink()
    fake_telegram = _FakeClient()
    fake_registry = _FakeClient()

    def fake_build_app(
        *,
        base_dir: Path,
        registry_api_url: str,
        bot_token: AuditedSecret,
    ) -> tuple[_FakeSink, _FakeClient, _FakeClient]:
        return fake_sink, fake_telegram, fake_registry

    monkeypatch.setattr("clawhip_daemon.app.main.build_app", fake_build_app)

    stop = asyncio.Event()
    await run(
        base_dir=tmp_path,
        registry_api_url=_REGISTRY_URL,
        bot_token=_make_bot_token(),
        stop_event=stop,
    )

    # Order pin: touch fired BEFORE sink.run (asserted inside fake_sink.run),
    # and unlink fired in the finally AFTER sink.run returned. FIFO of the
    # recorded list captures both invariants.
    assert ready_calls == ["touch", "unlink"], (
        f"expected /tmp/ready calls: ['touch', 'unlink']; got {ready_calls!r}"
    )
    assert fake_sink.sink_run_started, "sink.run was never awaited"
    # Belt-and-suspenders: both httpx clients were closed in finally.
    assert aclose_calls == ["aclose", "aclose"], (
        f"expected both clients to aclose; got {aclose_calls!r}"
    )


@pytest.mark.asyncio
async def test_run_unlinks_ready_even_when_sink_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3 + Epic-11 retro AI-6: unlink runs even if sink.run raises.

    Verifies the ``finally:`` block fires for both the unlink and the
    httpx-client cleanup when ``sink.run`` propagates an exception —
    no /tmp/ready file is left behind, no client handle is leaked.
    """
    ready_calls: list[str] = []
    real_touch = Path.touch
    real_unlink = Path.unlink

    def recording_touch(self: Path, *args: Any, **kwargs: Any) -> Any:
        if str(self) == "/tmp/ready":  # noqa: S108
            ready_calls.append("touch")
            return None
        return real_touch(self, *args, **kwargs)

    def recording_unlink(self: Path, *args: Any, **kwargs: Any) -> Any:
        if str(self) == "/tmp/ready":  # noqa: S108
            ready_calls.append("unlink")
            return None
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", recording_touch)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    aclose_calls: list[str] = []

    class _FakeClient:
        async def aclose(self) -> None:
            aclose_calls.append("aclose")

    class _FailingSink:
        async def run(self, *, stop_event: asyncio.Event) -> None:
            raise RuntimeError("sink_run_failure_synthetic")

    def fake_build_app(
        *,
        base_dir: Path,
        registry_api_url: str,
        bot_token: AuditedSecret,
    ) -> tuple[_FailingSink, _FakeClient, _FakeClient]:
        return _FailingSink(), _FakeClient(), _FakeClient()

    monkeypatch.setattr("clawhip_daemon.app.main.build_app", fake_build_app)

    stop = asyncio.Event()
    with pytest.raises(RuntimeError, match="sink_run_failure_synthetic"):
        await run(
            base_dir=tmp_path,
            registry_api_url=_REGISTRY_URL,
            bot_token=_make_bot_token(),
            stop_event=stop,
        )

    # unlink MUST still have fired in finally even though sink.run raised.
    assert ready_calls == ["touch", "unlink"], (
        f"finally block did not unlink /tmp/ready on sink failure; recorded: {ready_calls!r}"
    )
    # And both httpx clients must still be closed (resource cleanup
    # invariant — Epic 11 retro AI-6).
    assert aclose_calls == ["aclose", "aclose"], (
        f"expected both clients aclose on sink failure; got {aclose_calls!r}"
    )
