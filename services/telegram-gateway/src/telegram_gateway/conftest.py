"""Shared fixtures for co-located telegram-gateway tests (Story 3.1 AC-10).

Why a co-located conftest? Per Epic-2-retro Action Item #5 + the
recurring 2.16/2.17 pattern, ``tests/conftest.py`` (the repo's
top-level conftest) is NOT discoverable from ``services/**`` co-located
test files when pytest is invoked under ``rootdir=<repo>``: pytest's
conftest discovery walks UP from each test's directory stopping at the
first one with a ``__init__.py``, but the ``services/telegram-gateway/
src/telegram_gateway/`` chain has ``__init__.py`` files that block
upward traversal. We therefore re-declare the fixtures we need (the
:class:`secret.accessed` schema-registration autouse + the
:func:`fixed_clock` factory) inside this co-located conftest.

Schema-registry autouse pattern: ``packages/events/.../test_envelope.py``
runs an autouse fixture that calls ``unregister_all()`` at teardown,
emptying the registry between cases. Without re-registering
``secret.accessed`` here, the cold-start audit-count test (AC-9) fails
with ``EventSchemaUnknown('secret.accessed', '1.0.0')`` whenever it
runs after an envelope-test file. The fixture is idempotent (catches
the ValueError raised by re-registering the same key).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from events import FROZEN_EPOCH, FrozenClock
from events.schema_registry import register
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed (mirror of registry_api/test_app.py:48)
    SecretAccessedPayload,
)


def _ensure_secret_accessed_registered() -> None:
    """Idempotently register ``secret.accessed`` for both schema versions."""
    for _v in ("1.0.0", "1.0.1"):
        with contextlib.suppress(ValueError):
            register("secret.accessed", _v, SecretAccessedPayload)


# Module-import side effect: register on collection so even tests that
# instantiate :class:`AuditedSecret` at module top-level succeed.
_ensure_secret_accessed_registered()


@pytest.fixture(autouse=True)
def _re_register_secret_accessed() -> Iterator[None]:
    """Function-scoped autouse: re-register before every test.

    Mirrors the pattern from
    :mod:`packages.secret-hygiene.src.secret_hygiene.test_audited_secret`.
    """
    _ensure_secret_accessed_registered()
    yield


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH (mirrors tests/conftest.py)."""
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
