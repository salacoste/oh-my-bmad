# Story 1.3: Upstream vendoring + migrator scaffold

Status: in-progress

## Story

As the **operator**,
I want **OMC and `clawhip` vendored under `upstream/` with a `just sync-upstream <name>` recipe, plus a pre-built migrator skeleton at `scripts/migrator/` that runs a synthetic v1.0.0 → v1.0.1 additive-upgrade path**,
so that **upstream-fork governance is explicit (SHA-pinned, in-tree, auditable) and the schema-migrator machinery exists before the first real event-log schema evolution — avoiding a retrofit-under-pressure scenario**.

## Acceptance Criteria

1. **AC-1: `upstream/` placeholder content for OMC and `clawhip`.** `upstream/omc/` and `upstream/clawhip/` each contain at minimum a `README.md` marking the slot (current status: placeholder; when real source lands, sync via `just sync-upstream`). `upstream/` no longer consists only of `.gitkeep` — two subdirectories replace it.

2. **AC-2: `VENDORED.md` manifest at repo root.** Records each upstream fork with:
   - Source URL (`https://github.com/Yeachan-Heo/oh-my-claudecode`, `https://github.com/Yeachan-Heo/clawhip`).
   - Current pinned commit SHA (may be `PLACEHOLDER` until first real sync).
   - Sync date (ISO 8601 UTC).
   - Notes on when real content lands (OMC → Story 5.10 orchestrator-adapter; clawhip → Story 2.8 clawhip-bridge).

