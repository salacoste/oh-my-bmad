"""``deployment.signature_rejected`` event — Story 8.6 (FR56a, NFR-S9).

Emitted by ``scripts/emit_signature_rejected.py`` after ``just verify-images``
(Story 8.5) detects a cosign / SLSA / SBOM attestation failure. Capturing the
rejection on the event spine closes the supply-chain triumvirate loop: every
verification REJECTION becomes a permanent, replayable record on the same
JSONL log Epic 11's ``just verify-approval`` will consume.

**Registration site**

This module pioneers the ``packages/events/src/events/types/<domain>.py``
registration pattern. See ``events/types/__init__.py`` for the rationale.

**Schema-version distinction**

Event-type ``schema_version`` registers at ``"1.0.0"`` (first version of this
NEW event type). The envelope-level ``schema_version`` field (set when
``EventEnvelope.create()`` constructs the envelope around this payload) stays
at ``"1.0.0"`` until Story 9.7 bumps the envelope schema to ``"1.1.0"`` with
the ``trace_id`` field. The two ``schema_version``s are independent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from events.schema_registry import register

# Anchored canonical-image regexp (F1 carry-over from Story 8.5):
# ``ghcr.io/<owner>/oh-my-bmad-<service>``. The ``oh-my-bmad-`` prefix is
# enforced by the release.yml matrix and prevents fork-spoofing attempts
# that publish look-alike image names from arbitrary owners.
#
# Code-review fix F5+F6:
# - Owner segment must start and end with alphanumeric (no trailing ``-``),
#   matching GitHub username rules.
# - Service segment allows digits (e.g. a future ``oh-my-bmad-registry2``)
#   and must start+end with alphanumeric.
_IMAGE_PATTERN = (
    r"^ghcr\.io/"
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"/oh-my-bmad-"
    r"[a-z](?:[a-z0-9-]*[a-z0-9])?$"
)

# sha256 digest including the algorithm prefix — matches what cosign,
# ``actions/attest-build-provenance``, and GHCR release-notes all emit.
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

# Semver tag including pre-release identifiers (e.g. ``v0.1.5-rc1``). Mirrors
# the release.yml ``v*`` tag filter and the OMB_VERSION conventions from
# .env.example.
#
# Code-review fix F6: pre-release follows semver.org BNF — dot-separated
# alphanumeric+hyphen identifiers; no leading/trailing dots; no empty
# identifiers. Rejects degenerate values like ``v1.0.0-.``, ``v1.0.0-..foo``,
# ``v1.0.0--bar``.
_OMB_VERSION_PATTERN = (
    r"^v[0-9]+\.[0-9]+\.[0-9]+"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# GitHub-username rules: alphanumeric or hyphens, must start AND END with
# alphanumeric. Mirrors the anchored cert-identity-regexp in the
# just-verify-images recipe and GitHub's actual username constraint.
_GHCR_OWNER_PATTERN = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"

# Operator identifier — code-review fix F13. Reserved for Epic 11's
# HMAC-keyed identity. Pre-Epic-11 emissions leave ``operator_id=None``;
# the format regex prevents free-form claims like ``--operator-id admin``
# from polluting the audit trail before Epic 11 enforces authentication.
_OPERATOR_ID_PATTERN = r"^op-[a-zA-Z0-9-]+$"


class DeploymentSignatureRejectedPayload(BaseModel):
    """Payload for the ``deployment.signature_rejected`` event.

    Emitted when ``just verify-images`` fails one of the three cosign checks
    (signature, SLSA L2 provenance, or CycloneDX SBOM attestation) on a
    Platform image. The payload captures enough forensic detail for Epic 11's
    ``just verify-approval`` to triage the failure offline.

    ``operator_id`` is reserved for Epic 11 (operator-local HMAC
    non-repudiation via ``OPERATOR_HMAC_KEY``). Phase 2 Epic 8 always emits
    ``None``; Epic 11 will populate it with the operator's HMAC-keyed
    identifier.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    image: str = Field(pattern=_IMAGE_PATTERN, max_length=512)
    digest: str = Field(pattern=_DIGEST_PATTERN)
    attestation_type: Literal["signature", "slsaprovenance", "cyclonedx"]
    error_message: str = Field(min_length=1, max_length=4096)
    omb_version: str = Field(pattern=_OMB_VERSION_PATTERN, max_length=64)
    ghcr_owner: str = Field(pattern=_GHCR_OWNER_PATTERN, max_length=64)
    operator_id: str | None = Field(
        default=None,
        pattern=_OPERATOR_ID_PATTERN,
        max_length=128,
    )


# Side-effect: register at module-load. Idempotent — re-import is a no-op per
# schema_registry.py:80. Test isolation strategies that call ``unregister_all()``
# must re-import this module (or call ``register(...)`` directly) to restore.
register(
    "deployment.signature_rejected",
    "1.0.0",
    DeploymentSignatureRejectedPayload,
)


__all__ = ["DeploymentSignatureRejectedPayload"]
