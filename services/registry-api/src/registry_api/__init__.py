"""Registry API service — HTTP application API surface.

Story 1.1 shipped the scaffold (`hello()` + `__version__` stub).
Story 2.9 ships the real FastAPI app: POST /v1/tasks + GET /v1/tasks/{id},
middleware stack, RFC 7807 error envelopes, and OpenAPI auto-docs.

Public surface:
  - ``build_app``: factory → ``FastAPI`` instance (wired with lifespan,
                   middleware, exception handlers, and routes).
  - ``__version__``: package version string.
"""

from registry_api.app import build_app

__version__ = "0.2.0"


def hello() -> str:
    return "registry-api hello"


__all__ = ["build_app", "hello", "__version__"]
