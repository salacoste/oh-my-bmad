"""HMAC-SHA256 signing of approval events (Story 11.1 / FR64 / NFR-S10).

This module exposes :func:`compute_approval_hmac` — a **pure function** that
hashes the canonical signing payload with an operator-local secret. Story
11.4's ``just verify-approval`` recipe re-imports this exact function for
offline verification (single source of truth, D3 in the story spec).

Design constraints (do NOT relax without an ADR):

* **Pure function** — no logging, no I/O, no side effects. The caller is
  responsible for emitting / persisting the resulting hex digest. Any leak
  surface (logs, events, snapshots) is owned by the caller.
* **Canonical signing string** — pipe-delimited:
  ``f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}"`` (D4 in the
  story spec). Per FR64 wording, the ``override`` field is NOT included.
  The pipe character (``|``) is the canonical delimiter and is FORBIDDEN
  in any canonical-string field; :func:`compute_approval_hmac` raises
  ``ValueError`` if ``task_id`` or ``actor_id`` contains ``|`` (Story
  11.1 P1-H1 — canonical-string-injection guard). The guard is necessary
  so two distinct ``(task_id, action, timestamp, actor_id)`` tuples
  cannot share a canonical string. Latent today (actor_id is hardcoded
  to ``"http-api"``) but mandatory before Story 6.1+ JWT auth lands —
  real-world JWT ``sub`` values like ``"org|alice"`` would otherwise
  collide on the canonical string and forge HMAC signatures. (Note:
  ``decision_id`` is not in the canonical string so its pipe-guard lives
  at the payload layer via ``TaskApprovalSignedPayload`` Field constraints
  — P1-H2 defense-in-depth.)
* **ISO-8601 UTC timestamp** — caller passes a timezone-aware
  :class:`datetime.datetime` (Pydantic ``AwareDatetime`` upstream); the
  serialized form is whatever :py:meth:`datetime.datetime.isoformat`
  produces. Story 11.1 uses the same ``decided_at`` instant for the paired
  ``approval.granted`` and ``task.approval_signed`` events so the signature
  input is deterministic and reproducible offline.
* **Hex output** — returns the 64-character lowercase hex digest of
  HMAC-SHA256. Hex (not base64) was chosen because it is operator-readable
  in events / logs / ``just verify-approval`` output. 64 hex chars = 32
  bytes = HMAC-SHA256 output size.
* **NFR-S10 key isolation** — the key is accepted as a Pydantic
  :class:`pydantic.SecretStr`; ``.get_secret_value()`` is called EXACTLY
  ONCE inside this function and the result never leaves the local frame.

Future risk note (Story 11.4 scope):

* The verifier MUST use :py:func:`hmac.compare_digest` for constant-time
  comparison. This producer side has no comparison surface, so timing
  attacks are not a concern here.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Literal

from pydantic import SecretStr


def compute_approval_hmac(
    *,
    key: SecretStr,
    task_id: str,
    action: Literal["approve"],
    timestamp: datetime,
    actor_id: str,
) -> str:
    """Compute HMAC-SHA256 over the canonical approval signing payload.

    Canonical signing string (D4 in Story 11.1):
        ``f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}"``

    Pipe-delimited, ISO-8601 UTC ``timestamp``, deterministic field
    ordering.

    **Canonical-string injection guard (Story 11.1 P1-H1)**: the pipe
    character (``|``) is the canonical delimiter and is FORBIDDEN in any
    canonical-string field. This function raises :exc:`ValueError` if
    ``task_id`` or ``actor_id`` contains ``|``. The guard is latent today
    (``actor_id`` is hardcoded to ``"http-api"``) but mandatory before
    Story 6.1+ JWT auth lands — real-world JWT ``sub`` values like
    ``"org|alice"`` would cause two distinct inputs to share a canonical
    string and thus the same HMAC, forging a signing record. The
    ``action`` field is a ``Literal["approve"]`` type constraint (no
    pipe possible) and ``timestamp.isoformat()`` produces ISO-8601 (no
    pipe possible), so only string inputs need the guard.

    Per FR64 wording, the ``override`` field of the approval is NOT part
    of the canonical signing string.

    Args:
        key: Operator-local HMAC key wrapped in :class:`SecretStr` so the
            secret is masked in ``repr()`` / logs / Pydantic dumps. The
            secret value is extracted EXACTLY ONCE inside this function.
        task_id: The task identifier (prefixed UUIDv7, e.g. ``t-...``).
            Must not contain ``|`` (pipe character).
        action: The decision action — fixed to ``"approve"`` because only
            approve actions are signed (FR64 — reject/stop/retry are NOT
            signed).
        timestamp: Timezone-aware decision timestamp. Story 11.1 reuses
            the same ``decided_at`` for the paired ``approval.granted``
            and ``task.approval_signed`` events so the signing input is
            deterministic and offline-verifiable.
        actor_id: The operator identifier (allowlist-validated upstream).
            Must not contain ``|`` (pipe character).

    Returns:
        64-character lowercase hex digest of HMAC-SHA256.

    Raises:
        ValueError: If ``task_id`` or ``actor_id`` contains the pipe
            character ``|`` (canonical-string injection guard, P1-H1).
    """
    for field_name, field_value in (
        ("task_id", task_id),
        ("actor_id", actor_id),
    ):
        if "|" in field_value:
            raise ValueError(
                f"pipe character forbidden in HMAC input {field_name!r} "
                f"(P1-H1: prevents canonical-string injection when Story 6.1+ "
                f"introduces real actor_id values that may contain '|')"
            )
    canonical = f"{task_id}|{action}|{timestamp.isoformat()}|{actor_id}".encode()
    return hmac.new(
        key=key.get_secret_value().encode("utf-8"),
        msg=canonical,
        digestmod=hashlib.sha256,
    ).hexdigest()


__all__ = ["compute_approval_hmac"]
