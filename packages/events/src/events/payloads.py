"""Payload Pydantic models for all typed event schemas.

Relocated from ``registry_state.domain.event_types`` by Story 3.5.2.
The models are pure data classes — no side effects, no ``register()`` calls.
Registration stays in ``registry_state.domain.event_types`` because of a
circular-import constraint documented there.

All models use ``ConfigDict(frozen=True, strict=True, extra="forbid")``
matching the Story 2.1 discipline.

All ``actor_id`` fields enforce ``Field(min_length=1, max_length=128)`` for
log-bloat protection in append-only audit events (Story 11.2 pass-1 review
P1-H1 — extends invariant codebase-wide including Story 11.1's
``TaskApprovalSignedPayload.actor_id`` inheritance gap).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Shared regexes for ID validation (Story 2.10 review-pass tightening).
# ---------------------------------------------------------------------------

_SESSION_ID_PATTERN = r"^s-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_TASK_ID_PATTERN = r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_WORKER_ID_PATTERN = r"^w-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
# Git ref-name character-level validation. Sequence rules (.., //, /., .lock)
# enforced by @field_validator on TaskCompletedPayload.
_PR_BRANCH_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9/_.-]*[A-Za-z0-9_-])?$"


class TaskCreatedPayload(BaseModel):
    """Payload for the ``task.created`` event.

    Story 2.9 F7+F9: ``title`` (when present) is bounded to 512 chars; ``repo``
    and ``hint`` are optional creation-time inputs surfaced from the HTTP API.
    All three fields default to ``None`` so existing emit-sources that only
    pass ``task_id`` continue to work unchanged.

    Story 3.9 AC-1 / AC-11 — additive minor bump (1.0.0 → 1.1.0): ``chat_id``
    and ``reply_to_message_id`` carry the Telegram thread binding so outbound
    sinks can deliver progress events to the originating thread (FR13). Both
    are ``int | None`` (Telegram chat ids are negative for supergroups; the
    type is ``int``, NOT ``PositiveInt``). The single payload class is
    registered under both ``1.0.0`` and ``1.1.0`` per the same-model
    contract — pre-3.9 events deserialize cleanly with the new fields
    defaulting to ``None`` (additive-only NFR-M3).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=2048)
    hint: str | None = Field(default=None, min_length=1, max_length=4096)
    # Story 3.9: Telegram thread binding (FR13).
    # M13: chat_id=0 rejected; L20: explicit BigInteger bounds.
    chat_id: int | None = Field(default=None, ge=-(2**63), le=(2**63) - 1)
    # M13: reply_to_message_id must be strictly positive (Telegram msg IDs ≥ 1).
    reply_to_message_id: int | None = Field(default=None, gt=0)

    @field_validator("chat_id")
    @classmethod
    def _chat_id_not_zero(cls, v: int | None) -> int | None:
        if v == 0:
            raise ValueError("chat_id must not be 0 — Telegram never uses chat_id=0")
        return v


class TaskPlanningStartedPayload(BaseModel):
    """Payload for the ``task.planning.started`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(pattern=_TASK_ID_PATTERN)


class PlanStep(BaseModel):
    """A single step in a structured plan (Story 5.11 / FR2)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    step: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=500)


class TaskPlanReadyPayload(BaseModel):
    """Payload for the ``task.plan.ready`` event.

    Story 5.11 — additive minor bump (1.0.x → 1.1.0): ``plan`` and
    ``estimated_steps`` carry the structured step list so the Telegram
    plan-ready renderer can show individual steps. Both default to empty/zero
    so pre-5.11 events deserialize cleanly (NFR-M3 additive-only).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    plan_summary: str
    plan: tuple[PlanStep, ...] = Field(default=())
    estimated_steps: int = Field(default=0, ge=0)

    @field_validator("plan", mode="before")
    @classmethod
    def _coerce_plan_to_tuple(cls, v: object) -> object:
        """Coerce a JSON list into a tuple (Story 11.3.4).

        ``strict=True`` does not coerce list→tuple, but payloads delivered over
        the MCP/JSON boundary (clawhip-bridge ``emit_event`` →
        ``EventEnvelope.create`` → ``model_validate``) always arrive as JSON
        arrays = Python lists. Without this, any ``task.plan.ready`` carrying a
        populated ``plan`` is rejected at emit with ``tuple_type`` — which the
        S-1/S-2 separability harness surfaced (the real orchestrator-adapter
        emits the same way; PR-gate CI never round-trips a populated plan over
        stdio-JSON). Coercing on *input* keeps the stored value a frozen tuple
        and leaves ``extra="forbid"`` / strict field validation intact. No-op
        for tuples and for the empty default.
        """
        if isinstance(v, list):
            return tuple(v)
        return v


class TaskExecutionStartedPayload(BaseModel):
    """Payload for the ``task.execution.started`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    session_id: str


class TaskStepCompletedPayload(BaseModel):
    """Payload for the ``task.step.completed`` event (Story 5.12 / FR3+FR31)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    step: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=500)
    output_summary: str = Field(max_length=2000)


class TaskBlockerRaisedPayload(BaseModel):
    """Payload for the ``task.blocker_raised`` event.

    Story 3.11 AC-1 — additive minor bump (1.0.x → 1.1.0): three optional
    FR15 fields (``blocked_since``, ``last_event``, ``last_action``) are
    added so the Telegram blocker-notification renderer can include
    blocked-since timestamp, the most recent event before the blocker,
    and the most recent agent action. All three default to ``None`` so
    legacy v1.0.x events deserialize cleanly. The single payload class
    is registered under ``1.0.0``, ``1.0.1``, and ``1.1.0`` (additive-only
    NFR-M3).

    Story 3.10 H3 carry-forward — model-boundary string validators:
    ``task_id`` (1..64 chars) and ``reason`` (1..2000 chars) gain explicit
    ``min_length`` / ``max_length`` validators matching the cap pattern set
    on :class:`TaskApprovalRequestedPayload`. ``last_event`` is bounded to
    1..128 chars (event-type registry convention); ``last_action`` is
    bounded to 1..2000 chars (same shape as approval ``action``). Empty
    strings on the optional fields would render a useless ``Last event:``
    line with a trailing space — Story 3.11 review H7 closes that gap by
    adding ``min_length=1`` to both.

    Story 3.11 review H8: ``blocked_since`` is typed
    :class:`pydantic.AwareDatetime` so naive timestamps (``.isoformat()``
    omits the offset suffix → operator sees ambiguous "12:00:00" with no
    timezone) are rejected at the payload boundary.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=2000)
    # Story 3.11 — optional FR15 fields (additive, schema 1.1.0).
    # H8: AwareDatetime — naive datetimes rejected at the model boundary.
    blocked_since: AwareDatetime | None = None
    # H7: min_length=1 — empty string produces a useless ``Last event:`` line.
    last_event: str | None = Field(default=None, min_length=1, max_length=128)
    last_action: str | None = Field(default=None, min_length=1, max_length=2000)


class TaskSummaryEmittedPayload(BaseModel):
    """Payload for the ``task.summary_emitted`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    summary: str


