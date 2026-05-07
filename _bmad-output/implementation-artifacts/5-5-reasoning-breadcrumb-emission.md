# Story 5.5: `agent.reasoning.*` breadcrumb emission with sanitizer integration

Status: review

## Story

As the operator,
I want the worker to emit `agent.reasoning.*` typed events (planning rationale, retry justifications, rejected hypotheses, tool-call arguments) passed through the secret sanitizer,
So that `/logs` and `/status` can surface *why* the agent did what it did, not just what it did (FR17b, NFR-O6).

## Acceptance Criteria

1. **AC-1: Reasoning extraction** — Given Claude Code produces an `assistant` message with `thinking` content blocks or text blocks containing planning rationale, when the runner processes the message, then each reasoning fragment is extracted into an `ExtractedEvent` with `event_type="agent.reasoning.*"` and the sanitized text content.

2. **AC-2: Event types** — At least three `agent.reasoning.*` subtypes are classified: `agent.reasoning.plan_drafted` (planning rationale from text/thinking blocks), `agent.reasoning.tool_call_rationale` (reasoning preceding a tool_use), `agent.reasoning.step_summary` (reasoning after tool result). The subtype is determined by context position relative to tool_use blocks.

3. **AC-3: Sanitizer integration** — When any reasoning breadcrumb is extracted, all text fields in the event payload pass through `secret_hygiene.sanitizer._redact_value()` before emission. If the sanitized output differs from the input (secrets detected), the payload text is replaced with `{reason: "sensitive_content_suppressed"}` and the event is STILL emitted (never dropped).

4. **AC-4: Payload models registered** — New Pydantic payload models (`AgentReasoningBreadcrumbPayload` or per-subtype models) are defined in `packages/events/src/events/payloads.py` and registered in the schema registry via `register()` calls. `scripts/check_event_registry.py` exits 0.

5. **AC-5: Domain module** — A new `services/worker-wrapper/src/worker_wrapper/domain/reasoning.py` module contains the reasoning extraction + sanitization logic. This is domain (not adapter) — pure event shaping with no IO dependencies. The `domain/` layer imports from `packages/` (events, secret-hygiene) and stdlib only.

6. **AC-6: Runner integration** — `ClaudeCodeRunner._extract_events()` (from Story 5.4) is extended to also extract reasoning breadcrumbs from `thinking` and `text` content blocks in assistant messages. The extraction delegates to `domain/reasoning.py` for sanitization + event shaping. The existing `ExtractedEvent` dataclass gains an optional `reasoning_text: str | None` field or reasoning events use a new `ReasoningEvent` subclass.

7. **AC-7: NFR-O1 compliance** — `scripts/check_imports.py` exits 0. No `subprocess.check_output().decode()` pattern. The `domain/reasoning.py` module has zero IO imports (no structlog — use stdlib logging if needed, per the domain-layer rule).

8. **AC-8: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

9. **AC-9: Tests** — At least 10 new tests covering: thinking block extraction, text-as-planning extraction, sanitizer integration (clean + secret-containing), sensitive_content_suppressed replacement, multi-block reasoning with tool_use interleaving, per-subtype classification, schema registry registration, domain-layer IO-free enforcement.

10. **AC-10: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

11. **AC-11: Atomic commit** — title: `feat(worker-wrapper): add agent.reasoning.* breadcrumb emission with sanitizer · E5`

## Tasks / Subtasks

