"""Scaffold entry point for ``python -m metrics_subscriber``.

Story 10.1 — prints version + exits 0. Real lifespan + tail loop arrive
in Story 10.2; FastAPI exposition in Story 10.3.
"""

from __future__ import annotations

from metrics_subscriber import __version__


def main() -> int:
    print(f"metrics-subscriber {__version__} (scaffold; not yet wired — Story 10.1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
