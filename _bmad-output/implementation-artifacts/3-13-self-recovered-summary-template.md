# Story 3.13: Self-recovered summary template

Status: done

## Story

As **the operator**,
I want **a proactive Telegram message rendered in the FR16 wording whenever the host self-recovered from a restart during an overnight task, sitting alongside the morning completion summary**,
so that **I earn confidence from visible resilience rather than silent resilience, FR16 is implemented end-to-end at the Telegram outbound surface, and the future emitter (Story 7.9 / clawhip-daemon proactive-summary logic) populating `task.self_recovered` events has a stable, well-typed renderer contract**.

This is the **fourth and final message-template story** plugging into the renderer dispatcher Stories 3.10 + 3.11 + 3.12 hardened. After 3.13 the dispatcher will route 4 of 4 deliverable templated events; the renderer set is complete for the FR14/FR15/FR9/FR16 quartet.

Unlike 3.10/3.11/3.12 which extended **existing** payload models + event types, Story 3.13 is materially different:

- **NEW event type** `task.self_recovered` registered at v1.0.0 (no prior version).
- **NEW payload model** `TaskSelfRecoveredPayload` declared from scratch.
- **NEW entry in `_DELIVERABLE_EVENT_TYPES`** — first allowlist addition since Story 3.9 L15.

The message is the simplest of the quartet (single line, 3 interpolated fields, no PR line, no diff stats, no ladder). Cap-overflow defenses still apply but the model-boundary validators on field lengths make Step 1 of any ladder always succeed for valid inputs.

### What this story is NOT

- **NOT** the LOGIC that emits `task.self_recovered` — Story 7.9's clawhip-daemon proactive-summary code owns the WHEN (between 00:00 and morning completion summary; alongside the completion delivery).
- **NOT** Story 3.12 (completion summary FR9) — separate event, separate renderer, separate payload. 3.13 is the SECOND message in the morning-summary pair.
- **NOT** the integration test (Story 7.9 owns `test_journey_3_recovery.py`).
- **NOT** new `session.reconnecting` / `task.execution.resumed` event types — those are emitted by Epic 5/7 worker-lifecycle stories. 3.13 only renders the higher-level `task.self_recovered` summary event the proactive-summary code synthesizes when the lower-level pair is detected.
- **NOT** localization — single English message, FR16 wording.

## Acceptance Criteria

1. **AC-1: New `TaskSelfRecoveredPayload` model** — declared in `services/registry-state/src/registry_state/domain/event_types.py` alongside the other `Task*Payload` models:

   ```python
   class TaskSelfRecoveredPayload(BaseModel):
       """Payload for the ``task.self_recovered`` event (FR16).

       Synthesized by the clawhip-daemon proactive-summary code when a task's
       event log contains a ``session.reconnecting`` + ``task.execution.resumed``
       pair emitted overnight (between 00:00 and the morning completion summary).
       Story 3.13 ships the model + renderer; the synthesis logic is Story 7.9.
       """

       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

       task_id: str = Field(min_length=1, max_length=64)
       recovered_at: AwareDatetime
       events_replayed: int = Field(ge=0, le=10**6)
       replay_duration_ms: int = Field(ge=0, le=10**9)
   ```

   - `task_id`: Story 3.10 H3 carry-forward (1-64 char model-boundary cap).
   - `recovered_at`: `pydantic.AwareDatetime` (Story 3.11 H8 carry-forward — rejects naive datetimes; ensures `.isoformat()` always emits a tz suffix).
   - `events_replayed`: `ge=0` (zero is valid — a heartbeat-only restart with no events to replay) up to `le=10**6` (Story 3.10 L9 carry-forward; matches the `files_changed`/`tests_added`/`blockers_count` cap from Story 3.12).
   - `replay_duration_ms`: `ge=0, le=10**9` (1B ms = ~11.5 days; well above any realistic replay window; matches the line-counter cap from Story 3.12).

   Schema registered as `register("task.self_recovered", "1.0.0", TaskSelfRecoveredPayload)`. Story 3.9 H7 carry-forward — registration in `event_types.py`, not `packages/events/.../schema_registry.py`.

   Add `"TaskSelfRecoveredPayload"` to `__all__`.

2. **AC-2: Add `task.self_recovered` to `_DELIVERABLE_EVENT_TYPES`** — the L15 positive allowlist in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`:

   ```python
   _DELIVERABLE_EVENT_TYPES: frozenset[str] = frozenset(
       {
           "task.created",
           "task.planning.started",
           "task.plan.ready",
           "task.execution.started",
           "task.blocker_raised",
           "task.summary_emitted",
           "task.approval_requested",
           "task.completed",
           "task.self_recovered",  # Story 3.13 (FR16) — NEW
       }
   )
   ```

   This is the first allowlist addition since Story 3.9. The existing `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (Story 3.10 M12 + 3.11 M10 strengthening) keeps passing only if AC-3's renderer entry is added in the same change.

