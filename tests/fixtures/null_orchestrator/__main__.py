"""``python -m null_orchestrator`` entrypoint shim.

Re-exports :func:`null_orchestrator.main` so the canonical
``python -m null_orchestrator`` invocation (referenced by the docstring of
``null_orchestrator.py``) actually works. The Dockerfile's ENTRYPOINT may
invoke either ``python /app/null_orchestrator.py`` (direct script form) or
``python -m null_orchestrator`` (package form) — both paths land at
:func:`null_orchestrator.main`.
"""

from .null_orchestrator import main

if __name__ == "__main__":
    main()