3. **AC-3: `just sync-upstream <name>` recipe.** Takes a single argument (`omc` or `clawhip`). The recipe:
   - Validates the argument is one of the two supported names (clear error otherwise).
   - Fetches the upstream repo via `git clone` (to a temp path), or updates if already present.
   - Copies the fetched content into `upstream/<name>/` (excluding `.git/`).
   - Reads the fetched `HEAD` SHA and writes it into `VENDORED.md` alongside a fresh sync date.
   - If the upstream URL is unreachable (e.g., repo doesn't exist), the recipe exits non-zero with a clear error — not a silent no-op.
   - Graceful mode: if `upstream/<name>/` already has content and the fetch fails, prints a warning but leaves existing content intact (protects the operator from network hiccups).

4. **AC-4: `scripts/migrator/Dockerfile`.** Multi-stage Python 3.12-slim-bookworm build. Installs `uv` and the migrator package. Entrypoint is `python -m migrator <from>-to-<to>` (e.g., `v1.0.0-to-v1.0.1`).

5. **AC-5: `scripts/migrator/src/migrator/__main__.py`.** Implements a trivial v1.0.0 → v1.0.1 additive upgrade: reads a JSONL event log, for each event emits an equivalent v1.0.1-shaped event (adds a new optional field e.g. `extensions: {}`), writes the migrated output to a new file, and archives the original with suffix `.v1.0.0.archive`. On unknown `(from,to)` argument exits non-zero with a clear error.

6. **AC-6: Synthetic v1.0.0 → v1.0.1 test fixture.** `scripts/migrator/tests/fixtures/` contains `sample_v1.0.0.jsonl` with ≥3 minimal events (correct envelope shape per Architecture §Implementation Patterns). A `just migrator-test-additive` recipe runs the migrator against the fixture inside a throwaway Docker container and asserts the output file exists, is valid JSONL, and every event has the new additive field.

7. **AC-7: `scripts/migrator/pyproject.toml`.** Declares a `migrator` Python project (not a workspace member — it's an operator script, not a service). `name = "migrator"`, `requires-python = ">=3.12"`, `dependencies = []`, same `uv_build>=0.11.0,<1.0` bound as other project files for consistency. Lives outside the `uv.workspace` members pattern (so the root `uv sync` doesn't pull it in).

8. **AC-8: `just bootstrap-verify` regression.** Still passes unchanged (Story 1.2's 13 workspace-member imports remain green).

9. **AC-9: `just sync-upstream omc` + `just sync-upstream clawhip` dry-run successful.** Running both recipes against the real upstream URLs should succeed (pulling real content) *if* those repos exist. **If either upstream URL returns 404**, the recipe must exit non-zero with a clear error — not a silent no-op. Document in Completion Notes which of the two URLs actually resolved during story implementation; if neither, the scaffold still lands and the operator can retry later.

10. **AC-10: Atomic code commit.** All of `upstream/omc/`, `upstream/clawhip/`, `VENDORED.md`, `just sync-upstream` + `just migrator-test-additive` additions, `scripts/migrator/` tree, and fixtures land in one commit titled `chore(scaffold): story 1.3 — upstream vendoring + migrator scaffold · FR22 FR50 NFR-M3`. Docs-only follow-up commits permitted per Story 1.2's AC-11 precedent.

## Tasks / Subtasks

- [ ] **Task 1: Set up `upstream/` placeholder directories** (AC: #1)
  - [ ] Remove `upstream/.gitkeep` (no longer needed — subdirs replace it).
  - [ ] Create `upstream/omc/README.md` and `upstream/clawhip/README.md`, each marking the slot and pointing at `just sync-upstream <name>` for populating real content.
- [ ] **Task 2: Write `VENDORED.md` manifest** (AC: #2)
  - [ ] Two entries: OMC + clawhip.
  - [ ] SHA field set to `PLACEHOLDER` initially; sync date to ISO 8601 UTC of the story commit.
  - [ ] Include a "Next sync" column pointing at the story that first needs real upstream content.
- [ ] **Task 3: Implement `just sync-upstream <name>` recipe** (AC: #3, #9)
  - [ ] Recipe signature: `sync-upstream name:` (just's positional arg).
  - [ ] Validate `name` ∈ {`omc`, `clawhip`}; error otherwise.
  - [ ] Read URL from a lookup dict (hard-coded in the recipe; two entries).
  - [ ] `git clone --depth 1` into a temp dir, capture `HEAD` SHA, rsync (excluding `.git/`) into `upstream/<name>/`.
  - [ ] Update `VENDORED.md` in-place: replace the entry's SHA + sync date.
  - [ ] Graceful fallback on network failure (preserve existing content, print warning).
- [ ] **Task 4: Scaffold `scripts/migrator/` tree** (AC: #4, #5, #7)
  - [ ] `scripts/migrator/pyproject.toml` (name="migrator", python>=3.12, uv_build>=0.11.0,<1.0, no deps).
  - [ ] `scripts/migrator/src/migrator/__init__.py` with docstring + `__version__ = "0.1.0"`.
  - [ ] `scripts/migrator/src/migrator/__main__.py` — the actual migrator logic:
    - Parse CLI arg `<from>-to-<to>` (e.g., `v1.0.0-to-v1.0.1`).
    - Read `EVENT_LOG_PATH` env var (default `/var/lib/oh-my-bmad/registry/events/current.jsonl`) — the path to the JSONL event log to migrate.
    - For `v1.0.0-to-v1.0.1`: additive upgrade — for each event in the log, copy through + add a new optional field (e.g., `extensions: {}`). Emit migrated events to `<path>.v1.0.1.jsonl`, then move original to `<path>.v1.0.0.archive`.
    - Unknown `<from>-to-<to>`: exit non-zero with a clear error listing supported pairs.
  - [ ] `scripts/migrator/Dockerfile`: Python 3.12-slim-bookworm base, install uv, copy the migrator package, set entrypoint `python -m migrator`.
- [ ] **Task 5: Synthetic test fixture + `just migrator-test-additive` recipe** (AC: #6)
  - [ ] Create `scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl` with ≥3 minimal events (JSONL lines each a valid event envelope — simplified shape OK for Story 1.3; full envelope model arrives in Story 2.1).
  - [ ] Create `just migrator-test-additive` recipe that:
    - Builds the migrator Docker image (`docker build -t oh-my-bmad-migrator:test scripts/migrator/`).
    - Copies the fixture to a temp dir, runs the migrator against it (`docker run --rm -v $(pwd)/tmp:/data -e EVENT_LOG_PATH=/data/sample.jsonl oh-my-bmad-migrator:test v1.0.0-to-v1.0.1`).
    - Asserts the output file exists, every line is valid JSON, every event has the new `extensions` field.
    - Tears down the temp dir.
  - [ ] Run `just migrator-test-additive` and confirm it exits 0.
- [ ] **Task 6: Attempt real upstream sync** (AC: #9)
  - [ ] Run `just sync-upstream omc` once and capture output. If successful, the placeholder README is replaced with real source.
  - [ ] Run `just sync-upstream clawhip` once and capture output. Same handling.
  - [ ] Document in Completion Notes: which URLs resolved, which SHAs landed in `VENDORED.md`, whether placeholder content was retained due to fetch failure.
- [ ] **Task 7: Regression check** (AC: #8)
  - [ ] Run `just bootstrap-verify`; confirm 13 workspace-member imports still green and no new warnings.
- [ ] **Task 8: Commit atomically** (AC: #10)
  - [ ] Single commit per AC-10 title.

## Dev Notes

### Architecture patterns for this story

- **Vendored-with-sync (not submodules).** Architecture §Starter Template Evaluation / "Upstream Fork Integration" explicitly chose this pattern — submodules add clone/rebuild/CI friction not worth it for a solo operator; vendoring keeps the tree self-contained; re-syncs are explicit + auditable via `VENDORED.md`.
- **Migrator as a one-shot container.** Architecture §Core Architectural Decisions / Event-schema migrations: `docker compose run --rm migrator <from>-to-<to>`. The container is stateless, runs on demand, then exits.
- **Additive-only within major schema version.** NFR-M3: event-schema evolution is additive-only within a major version; breaking changes require a migrator. The v1.0.0 → v1.0.1 upgrade this story ships is intentionally trivial (new optional field) to exercise the mechanism without introducing real schema risk.

### What this story does NOT do

- Real OMC / clawhip content — only placeholder directories + sync mechanism. Real content pulls when the upstreams are first needed (Story 5.10 for OMC, Story 2.8 for clawhip). If the upstream URLs fail to resolve during this story, placeholders remain; scaffold is still usable.
- Full event-envelope model — Story 2.1 lands the Pydantic v2 `EventEnvelope` + schema registry + canonical serializer. The synthetic fixture for this story uses a simplified JSONL shape that's schema-compatible with v1.0.0 but does not require the full model to exist yet.
- Docker Compose wiring for the migrator. Story 1.4 owns `docker-compose.yml` + `docker-compose.macos.yml` and will wire the migrator as a compose-invokable service (`docker compose run --rm migrator …`). Story 1.3 ships only the Dockerfile + standalone `docker run` invocation.
- `pytest` — the migrator test is a bash assertion inside the `just migrator-test-additive` recipe. Story 1.5 lands pytest; the migrator test can be promoted to a real pytest case at that point.
- CI integration for `just migrator-test-additive`. Story 1.5 wires CI; this story ships only the recipe.

### Source tree components to touch

```
oh-my-bmad/
├── VENDORED.md                                 # Task 2 NEW
├── justfile                                    # Task 3 + Task 5 — append recipes
├── upstream/
│   ├── omc/
│   │   └── README.md                           # Task 1 NEW (placeholder)
│   └── clawhip/
│       └── README.md                           # Task 1 NEW (placeholder)
└── scripts/
    └── migrator/                               # Task 4 NEW
        ├── Dockerfile
        ├── pyproject.toml
        ├── src/
        │   └── migrator/
        │       ├── __init__.py
        │       └── __main__.py
        └── tests/
            └── fixtures/
                └── sample_v1.0.0.jsonl         # Task 5 NEW
```

**Files: 9 new + 2 modified (justfile, VENDORED.md grows); `upstream/.gitkeep` removed.**

### Dockerfile sketch for the migrator

```dockerfile
FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN uv sync --no-dev --frozen || uv sync --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "migrator"]
```

### Synthetic v1.0.0 fixture sketch

`scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl`:

```jsonl
{"event_id":"e-0190abc1-2000-7000-8000-000000000001","schema_version":"1.0.0","type":"task.created","emitted_at":"2026-04-22T00:00:00Z","actor":{"kind":"operator","id":"r2d2"},"payload":{"task_id":"t-0190abc1-2000-7000-8000-000000000002"}}
{"event_id":"e-0190abc1-2000-7000-8000-000000000003","schema_version":"1.0.0","type":"task.planning.started","emitted_at":"2026-04-22T00:00:01Z","actor":{"kind":"orchestrator","id":"omc"},"payload":{"task_id":"t-0190abc1-2000-7000-8000-000000000002"}}
{"event_id":"e-0190abc1-2000-7000-8000-000000000004","schema_version":"1.0.0","type":"task.completed","emitted_at":"2026-04-22T00:00:02Z","actor":{"kind":"orchestrator","id":"omc"},"payload":{"task_id":"t-0190abc1-2000-7000-8000-000000000002","files_changed":0}}
```

Migrator adds `"extensions": {}` to each event and emits v1.0.1 output.

### Testing standards for this story

No pytest yet. Verification:

1. `just sync-upstream omc` + `just sync-upstream clawhip` — success or clear error (not silent no-op).
2. `just migrator-test-additive` — Docker build succeeds, migrator runs against fixture, output file valid JSONL with new additive field.
3. `just bootstrap-verify` — regression: still passes unchanged.
4. Every event in the migrator output has `"schema_version": "1.0.1"` + `"extensions": {}`.

### Project Structure Notes

#### Alignment with unified project structure

- `upstream/<name>/` subdirs replace the single `.gitkeep` from Story 1.1. Follows Architecture §Project Structure repo-layout.
- `scripts/migrator/` lives under `scripts/` (operator utilities), not `services/` or `packages/` — matches Architecture's split where `scripts/` holds one-shot container entrypoints (migrator, bootstrap scripts).
- `VENDORED.md` is a new top-level doc file; sibling to `README.md`, `LICENSE`.

#### Detected variances

- **AC-9 "real upstream sync"** is best-effort — the real upstream URLs may not exist yet. Placeholder fallback is explicit in the ACs.
- **AC's `docker compose run ...` phrasing** is deferred to Story 1.4's compose wiring. Story 1.3 delivers the underlying capability via `docker run`.

### References

- `epics.md` §Epic 1 / Story 1.3 — source of the 4 paragraphs describing the deliverable.
- `architecture.md` §Starter Template Evaluation / "Upstream Fork Integration — Vendored-with-Sync" — rationale for vendoring vs submodules; `just sync-upstream` recipe pattern.
- `architecture.md` §Core Architectural Decisions / Data Architecture / Event-schema migrations — migrator container approach + additive-only-within-major rule.
- `prd.md` FR22 — "Platform can execute a migrator tool that reads an old-version event log and emits equivalent new-version events."
- `prd.md` FR50 — "Operator can run a schema migrator as a one-shot container command."
- `prd.md` NFR-M3 — Event schema evolution: within a major schema version, additive changes only; breaking changes require migrator.
- `1-1-monorepo-proof.md` / `1-2-remaining-service-and-mcp-scaffolds.md` — carry-forward patterns (pyproject.toml shape, docstring convention, `uv_build` bound post-review-fix).

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — this story has meaningful substance (migrator logic, recipe with arg validation, Docker multi-stage build) but nothing requiring Opus-level reasoning.

### Debug Log References

_Placeholder._

### Completion Notes List

_To be filled by the dev agent. Record per AC: pass/fail + evidence._

- AC-1 — upstream/omc/ + upstream/clawhip/ placeholder READMEs present.
- AC-2 — VENDORED.md content.
- AC-3 — `just sync-upstream <name>` recipe works; validates arg; graceful fallback verified.
- AC-4 — Dockerfile builds successfully (exit code).
- AC-5 — migrator __main__.py runs v1.0.0-to-v1.0.1 against fixture (exit 0 + output file check).
- AC-6 — `just migrator-test-additive` exits 0; fixture contains ≥3 events; output has additive field.
- AC-7 — scripts/migrator/pyproject.toml shape correct.
- AC-8 — `just bootstrap-verify` still green.
- AC-9 — real URL sync attempt outcome (which upstreams resolved, which SHAs landed).
- AC-10 — single atomic commit SHA.

Also record any deviations encountered (e.g., upstream URLs that don't exist, Docker-build quirks, fixture format decisions).

### File List

_To be filled by the dev agent. Expected: 9 new + 2 modified + 1 deleted (`upstream/.gitkeep`)._

### Change Log

_To be appended by the dev agent on completion._
