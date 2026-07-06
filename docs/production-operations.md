# Production operations runbook and preflight contract

Story 131.1 defines a docs/status-only contract for future production operations.
It does not create a command surface, runtime event type, production audit emitter,
credential path, deployment recipe, lifecycle job, GitHub mutation path, or service
integration. Any production operation not explicitly implemented by a later story
must fail closed: stop before mutation, record the docs/status reason when useful,
and require a new approved story before retrying.

This document is the operator checklist future Stories 131.2-131.6 must satisfy
before they activate any production action. The contract is intentionally stricter
than the current implementation surface.

## Operating principles

- **No authority by documentation alone.** This document describes evidence and
  approval requirements; it does not authorize a live mutation.
- **Dry-run first.** Every future mutating or credential-bearing operation needs a
  non-mutating dry run with durable evidence before an apply/activation story can
  consume it.
- **Fail closed by default.** Missing authority, stale evidence, unknown operation
  class, unsupported target, disabled emergency state, or absent rollback evidence
  means the operation is denied before side effects.
- **Evidence before approval.** Approvals must bind to a specific preflight record,
  dry-run evidence reference, target, expiry window, rollback reference, and request
  or trace/correlation id.
- **Runtime separation.** The docs/status audit schema below is a human/status
  record shape only. It is not a runtime event emitter, event-spine schema, payload
  model, registry route, MCP tool, or command implementation.

## Operation classes

| Class | Purpose | Current Story 131.1 behavior | Minimum future gate |
|---|---|---|---|
| `read_only_diagnostics` | Inspect health, logs, replay validation, status pages, dashboards, image signatures, or existing audit/status records without changing state. | Documentation only. Existing read-only runbooks remain unchanged. | Operator self-check or peer review when the read touches sensitive metadata; no mutation, no credential creation, no target state change. |
| `credential_handling` | Provision, rotate, revoke, escrow, scope, or validate production credentials, tokens, signing keys, GitHub App installation tokens, registry credentials, or database secrets. | Deferred/fail-closed. No secret or credential material is added here. | Security owner approval, scoped target, secret-store evidence, revocation path, emergency disable state, and freshness window. |
| `github_write_activation` | Permit real issue/PR/comment/label/branch mutation through a GitHub integration for a named repository. | Deferred/fail-closed. The GitHub MCP write-enable variable remains a future operator-controlled gate, not asserted here. | Repo owner plus security/operator approval, scoped token evidence, dry-run issue/PR target proof, rollback/cleanup plan, and audit binding. |
| `deployment_change` | Change images, compose/service definitions, infrastructure, runtime topology, environment variables, database endpoints, or release channels. | Deferred/fail-closed. No deployment recipe is changed by Story 131.1. | Release owner approval, signed artifact/digest evidence, environment diff, rollback image/config reference, health-check plan, and freeze-window check. |
| `lifecycle_retention` | Apply destructive lifecycle, retention, object-storage, backup pruning, archive mutation, or scheduled cleanup policy. | Deferred/fail-closed except existing separately documented local Epic 129 `prune_hot_segment` support. Story 131.1 adds no job. | Data owner approval, dry-run manifest, restore proof, retention policy reference, backup coverage, stale-evidence rejection, and drill evidence. |
| `emergency_disable` | Disable a production operation, credential, integration, scheduler, write path, or command surface during incident response. | Contract only. No switch or command is added here. | Incident commander approval when time permits; otherwise break-glass record with post-action review, durable evidence, and fail-closed state proof. |
| `rollback_drill` | Restore from backup, reverse a deployment/config change, replay/validate state, or perform a drill without business-impacting mutation unless separately approved. | Contract only. No restore command or drill scheduler is added here. | Rollback owner approval, restore prerequisites, target identity confirmation, backup/checksum evidence, and success/failure evidence. |

Unsupported classes include broad shell access, arbitrary file deletion, unscoped
repository mutation, credential reuse outside the named target, undeclared service
changes, unbounded retention, and any operation whose target or authority cannot be
identified. Unsupported operations must be recorded as denied or deferred and must
not be partially applied.

## Required preflight fields

Every future operation record must include these fields before any later story can
consume it for activation:

