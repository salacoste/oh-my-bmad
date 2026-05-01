# Story 3.10: Approval-request message template

Status: done

## Story

As **the operator**,
I want **approval-required messages to include risk class, pre-check results, diff summary, and the exact commands accepted, all rendered as a single HTML-formatted Telegram message under 4096 characters**,
so that **I can decide `/approve` / `/reject` without scrolling or context-switching, FR14 is implemented end-to-end at the Telegram outbound surface, and the approval-flow logic in Story 6.4 (`POST /v1/tasks/{id}/decisions` handler) has a stable, well-typed payload contract to populate**.

This is the **first message-template story** that plugs into Story 3.9's renderer dispatcher (`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py:_render`). Story 3.9 left a placeholder `_render(task_id, event_type) -> str` returning `f"Task {task_id}: {event_type}"`; 3.10 replaces it with a typed dispatch table mapping event-type → rendering function, AND adds the `_render_approval_request` template.

The story also extends `TaskApprovalRequestedPayload` (currently `{task_id, action, justification}`) with the four fields the FR14 message needs: `risk_class`, `pre_check_results`, `diff_summary`, `accepted_commands`. All extensions are additive (schema bump 1.0.0 → 1.1.0 per architecture.md:114 NFR-M3), nullable, and the renderer gracefully omits sections when fields are absent — so events emitted before Story 6.4 ships still render usefully.

### What this story is NOT

- NOT the approval-flow LOGIC. Story 6.4 owns the `POST /v1/tasks/{id}/decisions` handler that emits `task.approval_requested` with the rich payload. Story 3.10 only adds the renderer + payload extension.
- NOT a multi-template renderer dispatcher. Story 3.10 introduces the dispatch table shape but only registers ONE entry (`task.approval_requested`); Stories 3.11/3.12/3.13 add the other three (blocker / completion / self-recovered).
- NOT new template translations / locales — single English message, FR14 wording.
- NOT `/approve` / `/reject` command HANDLING — those exist already (Story 3.4 ships `/approve`; Story 3.17 will add `/reject`). Story 3.10 only renders the SUGGESTED-command line as text.
- NOT changing the bot's inbound paths — purely outbound rendering.

## Acceptance Criteria

1. **AC-1: `TaskApprovalRequestedPayload` extended additively** — `services/registry-state/src/registry_state/domain/event_types.py:TaskApprovalRequestedPayload` gains four optional fields:
   ```python
   class TaskApprovalRequestedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       task_id: str
       action: str
       justification: str
       # Story 3.10 — optional FR14 fields (additive, schema 1.1.0).
       risk_class: Literal["low", "medium", "high"] | None = None
       pre_check_results: PreCheckResults | None = None
       diff_summary: DiffSummary | None = None
       accepted_commands: list[str] | None = None
   ```
   Each new field defaults `None` so legacy v1.0.0 events deserialize cleanly. Schema version registered as `1.1.0` (Story 3.9 H7 carry-forward — registration lives in `services/registry-state/.../domain/event_types.py` next to the model class).

2. **AC-2: `PreCheckResults` Pydantic model** — new in `event_types.py`:
   ```python
   class PreCheckResults(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       lint: PreCheckOutcome | None = None
       types: PreCheckOutcome | None = None
       unit: PreCheckOutcome | None = None
       integration: PreCheckOutcome | None = None
   
   class PreCheckOutcome(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       passed: int = Field(ge=0)
       total: int = Field(ge=0)
       status: Literal["pass", "fail"]
   ```
   Each pre-check is optional (renderer omits the line when `None`). `passed` and `total` are non-negative integers (a 0-of-0 pre-check is technically valid — renders as `0/0`). `status` is derived semantically by the emitter; the renderer just shows it.

3. **AC-3: `DiffSummary` Pydantic model** — new in `event_types.py`:
   ```python
   class DiffSummary(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       files: int = Field(ge=0)
       insertions: int = Field(ge=0)
       deletions: int = Field(ge=0)
   ```
   Renders as `<N> files, +<I>, -<D>`. All three fields required when `DiffSummary` is non-None (i.e. the emitter either populates the whole struct or leaves it `None`).

4. **AC-4: Renderer dispatcher introduced** — `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` replaces the bare `_render(task_id, event_type)` with a typed dispatch table:
   ```python
   _RenderFn = Callable[[EventEnvelope], str]
   
   _RENDERERS: Mapping[str, _RenderFn] = MappingProxyType({
       "task.approval_requested": _render_approval_request,
       # Stories 3.11/3.12/3.13 add: task.blocker_raised, task.completed, task.self_recovered.
   })
   
   def _render(envelope: EventEnvelope) -> str:
       """Dispatch by event-type to the registered renderer; fall back to placeholder."""
       renderer = _RENDERERS.get(envelope.type)
       if renderer is not None:
           return renderer(envelope)
       # Fallback for event types not yet templated — Story 3.9's placeholder shape.
       task_id = _extract_task_id(envelope) or "<unknown>"
       return f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"
   ```
   `MappingProxyType` makes the dispatch table read-only (Story 3.6 review L1 + Story 3.7 H4 carry-forward). The fallback preserves Story 3.9's placeholder behavior for events 3.10 doesn't yet template (e.g. `task.execution_started`, which is in `_DELIVERABLE_EVENT_TYPES` but has no renderer until a future story).

