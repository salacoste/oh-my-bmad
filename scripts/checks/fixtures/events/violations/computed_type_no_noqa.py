# Fixture: emission with a non-literal type and no noqa suppression — VIOLATION (EVT001).
some_var = "task.created"
emit_event(type=some_var, payload={})
