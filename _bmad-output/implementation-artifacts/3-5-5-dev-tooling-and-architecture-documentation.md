# Story 3.5.5: Dev tooling and architecture documentation

Status: ready-for-dev

## Story

As **the platform engineer**,
I want **dev-tooling quirks documented, architectural decisions recorded, and renderer conventions consolidated into a shared reference**,
so that **future stories (Epic 4+) stop re-discovering the same issues and code reviews stop re-litigating settled decisions**.

This is a tech-debt documentation story from the Epic 3 retrospective. During Epics 1-3, three process gaps accumulated cognitive overhead:

1. The `uv sync --no-dev` quirk was re-discovered in 10+ stories with no written record.
2. The `from_user=None` allowlist-middleware auth decision was raised and dismissed in 4 consecutive code reviews (3.16-3.19) with no ADR.
3. Renderer carry-forward lists (25+ items across Stories 3.10-3.13) were copy-pasted into each spec instead of referenced from a shared doc.

**What this story is NOT:**
- NOT adding new test coverage or changing production code.
- NOT creating new tooling — only documenting existing patterns and decisions.
- NOT touching the deferred-work.md items (those remain as-is).

## Acceptance Criteria

1. **AC-1: `docs/development.md` created** — Documents the `uv sync --no-dev` quirk (strips dev-only deps like `asgi-lifespan`), the fix (`uv sync --all-packages`), and when each variant is appropriate. Includes a troubleshooting section.

2. **AC-2: `docs/adr/0001-allowlist-middleware-auth.md` created** — Records the architectural decision that allowlist middleware (Story 3.2) handles auth, and `from_user=None` updates are rejected with `user_id=0, reason="no_from_user"`. Closes the question permanently so code reviews stop re-raising it.

3. **AC-3: `docs/RENDERER_CONVENTIONS.md` created** — Consolidates the carry-forward patterns from Stories 3.10-3.13 into a single reference: HTML escape contract, newline collapse, character budgets, section-drop ladder pattern, emergency one-liner, renderer dispatcher architecture, type-mismatch guard, renderer purity, model-boundary validators, test conventions, and emoji discipline.

4. **AC-4: `docs/message-design.md` cross-reference** — Add a reference to `RENDERER_CONVENTIONS.md` in the existing `message-design.md` file so the two docs link to each other.

