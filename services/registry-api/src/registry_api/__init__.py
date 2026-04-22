"""Registry API service — HTTP application API surface.

Story 1.1 ships only `__version__` and a `hello()` callable as a scaffold-proof.
The real FastAPI app, ports-and-adapters layout (`app/`, `domain/`, `adapters/`),
and HTTP route handlers arrive in Story 2.9 (registry-api HTTP skeleton).
"""

__version__ = "0.1.0"


def hello() -> str:
    return "registry-api hello"