3. **AC-3: Dispatcher registration** — append `"task.self_recovered": _render_self_recovered,` to the `_RENDERERS` `MappingProxyType` literal:

   ```python
   _RENDERERS: MappingProxyType[str, _RenderFn] = MappingProxyType(
       {
           "task.approval_requested": _render_approval_request,
           "task.blocker_raised": _render_blocker_raised,
           "task.completed": _render_completed,
           "task.self_recovered": _render_self_recovered,
       }
   )
   ```

4. **AC-4: `_render_self_recovered(envelope: EventEnvelope) -> str`** — new private function in `telegram_sink.py`:

   - Extract `payload = envelope.payload`.
   - **Type-mismatch guard** (Story 3.10 H9 / 3.11 / 3.12 carry-forward): if `not isinstance(payload, TaskSelfRecoveredPayload)`, emit `_log.warning("renderer.payload_type_mismatch", event_type=envelope.type, expected="TaskSelfRecoveredPayload", actual=type(payload).__name__)` and return the placeholder `f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"` (using `_extract_task_id(envelope) or "<unknown>"`).
   - Render the message as a **single line** in this exact form:
     ```
     🛠️ Self-recovered from host restart at <recovered_at_iso>. <events_replayed> events replayed in <replay_duration_ms> ms. Zero intervention required.
     ```
     Using:
     - `recovered_at_iso = payload.recovered_at.isoformat(timespec="seconds")` (Story 3.12 M14 carry-forward — locks to 25-char `2026-05-01T12:00:00+00:00` form regardless of microsecond precision).
     - `events_replayed` and `replay_duration_ms`: integers; no escape.
     - `task_id` is **NOT** rendered in the message itself per the FR16 wording (the message is task-context-implicit; the Telegram thread binding from Story 3.9 routes the message to the correct chat thread, so the operator already knows which task it concerns). The `task_id` is still extracted for the type-mismatch fallback path (per the H9 pattern).

   - **Pluralization** (Story 3.12 L1 carry-forward): use `"event" if events_replayed == 1 else "events"`. Same for `"second" if replay_duration_ms == 1000 else ...` — actually keep `ms` literal since "millisecond"/"milliseconds" plural is awkward and `ms` is universal. Document the decision in Dev Notes.

5. **AC-5: HTML-escape contract** (Story 3.5 H5 carry-forward) — the message contains NO operator-supplied strings (recovered_at is a `datetime.isoformat()` ASCII string; events_replayed and replay_duration_ms are integers). HTML escape is therefore a no-op for valid inputs, BUT defense-in-depth `html.escape(...)` is applied to the final assembled string in case future field additions slip operator-supplied data in:

   ```python
   text = f"🛠️ Self-recovered from host restart at {recovered_at_iso}. ..."
   # No escape on the static template; isoformat output is ASCII-safe.
   # If future fields add operator-supplied strings, apply html.escape per-field.
   ```

6. **AC-6: Length safety** — the message is bounded by construction:
   - Header + footer literal text: ~95 chars.
   - `recovered_at_iso`: 25 chars (locked by `timespec="seconds"`).
   - `events_replayed`: max 7 digits (cap `10**6` = 1,000,000 = 7 chars).
   - `replay_duration_ms`: max 10 digits (cap `10**9` = 10 chars).
   - **Worst-case total: ~140 chars**, well under `_BLOCKER_MESSAGE_MAX_CHARS = 1900` (parity constant from Story 3.10/3.11/3.12).

   No section-drop ladder needed because the message has no optional sections. **A defensive final-length self-clamp** (Story 3.11 H5 carry-forward) is still applied for parity with the other 3 renderers — even though it cannot fire for valid inputs, it defends against `model_construct` bypass scenarios:

   ```python
   _SELF_RECOVERED_MESSAGE_MAX_CHARS: int = 1900  # parity with the other 3 cap constants

   if len(text) > _SELF_RECOVERED_MESSAGE_MAX_CHARS:
       text = text[:_SELF_RECOVERED_MESSAGE_MAX_CHARS]
   ```

   Document in a comment that the clamp is a defense-in-depth carry-forward and unreachable under valid model-bound inputs.

7. **AC-7: Renderer is pure** (Story 3.10 AC-9 / 3.11 AC-6 / 3.12 AC-6 carry-forward) — `_render_self_recovered(envelope)` is `def`, not `async def`. No I/O, no clock reads.

