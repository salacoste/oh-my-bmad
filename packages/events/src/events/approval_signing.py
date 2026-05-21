"""HMAC-SHA256 signing of approval events (Story 11.1 / FR64 / NFR-S10).

This module exposes :func:`compute_approval_hmac` — a **pure function** that
hashes the canonical signing payload with an operator-local secret. Story
11.4's ``just verify-approval`` recipe re-imports this exact function for
offline verification (single source of truth, D3 in the story spec).

Story 11.4 implementation note (relocation rationale, PP3):

* Originally lived in ``services/registry-api/src/registry_api/adapters/
  approval_signing.py``. Pass-1 review of Story 11.4 (PP3) discovered that
  the verifier's ``from registry_api.adapters.approval_signing import …``
  transitively triggered ``registry_api/__init__.py``'s ``build_app`` import
  — pulling FastAPI, SQLAlchemy, Anthropic and the full registry-state SQL
  stack into the "offline" verifier process. That falsified the verifier's
  "pure-Python / no FastAPI / no SQLAlchemy" contract.
* Resolution: relocate the pure function to ``packages/events`` (the shared
  event package — which is the natural home for the canonical signing
  primitive). The registry-api adapter at the original path now re-exports
  this function as a thin compatibility shim for existing call sites
  (``decisions.py``). Verifier imports directly from ``events`` and no
  longer transitively imports any service-layer code.

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
* **ISO-8601 UTC timestamp — millisecond precision (Story 11.4 PP2)** —
  caller passes a timezone-aware :class:`datetime.datetime` (Pydantic
  ``AwareDatetime`` upstream). The timestamp is **truncated to
  millisecond precision** before serialization so the canonical signing
  string matches the on-disk canonical form (``events.canonical.
  _datetime_to_iso_z`` truncates sub-ms microseconds). Without this
  truncation, sign-time canonical (``...123456+00:00``) diverged from
  the stored form (``...123Z``) and offline verification of any real
  production event with non-zero sub-ms µs would fail. The pre-PP2
  behaviour passed unit tests only because every fixture used
  ``microsecond=0``. The Story 11.1 golden vector input was already at
  ms-precision, so the hex digest is unchanged.
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
* The ``action`` parameter is widened to ``str`` (Story 11.4 PP1) so the
  verifier can re-compute HMACs over signed envelopes whose payload action
  is anything other than ``"approve"`` (a future Phase 3 expansion may
  sign reject events). Sign-time call sites in ``routes/decisions.py``
  still pass ``action="approve"`` literally; the Pydantic
  ``TaskApprovalSignedPayload.action: Literal["approve"]`` field
  constraint continues to enforce the producer-side invariant. The pure
  function itself does no producer-side action validation — that is a
  caller concern, and widening preserves Story 11.1 D3 "single source of
  truth" for verifier symmetry.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from pydantic import SecretStr


def compute_approval_hmac(
    *,
    key: SecretStr,
    task_id: str,
    action: str,
    timestamp: datetime,
    actor_id: str,
) -> str:
    """Compute HMAC-SHA256 over the canonical approval signing payload.

    Canonical signing string (D4 in Story 11.1, refined by Story 11.4 PP2):
        ``f"{task_id}|{action}|{ms_truncated_timestamp.isoformat()}|{actor_id}"``

    Pipe-delimited, ISO-8601 UTC ``timestamp`` (ms-precision — sub-ms
    microseconds truncated to match storage canonical form), deterministic
    field ordering.

    **Millisecond truncation (Story 11.4 PP2)**: the ``timestamp`` argument
    is truncated to ms-precision before :py:meth:`datetime.datetime.isoformat`
    is called. This is required so that sign-time canonical bytes match
    storage canonical bytes (which ``events.canonical._datetime_to_iso_z``
    truncates to ms + ``Z`` suffix). Pre-PP2, any production event with
    non-zero sub-ms µs would fail offline verification because the stored
    ISO string ``…123Z`` parses to ``microsecond=123000`` but the signed
    canonical was ``…123456+00:00``. The Story 11.1 golden vector input had
    ``microsecond=0`` (truncation is a no-op for zero microseconds) so the
    hex digest is unchanged.

    **Canonical-string injection guard (Story 11.1 P1-H1)**: the pipe
    character (``|``) is the canonical delimiter and is FORBIDDEN in any
    canonical-string field. This function raises :exc:`ValueError` if
    ``task_id`` or ``actor_id`` contains ``|``. The guard is latent today
    (``actor_id`` is hardcoded to ``"http-api"``) but mandatory before
    Story 6.1+ JWT auth lands — real-world JWT ``sub`` values like
    ``"org|alice"`` would cause two distinct inputs to share a canonical
    string and thus the same HMAC, forging a signing record. The ``action``
    field is checked for ``|`` as well (Story 11.4 PP1 widened action to
    ``str``; defense-in-depth against future signed event types). The
    timestamp's ``isoformat()`` produces ISO-8601 (no pipe possible), so
    only string inputs need the guard.

    Per FR64 wording, the ``override`` field of the approval is NOT part
    of the canonical signing string.

    Args:
        key: Operator-local HMAC key wrapped in :class:`SecretStr` so the
            secret is masked in ``repr()`` / logs / Pydantic dumps. The
            secret value is extracted EXACTLY ONCE inside this function.
        task_id: The task identifier (prefixed UUIDv7, e.g. ``t-...``).
            Must not contain ``|`` (pipe character).
        action: The decision action. Sign-time call sites pass
            ``"approve"`` (Story 11.1 FR64 — reject/stop/retry are NOT
            signed today). Widened from ``Literal["approve"]`` to ``str``
            in Story 11.4 PP1 so the verifier can re-compute HMACs of
            envelopes whose payload action is anything else (covers a
            future Phase 3 expansion). The pure function itself enforces
            no producer-side ``"approve"`` invariant — that is a caller
            concern (``routes/decisions.py``) and is also enforced by the
            Pydantic ``TaskApprovalSignedPayload.action: Literal["approve"]``
            field constraint on the producer side. Must not contain ``|``.
        timestamp: Timezone-aware decision timestamp. Story 11.1 reuses
            the same ``decided_at`` for the paired ``approval.granted``
            and ``task.approval_signed`` events so the signing input is
            deterministic and offline-verifiable. Truncated to ms-precision
            before serialization (Story 11.4 PP2).
        actor_id: The operator identifier (allowlist-validated upstream).
            Must not contain ``|`` (pipe character).

    Returns:
        64-character lowercase hex digest of HMAC-SHA256.

    Raises:
        ValueError: If ``task_id``, ``action`` or ``actor_id`` contains
            the pipe character ``|`` (canonical-string injection guard,
            P1-H1).
    """
    for field_name, field_value in (
        ("task_id", task_id),
        ("action", action),
        ("actor_id", actor_id),
    ):
        if "|" in field_value:
            raise ValueError(
                f"pipe character forbidden in HMAC input {field_name!r} "
                f"(P1-H1: prevents canonical-string injection when Story 6.1+ "
                f"introduces real actor_id values that may contain '|')"
            )
    # Story 11.4 PP2 — truncate to ms-precision to match storage canonical
    # form (_datetime_to_iso_z in events.canonical truncates µs → ms + Z).
    # Without this, sign-time canonical and verify-time canonical diverge
    # for any timestamp with non-zero sub-ms microseconds.
    ms_truncated = timestamp.replace(microsecond=(timestamp.microsecond // 1000) * 1000)
    canonical = f"{task_id}|{action}|{ms_truncated.isoformat()}|{actor_id}".encode()
    return hmac.new(
        key=key.get_secret_value().encode("utf-8"),
        msg=canonical,
        digestmod=hashlib.sha256,
    ).hexdigest()


__all__ = ["compute_approval_hmac"]
