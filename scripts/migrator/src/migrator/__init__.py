"""migrator — event-log schema migrator (one-shot container).

Story 1.3 ships the scaffold + a trivial v1.0.0 → v1.0.1 additive upgrade.
Real schema evolutions arrive alongside real schema changes. Invoke via
`python -m migrator <from>-to-<to>` or `docker compose run --rm migrator
<from>-to-<to>` (compose wiring in Story 1.4).

Story 2.14 re-exports :func:`main` (and the ``v1.0.0 → v1.0.1`` migration
helper) from :mod:`migrator.cli` so in-process integration tests can
import them via the package root. The actual implementation lives in
``cli.py`` (a regular importable module) — ``__main__.py`` only wires
the CLI for ``python -m migrator``. mypy treats ``__main__`` specially
and refuses to resolve imports targeting it, hence the split.
"""

from migrator.cli import main, migrate_v1_0_0_to_v1_0_1

__all__ = ["main", "migrate_v1_0_0_to_v1_0_1"]

__version__ = "0.1.0"
