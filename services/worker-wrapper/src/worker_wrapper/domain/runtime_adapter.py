"""RuntimeAdapter protocol — pluggable CLI runtime abstraction (Phase 5 / FR89).

Defines the structural contract every CLI runtime adapter must satisfy.
The task driver (``lifecycle.py:run_task``) depends on this protocol, not
on any concrete adapter class (Dependency Inversion per ADR-0015 D2).

Phase 5 introduces a **5th archetype: Multi-Runtime Worker**.  The protocol
ensures every runtime adapter satisfies the same contract the lifecycle
manager and budget supervisor depend on:

- :meth:`runtime_name` — identifies the adapter (closed enum).
- :meth:`run` — full execution cycle (spawn → stream → build result).
- :meth:`cancel` — cooperative cancellation (SIGTERM).
- :meth:`terminate_with_grace` — SIGTERM → grace → SIGKILL escalation (P5-I3).
- :meth:`health_check` — binary/API-key/version probe (FR95).

Structural subtyping (``@runtime_checkable``) allows the existing
``ClaudeCodeRunner`` to satisfy the protocol without inheriting from a
base class — no changes to its public API required.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from worker_wrapper.adapters.claude_code_runner import TerminationResult


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of a runtime health probe (FR95).

    Fields:
        installed: ``True`` if the runtime binary is found via ``shutil.which``.
        api_key_valid: ``True`` if a minimal API call succeeded.  Lazy —
            cached for 60 seconds to avoid per-task API calls.  ``False``
            without an API call when ``installed`` is ``False``.
        version: Parsed version string from ``<binary> --version``, or
            ``""`` when ``installed`` is ``False``.
    """

    installed: bool
    api_key_valid: bool = False
    version: str = ""


# ``CodexResult`` is a parallel structure to ``ClaudeCodeResult`` defined here
# so the protocol can reference a generic result type.  Individual adapters
# return their own concrete result type (which the task driver consumes
# opaquely).  The protocol uses ``Any`` for the return type of ``run()``
# because each adapter returns a different result dataclass.
#
# When a unified ``RuntimeResult`` is needed (e.g. for the handoff flow),
# it will be defined here as a separate dataclass mapping the common fields
# from both ``ClaudeCodeResult`` and ``CodexResult``.


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Protocol for pluggable CLI runtime adapters (ADR-0015 D1).

    Each adapter manages the full lifecycle of one CLI agent subprocess:
    spawn, stream-read structured output, health-check, and terminate.
    The task driver depends on this protocol, not on any concrete class.

    **P5-I2**: ``parse_output`` uses structured JSON deserialization only —
    no regex on subprocess stdout.  The CI gate asserts this per adapter.

    **P5-I3**: ``terminate_with_grace`` implements SIGTERM → grace → SIGKILL
    escalation with ``TerminationResult``-compatible return.  The CI gate
    asserts every adapter satisfies this contract.
    """

    @property
    def runtime_name(self) -> str:
        """Runtime identifier: ``"claude-code"`` | ``"codex"`` | future names."""
        ...

    async def run(self, prompt: str, worktree_path: Path) -> Any:
        """Run the runtime with the given prompt and return a structured result.

        The return type is adapter-specific (``ClaudeCodeResult``,
        ``CodexResult``, etc.).  The task driver consumes it opaquely.
        """
        ...

    async def cancel(self) -> None:
        """Cancel a running subprocess (forward SIGTERM)."""
        ...

    async def terminate_with_grace(
        self,
        *,
        grace_period_s: float = 5.0,
    ) -> TerminationResult:
        """Terminate with SIGTERM → wait → SIGKILL escalation (P5-I3).

        Every adapter MUST implement this with the same semantics as
        ``ClaudeCodeRunner.terminate_with_grace`` — the budget supervisor
        calls the injected terminate callback, which the task driver wires
        to this method.
        """
        ...

    async def health_check(self) -> HealthCheckResult:
        """Probe runtime availability: binary installed, API key valid, version (FR95).

        Returns:
            :class:`HealthCheckResult` with ``installed``, ``api_key_valid``,
            and ``version`` fields.
        """
        ...
