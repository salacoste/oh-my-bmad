#!/usr/bin/env python3
"""Poll litestream ``/metrics``, debounce, and emit ``replication.lagging`` (Story 13.4 / NFR-R7).

Run via ``just litestream-lag-check`` (typically on a ~30s cron/timer). The
script:

  1. GETs the litestream Prometheus endpoint (``OMB_LITESTREAM_METRICS_URL``,
     default ``http://localhost:9090/metrics``) using stdlib ``urllib`` — no
     third-party HTTP client.
  2. Parses ``litestream_sync_count`` and ``litestream_sync_error_count`` for the
     configured database (``OMB_LITESTREAM_DB``, default the registry-state DB
     path) out of the Prometheus text body.
  3. Loads the small JSON debounce-state file
     (``OMB_LITESTREAM_LAG_STATE_PATH``), runs the PURE state machine in
     ``scripts/replication_lag_detector.py``, and saves the new state.
  4. IF the detector says emit: builds a ``replication.lagging`` EventEnvelope
     (schema_version ``1.1.0``) and appends it to the per-day JSONL event log
     under ``fcntl.flock(LOCK_EX | LOCK_NB)`` — the SAME single-writer-respecting
     append precedent as ``scripts/emit_signature_rejected.py`` (Story 8.6 /
     FR26). Exit 3 on lock contention.

**FR26 single-writer carry-out**: the script never opens an HTTP append endpoint
and never touches registry-state. It writes ONLY via the flock-guarded direct
append, exactly like ``emit_signature_rejected.py``; if registry-state holds the
lock the script aborts with exit code 3.

**Exit codes**

* ``0`` — sample processed (whether or not an event was emitted).
* ``1`` — metrics fetch/parse failure, payload validation failure, or file-IO
  error.
* ``3`` — flock contention; registry-state appears to be the live writer.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# scripts/ is not an installed package; make sibling modules importable when the
# script is invoked directly (``./scripts/check_replication_lag.py`` or
# ``uv run python scripts/check_replication_lag.py``).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from events import (  # noqa: E402
    Actor,
    EventEnvelope,
    ReplicationLaggingPayload,
    SystemClock,
    new_event_id,
    new_request_id,
)
from events.canonical import to_canonical_json  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from replication_lag_detector import (  # noqa: E402
    Sample,
    evaluate,
    state_from_json,
)

_DEFAULT_METRICS_URL = "http://localhost:9090/metrics"
_DEFAULT_DB = "/var/lib/oh-my-bmad/registry/state.sqlite3"
_DEFAULT_EVENT_LOG_DIR = Path("/var/lib/oh-my-bmad/registry/events")
_DEFAULT_STATE_PATH = Path("/var/lib/oh-my-bmad/registry/replication-lag-state.json")
_HTTP_TIMEOUT_S = 10.0


def _fetch_metrics(url: str) -> str:
    """GET the litestream Prometheus ``/metrics`` body via stdlib ``urllib``.

    Raises ``OSError`` (incl. ``urllib.error.URLError``) on connection failure so
    the caller can map it to exit code 1.
    """
    req = urllib.request.Request(url, method="GET")  # noqa: S310 — operator-supplied http(s) URL
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def _parse_counter(body: str, metric: str, db: str) -> int | None:
    """Extract ``<metric>{db="<db>"} <value>`` from a Prometheus text body.

    Returns the integer value (counters are whole numbers) or ``None`` if no
    matching sample is present. Tolerant of extra labels and label ordering: a
    line matches when it starts with ``<metric>{``, contains the ``db="<db>"``
    label, and is not a ``# HELP`` / ``# TYPE`` comment.
    """
    db_label = f'db="{db}"'
    prefix = metric + "{"
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(prefix):
            continue
        brace_close = line.find("}")
        if brace_close == -1:
            continue
        labels = line[len(prefix) : brace_close]
        if db_label not in labels:
            continue
        value_str = line[brace_close + 1 :].strip()
        if not value_str:
            continue
        try:
            # Prometheus emits floats (e.g. ``42`` or ``42.0``); counters are
            # integral — truncate to int for the detector.
            return int(float(value_str))
        except ValueError:
            return None
    return None


def _daily_log_path(base_dir: Path, *, now: datetime) -> Path:
    """Return the per-UTC-day log file, mirroring ``emit_signature_rejected.py``.

    Both sites compute ``base_dir / "<YYYY-MM-DD>.jsonl"`` from the UTC date of
    *now* so the helper writes to the SAME file registry-state would write to.
    """
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError(f"now must be UTC-aware (tzinfo=UTC); got {now.tzinfo!r}")
    return base_dir / f"{now.date().isoformat()}.jsonl"


def _append_with_lock(path: Path, line: bytes) -> None:
    """Append *line* to *path* under ``LOCK_EX | LOCK_NB``, then fsync.

    Follows the ``scripts/emit_signature_rejected.py`` precedent (Story 8.6 /
    FR26): non-blocking exclusive lock (raises ``BlockingIOError`` on contention
    → exit 3), POSIX append-mode, fsync-under-lock for a durable atomic append.

    File mode **0o660** (rw-rw----), NOT 0o640 (Story 13.4 fix). If this script
    is the FIRST writer of the day it CREATES the JSONL; registry-state (a
    different uid in the shared ``omb`` group) must be able to re-open it for
    crash recovery. Story 11.3.11 set registry-state's own writer to 0o660 and
    its reader (event_log.py) explicitly REJECTS a group-non-writable 0o640 file
    created by an "external tool" — so this external emitter must match 0o660.
    The container umask (022) would strip group-write back to 0o640 on
    ``os.open``, so we ``os.fchmod`` to 0o660 explicitly (mirrors event_log.py).
    Others-triad stays 0 — the audit log is never world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o660,
    )
    with os.fdopen(fd, "ab", closefd=True) as f:
        # Defeat umask 022 (which would leave 0o640, no group-write → registry-state
        # cross-uid recovery fails). Best-effort: only the file owner may fchmod.
        with contextlib.suppress(OSError):
            os.fchmod(f.fileno(), 0o660)
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _load_state(path: Path) -> dict[str, object] | None:
    """Load the JSON debounce state, or ``None`` if the file is absent."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(raw)


def _save_state(path: Path, state: dict[str, object]) -> None:
    """Atomically persist the JSON debounce state (write-temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _build_envelope(*, db: str, fields: object) -> EventEnvelope:
    """Construct the ``replication.lagging`` envelope from the detector fields."""
    # ``fields`` is an EmitFields dataclass; access attrs directly. Typed as
    # ``object`` to avoid importing the dataclass name twice in the signature.
    payload = ReplicationLaggingPayload(
        db=db,
        signal=fields.signal,  # type: ignore[attr-defined]
        threshold_seconds=fields.threshold_seconds,  # type: ignore[attr-defined]
        sustained_seconds=fields.sustained_seconds,  # type: ignore[attr-defined]
        sync_error_count=fields.sync_error_count,  # type: ignore[attr-defined]
    )
    clock = SystemClock()
    return EventEnvelope.create(
        event_id=new_event_id(),
        schema_version="1.1.0",
        type="replication.lagging",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id="litestream-lag-check"),
        payload=payload,
        trace_id=new_request_id(),
        request_id=new_request_id(),
    )