class PreCheckOutcome(BaseModel):
    """Outcome of a single pre-check (lint / types / unit / integration).

    Story 3.10 AC-2: ``passed`` and ``total`` are non-negative integers (a 0/0
    result is technically valid — renders as ``0/0``). ``status`` is derived
    semantically by the emitter; the renderer just shows it.

    Story 3.10 review-pass tightening:

    * **H4** — cross-field invariant: ``passed <= total`` (a check cannot pass
      more cases than it ran). Without this, the renderer would print
      nonsense like ``999/3 (failed)``.
    * **H5** — semantic invariant on ``status``: ``"pass"`` requires
      ``passed == total``; ``"fail"`` requires ``passed < total``.
      ``"skipped"`` and ``"error"`` (M13) are state-of-the-check values and
      have no count constraint.
    * **M13** — ``status`` widened to include ``"skipped"`` (env unavailable)
      and ``"error"`` (the check itself crashed). Renderer maps
      ``skipped → ⏭️``, ``error → ⚠️``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    status: Literal["pass", "fail", "skipped", "error"]

    @model_validator(mode="after")
    def _check_passed_le_total(self) -> PreCheckOutcome:
        if self.passed > self.total:
            raise ValueError(f"passed ({self.passed}) cannot exceed total ({self.total})")
        return self

    @model_validator(mode="after")
    def _check_status_count_consistency(self) -> PreCheckOutcome:
        # H5: only enforce the count invariant for the binary pass/fail
        # outcomes. "skipped" and "error" (M13) are check-state values that
        # carry no count semantics.
        if self.status == "pass" and self.passed != self.total:
            raise ValueError(
                f"status='pass' requires passed == total; got "
                f"passed={self.passed}, total={self.total}"
            )
        if self.status == "fail" and self.passed >= self.total:
            raise ValueError(
                f"status='fail' requires passed < total; got "
                f"passed={self.passed}, total={self.total}"
            )
        return self


class PreCheckResults(BaseModel):
    """Aggregate pre-check results for an approval request (Story 3.10 AC-2).

    Each individual check is optional — the renderer omits the line when the
    corresponding field is ``None``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lint: PreCheckOutcome | None = None
    types: PreCheckOutcome | None = None
    unit: PreCheckOutcome | None = None
    integration: PreCheckOutcome | None = None


class DiffSummary(BaseModel):
    """Diff summary for an approval request (Story 3.10 AC-3).

    Renders as ``<files> files, +<insertions>, -<deletions>``. All three
    fields are required when ``DiffSummary`` is non-None — the emitter
    either populates the whole struct or leaves it ``None``.

    Story 3.10 review-pass L9: per-field upper bound ``<= 10**9`` so a
    buggy diff parser shipping ``insertions=2**63 - 1`` is rejected at the
    payload boundary instead of rendering a 19-digit number.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    files: int = Field(ge=0, le=10**9)
    insertions: int = Field(ge=0, le=10**9)
    deletions: int = Field(ge=0, le=10**9)


#: Story 3.10 H6 — per-element bounds for entries in
#: :attr:`TaskApprovalRequestedPayload.accepted_commands`. Each command must
#: be a non-empty string of at most 200 chars (matches the renderer's
#: per-bullet visual budget). Renderer keeps its own caps as
#: defense-in-depth.
AcceptedCommand = Annotated[str, Field(min_length=1, max_length=200)]


class TaskApprovalRequestedPayload(BaseModel):
    """Payload for the ``task.approval_requested`` event.

    Story 3.10 AC-1 — additive minor bump (1.0.0 → 1.1.0): four optional
    FR14 fields (``risk_class``, ``pre_check_results``, ``diff_summary``,
    ``accepted_commands``) are added so the Telegram approval-request
    renderer can include risk class, pre-check status, diff summary, and
    the exact commands accepted. All four default to ``None`` so legacy
    v1.0.0 events (Story 2.8 emit shape) deserialize cleanly. The single
    payload class is registered under both ``1.0.0`` and ``1.1.0``
    (additive-only NFR-M3).

    Story 3.10 review-pass tightening:

    * **H3** — ``task_id`` (1..64 chars), ``action`` (1..2000 chars), and
      ``justification`` (1..10_000 chars) gain explicit ``min_length`` /
      ``max_length`` validators. Caps reasonably above the renderer's
      3500-char total cap so wire-level validation fails fast on bad
      inputs from upstream emitters (Story 6.4).
    * **H6** — ``accepted_commands`` gains model-level bounds: ``<= 20``
      entries (renderer trims to 10 — model bound is intentionally looser
      so future stories can negotiate without a schema bump), each entry
      ``1..200`` chars via :data:`AcceptedCommand`. Defends consumers
      beyond the renderer (audit log, registry-API echo).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    action: str = Field(min_length=1, max_length=2000)
    justification: str = Field(min_length=1, max_length=10_000)
    # Story 3.10 — optional FR14 fields (additive, schema 1.1.0).
    risk_class: Literal["low", "medium", "high"] | None = None
    pre_check_results: PreCheckResults | None = None
    diff_summary: DiffSummary | None = None
    accepted_commands: list[AcceptedCommand] | None = Field(default=None, max_length=20)


