# HMAC approval signing — how approval decisions are cryptographically non-repudiatable

> Phase-2 deep-dive (Epic 11, FR64 / FR65 / FR65a / NFR-S10). Companion to
> [ADR-0006](../adr/0006-approval-signing-and-rotation-protocol.md) — the ADR records the
> *decision*; this explains *how it works* so you can debug and operate it.

## The problem it solves

In Phase 1, when an operator approved a task via HTTP (`POST /v1/tasks/.../decisions`),
the Platform recorded an `approval.granted` event. But anyone with write access to the JSONL
log or the SQLite snapshot could forge that event retroactively. Multi-day autonomous
sessions (where a Coding Agent runs unattended and requires human approval at key
decision points) need cryptographic proof: *this approval came from the holder of the
operator's signing key, and the decision has not been tampered with since*.

HMAC approval signing provides that proof. Each approval is signed at decision time with
a key held only by the operator (never stored on the Platform), and the signature is
verifiable offline — without booting any service — against a frozen JSONL archive. If
the operator rotates the key, both old and new approvals remain verifiable with their
respective keys.

## The one rule: sign once at decision time, verify at any time

An approval is signed **exactly once**, at the moment the operator makes the decision
(Story 11.1, FR64), and the signature is **immutable** — part of the event payload and
the JSONL log forever. A signature is computed over the **canonical approval payload**
(a pipe-delimited string), never the raw HTTP request body or the entire event envelope.
This decouples offline verification from envelope-format evolution: the verifier can
re-compute the signature using only the payload fields, not the full event structure.

Two event types are always emitted in tandem (Story 11.1):

- **`approval.granted`** — the operator's decision (task_id, action="approve", decided_at).
  This is the authoritative record of intent; no signature on this event.
- **`task.approval_signed`** — the sibling carrying the HMAC hex digest. Story 11.1
  guarantees `approval.granted` is appended to the JSONL log BEFORE `task.approval_signed`
  (ordering invariant), so the verifier can find both in sequence.

Both events share the same `task_id`, `action`, `decided_at`, and `actor_id`. The
canonical signing string is constructed from these four fields only — the `override`
field (if present) is **not** signed, per FR64 wording.

## The canonical signing form and why millisecond precision matters

The canonical string is **pipe-delimited**:

```
f"{task_id}|{action}|{ms_truncated_timestamp.isoformat()}|{actor_id}"
```

Pipe (`|`) is the canonical delimiter and is **forbidden** in any field value. If
`task_id` or `actor_id` contains a pipe, `compute_approval_hmac` raises `ValueError`
(Story 11.1 P1-H1 canonical-string-injection guard). This is latent today (actor_id
is hardcoded to `"http-api"`), but mandatory before Story 6.1+ JWT auth arrives —
real-world JWT `sub` values like `"org|alice"` would otherwise cause two distinct
approvals to collide on the canonical string and share the same HMAC, forging a
signature record.

**Millisecond truncation (Story 11.4 PP2)** — the timestamp is truncated to millisecond
precision **before** `isoformat()` is called. This is required so the sign-time canonical
bytes match the storage canonical bytes (which `events.canonical._datetime_to_iso_z`
truncates sub-ms microseconds to `…123Z`). Pre-PP2, any production event with non-zero
sub-ms microseconds would fail offline verification: the stored ISO string parsed to
`microsecond=123000` but the signed canonical was `…123456+00:00`. The Story 11.1 golden
vector input had `microsecond=0` (truncation is a no-op), so the hex digest is unchanged
— but the contract is now explicit and enforced.

Key files (source of truth):
- **Pure HMAC function:** `packages/events/src/events/approval_signing.py:90-187`
  (`compute_approval_hmac`). This is the single source of truth (SSoT, ADR-0006 D3).
  The verifier (`scripts/verify_approval.py`, Story 11.4) imports this function
  directly; no fork, no re-implementation.
- **Timestamp normalization:** `packages/events/src/events/approval_signing.py:177-181`
  shows the millisecond truncation logic. Storage canonical form is in
  `packages/events/src/events/canonical.py:_datetime_to_iso_z` (Story 2.1).

## Where signing happens in the write path (FR26 single-writer preserved)

