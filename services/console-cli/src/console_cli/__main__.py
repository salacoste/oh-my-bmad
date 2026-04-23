"""console-cli hello-world entrypoint — Story 1.8 scaffold for 1.4 parity.

Real Typer CLI lands in Story 4.1 (typer-binary-scaffold).
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import NoReturn

_SERVICE = "console-cli"
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
