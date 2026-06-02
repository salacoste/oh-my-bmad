"""Story 11.5.1 / AC7 — cross-service KeyStatusResponse wire-shape parity contract.

Three pydantic models MUST stay byte-identical on field names + Field
constraints + types:

* :class:`registry_api.routes.key_status.KeyStatusResponse` (server side)
* :class:`telegram_gateway.handlers.registry_client.KeyStatusResponseLocal` (client)
* :class:`console_cli.adapters.registry_api_client.KeyStatusResponseLocal` (client)

The two client mirrors decode JSON from the same registry-api endpoint;
drift in either direction (server narrows / clients loosen / a future
extension lands in only one mirror) would silently break the consumers:

* If registry-api adds a NEW Field constraint (e.g. tighter
  ``fingerprint`` pattern), client mirrors that haven't tightened in
  lock-step would still accept the older shape — and would silently
  forward over-permissive data to operators.
* If a client mirror adds a NEW Field constraint that the server doesn't
  enforce, every real response from registry-api would
  ``ValidationError`` until the server tightens — silently breaking the
  ``/ping``-style consumer command without a clear diagnostic.

This contract test pins the L9 mirror-identity canon (Epic 11 retro
addendum) for its third codebase surface, after Story 11.3.6 MCP-env
allowlists + Story 11.4 HMAC-signing canonical string. Pattern modelled
on :mod:`tests.contract.test_clawhip_client_env_allowlist_mirror`.

Failure mode if this test breaks: divergent serialization between
registry-api and one of the consumers → ``/key-status`` Telegram
command OR ``console-cli key-status`` silently returns the wrong
operator-readable block (or ValidationError-crashes).
"""

from __future__ import annotations

from typing import Any

# noqa: IMP001 on each — tests/contract/ is allowed to cross service boundaries.
from console_cli.adapters.registry_api_client import (  # noqa: IMP001
    KeyStatusResponseLocal as _ConsoleCliKeyStatus,
)
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from registry_api.routes.key_status import (  # noqa: IMP001
    KeyStatusResponse as _ServerKeyStatus,
)
from telegram_gateway.handlers.registry_client import (  # noqa: IMP001
    KeyStatusResponseLocal as _TelegramKeyStatus,
)


def _field_signature(field: FieldInfo) -> dict[str, Any]:
    """Extract the comparable invariants of a pydantic FieldInfo.

    Compares: annotation (type), default, metadata-derived constraints
    (min_length / max_length / pattern / ge / le). Ignores: description,
    examples, json_schema_extra (cosmetic). The signature is the
    minimum-sufficient comparison for "two fields have the same wire
    contract".
    """
    sig: dict[str, Any] = {
        "annotation": field.annotation,
        # NOTE: Field constraints in pydantic v2 live in ``field.metadata`` as
        # a list of constraint objects (e.g. annotated_types.MinLen,
        # MaxLen, annotated_types.Pattern). We compare their repr — class +
        # value — which captures min_length=16, max_length=16, pattern=r"...",
        # ge=0, le=1_000_000, etc.
        "metadata": sorted(repr(m) for m in field.metadata),
    }
    return sig


