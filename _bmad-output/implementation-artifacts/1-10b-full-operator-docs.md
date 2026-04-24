# Story 1.10b: Full operator documentation set (MVP-ship-blocker, post-Bootstrap)

Status: review

## Story

As the **operator (and any future collaborator)**,
I want **`docs/operator-runbook.md` + `docs/schema-evolution.md` + `docs/exceptions.md` + `docs/testing-guide.md` + `docs/backup-restore.md` + `docs/message-design.md`**,
so that **a full runnable, recoverable, maintainable, debuggable documentation set exists before MVP ship, closing Epic 1's NFR-M7 coverage**.

## Acceptance Criteria

1. **AC-1: `docs/operator-runbook.md`** — paging conditions + recovery procedures. Sections:
   - **Running-state check** — quick `docker compose ps` + `just lint` sanity probes.
   - **Service down** — 6 per-service recovery playbooks (registry-api, registry-state, telegram-gateway, orchestrator-adapter, worker-wrapper, clawhip-daemon). For each: typical cause, log-grep pattern, restart command, verification. Phase-1-honest: most services are hello-world until real logic lands.
   - **SQLite WAL recovery** — if `registry-state` crashed mid-write, how to check the WAL + trigger a clean replay on restart. References Story 2.6 for real recovery logic.
   - **Tunnel failure** — Cloudflared / ngrok / BYO troubleshooting common failure modes.
   - **Worktree lock stuck** — arrives in Story 5.3; reference only.
   - **Budget exceeded** — arrives in Story 5.15 + Story 6.11; reference only.
   - **License scan flagged** — arrives in Story 6.9; reference only.
   - Target: ~200–250 lines.

2. **AC-2: `docs/schema-evolution.md`** — how to add an event type + how to ship a migrator. Sections:
   - **Additive-only within major schema** — NFR-M3 rule stated + rationale.
   - **Add a new event type** — step-by-step: update `packages/events/src/events/schema_registry.py` REGISTRY frozenset, define Pydantic payload model, register in the schema-registry map, add emission site. Cross-reference Story 1.6's `check_event_registry.py` gate.
   - **Ship a breaking-change migrator** — new `scripts/migrator/` directory per target version pair; add `v<from>-to-<to>` key to `MIGRATIONS` dict in `__main__.py`; write migration function. Reference Story 1.3's `migrator-test-additive` as the template.
   - **Run the migrator** — `docker compose run --rm migrator v1.0.0-to-v1.1.0` (current v1.0.0→v1.0.1 is the shipped example).
   - **Roll-back procedure** — restore pre-migration archive (`.v<from>.archive` file) from the `migrator` atomic-write-rename pattern.
   - Target: ~180–220 lines.

3. **AC-3: `docs/exceptions.md`** — documented naming-rule + convention exceptions. Sections:
   - **MCP-server triple-naming** — directory `task-registry/` + project `task-registry-mcp` + module `task_registry_mcp` — 1:3:3 mapping (Story 1.2). Rationale: `uv_build` kebab-to-snake derivation + unambiguous contract-naming.
   - **`SCAFFOLD VERSION` tags** — the `# SCAFFOLD VERSION — Story X replaces...` header pattern introduced in Story 1.4 and retained through Story 1.8's Dockerfile refactor. When to remove.
   - **Scaffold `__main__.py` files** — Story 1.4's hello-world pattern retained across every service until real logic lands. Listed with owning-replacement-story.
   - **`# noqa: IMP001 | EVT001 | SW001` suppression tags** — Story 1.6 per-script conventions. When each applies.
   - **`.secret-hygiene-ignore` vs pre-commit `exclude:`** — Story 1.7 dual-mechanism rationale.
   - **worker-wrapper 283 MB over AC-7 200 MB budget** — Story 1.8 documented deviation. Architecture constraint, not a bug.
   - **`OMB_IMAGE_REGISTRY=ghcr.io/r2d2`** default — Story 1.4 seeded; Story 1.9 fork-note added. Don't forget to change on fork.
   - Target: ~120–180 lines.

