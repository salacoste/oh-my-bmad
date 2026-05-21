#!/usr/bin/env python
"""Story 11.4 / FR65 — offline HMAC verification of task.approval_signed events.

Pure-Python tool. No FastAPI / SQLAlchemy / httpx imports. Reads a frozen JSONL
log directory, locates an event by event_id, recomputes its HMAC via Story
11.1's compute_approval_hmac (single source of truth per Story 11.1 D3 —
relocated to packages/events in Story 11.4 PP3), and emits structured
match/mismatch with investigation guidance.

Trust model: ``--log-dir`` is operator-supplied; the verifier resolves but
does NOT sandbox the path. Operators MUST point ``--log-dir`` only at
trusted JSONL archives (the verifier echoes the path in stderr warnings
on malformed lines).

Usage: see argparse --help.

Exit codes:
  0 — match
  1 — mismatch (HMAC re-computation differs from stored)
  2 — event not found, event type mismatch, payload missing field,
      payload field invalid, or payload canonical violation
  3 — key invalid (missing, <32 bytes, file-read error)
  4 — log-dir missing or unreadable
  5 — internal error (bug)
"""

from __future__ import annotations

import argparse
import hmac as _hmac_stdlib
import json
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from events.approval_signing import compute_approval_hmac
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# VerifyResult — structured result type
# ---------------------------------------------------------------------------

_Status = Literal["match", "mismatch", "error"]

# All reason codes (AC5 table + PP5 + PP13 additions)
_REASON_CODES = {
    "signature_match",
    "signature_mismatch",
    "event_not_found",
    "event_type_mismatch",
    "payload_missing_field",
    "payload_invalid",  # PP13 — field present but unparseable
    "payload_canonical_violation",  # PP5 — pipe in canonical-string field
    "key_missing",
    "key_too_short",
    "key_file_unreadable",
    "log_dir_missing",
    "log_dir_unreadable",
    "internal_error",
}

# reason → investigation next-steps
_INVESTIGATION_STEPS: dict[str, tuple[str, ...]] = {
    "signature_mismatch": (
        "Verify OPERATOR_HMAC_KEY matches the key in effect when this event was signed"
        " (check key.rotated events around decided_at -- Story 11.5).",
        "If key is correct, the event payload may have been tampered with -- diff the"
        " stored envelope against any backup copy.",
        "If you rotated keys, retry with the prior key via --key-file PATH.",
    ),
    "event_not_found": (
        "Confirm the event_id is correct (UUIDv7 of the task.approval_signed event,"
        " NOT the sibling approval.granted event_id).",
        "Check the date range covered by --log-dir; the event may be in a different"
        " archive directory.",
    ),
    "event_type_mismatch": (
        "Confirm you passed the task.approval_signed event_id (not the approval.granted"
        " sibling -- they have distinct event_ids per Story 11.1 paired-event ordering).",
        "Run: just verify-approval <event_id> --log-dir <dir> --json to inspect the"
        " actual event type stored at that event_id.",
    ),
    "payload_missing_field": (
        "Possible schema-version regression; report to platform team with the full envelope JSON.",
    ),
    "payload_invalid": (
        "A required payload field is present but unparseable (e.g. invalid ISO-8601 timestamp).",
        "Inspect the stored envelope's payload via a JSON viewer; report to platform team"
        " if values look syntactically correct.",
    ),
    "payload_canonical_violation": (
        "A canonical-string field (task_id, action, or actor_id) contains the forbidden"
        " pipe character '|'.",
        "This indicates schema-violation at sign-time (Story 11.1 P1-H1 guard should have"
        " prevented this); the stored event may be corrupted or forged.",
    ),
    "key_missing": (
        "Set OPERATOR_HMAC_KEY env var or pass --key-file PATH.",
        "For archive verification of pre-rotation approvals, pass --key-file pointing"
        " to the offline backup key file.",
    ),
    "key_too_short": (
        "Key must be at least 32 bytes (UTF-8 encoded).",
        "Regenerate per FR64 / Story 11.5 ADR-0006.",
    ),
    "key_file_unreadable": (
        "Check that --key-file PATH exists and the process has read permission.",
        "Key file content must be UTF-8 decodable; binary key files (e.g. raw openssl"
        " rand output) are not supported -- store keys in hex/base64 form.",
    ),
    "log_dir_missing": (
        "Check the --log-dir path; default is /var/lib/oh-my-bmad/registry/events.",
        "For archive verification, pass --log-dir pointing to the frozen archive directory.",
    ),
    "log_dir_unreadable": (
        "Directory exists but cannot be listed; check permissions.",
        "Operator may need elevated privileges (sudo) to read the event log.",
    ),
    "internal_error": ("File a bug report with the full traceback printed to stderr.",),
}