The registry-api HTTP `POST /v1/tasks/.../decisions` handler (Story 6.4) wires signing
into the approval emission path (Story 11.1 FR64 wiring, refined by Story 11.4):

1. **Decision received** — the operator sends `{"action": "approve", "override": null}`
   to `POST /v1/tasks/{task_id}/decisions`.
2. **HMAC computed** — registry-api calls `compute_approval_hmac(key=OPERATOR_HMAC_KEY,
   task_id=…, action="approve", timestamp=decided_at, actor_id="http-api")`, yielding a
   64-character lowercase hex digest (HMAC-SHA256).
3. **Two events emitted atomically** — via `EventLogWriter` (Story 2.4, FR26
   single-writer rule; never writes SQLite directly):
   - `approval.granted` with the decision payload.
   - `task.approval_signed` with the hex digest (a `TaskApprovalSignedPayload`).

Story 11.1 P1-H2 applies full Field constraints at schema_version `1.0.0`:
- `task_id` / `decision_id`: alphanumeric + `_:.-` only (explicit no-pipe, P1-H1
  defense-in-depth).
- `hmac_sha256`: exactly 64 lowercase hex characters (contract).
- `actor_id`: non-empty string (pattern-constrained to JWT `sub` format when Story
  6.1+ auth lands).

The HMAC-SHA256 algorithm is specified in ADR-0006 §Decision / HMAC algorithm: the
operator's local `OPERATOR_HMAC_KEY` (Pydantic `SecretStr`, minimum 32 bytes / 256 bits)
is the keying material. The output is hex (not base64) because it is operator-readable
in events, logs, and `just verify-approval` output.

## The offline verification path (why imports matter)

The `scripts/verify_approval.py` verifier (Story 11.4, FR65) is a pure-Python CLI:

- **No FastAPI** — verified by Story 11.4 PP3 subprocess `sys.modules` probe.
- **No SQLAlchemy** — same probe.
- **No service-layer transitive imports** — the only crypto import is `from
  events.approval_signing import compute_approval_hmac`.
- **Pure-Python JSONL reader** — replays the canonical envelope from disk via
  `json.loads`; no envelope-validation wiring, no event-log writer.
- **Works against a frozen archive** — no registry-state, no event-log writer, no
  SQLite engine. Operators can run it on a backup laptop with only the JSONL files
  + the key file.

The verifier finds the target event by `event_id` (Story 11.4 AC1), validates the
event type is `task.approval_signed` (error reason: `event_type_mismatch` if not),
extracts the canonical fields (`task_id`, `action`, `timestamp`, `actor_id`) from
the paired `approval.granted` sibling (AC4), recomputes the HMAC via `compute_approval_hmac`,
and compares the two hex digests using `hmac.compare_digest` for constant-time
comparison (AC2 / ADR-0006 constant-time comparison section).

Exit codes (Story 11.4 AC3):
- **0** — match
- **1** — mismatch (HMAC re-computation differs from stored)
- **2** — event not found, event type mismatch, payload missing field, payload field
  invalid, or payload canonical violation
- **3** — key invalid (missing, <32 bytes, file-read error)
- **4** — log-dir missing or unreadable
- **5** — internal error (bug)

Usage (Story 11.4, FR65):

```sh
# Verify using default log directory + OPERATOR_HMAC_KEY env var
just verify-approval <EVENT_ID>

# Verify against a frozen backup
just verify-approval <EVENT_ID> /path/to/archive/events

# Machine-readable JSON output
just verify-approval <EVENT_ID> --json
```

The `justfile` recipe (line 586) is:
```
verify-approval EVENT_ID LOG_DIR='/var/lib/oh-my-bmad/registry/events' *FLAGS='':
    uv run python scripts/verify_approval.py {{EVENT_ID}} --log-dir {{LOG_DIR}} {{FLAGS}}
```

This is the operator's safety net: even if every other Platform process is down, the
audit trail remains independently verifiable for the audit-window duration.

## Key rotation (FR65a: pre-rotation approvals remain verifiable)

When the operator rotates the `OPERATOR_HMAC_KEY` (Story 11.5, FR65a), both old and
new approvals remain verifiable — old approvals with the archived prior key, new
approvals with the current key. The rotation itself produces an immutable audit record.

