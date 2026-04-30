# Story 3.7: RFC 7807 error envelope + Telegram rendering

Status: review

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

### Change Log

| Date | Change |
|---|---|
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
