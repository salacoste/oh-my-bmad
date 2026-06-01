"""Story 11.3.9 / AC6 — cross-service HealthResponse wire-shape parity contract.

Two pydantic models MUST stay byte-identical on field names + Field
constraints + types:

* :class:`registry_api.routes.health.HealthResponse` (server side)
* :class:`telegram_gateway.handlers.registry_client.HealthResponseLocal` (client mirror)

The client mirror decodes JSON from ``GET /v1/health``; drift in either
direction would silently break the Telegram ``/ping`` command:

* If registry-api adds a NEW Field constraint (e.g. tighter
  ``registry_status`` pattern), a client mirror that hasn't tightened
  in lock-step would still accept the older shape and silently forward
  over-permissive data to operators.
* If a client mirror adds a NEW Field constraint that the server
  doesn't enforce, every real response from registry-api would
  ``ValidationError`` and ``/ping`` would crash with a misleading
  diagnostic.

Pattern modelled on Story 11.5.1 / AC7's
``test_key_status_client_server_shape_parity.py`` (which itself sits
in the same L9 mirror-identity canon — Epic 11 retro addendum, 4th
codebase surface after MCP-env allowlists, HMAC canonical string, and
the Story 11.3.8 ``ensure_shared_dir`` filesystem helper).

Note vs Story 11.5.1: HealthResponse has only ONE client mirror
(telegram-gateway). The console-cli ``key-status`` command has its own
mirror, but there is no ``console-cli ping`` command — so a 2-way
parity test suffices here vs Story 11.5.1's 3-way.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from registry_api.routes.health import (  # noqa: IMP001 — tests/contract/ crosses service boundaries by design
    HealthResponse as _ServerHealth,
)
from telegram_gateway.handlers.registry_client import (  # noqa: IMP001
    HealthResponseLocal as _TelegramHealth,
)


def _field_signature(field: FieldInfo) -> dict[str, Any]:
    """Extract the comparable invariants of a pydantic FieldInfo.

    Compares: annotation (type), metadata-derived constraints (min_length /
    max_length / ge / le / pattern). Ignores: description, examples,
    json_schema_extra (cosmetic). Same shape as Story 11.5.1's parity test
    so a future helper extraction is mechanical.
    """
    return {
        "annotation": field.annotation,
        "metadata": sorted(repr(m) for m in field.metadata),
    }


def _model_signature(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Per-field signature dict for a pydantic BaseModel class."""
    return {name: _field_signature(field) for name, field in model.model_fields.items()}


def test_health_field_names_identical_across_2_schemas() -> None:
    """Server + client schema must declare the same field-set.

    A drift means a future extension landed in only one schema (e.g.
    a new ``dispatch_status`` on the server that the client doesn't
    know about → silently dropped via ``extra="ignore"`` on the client,
    operators never see the field in /ping).
    """
    server_fields = set(_ServerHealth.model_fields.keys())
    telegram_fields = set(_TelegramHealth.model_fields.keys())

    assert server_fields == telegram_fields, (
        f"HealthResponse field-set drift (server vs telegram-gateway):\n"
        f"  only in server:   {sorted(server_fields - telegram_fields)}\n"
        f"  only in telegram: {sorted(telegram_fields - server_fields)}\n"
        "Update both schemas together — mirror-identity contract (L9)."
    )


def test_health_field_signatures_identical_across_2_schemas() -> None:
    """Per-field signature parity: same annotation + same Field constraints.

    Drift here means a constraint loosened/tightened in only one schema
    (e.g. server tightens ``clawhip_queue_depth`` ``le=100_000`` but
    client still allows ``le=1_000_000`` → consumer accepts values the
    server would reject; or vice versa → consumer ValidationError on
    legitimate server responses).
    """
    server_sig = _model_signature(_ServerHealth)
    telegram_sig = _model_signature(_TelegramHealth)

    for field_name in server_sig:
        assert server_sig[field_name] == telegram_sig.get(field_name), (
            f"HealthResponse field signature drift on {field_name!r} "
            f"(server vs telegram-gateway):\n"
            f"  server:   {server_sig[field_name]!r}\n"
            f"  telegram: {telegram_sig.get(field_name)!r}\n"
            "Update both schemas together — mirror-identity contract (L9)."
        )


def test_health_queue_depth_wire_clamp_pinned() -> None:
    """``clawhip_queue_depth`` is pinned to ``ge=0, le=1_000_000``.

    Story 11.3.9 / AC3 + L4 defensive upper bound: a runaway count from
    a corrupted index must NOT bypass the wire serialiser. This is a
    NAMED contract invariant separate from the parity tests — even if
    BOTH schemas drifted IN LOCKSTEP to remove the upper bound, the
    parity check alone wouldn't catch it.

    Mirrors :data:`registry_api.probes.health_probes.QUEUE_DEPTH_MAX`
    (which is the server-side defense-in-depth clamp before the wire
    serialiser). If you bump QUEUE_DEPTH_MAX, bump the Field bound here
    too AND update this test.
    """
    server_meta = _model_signature(_ServerHealth)["clawhip_queue_depth"]["metadata"]
    joined = " ".join(server_meta)
    assert "Ge(ge=0)" in joined, f"clawhip_queue_depth must have ge=0; got metadata {server_meta!r}"
    assert "Le(le=1000000)" in joined, (
        f"clawhip_queue_depth must have le=1_000_000 (Story 11.3.9 / AC3 + L4 clamp); "
        f"got metadata {server_meta!r}"
    )


def test_health_status_strings_have_64_char_upper_bound() -> None:
    """``registry_status`` + ``worker_status`` pinned to ``min_length=1, max_length=64``.

    AC5 NEVER-5xx discipline means the route always returns SOMETHING for
    these fields. ``min_length=1`` enforces non-empty (catches a future
    bug where the route returns ``""`` for an unhandled probe result).
    ``max_length=64`` is the defensive upper bound (Story 11.3.9 / H1
    note: server contract not yet finalised; a typo'd 200-char status
    would render badly in Telegram /ping).
    """
    for field_name in ("registry_status", "worker_status"):
        meta = _model_signature(_ServerHealth)[field_name]["metadata"]
        joined = " ".join(meta)
        assert "MinLen(min_length=1)" in joined, (
            f"{field_name} must have min_length=1 (AC5 non-empty guarantee); got metadata {meta!r}"
        )
        assert "MaxLen(max_length=64)" in joined, (
            f"{field_name} must have max_length=64 (defensive upper bound); got metadata {meta!r}"
        )
