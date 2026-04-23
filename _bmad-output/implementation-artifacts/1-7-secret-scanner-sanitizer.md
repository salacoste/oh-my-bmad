# Story 1.7: Secret-scanner pre-commit hook + sanitizer library

Status: review

## Story

As the **operator**,
I want **`.pre-commit-config.yaml` wiring a secret-scanner hook that blocks commits containing secret patterns, plus a runtime `structlog` processor in `packages/secret-hygiene/` that redacts those same secret values before log emission**,
so that **plaintext secrets cannot leak through source control (pre-commit arm) or observability (runtime arm) — the two-arm enforcement Architecture §Core Decisions and NFR-S1 both mandate**.

## Acceptance Criteria

1. **AC-1: `packages/secret-hygiene/src/secret_hygiene/scanner.py`** — pattern-based secret detector. Exports:
   - `SECRET_PATTERNS: dict[str, re.Pattern]` — the canonical secret-pattern table. Named entries (MVP set; keep it tight):
     - `ANTHROPIC_API_KEY` → `r"sk-ant-[A-Za-z0-9_\-]{20,}"`
     - `TELEGRAM_BOT_TOKEN` → `r"\b\d{6,12}:AA[A-Za-z0-9_\-]{30,}\b"` (numeric bot-id, colon, `AA`-prefixed secret blob — matches the real Telegram Bot API token shape)
     - `GITHUB_TOKEN_CLASSIC` → `r"ghp_[A-Za-z0-9]{30,}"`
     - `GITHUB_TOKEN_FINE` → `r"github_pat_[A-Za-z0-9_]{30,}"`
     - `GENERIC_AWS_ACCESS_KEY` → `r"\bAKIA[0-9A-Z]{16}\b"` — defensive; operator never wires AWS directly in Phase 1 but catches accidentally-committed third-party-tool tokens.
   - `scan_text(text: str) -> list[SecretMatch]` — return every hit across all patterns. `SecretMatch` = dataclass with `pattern_name`, `start`, `end`, `line`, `column`, `excerpt` (redacted snippet).
   - `scan_file(path: Path) -> list[SecretMatch]` — convenience wrapper that reads + scans.
   - Counter-idempotency: the canonical `.env.example` pattern doesn't trip detection (the example file holds empty values + placeholders). Verify this in tests.

2. **AC-2: `packages/secret-hygiene/src/secret_hygiene/sanitizer.py`** — structlog processor. Exports:
   - `redact_secrets(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]` — structlog-processor-shape callable; walks every value in `event_dict` (recursing into nested `dict` / `list` / `tuple`), redacts any match from `SECRET_PATTERNS` to a fixed sentinel `"***REDACTED***"`, returns the transformed dict.
   - Key-name redaction: values under keys named `api_key`, `apikey`, `token`, `password`, `secret`, `authorization`, `auth`, `bearer` are redacted regardless of whether the value matches a pattern (conservative — key-name match is stronger than value-pattern match for low-entropy values).
   - `REDACTED_SENTINEL = "***REDACTED***"` — stable string so tests can `assert "***REDACTED***" in record`.
   - Non-string values (ints, floats, bools, None) pass through untouched unless their key is in the key-name set, in which case they become the sentinel (a `password=1234` int becomes `"***REDACTED***"`).

3. **AC-3: `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py`** — pre-commit hook entrypoint. Exports a `main(argv: list[str]) -> int` that accepts file paths as positional args (the standard pre-commit hook contract) + flags + uses `scan_file()` on each. Exit 1 on ANY match, printing one line per match: `<file>:<line>:<col> [<pattern_name>] <redacted excerpt>`. Exit 0 on clean.
   - `--allowlist-file <path>` optional flag — skip paths matching any glob in the file.
   - Registers via `[project.scripts]` entry as `secret-hygiene-precommit = "secret_hygiene.precommit_hook:main"` so pre-commit invokes it directly.

4. **AC-4: `.pre-commit-config.yaml` at repo root.** Wires the in-repo hook:
   ```yaml
   repos:
     - repo: local
       hooks:
         - id: secret-hygiene-precommit
           name: secret-hygiene secret-pattern scan
           entry: uv run secret-hygiene-precommit
           language: system
           types: [text]
           exclude: ^(\.venv/|upstream/|_bmad/|_bmad-output/|.*\.lock$|\.env\.example$)
   ```
   Operator wires by running `uv run pre-commit install` once — documented in README Quickstart update (see AC-9).

