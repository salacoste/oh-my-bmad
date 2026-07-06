"""Non-mutating object-storage retention dry-run planner (Story 130.2).

The module validates local policy and object-manifest JSON files and returns a
metadata-only dry-run plan. It never loads credentials, calls object storage,
starts a scheduler, mutates archive manifests, or deletes/transitions objects.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Final, Literal

SCHEMA_VERSION: Final = 1
DEFAULT_POLICY_VERSION: Final = "story130.retention.dry_run.v1"
_TIMESTAMP_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DOMAIN_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_GLOB_CHARS: Final = frozenset("*?[]{}")
_ALLOWED_ACTIONS: Final = frozenset({"retain", "transition", "delete"})

PlannedAction = Literal["retain", "transition", "delete", "blocked"]
DecisionStatus = Literal["planned", "blocked"]


class RetentionPlanError(ValueError):
    """Retention dry-run input is invalid or ambiguous."""


@dataclass(frozen=True)
class RetentionDomainPolicy:
    """Per-domain retention policy used by Story 130.2 dry-runs."""

    domain: str
    retention_window_days: int
    allowed_actions: tuple[str, ...]
    transition_after_days: int | None
    delete_after_days: int | None
    protected_holds: tuple[str, ...]
    excluded_keys: tuple[str, ...]


@dataclass(frozen=True)
class RetentionPolicy:
    """Validated retention policy metadata."""

    policy_id: str
    policy_version: str
    owner: str
    authority_source: str
    generated_at: str
    evidence_max_age_days: int
    default_action: Literal["retain"]
    domains: Mapping[str, RetentionDomainPolicy]


@dataclass(frozen=True)
class RetentionObjectIdentity:
    """Manifest-backed object identity used by retention decisions."""

    domain: str
    manifest_ref: str
    object_key: str
    version_or_generation: str
    etag_or_checksum: str
    size_bytes: int
    created_at_utc: str
    last_modified_at_utc: str
    storage_class: str
    hold_refs: tuple[str, ...]


@dataclass(frozen=True)
class RetentionBlocker:
    """Fail-closed blocker for one object."""

    code: str
    message: str
    object_identity: RetentionObjectIdentity


@dataclass(frozen=True)
class RetentionDecision:
    """Metadata-only planned decision for one object."""

    status: DecisionStatus
    planned_action: PlannedAction
    reason: str
    policy_domain: str
    age_basis: Literal["last_modified_at_utc"]
    age_days: int
    object_identity: RetentionObjectIdentity
    blocker: RetentionBlocker | None


@dataclass(frozen=True)
class RetentionDryRunPlan:
    """Immutable content-addressed retention dry-run plan."""

    schema_version: int
    safety_policy_version: str
    generated_at: str
    policy: RetentionPolicy
    manifest_id: str
    manifest_generated_at: str
    decisions: tuple[RetentionDecision, ...]
    blockers: tuple[RetentionBlocker, ...]
    plan_hash: str

    def canonical_payload(self) -> dict[str, object]:
        """Return deterministic payload covered by :attr:`plan_hash`."""
        return {
            "schema_version": self.schema_version,
            "safety_policy_version": self.safety_policy_version,
            "policy": _policy_payload(self.policy),
            "manifest_id": self.manifest_id,
            "manifest_generated_at": self.manifest_generated_at,
            "decisions": [_decision_payload(decision) for decision in self.decisions],
            "blockers": [_blocker_payload(blocker) for blocker in self.blockers],
        }

    def canonical_json(self) -> str:
        """Return compact sorted-key JSON for the canonical payload."""
        return _canonical_json(self.canonical_payload())

    @property
    def artifact_filename(self) -> str:
        """Recommended operator artifact filename for this dry-run plan."""
        return f"retention-dry-run-plan-{self.plan_hash}.json"


def create_retention_dry_run_plan(
    *,
    policy_path: Path,
    object_manifest_path: Path,
    now: datetime | None = None,
    safety_policy_version: str = DEFAULT_POLICY_VERSION,
) -> RetentionDryRunPlan:
    """Create a non-mutating object retention dry-run plan from local JSON files."""
    effective_now = _normalize_now(now)
    policy = _load_policy(policy_path, effective_now)
    manifest = _load_object_manifest(object_manifest_path)
    manifest_id = _required_str(manifest, "manifest_id", location="object manifest")
    manifest_generated_at = _required_timestamp(
        manifest,
        "generated_at",
        now=effective_now,
        location="object manifest",
    )
    _validate_freshness(
        manifest_generated_at,
        now=effective_now,
        max_age_days=policy.evidence_max_age_days,
        location="object manifest.generated_at",
    )
    objects = manifest.get("objects")
    if not isinstance(objects, list):
        raise RetentionPlanError("object manifest objects must be a list")

    identities = _validate_object_identities(
        objects,
        manifest_id=manifest_id,
        policy=policy,
        now=effective_now,
    )
    decisions: list[RetentionDecision] = []
    blockers: list[RetentionBlocker] = []
    for identity in identities:
        decision = _decide_object(identity, policy=policy, now=effective_now)
        decisions.append(decision)
        if decision.blocker is not None:
            blockers.append(decision.blocker)

    generated_at = _format_utc(effective_now)
    payload_without_hash = {
        "schema_version": SCHEMA_VERSION,
        "safety_policy_version": safety_policy_version,
        "policy": _policy_payload(policy),
        "manifest_id": manifest_id,
        "manifest_generated_at": manifest_generated_at,
        "decisions": [_decision_payload(decision) for decision in decisions],
        "blockers": [_blocker_payload(blocker) for blocker in blockers],
    }
    plan_hash = hashlib.sha256(_canonical_json(payload_without_hash).encode("utf-8")).hexdigest()
    return RetentionDryRunPlan(
        schema_version=SCHEMA_VERSION,
        safety_policy_version=safety_policy_version,
        generated_at=generated_at,
        policy=policy,
        manifest_id=manifest_id,
        manifest_generated_at=manifest_generated_at,
        decisions=tuple(decisions),
        blockers=tuple(blockers),
        plan_hash=plan_hash,
    )


def _load_policy(path: Path, now: datetime) -> RetentionPolicy:
    raw = _load_json(path, location="retention policy")
    _require_schema(raw, location="retention policy")
    policy_id = _required_str(raw, "policy_id", location="retention policy")
    policy_version = _required_str(raw, "policy_version", location="retention policy")
    owner = _required_str(raw, "owner", location="retention policy")
    authority_source = _required_str(raw, "authority_source", location="retention policy")
    generated_at = _required_timestamp(raw, "generated_at", now=now, location="retention policy")
    evidence_max_age_days = _required_int(
        raw,
        "evidence_max_age_days",
        min_value=0,
        location="retention policy",
    )
    _validate_freshness(
        generated_at,
        now=now,
        max_age_days=evidence_max_age_days,
        location="retention policy.generated_at",
    )
    default_action = _required_str(raw, "default_action", location="retention policy")
    if default_action != "retain":
        raise RetentionPlanError("retention policy default_action must be retain in Story 130.2")
    domains_raw = raw.get("domains")
    if not isinstance(domains_raw, dict) or not domains_raw:
        raise RetentionPlanError("retention policy domains must be a non-empty object")
    domains: dict[str, RetentionDomainPolicy] = {}
    for domain, value in sorted(domains_raw.items()):
        if not isinstance(domain, str) or not _DOMAIN_RE.fullmatch(domain):
            raise RetentionPlanError(f"retention policy domain is invalid: {domain!r}")
        if not isinstance(value, dict):
            raise RetentionPlanError(f"retention policy domain {domain!r} must be an object")
        domains[domain] = _load_domain_policy(domain, value)
    return RetentionPolicy(
        policy_id=policy_id,
        policy_version=policy_version,
        owner=owner,
        authority_source=authority_source,
        generated_at=generated_at,
        evidence_max_age_days=evidence_max_age_days,
        default_action="retain",
        domains=domains,
    )


def _load_domain_policy(domain: str, raw: Mapping[str, object]) -> RetentionDomainPolicy:
    retention_window_days = _required_int(
        raw,
        "retention_window_days",
        min_value=0,
        location=f"retention policy domain {domain}",
    )
    allowed_actions_raw = raw.get("allowed_actions")
    if not isinstance(allowed_actions_raw, list) or not allowed_actions_raw:
        raise RetentionPlanError(
            f"retention policy domain {domain} allowed_actions must be a non-empty list"
        )
    allowed_actions = tuple(
        _required_action(action, domain=domain) for action in allowed_actions_raw
    )
    if len(set(allowed_actions)) != len(allowed_actions):
        raise RetentionPlanError(f"retention policy domain {domain} allowed_actions has duplicates")
    if "retain" not in allowed_actions:
        raise RetentionPlanError(
            f"retention policy domain {domain} allowed_actions must include retain"
        )

    transition_after_days = _optional_int(
        raw,
        "transition_after_days",
        min_value=0,
        location=f"retention policy domain {domain}",
    )
    delete_after_days = _optional_int(
        raw,
        "delete_after_days",
        min_value=0,
        location=f"retention policy domain {domain}",
    )
    if transition_after_days is not None and transition_after_days < retention_window_days:
        raise RetentionPlanError(
            f"domain {domain} transition_after_days must be >= retention_window_days"
        )
    if delete_after_days is not None and delete_after_days < retention_window_days:
        raise RetentionPlanError(
            f"retention policy domain {domain} delete_after_days must be >= retention_window_days"
        )
    if (
        transition_after_days is not None
        and delete_after_days is not None
        and delete_after_days < transition_after_days
    ):
        raise RetentionPlanError(
            f"retention policy domain {domain} delete_after_days must be >= transition_after_days"
        )
    if transition_after_days is not None and "transition" not in allowed_actions:
        raise RetentionPlanError(
            f"retention policy domain {domain} transition_after_days requires transition action"
        )
    if delete_after_days is not None and "delete" not in allowed_actions:
        raise RetentionPlanError(
            f"retention policy domain {domain} delete_after_days requires delete action"
        )

    protected_holds = _string_tuple(
        raw.get("protected_holds", []),
        location=f"retention policy domain {domain} protected_holds",
        validate_key=False,
    )
    excluded_keys = _string_tuple(
        raw.get("excluded_keys", []),
        location=f"retention policy domain {domain} excluded_keys",
        validate_key=True,
    )
    return RetentionDomainPolicy(
        domain=domain,
        retention_window_days=retention_window_days,
        allowed_actions=allowed_actions,
        transition_after_days=transition_after_days,
        delete_after_days=delete_after_days,
        protected_holds=protected_holds,
        excluded_keys=excluded_keys,
    )


def _load_object_manifest(path: Path) -> dict[str, object]:
    raw = _load_json(path, location="object manifest")
    _require_schema(raw, location="object manifest")
    return raw


def _validate_object_identities(
    objects: Sequence[object],
    *,
    manifest_id: str,
    policy: RetentionPolicy,
    now: datetime,
) -> tuple[RetentionObjectIdentity, ...]:
    identities: list[RetentionObjectIdentity] = []
    seen_keys: set[tuple[str, str]] = set()
    for idx, item in enumerate(objects):
        location = f"object manifest objects[{idx}]"
        if not isinstance(item, dict):
            raise RetentionPlanError(f"{location} must be an object")
        identity = _load_object_identity(
            item, manifest_id=manifest_id, policy=policy, now=now, location=location
        )
        key = (identity.domain, identity.object_key)
        if key in seen_keys:
            raise RetentionPlanError(
                f"repeats object key for domain {identity.domain!r}: {identity.object_key!r}"
            )
        seen_keys.add(key)
        identities.append(identity)
    return tuple(identities)


def _load_object_identity(
    raw: Mapping[str, object],
    *,
    manifest_id: str,
    policy: RetentionPolicy,
    now: datetime,
    location: str,
) -> RetentionObjectIdentity:
    domain = _required_str(raw, "domain", location=location)
    if domain not in policy.domains:
        raise RetentionPlanError(
            f"{location} domain is not declared by retention policy: {domain!r}"
        )
    manifest_ref = _required_str(raw, "manifest_ref", location=location)
    if manifest_ref != manifest_id:
        raise RetentionPlanError(f"{location} manifest_ref must match manifest_id")
    object_key = _required_str(raw, "object_key", location=location)
    _validate_object_key(object_key, location=f"{location}.object_key")
    version_or_generation = _required_str(raw, "version_or_generation", location=location)
    etag_or_checksum = _required_str(raw, "etag_or_checksum", location=location)
    size_bytes = _required_int(raw, "size_bytes", min_value=0, location=location)
    created_at_utc = _required_timestamp(raw, "created_at_utc", now=now, location=location)
    last_modified_at_utc = _required_timestamp(
        raw, "last_modified_at_utc", now=now, location=location
    )
    if _parse_timestamp(created_at_utc, location=f"{location}.created_at_utc") > _parse_timestamp(
        last_modified_at_utc,
        location=f"{location}.last_modified_at_utc",
    ):
        raise RetentionPlanError(f"{location} created_at_utc must be <= last_modified_at_utc")
    storage_class = _required_str(raw, "storage_class", location=location)
    hold_refs = _string_tuple(
        raw.get("hold_refs"), location=f"{location}.hold_refs", validate_key=False
    )
    return RetentionObjectIdentity(
        domain=domain,
        manifest_ref=manifest_ref,
        object_key=object_key,
        version_or_generation=version_or_generation,
        etag_or_checksum=etag_or_checksum,
        size_bytes=size_bytes,
        created_at_utc=created_at_utc,
        last_modified_at_utc=last_modified_at_utc,
        storage_class=storage_class,
        hold_refs=hold_refs,
    )


def _decide_object(
    identity: RetentionObjectIdentity,
    *,
    policy: RetentionPolicy,
    now: datetime,
) -> RetentionDecision:
    domain_policy = policy.domains[identity.domain]
    age_days = (
        now - _parse_timestamp(identity.last_modified_at_utc, location="last_modified_at_utc")
    ).days
    blocker = _object_blocker(identity, domain_policy)
    if blocker is not None:
        return RetentionDecision(
            status="blocked",
            planned_action="blocked",
            reason=blocker.code,
            policy_domain=identity.domain,
            age_basis="last_modified_at_utc",
            age_days=age_days,
            object_identity=identity,
            blocker=blocker,
        )
    planned_action: PlannedAction = "retain"
    reason = "within_retention_window"
    if (
        domain_policy.delete_after_days is not None
        and age_days >= domain_policy.delete_after_days
        and "delete" in domain_policy.allowed_actions
    ):
        planned_action = "delete"
        reason = "delete_threshold_reached"
    elif (
        domain_policy.transition_after_days is not None
        and age_days >= domain_policy.transition_after_days
        and "transition" in domain_policy.allowed_actions
    ):
        planned_action = "transition"
        reason = "transition_threshold_reached"
    elif age_days >= domain_policy.retention_window_days:
        reason = "retention_window_elapsed_no_destructive_action_allowed"
    return RetentionDecision(
        status="planned",
        planned_action=planned_action,
        reason=reason,
        policy_domain=identity.domain,
        age_basis="last_modified_at_utc",
        age_days=age_days,
        object_identity=identity,
        blocker=None,
    )


def _object_blocker(
    identity: RetentionObjectIdentity,
    domain_policy: RetentionDomainPolicy,
) -> RetentionBlocker | None:
    matched_holds = sorted(set(identity.hold_refs) & set(domain_policy.protected_holds))
    if matched_holds:
        return RetentionBlocker(
            code="protected_hold_active",
            message=f"object has protected hold(s): {', '.join(matched_holds)}",
            object_identity=identity,
        )
    if identity.object_key in domain_policy.excluded_keys:
        return RetentionBlocker(
            code="operator_exclusion_active",
            message="object key is explicitly excluded by retention policy",
            object_identity=identity,
        )
    return None


def _load_json(path: Path, *, location: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RetentionPlanError(f"{location} file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RetentionPlanError(f"{location} JSON is invalid: {path}") from exc
    except OSError as exc:
        raise RetentionPlanError(f"{location} is not readable: {path}") from exc
    if not isinstance(raw, dict):
        raise RetentionPlanError(f"{location} must be a JSON object")
    return raw


def _require_schema(raw: Mapping[str, object], *, location: str) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise RetentionPlanError(f"{location} schema_version must be {SCHEMA_VERSION}")


def _required_str(raw: Mapping[str, object], field: str, *, location: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or value == "":
        raise RetentionPlanError(f"{location} {field} must be a non-empty string")
    return value


def _required_int(raw: Mapping[str, object], field: str, *, min_value: int, location: str) -> int:
    value = raw.get(field)
    if not isinstance(value, int):
        raise RetentionPlanError(f"{location} {field} must be an integer")
    if value < min_value:
        raise RetentionPlanError(f"{location} {field} must be >= {min_value}")
    return value


def _optional_int(
    raw: Mapping[str, object], field: str, *, min_value: int, location: str
) -> int | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, int):
        raise RetentionPlanError(f"{location} {field} must be an integer")
    if value < min_value:
        raise RetentionPlanError(f"{location} {field} must be >= {min_value}")
    return value


def _required_action(value: object, *, domain: str) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_ACTIONS:
        raise RetentionPlanError(
            f"domain {domain} allowed_actions must contain only retain, transition, delete"
        )
    return value


def _required_timestamp(
    raw: Mapping[str, object], field: str, *, now: datetime, location: str
) -> str:
    value = _required_str(raw, field, location=location)
    parsed = _parse_timestamp(value, location=f"{location}.{field}")
    if parsed > now:
        raise RetentionPlanError(f"{location} {field} must not be in the future")
    return value


def _parse_timestamp(value: str, *, location: str) -> datetime:
    if not _TIMESTAMP_RE.fullmatch(value):
        raise RetentionPlanError(f"{location} must be strict UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RetentionPlanError(f"{location} must be a valid UTC timestamp") from exc


def _validate_freshness(value: str, *, now: datetime, max_age_days: int, location: str) -> None:
    parsed = _parse_timestamp(value, location=location)
    if now - parsed > timedelta(days=max_age_days):
        raise RetentionPlanError(f"{location} is older than evidence_max_age_days")


def _normalize_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC).replace(microsecond=0)
    if now.tzinfo is None:
        raise RetentionPlanError("now must be timezone-aware UTC")
    return now.astimezone(UTC).replace(microsecond=0)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _string_tuple(value: object, *, location: str, validate_key: bool) -> tuple[str, ...]:
    if value is None:
        raise RetentionPlanError(f"{location} must be a list")
    if not isinstance(value, list):
        raise RetentionPlanError(f"{location} must be a list")
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            raise RetentionPlanError(f"{location}[{idx}] must be a non-empty string")
        if validate_key:
            _validate_object_key(item, location=f"{location}[{idx}]")
        out.append(item)
    if len(set(out)) != len(out):
        raise RetentionPlanError(f"{location} must not contain duplicates")
    return tuple(out)


def _validate_object_key(value: str, *, location: str) -> None:
    if value == "":
        raise RetentionPlanError(f"{location} must be non-empty")
    if value.endswith("/"):
        raise RetentionPlanError(f"{location} must be an exact object key, not a prefix")
    if "\\" in value:
        raise RetentionPlanError(f"{location} must be a POSIX object key")
    if any(char in value for char in _GLOB_CHARS):
        raise RetentionPlanError(f"{location} must not contain wildcard or glob characters")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise RetentionPlanError(f"{location} must be relative and must not contain '..'")


def _identity_payload(identity: RetentionObjectIdentity) -> dict[str, object]:
    return {
        "domain": identity.domain,
        "manifest_ref": identity.manifest_ref,
        "object_key": identity.object_key,
        "version_or_generation": identity.version_or_generation,
        "etag_or_checksum": identity.etag_or_checksum,
        "size_bytes": identity.size_bytes,
        "created_at_utc": identity.created_at_utc,
        "last_modified_at_utc": identity.last_modified_at_utc,
        "storage_class": identity.storage_class,
        "hold_refs": list(identity.hold_refs),
    }


def _domain_policy_payload(domain: RetentionDomainPolicy) -> dict[str, object]:
    return {
        "retention_window_days": domain.retention_window_days,
        "allowed_actions": list(domain.allowed_actions),
        "transition_after_days": domain.transition_after_days,
        "delete_after_days": domain.delete_after_days,
        "protected_holds": list(domain.protected_holds),
        "excluded_keys": list(domain.excluded_keys),
    }


def _policy_payload(policy: RetentionPolicy) -> dict[str, object]:
    return {
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "owner": policy.owner,
        "authority_source": policy.authority_source,
        "generated_at": policy.generated_at,
        "evidence_max_age_days": policy.evidence_max_age_days,
        "default_action": policy.default_action,
        "domains": {
            domain: _domain_policy_payload(domain_policy)
            for domain, domain_policy in sorted(policy.domains.items())
        },
    }


def _blocker_payload(blocker: RetentionBlocker) -> dict[str, object]:
    return {
        "code": blocker.code,
        "message": blocker.message,
        "object_identity": _identity_payload(blocker.object_identity),
    }


def _decision_payload(decision: RetentionDecision) -> dict[str, object]:
    return {
        "status": decision.status,
        "planned_action": decision.planned_action,
        "reason": decision.reason,
        "policy_domain": decision.policy_domain,
        "age_basis": decision.age_basis,
        "age_days": decision.age_days,
        "object_identity": _identity_payload(decision.object_identity),
        "blocker": _blocker_payload(decision.blocker) if decision.blocker else None,
    }


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "RetentionBlocker",
    "RetentionDecision",
    "RetentionDomainPolicy",
    "RetentionDryRunPlan",
    "RetentionObjectIdentity",
    "RetentionPlanError",
    "RetentionPolicy",
    "create_retention_dry_run_plan",
]