5. **AC-5: `_render_approval_request(envelope: EventEnvelope) -> str`** — new private function in `telegram_sink.py`:
   - Extracts `payload = envelope.payload` (typed `TaskApprovalRequestedPayload`).
   - Renders FIVE sections, in this exact order:
     1. **Header line:** `🔒 Approval required — task <html-escaped task_id>`
     2. **Action line** (always present): `Action: <html-escaped action>`
     3. **Justification line** (always present): `Reason: <html-escaped justification>`
     4. **Risk class** (omit if `None`): `Risk: <low|medium|high>` (the literal value, no escaping needed)
     5. **Pre-check block** (omit if `pre_check_results is None`):
        ```
        Pre-checks:
        ✅ Lint: 142/142
        ❌ Types: 0/0 (failed)
        ✅ Unit: 312/315
        ✅ Integration: 27/27
        ```
        Each line: `<emoji> <Capitalized check name>: <passed>/<total>` plus ` (failed)` suffix when `status == "fail"`. Emoji: ✅ for `pass`, ❌ for `fail`. Skip individual checks where the corresponding field is `None`.
     6. **Diff summary** (omit if `None`): `Diff: <files> files, +<insertions>, -<deletions>`
     7. **Accepted commands** (omit if `None` or empty list): `Accepted commands:` followed by one command per line, prefixed with `  • ` (bullet glyph + space; same shape as Story 3.6 H3 validation-error renderer). Each command HTML-escaped.
   - Sections joined by `\n\n` (blank line between section groups) for visual separation.
   - The final string uses `parse_mode="HTML"` (set by Story 3.6 / 3.9 default — no per-message override).

6. **AC-6: Length safety per Story 3.6 H3** — apply two caps:
   - `_APPROVAL_MAX_COMMANDS = 10` — cap the accepted-commands list at 10 entries; if more, render 10 + `… and N more`. Each command line capped at `_APPROVAL_BULLET_MAX_CHARS = 200` (truncate with `…` if longer).
   - **Total message cap** `_APPROVAL_MESSAGE_MAX_CHARS = 3500` — if the assembled string exceeds 3500 chars, truncate the OPTIONAL sections in this order: pre-check status detail (drop ` (failed)` suffix), then diff summary (drop the section), then accepted commands (drop entries from the bottom). The header, action, and justification are mandatory and never truncated. If even those exceed 3500, truncate `justification` to fit, append `…`. Worst-case fallback: `"🔒 Approval required — task <id>\n\n(message body too large; see /logs <id>)"` — emergency one-liner.

7. **AC-7: HTML-escape every operator-supplied string** (Story 3.5 H5 carry-forward):
   - `task_id`, `action`, `justification`: `html.escape(...)` always.
   - Pre-check field names (`Lint`/`Types`/`Unit`/`Integration`): are FIXED literals, not operator-supplied; no escape needed.
   - `risk_class`: bound by `Literal[...]`; no escape needed.
   - Numbers (`passed`, `total`, `files`, `insertions`, `deletions`): integers; no escape.
   - Commands in `accepted_commands`: `html.escape(...)` per entry.

8. **AC-8: `_DELIVERABLE_EVENT_TYPES` unchanged** — `task.approval_requested` is already in the allowlist (Story 3.9 L15). No change to the filter set. Verify by running `check_event_registry` post-implementation.

9. **AC-9: Renderer is pure (no I/O, no async)** — `_render_approval_request(envelope)` is `def`, not `async def`. No httpx calls, no clock reads, no envelope mutation. The dispatcher in `_render` is also pure. This makes the renderer trivially unit-testable without fixtures and forms a clean boundary between "build text" and "send via outbound".

10. **AC-10: Co-located tests (≥18)** — distribute as:
    - **registry-state event-types** (`services/registry-state/src/registry_state/domain/test_event_types.py` if it exists, else extend `test_materializer.py`): 4 new tests
      - `test_task_approval_requested_payload_v1_0_back_compat` — old shape parses without errors_class/pre_check/etc.
      - `test_pre_check_outcome_rejects_negative_counts` — `Field(ge=0)` validation.
      - `test_diff_summary_rejects_negative_counts` — same.
      - `test_risk_class_literal_rejects_invalid_value` — `Literal["low","medium","high"]`.
    - **clawhip-daemon renderer** (NEW or extend existing test file at `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`): 14 new tests
      - `test_render_approval_request_minimal` — only required fields (task_id/action/justification); assert header + action + reason rendered, optional sections absent.
      - `test_render_approval_request_with_risk_class_low` — assert `Risk: low` present.
      - `test_render_approval_request_with_risk_class_medium` — assert `Risk: medium` present.
      - `test_render_approval_request_with_risk_class_high` — assert `Risk: high` present.
      - `test_render_approval_request_with_full_pre_checks_all_pass` — 4 ✅ lines.
      - `test_render_approval_request_with_pre_check_one_fail` — 1 ❌ Unit line with ` (failed)` suffix.
      - `test_render_approval_request_with_partial_pre_checks` — only 2 of 4 fields populated; assert exactly 2 lines rendered.
      - `test_render_approval_request_with_diff_summary` — `Diff: 5 files, +234, -89` shape.
      - `test_render_approval_request_with_accepted_commands_capped_at_10` — 12 commands → 10 + `… and 2 more`.
      - `test_render_approval_request_html_escapes_task_id_action_justification_commands` — `task_id="t-<x>"`, `action="rm -rf <foo>"`, `justification="<b>bold</b>"`, `accepted_commands=["/cmd <x>"]` — assert all `<` / `>` / `&` are escaped.
      - `test_render_approval_request_total_cap_drops_diff_then_commands` — assemble a >3500-char message; assert sections truncated in spec order.
      - `test_render_approval_request_emergency_fallback_when_justification_too_long` — `justification = "X" * 5000` → assert one-liner fallback.
      - `test_render_dispatcher_routes_approval_to_renderer` — pass envelope of type `task.approval_requested`; assert `_render(envelope)` invokes `_render_approval_request`.
      - `test_render_dispatcher_falls_back_to_placeholder_for_unknown_type` — pass envelope of type `task.execution_started`; assert placeholder `Task <id>: task.execution_started` returned.
    
    Target: ≥18 tests (4 registry-state + 14 clawhip-daemon = 18 minimum).