- [x] **Task 1: Define payload models** (AC: #4)
  - [x] Add `AgentReasoningBreadcrumbPayload` to `packages/events/src/events/payloads.py`
  - [x] Fields: `session_id`, `subtype` (Literal), `text` (sanitized), `suppressed`, `tool_name` (optional), `raw_length`
  - [x] Register in schema registry: `agent.reasoning.plan_drafted` v1.0.0, `agent.reasoning.tool_call_rationale` v1.0.0, `agent.reasoning.step_summary` v1.0.0
  - [x] Add to `__all__` in payloads.py

- [x] **Task 2: Create `domain/reasoning.py`** (AC: #3, #5)
  - [x] `classify_reasoning_block(block, prev_block_type, next_block_type) -> ReasoningSubtype | None`
  - [x] `sanitize_reasoning_text(text: str) -> tuple[str, bool]` — uses `scan_text()` for secret detection
  - [x] `build_reasoning_breadcrumb(block, session_id, ...) -> ReasoningBreadcrumb | None` — orchestrates classify + sanitize
  - [x] `extract_reasoning_from_content(content, session_id) -> list[ReasoningBreadcrumb]`
  - [x] Zero IO imports — stdlib `logging` only (no structlog)

- [x] **Task 3: Extend `ClaudeCodeRunner._extract_events`** (AC: #1, #2, #6)
  - [x] Added `reasoning: list[ReasoningBreadcrumb]` to `ClaudeCodeResult`
  - [x] Extended `_extract_events()` to call `extract_reasoning_from_content()`
  - [x] Reasoning list reset on new `run()`, included in timeout path

- [x] **Task 4: Write tests** (AC: #9)
  - [x] `test_reasoning.py` — 37 tests in 7 classes: sanitize, classify, extract, build, extract_from_content, domain-no-IO, schema-registry
  - [x] Extended `test_claude_code_runner.py` — 6 integration tests in `TestRunnerReasoningExtraction`
  - [x] Secret suppression tested, multi-block classification tested
  - [x] Schema registry registration verified (with `importlib.reload` to handle `unregister_all` pollution)
  - [x] NFR-O1 domain zero-IO verified via `inspect.getsource()`

- [x] **Task 5: Verification + commit** (AC: #7, #8, #10, #11)
  - [x] `mypy --strict` clean on all modified files
  - [x] `ruff check` clean on all modified files
  - [x] `just test` — 1441 passed, 0 failed, 0 regressions
  - [ ] Atomic commit

## Dev Notes

### Claude Code SDK: reasoning content blocks

With `--output-format stream-json`, the `assistant` message `content` array can contain these block types:

```json
{
  "type": "assistant",
  "message": {
    "content": [
      {"type": "thinking", "thinking": "Let me analyze the requirements..."},
      {"type": "text", "text": "I'll start by implementing the adapter..."},
      {"type": "tool_use", "id": "...", "name": "Write", "input": {"file_path": "...", "content": "..."}},
      {"type": "tool_result", "tool_use_id": "...", "content": "File written successfully"},
      {"type": "text", "text": "Good, the file was written. Now I'll run the tests..."}
    ]
  }
}
```

The `thinking` block is emitted when Claude uses extended thinking. The `text` blocks contain Claude's visible reasoning/planning. Both are structured JSON — NFR-O1 compliant.

### Reasoning extraction strategy

Scan assistant message content blocks in order. Classification rules:

| Block type | Context | Subtype |
|---|---|---|
| `thinking` | Any position | `agent.reasoning.plan_drafted` |
| `text` | Before a `tool_use` block | `agent.reasoning.plan_drafted` |
| `text` | After a `tool_result` block | `agent.reasoning.step_summary` |
| `text` | Immediately before a `tool_use` with rationale about the tool | `agent.reasoning.tool_call_rationale` |

The simplest approach: track `prev_block_type` while iterating content blocks.

### Sanitizer integration pattern

The `secret_hygiene.sanitizer` module provides:
- `_redact_value(value)` — returns sanitized value (recursively handles dicts, lists, strings)
- `scan_text(text)` — returns list of `SecretMatch` if secrets found
- `REDACTED_SENTINEL` — the `"***REDACTED***"` replacement string

For reasoning breadcrumbs, the approach is:

```python
from secret_hygiene.scanner import scan_text

def sanitize_reasoning_text(text: str) -> tuple[str, bool]:
    matches = scan_text(text)
    if not matches:
        return text, False  # clean
    return "", True  # suppressed — emit with sensitive_content_suppressed
```

Do NOT use `_redact_value` directly for partial redaction — it replaces entire values. Use `scan_text` to detect presence, then suppress the whole text if any match is found. This is the safest approach because:
1. Partial redaction of reasoning text could leave enough context to reconstruct the secret
2. The AC says "if sanitization cannot safely redact" → suppress entirely

### New payload model shape

```python
class AgentReasoningBreadcrumbPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    subtype: Literal["plan_drafted", "tool_call_rationale", "step_summary"]
    text: str  # sanitized — may be "" if suppressed
    suppressed: bool = False  # True when sensitive content was detected
    tool_name: str | None = Field(default=None, min_length=1, max_length=64)
    raw_length: int = Field(ge=0)  # original text length before sanitization
```

### Import-graph rules (CRITICAL)

`domain/reasoning.py` MUST:
- Import from `packages/events/` — ALLOWED
- Import from `packages/secret-hygiene/` — ALLOWED
- Import from `worker_wrapper` own modules (e.g. `adapters.claude_code_runner.ExtractedEvent`) — ALLOWED
- Import `dataclasses`, `typing`, `logging`, `re` — ALLOWED (stdlib)
- Import `structlog` — **FORBIDDEN** in domain (use stdlib `logging` if needed)
- Import `asyncio`, `json`, `os` — **FORBIDDEN** in domain (IO deps)
- Import from `services/*`, `mcp-servers/*` — **FORBIDDEN**

### What already exists (Stories 5.1–5.4)

| File | Current state | What to change |
|---|---|---|
| `adapters/claude_code_runner.py` | `ClaudeCodeRunner` with `_extract_events()` scanning tool_use blocks | Extend to also scan thinking/text blocks, delegate to domain/reasoning.py |
| `adapters/__init__.py` | Exports `ClaudeCodeResult`, `ClaudeCodeRunner`, `ExtractedEvent` | May need to add `ReasoningEvent` or reasoning-related exports |
| `domain/` | `worktree_lock.py`, `atomic_edit.py` | Add `reasoning.py` |
| `domain/__init__.py` | May be empty | Add reasoning exports |
| `app/config.py` | `WorkerSettings` with claude_* fields | No changes needed |
| `app/main.py` | Session lifecycle | No changes needed |
| `packages/events/payloads.py` | 20+ payload models | Add `AgentReasoningBreadcrumbPayload` |
| `packages/secret-hygiene/scanner.py` | `scan_text()` — returns list of SecretMatch | Use for secret detection |
| `packages/secret-hygiene/sanitizer.py` | `_redact_value()`, `redact_secrets()` structlog processor | Reference for sanitization approach |

### Key patterns from previous stories

1. **Function-level structlog** (app/adapter) / **stdlib logging** (domain) — `domain/reasoning.py` uses `logging.getLogger(__name__)`, NOT structlog
2. **`_classify_*` as `@staticmethod`** — from Story 5.4, makes testing easy without runner instance
3. **`ExtractedEvent` dataclass** — reuse for reasoning events; add optional fields or create parallel `ReasoningEvent`
4. **Schema registry pattern** — `register(event_type, schema_version, payload_model)` from `events.schema_registry`
5. **Pydantic payload discipline** — `ConfigDict(frozen=True, strict=True, extra="forbid")` on ALL payload models
6. **Domain = zero IO** — enforced by `check_imports.py`; structlog is IO-adjacent
7. **Best-effort event emission** — reasoning events are best-effort; don't crash if extraction fails

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1502-1517 — Story 5.5 definition]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 692 — `domain/reasoning.py` in directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 34 — NFR-O6 reasoning breadcrumb sanitization]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 336-341 — import-graph rules]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 386-401 — event envelope strict schema]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 116 — NFR-O1 custom ruff rule]
- [Source: `_bmad-output/implementation-artifacts/5-4-claude-code-subprocess-supervision.md` — previous story: ClaudeCodeRunner + ExtractedEvent]
- [Source: `packages/secret-hygiene/src/secret_hygiene/scanner.py` — `scan_text()` API for secret detection]
- [Source: `packages/secret-hygiene/src/secret_hygiene/sanitizer.py` — `_redact_value()` + `REDACTED_SENTINEL`]
- [Source: `packages/events/src/events/payloads.py` — existing payload model patterns]
- [Source: `packages/events/src/events/schema_registry.py` — `register()` API]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7

