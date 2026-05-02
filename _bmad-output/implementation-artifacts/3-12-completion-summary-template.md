# Story 3.12: Completion summary template

Status: done

## Story

As **the operator**,
I want **completion messages to render as a single HTML-formatted Telegram message containing the task id, optional PR number/branch/URL, file/line/test counters, CI state, and blocker count, all in the FR9 wording**,
so that **I can scan a morning summary in one glance, FR9 is implemented end-to-end at the Telegram outbound surface, and the future emitter (Story 5.13) populating the rich `task.completed` payload has a stable, well-typed renderer contract to feed**.

This is the **third message-template story** plugging into the renderer dispatcher Stories 3.10 + 3.11 hardened. After 3.12 the dispatcher will route 3 of the 4 deliverable templated events; only `task.self_recovered` (Story 3.13) remains untemplated.

The current `TaskCompletedPayload` is `{task_id, summary, pr_url}` — already partially aligned with FR9 (pr_url present). Story 3.12 extends it additively (1.0.x → 1.1.0 per NFR-M3) with the seven FR9 counters: `pr_number`, `pr_branch`, `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state`, `blockers_count`. All new fields optional/nullable so pre-3.12 emitters keep working unchanged. Story 5.13 will eventually populate them; the renderer here gracefully omits sections when fields are absent.

### What this story is NOT

- **NOT** the LOGIC that emits `task.completed` with rich payloads — Story 5.13 owns that. Story 3.12 only adds the renderer + payload extension.
- **NOT** the PR-draft creation flow — Story 5.14 owns that.
- **NOT** Story 3.13 (self-recovered template, FR16) — separate event type, separate renderer.
- **NOT** localization / multi-language templates — single English message, FR9 wording.
- **NOT** changing inbound paths — purely outbound rendering.

## Acceptance Criteria

1. **AC-1: `TaskCompletedPayload` extended additively** — `services/registry-state/src/registry_state/domain/event_types.py:TaskCompletedPayload` gains seven optional fields plus tightened validators on the existing fields:

   ```python
   class TaskCompletedPayload(BaseModel):
       model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
       task_id: str = Field(min_length=1, max_length=64)
       summary: str = Field(min_length=1, max_length=2000)
       pr_url: str | None = Field(default=None, min_length=1, max_length=500)
       # Story 3.12 — optional FR9 fields (additive, schema 1.1.0).
       pr_number: int | None = Field(default=None, ge=1, le=10**9)
       pr_branch: str | None = Field(default=None, min_length=1, max_length=255)
       files_changed: int | None = Field(default=None, ge=0, le=10**6)
       lines_added: int | None = Field(default=None, ge=0, le=10**9)
       lines_removed: int | None = Field(default=None, ge=0, le=10**9)
       tests_added: int | None = Field(default=None, ge=0, le=10**6)
       ci_state: Literal["green", "red", "unknown"] | None = None
       blockers_count: int | None = Field(default=None, ge=0, le=10**6)
   ```

   `task_id`/`summary`/`pr_url` validators apply Story 3.10 H3 carry-forward (model-boundary min/max bounds). All new fields default `None` so legacy v1.0.x events deserialize cleanly under v1.1.0. Schema version `1.1.0` registered alongside existing `1.0.0`/`1.0.1` (Story 3.9 H7 carry-forward).

   `lines_added`/`lines_removed`/`pr_number` get `le=10**9` upper bounds (Story 3.10 review L9 carry-forward — defense against buggy diff-parser overflow). `files_changed`/`tests_added`/`blockers_count` get `le=10**6` (more than enough for any reasonable PR; defends against integer-overflow injection). `pr_branch` capped at 255 chars (git ref-name length limit). `pr_url` retains `max_length=500` (real GitHub PR URLs are well under 200 chars; 500 is comfortable headroom).

2. **AC-2: `_render_completed(envelope: EventEnvelope) -> str`** — new private function in `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`:

   - Extract `payload = envelope.payload`.
   - **Type-mismatch guard** (Story 3.10 review H9 / 3.11 carry-forward): if `not isinstance(payload, TaskCompletedPayload)`, emit `_log.warning("renderer.payload_type_mismatch", event_type=envelope.type, expected="TaskCompletedPayload", actual=type(payload).__name__)` and return the placeholder `f"Task {html.escape(task_id)}: {html.escape(envelope.type)}"` (using `_extract_task_id(envelope) or "<unknown>"`).
   - Render sections in this exact order, joined by `\n\n`:
     1. **Header line** (always): `✅ Task <html-escaped task_id> complete.`
     2. **PR line** (omit if `pr_number is None` and `pr_branch is None` and `pr_url is None`): one of three forms based on what's present:
        - All three populated: `PR #<pr_number>: <html-escaped pr_branch> — <html-escaped pr_url>`
        - `pr_number` + `pr_branch` only: `PR #<pr_number>: <html-escaped pr_branch>`
        - `pr_number` + `pr_url` only: `PR #<pr_number>: <html-escaped pr_url>`
        - `pr_branch` + `pr_url` only: `<html-escaped pr_branch> — <html-escaped pr_url>`
        - `pr_url` only: `PR: <html-escaped pr_url>`
        - `pr_branch` only: `Branch: <html-escaped pr_branch>`
        - `pr_number` only: `PR: #<pr_number>`
     3. **Diff stats line** (omit if `files_changed is None` and `lines_added is None` and `lines_removed is None`): assembled progressively:
        - All three: `<files_changed> files changed, <lines_added>+ / <lines_removed>- lines.`
        - `files_changed` only: `<files_changed> files changed.`
        - `lines_added` and `lines_removed` only: `<lines_added>+ / <lines_removed>- lines.`
        - `lines_added` only: `<lines_added> lines added.`
        - `lines_removed` only: `<lines_removed> lines removed.`
     4. **Tests line** (omit if `tests_added is None`): `<tests_added> tests added.`
     5. **CI state line** (omit if `ci_state is None`): `CI: <ci_state_emoji> <ci_state>` where the emoji map is `{"green": "✅", "red": "❌", "unknown": "❓"}`.
     6. **Blockers line** (omit if `blockers_count is None`): `<blockers_count> blockers raised.`
     7. **Summary line** (always present): `<html-escaped collapsed-summary>` — the human-readable summary string from the original payload.

   - **Multi-line collapse** (Story 3.10 H11 / 3.11 carry-forward): apply `_collapse_newlines(text)` (the helper Story 3.11 review-pass extracted) to `summary`, `pr_branch`, `pr_url` before `html.escape`.

3. **AC-3: Dispatcher registration** — append `"task.completed": _render_completed,` to the `_RENDERERS` `MappingProxyType` literal so the dispatcher routes completion events to the new renderer:

   ```python
   _RENDERERS: MappingProxyType[str, _RenderFn] = MappingProxyType(
       {
           "task.approval_requested": _render_approval_request,
           "task.blocker_raised": _render_blocker_raised,
           "task.completed": _render_completed,
       }
   )
   ```

   `task.completed` is already in `_DELIVERABLE_EVENT_TYPES` (Story 3.9 L15) so the existing `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test (Story 3.10 M12 + 3.11 M10 strengthening) keeps passing without modification.

4. **AC-4: HTML-escape contract** (Story 3.5 H5 carry-forward):
   - `task_id`: `html.escape(...)` on the header interpolation site.
   - `summary`: collapse newlines via `_collapse_newlines(...)` first, then `html.escape(...)`.
   - `pr_branch`: collapse newlines (defense-in-depth — branch names should not contain `\n` per git ref-name rules, but if a buggy emitter slips one in, defend), then `html.escape(...)`.
   - `pr_url`: collapse newlines, then `html.escape(...)`. URLs may contain `&` (query params); HTML escape handles correctly.
   - `pr_number`, `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `blockers_count`: integers; no escape.
   - `ci_state`: bound by `Literal[...]`; no escape needed (Story 3.10 M14 defense-in-depth — apply `html.escape` anyway for drift-safety, no-op on current Literals).