### The rotation detector and `key.rotated` event emission

Story 11.5's key-rotation detector (`detect_and_emit_key_rotation` in
`services/registry-api/src/registry_api/adapters/key_rotation.py`) runs synchronously
in registry-api's lifespan startup **before** the FastAPI app starts accepting requests
(Story 11.5 AC4, D3 synchronous + fail-loud):

1. **Read the most-recent fingerprint** — from the JSONL event log first (Story 11.5
   PD1, "event-log-first lookup"), scanning for the latest `key.rotated` event's
   `new_key_fingerprint` field. If the log has no `key.rotated` events, fall back to
   the `KeyFingerprint` singleton row in registry-state (snapshot-restored-deployment
   fallback).
2. **Compute current fingerprint** — via `compute_key_fingerprint(OPERATOR_HMAC_KEY)`
   (Story 11.5, ADR-0006 D2). A fingerprint is `SHA-256(key_bytes).hex()[:16]` = 16
   lowercase hex chars = 64 bits. Operator-readable; collision-safe for single-operator
   key populations (2⁻⁶⁴ collision probability, negligible).
3. **Compare and emit if different** — if the computed fingerprint differs from the
   last-known, emit exactly one `key.rotated` event via `EventLogWriter` (FR26
   single-writer rule, never writes SQLite directly).

**Bootstrap sentinel (D1)** — first boot with no prior fingerprint row emits `key.rotated`
with `previous_key_fingerprint = "0000000000000000"` (16 zero-hex chars). The probability
that a real `SHA-256(key_bytes).hex()[:16]` equals 16 zeros is 2⁻⁶⁴, negligible. The
sentinel is reserved in `compute_key_fingerprint`'s docstring (Story 11.5 PP4), so future
call sites know the collision-impossible value. Story 11.5 PP4 adds a defensive check:
if the computed fingerprint collides with the sentinel, `compute_key_fingerprint` raises
`ValueError`, forcing the operator to choose a different key.

**Event-log-first lookup (PD1)** — pre-PD1, the detector consulted only SQLite, risking
duplicate-emission windows across fast restarts (the JSONL log is the SSoT per arch_refs
P2-I3 derived-projection principle; the subscriber-materializer may lag). Post-PD1, the
detector reads the log first, SQLite only as fallback.

The `key.rotated` event payload (Story 11.2 schema_version `1.1.0`) records:
- `rotated_at` — timezone-aware timestamp (naïve timestamps rejected at the payload boundary).
- `previous_key_fingerprint` — the 16-hex fingerprint of the prior key.
- `new_key_fingerprint` — the 16-hex fingerprint of the new key.
- `actor_id` — hardcoded to `"key-rotation-detector"` (D4, ADR-0006). Distinguishes
  rotation events from operator-driven approval events without polluting the env-var space.

### Verifying pre-rotation approvals

Operators retain prior keys for audit-window duration; verifying a pre-rotation approval
requires passing the archived key file to `just verify-approval`:

```sh
just verify-approval <EVENT_ID> /var/lib/oh-my-bmad/registry/events --key-file /path/to/archived/key
```

The verifier (Story 11.4 AC4) reads the key from `--key-file` if present, otherwise from
`OPERATOR_HMAC_KEY` (environment variable). Story 11.4 PP4 catches `UnicodeDecodeError`
on non-UTF-8 key-file content; PP7 strips trailing whitespace/newlines (so
`echo $KEY > key.bin` works).

Story 11.5 AC5 enriches the `signature_mismatch` reason in investigation steps: if
verification fails, operators can find the corresponding `key.rotated` events via:

```sh
grep '"type":"key.rotated"' <log-dir>/*.jsonl
```

The `previous_key_fingerprint` field on the nearest preceding `key.rotated` event tells
you which key was in effect at signing time.

Key files (source of truth):
- **Key fingerprint computation:** `packages/events/src/events/approval_signing.py:190-245`
  (`compute_key_fingerprint`).
- **Rotation detection:** `services/registry-api/src/registry_api/adapters/key_rotation.py`
  (`detect_and_emit_key_rotation`).
- **Key rotation event schema:** Story 11.2, `packages/events/src/events/payloads.py`
  (`KeyRotatedPayload`).

## The key-isolation invariant (NFR-S10)

