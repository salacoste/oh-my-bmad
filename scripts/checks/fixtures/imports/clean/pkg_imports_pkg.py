# Fixture: package importing from another package — allowed.
# Owner classification: ("package", "secret-hygiene") — see _meta.py
from events import __version__  # allowed: package → package

__all__ = ["__version__"]