class TaskCompletedPayload(BaseModel):
    """Payload for the ``task.completed`` event.

    Story 3.12 AC-1 — additive minor bump (1.0.x → 1.1.0): seven optional
    FR9 fields plus tightened model-boundary validators on the existing
    fields. The seven new fields (``pr_number``, ``pr_branch``,
    ``files_changed``, ``lines_added``, ``lines_removed``, ``tests_added``,
    ``ci_state``, ``blockers_count``) all default to ``None`` so legacy
    v1.0.x events deserialize cleanly under v1.1.0 (NFR-M3 additive-only).

    Story 3.10 H3 carry-forward — model-boundary string validators:
    ``task_id`` (1..64 chars), ``summary`` (1..2000 chars), and ``pr_url``
    (1..500 chars) gain explicit ``min_length`` / ``max_length`` validators
    matching the cap pattern set on
    :class:`TaskApprovalRequestedPayload` / :class:`TaskBlockerRaisedPayload`.

    Story 3.10 review L9 carry-forward — per-field upper bounds on the new
    integer counters defend against buggy diff-parser overflow:
    ``pr_number``, ``lines_added``, ``lines_removed`` cap at ``10**9``;
    ``files_changed``, ``tests_added``, ``blockers_count`` cap at ``10**6``
    (more than enough for any reasonable PR; defends against integer-overflow
    injection). ``pr_branch`` capped at 255 chars (git ref-name length limit).

    Story 3.12 review M14 — counter-cap semantic distinction. The 1M cap on
    file-level counters (``files_changed`` / ``tests_added`` / ``blockers_count``)
    is intentional — even a monorepo-wide refactor rarely touches >1M files,
    and tests-added / blockers-raised counts in a single ``task.completed``
    are bounded by per-task scope. The 1B cap on line-level counters
    (``lines_added`` / ``lines_removed`` / ``pr_number``) is the broader
    defense-in-depth bound matching :class:`DiffSummary` (Story 3.10 L9).
    A future story producing legitimate >1M-file events should bump the
    file-level cap explicitly rather than relying on overflow.

    Story 3.12 review M2 — ``pr_url`` constrained to ``http://`` /
    ``https://`` schemes via ``pattern=r"^https?://"``. Defense against
    operator-supplied / upstream-emitted ``javascript:`` / ``data:`` URLs
    that Telegram link-affordance heuristics might surface as clickable
    even after HTML escape.

    Story 3.12 review L4 — schema-version registration ordering. Three
    versions (1.0.0, 1.0.1, 1.1.0) of the same model are registered. The
    ``schema_registry.register`` contract is "same model for same key is a
    no-op idempotent op"; insertion order is irrelevant for the per-version
    lookup. If a future ``1.0.2`` lands (e.g. a hot-fix forced into the v1.0
    line) it must be registered explicitly with the THEN-CURRENT model — the
    1.1.0 entry is unaffected. Versions form a partial map; there is no
    "default-to-latest" behavior to worry about.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    summary: str = Field(min_length=1, max_length=2000)
    # Story 3.12 review M2: scheme-constrained — ``http(s)://`` only. Other
    # schemes (``javascript:`` / ``data:`` / ``file:``) are rejected at the
    # model boundary, not at the renderer.
    pr_url: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"^https?://")
    # Story 3.12 — optional FR9 fields (additive, schema 1.1.0).
    # Story 3.12 review L5: ``ge=1`` matches GitHub PR numbering (PRs start at
    # 1). Other VCS systems (Gitea, Forgejo, GitLab MRs) also use ``>=1`` so
    # this constraint is portable in practice; if a future host uses 0-based
    # numbering, the cap can relax with an explicit migration comment.
    pr_number: int | None = Field(default=None, ge=1, le=10**9)
    pr_branch: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=_PR_BRANCH_PATTERN
    )

    @field_validator("pr_branch")
    @classmethod
    def _validate_git_ref_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if ".." in v:
            msg = "pr_branch must not contain '..'"
            raise ValueError(msg)
        if "//" in v:
            msg = "pr_branch must not contain consecutive slashes"
            raise ValueError(msg)
        if "/." in v:
            msg = "pr_branch must not contain '/.'"
            raise ValueError(msg)
        if v.endswith(".lock"):
            msg = "pr_branch must not end with '.lock'"
            raise ValueError(msg)
        return v

    files_changed: int | None = Field(default=None, ge=0, le=10**6)
    lines_added: int | None = Field(default=None, ge=0, le=10**9)
    lines_removed: int | None = Field(default=None, ge=0, le=10**9)
    tests_added: int | None = Field(default=None, ge=0, le=10**6)
    ci_state: Literal["green", "red", "unknown"] | None = None
    blockers_count: int | None = Field(default=None, ge=0, le=10**6)
    # Story 5.15 — cumulative token usage for observability (FR44).
    token_usage: int | None = Field(default=None, ge=0, le=10**9)


# ---------------------------------------------------------------------------
# Story 2.10 — failure-detection payload models (FR24a, NFR-R5).
# ---------------------------------------------------------------------------


class ServiceCrashedPayload(BaseModel):
    """Payload for the ``service.crashed`` event.

    Emitted when a supervised process exits with a non-zero exit code.

    Field rules (post-2.10 review-pass):

    * ``service``: 1..128 chars (logical service name, e.g. ``worker-wrapper``).
    * ``exit_code``: any integer except ``0`` — ``service.crashed`` MUST NOT
      be emitted for clean exits (validator rejects ``0`` with a clear
      message).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    service: str = Field(min_length=1, max_length=128)
    exit_code: int = Field(...)

    @field_validator("exit_code")
    @classmethod
    def _exit_code_nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError(
                "exit_code must be non-zero for service.crashed (clean exits "
                "do not constitute a crash; got exit_code=0)"
            )
        return v


