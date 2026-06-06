"""Runtime adapter factory — resolves runtime name to concrete adapter (FR89).

Returns the appropriate ``RuntimeAdapter`` implementation based on the
configured runtime.  Uses lazy imports for each adapter module so that
e.g. ``codex_runner.py`` (and its ``codex`` binary dependency) is only
imported when actually needed — deployments that only use Claude Code
never touch the Codex import path (ADR-0015 D2).

Fail-loud on unknown runtime names: raises ``ValueError`` rather than
silently falling back to Claude Code (which would mask operator
misconfiguration).
"""

from __future__ import annotations

from __future__ import annotations

from typing import TYPE_CHECKING

from worker_wrapper.app.config import WorkerSettings

if TYPE_CHECKING:
    from worker_wrapper.domain.runtime_adapter import RuntimeAdapter

# Closed set of supported runtime names.  The factory raises ValueError
# for any name not in this set.  Adding a new runtime requires:
#   1. Creating ``adapters/<name>_runner.py`` implementing RuntimeAdapter
#   2. Adding the name here
#   3. Adding the factory branch below
#   4. Updating ADR-0010 recipe step 9 (ADR-0015 D6)
SUPPORTED_RUNTIMES: frozenset[str] = frozenset({"claude-code", "codex"})


def get_runtime_adapter(
    settings: WorkerSettings,
    *,
    runtime: str | None = None,
) -> "RuntimeAdapter":
    """Return the concrete ``RuntimeAdapter`` for the configured runtime.

    Selection priority:

    1. ``runtime`` argument (per-task override from ``TaskCreatedPayload``).
    2. ``settings.runtime`` (worker-level default).
    3. Falls back to ``"claude-code"`` when both are ``None`` / empty.

    Args:
        settings: Worker settings (used to construct the adapter).
        runtime: Optional per-task runtime override.

    Returns:
        A concrete ``RuntimeAdapter`` instance.

    Raises:
        ValueError: If the runtime name is not in ``SUPPORTED_RUNTIMES``.
    """
    resolved = runtime or settings.runtime or "claude-code"

    if resolved not in SUPPORTED_RUNTIMES:
        raise ValueError(
            f"Unknown runtime: {resolved!r}. "
            f"Supported: {sorted(SUPPORTED_RUNTIMES)}"
        )

    if resolved == "claude-code":
        from worker_wrapper.adapters.claude_code_runner import ClaudeCodeRunner

        return ClaudeCodeRunner(settings)

    if resolved == "codex":
        from worker_wrapper.adapters.codex_runner import CodexRunner

        return CodexRunner(settings)

    # Defensive: should be unreachable due to the frozenset check above.
    raise ValueError(f"Unhandled runtime: {resolved!r}")  # pragma: no cover
