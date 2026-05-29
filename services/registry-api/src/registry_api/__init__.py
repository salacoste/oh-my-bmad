"""Registry API service — HTTP application API surface.

Story 1.1 shipped the scaffold (`hello()` + `__version__` stub).
Story 2.9 ships the real FastAPI app: POST /v1/tasks + GET /v1/tasks/{id},
middleware stack, RFC 7807 error envelopes, and OpenAPI auto-docs.
Story 2.13 wires IdempotencyCacheStore into POST /v1/tasks for FR28 / NFR-R4:
duplicate Idempotency-Key submissions return the prior result without
producing duplicate task rows.

Public surface (advertised via ``__all__``):
  - ``build_app``: factory → ``FastAPI`` instance (wired with lifespan,
                   middleware, exception handlers, and routes).

``__version__`` is implicitly public via the dunder convention; ``hello()`` is
a legacy Story 1.1 scaffold and is intentionally not advertised (still
importable; the bootstrap-verify check still references it).
"""

from registry_api._version import __version__
from registry_api.app import build_app


def hello() -> str:
    return "registry-api hello"


__all__ = ["__version__", "build_app"]
