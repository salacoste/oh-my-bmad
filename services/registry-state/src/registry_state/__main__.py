"""registry-state entrypoint — thin shim for ``python -m registry_state`` (Story 2.5).

Delegates to ``registry_state.app.main`` which runs the event-log subscriber
loop (the real long-lived behavior shipped in Story 2.5). The old no-op
placeholder from Story 1.4 is gone — the subscriber loop IS the long-lived
behavior.
"""

from registry_state.app.main import main

if __name__ == "__main__":
    main()
