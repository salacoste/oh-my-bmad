---
id: ADR-0006
status: accepted
date: 2026-05-21
supersedes: null
---

# ADR-0006: Approval signing + rotation protocol

## Status

**Accepted** — 2026-05-21. Authored by Story 11.5 as the closing artefact
of Epic 11 (operator approval signing / FR64 + FR65 + FR65a + NFR-S10 /
Phase 2 milestone). This ADR closes one of the five Phase-2 forward-
referenced ADR acceptance-gate items declared in [ADR-0003](./0003-phase-2-gate.md)
§Decision-item-4 (ADR-0004 through ADR-0008). It MUST be `accepted` before
any further approval-signing-touching story merges to `main`.

## Context

Phase 2's functional requirements FR64, FR65, FR65a and non-functional
requirement NFR-S10 created the operator-level approval-signing
subsystem. The protocol is required because Phase 1's `task.approval` /
`approval.granted` audit events are unauthenticated — anyone with write
access to the JSONL log could forge an approval record. Operators
running multi-day autonomous Coding-Agent sessions need cryptographic
proof that:

1. **(FR64)** Each operator approval is signed at decision time by a
   key held by the operator. An attacker who later compromises the
   registry-state SQLite or the JSONL log cannot retroactively forge
   `approval.granted` events for tasks they did not personally approve.
2. **(FR65)** Operators can verify any historical approval offline,
   without booting the Platform stack, against a frozen archive (so
   audit-window evidence survives a registry-state crash or rebuild).
3. **(FR65a)** When the operator rotates the signing key, both
   pre-rotation and post-rotation approvals remain verifiable —
   pre-rotation against the archived prior key, post-rotation against
   the live key. The rotation itself produces a tamper-proof audit
   record (`key.rotated`).
4. **(NFR-S10)** The key NEVER appears in any event, log, snapshot, or
   the registry-state database. Compromise of any of those surfaces
   leaks data but does NOT leak the signing key.

Stories 11.1-11.5 implemented this incrementally:

| Story | Date       | Delivered                                                                    |
|-------|------------|------------------------------------------------------------------------------|
| 11.1  | 2026-05-19 | `compute_approval_hmac` pure HMAC primitive; FR64 sign-time wiring           |
| 11.2  | 2026-05-19 | `KeyRotatedPayload` + `CapabilityDeniedPayload` schemas at schema_version 1.1.0 |
| 11.3  | 2026-05-20 | FR63 pinned-thread approval routing (orthogonal to signing, same epic)       |
| 11.4  | 2026-05-21 | `just verify-approval` offline recipe + verifier CLI (FR65)                  |
| 11.5  | 2026-05-21 | Key rotation flow + `key.rotated` event emission + this ADR (FR65a)          |

This ADR records the final stable contract that those five stories
converged on; subsequent stories (notably Story 6.1+ JWT auth and any
Phase 3 migration to asymmetric signatures) MUST honour the invariants
documented below or supersede this ADR via a successor.

## Decision

The approval-signing + verification + rotation protocol consists of
ten contract points. Each is implemented in production code today;
this section is the single SoT against which divergence is detected.

### Canonical signing string

Sign-time and verify-time canonical input is a pipe-delimited string:

```
f"{task_id}|{action}|{ms_truncated_timestamp.isoformat()}|{actor_id}"
```

Pipe (`|`) is the canonical delimiter and is **forbidden** in any
canonical-string field. `compute_approval_hmac` raises `ValueError` if
`task_id`, `action`, or `actor_id` contains `|` (Story 11.1 P1-H1
canonical-string-injection guard). Per FR64 wording, the `override`
field of an approval is NOT part of the canonical signing string.

**Latent today, mandatory before Story 6.1+ JWT auth lands**: real-world
JWT `sub` values like `"org|alice"` would otherwise cause two distinct
`(task_id, action, timestamp, actor_id)` tuples to share a canonical
string and thus the same HMAC, forging a signing record.

### Timestamp normalisation — millisecond precision

