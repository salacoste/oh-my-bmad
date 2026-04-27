"""migrator entrypoint — `python -m migrator <from>-to-<to>`.

Thin wrapper around :func:`migrator.cli.main`. The real implementation lives
in :mod:`migrator.cli` so it remains importable from regular Python (mypy
refuses to resolve ``migrator.__main__`` even when the package is reachable
on ``mypy_path``). Story 2.14 made this split.
"""

from __future__ import annotations

import sys

from migrator.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
