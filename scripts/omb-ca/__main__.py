#!/usr/bin/env python3
"""Allow running ``python scripts/omb-ca`` directly."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# When invoked as ``python scripts/omb-ca`` the package is not on sys.path.
# Load __init__.py manually so the CLI works without installation.
_PKG_DIR = Path(__file__).resolve().parent
_INIT_PY = _PKG_DIR / "__init__.py"


def _bootstrap() -> None:
    spec = importlib.util.spec_from_file_location(
        "omb_ca",
        _INIT_PY,
        submodule_search_locations=[str(_PKG_DIR)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["omb_ca"] = mod
    spec.loader.exec_module(mod)
    mod.main()  # type: ignore[attr-defined]


if __name__ == "__main__":
    _bootstrap()
