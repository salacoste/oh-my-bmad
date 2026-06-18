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
PanelFamily = Literal[
    "task-detail",
    "event-timeline",
    "trace-correlation",
    "task-history",
    "replay-readiness",
    "lifecycle-readiness",
    "health-readiness",
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


@dataclass(frozen=True)
class PanelReadRoute:
    route_pattern: str
    source_category: SourceCategory
    route_input_identifiers: tuple[Identifier, ...]
    row_display_identifiers: tuple[Identifier, ...]
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    allowed_states: frozenset[DisplayState]
    non_authoritative_states: frozenset[DisplayState]


@dataclass(frozen=True)
class PanelContract:
    panel_family: PanelFamily
    title: str
    routes: tuple[PanelReadRoute, ...]


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
STORY_96_1_ROUTE_PATTERNS = (
    "/v1/tasks/{task_id}",
    "/v1/tasks/{task_id}/events",
    "/v1/tasks/{task_id}/transitions",
    "/v1/trace/{trace_id}",
)
STORY_96_1_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}": ("task_id",),
        "/v1/tasks/{task_id}/events": ("task_id",),
        "/v1/tasks/{task_id}/transitions": ("task_id",),
        "/v1/trace/{trace_id}": ("trace_id",),
    }
)
STORY_96_1_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}": ("task_id",),
        "/v1/tasks/{task_id}/events": ("task_id", "event_id", "trace_id"),
        "/v1/tasks/{task_id}/transitions": ("task_id", "event_id", "trace_id"),
        "/v1/trace/{trace_id}": ("trace_id", "event_id", "task_id", "session_id"),
    }
)
STORY_96_1_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {
        "task-detail": ("/v1/tasks/{task_id}",),
        "event-timeline": (
            "/v1/tasks/{task_id}/events",
            "/v1/tasks/{task_id}/transitions",
        ),
        "trace-correlation": ("/v1/trace/{trace_id}",),
    }
)
STORY_96_1_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {
        "task-detail": "Task detail",
        "event-timeline": "Event timeline and transitions",
        "trace-correlation": "Trace correlation",
    }
)

STORY_96_2_ROUTE_PATTERNS = (
    "/v1/tasks/{task_id}/history",
    "/v1/events/replay",
    "/v1/events/replay/validate",
    "/v1/events/replay/snapshots",
    "/v1/health",
)
STORY_96_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}/history": ("task_id",),
        "/v1/events/replay": (),
        "/v1/events/replay/validate": (),
        "/v1/events/replay/snapshots": (),
        "/v1/health": (),
    }
)
STORY_96_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}/history": ("task_id", "event_id", "trace_id"),
        "/v1/events/replay": ("replay_id", "event_id", "trace_id"),
        "/v1/events/replay/validate": ("replay_id",),
        "/v1/events/replay/snapshots": (
            "snapshot_id",
            "lifecycle_manifest_id",
            "replay_id",
        ),
        "/v1/health": (),
    }
)
STORY_96_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {
        "task-history": ("/v1/tasks/{task_id}/history",),
        "replay-readiness": (
            "/v1/events/replay",
            "/v1/events/replay/validate",
        ),
        "lifecycle-readiness": ("/v1/events/replay/snapshots",),
        "health-readiness": ("/v1/health",),
    }
)
STORY_96_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {
        "task-history": "Task history",
        "replay-readiness": "Replay readiness",
        "lifecycle-readiness": "Lifecycle readiness",
        "health-readiness": "Health readiness",
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


def story_96_1_route_patterns() -> tuple[str, ...]:
    _validate_story_96_1_subset()
    return STORY_96_1_ROUTE_PATTERNS


def story_96_1_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_96_1_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_96_1_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_96_1_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_96_1_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_96_1_PANEL_ROUTES.items()
    )


def story_96_2_route_patterns() -> tuple[str, ...]:
    _validate_story_96_2_subset()
    return STORY_96_2_ROUTE_PATTERNS


def story_96_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_96_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_96_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_96_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_96_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_96_2_PANEL_ROUTES.items()
    )


def _panel_read_route(
    route_pattern: str,
    *,
    route_input_identifiers: Mapping[str, tuple[Identifier, ...]],
    row_display_identifiers: Mapping[str, tuple[Identifier, ...]],
) -> PanelReadRoute:
    contract = read_contract(route_pattern)
    if contract.route_status != "approved":
        raise ValueError(route_pattern)
    return PanelReadRoute(
        route_pattern=contract.route_pattern,
        source_category=contract.source_category,
        route_input_identifiers=route_input_identifiers[route_pattern],
        row_display_identifiers=row_display_identifiers[route_pattern],
        timestamp_policy=contract.timestamp_policy,
        freshness_policy=contract.freshness_policy,
        allowed_states=contract.allowed_states,
        non_authoritative_states=contract.allowed_states & NON_AUTHORITATIVE_STATES,
    )


def _validate_story_96_1_subset() -> None:
    selected = set(STORY_96_1_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_96_1_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 96.1 panel route mismatch")
    if set(STORY_96_1_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 96.1 route-input identifier mismatch")
    if set(STORY_96_1_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 96.1 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 96.1 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 96.1 route requires separate contract")


def _validate_story_96_2_subset() -> None:
    selected = set(STORY_96_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_96_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 96.2 panel route mismatch")
    if set(STORY_96_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 96.2 route-input identifier mismatch")
    if set(STORY_96_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 96.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 96.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 96.2 route requires separate contract")