_DEFAULT_LOG_DIR = "/var/lib/oh-my-bmad/registry/events"


@dataclass(frozen=True)
class VerifyResult:
    status: _Status
    reason: str
    event_id: str
    event_type: str | None = None
    task_id: str | None = None
    stored_hmac: str | None = None
    recomputed_hmac: str | None = None
    action: str | None = None
    decided_at: str | None = None
    actor_id: str | None = None
    investigation_steps: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Key loading (AC1 step 1 + D2)
# ---------------------------------------------------------------------------


def _load_key(args: argparse.Namespace) -> tuple[SecretStr | None, str | None]:
    """Return (key, error_reason).

    error_reason is one of: key_missing, key_too_short, key_file_unreadable.
    Never logs the key value (NFR-S10).

    Story 11.4 PP4: catches UnicodeDecodeError on non-UTF-8 key-file content
    and returns ``key_file_unreadable`` (clean exit 3) rather than raising.
    Story 11.4 PP7: strips trailing whitespace/newline from key-file bytes
    so ``echo $KEY > key.bin`` (which adds ``\\n``) does not produce a
    key-with-newline that differs from the env-var value.
    """
    if args.key_file:
        try:
            raw = Path(args.key_file).read_bytes()
        except OSError:
            return None, "key_file_unreadable"
        # PP7: strip trailing whitespace/newlines added by `echo > key.bin`.
        raw = raw.rstrip()
    else:
        raw_str = os.environ.get("OPERATOR_HMAC_KEY")
        if not raw_str:
            return None, "key_missing"
        raw = raw_str.encode("utf-8")

    byte_len = len(raw)
    if byte_len < 32:
        # NFR-S10: log byte count only on the failure path, never the key value.
        print(f"key loaded ({byte_len} bytes -- below 32-byte minimum)", file=sys.stderr)
        return None, "key_too_short"

    # PP4: decode key bytes; binary keys (non-UTF-8) → key_file_unreadable.
    # PP17: removed success-path byte-count log (gratuitous info disclosure).
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None, "key_file_unreadable"
    return SecretStr(decoded), None


# ---------------------------------------------------------------------------
# JSONL log scanner (AC3)
# ---------------------------------------------------------------------------


def _scan_log_dir(
    log_dir: Path,
    event_id: str,
    *,
    warn: Callable[[str], None],
) -> dict[str, object] | None:
    """Streaming search: scan all *.jsonl files in sorted order, line-by-line.

    Skip blank lines. Warn on JSONDecodeError but continue scanning.
    First match wins. Returns the raw envelope dict or None.

    Story 11.4 PP20: event_id comparison is case-insensitive (UUIDv7 lower
    vs upper hex variants both match).
    """
    target = event_id.lower()
    for jsonl_path in sorted(log_dir.glob("*.jsonl")):
        with jsonl_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    parsed: dict[str, object] = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    warn(f"skip malformed line in {jsonl_path}: {exc}")
                    continue
                stored_id = parsed.get("event_id")
                if isinstance(stored_id, str) and stored_id.lower() == target:
                    return parsed
    return None


# ---------------------------------------------------------------------------
# HMAC verification (AC1 steps 5-6)
# ---------------------------------------------------------------------------


def _verify(
    envelope: dict[str, object],
    key: SecretStr,
) -> tuple[bool, str, str]:
    """Return (matches, recomputed_hmac, stored_hmac).

    Uses hmac.compare_digest for constant-time comparison (Story 11.1 future-risk
    note + AC1 step 6).

    Story 11.4 PP1: passes ``action=str(payload["action"])`` instead of the
    previously-hardcoded ``"approve"``. The hardcoded action silently accepted
    envelopes whose ``payload.action`` had been mutated (e.g. approve→reject)
    because the recomputation still hashed against literal ``"approve"`` and
    matched the stored HMAC. The widened ``action: str`` parameter on
    ``compute_approval_hmac`` (Story 11.4 PP1) supports this verifier-side
    use case while sign-time call sites still pass ``"approve"`` literally.
    """
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    recomputed = compute_approval_hmac(
        key=key,
        task_id=str(payload["task_id"]),
        action=str(payload["action"]),  # PP1: read from payload, not hardcoded
        timestamp=datetime.fromisoformat(str(payload["timestamp"])),
        actor_id=str(payload["actor_id"]),
    )
    stored = str(payload["hmac_sha256"])
    return _hmac_stdlib.compare_digest(recomputed, stored), recomputed, stored


# ---------------------------------------------------------------------------
# Output formatters (AC4)
# ---------------------------------------------------------------------------


