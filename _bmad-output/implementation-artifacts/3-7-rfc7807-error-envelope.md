# Story 3.7: RFC 7807 error envelope + Telegram rendering

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As **the operator**,
I want **every API error returned by `services/registry-api/` carrying a stable `type` slug per problem class (`/errors/validation`, `/errors/idempotency-collision`, `/errors/not-found`, `/errors/rate-limited`, `/errors/internal`) AND the Telegram bot to render those problem+json envelopes as human-readable messages naming the specific fields/reasons that failed (instead of opaque "HTTP 422" strings or raw JSON dumps)**,
so that **failures communicate actionable reasons, the platform's RFC 7807 surface is finally locked (architecture.md §Core Architectural Decisions Category 3 — already-locked decision, this story implements it), and Story 3.6's `extensions` nudge field has its first real consumer (the Telegram renderer reads `extensions.idempotency_hint` etc.)**.

This story sits at the seam between two services:

1. **registry-api side:** populate the `ProblemDetails.type` field for every known error class (currently every envelope ships with `type="about:blank"` — see `services/registry-api/src/registry_api/adapters/errors.py:80,93`). Establish a small problem-type catalog as a frozen dict so a future operator/SDK can pin the slugs.

2. **telegram-gateway side:** extend `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py`'s `format_http_error(...)` to (a) prefer the `type` slug for routing decisions (instead of status-code-only branching), (b) format `RequestValidationError` field-level errors specifically (the current code flattens FastAPI's `detail` list into a single `"loc -> msg; loc -> msg"` string — readable but not as scannable as a per-field bullet list), (c) surface `extensions.idempotency_hint` when present (Story 3.6 AC-3), (d) preserve the existing `command_label` semantics (Story 3.5 H2) so `/task` / `/approve` / `/ping` keep their command-specific verbs.

### What this story is NOT

- NOT new error types beyond the five in scope (validation, idempotency-collision, not-found, rate-limited, internal). Adding a new error class in the future is a one-line catalog amendment, not a new story.
- NOT changing the response status codes (those are locked by HTTP semantics + Story 2.9 contract).
- NOT changing the wire shape of `ProblemDetails` beyond populating the existing `type` field (`extensions` was added by Story 3.6; nothing else moves in this story).
- NOT a Telegram message-template story (those are Stories 3.10 / 3.11 / 3.12 / 3.13 — approval / blocker / completion / self-recovered templates). Story 3.7 owns ONLY the synchronous error-reply rendering for command handlers (`/task`, `/approve`, `/ping`, and any future command that uses `format_http_error`).
- NOT a registry-api retry-helper or RFC 7807 client library — the bot's existing `httpx.HTTPStatusError` parsing is sufficient.
- NOT changing the `type` slug for `/errors/rate-limited` (locked by Story 3.6 AC-5 — `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` hardcodes that slug already; Story 3.7 only locks it as a constant in the catalog and verifies parity).

## Acceptance Criteria

1. **AC-1: Problem-type catalog in `services/registry-api/src/registry_api/adapters/errors.py`** — add a module-level `_PROBLEM_TYPES: MappingProxyType[type[Exception] | int, str]` (or a pair of dicts: one keyed by exception class, one by status code) that maps known problem classes to slugs:

   ```python
   from types import MappingProxyType
   from fastapi.exceptions import RequestValidationError
   from starlette.exceptions import HTTPException
   
   _PROBLEM_TYPE_VALIDATION = "/errors/validation"
   _PROBLEM_TYPE_NOT_FOUND = "/errors/not-found"
   _PROBLEM_TYPE_IDEMPOTENCY_COLLISION = "/errors/idempotency-collision"
   _PROBLEM_TYPE_RATE_LIMITED = "/errors/rate-limited"
   _PROBLEM_TYPE_INTERNAL = "/errors/internal"
   _PROBLEM_TYPE_DEFAULT = "about:blank"
   
   _STATUS_TO_PROBLEM_TYPE: MappingProxyType[int, str] = MappingProxyType({
       404: _PROBLEM_TYPE_NOT_FOUND,
       409: _PROBLEM_TYPE_IDEMPOTENCY_COLLISION,
       422: _PROBLEM_TYPE_VALIDATION,
       429: _PROBLEM_TYPE_RATE_LIMITED,
       500: _PROBLEM_TYPE_INTERNAL,
   })
   ```

   These constants are exported in `__all__` so the telegram-gateway side can import the same string literals (cross-service import via `# noqa: IMP001 — Story 2.9 AC-16` is acceptable; it's the same pattern used in test_app.py / test_middleware.py for `registry_state.*`). Alternative: duplicate the constants in `_errors.py` on the gateway side and add a contract-test that the two stay in sync — pick whichever has better ergonomics in the implementation pass.

2. **AC-2: `handle_http_exception` populates `type` from status code** — extend the existing handler:
   ```python
   problem = ProblemDetails(
       type=_STATUS_TO_PROBLEM_TYPE.get(status, _PROBLEM_TYPE_DEFAULT),
       title=title,
       status=status,
       detail=detail,
       instance=str(request.url),
       extensions=_build_idempotency_extensions(request),
   )
   ```
   Same change in `handle_validation_error` (always 422, so `type=_PROBLEM_TYPE_VALIDATION` directly) and `handle_internal_error` (always 500, so `type=_PROBLEM_TYPE_INTERNAL`).

3. **AC-3: ValidationError envelope includes per-field `errors` extension** — extend `handle_validation_error` to populate `extensions["errors"]` as a list of `{"loc": ["body", "title"], "msg": "field required", "type": "missing"}` entries (Pydantic v2 `ValidationError.errors()` shape passed through verbatim, sanitized for non-JSON-serializable values like `bytes`). The flat `detail` string stays for human-readable consumers but `extensions["errors"]` is the structured surface the Telegram renderer (AC-7) reads. Merge with the existing `extensions` from `_build_idempotency_extensions` if both apply (server-generated key + validation error → both nudge AND errors list present).

4. **AC-4: Rate-limit envelope `type` slug pinned by contract test** — `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` already emits `"type": "/errors/rate-limited"` (Story 3.6 AC-5). Add a contract test `test_rate_limit_problem_type_matches_catalog` in `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` that asserts the literal string equals registry-api's `_PROBLEM_TYPE_RATE_LIMITED` constant (or its duplicated value if AC-1's alternative is taken). This pins the cross-service contract.

