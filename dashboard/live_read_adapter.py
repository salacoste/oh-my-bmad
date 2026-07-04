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
    "health",
    "digest",
    "aggregate",
    "session",
    "lifecycle",
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
    "provider-unavailable",
    "empty-digest",
    "empty-list",
]
AuthorityState = Literal["authoritative", "non-authoritative", "needs-contract"]
DisplaySeverity = Literal["normal", "warning", "error", "blocked"]
FixtureProvenance = Literal["static-fixture", "snapshot-fixture", "contract-fixture"]
DegradedStateCategory = Literal[
    "none",
    "unavailable",
    "needs-contract",
    "partial",
    "stale",
    "invalid",
    "unauthorized",
    "backend-unavailable",
    "provider-unavailable",
    "empty-digest",
    "empty-list",
]
Identifier = Literal[
    "task_id",
    "session_id",
    "event_id",
    "trace_id",
    "replay_id",
    "task_status",
    "task_list_limit",
    "task_list_offset",
    "task_sort",
    "task_search_field",
    "task_search_operator",
    "task_search_query",
    "plan_hash",
]
PanelFamily = Literal[
    "task-detail",
    "event-timeline",
    "trace-correlation",
    "task-history",
    "replay-readiness",
    "health-readiness",
    "task-log-digest",
    "digest-stream",
    "aggregate-task-list",
    "session-list",
    "session-detail",
    "lifecycle-snapshot",
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


@dataclass(frozen=True)
class RouteViewModel:
    panel_family: PanelFamily
    route_pattern: str
    source_category: SourceCategory
    route_input_identifiers: tuple[Identifier, ...]
    row_display_identifiers: tuple[Identifier, ...]
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    display_state: DisplayState
    degraded_state_category: DegradedStateCategory
    authority_state: AuthorityState
    display_severity: DisplaySeverity
    display_copy: str
    read_only_contract: bool = True


@dataclass(frozen=True)
class PanelViewModel:
    panel_family: PanelFamily
    title: str
    routes: tuple[RouteViewModel, ...]


@dataclass(frozen=True)
class SourceIdentifier:
    name: str
    fixture_value: str


@dataclass(frozen=True)
class RouteFixtureRow:
    panel_family: PanelFamily
    source_route_pattern: str
    source_category: SourceCategory
    route_input_identifiers: tuple[Identifier, ...]
    row_display_identifiers: tuple[Identifier, ...]
    source_identifiers: tuple[SourceIdentifier, ...]
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    fixture_provenance: FixtureProvenance
    fixture_timestamp_label: str
    fixture_freshness_label: str
    display_state: DisplayState
    degraded_state_category: DegradedStateCategory
    authority_state: AuthorityState
    display_severity: DisplaySeverity
    display_copy: str
    renderer_context_fields: Mapping[str, str]
    read_only_contract: bool = True


@dataclass(frozen=True)
class PanelFixtureSnapshot:
    panel_family: PanelFamily
    title: str
    rows: tuple[RouteFixtureRow, ...]


@dataclass(frozen=True)
class RouteFixtureProbe:
    source_route_pattern: str
    renderable: bool
    display_state: DisplayState
    authority_state: AuthorityState
    display_severity: DisplaySeverity
    display_copy: str


STORY_127_3_SEARCH_ROUTE_PATTERN = (
    "/v1/tasks?field={task_search_field}&op={task_search_operator}"
    "&q={task_search_query}&status={task_status}&limit={task_list_limit}"
    "&offset={task_list_offset}&sort={task_sort}"
)

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
        source_category="health",
        route_pattern="/v1/health",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset({"healthy", "stale", "unavailable", "backend-unavailable"}),
    ),
    ReadContract(
        source_category="digest",
        route_pattern="/v1/tasks/{task_id}/logs/digest",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id",),
        allowed_states=frozenset(
            {
                "healthy",
                "unavailable",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "provider-unavailable",
                "empty-digest",
            }
        ),
    ),
    ReadContract(
        source_category="digest",
        route_pattern="/v1/tasks/{task_id}/logs/digest/stream",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id",),
        allowed_states=frozenset(
            {
                "healthy",
                "unavailable",
                "partial",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "provider-unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="aggregate",
        route_pattern="/v1/tasks",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="aggregate",
        route_pattern="/v1/tasks?status={task_status}&limit={task_list_limit}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_status", "task_list_limit"),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="aggregate",
        route_pattern="/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_list_limit", "task_list_offset"),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="aggregate",
        route_pattern="/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_status", "task_list_limit", "task_list_offset"),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="aggregate",
        route_pattern=STORY_127_3_SEARCH_ROUTE_PATTERN,
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=(
            "task_search_field",
            "task_search_operator",
            "task_search_query",
            "task_status",
            "task_list_limit",
            "task_list_offset",
            "task_sort",
        ),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="session",
        route_pattern="/v1/sessions",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="session",
        route_pattern="/v1/sessions/{session_id}",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("session_id",),
        allowed_states=frozenset(
            {
                "healthy",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
    ReadContract(
        source_category="lifecycle",
        route_pattern="/v1/events/replay/lifecycle/mutations",
        route_status="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset(
            {
                "healthy",
                "empty-list",
                "stale",
                "invalid",
                "unauthorized",
                "backend-unavailable",
                "unavailable",
            }
        ),
    ),
)