def _format_human(result: VerifyResult) -> str:
    """Format VerifyResult as human-readable text per AC4.

    Story 11.4 PP18: uses ASCII markers ``[PASS] / [FAIL] / --`` instead of
    ``✓ ✗ —`` so the output renders on Windows cp1252 consoles without
    crashing on UnicodeEncodeError.
    """
    lines: list[str] = []

    if result.status == "match":
        lines.append("[PASS] HMAC verification PASSED")
        lines.append(f"  event_id:    {result.event_id}")
        lines.append(f"  type:        {result.event_type}")
        lines.append(f"  task_id:     {result.task_id}")
        lines.append(f"  action:      {result.action}")
        lines.append(f"  decided_at:  {result.decided_at}")
        lines.append(f"  actor_id:    {result.actor_id}")
        lines.append(f"  signature:   {result.stored_hmac} (matches)")

    elif result.status == "mismatch":
        lines.append("[FAIL] HMAC verification FAILED")
        lines.append(f"  event_id:        {result.event_id}")
        lines.append(f"  task_id:         {result.task_id}")
        lines.append(f"  action:          {result.action}")
        lines.append(f"  decided_at:      {result.decided_at}")
        lines.append(f"  actor_id:        {result.actor_id}")
        lines.append(f"  stored hmac:     {result.stored_hmac}")
        lines.append(f"  recomputed hmac: {result.recomputed_hmac}")
        lines.append(
            f"  reason:          {result.reason} -- stored HMAC does not match recomputation"
        )

        if result.investigation_steps:
            lines.append("")
            lines.append("Investigation next steps:")
            for i, step in enumerate(result.investigation_steps, 1):
                lines.append(f"  {i}. {step}")

    else:
        # error
        lines.append(f"[FAIL] verification error: {result.reason}")
        lines.append(f"  event_id: {result.event_id}")
        if result.investigation_steps:
            lines.append("")
            lines.append("Next steps:")
            for i, step in enumerate(result.investigation_steps, 1):
                lines.append(f"  {i}. {step}")

    return "\n".join(lines)