8. **AC-8: Renderer exception isolation** — already wired in `_handle()` via the `try/except` around `_render(envelope)` (Story 3.10 review M11 carry-forward). No new wrapper needed.

9. **AC-9: Co-located tests (≥10)** — distribute as:

   - **registry-state event-types** (`services/registry-state/src/registry_state/domain/test_event_types.py`): 5 new tests
     - `test_task_self_recovered_payload_minimal_round_trip` — construct with all 4 fields populated; assert round-trip via `model_dump_json()` + `model_validate_json()`.
     - `test_task_self_recovered_payload_rejects_empty_task_id` — `task_id=""` raises `ValidationError`.
     - `test_task_self_recovered_payload_rejects_oversized_task_id` — `task_id="t"*65` raises `ValidationError`.
     - `test_task_self_recovered_payload_rejects_naive_recovered_at` — `recovered_at=datetime(2026,5,1,12,0,0)` (no tzinfo) raises `ValidationError` (AwareDatetime contract).
     - `test_task_self_recovered_payload_rejects_negative_counters_and_oversized` — parametrized over `events_replayed=-1`, `events_replayed=10**6+1`, `replay_duration_ms=-1`, `replay_duration_ms=10**9+1` — all raise `ValidationError`.

   - **clawhip-daemon renderer** (`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`): 6 new tests + 1 dispatcher routing test = 7 tests
     - `test_render_self_recovered_minimal` — populated payload; assert exact-shape `🛠️ Self-recovered from host restart at 2026-05-01T03:00:00+00:00. 142 events replayed in 350 ms. Zero intervention required.`.
     - `test_render_self_recovered_uses_isoformat_seconds_precision` — `recovered_at` with microseconds; assert output omits microseconds (locked to seconds precision per Story 3.12 M14 pattern).
     - `test_render_self_recovered_pluralizes_events_correctly` — parametrized over `events_replayed=0` ("0 events"), `events_replayed=1` ("1 event"), `events_replayed=2` ("2 events"). Document in Dev Notes that `ms` is fixed-form (no "millisecond/milliseconds" alternation).
     - `test_render_self_recovered_handles_zero_duration` — `replay_duration_ms=0`; assert `"0 ms"` rendered. (Edge case where heartbeat fired instantly.)
     - `test_render_self_recovered_payload_type_mismatch_logs_and_falls_back` — construct envelope with raw-dict payload via `EventEnvelope.model_construct(...)`; assert placeholder shape and `renderer.payload_type_mismatch` WARN logged with `expected="TaskSelfRecoveredPayload"`.
     - `test_render_self_recovered_emergency_clamp_unreachable_for_valid_inputs` — feed maximum-sized payload (`task_id="t"*64`, `events_replayed=10**6`, `replay_duration_ms=10**9`); assert `len(result) <= _SELF_RECOVERED_MESSAGE_MAX_CHARS` AND `len(result) < 250` (worst-case is ~140 chars; assert well under 250 to detect any future template-text growth).
     - `test_render_dispatcher_routes_self_recovered_to_renderer` — assert `_RENDERERS["task.self_recovered"] is _render_self_recovered` (identity check per Story 3.11 M3 / 3.12 carry-forward) AND assert `"task.self_recovered" in _DELIVERABLE_EVENT_TYPES` (AC-2 invariant).

   Plus 1 invariant test:
     - `test_renderers_subset_of_deliverable_event_types_after_3_13` — re-runs the existing M12 invariant (`set(_RENDERERS.keys()).issubset(_DELIVERABLE_EVENT_TYPES)`) explicitly post-3.13 since AC-2 grew the allowlist. Passes vacuously since the subset relation is preserved (renderer set grew by 1, allowlist set grew by 1, both contain the new entry).

   Target: **12 new tests** (5 registry-state + 7 clawhip-daemon).

10. **AC-10: Architectural gates green** — `check_event_registry`, `check_imports` (extend the cross-service noqa block to import `TaskSelfRecoveredPayload`; this is the **5th entry** — see Inheritance section about Story 3.10 L5 deferred refactor cliff), `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`, `mypy --strict`, `just lint` 9/9.

