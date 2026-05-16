"""Per-invocation correlation identifiers minted at console-cli command entry.

Each `oh-my-bmad-cli <command>` invocation mints a fresh triple of
identifiers — `request_id`, `idempotency_key`, and `trace_id` — used to
correlate the outbound HTTP call to registry-api with downstream event-spine
records.

Centralising the mint logic here keeps the 10 command modules in sync: when
a future identifier joins the triple, only this helper changes.

The `trace_id` is a **bare UUIDv7** per Story 9.1's shape contract. The
``tg:<update_id>`` form is reserved for the Telegram ingress (Story 9.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from events import new_idempotency_key, new_request_id
from events.ids import new_uuid7


@dataclass(frozen=True)
class CommandMetadata:
    """Per-invocation correlation identifiers minted at command entry.

    Attributes:
        request_id: Per-call request correlation id (UUIDv7), forwarded
            as ``X-Request-ID`` to registry-api.
        idempotency_key: Per-call idempotency key (UUIDv7), forwarded
            as ``Idempotency-Key`` to registry-api for write endpoints.
        trace_id: Bare UUIDv7 trace identifier per Story 9.1's contract,
            forwarded as ``X-Trace-Id`` to registry-api so the event spine
            can link the command-originated envelope to its origin.
    """

    request_id: str
    idempotency_key: str
    trace_id: str


def mint_command_metadata() -> CommandMetadata:
    """Mint a fresh ``(request_id, idempotency_key, trace_id)`` triple.

    Called once per command invocation by every console-cli command module.
    All three fields are independent bare-UUIDv7 strings; the ``trace_id``
    validates against the UUIDv7 branch of ``events.envelope.is_valid_trace_id``.
    """
    return CommandMetadata(
        request_id=new_request_id(),
        idempotency_key=new_idempotency_key(),
        trace_id=new_uuid7(),
    )


__all__ = ["CommandMetadata", "mint_command_metadata"]
