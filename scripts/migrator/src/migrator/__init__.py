"""migrator — event-log schema migrator (one-shot container).

Story 1.3 ships the scaffold + a trivial v1.0.0 → v1.0.1 additive upgrade.
Real schema evolutions arrive alongside real schema changes. Invoke via
`python -m migrator <from>-to-<to>` or `docker compose run --rm migrator
<from>-to-<to>` (compose wiring in Story 1.4).
"""

__version__ = "0.1.0"