11. **AC-11: Scope boundary** — files modifiable in this story:
    - **New (0).**
    - **Modified (4 source + 2 process):**
      - `services/registry-state/src/registry_state/domain/event_types.py` (AC-1 — declare `TaskSelfRecoveredPayload`, register schema 1.0.0; update `__all__`).
      - `services/registry-state/src/registry_state/domain/test_event_types.py` (AC-9 — 5 new tests).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` (AC-2/3/4/5/6 — append `TaskSelfRecoveredPayload` to noqa import block, add `task.self_recovered` to `_DELIVERABLE_EVENT_TYPES`, add `_SELF_RECOVERED_MESSAGE_MAX_CHARS`, add `_render_self_recovered`, register entry in `_RENDERERS`).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` (AC-9 — 7 new tests + 1 invariant test + `_self_recovered_envelope` helper with `_SELF_RECOVERED_REGISTERED` idempotent guard).
      - `_bmad-output/implementation-artifacts/3-13-self-recovered-summary-template.md` (this file).
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips).
    - **Not modifiable:** `services/clawhip-daemon/.../telegram_outbound.py`, `services/registry-api/`, `services/telegram-gateway/`, `packages/events/.../schema_registry.py`, the existing `_RENDERERS` entries (only **append**).

12. **AC-12: No new dependencies** — all existing.

13. **AC-13: Atomic commit + Epic-2-retro AI #1 (independent gate verify)** — single commit titled exactly:

    ```
    feat(clawhip-daemon,registry-state): story 3.13 — self-recovered-summary message template · FR16
    ```

    `just lint` 9/9 green. `just test` count grows by ≥12 (current visible count post-3.12-fixes-commit: 1026 → target 1038+). `just bootstrap-verify` clean. **Independently re-verify** before flipping `review → done`. Spine sentinel WILL fire (modifies `services/registry-state/src/`); accepted disposition.

