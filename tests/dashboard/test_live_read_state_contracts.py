from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from tests.dashboard import test_live_read_contracts as live_contracts

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
RouteContract = Literal["approved", "needs-separate-contract"]
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

APPROVED_ROUTE_PATTERNS = frozenset(
    route for method, route in live_contracts.APPROVED_READ_ROUTES if method == "GET"
)
NEEDS_SEPARATE_CONTRACT_ROUTES = live_contracts.NEEDS_SEPARATE_CONTRACT_GET_ROUTES
EXPECTED_IDENTIFIERS_BY_ROUTE = {
    "/v1/tasks/{task_id}": frozenset({"task_id"}),
    "/v1/tasks/{task_id}/events": frozenset({"task_id", "event_id"}),
    "/v1/tasks/{task_id}/transitions": frozenset({"task_id", "event_id"}),
    "/v1/trace/{trace_id}": frozenset({"trace_id"}),
    "/v1/tasks/{task_id}/history": frozenset({"task_id", "event_id"}),
    "/v1/events/replay": frozenset({"replay_id"}),
    "/v1/events/replay/validate": frozenset({"replay_id"}),
    "/v1/events/replay/snapshots": frozenset({"snapshot_id", "lifecycle_manifest_id"}),
    "/v1/health": frozenset(),
}
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
REPLAY_OR_LIFECYCLE_CATEGORIES = frozenset({"replay", "lifecycle"})
UNCERTAINTY_COPY_TERMS = (
    "unavailable",
    "needs contract",
    "not approved",
    "partial",
    "stale",
    "invalid",
    "unauthorized",
    "backend unavailable",
)
SUCCESS_COPY_TERMS = ("healthy", "authoritative", "success", "ok")


@dataclass(frozen=True)
class LiveValueContract:
    name: str
    source_category: SourceCategory
    route_pattern: str | None
    route_contract: RouteContract
    timestamp_policy: TimestampPolicy
    freshness_policy: FreshnessPolicy
    required_identifiers: tuple[Identifier, ...]
    allowed_states: frozenset[DisplayState]
    unavailable_copy: str | None = None


@dataclass(frozen=True)
class DisplayStateFixture:
    name: str
    source_category: SourceCategory
    state: DisplayState
    copy: str
    authoritative: bool = False
    route_pattern: str | None = None


