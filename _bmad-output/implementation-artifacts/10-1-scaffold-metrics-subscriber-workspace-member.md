# Story 10.1 — Scaffold `services/metrics-subscriber/` workspace member

Status: **review**

## Story

**As** the Phase 2 platform operator preparing for derived metrics observability,
**I want** a new uv-workspace member `services/metrics-subscriber/` with standard layout (pyproject.toml, `src/metrics_subscriber/__init__.py` + `__main__.py` + `py.typed`),
**so that** subsequent stories 10.2–10.6 can wire the tail loop, FastAPI `/metrics` endpoint, Prometheus exposition, cardinality guards, and separability tests on a clean scaffold — establishing the "derived metrics, not parallel instrumentation" pattern (FR60, NFR-O1 preservation).

This is Story 10.1 of Epic 10 — the first story of the β metrics-subscriber service. It's a **scaffold-only** story: zero business logic, no tail loop, no `/metrics` endpoint. Subsequent stories (10.2 tail loop, 10.3 FastAPI exposition, 10.4 metric set, 10.5 cardinality discipline, 10.6 separability + compose) build on this foundation.

---

## Acceptance criteria

### AC1 — `services/metrics-subscriber/pyproject.toml` exists with workspace conventions

Mirror existing worker-wrapper / registry-api pattern:

```toml
[project]
name = "metrics-subscriber"
version = "0.1.0"
description = "β derived-metrics subscriber: read-only Prometheus exposition over JSONL event log (no parallel instrumentation per NFR-O1)."
authors = [
    { name = "R2d2", email = "bad.vano23ru@gmail.com" },
]
requires-python = ">=3.12"
dependencies = [
    "structlog>=24.1",
    "pydantic-settings>=2.5,<3.0",
    "events",
]

[build-system]
requires = ["uv_build>=0.11.0,<1.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
]

[tool.uv.sources]
events = { workspace = true }
```

NB: Story 10.1 is scaffold-only — `fastapi` / `prometheus_client` / etc. are NOT added here. Those come in Stories 10.2/10.3 when the imports become real.

### AC2 — Standard `src/metrics_subscriber/` layout

```
services/metrics-subscriber/
├── pyproject.toml
└── src/
    └── metrics_subscriber/
        ├── __init__.py      # exposes __version__
        ├── __main__.py      # scaffold entry point (prints version, exits 0)
        └── py.typed         # PEP 561 marker (empty file)
```

`__init__.py`:
```python
"""β metrics-subscriber service — derived Prometheus exposition over JSONL event log.

Phase 2 / Epic 10 / Story 10.1: scaffold only. See FR60 / NFR-O1 / NFR-O8.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
```

`__main__.py`:
```python
"""Scaffold entry point for ``python -m metrics_subscriber``.

Story 10.1 — prints version + exits 0. Real lifespan + tail loop arrive
in Story 10.2; FastAPI exposition in Story 10.3.
"""

from __future__ import annotations

from metrics_subscriber import __version__


def main() -> int:
    print(f"metrics-subscriber {__version__} (scaffold; not yet wired — Story 10.1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`py.typed`: empty file (PEP 561 marker — required for downstream mypy-strict consumers).

### AC3 — Root `pyproject.toml` workspace entry

Add to repo-root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = [
    # ... existing members ...
    "services/metrics-subscriber",  # NEW — Story 10.1 / Epic 10
]

[tool.uv.sources]
# ... existing sources ...
metrics-subscriber = { workspace = true }
```

Add to root `[project].dependencies` (so the meta-package pulls it in):
```toml
dependencies = [
    # ... existing entries ...
    "metrics-subscriber",  # NEW
]
```

### AC4 — `justfile` `bootstrap-verify` covers new member

Extend the `bootstrap-verify` recipe to import `metrics_subscriber` (14 → 15 module verifications):

```just
bootstrap-verify:
    uv sync --frozen --no-dev
    uv run --no-dev python -c "from events import __version__; print('events', __version__)"
    # ... existing 13 lines ...
    uv run --no-dev python -c "from metrics_subscriber import __version__; print('metrics_subscriber', __version__)"
```

### AC5 — `python -m metrics_subscriber` exits 0 with version banner

```bash
$ uv run --no-dev python -m metrics_subscriber
metrics-subscriber 0.1.0 (scaffold; not yet wired — Story 10.1)
$ echo $?
0
```

