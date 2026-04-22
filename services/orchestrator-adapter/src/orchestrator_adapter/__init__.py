"""orchestrator-adapter — Subprocess supervisor for the vendored OMC orchestrator; translates platform-task events into the OMC contract without leaking OMC into registry/worker code.

Story 1.2 ships only `__version__`. Real logic arrives in: Story 5.10 (OMC subprocess supervision + task-dispatch translation).
"""

__version__ = "0.1.0"