def main(argv: list[str] | None = None) -> int:
    # No argparse flags — configuration is env-driven (cron/timer friendly).
    _ = argv
    metrics_url = os.environ.get("OMB_LITESTREAM_METRICS_URL", _DEFAULT_METRICS_URL)
    db = os.environ.get("OMB_LITESTREAM_DB", _DEFAULT_DB)
    # .resolve() normalizes any `..` in operator-supplied paths (review MEDIUM;
    # matches emit_signature_rejected.py's path handling).
    state_path = Path(os.environ.get("OMB_LITESTREAM_LAG_STATE_PATH", str(_DEFAULT_STATE_PATH))).resolve()
    event_log_dir = Path(os.environ.get("EVENT_LOG_DIR", str(_DEFAULT_EVENT_LOG_DIR))).resolve()

    try:
        body = _fetch_metrics(metrics_url)
    except (urllib.error.URLError, OSError) as exc:
        print(
            f"ERROR: failed to fetch litestream metrics from {metrics_url}: {exc}", file=sys.stderr
        )
        return 1

    sync_count = _parse_counter(body, "litestream_sync_count", db)
    sync_error_count = _parse_counter(body, "litestream_sync_error_count", db)
    if sync_count is None:
        print(
            f"ERROR: litestream_sync_count{{db={db!r}}} not found in metrics "
            f"(is the db path correct and the sidecar replicating?)",
            file=sys.stderr,
        )
        return 1
    if sync_error_count is None:
        # sync_error_count is 0 until the first error; litestream may omit the
        # series entirely until then. Treat absence as 0.
        sync_error_count = 0

    try:
        raw_state = _load_state(state_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to read lag-state file {state_path}: {exc}", file=sys.stderr)
        return 1

    prior_state = state_from_json(raw_state) if raw_state is not None else None
    sample = Sample(
        sync_count=sync_count,
        sync_error_count=sync_error_count,
        observed_at_epoch=time.time(),
    )
    result = evaluate(prior_state, sample)

    if result.should_emit:
        try:
            envelope = _build_envelope(db=db, fields=result.emit_fields)
        except (ValidationError, ValueError) as exc:
            print(f"ERROR: invalid replication.lagging payload: {exc}", file=sys.stderr)
            return 1
        line = to_canonical_json(envelope) + b"\n"
        path = _daily_log_path(event_log_dir, now=envelope.emitted_at)
        try:
            _append_with_lock(path, line)
        except BlockingIOError:
            print(
                f"ERROR: registry-state holds an exclusive lock on {path}.\n"
                f"The Platform stack appears to be running. Stop it with "
                f"`docker compose down`\n"
                f"before emitting replication.lagging events (FR26 single-writer).",
                file=sys.stderr,
            )
            return 3
        except OSError as exc:
            print(f"ERROR: failed to append to {path}: {exc}", file=sys.stderr)
            return 1
        print(envelope.event_id)

    # Persist the new debounce state regardless of emit (onset/recovery tracking).
    try:
        _save_state(state_path, dict(result.new_state))
    except OSError as exc:
        print(f"ERROR: failed to write lag-state file {state_path}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