UNAVAILABLE_READ_CONTRACTS: tuple[ReadContract, ...] = ()

EXCLUDED_ROUTE_PATTERNS: frozenset[str] = frozenset()
STORY_99_1_NEEDS_SEPARATE_CONTRACT_ROUTE_PATTERNS: frozenset[str] = frozenset()
STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS = frozenset(
    STORY_99_1_NEEDS_SEPARATE_CONTRACT_ROUTE_PATTERNS | EXCLUDED_ROUTE_PATTERNS
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
        "provider-unavailable",
        "empty-digest",
        "empty-list",
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
    "/v1/health",
)
STORY_96_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}/history": ("task_id",),
        "/v1/events/replay": (),
        "/v1/events/replay/validate": (),
        "/v1/health": (),
    }
)
STORY_96_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks/{task_id}/history": ("task_id", "event_id", "trace_id"),
        "/v1/events/replay": ("replay_id", "event_id", "trace_id"),
        "/v1/events/replay/validate": ("replay_id",),
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
        "health-readiness": ("/v1/health",),
    }
)
STORY_96_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {
        "task-history": "Task history",
        "replay-readiness": "Replay readiness",
        "health-readiness": "Health readiness",
    }
)

STORY_108_2_ROUTE_PATTERNS = ("/v1/tasks/{task_id}/logs/digest",)
STORY_108_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/tasks/{task_id}/logs/digest": ("task_id",)}
)
STORY_108_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/tasks/{task_id}/logs/digest": ("task_id", "trace_id")}
)
STORY_108_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {"task-log-digest": ("/v1/tasks/{task_id}/logs/digest",)}
)
STORY_108_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"task-log-digest": "Task log digest"}
)

STORY_112_2_ROUTE_PATTERNS = ("/v1/tasks/{task_id}/logs/digest/stream",)
STORY_112_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/tasks/{task_id}/logs/digest/stream": ("task_id",)}
)
STORY_112_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/tasks/{task_id}/logs/digest/stream": ("task_id", "trace_id")}
)
STORY_112_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {"digest-stream": ("/v1/tasks/{task_id}/logs/digest/stream",)}
)
STORY_112_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"digest-stream": "Digest stream"}
)