LIVE_VALUE_CONTRACTS = (
    LiveValueContract(
        name="task-detail",
        source_category="task",
        route_pattern="/v1/tasks/{task_id}",
        route_contract="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id",),
        allowed_states=frozenset(
            {"healthy", "unavailable", "stale", "unauthorized", "backend-unavailable"}
        ),
    ),
    LiveValueContract(
        name="task-events",
        source_category="event",
        route_pattern="/v1/tasks/{task_id}/events",
        route_contract="approved",
        timestamp_policy="emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="task-transitions",
        source_category="event",
        route_pattern="/v1/tasks/{task_id}/transitions",
        route_contract="approved",
        timestamp_policy="emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="trace-correlation",
        source_category="trace",
        route_pattern="/v1/trace/{trace_id}",
        route_contract="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("trace_id",),
        allowed_states=frozenset(
            {"healthy", "unavailable", "partial", "stale", "backend-unavailable"}
        ),
    ),
    LiveValueContract(
        name="task-history",
        source_category="history",
        route_pattern="/v1/tasks/{task_id}/history",
        route_contract="approved",
        timestamp_policy="retrieved-or-emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("task_id", "event_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="replay-state",
        source_category="replay",
        route_pattern="/v1/events/replay",
        route_contract="approved",
        timestamp_policy="retrieved-or-emitted-at-required",
        freshness_policy="fresh-or-stale-required",
        required_identifiers=("replay_id",),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="replay-validation",
        source_category="replay",
        route_pattern="/v1/events/replay/validate",
        route_contract="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=("replay_id",),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="snapshot-listing",
        source_category="lifecycle",
        route_pattern="/v1/events/replay/snapshots",
        route_contract="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=("snapshot_id", "lifecycle_manifest_id"),
        allowed_states=frozenset({"healthy", "partial", "stale", "invalid", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="health",
        source_category="health",
        route_pattern="/v1/health",
        route_contract="approved",
        timestamp_policy="retrieved-at-required",
        freshness_policy="healthy-or-stale-required",
        required_identifiers=(),
        allowed_states=frozenset({"healthy", "stale", "unavailable", "backend-unavailable"}),
    ),
    LiveValueContract(
        name="aggregate-overview",
        source_category="aggregate",
        route_pattern="/v1/tasks",
        route_contract="needs-separate-contract",
        timestamp_policy="not-available-until-contract",
        freshness_policy="not-authoritative-until-contract",
        required_identifiers=(),
        allowed_states=frozenset({"unavailable", "needs-contract"}),
        unavailable_copy="Aggregate overview unavailable: needs contract before live data.",
    ),
    LiveValueContract(
        name="session-list",
        source_category="session",
        route_pattern="/v1/sessions",
        route_contract="needs-separate-contract",
        timestamp_policy="not-available-until-contract",
        freshness_policy="not-authoritative-until-contract",
        required_identifiers=("session_id",),
        allowed_states=frozenset({"unavailable", "needs-contract"}),
        unavailable_copy="Session list unavailable: no approved read contract.",
    ),
)

DEGRADED_STATE_FIXTURES = (
    DisplayStateFixture(
        name="aggregate-missing-contract",
        source_category="aggregate",
        route_pattern="/v1/tasks",
        state="needs-contract",
        copy="Unavailable: needs contract before aggregate display.",
    ),
    DisplayStateFixture(
        name="session-missing-contract",
        source_category="session",
        route_pattern="/v1/sessions",
        state="unavailable",
        copy="Unavailable: session list read is not approved.",
    ),
    DisplayStateFixture(
        name="replay-partial",
        source_category="replay",
        route_pattern="/v1/events/replay",
        state="partial",
        copy="Partial replay data; display degraded state only.",
    ),
    DisplayStateFixture(
        name="replay-invalid",
        source_category="replay",
        route_pattern="/v1/events/replay/validate",
        state="invalid",
        copy="Invalid replay validation result; display degraded state only.",
    ),
    DisplayStateFixture(
        name="lifecycle-stale",
        source_category="lifecycle",
        route_pattern="/v1/events/replay/snapshots",
        state="stale",
        copy="Stale lifecycle snapshot listing; freshness expired.",
    ),
    DisplayStateFixture(
        name="health-backend-unavailable",
        source_category="health",
        route_pattern="/v1/health",
        state="backend-unavailable",
        copy="Backend unavailable; live dashboard state is uncertain.",
    ),
    DisplayStateFixture(
        name="task-unauthorized",
        source_category="task",
        route_pattern="/v1/tasks/{task_id}",
        state="unauthorized",
        copy="Unauthorized read; dashboard cannot display live data.",
    ),
)


def test_every_future_live_value_declares_source_freshness_and_identifier_contracts() -> None:
    names = {contract.name for contract in LIVE_VALUE_CONTRACTS}
    assert len(names) == len(LIVE_VALUE_CONTRACTS)

    for contract in LIVE_VALUE_CONTRACTS:
        assert_valid_live_value_contract(contract)

    approved_contract_routes = {
        contract.route_pattern
        for contract in LIVE_VALUE_CONTRACTS
        if contract.route_contract == "approved"
    }
    assert approved_contract_routes == APPROVED_ROUTE_PATTERNS
    assert approved_contract_routes == set(EXPECTED_IDENTIFIERS_BY_ROUTE)

    covered_categories = {contract.source_category for contract in LIVE_VALUE_CONTRACTS}
    assert {
        "task",
        "event",
        "trace",
        "history",
        "replay",
        "lifecycle",
        "health",
        "aggregate",
        "session",
    } <= covered_categories


def test_unapproved_aggregate_and_session_reads_render_needs_contract_copy() -> None:
    unapproved = [
        contract
        for contract in LIVE_VALUE_CONTRACTS
        if contract.route_contract == "needs-separate-contract"
    ]
    assert {contract.source_category for contract in unapproved} == {"aggregate", "session"}

    for contract in unapproved:
        assert contract.route_pattern in NEEDS_SEPARATE_CONTRACT_ROUTES
        assert contract.route_pattern not in APPROVED_ROUTE_PATTERNS
        assert contract.allowed_states <= {"unavailable", "needs-contract"}
        assert contract.unavailable_copy is not None
        assert_copy_is_bounded_uncertainty(contract.unavailable_copy)


def test_non_authoritative_states_never_render_healthy_or_authoritative() -> None:
    for fixture in DEGRADED_STATE_FIXTURES:
        assert fixture.state in NON_AUTHORITATIVE_STATES
        assert_render_state_is_fail_closed(fixture)


def test_invalid_or_partial_replay_and_lifecycle_states_cannot_be_healthy() -> None:
    risky_fixtures = [
        fixture
        for fixture in DEGRADED_STATE_FIXTURES
        if fixture.source_category in REPLAY_OR_LIFECYCLE_CATEGORIES
    ]
    assert risky_fixtures

    for fixture in risky_fixtures:
        assert fixture.state in {"partial", "stale", "invalid", "backend-unavailable"}
        assert_render_state_is_fail_closed(fixture)
        healthy_probe = replace(
            fixture,
            state="healthy",
            copy=f"Healthy authoritative {fixture.name}",
            authoritative=True,
        )
        assert_display_state_contract_fails(healthy_probe)


def test_guard_sensitivity_rejects_missing_provenance_freshness_or_identifiers() -> None:
    valid = LIVE_VALUE_CONTRACTS[0]
    invalid_contracts = (
        replace(valid, source_category="task", route_pattern=None),
        replace(valid, timestamp_policy="not-available-until-contract"),
        replace(valid, freshness_policy="not-authoritative-until-contract"),
        replace(valid, required_identifiers=()),
        replace(valid, route_pattern="/v1/tasks", route_contract="approved"),
    )

    for contract in invalid_contracts:
        assert_live_value_contract_fails(contract)


def test_guard_sensitivity_rejects_synthetic_authoritative_success_for_missing_contracts() -> None:
    bad_fixtures = (
        DisplayStateFixture(
            name="bad-aggregate-success",
            source_category="aggregate",
            route_pattern="/v1/tasks",
            state="healthy",
            copy="Healthy authoritative aggregate success.",
            authoritative=True,
        ),
        DisplayStateFixture(
            name="bad-session-success",
            source_category="session",
            route_pattern="/v1/sessions",
            state="healthy",
            copy="Session list OK.",
            authoritative=True,
        ),
        DisplayStateFixture(
            name="bad-stale-copy",
            source_category="health",
            route_pattern="/v1/health",
            state="stale",
            copy="OK",
        ),
    )

    for fixture in bad_fixtures:
        assert_display_state_contract_fails(fixture)


def assert_valid_live_value_contract(contract: LiveValueContract) -> None:
    assert contract.name
    assert contract.source_category
    assert contract.timestamp_policy
    assert contract.freshness_policy
    assert contract.allowed_states
    assert contract.allowed_states <= ({"healthy"} | NON_AUTHORITATIVE_STATES)

    if contract.route_contract == "approved":
        assert contract.route_pattern in APPROVED_ROUTE_PATTERNS, contract
        assert contract.route_pattern not in NEEDS_SEPARATE_CONTRACT_ROUTES, contract
        assert contract.timestamp_policy != "not-available-until-contract", contract
        assert contract.freshness_policy != "not-authoritative-until-contract", contract
        assert contract.route_pattern is not None
        assert (
            frozenset(contract.required_identifiers)
            == EXPECTED_IDENTIFIERS_BY_ROUTE[contract.route_pattern]
        ), contract
    else:
        assert contract.route_pattern in NEEDS_SEPARATE_CONTRACT_ROUTES, contract
        assert contract.timestamp_policy == "not-available-until-contract", contract
        assert contract.freshness_policy == "not-authoritative-until-contract", contract
        assert contract.allowed_states <= {"unavailable", "needs-contract"}, contract
        assert contract.unavailable_copy is not None, contract


def assert_render_state_is_fail_closed(fixture: DisplayStateFixture) -> None:
    assert fixture.state in NON_AUTHORITATIVE_STATES, fixture
    assert not fixture.authoritative, fixture
    assert_copy_is_bounded_uncertainty(fixture.copy)
    lowered = fixture.copy.lower()
    if fixture.source_category in {"aggregate", "session"}:
        assert fixture.route_pattern in NEEDS_SEPARATE_CONTRACT_ROUTES, fixture
        assert fixture.state in {"unavailable", "needs-contract"}, fixture
    for success_term in SUCCESS_COPY_TERMS:
        assert success_term not in lowered, fixture


def assert_copy_is_bounded_uncertainty(copy: str) -> None:
    lowered = copy.lower()
    assert any(term in lowered for term in UNCERTAINTY_COPY_TERMS), copy


def assert_live_value_contract_fails(contract: LiveValueContract) -> None:
    try:
        assert_valid_live_value_contract(contract)
    except AssertionError:
        return
    raise AssertionError(f"live value contract probe unexpectedly passed: {contract}")


def assert_display_state_contract_fails(fixture: DisplayStateFixture) -> None:
    try:
        assert_render_state_is_fail_closed(fixture)
    except AssertionError:
        return
    raise AssertionError(f"display state probe unexpectedly passed: {fixture}")