4. **AC-4: `docs/testing-guide.md`** — how to use the test harness + how to add tests. Sections:
   - **Running tests** — `just test` (PR-gate), `just test-slow`, `just test-contract`; pytest marker taxonomy from Story 1.5.
   - **Test-tree layout** — top-level `tests/{separability,crash-injection,idempotency,integration,contract,migrator}/` + co-located `test_*.py` (Architecture line 344).
   - **Writing a unit test** — example pattern with `pytest.fixture` + `pytest.mark.<marker>`.
   - **Writing an integration test** — cross-service test that uses the Story-2.1 EventEnvelope fixture (reference only — lands then).
   - **Recording a contract-fixture** — the workflow for capturing upstream-fork behavior (stdin/stdout of `omc` or `clawhip`) into `tests/contract/fixtures/<adapter>/` directory. Every upstream sync (`just sync-upstream`) must re-run contract tests before merging. References Story 2.8 + Story 5.10 adapter stories.
   - **CI gates** — `scripts/check_imports.py` + `check_event_registry.py` + `check_single_writer.py` + `scan-secrets` run in `just lint` and CI. How to read their output + how to suppress with noqa.
   - **Fixtures** — `tests/conftest.py`'s `fixed_clock` + `seeded_uuid7` stubs (real bodies arrive Stories 2.1 / 2.2).
   - Target: ~200–250 lines.

5. **AC-5: `docs/backup-restore.md`** — expand the README summary into a full step-by-step. Sections:
   - **What's backed up** — `oh-my-bmad-data` named volume contents: registry SQLite DB, event log JSONL files, artifact blobs (Story 2.6 adds artifacts).
   - **`just backup`** — detailed explanation of the volume-mount pattern + suffix validation + atomic stack-down/tar/stack-up cycle + EXIT trap for auto-restart on failure.
   - **Off-host rsync** — recommended pattern for copying the `.tgz` to a different host / S3 / Backblaze. `rsync -avz oh-my-bmad-backup-*.tgz user@backup-host:~/backups/` example.
   - **Restore to a fresh host** — `docker volume create oh-my-bmad_oh-my-bmad-data` + `docker run --rm -v ... alpine:3 tar -xzf` extraction pattern + verification via `just dev` and registry health.
   - **Backup cadence** — weekly snapshot + monthly event-log rotation per NFR-SC2.
   - **Testing your backup** — quarterly restore drill is the canonical operations pattern; document how to exercise it.
   - Target: ~150–200 lines.

6. **AC-6: `docs/message-design.md`** — Telegram message template catalog. Sections:
   - **Template discipline** — Every bot message follows: (a) ≤ 4096 char limit (Telegram max); (b) Markdown-v2 escape conventions; (c) structured field order (task_id + summary + actions); (d) emoji-minimalism (one prefix emoji per message category).
   - **Template catalog** (Phase 1 scope — all Phase-3 stories reference this doc):
     - `/ping pong` reply — Story 3.5.
     - Approval request — Story 3.10.
     - Blocker notification — Story 3.11.
     - Completion summary — Story 3.12.
     - Self-recovered summary — Story 3.13.
     - `/status` reconstituted state — Story 3.14 + Story 7.2.
     - `/logs` LLM digest — Story 3.15 + Story 7.3.
   - For each template: example rendering, 4096-char budget allocation, field list (which platform events supply which field), rationale.
   - Phase-1 reality: templates themselves land in Epic 3; this doc is the **catalog-before-content** reference. Each section's rendering is a **mock** example with the exact shape the owning story will produce.
   - **Emoji discipline** — ✅ success, ⚠️ warning, 🛑 blocker, 🔒 approval-required, 🔄 recovery, 📝 plan, 🎯 task — mapping fixed so operator's Telegram thread is visually grep-able.
   - Target: ~200–250 lines.

7. **AC-7: README cross-linking.** Each of the 6 new docs is referenced from the root `README.md`. Add a "Full operator documentation" section after the Quickstart (or within the existing `docs/` table row) with 6 relative links:
   ```markdown
   - [Operator runbook](docs/operator-runbook.md) — paging + recovery procedures.
   - [Schema evolution](docs/schema-evolution.md) — add an event type / ship a migrator.
   - [Exceptions](docs/exceptions.md) — documented naming-rule + convention exceptions.
   - [Testing guide](docs/testing-guide.md) — harness usage + how to add tests.
   - [Backup / restore](docs/backup-restore.md) — volume snapshot + off-host copy + fresh-host restore.
   - [Message design](docs/message-design.md) — Telegram template catalog.
   ```
   Delete the stale `docs/` table row reference that says "land in Story 1.10b" once the files are present.

