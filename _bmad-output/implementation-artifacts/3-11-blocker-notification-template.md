# Story 3.11: Blocker notification template

Status: done

## Story

As **the operator**,
I want **blocker messages to render as a single HTML-formatted Telegram message containing the task id, the blocker reason, a `/logs <id>` pointer, and the enumerated available recovery commands (`/logs`, `/retry`, `/stop`, `/handoff`)**,
so that **I know what to do next without querying the registry manually, FR15 is implemented end-to-end at the Telegram outbound surface, and any future emitter that wants to attach blocked-since / last-event / last-action context can do so without breaking pre-3.11 events**.

This is the **second message-template story** plugging into the renderer dispatcher Story 3.10 hardened. Story 3.10 left `_RENDERERS: MappingProxyType[str, _RenderFn]` registered with one entry (`task.approval_requested`); 3.11 adds a second entry (`task.blocker_raised`) and the matching `_render_blocker_raised` template.

The current `TaskBlockerRaisedPayload` (`{task_id, reason}`) is sufficient for the epic's minimum AC text. **FR15** mentions blocked-since, last event, last agent action — these land as **optional** fields on the payload (additive 1.0.x → 1.1.0 per NFR-M3), nullable, and the renderer omits sections when fields are absent. Pre-3.11 emitters keep working unchanged.

The "available commands" footer (`/logs`, `/retry`, `/stop`, `/handoff`) is a **renderer-side static constant** — these are platform-defined recovery commands, not operator-supplied data, so they live next to the renderer (mirrors the approach Story 3.6 H3 took for validation-error bullets).

### What this story is NOT

- **NOT** a third / fourth template — Stories 3.12 (`task.completed` summary) and 3.13 (`task.self_recovered` summary) follow the same pattern. 3.11 only registers `task.blocker_raised`.
- **NOT** a multi-template renderer dispatcher introduction — Story 3.10 already established it; 3.11 only adds an entry.
- **NOT** the LOGIC that emits `task.blocker_raised` — that lives in the worker / clawhip-daemon's failure-detection paths (Story 2.10 introduced the payload model; future stories raise the event with rich context).
- **NOT** new `/logs` / `/retry` / `/stop` / `/handoff` command HANDLING — those are separate Telegram surface stories. 3.11 only renders the suggested-command line as text.
- **NOT** changing inbound paths — purely outbound rendering.
- **NOT** introducing localization / multi-language templates — single English message.

## Acceptance Criteria

1. **AC-1: `TaskBlockerRaisedPayload` extended additively** — `services/registry-state/src/registry_state/domain/event_types.py:TaskBlockerRaisedPayload` gains three optional fields plus tightened validators on the existing fields:

   ```python
   class TaskBlockerRaisedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       task_id: str = Field(min_length=1, max_length=64)
       reason: str = Field(min_length=1, max_length=2000)
       # Story 3.11 — optional FR15 fields (additive, schema 1.1.0).
       # L14 + H8: AwareDatetime — naive datetimes rejected at the model boundary
       # (.isoformat() on a naive value omits the offset suffix → operator
       # sees ambiguous "12:00:00" with no timezone).
       blocked_since: AwareDatetime | None = None
       # L13 + H7: min_length=1 — empty string would render a useless
       # ``Last event: `` line with a trailing space.
       last_event: str | None = Field(default=None, min_length=1, max_length=128)
       last_action: str | None = Field(default=None, min_length=1, max_length=2000)
   ```

   `task_id` / `reason` validators apply Story 3.10 review H3 carry-forward (model-layer min/max bounds; matches the cap pattern set on `TaskApprovalRequestedPayload`). All new fields default `None` so legacy v1.0.x events deserialize cleanly under v1.1.0. Schema version `1.1.0` registered next to the existing `1.0.0` / `1.0.1` entries (Story 3.9 H7 carry-forward — registration in `event_types.py` not `packages/events/src/events/schema_registry.py`).

   `blocked_since` is `pydantic.AwareDatetime` (Story 3.11 review H8 / L14 — Pydantic v2 ISO-8601 string parsing in/out, with naive timestamps rejected at the model boundary). `last_event` is the string event-type name of the most recent event before the blocker (e.g. `"task.execution.started"`); 1..128 chars matching event-type registry conventions. `last_action` is a free-text agent-action description, 1..2000 chars (same shape as approval `action`). The `min_length=1` constraint on the optional string fields (Story 3.11 review H7 / L13) prevents empty-string emitters from rendering useless trailing-space lines.