# Aggregate task-list approved-route inventory is cumulative: earlier approved
# route contracts stay as inert fixture/adapter evidence so historical dashboard
# contracts remain independently green. Story 121.2 runtime consumption below uses
# only the visible status+limit+offset controls and exact canonical fetch.
STORY_109_2_ROUTE_PATTERNS = (
    "/v1/tasks",
    "/v1/tasks?status={task_status}&limit={task_list_limit}",
    "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
    "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
    STORY_127_3_SEARCH_ROUTE_PATTERN,
)
STORY_109_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks": (),
        "/v1/tasks?status={task_status}&limit={task_list_limit}": (
            "task_status",
            "task_list_limit",
        ),
        "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}": (
            "task_list_limit",
            "task_list_offset",
        ),
        "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}": (
            "task_status",
            "task_list_limit",
            "task_list_offset",
        ),
        STORY_127_3_SEARCH_ROUTE_PATTERN: (
            "task_search_field",
            "task_search_operator",
            "task_search_query",
            "task_status",
            "task_list_limit",
            "task_list_offset",
            "task_sort",
        ),
    }
)
STORY_109_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {
        "/v1/tasks": ("task_id", "event_id", "trace_id"),
        "/v1/tasks?status={task_status}&limit={task_list_limit}": (
            "task_id",
            "event_id",
            "trace_id",
        ),
        "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}": (
            "task_id",
            "event_id",
            "trace_id",
        ),
        "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}": (
            "task_id",
            "event_id",
            "trace_id",
        ),
        STORY_127_3_SEARCH_ROUTE_PATTERN: (
            "task_id",
            "event_id",
            "trace_id",
        ),
    }
)
STORY_109_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {
        "aggregate-task-list": (
            "/v1/tasks",
            "/v1/tasks?status={task_status}&limit={task_list_limit}",
            "/v1/tasks?limit={task_list_limit}&offset={task_list_offset}",
            "/v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
            STORY_127_3_SEARCH_ROUTE_PATTERN,
        )
    }
)
STORY_109_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"aggregate-task-list": "Aggregate task list"}
)

STORY_110_2_ROUTE_PATTERNS = ("/v1/sessions",)
STORY_110_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/sessions": ()}
)
STORY_110_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/sessions": ("session_id", "task_id")}
)
STORY_110_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {"session-list": ("/v1/sessions",)}
)
STORY_110_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"session-list": "Session list"}
)

STORY_111_2_ROUTE_PATTERNS = ("/v1/sessions/{session_id}",)
STORY_111_2_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/sessions/{session_id}": ("session_id",)}
)
STORY_111_2_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/sessions/{session_id}": ("session_id", "task_id")}
)
STORY_111_2_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {"session-detail": ("/v1/sessions/{session_id}",)}
)
STORY_111_2_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"session-detail": "Session detail"}
)

STORY_129_5_ROUTE_PATTERNS = ("/v1/events/replay/lifecycle/mutations",)
STORY_129_5_ROUTE_INPUT_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/events/replay/lifecycle/mutations": ()}
)
STORY_129_5_ROW_DISPLAY_IDENTIFIERS: Mapping[str, tuple[Identifier, ...]] = MappingProxyType(
    {"/v1/events/replay/lifecycle/mutations": ("plan_hash",)}
)
STORY_129_5_PANEL_ROUTES: Mapping[PanelFamily, tuple[str, ...]] = MappingProxyType(
    {"lifecycle-snapshot": ("/v1/events/replay/lifecycle/mutations",)}
)
STORY_129_5_PANEL_TITLES: Mapping[PanelFamily, str] = MappingProxyType(
    {"lifecycle-snapshot": "Lifecycle mutation status"}
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


def story_108_2_route_patterns() -> tuple[str, ...]:
    _validate_story_108_2_subset()
    return STORY_108_2_ROUTE_PATTERNS


def story_108_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_108_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_108_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_108_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_108_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_108_2_PANEL_ROUTES.items()
    )


def story_112_2_route_patterns() -> tuple[str, ...]:
    _validate_story_112_2_subset()
    return STORY_112_2_ROUTE_PATTERNS


def story_112_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_112_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_112_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_112_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_112_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_112_2_PANEL_ROUTES.items()
    )


def story_109_2_route_patterns() -> tuple[str, ...]:
    _validate_story_109_2_subset()
    return STORY_109_2_ROUTE_PATTERNS


def story_109_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_109_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_109_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_109_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_109_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_109_2_PANEL_ROUTES.items()
    )


def story_110_2_route_patterns() -> tuple[str, ...]:
    _validate_story_110_2_subset()
    return STORY_110_2_ROUTE_PATTERNS


def story_110_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_110_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_110_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_110_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_110_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_110_2_PANEL_ROUTES.items()
    )


def story_111_2_route_patterns() -> tuple[str, ...]:
    _validate_story_111_2_subset()
    return STORY_111_2_ROUTE_PATTERNS


def story_111_2_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_111_2_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_111_2_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_111_2_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_111_2_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_111_2_PANEL_ROUTES.items()
    )


def story_129_5_route_patterns() -> tuple[str, ...]:
    _validate_story_129_5_subset()
    return STORY_129_5_ROUTE_PATTERNS


