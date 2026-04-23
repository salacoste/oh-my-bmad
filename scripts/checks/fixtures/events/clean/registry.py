# Fixture-local registry used by events self-test.
# Contains one registered type so emit_registered.py passes.
REGISTRY: frozenset[str] = frozenset({"task.created"})