| Field | Required content | Fail-closed rule |
|---|---|---|
| `operation_id` | Stable id unique to the requested operation, preferably including story/epic and UTC date. | Missing or duplicate id denies apply. |
| `operation_class` | One of the approved classes above. | Unknown or mismatched class denies apply. |
| `actor` | Human/operator, service account, or system actor requesting the action, plus contact or escalation path. | Anonymous or ambiguous actor denies apply. |
| `target_environment` | Environment such as local, staging, production, disaster-recovery, or named tenant. | Environment mismatch denies apply. |
| `target_repository_or_service` | Repository, service, database, storage bucket, credential, image digest, or command surface in scope. | Wildcard or broad target denies apply. |
| `authority_source` | Story/approval/incident/change ticket/owner policy granting permission. | Missing, expired, or unrelated authority denies apply. |
| `dry_run_evidence_ref` | Durable link/path/hash for non-mutating dry-run output. | Missing, stale, or mutable evidence denies apply. |
| `approval_ref` | Approval id, reviewer, timestamp, and approval level. | Missing approval or level mismatch denies apply. |
| `risk_summary` | Concise blast radius, data impact, customer/operator impact, and reversibility assessment. | Empty or understated risk denies apply. |
| `rollback_ref` | Rollback plan, restore plan, drill record, image/config reference, or cleanup checklist. | Missing rollback/restore evidence denies mutating apply. |
| `emergency_disable_state` | Current disable switch or manual stop state, who can invoke it, and evidence of fail-closed behavior. | Unknown disable state denies mutating apply. |
| `expiry_freshness` | Expiry timestamp and freshness window for approval, dry-run evidence, credentials, and target identity. | Expired/stale records deny apply. |
| `request_trace_correlation_id` | Request id, trace id, correlation id, or incident/change id linking logs and evidence. | Missing correlation id denies mutating apply. |
| `status` | `draft`, `dry_run_recorded`, `awaiting_approval`, `approved_for_apply`, `applied`, `rolled_back`, `denied`, `expired`, or `deferred`. | Any status other than approved-for-apply denies apply; Story 131.1 can only set docs/status states. |

Preflight records should be immutable once approved. Corrections require a new
record or an explicit supersedes link so later reviewers can see what changed.

## Dry-run evidence requirements

A dry run is valid only when it proves the intended target and proposed action
without changing production state:

1. It must not create, update, delete, rotate, revoke, deploy, schedule, push,
   comment, label, merge, prune, move, truncate, chmod, rewrite, or restore state.
2. It must capture command inputs or read-only API requests, target identity,
   selected operation class, actor, timestamp, request/trace/correlation id,
   expected diff or planned mutation, and explicit statement that no mutation
   occurred.
3. It must be durable: committed docs/status artifact, immutable CI artifact,
   signed log excerpt, checksum-bound file, or issue/change record that later
   reviewers can inspect.
4. It must bind to exactly one future apply/activation request. Reusing dry-run
   evidence for a different target, environment, credential, repository, image,
   or retention policy is denied.
5. It must expire. Default freshness is 24 hours for deployment/GitHub/credential
   operations and 7 days for read-only diagnostics unless a stricter owner policy
   applies.

Story 131.1 provides the dry-run checklist only. It does not add an apply command
or any runtime path that can consume the checklist.

## Approval levels

| Level | Applies to | Required approver/evidence |
|---|---|---|
| L0 read-only operator check | `read_only_diagnostics` with no sensitive secret material and no mutation. | Named operator records the command/output or link; peer review optional unless incident policy says otherwise. |
| L1 sensitive diagnostic | Read-only checks that expose sensitive metadata, incident state, customer data boundaries, or credential fingerprints. | Operator plus service owner or incident lead; redaction note required. |
| L2 credential control | `credential_handling` operations. | Security owner plus target service/repo owner; revocation and emergency-disable evidence required. |
| L3 GitHub write activation | `github_write_activation` for one repository/service integration. | Repo owner plus security/operator approval; scoped credential evidence and dry-run target proof required. |
| L4 deployment change | `deployment_change` for staging/production or shared infrastructure. | Release owner plus service owner; digest/config diff, rollback reference, health checks, and freeze-window evidence required. |
| L5 retention/destructive lifecycle | `lifecycle_retention` or object-storage/backup pruning. | Data owner plus operations owner; dry-run manifest, restore proof, and rollback/drill prerequisite required. |
| L6 rollback/drill | `rollback_drill`, restore, or reverse-change exercise. | Rollback owner plus affected service owner; backup/checksum/source reference and success criteria required. |
| L7 emergency disable | `emergency_disable` during incident or break-glass path. | Incident commander when available; otherwise immediate fail-closed action with post-action review, timestamp, actor, scope, and evidence. |

An approval at one level does not imply approval for a broader class. For example,
L0 diagnostics do not authorize credentials, GitHub mutation, deployment changes,
retention changes, rollback, or emergency-disable operations.

## Docs/status audit schema

The following schema is for documentation/status tracking only. It is explicitly
not a runtime production audit event, not an event type registration, not a payload
implementation, and not an emitter contract.

Required docs/status audit fields:

