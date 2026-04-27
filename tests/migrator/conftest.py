"""Tree-specific fixtures for the migrator integration tests (Story 2.14).

* Adds ``scripts/migrator/src`` to :data:`sys.path` so the ``migrator``
  package is importable in-process. The migrator is shipped as a Docker
  one-shot (Story 1.3) and intentionally NOT registered as a uv workspace
  member — its only callers in the platform are the Docker entry-point
  and these tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/migrator/conftest.py: parents[0]=migrator, [1]=tests, [2]=repo root.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_MIGRATOR_SRC: Path = _REPO_ROOT / "scripts" / "migrator" / "src"
if _MIGRATOR_SRC.is_dir() and str(_MIGRATOR_SRC) not in sys.path:
    sys.path.insert(0, str(_MIGRATOR_SRC))
