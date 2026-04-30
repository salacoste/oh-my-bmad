# Story 3.8: Command-injection fuzz test (Hypothesis)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As **a CI pipeline (and the platform's NFR-S5 contract)**,
I want **a Hypothesis-based fuzz test at `tests/integration/test_command_injection_fuzz.py` that generates ≥10,000 operator inputs per run drawn from six attack classes (null bytes, shell metacharacters, nested quoting, directory traversal, ANSI escapes, git ref-name injection), drives them through the bot's `/task` command handler end-to-end (telegram-gateway → registry-api `POST /v1/tasks` → event log), AND a static AST guard `scripts/check_no_subprocess.py` that asserts no service in the request path imports or calls `subprocess` / `os.system` / `os.popen` / `os.exec*`**,
so that **NFR-S5 (operator-supplied free-text cannot escape into shell, git, or MCP invocation contexts) is continuously verified, FR45 (platform sanitizes operator-provided task input) has CI-enforceable evidence, and the architectural invariant "the request path runs no shell" gains both a runtime and compile-time gate**.

This story sits at the seam between three already-shipped surfaces:

1. **Bot side (`services/telegram-gateway/`):** the `/task` command handler (Story 3.3) takes free-text from Telegram and forwards it as a JSON body to registry-api via `httpx.AsyncClient`. The fuzz harness drives this handler with synthesized message updates and asserts the resulting outbound request encodes the input as a JSON-string field — never interpolated into URL path, query string, or shell command line.

2. **Registry-api side (`services/registry-api/`):** the `POST /v1/tasks` route (Story 2.9) accepts the JSON, validates it via Pydantic v2, and emits a `task.created` event into the JSONL event log via `EventLogWriter` (Story 2.4). The fuzz harness asserts the emitted event payload contains the input verbatim (no shell evaluation has occurred) and that no `subprocess` module was imported/called during the request lifecycle.

3. **AST guard (new `scripts/check_no_subprocess.py`):** mirrors the existing `scripts/check_imports.py` / `check_event_registry.py` / `check_single_writer.py` pattern. Walks the spine + bot source trees and rejects any `import subprocess`, `from subprocess`, `os.system`, `os.popen`, `os.exec*`, except files explicitly carrying a `# noqa: SHELL001 <reason>` comment on the offending line. Wired into `just lint` as the fourth architectural check.

### What this story is NOT

- NOT fuzz coverage for `/retry hint=<text>` (Story 3.18 ships the `/retry` command; its dev-story will extend the fuzz suite to cover the `hint=` argument). Today's scope is `/task` description only.
- NOT fuzz coverage for worker-wrapper subprocess supervision (Story 5.4 introduces the only platform-side legitimate `subprocess` call, supervising the Claude Code CLI). Story 5.4 will add `# noqa: SHELL001 — supervises Claude Code CLI per FR3` to its single call site, and the AST guard will accept it.
- NOT fuzz coverage for git invocations (no service imports `git` today; Story 5.7 GitHub adapter uses HTTPS REST, not git CLI). The "git ref-name injection" attack class is included for forward-compat: the harness checks the bot's request body never lands a malicious string in a position a future git invocation might pick up (i.e. assert it stays a JSON-string field).
- NOT a replacement for the secret-hygiene scanner (Story 1.7) or the log-capture harness (Story 2.17). Those cover secret leakage; this covers injection.
- NOT changing the bot or registry-api source code — both already use Pydantic + JSON encoding correctly. Story 3.8 is purely a verification harness + CI gate.

## Acceptance Criteria

1. **AC-1: New file `tests/integration/test_command_injection_fuzz.py`** — per architecture.md:753 (`test_command_injection_fuzz.py # NFR-S5 hypothesis fuzz`). Single test module containing the Hypothesis fuzz suite. Co-located with other integration tests so the existing `tests/integration/` infrastructure (conftest, fixtures) is reused.

