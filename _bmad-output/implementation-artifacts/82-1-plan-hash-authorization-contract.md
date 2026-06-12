# Story 82.1: Plan-hash authorization contract

## Status

Done.

## Story

As an operator and maintainer,
I want any future destructive lifecycle apply authorization to bind to the exact dry-run `plan_hash`,
so that no hot-log mutation can proceed from stale, ambiguous, or mismatched lifecycle evidence.

## Acceptance criteria

1. Future apply authorization evidence is scoped to an exact `LifecycleDryRunPlan.plan_hash`.
2. The authorization evidence shape includes affected segment identities, dry-run artifact reference, replay proof reference, rollback evidence reference, operator identity, timestamp, and audit event/ledger reference.
3. Future apply must re-compute the dry-run plan hash immediately before mutation and fail closed on any mismatch.
4. Future apply cannot be enabled by a `dry_run=false` flag or boolean toggle on the dry-run command.
5. Missing, stale, unsigned, unverifiable, or ambiguous authorization blocks future apply.
6. This story introduces no runtime/package/API/deployment behavior.

## Contract

A future lifecycle apply implementation must require durable authorization evidence with at least these fields:

| Field | Requirement |
|---|---|
| `plan_hash` | Exact SHA-256 hash from `LifecycleDryRunPlan.plan_hash`; required. |
| `dry_run_artifact_ref` | Immutable/content-addressed artifact path, URI, or event id for the dry-run plan; required. |
| `safety_policy_version` | Must match the dry-run plan safety policy version; required. |
| `retention_input_digest` | Digest or canonical representation of retention inputs covered by the plan; required. |
| `affected_segments` | Segment identities intended for mutation: source, logical date, sequence range, original relpath, archive relpath, sha256, event count; required. |
| `blocker_count` | Must be zero for a future apply; required. |
| `replay_validation_ref` | Durable reference to the replay proof described by Story 83.1; required. |
| `rollback_evidence_ref` | Durable reference to backup/restore evidence described by Story 83.1; required. |
| `operator_identity` | Operator id/handle plus signing key fingerprint where applicable; required. |
| `authorized_at` | Timestamp of operator approval; required. |
| `authorization_event_ref` | Event id or equally durable audit-ledger reference for the authorization; required. |

## Future apply preflight

Immediately before any future mutation, after acquiring any future apply lock and before touching hot logs, the implementation must:

1. regenerate the lifecycle dry-run plan from current hot logs, archive manifest, safety policy version, and retention inputs;
2. compare the regenerated `plan_hash` to the authorized `plan_hash`;
3. compare affected segment identities to the authorized segment set;
4. verify the authorization evidence is present, signed/verifiable where applicable, and tied to the current operator identity;
5. verify replay and rollback evidence references still exist;
6. fail closed before mutation if any comparison or verification fails.

## Fail-closed matrix

Future apply is blocked when any of these are true:

- authorization is missing;
- authorization references a different `plan_hash`;
- dry-run artifact cannot be loaded or no longer matches the hash;
- affected segment identity changed;
- retention inputs or safety policy version changed;
- dry-run plan has blockers;
- replay validation reference is missing, stale, or failed;
- rollback evidence reference is missing or unverifiable;
- operator identity is absent, ambiguous, unsigned, or unverifiable;
- authorization is recorded outside the event spine or an equally durable audit ledger;
- apply is attempted through a dry-run boolean toggle instead of a distinct future apply surface.

## Non-goals

- No apply/prune command/API/tool is added.
- No hot log, archive, manifest, snapshot, or database mutation is added.
- No new approval event schema is implemented.
- No runtime source, package source, API route, deployment file, CI, dependency, or lockfile changes are introduced.

## Evidence

This is a docs/status contract only. It updates Phase 17 planning/docs to make future destructive lifecycle apply authorization exact-plan-hash scoped and fail-closed.