def story_129_5_panel_contracts() -> tuple[PanelContract, ...]:
    _validate_story_129_5_subset()
    return tuple(
        PanelContract(
            panel_family=panel_family,
            title=STORY_129_5_PANEL_TITLES[panel_family],
            routes=tuple(
                _panel_read_route(
                    route_pattern,
                    route_input_identifiers=STORY_129_5_ROUTE_INPUT_IDENTIFIERS,
                    row_display_identifiers=STORY_129_5_ROW_DISPLAY_IDENTIFIERS,
                )
                for route_pattern in route_patterns
            ),
        )
        for panel_family, route_patterns in STORY_129_5_PANEL_ROUTES.items()
    )


def story_99_1_forbidden_renderable_route_patterns() -> frozenset[str]:
    return STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS


def story_99_1_panel_view_models(
    display_state: DisplayState = "healthy",
) -> tuple[PanelViewModel, ...]:
    return tuple(
        PanelViewModel(
            panel_family=panel.panel_family,
            title=panel.title,
            routes=tuple(
                story_99_1_route_view_model(route.route_pattern, display_state=display_state)
                for route in panel.routes
            ),
        )
        for panel in _story_99_1_panel_contracts()
    )


def story_99_1_route_view_models(
    display_state: DisplayState = "healthy",
) -> tuple[RouteViewModel, ...]:
    return tuple(
        route
        for panel in story_99_1_panel_view_models(display_state=display_state)
        for route in panel.routes
    )


def story_99_1_route_view_model(
    route_pattern: str,
    display_state: DisplayState = "healthy",
) -> RouteViewModel:
    if route_pattern in STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS:
        raise ValueError(route_pattern)

    panel_route = _story_99_1_panel_route_lookup().get(route_pattern)
    if panel_route is None:
        raise ValueError(route_pattern)

    panel_family, route = panel_route
    contract = read_contract(route.route_pattern)
    if contract.route_status != "approved":
        raise ValueError(route_pattern)
    if display_state not in contract.allowed_states:
        raise ValueError(display_state)

    return RouteViewModel(
        panel_family=panel_family,
        route_pattern=route.route_pattern,
        source_category=route.source_category,
        route_input_identifiers=route.route_input_identifiers,
        row_display_identifiers=route.row_display_identifiers,
        timestamp_policy=route.timestamp_policy,
        freshness_policy=route.freshness_policy,
        display_state=display_state,
        degraded_state_category=_degraded_state_category(display_state),
        authority_state=_authority_state(display_state),
        display_severity=_display_severity(display_state),
        display_copy=_display_copy(panel_family=panel_family, display_state=display_state),
    )


STORY_99_2_FIXTURE_PROVENANCE: FixtureProvenance = "static-fixture"
STORY_99_2_FIXTURE_TIMESTAMP_LABEL = (
    "static fixture readiness timestamp label; runtime data remains disconnected"
)
STORY_99_2_FIXTURE_FRESHNESS_LABEL = (
    "static fixture readiness freshness label; runtime data remains disconnected"
)
_MAPPING_PROXY_TYPE: type[object] = type(MappingProxyType({}))
_STORY_99_2_FORBIDDEN_TEXT_PARTS = (
    "<" + "script",
    "fe" + "tch",
    "x" + "hr",
    "web" + "socket",
    "event" + "source",
    "poll" + "ing",
    "http" + "://",
    "https" + "://",
    "post",
    "put",
    "patch",
    "de" + "lete",
    "form",
    "button",
    "input",
    "con" + "trol",
    "mut" + "ation",
    "destructive",
    "start",
    "stop",
    "re" + "try",
    "approve",
    "reject",
    "javascript:",
    "data:",
    "backend success",
    "current",
    "fetched",
    "live",
)


def story_99_2_fixture_snapshots(
    display_state: DisplayState = "healthy",
) -> tuple[PanelFixtureSnapshot, ...]:
    return tuple(
        PanelFixtureSnapshot(
            panel_family=panel.panel_family,
            title=panel.title,
            rows=tuple(
                story_99_2_route_fixture_row(route.route_pattern, display_state=display_state)
                for route in panel.routes
            ),
        )
        for panel in _story_99_1_panel_contracts()
    )