### Debug Log References

### Completion Notes List

- Used `ReasoningBreadcrumb` dataclass (parallel to `ExtractedEvent`) instead of subclassing, keeping concerns separated
- Sanitization uses full text suppression (empty string) when secrets detected — partial redaction unsafe for reasoning text
- Classification priority: `tool_call_rationale` (text before tool_use) > `step_summary` (text after tool_result) > `plan_drafted` (default)
- Schema registry test uses `importlib.reload()` to handle `unregister_all()` pollution from `test_schema_registry.py` autouse fixture
- Domain module uses `logging` not `structlog`, enforces zero IO imports via `TestDomainNoIO` tests

### File List

- `packages/events/src/events/payloads.py` — added `AgentReasoningBreadcrumbPayload` model + `__all__` entry
- `services/registry-state/src/registry_state/domain/event_types.py` — added 3 `register()` calls + import + `__all__` entry
- `services/worker-wrapper/src/worker_wrapper/domain/reasoning.py` — NEW: domain module with extraction + sanitization
- `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — extended with reasoning extraction
- `services/worker-wrapper/src/worker_wrapper/adapters/__init__.py` — added `ReasoningBreadcrumb` export
- `services/worker-wrapper/src/worker_wrapper/test_reasoning.py` — NEW: 37 domain tests
- `services/worker-wrapper/src/worker_wrapper/test_claude_code_runner.py` — added 6 integration tests