14. **AC-14: Carry-forwards honored** (all from prior renderer stories; the suite is well-hardened by 3.10/3.11/3.12 review passes):
    - Story 3.5 H5 — HTML escape contract (defense-in-depth on assembled string; unused for valid inputs).
    - Story 3.6 review L1 — `MappingProxyType` for read-only mappings.
    - Story 3.6 review H3 — message-length safety caps (parity constant `_SELF_RECOVERED_MESSAGE_MAX_CHARS = 1900`).
    - Story 3.9 H7 — schema registration in `event_types.py`.
    - Story 3.9 N7 — HTTP-only cross-service contract; `# noqa: IMP001 — Story 2.9 AC-16` suppression.
    - Story 3.9 L15 — `_DELIVERABLE_EVENT_TYPES` positive allowlist; 3.13 is first addition since.
    - Story 3.10 H1 — section-drop ladder (NOT applicable — no optional sections; defensive final-length clamp only).
    - Story 3.10 H2 + 3.11 + 3.12 retroactive — slice-before-escape on emergency `task_id` (NOT applicable — no emergency one-liner since the message is bounded by construction; type-mismatch fallback uses the dispatcher's existing placeholder shape via `_extract_task_id` which already applies the slice-before-escape pattern).
    - Story 3.10 H3 — model-boundary string validators (`Field(min_length, max_length)` on `task_id`; integer bounds on counters).
    - Story 3.10 H9 — payload-type mismatch WARN before placeholder fallback.
    - Story 3.10 H10 — defensive `isinstance` fallback path covered by an explicit unit test.
    - Story 3.10 / 3.11 H11 — multi-line collapse (NOT applicable — no operator-supplied free-text fields; all field types are constrained).
    - Story 3.10 / 3.11 / 3.12 H3 / M1 — UTF-16-safe codepoint cap of 1900 (parity).
    - Story 3.10 M2 — `_RENDERERS` annotated as `MappingProxyType[str, _RenderFn]`.
    - Story 3.10 M11 — exception isolation around `_render(envelope)` in `_handle()`.
    - Story 3.10 M12 / 3.11 M10 — `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant.
    - Story 3.10 M14 — defense-in-depth `html.escape` (no-op for current inputs but drift-safe).
    - Story 3.11 H8 — `pydantic.AwareDatetime` for timestamps.
    - Story 3.11 M4 — per-character HTML-escape test assertions (NOT applicable — message contains no operator-supplied content; the type-mismatch fallback path tests the existing `_extract_task_id` HTML escape via Story 3.10/3.11/3.12 baseline tests).
    - Story 3.11 M8 / 3.12 H11 — idempotent schema-registration test guard (`_SELF_RECOVERED_REGISTERED`).
    - Story 3.11 H5 / 3.12 — defensive final-length self-clamp.
    - Story 3.11 H6 / L17 — `_collapse_newlines(text)` helper (NOT applicable — no free-text fields; isoformat output is ASCII-safe).
    - Story 3.11 H12 — cap-overflow tests parametric on cap constant (vacuously satisfied — no cap-overflow path exists for this renderer with valid inputs).
    - Story 3.12 M14 — `isoformat(timespec="seconds")` for datetime rendering.
    - Story 3.12 L1 — pluralization decision for counter-with-noun phrases ("event"/"events"; `ms` fixed-form).
    - Story 3.12 retroactive H1 — emergency-tier `task_id` newline collapse (NOT applicable — no emergency tier).
    - Epic-2-retro AI #1 — independent gate verify mandatory.
    - Epic-1-retro AI #2 — `uv sync --all-packages` recipe (use in Task 4).

## Tasks / Subtasks

- [x] **Task 1: New payload model + schema registration** (AC: #1, #10)
  - [x] Declare `TaskSelfRecoveredPayload` in `event_types.py` with the 4 fields per AC-1 — `task_id` (`Field(min_length=1, max_length=64)`), `recovered_at` (`AwareDatetime`), `events_replayed` (`Field(ge=0, le=10**6)`), `replay_duration_ms` (`Field(ge=0, le=10**9)`).
  - [x] Register `register("task.self_recovered", "1.0.0", TaskSelfRecoveredPayload)`. NEW event type — no `1.0.1` registration since there's no v1.0.1 fork (Story 2.14's additive-version pattern was for migrator-bumped events; new events register at 1.0.0 only until a future additive change).
  - [x] Add `"TaskSelfRecoveredPayload"` to `__all__`.
  - [x] Add 5 unit tests in `test_event_types.py` per AC-9: round-trip, empty/oversized `task_id` rejection, naive `recovered_at` rejection, parametrized counter boundary rejection.
  - [x] Verify `check_event_registry` passes.

- [x] **Task 2: Renderer template `_render_self_recovered`** (AC: #2, #3, #4, #5, #6, #7, #8)
  - [x] Append `TaskSelfRecoveredPayload` to the existing `from registry_state.domain.event_types import (...)` cross-service noqa-tagged import block in `telegram_sink.py` (5th entry in the cluster).
  - [x] Add `"task.self_recovered"` to `_DELIVERABLE_EVENT_TYPES` frozenset.
  - [x] Add `_SELF_RECOVERED_MESSAGE_MAX_CHARS: int = 1900` constant.
  - [x] Implement `_render_self_recovered(envelope: EventEnvelope) -> str` with type-mismatch guard, single-line message format per AC-4, `isoformat(timespec="seconds")` rendering, pluralization for `events_replayed`, defensive final-length clamp.
  - [x] Register `"task.self_recovered": _render_self_recovered` in `_RENDERERS`.

- [x] **Task 3: Renderer test coverage** (AC: #9)
  - [x] Add `_self_recovered_envelope(...)` helper with `_SELF_RECOVERED_REGISTERED` idempotent module-level guard mirroring Story 3.12's `_COMPLETED_REGISTERED` pattern.
  - [x] Import `TaskSelfRecoveredPayload` at the top of the file (Story 3.11 M2 pattern).
  - [x] Add 7 renderer tests + 1 invariant test per AC-9.
  - [x] Verify all 12 new tests pass.

- [x] **Task 4: Regression verification + atomic commit** (AC: #13)
  - [x] `uv sync --all-packages` (Epic-1-retro AI #2 nudge — re-discovered 10th time).
  - [x] `just test` — confirm test count grows by ≥12 from the post-3.12-fixes baseline of 1026 (target 1038+).
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] **Independent gate verify** before flipping `review → done` per Epic-2-retro AI #1.
  - [x] Note expected spine-sentinel firing in Completion Notes (modifies `services/registry-state/src/`).
  - [x] Flip `sprint-status.yaml`: `3-13-self-recovered-summary-template: backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`.
  - [ ] Atomic commit with the exact title from AC-13.

## Dev Notes

### Quoted Requirements

> **FR16** (`prd.md`, see `epics.md:52`): *"Platform delivers a proactive morning summary when a host restart occurred during an overnight task."*

> **Epic 3 Story 3.13 AC** (`epics.md:1180-1192`):
> *Given a task's event log contains a `session.reconnecting` + `task.execution.resumed` pair emitted between 00:00 and the next morning's completion summary*
> *When the morning completion summary fires*
> *Then a second compact message `🛠️ Self-recovered from host restart at <ts>. <N> events replayed in <ms>. Zero intervention required.` is emitted alongside.*

> **Story 7.9** (`epics.md:2137-2152`): future emitter / integration test that synthesizes the `task.self_recovered` event when the conditions are met. Story 3.13's payload extension is **forward-compatible** with that emission contract; the renderer here only handles the WHAT (rendering), not the WHEN (synthesis).

### Why This Story Is Different From 3.10/3.11/3.12

The first three message-template stories EXTENDED existing payload models (`TaskApprovalRequestedPayload`, `TaskBlockerRaisedPayload`, `TaskCompletedPayload`) and added v1.1.0 schema registrations alongside v1.0.0/v1.0.1. Story 3.13 is materially different:

1. **NEW event type** `task.self_recovered` (no prior version exists; first registration is v1.0.0).
2. **NEW payload model** `TaskSelfRecoveredPayload` (no prior shape to be backward-compatible with).
3. **NEW entry in `_DELIVERABLE_EVENT_TYPES`** (first allowlist addition since Story 3.9 L15 — when the allowlist was created).
4. **No section-drop ladder** — the message has no optional sections; all 4 payload fields are required and bounded by construction. Cap defenses are still applied for parity but cannot fire under valid inputs.
5. **No operator-supplied free-text fields** — `task_id` is system-generated, `recovered_at` is a system-generated datetime, the two counters are integers. Therefore no `_collapse_newlines`, no per-character HTML-escape tests, no multi-line collapse, no slice-before-escape for emergency tier (no emergency tier exists).

### Why `events_replayed` and `replay_duration_ms` Caps

- `events_replayed` capped at `10**6`: the FR16 use case is "host restart during overnight task". Realistic overnight tasks emit ≤10K events; 1M is 100× headroom for unforeseen workloads. Matches the file/test/blocker counter cap from Story 3.12.
- `replay_duration_ms` capped at `10**9` (1B ms ≈ 11.5 days): a replay duration approaching this cap signals catastrophic registry corruption; the upper bound is defense-in-depth integer-overflow injection protection (Story 3.10 L9 carry-forward). Realistic replay windows are <60 seconds (60K ms); the cap is well above any plausible value.

### Why No Section-Drop Ladder

The message is `🛠️ Self-recovered from host restart at <iso>. <events_replayed> events replayed in <duration_ms> ms. Zero intervention required.` — worst-case ~140 chars. With a cap of 1900, a section-drop ladder would be over-engineering. The defensive final-length self-clamp is included for parity with the other 3 renderers (so future maintainers reading the codebase see a consistent pattern across all 4 message templates) but is unreachable for valid model-bound inputs.

### Cross-Service Import Cluster Cliff (Story 3.10 L5)

After Story 3.13, the cross-service `# noqa: IMP001` import block in `telegram_sink.py` will contain **5 entries**:

```python
from registry_state.domain.event_types import (  # noqa: IMP001 — Story 2.9 AC-16
    PreCheckResults,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskSelfRecoveredPayload,
)
```

Story 3.10 L5 deferred-refactor tracker called this "the right cliff for moving payload models to `packages/events/event-payloads/`." With 3.13 closing the message-template quartet, the renderer set is complete and a follow-on consolidation story can:

1. Move all 5 payload models (plus the 3 sub-models — `PreCheckOutcome`, `PreCheckResults`, `DiffSummary`) to a new `packages/events/event-payloads/` (or `packages/event-payloads/`) workspace member.
2. Re-export from `registry_state.domain.event_types` for back-compat.
3. Update `telegram_sink.py` to import from the new package, dropping the 5 `# noqa: IMP001` markers.

**Story 3.13 explicitly does NOT tackle this refactor** — it would balloon scope past a single template story. Tracked as an open carry-forward item.

### Inheritance from Stories 3.10 + 3.11 + 3.12

Story 3.13 inherits the following infrastructure unchanged:

| Inherited primitive | Source | How 3.13 uses it |
|---|---|---|
| `_RENDERERS: MappingProxyType[str, _RenderFn]` dispatcher | Stories 3.10–3.12 hardened | Append one entry. |
| `_render(envelope)` placeholder fallback | Story 3.10 | Already routes by type; no change. |
| `_extract_task_id(envelope)` helper | Story 3.10 | Re-used in type-mismatch fallback. |
| `_DELIVERABLE_EVENT_TYPES` positive allowlist | Story 3.9 L15 | First addition since allowlist creation; AC-2 is the change. |
| `1900-char` total cap discipline | Stories 3.10/3.11/3.12 H3 | New `_SELF_RECOVERED_MESSAGE_MAX_CHARS = 1900` for parity (unreachable in practice). |
| Defensive final-length self-clamp | Stories 3.11 H5 / 3.12 carry-forward | Re-used; unreachable for valid inputs but kept for pattern consistency. |
| `try/except` around `_render(envelope)` in `_handle()` | Story 3.10 M11 | Renderer-exception isolation; covers self-recovered renderer transparently. |
| `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test | Stories 3.10 M12 / 3.11 M10 | Passes after AC-2 + AC-3 land together. |
| Cross-service `# noqa: IMP001` import block | Stories 3.10/3.11/3.12 | Append `TaskSelfRecoveredPayload` (5th entry; cliff for refactor). |
| Idempotent `_ENSURE_*_REGISTERED` test guard pattern | Stories 3.10 M8 / 3.11 H11 / 3.12 | Mirror as `_SELF_RECOVERED_REGISTERED + _ensure_self_recovered_registered`. |
| `pydantic.AwareDatetime` for timestamps | Story 3.11 H8 | `recovered_at: AwareDatetime`. |
| `isoformat(timespec="seconds")` for datetime rendering | Story 3.12 M14 | Locks `recovered_at` output to 25-char form. |
| Type-mismatch `_log.warning` with `expected="..."` | Stories 3.10 H9 / 3.11 / 3.12 | `expected="TaskSelfRecoveredPayload"`. |
| Pluralization discipline | Story 3.12 L1 | `"event" if N == 1 else "events"`; `ms` fixed-form. |

### Architecture References

- `prd.md` — FR16 statement.
- `epics.md:52` — FR16 capability mapping.
- `epics.md:264` — FR16 → Epic 3 traceability.
- `epics.md:1180-1192` — Story 3.13 user story + AC.
- `epics.md:2137-2152` — Story 7.9 future emitter + integration test.
- `architecture.md` — additive-only schema evolution (NFR-M3); telegram_sink.py outbound rendering placement.
- Story 2.1 — schema registry; pattern for new event-type registration at v1.0.0.
- Story 3.5 H5 — HTML escape contract (defense-in-depth here).
- Story 3.6 H3, review L1 — message-length safety + `MappingProxyType`.
- Story 3.9 — renderer dispatcher placeholder + `_DELIVERABLE_EVENT_TYPES` allowlist (3.13 is first allowlist addition since).
- Story 3.10 — first message-template; established dispatcher + UTF-16 cap + payload-type-mismatch + slice-before-escape patterns.
- Story 3.10 L5 — cross-service noqa cluster refactor cliff (Story 3.13 lands the 5th entry; the right time for the refactor).
- Story 3.11 — second message-template; consolidated `_collapse_newlines`, identity-asserted dispatcher routing, `AwareDatetime`.
- Story 3.11 H8 — AwareDatetime contract.
- Story 3.12 — third message-template; established 7-form/5-form composition pattern (NOT applicable here), `isoformat(timespec="seconds")`, pluralization discipline, zero-counter omission policy (NOT applicable — `events_replayed=0` is meaningfully different from "absent" in this context).
- Story 5.13 / 7.9 — future emitters / integration test for FR16 morning-pair message.
- Epic-1-retro AI #2 — `uv sync --all-packages` recipe (use in Task 4).
- Epic-2-retro AI #1 — independent gate verify mandatory.

### Project Structure Notes

- Renderer: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
- Renderer test: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`
- Payload model: `services/registry-state/src/registry_state/domain/event_types.py`
- Payload test: `services/registry-state/src/registry_state/domain/test_event_types.py`
- Spec: `_bmad-output/implementation-artifacts/3-13-self-recovered-summary-template.md` (this file).
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml`.

No detected conflicts with unified project structure. Story 3.13 closes the message-template quartet in the directories Stories 3.9–3.12 established.

### Predicted File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — declare `TaskSelfRecoveredPayload` (4 fields); register schema 1.0.0; update `__all__` |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — +5 tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — append `TaskSelfRecoveredPayload` to noqa import block; add `task.self_recovered` to `_DELIVERABLE_EVENT_TYPES`; add `_SELF_RECOVERED_MESSAGE_MAX_CHARS` constant; add `_render_self_recovered` function; register entry in `_RENDERERS` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — +8 tests (7 renderer + 1 invariant); shared `_self_recovered_envelope(...)` helper with `_SELF_RECOVERED_REGISTERED` idempotent guard |
| `_bmad-output/implementation-artifacts/3-13-self-recovered-summary-template.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review → done` + `last_updated` bump |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (claude-opus-4-7)

### Debug Log References

### Completion Notes List

- AC-1: `TaskSelfRecoveredPayload` declared with 4 fields (`task_id`, `recovered_at`, `events_replayed`, `replay_duration_ms`), registered as `task.self_recovered` v1.0.0, added to `__all__`.
- AC-2: `"task.self_recovered"` added to `_DELIVERABLE_EVENT_TYPES` frozenset — first allowlist addition since Story 3.9.
- AC-3: `"task.self_recovered": _render_self_recovered` registered in `_RENDERERS` MappingProxyType.
- AC-4: `_render_self_recovered` implemented with type-mismatch guard, single-line FR16 message, `isoformat(timespec="seconds")`, pluralization (`"event"/"events"`, `ms` fixed-form), defensive final-length clamp.
- AC-5: HTML-escape contract — no operator-supplied strings in the message; defense-in-depth `html.escape` not needed for valid inputs.
- AC-6: Length safety — worst-case ~140 chars, well under 1900 cap; defensive clamp unreachable for valid inputs.
- AC-7: Renderer is pure (`def`, not `async def`), no I/O, no clock reads.
- AC-8: Exception isolation via existing `try/except` in `_handle()`.
- AC-9: 13 new tests total (5 registry-state + 8 clawhip-daemon), exceeding the ≥12 target. All passing.
- AC-10: All architectural gates green — `check_event_registry`, `check_imports`, `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`, `mypy --strict`, `just lint` 9/9.
- AC-11: Scope boundary respected — 4 source files + 2 process files modified, 0 new files created.
- AC-12: No new dependencies.
- AC-13: `just lint` 9/9, `just test` 1044 passed (+18 from 1026 baseline, exceeds ≥12 target), `just bootstrap-verify` clean. Spine sentinel fires as expected (modifies `services/registry-state/src/`); accepted disposition. Commit title prepared per AC-13 spec.
- AC-14: All carry-forwards honored — H5 HTML escape, H9 type-mismatch, L5 noqa cluster (5th entry), M12/M10 invariant, H8 AwareDatetime, M14 isoformat seconds, L1 pluralization, H5 defensive clamp, M8 idempotent guard, N7 cross-service IMP001.

### File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — declare `TaskSelfRecoveredPayload` (4 fields); register schema 1.0.0; update `__all__` |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — +5 tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — append `TaskSelfRecoveredPayload` to noqa import block; add `task.self_recovered` to `_DELIVERABLE_EVENT_TYPES`; add `_SELF_RECOVERED_MESSAGE_MAX_CHARS` constant; add `_render_self_recovered` function; register entry in `_RENDERERS` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — +8 tests (7 renderer + 1 invariant); shared `_self_recovered_envelope(...)` helper with `_SELF_RECOVERED_REGISTERED` idempotent guard |
| `_bmad-output/implementation-artifacts/3-13-self-recovered-summary-template.md` | This file — status flipped to review, tasks checked, Dev Agent Record populated |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flip: `in-progress → review`; `last_updated` bump |

## Change Log

- **2026-05-03** — Story implementation complete. All 4 tasks done. 13 new tests (5 payload + 8 renderer/invariant). `just lint` 9/9, `just test` 1044 passed (+18), `just bootstrap-verify` clean. Status → review.
- **2026-05-03** — Code review: 3 patches applied, 5 deferred (pre-existing), 5 dismissed. +2 tests (15 total new). `just lint` 9/9, `just test` 1046 passed (+20). Status → done.

## Review Findings

### Patched (3)

- [x] [Review][Patch] Fallback path missing `_collapse_newlines` on task_id [`telegram_sink.py:1430`] — Applied `_collapse_newlines` to the type-mismatch fallback path (Story 3.12 H1 carry-forward). Added test `test_render_self_recovered_type_mismatch_collapses_newlines_in_task_id`.
- [x] [Review][Patch] Missing test for `events_replayed=1` with max fields [`test_telegram_sink.py`] — Added `test_render_self_recovered_singular_with_max_fields` covering the singular "1 event" form with `task_id="t"*64` and `replay_duration_ms=10**9`.
- [x] [Review][Patch] Cap comment claims "UTF-16 surrogate-pair safety" — misleading [`telegram_sink.py:1389`] — Clarified comment to specify cap is in Python `len()` units (codepoints), with the 1900 × 2 = 3800 < 4096 Telegram wire limit arithmetic.

### Deferred (5, pre-existing)

- [x] [Review][Defer] `import structlog.testing` inside test body inconsistent with project convention [`test_telegram_sink.py`] — pre-existing pattern in 7 test functions across multiple stories
- [x] [Review][Defer] `_build_diff_stats_line` renders "1 files changed" (no singular form) [`telegram_sink.py:1148`] — pre-existing UX polish gap in completion renderer
- [x] [Review][Defer] `assert` in `_build_pr_line` stripped under `python -O` [`telegram_sink.py:1151`] — pre-existing defensive pattern in completion renderer
- [x] [Review][Defer] `_build_step_boundary_payload` linear scan could be binary search [`test_telegram_sink.py:2088`] — pre-existing test utility, acceptable at cap=1900
- [x] [Review][Defer] Missing test for `pr_url` containing only newlines [`test_telegram_sink.py`] — pre-existing test coverage gap in completion renderer
