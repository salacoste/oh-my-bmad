"""registry-state hello-world entrypoint — Story 1.4 scaffold.

Long-lived no-op so the compose container stays up, passes the
`test -f /tmp/ready` healthcheck, and exits cleanly on SIGTERM/SIGINT.
Real event-log subscriber + SQLite materializer land in Stories 2.4/2.5.
"""
from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import NoReturn

_SERVICE = "registry-state"
_READY = Path("/tmp/ready")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(_SERVICE)


def _stop(signum: int, _frame: FrameType | None) -> NoReturn:
    log.info("%s stopping (signal=%s)", _SERVICE, signum)
    _READY.unlink(missing_ok=True)
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _READY.touch()
    log.info("%s ready", _SERVICE)
    signal.pause()


if __name__ == "__main__":
    main()