class SessionHeartbeatTimeoutPayload(BaseModel):
    """Payload for the ``session.heartbeat_timeout`` event.

    Emitted when a session's last heartbeat is older than 2× the configured
    heartbeat interval (strict ``>`` boundary — see :class:`HeartbeatMonitor`).

    Field rules (post-2.10 review-pass):

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``task_id``: must match ``^t-<uuidv7>$``.
    * ``last_heartbeat_at``: :class:`pydantic.AwareDatetime` — naive timestamps
      are rejected at the payload boundary.
    * ``timeout_threshold_s``: ``> 0`` and finite (``NaN``/``inf`` rejected).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    last_heartbeat_at: AwareDatetime
    timeout_threshold_s: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("last_heartbeat_at")
    @classmethod
    def _last_heartbeat_utc(cls, v: AwareDatetime) -> AwareDatetime:
        if v.utcoffset() != timedelta(0):
            raise ValueError(
                f"last_heartbeat_at must be UTC (zero offset); got utcoffset={v.utcoffset()!r}"
            )
        return v


class SinkDeliveryFailedPayload(BaseModel):
    """Payload for the ``sink.delivery_failed`` event.

    Emitted when a sink (e.g. Telegram) has accumulated ``failure_threshold``
    consecutive delivery failures.

    Field rules (post-2.10 review-pass):

    * ``sink_name``: 1..128 chars.
    * ``consecutive_failures``: ``>= 1`` (the gate fires at threshold; emit
      MUST NOT be called for zero-failure ticks).
    * ``last_error``: optional, ``<= 4096`` chars. Defense-in-depth secret
      redaction is applied at the emit site (:func:`emit_sink_delivery_failed`
      runs ``_redact_last_error`` before constructing the payload), so any
      tokens that slip past caller sanitization are masked. Callers SHOULD
      still sanitize.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sink_name: str = Field(min_length=1, max_length=128)
    consecutive_failures: int = Field(ge=1)
    last_error: str | None = Field(default=None, max_length=4096)


class TaskStopRequestedPayload(BaseModel):
    """Payload for the ``task.stop_requested`` event.

    Emitted when an operator (Telegram, console, etc.) requests that an
    in-flight task stop. Materializer state transition (e.g. ``tasks.status =
    "stopped"``) is wired in Epic 3.

    Field rules (post-2.10 review-pass):

    * ``task_id``: must match ``^t-<uuidv7>$``.
    * ``actor_id``: 1..128 chars (free-form operator identifier — e.g.
      ``telegram:12345678`` or ``console``).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    actor_id: str = Field(min_length=1, max_length=128)


class TaskSelfRecoveredPayload(BaseModel):
    """Payload for the ``task.self_recovered`` event (FR16).

    Synthesized by the clawhip-daemon proactive-summary code when a task's
    event log contains a ``session.reconnecting`` + ``task.execution.resumed``
    pair emitted overnight (between 00:00 and the morning completion summary).
    Story 3.13 ships the model + renderer; the synthesis logic is Story 7.9.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    recovered_at: AwareDatetime
    events_replayed: int = Field(ge=0, le=10**6)
    replay_duration_ms: int = Field(ge=0, le=10**9)


class SecretAccessedPayload(BaseModel):
    """Payload for the ``secret.accessed`` audit event (FR42 / NFR-S3).

    The secret VALUE is NEVER included — only the metadata identifying
    which secret was read, by which actor, and at what scope. The actor
    identity is carried on the envelope's ``actor`` field; this payload
    records the *what* (``secret_name``) and the *kind of access*
    (``scope``).

    Story 2.16 ships the infrastructure (this payload + the
    :class:`secret_hygiene.AuditedSecret` wrapper). Phase 1 only supports
    ``scope="read"``; future stories may add ``"rotated"`` or
    ``"exposed"`` once those workflows exist.

    Field rules:

    * ``secret_name``: 1..128 chars (stable identifier — e.g.
      ``"anthropic_api_key"``, ``"telegram_bot_token"``).
    * ``scope``: literal ``"read"`` (Phase 1).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    secret_name: str = Field(min_length=1, max_length=128)
    scope: Literal["read"] = "read"


# ---------------------------------------------------------------------------
# Story 5.2 — session lifecycle payload models (FR24a).
# ---------------------------------------------------------------------------


class SessionStartedPayload(BaseModel):
    """Payload for the ``session.started`` event.

    Emitted when a worker starts and registers itself with the session registry.

    Field rules:

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``worker_id``: must match ``^w-<uuidv7>$``.
    * ``task_id``: optional, must match ``^t-<uuidv7>$`` when present.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    worker_id: str = Field(min_length=1, pattern=_WORKER_ID_PATTERN)
    task_id: str | None = Field(default=None, min_length=1, pattern=_TASK_ID_PATTERN)


