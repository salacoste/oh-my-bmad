"""Typed exception hierarchy for event-envelope + schema-registry errors.

Stories that emit events import these and raise them via the MCP surface
(Story 2.8 clawhip-bridge + Story 2.4 registry-state writer). Story 2.1 only
defines the classes; runtime emission of `event.unknown_schema` events lands
in Story 2.4.
"""

from __future__ import annotations


class EventsError(Exception):
    """Base class for all event-envelope / schema-registry errors."""


class EventSchemaUnknown(EventsError):  # noqa: N818
    """Raised when EventEnvelope.create() sees an unregistered (type, version)."""

    def __init__(
        self,
        event_type: str,
        schema_version: str,
        registered_types: frozenset[str],
    ) -> None:
        self.event_type = event_type
        self.schema_version = schema_version
        self.registered_types = registered_types
        super().__init__(self._format())

    def _format(self) -> str:
        total = len(self.registered_types)
        if total == 0:
            known = "(empty registry)"
        elif total <= 10:
            known = ", ".join(sorted(self.registered_types))
        else:
            first_10 = sorted(self.registered_types)[:10]
            known = ", ".join(first_10) + f", ... ({total - 10} more)"
        return (
            f"unknown event schema ({self.event_type!r}, {self.schema_version!r}); "
            f"registered types: {known}"
        )


class EventValidationError(EventsError):
    """Wraps Pydantic ValidationError with platform-facing formatting."""

    def __init__(self, message: str, *, pydantic_error: Exception | None = None) -> None:
        self.pydantic_error = pydantic_error
        super().__init__(message)


class CanonicalSerializationError(EventsError):
    """Raised when canonical serialization fails (NaN/Inf, non-serializable payload)."""


class WorktreeLockHeld(EventsError):  # noqa: N818
    """Raised when a worker attempts to lock a worktree already held by another session.

    Attributes:
        session_id: The session that currently holds the lock.
        worktree_path: The worktree directory containing the lock file.
    """

    def __init__(self, session_id: str, worktree_path: str) -> None:
        self.session_id = session_id
        self.worktree_path = worktree_path
        super().__init__(f"worktree lock held by session {session_id!r} at {worktree_path!r}")


class BudgetExceeded(EventsError):  # noqa: N818
    """Reserved for future internal signaling of budget breaches.

    The public interface for budget breaches is the ``task.budget_exceeded``
    event — consumers should listen for the event, not catch this exception.
    Planned for internal signaling in Story 5.17a (resume-after-approval).
    Currently unused; the event-payload path (``TaskBudgetExceededPayload``)
    is used exclusively.
    """

    def __init__(self, task_id: str, token_limit: int, tokens_used: int, step: int) -> None:
        self.task_id = task_id
        self.token_limit = token_limit
        self.tokens_used = tokens_used
        self.step = step
        super().__init__(
            f"task {task_id!r} exceeded token budget at step {step}: "
            f"{tokens_used}/{token_limit} tokens"
        )


class CursorSchemaVersionError(EventsError):  # noqa: N818
    """Raised when a persisted cursor file declares an unknown schema_version.

    Story 10.2 pass-1 VH-9: refusing to start on an unknown schema_version
    (vs silently resetting to offset 0) avoids replaying an entire day's
    worth of events after a forward-rollback (v2 written, then rolled back
    to v1). Operators must inspect and fix manually.
    """

    def __init__(self, *, cursor_path: str, schema_version: object, expected: str) -> None:
        self.cursor_path = cursor_path
        self.schema_version = schema_version
        self.expected = expected
        super().__init__(
            f"cursor at {cursor_path!r} has unknown schema_version={schema_version!r} "
            f"(expected {expected!r}); refuse to start — manual operator inspection required"
        )


class ParseSkipThresholdExceeded(EventsError):  # noqa: N818
    """Raised when a JSONL log accumulates too many consecutive parse-skips.

    Story 10.2 pass-2 P2-H3 (Q5): routes operational log-corruption
    through a structured failure path so the subscriber exits with a
    distinct code (3) and logs the offset of the corrupted region.
    Without this, the prior generic ``RuntimeError`` caused an
    infinite restart loop — the cursor would persist before the
    corrupted run, then the next restart re-read the same bytes and
    re-raised.

    Operators can manually advance the cursor past the corrupted
    region via an offline tool (Story 10.4+ scope).

    Attributes:
        path: The JSONL file where the threshold was exceeded.
        offset: The byte offset where the corrupted region begins
            (i.e., the start of the run of un-parseable lines).
        threshold: The configured ``max_contiguous_parse_skips``.
    """

    def __init__(self, *, path: str, offset: int, threshold: int) -> None:
        self.path = path
        self.offset = offset
        self.threshold = threshold
        super().__init__(
            f"metrics_subscriber_parse_skip_threshold_exceeded: "
            f"more than {threshold} consecutive unparseable lines in "
            f"{path!r} starting at offset={offset}; refusing to advance "
            "cursor past corrupted region. Operator inspection required."
        )


class CapabilityDenied(EventsError):  # noqa: N818
    """Raised when a caller's actor kind is not authorized for the requested tier.

    Attributes:
        action: The action that was attempted.
        actor_kind: The caller's actor kind.
        required_tier: The tier required for this action (as int).
        reason: Human-readable explanation.
    """

    def __init__(self, action: str, actor_kind: str, required_tier: int, reason: str) -> None:
        self.action = action
        self.actor_kind = actor_kind
        self.required_tier = required_tier
        self.reason = reason
        super().__init__(self._format())

    def _format(self) -> str:
        return (
            f"capability denied: actor_kind={self.actor_kind!r} "
            f"not authorized for Tier.{self.required_tier} "
            f"action {self.action!r}: {self.reason}"
        )


__all__ = [
    "BudgetExceeded",
    "CanonicalSerializationError",
    "CapabilityDenied",
    "CursorSchemaVersionError",
    "EventSchemaUnknown",
    "EventValidationError",
    "EventsError",
    "ParseSkipThresholdExceeded",
    "WorktreeLockHeld",
]
