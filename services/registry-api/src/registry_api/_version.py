"""Single source of truth for ``registry_api.__version__``.

Separated from ``__init__.py`` so submodules (e.g. ``routes/health.py``)
can ``from registry_api._version import __version__`` without triggering
the package's top-level ``__init__.py`` (which itself imports
``build_app`` from ``app.py``, creating a circular-import path when a
route module tries to read the version).
"""

from __future__ import annotations

__version__: str = "0.3.0"
