---
id: ADR-0021
status: accepted
date: 2026-06-08
supersedes: null
---

# ADR-0021: API Versioning Strategy

## Status

**Accepted** — 2026-06-08. The registry-api HTTP surface is versioned at `/v1` with no versioning guidelines beyond the prefix. The deferred-work item D6(7-5-6) notes that adding `response_model` to existing endpoints would break the wire contract if schemas diverge. This ADR establishes the rules for evolving the API without breaking consumers (console-cli, telegram-gateway).

## Context

The registry-api service exposes REST endpoints under the `/v1/` prefix (see [`api-contracts.md`](../api-contracts.md)). Five endpoints ship today: task creation, task fetch, operator decisions, log digest, and raw event stream. The CLI client and telegram-gateway consume these endpoints via simple URL construction.

The existing wire contract has no formal versioning rules. The deferred-work item D6(7-5-6) identifies that adding FastAPI `response_model` to existing handlers risks diverging the schema from the current wire format, which would silently break consumers that depend on field presence or shapes.

Breaking changes must be managed as the platform evolves through Phase 6 and beyond. Without explicit rules, additive improvements (new fields, new endpoints) risk unintentional breakage, and there is no decision framework for when a new major version is warranted.

## Decision

1. **Additive-only within v1.** New fields may be added to request or response payloads. Clients MUST ignore unknown fields (robustness principle). No existing field may be removed, renamed, or have its type changed within `/v1/`.

2. **New endpoints are free.** Adding new routes under `/v1/` does not require a version bump. The endpoint path itself is the namespace.

3. **v2 requires a new ADR.** Any breaking change — field removal, field rename, type change, semantic change to an existing field, or removal of an endpoint — requires a new ADR and a `/v2/` route prefix. The v1 surface continues to operate unchanged until the ADR specifies its decommission timeline.

4. **`response_model` is opt-in for existing endpoints.** New endpoints SHOULD use FastAPI `response_model` for schema documentation and OpenAPI generation. Existing endpoints MAY opt in only if the declared schema exactly matches the current wire contract. If the schema diverges (even by stripping fields that Pydantic excludes by default), the endpoint must stay without `response_model` until a versioned migration.

5. **URL-path versioning only.** The version is in the URL path (`/v1/`, `/v2/`), not in request headers (`Accept`, custom version headers) or query parameters. This matches the CLI's consumption pattern (simple URL construction) and avoids content-negotiation complexity.

## Consequences

- **Positive:** Clients that follow the robustness principle (ignore unknown fields) gain resilience to additive changes without any code changes.
- **Positive:** API evolution is predictable and self-documenting — the `/v1/` surface is frozen, new features land as new fields or new endpoints.
- **Positive:** Breaking changes are gated behind ADRs, forcing explicit acknowledgment and a migration plan before operators are disrupted.
- **Positive:** The wire contract is stable for operators running against `/v1/` — no silent breakage from schema drift.
- **Negative:** Some potentially useful changes (field rename, type narrowing) are deferred to `/v2/` even when the impact appears small. This is intentional: the cost of an accidental break far exceeds the cost of waiting for a versioned migration.
- **Negative:** `response_model` cannot be retroactively added to existing endpoints if the schema does not match the wire format. This means some endpoints remain undocumented in OpenAPI until a version bump.

## Linked artifacts

- [`api-contracts.md`](../api-contracts.md) — registry-api endpoint catalog and wire contract.
- ADR-0020 — Phase 6 gate (server execution pool, the next phase of API evolution).
- ADR-0017 — Postgres migration strategy (future backend changes may motivate `/v2/`).

— *R2d2, 2026-06-08.*
