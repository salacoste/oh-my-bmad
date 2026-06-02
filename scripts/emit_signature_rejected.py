#!/usr/bin/env python3
"""One-shot CLI helper that records a supply-chain verification failure on the event spine.

Story 8.6 / FR56a / NFR-S9. Operator invokes this AFTER ``just verify-images``
(Story 8.5) detects a cosign / SLSA / SBOM attestation failure, and BEFORE
re-running the verification post-fix. The script writes a single
``deployment.signature_rejected`` envelope to the Platform's JSONL event log
so Epic 11's ``just verify-approval`` can replay the rejection history.

**FR26 single-writer carry-out (defense-in-depth)**

The script acquires ``LOCK_EX | LOCK_NB`` on the target daily-log file before
appending. If registry-state holds the lock (Platform stack is running), the
script aborts with exit code 3 and a recoverable error message. The
workflow-design guarantee — operator runs ``verify-images`` BEFORE ``docker
compose up`` — should make this contention path unreachable in practice; the
OS-level lock is defense-in-depth for the post-upgrade re-verify case.

**Exit codes**

* ``0`` — envelope appended successfully; the new ``event_id`` is printed to stdout.
* ``1`` — payload validation failure (Pydantic ``ValidationError`` — e.g.,
  malformed digest, image, or version that passes argparse but fails the
  registered payload-model regex). Also covers other unhandled file-IO
  failures.
* ``2`` — argparse usage error (missing required arg, invalid ``--attestation-type``
  choice). Argparse emits this exit code directly; the helper never returns
  ``2`` from its own catch blocks (F4 code-review fix — disambiguates argparse
  errors from Pydantic payload errors for operator triage).
* ``3`` — flock contention; registry-state appears to be the live writer.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from events import (
    Actor,
    DeploymentSignatureRejectedPayload,
    EventEnvelope,
    SystemClock,
    append_event_line,
    new_event_id,
    new_request_id,
)
from events.canonical import to_canonical_json
from pydantic import ValidationError

_DEFAULT_EVENT_LOG_DIR = Path("/var/lib/oh-my-bmad/events")
_ERROR_MESSAGE_MAX = 4096


def _daily_log_path(base_dir: Path, *, now: datetime) -> Path:
    """Return the per-UTC-day log file matching ``registry_state.adapters.event_log:106``.

    Mirrors the recipe so the helper writes to the SAME file registry-state
    would write to during normal operation. Both sites compute
    ``base_dir / "<YYYY-MM-DD>.jsonl"`` from the UTC date of *now*.
    """
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(f"now must be UTC-aware (tzinfo=UTC); got {now.tzinfo!r}")
    return base_dir / f"{now.date().isoformat()}.jsonl"


def _build_envelope(
    *,
    image: str,
    digest: str,
    attestation_type: str,
    error_message: str,
    omb_version: str,
    ghcr_owner: str,
    operator_id: str | None,
) -> EventEnvelope:
    """Construct the ``deployment.signature_rejected`` envelope from CLI args."""
    clock = SystemClock()
    truncated_error = error_message[:_ERROR_MESSAGE_MAX]
    payload = DeploymentSignatureRejectedPayload(
        image=image,
        digest=digest,
        # ``attestation_type`` is typed ``str`` on the helper boundary; the
        # payload model narrows it to a ``Literal`` of three values. Argparse
        # ``choices=`` (line ~143) restricts the runtime value before reaching
        # this constructor, so the type-ignore is safe.
        attestation_type=attestation_type,  # type: ignore[arg-type]
        error_message=truncated_error,
        omb_version=omb_version,
        ghcr_owner=ghcr_owner,
        operator_id=operator_id,
    )
    # Story 9.7: trace_id is now REQUIRED. This script is a CLI helper that
    # runs standalone without a preceding operator command; mint a fresh
    # synthetic bare-UUIDv7 as the trace_id correlation handle.
    return EventEnvelope.create(
        event_id=new_event_id(),
        schema_version="1.1.0",
        type="deployment.signature_rejected",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="operator", id="cli-helper"),
        payload=payload,
        trace_id=new_request_id(),
        request_id=new_request_id(),
    )


def _append_with_lock(path: Path, line: bytes) -> None:
    """Append *line* to *path* via the shared :func:`events.append_event_line`.

    Thin wrapper retained for the existing call site. Delegates to the canonical
    FR26-respecting external-emitter append (Epic-13 retro AI-13.2).

    **Story 13.4a / retro AI-13.2 FIX:** this previously created the file at
    ``0o640`` (the original Story-8.6 mode). An external emitter creating the
    day's JSONL group-non-writable crash-loops registry-state's cross-uid
    recovery (Stories 11.3.11/11.3.12) — the SAME systemic bug 13.4's lag-check
    independently hit. ``append_event_line`` creates at ``0o660`` + ``os.fchmod``
    (defeats umask 022), others-triad 0 (never world-readable). All external
    emitters now share that ONE implementation so the 0o640 mistake can't recur.

    Raises ``BlockingIOError`` if the lock is held (FR26 contention → exit 3).
    """
    append_event_line(path, line)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="emit_signature_rejected",
        description=(
            "Record a supply-chain verification failure on the Platform event spine. "
            "Run this after `just verify-images` (Story 8.5) reports a failure."
        ),
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Full canonical image ref, e.g. ghcr.io/<owner>/oh-my-bmad-<service>",
    )
    parser.add_argument(
        "--digest",
        required=True,
        help="Content digest including the algorithm prefix: sha256:<64-hex>",
    )
    parser.add_argument(
        "--attestation-type",
        required=True,
        choices=["signature", "slsaprovenance", "cyclonedx"],
        help="Which cosign verification failed: signature | slsaprovenance | cyclonedx",
    )
    parser.add_argument(
        "--error-message",
        required=True,
        help=f"Raw cosign stderr (truncated to {_ERROR_MESSAGE_MAX} chars)",
    )
    parser.add_argument(
        "--omb-version",
        required=True,
        help="Release tag the operator was verifying (e.g. v0.1.5 or v0.1.5-rc1)",
    )
    parser.add_argument(
        "--ghcr-owner",
        required=True,
        help="GHCR owner namespace (e.g. salacoste); matches OMB_GHCR_OWNER in .env",
    )
    parser.add_argument(
        "--operator-id",
        default=None,
        help="Optional operator identifier (Epic 11 will populate via HMAC; empty in Epic 8)",
    )
    parser.add_argument(
        "--event-log-dir",
        type=Path,
        default=None,
        help=(
            "Override the event-log base directory (default: "
            "$EVENT_LOG_DIR or /var/lib/oh-my-bmad/events)"
        ),
    )
    return parser.parse_args(argv)


def _resolve_event_log_dir(override: Path | None) -> Path:
    """Resolve the event-log directory, normalizing path traversal segments.

    Code-review fix F7: call ``Path.resolve()`` to normalize ``..`` segments
    and symlinks before the helper writes anywhere. Operator-supplied paths
    (via ``--event-log-dir`` or the ``EVENT_LOG_DIR`` env var) are
    canonicalized so a value like ``/tmp/../etc/`` does not silently escape.
    The default path is also resolved so test-mode behavior is consistent.
    """
    if override is not None:
        return override.resolve()
    env_value = os.environ.get("EVENT_LOG_DIR")
    if env_value:
        return Path(env_value).resolve()
    return _DEFAULT_EVENT_LOG_DIR.resolve()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        envelope = _build_envelope(
            image=args.image,
            digest=args.digest,
            attestation_type=args.attestation_type,
            error_message=args.error_message,
            omb_version=args.omb_version,
            ghcr_owner=args.ghcr_owner,
            operator_id=args.operator_id,
        )
    except (ValidationError, ValueError) as exc:
        # Code-review fix F2+F4: catch ``ValidationError`` explicitly (do not
        # rely on it being a ``ValueError`` subclass) and return exit code 1.
        # Argparse owns exit code 2 for missing / invalid-choice args; the
        # Pydantic validation path is a distinct failure mode and must use
        # its own exit code so operators can tell argparse usage errors from
        # payload-shape errors.
        print(f"ERROR: invalid payload field: {exc}", file=sys.stderr)
        return 1

    line = to_canonical_json(envelope) + b"\n"
    base_dir = _resolve_event_log_dir(args.event_log_dir)
    path = _daily_log_path(base_dir, now=envelope.emitted_at)

    try:
        _append_with_lock(path, line)
    except BlockingIOError:
        # Code-review fix F8: 3-line stderr format matches the spec's literal
        # quote. The third line ("before emitting…") was concatenated onto
        # the second in the prior version; reviewer-tooling consumers may
        # depend on the line layout. Substring match in the integration test
        # remains stable.
        print(
            f"ERROR: registry-state holds an exclusive lock on {path}.\n"
            f"The Platform stack appears to be running. Stop it with "
            f"`docker compose down`\n"
            f"before emitting deployment.signature_rejected events "
            f"(FR26 single-writer).",
            file=sys.stderr,
        )
        return 3
    except OSError as exc:
        print(f"ERROR: failed to append to {path}: {exc}", file=sys.stderr)
        return 1

    print(envelope.event_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