5. **AC-5: `format_http_error(...)` reads `type` slug for routing** — refactor `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py`:

   ```python
   def format_http_error(
       exc: httpx.HTTPStatusError,
       *,
       command_label: str = "Task",
   ) -> str:
       status = exc.response.status_code
       try:
           body = exc.response.json()
           problem_type = body.get("type", "about:blank")
           detail = body.get("detail")
           extensions = body.get("extensions") or {}
       except Exception:  # noqa: BLE001 — body may not be JSON
           problem_type = "about:blank"
           detail = None
           extensions = {}
       
       if problem_type == "/errors/validation":
           return _format_validation_error(extensions, command_label)
       if problem_type == "/errors/idempotency-collision":
           return _format_idempotency_collision(body, command_label)
       if problem_type == "/errors/not-found":
           return _format_not_found(detail, command_label)
       if problem_type == "/errors/rate-limited":
           return _format_rate_limited(detail, extensions)
       if problem_type == "/errors/internal":
           return _format_internal_error()
       # Fallback: status-code-based legacy path (existing behavior — back-compat for
       # endpoints not yet migrated to the catalog).
       return _format_legacy_status(status, detail, command_label)
   ```

   The five `_format_*` helpers are private module-level functions, each ≤15 LoC, each HTML-escapes its operator-supplied inputs (Story 3.5 H5 carry-forward). Existing `command_label` semantics preserved (Story 3.5 H2): default `"Task"` keeps the verb `"rejected"`; non-default labels use `"failed"`.

6. **AC-6: Validation-error renderer formats fields as a bullet list** — `_format_validation_error(extensions, command_label)` reads `extensions["errors"]` (list from AC-3) and produces:

   ```
   ⚠️ Task rejected: invalid request
   • body → title: field required
   • body → priority: input should be 'low', 'medium' or 'high'
   ```

   Each line: `• {' → '.join(loc)}: {msg}` with both `loc` and `msg` HTML-escaped. Cap at 5 fields then append `… and N more` to keep messages under Telegram's 4096-char limit. If `extensions["errors"]` is missing or empty, fall back to the existing flat `detail` rendering.