The operator key NEVER appears in any event, log, snapshot, or the registry-state SQLite
store. This is enforced at multiple layers:

- **Settings boundary** — `OPERATOR_HMAC_KEY` is wrapped in Pydantic `SecretStr`. The
  `repr()` and `model_dump()` methods mask the value.
- **Pure function isolation** — `compute_approval_hmac` and `compute_key_fingerprint`
  call `.get_secret_value()` **exactly once** inside each function, and the result never
  leaves the local frame. The caller owns any leak surface (logs, events, snapshots).
- **One-way outputs** — the HMAC hex output (intended for event payloads + logs) is the
  64-character HMAC-SHA256 digest — it cannot be reversed to the key. The fingerprint
  hex output (also intended for events + logs) is the one-way SHA-256 truncation — it
  cannot be reversed to the key.
- **Verifier logging** — `scripts/verify_approval.py` (Story 11.4 PP11) never logs key
  bytes. Structured logs include byte count + fingerprint, never the raw bytes.
- **Integration testing** — Story 11.5 AC8 `tests/integration/test_hmac_key_isolation.py`
  greps the event-log JSONL, registry-state SQLite, and captured structlog output for a
  canary key sentinel. All four tests are `@pytest.mark.slow` (D5) and gate the Epic 11
  acceptance criteria.

Compromise of the JSONL log, SQLite snapshot, or service logs leaks data but does **not**
leak the signing key. Compromise of `.env` or the filesystem location holding the key is
a separate breach — the isolation invariant concerns Platform surfaces only.

## Putting it together: a complete flow

1. **Operator sets up** — generates a 32+ byte random key and stores it in `OPERATOR_HMAC_KEY`.
2. **First registry-api boot** — the rotation detector finds no `key.rotated` event, emits
   one with `previous_key_fingerprint="0000000000000000"` (bootstrap sentinel) and
   `new_key_fingerprint=<computed>`.
3. **Operator makes an approval** — POSTs `{"action": "approve"}` to `POST /v1/tasks/{task_id}/decisions`.
4. **Decision handler signs** — calls `compute_approval_hmac(…)` with the OPERATOR_HMAC_KEY,
   yielding a 64-char hex digest.
5. **Two events are logged** — `approval.granted` (the decision), then `task.approval_signed`
   (the signature). Both have the same `task_id`, `action="approve"`, `decided_at`,
   `actor_id="http-api"`.
6. **Offline verification (audit, 1 month later)** — operator runs `just verify-approval
   <EVENT_ID>` with the same key, the verifier recomputes the HMAC, compares digest,
   outputs match ✓ or mismatch ✗ with investigation steps.
7. **Key rotation (audit window expires)** — operator changes `OPERATOR_HMAC_KEY`, restarts
   registry-api. The rotation detector finds the old fingerprint, computes the new one,
   emits `key.rotated` event recording the transition.
8. **Pre-rotation approval verification (still within audit window)** — operator runs
   `just verify-approval <OLD_EVENT_ID> --key-file /path/to/prior_key`, verifier reads
   the prior key, recomputes the HMAC, outputs match ✓.

## See also

- [ADR-0006](../adr/0006-approval-signing-and-rotation-protocol.md) — the decision + ten
  contract points (canonical form, timestamp normalization, HMAC algorithm, key fingerprint,
  rotation detection, key isolation, offline verifier).
- [trace-id-propagation.md](./trace-id-propagation.md) — operator correlation (distinct from
  authentication). `trace_id` is operator-visible and freely forwarded; HMAC signing is the
  authentication mechanism.
- [Operator runbook § Approval signing](../operator-runbook.md#approval-signing--offline-verification)
  — procedural steps for Setup, Verify, and Key Rotation.
- [Story 11.1](../_bmad-output/implementation-artifacts/11-1-operator-approval-hmac-signing.md) —
  Sign-time wiring (FR64).
- [Story 11.4](../_bmad-output/implementation-artifacts/11-4-just-verify-approval-offline-recipe.md) —
  Offline verifier (FR65).
- [Story 11.5](../_bmad-output/implementation-artifacts/11-5-key-rotation-flow-key-rotated-event.md) —
  Key rotation + `key.rotated` event (FR65a).