2. **AC-2: `_render_blocker_raised(envelope: EventEnvelope) -> str` + `_assemble_blocker_sections(...)` helper** — new private functions in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`. The helper mirrors Story 3.10's `_assemble_approval_sections` pattern (boolean-bag levers the section-drop ladder pulls); the renderer is the public dispatcher entry point (Story 3.11 review M16):

   - Extract `payload = envelope.payload`.
   - **Type-mismatch guard** (Story 3.10 review H9 carry-forward): if `not isinstance(payload, TaskBlockerRaisedPayload)`, emit `_log.warning("renderer.payload_type_mismatch", event_type=envelope.type, expected="TaskBlockerRaisedPayload", actual=type(payload).__name__)` and return the placeholder string `f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"` (using `_extract_task_id(envelope) or "<unknown>"`).
   - Render sections in this exact order, joined by `\n\n`:
     1. **Header line** (always): `⛔ Task <html-escaped task_id> blocked. <html-escaped collapsed-reason>. See /logs <html-escaped task_id> for detail.`
     2. **Blocked-since** (omit if `None`): `Blocked since: <ISO-8601 UTC timestamp>` — render via `payload.blocked_since.isoformat()`; the value is internal (not operator-supplied) so no escape needed.
     3. **Last event** (omit if `None`): `Last event: <html-escaped last_event>`
     4. **Last action** (omit if `None`): `Last action: <html-escaped collapsed-last_action>`
     5. **Available commands footer** (always): `Available commands:` followed by exactly four bullet lines in this order:
        ```
          • /logs
          • /retry
          • /stop
          • /handoff
        ```
        These are static, not operator-supplied — declared as a module-level `_BLOCKER_AVAILABLE_COMMANDS: tuple[str, ...] = ("/logs", "/retry", "/stop", "/handoff")` and rendered through `_build_command_bullets(list(_BLOCKER_AVAILABLE_COMMANDS), 4)` (re-uses Story 3.10 H8's helper). HTML-escape inside `_build_command_bullets` is a no-op on these literals but preserves the contract.
   - **Multi-line collapse** (Story 3.10 review H11 carry-forward): apply `.replace("\n", " ")` to `reason` and `last_action` before HTML-escaping, so embedded newlines never collide with the `\n\n` section separator.

3. **AC-3: Dispatcher registration** — append `"task.blocker_raised": _render_blocker_raised,` to the `_RENDERERS` `MappingProxyType` literal so the dispatcher routes blocker events to the new renderer:

   ```python
   _RENDERERS: MappingProxyType[str, _RenderFn] = MappingProxyType(
       {
           "task.approval_requested": _render_approval_request,
           "task.blocker_raised": _render_blocker_raised,
       }
   )
   ```

   `task.blocker_raised` is already in `_DELIVERABLE_EVENT_TYPES` (telegram_sink.py:94 — Story 3.9 L15) so the existing `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (Story 3.10 review M12) keeps passing without modification.

4. **AC-4: HTML-escape contract** (Story 3.5 H5 carry-forward):
   - `task_id`: `html.escape(...)` on the two interpolation sites (header + `/logs <id>` pointer).
   - `reason`: collapse newlines first, then `html.escape(...)`.
   - `last_event`: `html.escape(...)` (string is registry-controlled in normal operation; defense-in-depth).
   - `last_action`: collapse newlines first, then `html.escape(...)`.
   - `blocked_since`: emitted via `.isoformat()` → ASCII timestamp; no escape needed.
   - Static command bullets (`/logs`, `/retry`, `/stop`, `/handoff`): pass through `_build_command_bullets` which applies `html.escape` per entry — no-op on these literals, but no special-casing.

5. **AC-5: Length safety** — apply a total cap and a section-drop ladder analogous to Story 3.10 (review M1 UTF-16 carry-forward):

   ```python
   _BLOCKER_MESSAGE_MAX_CHARS: int = 2000
   ```

   Same 2000-codepoint cap as approvals — leaves headroom for worst-case UTF-16 surrogate-pair expansion under Telegram's 4096-unit wire limit.

   **Section-drop ladder** (sequential rebuilds, simplest-fits-wins):

   - Step 1: full message (header + all optional fields + commands footer) — return if `len(text) <= _BLOCKER_MESSAGE_MAX_CHARS`.
   - Step 2: drop `last_action` — return if fits.
   - Step 3: drop `last_event` — return if fits.
   - Step 4: drop `blocked_since` — return if fits.
   - Step 5: emergency one-liner — `⛔ Task <task_id> blocked. (message body too large; see /logs <task_id>)` with `task_id` HTML-escaped and capped at 64 chars (Story 3.10 review H2 carry-forward — re-use `_EMERGENCY_TASK_ID_MAX_CHARS`). The available-commands footer is dropped at this stage; the operator still sees `/logs` in the body.

   **The header and the available-commands footer are NEVER truncated together** — at the emergency tier we drop the footer because the header itself contains `/logs <id>` as a recovery path. The optional context fields (blocked_since / last_event / last_action) are diagnostic, recoverable via `/logs`, and dropped first. `reason` is part of the mandatory header — if it's pathologically long, it propagates into the emergency one-liner as well, but the model-boundary cap of 2000 chars (AC-1) makes that path unreachable in practice (header overhead ≈ 80 chars + reason 2000 chars + footer ≈ 80 chars = 2160 chars — already triggers Step 5 emergency before any optional sections can be considered).

6. **AC-6: Renderer is pure** (Story 3.10 AC-9 carry-forward) — `_render_blocker_raised(envelope)` is `def`, not `async def`. No httpx calls, no clock reads, no envelope mutation. The dispatcher routing in `_render` is also pure. Trivially unit-testable without fixtures.

7. **AC-7: Renderer exception isolation** (Story 3.10 review M11 carry-forward) — already wired in `_handle()` via the `try/except` around `_render(envelope)`. Verify: an unexpected exception inside `_render_blocker_raised` (e.g. an unforeseen `html.escape` UTF surrogate edge case) is caught by the existing handler and falls back to the placeholder shape without crashing the sink loop. **No new try/except is added** — the existing one in `_handle()` (Story 3.11 review H9 — symbol-only reference: `TelegramSink._handle()` `try/except` block in `telegram_sink.py`) covers this renderer too.

8. **AC-8: Co-located tests (≥12)** — distribute as:

   - **registry-state event-types** (`services/registry-state/src/registry_state/domain/test_event_types.py`): 3 new tests
     - `test_task_blocker_raised_payload_v1_0_back_compat` — `{task_id: "t-…", reason: "…"}` parses cleanly under the v1.1.0 model with `blocked_since` / `last_event` / `last_action` defaulting to `None`.
     - `test_task_blocker_raised_payload_rejects_empty_task_id` — `task_id=""` raises `ValidationError` (Story 3.10 H3 model-layer min/max carry-forward).
     - `test_task_blocker_raised_payload_rejects_oversized_reason` — `reason="X" * 2001` raises `ValidationError` (max_length=2000).

   - **clawhip-daemon renderer** (`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`): 10 new tests
     - `test_render_blocker_raised_minimal` — only `task_id` + `reason`; assert header line present, `/logs <id>` pointer present, `Available commands:` block with all 4 bullets, optional sections absent.
     - `test_render_blocker_raised_with_blocked_since` — `blocked_since=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)`; assert `Blocked since: 2026-05-01T12:00:00+00:00` line present.
     - `test_render_blocker_raised_with_last_event` — `last_event="task.execution.started"`; assert `Last event: task.execution.started` line present.
     - `test_render_blocker_raised_with_last_action` — `last_action="ran pytest tests/"`; assert `Last action: ran pytest tests/` line present.
     - `test_render_blocker_raised_html_escapes_task_id_reason_last_event_last_action` — pass `task_id="t-<x>"`, `reason="<b>boom</b>"`, `last_event="evt<>"`, `last_action="rm -rf <foo>"`; assert no raw `<` or `>` (only `&lt;` / `&gt;`) appear anywhere in the output.
     - `test_render_blocker_raised_collapses_multiline_reason_and_last_action` — `reason="line1\nline2"`, `last_action="step1\nstep2"`; assert `\n` replaced by space within the reason / last_action sections (header / available-commands separators remain intact).
     - `test_render_blocker_raised_total_cap_drops_last_action_first` — populate all optional fields with sizes that overflow only when `last_action` is present; assert `last_action` absent, `last_event` and `blocked_since` present.
     - `test_render_blocker_raised_total_cap_drops_in_spec_order` — sized so Step 2 + Step 3 drops are needed; assert `last_action` and `last_event` absent, `blocked_since` present.
     - `test_render_blocker_raised_emergency_fallback_when_reason_too_long` — `reason="X" * 1990` (just under model boundary; model rejects 2001+); assemble payload that trips Step 5 — assert one-liner `⛔ Task <id> blocked. (message body too large; see /logs <id>)` shape and that `Available commands:` is absent.
     - `test_render_blocker_raised_payload_type_mismatch_logs_and_falls_back` — construct an envelope with raw-dict payload via `EventEnvelope.model_construct(...)`; assert `_render_blocker_raised(envelope)` returns the `Task <id>: task.blocker_raised` placeholder shape and a `renderer.payload_type_mismatch` WARN was logged (Story 3.10 H10 carry-forward).

   Plus 1 dispatcher routing test:
     - `test_render_dispatcher_routes_blocker_to_renderer` — pass envelope of type `task.blocker_raised`; assert `_render(envelope)` invokes `_render_blocker_raised` (header line check is sufficient).

   Target (Story 3.11 review H10 — count corrected from 13): **14 new tests** (3 registry-state + 11 clawhip-daemon = 10 renderer + 1 dispatcher routing). The Story 3.10 review M12 invariant test (`assert set(_RENDERERS.keys()).issubset(_DELIVERABLE_EVENT_TYPES)`) automatically validates the new dispatcher entry — no additional coverage needed for that invariant.

9. **AC-9: Architectural gates green**:
   - `check_event_registry`: passes — `task.blocker_raised` v1.1.0 registered alongside v1.0.0 and v1.0.1 (additive, same-model contract).
   - `check_imports`: clawhip-daemon's `telegram_sink.py` cross-imports `TaskBlockerRaisedPayload` with `# noqa: IMP001 — Story 2.9 AC-16` — append to the existing `from registry_state.domain.event_types import (...)` block (Story 3.10 already opened it). Story 3.10 L5 deferred-refactor note still applies (move payload models to `packages/events/`); 3.11 explicitly does **not** tackle it (per the L5 tracker — wider scope than a single template story).
   - `check_single_writer`: vacuously green — no SQLite writes.
   - `check_no_subprocess`: vacuously green — pure stdlib + Pydantic.
   - `secret-hygiene-precommit`: clean — synthetic test inputs use `t-…` / `***FAKE***` sentinels (Story 3.6 review L5 + Story 3.8 L5 carry-forward).
   - `mypy --strict` clean. New optional fields use `| None` and `Field(default=None, ...)`. The `datetime` import goes in `from datetime import datetime` at the top of `event_types.py` (verify the import isn't already there to avoid duplication).
   - `just lint` 9/9 green.

10. **AC-10: Scope boundary** — files modifiable in this story:

    - **New (0):** none.
    - **Modified (4 source + 2 process):**
      - `services/registry-state/src/registry_state/domain/event_types.py` (AC-1 — extend `TaskBlockerRaisedPayload`, register schema 1.1.0; add `from datetime import datetime` if not already imported; update `__all__` if needed — no new symbols since `TaskBlockerRaisedPayload` is already exported).
      - `services/registry-state/src/registry_state/domain/test_event_types.py` (AC-8 — add 3 new tests).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` (AC-2, AC-3, AC-4, AC-5 — add `_BLOCKER_AVAILABLE_COMMANDS` constant, `_BLOCKER_MESSAGE_MAX_CHARS` constant, `_render_blocker_raised` function; extend `_RENDERERS`; add `TaskBlockerRaisedPayload` to the existing cross-service noqa import block).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` (AC-8 — add 10 renderer tests + 1 dispatcher routing test = 11 tests in this file).
      - `_bmad-output/implementation-artifacts/3-11-blocker-notification-template.md` (this file — status flips through dev cycle).
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips: `backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`).
    - **Not modifiable:**
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/telegram_outbound.py` — outbound is a pure transport (Story 3.6 H1 carry-forward); rendering is the sink's job.
      - `services/registry-api/` / `services/telegram-gateway/` — these don't emit `task.blocker_raised` (worker / clawhip-daemon failure-detection paths do — already in place since Story 2.10).
      - `packages/events/src/events/schema_registry.py` — Story 3.9 H7 carry-forward landed registration in `event_types.py` to dodge the circular import; no change to the package surface.
      - `_DELIVERABLE_EVENT_TYPES` (telegram_sink.py:88-99) — `task.blocker_raised` is already in the allowlist.

11. **AC-11: No new dependencies** — all existing (Pydantic v2 with `datetime`, structlog, stdlib `html` / `types.MappingProxyType`). `_build_command_bullets` already exists from Story 3.10. No third-party additions.

12. **AC-12: Atomic commit + Epic-2-retro AI #1 (independent gate verify)** — single commit titled exactly:

    ```
    feat(clawhip-daemon,registry-state): story 3.11 — blocker-notification message template · FR15
    ```

    `just lint` 9/9 green. `just test` count grows by ≥14 (Story 3.11 review H10 — count corrected from 13; current visible count post-3.10-fixes: 926 → target 940+). `just bootstrap-verify` clean. **Independently re-verify** before flipping `review → done` per Epic-2-retro AI #1.

    Spine sentinel WILL fire (modifies `services/registry-state/src/`); accepted disposition (Story 3.10 AC-14 carry-forward).

13. **AC-13: Carry-forwards honored**:
    - Story 3.5 H5 — HTML escape every operator-supplied string.
    - Story 3.6 review L1 — `MappingProxyType` for read-only mappings (the dispatcher table — already there; we only add an entry).
    - Story 3.6 review H3 — message-length safety caps (per-bullet from `_APPROVAL_BULLET_MAX_CHARS` reused via `_build_command_bullets`; total cap 2000 chars defined fresh as `_BLOCKER_MESSAGE_MAX_CHARS`).
    - Story 3.7 H4 — `extensions["validation_errors"]` namespace pattern: not directly applicable (no validation envelope here). The principle of avoiding `extensions`-bag pollution was followed by adding `blocked_since` / `last_event` / `last_action` directly to the existing `TaskBlockerRaisedPayload` rather than under a generic `extensions`. The fields are tightly bound to `task.blocker_raised` semantics.
    - Story 3.8 — clawhip-daemon source stays in `_SPINE_ROOTS` (Story 3.8 M12). New code in `telegram_sink.py` remains subprocess-free; `check_no_subprocess` passes vacuously.
    - Story 3.9 H7 — schema registration in `event_types.py` (NOT `packages/events/.../schema_registry.py`); avoids the circular import the dev pass discovered.
    - Story 3.9 N7 — HTTP-only cross-service contract preferred. The cross-service import for `TaskBlockerRaisedPayload` (`from registry_state.domain.event_types import ... TaskBlockerRaisedPayload ...`) is a documented and noqa-suppressed exception; alternatives (Story 3.10 L5 — shared `packages/events/event-payloads/`) deferred.
    - Story 3.10 H1 — section-drop ladder approach (sequential rebuilds, simplest-fits-wins) reused for blocker.
    - Story 3.10 H2 — `_EMERGENCY_TASK_ID_MAX_CHARS = 64` reused for the Step 5 fallback.
    - Story 3.10 H3 — model-boundary string validators (`Field(min_length=1, max_length=...)`) applied to `task_id` / `reason`.
    - Story 3.10 H7 — bullet-prefix math: `_build_command_bullets` already accounts for `_BULLET_PREFIX` length internally.
    - Story 3.10 H8 — `_build_command_bullets` extracted helper reused.
    - Story 3.10 H9 — payload-type mismatch WARN before placeholder fallback.
    - Story 3.10 H10 — defensive `isinstance` fallback path covered by an explicit unit test (`test_render_blocker_raised_payload_type_mismatch_logs_and_falls_back`).
    - Story 3.10 H11 — multi-line collapse on operator-supplied free-form text fields (`reason`, `last_action`).
    - Story 3.10 M1 — UTF-16 codepoint cap of 2000 chars (matches approval renderer).
    - Story 3.10 M2 — `_RENDERERS` annotated as `MappingProxyType[str, _RenderFn]` (already; we only add an entry).
    - Story 3.10 M11 — exception isolation around `_render(envelope)` in `_handle()` (already; covers blocker renderer transparently).
    - Story 3.10 M12 — `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (already; passes for the new entry without changes since `task.blocker_raised` was already in the allowlist).
    - Story 3.10 M14 — defense-in-depth `html.escape` even on registry-controlled strings (`last_event`).
    - Story 3.10 L4 — section-build helpers consistent across initial-render and overflow-trim paths.
    - Epic-2-retro AI #1 — independent gate verify before flipping `review → done`. **Mandatory.**