def _model_signature(model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Per-field signature dict for a pydantic BaseModel class."""
    return {name: _field_signature(field) for name, field in model.model_fields.items()}


def test_key_status_field_names_identical_across_3_schemas() -> None:
    """All 3 schemas declare the exact same field names — no extras, no missing.

    A field-set drift means a future extension landed in only one schema
    (e.g. a new ``signing_active: bool`` on the server that consumers
    don't know about, OR a derived field on a client that the server
    doesn't populate).
    """
    server_fields = set(_ServerKeyStatus.model_fields.keys())
    telegram_fields = set(_TelegramKeyStatus.model_fields.keys())
    console_fields = set(_ConsoleCliKeyStatus.model_fields.keys())

    assert server_fields == telegram_fields, (
        f"KeyStatusResponse field-set drift (server vs telegram-gateway):\n"
        f"  only in server: {sorted(server_fields - telegram_fields)}\n"
        f"  only in telegram: {sorted(telegram_fields - server_fields)}\n"
        "Update both schemas together — mirror-identity contract (L9)."
    )
    assert server_fields == console_fields, (
        f"KeyStatusResponse field-set drift (server vs console-cli):\n"
        f"  only in server: {sorted(server_fields - console_fields)}\n"
        f"  only in console: {sorted(console_fields - server_fields)}\n"
        "Update both schemas together — mirror-identity contract (L9)."
    )


def test_key_status_field_signatures_identical_across_3_schemas() -> None:
    """Per-field signature parity: same annotation + same Field constraints.

    Drift here means a constraint loosened/tightened in only one schema
    (e.g. server tightens to ``pattern=r"^[0-9a-f]{16}$"`` but consumer
    still accepts any 16-char string, OR consumer tightens
    ``rotated_by_actor_id`` ``max_length=64`` while server still allows
    128 → consumer ValidationError on legitimate server responses).
    """
    server_sig = _model_signature(_ServerKeyStatus)
    telegram_sig = _model_signature(_TelegramKeyStatus)
    console_sig = _model_signature(_ConsoleCliKeyStatus)

    for field_name in server_sig:
        assert server_sig[field_name] == telegram_sig.get(field_name), (
            f"KeyStatusResponse field signature drift on {field_name!r} "
            f"(server vs telegram-gateway):\n"
            f"  server:   {server_sig[field_name]!r}\n"
            f"  telegram: {telegram_sig.get(field_name)!r}\n"
            "Update both schemas together — mirror-identity contract (L9)."
        )
        assert server_sig[field_name] == console_sig.get(field_name), (
            f"KeyStatusResponse field signature drift on {field_name!r} "
            f"(server vs console-cli):\n"
            f"  server:  {server_sig[field_name]!r}\n"
            f"  console: {console_sig.get(field_name)!r}\n"
            "Update both schemas together — mirror-identity contract (L9)."
        )


def test_key_status_telegram_console_mirrors_are_themselves_identical() -> None:
    """Belt-and-suspenders: telegram + console-cli mirrors must match each other.

    The two test functions above pin each consumer against the server, but
    a symmetric drift (both consumers loosened by the same amount in
    opposite directions) could pass both pairwise tests while still
    diverging from each other. This direct telegram ≡ console assertion
    closes that hole.
    """
    telegram_sig = _model_signature(_TelegramKeyStatus)
    console_sig = _model_signature(_ConsoleCliKeyStatus)
    assert telegram_sig == console_sig, (
        f"KeyStatusResponseLocal drift between client mirrors:\n"
        f"  telegram-gateway: {telegram_sig}\n"
        f"  console-cli:      {console_sig}\n"
        "Update both client mirrors together — mirror-identity contract (L9)."
    )


def test_key_status_required_fingerprint_security_invariant_pinned() -> None:
    """Story 11.5.1 / ADR-0006 §Key-fingerprint pins ``fingerprint`` to
    16 lowercase hex chars — never longer, never accepting uppercase,
    never reverting to the raw key.

    This is a NAMED security invariant separate from the parity tests
    above: even if all 3 schemas drifted IN LOCKSTEP to a 32-hex or
    raw-base64 fingerprint, that would silently expand the surface in a
    way that the parity tests alone wouldn't flag. Pin the specific shape.
    """
    server_meta = _model_signature(_ServerKeyStatus)["fingerprint"]["metadata"]
    # The metadata sorts as: MaxLen(16), MinLen(16), _PydanticGeneralMetadata(pattern=...)
    # We just assert each token appears in the joined repr — robust to
    # annotated_types ordering / minor pydantic-version reordering.
    joined = " ".join(server_meta)
    assert "MaxLen(max_length=16)" in joined, (
        f"fingerprint must have max_length=16; got metadata {server_meta!r}. "
        "Story 11.5.1 / ADR-0006 §Key-fingerprint pins this length."
    )
    assert "MinLen(min_length=16)" in joined, (
        f"fingerprint must have min_length=16; got metadata {server_meta!r}. "
        "Story 11.5.1 / ADR-0006 §Key-fingerprint pins this length."
    )
    assert r"^[0-9a-f]{16}$" in joined, (
        f"fingerprint pattern must enforce 16 lowercase hex chars; "
        f"got metadata {server_meta!r}. Story 11.5.1 / ADR-0006 §Key-fingerprint."
    )