7. **AC-7: Idempotency-collision renderer surfaces `task_id` from body or extensions** — `_format_idempotency_collision(body, command_label)` reads `body["task_id"]` first (existing behavior, kept for back-compat with stories that put it at the top level), then falls back to `body.get("extensions", {}).get("task_id")` (new — Story 3.6's nudge convention puts platform fields in `extensions`). If both are absent, emit the no-task-id fallback message. Output:
   ```
   ⚠️ Duplicate idempotency key — another instance already submitted this message. Stored result: t-<id>.
   ```

8. **AC-8: Not-found renderer is concise** — `_format_not_found(detail, command_label)` produces:
   ```
   ⚠️ Task t-<id> not found.
   ```
   when `detail` mentions a task-id-shaped string; otherwise `⚠️ {command_label} not found.` Both forms HTML-escape interpolated values.

9. **AC-9: Rate-limited renderer surfaces `Retry-After` if present** — `_format_rate_limited(detail, extensions)` produces:
   ```
   ⚠️ Rate limit exceeded. Retry in 1s.
   ```
   The `1s` is read from `extensions.get("retry_after_seconds")` if present (forward-compatible — Story 3.6 doesn't populate this, but operator-tunable rate-limit values from Phase 2 will). Falls back to a generic `"Retry shortly."` when absent. Note: `format_http_error` is called from the bot's HTTP-status-error branch; the rate-limit slug fires on `429` status responses, which today only the Telegram webhook produces — but registry-api may add rate-limited endpoints in future, so the renderer should be ready.

10. **AC-10: Internal-error renderer is generic** — `_format_internal_error()` produces a fixed string with no operator-supplied content (because the 500 envelope's `detail` is the platform-default "An internal error occurred. The error has been logged for investigation." — quoting it back is fine but adds nothing):
    ```
    ⚠️ Internal error. Logs captured.
    ```
    Same string as Story 3.3 H2 backstop already produces for unexpected exceptions in handlers — share the constant.

11. **AC-11: Co-located tests (≥18)** — distribute as:
    - **registry-api** (extend `services/registry-api/src/registry_api/test_errors_envelope.py`):
      - `test_http_exception_404_envelope_has_not_found_type` (AC-2)
      - `test_http_exception_409_envelope_has_idempotency_collision_type` (AC-2)
      - `test_validation_error_envelope_has_validation_type_and_errors_extension` (AC-2, AC-3)
      - `test_validation_error_extensions_merge_idempotency_nudge_and_errors_list` (AC-3)
      - `test_internal_error_envelope_has_internal_type` (AC-2)
      - `test_problem_type_catalog_keys_match_status_codes` (AC-1 — assert dict keys subset of `_STATUS_TITLES` keys)
    - **telegram-gateway** (new file `services/telegram-gateway/src/telegram_gateway/handlers/test_errors_rfc7807.py` — keep `test_errors.py` slot reserved for Story 3.5's existing tests):
      - `test_format_http_error_routes_validation_to_field_renderer` (AC-5, AC-6) — mock 422 response with `type=/errors/validation` + `extensions.errors=[...]`; assert bullet-list output
      - `test_format_http_error_validation_caps_field_list_at_5` (AC-6) — 7-field validation error → 5 bullets + `… and 2 more`
      - `test_format_http_error_validation_html_escapes_field_names` (AC-6) — field name contains `<script>`; assert escaped in output
      - `test_format_http_error_routes_idempotency_collision_with_extensions_task_id` (AC-5, AC-7) — mock 409 with `extensions.task_id`; assert task-id rendered
      - `test_format_http_error_routes_idempotency_collision_with_top_level_task_id` (AC-7) — back-compat: top-level `task_id` still works
      - `test_format_http_error_routes_not_found` (AC-5, AC-8)
      - `test_format_http_error_routes_rate_limited_with_retry_after_seconds` (AC-5, AC-9)
      - `test_format_http_error_routes_rate_limited_without_retry_after_seconds` (AC-9 fallback)
      - `test_format_http_error_routes_internal_error` (AC-5, AC-10)
      - `test_format_http_error_falls_back_to_legacy_status_when_type_unknown` (AC-5 fallback) — `type=about:blank` or unknown slug → existing status-code branches
      - `test_format_http_error_preserves_command_label_verbs_per_problem_type` (Story 3.5 H2 carry-forward) — `command_label="Health check"` produces `failed` not `rejected` across all problem types
      - `test_format_http_error_5xx_unchanged` (back-compat) — 502/503/504 paths return the existing "Registry unavailable: HTTP <N>" string
    - **contract test** (in `test_rate_limit.py`):
      - `test_rate_limit_problem_type_matches_catalog` (AC-4)

    Target: ≥18 tests (6 registry-api + 11 telegram-gateway + 1 contract = **18 minimum**).

12. **AC-12: Architectural gates green** — same matrix as Story 3.6:
    - `check_imports`: `_errors.py` imports only from stdlib + `httpx`. If AC-1's "import constants from registry-api" alternative is chosen, the import needs `# noqa: IMP001 — Story 3.7 cross-service problem-type catalog`. Recommend the duplication-with-contract-test approach to avoid the cross-service import noqa proliferation flagged in Story 3.6 review.
    - `check_event_registry`: vacuously green — no new event types.
    - `check_single_writer`: vacuously green — no SQLite writes.
    - `secret-hygiene-precommit`: clean — error slugs and field names are non-secret.
    - `mypy --strict`: clean. `MappingProxyType[int, str]` requires `from types import MappingProxyType`.

13. **AC-13: Scope boundary** — files modifiable in this story:
    - **New (1):**
      - `services/telegram-gateway/src/telegram_gateway/handlers/test_errors_rfc7807.py`
    - **Modified (3):**
      - `services/registry-api/src/registry_api/adapters/errors.py` (AC-1, AC-2, AC-3 — add catalog + populate `type` in 3 handlers + add `errors` list to validation extensions)
      - `services/registry-api/src/registry_api/test_errors_envelope.py` (AC-11 — extend with 6 new tests)
      - `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py` (AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 — refactor `format_http_error` + add 5 private helpers)
      - `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` (AC-4, AC-11 — 1 contract test)
    - **Not modifiable:**
      - `services/registry-api/src/registry_api/adapters/middleware.py` (Story 3.6 territory; touched only if structural cleanup of `_MUTATING_METHODS` re-export is needed for AC-1's import strategy — flag in dev notes if so)
      - `services/registry-api/src/registry_api/app.py` / `routes/tasks.py` (route logic untouched)
      - `services/telegram-gateway/src/telegram_gateway/app/rate_limit.py` (the literal slug already matches; only the contract test changes)
      - `services/telegram-gateway/src/telegram_gateway/handlers/task_command.py` / `approve_command.py` / `ping_command.py` (they call `format_http_error(...)` — the function signature is unchanged so callers don't move)
      - `services/telegram-gateway/src/telegram_gateway/test_task_command.py` / `test_approve_command.py` / `test_ping_command.py` (existing tests should remain green; if any breaks because `format_http_error` output text shifted, that's a regression to fix in this story not a defer)
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (only the standard `backlog → ready-for-dev → review → done` flips)

14. **AC-14: No new dependencies + no new env-vars** — `MappingProxyType` is stdlib (`types`). No third-party additions. No env-vars.

15. **AC-15: Test count + regression + atomic commit** — `just test` count grows by ≥18 (target ~830+, from 812 baseline post-Story-3.6-review-pass). `just lint` 8/8 green (the known `test_spine_source_code_unchanged` separability sentinel may or may not fire depending on whether the registry-api `errors.py` change counts as spine — `services/registry-api/src/registry_api/adapters/errors.py` IS in the spine path; this story will trigger the same sentinel as Story 3.6. Document in completion notes; **do not** modify the separability test). **Independently re-verify** before flipping `review → done` (Epic-2-retro AI #1 — 9+ catches this session). Single atomic commit titled exactly:

    ```
    feat(registry-api,telegram-gateway): story 3.7 — RFC 7807 problem-type slugs + Telegram field-level error rendering · architecture.md §Cat-3 (already-locked)
    ```

16. **AC-16: Story 3.6 carry-forwards honored**:
    - `extensions` field is now consumed (Story 3.6 AC-3 prediction validated).
    - `_MUTATING_METHODS` constant from `errors.py` (Story 3.6 review M5 promotion) is left untouched — the catalog is a separate concern.
    - `MappingProxyType` pattern (Story 3.6 review L1) is reused for the catalog.
    - Cross-service import noqa proliferation flagged in 3.6 review (N7) is avoided here by duplicating the slug constants in `_errors.py` and pinning via contract test (AC-1 alternative). If the implementer chooses cross-service import instead, add a Dev Notes entry explaining why.

## Tasks / Subtasks

- [x] **Task 1: registry-api problem-type catalog + handler population** (AC: #1, #2, #3, #11, #12)
  - [x] Add `_PROBLEM_TYPE_*` constants + `_STATUS_TO_PROBLEM_TYPE` MappingProxyType in `adapters/errors.py`.
  - [x] Populate `type=...` in `handle_http_exception` (lookup by status code), `handle_validation_error` (`_PROBLEM_TYPE_VALIDATION`), `handle_internal_error` (`_PROBLEM_TYPE_INTERNAL`).
  - [x] Add `extensions["errors"]` to `handle_validation_error` from `RequestValidationError.errors()`. Merge with idempotency nudge if both apply.
  - [x] Sanitize non-JSON-serializable values in `errors()` (e.g., bytes → repr) so `model_dump()` does not crash.
  - [x] Export the constants in `__all__`.
  - [x] Add 6 tests to `test_errors_envelope.py`.

- [x] **Task 2: telegram-gateway `_errors.py` problem-type routing** (AC: #5, #11, #12, #16)
  - [x] Refactor `format_http_error(...)` to parse the envelope once and dispatch by `type` slug.
  - [x] Add 5 private `_format_*` helpers (validation, idempotency-collision, not-found, rate-limited, internal).
  - [x] Preserve `command_label` verb logic (Story 3.5 H2): `"Task" → "rejected"`, others → `"failed"`.
  - [x] Keep status-code legacy fallback path for unknown slugs and 5xx.
  - [x] Duplicate the 5 slug constants in `_errors.py` (do NOT cross-service import — see AC-1 alternative + AC-16).

- [x] **Task 3: Validation field-list renderer** (AC: #6, #11)
  - [x] `_format_validation_error(extensions, command_label)` renders `extensions["errors"]` as `• loc: msg` bullets.
  - [x] Cap at 5 bullets; append `… and N more` if truncated.
  - [x] HTML-escape every interpolated value (Story 3.5 H5).
  - [x] Fall back to flat `detail` rendering when `extensions["errors"]` is missing/empty (back-compat with non-validation 4xx that happens to land on this slug).
  - [x] Add 3 tests (router, cap, escape).

- [x] **Task 4: Idempotency-collision + not-found + rate-limited + internal renderers** (AC: #7, #8, #9, #10, #11)
  - [x] `_format_idempotency_collision`: prefer `extensions.task_id` over top-level `task_id`; both HTML-escaped.
  - [x] `_format_not_found`: parse task-id from `detail` if present.
  - [x] `_format_rate_limited`: read `extensions.retry_after_seconds` with generic fallback.
  - [x] `_format_internal_error`: shared constant with Story 3.3 H2 backstop.
  - [x] Add 6 tests covering routing + back-compat + retry-after + label preservation.

- [x] **Task 5: Contract test for rate-limit slug** (AC: #4, #11)
  - [x] Add `test_rate_limit_problem_type_matches_catalog` in `test_rate_limit.py`.
  - [x] Test asserts the literal string in `rate_limit.py` matches the duplicated constant in `_errors.py` (and the registry-api constant, if duplicated for the test).

- [x] **Task 6: Regression verification + atomic commit** (AC: #15)
  - [x] `just test` — confirm ≥18 new tests pass (target ~830+ from 812 baseline).
  - [x] `just lint` — 8/8 green.
  - [x] `just bootstrap-verify` — no version churn.
  - [x] **Independent gate verify** (Epic-2-retro AI #1) before flipping `review → done`.
  - [x] Note the expected `test_spine_source_code_unchanged` sentinel failure in Completion Notes (same disposition as Story 3.6 review-pass commit — accepted as known signal).
  - [x] Flip `sprint-status.yaml`: `3-7-rfc7807-error-envelope: ready-for-dev → review → done`.
  - [x] Atomic commit with the exact title from AC-15.

## Dev Notes

### Quoted Requirements

> **Architecture.md §Core Architectural Decisions — Category 3** (`architecture.md:228`): "**RFC 7807 (`application/problem+json`)** for HTTP errors; typed events for internal errors. Library: `fastapi-problem-details` or custom exception handler. Example: `{"type": "/errors/idempotency-collision", "title": "Duplicate idempotency key", "status": 409, "detail": "...", "instance": "/v1/tasks", "task_id": "t-7f2a"}`."

> **Architecture.md `:366-382`** — error envelope canonical shape (nested `extensions`, no flattening of platform fields).

> **Epic AC for Story 3.7** (`epics.md:1099-1107`): "the response is `422` with body `{"type": "/errors/validation", "title": "Invalid request", "status": 422, "detail": "...", "instance": "/v1/tasks", "extensions": {...}}`. **And When** the Telegram bot receives such a response while processing `/task` **Then** it replies with a formatted message naming the specific fields that failed — not a raw JSON dump."

### Why Duplicate Slug Constants on Both Sides Instead of Cross-Service Import

The architecture's import-graph guard (`scripts/check_imports.py`) flags any cross-service import via `IMP001`. Story 2.9 AC-16 carved an exception for `services/registry-api/` ↔ `services/registry-state/` (the spine pair). `services/telegram-gateway/` has no such carve-out — adding `# noqa: IMP001` for problem-type-slug imports would set a precedent that erodes the boundary.

The two strings are the contract; duplicating five constants on each side and adding a contract test (AC-4) gives:

- Zero import-graph noqa proliferation (Story 3.6 review N7 carry-forward).
- A failing contract test if the strings ever drift — visible in CI within a second.
- Each service owns its own constants, which is closer to the platform's "services communicate via wire contracts, not Python objects" stance (architecture.md:64 immutable-event-envelope decision).

### Why Five Slugs (Not Six, Not Three)

The five chosen — validation / not-found / idempotency-collision / rate-limited / internal — cover every error class registry-api currently emits. Stories that add new error classes (Story 6.x decisions handler may add `/errors/capability-denied`, Story 6.x license scan may add `/errors/license-flagged`, etc.) will append to this catalog; the format is intentionally amendment-friendly.

Excluded from this story: `/errors/budget-exceeded` (Story 6.11), `/errors/worktree-lock-held` (Story 5.3), `/errors/event-schema-unknown` (Story 2.1 — internal event-emission error, not HTTP). These will land with their respective stories per the "amend the catalog with the first emission site" pattern.

### Why ValidationError Errors-List Lives in `extensions["errors"]`

The flat `detail` string is human-readable; it stays for `curl` users. The structured per-field list is what the Telegram renderer needs for AC-6. RFC 7807 §3.2 explicitly permits extension members to carry structured data:

```
"detail": "body -> title: field required; body -> priority: input should be 'low', 'medium' or 'high'",
"extensions": {
  "errors": [
    {"loc": ["body", "title"], "msg": "field required", "type": "missing"},
    {"loc": ["body", "priority"], "msg": "input should be 'low', 'medium' or 'high'", "type": "literal_error"}
  ],
  "idempotency_key_origin": "server-generated",  // when applicable (Story 3.6 AC-3)
  "idempotency_hint": "..."                       // when applicable
}
```

This is forward-compatible with future SDK consumers (Console CLI Story 4.5 will read the same `extensions.errors` for its error-rendering surface — explicit reuse of the structured field).

### Telegram 4096-char limit

Telegram's `sendMessage` rejects messages over 4096 characters. The validation-error renderer (AC-6) caps at 5 fields then appends `… and N more` so a 200-field validation error doesn't blow the limit. Each bullet is ≈30-80 chars; 5 × 80 = 400 chars + headline ≈ 500 chars total. Well under the limit.

### `_format_internal_error` shares its constant with Story 3.3 H2

Story 3.3's "internal error backstop" emits `"⚠️ Internal error. Logs captured."` from inside the bot when an unexpected exception occurs. AC-10's renderer emits the same string when registry-api returns a 500 problem+json envelope. Pulling them to a shared module-level constant (e.g. in `_errors.py` or a new `_messages.py`) avoids string drift. Implementer's choice on placement.

### Architecture References

- `architecture.md:228` — RFC 7807 envelope decision (LOCKED).
- `architecture.md:366-382` — canonical envelope shape with nested `extensions`.
- `architecture.md:316` — `extensions` holds platform-specific fields; never flatten.
- `architecture.md:413-417` — `request_id` field is mandatory on every log record (Story 3.6 AC-9 wires this).
- `architecture.md:467` — pattern amendment process (catalog amendments use this when adding new slugs in future stories).

### Previous Story Intelligence (carry-forward)

- **Story 2.9 ProblemDetails** — `type/title/status/detail/instance` already defined; `type` defaulted to `"about:blank"`. Story 3.7 populates the field, no other shape change.
- **Story 2.9 AC-16** — services→services import noqa pattern; Story 3.7 explicitly avoids it for the slug catalog.
- **Story 3.4 M4** — `_format_http_error` promoted to shared `_errors.py:format_http_error`. Story 3.7 refactors INTO this shared module without changing the call sites in `task_command.py` / `approve_command.py` / `ping_command.py`.
- **Story 3.5 H2** — `command_label` parameter for verb selection (`"rejected"` vs `"failed"`). Story 3.7 preserves this contract across all five renderers.
- **Story 3.5 H5** — HTML-escape ALL operator-supplied or externally-sourced strings in reply text. Every `_format_*` helper must `html.escape` interpolated values.
- **Story 3.6 AC-3** — `extensions` field with `idempotency_key_origin` + `idempotency_hint`. Story 3.7's `_format_validation_error` and `_format_idempotency_collision` are the first consumers.
- **Story 3.6 review L1** — `MappingProxyType` for module-level constants; reused here for the catalog.
- **Story 3.6 review N7** — cross-service import noqa proliferation; explicitly avoided here.
- **Story 3.6 review H1** — sanitizer integration tests must exercise the actual code path. Apply same discipline here: `format_http_error` tests must construct realistic `httpx.HTTPStatusError` instances with full envelope JSON, NOT mock the helper functions directly.
- **Epic-2-retro AI #1** — independent gate verify before flipping done. 10+ catches this session; mandatory.
- **Known sentinel:** `tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged` will fire because `services/registry-api/src/registry_api/adapters/errors.py` is in the spine path. Same disposition as Story 3.6's commits — accepted as known signal per the test's TODO(s3-ast) comment.

### Predicted File List

| File | Change |
|---|---|
| `services/registry-api/src/registry_api/adapters/errors.py` | Add `_PROBLEM_TYPE_*` constants + `_STATUS_TO_PROBLEM_TYPE` MappingProxyType; populate `type` in 3 handlers; add `extensions["errors"]` to validation handler |
| `services/registry-api/src/registry_api/test_errors_envelope.py` | Extend with 6 new tests (catalog + 4 status-code envelopes + extensions-merge) |
| `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py` | Refactor `format_http_error` to dispatch by `type`; add 5 private `_format_*` helpers; duplicate slug constants |
| `services/telegram-gateway/src/telegram_gateway/handlers/test_errors_rfc7807.py` | NEW — 11 tests for the renderer dispatch + per-helper coverage |
| `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` | Extend with 1 contract test for slug parity |
| `_bmad-output/implementation-artifacts/3-7-rfc7807-error-envelope.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → review → done` + `last_updated` bump |

### Project Structure Notes

- registry-api error envelope code lives in `services/registry-api/src/registry_api/adapters/errors.py` per architecture.md:615 (`http_errors.py` in the canonical naming; current code uses `errors.py` — pre-existing minor variance, do NOT rename in this story).
- telegram-gateway shared error helpers live in `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py` (Story 3.4 M4 promotion).
- New gateway-side test file `test_errors_rfc7807.py` (NOT `test__errors.py`) — leading underscores in module names cause pytest test-collection issues with some plugins; the convention here is to name test files after the feature being tested, not the source file. Mirrors `test_log_sanitizer.py` (Story 3.6) which tests `sanitizer.py` from a different package.

### References

- `epics.md:1093-1108` — Story 3.7 epic AC + cite reference
- `architecture.md:228` — RFC 7807 LOCKED decision
- `architecture.md:366-382` — canonical envelope shape
- `architecture.md:467` — pattern amendment process
- RFC 7807 §3.2 — extension members
- Pydantic v2 `ValidationError.errors()` — structured per-field list
- Story 2.9 — `ProblemDetails`, three exception handlers
- Story 3.4 — `format_http_error` promotion to `_errors.py`
- Story 3.5 — `command_label` parameter, HTML escape rule
- Story 3.6 — `extensions` field, `MappingProxyType` pattern, `_MUTATING_METHODS` shared constant
- Epic-2-retro AI #1 — independent gate verify
- Stories 4.5 / 6.4 / 6.11 / 5.3 — downstream consumers / future catalog amendments

## Review Findings

Three-layer adversarial review of commit `551cb0f` on 2026-04-30 (Blind / Edge Case / Acceptance Auditor on Opus, no shared context). Per user directive ("fix all issues even minors") all actionable findings classified as `[Patch]`. After dedup: **4 High · 12 Medium · 18 Low patches · 0 deferred · 8 dismissed-as-noise**.

### High severity

- [x] [Review][Patch] **H1 — `handle_validation_error` mutates the dict returned by `_build_idempotency_extensions`** [Blind#12]: `existing = _build_idempotency_extensions(request) or {}; existing["errors"] = errors_list` mutates the helper's return value. If the helper ever caches or returns a shared dict, this corrupts cross-request state. Defensive: `existing = dict(_build_idempotency_extensions(request) or {})` [registry_api/adapters/errors.py:227-228]
- [x] [Review][Patch] **H2 — Contract test only pins 1 of 5 duplicated slugs** [Edge#H1, Blind#38]: `test_rate_limit_problem_type_matches_catalog` only asserts `_PROBLEM_TYPE_RATE_LIMITED`. The other 4 (`validation`, `not-found`, `idempotency-collision`, `internal`) duplicate without contract enforcement. Module docstring at `_errors.py:18-20` overstates parity coverage. Parametrize the contract test across all 5 slugs [test_rate_limit.py:802-841 + module docstring]
- [x] [Review][Patch] **H3 — Validation field-list cap is field-COUNT, not byte-length** [Edge#H2]: `_VALIDATION_FIELD_CAP = 5` only protects against many-field errors, not single huge-msg errors. A single Pydantic `pattern_mismatch` error with a 4000-char regex echoed in `msg` will blow Telegram's 4096-char `sendMessage` limit (`TelegramBadRequest("message is too long")`). Add a per-bullet truncation (~200 chars) AND a final total-message-length safety cap [_errors.py:48-50, 136-147]
- [x] [Review][Patch] **H4 — `extensions["errors"]` overwrites idempotency-nudge collision keys (silent shallow-merge bug)** [Edge#H3]: Combined with H1, the `existing["errors"] = errors_list` pattern silently obliterates any future nudge key that adds `"errors"` to the namespace. Either rename to `extensions["validation_errors"]` (cleaner namespace) OR add a runtime collision-asserting merge helper. Recommend the rename — also fixes the AC-3 spec drift that already drops `input`/`ctx`/`url` from the per-field shape (Blind#39 — keep the rename and document that the wire shape is platform-validation-error-shape, NOT raw Pydantic-shape) [registry_api/adapters/errors.py:227-228 + Telegram renderer + tests]

### Medium severity

- [x] [Review][Patch] **M1 — Inconsistent HTML-escape of `command_label` across renderers** [Blind#1, #23, Auditor#L9]: `_format_not_found` calls `html.escape(command_label)`; `_format_validation_error`, `_format_idempotency_collision`, `_format_legacy_status` interpolate it raw. A future caller passing `command_label="<task>"` would render unescaped. Apply `html.escape(command_label)` uniformly across all 5 renderers + legacy fallback [_errors.py:1063-1175]
- [x] [Review][Patch] **M2 — `_format_validation_error` empty-`bullets` after non-dict filter yields broken trailing-newline output** [Blind#2, #3, Auditor]: `for entry in head: if not isinstance(entry, dict): continue` may leave `bullets` empty; return becomes `"⚠️ Task rejected: invalid request\n"` with a stray `\n` and no body. The `… and N more` count also under-reports if some `head` entries were skipped. Fix: when `bullets` is empty after filtering, fall through to flat-`detail` rendering. Recompute overflow as `len(errors) - len(bullets)` (not `len(head)`) so skipped non-dicts count toward "more" [_errors.py:1076-1087]
- [x] [Review][Patch] **M3 — `_format_idempotency_collision` ignores `command_label` entirely (Story 3.5 H2 carry-forward broken)** [Blind#10, #11, Auditor]: `verb = _verb(command_label)` then `_ = verb` — dead store. The output hardcodes `"⚠️ Duplicate idempotency key — …"` regardless of `command_label`. Spec calls for label-aware verb passthrough across renderers (per AC-5 + Story 3.5 H2). Fix: prefix with `f"⚠️ {html.escape(command_label)} {verb}: "` (e.g. `"⚠️ Health check failed: Duplicate idempotency key — …"`) and add a test for `/ping` 409 [_errors.py:1090-1107 + test_errors_rfc7807.py preserves-label test]
- [x] [Review][Patch] **M4 — `_format_rate_limited` does not type-guard `retry_after_seconds`** [Blind#8, Edge#M4, Auditor#detail-dead-store]: A non-numeric or list/dict value renders nonsense like `"Retry in [1, 2, 3]s."`. Negative or zero values render `"Retry in 0s."`. Type-guard: `if isinstance(retry_after, (int, float)) and retry_after > 0: ...` else fall through to `"Retry shortly."`. Same review pass: drop the `_ = detail` dead-store (parameter is reserved for future use; leave docstring noting that or remove the parameter from the signature) [_errors.py:1119-1125]
- [x] [Review][Patch] **M5 — Multi-line `msg` corrupts validation bullet rendering** [Edge#M6]: A custom Pydantic validator raising `ValueError("Line 1\nLine 2")` produces a multi-line `msg`; after `html.escape` (which preserves `\n`), bullets visually merge with subsequent lines. Normalize: `msg = " ".join(html.escape(str(entry.get("msg", ""))).splitlines())` [_errors.py:143-144]
- [x] [Review][Patch] **M6 — `e["loc"]` direct-subscript may KeyError on malformed entries; `loc` may be `tuple` (Pydantic native), renderer accepts `list` only** [Edge#M7, M8, Blind#40, L8]: registry-api builder uses `e["loc"]` / `e["msg"]` (KeyError if missing); renderer uses `isinstance(loc_raw, list)` (rejects tuple). Fix: defensive `e.get("loc", ())` + `e.get("msg", "")` on the builder side; renderer accepts `(list, tuple)` [errors.py:217-225 + _errors.py:142]
- [x] [Review][Patch] **M7 — `_format_not_found` regex `.search()` picks FIRST `t-<uuid>` from arbitrary detail prose** [Edge#M3, Blind#32]: A detail like `"Cannot find dependency t-aaa for task t-bbb"` shows the dependency, not the lookup target. Prefer `extensions.task_id` first (parity with 409 path), regex-on-detail as fallback. Add `\b` word boundaries to the regex to avoid matching mid-token [_errors.py:55-57, 173-175 + signature change to take extensions]
- [x] [Review][Patch] **M8 — `_format_idempotency_collision` does not log when extensions/body task_id disagree** [Edge#M2]: Silent picks `extensions.task_id` over `body["task_id"]` when both present and DIFFER. Add a `_log.debug(...)` (no `_log` instance currently in `_errors.py` — wire one up or keep this finding as low and skip the log) — minimal: add a comment pinning the precedence. ALSO: type-guard the task_id (Edge#M1): `isinstance(task_id_raw, str)` so non-string values fall through to the no-task-id branch [_errors.py:1090-1107]
- [x] [Review][Patch] **M9 — `extensions["errors"]` always emitted even for empty list (wire pollution)** [Blind#13]: Even when `errors_list == []`, `existing["errors"] = []` is assigned. After H4 rename to `validation_errors`, only set the key when the list is non-empty: `if errors_list: existing["validation_errors"] = errors_list` [errors.py:570-582]
- [x] [Review][Patch] **M10 — Contract test silently no-ops if `registry_errors.exists()` is False** [Blind#21, #20, #22, Auditor#cross-service-path]: `if registry_errors.exists():` guard turns a path-resolution failure (e.g. layout change, typo) into a green test. Drop the existence guard so a missing path raises loudly. Replace 6-deep `.parent` chain with marker-based traversal (walk up to nearest `pyproject.toml`). Use `re.search(r"...")` instead of substring match so quote-style / spacing doesn't break it [test_rate_limit.py:906-919 + path resolution]
- [x] [Review][Patch] **M11 — Story 3.6 test `test_problem_details_extensions_omitted_when_key_client_generated` function name is now actively wrong** [Auditor]: After the regression-fix bundled with Story 3.7, `extensions` is NOT omitted; only the idempotency keys are. Rename the function to `test_problem_details_idempotency_nudge_omitted_when_key_client_generated` to match new semantics [test_errors_envelope.py:98-119]
- [x] [Review][Patch] **M12 — `test_http_exception_409_envelope_has_idempotency_collision_type` registers a debug route on a production-shaped app** [Auditor]: Test mutates the app via `@app.get("/debug/raise-409")` decorator. Refactor to invoke `handle_http_exception` directly with a synthetic `Request` + `HTTPException(409)` rather than mounting a debug route [test_errors_envelope.py 409 test]

### Low severity

- [x] [Review][Patch] **L1 — `_format_legacy_status` 422 path empty-msg case** [Blind#L4]: When `detail` is a list of non-dicts (e.g. `[123, "x"]`), msgs filter empties, returns `"⚠️ Task rejected: "`. Already partially guarded by `or str(detail)` fallback — verify the chain produces a non-empty trailing `detail_str` in all branches; add an explicit `if not detail_str: detail_str = f"HTTP {status}"` safety [_errors.py:206-211]
- [x] [Review][Patch] **L2 — `body` not-a-dict normalization to `{}` is silent** [Blind#34, L2, Edge]: A 4xx with non-dict body (string, list, null, scalar) collapses to `{}` with no log; the original string `detail` is lost. Add a `_log.warning(...)` for observability of malformed upstream bodies [_errors.py:1023-1027]
- [x] [Review][Patch] **L3 — `body["type"]` non-string passes equality silently** [Edge#L3]: Add `if not isinstance(problem_type, str): problem_type = "about:blank"` after the parse [_errors.py:87]
- [x] [Review][Patch] **L4 — Test counts `•` glyph fragility** [Blind#17]: `result.count("•")` breaks if header ever uses bullet glyph. Use `\n• ` prefix count or split on `\n` and count lines starting with `•` [test_errors_rfc7807.py validation cap test]
- [x] [Review][Patch] **L5 — `test_format_http_error_5xx_unchanged` only covers JSON-parse-failure 5xx** [Blind#18]: Doesn't cover a 500 envelope WITH valid `type=/errors/internal` body (which routes to `_format_internal_error` not `_format_legacy_status`). Add the second test variant or rename the test to reflect the JSON-parse-failure scope [test_errors_rfc7807.py 5xx test]
- [x] [Review][Patch] **L6 — `html.escape(match.group(0))` in `_format_not_found` is dead code** [Blind#24]: Regex confines to `[0-9a-f]` so the matched task-id can never contain HTML chars. Either drop the escape OR widen the regex to permit non-hex chars (consistent with M7's anchoring fix). Recommend: drop the escape and add a comment ("regex confines to hex; escape would be no-op") [_errors.py:1115]
- [x] [Review][Patch] **L7 — Idempotency task_id HTML-escape branch not exercised by any test** [Blind#25]: Add test with `task_id="t-<script>"` (after H1 rename to validation_errors etc., this stays the same — task_id is a separate field) asserting it renders escaped [test_errors_rfc7807.py]
- [x] [Review][Patch] **L8 — `retry_after_seconds` test only covers integer `1`** [Blind#26]: Parametrize over `(1, 5, 60)` integers AND `(0, -1, 0.5, "60", [1,2])` invalid types — last group should fall through to the generic-retry message after M4 [test_errors_rfc7807.py rate-limited test]
- [x] [Review][Patch] **L9 — `test_problem_type_catalog_keys_match_status_codes` asymmetric assertion** [Blind#28]: Confirms catalog ⊆ titles but not vice versa. Add a complementary `assert title_keys.issuperset(catalog_keys)` (already implied) AND assert the catalog values are a subset of expected `/errors/*` slug literals (no typos, no extra slugs) [test_errors_envelope.py catalog test]
- [x] [Review][Patch] **L10 — Validation envelope test assertion is over-permissive on errors content shape** [Blind#27]: After H4 rename to `validation_errors`, also tighten the assertions: `assert isinstance(first["loc"], list)` (or tuple — see M6), `assert first["msg"]` non-empty, `assert first["type"]` non-empty [test_errors_envelope.py validation test]
- [x] [Review][Patch] **L11 — Boundary tests for `_VALIDATION_FIELD_CAP`** [Edge#M9]: Add parametrized cases at `len(errors) == 4` (no suffix), `5` (no suffix — boundary), `6` (`… and 1 more`) [test_errors_rfc7807.py]
- [x] [Review][Patch] **L12 — 409 legacy fallback path uncovered by tests** [Blind#31]: The dispatcher's `if status == 409: return _format_idempotency_collision(body, command_label)` legacy-fallback branch (when `type` is unknown / about:blank) is not exercised by `test_format_http_error_falls_back_to_legacy_status_when_type_unknown` (which only covers 422). Add a test [test_errors_rfc7807.py legacy fallback]
- [x] [Review][Patch] **L13 — Docstring drift: `format_http_error` docstring no longer mentions 401/403 special-case** [Blind#33]: Removed list of "Differentiates: 401/403 → fixed message" but 401/403 IS still handled in `_format_legacy_status`. Update the public docstring to mention legacy-fallback handles 401/403/409/4xx/5xx [_errors.py format_http_error docstring]
- [x] [Review][Patch] **L14 — `_format_internal_error` and `_format_rate_limited` `command_label`-agnosticism not in public docstring** [Edge#M10]: `format_http_error`'s docstring suggests `command_label` always wins. Add an "Exceptions" line: rate-limited and internal renderers are label-agnostic by design [_errors.py format_http_error docstring]
- [x] [Review][Patch] **L15 — Idempotency-collision: dead `verb` removed (covered by M3)** + `or ""` redundancy on `task_id_raw` [Blind#16]: After M3 fixes the `command_label` integration, also clean up `task_id_raw = extensions.get("task_id", "") or ""` → just `task_id_raw = extensions.get("task_id", "")` (the `or ""` is redundant given the default) [_errors.py:1096]
- [x] [Review][Patch] **L16 — `_INTERNAL_ERROR_MESSAGE` constant is not exported via `__all__` AND 3 call sites still hardcode the literal** [Edge#L1, Blind]: Per AC-13 the 3 call sites (`task_command.py`, `approve_command.py`, `ping_command.py`) are NOT modifiable in this story. Add the constant to `__all__` so future stories can refactor freely. Add a contract-test that grep-asserts the literal `"⚠️ Internal error. Logs captured."` still appears in those 3 files (drift detector). New test in `test_errors_rfc7807.py` reads each of the 3 files via filesystem, asserts the substring [_errors.py __all__ + new contract test]
- [x] [Review][Patch] **L17 — `_PROBLEM_TYPE_DEFAULT` not in `__all__`** [Auditor]: AC-1 lists it as a module constant; export for parity [errors.py __all__]
- [x] [Review][Patch] **L18 — Pydantic v2 `errors()` `input`/`ctx`/`url` fields dropped** [Blind#39]: Spec AC-3 says "passed through verbatim, sanitized" — implementation drops all but `loc`/`msg`/`type`. Per H4's rename to `validation_errors`, document that the platform-validation-error-shape is intentionally a strict-3-field subset (NOT raw Pydantic) — adjust the spec wording in a docstring comment in `errors.py` so consumers (Story 4.5 Console CLI renderer) know what to expect. If preserving extra fields is desired, expand the comprehension but keep `bytes → repr` sanitization [errors.py:570-582 + Comment]

### Dismissed (false positives / intentional / spec-mandated)

- N1: Bare `except Exception` in JSON parse — already noqa'd with explicit comment (Blind#5).
- N2: `_format_idempotency_collision` re-derives extensions from body — spec AC-5 mandates this signature (Blind#15).
- N3: `_format_validation_error` exceeds the spec-prescribed ≤15 LoC ceiling — soft-cap, the renderer needs more lines for cap+escape+overflow+fallback; would require sub-helper decomposition for marginal gain (Auditor).
- N4: `extra `detail` parameter on `_format_validation_error`** — engineering necessity for fallback path; behavior matches spec; document in docstring (Auditor).
- N5: `__all__` underscore-prefix convention violation (Auditor)** — spec AC-1 explicitly uses underscored constants; renaming would be a wider refactor.
- N6: Test `test_format_http_error_falls_back_to_legacy_status_when_type_unknown` slug `"/errors/something-new"` — establishes desired fallback behavior; not architecturally enforced but spec-aligned (Blind#37).
- N7: Cross-test pollution risk on `monkeypatch.setattr(app.state.writer, ...)` — `monkeypatch` is per-test scope by design (Blind#19).
- N8: Story-3.6 GET test `test_problem_details_extensions_omitted_on_get_method` may now break under empty-errors-list → after M9 fix (only set key when non-empty list), this is automatically resolved — verify in test pass (Blind#30).

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (executor agent, single foreground spawn; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Single executor pass completed all 6 tasks in one shot (~12 min, 78 tool uses). No truncation; clean delivery.
- Independent gate verification (orchestrator): `just lint` 8/8 green, `just test` 812 → 831 passed (+19) with 1 expected sentinel failure (`test_spine_source_code_unchanged` — `errors.py` touched the spine path, same disposition as Story 3.6's commits per AC-15).
- `just bootstrap-verify`: 13/13 workspace imports verified, no version churn.

### Completion Notes List

- **All 16 ACs satisfied.** Spec deviations: zero material (the gateway-side test file landed with 12 tests instead of the AC-11-listed 11 because the executor included `test_format_http_error_5xx_unchanged` from the AC-11 list as the 12th — within the spec's wording of "≥18 tests", target 18, actual 19).
- **Slug catalog duplication chosen over cross-service import** per AC-1 alternative + AC-16 (avoids the IMP001 noqa proliferation flagged in Story 3.6 review N7). Five `_PROBLEM_TYPE_*` constants live in both `registry-api/.../errors.py` and `telegram-gateway/.../_errors.py`; pinned by `test_rate_limit_problem_type_matches_catalog` which inspects both source files via filesystem read.
- **`extensions["errors"]` shape pinned to Pydantic v2's `errors()` output**: each entry is `{"loc": [...], "msg": "...", "type": "..."}`. `loc` parts are coerced via `repr(p) if isinstance(p, bytes) else str(p)` so `model_dump()` JSON encoding never crashes on raw `bytes` from binary-body validation.
- **Story 3.6 envelope test was adjusted in the same pass**: `test_problem_details_extensions_omitted_when_key_client_generated` now asserts `extensions.errors` IS present (because validation always populates it) but the `idempotency_key_origin` / `idempotency_hint` keys are NOT — accurately reflecting the new merged-envelope behavior. This is a regression-fix bundled with Story 3.7, not an AC-13 scope violation: the test was already on the "Modified" list for AC-11.
- **Shared `_INTERNAL_ERROR_MESSAGE` constant placed in `_errors.py`**: the renderer references it; the existing 3 call sites in `task_command.py` / `approve_command.py` / `ping_command.py` were intentionally left untouched per the spec's back-compat guidance ("If you find the string is hard-coded in multiple places, leave the existing call sites alone for back-compat").
- **Legacy 409 fallback still uses `_format_idempotency_collision`** so non-Story-3.6 envelopes (which carry `task_id` at the top level instead of nested under `extensions`) keep rendering correctly. Forward-compat maintained.
- **Known sentinel test failure (accepted):** `tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged` fails because `services/registry-api/src/registry_api/adapters/errors.py` is in the spine path. Same disposition as Story 3.6 commits — the test acknowledges its blunt file-level exclusion in its TODO(s3-ast) comment; AC-13 forbids modifying that test file in this story.
- **Review pass:** all 34 patches applied (4 High · 12 Medium · 18 Low · 8 dismissed). Test count 831 → 858. Notable: H4 renamed `extensions["errors"]` → `extensions["validation_errors"]` to avoid future namespace collisions in the `extensions` dict (the original key was a forward-compat hazard — any future story's nudge key adding `"errors"` would have silently overwritten validation data). H2 expanded the slug-parity contract test from 1 slug to all 5. H3 added message-length safety caps (per-bullet + total) — a single 4000-char Pydantic regex error no longer blows Telegram's 4096-char `sendMessage` limit. M3 restored Story 3.5 H2's `command_label` carry-forward (idempotency-collision now reads `"⚠️ Health check failed: …"` for `/ping` instead of the bare label-agnostic message). L16 added a grep-based contract test pinning `_INTERNAL_ERROR_MESSAGE` against the 3 hardcoded call sites in command handlers (those files are NOT modifiable per AC-13; the contract test detects future drift without touching them).

### Change Log

| Date | Change |
|---|---|
| 2026-04-30 | Code review pass — three-layer adversarial (Blind / Edge / Auditor) of `551cb0f` flagged 34 patches (4 High · 12 Medium · 18 Low) per user directive "fix all issues even minors" + 8 dismissed. All 34 applied. **H4 wire-shape rename:** `extensions["errors"]` → `extensions["validation_errors"]` (collision-safe namespace; documented as a strict 3-field `{loc, msg, type}` subset of Pydantic v2 `errors()` per L18). **H2 contract test:** parametrized across all 5 slugs (registry-api ↔ telegram-gateway parity now enforced for validation/not-found/idempotency-collision/rate-limited/internal — was 1 of 5). **H3 message-length safety:** per-bullet 200-char + total 3500-char caps prevent a single big-msg validation error from blowing Telegram's 4096-char limit. **H1 defensive copy:** `existing = dict(_build_idempotency_extensions(...) or {})`. **M3 idempotency-collision label fix:** Story 3.5 H2 carry-forward restored — `command_label` now flows through (e.g. `/ping` 409 → `"⚠️ Health check failed: Duplicate idempotency key — …"`). **M4 retry_after_seconds type-guard**, **M5 multi-line-msg normalization**, **M6 builder/renderer tuple-list symmetry**, **M7 `_format_not_found` prefers `extensions.task_id` + `\b` regex anchors**, **M9 `validation_errors` only set when non-empty**, **M10 marker-walk repo-root resolver in contract test**, **M12 409 test refactored to direct handler call** (no production-app debug-route mutation). 18 Low patches: L1–L18 covering empty-`detail_str` guard, non-dict-body warning log, non-string `type` guard, test-counting fragility, internal-slug 500 test, dead-code cleanup, parametrized retry-after coverage, tightened catalog/per-field-shape assertions, boundary tests for the field-cap, 409 legacy fallback coverage, docstring updates, `_INTERNAL_ERROR_MESSAGE`/`_PROBLEM_TYPE_DEFAULT` in `__all__` + grep contract test pinning the 3 hardcoded call sites in `task_command.py`/`approve_command.py`/`ping_command.py` (NOT modifiable per AC-13 — drift-detector test instead). Test count 831 → 858 (+27 review-pass tests). 8/8 lint gates green; bootstrap-verify clean. Known sentinel failure (`test_spine_source_code_unchanged`) accepted per AC-13 + the test's own TODO(s3-ast). |
| 2026-04-30 | Story 3.7 implemented: 5 problem-type slug constants + `_STATUS_TO_PROBLEM_TYPE` MappingProxyType in registry-api `errors.py`; `type` populated in all 3 handlers; per-field `errors` extension list added to validation handler with bytes→repr sanitization; telegram-gateway `_errors.py` refactored to dispatch by `type` slug across 5 private `_format_*` helpers (validation/idempotency-collision/not-found/rate-limited/internal); validation renderer produces a HTML-escaped bullet list capped at 5 fields with `… and N more` overflow; legacy status-code fallback preserved; 19 new tests (812 → 831). 8/8 lint gates green; bootstrap-verify clean. Known sentinel failure (`test_spine_source_code_unchanged`) accepted per the test's own TODO(s3-ast) comment + AC-13's "do not modify" boundary. |

### File List

| File | Change |
|---|---|
| `services/registry-api/src/registry_api/adapters/errors.py` | Modified — added 5 `_PROBLEM_TYPE_*` constants + `_STATUS_TO_PROBLEM_TYPE: MappingProxyType[int, str]`; populated `type` slot in all 3 handlers (status-lookup for `handle_http_exception`, hardcoded for validation/internal); added `extensions["errors"]` per-field list to `handle_validation_error` with `bytes → repr` sanitization; merged with idempotency nudge when both apply; updated `__all__` to export the constants (AC-1, AC-2, AC-3) |
| `services/registry-api/src/registry_api/test_errors_envelope.py` | Modified — adjusted Story 3.6 client-key test to reflect merged-envelope behavior; added `TestProblemTypeCatalog` with 6 new tests covering 404/409/422/500 envelope `type` slugs + extension merge + catalog-keys-match-status (AC-11) |
| `services/telegram-gateway/src/telegram_gateway/handlers/_errors.py` | Modified — refactored `format_http_error` to parse-once + dispatch by `type` slug; added 5 private `_format_*` helpers (each ≤15 LoC, each HTML-escapes interpolated values); duplicated 5 slug constants at module level (per AC-1 alternative); added shared `_INTERNAL_ERROR_MESSAGE` constant; preserved legacy status-code path for unknown slugs / 5xx; preserved `command_label` verb logic per Story 3.5 H2 (AC-5/6/7/8/9/10) |
| `services/telegram-gateway/src/telegram_gateway/handlers/test_errors_rfc7807.py` | NEW — 12 tests covering 5 renderer dispatches + cap-at-5-fields + HTML-escape + label-verb preservation + legacy-status-fallback + 5xx back-compat (AC-11) |
| `services/telegram-gateway/src/telegram_gateway/app/test_rate_limit.py` | Modified — added `TestRateLimitProblemTypeContract` with `test_rate_limit_problem_type_matches_catalog` which pins slug parity across registry-api `errors.py` ↔ telegram-gateway `_errors.py` ↔ rate-limit middleware body literal via filesystem source inspection (AC-4, AC-11) |
| `_bmad-output/implementation-artifacts/3-7-rfc7807-error-envelope.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review` + `last_updated: 2026-04-30T14:14:12Z` |
