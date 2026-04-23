# Fixture: service importing from a package — allowed.
# Owner classification: ("service", "registry-api") — see _meta.py
from events import __version__  # allowed: service → package

__all__ = ["__version__"]