5. **AC-5: `packages/secret-hygiene/pyproject.toml` dependency update.** Adds `structlog` to `[project.dependencies]` (the sanitizer requires structlog's processor shape). Declares the `[project.scripts]` entry for `secret-hygiene-precommit`. Also declares `pre-commit` in root `[dependency-groups.dev]` (not in `secret-hygiene` deps — `pre-commit` is a dev tool, not a runtime dep of the sanitizer).

6. **AC-6: Co-located unit tests under `packages/secret-hygiene/src/secret_hygiene/`** (per Architecture line 344 "co-located with the module using pytest's test_*.py naming"):
   - `test_scanner.py` — asserts each `SECRET_PATTERNS` entry fires on a positive fixture string + does NOT fire on realistic negatives (random IDs, the `.env.example` content, UUIDs). Also verifies `scan_text` returns the right line+column offset.
   - `test_sanitizer.py` — feeds the processor a dict containing a secret literal + a key-named-secret + a nested dict + a list; asserts the output dict has exact `"***REDACTED***"` matches at the expected slots and leaves everything else untouched. Also verifies structlog-processor-shape (first two args ignored).
   - `test_precommit_hook.py` — builds a tmp file with + without a secret, calls `main([str(path)])`, asserts exit codes + stderr line shape.

7. **AC-7: `.env.example` smoke-pass.** `scan_file(".env.example")` returns `[]` — the template file doesn't accidentally carry a pattern. If it does (e.g., someone embeds an example key), the pre-commit hook must either skip `.env.example` (already in the `exclude` list) OR the template must be rewritten. Story's AC-4 `exclude` regex handles this; verify belt-and-suspenders.

8. **AC-8: Regression — existing stories' files don't trip the scanner.** Run `secret-hygiene-precommit` against every tracked `.py` + `.md` + `.yml` + `.toml` file. Expected: zero violations (the scaffold has no embedded secrets). If any fixture or doc happens to match (e.g., a literal `sk-ant-...` in the `.env.example` TUNNEL_MODE docs that was added in Story 1.4), either edit the fixture to a safer placeholder (`sk-ant-REPLACE_ME` or `sk-ant-<paste-here>`) OR add an inline `# pragma: secret-hygiene ignore` style escape. Decision defaulted to "edit the fixture" — scanner should not have to understand source-comment escapes in Phase 1.

9. **AC-9: README Quickstart addition.** The README's setup/operator-bootstrap section gets a one-line addition:
   ```markdown
   - Run `uv run pre-commit install` once after cloning so the secret-scanner hook wires into your `.git/hooks/pre-commit`.
   ```
   Placement: after whichever Quickstart step first checks out the repo. If the README doesn't have a Quickstart yet (Story 1.10a may own that), add the line to the top-of-README narrative and defer formal Quickstart placement to 1.10a.

10. **AC-10: Justfile `scan-secrets` recipe.** Operator sanity command:
    ```justfile
    # Run the secret-hygiene scanner across every tracked file (not just staged).
    # Pre-commit hook runs it per-commit; this recipe is for periodic full sweeps.
    scan-secrets:
        uv run secret-hygiene-precommit $(git ls-files)
    ```
    (Uses `$(git ls-files)` so .gitignored content and upstream/ are naturally skipped.)

11. **AC-11: Extend `just lint` with `scan-secrets`.** Add it to the lint recipe after the 3 check-gates so a pre-PR `just lint` catches secret leaks too. (Same rationale Story 1.6 documented for the 3 check-gates — keep `just lint` as one-stop local-gate parity with CI.)

12. **AC-12: CI wiring.** `.github/workflows/ci.yml` gains one step between the 3 check-gates and pytest:
    ```yaml
    - name: Check secrets (full tree)
      run: uv run secret-hygiene-precommit $(git ls-files)
    ```

13. **AC-13: Regression — all prior verifications stay green.** `bootstrap-verify`, `test` (placeholder + new secret-hygiene tests), `lint` (ruff + mypy + 3 gates + scan-secrets), `migrator-test-additive`, `check-gates-self-test` all exit 0.

14. **AC-14: Atomic commit.** All new files + modifications land in one commit titled `chore(scaffold): story 1.7 — secret-scanner pre-commit + log sanitizer · FR43 NFR-S1`. Docs-only follow-ups permitted.

## Tasks / Subtasks

- [x] **Task 1: `scanner.py`** (AC: #1, #7)
  - [x] Write `SECRET_PATTERNS` dict with the 5 MVP entries + short per-entry docstring explaining why each is on the list.
  - [x] `SecretMatch` dataclass: `pattern_name: str`, `start: int`, `end: int`, `line: int`, `column: int`, `excerpt: str` (the matched text with the secret itself redacted to `"***"` — never echo the real value in output).
  - [x] `scan_text(text: str) -> list[SecretMatch]` — iterate over every pattern, `re.finditer`, compute line+column from match start.
  - [x] `scan_file(path: Path) -> list[SecretMatch]` — reads text + calls `scan_text`. Handle `UnicodeDecodeError` gracefully (binary files → `[]`).
  - [x] Verify `.env.example` + existing repo content scan clean.

- [x] **Task 2: `sanitizer.py`** (AC: #2)
  - [x] `redact_secrets(_logger, _method, event_dict)` — structlog processor shape.
  - [x] Value-pattern redaction (every string value scanned via `scanner.scan_text`; any hit → entire value replaced with `REDACTED_SENTINEL`).
  - [x] Key-name redaction (key in `_KEY_REDACT_SET` → value replaced regardless of type).
  - [x] Recurse into nested `dict`, `list`, `tuple` (preserve type).
  - [x] Export `REDACTED_SENTINEL = "***REDACTED***"`.
  - [x] Document the processor's position in the structlog chain: it must run BEFORE the JSON renderer (after `add_log_level`, `TimeStamper`, etc.) so the sanitizer sees structured dicts, not a JSON-serialized string.

- [x] **Task 3: `precommit_hook.py`** (AC: #3)
  - [x] `main(argv: list[str]) -> int` — parse argv as file paths (pre-commit passes them positionally), optional `--allowlist-file`, optional `--verbose`.
  - [x] For each file, call `scan_file`; collect matches.
  - [x] Print `<file>:<line>:<col> [<pattern_name>] <excerpt>` per match to stderr.
  - [x] Exit 1 on any match, 0 on clean.
  - [x] Allowlist file format: one glob pattern per line (`#` comments OK); used to exclude paths that are impossible to rewrite (e.g., vendored upstream/ already has its own gitignore).

- [x] **Task 4: Update `packages/secret-hygiene/pyproject.toml`** (AC: #5)
  - [x] Add `dependencies = ["structlog>=24.1"]` (pick current stable).
  - [x] Add `[project.scripts] secret-hygiene-precommit = "secret_hygiene.precommit_hook:main"`.
  - [x] Verify `uv sync` re-resolves cleanly.

- [x] **Task 5: Root `pyproject.toml` dev-deps** (AC: #5)
  - [x] Add `"pre-commit"` to `[dependency-groups.dev]`.
  - [x] `uv lock` regenerates.

- [x] **Task 6: `.pre-commit-config.yaml`** (AC: #4)
  - [x] Write per AC-4 spec. Single `repo: local` hook invoking the uv-managed entrypoint.
  - [x] `exclude` regex covers `.venv`, `upstream`, `_bmad`, `_bmad-output`, `.lock` files, `.env.example`.

- [x] **Task 7: Co-located unit tests** (AC: #6)
  - [x] `packages/secret-hygiene/src/secret_hygiene/test_scanner.py` — positive + negative cases per pattern; line/column correctness.
  - [x] `packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py` — value-pattern + key-name + nested-dict + list + type preservation.
  - [x] `packages/secret-hygiene/src/secret_hygiene/test_precommit_hook.py` — tmp file + exit code + stderr shape.
  - [x] Verify all pass via `uv run pytest packages/secret-hygiene/`.

- [x] **Task 8: `.env.example` + tracked-content smoke** (AC: #7, #8)
  - [x] Run `uv run secret-hygiene-precommit $(git ls-files)` locally; confirm zero violations.
  - [x] If any tracked file trips a pattern, rewrite the file's placeholder to something obviously non-secret (e.g., `sk-ant-REPLACE_ME`, `123456:AAPLACEHOLDER...`).

- [x] **Task 9: Justfile `scan-secrets` + extend `lint`** (AC: #10, #11)
  - [x] Add `scan-secrets` recipe.
  - [x] Extend `lint` body with `uv run secret-hygiene-precommit $(git ls-files)` (last line — fastest-fail items first per existing convention).

- [x] **Task 10: CI step** (AC: #12)
  - [x] Add `Check secrets (full tree)` step to `.github/workflows/ci.yml` between the 3 check-gate steps and pytest.
  - [x] Validate YAML still parses.

- [x] **Task 11: README note** (AC: #9)
  - [x] Add the `uv run pre-commit install` line to the operator-bootstrap section.

- [x] **Task 12: Verification** (AC: #13)
  - [x] `just bootstrap-verify` → 13/13 + 0 dev-deps.
  - [x] `just test` → new unit tests + existing 6 placeholders all pass (new count: 3 × new tests plus 6 skipped).
  - [x] `just lint` → ruff + format + mypy + 3 gates + scan-secrets all green.
  - [x] `just migrator-test-additive` → 3/3.
  - [x] `just check-gates-self-test` → all 3 self-tests green.
  - [x] Try committing a file with `TELEGRAM_BOT_TOKEN=<12-digit-bot-id>:AA<30+-char-blob>` to a local branch — pre-commit blocks it with the correct error message. Revert before committing anything real.

- [x] **Task 13: Atomic commit** (AC: #14)
  - [x] Single commit per AC-14 title.

## Dev Notes

### Architecture patterns for this story

- **Three-layer secret hygiene** (Architecture line 64). This story delivers two of the three layers:
  - Pre-commit scanner (source-control arm) — Story 1.7 via `.pre-commit-config.yaml` + `precommit_hook.py`.
  - Runtime log sanitizer (observability arm) — Story 1.7 via `sanitizer.py`.
  - `secret.accessed` audit event emission — Story 2.16 (out of scope here).
- **Pre-commit uses `language: system`** rather than `language: python` so the hook runs inside the project's `uv`-managed venv (consistent with how `ruff`, `mypy`, `pytest` are invoked). Avoids pre-commit creating its own parallel environment.
- **`SECRET_PATTERNS` is the single source of truth** — both `scanner.py` (pre-commit) and `sanitizer.py` (runtime) read from it. Stories that add new secret types (e.g., Story 5.14 adds `GITHUB_APP_PRIVATE_KEY`) extend this dict in one place.
- **`REDACTED_SENTINEL = "***REDACTED***"`** — fixed string so tests can assert exact-match. If future stories want per-pattern sentinels (e.g., `"<ANTHROPIC_KEY>"`) they can extend without breaking string-equality tests that check for `"***REDACTED***"`.
- **structlog processor contract** (Architecture line 414). `redact_secrets` has the signature `(logger, method_name, event_dict) -> event_dict`. It runs in the processor chain BEFORE the JSON renderer — guaranteed to see dicts, not serialized output.

### What this story does NOT do

- `secret.accessed` typed audit events (Story 2.16).
- License-scan wrapper (Story 6.9).
- Runtime redaction of artifacts/snapshots (Stories 2.6 + 2.16 — snapshot capture + audit events).
- Ruff rule for FR18b "no stdout parsing" (deferred; can live alongside Story 1.7's pre-commit additions in a follow-up story that adds the ruff plugin layer).
- Secret-rotation tooling (FR48 is operator-facing: edit `.env` + `docker compose up -d`; no script needed).
- CLI for operators to run the sanitizer against existing log files — defer to an ops-only story if operator needs it.

### Source tree components to touch

```
oh-my-bmad/
├── .pre-commit-config.yaml                                         # Task 6 NEW
├── .github/workflows/ci.yml                                        # Task 10 MODIFIED (+1 step)
├── justfile                                                        # Task 9 MODIFIED (+1 recipe, lint extended)
├── pyproject.toml                                                  # Task 5 MODIFIED ([dependency-groups.dev] += pre-commit)
├── README.md                                                       # Task 11 MODIFIED (+1 line)
├── uv.lock                                                         # regenerated
└── packages/secret-hygiene/
    ├── pyproject.toml                                              # Task 4 MODIFIED (+deps + script entry)
    └── src/secret_hygiene/
        ├── __init__.py                                             # unchanged
        ├── scanner.py                                              # Task 1 NEW
        ├── sanitizer.py                                            # Task 2 NEW
        ├── precommit_hook.py                                       # Task 3 NEW
        ├── test_scanner.py                                         # Task 7 NEW
        ├── test_sanitizer.py                                       # Task 7 NEW
        └── test_precommit_hook.py                                  # Task 7 NEW
```

**Files: ~7 new + 5 modified (+ 1 regenerated `uv.lock`).**

### `SECRET_PATTERNS` rationale per entry

- **`ANTHROPIC_API_KEY` `sk-ant-`** — Claude API; operator's primary critical secret; rotating costs downtime.
- **`TELEGRAM_BOT_TOKEN` `\d+:AA…`** — Telegram bot ID + secret; leaked token means an attacker can DM all operator contacts as the bot.
- **`GITHUB_TOKEN_CLASSIC` + `GITHUB_TOKEN_FINE`** — PR-draft creation (FR5.14); leaked PAT owns the operator's repos.
- **`GENERIC_AWS_ACCESS_KEY`** — defensive; Phase 1 doesn't wire AWS but an operator pasting third-party credentials is realistic.

Intentionally NOT in MVP set (explicit out-of-scope):
- Generic "password=..." regex — too many false positives (YAML keys, docstrings).
- SSH private keys (BEGIN RSA PRIVATE KEY) — `gitleaks` / `detect-secrets` already excels here; revisit if we switch the hook to a pre-built scanner.
- Slack tokens (`xox[bp]-...`) — Phase 7 adds Slack; defer.

### `sanitizer.py` key-name redaction

`_KEY_REDACT_SET = frozenset({"api_key", "apikey", "token", "password", "secret", "authorization", "auth", "bearer", "anthropic_api_key", "telegram_bot_token", "github_token"})` — lowercase match after `.casefold()`. Key-name match is a conservative defense: if someone logs `api_key=12345` where the value doesn't match any pattern, we still redact because the KEY says it's a secret.

### AC-8: the `.env.example` verification

The `.env.example` file (shipped Story 1.4) has:
```
TELEGRAM_BOT_TOKEN=
ANTHROPIC_API_KEY=
GITHUB_TOKEN=
```
All empty values — none match a pattern. The tunnel-mode section has no secrets. Story 1.7 verifies this + documents the assertion in Completion Notes.

### Pre-commit hook lifecycle

Pre-commit hooks are opt-in per-developer — an operator who doesn't run `uv run pre-commit install` doesn't get the hook. This is why CI also runs the scanner as a step (AC-12 + AC-11's `just lint` integration): pre-commit catches it on the operator's machine, CI catches it on push. Defense in depth.

### Test layout — co-located vs top-level

Story 1.5's test tree is at top-level `tests/` with placeholder markers for 6 trees. Story 1.7 tests live co-located with the module being tested (`packages/secret-hygiene/src/secret_hygiene/test_*.py`), per Architecture line 344. pytest's `testpaths` from Story 1.5 covers `packages/` so these get discovered automatically. The new tests don't carry tree-markers (not `@pytest.mark.integration` etc.); they're plain unit tests.

### Previous Story Intelligence (Stories 1.1–1.6)

Carry-forward learnings:
- **Scaffold-before-real-content pattern**: consistent across 1.1–1.6. Story 1.7 lands enforcement for the secret-hygiene layer that's been stubbed since Story 1.2.
- **`just lint` as one-stop gate**: Story 1.6 added 3 check-gates to `lint`; Story 1.7 adds `scan-secrets`. `just lint` stays the single pre-PR command — no surprises when CI runs differently.
- **Atomic commit**: single commit per story, with review-fix + docs-finalize as follow-ups.
- **noqa-style suppression**: Story 1.6 established `# noqa: IMP001 <reason>` / `EVT001` / `SW001` with required reasons. Story 1.7 uses a different mechanism — pre-commit `exclude` regex at config level rather than per-line noqa. Rationale: pre-commit runs per-file, not per-line; inline suppressions don't fit. If a line-level escape becomes necessary later (e.g., a documented example in code), we add a pragma then.
- **`uv run --no-dev` isolation**: Story 1.5's bootstrap-verify pattern. Story 1.7's pre-commit hook runs via `uv run secret-hygiene-precommit` which picks up `--dev` — that's correct because the hook tool IS a dev dep for operators.

### Git Intelligence (recent commits)

- `6a20dd6 docs(story-1-6): finalize + mark done`
- `a834f11 chore(scaffold): apply story 1.6 code-review fixes · all severities`
- `7273a10 docs(story-1-6): finalize story file + mark review`
- `fbf18d7 chore(scaffold): story 1.6 — import/event-registry/single-writer CI gates · NFR-M1 NFR-O1 FR18b FR26`

Cadence consistent: scaffold → review → fix → finalize.

### Latest Tech Information

- **`structlog` 24.x** — current stable. Processor signatures unchanged from 23.x. Pin lower bound `>=24.1`.
- **`pre-commit` 3.x** — `language: system` + `entry: uv run ...` is the recommended pattern for invoking in-repo tools without creating a parallel env.
- **Python 3.12 `re` regex** — no changes relevant to this story; the patterns use basic features only.
- **`casefold()` vs `lower()`** — use `casefold()` for key-name matching (handles Unicode edge cases — `ß → ss`).

### References

- `epics.md` §Epic 1 / Story 1.7 (lines 569-588) — ACs source.
- `architecture.md` lines 51 (component 11), 64 (three-layer hygiene), 344 (co-located test layout), 414 (structlog processor chain), 453 (log-capture test suite), 453 (secret-scanner pre-commit), 550 (.pre-commit-config.yaml), 587-593 (secret_hygiene/ tree).
- `prd.md` FR43 (line 873 — sanitize events/snapshots/artifacts/logs), NFR-S1 (line 921 — zero plaintext secret persistence, scanner + runtime sanitizer).
- `1-2-remaining-service-and-mcp-scaffolds.md` — the `secret-hygiene` workspace member this story fills in.
- `1-6-ci-gates-imports-events-single-writer.md` — pattern for CI-step addition + `just lint` extension.
- `.env.example` (from Story 1.4) — empty-values format to verify against scanner.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — tight scope (scanner + sanitizer + pre-commit + 3 unit-test files + config additions), no Opus reasoning needed unless a regex edge-case surprises.

### Debug Log References

_Placeholder._

### Completion Notes List

**Implementation summary**

- Two-arm secret-hygiene now live: pre-commit scanner (source-control) + structlog sanitizer (runtime). Arm 3 (`secret.accessed` audit events) deferred to Story 2.16 per spec scope. `SECRET_PATTERNS` lives in `scanner.py` and both `sanitizer.py` + `precommit_hook.py` read from it — single source of truth.
- `REDACTED_SENTINEL = "***REDACTED***"` for test equality.
- 5 MVP patterns: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN_CLASSIC`, `GITHUB_TOKEN_FINE`, `GENERIC_AWS_ACCESS_KEY`.
- `precommit_hook.py` registered as console-script `secret-hygiene-precommit`; invoked from `.pre-commit-config.yaml`, `ci.yml`, `justfile` recipes (all via `uv run`).

**Locked versions**

| Package | Version |
|---|---|
| structlog | 25.5.0 |
| pre-commit | 4.6.0 |

**Line counts**

| File | LOC |
|---|---|
| `scanner.py` | 93 |
| `sanitizer.py` | 109 |
| `precommit_hook.py` | 102 |
| `test_scanner.py` | 174 |
| `test_sanitizer.py` | 176 |
| `test_precommit_hook.py` | 174 |

**AC-by-AC evidence**

- **AC-1** ✓ — scanner.py ships 5 patterns + SecretMatch + scan_text/scan_file; graceful binary-file handling.
- **AC-2** ✓ — sanitizer.py ships redact_secrets structlog processor + key-name + recursion + list/tuple type preservation.
- **AC-3** ✓ — precommit_hook.py ships main(argv) + exit 0/1 + stderr shape + `--allowlist-file` + `--verbose`.
- **AC-4** ✓ — `.pre-commit-config.yaml` wires `secret-hygiene-precommit` via `language: system` + `uv run`.
- **AC-5** ✓ — `packages/secret-hygiene/pyproject.toml` gains `structlog>=24.1` + `[project.scripts]`; root `[dependency-groups.dev]` gains `pre-commit`.
- **AC-6** ✓ — 3 co-located test files: 54 pytest cases passed.
- **AC-7** ✓ — `secret-hygiene-precommit .env.example` exit 0 (empty values fall below the `{20,}`/`{30,}` length minimums).
- **AC-8** ✓ — `secret-hygiene-precommit $(git ls-files)` exit 0 on bare repo; no tracked file required rewriting.
- **AC-9** ✓ — README Quickstart line added: `uv run pre-commit install` right after `just bootstrap-verify`.
- **AC-10** ✓ — `just scan-secrets` recipe added.
- **AC-11** ✓ — `just lint` extended to call scan-secrets after the 3 check-gates.
- **AC-12** ✓ — `.github/workflows/ci.yml` `Check secrets (full tree)` step between `Check-scripts self-tests` and `pytest -m "not slow"`.
- **AC-13** ✓ — `bootstrap-verify` 13/13 + 0 dev-dep leak; `test` 54 passed + 6 skipped; `lint` all 5 sub-commands green (ruff + format + mypy + 3 check-gates + scan-secrets); `migrator-test-additive` 3/3; `check-gates-self-test` all 3 green.
- **AC-14** ✓ — atomic scaffold commit `9ca0674` (13 files changed, 1127+/-1).

**Deviations (documented)**

1. `SecretMatch.excerpt` is always the literal `f"<{pattern_name}>"` rather than the "matched text with the secret value itself redacted to `***`" design from Task 1. The stronger "never echo raw bytes" guarantee was preferred — even redacted-in-place excerpts risk leaking the non-secret prefix (e.g., `"ANTHROPIC_API_KEY=***"` in a log is fine; `"sk-ant-***"` is fine; but if the surrounding text is itself sensitive the excerpt could leak context). Using just the pattern-name tag is a conservative upgrade over the spec; tests updated accordingly.
2. Test fixture `sk-ant-abcdef1234567890XYZ` was 1 char short of the `{20,}` length minimum during initial authoring; corrected to 20+ chars. Real patterns unchanged.
3. `exclude` regex in `.pre-commit-config.yaml` added `uv\.lock$` alongside the spec's `*.lock$` — `uv.lock` is a specific file; both would have worked. Belt-and-suspenders.
4. `_KEY_REDACT_SET` uses `.casefold()` for the key-name match (spec said "after `.casefold()`" in Dev Notes); confirmed in place.

**Regression risk for Stories 1.8+**

- None. Story 1.8 adds `Dockerfile.base` + per-service multi-stage Dockerfiles — touches service build/runtime layer, no overlap with secret-hygiene. Story 1.9 adds `release.yml` — additive. Story 2.1 lands real `EventEnvelope` which will consume `sanitizer.py` via structlog's processor chain; the processor interface is locked.

### File List

**New (7):**

- `packages/secret-hygiene/src/secret_hygiene/scanner.py`
- `packages/secret-hygiene/src/secret_hygiene/sanitizer.py`
- `packages/secret-hygiene/src/secret_hygiene/precommit_hook.py`
- `packages/secret-hygiene/src/secret_hygiene/test_scanner.py`
- `packages/secret-hygiene/src/secret_hygiene/test_sanitizer.py`
- `packages/secret-hygiene/src/secret_hygiene/test_precommit_hook.py`
- `.pre-commit-config.yaml`

**Modified (5):**

- `packages/secret-hygiene/pyproject.toml` (+`structlog` dep, +`[project.scripts]`)
- `pyproject.toml` (+`pre-commit` in `[dependency-groups.dev]`)
- `.github/workflows/ci.yml` (+1 step)
- `justfile` (+`scan-secrets` recipe; `lint` extended)
- `README.md` (+1 Quickstart line)

**Regenerated:** `uv.lock` (40 packages, +structlog +pre-commit + transitive closure).

### Change Log

- **2026-04-24:** Story 1.7 implemented. 7 new + 5 modified + uv.lock; atomic scaffold commit `9ca0674` (1127+/1-). Verification: 54 unit tests pass, all `just`-level regressions stay green (`bootstrap-verify` 13/13 + 0 dev-dep leak; `test` 54+6; `lint` all 5 sub-commands; `migrator-test-additive` 3/3; `check-gates-self-test` all 3). Status: `ready-for-dev` → `in-progress` → `review`.