The `timestamp` argument to `compute_approval_hmac` is truncated to
millisecond precision before `isoformat()`. This is required so the
sign-time canonical bytes match the storage canonical bytes (which
`events.canonical._datetime_to_iso_z` truncates to ms + `Z` suffix).

Three places agree on the ms-precision contract:

1. Sign-time canonical (`compute_approval_hmac`, Story 11.4 PP2).
2. Verify-time canonical (`scripts/verify_approval.py`).
3. On-disk canonical (`events.canonical._datetime_to_iso_z`, Story 2.1).

Pre-PP2, any production event with non-zero sub-ms microseconds would
fail offline verification because the stored ISO string `…123Z` parsed
to `microsecond=123000` but the signed canonical was `…123456+00:00`.
The Story 11.1 golden vector input had `microsecond=0` (truncation is a
no-op for zero µs) so the published hex digest is unchanged.

### HMAC algorithm

HMAC-SHA256 with the operator's local `OPERATOR_HMAC_KEY` (Pydantic
`SecretStr`, minimum 32 bytes / 256 bits per Story 11.1 P1-M4). The
output is the 64-character lowercase hex digest. Hex (not base64) was
chosen because it is operator-readable in events / logs /
`just verify-approval` output. 64 hex chars = 32 bytes = HMAC-SHA256
output size.

### Single source of truth (SSoT)

The pure HMAC function `compute_approval_hmac` lives in
`packages/events/src/events/approval_signing.py`. registry-api re-exports
it from `services/registry-api/src/registry_api/adapters/approval_signing.py`
as a thin compatibility shim for existing call sites
(`routes/decisions.py`).

**Never fork.** The relocation closed Story 11.4 pass-1 P0 finding PP3:
the verifier `scripts/verify_approval.py` was transitively pulling
FastAPI, SQLAlchemy, Anthropic and the full registry-state SQL stack via
`registry_api/__init__.py`'s `build_app` import, falsifying the
verifier's "pure-Python / no FastAPI / no SQLAlchemy" contract. After
PP3, the verifier imports `compute_approval_hmac` directly from
`events.approval_signing` and confirms no service-layer transitive
imports via a subprocess `sys.modules` probe.

Story 11.5 placed `compute_key_fingerprint` alongside it (D2) so the
audit/security review surface for HMAC-key crypto is a single module.

### Constant-time comparison

The verifier (`scripts/verify_approval.py` Story 11.4 PP1) uses
`hmac.compare_digest` for comparing the stored hex digest against the
recomputed hex digest. Never `==`. Producer-side code has no comparison
surface (signing only writes), so timing attacks are not a concern on
the sign side.

### Key fingerprint

`compute_key_fingerprint(key: SecretStr) -> str` (Story 11.5 AC1) returns
`SHA-256(key_bytes)[:16]` = 16 lowercase hex chars = 64 bits.

* **Operator-readable**: short enough to scan in logs and compare visually.
* **Collision-safe for single-operator key populations**: 2⁻⁶⁴ collision
  probability for the deployment model this Platform serves.
* **NEVER reveals the key**: one-way SHA-256 + truncation; the original
  key cannot be recovered from the fingerprint even in theory.

The fingerprint is the dedup invariant for the rotation detector — see
the next section.

### Rotation detection

`registry_api/adapters/key_rotation.py:detect_and_emit_key_rotation`
(Story 11.5 AC4) runs synchronously in registry-api's lifespan startup
BEFORE the FastAPI app starts accepting requests. It:

1. Reads the singleton `KeyFingerprint` row from registry-state (PK =
   literal `"current"`).
2. Computes the fingerprint of the supplied current
   `OPERATOR_HMAC_KEY` via `compute_key_fingerprint`.
3. Compares the two. If equal, no-op. If different (or empty table),
   emits exactly one `key.rotated` event via `EventLogWriter` (FR26
   single-writer rule — never writes SQLite directly).