class SessionHeartbeatPayload(BaseModel):
    """Payload for the ``session.heartbeat`` event.

    Emitted periodically while the worker is running (default every 30 s).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)


class SessionFinishedPayload(BaseModel):
    """Payload for the ``session.finished`` event.

    Emitted on graceful shutdown (SIGTERM/SIGINT) before the process exits.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)


class SessionReconnectingPayload(BaseModel):
    """Payload for the ``session.reconnecting`` event (FR29 / Story 7.8).

    Emitted when a worker reconnects to an existing session after a host
    restart.  Paired with ``task.execution.resumed`` to signal the
    restart-recovery window.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=256)


class TaskExecutionResumedPayload(BaseModel):
    """Payload for the ``task.execution.resumed`` event (FR29 / Story 7.8).

    Emitted after ``session.reconnecting`` when the worker has replayed
    events from the journal and resumed task execution.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    events_replayed: int = Field(ge=0, le=10**6)
    replay_duration_ms: int = Field(ge=0, le=10**9)


# Schema version constant for telegram.rejected — single source of truth
# so middleware.py and future consumers don't hardcode the string.
TELEGRAM_REJECTED_SCHEMA_VERSION = "1.0.0"


class TelegramRejectedPayload(BaseModel):
    """Payload for the ``telegram.rejected`` event (FR11 / NFR-S4 audit trail).

    Emitted by the telegram-gateway's allowlist outer middleware when an
    inbound Telegram update fails the allowlist check. PII surface is
    intentionally minimal: ``user_id`` plus a structured ``reason`` only.
    No message content, no username, no chat metadata.

    Field rules:

    * ``user_id``: ``>= 0``. Real Telegram user ids are positive integers
      (``>= 1``); ``0`` is reserved as the sentinel for events lacking a
      ``from_user`` (e.g. ``my_chat_member``, ``poll`` updates) which
      the middleware rejects defensively per Story 3.2 AC-7.
    * ``reason``: ``"not_in_allowlist"`` (default — user id known but not
      whitelisted) or ``"no_from_user"`` (event arrived without a sender
      identity; rejected via the ``user_id=0`` sentinel).

    Cross-field invariant (L6):
    * ``user_id=0`` requires ``reason="no_from_user"``
    * ``user_id>0`` requires ``reason!="no_from_user"``
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    user_id: int = Field(ge=0)
    reason: Literal["not_in_allowlist", "no_from_user"] = "not_in_allowlist"

    @model_validator(mode="after")
    def _check_user_id_reason_consistency(self) -> TelegramRejectedPayload:
        if self.user_id == 0 and self.reason != "no_from_user":
            raise ValueError("user_id=0 requires reason='no_from_user'")
        if self.user_id > 0 and self.reason == "no_from_user":
            raise ValueError("reason='no_from_user' requires user_id=0")
        return self


# ---------------------------------------------------------------------------
# Story 5.5 — agent.reasoning.* breadcrumb payload (FR17b, NFR-O6).
# ---------------------------------------------------------------------------


class AgentReasoningBreadcrumbPayload(BaseModel):
    """Payload for ``agent.reasoning.*`` breadcrumb events (FR17b, NFR-O6).

    Emitted when the worker extracts reasoning content from Claude Code's
    ``thinking`` or ``text`` content blocks in stream-json output.  All text
    fields are sanitized through the secret-hygiene scanner before emission;
    if secrets are detected the text is suppressed.

    Field rules:

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``subtype``: one of three classification literals.
    * ``text``: sanitized reasoning text.  Empty string when suppressed.
    * ``suppressed``: ``True`` when secret-hygiene scanner detected sensitive
      content and the text was replaced.
    * ``tool_name``: the tool_use name when subtype is ``tool_call_rationale``.
    * ``raw_length``: original text length before sanitization (observability).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    subtype: Literal["plan_drafted", "tool_call_rationale", "step_summary"]
    text: str = Field(max_length=50_000)
    suppressed: bool = False
    tool_name: str | None = Field(default=None, min_length=1, max_length=64)
    raw_length: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_text_suppressed_consistency(self) -> AgentReasoningBreadcrumbPayload:
        if not self.suppressed and self.text == "":
            msg = "Non-suppressed breadcrumb must have non-empty text"
            raise ValueError(msg)
        return self


class FileEditedPayload(BaseModel):
    """Payload for ``file.edited`` events (FR30, NFR-R2).

    Emitted when the worker performs an atomic file edit or write.  Captures
    the file path, which Claude Code tool triggered the edit, line change
    counts, and whether secrets were detected (which aborts the write).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    file_path: str = Field(min_length=1, max_length=4096)
    tool_name: Literal["Write", "Edit"]
    lines_added: int = Field(ge=0, le=10**9)
    lines_removed: int = Field(ge=0, le=10**9)
    secrets_detected: bool = False


class TaskBudgetExceededPayload(BaseModel):
    """Payload for the ``task.budget_exceeded`` event (FR44 / NFR-P5).

    Emitted when cumulative token usage for a task exceeds the configured
    ``task_token_budget`` ceiling.  The task is blocked pending extension
    approval — ``task.completed`` is NOT emitted in this path.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    token_limit: int = Field(gt=0)
    tokens_used: int = Field(gt=0)
    step: int = Field(ge=1)