5. **AC-5: Length safety** — apply the project-standard cap (`_BLOCKER_MESSAGE_MAX_CHARS = 1900` after Story 3.11 review H3 tightening — both renderers use the same constant; 3.12 reuses) and a section-drop ladder.

   ```python
   _COMPLETED_MESSAGE_MAX_CHARS: int = 1900  # parity with _APPROVAL_MESSAGE_MAX_CHARS / _BLOCKER_MESSAGE_MAX_CHARS
   ```

   **Section-drop ladder** (sequential rebuilds via `_assemble_completed_sections(payload, *, include_X)` helper analogous to Story 3.11's `_assemble_blocker_sections`):

   - Step 1: full message (header + PR + diff stats + tests + CI + blockers + summary) — return if `len(text) <= _COMPLETED_MESSAGE_MAX_CHARS`.
   - Step 2: drop diff stats (typically the longest counter line; `summary` more useful to operator) — return if fits.
   - Step 3: drop blockers count — return if fits.
   - Step 4: drop tests count — return if fits.
   - Step 5: drop CI state — return if fits.
   - Step 6: drop PR line — return if fits.
   - Step 7: emergency one-liner — `✅ Task <task_id> complete. (message body too large; see /logs <task_id>)` with `task_id` HTML-escaped via the slice-before-escape pattern (Story 3.11 H2 carry-forward — slice raw, then escape; cap at `_EMERGENCY_TASK_ID_MAX_CHARS = 64`). Final-length self-clamp per Story 3.11 H5 carry-forward.

   **The summary line is dropped LAST among optional fields** because it's the operator-supplied human-readable description — the payload's most semantically valuable field (FR9 explicitly wants summary in the message). Only the emergency one-liner drops it.

6. **AC-6: Renderer is pure** (Story 3.10 AC-9 / 3.11 AC-6 carry-forward) — `_render_completed(envelope)` is `def`, not `async def`. No I/O. Trivially unit-testable.

7. **AC-7: Renderer exception isolation** — already wired in `_handle()` via the `try/except` around `_render(envelope)` (Story 3.10 review M11 carry-forward). No new wrapper needed.

8. **AC-8: Co-located tests (≥14)** — distribute as:

   - **registry-state event-types** (`services/registry-state/src/registry_state/domain/test_event_types.py`): 4 new tests
     - `test_task_completed_payload_v1_0_back_compat` — `{task_id, summary, pr_url=None}` parses cleanly under v1.1.0 with all 8 new fields defaulting to `None`.
     - `test_task_completed_payload_rejects_oversized_pr_branch` — `pr_branch="b"*256` raises `ValidationError` (max_length=255).
     - `test_task_completed_payload_rejects_negative_counters` — each of `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `blockers_count` rejects negative; `pr_number` rejects 0.
     - `test_task_completed_payload_rejects_invalid_ci_state` — `ci_state="yellow"` raises `ValidationError` (Literal["green","red","unknown"] enforces).

   - **clawhip-daemon renderer** (`services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`): 11 new tests
     - `test_render_completed_minimal` — only `task_id` + `summary`; assert header + summary, no PR/diff/tests/CI/blockers lines.
     - `test_render_completed_with_pr_full` — `pr_number=42, pr_branch="feat/foo", pr_url="https://..."`; assert all three rendered.
     - `test_render_completed_with_pr_partial` — only `pr_number=42`; assert `PR: #42` form.
     - `test_render_completed_with_diff_stats_full` — `files_changed=5, lines_added=234, lines_removed=89`; assert `5 files changed, 234+ / 89- lines.` shape.
     - `test_render_completed_with_tests_added` — `tests_added=12`; assert `12 tests added.` line.
     - `test_render_completed_with_ci_state_green` — `ci_state="green"`; assert `CI: ✅ green` line. Parametrize over `("green", "✅"), ("red", "❌"), ("unknown", "❓")`.
     - `test_render_completed_with_blockers_count` — `blockers_count=2`; assert `2 blockers raised.` line.
     - `test_render_completed_html_escapes_task_id_summary_pr_branch_pr_url` — pass payload with `<x>` characters in each field; assert per-character checks (Story 3.11 M4 carry-forward — `assert "<" not in result.replace("&lt;", "")`).
     - `test_render_completed_collapses_multiline_summary_and_pr_branch` — `summary="line1\nline2"`, `pr_branch="feat/foo\nbar"`; assert `\n` replaced by space.
     - `test_render_completed_total_cap_drops_in_spec_order` — sized to overflow only when diff stats present; assert diff stats absent, blockers/tests/CI/PR/summary present. Sized parametric on `_COMPLETED_MESSAGE_MAX_CHARS` (Story 3.11 H12 carry-forward).
     - `test_render_completed_emergency_fallback_when_summary_too_long` — `summary="X" * (_COMPLETED_MESSAGE_MAX_CHARS + 90)` (under model boundary 2000); assert one-liner shape and `assert len(result) <= _COMPLETED_MESSAGE_MAX_CHARS` (H5 carry-forward).
     - `test_render_completed_payload_type_mismatch_logs_and_falls_back` — construct envelope with raw-dict payload via `EventEnvelope.model_construct(...)`; assert placeholder shape and `renderer.payload_type_mismatch` WARN logged.
     - `test_render_dispatcher_routes_completed_to_renderer` — assert `_RENDERERS["task.completed"] is _render_completed` (identity check per Story 3.11 M3/M10).

   Target: **14-15 new tests** (4 registry-state + 11 clawhip-daemon).

9. **AC-9: Architectural gates green** — `check_event_registry`, `check_imports` (extend the cross-service noqa block to import `TaskCompletedPayload`), `check_single_writer`, `check_no_subprocess`, `secret-hygiene-precommit`, `mypy --strict`, `just lint` 9/9.

10. **AC-10: Scope boundary** — files modifiable in this story:
    - **New (0).**
    - **Modified (4 source + 2 process):**
      - `services/registry-state/src/registry_state/domain/event_types.py` (AC-1 — extend `TaskCompletedPayload` + register schema 1.1.0).
      - `services/registry-state/src/registry_state/domain/test_event_types.py` (AC-8 — 4 new tests).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` (AC-2/3/4/5 — append `TaskCompletedPayload` to noqa import block, add `_COMPLETED_MESSAGE_MAX_CHARS` constant, add `_assemble_completed_sections` helper, add `_render_completed`, register entry in `_RENDERERS`).
      - `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` (AC-8 — 11 new tests + `_completed_envelope` shared helper with M8-pattern idempotent `_COMPLETED_REGISTERED` guard).
      - `_bmad-output/implementation-artifacts/3-12-completion-summary-template.md` (this file).
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (status flips).
    - **Not modifiable:** `services/clawhip-daemon/.../telegram_outbound.py`, `services/registry-api/`, `services/telegram-gateway/`, `packages/events/.../schema_registry.py`, `_DELIVERABLE_EVENT_TYPES`.

11. **AC-11: No new dependencies** — all existing.

12. **AC-12: Atomic commit + Epic-2-retro AI #1 (independent gate verify)** — single commit titled exactly:

    ```
    feat(clawhip-daemon,registry-state): story 3.12 — completion-summary message template · FR9
    ```

    `just lint` 9/9 green. `just test` count grows by ≥14 (current visible count post-3.11-fixes-commit: 956 → target 970+). `just bootstrap-verify` clean. **Independently re-verify** before flipping `review → done`. Spine sentinel WILL fire (modifies `services/registry-state/src/`); accepted disposition.

13. **AC-13: Carry-forwards honored** (all from prior renderer stories; Story 3.11 review pass consolidated most into shared helpers):
    - Story 3.5 H5 — HTML escape every operator-supplied string.
    - Story 3.6 review L1 — `MappingProxyType` for read-only mappings.
    - Story 3.6 review H3 — message-length safety caps.
    - Story 3.9 H7 — schema registration in `event_types.py`.
    - Story 3.9 N7 — HTTP-only cross-service contract; `# noqa: IMP001 — Story 2.9 AC-16` suppression.
    - Story 3.10 H1 — section-drop ladder approach.
    - Story 3.10 H2 / 3.11 H2 — slice-before-escape on emergency `task_id`.
    - Story 3.10 H3 — model-boundary string validators (`Field(min_length, max_length)`).
    - Story 3.10 H8 — `_build_command_bullets` helper (not used here — completion has no bullet list).
    - Story 3.10 H9 — payload-type mismatch WARN before placeholder fallback.
    - Story 3.10 H10 — defensive `isinstance` fallback path covered by an explicit unit test.
    - Story 3.10 / 3.11 H11 — multi-line collapse on operator-supplied free-form text fields.
    - Story 3.10 / 3.11 H3 / M1 — UTF-16-safe codepoint cap of 1900 (parity with both renderers).
    - Story 3.10 M2 — `_RENDERERS` annotated as `MappingProxyType[str, _RenderFn]`.
    - Story 3.10 M11 — exception isolation around `_render(envelope)` in `_handle()`.
    - Story 3.10 M12 / 3.11 M10 — `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant + identity assertion.
    - Story 3.10 M14 — defense-in-depth `html.escape` even on Literal-bound strings.
    - Story 3.11 M4 — per-character HTML-escape test assertions.
    - Story 3.11 M8 — idempotent schema-registration test guard (`_COMPLETED_REGISTERED`).
    - Story 3.11 H5 — defensive final-length self-clamp on emergency tier.
    - Story 3.11 H6 / L17 — `_collapse_newlines(text)` helper for `\r\n`/`\r`/`\n` collapse.
    - Story 3.11 H12 — cap-overflow tests parametric on cap constant.
    - Story 3.11 H1 — strip trailing punctuation from operator-supplied free-text before sentence-form interpolation. (Not directly applicable to 3.12 since the header `✅ Task <id> complete.` has its own period and `summary` is rendered as a separate section, not interpolated into a sentence form. Documented for completeness.)
    - Epic-2-retro AI #1 — independent gate verify mandatory.
    - Epic-2-retro AI #4 / #5 / #10 / #12 (now Epic-1-retro AI #2-5 second-nudges) — `uv sync --all-packages`, schema-registry test isolation, no-new-conftest convention, per-test-tree mypy override. **Convention is to inline schema-registration via the established `_ensure_*_registered` pattern; see Story 3.11's `_ensure_blocker_raised_registered` for the canonical shape.**

## Tasks / Subtasks

- [x] **Task 1: Payload extension + validators** (AC: #1, #9)
  - [x] Extend `TaskCompletedPayload` with model-boundary validators on `task_id` / `summary` / `pr_url` and 7 optional FR9 fields (`pr_number`, `pr_branch`, `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state`, `blockers_count`).
  - [x] Register `("task.completed", "1.1.0", TaskCompletedPayload)` alongside existing `1.0.0` / `1.0.1` entries.
  - [x] Add 4 unit tests in `test_event_types.py`: v1.0 back-compat, oversized pr_branch rejection, negative counter rejection, invalid ci_state Literal rejection.
  - [x] Verify `check_event_registry` passes.

- [x] **Task 2: Renderer template `_render_completed`** (AC: #2, #3, #4, #5, #6, #7)
  - [x] Append `TaskCompletedPayload` to the existing `from registry_state.domain.event_types import (...)` cross-service noqa-tagged import block in `telegram_sink.py`.
  - [x] Add `_COMPLETED_MESSAGE_MAX_CHARS: int = 1900` and `_CI_STATE_EMOJI: Mapping[str, str] = MappingProxyType({"green": "✅", "red": "❌", "unknown": "❓"})` constants.
  - [x] Implement `_render_completed(envelope: EventEnvelope) -> str` with type-mismatch guard + 7-step section-drop ladder.
  - [x] Implement `_assemble_completed_sections(payload, *, include_pr, include_diff_stats, include_tests, include_ci_state, include_blockers, include_summary) -> str` helper analogous to `_assemble_blocker_sections`.
  - [x] Register `"task.completed": _render_completed` in `_RENDERERS`.
  - [x] Re-use `_collapse_newlines`, `_EMERGENCY_TASK_ID_MAX_CHARS` from prior renderer stories.

- [x] **Task 3: Renderer test coverage** (AC: #8)
  - [x] Add 11 renderer tests in `test_telegram_sink.py` covering minimal, each optional field individually, all 3 ci_state branches (parametrized), HTML escape, multi-line collapse, drop-order ladder, emergency fallback, type-mismatch, dispatcher routing identity assertion.
  - [x] Add `_completed_envelope(...)` helper with `_COMPLETED_REGISTERED` idempotent guard mirroring Story 3.11's `_ensure_blocker_raised_registered`.
  - [x] Verify all 14 new tests pass.

- [x] **Task 5: Code-review fixes pass** (3-layer adversarial Opus review — 10H / 14M / 14L = 38 patches + 7 deferred + 10 dismissed)
  - [x] All 38 patches applied; **retroactive H1 fix** in all 3 renderer emergency tiers (3.10 + 3.11 + 3.12) — `_collapse_newlines(payload.task_id)` before slice-then-escape; 3 new tests added.
  - [x] M1 zero-counter omission policy; M2 `pr_url` http(s)-scheme validator; M8 `ci_state_drift` WARN + L8 distinct fallback emoji `❔`.
  - [x] H10 parametrized 7 PR-line forms + 7 diff-stats forms; H7 6 upper-bound counter rejection tests.
  - [x] Independent gate verify: `just lint` 9/9, `just test` 975 → 1026 (+51 net, exceeded ≥1010 target by 16), `just bootstrap-verify` clean. Spine sentinel fires as expected.
  - [x] Status flipped: review → done.

- [x] **Task 4: Regression verification + atomic commit** (AC: #12)
  - [x] `just test` — confirm test count grows by ≥14 from the post-3.11-fixes baseline of 956 (target 970+).
  - [x] `just lint` 9/9 green.
  - [x] `just bootstrap-verify` clean. Run `uv sync --all-packages` first per Epic-1-retro AI #2.
  - [x] **Independent gate verify** before flipping `review → done` per Epic-2-retro AI #1.
  - [x] Note expected spine-sentinel firing in Completion Notes (modifies `services/registry-state/src/`).
  - [x] Flip `sprint-status.yaml`: `3-12-completion-summary-template: backlog → ready-for-dev → in-progress → review → done`; bump `last_updated`.
  - [x] Atomic commit with the exact title from AC-12.

## Dev Notes

### Quoted Requirements

> **FR9** (`prd.md`, see `epics.md:43`): *"Platform emits a structured completion summary (file count, line count, test count, CI state, blockers)."*

> **Epic 3 Story 3.12 AC** (`epics.md:1166-1178`):
> *Given a task emits `task.completed`*
> *When the telegram-sink renders the outbound message*
> *Then the message contains `✅ Task <id> complete. PR #<N>: <branch>. <files> files changed, <lines> lines, <tests> tests added. CI green. <blockers> blockers raised.`*

> **Story 5.13** (`epics.md:1617-1629`): future emitter that will populate the rich payload — `{files_changed, lines_added, lines_removed, tests_added, ci_state, blockers_count, pr_url?}`. Story 3.12's payload extension is **forward-compatible** with that emission contract; the only delta is whether 5.13 chooses to emit `lines: int` (combined) vs `lines_added`+`lines_removed` (split — what 3.12 picks). The split form is more informative for operators and the renderer can compose the combined form trivially when needed.

### Why Split `lines_added` / `lines_removed` Instead of FR9's Combined `<lines>`

FR9's wording shows `<lines> lines` (combined). The PRD/epic AC text uses singular `<lines>` as a placeholder. But Story 5.13's payload AC line (`epics.md:1627`) explicitly enumerates `lines_added` AND `lines_removed` as separate counters. Story 3.12 follows 5.13's split shape for two reasons:
1. Future-compatibility — when 5.13 ships its emitter, it'll populate both fields; if 3.12's payload had a single `lines: int`, 5.13 would need a payload migration.
2. Operator-readability — `234+ / 89-` is more useful than `145 lines net` for code reviewers.

The renderer's diff-stats line is `<files> files changed, <lines_added>+ / <lines_removed>- lines.` — close enough to FR9's wording while honoring 5.13's split-counter contract.

### Why CI State Uses an Emoji Map (Not a Dict-Literal Inline)

Story 3.10's `_PRE_CHECK_STATUS_EMOJI` (a `MappingProxyType` mapping pass/fail/skipped/error → ✅/❌/⏭️/⚠️) is the established pattern. Story 3.12 reuses the shape. Three reasons:
1. **Defensive**: a future status value (e.g. `"flaky"`) renders the distinct fallback `❔` (Story 3.12 review L8 — distinct from the explicit `"unknown" → "❓"` mapping so SRE has a visible drift signal) rather than crashing. A structured `renderer.ci_state_drift` WARN (Story 3.12 review M8) is also emitted to give SRE a log-side signal.
2. **Localization-ready**: when i18n lands (deferred per Epic 1 retro), the map can be parameterized by locale.
3. **Test ergonomics**: parametrized tests can iterate the map keys.

The defense-in-depth `html.escape(payload.ci_state)` (Story 3.10 M14 carry-forward, Story 3.12 review L9) is intentionally retained even though the field is `Literal["green","red","unknown"]`-bound at the model boundary: a `model_construct`-bypass payload carrying an unmapped string value (e.g. via the schema-registration race scenario) still renders safely. The runtime cost is negligible — `html.escape` on a 5-char string is microseconds.

### Why the Section-Drop Ladder Drops Diff Stats First

The diff-stats line is the longest counter section (~30 chars vs ~10-15 for tests/CI/blockers). Dropping it recovers the most cap budget per drop step. The summary line is preserved as long as possible because it's the operator-supplied human-readable description — semantically the most valuable field per FR9's intent ("scan a morning summary in one glance"). Even at the emergency one-liner, the operator can `/logs <id>` to see the full summary.

### Zero-counter omission policy (Story 3.12 review M1 / L1 / L10)

`tests_added=0`, `blockers_count=0`, `files_changed=0`, `lines_added=0`, `lines_removed=0` are treated as "no activity to report" and the corresponding line is omitted entirely. Conflating "0 tests added" with "field absent" / "pre-3.12 back-compat" was misleading; emitters that explicitly want to convey "I ran 0 tests" can do so via the summary text. The decision sidesteps L1's plural-form tension (e.g. `1 tests added.` vs `1 test added.`) by short-circuiting the most awkward case (`0 tests added.`) entirely; for `N=1` the renderer keeps the simple plural form since the alternative (singular/plural switch on every counter) adds branch density without operator-side benefit.

For diff stats: if any of `files_changed` / `lines_added` / `lines_removed` is `> 0` the line renders with whatever subset is non-zero; if ALL three are `0` (or `None`), the line is omitted. This handles the L10 contradictory-line case (`files_changed > 0` with `lines_added=0` and `lines_removed=0` would otherwise render an oddly-truncated `"5 files changed."` with no line counts — which is now the correct shape since the line counts are genuinely zero).

### `pr_branch` rendering caveats (Story 3.12 review L11)

`pr_branch` is constrained to 1..255 chars at the model boundary but accepts any ASCII content. Branch names with embedded spaces (technically invalid per git ref-name rules but model-permissible) render via the em-dash separator as `feat foo bar — https://...`, which is visually ambiguous. **See deferred D3 — `pr_branch` git ref-name pattern** for the cross-cutting validator that would reject these at the payload boundary. Story 3.12 does not add the pattern locally because D3 is a shared concern across any future renderer that surfaces branch names.

### Inline import convention for `structlog.testing` (Story 3.12 review L13)

Story 3.11 review M2 promoted `TaskApprovalRequestedPayload` and `TaskBlockerRaisedPayload` from inline imports to top-of-file. The convention is **payload classes top-level; `structlog.testing.capture_logs` inline**. The asymmetry is deliberate: payload classes appear in test-helper signatures (`_approval_envelope` / `_blocker_envelope` / `_completed_envelope`) where forward-ref resolution is needed; `structlog.testing.capture_logs` is a context-manager used only inside test bodies and the inline-import keeps the dependency local to the (typically one or two) tests that exercise log capture. Story 3.12 follows the same convention: `TaskCompletedPayload` is top-level; `structlog.testing` is inline in `test_render_completed_payload_type_mismatch_logs_and_falls_back` and `test_render_completed_minimal_emits_no_payload_type_mismatch_warn`.

### Inheritance from Stories 3.10 + 3.11

Story 3.12 inherits the following **unchanged** infrastructure:

| Inherited primitive | Source | How 3.12 uses it |
|---|---|---|
| `_RENDERERS: MappingProxyType[str, _RenderFn]` dispatcher | Story 3.10, 3.11 hardened | Append one entry. |
| `_render(envelope)` placeholder fallback | Story 3.10 | Already routes by type; no change. |
| `_extract_task_id(envelope)` helper | Story 3.10 | Re-used in type-mismatch fallback. |
| `_collapse_newlines(text)` helper | Story 3.11 review L17 | Re-used at all 3 collapse sites (`summary`, `pr_branch`, `pr_url`). |
| `_EMERGENCY_TASK_ID_MAX_CHARS = 64` | Story 3.10 H2 / 3.11 H2 | Re-used in Step 7 emergency fallback (slice-before-escape). |
| 1900-char total cap discipline | Story 3.10 / 3.11 H3 | New `_COMPLETED_MESSAGE_MAX_CHARS = 1900` for parity. |
| Defensive final-length self-clamp on emergency tier | Story 3.11 H5 | Re-used at Step 7. |
| `try/except` around `_render(envelope)` in `_handle()` | Story 3.10 M11 | Renderer-exception isolation; covers completion renderer transparently. |
| `_RENDERERS ⊆ _DELIVERABLE_EVENT_TYPES` invariant test | Story 3.10 M12, 3.11 M10 | Passes for the new entry without changes since `task.completed` was already in the allowlist (Story 3.9 L15). |
| Cross-service `# noqa: IMP001 — Story 2.9 AC-16` import block | Stories 3.10, 3.11 | Append `TaskCompletedPayload`. Cluster grows to 4 entries; Story 3.10 L5 deferred-refactor tracker still stands. |
| Idempotent `_ENSURE_*_REGISTERED` test guard pattern | Story 3.10 M8, 3.11 H11 | Mirror as `_COMPLETED_REGISTERED + _ensure_completed_registered`. |
| HTML-escape per-character test assertions | Story 3.11 M4 | Mirror in `test_render_completed_html_escapes_*`. |
| Cap-overflow tests parametric on cap constant | Story 3.11 H12 | All 3.12 cap-overflow tests use `_COMPLETED_MESSAGE_MAX_CHARS - X`. |

**Story 3.12 review L12 — citations are decorative, primitives are load-bearing.** The "Story 3.X review YN" citations sprinkled through this spec and source comments are documentary; future-me cannot grep `git log` to mechanically confirm a carry-forward still holds. The actual mechanism is the **named primitives in code** — `_RENDERERS`, `_collapse_newlines`, `_EMERGENCY_TASK_ID_MAX_CHARS`, `_assemble_completed_sections`, `_build_pr_line`, `_build_diff_stats_line`. If a future refactor renames one of these, every citing comment becomes stale — but the test that asserts identity (`_RENDERERS["task.completed"] is _render_completed`) or the parametric cap test (uses `_COMPLETED_MESSAGE_MAX_CHARS`) will fail loudly.

### Architecture References

- `prd.md` — FR9 statement.
- `epics.md:43` — FR9 capability mapping.
- `epics.md:1166-1178` — Story 3.12 user story + AC.
- `epics.md:1617-1629` — Story 5.13 payload emission spec (future emitter).
- `architecture.md` — additive-only schema evolution (NFR-M3); telegram_sink.py outbound rendering placement.
- Story 2.1 — schema registry; existing `register("task.completed", "1.0.0"/"1.0.1", ...)`.
- Story 3.5 H5 — HTML escape contract.
- Story 3.6 H3, review L1 — message-length safety + `MappingProxyType`.
- Story 3.9 — renderer dispatcher placeholder + `_DELIVERABLE_EVENT_TYPES` allowlist (already includes `task.completed`).
- Story 3.10 — first message-template; established dispatcher + section-drop + UTF-16 cap + multi-line collapse + payload-type-mismatch + slice-before-escape patterns.
- Story 3.11 — second message-template; consolidated `_collapse_newlines`, parametric cap-overflow tests, identity-asserted dispatcher routing.
- Story 3.13 — future sibling (self-recovered summary FR16); 3.12's pattern carries forward.
- Story 5.13 — future emitter populating the rich payload Story 3.12 declares.
- Epic-1-retro AI #2/#3/#4/#5 — `uv sync --all-packages` recipe / schema-registry isolation / no-new-conftest / per-test-tree mypy override conventions (second-nudge from Epic 2 retro; this story should USE them).
- Epic-2-retro AI #1 — independent gate verify mandatory.

### Project Structure Notes

- Renderer: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`
- Renderer test: `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py`
- Payload model: `services/registry-state/src/registry_state/domain/event_types.py`
- Payload test: `services/registry-state/src/registry_state/domain/test_event_types.py`
- Spec: `_bmad-output/implementation-artifacts/3-12-completion-summary-template.md` (this file).
- Sprint status: `_bmad-output/implementation-artifacts/sprint-status.yaml`.

No detected conflicts with unified project structure — Story 3.12 sits in directories Stories 3.9 / 3.10 / 3.11 already established.

### Predicted File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — extend `TaskCompletedPayload` with 8 optional FR9 fields + `task_id`/`summary`/`pr_url` validators; register schema 1.1.0 |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — +4 tests |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — append `TaskCompletedPayload` to noqa import block; add `_COMPLETED_MESSAGE_MAX_CHARS` and `_CI_STATE_EMOJI` constants; add `_assemble_completed_sections` helper and `_render_completed` function; register entry in `_RENDERERS` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — +11 tests; shared `_completed_envelope(...)` helper with `_COMPLETED_REGISTERED` idempotent guard |
| `_bmad-output/implementation-artifacts/3-12-completion-summary-template.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review → done` + `last_updated` bump |

## Review Findings

Three-layer adversarial review (Blind Hunter / Edge Case Hunter / Acceptance Auditor — three Opus agents, no shared context) of the in-tree diff on 2026-05-01. User directive "fix all issues even minors" applies. After dedup across layers: **10 High · 14 Medium · 14 Low = 38 patches**, 7 deferred (broader-concern refactors / pre-existing 3.10-3.11 patterns), 10 dismissed (noise / project-wide conventions).

### High severity

- [x] [Review][Patch] **H1 — Step 7 emergency one-liner uses raw uncollapsed `task_id`** [Blind#1,11 + Edge#4,5]: `_render_completed` Step 7 slices `payload.task_id` BEFORE `_collapse_newlines`. Same gap exists in `_render_blocker_raised` (Story 3.11) and `_render_approval_request` (Story 3.10). A `task_id` containing `\n` smuggles a newline into the supposedly single-line emergency message. Fix: apply `_collapse_newlines(payload.task_id)` before slice in all 3 emergency tiers (3.10 + 3.11 + 3.12 retroactive). [telegram_sink.py:emergency tier — 3 renderers]
- [x] [Review][Patch] **H2 — `_build_diff_stats_line` silently drops `lines_added` or `lines_removed` when paired with `files_changed`** [Blind#3 + Edge#1,2]: 5 named branches cover (fc+la+lr), (fc-only), (la+lr), (la-only), (lr-only). Combinations (fc+la-no-lr) and (fc+lr-no-la) fall through to `if fc is not None: return f"{fc} files changed."` — silent data loss. Fix: explicitly handle both 2-of-3 combinations: `f"{fc} files changed, {la}+ lines."` and `f"{fc} files changed, {lr}- lines."` [telegram_sink.py:_build_diff_stats_line]
- [x] [Review][Patch] **H3 — Section-drop ladder Steps 3-6 untested at boundary** [Blind#9 + Edge#9 + Auditor#7]: only Step 2 transition is exercised (`test_render_completed_total_cap_drops_in_spec_order`). Steps 3 (drop blockers), 4 (drop tests), 5 (drop CI), 6 (drop PR) have NO boundary tests. Fix: add 4 parametrized tests, each sized parametric on `_COMPLETED_MESSAGE_MAX_CHARS - X` so exactly that step fits while the previous one overflows. [test_telegram_sink.py]
- [x] [Review][Patch] **H4 — Step 6 → Step 7 boundary untested** [Blind#10]: emergency-fallback test jumps directly to Step 7 with `cap + 90` — the transition where `header + "\n\n" + summary` exactly equals `_COMPLETED_MESSAGE_MAX_CHARS` should still take Step 6, not fall through to Step 7. Fix: add a test sized at the exact threshold. [test_telegram_sink.py]
- [x] [Review][Patch] **H5 — No surrogate-pair / wide-emoji length math test** [Blind#15]: Story 3.10 M1 / 3.11 H3 cap-tightening was motivated by UTF-16 surrogate-pair safety. No test feeds a `summary` containing 4-byte UTF-8 emoji at the boundary to verify the cap actually defends against wire-limit breach. Fix: add `test_render_completed_utf16_surrogate_pair_safety` with `summary="😀" * (_COMPLETED_MESSAGE_MAX_CHARS // 2)`; assert `len(text.encode("utf-16-le")) // 2 <= 4096`. [test_telegram_sink.py]
- [x] [Review][Patch] **H6 — Emergency-clamp test absent for completed renderer** [Blind#16]: Story 3.11 added `test_render_blocker_raised_emergency_clamps_to_cap_when_task_id_oversized`. The completed renderer has the same defensive `if len(result) > cap: result = result[:cap]` (Story 3.11 H5 carry-forward) but no equivalent test feeding a 64-char `<` task_id to verify clamp doesn't split mid-entity. Fix: add `test_render_completed_emergency_clamps_to_cap_when_task_id_oversized`. [test_telegram_sink.py]
- [x] [Review][Patch] **H7 — Upper-bound counter rejection tests missing (L9 carry-forward gap)** [Auditor#1 + Blind#33]: `pr_number=10**9+1`, `lines_added=10**9+1`, `lines_removed=10**9+1`, `files_changed=10**6+1`, `tests_added=10**6+1`, `blockers_count=10**6+1` — all should raise `ValidationError` per the AC-1 `le=` bounds; none tested. Story 3.10 review L9 introduced the per-field upper-bound discipline; 3.12 honors it in code but not in tests. Fix: add 6 parametrized boundary-rejection tests in `test_event_types.py`. [test_event_types.py]
- [x] [Review][Patch] **H8 — AC-6 pure-`def` invariant not tested** [Auditor#2]: AC-6 says renderer is `def`, not `async def`. Story 3.11 review L8 added `inspect.iscoroutinefunction(_render_blocker_raised) is False` test. 3.12 has no equivalent. Fix: add `inspect.iscoroutinefunction(_render_completed) is False` assertion to dispatcher routing test or as a standalone test. [test_telegram_sink.py]
- [x] [Review][Patch] **H9 — Positive-path no-WARN assertion missing** [Auditor#3 + Story 3.11 L11 carry-forward]: Story 3.11 L11 added "happy-path test asserts no `payload_type_mismatch` WARN fired". 3.12's positive-path tests don't capture logs and assert silence. Fix: wrap minimal happy-path test with `structlog.testing.capture_logs()` and assert `not any(rec.get("event") == "renderer.payload_type_mismatch" for rec in captured)`. [test_telegram_sink.py]
- [x] [Review][Patch] **H10 — 5 of 7 PR-line forms + 4 of 5 diff-stats forms untested** [Blind#4,5 + Edge#10,11 + Auditor]: only `pr_full` (3-true) and `pr_partial` (number-only) are tested for PR-line; only `diff_stats_full` (3-true) is tested for diff-stats. Per H2, 2 missing diff-stats forms become 7 (fc+la, fc+lr). Fix: parametrize both helpers across all branches — 7 PR forms + 7 diff-stats forms = 14 new test cases. [test_telegram_sink.py]

### Medium severity

- [x] [Review][Patch] **M1 — Zero-counter values render misleading lines** [Blind#24,25,26,27 + Edge#14,15,19]: `tests_added=0` renders `"0 tests added."`; `blockers_count=0` renders `"0 blockers raised."`; `files_changed=0` renders `"0 files changed."`; `lines_added=0` + `lines_removed=0` renders `"0+ / 0- lines."`. Conflates "no activity" with "field absent". Fix: treat `0` as "do not render this line" — render only when `value is not None and value > 0` for `tests_added` / `blockers_count`; for diff stats, render only when at least one of (fc, la, lr) is `> 0`. Document in Dev Notes that counts default to "not present" semantics. [telegram_sink.py:_assemble_completed_sections + _build_diff_stats_line]
- [x] [Review][Patch] **M2 — `pr_url` validator accepts `javascript:` / `data:` schemes** [Blind#7]: `Field(min_length=1, max_length=500)` doesn't constrain scheme. After `html.escape`, Telegram clients with link-affordance heuristics may still surface as a clickable string. Fix: add `pattern=r"^https?://"` constraint or a `field_validator` rejecting non-http(s) schemes. Add a rejection test. [event_types.py:TaskCompletedPayload + test_event_types.py]
- [x] [Review][Patch] **M3 — `pr_url` upper bound (500), `pr_branch` upper bound (255), `summary` upper bound (2000) untested** [Blind#33,34,35]: Story 3.11 review M5/M6/M7 added boundary tests for blocker fields. 3.12 added the validators but not the boundary tests. Fix: add 4 parametrized rejection tests for `pr_url > 500`, `pr_branch > 255`, `pr_branch == ""`, `pr_url == ""`. [test_event_types.py]
- [x] [Review][Patch] **M4 — `_collapse_newlines` not tested for `\r\n` / `\r`** [Blind#14 + Edge unicode]: existing test only covers `\n`. Story 3.11 H6 carry-forward claim is `\r\n` / `\r` / `\n` all collapse. Add `test_render_completed_collapses_crlf_and_bare_cr` with `summary="line1\r\nline2"` and `summary="line1\rline2"`. Also add U+2028 / U+2029 as a known-gap deferred item (D2). [test_telegram_sink.py]
- [x] [Review][Patch] **M5 — `pr_branch` / `pr_url` containing only newlines collapse to empty + render malformed line** [Edge#7]: `pr_branch="\n\n\n"` passes `min_length=1` (3 chars on raw input), then `_collapse_newlines` produces `"   "` (whitespace) → `html.escape` → renders `"PR #42:    "` (trailing whitespace, no value). Fix: after collapse, treat `branch_esc.strip() == ""` as None-equivalent. [telegram_sink.py:_build_pr_line]
- [x] [Review][Patch] **M6 — `tests/integration/test_task_thread_binding.py` assertion weakened** [Blind#47 + Blind#48]: changed from exact-shape `text.startswith("Task t-") AND text.endswith(": task.completed")` to `text.startswith("✅ Task ") AND "complete." in text`. The new assertion would pass for ANY message starting with `✅ Task` containing `complete.` — including the emergency one-liner shape. Fix: assert the full minimal shape `result == f"✅ Task {task_id}\n\n{summary}"` for the test fixture. [tests/integration/test_task_thread_binding.py + test_telegram_sink.py:test_sink_dispatches_on_task_event]
- [x] [Review][Patch] **M7 — No test for `extra="forbid"` rejection on `TaskCompletedPayload`** [Blind#32]: `model_config = ConfigDict(..., extra="forbid")` was tightened on the new model. Without an explicit test, a typo in field name (e.g., `pr_numbar=42`) would silently pass at the application layer. Fix: add `test_task_completed_payload_rejects_extra_field`. [test_event_types.py]
- [x] [Review][Patch] **M8 — `ci_state="flaky"` via `model_construct` bypass renders ❓ silently with no SRE drift signal** [Edge#3]: `_CI_STATE_EMOJI.get(payload.ci_state, "❓")` falls back to ❓; `html.escape(payload.ci_state)` renders the unknown literal value. No log/warn for the drift. Fix: when `ci_state not in _CI_STATE_EMOJI`, emit `_log.warning("renderer.ci_state_drift", value=payload.ci_state, expected=list(_CI_STATE_EMOJI.keys()))` before falling back. [telegram_sink.py:_assemble_completed_sections CI-state branch]
- [x] [Review][Patch] **M9 — Test sizing docstring arithmetic is wrong** [Blind#12]: `test_render_completed_total_cap_drops_in_spec_order` docstring claims "header 56 + PR 16 + diff 36 + tests 15 + CI 11 + blockers 18 + 2*5 separators". Header is ~54 chars; separators between 7 sections = 6 not 5 (`2*6=12` not `2*5=10`). Fix: recompute or replace with a programmatic assertion `expected_size = len(header) + sum(...)` rather than hand-math. [test_telegram_sink.py]
- [x] [Review][Patch] **M10 — `test_render_completed_minimal` fragile slicing assertion** [Blind#19]: uses `result.split("complete.")[0] + result.split("complete.")[1].split("task complete")[0]` — assumes `"complete."` and `"task complete"` substring cardinality. Fragile if summary ever contains `"complete."`. Fix: replace with structural `assert "PR" not in result` + `assert "Diff" not in result` + similar for tests/CI/blockers. [test_telegram_sink.py:test_render_completed_minimal]
- [x] [Review][Patch] **M11 — HTML-escape test missing assertion for bare `&` slipping past escape** [Edge#17 + Blind#40]: per-character checks cover `<` and `>` after stripping `&lt;` / `&gt;`; missing equivalent for `&`. Fix: add `assert "&" not in result.replace("&lt;", "").replace("&gt;", "").replace("&amp;", "").replace("&quot;", "").replace("&#x27;", "")`. [test_telegram_sink.py:test_render_completed_html_escapes_*]
- [x] [Review][Patch] **M12 — Wire-format v1.0.0 back-compat untested end-to-end** [Edge#19 + Blind#42]: `test_task_completed_payload_v1_0_back_compat` constructs a v1.1.0-shaped payload object directly; doesn't deserialize a raw v1.0.0 wire-format dict through the schema-registry lookup. Fix: add `test_task_completed_v1_0_0_envelope_renders_through_dispatcher` that builds an envelope with `schema_version="1.0.0"` and minimal payload (`{task_id, summary, pr_url}`), routes through `_render(envelope)`, asserts no error and a sensible output. [test_telegram_sink.py]
- [x] [Review][Patch] **M13 — `_build_pr_line` final fall-through unguarded** [Blind#2]: after exhausting 7 named branches, the implicit final return depends on `pr_number is not None`. If a future field is added, fall-through silently misrenders. Fix: add an explicit `assert pr_number is not None, "exhausted PR-line branches"` before the final return, or `else: raise AssertionError(...)`. [telegram_sink.py:_build_pr_line]
- [x] [Review][Patch] **M14 — Counter caps inconsistent (10**6 for some, 10**9 for others)** [Blind#44]: `files_changed`/`tests_added`/`blockers_count` capped at `10**6`; `lines_added`/`lines_removed` capped at `10**9`. The 1M cap is "more than enough for a reasonable PR" but a monorepo refactor (or a `task.completed` from non-PR context) could legitimately exceed. Fix: align all counters at `10**9` for consistency, OR document the semantic distinction in event_types.py docstring ("file-level counts are 1M; line-level are 1B"). [event_types.py:TaskCompletedPayload]

### Low severity

- [x] [Review][Patch] **L1 — `tests_added` plural-form even when N==0 or N==1** [Blind#23,26]: `f"{N} tests added."` produces "0 tests added." and "1 tests added.". Fix in M1 (omit on 0) handles N==0; for N==1, accept fixed plural form OR conditional `"test" if N == 1 else "tests"`. Document decision in Dev Notes. [telegram_sink.py + Dev Notes]
- [x] [Review][Patch] **L2 — Dispatcher routing test uses weak `startswith("✅ Task ")` shape check** [Blind#28]: identity assertion `_RENDERERS["task.completed"] is _render_completed` is the real coverage. Either remove the redundant startswith OR strengthen by also asserting `not result.startswith("Task ")` (placeholder fallback shape, no emoji). Fix: tighten to `assert result.startswith("✅ Task ") and not result.startswith("Task ")`. [test_telegram_sink.py:test_render_dispatcher_routes_completed_to_renderer]
- [x] [Review][Patch] **L3 — `_render_completed` return type not asserted** [Blind#41]: type annotation `-> str` is mypy-only; runtime regression to `None` would silently break Telegram dispatch. Fix: add `assert isinstance(result, str)` to minimal happy-path test. [test_telegram_sink.py]
- [x] [Review][Patch] **L4 — Schema registration ordering documentation missing** [Blind#42]: `register("task.completed", "1.1.0", ...)` lands AFTER `1.0.0`/`1.0.1`; if registry's "latest" lookup is order-dependent, a future `1.0.2` would overwrite `1.1.0` semantics. Fix: add a comment block at the registration site documenting the version-ordering convention; OR add a unit test asserting `schema_registry.lookup("task.completed", default_to_latest=True)` returns `("1.1.0", TaskCompletedPayload)`. [event_types.py + test_event_types.py]
- [x] [Review][Patch] **L5 — `pr_number` `ge=1` mismatches non-GitHub VCS** [Blind#43]: GitHub PRs start at 1 (correct); other systems may differ. Fix: add a one-line comment to the `pr_number` field docstring documenting the GitHub-PR assumption. [event_types.py:TaskCompletedPayload]
- [x] [Review][Patch] **L6 — Literal-error-code not asserted in `test_task_completed_payload_rejects_invalid_ci_state`** [Blind#45]: test asserts `ValidationError` raised but doesn't match the specific `literal_error` code from Pydantic. Fix: extend with `assert exc_info.value.errors()[0]["type"] == "literal_error"`. [test_event_types.py]
- [x] [Review][Patch] **L7 — `_DELIVERABLE_EVENT_TYPES` membership pre-existence not asserted** [Blind#49]: M12 invariant test passes for new entries because `task.completed` was added in Story 3.9. If a future refactor derives `_DELIVERABLE_EVENT_TYPES` from `_RENDERERS.keys()`, the invariant becomes vacuous. Fix: add `test_task_completed_already_in_deliverable_event_types_per_story_3_9` with explicit fixture confirming membership pre-existed. [test_telegram_sink.py]
- [x] [Review][Patch] **L8 — `_CI_STATE_EMOJI` defensive fallback `"❓"` collides with explicit `"unknown" → "❓"`** [Blind#37,38]: a future `Literal["green","red","unknown","flaky"]` adding `"flaky"` without updating the map would render as `❓` AND silently collide with `"unknown"`. Fix: change defensive fallback emoji to a distinct sentinel (e.g. `"⁉️"` or `"❔"`) so unmapped status visually distinguishes from explicit "unknown". Document in module comment. [telegram_sink.py:_CI_STATE_EMOJI]
- [x] [Review][Patch] **L9 — `ci_state` defense-in-depth `html.escape` adds runtime cost for Literal-bound value** [Blind#36]: comment says "no-op for current values, drift-safe". Defensible. Fix: add inline note explaining the cost is intentional carry-forward from Story 3.10 M14, not accidental. [telegram_sink.py:_assemble_completed_sections CI line]
- [x] [Review][Patch] **L10 — `lines_added=0, lines_removed=0` with `files_changed > 0` would render contradictory line** [Blind#27]: covered by M1's "render only when > 0" policy for diff stats. Document the resulting "0 files changed (with no line stats)" omission case in Dev Notes "Why the Section-Drop Ladder Drops Diff Stats First". [telegram_sink.py + Dev Notes]
- [x] [Review][Patch] **L11 — `pr_branch` with embedded space renders ambiguous `branch — url` line** [Edge#18]: branches with spaces (technically invalid per git ref-name rules but accepted by the model) render as `feat foo bar — https://...` where the em-dash is visually ambiguous. Fix: documented in Dev Notes as a "see deferred D3 — pr_branch git ref-name pattern" reference. [Dev Notes]
- [x] [Review][Patch] **L12 — Carry-forward citation maintenance overhead** [Blind#50]: spec cites "Story 3.6 review L1 / Story 3.7 H4" etc. without programmatic linkage. Future-me cannot grep to confirm carry-forwards still hold. Fix: add a one-line note to the Inheritance table acknowledging citations are decorative; the actual mechanism is the named primitives in code (`_RENDERERS`, `_collapse_newlines`, etc.). [Inheritance table in spec]
- [x] [Review][Patch] **L13 — Inline `import structlog.testing` is project-wide convention** [Auditor#23]: Story 3.11 review M2 carry-forward says "top-level imports". Auditor verified the project-wide convention is inline imports for `structlog.testing`. Fix: add a one-line note in Dev Notes acknowledging the convention deviation from Story 3.11 M2 in this specific case (`structlog.testing` is inline; `TaskCompletedPayload` is top-level). [Dev Notes]
- [x] [Review][Patch] **L14 — `_EMERGENCY_TASK_ID_MAX_CHARS` reference unverifiable from diff alone** [Blind#17]: the constant is referenced but not introduced in this diff; reader must check Story 3.10/3.11 to verify it equals 64. Fix: add an inline comment at the call site documenting the constant's origin and value (`# _EMERGENCY_TASK_ID_MAX_CHARS = 64 — Story 3.10 H2 carry-forward`). [telegram_sink.py:_render_completed emergency tier]

### Deferred (broader scope or pre-existing)

- [x] [Review][Defer] **D1 — `task_id` regex pattern absent** [Blind#8] — broader concern; affects all renderers; needs uniform `pattern=` validator across `Task*Payload` models. Defer to a cross-cutting "task_id format hardening" story.
- [x] [Review][Defer] **D2 — U+2028 / U+2029 LINE/PARAGRAPH SEPARATOR survive `_collapse_newlines`** [Edge#6] — broader concern; would require widening the `_collapse_newlines` helper across all 3 renderers. Defer to a future "unicode-line-break hardening" story.
- [x] [Review][Defer] **D3 — `pr_branch` accepts characters git ref-name disallows** [Blind#6] — broader concern; needs git-ref-name pattern validator (potentially shared with other branch-name fields). Defer to a future cross-cutting validation story.
- [x] [Review][Defer] **D4 — `pr_url` already-escaped `&amp;amp;` produces double-escape** [Edge#8] — operator-supplied input quality issue; documented behavior. Defer to a future "input-sanitization hardening" story.
- [x] [Review][Defer] **D5 — `_COMPLETED_REGISTERED` global mutable flag** [Blind#30] — established 3.10 M8 / 3.11 H11 pattern across 4+ test helpers; would require parallel refactor. Defer to a "test-helper consolidation" story; pattern is consistent.
- [x] [Review][Defer] **D6 — `_completed_envelope` `Random(312)` fixed seed** [Blind#29] — established 3.10/3.11 pattern (`Random(311)`, `Random(789)`); consistency wins over isolation given pytest single-threaded default.
- [x] [Review][Defer] **D7 — `isinstance` blocks subclasses (docstring clarity)** [Blind#18] — docstring polish across 3 renderers; defer to a docs sweep.

### Dismissed (false positives / out-of-scope)

- N1: PR-line "all-three-None" branch is unreachable [Blind#2] — early `None` return guards this; explicit assert added in M13 patch.
- N2: `test_render_completed_with_pr_partial` weak negative `assert "feat/" not in result` [Blind#20] — covered by M10 patch.
- N3: `test_render_completed_with_diff_stats_full` separator-placement assertion [Blind#21] — covered by M9 / structural-assertion patch.
- N4: `test_render_completed_with_ci_state` shape-only check [Blind#22] — `green`/`red`/`unknown` have no escapable chars; vacuous defense-in-depth test would add no value.
- N5: `_render_completed` private-symbol cross-import in test [Blind#46] — established 3.10/3.11 convention; documented.
- N6: `_log` reference unverifiable from diff [Blind#38] — `_log = structlog.get_logger(...)` introduced in Story 3.9; not a 3.12 concern.
- N7: `captured` list iteration with `any(...)` [Blind#39] — pytest produces an adequate failure message; refactor is style preference.
- N8: `tests_added=0` singular-form pluralization [Blind#23] — covered by M1 omit-on-zero policy.
- N9: Story 3.9 `_DELIVERABLE_EVENT_TYPES` set-population assertion [Blind#49] — covered by L7 patch.
- N10: Carry-forward citations decorative [Blind#50] — covered by L12 patch.

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (executor agent on Opus, single-pass implementation, 51 tool uses, ~11 min wall-clock; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Single executor pass completed all 3 implementation tasks cleanly. Smaller scope than Stories 3.10/3.11 because the dispatcher infrastructure is fully hardened — 3.12 is a pure additive renderer with no retroactive 3.10/3.11 fixes needed.
- **Two helper functions extracted beyond spec:** `_build_pr_line` (handles the 7-form PR-line composition cleanly) and `_build_diff_stats_line` (handles the 5-form diff-stats composition). Pure refactor for readability — all behavior matches AC-2's section table. Spec named only `_assemble_completed_sections`; the extra helpers reduce branching density inside the assembler.
- **One in-scope test-file update beyond spec's predicted list:** `tests/integration/test_task_thread_binding.py` and the unit-test sibling `test_sink_dispatches_on_task_event` had baseline assertions matching Story 3.9's placeholder shape `Task <id>: task.completed` — they had to update to Story 3.12's typed shape `✅ Task <id> complete.\n\n<summary>` once 3.12 templates that exact event type. AC-10 "Not modifiable" list does not include `tests/integration/`; treated as in-scope baseline-update.
- **Independent gate verification (orchestrator):** `just lint` 9/9 green. `just test` 975 passed (1 expected spine-sentinel failure). `just bootstrap-verify` clean (13 workspace imports).
- **Pre-existing dev-tooling quirk:** `uv sync --no-dev` strips `asgi-lifespan` (Epic-1-retro AI #2 second-nudge — re-discovered 9th time across project history). Restored via `uv sync --all-packages` before `just lint`. Until Epic-1-retro AI #2 lands the documentation note, this re-discovery will continue.

### Completion Notes List

- **All 13 ACs satisfied.**
- **Test count: +19 net** (4 registry-state + 15 clawhip-daemon = 19; spec targeted ≥14). Post-3.11-fixes baseline 956 → 975. The clawhip-daemon delta is 15 because `test_render_completed_with_ci_state` is parametrized over 3 ci_state values per AC-8's instruction "Parametrize over `('green', '✅'), ('red', '❌'), ('unknown', '❓')`" — 3 collected items from 1 test function. Net delta-by-design, not deviation.
- **7-step section-drop ladder** implemented as pure `_assemble_completed_sections(...)` helper with progressive `include_X` flag toggling — mirrors Story 3.11's `_assemble_blocker_sections` boolean-bag pattern. Step order: full → drop diff stats (longest line; biggest budget recovery per drop) → drop blockers → drop tests → drop CI → drop PR → emergency. **Summary line preserved until emergency tier** per AC-5 rationale.
- **All 17 carry-forwards from Stories 3.10 + 3.11 honored** (slice-before-escape, `_collapse_newlines`, 1900 cap, final-length self-clamp, type-mismatch WARN, `MappingProxyType[str, _RenderFn]`, parametric cap-overflow tests, per-character HTML-escape, identity-asserted dispatcher routing, `_COMPLETED_REGISTERED` idempotent guard, top-level test imports, defense-in-depth `html.escape` on Literal-bound `ci_state`, L9 per-field counter overflow bounds).
- **Defensive `isinstance` fallback** handles raw-dict envelopes via `EventEnvelope.model_construct(...)` — falls back to placeholder shape with `renderer.payload_type_mismatch` WARN logged, matching Story 3.10 H9 / 3.11 carry-forward.
- **Pre-existing exception-isolation in `_handle()`** covers the new renderer transparently (Story 3.10 review M11). No new try/except wrapper.
- **Cross-service noqa import block grew to 4 entries** (`PreCheckResults`, `TaskApprovalRequestedPayload`, `TaskBlockerRaisedPayload`, `TaskCompletedPayload`). Story 3.10 L5 deferred-refactor tracker stands: a future story can move payload models to `packages/events/event-payloads/` to clean the noqa cluster. Story 3.13 will be the 5th entry — likely the right cliff for the refactor.
- **Spine sentinel fired as expected** — modifies `services/registry-state/src/registry_state/domain/event_types.py` (AC-1). Accepted disposition per AC-12 / Story 3.10 AC-14 carry-forward.
- **No code-shape deviations from spec.** The 2 extra refactor helpers (`_build_pr_line`, `_build_diff_stats_line`) and the 1 baseline test-update (`test_task_thread_binding.py` + `test_sink_dispatches_on_task_event`) are documented above as in-scope additions.
- **`Status: review` set; sprint-status flipped to `review`.** No commit performed (per OMC commit policy — user runs `/bmad-code-review` workflow next, then commit after review approval).

### Change Log

| Date | Change |
|---|---|
| 2026-05-01 | Review pass on commit-pending diff (Blind Hunter / Edge Case Hunter / Acceptance Auditor — three Opus agents, no shared context): 93 raw findings → 38 unique patches applied (10H / 14M / 14L) + 7 deferred + 10 dismissed. **Retroactive H1 fix in all 3 renderers (3.10 + 3.11 + 3.12)**: emergency tier now applies `_collapse_newlines(payload.task_id)` before slice-then-escape — defends against `task_id` containing `\n` smuggling a newline into the supposedly single-line emergency message; 3 new tests added. Other notable patches: M1 zero-counter omission policy; M2 `pr_url` `pattern=r"^https?://"` validator; M8 `ci_state_drift` WARN + L8 distinct fallback emoji `❔`; H10 parametrized 7 PR-line + 7 diff-stats forms; H7 6 upper-bound counter rejection tests. Test count 975 → 1026 visible (+51 net; target was ≥1010, exceeded by 16). Spine sentinel fired as expected. 9/9 lint green; bootstrap-verify clean. |
| 2026-05-01 | Story 3.12 implemented: `TaskCompletedPayload` schema 1.0.0/1.0.1 → 1.1.0 (additive — `pr_number`, `pr_branch`, `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state: Literal["green","red","unknown"]`, `blockers_count` as 8 optional FR9 fields; existing `task_id`/`summary`/`pr_url` got Story 3.10 H3 model-boundary validators). New `_render_completed(envelope)` template renders FR9 message (header `✅ Task <id> complete.` + optional PR line in 7 forms based on which of `pr_number`/`pr_branch`/`pr_url` are populated + optional diff-stats line in 5 forms based on `files_changed`/`lines_added`/`lines_removed` populated combinations + optional `<N> tests added.` + optional `CI: <emoji> <state>` + optional `<N> blockers raised.` + always-present summary; all HTML-escaped; `summary`/`pr_branch`/`pr_url` `_collapse_newlines`'d). New `_CI_STATE_EMOJI` MappingProxyType (green→✅, red→❌, unknown→❓; defensive ❓ fallback for future status values). Two refactor helpers extracted beyond spec: `_build_pr_line` (7-form composition) and `_build_diff_stats_line` (5-form composition). Message-length safety per Story 3.11 H3/H5 carry-forward (`_COMPLETED_MESSAGE_MAX_CHARS = 1900` parity with approval/blocker; 7-step section-drop ladder full → diff stats → blockers → tests → CI → PR → emergency; emergency one-liner uses slice-before-escape on `task_id` and final-length self-clamp). Renderer registered in `_RENDERERS` dispatch table alongside `task.approval_requested` and `task.blocker_raised`. 19 new tests across 2 files (4 payload validation + 15 renderer-collected including 3-way ci_state parametrization). Test count 956 → 975 visible (+19 net). 9/9 lint gates green; bootstrap-verify clean. **Third message-template story** plugging into Story 3.9's dispatcher; only Story 3.13 (self-recovered summary FR16) remains in the message-template quartet. |

### File List

| File | Change |
|---|---|
| `services/registry-state/src/registry_state/domain/event_types.py` | Modified — extended `TaskCompletedPayload` with 8 optional FR9 fields (`pr_number`, `pr_branch`, `files_changed`, `lines_added`, `lines_removed`, `tests_added`, `ci_state` Literal, `blockers_count`) + Story 3.10 H3 model-boundary validators on `task_id`/`summary`/`pr_url`; registered schema `1.1.0` alongside `1.0.0`/`1.0.1`; updated `__all__` if needed |
| `services/registry-state/src/registry_state/domain/test_event_types.py` | Modified — added `TaskCompletedPayload` to test imports; added 4 unit tests (v1.0 back-compat, oversized `pr_branch` rejection, negative-counter rejection across all 5 counter fields, invalid `ci_state` Literal rejection) |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py` | Modified — appended `TaskCompletedPayload` to cross-service `# noqa: IMP001` import block (4th entry); added `_COMPLETED_MESSAGE_MAX_CHARS: int = 1900` and `_CI_STATE_EMOJI: Mapping[str, str] = MappingProxyType({...})` constants; added `_build_pr_line(payload)`, `_build_diff_stats_line(payload)`, `_assemble_completed_sections(payload, *, include_pr, include_diff_stats, include_tests, include_ci_state, include_blockers, include_summary)` helpers; added `_render_completed(envelope) -> str` with type-mismatch guard + 7-step section-drop ladder; registered `"task.completed": _render_completed` in `_RENDERERS` `MappingProxyType` |
| `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/test_telegram_sink.py` | Modified — added top-level `TaskCompletedPayload` import (Story 3.11 M2 pattern); added `_COMPLETED_MESSAGE_MAX_CHARS` / `_render_completed` to imports from telegram_sink; added `_COMPLETED_REGISTERED` module-level flag + `_ensure_completed_registered()` idempotent guard (Story 3.10 M8 / 3.11 H11 pattern); added `_completed_envelope(...)` helper; added 11 tests (12 collected including 3-way `ci_state` parametrization); updated 1 baseline Story 3.9 test (`test_sink_dispatches_on_task_event`) to assert new typed-renderer output shape |
| `tests/integration/test_task_thread_binding.py` | Modified — updated baseline E2E assertion (Story 3.9 placeholder `Task <id>: task.completed` → Story 3.12 typed `✅ Task <id> complete.\n\n<summary>`). NOT in spec's "Predicted File List" but required because Story 3.9 chose `task.completed` as its placeholder example, which 3.12 templates. AC-10's "Not modifiable" list does not include `tests/integration/`; treated as in-scope baseline-update |
| `_bmad-output/implementation-artifacts/3-12-completion-summary-template.md` | This file — task checkboxes ticked, Dev Agent Record / File List / Change Log filled, Status flipped to `review` |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flipped: `backlog → ready-for-dev → in-progress → review` + `last_updated` bump to 2026-05-01T20:00:00Z |