```yaml
- date: "YYYY-MM-DD"
  event: story-131-1-production-operations-docs-status-only
  epic: epic-131
  story: 131-1-production-operations-runbook-and-preflight-contract
  operation_id: docs-status-only-<id>
  operation_class: read_only_diagnostics | credential_handling | github_write_activation | deployment_change | lifecycle_retention | emergency_disable | rollback_drill
  actor: <human-or-system-recording-status>
  target_environment: docs-status
  target_repository_or_service: <repo-or-doc-path>
  authority_source: <story-or-approved-plan-ref>
  dry_run_evidence_ref: <non-mutating-evidence-ref-or-not-applicable-for-docs-only>
  approval_ref: <approval-or-plan-ref>
  risk_summary: <fail-closed-risk-summary>
  rollback_ref: <docs-revert-or-future-rollback-ref>
  emergency_disable_state: <disabled-or-not-applicable-with-reason>
  expiry_freshness: <date-or-policy>
  request_trace_correlation_id: <request-trace-correlation-or-story-id>
  status: done | deferred | denied | expired
  summary: >-
    Human-readable status note. Must not claim runtime production audit emission.
```

Future runtime audit emitters require their own story, event-type design, tests,
registry updates, and verification. Until then, production audit emission remains
fail-closed/deferred.

## Emergency disable evidence and fail-closed behavior

Future operations that can mutate production must identify how they stop safely.
Emergency-disable evidence must include:

- the disable mechanism or manual stop authority;
- current state: disabled, armed for a named operation, unavailable, or not
  applicable for read-only diagnostics;
- the actor allowed to invoke it and the escalation contact;
- proof that disabled/unknown state prevents mutation rather than allowing best
  effort continuation;
- post-disable verification such as read-only status, log excerpt, config diff,
  or credential revocation evidence;
- post-incident review reference when break-glass was used.

If emergency-disable state is missing, stale, ambiguous, or contradicts target
state, mutating operations must be denied before execution. Emergency disable must
prefer stopping too much over allowing an unapproved mutation.

## Rollback evidence and restore/drill prerequisites

Rollback evidence must be specific enough for a different operator to validate and
execute in a later approved story:

- deployment rollback: previous image digest, config/env diff, health-check plan,
  and verification endpoint or log query;
- GitHub mutation cleanup: target issue/PR/comment/branch identifiers, reversible
  action list, and repository owner approval;
- credential rollback: revocation/rotation plan, consumers to restart or recheck,
  and secret-store version reference;
- lifecycle/retention rollback: backup/archive manifest, checksum verification,
  restore destination, replay validation, and proof the source can be recovered;
- drill: isolated target, success criteria, stop condition, no-production-mutation
  proof unless separately approved, and a durable drill record.

A rollback or restore drill cannot rely on undocumented operator memory. If backup,
checksum, target identity, or restore permissions are unavailable, the operation
stays deferred/fail-closed.

## Docs ownership and maintenance

- Owner: production-readiness documentation owner for Epic 131 until a later story
  assigns service-specific owners.
- Reviewers: operations owner, security owner for credential/GitHub classes, data
  owner for retention/lifecycle classes, release owner for deployment classes.
- Update triggers: new production operation class, changed approval policy, changed
  rollback/restore mechanism, new command surface, new credential path, or future
  Story 131.2-131.6 activation.
- Canonical status: `_bmad-output/implementation-artifacts/sprint-status.yaml`.
  `docs/feature-status.md` remains a derivative human summary.
- This document should be kept concrete. Do not replace required fields with broad
  “TBD” headings unless the operation is explicitly marked unsupported/fail-closed.

## Future-story boundaries

- **Story 131.2 — credential readiness:** defines the static/readiness contract
  for `GITHUB_MCP_SCOPED_TOKEN` in `docs/production-credential-inventory.json`
  and enforces it with `scripts/check_production_credentials.py`. This is not
  real-write activation: credential values, real GitHub writes, deployment
  mutations, command surfaces, and runtime production audit emitters remain
  fail-closed/deferred until later approved stories.
- **Story 131.3 — GitHub write activation:** may introduce a controlled GitHub
  mutation path for a named repository only after dry-run, approval, scoped token,
  emergency-disable, rollback, and audit requirements are met. Until then, real
  GitHub writes remain fail-closed/deferred.
- **Story 131.4 — deployment change control:** may bind image/config/environment
  changes to preflight, approval, rollback, and health evidence. Until then,
  deployment mutations remain fail-closed/deferred.
- **Story 131.5 — lifecycle/retention operations:** may extend object-storage,
  archive, backup, retention, or cleanup behavior only after dry-run
  manifests, restore proof, data-owner approval, and drill evidence exist. Until
  then, lifecycle/retention jobs remain fail-closed/deferred.
- **Story 131.6 — production audit/closure evidence:** may add runtime audit event
  emitters or closure evidence only through explicit event schema, implementation,
  and tests. Until then, production audit emitters remain fail-closed/deferred.

Epic 129/PR #100 docs reconciliation is a separate/pending stream until merged; this
Story 131.1 contract does not reclassify Epic 129 support or PR #100 status.
