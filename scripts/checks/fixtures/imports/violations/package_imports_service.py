# Fixture: package importing from a service — VIOLATION (IMP001).
# Owner classification: ("package", "events") — see _meta.py
from registry_api import __version__  # package → service import