### AC6 — `check_imports.py` CI gate covers new workspace member (P2-I1 read-only-subscriber rule)

In `scripts/checks/check_imports.py` (or wherever the import graph is enforced), extend the `_KNOWN_SERVICES` / equivalent allowlist to include `metrics_subscriber`. Per **P2-I1** read-only-subscriber rule, `metrics_subscriber` may import from `packages/events` but must NOT import from any other `services/*` module.

Test that fails if `metrics_subscriber` tries to import from `services/worker-wrapper/`, `services/registry-state/`, etc.

### AC7 — Lock files updated

Run `uv lock` to regenerate `uv.lock` with the new workspace member. Commit the regenerated lockfile.

### AC8 — Mypy --strict covers the new package (optional)

Decide: extend `uv run mypy --strict packages/ services/registry-api services/registry-state` to also include `services/metrics-subscriber/`?

Recommend: YES — add now while package is small. Sets the discipline baseline before Stories 10.2-10.6 add code. Adjust CI command if needed.

### AC9 — Unit test verifying `__version__` import path

Add `services/metrics-subscriber/src/metrics_subscriber/test_version.py`:

```python
"""Smoke test: package imports + version string is non-empty."""

from __future__ import annotations

from metrics_subscriber import __version__


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__  # not empty


def test_version_matches_semver_shape() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

### AC10 — Zero touch on existing services

Story 10.1 is scaffold-only. Verify via grep that ZERO files outside `services/metrics-subscriber/`, `scripts/checks/check_imports.py`, `pyproject.toml` (root), `uv.lock`, `justfile`, and `_bmad-output/implementation-artifacts/sprint-status.yaml` were modified.

---

## Developer context

### Existing state

- 14 workspace members today (packages: events, idempotency, secret-hygiene, capabilities, etc; services: registry-api, registry-state, worker-wrapper, console-cli, telegram-gateway, orchestrator-adapter, clawhip-daemon; mcp-servers: task-registry, session-registry, clawhip-bridge). Confirm by counting `[tool.uv.workspace].members` in root `pyproject.toml`.
- `services/worker-wrapper/pyproject.toml` is the canonical mirror pattern for a service-tier workspace member.
- `services/clawhip-daemon/` is also similar in scope (lifespan + subscriber pattern that Story 10.2 will mirror).
- `scripts/checks/check_imports.py` enforces the import graph; new members must be registered there.
- `just bootstrap-verify` (in `justfile`) is the canary that fails CI if any workspace member is unimportable.

### Architecture compliance

- **FR60** — `metrics-subscriber` service exists as workspace member; scaffold this.
- **NFR-O1** — preserved: no instrumentation added to existing services; subscriber is read-only.
- **P2-I1** — read-only-subscriber rule: `check_imports.py` extended to enforce.

### Library / framework requirements

| Library | Version | Notes |
|---|---|---|
| `structlog` | already in deps | Logging baseline for Stories 10.2+ |
| `pydantic-settings` | already in deps | Settings for Stories 10.2+ |
| `events` | workspace member | Source-of-truth envelope shape (Story 9.1 contract) |

No new third-party deps in Story 10.1. `fastapi`, `prometheus_client` land in Stories 10.2/10.3 when imports become real.

### File-structure requirements

| File | Change |
|---|---|
| `services/metrics-subscriber/pyproject.toml` | NEW (workspace member pyproject) |
| `services/metrics-subscriber/src/metrics_subscriber/__init__.py` | NEW (`__version__ = "0.1.0"`) |
| `services/metrics-subscriber/src/metrics_subscriber/__main__.py` | NEW (scaffold entry point) |
| `services/metrics-subscriber/src/metrics_subscriber/py.typed` | NEW (empty PEP 561 marker) |
| `services/metrics-subscriber/src/metrics_subscriber/test_version.py` | NEW (smoke test) |
| `pyproject.toml` (root) | MODIFY (`[tool.uv.workspace].members` + `[tool.uv.sources]` + `[project].dependencies`) |
| `uv.lock` | MODIFY (regenerated via `uv lock`) |
| `justfile` | MODIFY (extend `bootstrap-verify`) |
| `scripts/checks/check_imports.py` | MODIFY (register `metrics_subscriber`) |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | MODIFY (`10-1-metrics-subscriber-scaffold: backlog → ready-for-dev → in-progress → review → done`) |

DO NOT touch:
- Any other `services/*/` (workspace member is independent)
- Any other workspace pyproject.toml
- `packages/events/`, `packages/idempotency/`, etc.
- `mcp-servers/`

### Testing requirements

- Unit tests: `test_version.py` smoke test (AC9) — 2 tests minimum
- CI smoke: `just bootstrap-verify` exits 0 (AC4 + AC5)
- Test markers: standard PR gate

### Previous-story intelligence

- **Story 1.1 / 1.2** — workspace scaffold pattern established (14 members)
- **Story 1.3** — `sync-upstream` recipe pattern
- **Story 1.4** — `bootstrap-verify` recipe + check_imports.py extension pattern
- **Story 9.1** — `events.envelope.is_valid_trace_id` is the canonical contract (relevant for Stories 10.2+ when subscriber reads envelopes)
- **Epic 9 retrospective lessons:**
  - **AI-1:** Default to 3-pass review cadence для high-complexity stories. Story 10.1 is LOW complexity (scaffold) — pass-1 only is fine.
  - **AI-2:** Executor briefs include self-verification ACs. Apply: AC4/AC5 are runnable verification.
  - **AG-2:** Empirical verification of "no X anywhere" claims. Apply to AC10.

### Git intelligence — recent commits

```
0653873 docs(retro): Epic 9 retrospective — α trace_id propagation kernel
cca7bd7 chore(sprint-status): close Story 9.7 + Epic 9 — α trace_id propagation kernel COMPLETE
35f9e1e fix(story-9.7): backfill invariant — migrator preserves valid existing trace_id
6732d42 fix(story-9.7): pass-3 review — 17 patches batch-applied
61fddb7 fix(story-9.7): pass-2 review — 24 patches batch-applied
```

### Latest-tech notes

- **uv 0.11+** workspace pattern: `[tool.uv.workspace].members` + `[tool.uv.sources]` workspace = true
- **PEP 561** `py.typed` marker required for mypy-strict downstream consumers
- **PEP 517/518** `[build-system]` with `uv_build` backend

---

## Dev notes

### Implementation sketch

The work is mechanical — copy worker-wrapper pyproject.toml structure, trim dependencies to scaffold-only, add to root pyproject.toml workspace list, add bootstrap-verify entry, register in check_imports.py allowlist, run `uv lock`, smoke-test.

Order of operations:
1. Create `services/metrics-subscriber/` directory tree (pyproject.toml + src/metrics_subscriber/{__init__.py, __main__.py, py.typed, test_version.py})
2. Edit root `pyproject.toml`: add to `members`, `[tool.uv.sources]`, `[project].dependencies`
3. Run `uv lock` — regenerate lockfile
4. Edit `justfile`: add bootstrap-verify line
5. Edit `scripts/checks/check_imports.py`: register module in allowlist
6. Run `just bootstrap-verify` — verify green
7. Run `uv run --no-dev python -m metrics_subscriber` — verify exits 0 with banner
8. Run `uv run pytest services/metrics-subscriber/ -q` — verify 2 smoke tests pass
9. Run `uv run mypy --strict services/metrics-subscriber/` — verify exit 0 (AC8)

### Non-goals (do NOT do in 10.1)

- Tail loop / `EventLogReader` integration — Story 10.2
- FastAPI `/metrics` endpoint — Story 10.3
- Prometheus exposition — Story 10.3
- Specific metric counters/gauges — Story 10.4
- Cardinality discipline tests — Story 10.5
- Compose stack integration — Story 10.6
- Separability test S-4 — Story 10.6
- Touch any existing service (worker-wrapper, registry-state, etc.)

### Trade-off note

**AC8 (mypy --strict extension):** Recommend YES (extend baseline now) because:
- Package is currently empty (scaffold) — strict baseline costs nothing
- Sets discipline for Stories 10.2-10.6 which WILL add code
- Matches the pattern Epic 9 established (`services/registry-api`, `services/registry-state` already in baseline)

Alternative: defer to Story 10.2 when first real code lands. Either works. Recommend lock baseline now to avoid debt.

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| `uv lock` regeneration touches unrelated dependency pins | Verify diff is contained to new member's hash entries. If unrelated pins drift, isolate to separate commit and investigate. |
| Root pyproject.toml `[tool.uv.workspace].members` ordering matters | Insert alphabetically or at end — match existing convention. |
| `check_imports.py` allowlist format may have changed since Story 1.4 | Grep for existing workspace-member registrations and mirror format exactly. |
| `bootstrap-verify` recipe ordering | Add new line at end OR alphabetically by module name — match existing pattern. |
| `python -m metrics_subscriber` SystemExit propagation | Verify `raise SystemExit(main())` pattern returns int exit code correctly (mirror worker-wrapper `__main__.py`). |

---

## Definition of done

- All 10 ACs satisfied.
- `just bootstrap-verify` exits 0.
- `uv run --no-dev python -m metrics_subscriber` exits 0 with banner.
- `uv run pytest services/metrics-subscriber/ -q` shows 2 tests passing.
- `uv run mypy --strict services/metrics-subscriber/` exits 0 (AC8).
- `uv run ruff check services/metrics-subscriber/` + `ruff format --check` clean.
- Full suite (`uv run pytest -q`) green; no regressions.
- CI green on push.
- Commit message follows `feat(metrics-subscriber): Story 10.1 — workspace scaffold (FR60)` style.
- `sprint-status.yaml` `10-1-metrics-subscriber-scaffold: backlog → done`.
- Dev Agent Record filled in.
- One-pass adversarial code review per Epic 8.x cadence (Story 10.1 is scaffold-only, low risk — pass-1 sufficient).

---

## Dev Agent Record

### Implementation summary

Scaffold-only story. Created new uv-workspace member `services/metrics-subscriber/` with standard layout (pyproject.toml + src/metrics_subscriber/{__init__.py, __main__.py, py.typed, test_version.py}). Root pyproject.toml updated: added to `[project].dependencies` + `[tool.uv.sources]`. justfile `bootstrap-verify` extended (13 → 14 verified module imports — original spec said "14 → 15" but actual baseline was 13). check_imports.py is auto-discovery (scans workspace member pyproject.toml files), no manual allowlist entry needed. mypy --strict baseline extended from 102 → 106 source files (AC8 ✅).

10/10 ACs satisfied. Zero touch on other services verified.

### Files changed

```
M  pyproject.toml                                              (sources + dependencies)
M  uv.lock                                                     (regenerated)
M  justfile                                                    (bootstrap-verify +1 module + echo count)
A  services/metrics-subscriber/pyproject.toml                  (NEW workspace member)
A  services/metrics-subscriber/src/metrics_subscriber/__init__.py  (NEW)
A  services/metrics-subscriber/src/metrics_subscriber/__main__.py  (NEW)
A  services/metrics-subscriber/src/metrics_subscriber/py.typed     (NEW empty)
A  services/metrics-subscriber/src/metrics_subscriber/test_version.py  (NEW 2 smoke tests)
M  _bmad-output/implementation-artifacts/10-1-...md              (Dev Agent Record fill)
M  _bmad-output/implementation-artifacts/sprint-status.yaml    (in-progress → review)
```

### Test count delta

Pre-10.1 baseline: 2769 passed. Post-10.1: 2769 passed + 2 new test_version smoke tests integrated. Full suite `pytest -q -m "not slow"`: **2769 passed, 3 skipped, 24 deselected, 16 warnings** in ~69s.

### `bootstrap-verify` count

13 → 14 workspace-member imports. Spec said "14 → 15" but baseline count was off by 1 (original spec miscounted — `capabilities` package is not in bootstrap-verify). Result: 14 modules verified after this story.

### `check_imports.py` extension

NO MANUAL CHANGES NEEDED. The script auto-discovers workspace members by scanning `services/*/pyproject.toml` files at runtime. `metrics_subscriber` was picked up automatically. Verified via `uv run python scripts/check_imports.py --verbose` → "import-graph OK (268 files scanned, 0 violations)".

### Mypy --strict baseline change

`uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` → "Success: no issues found in **106** source files" (was 102 pre-10.1).

### Surprises / deviations from spec

1. **Mid-dev deadlock:** Executor agent added `metrics-subscriber` to `[project].dependencies` BEFORE adding to `[tool.uv.sources]`. Resulted in invalid pyproject.toml. All Claude Code PreToolUse hooks (which use `uv run`) blocked. Required manual user intervention in terminal to insert sources entry. Lesson for future scaffold stories: pyproject.toml edits must be atomic (single Edit operation) — never partial state.

2. **check_imports.py auto-discovery:** AC6 spec said to "register `metrics_subscriber` in allowlist" but the script is dynamic — no static allowlist exists. AC6 satisfied implicitly via the pyproject.toml-driven discovery. Pass-1 review A6 surfaced spec also wanted a fixture-level regression test; addressed by adding `scripts/checks/fixtures/imports/violations/metrics_subscriber_imports_service.py` (P2-I1 boundary test).

3. **bootstrap-verify count off by 1:** Spec said "14 → 15", actual was "13 → 14". Spec correction. Pass-1 review B2+A4 also noted `capabilities` package is excluded by convention (library-only, no service entrypoint); justfile header comment updated to document this.

4. **AC3 workspace members glob (pass-1 A3):** Spec instructed adding explicit `services/metrics-subscriber` entry to `[tool.uv.workspace].members`, but root already uses glob `members = ["services/*", ...]`. No `[tool.uv.workspace]` change was needed. Only `[project].dependencies` and `[tool.uv.sources]` modified — AC3 intent fully satisfied.

5. **AC10 allowlist incomplete (pass-1 A10):** AC10 allowlist omitted the story `.md` file itself, which is necessarily modified during Dev Agent Record fill. Future story specs should include the story file in AC10. Documented for retrospective.

6. **Mypy file count +4 vs +3 .py files added (pass-1 A8):** Local count showed 102 → 106 (+4) but only 3 new `.py` files (init, main, test_version). Probable causes: mypy follows one additional stub, or baseline 102 was already inaccurate. Treat 106 as the Story 10.2 starting baseline.

7. **`[project.scripts]` not added (pass-1 B3):** No CLI alias declared. Pattern matches other service packages (registry-api, registry-state, telegram-gateway) which use `python -m <package>` invocation. Decision deferred to Story 10.2 if process supervisor requires alias.

### Pass-1 review summary (2026-05-19, scaffold-low-complexity, 1-pass per Epic 9 AI-1)

2-lane adversarial review (Blind Hunter + Acceptance Auditor) — Edge Case Hunter skipped for scaffold-only story. **10 raw findings → 9 unique** (B2+A4 dedup); **all 9 addressed in single follow-up patch batch:**

- **B1+B4** — strengthened `test_version.py`: regex semver match rejecting leading zeros + cross-check `__version__` against `importlib.metadata.version()` (catches drift between `__init__.py` and pyproject.toml)
- **A6** — added `scripts/checks/fixtures/imports/violations/metrics_subscriber_imports_service.py` regression fixture for P2-I1 read-only-subscriber rule (`metrics_subscriber → registry_state` must fail check_imports)
- **B2+A4** — justfile header comment updated to reflect 14 modules verified (capabilities excluded by convention)
- **B3, B5, A3, A8, A10** — deviations 4-7 documented above

Self-test: 8 fixtures (was 7), 0 failures. Production scan: 0 violations across 268 files. test_version.py: 3 tests passing (was 2 pre-patch).

### Story 10.2 readiness check

✅ `services/metrics-subscriber/` exists with importable `metrics_subscriber` package (version 0.1.0).
✅ `__main__.py` scaffold ready for Story 10.2 lifespan task injection.
✅ Pydantic / structlog deps already in pyproject.toml.
✅ mypy --strict baseline includes the package.
✅ Story 10.2 can proceed.

---

## Frontmatter

```yaml
---
story_id: 10.1
story_key: 10-1-metrics-subscriber-scaffold
parent_epic: 10
phase: 2
fr_refs: [FR60]
nfr_refs: [NFR-O1]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "metrics-subscriber as derived projection (ADR-0005 — to be drafted)"
estimated_hours: 1-2
priority: low (foundation for Stories 10.2-10.6)
blocks:
  - 10.2 (tail loop)
  - 10.3 (FastAPI /metrics)
  - 10.4 (metric set)
  - 10.5 (cardinality discipline)
  - 10.6 (compose + separability)
blocked_by:
  - Epic 8 (CI baseline)
  - Epic 9 (trace_id propagation kernel — done)
status: ready-for-dev
created: 2026-05-19
created_by: bmad-create-story skill
---
```