5. **AC-5: `just lint` 9/9 green** — all lint gates pass (new docs don't break anything).

6. **AC-6: `just test` unchanged** — no test count change (1161 passed, 5 skipped, 14 deselected).

7. **AC-7: Atomic commit** — title: `docs: add development guide, ADR-0001, and RENDERER_CONVENTIONS.md · E3.5-debt`

## Tasks / Subtasks

- [ ] **Task 1: Create `docs/development.md`** (AC: #1)
  - [ ] Document `uv sync` variants: `--no-dev`, `--all-packages`, `--all-groups --all-packages`
  - [ ] Document the quirk: `uv sync --no-dev` strips dev-only deps (e.g. `asgi-lifespan`, `sniffio`) that tests need; `--all-packages` restores them
  - [ ] Add troubleshooting section: "tests fail with ImportError after uv sync" → run `uv sync --all-packages`
  - [ ] Reference the `just fix-venv` recipe if it exists, or document the manual fix

- [ ] **Task 2: Create `docs/adr/0001-allowlist-middleware-auth.md`** (AC: #2)
  - [ ] Record the decision: allowlist middleware (Story 3.2) is the single auth gate
  - [ ] Document `from_user=None` handling: rejected with `user_id=0, reason="no_from_user"`
  - [ ] Include context: why this was raised in 4 consecutive reviews (3.16-3.19) and why it's closed
  - [ ] Reference the test: `test_event_without_from_user_rejected_with_sentinel` in `test_allowlist.py`

- [ ] **Task 3: Create `docs/RENDERER_CONVENTIONS.md`** (AC: #3)
  - [ ] Consolidate these sections from Stories 3.10-3.13 carry-forward lists:
    - HTML escape contract (escape exactly once, truncate before escaping, escape all operator-supplied strings)
    - Newline collapse (`_collapse_newlines` helper, known U+2028/U+2029 gap)
    - Character budget discipline (1900/4000/1000 tiers, parity invariant, UTF-16 safety)
    - Section-drop ladder pattern (progressive section removal on overflow)
    - Emergency one-liner fallback (collapse → slice → escape ordering)
    - Renderer dispatcher architecture (`MappingProxyType`, `EventEnvelope` input, placeholder fallback)
    - Type-mismatch guard (schema-registry race window, `isinstance` check)
    - Renderer purity contract (`def` not `async def`, no I/O, no mutation)
    - Model-boundary validators (`ConfigDict(frozen=True, strict=True, extra="forbid")`, per-field validators)
    - Schema registration convention (`event_types.py` side-by-side with model class)
    - Test conventions (idempotent registration guard, parametric cap-overflow, per-character HTML assertions)
    - Emoji discipline (fixed catalog from `message-design.md`)
    - Cross-service import pattern (`IMP001` noqa cluster, deferred refactor to `packages/events/`)
  - [ ] Link to `docs/message-design.md` for template mockups and field lists

- [ ] **Task 4: Cross-reference `docs/message-design.md`** (AC: #4)
  - [ ] Add a "Renderer conventions" section linking to `RENDERER_CONVENTIONS.md`

- [ ] **Task 5: Verification + commit** (AC: #5, #6, #7)
  - [ ] `just lint` 9/9 green
  - [ ] `just test` — 1161 passed, 5 skipped, 14 deselected (unchanged)
  - [ ] Verify all 4 new/modified docs are well-formed markdown
  - [ ] Atomic commit

## Dev Notes

### Documentation Scope

This story is purely documentation — no production code changes. All files are in the `docs/` directory. The only production-adjacent file touched is `docs/message-design.md` (a documentation file, not code).

### ADR Format

Use the lightweight ADR format:
```
# ADR-0001: [Title]

## Status
Accepted

## Context
[Why the decision was needed]

## Decision
[What was decided]

## Consequences
[Implications]
```

### `docs/development.md` Content Sources

- Epic 1 retrospective (AI #2): first mention of the `uv sync` quirk
- Epic 3 retrospective (Challenge #2): "uv sync quirk re-discovered in 10+ stories"
- Justfile: `just fix-venv` recipe and `uv sync` invocations
- Root `pyproject.toml`: dev-dependency groups that `--no-dev` strips

### `RENDERER_CONVENTIONS.md` Content Sources

- Story 3.10 (`3-10-approval-request-template.md`): carry-forward H5, H9, H11, M14
- Story 3.11 (`3-11-blocker-notification-template.md`): carry-forward L17, H2/H5
- Story 3.12 (`3-12-completion-summary-template.md`): carry-forward M1, M14
- Story 3.13 (`3-13-self-recovered-summary-template.md`): carry-forward patterns
- `docs/message-design.md`: emoji catalog, template discipline
- `services/clawhip-daemon/src/clawhip_daemon/adapters/sinks/telegram_sink.py`: implementation reference
- `deferred-work.md`: known gaps (U+2028/U+2029, boolean-bag refactor, task_id validator)

### Previous Story Learnings (Stories 3.5.1-3.5.4)

- `just lint` 9/9 is the gatekeeper — all 9 checks must pass.
- Carry-forward: the three-layer review catches import inconsistencies.
- This story touches documentation files only — no production code.
- Commit pattern: functional commit tagged `· E3.5-debt`.

### File List

| File | Change |
|---|---|
| `docs/development.md` | New — dev tooling quirk documentation |
| `docs/adr/0001-allowlist-middleware-auth.md` | New — ADR for allowlist auth decision |
| `docs/RENDERER_CONVENTIONS.md` | New — consolidated renderer conventions |
| `docs/message-design.md` | Modified — add cross-reference to RENDERER_CONVENTIONS.md |
| `_bmad-output/implementation-artifacts/3-5-4-*.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips |

### References

- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Challenge #2 (uv sync quirk), Challenge #3 (from_user=None review energy)]
- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-04.md` — Action Items 2, 3, 5]
- [Source: `docs/message-design.md` — existing template design reference]
- [Source: `services/telegram-gateway/src/telegram_gateway/test_allowlist.py` — `test_event_without_from_user_rejected_with_sentinel`]
- [Source: `justfile` — `uv sync` invocations and `fix-venv` recipe]
- [Source: `docs/testing-guide.md` — existing test documentation for style reference]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
