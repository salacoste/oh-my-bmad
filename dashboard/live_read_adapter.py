from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

SourceCategory = Literal[
    "task",
    "event",
    "trace",
    "history",
    "replay",
    "lifecycle",
    "health",
    "aggregate",
    "session",
]
RouteStatus = Literal["approved", "needs-separate-contract"]
TimestampPolicy = Literal[
    "retrieved-at-required",
    "emitted-at-required",
    "retrieved-or-emitted-at-required",
    "not-available-until-contract",
]
FreshnessPolicy = Literal[
    "fresh-or-stale-required",
    "healthy-or-stale-required",
    "not-authoritative-until-contract",
]
DisplayState = Literal[
    "healthy",
    "unavailable",
    "needs-contract",
    "partial",
    "stale",
    "invalid",
    "unauthorized",
    "backend-unavailable",
]
Identifier = Literal[
    "task_id",
    "session_id",
    "event_id",
    "trace_id",
    "snapshot_id",
    "replay_id",
    "lifecycle_manifest_id",
]


@dataclass(frozen=True)
class ReadContract:
    source_category: SourceCategory
    route_pattern: str
    route_status: RouteStatus
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    required_identifiers: tuple[Identifier, ...]
    allowed_states: frozenset[DisplayState]


@dataclass(frozen=True)
class ReadRequest:
    method: Literal["GET"]
    route_pattern: str
    source_category: SourceCategory
    required_identifiers: tuple[Identifier, ...]


@dataclass(frozen=True)
class ResultMeta:
    source_category: SourceCategory
    route_pattern: str
    state: DisplayState
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    identifiers: Mapping[str, str]
    authoritative: bool


APPROVED_READ_CONTRACTS: tuple[ReadContract, ...] = (
    ReadContract(
        source_category="task",
        route_pattern="/v1/tasks/{task_id}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id",),
        allowed_states=frozenset(
            {"healthy", "unavailable", "stale", "unauthorized", "backend-unavailable"}
        ),
    ),
    ReadContract(
        source_category="event",
        route_pattern="/v1/tasks/{task_id}/events",
        route_status="approved",
        timestamp_policy="emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="event",
        route_pattern="/v1/tasks/{task_id}/transitions",
        route_status="approved",
        timestamp_policy="emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="trace",
        route_pattern="/v1/trace/{trace_id}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("trace_id",),
        allowed_states=frozenset(
            {"healthy", "unavailable", "partial", "stale", "backend-unavailable"}
        ),
    ),
    ReadContract(
        source_category="history",
        route_pattern="/v1/tasks/{task_id}/history",
        route_status="approved",
        timestamp_policy="retrieved-or-emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="replay",
        route_pattern="/v1/events/replay",
        route_status="approved",
        timestamp_policy="retrieved-or-emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("replay_id",),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="replay",
        route_pattern="/v1/events/replay/validate",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=("replay_id",),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="lifecycle",
        route_pattern="/v1/events/replay/snapshots",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=("snapshot_id", "lifecycle_manifest_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="health",
        route_pattern="/v1/health",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset({"healthy", "stale", "unavailable", "backend-unavailable"}),
    ),
)

UNAVAILABLE_READ_CONTRACTS: tuple[ReadContract, ...] = (
    ReadContract(
        source_category="aggregate",
        route_pattern="/v1/tasks",
        route_status="needs-separate-contract",
        timestamp_policy="not-available-until-contract",
        freshness_policy="not-authoritative-until-contract",
        required_identifiers=(),
        allowed_states=frozenset({"unavailable", "needs-contract"}),
    ),
    ReadContract(
        source_category="session",
        route_pattern="/v1/sessions",
        route_status="needs-separate-contract",
        timestamp_policy="not-available-until-contract",
        freshness_policy="not-authoritative-until-contract",
        required_identifiers=("session_id",),
        allowed_states=frozenset({"unavailable", "needs-contract"}),
    ),
)

EXCLUDED_ROUTE_PATTERNS = frozenset(
    {
        "/v1/tasks/{task_id}/logs/digest",
        "/v1/tasks/{task_id}/logs/digest/stream",
    }
)

NON_AUTHORITATIVE_STATES = frozenset(
    {
        "unavailable",
        "needs-contract",
        "partial",
        "stale",
        "invalid",
        "unauthorized",
        "backend-unavailable",
    }
)


def approved_read_contracts() -> tuple[ReadContract, ...]:
    return APPROVED_READ_CONTRACTS


def unavailable_read_contracts() -> tuple[ReadContract, ...]:
    return UNAVAILABLE_READ_CONTRACTS


def all_read_contracts() -> tuple[ReadContract, ...]:
    return APPROVED_READ_CONTRACTS + UNAVAILABLE_READ_CONTRACTS


def read_contract(route_pattern: str) -> ReadContract:
    for contract in all_read_contracts():
        if contract.route_pattern == route_pattern:
            return contract
    raise KeyError(route_pattern)


def read_request(route_pattern: str) -> ReadRequest:
    contract = read_contract(route_pattern)
    if contract.route_status != "approved":
        raise ValueError(route_pattern)
    return ReadRequest(
        method="GET",
        route_pattern=contract.route_pattern,
        source_category=contract.source_category,
        required_identifiers=contract.required_identifiers,
    )


def result_meta(
    route_pattern: str,
    state: DisplayState,
    identifiers: Mapping[str, str] | None = None,
) -> ResultMeta:
    contract = read_contract(route_pattern)
    if state not in contract.allowed_states:
        raise ValueError(state)
    return ResultMeta(
        source_category=contract.source_category,
        route_pattern=contract.route_pattern,
        state=state,
        timestamp_policy=contract.timestamp_policy,
        freshness_policy=contract.freshness_policy,
        identifiers=MappingProxyType({} if identifiers is None else dict(identifiers)),
        authoritative=contract.route_status == "approved" and state == "healthy",
    )