class TaskBudgetEnforcementTriggeredPayload(BaseModel):
    """Payload for the ``task.budget_enforcement_triggered`` event (FR67 / Story 12.2).

    The ACTION-RECORD event: emitted by worker-wrapper AFTER it SIGTERMs the
    Claude Code subprocess in response to a ``task.budget_exceeded`` signal
    (Story 5.15 / FR44). Distinct from ``task.budget_exceeded`` — that event
    SIGNALS the ceiling was crossed; this one RECORDS that the platform
    terminated the subprocess and how the task subsequently transitioned.

    Story 12.1's ``budget_supervisor`` owns the SIGTERM (enforcement leg) and
    deliberately deferred this audit event to Story 12.2; the trigger data
    (``token_limit`` → ``budget_threshold``, ``tokens_used`` → ``actual_spend``,
    ``step``) is carried through ``BudgetSupervisorResult``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    # The token (or dollar) ceiling that was crossed. ``int`` mirrors the
    # token-budget shape of TaskBudgetExceededPayload; dollar ceilings (when
    # added) are integer cents.
    budget_threshold: int = Field(gt=0)
    # Cumulative spend at the moment enforcement triggered.
    actual_spend: int = Field(gt=0)
    # The only action this story takes (FR67 names it explicitly). A Literal
    # rather than a free str so a future action variant is a deliberate
    # schema change, not an accidental typo.
    action_taken: Literal["subprocess_terminated"] = "subprocess_terminated"
    # How the task transitioned post-enforcement, per the per-task policy
    # declared at submission. Until Story 12.4 stores per-task policy, the
    # emitter sources this from the operator-configured default
    # (OMB_DEFAULT_BUDGET_ACTION). The event SHAPE is forward-compatible —
    # Story 12.4 changes only the value SOURCE, not this field.
    post_trigger_transition: Literal["failed", "awaiting_approval"]
    # Step counter from the matching task.budget_exceeded payload (PP16),
    # carried through BudgetSupervisorResult.step.
    step: int = Field(ge=1)


class Tier3ActionAttemptedPayload(BaseModel):
    """Payload for the ``tier3.action_attempted`` event (FR38 / Story 6.2).

    Emitted when a Tier-3 capability check is performed.  Records whether
    the action was accepted or denied, with a reason on denial.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    action: str = Field(min_length=1, max_length=2000)
    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    accepted: bool
    reason: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _reason_required_on_denial(self) -> Tier3ActionAttemptedPayload:
        if not self.accepted and self.reason is None:
            raise ValueError("reason is required when accepted=False")
        return self


class Tier3ActionPerformedPayload(BaseModel):
    """Payload for the ``tier3.action_performed`` event (FR38 / Story 6.6).

    Emitted when a Tier-3 action is actually executed after approval.
    ``actor`` is carried on the envelope, not duplicated here.
    ``performed_at`` is ``envelope.emitted_at`` — not duplicated.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    action: str = Field(min_length=1, max_length=2000)
    accepted: bool
    approval_event_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _reason_required_on_denial(self) -> Tier3ActionPerformedPayload:
        if not self.accepted and self.reason is None:
            raise ValueError("reason is required when accepted=False")
        return self


# ---------------------------------------------------------------------------
# Story 6.4 — operator decision payload models (FR7, FR41).
# ---------------------------------------------------------------------------


class ApprovalGrantedPayload(BaseModel):
    """Payload for the ``approval.granted`` event (FR7 / Story 6.4).

    Emitted when an operator approves a task (via Telegram ``/approve``,
    console CLI, or direct POST to ``/v1/tasks/{id}/decisions``).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    decision_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    override: str | None = Field(default=None, max_length=64)


class ApprovalRejectedPayload(BaseModel):
    """Payload for the ``approval.rejected`` event (FR7 / Story 6.4).

    Emitted when an operator rejects a task.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    decision_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, min_length=1, max_length=4096)


class TaskRetryRequestedPayload(BaseModel):
    """Payload for the ``task.retry_requested`` event (FR7 / Story 6.4).

    Emitted when an operator retries a blocked/failed task, optionally
    injecting a clarifying hint into the orchestrator's next planning pass.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    decision_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    hint: str | None = Field(default=None, min_length=1, max_length=4096)


class LicenseOverridePayload(BaseModel):
    """Payload for the ``tier3.license_override`` event (FR41 / Story 6.4).

    Emitted alongside ``approval.granted`` when the operator explicitly
    overrides a license flag with ``override: "license"``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    decision_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4096)


class BudgetOverridePayload(BaseModel):
    """Payload for the ``tier3.budget_override`` event (FR44 / Story 6.11).

    Emitted alongside ``approval.granted`` when the operator explicitly
    overrides a budget-exceeded block with ``override: "budget"``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    decision_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    old_limit: int = Field(gt=0)
    new_limit: int = Field(gt=0, le=1_000_000_000)

    @model_validator(mode="after")
    def _new_limit_exceeds_old(self) -> BudgetOverridePayload:
        if self.new_limit <= self.old_limit:
            raise ValueError("new_limit must exceed old_limit")
        return self