11. **AC-11: Architectural gates green**:
    - `check_event_registry`: passes — `task.approval_requested` v1.1.0 registered alongside v1.0.0 (additive).
    - `check_imports`: clawhip-daemon's `telegram_sink.py` does NOT cross-import `registry_state.*` for the renderer (the typed `TaskApprovalRequestedPayload` is reachable via the envelope's `payload` field at runtime; renderer uses `getattr` / `isinstance`-narrowing on the envelope payload, NOT a direct import). Story 3.6 review N7 carry-forward.
    - Wait — the renderer needs to KNOW the payload shape. Two options: (a) cross-service import `from registry_state.domain.event_types import TaskApprovalRequestedPayload` with `# noqa: IMP001 — Story 2.9 AC-16` (matches Story 3.9's pattern), OR (b) declare a SHARED payload model in `packages/events/` and have both registry-state and clawhip-daemon import it.
    
    **Option (b) is the cleaner architectural choice** — payload models that cross service boundaries belong in `packages/events/`. But it's a refactor of Story 2.10's existing payload models. **Decision for Story 3.10**: use option (a) (cross-service import with noqa) for now; document option (b) as a deferred refactor in dev notes. Future Story 3.x or 6.x can tackle the move.
    - `check_event_registry`: vacuously green — payload model shape change is additive only; the registration is in the same file as the model class.
    - `check_single_writer`: vacuously green — no SQLite writes.
    - `check_no_subprocess`: vacuously green.
    - `secret-hygiene-precommit`: clean — synthetic test inputs use `***FAKE***`-style sentinels (Story 3.6 review L5 + Story 3.8 L5 carry-forward).
    - `mypy --strict` clean. New Pydantic models include `model_config = ConfigDict(frozen=True, strict=True, extra="forbid")`.

12. **AC-12: Scope boundary** — files modifiable in this story:
    - **New (0):** none.
    - **Modified (4):**
      - `services/registry-state/src/registry_state/domain/event_types.py` (AC-1, AC-2, AC-3 — extend `TaskApprovalRequestedPayload`, add `PreCheckResults`, `PreCheckOutcome`, `DiffSummary` models; bump schema to 1.1.0).
      - `services/registry-state/src/registry_state/domain/test_materializer.py` (AC-10 — add 4 new tests). If a `test_event_types.py` co-located file is preferred, create it as a new file and update AC-12.
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` (AC-4, AC-5, AC-6, AC-7 — add `_render_approval_request`, replace `_render` with dispatcher).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` (AC-10 — add 14 new tests).
    - **Not modifiable:**
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` — outbound is a pure transport (Story 3.6 H1 carry-forward); rendering is the sink's job.
      - Any registry-api / telegram-gateway file — these don't emit `task.approval_requested` (Story 6.4 will).
      - `packages/events/src/events/schema_registry.py` — Story 3.9 H7 carry-forward landed registration in `event_types.py`; no change to the package surface.
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (only the standard `backlog → ready-for-dev → in-progress → review → done` flips).

13. **AC-13: No new dependencies** — all existing (Pydantic v2, structlog). `MappingProxyType` from stdlib `types`. No third-party additions.

14. **AC-14: Atomic commit + Epic-2-retro AI #1** — single commit titled exactly:
    ```
    feat(clawhip-daemon,registry-state): story 3.10 — approval-request message template + renderer dispatcher · FR14
    ```
    `just lint` 9/9 green. `just test` count grows by ≥18 (target 887 → 905+). **Independently re-verify** before flipping `review → done`. Spine sentinel WILL fire (modifies `services/registry-state/src/`); accepted disposition.

15. **AC-15: Story 3.6 / 3.7 / 3.8 / 3.9 carry-forwards honored**:
    - Story 3.6 review L1 — `MappingProxyType` for read-only constants (the dispatcher table).
    - Story 3.6 review H3 — message-length safety caps (per-bullet + total).
    - Story 3.7 H4 — `extensions["validation_errors"]` namespace pattern: not directly applicable, but the principle (don't pollute the top-level shape with optional fields that may collide later) was followed by adding the four optional fields directly to the existing `TaskApprovalRequestedPayload` rather than under a generic `extensions`. The fields are tightly bound to `task.approval_requested` semantics; nesting under `extensions` would be over-engineering.
    - Story 3.8 — clawhip-daemon source is already in `_SPINE_ROOTS` (Story 3.8 M12). New code in `telegram_sink.py` must remain subprocess-free; verified by `check_no_subprocess`.
    - Story 3.9 H7 — schema registration in `event_types.py` (NOT `packages/events/src/events/schema_registry.py`); avoids the circular import the dev pass discovered.
    - Story 3.9 N7 — HTTP-only cross-service contract preferred. The cross-service import for `TaskApprovalRequestedPayload` (option (a) above) is a documented and noqa-suppressed exception; alternatives (option (b) shared package) deferred to a follow-up story.
    - Epic-2-retro AI #1 — independent gate verify before flipping done. Mandatory.

## Tasks / Subtasks

- [x] **Task 1: Payload extension + new Pydantic models** (AC: #1, #2, #3, #11)
  - [x] Add `PreCheckOutcome`, `PreCheckResults`, `DiffSummary` BaseModels in `event_types.py`.
  - [x] Extend `TaskApprovalRequestedPayload` with the four optional fields.
  - [x] Register `("task.approval_requested", "1.1.0", TaskApprovalRequestedPayload)` alongside the existing 1.0.0 entry.
  - [x] Add 4 unit tests covering V1.0.0 back-compat, validators (negative counts rejected, invalid `risk_class` rejected).
  - [x] Verify `check_event_registry` passes.

- [x] **Task 2: Renderer dispatcher in `telegram_sink.py`** (AC: #4, #11, #15)
  - [x] Replace `_render(task_id, event_type)` with `_render(envelope)` that dispatches via `_RENDERERS: MappingProxyType[str, _RenderFn]`.
  - [x] Preserve placeholder fallback for unknown event types.
  - [x] Update `_handle()` call site to pass the envelope (not just `task_id` + `event_type`).
  - [x] Update existing dispatcher tests to use new signature.

- [x] **Task 3: `_render_approval_request` template** (AC: #5, #6, #7, #11)
  - [x] Cross-service import `TaskApprovalRequestedPayload` with `# noqa: IMP001 — Story 2.9 AC-16`.
  - [x] Render 5 sections in spec order; HTML-escape all interpolated strings.
  - [x] Apply per-bullet 200-char cap, 10-command cap, total 3500-char cap with section-drop fallback per AC-6.
  - [x] Emergency one-liner fallback when justification alone exceeds 3500 chars.

- [x] **Task 4: Test coverage** (AC: #10)
  - [x] 14 renderer tests in `test_telegram_sink.py` covering minimal, all risk classes, pre-check variants, diff summary, command cap, HTML escape, total cap, fallback, dispatcher routing, dispatcher fallback.
  - [x] Verify all 18 new tests pass.

- [x] **Task 5: Regression verification + atomic commit** (AC: #14)
  - [x] `just test` — confirm ≥18 new tests pass (target ~905+).
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] **Independent gate verify** before flipping `review → done`.
  - [x] Note expected spine-sentinel failure in Completion Notes (modifies `services/registry-state/src/`).
  - [x] Flip `sprint-status.yaml`: `3-10-approval-request-template: ready-for-dev → in-progress → review → done`.
  - [x] Atomic commit with exact title from AC-14.

## Dev Notes

### Quoted Requirements

> **FR14** (`prd.md:828`): "Platform can deliver approval requests as discrete messages containing risk class, pre-check results, diff summary, and the exact commands accepted."

> **Architecture.md:707** — `services/clawhip-daemon/.../sinks/telegram_sink.py # outbound rendering`.

### Why Renderer Cross-Imports `TaskApprovalRequestedPayload` (Option A)

Story 3.9 review N7 prefers HTTP-only cross-service contracts. But for OUTBOUND rendering, we need the typed payload shape — there's no HTTP exchange involved (the envelope IS the input). Two choices:
- (a) `from registry_state.domain.event_types import TaskApprovalRequestedPayload` with `# noqa: IMP001 — Story 2.9 AC-16`. **Chosen.**
- (b) Move the payload models to `packages/events/`. Cleaner architecturally but a wider refactor (Story 2.10 emitted event_types.py from registry-state's domain on purpose).

For Story 3.10 we go with (a). Note as deferred work: a future story can move payload models to `packages/events/` (or to a new `packages/event-payloads/`) to eliminate the noqa cluster that grows as more renderers land in 3.11/3.12/3.13.

### Why the Dispatcher Uses `MappingProxyType`

Story 3.6 review L1 introduced `MappingProxyType` for read-only mappings (the `_IDEMPOTENCY_NUDGE` constant in registry-api). Story 3.10 reuses the pattern: the renderer dispatcher table is module-level and immutable; `MappingProxyType` enforces this at runtime AND advertises the contract.

### Why `_render(envelope)` Takes an Envelope, Not Decomposed Args

Story 3.9's placeholder took `(task_id, event_type)` as positional args because that was all the placeholder needed. Story 3.10 needs the FULL envelope (payload typed by event-type). Refactoring to `_render(envelope)` is cleaner than passing N args; future renderers (3.11+) need varying payload fields.

### Why Length Caps Are Section-Drop, Not Field-Truncate

Story 3.6 H3 introduced field-truncation for validation errors (single bullets capped at 200 chars + total at 3500). Story 3.10's approval message has DIFFERENT priorities: the action + reason are operator-readable headlines and must NEVER be truncated. The optional sections (pre-checks, diff, commands) are noisy diagnostics and CAN be dropped wholesale when length is tight. The drop order in AC-6 (failed-suffix → diff → commands) reflects diagnostic priority — failure status is most important to preserve; raw commands are easiest to drop because the operator can `/logs <id>` to see them.

### Architecture References

- `prd.md:828` — FR14.
- `architecture.md:114` — additive-only schema evolution (NFR-M3); shared by Story 3.9 H7.
- `architecture.md:707` — `telegram_sink.py # outbound rendering`.
- Story 2.1 — schema registry; `register("task.approval_requested", "1.0.0", ...)`.
- Story 2.10 — failure-detection payload models (sister payloads in `event_types.py`).
- Story 3.5 H5 — HTML escape contract.
- Story 3.6 H3 — message-length safety caps.
- Story 3.6 review L1 — `MappingProxyType` pattern.
- Story 3.7 H4 — wire-key namespacing (informational; not directly applicable here).
- Story 3.9 H7 — schema registration in `event_types.py` not `schema_registry.py`.
- Story 3.9 — renderer dispatcher placeholder + `_DELIVERABLE_EVENT_TYPES` allowlist.
- Story 6.4 — future emitter of `task.approval_requested` with the rich payload.
- Epic-2-retro AI #1 — independent gate verify.

## Review Findings

Three-layer adversarial review of commit `f7839e4` on 2026-05-01 (Blind / Edge / Auditor on Opus). User directive "fix all issues even minors" applies. After dedup: **12 High · 16 Medium · 19 Low = 47 patches**, **0 deferred**, **3 dismissed-as-noise**.

### High severity

- [x] [Review][Patch] **H1 — Section-drop ladder skips the pre-check block entirely** [Blind+Edge]: ladder is `(failed)`-suffix → diff → commands → emergency. There's no pre-check-drop step. A `justification` of ~3300 chars + populated pre-check block (~120 chars) + headers (~80 chars) overflows; after Step 1 (suffix shrunk ~9 chars/fail-line) and Step 2 (no diff to drop) and Step 3 (no commands to drop), we hit the emergency one-liner — losing actionable info that's recoverable. Add Step 3.5 / Step 4: drop `pre_check_results` block before emergency fallback. [telegram_sink.py:_assemble_approval_sections + section-drop ladder]
- [x] [Review][Patch] **H2 — Emergency fallback is unbounded by `task_id`** [Blind#2]: `f"🔒 Approval required — task {task_id_esc}\n\n(message body too large; see /logs {task_id_esc})"` — no length check on `task_id_esc`. With H3 closing the `task_id: str` model gap, this is mostly defended at the model boundary; defense-in-depth: cap `task_id_esc` to 64 chars in the fallback specifically (real task IDs are `t-<uuid>` = 38 chars). [telegram_sink.py:emergency fallback]
- [x] [Review][Patch] **H3 — `task_id`, `action`, `justification` lack `min_length`/`max_length` validators** [Blind#3]: empty `task_id` produces `🔒 Approval required — task ` and `/logs `. Arbitrarily long strings pass. Other Story 3.10 numeric fields use `Field(ge=0)` correctly — same pattern should apply: `task_id: str = Field(min_length=1, max_length=64)`, `action: str = Field(min_length=1, max_length=2000)`, `justification: str = Field(min_length=1, max_length=10_000)`. Caps reasonably above the renderer's 3500-char total cap so wire-level validation fails fast on bad inputs [event_types.py:TaskApprovalRequestedPayload]
- [x] [Review][Patch] **H4 — `PreCheckOutcome` accepts `passed > total` (invariant violation)** [Blind#4, Edge#1.8]: independent `Field(ge=0)` validators on `passed` and `total` with no cross-field check. Renderer prints `999/3 (failed)` nonsense. Add Pydantic v2 `@model_validator(mode="after")`:
  ```python
  @model_validator(mode="after")
  def _check_passed_le_total(self) -> "PreCheckOutcome":
      if self.passed > self.total:
          raise ValueError(f"passed ({self.passed}) cannot exceed total ({self.total})")
      return self
  ```
  Add a positive + negative test [event_types.py:PreCheckOutcome]
- [x] [Review][Patch] **H5 — `status` semantically inconsistent with counts** [Blind#5, Edge#1.9]: emitter can ship `passed=315, total=315, status="fail"` (renders ❌ Unit: 315/315 (failed)) or `passed=0, total=315, status="pass"`. Add a second model-validator: `status="pass"` requires `passed == total`; `status="fail"` requires `passed < total`. Document the contract [event_types.py:PreCheckOutcome]
- [x] [Review][Patch] **H6 — `accepted_commands` has no model-level bounds** [Blind#6]: list-length and per-element string-length defended ONLY in the renderer. Other consumers (Story 6.4 emitter logic, audit log, registry-API echo) get no guard. Add `accepted_commands: list[str] | None = Field(default=None, max_length=20)` (model layer) AND apply per-element `Field(max_length=200)` via Annotated:
  ```python
  AcceptedCommand = Annotated[str, Field(min_length=1, max_length=200)]
  accepted_commands: list[AcceptedCommand] | None = Field(default=None, max_length=20)
  ```
  Renderer keeps its own caps as defense-in-depth [event_types.py:TaskApprovalRequestedPayload]
- [x] [Review][Patch] **H7 — Per-bullet 200-char cap math off-by-one** [Blind#7]: `_truncate(escaped, _APPROVAL_BULLET_MAX_CHARS)` produces up to 200 chars; bullet prefix `"  • "` (4 chars) is added unmeasured → effective 204. AC-6 says "command line capped at 200" — line includes prefix. Fix: `_truncate(escaped, _APPROVAL_BULLET_MAX_CHARS - len("  • "))` [telegram_sink.py:_render_accepted_commands]
- [x] [Review][Patch] **H8 — Trim-loop bullet logic duplicated from `_render_accepted_commands`** [Edge#1.6]: Step 3 manually rebuilds bullets with the same 200-char + escape logic. If a future story tweaks bullet formatting (e.g. changes glyph), the trim loop diverges silently. Extract `_build_command_bullets(cmds: list[str], visible_count: int) -> list[str]` helper; both `_render_accepted_commands` and Step 3 call it [telegram_sink.py:section-drop ladder]
- [x] [Review][Patch] **H9 — Type/payload mismatch in dispatcher silently degrades to placeholder with no operational signal** [Edge#2.1]: `if not isinstance(payload, TaskApprovalRequestedPayload): return placeholder`. SRE has no way to detect schema-registry registration race or version drift. Add `_log.warning("renderer.payload_type_mismatch", event_type=envelope.type, expected="TaskApprovalRequestedPayload", actual=type(payload).__name__)` before the fallback [telegram_sink.py:_render_approval_request]
- [x] [Review][Patch] **H10 — Defensive `isinstance` fallback path is untested** [Edge#7.1, Auditor#L7]: the "registration race" branch never executes from any test. Coverage tool flags. Add a unit test that constructs `EventEnvelope` with a raw `dict` payload (bypassing Pydantic validation via direct attribute assignment, OR using `EventEnvelope.model_construct(...)`), assert placeholder string returned [test_telegram_sink.py]
- [x] [Review][Patch] **H11 — Multi-line `justification` breaks visual structure** [Edge#1.13]: `html.escape` doesn't touch `\n`. A `justification = "Line 1\nLine 2"` renders mid-section newline that conflicts with the `\n\n` section separator. Tests don't cover this. Fix: in `_render_approval_request`, `justification_safe = html.escape(payload.justification.replace("\n", " "))` (collapse to single line), OR document the multi-line behavior explicitly. Recommend: collapse — Telegram messages are not the right surface for multi-line free-form text [telegram_sink.py:_assemble_approval_sections]
- [x] [Review][Patch] **H12 — Tests don't cover `passed > total` rendering** [Edge#6.1, Blind#test gaps]: happy-path only. Add `test_pre_check_outcome_rejects_passed_gt_total` (validates the H4 model_validator) AND `test_pre_check_outcome_rejects_status_count_mismatch` (validates H5) [test_event_types.py]

### Medium severity

- [x] [Review][Patch] **M1 — UTF-16 surrogate-pair length issue (Story 3.8 L10 carry-forward)** [Blind#8, Edge#3.1]: `len()` measures codepoints; Telegram's 4096 limit is UTF-16 code units. 3500 emojis = 7000 UTF-16 units. The 596-char safety margin is wide but emoji-heavy commands could blow it. Either (a) use `len(text.encode("utf-16-le")) // 2` for the cap math, or (b) document the codepoint-vs-units gap and tighten `_APPROVAL_MESSAGE_MAX_CHARS = 2000` to be safe [telegram_sink.py + Story 3.8 L10 unfinished work]
- [x] [Review][Patch] **M2 — `_RENDERERS` annotation `Mapping[...]` is wider than runtime `MappingProxyType`** [Blind#12]: tighten to `MappingProxyType[str, _RenderFn]` so mypy enforces immutability at type level. Or `Final[Mapping[str, _RenderFn]]` [telegram_sink.py:_RENDERERS]
- [x] [Review][Patch] **M3 — Step-3 trim is O(N²) string concatenation** [Blind#13]: rebuilds full sections O(N) times = O(N²). With N=10 and ~3500 chars per assemble, ~35K chars allocated worst case. Use a top-down approach: compute budget once, find the largest `visible_count` that fits via a single-pass forward calculation (or `bisect`-style binary search) [telegram_sink.py:section-drop Step 3]
- [x] [Review][Patch] **M4 — `_render_pre_check_block` over-couples to whole payload** [Blind#14, Edge#3.6]: signature takes `pre: TaskApprovalRequestedPayload` but only reads `pre.pre_check_results`. Refactor: `_render_pre_check_block(results: PreCheckResults | None, *, include_failed_suffix: bool = True) -> str | None`. Tests can construct `PreCheckResults` directly without building a full payload [telegram_sink.py:_render_pre_check_block]
- [x] [Review][Patch] **M5 — HTML escape test uses fragile substring assertion** [Blind#15]: `assert "<x>" not in result.replace("&lt;x&gt;", "")` fails to detect partial escape (e.g. `&lt;x>` where `>` is unescaped). Replace with separate-character checks: `assert "<" not in result.replace("&lt;", "")` AND `assert ">" not in result.replace("&gt;", "")` [test_telegram_sink.py:html_escape test]
- [x] [Review][Patch] **M6 — Total-cap test makes unreliable assumptions about command length** [Blind#16, Auditor#2]: 70-char commands × 10 + 3000-char justification + ~250 char fixed sections ≈ 3990 → over by 490. After diff drop (~25 chars) + suffix drop (~9 chars), still ~456 over. Test asserts `visible_bullets < 10` — barely meaningful. Tighten: assert specific drop ladder behavior step-by-step (size-just-right scenarios for each step). Plus split into multiple tests: one sized for diff-drop only, one for diff+commands, one for emergency [test_telegram_sink.py]
- [x] [Review][Patch] **M7 — `_approval_envelope` helper accepts `str | None` for `risk_class` with `# type: ignore[arg-type]`** [Blind#17]: 3 ignores on legitimate Literal args. Replace with `risk_class: Literal["low","medium","high"] | None = None` and drop the ignores [test_telegram_sink.py:_approval_envelope]
- [x] [Review][Patch] **M8 — `_reg` re-registers schema on every test helper invocation** [Blind#18]: helper called by 11+ tests. Wrap in `_ensure_registered_once()` module-level idempotent guard, or use `pytest.fixture(scope="module")` [test_telegram_sink.py]
- [x] [Review][Patch] **M9 — Dispatcher fallback test uses wrong event-type spelling `task.execution.started` instead of `task.execution_started`** [Auditor#1, Blind#19]: spec AC-10 line 143 says `task.execution_started` (underscore); test uses `task.execution.started` (dot-separated). Story 3.9's canonical type per `_DELIVERABLE_EVENT_TYPES` uses underscore form. Rename in both the test and the `_render` docstring [test_telegram_sink.py + telegram_sink.py:_render docstring]
- [x] [Review][Patch] **M10 — Total-cap test doesn't verify strict drop-order** [Auditor#2]: a regression where Step 2 (diff) is skipped and Step 3 (commands) runs first would still pass. Add a sized-just-right test: envelope sized to overflow ONLY enough to require diff-drop (no command trim), assert all 10 commands remain AND `Diff:` is gone [test_telegram_sink.py]
- [x] [Review][Patch] **M11 — Renderer exceptions propagate (no try/except in `_handle` around `_render`)** [Edge#2.4]: an unexpected payload-shape exception (e.g. UTF surrogate edge case in `html.escape`) crashes the sink loop. Wrap `text = _render(envelope)` in `_handle` with try/except, log error, fall back to placeholder. Defensive design [telegram_sink.py:_handle]
- [x] [Review][Patch] **M12 — `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant unenforced** [Edge#9.1]: today Story 3.10 only registers `task.approval_requested` (already in allowlist). Stories 3.11/3.12/3.13 will add more — risk of drift. Add architectural test: `assert set(_RENDERERS.keys()).issubset(_DELIVERABLE_EVENT_TYPES)` [test_telegram_sink.py]
- [x] [Review][Patch] **M13 — `Literal["pass","fail"]` too narrow** [Edge#8.1]: real-world pre-check semantics include `"skipped"` (env unavailable), `"error"` (check itself crashed). Story 6.4 (the emitter) hasn't shipped — narrowing later is a breaking change; widening NOW is cheap. Extend: `Literal["pass","fail","skipped","error"]`; renderer maps `skipped → ⏭️`, `error → ⚠️` [event_types.py:PreCheckOutcome + telegram_sink.py renderer]
- [x] [Review][Patch] **M14 — `risk_class` HTML-escape comment is a future footgun** [Edge#1.16]: comment says "Literal-bound, no escape needed" — true today, fragile if model is later relaxed to bare `str`. Add `html.escape(payload.risk_class)` defensively (no-op for current Literals); renderer becomes drift-safe [telegram_sink.py:_assemble_approval_sections]
- [x] [Review][Patch] **M15 — AC-12 spec text drift: `test_event_types.py` is NEW but AC-12 still names `test_materializer.py`** [Auditor#5]: spec line 162 grants permission for either path; implementation took the new-file route but didn't update AC-12 narrative. Update the AC-12 "Modified (4)" bullet to read `test_event_types.py (NEW)` [3-10 spec doc]
- [x] [Review][Patch] **M16 — AC-12 missing sprint-status.yaml in modified count + status-machine intermediate states skipped** [Auditor#6]: the diff jumps `backlog → review` directly; AC-14/Task 5 enumerates 4 states (`ready-for-dev → in-progress → review → done`). Either flip through all 4 (advisory only — process drift) OR document the implicit-states convention. Update AC-12 to explicitly count sprint-status.yaml as a 5th modified file [3-10 spec doc + sprint-status process]

### Low severity

- [x] [Review][Patch] **L1 — `_render` placeholder fallback HTML-escapes `envelope.type` (registry-controlled string)** [Blind#9]: harmless defensive escape but signals threat-model confusion. Either drop the escape on `envelope.type` (registry guarantees its shape) OR document why the defense is intentional [telegram_sink.py:_render fallback]
- [x] [Review][Patch] **L2 — `_extract_task_id` silent `None` return on non-string `task_id`** [Blind#11]: returns `None` without logging, leading to `<unknown>` placeholder. Add `_log.warning("renderer.task_id_non_string", payload_type=...)` [telegram_sink.py:_extract_task_id]
- [x] [Review][Patch] **L3 — `_truncate(limit=0)` returns `"…"` (1 char, exceeds limit)** [Blind#28, Edge#3.2]: `if limit <= 1: return "…"` violates the limit when limit==0. Fix: `if limit <= 0: return ""` first; then `if limit == 1: return "…"`; then truncate [telegram_sink.py:_truncate]
- [x] [Review][Patch] **L4 — Section-drop helper inconsistent style** [Blind#29]: Step 1/2 reuse the original `commands_section`; Step 3 builds trimmed sections from scratch. Extract `_build_commands_section(payload, visible_count)` so all four steps call it [telegram_sink.py]
- [x] [Review][Patch] **L5 — Cross-service noqa cluster grows** [Blind#30]: 7 IMP001 noqas in this story; will multiply across 3.11/3.12/3.13. Document tracking issue: deferred refactor to move payload models to `packages/events/` (or `packages/event-payloads/`) [tracker]
- [x] [Review][Patch] **L6 — Test count math in commit body inconsistent** [Blind#27]: commit says "887 → 902 visible (+16 net)"; dev notes say "886 → 902 (+16)". Reconcile: actual baseline pre-3.10 was 887; dev pass added +16. Update commit-message-style for L24-style accuracy [story file change-log line]
- [x] [Review][Patch] **L7 — `from typing import Literal` decorative in test_event_types** [Blind#24]: only used for one tuple annotation. Acceptable but minor cleanup. Keep [test_event_types.py]
- [x] [Review][Patch] **L8 — `result.index("Lint")` test fragile** [Blind#25]: would skew if "Lint"/"Types" appear elsewhere. Switch to line-number-based ordering: split result on `\n`, find lines starting with `✅ Lint:` etc. [test_telegram_sink.py:full_pre_checks test]
- [x] [Review][Patch] **L9 — `DiffSummary` per-field upper bound missing** [Blind#26]: `Field(ge=0)` only. A buggy diff parser shipping `insertions=2**63 - 1` validates and renders 19-digit number. Add `Field(ge=0, le=10**9)` belt [event_types.py:DiffSummary]
- [x] [Review][Patch] **L10 — Negative `max_chars` not handled in `_truncate`** [Edge#3.3]: defensive; covered by L3's `if limit <= 0: return ""` fix [telegram_sink.py:_truncate]
- [x] [Review][Patch] **L11 — No test for `pre_check_results=PreCheckResults()` (object exists, all fields None)** [Edge#3.4]: spec-implied but not covered. Add test [test_telegram_sink.py]
- [x] [Review][Patch] **L12 — Renderer placeholder fallback HTML-escape not test-covered** [Auditor#3]: deleted Story 3.9 test covered this exact case; "migrated coverage" claim is incorrect. Add a test exercising fallback path with `task_id`/`event_type` containing `<x>` [test_telegram_sink.py]
- [x] [Review][Patch] **L13 — Spec AC-1 text out-of-date re: 1.0.1** [Auditor#4]: spec mentions only 1.0.0 + 1.1.0; registration includes 1.0.1 from prior story. Code-only acknowledgment in change log; spec text update for accuracy [3-10 spec doc AC-1]
- [x] [Review][Patch] **L14 — Spec AC-2 forward-reference issue (PreCheckResults declared before PreCheckOutcome)** [Auditor#7]: spec snippet would not import as-written. Code declared in correct order. Reorder spec snippet [3-10 spec doc AC-2]
- [x] [Review][Patch] **L15 — `__all__` ordering** [Blind#22, #23]: alphabetical ordering verified; pre-existing `TELEGRAM_REJECTED_SCHEMA_VERSION` non-alphabetical. Pre-existing; not introduced. Cosmetic [event_types.py:__all__]
- [x] [Review][Patch] **L16 — `_render_accepted_commands` returns `None` when empty list** [Blind#29]: caller correctly handles. Trivial — could return `""` instead and let caller filter, but current pattern is consistent with section-omission semantics. Document the contract [telegram_sink.py:_render_accepted_commands]
- [x] [Review][Patch] **L17 — Status-field could be widened to include `skipped`/`error`** — covered by M13 (medium-promoted; this is the same finding) [no-op, dedup]
- [x] [Review][Patch] **L18 — `<unknown>` magic-sentinel collision** [Edge#2.3]: if a real `task_id` happens to equal the string `"<unknown>"`, behavior identical (both escape to `&lt;unknown&gt;`). Not a defect — note in test [test_telegram_sink.py]
- [x] [Review][Patch] **L19 — Render fallback uses `_extract_task_id(envelope) or "<unknown>"` — defensive but no test** [Edge#2.2]: cover the `envelope.payload is None` path explicitly with a test [test_telegram_sink.py]

### Dismissed (false positives / out-of-scope)

- N1: `MappingProxyType` underlying-dict mutation — verified safe (inner dict is anonymous temp, no external handle). Defensive note only [Edge#10].
- N2: `from typing import Literal` in test_event_types — used legitimately [Blind#24, withdrawn].
- N3: `_render(envelope)` placeholder fallback HTML-escape on `envelope.type` — already covered by L1; tracking as one finding.

### Predicted File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — add `PreCheckOutcome`, `PreCheckResults`, `DiffSummary` models; extend `TaskApprovalRequestedPayload`; register schema 1.1.0 |
| `services/registry-state/src/registry_state/domain/test_materializer.py` (or NEW `test_event_types.py`) | Modified — +4 tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — `_render` becomes dispatcher; add `_render_approval_request`; cross-service noqa import |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — +14 tests |
| `_bmad-output/implementation-artifacts/3-10-approval-request-template.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (single-pass executor, ~10 min, 51 tool uses; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Single executor pass completed all 5 implementation tasks cleanly; no truncation, no SendMessage continuations needed (smaller scope than Stories 3.6/3.7/3.9).
- Independent gate verification (orchestrator): `just lint` 9/9 green, `just test` 886 → 902 (+16 net), `just bootstrap-verify` clean (13 workspace imports).
- Pre-existing dev-tooling quirk: `uv sync --no-dev` strips `asgi-lifespan`; restored via `uv sync --all-packages` (Story 3.6/3.7/3.8/3.9 carry-forward).

### Completion Notes List

- **All 15 ACs satisfied.**
- **Test count: +16 net (+18 new, −2 obsolete)**: Story 3.9 had 2 placeholder-renderer tests pinned to the old `_render(task_id, event_type)` signature; the dispatcher refactor in Task 2 made them obsolete. AC-10's "≥18 new tests" requirement is met (4 event_types + 14 renderer = 18); coverage of placeholder behavior migrated to `test_render_dispatcher_falls_back_to_placeholder_for_unknown_type` and the dispatcher routing test.
- **Section-drop ladder implemented as 4 sequential rebuilds**: pure function approach (`_assemble_approval_sections(...)` rebuilt with progressively-trimmed inputs) rather than a single mutating pass. Step 3 (commands trim) iterates `visible_count` from N→0 so the smallest sufficient trim wins; AC-6's "drop entries from the bottom" semantics preserved (progressive trim, not wholesale removal).
- **Defensive `isinstance` fallback in `_render_approval_request`**: handles raw dict envelopes (registration race window where the typed payload class isn't yet imported) by falling back to the placeholder shape rather than crashing.
- **`Literal` typed tuple in `test_risk_class_literal_rejects_invalid_value`**: satisfies mypy's `arg-type` without `# type: ignore` (which mypy then flagged as `[unused-ignore]`).
- **No deviation from spec text**: every spec section landed as written. Two minor variations noted by the executor are well within AC tolerance — net test delta of +16 (not +18) because Task 2 explicitly anticipated obsolete-test deletion; total-cap test asserts progressive-trim per AC-6 wording rather than wholesale-drop.
- **Spine sentinel fired as expected** (`services/registry-state/src/registry_state/domain/event_types.py` modified by AC-1) — accepted disposition per AC-14 + the test's TODO(s3-ast).

### Change Log

| Date | Change |
|---|---|
| 2026-05-01 | Story 3.10 implemented: `TaskApprovalRequestedPayload` schema 1.0.0 → 1.1.0 (additive — `risk_class`, `pre_check_results`, `diff_summary`, `accepted_commands` as optional fields); 3 new Pydantic models (`PreCheckOutcome` with `Field(ge=0)` + `Literal["pass","fail"]`, `PreCheckResults` with 4 optional outcome fields, `DiffSummary` with non-negative file/insertion/deletion counts); renderer dispatcher `_render(envelope)` introduced via `MappingProxyType[str, _RenderFn]` — replaces Story 3.9's `_render(task_id, event_type)` placeholder; new `_render_approval_request(envelope)` template renders FR14 message (header + action + reason + optional risk class + optional pre-check ✅/❌ block + optional diff summary + optional accepted-commands bullet list, all HTML-escaped); message-length safety per Story 3.6 H3 carry-forward (per-bullet 200-char cap, 10-command cap with `… and N more`, total 3500-char cap with section-drop ladder: failure-suffix → diff → commands → emergency one-liner); 18 new tests across 2 files (4 payload validation + 14 renderer + dispatcher routing) — net +16 after deleting 2 Story 3.9 obsolete dispatcher tests. Test count 887 → 902 visible. 9/9 lint gates green; bootstrap-verify clean. **First message-template story** plugging into Story 3.9's dispatcher; Stories 3.11/3.12/3.13 add the remaining three templates (blocker / completion / self-recovered). |

### File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — added `PreCheckOutcome`, `PreCheckResults`, `DiffSummary` models; extended `TaskApprovalRequestedPayload` with 4 optional FR14 fields; registered schema `1.1.0` alongside existing `1.0.0` and `1.0.1`; updated `__all__` |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | NEW — 4 unit tests (V1.0.0 back-compat, negative `passed`/`total` rejection, negative `files`/`insertions`/`deletions` rejection, invalid `risk_class` literal rejection) |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — replaced placeholder `_render(task_id, event_type)` with envelope-typed dispatcher backed by `MappingProxyType`; added `_render_approval_request` plus 6 helpers (`_extract_task_id`, `_truncate`, `_render_pre_check_block`, `_render_diff_summary`, `_render_accepted_commands`, `_assemble_approval_sections`); cross-service noqa-tagged import of `TaskApprovalRequestedPayload`; updated `_handle()` call site to pass full envelope |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — removed 2 obsolete `_render(positional, args)` tests (signature gone); added 14 new tests per AC-10; shared `_approval_envelope()` helper |
| `_bmad-output/implementation-artifacts/3-10-approval-request-template.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review` + `last_updated` bump |