**Bootstrap sentinel (D1)**: first boot with no prior fingerprint row
emits `key.rotated` with `previous_key_fingerprint = "0000000000000000"`
(16 zero-hex chars). The probability that a real `SHA-256(key)[:8]`
equals 16 zeros is 2⁻⁶⁴, which is negligible for the single-operator
key population. `KeyRotatedPayload`'s `previous != new` invariant
therefore holds. The sentinel is also reserved in
`compute_key_fingerprint`'s docstring so future call sites know the
collision-impossible value.

**Synchronous + fail-loud (D3)**: rotation events MUST be persisted
before registry-api serves requests. If event-log I/O fails during
emission, lifespan startup raises and registry-api refuses to serve.
Same rationale as Story 2.4's `EventLogWriter.recover()` being
synchronous on startup — the audit invariant supersedes uptime;
operators address the storage problem before any approval traffic
flows.

**Actor identity (D4)**: `actor_id = "key-rotation-detector"`.
Distinguishes rotation events from operator-driven approval events
in audit logs without polluting the env-var space. Allowed by
`KeyRotatedPayload.actor_id` `min_length=1` constraint (Story 11.2 D3
explicitly relaxes from the no-pipe pattern for richer service
identifiers).

### Pre-rotation verification

Operators retain prior keys for audit-window duration; verifying a
pre-rotation approval requires the operator to point
`just verify-approval` at the archived key file:

```
just verify-approval <event-id> --log-dir <archive-dir> --key-file <path-to-archived-key>
```

The verifier (Story 11.4 AC4) reads the key from `--key-file` if
present, otherwise from `OPERATOR_HMAC_KEY`. Story 11.4 PP4 catches
`UnicodeDecodeError` on non-UTF-8 key-file content; PP7 strips
trailing whitespace/newlines (so `echo $KEY > key.bin` works).

The Story 11.5 AC5 update to the `signature_mismatch` reason's
investigation steps surfaces `key.rotated` events explicitly so
operators can find the previous key fingerprint via:

```
grep '"type":"key.rotated"' <log-dir>/*.jsonl
```

### Key isolation (NFR-S10)

The operator key NEVER appears in any event, log, snapshot, or the
registry-state SQLite store. Enforced by:

* `OPERATOR_HMAC_KEY` is wrapped in `pydantic.SecretStr` at the
  settings boundary — `repr()` and `model_dump()` mask the value.
* `compute_approval_hmac` and `compute_key_fingerprint` call
  `.get_secret_value()` exactly ONCE inside each function and the
  result never leaves the local frame.
* The HMAC hex output (intended for event payloads + logs) is the
  one-way digest — it cannot be reversed to the key.
* The fingerprint hex output (also intended for events + logs) is
  the one-way SHA-256 truncation — it cannot be reversed to the key.
* The verifier (Story 11.4 PP11) never logs key bytes; structured
  logs include byte count + fingerprint, never the raw bytes.
* Story 11.5 AC8 `tests/integration/test_hmac_key_isolation.py`
  greps event-log JSONL, registry-state SQLite, and captured
  structlog output for a canary key sentinel; all four tests are
  `@pytest.mark.slow` (D5) and gate the Epic 11 acceptance criteria.

### Offline verifier

`scripts/verify_approval.py` (Story 11.4) is a pure-Python CLI:

* **No FastAPI** — verified by Story 11.4 PP3 `sys.modules` probe.
* **No SQLAlchemy** — same probe.
* **No service-layer transitive imports** — `from events.approval_signing
  import compute_approval_hmac` is the only crypto import path.
* **Pure-Python JSONL reader** — replays the canonical envelope from
  disk via `json.loads`; no envelope-validation wiring.
* **Works against a frozen archive** — no registry-state, no event-log
  writer, no SQLite engine. Operators can run it on a backup laptop
  with only the JSONL files + the key file.

The verifier is the operator's safety net: even if every other Platform
process is down, the audit trail remains independently verifiable for
the audit-window duration.

## Consequences

### Capability tier interactions

