"""secret-hygiene — Three-layer secret hygiene enforcement per NFR-S1: scanner (pre-commit), structlog log-sanitizer (runtime), license-scan wrapper (pre-push).

Story 1.2 ships only `__version__`. Real logic arrives in: Stories 1.7 (scanner + sanitizer) + 6.8–6.10 (pre-commit hook + license scan).
"""

__version__ = "0.1.0"