def story_99_2_route_fixture_row(
    route_pattern: str,
    display_state: DisplayState = "healthy",
    *,
    renderer_context_fields: Mapping[str, str] | None = None,
) -> RouteFixtureRow:
    if route_pattern in STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS:
        raise ValueError(route_pattern)

    panel_route = _story_99_1_panel_route_lookup().get(route_pattern)
    if panel_route is None:
        raise ValueError(route_pattern)

    panel_family, route = panel_route
    contract = read_contract(route.route_pattern)
    if contract.route_status != "approved":
        raise ValueError(route_pattern)
    if display_state not in contract.allowed_states:
        raise ValueError(display_state)

    row = RouteFixtureRow(
        panel_family=panel_family,
        source_route_pattern=route.route_pattern,
        source_category=route.source_category,
        route_input_identifiers=route.route_input_identifiers,
        row_display_identifiers=route.row_display_identifiers,
        source_identifiers=_story_99_2_source_identifiers(route),
        timestamp_policy=route.timestamp_policy,
        freshness_policy=route.freshness_policy,
        fixture_provenance=STORY_99_2_FIXTURE_PROVENANCE,
        fixture_timestamp_label=STORY_99_2_FIXTURE_TIMESTAMP_LABEL,
        fixture_freshness_label=STORY_99_2_FIXTURE_FRESHNESS_LABEL,
        display_state=display_state,
        degraded_state_category=_degraded_state_category(display_state),
        authority_state=_authority_state(display_state),
        display_severity=_display_severity(display_state),
        display_copy=_story_99_2_fixture_copy(
            panel_family=panel_family,
            display_state=display_state,
        ),
        renderer_context_fields=MappingProxyType(
            {} if renderer_context_fields is None else dict(renderer_context_fields)
        ),
    )
    return validate_story_99_2_fixture_row(row)


def story_99_2_route_fixture_probe(route_pattern: str) -> RouteFixtureProbe:
    try:
        row = story_99_2_route_fixture_row(route_pattern)
    except ValueError:
        return RouteFixtureProbe(
            source_route_pattern=route_pattern,
            renderable=False,
            display_state="needs-contract",
            authority_state="needs-contract",
            display_severity="blocked",
            display_copy=(
                "static fixture readiness needs contract fixture review; "
                "runtime data remains disconnected"
            ),
        )
    return RouteFixtureProbe(
        source_route_pattern=row.source_route_pattern,
        renderable=True,
        display_state=row.display_state,
        authority_state=row.authority_state,
        display_severity=row.display_severity,
        display_copy=row.display_copy,
    )


def story_99_2_forbidden_renderable_route_patterns() -> frozenset[str]:
    return STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS


def validate_story_99_2_fixture_row(row: RouteFixtureRow) -> RouteFixtureRow:
    if row.source_route_pattern in STORY_99_1_FORBIDDEN_RENDERABLE_ROUTE_PATTERNS:
        raise ValueError(row.source_route_pattern)
    panel_route = _story_99_1_panel_route_lookup().get(row.source_route_pattern)
    if panel_route is None:
        raise ValueError(row.source_route_pattern)

    panel_family, route = panel_route
    if row.panel_family != panel_family:
        raise ValueError(row.panel_family)
    if row.source_category != route.source_category:
        raise ValueError(row.source_category)
    if row.route_input_identifiers != route.route_input_identifiers:
        raise ValueError(row.route_input_identifiers)
    if row.row_display_identifiers != route.row_display_identifiers:
        raise ValueError(row.row_display_identifiers)
    if row.source_identifiers != _story_99_2_source_identifiers(route):
        raise ValueError(row.source_identifiers)
    if row.timestamp_policy != route.timestamp_policy:
        raise ValueError(row.timestamp_policy)
    if row.freshness_policy != route.freshness_policy:
        raise ValueError(row.freshness_policy)
    if row.fixture_provenance not in {"static-fixture", "snapshot-fixture", "contract-fixture"}:
        raise ValueError(row.fixture_provenance)
    if row.display_state not in route.allowed_states:
        raise ValueError(row.display_state)
    if row.degraded_state_category != _degraded_state_category(row.display_state):
        raise ValueError(row.degraded_state_category)
    if row.authority_state != _authority_state(row.display_state):
        raise ValueError(row.authority_state)
    if row.display_severity != _display_severity(row.display_state):
        raise ValueError(row.display_severity)
    if row.read_only_contract is not True:
        raise ValueError("read-only marker required")
    if not isinstance(row.renderer_context_fields, _MAPPING_PROXY_TYPE):
        raise ValueError("renderer context fields must be immutable")
    if row.renderer_context_fields:
        raise ValueError("renderer context fields are not allowed for fixtures")

    _validate_story_99_2_safe_text(row.display_copy)
    _validate_story_99_2_safe_text(row.fixture_timestamp_label)
    _validate_story_99_2_safe_text(row.fixture_freshness_label)
    for source_identifier in row.source_identifiers:
        _validate_story_99_2_safe_text(source_identifier.name)
        _validate_story_99_2_safe_text(source_identifier.fixture_value)

    lowered_copy = row.display_copy.lower()
    for required in ("static", "fixture", "readiness", "runtime data remains disconnected"):
        if required not in lowered_copy:
            raise ValueError(row.display_copy)
    if row.display_state == "healthy":
        if "contract fixture authority" not in lowered_copy:
            raise ValueError(row.display_copy)
    elif row.display_state.replace("-", " ") not in lowered_copy:
        raise ValueError(row.display_copy)
    return row