class TaskLicenseFlaggedPayload(BaseModel):
    """Payload for the ``task.license_flagged`` event (FR40 / Story 6.10).

    Emitted when the license scan detects an incompatible license in files
    staged for push.  Carries structured finding data so the approval gate
    can block and the operator can review details.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64, pattern=_TASK_ID_PATTERN)
    reason_code: str = Field(min_length=1, max_length=64)
    file_list: list[str] = Field(min_length=1, max_length=100)
    detected_licenses: list[str] = Field(min_length=1, max_length=100)


class TaskApprovalSignedPayload(BaseModel):
    """Sibling event emitted alongside ``approval.granted`` carrying HMAC signature.

    Story 11.1 P1-H2 applied full Field constraints at schema_version ``1.0.0``.
    Story 11.2 will bump schema_version ``1.0.0`` → ``1.1.0`` (additive — same
    payload shape) and add contract-fixture forward-compat pair. The Field
    constraints here (HMAC format, no-pipe patterns) are Story 11.1 invariants,
    not deferred to 11.2.

    Field constraints (P1-H2):

    * ``task_id`` / ``decision_id``: alphanumeric + ``_:.-`` only (explicit
      no-pipe — defense-in-depth against canonical-string injection, P1-H1).
    * ``actor_id``: non-empty string (pattern constrained to Story 6.1+ JWT
      sub format when auth lands; not further restricted here).
    * ``hmac_sha256``: exactly 64 lowercase hex characters (HMAC-SHA256
      hexdigest contract).

    See ADR-0006 (to be drafted in Story 11.5) for signing / verification protocol.

    Ordering invariant (per Story 11.1): the paired ``approval.granted`` event
    MUST be appended to the event log BEFORE this ``task.approval_signed``
    event so that downstream verification (``just verify-approval``, Story
    11.4) can find the signed sibling AFTER the granted parent in tail order.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # P1-H2 field constraints: explicit no-pipe on canonical-string fields.
    task_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_:.-]+$")
    decision_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_:.-]+$")
    # pattern extended in Story 6.1+; max_length=128 per Story 11.2 pass-1 P1-H1.
    actor_id: str = Field(min_length=1, max_length=128)
    action: Literal["approve"]
    timestamp: AwareDatetime
    hmac_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Story 11.2 — key-rotation + capability-denial audit payloads
# (FR65a / NFR-S10 / Epic 10 retro DD5).
# ---------------------------------------------------------------------------


class KeyRotatedPayload(BaseModel):
    """Audit event recording that the operator's HMAC signing key has been rotated.

    Emitted exactly once per actual rotation by Story 11.5's key-rotation
    detector (compares fingerprint of current ``OPERATOR_HMAC_KEY`` against
    last-known fingerprint persisted in registry-state).

    Per FR65a: pre-rotation approvals remain verifiable ONLY via the prior
    key — operator's responsibility to retain it for audit-window duration.
    The ``key.rotated`` event records the rotation timestamp + fingerprints
    (NEVER the keys themselves — NFR-S10 isolation: keys NEVER appear in
    events/logs/snapshots/registry).

    Field rules (Story 11.2 D2, D3):

    * ``rotated_at`` — :class:`pydantic.AwareDatetime`; naive timestamps
      rejected at the payload boundary.
    * ``previous_key_fingerprint`` / ``new_key_fingerprint`` — exactly 16
      lowercase hex chars (D2: ``SHA-256(key_bytes).hex()[:16]`` = 64
      bits; operator-readable in audit logs, collision-safe for
      single-operator key populations). Story 11.5 owns the
      fingerprint computation.
    * Cross-field invariant (D3): ``previous_key_fingerprint`` MUST differ
      from ``new_key_fingerprint``. Equality is not a rotation — it would
      either be a detection bug or a replay-attack attempting to emit a
      no-op rotation event. Reject at model boundary.
    * **Bootstrap sentinel (Story 11.5 D1 + PP17)** — first-boot
      detection emits with ``previous_key_fingerprint = "0000000000000000"``
      (16 zero-hex chars; collision probability with a real
      ``SHA-256(key_bytes).hex()[:16]`` is 2⁻⁶⁴). The cross-field
      validator above still applies — a malformed payload with both
      fields set to the sentinel is rejected (no-op rotation). See
      ADR-0006 for the rationale.
    * ``actor_id`` — non-empty string; constrained to ``min_length=1`` only
      (NOT the Story 11.1 no-pipe pattern) because the rotation actor may
      be a richer service-account identifier than ``approval.granted``
      operator strings.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    rotated_at: AwareDatetime
    previous_key_fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    new_key_fingerprint: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$")
    actor_id: str = Field(min_length=1, max_length=128)  # max_length=128 per pass-1 P1-H1

    @model_validator(mode="after")
    def _reject_no_op_rotation(self) -> KeyRotatedPayload:
        if self.previous_key_fingerprint == self.new_key_fingerprint:
            raise ValueError(
                "previous_key_fingerprint must differ from new_key_fingerprint (no-op rotation)"
            )
        return self


class ApprovalInboxOpenedPayload(BaseModel):
    """Operator opened a pinned approval inbox via the ``/approvals`` command (Story 11.3).

    Drives the registry-state ``approval_inbox`` table (Story 11.3 AC2):
    one row per ``operator_chat_id`` with the operator's pinned Forum-Topic
    inbox thread. Read by clawhip-daemon (via registry-api HTTP — D5
    outcome below) to route ``task.approval_requested`` events to the
    pinned inbox instead of the originating task thread (FR63).

    Idempotent: re-emitting for the same ``operator_chat_id`` replaces
    the previous ``inbox_thread_id`` (operator opened a NEW inbox; the
    old one is deprecated). UPSERT semantics in the materializer.

    Field rules (Story 11.3 AC3):

    * ``operator_chat_id`` — bounded to int64 range (Telegram chat ids
      can be negative for supergroups, so the lower bound is ``-2**63``,
      not ``1``).
    * ``inbox_thread_id`` — bounded to ``>= 1`` and ``< 2**63`` (Telegram
      thread ids are positive int64).
    * ``opened_at`` — :class:`pydantic.AwareDatetime`; naive timestamps
      rejected at the payload boundary.
    * ``opened_by_actor_id`` — non-empty string, ``max_length=128`` per
      Story 11.2 P1-H1 codebase-wide invariant.

    No ``task_id`` field: this event is operator-session-scoped, not
    task-scoped. The materializer's ``_extract_ids`` returns ``(None,
    None)`` accordingly — the ``approval.inbox_opened`` event row in
    the ``events`` table has ``task_id IS NULL``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # Telegram chat_id is int64; supergroups have negative ids. Bounded
    # to the int64 range for canonical-JSON portability across SQLite
    # BIGINT columns.
    operator_chat_id: int = Field(ge=-(2**63), le=2**63 - 1)
    # Telegram thread_id is positive int64; ``forum_topic.message_thread_id``
    # is always >= 1 (Telegram Bot API contract).
    inbox_thread_id: int = Field(ge=1, le=2**63 - 1)
    opened_at: AwareDatetime
    # Story 11.3 review P30: ``opened_by_actor_id`` restricted to
    # ``[A-Za-z0-9_-]`` so control characters / pipe injection / shell
    # metacharacters cannot reach downstream renderers. Matches the
    # Story 11.1 P1-H3 actor_id pattern discipline.
    opened_by_actor_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_\-]{1,128}$"
    )


