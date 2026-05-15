"""Unit tests for ``events.types.deployment`` — Story 8.6 (FR56a).

Covers:

* Payload round-trip via canonical-JSON encoder
* ``extra="forbid"`` rejects unknown fields
* Each field's regex / constraint rejects malformed inputs
* Registration is live AND idempotent at import time

Test isolation: this module's payload registers as a side-effect of import.
Tests that need a fresh registry use the ``_isolated_registry`` fixture,
which snapshots+restores instead of ``unregister_all()`` — the registration
is module-load-time, not test-scope.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

import events.schema_registry as sr
from events.canonical import from_canonical_json, to_canonical_json
from events.clock import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id
from events.schema_registry import REGISTRY, register
from events.types.deployment import DeploymentSignatureRejectedPayload

_VALID_DIGEST = "sha256:" + ("a" * 64)
# Code-review fix F14: declared as ``dict[str, Any]`` (not ``dict[str, object]``)
# so spreading into Pydantic constructors doesn't trigger ``[arg-type]`` errors.
# Pydantic itself enforces the per-field strict types at validation time, so
# the loose static type is intentional and safe.
_VALID_KWARGS: dict[str, Any] = {
    "image": "ghcr.io/salacoste/oh-my-bmad-registry-api",
    "digest": _VALID_DIGEST,
    "attestation_type": "signature",
    "error_message": "no matching signatures",
    "omb_version": "v0.1.5",
    "ghcr_owner": "salacoste",
}


@pytest.fixture(autouse=True)
def _ensure_deployment_registered() -> Generator[None, None, None]:
    """Compensate for sibling-file ``unregister_all()`` cascades.

    ``packages/events/src/events/test_canonical.py`` and ``test_envelope.py``
    use autouse ``unregister_all()`` fixtures that blast the registry around
    every test. By the time pytest reaches this file (alphabetical order
    puts ``types/`` after the siblings), the module-load-time registration
    of ``deployment.signature_rejected`` has been cleared. Re-register the
    canonical class object before each test so the registry is in a known
    state.

    The ``register()`` call is idempotent for same-model re-register
    (schema_registry.py:80), so this is safe even when the entry is already
    present.
    """
    register(
        "deployment.signature_rejected",
        "1.0.0",
        DeploymentSignatureRejectedPayload,
    )
    yield


@pytest.fixture()
def _isolated_registry() -> Generator[None, None, None]:
    """Snapshot REGISTRY around tests that mutate it.

    Use this for tests that need to temporarily register a different model
    at the canonical key (e.g. ``test_register_different_model_at_same_key_rejected``).
    Snapshot + restore preserves the global state for tests that follow.
    """
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    sr._rebuild_types_cache()  # noqa: SLF001 — test-only registry rebuild


class TestRegistration:
    def test_registered_at_module_load(self) -> None:
        """Verify wiring — module-level register() fires on import.

        The autouse ``_ensure_deployment_registered`` fixture above guarantees
        the canonical class is present in REGISTRY at the start of every
        test in this file, regardless of suite ordering with sibling
        ``unregister_all()`` autouse fixtures (test_canonical.py /
        test_envelope.py). This test confirms the WIRING — that the
        canonical key + class object are bound together exactly as the
        module-level ``register()`` call sets them.
        """
        assert ("deployment.signature_rejected", "1.0.0") in REGISTRY
        assert (
            REGISTRY[("deployment.signature_rejected", "1.0.0")]
            is DeploymentSignatureRejectedPayload
        )
        assert "deployment.signature_rejected" in sr.EVENT_TYPES

    def test_register_is_idempotent_on_reimport(self) -> None:
        # Re-registering the same model is a no-op per schema_registry.py:80.
        register(
            "deployment.signature_rejected",
            "1.0.0",
            DeploymentSignatureRejectedPayload,
        )
        assert (
            REGISTRY[("deployment.signature_rejected", "1.0.0")]
            is DeploymentSignatureRejectedPayload
        )

    def test_register_different_model_at_same_key_rejected(self, _isolated_registry: None) -> None:
        class _Imposter(BaseModel):
            x: int

        with pytest.raises(ValueError, match="already registered"):
            register("deployment.signature_rejected", "1.0.0", _Imposter)


class TestPayloadRoundTrip:
    def test_envelope_round_trips_through_canonical_json(self) -> None:
        clock = FrozenClock(1_000_000_000, now=FROZEN_EPOCH)
        payload = DeploymentSignatureRejectedPayload(**_VALID_KWARGS)
        env = EventEnvelope.create(
            event_id=new_event_id(),
            schema_version="1.0.0",
            type="deployment.signature_rejected",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="operator", id="cli-helper"),
            payload=payload,
            request_id=new_request_id(),
        )
        line = to_canonical_json(env)
        restored = from_canonical_json(line)
        assert restored.type == "deployment.signature_rejected"
        assert restored.schema_version == "1.0.0"
        assert isinstance(restored.payload, dict)
        assert restored.payload["image"] == _VALID_KWARGS["image"]
        assert restored.payload["digest"] == _VALID_DIGEST
        assert restored.payload["attestation_type"] == "signature"
        assert restored.payload.get("operator_id") is None

    def test_operator_id_round_trips_when_set(self) -> None:
        payload = DeploymentSignatureRejectedPayload(
            **_VALID_KWARGS,
            operator_id="op-hmac-abc123",
        )
        # model_dump should preserve operator_id
        dumped = payload.model_dump()
        assert dumped["operator_id"] == "op-hmac-abc123"


class TestExtraForbid:
    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DeploymentSignatureRejectedPayload(
                **_VALID_KWARGS,
                unexpected="value",
            )
        assert "extra" in str(exc_info.value).lower()


class TestImageRegex:
    @pytest.mark.parametrize(
        "bad_image",
        [
            "docker.io/salacoste/oh-my-bmad-registry-api",  # wrong registry
            "ghcr.io/salacoste/some-other-image",  # missing oh-my-bmad- prefix
            "ghcr.io/Salacoste/oh-my-bmad-registry-api",  # uppercase owner
            "ghcr.io//oh-my-bmad-registry-api",  # empty owner
            "ghcr.io/salacoste/oh-my-bmad-",  # trailing hyphen no service
            "ghcr.io/salacoste-/oh-my-bmad-registry-api",  # F6: trailing hyphen owner
            "ghcr.io/salacoste/oh-my-bmad-registry-",  # F6: trailing hyphen service
            "",
        ],
    )
    def test_malformed_image_rejected(self, bad_image: str) -> None:
        kwargs = {**_VALID_KWARGS, "image": bad_image}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)

    @pytest.mark.parametrize(
        "good_image",
        [
            "ghcr.io/salacoste/oh-my-bmad-registry-api",
            "ghcr.io/owner1/oh-my-bmad-registry2",  # F5: digits allowed in service
            "ghcr.io/0wner/oh-my-bmad-registry-api",  # digit-starting owner
            "ghcr.io/a/oh-my-bmad-b",  # minimal valid
        ],
    )
    def test_well_formed_image_accepted(self, good_image: str) -> None:
        kwargs = {**_VALID_KWARGS, "image": good_image}
        DeploymentSignatureRejectedPayload(**kwargs)


class TestDigestRegex:
    @pytest.mark.parametrize(
        "bad_digest",
        [
            "abc123",  # missing prefix
            "sha256:" + "a" * 63,  # too short
            "sha256:" + "a" * 65,  # too long
            "sha256:" + "g" * 64,  # non-hex
            "SHA256:" + "a" * 64,  # uppercase prefix
            "sha512:" + "a" * 64,  # wrong algorithm
            "",
        ],
    )
    def test_malformed_digest_rejected(self, bad_digest: str) -> None:
        kwargs = {**_VALID_KWARGS, "digest": bad_digest}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)


class TestAttestationTypeLiteral:
    @pytest.mark.parametrize(
        "bad_type",
        ["sbom", "provenance", "SIGNATURE", "cycloneDX", "", "signed"],
    )
    def test_unknown_attestation_type_rejected(self, bad_type: str) -> None:
        kwargs = {**_VALID_KWARGS, "attestation_type": bad_type}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)

    @pytest.mark.parametrize("good_type", ["signature", "slsaprovenance", "cyclonedx"])
    def test_three_known_attestation_types_accepted(self, good_type: str) -> None:
        kwargs = {**_VALID_KWARGS, "attestation_type": good_type}
        # Should not raise.
        DeploymentSignatureRejectedPayload(**kwargs)


class TestErrorMessageConstraints:
    def test_empty_error_message_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "error_message": ""}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)

    def test_error_message_at_max_length_accepted(self) -> None:
        kwargs = {**_VALID_KWARGS, "error_message": "x" * 4096}
        DeploymentSignatureRejectedPayload(**kwargs)

    def test_error_message_over_max_length_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "error_message": "x" * 4097}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)


class TestOmbVersionRegex:
    @pytest.mark.parametrize(
        "bad_version",
        [
            "0.1.5",  # missing v prefix
            "v0.1",  # incomplete semver
            "v0.1.5.1",  # too many components
            "V0.1.5",  # uppercase v
            "v0.1.5-",  # trailing hyphen no pre-release
            "v1.0.0-.",  # F6: trailing dot in pre-release
            "v1.0.0-..foo",  # F6: empty pre-release identifier
            "v1.0.0-foo.",  # F6: trailing dot
            "",
        ],
    )
    def test_malformed_omb_version_rejected(self, bad_version: str) -> None:
        kwargs = {**_VALID_KWARGS, "omb_version": bad_version}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)

    @pytest.mark.parametrize(
        "good_version",
        ["v0.1.5", "v0.1.5-rc1", "v1.0.0", "v0.1.5-alpha.1", "v10.20.30"],
    )
    def test_well_formed_omb_versions_accepted(self, good_version: str) -> None:
        kwargs = {**_VALID_KWARGS, "omb_version": good_version}
        DeploymentSignatureRejectedPayload(**kwargs)


class TestGhcrOwnerRegex:
    @pytest.mark.parametrize(
        "bad_owner",
        [
            "Salacoste",  # uppercase
            "-salacoste",  # leading hyphen
            "salacoste-",  # F6: trailing hyphen
            "salacoste!",  # invalid char
            "",
        ],
    )
    def test_malformed_ghcr_owner_rejected(self, bad_owner: str) -> None:
        kwargs = {**_VALID_KWARGS, "ghcr_owner": bad_owner}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)


class TestOperatorIdOptional:
    def test_default_is_none(self) -> None:
        payload = DeploymentSignatureRejectedPayload(**_VALID_KWARGS)
        assert payload.operator_id is None

    def test_well_formed_operator_id_accepted(self) -> None:
        # Code-review fix F13: ``operator_id`` follows ``^op-[a-zA-Z0-9-]+$``.
        kwargs = {**_VALID_KWARGS, "operator_id": "op-hmac-abc123"}
        DeploymentSignatureRejectedPayload(**kwargs)

    @pytest.mark.parametrize(
        "bad_operator_id",
        [
            "",  # empty
            "admin",  # missing op- prefix (free-form claim — F13 reject)
            "root",  # missing op- prefix
            "op-",  # prefix but no body
            "op-with spaces",  # invalid char
            "op-with/slash",  # invalid char
            "OP-uppercase",  # uppercase op-
        ],
    )
    def test_free_form_operator_id_rejected(self, bad_operator_id: str) -> None:
        kwargs = {**_VALID_KWARGS, "operator_id": bad_operator_id}
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)


class TestFrozenStrict:
    def test_post_construction_mutation_rejected(self) -> None:
        payload = DeploymentSignatureRejectedPayload(**_VALID_KWARGS)
        with pytest.raises(ValidationError):
            # Pydantic raises ValidationError on frozen-instance attribute set.
            payload.image = "ghcr.io/other/oh-my-bmad-registry-api"  # type: ignore[misc]

    def test_string_coercion_rejected_under_strict(self) -> None:
        # ``strict=True`` rejects type-coercion: int-as-string fails.
        kwargs = cast(dict[str, object], {**_VALID_KWARGS, "image": 12345})
        with pytest.raises(ValidationError):
            DeploymentSignatureRejectedPayload(**kwargs)