def story_99_2_fixture_rendered_metadata(row: RouteFixtureRow) -> Mapping[str, object]:
    validate_story_99_2_fixture_row(row)
    return MappingProxyType(
        {
            "panel_family": row.panel_family,
            "source_route_pattern": row.source_route_pattern,
            "source_category": row.source_category,
            "route_identifier_labels": row.route_input_identifiers,
            "row_identifier_labels": row.row_display_identifiers,
            "source_identifiers": tuple(
                (identifier.name, identifier.fixture_value) for identifier in row.source_identifiers
            ),
            "timestamp_policy": row.timestamp_policy,
            "freshness_policy": row.freshness_policy,
            "fixture_provenance": row.fixture_provenance,
            "fixture_timestamp_label": row.fixture_timestamp_label,
            "fixture_freshness_label": row.fixture_freshness_label,
            "display_state": row.display_state,
            "degraded_state_category": row.degraded_state_category,
            "authority_state": row.authority_state,
            "display_severity": row.display_severity,
            "display_copy": row.display_copy,
        }
    )


def _story_99_2_source_identifiers(route: PanelReadRoute) -> tuple[SourceIdentifier, ...]:
    if not route.route_input_identifiers:
        return (
            SourceIdentifier(
                name="source_category",
                fixture_value=f"fixture-{route.source_category}-source",
            ),
        )
    return tuple(
        SourceIdentifier(
            name=identifier,
            fixture_value=f"fixture-{identifier.replace('_', '-')}",
        )
        for identifier in route.route_input_identifiers
    )


def _story_99_2_fixture_copy(
    *,
    panel_family: PanelFamily,
    display_state: DisplayState,
) -> str:
    panel_label = panel_family.replace("-", " ")
    if display_state == "healthy":
        return (
            f"{panel_label} static fixture readiness has contract fixture authority; "
            "runtime data remains disconnected"
        )
    state_label = display_state.replace("-", " ")
    if display_state == "needs-contract":
        return (
            f"{panel_label} static fixture readiness needs contract fixture review; "
            "runtime data remains disconnected"
        )
    return (
        f"{panel_label} static fixture readiness is {state_label}; "
        "runtime data remains disconnected"
    )


def _validate_story_99_2_safe_text(text: str) -> None:
    lowered = text.lower()
    for part in _STORY_99_2_FORBIDDEN_TEXT_PARTS:
        if part in lowered:
            raise ValueError(text)
    if "/v1/" in lowered:
        raise ValueError(text)


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


def _story_99_1_panel_contracts() -> tuple[PanelContract, ...]:
    return (
        story_96_1_panel_contracts()
        + story_96_2_panel_contracts()
        + story_108_2_panel_contracts()
        + story_112_2_panel_contracts()
        + story_109_2_panel_contracts()
        + story_110_2_panel_contracts()
        + story_111_2_panel_contracts()
        + story_129_5_panel_contracts()
    )