8. **AC-8: Placeholder-reference discipline** (matches Story 1.10a's AC-7). Every future-story feature is cited by story number ("arrives in Story X.Y"), never as if live. NFR-M3 etc. architectural references cite the doc + line where first stated.

9. **AC-9: Cross-doc consistency.** If `operator-runbook.md` says "restart registry-state" and `backup-restore.md` says "stop + restart", the two must not conflict. Proofread pass verifies consistency (no conflicting command flags, no divergent service names).

10. **AC-10: Scan-secrets clean.** `uv run secret-hygiene-precommit docs/` → exit 0 on all new files. Same angle-bracket placeholder pattern as Story 1.10a.

11. **AC-11: Length discipline.** Each doc between 120 and 250 lines. Six docs × ~200 LOC average = ~1200 LOC added. Over-cap docs ship a "trim" pass before finalize.

12. **AC-12: Regression suite.** `just bootstrap-verify` + `just test` + `just lint` + `just migrator-test-additive` + `just check-gates-self-test` all exit 0.

13. **AC-13: Atomic commit.** One commit titled `docs(story-1-10b): full operator documentation set · NFR-M7`. Docs-only follow-ups permitted.

## Tasks / Subtasks

- [x] **Task 1: `docs/operator-runbook.md`** (AC: #1)
- [x] **Task 2: `docs/schema-evolution.md`** (AC: #2)
- [x] **Task 3: `docs/exceptions.md`** (AC: #3)
- [x] **Task 4: `docs/testing-guide.md`** (AC: #4)
- [x] **Task 5: `docs/backup-restore.md`** (AC: #5)
- [x] **Task 6: `docs/message-design.md`** (AC: #6)
- [x] **Task 7: README update — link to the 6 new docs** (AC: #7)
- [x] **Task 8: Scan-secrets + regression check** (AC: #10, #12)
- [x] **Task 9: Atomic commit** (AC: #13)

## Dev Notes

### Architecture patterns for this story

- **NFR-M7 full-set** — this closes the "runnable / recoverable / maintainable / debuggable" documentation promise the PRD makes. Story 1.10a delivered the Bootstrap subset (deployment + quickstart); 1.10b delivers everything else.
- **Placeholder-before-content discipline** — many topics (message templates, breakpoint budgets, restore drills) assume Phase-1+ features that don't exist yet. Each doc is honest about what's live vs. what's spec'd via `arrives in Story X.Y` references.
- **Cross-link density** — every doc references at least 2 other docs by relative path. The 6 docs form an interconnected reference web, not 6 isolated silos.

### What this story does NOT do

- `docs/api.md` (HTTP API reference) — auto-generated from FastAPI OpenAPI in Story 2.9; not a Phase-1.10 concern.
- `docs/architecture.md` — Architecture lives in `_bmad-output/planning-artifacts/architecture.md` as the source of truth. A symlink at `docs/architecture.md` pointing there is Architecture line 567's plan; can be added here OR deferred.
- Real Telegram message templates — Epic 3 stories own the code; 1.10b ships the catalog + Phase-1 mock renderings only.
- Real test fixtures for contract testing — Stories 2.8 + 5.10 land the upstream-fork contract tests; 1.10b documents the pattern only.

### Source tree components to touch

```
oh-my-bmad/
├── docs/
│   ├── operator-runbook.md                   # Task 1 NEW
│   ├── schema-evolution.md                   # Task 2 NEW
│   ├── exceptions.md                         # Task 3 NEW
│   ├── testing-guide.md                      # Task 4 NEW
│   ├── backup-restore.md                     # Task 5 NEW
│   └── message-design.md                     # Task 6 NEW
└── README.md                                 # Task 7 MODIFIED (+ docs cross-links)
```

**Files: 6 new + 1 modified. Docs-only.**

### Content sketches

Each doc follows this shape:
- Top-of-file: short one-line purpose statement.
- Table of contents (auto or hand-rolled).
- Sections per AC-1 through AC-6.
- Bottom: "See also" block with 3 relative links to related docs.

### Story-reference map (every doc should cite by story number)

| Feature | Story |
|---|---|
| Compose + env + justfile | 1.4 |
| Schema-registry stub | 1.6 |
| Scanner + sanitizer | 1.7 |
| Dockerfile.base + multi-stage | 1.8 |
| GHCR release workflow | 1.9 |
| Full EventEnvelope + clock + UUIDv7 | 2.1 / 2.2 |
| Registry SQLite schema | 2.3 |
| Event-log writer + atomic append | 2.4 |
| Registry materializer | 2.5 |
| Snapshot capture + replay | 2.6 |
| Idempotency cache | 2.7 |
| `clawhip-bridge` MCP | 2.8 |
| Registry HTTP API `/v1/health` | 2.9 |
| Telegram `/ping` | 3.5 |
| Telegram approval template | 3.10 |
| Blocker template | 3.11 |
| Completion summary template | 3.12 |
| Self-recovered summary | 3.13 |
| `/status` reconstituted | 3.14 |
| `/logs` digest | 3.15 |
| Worker lifecycle + atomic edit | 5.1 / 5.6 |
| OMC supervision | 5.10 |
| Task plan emission | 5.11 |
| Task execution driver | 5.12 |
| PR draft auto-creation | 5.14 |
| Budget enforcement | 5.15 / 6.11 |
| Tier enforcement | 6.1 / 6.2 / 6.3 |
| License scan | 6.9 / 6.10 |
| Reconstituted state handler | 7.1 |
| `/status` business logic | 7.2 |
| `/logs` business logic | 7.3 / 7.4 |
| Retry hint injection | 7.6 |

Use the table sparingly — only cite stories actually relevant to each doc.

### Previous Story Intelligence (Stories 1.1–1.10a)

- **Story 1.10a** already established doc-style patterns: version-matrix tables, angle-bracket placeholders for scan-secrets safety, honest "arrives in Story X" references. 1.10b maintains the same style.
- **Story 1.9** added README Upgrading section — `backup-restore.md` can reference it rather than duplicate.
- **Story 1.7** secret-scanner `.secret-hygiene-ignore` auto-loads from repo root + the `exclude:` regex from `.pre-commit-config.yaml` — the testing guide's secret-scanner section cites both.
- **Story 1.6** noqa-suppression tags (IMP001 / EVT001 / SW001) are docs/exceptions.md subject matter.
- **Story 1.4** `.env.example` placeholder conventions established — `message-design.md` + `operator-runbook.md` cite specific env vars.

### Git Intelligence

- `c82950b docs(story-1-10a): finalize + mark done`
- `9b2f984 docs(story-1-10a): apply code-review fixes · all severities`
- `1354b97 docs(story-1-10a): finalize story file + mark review`
- `e424932 docs(story-1-10a): deployment quickstart — vps.md + macos.md + README rewrite · NFR-M7`

Cadence stays 4-commit for code-heavy stories, 2-3 for docs-only.

### Latest Tech Information

- Markdown: GitHub-flavored; use fenced code blocks with language hints.
- Relative links: `[doc](./other-doc.md)` from within `docs/`; `[doc](docs/foo.md)` from root `README.md`.
- No external hosted diagrams — use ASCII art for simple flows.

### References

- `epics.md` §Epic 1 / Story 1.10b (lines 643–664) — AC source.
- `prd.md` NFR-M7 (line 947) — documentation completeness requirement.
- `architecture.md` lines 559–567 (`docs/` structure + purpose).
- All prior Story-1.x artifacts referenced in the doc-content cross-links.
- `1-10a-deployment-quickstart-docs.md` — the sibling docs story whose style patterns this story inherits.

## Dev Agent Record

### Agent Model Used

_To be filled by the dev agent._ Recommendation: **Claude Sonnet 4.6** — docs-authoring at scale (6 files, ~1200 total LOC). No Opus reasoning required.

### Debug Log References

_Placeholder._

### Completion Notes List

_To be filled by the dev agent. Record per AC._

### File List

_To be filled. Expected: 6 new + 1 modified._

### Change Log

_To be filled._