def _format_json(result: VerifyResult) -> str:
    """Format VerifyResult as JSON per AC4."""
    return json.dumps(
        {
            "status": result.status,
            "reason": result.reason,
            "event_id": result.event_id,
            "event_type": result.event_type,
            "task_id": result.task_id,
            "stored_hmac": result.stored_hmac,
            "recomputed_hmac": result.recomputed_hmac,
            "investigation_steps": list(result.investigation_steps),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _exit_code_for(result: VerifyResult) -> int:
    """Map result to exit code per AC4.

    0 = match
    1 = mismatch
    2 = event_not_found | event_type_mismatch | payload_missing_field
        | payload_invalid | payload_canonical_violation  (PP5 + PP13)
    3 = key_missing | key_too_short | key_file_unreadable
    4 = log_dir_missing | log_dir_unreadable
    5 = internal_error
    """
    code_map: dict[str, int] = {
        "signature_match": 0,
        "signature_mismatch": 1,
        "event_not_found": 2,
        "event_type_mismatch": 2,
        "payload_missing_field": 2,
        "payload_invalid": 2,
        "payload_canonical_violation": 2,
        "key_missing": 3,
        "key_too_short": 3,
        "key_file_unreadable": 3,
        "log_dir_missing": 4,
        "log_dir_unreadable": 4,
        "internal_error": 5,
    }
    return code_map.get(result.reason, 5)


# ---------------------------------------------------------------------------
# Result factories (AC5 — one per reason code)
# ---------------------------------------------------------------------------


def _err(
    event_id: str,
    reason: str,
    *,
    event_type: str | None = None,
    task_id: str | None = None,
) -> VerifyResult:
    """Build an error VerifyResult with investigation steps.

    Story 11.4 PP21: replaced ``**kwargs: object`` + ``# type: ignore`` with
    explicit optional parameters so mypy catches typo-args at call sites.
    """
    steps = _INVESTIGATION_STEPS.get(reason, ())
    return VerifyResult(
        status="error",
        reason=reason,
        event_id=event_id,
        event_type=event_type,
        task_id=task_id,
        investigation_steps=steps,
    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> VerifyResult:
    """Orchestrate log scanning → event validation → key loading → verification.

    Order: log-dir / event search first (exits 2/4) before key loading (exits 3).
    This matches AC1 self-verification: NONEXISTENT-EVENT with empty dir → exit 2.
    """
    event_id: str = args.event_id

    # --- Step 1: resolve and validate log dir ---
    # Story 11.4 PP12: resolve() normalises path separators / symlinks; we do
    # NOT sandbox (operator trust model documented in module docstring).
    log_dir = Path(args.log_dir).resolve(strict=False)
    if not log_dir.exists():
        return _err(event_id, "log_dir_missing")
    if not log_dir.is_dir():
        # PP8: passing a file path to --log-dir falls here; treat as missing-dir.
        return _err(event_id, "log_dir_missing")
    try:
        list(log_dir.iterdir())
    except PermissionError:
        return _err(event_id, "log_dir_unreadable")
    except OSError:
        # PP8: catch NotADirectoryError + other OSError subclasses; if we got
        # here despite is_dir() passing, treat as unreadable rather than crash.
        return _err(event_id, "log_dir_unreadable")

    # --- Step 2: scan JSONL files ---
    def _warn(msg: str) -> None:
        print(f"WARNING: {msg}", file=sys.stderr)

    envelope = _scan_log_dir(log_dir, event_id, warn=_warn)
    if envelope is None:
        return _err(event_id, "event_not_found")

    # --- Step 3: validate event type ---
    event_type = str(envelope.get("type", ""))
    if event_type != "task.approval_signed":
        return VerifyResult(
            status="error",
            reason="event_type_mismatch",
            event_id=event_id,
            event_type=event_type,
            investigation_steps=_INVESTIGATION_STEPS.get("event_type_mismatch", ()),
        )

    # --- Step 4: validate payload fields ---
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return _err(event_id, "payload_missing_field", event_type=event_type)

    required_fields = ("task_id", "action", "timestamp", "actor_id", "hmac_sha256")
    for req_field in required_fields:
        if req_field not in payload:
            return _err(event_id, "payload_missing_field", event_type=event_type)

    task_id = str(payload["task_id"])
    action = str(payload["action"])
    decided_at = str(payload["timestamp"])
    actor_id = str(payload["actor_id"])
    stored_hmac = str(payload["hmac_sha256"])

    # --- Step 4b (PP5): pre-validate canonical-string fields for pipe ---
    # Sign-time guard in events.approval_signing.compute_approval_hmac would
    # also reject these via ValueError; we surface a structured reason code
    # instead of letting it fall through to internal_error.
    for _canonical_field_name, canonical_field_value in (
        ("task_id", task_id),
        ("action", action),
        ("actor_id", actor_id),
    ):
        if "|" in canonical_field_value:
            return _err(
                event_id,
                "payload_canonical_violation",
                event_type=event_type,
                task_id=task_id,
            )

    # --- Step 5: load and validate key (after event found) ---
    key, key_err = _load_key(args)
    if key_err is not None:
        return _err(event_id, key_err, event_type=event_type, task_id=task_id)

    # --- Step 6: recompute and compare HMAC ---
    assert key is not None
    # PP13: narrow exception scope — ValueError from datetime/compute_approval_hmac
    # surfaces as payload_invalid (exit 2), not internal_error (exit 5). True bugs
    # (KeyError/AttributeError/etc.) still bubble to the main() top-level handler.
    try:
        matches, recomputed, stored = _verify(envelope, key)
    except ValueError:
        # Malformed timestamp or other field-parse failure.
        return _err(event_id, "payload_invalid", event_type=event_type, task_id=task_id)

    if matches:
        return VerifyResult(
            status="match",
            reason="signature_match",
            event_id=event_id,
            event_type=event_type,
            task_id=task_id,
            action=action,
            decided_at=decided_at,
            actor_id=actor_id,
            stored_hmac=stored_hmac,
            recomputed_hmac=recomputed,
        )
    else:
        return VerifyResult(
            status="mismatch",
            reason="signature_mismatch",
            event_id=event_id,
            event_type=event_type,
            task_id=task_id,
            action=action,
            decided_at=decided_at,
            actor_id=actor_id,
            stored_hmac=stored,
            recomputed_hmac=recomputed,
            investigation_steps=_INVESTIGATION_STEPS.get("signature_mismatch", ()),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        prog="verify_approval.py",
        description="Verify the HMAC of a task.approval_signed event from the JSONL log.",
    )
    parser.add_argument(
        "event_id",
        metavar="EVENT_ID",
        help="UUIDv7 event_id of the task.approval_signed event to verify",
    )
    parser.add_argument(
        "--log-dir",
        metavar="PATH",
        default=_DEFAULT_LOG_DIR,
        help=("JSONL log directory (default: /var/lib/oh-my-bmad/registry/events)"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON output (machine-readable); default is human text",
    )
    parser.add_argument(
        "--key-file",
        metavar="PATH",
        default=None,
        help=(
            "Read OPERATOR_HMAC_KEY from file instead of env (operator may keep old"
            " keys in offline backup files for archive verification; never logged)"
        ),
    )

    args = parser.parse_args(argv)

    try:
        result = _run(args)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        result = VerifyResult(
            status="error",
            reason="internal_error",
            event_id=getattr(args, "event_id", "<unknown>"),
            investigation_steps=_INVESTIGATION_STEPS.get("internal_error", ()),
        )

    if args.json_output:
        print(_format_json(result))
    else:
        print(_format_human(result))

    return _exit_code_for(result)


if __name__ == "__main__":
    sys.exit(main())