class CapabilityDeniedPayload(BaseModel):
    """Audit event recording a capability-tier denial at the MCP / HTTP boundary.

    Emitted by ``TierEnforcementMiddleware`` (HTTP) or capability-handler
    decorators (MCP) when a request exceeds the actor's permitted tier.
    Story 10.4 reserved ``omb_capability_denied_total{tier, boundary}``
    counter as a preview-only metric with pre-populated zero values
    pending this event type. Emission wiring deferred (out of scope for
    Story 11.2 — see DAR; tracked as DD5 half-done state).

    Per Epic 10 retro DD5 — registered here as the natural bundling
    opportunity with other Epic 11 event type registrations.

    Field rules:

    * ``tier`` / ``boundary`` — bounded ``Literal`` values that MUST stay
      in sync with Story 10.4's ``_CAPABILITY_TIERS`` /
      ``_CAPABILITY_BOUNDARIES`` constants in
      ``metrics-subscriber/app/metrics.py``. The enum-drift contract test
      ``test_capability_denied_payload_tier_enum_matches_story_10_4_metrics``
      catches any future divergence.
    * ``actor_id`` — ``min_length=1`` only; could be UUID, hostname,
      opaque MCP client ID. Story 6.1+ may tighten when real auth lands.
    * ``attempted_action`` — opaque string (e.g. ``task.create``,
      ``decision.approve``); deliberately UNCONSTRAINED beyond
      ``min_length=1`` because MCP methods are ad-hoc strings and
      enum-ing them would force schema_version bumps every time MCP
      adds a method.
    * ``reason`` — optional operator-facing explanation (e.g.
      "license-flagged paths"). Bounded to 4096 chars to match the
      sibling ``Tier3ActionAttemptedPayload.reason`` cap.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    tier: Literal["tier1", "tier2", "tier3"]
    boundary: Literal["mcp", "http"]
    actor_id: str = Field(min_length=1, max_length=128)  # max_length=128 per pass-1 P1-H1
    attempted_action: str = Field(min_length=1)
    reason: str | None = Field(default=None, min_length=1, max_length=4096)


__all__ = [
    "TELEGRAM_REJECTED_SCHEMA_VERSION",
    "AcceptedCommand",
    "AgentReasoningBreadcrumbPayload",
    "ApprovalGrantedPayload",
    "ApprovalInboxOpenedPayload",
    "BudgetOverridePayload",
    "ApprovalRejectedPayload",
    "CapabilityDeniedPayload",
    "DiffSummary",
    "FileEditedPayload",
    "KeyRotatedPayload",
    "LicenseOverridePayload",
    "PlanStep",
    "PreCheckOutcome",
    "PreCheckResults",
    "SecretAccessedPayload",
    "ServiceCrashedPayload",
    "SessionFinishedPayload",
    "SessionHeartbeatPayload",
    "SessionHeartbeatTimeoutPayload",
    "SessionReconnectingPayload",
    "SessionStartedPayload",
    "SinkDeliveryFailedPayload",
    "TaskApprovalRequestedPayload",
    "TaskApprovalSignedPayload",
    "TaskBlockerRaisedPayload",
    "TaskBudgetEnforcementTriggeredPayload",
    "TaskBudgetExceededPayload",
    "TaskCompletedPayload",
    "TaskLicenseFlaggedPayload",
    "TaskCreatedPayload",
    "TaskExecutionResumedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskRetryRequestedPayload",
    "TaskSelfRecoveredPayload",
    "TaskStepCompletedPayload",
    "TaskStopRequestedPayload",
    "TaskSummaryEmittedPayload",
    "TelegramRejectedPayload",
    "Tier3ActionAttemptedPayload",
    "Tier3ActionPerformedPayload",
]