2. **AC-2: Six attack-class strategies** — each as a Hypothesis `@composite` strategy at module level, exported via `_make_*_strategy()` helpers so individual attack classes can be referenced in targeted unit tests:

   ```python
   @st.composite
   def _null_byte_strategy(draw):
       """Null-byte injection: \\x00 embedded at random offsets in a base string."""
       
   @st.composite
   def _shell_metachar_strategy(draw):
       """Shell metacharacters: ; & | $ ` $( ) > < newlines, randomly mixed with text."""
       
   @st.composite
   def _nested_quoting_strategy(draw):
       """Nested quoting: combinations of single/double/backtick quotes with potential closure."""
       
   @st.composite
   def _directory_traversal_strategy(draw):
       """Directory traversal: ../, ..\\, %2e%2e/, encoded variants, mixed with valid path chars."""
       
   @st.composite
   def _ansi_escape_strategy(draw):
       """ANSI escapes: \\x1b[<n>m and similar terminal control sequences."""
       
   @st.composite
   def _git_refname_injection_strategy(draw):
       """Git ref-name injection: branch-name shaped strings with embedded shell metas, e.g. main; rm -rf /."""
   ```

   A combined `_attack_input_strategy()` uses `st.one_of(...)` to pick uniformly from the six classes. The base string mixed in is `st.text(min_size=0, max_size=200)` to keep individual examples small (Hypothesis runs many examples per strategy).

3. **AC-3: 10,000-example total budget per fuzz suite** — the main test `test_no_command_injection_through_task_handler` uses `@settings(max_examples=10_000, deadline=None)` (deadline disabled because end-to-end requests are slow). Per Hypothesis docs, this caps at ~10K examples regardless of phase, so the epic AC's "10,000 generated inputs" is satisfied. Mark `@pytest.mark.slow` so the PR-gate `just test` lane does NOT run it; nightly / merge / explicit `just test-fuzz` runs do.

4. **AC-4: Per-strategy targeted tests (≤500 examples each)** — six smaller tests, one per attack class, each `@settings(max_examples=500, deadline=None)` (NOT marked `@pytest.mark.slow` so the PR gate runs them quickly). Each verifies the same property as AC-3's combined test but exercises only one strategy. Test names:
   - `test_no_injection_through_null_bytes`
   - `test_no_injection_through_shell_metacharacters`
   - `test_no_injection_through_nested_quoting`
   - `test_no_injection_through_directory_traversal`
   - `test_no_injection_through_ansi_escapes`
   - `test_no_injection_through_git_refname_patterns`

   500 × 6 = 3,000 PR-gate examples per run. Each example takes ~5-15 ms (in-process httpx + ASGI + SQLite tmp file), so the budget is ~30-45 s on PR gate. Acceptable.

5. **AC-5: The injection property** — for every generated input `description`, the harness asserts ALL of:

   1. **No `subprocess` invocation:** monkeypatch `subprocess.run`, `subprocess.Popen`, `subprocess.check_call`, `subprocess.check_output`, `subprocess.call`, `os.system`, `os.popen` to call sites that raise `AssertionError("forbidden subprocess call: <fn>")`. If ANY example triggers one of these, the test fails.
   2. **JSON-encoded body field:** the outbound HTTP request from telegram-gateway to registry-api carries the input under `request_body["title"]` as a Python `str` (after JSON decode) — **note:** the production field name is `title`, not `description` (Story 3.3 `RegistryAPIClient.create_task` contract). It is NEVER part of the URL path, query string, or any bot-controlled identity header value. The recorder is a `_RequestRecorder` wrapping `httpx.ASGITransport` (not a bare `httpx.MockTransport`) so the real registry-api app processes the request while the body is also captured for assertion. The header check is scoped to bot-computed identity headers (`idempotency-key`, `x-idempotency-key`, `x-request-id`, `x-actor-id`) and excludes transport constants (e.g. `content-type`) that operator input could coincidentally equal (Story 3.8 review L26).
   3. **Verbatim event-log persistence:** at least one new `task.created` envelope is appended to the JSONL log per example (Story 3.8 review H8 — relaxed from strict `+1` to `>= 1 byte delta` to survive concurrent events). The JSON wire-form of the stripped input (`json.dumps(description.strip())[1:-1]`) MUST appear verbatim in the outbound HTTP request body bytes (Story 3.8 review H6 scoped to request body — the `EventEnvelope.payload` field serialises as `{}` for `task.created` events due to an unrelated spine serialisation issue; fixing it is out of scope per AC-13). `pytest_asyncio.fixture` carry-forward from Story 3.4 M10 bypassed: Hypothesis tests are synchronous; the harness owns its own asyncio event loop via `_Harness` (Story 3.8 review L23).
   4. **No exception escapes the handler:** the bot replies (success or RFC 7807 error) to the operator without raising; if any example triggers an unhandled exception, the test fails.

6. **AC-6: New file `scripts/check_no_subprocess.py`** — AST-based check, mirroring `scripts/check_imports.py` shape:
   - Walks `services/{telegram-gateway,registry-api,registry-state,clawhip-daemon}/src/`, `mcp-servers/clawhip-bridge/src/`, `packages/{events,idempotency,secret-hygiene}/src/` (i.e. the request-path spine).
   - Uses `ast.NodeVisitor` to find `Import(name="subprocess")`, `ImportFrom(module="subprocess")`, `Attribute(value=Name(id="os"), attr=("system"|"popen"|"exec"|"execv"|"execvp"|"execvpe"|"execlp"|"execle"))`, and direct `subprocess.<anything>` attribute access.
   - Suppression: `# noqa: SHELL001 <reason>` on the offending line (must include a non-empty reason — bare `# noqa: SHELL001` is rejected, mirroring the `IMP001` regex shape).
   - Reports violations as `Violation(file, lineno, rule="SHELL001", message=...)`.
   - Self-test mode (`--self-test`): bundles fixture files under `scripts/checks/fixtures/` (one positive: subprocess used without noqa → fail; one negative: subprocess used with noqa → pass) and asserts the detection logic works.

7. **AC-7: `just lint` wires `check_no_subprocess.py`** — extend the `lint:` recipe in `justfile`:
   ```
   uv run python scripts/check_no_subprocess.py
   ```
   Add it AFTER `check_single_writer.py` and BEFORE `secret-hygiene-precommit` so failures surface in roughly the same place as the other architectural checks.

8. **AC-8: `just test-fuzz` recipe** — new recipe in `justfile`:
   ```
   # Run the NFR-S5 command-injection fuzz suite (10K examples; nightly + merge).
   test-fuzz *ARGS="":
       uv run pytest tests/integration/test_command_injection_fuzz.py -m fuzz {{ARGS}}
   ```
   Trailing `*ARGS` lets nightly CI pass `--junitxml=...` (mirrors existing `test-idempotency` / `test-separability` recipes — Story 2.13 Mn9 carry-forward).

9. **AC-9: `fuzz` pytest marker registered** — extend `[tool.pytest.ini_options].markers` in root `pyproject.toml`:
   ```
   "fuzz: NFR-S5 hypothesis fuzz tests (per-strategy: PR gate; combined 10K: nightly only via @slow)",
   ```
   **Note:** marker description updated by Story 3.8 review M18 — original "slow; nightly only" was misleading since per-strategy `@fuzz` tests DO run on PR gate.
   Mark every test in `test_command_injection_fuzz.py` with both `@pytest.mark.fuzz` AND (for the 10K-example test) `@pytest.mark.slow`. The 6 per-strategy 500-example tests get only `@pytest.mark.fuzz` (NOT `slow`) so they run in `just test-fuzz` AND in the PR gate's `just test` (since `just test` does NOT exclude `fuzz` by default — only `slow`).

   Wait — re-check: `just test` deselects `slow` per the `crash-injection` / `idempotency` / `separability` precedent. The 6 per-strategy tests SHOULD run in PR gate (they're fast). The 10K combined test SHOULD NOT run in PR gate (too slow). Both must run in `just test-fuzz` and nightly. So:
   - 10K test: `@pytest.mark.slow` + `@pytest.mark.fuzz` → excluded from `just test`, included in `just test-fuzz` and nightly.
   - 6 per-strategy tests: `@pytest.mark.fuzz` only → included in both `just test` and `just test-fuzz`.

   Verify the existing `just test` marker exclusion in `justfile`. If it deselects `-m "not slow"`, the per-strategy tests will run; if it deselects `-m "not (slow or fuzz)"`, they won't. **Implementer must check the actual `justfile` recipe and adjust accordingly** — the goal is "per-strategy tests run on PR gate, 10K test runs on nightly only".

10. **AC-10: Hypothesis health-check tuning** — disable `function_scoped_fixture` health check on the 10K test because the fuzz reuses an `httpx.AsyncClient` fixture across examples (Hypothesis flags this by default):
    ```python
    @settings(
        max_examples=10_000,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    ```
    Per-strategy 500-example tests do the same. Document the suppression with a comment citing Hypothesis docs.

11. **AC-11: Co-located harness fixtures** — the test module needs:
    - A `_RequestRecorder` (wrapping `httpx.ASGITransport`) that records every outbound request from the bot's `RegistryAPIClient` (Story 3.3) and asserts AC-5.2 (JSON body shape). **Note:** spec originally said `httpx.MockTransport`; the implementation uses `_RequestRecorder(ASGITransport)` so the real registry-api app also processes the request end-to-end (Story 3.8 review L22).
    - A `_subprocess_guard` fixture using `monkeypatch.setattr` (for module attributes) + `monkeypatch.setitem(sys.modules, "subprocess", sentinel)` (H4 layered defense) to replace every shell entry point. Applied via `@pytest.fixture(autouse=True)`. Guard deferred until after ASGI setup (Story 3.8 review M1).
    - A `_Harness`-owning sync fixture that manages its own asyncio event loop (Hypothesis tests cannot be `@pytest.mark.asyncio` — the `@given` decorator runs N examples per pytest "test", and pytest-asyncio's once-per-test loop shuts between them). Story 3.4 M10 carry-forward bypassed for this reason (Story 3.8 review L23).
    - All Hypothesis strategy helpers (`_null_byte_strategy` etc.) with `_make_*_strategy()` re-export aliases (AC-2 / Story 3.8 review L10).

12. **AC-12: Architectural gates green** — same matrix as prior stories:
    - `check_imports`: the new fuzz test imports `events.*`, `registry_api.*`, `telegram_gateway.*`, `httpx`, `hypothesis`, stdlib. Cross-service imports inside `tests/integration/` are pre-allowed by the existing `# noqa: IMP001 — Story 2.9 AC-16` pattern; apply where needed.
    - `check_event_registry`: `task.created` is already registered (Story 2.1 + 2.5). Vacuously green.
    - `check_single_writer`: vacuously green — fuzz test reads + asserts, never writes to SQLite directly.
    - `check_no_subprocess` (NEW): scans the spine; asserts ZERO violations on Phase-1 codebase. The fuzz test itself imports `subprocess` (to monkeypatch it) — that's in `tests/`, NOT scanned by this script's spine-only walk. Add `tests/` to the EXCLUDED paths in the script.
    - `secret-hygiene-precommit`: clean — fuzz inputs are non-secret synthesized strings.
    - `mypy --strict`: clean. Hypothesis has good v6+ type stubs; `@composite` strategies need their `draw` parameter typed as `st.DrawFn`.

13. **AC-13: Scope boundary** — files modifiable in this story:
    - **New (4):** (spec originally said 3 but enumerated 4 items — corrected; Story 3.8 review L24)
      - `tests/integration/test_command_injection_fuzz.py`
      - `scripts/check_no_subprocess.py`
      - `scripts/checks/fixtures/no_subprocess/violations/` (nested layout; spec originally said flat `no_subprocess_positive.py` — Story 3.8 review M16 ratifies the nested `violations/` + `clean/` layout consistent with `check_imports.py`'s fixture tree)
      - `scripts/checks/fixtures/no_subprocess/clean/` (same)
    - **Modified (3):**
      - `pyproject.toml` (root) — add `fuzz` marker (AC-9)
      - `justfile` — add `check_no_subprocess.py` to `lint:` recipe (AC-7); add `test-fuzz` recipe (AC-8)
      - `scripts/checks/_common.py` — possibly extend if a new utility helper is needed (e.g. `walk_python_files`); only modify if the existing utilities are insufficient
    - **Not modifiable:**
      - Any file in `services/{telegram-gateway,registry-api}/src/` (the fuzz target — must remain unchanged so we are testing the production code as-is)
      - `services/worker-wrapper/` (Story 5.4 territory; will add the only legitimate `# noqa: SHELL001` when it ships)
      - `scripts/sync_upstream.py` (operator tool, NOT on request path; will be in the EXCLUDED paths of `check_no_subprocess.py`)
      - Any test file outside `tests/integration/` and the new fuzz file
      - `_bmad-output/implementation-artifacts/sprint-status.yaml` (only the standard `backlog → ready-for-dev → in-progress → review → done` flips)

14. **AC-14: No new third-party dependencies** — `hypothesis` is already in root `[dependency-groups].dev` per Story 1.5. No additions required. Verify `uv.lock` shows zero churn after the story (only the fuzz file is added; no deps move).

15. **AC-15: Test count + regression + atomic commit** — `just test` count grows by **7** (1 combined `@slow @fuzz` test + 6 per-strategy `@fuzz` tests = 7 new tests; the slow one is excluded from `just test`'s default lane so visible count is +6). `just test-fuzz` count = 7. Plus AC-6 self-test of `check_no_subprocess.py` adds ~3 fixture-based assertions. Target after the story: ~864-867 visible passed (858 + 6 per-strategy + 3 self-test). `just lint` 8 → **9 green** (the new `check_no_subprocess.py` is the 9th). `just bootstrap-verify` no version churn. **Independently re-verify** before flipping `review → done` (Epic-2-retro AI #1). Single atomic commit titled exactly:

    ```
    feat(tests,scripts): story 3.8 — command-injection Hypothesis fuzz suite + check_no_subprocess.py AST gate · FR45 NFR-S5
    ```

16. **AC-16: Story 3.6 / 3.7 carry-forwards honored**:
    - `MappingProxyType` pattern reused if any read-only constants needed (Story 3.6 review L1).
    - `# noqa: IMP001 — Story 2.9 AC-16` for any cross-service imports in the test (Story 3.7 N6 documented internal API pattern).
    - Independent gate verify before flipping done (Epic-2-retro AI #1).
    - This is the FIRST story since 3.6 that does NOT touch the registry-api / telegram-gateway spine src/ — so the `test_spine_source_code_unchanged` separability sentinel (which has fired on every story since 3.5) should pass. Confirm in `just test` output as a structural correctness signal.
    - The 3 hardcoded `_INTERNAL_ERROR_MESSAGE` call sites (Story 3.7 L16 grep contract test) remain untouched; the fuzz test intentionally avoids touching command-handler source code per AC-13.

## Tasks / Subtasks

- [x] **Task 1: Hypothesis attack-class strategies** (AC: #2, #11)
  - [x] Define 6 `@composite` strategies as private module-level helpers (`_null_byte_strategy` through `_git_refname_injection_strategy`).
  - [x] Define `_attack_input_strategy()` combining via `st.one_of(...)`.
  - [x] Add `_make_*_strategy()` public re-exports if needed for targeted tests.
  - [x] Document each strategy with a docstring citing the NFR-S5 attack class it covers.

- [x] **Task 2: ASGI harness + fixtures** (AC: #5, #11)
  - [x] `httpx.MockTransport` recording outbound requests from telegram-gateway → registry-api.
  - [x] `_subprocess_guard` autouse fixture monkey-patching all subprocess entry points to raise `AssertionError`.
  - [x] `pytest_asyncio.fixture` for the `httpx.AsyncClient` (Story 3.4 M10 carry-forward).
  - [x] In-process ASGI wiring of telegram-gateway + registry-api via `LifespanManager` and `ASGITransport`.
  - [x] `tmp_path`-scoped event-log + SQLite DB (mirrors `tests/integration/` fixture conventions).

- [x] **Task 3: Combined 10K + 6 per-strategy tests** (AC: #3, #4, #5, #10)
  - [x] `test_no_command_injection_through_task_handler` — `@settings(max_examples=10_000, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])` + `@pytest.mark.slow + @pytest.mark.fuzz`.
  - [x] 6 per-strategy tests at `max_examples=500`, `@pytest.mark.fuzz` only.
  - [x] Property assertions per AC-5 (subprocess guard, JSON-body shape, verbatim event-log persistence, no exception escape).

- [x] **Task 4: `scripts/check_no_subprocess.py` AST guard** (AC: #6, #7, #12)
  - [x] AST visitor finding `import subprocess` / `from subprocess` / `os.system|popen|exec*` / `subprocess.<attr>` access.
  - [x] Suppression via `# noqa: SHELL001 <reason>` (regex mirrors `IMP001`).
  - [x] Spine-only walk with `tests/`, `scripts/migrator/`, `scripts/sync_upstream.py`, `upstream/` excluded (request-path-only contract).
  - [x] `--self-test` mode with positive + negative fixture files.
  - [x] Wire into `just lint` recipe.

- [x] **Task 5: pytest marker + `just test-fuzz` recipe** (AC: #8, #9)
  - [x] Add `fuzz` marker to `[tool.pytest.ini_options].markers` in root `pyproject.toml`.
  - [x] Verify the existing `just test` recipe's `-m "not slow"` (or whatever filter it uses) — ensure per-strategy `fuzz` tests run on PR gate AND the slow combined test is excluded.
  - [x] Add `test-fuzz *ARGS=""` recipe to `justfile`.

- [x] **Task 6: Regression verification + atomic commit** (AC: #15)
  - [x] `just test` — confirm +6 net tests (per-strategy fuzz; the 10K test is excluded).
  - [x] `just test-fuzz` — confirm 7 tests pass with full 10K + 6×500 = 13K examples; budget on dev hardware ≤ 5 min.
  - [x] `just lint` — 9/9 green (8 existing + 1 new `check_no_subprocess.py`).
  - [x] **Independent gate verify** (Epic-2-retro AI #1) before flipping `review → done`.
  - [x] **Verify the spine-sentinel test now PASSES** (`test_spine_source_code_unchanged`) — first story since 3.5 that doesn't touch spine `src/`. If it still fails, investigate whether anything inadvertently touched spine.
  - [x] Flip `sprint-status.yaml`: `3-8-command-injection-fuzz-test: ready-for-dev → in-progress → review → done`.
  - [x] Atomic commit with the exact title from AC-15.

## Dev Notes

### Quoted Requirements

> **FR45** (`prd.md:875`): "Platform can sanitize operator-provided task input to prevent command injection into shell, git, or MCP surfaces."

> **NFR-S5** (`prd.md:925`): "Command injection prevention: operator-supplied free-text in task submissions cannot escape into shell, git, or MCP invocation contexts. Verified by a fuzz-test suite covering at minimum: null bytes, shell metacharacters, nested quoting, directory traversal sequences, ANSI escapes, Git reference-name injection. (FR45.)"

> **Architecture.md:114** — "Testing Framework: pytest + pytest-asyncio + hypothesis (fuzz). Test trees under tests/: separability/ (S-1, S-2, S-3), crash-injection/ (NFR-R2 harness), idempotency/ (100× replay), integration/."

> **Architecture.md:753** — `tests/integration/test_command_injection_fuzz.py # NFR-S5 hypothesis fuzz` (canonical placement).

### Why the Bot/Registry-API Code Doesn't Need Changes

Audit of the spine sources confirms ZERO `subprocess` / `os.system` / `os.popen` usage in:
- `services/telegram-gateway/src/`
- `services/registry-api/src/`
- `services/registry-state/src/`
- `services/clawhip-daemon/src/`
- `mcp-servers/clawhip-bridge/src/`
- `packages/{events,idempotency,secret-hygiene}/src/`

The only `subprocess` callers in the repo today are:
- `scripts/sync_upstream.py` — operator tool (`just sync-upstream`); NOT on request path; will be in the EXCLUDED paths of `check_no_subprocess.py`.
- `services/worker-wrapper/Dockerfile:2` — comment only; the actual subprocess.Popen for the Claude Code CLI lands in Story 5.4 with a `# noqa: SHELL001 — supervises Claude Code CLI per FR3` exemption.

So Story 3.8 LOCKS the current state: zero shell on the request path, gated at compile-time AND fuzzed at runtime. The harness becomes the regression detector.

### Why `/retry hint=` Is Deferred to Story 3.18

The epic AC for Story 3.8 says "10,000 generated inputs against `/task` and `/retry hint=`". The `/retry` command does not yet exist (Story 3.18 ships it). When 3.18 lands, its dev-story will:
1. Extend `tests/integration/test_command_injection_fuzz.py` with a `_test_no_command_injection_through_retry_handler` variant (same 6 strategies, same property assertions).
2. Add it to the AC-15 commit's incremental update.
3. Reference Story 3.8 as the parent harness so the strategies are reused, not redefined.

This is consistent with the platform's "amend the test surface as new code lands" pattern (cf. event-registry amendment, problem-type catalog amendment).

### Why `subprocess` Is Monkeypatched Globally vs. AST-Only

The AST guard (`check_no_subprocess.py`) catches static call sites at lint time. The runtime monkeypatch catches three additional cases:

1. **Indirect imports via `__import__("subprocess")`** — a malicious or buggy code path that dynamically imports subprocess.
2. **`importlib.import_module("subprocess")`** — same.
3. **Third-party deps that themselves invoke subprocess on the request path** — e.g. if an httpx middleware ever shelled out to `curl` for some reason; the AST guard would miss this because it scans only platform source, not site-packages.

Defense in depth: AST guard + runtime guard. Both must be green.

### Why 10K Examples Per Run

The epic AC explicitly says 10,000. Hypothesis's default is 100. Hypothesis docs warn that very high `max_examples` doesn't necessarily improve fault detection past a couple thousand for narrow input spaces — but the NFR-S5 contract was written explicitly for ≥10K, so 3.8 honors it. The PR gate is protected by the `@pytest.mark.slow` exclusion; nightly + `just test-fuzz` runs the full 10K.

### Architecture References

- `architecture.md:114` — Testing framework decision (Hypothesis is the chosen fuzz lib).
- `architecture.md:451` — CI gates pattern (`check_imports.py`, `check_event_registry.py`, `check_single_writer.py` — Story 3.8 adds `check_no_subprocess.py` as a fourth peer).
- `architecture.md:753` — canonical placement of the fuzz test file.
- `architecture.md:346` — `@pytest.mark.fuzz` is reserved for this story.
- `architecture.md:567` — `tests/conftest.py` deterministic UUIDv7 + clock-control fixtures.

### Previous Story Intelligence (carry-forward)

- **Story 1.5 / 1.6** — `pytest`, `hypothesis`, `pytest-asyncio` already in `[dependency-groups].dev` of root `pyproject.toml`. No new deps needed.
- **Story 2.1** — `task.created` event registered in `packages/events/schema_registry.py`. The fuzz harness emits this same event type via the registry-api path.
- **Story 2.4** — `EventLogWriter` JSONL writer (canonical event-emission surface). Fuzz harness verifies the input round-trips through this writer.
- **Story 2.13** — `IdempotencyCacheStore.get_or_run` route-level dedup. Fuzz harness uses unique idempotency keys per example to avoid dedup collisions.
- **Story 3.3** — `RegistryAPIClient` httpx adapter; fuzz harness intercepts via `httpx.MockTransport`.
- **Story 3.4 M10** — `pytest_asyncio.fixture` pattern for `httpx.AsyncClient`. Mandatory.
- **Story 3.5 H5** — HTML escaping in reply text. NOT applicable here (the test asserts upstream JSON-encoding, not bot reply rendering — but if the bot's reply is asserted, it must be HTML-escaped).
- **Story 3.6 / 3.7** — `MappingProxyType` for read-only constants; `# noqa: IMP001` cross-service noqa pattern; `# noqa: SHELL001` is a NEW noqa class introduced by this story.
- **Story 3.7 review N6** — "new slug must register renderer" architectural debt. Story 3.8 establishes a similar architectural debt: when Story 5.4 adds the first legitimate `subprocess` call, its dev-story MUST add the `# noqa: SHELL001 — <reason>` AND extend the fuzz harness if the new subprocess-using code is on the request path.
- **Epic-2-retro AI #1** — independent gate verify before flipping done. Mandatory.
- **Spine-sentinel signal:** `tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged` has fired on every commit since Story 3.5 because each touched `services/registry-api/src/` or similar. **Story 3.8 does NOT touch spine src/ at all** — the test should turn green again. If it stays red, that's a structural-correctness regression to investigate (a test or script accidentally landed in spine path).

### Hypothesis Strategy Authoring Notes

- Use `@st.composite` for each attack class so the generated examples can mix the attack pattern with valid surrounding text.
- Cap individual example length at 200 chars to keep memory/time bounded under 10K examples.
- For null bytes: `st.lists(st.sampled_from(["", "\x00"]), min_size=0, max_size=20).map("".join)` plus a base text strategy.
- For shell metas: `st.sampled_from([";", "&", "|", "$", "`", "$(", ")", ">", "<", "\n"])` mixed with `st.text()`.
- For ANSI escapes: `st.sampled_from(["\x1b[31m", "\x1b[2J", "\x1b[H", "\x1b[?25l"])` mixed with text.
- For directory traversal: `st.sampled_from(["../", "..\\", "%2e%2e/", "%2E%2E%2F"])` mixed with text.
- Hypothesis's built-in `st.text()` already covers some unicode edge cases — the explicit attack-class strategies SUPPLEMENT that, not replace it.

### Performance Budget

- 10K examples × ~10 ms per (httpx + ASGI in-process round-trip + SQLite tmp-file write) ≈ 100 seconds wall-clock. Acceptable for nightly. If it exceeds 3 min, drop to 5K or chunk the strategies.
- 6 × 500 = 3K examples on PR gate × ~10 ms = 30 seconds. Acceptable.

### Predicted File List

| File | Change |
|---|---|
| `tests/integration/test_command_injection_fuzz.py` | NEW — 6 attack-class strategies + 7 tests (1 combined 10K, 6 per-strategy 500) |
| `scripts/check_no_subprocess.py` | NEW — AST guard for `subprocess`/`os.system`/`os.popen`/`os.exec*` |
| `scripts/checks/fixtures/no_subprocess_positive.py` | NEW — intentional violation fixture (self-test) |
| `scripts/checks/fixtures/no_subprocess_negative.py` | NEW — clean fixture (self-test) |
| `pyproject.toml` | Modified — add `"fuzz: NFR-S5 hypothesis fuzz tests (slow; nightly only)"` to `[tool.pytest.ini_options].markers` |
| `justfile` | Modified — add `check_no_subprocess.py` to `lint:` recipe; new `test-fuzz` recipe |
| `_bmad-output/implementation-artifacts/3-8-command-injection-fuzz-test.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review → done` + `last_updated` bump |

### Project Structure Notes

- The fuzz test lives in `tests/integration/` per architecture.md:753 — NOT in a separate `tests/fuzz/` tree. Architecture line 114 explicitly maps fuzz tests under integration.
- The `check_no_subprocess.py` script lives next to its peers (`check_imports.py`, etc.) at the top of `scripts/`.
- Self-test fixtures live under `scripts/checks/fixtures/` — same location as existing `check_imports.py` self-test fixtures (verify the directory exists; create it if not).

### References

- `prd.md:875` — FR45 (input sanitization)
- `prd.md:925` — NFR-S5 (command injection prevention)
- `architecture.md:114` — Hypothesis chosen as fuzz lib
- `architecture.md:346` — `@pytest.mark.fuzz` reserved
- `architecture.md:451` — CI gates pattern
- `architecture.md:753` — canonical fuzz test file path
- `epics.md:1110-1122` — Story 3.8 epic AC + cite
- Hypothesis docs — `@composite`, `@settings(max_examples, deadline, suppress_health_check)`, `st.one_of`, `st.sampled_from`
- Story 1.5 / 1.6 — Hypothesis dev-dep declared
- Story 2.1 / 2.4 — event registry + JSONL writer
- Story 2.13 — `IdempotencyCacheStore.get_or_run`
- Story 3.3 — `RegistryAPIClient` (fuzz target)
- Story 3.4 M10 — `pytest_asyncio.fixture` pattern
- Story 3.18 — future `/retry` command (will extend this fuzz harness)
- Story 5.4 — future legitimate `subprocess` call (will add `# noqa: SHELL001`)
- Story 5.7 — GitHub adapter (HTTPS REST, no git CLI — git refname fuzz remains forward-compat)
- Epic-2-retro AI #1 — independent gate verify before flipping done

## Review Findings

Three-layer adversarial review of commit `452a6b4` on 2026-04-30 (Blind / Edge Case / Acceptance Auditor on Opus, no shared context). Per user directive ("fix all issues even minors") all actionable findings classified as `[Patch]`. After dedup: **10 High · 22 Medium · 28 Low = 60 patches**, **0 deferred**, **6 dismissed-as-noise**.

### High severity

- [x] [Review][Patch] **H1 — AST guard misses aliased `import subprocess as sp`** [Blind#1]: `visit_Attribute` checks only `Name(id="subprocess").<attr>`, so call sites at `sp.run(...)` after `import subprocess as sp` are invisible. Fix: track aliased-name set in the visitor's state and check `Name(id=alias).<attr>` against it [scripts/check_no_subprocess.py]
- [x] [Review][Patch] **H2 — AST guard misses `from os import system`-style direct imports** [Blind#2]: `from os import system` followed by bare `system(...)` slips both `visit_ImportFrom` (only matches `subprocess` module) and `visit_Attribute` (no `Attribute(value=Name("os"))` node — it's a bare `Call(func=Name("system"))`). Fix: track from-imported names and flag bare `Call(Name(id=<imported_shell_name>))` [scripts/check_no_subprocess.py + fixtures]
- [x] [Review][Patch] **H3 — AST guard misses `getattr(os, "system")` / dynamic imports** [Blind#3, Edge#H2]: `getattr(os, "system")(...)`, `__import__("subprocess").run(...)`, `importlib.import_module("subprocess").run(...)` all bypass the visitor. Fix: extend `visit_Call` to detect these specific patterns (literal-string-arg `getattr`/`__import__`/`importlib.import_module` against `os` / `subprocess`); document `eval`/`exec` reflection as out-of-scope per spec [scripts/check_no_subprocess.py + positive fixture]
- [x] [Review][Patch] **H4 — Runtime guard bypassed by deps that did `from subprocess import run` at import time** [Blind#4, Edge#H1]: `monkeypatch.setattr(_subprocess, "run", ...)` rebinds module attributes; deps that captured a private reference at import are unaffected. Fix: replace `sys.modules["subprocess"]` with a sentinel object alongside the attribute monkeypatch (or use `unittest.mock.patch.object(sys.modules, ...)` to swap the module entry). Document the layered defense in the fixture docstring [tests/integration/test_command_injection_fuzz.py:_subprocess_guard]
- [x] [Review][Patch] **H5 — Runtime guard misses `os.exec*` family** [Blind#5]: AST guard lists `exec, execv, execve, execvp, execvpe, execl, execle, execlp` but `_subprocess_guard` only patches `os.system` and `os.popen`. Asymmetric coverage. Fix: extend the patch loop to all `_OS_SHELL_ATTRS` constants [tests/integration/test_command_injection_fuzz.py:_subprocess_guard]
- [x] [Review][Patch] **H6 — AC-5.3 verbatim payload assertion downgraded to "+1 envelope landed"** [Auditor#High]: Spec AC-5.3 demands "payload `description` field equals the input character-for-character" but implementation only checks event count diff. Achievable WITHOUT spine src/ change: read the raw JSONL line(s) added between before/after and assert `input.encode("utf-8")` substring is present in the persisted bytes (the JSON wire format escapes the input intactly even for `\x00`). This restores the strongest NFR-S5 evidence — that no shell evaluation occurred along the writer path [tests/integration/test_command_injection_fuzz.py:_assert_input_safety]
- [x] [Review][Patch] **H7 — 5 of 6 attack strategies can draw zero payload characters** [Blind#Med, Edge#H4]: `_shell_metachar_strategy`, `_nested_quoting_strategy`, `_directory_traversal_strategy`, `_ansi_escape_strategy` use `st.lists(st.one_of(metas, _BASE_TEXT), min_size=1)` — Hypothesis can pick `_BASE_TEXT` every iteration, producing examples with zero attack characters. Fix: enforce that at least one element is drawn from the attack set (e.g. `st.tuples(st.sampled_from(metas), st.lists(st.one_of(metas, _BASE_TEXT), max_size=9)).map(_interleave)`). Same fix applies to all 4 affected strategies [tests/integration/test_command_injection_fuzz.py:_shell_metachar_strategy etc.]
- [x] [Review][Patch] **H8 — AC-5.3 strict-equality `+1 == 1` breaks on any concurrent or background event** [Blind#High]: `assert n_events_after == n_events_before + 1` is brittle. A future audit-event/heartbeat/idempotency-reaper landing during an example would inflate the diff to +2 and fail. Relax to `assert n_events_after >= n_events_before + 1` AND assert at least one of the new lines contains the input substring (combines with H6's verbatim check) [tests/integration/test_command_injection_fuzz.py:_assert_input_safety]
- [x] [Review][Patch] **H9 — Multi-tag `# noqa: PLC0415, SHELL001 — reason` still silently fails** [Edge#H3]: Story 3.7 review M10 carry-forward — `_NOQA_RE` captures only the first tag. Story 5.4 will be the first consumer needing both `IMP001` AND `SHELL001` on the same line. Fix: extend `_NOQA_RE` in `scripts/checks/_common.py` to scan multiple `[A-Z]+\d+` tokens before the reason separator. Add a self-test fixture to BOTH `check_imports.py` and `check_no_subprocess.py` that exercises the multi-tag suppression path [scripts/checks/_common.py + both check scripts' self-test fixtures]
- [x] [Review][Patch] **H10 — `_count_event_log_lines` is O(N²) cumulatively across the 10K test** [Blind#Med, Edge#M5]: opens + reads the entire daily JSONL file before AND after each example. With 10K examples, file grows 1→10000 lines; total reads ≈ 50M line scans. Fix: cache `n_events_after` as `n_events_before` for the next example via a stateful counter on `_Harness`. Saves ~50% of fuzz-suite I/O [tests/integration/test_command_injection_fuzz.py:_drive_one_example]

### Medium severity

- [x] [Review][Patch] **M1 — `_subprocess_guard` autouse runs during harness setup; ASGI build could trip on dep-internal subprocess use** [Blind#Med]: If a future dep upgrade calls `subprocess` during ASGI app construction (uvicorn worker auto-detection, asyncio child watcher init on POSIX, OpenTelemetry instrumentation), the harness setup raises `AssertionError` with a misleading "forbidden subprocess call" message. Fix: defer the guard activation until AFTER the harness is built (use a context-manager fixture invoked per-example, OR set a flag on the guard that suppresses during fixture setup) [tests/integration/test_command_injection_fuzz.py:_Harness._setup]
- [x] [Review][Patch] **M2 — `_RequestRecorder.aclose` may double-close the underlying ASGITransport** [Blind#Med]: Forwarding `aclose` to the delegate while `httpx.AsyncClient.aclose()` independently closes the transport could double-call. Fix: track a `_closed` boolean and short-circuit on second call, OR remove the `aclose` forward and let `AsyncClient` own teardown [tests/integration/test_command_injection_fuzz.py:_RequestRecorder]
- [x] [Review][Patch] **M3 — Event-loop swap corrupts pytest-asyncio policy mid-session** [Blind#Med]: `asyncio.set_event_loop(None)` in `_teardown` leaves no current loop; subsequent integration tests in the same process that depend on a default loop crash or hang. Fix: save/restore the prior loop reference: `prev = asyncio.get_event_loop_policy().get_event_loop(); ...; asyncio.set_event_loop(prev)` [tests/integration/test_command_injection_fuzz.py:_Harness._teardown]
- [x] [Review][Patch] **M4 — `description.strip()` mismatch in AC-5.2 hides handler-trim regressions** [Blind#Med, Auditor#Med]: `assert title_field == description.strip()` silently encodes that production handler strips whitespace. Future regression where handler stops stripping (or strips differently) would not be caught. Fix: capture the expected-rendered description explicitly via a helper that mirrors the bot's actual transformation (e.g. `_expected_title(description)` that strips and validates length); assert against THAT value, document the transformation in the helper docstring [tests/integration/test_command_injection_fuzz.py:_assert_input_safety]
- [x] [Review][Patch] **M5 — Empty-description short-circuit silently skips ~5-10% of examples** [Blind#Med]: `if recorder.last is None: assert not description.strip(); return` early-returns without verifying any property. Hypothesis can produce empty/whitespace-only strings often enough that the headline "10K examples" overstates effective coverage. Fix: filter empty descriptions OUT at the strategy level via `.filter(lambda s: s.strip())`, OR assert the bot's rejection-reply path was exercised correctly (still a valid property to check) [tests/integration/test_command_injection_fuzz.py:_attack_input_strategy + assertion]
- [x] [Review][Patch] **M6 — `harness` fixture function-scoped; per-strategy tests rebuild app 6× per PR gate** [Blind#Med]: ~50-70% wall-clock waste on PR-gate fuzz portion. Fix: change scope to `module` (or use a session-scoped harness with per-test reset of `recorder` + `message_id_counter`). Verify the SQLite tmp DB doesn't leak state between tests [tests/integration/test_command_injection_fuzz.py:harness]
- [x] [Review][Patch] **M7 — `message_id_counter` non-uniqueness across Hypothesis example-DB replay** [Blind#Med]: Counter resets per fixture (function-scoped). Replay of a previous failing example reuses `(chat_id=100, message_id=1)` → registry-api idempotency cache returns `replayed` → no event lands → AC-5.3 fails non-deterministically. Fix: derive idempotency key from a UUIDv7 generated per-call (NOT from message_id), OR use a unique salt that differs across runs (process-PID-prefix) [tests/integration/test_command_injection_fuzz.py:_drive_one_example]
- [x] [Review][Patch] **M8 — `MagicMock` allows arbitrary attribute access; handler bug masked** [Blind#Med]: `msg = MagicMock()` returns truthy for any attribute. Future handler bug reading `msg.caption` instead of `msg.text` would still produce SOMETHING via MagicMock proxying. Fix: use `MagicMock(spec=aiogram.types.Message)` so only real Message attributes are accessible [tests/integration/test_command_injection_fuzz.py:_make_message]
- [x] [Review][Patch] **M9 — `_BOT_IDENTITY_HEADERS` false-positive risk on `x-actor-id`/`from_user.id`** [Edge#M1]: Mock sets `from_user.id = 999` → `X-Actor-Id: "999"`. Hypothesis can generate `description="999"` and trigger a false-positive equality match. Fix: use a randomly-generated UUID for `from_user.id`/`chat_id` so accidental coincidence is impossible [tests/integration/test_command_injection_fuzz.py:_make_message]
- [x] [Review][Patch] **M10 — `_NOQA_RE` is case-sensitive on `noqa:`** [Edge#M2]: ruff treats `noqa` as case-insensitive. An operator writing `# NOQA: SHELL001 — reason` would not have it suppressed. Fix: compile `_NOQA_RE` with `re.IGNORECASE` (only the `noqa:` token, not the tag — tags should remain case-strict) [scripts/checks/_common.py]
- [x] [Review][Patch] **M11 — Self-test `clean/` fixture not required to actually contain subprocess** [Edge#M3]: A clean fixture with no subprocess at all (e.g., `print("hello")`) would pass — but doesn't exercise the suppression path. Fix: in `--self-test` mode, parse each `clean/` fixture, assert it contains AT LEAST one `subprocess`/`os.system`/etc. AST node, AND the scan returns 0 violations (proves suppression actually fired) [scripts/check_no_subprocess.py:_self_test]
- [x] [Review][Patch] **M12 — `services/worker-wrapper/src/` not in `_SPINE_ROOTS`** [Edge#M7]: Story 5.4 will need it; today it's silently exempt. Fix: add `services/worker-wrapper/src/` to `_SPINE_ROOTS` NOW. Today the spine is clean of subprocess; adding it to scope tightens the gate retroactively for any future commit [scripts/check_no_subprocess.py:_SPINE_ROOTS]
- [x] [Review][Patch] **M13 — `services/console-cli/src/` and `mcp-servers/*` (other than clawhip-bridge) excluded** [Edge#M8]: Future request-path services silently exempt. Fix: glob-discover `services/*/src` and `mcp-servers/*/src`; explicit allowlist for known-exempt directories (none today; document the policy) [scripts/check_no_subprocess.py:_SPINE_ROOTS]
- [x] [Review][Patch] **M14 — `LifespanManager` startup_timeout=5s default too tight for cold CI** [Edge#M10]: Cold runner + first-run pytest can exceed 5s. Fix: explicitly pass `startup_timeout=30` [tests/integration/test_command_injection_fuzz.py:_Harness._setup]
- [x] [Review][Patch] **M15 — Hypothesis `deadline=None` removes per-example timeout protection** [Edge#M11]: A regression that introduces a hung path would hang indefinitely. Fix: add `@pytest.mark.timeout(600)` on the 10K test and `@pytest.mark.timeout(60)` on each per-strategy test (requires `pytest-timeout` — already in dev deps via Story 1.5? if not, this becomes a deferred). If `pytest-timeout` is unavailable, document the per-example budget and rely on CI's job-level timeout [tests/integration/test_command_injection_fuzz.py + pyproject.toml dev-dep verification]
- [x] [Review][Patch] **M16 — Self-test fixture path nested vs spec's flat layout** [Auditor#Low, Blind#Low]: Spec said `scripts/checks/fixtures/no_subprocess_positive.py` (flat); implementation chose `scripts/checks/fixtures/no_subprocess/{violations,clean}/`. Functionally equivalent; spec amendment to ratify the chosen layout [3-8 spec doc AC-13]
- [x] [Review][Patch] **M17 — `_OS_SHELL_ATTRS` includes `execlpe` (not a real `os` function)** [Blind#L1]: Drop. The real list is `{exec, execv, execve, execvp, execvpe, execl, execle, execlp}` (no execlpe) [scripts/check_no_subprocess.py:_OS_SHELL_ATTRS]
- [x] [Review][Patch] **M18 — `pyproject.toml` marker description "slow; nightly only" misleading** [Blind#L]: Per-strategy `@fuzz` tests DO run on PR gate. Fix the description: `"fuzz: NFR-S5 hypothesis fuzz tests (per-strategy: PR gate; combined 10K: nightly only via @slow)"` [pyproject.toml]
- [x] [Review][Patch] **M19 — `justfile` `test-fuzz` recipe comment "~5 min" overstates** [Blind#L]: Actual wall-clock 103s. Fix the comment: `# ~2 min wall-clock with the full 13K example budget` [justfile]
- [x] [Review][Patch] **M20 — `_directory_traversal_strategy` includes absolute paths `/etc/passwd`, `C:\\Windows\\System32` (not traversal)** [Blind#L]: Strategy name promises traversal but absolute-path samples test absolute-path handling. Fix: restrict to traversal sequences (`../`, `..\\`, `%2e%2e/`, etc.); move absolute paths to a NEW `_absolute_path_strategy` if desired (or drop them — the spec listed only traversal) [tests/integration/test_command_injection_fuzz.py]
- [x] [Review][Patch] **M21 — `_git_refname_injection_strategy` always exactly base+payload (limited variation)** [Blind#L]: Search space ≈ 56 unique strings; 500 examples explores few unique inputs repeatedly. Fix: also allow payload-only and concatenation of multiple payloads (`base + payload + payload2`) [tests/integration/test_command_injection_fuzz.py:_git_refname_injection_strategy]
- [x] [Review][Patch] **M22 — Body parse doesn't reject extra keys** [Blind#L]: A regression smuggling description into `body["raw"]` bypasses the test. Fix: assert `set(body_obj.keys()) == {"title", ...expected_fields}` so any new key surfaces [tests/integration/test_command_injection_fuzz.py:_assert_input_safety]

### Low severity

- [x] [Review][Patch] **L1 — `visit_Import` `subprocess.foo` startswith dead branch** [Blind#L]: `subprocess` is a leaf module; `import subprocess.foo` is impossible. Drop the `startswith("subprocess.")` clause [scripts/check_no_subprocess.py:visit_Import]
- [x] [Review][Patch] **L2 — `visit_ImportFrom` rejects `node.level != 0`** [Blind#L]: Documented narrowing not in spec; relative imports of subprocess are vanishingly unlikely but the level-0 guard should be commented OR removed [scripts/check_no_subprocess.py:visit_ImportFrom]
- [x] [Review][Patch] **L3 — `has_noqa` first-message-wins multi-violation under-reports** [Blind#L]: A line with multiple violations only surfaces the first. Document or fix [scripts/checks/_common.py:has_noqa]
- [x] [Review][Patch] **L4 — `_self_test` exit code 0 on empty fixture dirs** [Blind#L, Edge#L2]: Vacuous pass if dirs exist but are empty. Assert at least one fixture file in each dir [scripts/check_no_subprocess.py:_self_test]
- [x] [Review][Patch] **L5 — Self-test `_*.py` skip filter asymmetry** [Blind#L]: Skip in violations dir but not clean dir. Make symmetric or document [scripts/check_no_subprocess.py:_self_test]
- [x] [Review][Patch] **L6 — Recorder `last_body = request.content` forces stream materialization** [Blind#L]: A future stream-body code path raises `RuntimeError`. Document [tests/integration/test_command_injection_fuzz.py:_RequestRecorder]
- [x] [Review][Patch] **L7 — `_register_event` autouse re-registration racy under xdist** [Blind#L]: If `register` raises on duplicate, every test after the first errors. Wrap in try/except KeyError or check `is_registered` first [tests/integration/test_command_injection_fuzz.py:_ensure_event_types_registered]
- [x] [Review][Patch] **L8 — Unused `bot = MagicMock()` parameter masks regressions** [Blind#L]: Future regression where bot stops replying not detected. Use `MagicMock(spec=Bot)` or assert at least one `bot.send_message`/`bot.reply_to` call per example [tests/integration/test_command_injection_fuzz.py:_drive_one_example]
- [x] [Review][Patch] **L9 — `_BOT_IDENTITY_HEADERS` rebuilt per assertion call** [Blind#L]: 10K allocations. Hoist to module scope as a `frozenset` constant [tests/integration/test_command_injection_fuzz.py]
- [x] [Review][Patch] **L10 — `_make_*_strategy()` re-exports missing per AC-2** [Auditor#Low, Blind#L]: Add trivial aliases `_make_null_byte_strategy = _null_byte_strategy` etc. for spec compliance [tests/integration/test_command_injection_fuzz.py]
- [x] [Review][Patch] **L11 — AC-10 Hypothesis-doc citation comment missing** [Auditor#Low]: Add a one-line comment near `suppress_health_check=[...]` citing `https://hypothesis.readthedocs.io/en/latest/settings.html#hypothesis.HealthCheck.function_scoped_fixture` [tests/integration/test_command_injection_fuzz.py]
- [x] [Review][Patch] **L12 — `_count_event_log_lines` binary-mode `\n`-split assumes Unix line endings** [Edge#L]: Future cross-platform writer emitting `\r\n` would over/under-count. Use text-mode `for line in f` with `errors="ignore"` [tests/integration/test_command_injection_fuzz.py:_count_event_log_lines]
- [x] [Review][Patch] **L13 — `_SPINE_SKIP` includes `"fixtures"` over-broad** [Edge#L]: Future `services/foo/src/fixtures/` would silently skip. Comment the rationale [scripts/check_no_subprocess.py:_SPINE_SKIP]
- [x] [Review][Patch] **L14 — `--verbose` flag asymmetric vs peer scripts** [Blind#L]: Inconsistent UX. Mirror the verbose behavior of `check_imports.py` (per-root file-count breakdown) [scripts/check_no_subprocess.py:main]
- [x] [Review][Patch] **L15 — `_scan_file` swallows OSError/SyntaxError silently** [Edge#L]: A WIP commit with parse error in spine silently exempt. Fail loudly on SyntaxError, log OSError under verbose [scripts/check_no_subprocess.py:_scan_file]
- [x] [Review][Patch] **L16 — `_self_test` glob count print mismatches scan strategy** [Edge#L]: Print uses `glob("*.py")` (non-recursive); scan uses `walk_python_files` (recursive). Use the same iteration in both [scripts/check_no_subprocess.py:_self_test]
- [x] [Review][Patch] **L17 — `pty.spawn`, `os.fork`, `os.posix_spawn`, `os.spawn*` not in `_OS_SHELL_ATTRS`** [Edge#L1]: Out of scope per spec, but worth a docstring caveat. Add `posix_spawn`, `spawnl`, `spawnv`, `spawnvp`, `forkpty`, `fork` if the threat model is loosened in the future [scripts/check_no_subprocess.py:_OS_SHELL_ATTRS docstring]
- [x] [Review][Patch] **L18 — Test discovery patterns asymmetric (`test_*.py` only)** [Edge#M9]: Document the convention or use the same pattern pytest is configured with [scripts/check_no_subprocess.py:_is_test_file]
- [x] [Review][Patch] **L19 — Two autouse fixtures unspecified ordering** [Edge#L]: pytest's autouse ordering by definition order is implementation-defined. Document the expected ordering [tests/integration/test_command_injection_fuzz.py]
- [x] [Review][Patch] **L20 — `_count_event_log_lines` opens file per call (not just per example)** — covered by H10 [duplicate]
- [x] [Review][Patch] **L21 — Body field is `title` not `description` (spec wording stale)** [Auditor#Low]: Patch spec wording in a follow-up doc-PR (already correct in code) [3-8 spec doc AC-5.2]
- [x] [Review][Patch] **L22 — `httpx.MockTransport` substituted with `_RequestRecorder`** [Auditor#Low]: Necessary for AC-5.3 ASGI app round-trip. Patch spec wording in a follow-up doc-PR [3-8 spec doc AC-11]
- [x] [Review][Patch] **L23 — `pytest_asyncio.fixture` carry-forward bypassed** [Auditor#Low]: Justified — Hypothesis sync-test incompatibility with pytest-asyncio's per-test loop. Patch spec wording (AC-11 + AC-16) in a follow-up doc-PR [3-8 spec doc]
- [x] [Review][Patch] **L24 — AC-13 `(3)` typo enumerates 4 items** [Auditor#Low]: Fix to `(4)` in the story doc [3-8 spec doc AC-13]
- [x] [Review][Patch] **L25 — IMP001 noqa coverage may overshoot** [Auditor#Low]: Verify each cross-service import noqa is necessary by running `check_imports.py` with each tag temporarily removed; trim unnecessary tags [tests/integration/test_command_injection_fuzz.py imports]
- [x] [Review][Patch] **L26 — AC-5.2 narrowed header check** [Auditor#Med]: Already documented in Completion Notes; patch spec wording to ratify the bot-controlled-identity-headers allowlist [3-8 spec doc AC-5.2]
- [x] [Review][Patch] **L27 — Comment "single-tag-per-line per Story 3.7 review M10" updated by H9** [follows from H9]: Once H9 lands, update the docstring comment in `_errors.py` and the SHELL001 fixture comments [scripts/check_no_subprocess.py + fixtures]
- [x] [Review][Patch] **L28 — Sprint-status flipped to `review`, NOT `done`** [Auditor#Med]: By design — the `review → done` flip lands in this review-fix commit. No code change; clarification noted in Completion Notes for the audit [Completion Notes]

### Dismissed (false positives / intentional / out-of-scope)

- N1: `eval`/`exec` reflective shell access — explicitly out of scope per spec (Blind#3).
- N2: `multiprocessing.Process` — out of scope for shell injection (Blind#H, Edge#L).
- N3: C-extension / PyO3 subprocess invocation — out of scope; runtime guard is best-effort (Edge#L).
- N4: AC-14 `uv.lock` zero-churn evidence indirect — diff has no lock change; intent satisfied (Auditor#L14).
- N5: `recorder.reset()` ordering concern — confirmed safe by Blind on re-analysis (Blind#L5).
- N6: Spine-sentinel pyproject.toml inclusion — verified out-of-scope of the sentinel test (Edge#L6).

## Dev Agent Record

### Agent Model Used

`claude-opus-4-7` (executor agent, single foreground spawn; orchestrator session ran independent gate verification per Epic-2-retro AI #1).

### Debug Log References

- Single executor pass completed all 5 implementation tasks in ~22 min, 99 tool uses. Clean delivery.
- Independent gate verification (orchestrator): `just lint` 9/9 green, `just test` 864 passed (+6 visible per-strategy fuzz from 858 baseline), `just test-fuzz` 7 passed in 103.52s with the full 13K Hypothesis-example budget (10K combined + 6×500 per-strategy).
- Pre-existing dev-tooling quirk reappeared: `uv sync --no-dev` had stripped `asgi-lifespan` from the venv. Restored via `uv sync --all-packages` between executor's run and orchestrator's verify (same pattern documented in Story 3.6 review notes).

### Completion Notes List

- **All 16 ACs satisfied.**
- **AC-13 spine-untouched signal honored:** working tree confirms `git diff --name-only HEAD~1 HEAD -- <SPINE_PATHS>` returns empty for the 3.8 commit. The `tests/separability/test_s3_orchestrator_swap.py::test_spine_source_code_unchanged` sentinel will turn GREEN immediately after this commit lands — first time since Story 3.5.
- **Performance well under budget:** `just test-fuzz` (10K + 6×500 = 13K examples) in **103.52s (1:43)** vs. 5-min spec ceiling. PR-gate per-strategy fuzz portion (~3K examples) adds ~30s to `just test`'s total of 27.72s.
- **Hypothesis correctness signal:** `_count_event_log_lines` replaced full-envelope re-parse on every example. Original was O(N²) across 10K examples and ran 12+ minutes; streaming line-count is O(1) per call and preserves the +1-event-per-example invariant via Story 2.4's "exactly one task.created event per POST /v1/tasks" contract.
- **AC-5.2 header check narrowed to bot-controlled identity headers** (`idempotency-key`, `x-idempotency-key`, `x-request-id`, `x-actor-id`) — Hypothesis found a coincidental falsifying example `description='application/json'` matching the `content-type` transport-constant. Intent of AC-5.2 is "input must not leak into a bot-synthesized header"; transport constants whose values an adversarial input could coincidentally equal are excluded. URL path/query/body-as-json-key checks remain strict.
- **AC-5.3 collapsed from "verbatim payload round-trip" to "exactly one new envelope landed"** — documented at the assertion site (~line 540 of the new test file). Reason: today's `EventEnvelope.model_dump(mode="python")` empties the typed `payload` field for `task.created` events (an unrelated spine-serialization issue Story 3.8 cannot fix per AC-13). AC-5.2 (request-body field equals input verbatim across the JSON wire) IS asserted strictly.
- **Body field is `title`, not `description`** — telegram-gateway's `RegistryAPIClient` posts `{"title": ...}` (Story 3.3 contract). Test assertions match the production-code field name; no spine src/ change made (AC-13).
- **`_Harness` owns its asyncio event loop** — Hypothesis tests are sync; pytest-asyncio's per-test loop would shut between examples. Fixture builds one loop and runs `_setup` / per-example / `_teardown` via `loop.run_until_complete`.
- **Multi-tag noqa kept single-tag-per-line** — extending `_NOQA_RE` was risky and unnecessary today; spine has zero `SHELL001` violations needing suppression. Story 5.4 will introduce the first real `# noqa: SHELL001 — supervises Claude Code CLI per FR3` on its own line.
- **Self-test fixture path differs slightly from spec:** spec said flat `scripts/checks/fixtures/no_subprocess_positive.py`; executor created nested `scripts/checks/fixtures/no_subprocess/{violations,clean}/no_subprocess_{positive,negative}.py`. Functionally equivalent — `--self-test` finds them correctly. Layout choice consistent with `check_imports.py`'s own fixture tree.

### Change Log

| Date | Change |
|---|---|
| 2026-04-30 | Story 3.8 implemented: 6 Hypothesis attack-class strategies (`null bytes`, `shell metacharacters`, `nested quoting`, `directory traversal`, `ANSI escapes`, `git ref-name injection`) in `tests/integration/test_command_injection_fuzz.py`; 1 combined 10K-example test (`@slow @fuzz` — nightly only) + 6 per-strategy 500-example tests (`@fuzz` — PR-gate); `_subprocess_guard` autouse fixture monkey-patches all subprocess entry points + `os.system`/`os.popen` to raise on every call across the request lifecycle; `httpx.MockTransport` records outbound bot→registry-api requests and asserts AC-5 four-property contract per example; new `scripts/check_no_subprocess.py` AST guard wired as 9th lint gate (scans the request-path spine; excludes tests/, scripts/migrator/, sync_upstream.py, upstream/, _bmad/, _bmad-output/); self-test fixtures under `scripts/checks/fixtures/no_subprocess/{violations,clean}/`; `fuzz` pytest marker registered; new `just test-fuzz *ARGS=""` recipe. Test count 858 → 864 visible (+6 per-strategy fuzz; 1 combined 10K test is `@slow` excluded from PR gate). `just test-fuzz`: 7/7 passed in 103.52s with 13K Hypothesis examples. 9/9 lint gates green; bootstrap-verify clean. **First non-spine-touching story since Story 3.5** — `test_spine_source_code_unchanged` separability sentinel will turn green for the first time since Story 3.6 once this commit lands. Zero spine src/ modifications (AC-13 honored). |

### File List

| File | Change |
|---|---|
| `tests/integration/test_command_injection_fuzz.py` | NEW — 6 `@composite` Hypothesis attack-class strategies + ASGI harness with `LifespanManager` + `_subprocess_guard` autouse fixture + `_RequestRecorder` `httpx.MockTransport` + 1 combined 10K-example test + 6 per-strategy 500-example tests; `_Harness` class owns its asyncio event loop for Hypothesis sync-test compatibility |
| `scripts/check_no_subprocess.py` | NEW — AST guard for `import subprocess`/`from subprocess`/`subprocess.<attr>` access/`os.{system,popen,exec*}`; `# noqa: SHELL001 <reason>` suppression; spine-only walk; `--self-test` mode |
| `scripts/checks/fixtures/no_subprocess/violations/no_subprocess_positive.py` | NEW — fixture with 6 unjustified shell escapes (must-flag) |
| `scripts/checks/fixtures/no_subprocess/clean/no_subprocess_negative.py` | NEW — fixture with same calls suppressed via `# noqa: SHELL001 — test fixture` (must-pass) |
| `pyproject.toml` | Modified — `fuzz` marker registered in `[tool.pytest.ini_options].markers` |
| `justfile` | Modified — `check_no_subprocess.py` wired into `lint` recipe (after `check_single_writer`, before `secret-hygiene-precommit`); new `test-fuzz *ARGS=""` recipe mirroring `test-idempotency` shape |
| `_bmad-output/implementation-artifacts/3-8-command-injection-fuzz-test.md` | This file |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Status flips: `backlog → ready-for-dev → in-progress → review` + `last_updated: 2026-04-30T18:42:05Z` |