def _story_99_1_panel_route_lookup() -> Mapping[str, tuple[PanelFamily, PanelReadRoute]]:
    route_lookup: dict[str, tuple[PanelFamily, PanelReadRoute]] = {}
    for panel in _story_99_1_panel_contracts():
        for route in panel.routes:
            if route.route_pattern in route_lookup:
                raise ValueError(f"duplicate Story 99.1 panel route: {route.route_pattern}")
            route_lookup[route.route_pattern] = (panel.panel_family, route)
    return MappingProxyType(route_lookup)


def _degraded_state_category(display_state: DisplayState) -> DegradedStateCategory:
    if display_state == "healthy":
        return "none"
    return display_state


def _authority_state(display_state: DisplayState) -> AuthorityState:
    if display_state == "healthy":
        return "authoritative"
    if display_state == "needs-contract":
        return "needs-contract"
    return "non-authoritative"


def _display_severity(display_state: DisplayState) -> DisplaySeverity:
    if display_state == "healthy":
        return "normal"
    if display_state in {"unavailable", "partial", "stale"}:
        return "warning"
    if display_state == "needs-contract":
        return "blocked"
    return "error"


def _display_copy(*, panel_family: PanelFamily, display_state: DisplayState) -> str:
    panel_label = panel_family.replace("-", " ")
    if display_state == "healthy":
        return (
            f"{panel_label} static readiness contract is prepared; "
            "runtime data remains disconnected."
        )
    if display_state == "needs-contract":
        return (
            f"{panel_label} needs a separate read contract before presentation; "
            "runtime data remains disconnected."
        )
    state_label = display_state.replace("-", " ")
    return (
        f"{panel_label} static readiness contract is {state_label}; "
        "runtime data remains disconnected."
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


def _validate_story_108_2_subset() -> None:
    selected = set(STORY_108_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_108_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 108.2 panel route mismatch")
    if set(STORY_108_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 108.2 route-input identifier mismatch")
    if set(STORY_108_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 108.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 108.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 108.2 route requires separate contract")


def _validate_story_112_2_subset() -> None:
    selected = set(STORY_112_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_112_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 112.2 panel route mismatch")
    if set(STORY_112_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 112.2 route-input identifier mismatch")
    if set(STORY_112_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 112.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 112.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 112.2 route requires separate contract")


def _validate_story_109_2_subset() -> None:
    selected = set(STORY_109_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_109_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 109.2 panel route mismatch")
    if set(STORY_109_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 109.2 route-input identifier mismatch")
    if set(STORY_109_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 109.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 109.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 109.2 route requires separate contract")


def _validate_story_110_2_subset() -> None:
    selected = set(STORY_110_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_110_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 110.2 panel route mismatch")
    if set(STORY_110_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 110.2 route-input identifier mismatch")
    if set(STORY_110_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 110.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 110.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 110.2 route requires separate contract")


def _validate_story_111_2_subset() -> None:
    selected = set(STORY_111_2_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_111_2_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 111.2 panel route mismatch")
    if set(STORY_111_2_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 111.2 route-input identifier mismatch")
    if set(STORY_111_2_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 111.2 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 111.2 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 111.2 route requires separate contract")


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


def _validate_story_129_5_subset() -> None:
    selected = set(STORY_129_5_ROUTE_PATTERNS)
    panel_selected = {
        route_pattern
        for route_patterns in STORY_129_5_PANEL_ROUTES.values()
        for route_pattern in route_patterns
    }
    if selected != panel_selected:
        raise ValueError("story 129.5 panel route mismatch")
    if set(STORY_129_5_ROUTE_INPUT_IDENTIFIERS) != selected:
        raise ValueError("story 129.5 route-input identifier mismatch")
    if set(STORY_129_5_ROW_DISPLAY_IDENTIFIERS) != selected:
        raise ValueError("story 129.5 row-display identifier mismatch")
    approved = {contract.route_pattern for contract in APPROVED_READ_CONTRACTS}
    if not selected <= approved:
        raise ValueError("story 129.5 route is not approved")
    blocked = selected & EXCLUDED_ROUTE_PATTERNS
    blocked |= selected & {contract.route_pattern for contract in UNAVAILABLE_READ_CONTRACTS}
    if blocked:
        raise ValueError("story 129.5 route requires separate contract")