Story 6.x defined the Tier-1/Tier-2/Tier-3 capability ladder. Signed
approvals are produced ONLY at the Tier-3 ingress (HTTP API `POST
/v1/tasks/.../decisions`). Tier-1/Tier-2 surfaces emit unsigned
audit events; the ApprovalSigningSettings boundary (Story 11.1) is the
gate. Story 6.x rejects in the Tier-3 surface land as
`deployment.signature_rejected` events (Story 8.6) — distinct from
`approval.rejected` (operator decision), guarded by the same HMAC
verification path.

### Story 6.1+ JWT auth migration

When real JWT-based operator authentication arrives (Story 6.1+), the
`actor_id` field becomes a JWT `sub` claim. The Story 11.1 P1-H1
pipe-injection guard becomes mandatory (latent today): JWT `sub` values
like `"org|alice"` would otherwise collide on the canonical signing
string. The guard is already in production via
`compute_approval_hmac.ValueError("pipe character forbidden")`.

### Backup / restore implications

Operators backing up the registry-state SQLite + JSONL event log
captures the `key.rotated` audit trail. Restore is straightforward:
the next registry-api boot will re-detect any post-restore rotation
and emit a new `key.rotated` event recording the transition. The
`KeyFingerprint` singleton row is restored alongside the rest of the
materialized state.

### Story 11.5.1 follow-up (backlog)

A `/key-status` Telegram command + `console-cli key-status` console
command surfacing the current fingerprint + last rotation time were
deferred (Story 11.5 AC6 / D-resolution) to Story 11.5.1. Default
behaviour: operators inspect the singleton row via SQL or read the
most-recent `key.rotated` event from the JSONL log.

## Alternatives considered

### (a) Per-event-type signing keys — REJECTED

Use separate HMAC keys for `approval.granted` vs `task.stop_requested`
vs `tier3.budget_override` events. Increases the key-management
surface (now an operator manages N keys, not one) without changing
the threat model (any compromised key forges approvals of its type).
The single-operator deployment model the Platform serves does not
benefit from key-per-event-type separation.

### (b) Asymmetric signatures via ed25519 — REJECTED for Phase 2

Operator holds the ed25519 private key; the Platform verifies via
the public key embedded in configuration. Eliminates the key-isolation
requirement entirely (the public key is fine to log) and creates a
hardware-wallet upgrade path.

**Deferred to Phase 3**: symmetric HMAC is sufficient for Phase 2's
single-operator model + materially simpler operator workflow
(`echo "$(openssl rand -base64 32)" > .env`). A Phase 3 ADR will
supersede this one when the Platform moves to multi-operator /
shared-key / federation territory.

### (c) Full envelope signing — REJECTED

Sign the entire canonical envelope JSON (event_id + emitted_at +
actor + payload + …) instead of just the canonical signing string.
Marginally stronger (catches envelope tampering, not just payload
tampering) but materially harder to verify offline: the verifier
would need to reproduce the canonical-JSON encoder byte-for-byte,
which couples it to the Story 2.1 canonical encoder version.

The payload-signing approach keeps the verifier independent of
envelope-format evolution; envelope-level tampering is already
detected because `task_id` + `actor_id` + `timestamp` are all part
of the canonical signing string AND are also stored at the envelope
level (mismatch between the two surfaces is its own integrity
signal — `event_type_mismatch` reason code in the verifier).

## References

* FR64, FR65, FR65a, NFR-S10 in `_bmad-output/planning-artifacts/prd.md`.
* Story 11.1 — `_bmad-output/implementation-artifacts/11-1-operator-approval-hmac-signing.md`
* Story 11.2 — `_bmad-output/implementation-artifacts/11-2-key-rotated-event-schema.md`
* Story 11.3 — `_bmad-output/implementation-artifacts/11-3-approvals-pinned-thread-handler.md`
* Story 11.4 — `_bmad-output/implementation-artifacts/11-4-just-verify-approval-offline-recipe.md`
* Story 11.5 — `_bmad-output/implementation-artifacts/11-5-key-rotation-flow-key-rotated-event.md`
* [ADR-0003: Phase-2 gate](./0003-phase-2-gate.md) — declared this ADR
  as an acceptance-gate item.
