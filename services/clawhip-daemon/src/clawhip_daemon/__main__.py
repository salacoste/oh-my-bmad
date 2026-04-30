"""clawhip-daemon entrypoint — Story 3.9 AC-8.

Replaces Story 1.4's hello-world no-op stub with the real Telegram outbound
sink subscriber loop implemented in :mod:`clawhip_daemon.app.main`.
"""

from clawhip_daemon.app.main import main

if __name__ == "__main__":
    main()
