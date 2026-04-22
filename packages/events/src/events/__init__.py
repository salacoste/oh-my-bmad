"""Shared event envelope + schema registry for the oh-my-bmad platform.

Story 1.1 ships only `__version__`; the full `EventEnvelope` Pydantic v2 model,
schema registry, canonical serializer, UUIDv7 helpers, and injectable clock
arrive in Story 2.1 (event envelope + schema registry + canonical serializer).
"""

__version__ = "0.1.0"