## Tasks / Subtasks

- [x] **Task 1: Payload extension + validators** (AC: #1, #9)
  - [x] Add `from datetime import datetime` to `event_types.py` if not already imported.
  - [x] Extend `TaskBlockerRaisedPayload` with `task_id` / `reason` validators (`Field(min_length=1, max_length=...)`) and three optional fields (`blocked_since`, `last_event`, `last_action`).
  - [x] Register `("task.blocker_raised", "1.1.0", TaskBlockerRaisedPayload)` alongside existing `1.0.0` / `1.0.1` entries (re-use the same model — same-model additive contract).
  - [x] Add 3 unit tests in `test_event_types.py`: v1.0.0 back-compat, empty-`task_id` rejection, oversized-`reason` rejection.
  - [x] Verify `check_event_registry` passes.

- [x] **Task 2: Renderer template `_render_blocker_raised`** (AC: #2, #3, #4, #5, #6, #7)
  - [x] Append `TaskBlockerRaisedPayload` to the existing `from registry_state.domain.event_types import (...)` cross-service noqa-tagged import block in `telegram_sink.py`.
  - [x] Add `_BLOCKER_AVAILABLE_COMMANDS: tuple[str, ...] = ("/logs", "/retry", "/stop", "/handoff")` and `_BLOCKER_MESSAGE_MAX_CHARS: int = 2000` constants near the existing approval renderer constants.
  - [x] Implement `_render_blocker_raised(envelope: EventEnvelope) -> str` with type-mismatch guard + section-drop ladder (5 steps).
  - [x] Register `"task.blocker_raised": _render_blocker_raised` in `_RENDERERS`.
  - [x] HTML-escape `task_id`, `reason` (after `\n` collapse), `last_event`, `last_action` (after `\n` collapse).
  - [x] Re-use `_build_command_bullets`, `_truncate`, `_EMERGENCY_TASK_ID_MAX_CHARS` from Story 3.10.

- [x] **Task 3: Renderer test coverage** (AC: #8)
  - [x] Add 10 renderer tests in `test_telegram_sink.py` covering minimal, each optional field individually, HTML escape, multi-line collapse, drop-order ladder (last_action only, then last_event), emergency fallback, type-mismatch.
  - [x] Add 1 dispatcher routing test (`task.blocker_raised` → `_render_blocker_raised`).
  - [x] Add a shared `_blocker_envelope(...)` helper analogous to Story 3.10's `_approval_envelope`, with idempotent schema registration via the existing `_ensure_*_registered` pattern.
  - [x] Verify all 13 new tests pass.

- [x] **Task 5: Code-review fixes pass** (3-layer adversarial Opus review — 12H / 16M / 20L = 48 patches + 7 deferred + 10 dismissed)
  - [x] All 48 patches applied across `event_types.py`, `test_event_types.py`, `telegram_sink.py`, `test_telegram_sink.py`, and the spec doc.
  - [x] Story 3.10 retroactive fixes (H2 + H3 + H5) applied in lockstep — both renderers now share UTF-16-parity caps (1900) and slice-before-escape emergency-task_id discipline.
  - [x] 16 net new tests; existing Story 3.10 cap-overflow tests rewritten parametric on the cap constant.
  - [x] `_collapse_newlines` helper extracted; consistent `\r\n` / `\r` / `\n` handling across all collapse sites (both renderers).
  - [x] Spec doc updated: AC-1 / AC-2 / AC-7 / AC-8 / AC-12 / Inheritance table / Dev Notes Completion.
  - [x] Independent gate verify (orchestrator): `just lint` 9/9 green, `just test` 940 → 956 visible (+16 net runtime tests passing), `just bootstrap-verify` clean. Spine sentinel fires as expected.
  - [x] Status flipped: review → done.

- [x] **Task 4: Regression verification + atomic commit** (AC: #12)
  - [x] `just test` — confirm test count grows by ≥13 from the post-3.10-fixes baseline of 926 (target 939+).
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean.
  - [x] **Independent gate verify** before flipping `review → done` per Epic-2-retro AI #1.
  - [x] Note expected spine-sentinel firing in Completion Notes (modifies `services/registry-state/src/`).
  - [ ] Flip `sprint-status.yaml`: `3-11-blocker-notification-template: ready-for-dev → in-progress → review → done`; bump `last_updated`. *(in-progress → review flip applied; review → done is the post-code-review step)*
  - [ ] Atomic commit with the exact title from AC-12. *(deferred to user per OMC commit policy)*

## Dev Notes

### Quoted Requirements

> **FR15** (`prd.md` — see `epics.md:51`): *"Platform delivers blocker notifications with blocked-since, last event, last action, available commands."*

> **Epic 3 Story 3.11 AC** (`epics.md:1158-1162`):
> *Given a task emits `task.blocker_raised`*
> *When the telegram-sink renders the outbound message*
> *Then the message contains `⛔ Task <id> blocked. <reason>. See /logs <id> for detail.` plus a compact list of available commands (`/logs`, `/retry`, `/stop`, `/handoff`).*

> **Architecture.md:707** — `services/clawhip-daemon/.../sinks/telegram_sink.py # outbound rendering`.

> **Architecture.md:652** — `services/telegram-gateway/.../message_templates.py # approval, blocker, completion, self-recovered`. *Note: this architectural sketch describes message-template ownership; Story 3.9/3.10 located the actual renderer in `clawhip-daemon/.../sinks/telegram_sink.py` because outbound rendering happens at the sink boundary, not in the gateway. Story 3.11 follows the established placement.*

### Why the Available-Commands Footer Is a Renderer-Side Constant (Not a Payload Field)

The four recovery commands `/logs`, `/retry`, `/stop`, `/handoff` are platform-defined. Putting them on the wire as a payload field would (a) require every emitter to know the canonical list, (b) drift if a later story adds `/escalate`, and (c) duplicate state between the renderer and the schema registry. Treating them as a renderer-side `tuple[str, ...]` constant means: one source of truth, no payload bloat, no cross-service coupling. If a future story makes the list dynamic per-task (e.g. `/handoff` only when worker is busy), THAT story can promote the constant to a payload field with a follow-on schema bump.

### Why `blocked_since` Is `datetime`, Not `str`

Pydantic v2 ISO-8601 timestamp parsing is solid; using `datetime` enforces well-formedness at the model boundary and gives the renderer a typed value to format. If the emitter passes an ill-formed string, `model_validate` rejects it and the event never reaches the sink. (Story 3.10 H3 model-boundary validation principle.)

### Why the Section-Drop Ladder Has No "Drop Available Commands" Step

The available-commands footer is the **point** of the message — without it, the operator has no in-band recovery instructions. Even the emergency one-liner (Step 5) preserves `/logs` in the body. So the ladder drops diagnostic context (last_action / last_event / blocked_since) before falling back to the one-liner. Operators can recover the dropped fields via `/logs <id>`; they cannot recover "what should I type next" except through external knowledge, which defeats FR15.

### Why Three Optional Fields Now (Not Just task_id / reason)

The minimum AC text requires only `task_id` + `reason`. But FR15's wording explicitly enumerates `blocked-since`, `last event`, `last action`, `available commands`. Since:
- additive-only schema evolution (NFR-M3) means we'd otherwise have to bump to v1.2.0 in a future story when the emitter starts populating these,
- the renderer already has to handle "field absent" gracefully for optional fields (current emitters won't populate them),
- carrying the three optionals now costs nothing on the wire when they're `None` (Pydantic v2 `extra="forbid"` doesn't serialize `None` defaults by default unless explicitly requested via `model_dump(exclude_none=False)`),

it's cheaper to land them as part of this story. Stories 3.12 / 3.13 follow the same pattern for completion / self-recovered.

### Inheritance from Story 3.10

Story 3.10 left the following infrastructure that Story 3.11 inherits **unchanged**:

Story 3.11 review H9 / L19: line citations replaced with symbol-only references so future stories' line drift doesn't churn this table. `task_id` cap entries note the retroactive 3.10 fix landed in this review pass (Story 3.11 review H2).

| Inherited primitive | Source | How 3.11 uses it |
|---|---|---|
| `_RENDERERS: MappingProxyType[str, _RenderFn]` dispatcher | `_RENDERERS` in `telegram_sink.py` | Append one entry. |
| `_render(envelope)` placeholder fallback | `_render()` dispatcher in `telegram_sink.py` | Already routes by type; no change. |
| `_extract_task_id(envelope)` helper | `_extract_task_id()` in `telegram_sink.py` | Re-used in type-mismatch fallback. |
| `_truncate(text, limit)` helper | `_truncate()` in `telegram_sink.py` | Re-used **transitively** via `_build_command_bullets` (no direct call from blocker renderer; the bullet-truncation path is defensive — it never fires for the 4 static commands `/logs` `/retry` `/stop` `/handoff`, all ≤7 chars vs. the ~196-char text-body cap). Story 3.11 review M15. |
| `_BULLET_PREFIX` / `_APPROVAL_BULLET_MAX_CHARS` / `_APPROVAL_BULLET_TEXT_MAX_CHARS` | bullet constants in `telegram_sink.py` | Re-used via `_build_command_bullets`. |
| `_build_command_bullets(cmds, visible_count)` | `_build_command_bullets()` helper in `telegram_sink.py` | Direct call with the static 4-command tuple (cached as `_BLOCKER_COMMANDS_LIST` per Story 3.11 review M13). |
| `_EMERGENCY_TASK_ID_MAX_CHARS = 64` | `_EMERGENCY_TASK_ID_MAX_CHARS` constant in `telegram_sink.py` | Re-used in Step 5 emergency fallback. **Story 3.11 review H2 (retroactive 3.10 fix):** the cap is now applied to the RAW `task_id` BEFORE `html.escape` (mid-entity slice safety) — applied to `_render_approval_request` retroactively in this same review pass per Story 3.11 review L19. |
| `try/except` around `_render(envelope)` in `_handle()` | `TelegramSink._handle()` `try/except` block in `telegram_sink.py` | Renderer-exception isolation; covers blocker renderer transparently. |
| `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (M12) | `test_telegram_sink.py` | Passes for the new entry without changes. |
| Cross-service `# noqa: IMP001 — Story 2.9 AC-16` import block | top-of-file imports in `telegram_sink.py` | Append `TaskBlockerRaisedPayload`. |

### Cross-Service Import Strategy (Story 3.10 L5 Carry-Forward)

`telegram_sink.py` already imports `PreCheckResults` and `TaskApprovalRequestedPayload` cross-service via the noqa-tagged block. Adding `TaskBlockerRaisedPayload` brings the count to 3 imports — still under any informal threshold for "this should be a shared package". The Story 3.10 L5 deferred-refactor tracker stands: a future story (post-3.13, when 4 cross-service payload imports exist) can move the payload models to `packages/events/event-payloads/` and clean the noqa cluster in one shot. **Story 3.11 does NOT tackle this refactor** — it would balloon scope past a single template story.

### Why No Architectural Test for Available-Commands Drift

The four-command list is small (4 entries) and the AC freezes the order. A test that asserts `_BLOCKER_AVAILABLE_COMMANDS == ("/logs", "/retry", "/stop", "/handoff")` would test "the constant equals itself" — useless. The render tests already implicitly verify the list (10 tests assert `Available commands:` block contents). If a future story changes the list, those tests fail loudly.

### Architecture References

- `prd.md` — FR15 statement.
- `epics.md:51` — FR15 capability mapping.
- `epics.md:262-264` — FR14/FR15/FR16 → Epic 3 traceability.
- `epics.md:1152-1164` — Story 3.11 user story + AC.
- `architecture.md:114` — additive-only schema evolution (NFR-M3).
- `architecture.md:643-707` — telegram-gateway / clawhip-daemon component placement.
- `architecture.md:652` — `message_templates.py` ownership note (locator-only — actual renderer lives in `telegram_sink.py` per Story 3.9/3.10).
- `architecture.md:707` — `telegram_sink.py # outbound rendering`.
- Story 2.1 — schema registry; existing `register("task.blocker_raised", "1.0.0", ...)`.
- Story 2.10 — `TaskBlockerRaisedPayload` introduction (failure-detection events).
- Story 3.5 H5 — HTML escape contract.
- Story 3.6 H3 — message-length safety caps.
- Story 3.6 review L1 — `MappingProxyType` pattern (already applied; 3.11 only adds an entry).
- Story 3.7 H4 — wire-key namespacing principle (informational).
- Story 3.9 H7 — schema registration in `event_types.py` not `schema_registry.py`.
- Story 3.9 — renderer dispatcher placeholder + `_DELIVERABLE_EVENT_TYPES` allowlist (already includes `task.blocker_raised`).
- Story 3.10 — first message-template story; established the dispatcher + section-drop + UTF-16 cap + multi-line collapse + payload-type-mismatch + bullet-helper + emergency-task_id-cap patterns.
- Story 3.10 review fixes (commit `f0b8233`) — H1–H12, M1–M16, L1–L19 patches; all carry-forwards apply.
- Stories 3.12 / 3.13 — future siblings (completion summary FR9, self-recovered summary FR16).
- Story 6.x — future emitter that will populate `blocked_since` / `last_event` / `last_action` from worker / failure-detection paths.
- Epic-2-retro AI #1 — independent gate verify mandatory.

### Project Structure Notes

- Renderer: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
- Renderer test: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`
- Payload model: `services/registry-state/src/registry_state/domain/event_types.py`
- Payload test: `services/registry-state/src/registry_state/domain/test_event_types.py`
- Spec: `_bmad-output/implementation-artifacts/3-11-blocker-notification-template.md` (this file).
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml`.

No detected conflicts or variances vs. unified project structure — Story 3.11 sits inside the directories Stories 3.9 / 3.10 already established.

### Predicted File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — extend `TaskBlockerRaisedPayload` with 3 optional fields + `task_id`/`reason` validators; register schema 1.1.0; add `from datetime import datetime` if not present |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — +3 tests (v1.0.0 back-compat, empty-task_id rejection, oversized-reason rejection) |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — append `TaskBlockerRaisedPayload` to cross-service noqa-import block; add `_BLOCKER_AVAILABLE_COMMANDS`, `_BLOCKER_MESSAGE_MAX_CHARS`, `_render_blocker_raised`; register entry in `_RENDERERS` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — +11 tests (10 renderer + 1 dispatcher routing); shared `_blocker_envelope(...)` helper |
| `_bmad-output/implementation-artifacts/3-11-blocker-notification-template.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review → done` + `last_updated` bump |

## Review Findings

Three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor — all Opus, no shared context) of the in-tree diff on 2026-05-01. User directive "fix all issues even minors" applies. After dedup across layers: **12 High · 16 Medium · 20 Low = 48 patches**, 7 deferred (pre-existing 3.10 patterns or wider-scope refactors), 10 dismissed (noise / false-positive / cosmetic preference).

### High severity

- [x] [Review][Patch] **H1 — Header double-punctuation when `reason` ends with `.`/`?`/`!`/`:`** [Blind#1]: header `f"⛔ Task {id} blocked. {reason}. See /logs..."` produces `"...crashed.. See /logs..."` for `reason="crashed."`. Strip trailing `.?!:` from `reason_safe` before injection. [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **H2 — Emergency one-liner escapes-then-slices `task_id`, can split HTML entity** [Blind#2 + Edge#1,13 + Auditor#13]: `task_id_for_fallback = task_id_esc[:64]` slices the escaped string; 17×`<` raw → `&lt;`×17 = 68 chars escaped, sliced at 64 lands mid-entity producing `...&lt;&lt;&l`. Story 3.10 has the same gap. Fix: `task_id_capped = payload.task_id[:_EMERGENCY_TASK_ID_MAX_CHARS]; task_id_esc = html.escape(task_id_capped)`. Apply to BOTH renderers (3.10 retroactively). [telegram_sink.py:_render_blocker_raised emergency tier + telegram_sink.py:_render_approval_request emergency tier]
- [x] [Review][Patch] **H3 — UTF-16 cap parity gap** [Blind#4 + Edge#5]: `_BLOCKER_MESSAGE_MAX_CHARS = 2000` codepoints; 2000 plane-1 emoji = 4000 UTF-16 units, plus header/footer/entities pushes over Telegram's 4096-unit wire limit. Either tighten to `1900` (extra 5% headroom) OR measure UTF-16 explicitly via `len(text.encode("utf-16-le")) // 2`. Recommend tightening cap (cheaper, mirrors Story 3.10 M1 cap value). Same fix for `_APPROVAL_MESSAGE_MAX_CHARS` for parity. [telegram_sink.py:_BLOCKER_MESSAGE_MAX_CHARS + _APPROVAL_MESSAGE_MAX_CHARS]
- [x] [Review][Patch] **H4 — `last_event` lacks `\n` collapse — H11 carry-forward inconsistency** [Blind#5 + Edge#2]: `reason` and `last_action` collapse newlines before escape; `last_event` only escapes. Schema permits 128 chars including `\n`. A buggy emitter passing `last_event="evt\n\nattacker"` injects a section break. Apply `.replace("\n", " ")` before `html.escape` on `last_event` too. [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **H5 — Step 5 emergency one-liner has no final length self-check** [Blind#3 + Edge#26 + Auditor#12]: pathological inputs (escape expansion combined with a 64-char raw `task_id` of `<` chars) could produce a >2000-char one-liner. Add `if len(result) > _BLOCKER_MESSAGE_MAX_CHARS: result = result[:_BLOCKER_MESSAGE_MAX_CHARS]` defensive clamp. Also add a positive `assert len(emergency_result) <= _BLOCKER_MESSAGE_MAX_CHARS` to the emergency-fallback test. [telegram_sink.py + test_telegram_sink.py]
- [x] [Review][Patch] **H6 — `\r` / `\r\n` survive newline collapse** [Edge#6]: `.replace("\n", " ")` leaves `\r` intact; a reason `"line1\r\nline2"` becomes `"line1\r line2"`. Telegram client rendering varies. Fix: collapse `\r\n` first, then bare `\r`, then `\n`. Apply to both `reason` and `last_action` collapse sites. [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **H7 — `last_event` / `last_action` admit empty string (`""`)** [Blind#18,19 + Edge#3 + Auditor#5]: `Field(default=None, max_length=128/2000)` has no `min_length`. Empty-string passes validation; renderer outputs `Last event: ` (trailing space) — a useless line. Add `min_length=1` to both. Mirror in spec AC-1 snippet. Add boundary tests (covered by M7/M8). [event_types.py:TaskBlockerRaisedPayload]
- [x] [Review][Patch] **H8 — `blocked_since` accepts naive `datetime`** [Blind#17,20 + Edge#4,23 + Auditor#6]: bare `datetime | None` admits both naive and tz-aware. `.isoformat()` on naive omits tz suffix → `Blocked since: 2026-05-01T12:00:00` ambiguous to operator. Use Pydantic v2 `AwareDatetime` (`from pydantic import AwareDatetime`) OR `@field_validator("blocked_since")` rejecting naive. Add a rejection test. [event_types.py:TaskBlockerRaisedPayload]
- [x] [Review][Patch] **H9 — Spec stale line citations across multiple ACs** [Auditor#1,3]: AC-7 cites `_handle()` at telegram_sink.py:928-938; reality is 1090-1092. Inheritance table cites `_RENDERERS` at :754 (actual :915), `_render` at :761 (actual :923), `_EMERGENCY_TASK_ID_MAX_CHARS` at :566 (actual :567), `_build_command_bullets` at :444 (actual :445), bullet constants at :322-324 (still valid). The Dev Notes Completion claim "AC-7 verified by reading the existing wrapper at telegram_sink.py:928-938" is factually wrong. Fix: replace exact line citations with symbol-only references throughout the spec (e.g. `telegram_sink.py:_RENDERERS`) so future drift doesn't accumulate. [3-11 spec doc — multiple ACs + Inheritance table]
- [x] [Review][Patch] **H10 — AC-8 / AC-12 test-count text says "13" but enumeration totals 14** [Auditor#2,19]: AC-8 line "Target: **13 new tests** (3 + 10)" but enumerates 11 clawhip-daemon tests (10 renderer + 1 dispatcher routing) = 14 total. AC-12 "test count grows by ≥13 ... target 939+" — actual is 940. Update AC-8 line to "**14** (3 + 11)", AC-12 to "≥14 ... target 940+", Task 3 line "Verify all 13 new tests pass" → "all 14". [3-11 spec doc AC-8, AC-12, Task 3]
- [x] [Review][Patch] **H11 — `_blocker_envelope` re-registers schema on every test invocation** [Blind#6 + Auditor#9]: helper calls `_reg("task.blocker_raised", "1.1.0", ...)` per call. Story 3.10 review M8 explicitly fixed this for the approval helper via `_REGISTERED` module-level idempotent guard. Add `_BLOCKER_REGISTERED: bool = False` + `_ensure_blocker_raised_registered()` matching the M8 pattern. [test_telegram_sink.py:_blocker_envelope]
- [x] [Review][Patch] **H12 — Cap-overflow tests hardcode `1990` / `1900` against today's `_BLOCKER_MESSAGE_MAX_CHARS=2000`** [Blind#21,26 + Edge#8,9]: `test_render_blocker_raised_emergency_fallback_when_reason_too_long` uses `reason="X"*1990`; `test_render_blocker_raised_total_cap_drops_last_action_first` uses `last_action="a"*1900`. If the cap is bumped (e.g. H3 tightening to 1900), the messages fit Step 1 silently and tests pass against a different code path. Make sizes parametric on `_BLOCKER_MESSAGE_MAX_CHARS` (e.g. `_BLOCKER_MESSAGE_MAX_CHARS - 10`, `_BLOCKER_MESSAGE_MAX_CHARS - 100`). [test_telegram_sink.py: emergency_fallback + drops_last_action_first + drops_in_spec_order]

### Medium severity

- [x] [Review][Patch] **M1 — Forward-ref hack + late `datetime` import unnecessary** [Blind#15 + Auditor#10]: `from __future__ import annotations` at line 13 makes ALL annotations deferred-string. The `# noqa: UP037` on `"datetime | None"` and `# noqa: E402` on the late `from datetime import UTC, datetime` are both vestigial. Move the import to the top imports block alongside other stdlib imports; drop both noqas; use `datetime | None` directly. [test_telegram_sink.py: imports + _blocker_envelope signature]
- [x] [Review][Patch] **M2 — In-helper duplicate import of `TaskBlockerRaisedPayload`** [Blind#41 + Auditor#11]: `_blocker_envelope` does `from registry_state.domain.event_types import TaskBlockerRaisedPayload` inside the helper body — repeated each call. Add to top-of-file imports (test code; no cross-service IMP001 noqa concern since `_BLOCKER_AVAILABLE_COMMANDS` is already imported from `clawhip_daemon` at the top — pattern already broken cleanly). Drop the inner import. [test_telegram_sink.py:_blocker_envelope]
- [x] [Review][Patch] **M3 — `test_render_dispatcher_routes_blocker_to_renderer` doesn't assert dispatcher routing** [Auditor#17]: the test calls `_render(env)` and checks `result.startswith("⛔ Task ")` plus the tautological `_BLOCKER_AVAILABLE_COMMANDS == ("/logs", "/retry", "/stop", "/handoff")`. The actual dispatcher invariant is `_RENDERERS["task.blocker_raised"] is _render_blocker_raised`. Replace tautological constant assertion with the identity assertion. [test_telegram_sink.py:test_render_dispatcher_routes_blocker_to_renderer]
- [x] [Review][Patch] **M4 — HTML-escape tests use fragile substring assertions** [Auditor#14, Story 3.10 M5 carry-forward]: `assert "<b>boom</b>" not in result` fails to detect partial-escape (e.g. `&lt;b>boom`). Add `assert "<" not in result.replace("&lt;", "")` AND `assert ">" not in result.replace("&gt;", "")` after the substring check. [test_telegram_sink.py:test_render_blocker_raised_html_escapes_*]
- [x] [Review][Patch] **M5 — No boundary test for `task_id > 64`** [Edge#17]: model adds `Field(min_length=1, max_length=64)` to `task_id` but no test asserts the upper bound. Emergency one-liner safety relies on this. Add `test_task_blocker_raised_payload_rejects_oversized_task_id` with `task_id="t"*65`. [test_event_types.py]
- [x] [Review][Patch] **M6 — No boundary test for `last_event > 128`** [Edge#15]: add `test_task_blocker_raised_payload_rejects_oversized_last_event` with `last_event="x"*129`. [test_event_types.py]
- [x] [Review][Patch] **M7 — No boundary test for `last_action > 2000`** [Edge#16]: add `test_task_blocker_raised_payload_rejects_oversized_last_action` with `last_action="a"*2001`. [test_event_types.py]
- [x] [Review][Patch] **M8 — No test for `blocked_since` populated round-trip / non-naive validation** [Edge#18 + tied to H8]: add `test_task_blocker_raised_payload_accepts_aware_blocked_since` (tz-aware succeeds) AND `test_task_blocker_raised_payload_rejects_naive_blocked_since` (naive raises ValidationError, after H8 lands). [test_event_types.py]
- [x] [Review][Patch] **M9 — No test for Step 4 (drop `blocked_since`) ladder transition** [Auditor#7]: only Steps 2, 3, and 5 are exercised. Add `test_render_blocker_raised_total_cap_drops_blocked_since_at_step_4` sized to overflow Step 3 but fit Step 4 (e.g. `reason="r"*1820`, `blocked_since=...`, `last_event="x"*128`, `last_action="a"*200`). [test_telegram_sink.py]
- [x] [Review][Patch] **M10 — `_RENDERERS` invariant test is subset-only; no positive assertion the new entry was added** [Edge#7]: existing M12 invariant test passes vacuously if `task.blocker_raised` is removed from `_RENDERERS` (it's still in `_DELIVERABLE_EVENT_TYPES`). Add an explicit identity assertion in `test_render_dispatcher_routes_blocker_to_renderer` (also covers M3): `assert _RENDERERS["task.blocker_raised"] is _render_blocker_raised`. [test_telegram_sink.py]
- [x] [Review][Patch] **M11 — No happy-path test exercising all 4 sections coexisting** [Edge#19]: each optional field has its own minimal test, but no test asserts header + blocked_since + last_event + last_action + footer all render together (Step 1, full size). Add `test_render_blocker_raised_full_payload_all_sections`. [test_telegram_sink.py]
- [x] [Review][Patch] **M12 — `\n` vs `chr(10)` style inconsistency within `_assemble_blocker_sections`** [Blind#13 + Edge#34 + Auditor#4, Story 3.10 L4 carry-forward]: `reason.replace("\n", " ")` and `last_action.replace(chr(10), " ")` differ stylistically — both are 0x0A. Standardize on `"\n"` for both. After H6, the chained `.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")` should be identical for both fields (extract a `_collapse_newlines` helper). [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **M13 — `_BLOCKER_AVAILABLE_COMMANDS` reconstructed via `list(...)` per render** [Blind#11 + Edge#21]: the tuple is module-level immutable; `list(_BLOCKER_AVAILABLE_COMMANDS)` allocates a new list every render call. Cache as `_BLOCKER_COMMANDS_LIST: list[str] = list(_BLOCKER_AVAILABLE_COMMANDS)` at module scope and pass directly. [telegram_sink.py]
- [x] [Review][Patch] **M14 — `blocked_since` rendered with microseconds — ladder math drift** [Blind#44 + Edge#10]: `.isoformat()` emits `2026-05-01T12:00:00.123456+00:00` (32 chars) when microseconds present vs `+00:00` (25 chars) without. Use `payload.blocked_since.isoformat(timespec="seconds")` to lock to 25-char form. Update test sizing math accordingly. [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **M15 — Inheritance-table claim "`_truncate` re-used" misleading** [Auditor#20]: `_truncate` is only reachable transitively via `_build_command_bullets` (over-cap bullet path), and the 4 static commands are all ≤7 chars — well under the 196-char cap — so the path NEVER fires for blocker. Amend the row to read "Re-used transitively via `_build_command_bullets` (no direct call from blocker renderer; defensive — never fires for the 4 static commands)" or remove the row. [3-11 spec doc Inheritance table]
- [x] [Review][Patch] **M16 — Spec AC-2 doesn't name `_assemble_blocker_sections` helper; AC-10 doesn't enumerate it** [Auditor#24]: implementation introduced the helper (mirroring Story 3.10's `_assemble_approval_sections`) but spec lists only `_render_blocker_raised`. Amend AC-2 to enumerate the helper alongside the renderer. [3-11 spec doc AC-2 + AC-10]

### Low severity

- [x] [Review][Patch] **L1 — Docstring example placeholders look like HTML** [Blind#35]: `_render_blocker_raised` docstring uses `<task_id>` / `<reason>` placeholders in a file that explicitly HTML-escapes. Use `{task_id}` / `{reason}` instead. [telegram_sink.py:_render_blocker_raised docstring]
- [x] [Review][Patch] **L2 — `_RENDERERS` insertion-order documentation** [Blind#36]: the dict-literal layout suggests "registration order = priority". Add a one-line comment that order is irrelevant (lookup by key). [telegram_sink.py:_RENDERERS]
- [x] [Review][Patch] **L3 — Test-file section header hardcodes count "(11)"** [Blind#37]: drifts when tests are added/removed. Drop the count from the heading. [test_telegram_sink.py: blocker test section header]
- [x] [Review][Patch] **L4 — `next()` over `result.split("\n")` lacks default** [Blind#45 + Auditor]: `next(line for line in result.split("\n") if line.startswith("⛔ "))` raises `StopIteration` if header drift breaks the prefix match. Use `next((... ), "")` with a default and an explicit `assert header_line` to fail with a clear message. [test_telegram_sink.py:test_render_blocker_raised_collapses_multiline_*]
- [x] [Review][Patch] **L5 — No order-preserving footer assertion** [Blind#46]: minimal test checks each bullet exists but not order. Assert `result.index("/logs") < result.index("/retry") < result.index("/stop") < result.index("/handoff")`. [test_telegram_sink.py:test_render_blocker_raised_minimal]
- [x] [Review][Patch] **L6 — Renderer-dispatcher comment cites only Story 3.10 AC-4** [Blind#40]: the block at `_RENDERERS` references "Story 3.10 AC-4 / AC-15"; should reference "Story 3.10 AC-4 + Story 3.11 AC-3" now that 3.11 added an entry. [telegram_sink.py:_RENDERERS comment]
- [x] [Review][Patch] **L7 — Type-mismatch test missing assert for absence of footer leak** [Blind#16]: `test_render_blocker_raised_payload_type_mismatch_logs_and_falls_back` checks exact equality with `"Task t-raw-dict-blocker: task.blocker_raised"` — strict but adds defensive `assert "⛔" not in result` and `assert "Available commands:" not in result` to lock in the absence of the blocker-renderer shape on the fallback path. [test_telegram_sink.py]
- [x] [Review][Patch] **L8 — No `inspect.iscoroutinefunction` assertion** [Auditor#23]: AC-6 says renderer is `def`, not `async def`; no test asserts this. Add `import inspect; assert not inspect.iscoroutinefunction(_render_blocker_raised)` to a test. [test_telegram_sink.py]
- [x] [Review][Patch] **L9 — AC-9 mypy claim un-evidenced; document noqas** [Auditor#21]: spec claims "mypy --strict clean" but the diff carries `# noqa: UP037`, `# noqa: E402`, and one `# type: ignore[arg-type]` (the last on `model_construct` test bypass). After M1 lands, only the `type: ignore` remains — document it in Dev Notes Completion. [3-11 spec doc Dev Notes / Completion Notes]
- [x] [Review][Patch] **L10 — Step 4 explicitly toggles `include_blocked_since=False` even when payload field is None** [Edge#11]: redundant double-gating; the `is not None` short-circuit already handles it. Add an inline comment that the boolean flag is the ladder-driven lever (separate from None-omission) so future maintainers don't conflate the two. Or refactor to a drop-set parameter (deferred to a future story per Auditor#8 — too invasive). [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **L11 — Missing test that success path emits no `payload_type_mismatch` WARN** [Auditor#25]: positive `_payload_type_mismatch_logs_and_falls_back` test is the negative-case test sibling. Add a positive `assert not any(rec.get("event") == "renderer.payload_type_mismatch" for rec in captured)` to a happy-path test. [test_telegram_sink.py]
- [x] [Review][Patch] **L12 — `last_event` / `last_action` non-string defensive isinstance gap** [Edge#16, Auditor#16]: a `model_construct`-bypass scenario could pass non-strings; `html.escape(non_str)` raises. The existing `try/except` in `_handle()` (Story 3.10 M11) catches it and falls back to placeholder. Document this reliance in `_render_blocker_raised`'s docstring rather than adding redundant per-field isinstance checks. [telegram_sink.py:_render_blocker_raised docstring]
- [x] [Review][Patch] **L13 — Spec AC-1 snippet should include `min_length=1` for `last_event` and `last_action`** [follow-on from H7]: amend the AC-1 code snippet to match the H7 patch. [3-11 spec doc AC-1]
- [x] [Review][Patch] **L14 — Spec AC-1 snippet should declare `blocked_since` as `AwareDatetime`** [follow-on from H8]: amend the AC-1 code snippet to match H8 patch. [3-11 spec doc AC-1]
- [x] [Review][Patch] **L15 — Cap-tighten propagation note** [follow-on from H3]: when `_BLOCKER_MESSAGE_MAX_CHARS` and `_APPROVAL_MESSAGE_MAX_CHARS` both move to 1900, document the parity in Dev Notes / docstring comment so a future story doesn't accidentally diverge them. [telegram_sink.py: cap constants comment]
- [x] [Review][Patch] **L16 — Test count target text update** [follow-on from H10]: after H10 lands, also update the Dev Notes Change Log entry's "Test count 926 → 940 (+14 net)" to read clearly (already correct in the dev-pass commit text; just verify spec consistency). [3-11 spec doc Change Log + AC-12]
- [x] [Review][Patch] **L17 — Inline `_collapse_newlines` helper** [follow-on from H6 + M12]: extract `def _collapse_newlines(text: str) -> str: return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")` and call from all 3 collapse sites (`reason`, `last_event` after H4, `last_action`). Improves consistency and centralizes the future widening to other whitespace classes. [telegram_sink.py:_assemble_blocker_sections]
- [x] [Review][Patch] **L18 — Mirror Step 4 reachability comment** [follow-on from M9]: add a one-line comment near Step 4 in `_render_blocker_raised` documenting the narrow band where Step 4 fires (roughly `1820 < len(reason) ≤ 1844` with all 3 optional fields populated) so future readers don't think Step 4 is dead code. [telegram_sink.py:_render_blocker_raised]
- [x] [Review][Patch] **L19 — Inheritance-table emergency-task_id-cap row references retroactive 3.10 fix** [follow-on from H2]: after H2 lands (3.10 retroactive fix), update the Inheritance table row to note "applied retroactively to 3.10 in this review pass". [3-11 spec doc Inheritance table]
- [x] [Review][Patch] **L20 — Mention executor's "1 type: ignore[arg-type]" noqa in Dev Notes** [Auditor#21 follow-up]: explicit one-line note in Dev Notes acknowledging the `type: ignore` on `model_construct` payload bypass (legitimate — bypassing Pydantic validation by construction; mypy can't infer the runtime dict acceptance). [3-11 spec doc Dev Notes / Completion Notes]

### Deferred (pre-existing 3.10 patterns or wider-scope)

- [x] [Review][Defer] **D1 — `_assemble_blocker_sections` boolean-bag signature** [Auditor#8] — refactor to drop-set parameter improves readability but mirrors Story 3.10's `_assemble_approval_sections` pattern; out-of-scope for review pass; defer to a future cross-renderer cleanup story.
- [x] [Review][Defer] **D2 — Footer hardcoded English (no i18n)** [Blind#24] — entire project is English-only Phase 1; i18n is out of MVP scope.
- [x] [Review][Defer] **D3 — `_extract_task_id` `<unknown>` sentinel** [Blind#25] — pre-existing 3.10 pattern; uniform fix across renderers belongs in a separate cross-cutting story.
- [x] [Review][Defer] **D4 — Sprint-status state-machine skipped intermediate states** [Auditor#18, Story 3.10 M16 carry-forward] — process drift; convention vs. doctrine; deferred per Story 3.10 M16's same-direction defer.
- [x] [Review][Defer] **D5 — `task_id` whitespace `pattern=` validator** [Edge#22] — broader concern affecting approval renderer too; needs a uniform validator across all task_id usages; a separate story.
- [x] [Review][Defer] **D6 — Module constants lack `Final` annotation** [Blind#47] — project convention follows `_APPROVAL_*` (no `Final`); inconsistency would create style drift; defer.
- [x] [Review][Defer] **D7 — Header-overflow fail-fast** [Edge#27] — over-engineered; Step 5 emergency tier already handles pathological task_ids after H2 + H5 fixes.

### Dismissed (false positives / out-of-scope)

- N1: `Random(311)` / `Random(789)` symbolic seeds [Blind#34] — no defect.
- N2: `mono_ns=7_000_000` magic number [Blind#33, #38] — established 3.9/3.10 pattern.
- N3: `payload = TaskBlockerRaisedPayload(...)` extra="forbid" already validates [Blind#42] — note only.
- N4: "Available commands footer always except emergency" wording precision [Blind#28] — wording is fine.
- N5: "M14 carry-forward" comment context [Blind#29] — pre-existing 3.10 pattern.
- N6: Emergency fallback i18n [Blind#31] — covered by D2 defer.
- N7: Test docstring comma-splice [Blind#43] — cosmetic preference.
- N8: `_RENDERERS` hot-reload race [Blind#22] — out-of-scope; Phase 1 has no hot reload.
- N9: `len(_BLOCKER_AVAILABLE_COMMANDS)` redundant arg [Blind#12] — verified `_build_command_bullets(cmds, visible_count)` signature is consistent with the approval renderer call; passing 4 is correct (it's a "show this many" arg, not a max).
- N10: `tuple[str, str, str, str]` strong tuple typing [Blind#30] — style preference; no defect.

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (executor agent on Opus, single-pass implementation, 47 tool uses, ~7 min wall-clock; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Single executor pass completed all 3 implementation tasks cleanly; no truncation, no SendMessage continuations needed (smaller scope than Stories 3.6 / 3.7 / 3.9 / 3.10 — the dispatcher infrastructure 3.10 hardened did most of the structural lifting).
- **Sizing-rationale tweak in `test_render_blocker_raised_total_cap_drops_in_spec_order`:** initial sizing (reason=1500, last_event=100, last_action=500) wasn't aggressive enough — after Step 2 (drop `last_action`), the message fit under cap with `last_event` retained, so Step 3 didn't fire. Adjusted to reason=1750, last_event=128 (the model max), last_action=200 so Step 2 alone leaves the message just over the 2000-char cap, forcing Step 3 (`last_event` drop) to also fire. Sizing-rationale comment added to the test docstring. No spec deviation — the AC-8 test name and intent are preserved; the test exercises the exact "drops in spec order" semantics the spec calls for.
- **Independent gate verification (orchestrator):** `just lint` 9/9 green. `just test` 940 passed (1 expected spine-sentinel failure — see below). `just bootstrap-verify` clean (13 workspace imports).
- **Pre-existing dev-tooling quirk:** `uv sync --no-dev` strips `asgi-lifespan` from the venv (Story 3.6 / 3.7 / 3.8 / 3.9 / 3.10 carry-forward). Restored via `uv sync --all-packages` before `just lint`. Not a 3.11 regression — the same quirk has fired on every recent story.

### Completion Notes List

- **All 13 ACs satisfied.**
- **Story 3.11 review pass (L9 / L20 documentation):** mypy --strict is clean across the 4 modified files. After Story 3.11 review M1 landed (forward-ref + late-import cleanup), the only `# noqa` / `# type: ignore` markers in the diff are:
  - `# noqa: IMP001 — Story 2.9 AC-16` on cross-service payload imports (the long-tracked deferred refactor — Story 3.10 L5).
  - `# type: ignore[arg-type]` on the `model_construct(payload={"task_id": "t-raw-dict-blocker"})` test bypass — legitimate (the test deliberately bypasses Pydantic field validation by construction; mypy can't infer the runtime dict acceptance).
  - Two pre-existing `# noqa: IMP001, I001` markers on inline imports in unrelated test helpers (`_task_created_envelope`, `_task_completed_envelope`, `_service_crashed_envelope`) — left in place per the M1 / M2 narrow scope.
- **Test count: +14 net** (3 registry-state + 11 clawhip-daemon = 14). Post-3.10-fixes baseline 926 → 940. Spec text in AC-8 says "Target: 13 new tests (3 registry-state + 10 clawhip-daemon)" but the AC-8 enumeration immediately below lists 10 renderer tests + 1 dispatcher routing test = 11 in the clawhip-daemon slice. Implementation matched the **enumerated** test set (the more specific signal). **Spec-text reconciliation note for the reviewer:** the "13" → "14" delta in AC-8 summary line vs. enumeration is a one-character spec-text inconsistency; either update the spec to read "14" or treat the dispatcher test as the 11th renderer entry — either way the test coverage is the enumerated full set.
- **Section-drop ladder implemented as 5 sequential rebuilds via `_assemble_blocker_sections(...)` helper** — pure-function approach mirroring Story 3.10's `_assemble_approval_sections`. Order: full → drop `last_action` (Step 2) → drop `last_event` (Step 3) → drop `blocked_since` (Step 4) → emergency one-liner (Step 5). The emergency one-liner drops the available-commands footer and uses `_EMERGENCY_TASK_ID_MAX_CHARS = 64` to defensively cap `task_id` (Story 3.10 H2 carry-forward).
- **Defensive `isinstance` fallback in `_render_blocker_raised`** — handles raw-dict envelopes constructed via `EventEnvelope.model_construct(...)` (registration-race window where the typed payload class isn't yet imported) by falling back to the placeholder shape rather than crashing. Logs `renderer.payload_type_mismatch` WARN with `expected="TaskBlockerRaisedPayload"` so SRE has a signal in production (Story 3.10 H9 carry-forward).
- **Available-commands footer is a renderer-side static** — `_BLOCKER_AVAILABLE_COMMANDS: tuple[str, ...] = ("/logs", "/retry", "/stop", "/handoff")` is module-level. Re-uses Story 3.10's `_build_command_bullets` helper for the bullet rendering. Implementation matches the rationale captured in Dev Notes ("Why the Available-Commands Footer Is a Renderer-Side Constant").
- **Pre-existing exception-isolation in `_handle()` covers the new renderer transparently** (Story 3.10 review M11 carry-forward) — no new try/except wrapper needed. AC-7 verified by reading the existing wrapper in `TelegramSink._handle()` `try/except` block (Story 3.11 review H9 — symbol-only citation; replaces stale line-number reference); renderer-raised exceptions fall back to placeholder shape and emit a structured ERROR.
- **Cross-service import group grew to 3** (`PreCheckResults`, `TaskApprovalRequestedPayload`, `TaskBlockerRaisedPayload` all `# noqa: IMP001 — Story 2.9 AC-16`). The Story 3.10 L5 deferred-refactor tracker stands: future story (post-3.13, when 4 cross-service payload imports exist) can move payload models to `packages/events/event-payloads/` to clean the noqa cluster. **Story 3.11 explicitly did NOT tackle this refactor** — wider scope than a single template story.
- **Spine sentinel fired as expected** (`tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged`) — modifying `services/registry-state/src/registry_state/domain/event_types.py` (AC-1) AND `services/registry-state/src/registry_state/domain/test_event_types.py` (AC-8) trips the sentinel. Accepted disposition per AC-12 / Story 3.10 AC-14 carry-forward.
- **No code-shape deviations from spec.** All carry-forwards honored: H2 (emergency `task_id` cap), H3 (model-boundary string validators with min/max), H9 (type-mismatch WARN before placeholder), H11 (multi-line collapse on operator-supplied free-form text), M1 (UTF-16 codepoint cap of 2000), M2 (`MappingProxyType[str, _RenderFn]` annotation tightness), M14 (defense-in-depth `html.escape` on registry-controlled strings).
- **`Status: review` set; sprint-status.yaml flipped to `review`.** No commit performed (per OMC commit policy — user runs `code-review` workflow next; commit happens after review approval).

### Change Log

| Date | Change |
|---|---|
| 2026-05-01 | Review pass on commit-pending diff (Blind Hunter / Edge Case Hunter / Acceptance Auditor — three Opus agents, no shared context): 99 raw findings → 48 unique patches applied (12 High / 16 Medium / 20 Low) + 7 deferred + 10 dismissed-as-noise. Retroactive 3.10 fixes: H2 (slice-before-escape on `_render_approval_request` emergency one-liner) + H3 (`_APPROVAL_MESSAGE_MAX_CHARS` 2000 → 1900 in lockstep with `_BLOCKER_MESSAGE_MAX_CHARS` for UTF-16 parity) + H5 (defensive final-length self-clamp on emergency tier in BOTH renderers). 3 Story 3.10 cap-overflow tests rewritten parametric on the cap constant. Spec doc edits: AC-1 (`min_length=1` on `last_event` / `last_action`; `blocked_since: AwareDatetime`), AC-2 (enumerate `_assemble_blocker_sections` helper), AC-7 + AC-12 (symbol-only line citations), AC-8 + Task 3 (test-count "13" → "14"), Inheritance table (`_truncate` re-used transitively + H2 retroactive note), Dev Notes Completion (mypy noqa documentation). New `_collapse_newlines(text)` helper covers `\r\n` / `\r` / `\n` collapse uniformly; called from all 3 collapse sites + retroactively at the approval renderer's `justification`. Test count 940 → 956 visible (+16 net independently verified; executor saw +25 due to ordering / parametric expansion). Spine sentinel fired as expected. 9/9 lint gates green; bootstrap-verify clean. |
| 2026-05-01 | Story 3.11 implemented: `TaskBlockerRaisedPayload` schema 1.0.0 → 1.1.0 (additive — `blocked_since: datetime \| None`, `last_event: str \| None Field(max_length=128)`, `last_action: str \| None Field(max_length=2000)` as optional FR15 fields; existing `task_id` / `reason` got Story 3.10 H3 model-boundary validators `Field(min_length=1, max_length=64/2000)`); new `_render_blocker_raised(envelope)` template renders FR15 message (header `⛔ Task <id> blocked. <reason>. See /logs <id> for detail.` + optional `Blocked since:` / `Last event:` / `Last action:` blocks + always-present `Available commands:` footer with 4 static bullets `/logs` `/retry` `/stop` `/handoff`, all HTML-escaped, multi-line `\n` collapsed); message-length safety per Story 3.10 H1 carry-forward (5-step section-drop ladder: full → drop `last_action` → drop `last_event` → drop `blocked_since` → emergency one-liner with 64-char `task_id` cap and no commands footer); renderer registered in `_RENDERERS` dispatch table alongside `task.approval_requested`; `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (Story 3.10 M12) passes for the new entry without changes since `task.blocker_raised` was already in the allowlist (Story 3.9 L15). 14 new tests across 2 files (3 payload validation + 10 renderer + 1 dispatcher routing). Test count 926 → 940 visible (+14 net). 9/9 lint gates green; bootstrap-verify clean. **Second message-template story** plugging into Story 3.9's dispatcher; Stories 3.12 / 3.13 add the remaining two templates (completion summary FR9 / self-recovered summary FR16). |

### File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — extended `from datetime import datetime` (added to existing import); extended `TaskBlockerRaisedPayload` with model-boundary validators on `task_id` / `reason` and 3 optional FR15 fields (`blocked_since`, `last_event`, `last_action`); registered schema `1.1.0` alongside existing `1.0.0` / `1.0.1` entries |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — added `TaskBlockerRaisedPayload` to imports; added 3 unit tests (v1.0.0 back-compat, empty-`task_id` rejection, oversized-`reason` rejection) |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — appended `TaskBlockerRaisedPayload` to the cross-service `# noqa: IMP001` import block; added `_BLOCKER_AVAILABLE_COMMANDS` and `_BLOCKER_MESSAGE_MAX_CHARS` constants; added `_assemble_blocker_sections(...)` helper and `_render_blocker_raised(envelope)` function with type-mismatch guard and 5-step section-drop ladder; registered `"task.blocker_raised": _render_blocker_raised` in `_RENDERERS` `MappingProxyType` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — added `_BLOCKER_AVAILABLE_COMMANDS` / `_BLOCKER_MESSAGE_MAX_CHARS` to imports; added `from datetime import UTC, datetime`; added shared `_blocker_envelope(...)` helper with idempotent schema registration; added 11 tests (10 renderer + 1 dispatcher routing) |
| `_bmad-output/implementation-artifacts/3-11-blocker-notification-template.md` | This file — task checkboxes ticked, Dev Agent Record / File List / Change Log filled, Status flipped to `review` |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flipped: `backlog → ready-for-dev → in-progress → review` + `last_updated` bump to 2026-05-01T17:00:00Z |
