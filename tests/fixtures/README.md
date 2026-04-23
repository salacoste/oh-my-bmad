# tests/fixtures

Shared fixture modules for cross-tree reuse. Scope rules:

- **Deterministic primitives** (clock, UUIDv7 generator, seeded random) live in
  `tests/conftest.py` — always loaded.
- **Event-log fixtures** (sample envelopes, minimal journey traces) land under
  `tests/fixtures/events/` starting Story 2.1.
- **Registry-state fixtures** (prebuilt SQLite snapshots) land under
  `tests/fixtures/registry/` starting Story 2.4.
- **Tree-specific fixtures** (e.g., crash-injection hooks, separability swap
  matrix) live in the owning tree's `conftest.py` — not here.

Convention: one fixture per file, filename == fixture name.
