# Fixture: metrics-subscriber importing from another service — VIOLATION (IMP001).
# Owner classification: ("service", "metrics-subscriber") — see _meta.py
# P2-I1 read-only-subscriber rule (Story 10.1 AC6): metrics-subscriber must
# only import from packages/ — never from any other service.
from registry_state import __version__  # cross-service import — must fail
