# Fixture: service importing from another service — VIOLATION (IMP001).
# Owner classification: ("service", "registry-api") — see _meta.py
from registry_state import __version__  # cross-service import
