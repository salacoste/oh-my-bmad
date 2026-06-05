# Phase 3 Retrospective: Fleet Servers + Enforcement Gates

## 1. Phase 3 Overview

**Scope:** FR72–FR77 (digest deploy, mutation gate, five MCP fleet servers), NFR-O11 (mutation threshold), M8 (multi-agent orchestration surface), S12 (supply-chain confirmation across fleet).

**Timeline:** 2026-06-04 – 2026-06-05.

**Epics:** 6 (Epic 14 through Epic 19).

**Stories:** 33 total (5 + 7 + 6 + 5 + 5 + 5).

**PRs:** ~25 merged (#38–#59), all CI-green.

**Phase 3 shipped:**
- The digest-pinned deploy cutover (FR77, no tag-resolution path remains).
- A mutation-testing gate (cosmic-ray, gating at 82%, NFR-O11).
- A trace-id-required AST gate (NFR-O7, closing Phase-2 Story-9.7 deferral).
- Five MCP fleet servers, all born under ADR-0010 and both AST gates:
  - **git-mcp** (Epic 15, sigma) — sandboxed worktree operations, the reference implementation.
  - **github-mcp** (Epic 16, tau) — scoped-credential REST client, first recipe reuse.
  - **verification-mcp** (Epic 17, upsilon) — sandboxed build/test runner, cleanest reuse.
  - **memory-mcp** (Epic 18, phi) — SQLite-FTS5 persistent store, new archetype.
  - **artifact-mcp** (Epic 19, chi) — content-addressed binary store, final server.
- Three new ADRs: ADR-0010 (MCP-server recipe), ADR-0011 (artifact store), ADR-0012 (memory store).
- The G-SEC-2 MCP-subprocess half closed (broad `GITHUB_TOKEN` no longer reaches any MCP child).

---

## 2. What Went Well

### The entry-epic pattern: ship gates before tools
Epic 14 built only enforcement infrastructure — the mutation gate, the trace-id AST gate, the G-FN disposition triage — before any fleet tool existed. Every server in Epics 15–19 was born under green enforcement from line one. No retroactive gate-fitting was needed. This is the right phase-opening discipline: infrastructure, then surface.

### ADR-0010 recipe transfers across archetypes
The recipe (tiering, trace_id, allowlist, separability, supply-chain, ATDD-first) was discovered by building git-mcp (Epic 15) and then reused verbatim across four more servers spanning three archetypes: subprocess sandbox (git, verification), REST client (github), and own-store (memory, artifact). Each archetype brought a new *security model* (RCE, credential leak, store isolation) but the structural recipe held. ADR-0010 is the load-bearing document.

### Diff-audit + independent security review yield
Diff-audit discipline caught two P0 defects that gate-green first passes shipped:
- **git-mcp (15.3/15.4):** four P0 RCE/SSRF vectors (repo-local config, attacker-named drivers, URL rewrites) caught by the security-reviewer after the executor's passes were functionally correct.
- **github-mcp (16.3):** a credential-disclosure vector (scoped token returned in the tool result) caught by diff-audit.

Both were caught *before merge* because the review lane was independent, not self-approved.

### Prove-live → assert-shielded regression discipline
Every P0 security fix shipped with a regression test that first proves the vector executes live (unshielded), then asserts the shielded path blocks it. This prevents a future refactor from silently removing a shield. All five fleet servers inherited this discipline.

### ATDD-first workflow (trio-PR pattern)
The ATDD red-phase + scaffold + AST-gate trio PR (established in 15.1/15.2/15.2a) became the standard opening for every server. It keeps the red-phase honest (runtime failures, not collection errors) and means every tool had contracts before implementation.

### Store archetype established cleanly
memory-mcp (Epic 18) introduced the store archetype with three decisive patterns that artifact-mcp (Epic 19) reused verbatim: raw stdlib sqlite3 (zero deps, invisible to single-writer gate), 0o660/0o2775 file modes folded into `__init__` (never point-fixed), and metadata-only spine events (content never leaves the store).

---

## 3. What Could Be Improved

### Delegated passes are functionally correct but security-open
The pattern repeated twice: a delegated executor produces gate-green, functionally correct code that contains P0 security defects (git RCE/SSRF, github credential leak). The delegation model works for implementation throughput but the security axis requires an independent adversarial review. This is now process (always diff-audit delegated security work, never self-approve the P0 area), but it was discovered reactively, not proactively.

### Over-claiming on security closure
Epic 16 nearly committed "closes G-SEC-2" when only the MCP-subprocess half was closed. The independent reviewer caught the framing error — the claude-agent spawn path still forwards the broad PAT. Precision in security claims matters: a half-closed gap labeled "closed" stops anyone from finishing it.

### Purpose-auditing gap
The artifact-mcp binary-safety defect (19.3) was gate-green, security-clean, and style-correct — but the wire contract (UTF-8 in, base64 out) couldn't store binary data, defeating the server's reason to exist. Diff-audit was checking security and style, not "does this contract actually serve the stated purpose?" This is a gap in the review checklist.

### The tier-declaration AST gate has blind spots
`check_tier_declarations.py` scans only `handlers/tools.py` by path convention. A server registering tools in `server.py` would not be caught. In practice all Phase-3 servers used the standard layout, but the gate's discovery scope is fragile.

---

## 4. Key Lessons Learned

1. **Build the enforcement gate before the thing it enforces.** A gate that predates its subjects is enforced from line one; a gate added afterward leaves a backlog of violations. (Epic 14)

2. **The worktree is the untrusted sandbox.** Env-hermeticity is necessary but not sufficient — repo-local `.git/config` is attacker-writable and git reads it on every op. Two vector classes (fixed-key and attacker-named) need two defenses. (Epic 15)

3. **A quality gate is set at-or-below the current floor and ratcheted UP, never lowered silently.** Applies to mutation thresholds, cardinality baselines, and any numeric enforcement gate. (Epic 14)

4. **A tool result is a disclosure surface.** Never echo a credential through the tool boundary, even to satisfy a test contract. Record the outbound header instead. (Epic 16)

5. **Independent security review catches over-claims and framing errors, not just bugs.** The github-mcp code was correct; the *claim* about what it closed was wrong. A separate lane sees what the author is too close to. (Epic 16)

6. **Match review weight to credential/threat profile.** Credential-bearing changes (github) get a separate security-review lane. Non-secret changes (verification, memory, artifact) get a main-context diff-audit. Proportionate, not perfunctory. (Epic 17)

7. **For a content store, the wire contract must be binary-safe and symmetric from day one.** When a tool's purpose is "store arbitrary bytes," a text-only format is a functional defect, not a nicety. Diff-audit the purpose, not just security. (Epic 19)

8. **Content-addressing buys integrity for free.** SHA-256 addressing gives dedup, tamper-detection (re-hash on read), and crash-safety (write-temp-then-rename) with no extra journaling beyond WAL on the index. (Epic 19)

9. **Encode hard-won operational bugs as design preconditions in the gating ADR.** ADR-0012 §7 turned the Phase-2 umask crash-loop into a non-negotiable store-init rule, so memory-mcp never re-suffered it. (Epic 18)

10. **Raw stdlib sqlite3 sidesteps the single-writer gate and adds zero deps.** A store-owning server using SQLAlchemy would trip the AST gate. Raw `sqlite3` is invisible to the gate, needs no ORM, and gives direct FTS5 access. (Epic 18)

11. **A store's spine event carries metadata, never the stored content.** Events are queryable and bounded-cardinality. Content lives only in the store. (Epics 18, 19)

12. **Editable installs defeat copy-based mutation testing — mutate in place.** cosmic-ray's VCS-in-place mutation is the only approach that reaches editable installs in a uv workspace. Verify a non-zero smoke score before trusting any mutation harness. (Epic 14)

13. **System-initiated actions (retention sweep, GC) bypass Tier-3 but are bounded by operator policy.** The Tier-3 gate is for actor-initiated deletions. Policy-bounded system actions are a separate trust path. (Epic 19)

---

## 5. Deferred Items Carried Forward

### From Epic 14
- **AI-14.1:** Ratchet the mutation threshold above 82 as kernel suites kill more mutants.
- **AI-14.2:** Close G-FN-2 (re-enable spawner audit emission) — nested-context detection or lift-emission-to-spawner.
- **G-FN-3:** Bound liveness probes (`asyncio.wait_for` around `verify_connectivity`), AC-gated on Linux-nightly repro.

### From Epic 15
- **AI-15.2:** Broaden `check_tier_declarations.py` discovery beyond `handlers/tools.py` — revisit if a future server diverges from standard layout.
- **AI-15.3:** `run_git` output cap (bounded reader + kill on `communicate()`) before tools expose raw blob content.

### From Epic 16
- **AI-16.1 (HIGH):** Close the G-SEC-2 claude-agent half — migrate `claude_code_runner.py:89` from broad `GITHUB_TOKEN` to a scoped git credential helper. This is the only remaining G-SEC-2 work.
- **AI-16.2:** Flip `simulate=False` (config-gated) and validate a real GitHub write before declaring the write surface production-ready.
- **Per-server env map:** The scoped token reaches all MCP children via the shared allowlist — a per-server env map would scope it further. Acceptable per ADR-0010, defense-in-depth.

### From Epic 17
- **AI-17.1:** Apply the output-cap to `run_recipe`'s `communicate()` when the git-mcp P1 output-cap lands — same root pattern.

### From Epics 18, 19
- **Future memory `delete`/`forget` tool** would be Tier-3-gated per ADR-0012 §4 — out of FR75 scope.
- **Artifact retention sweep** at very large object counts is O(n) index scan; revisit if the store grows.

---

## 6. Phase 4 Readiness Assessment

### What Phase 3 Established

| Capability | Status | Notes |
|---|---|---|
| ADR-0010 recipe | Proven across 5 servers, 3 archetypes | Structural recipe is load-bearing |
| ATDD-first + trio-PR pattern | Standard for all servers | Red-phase honest from day one |
| AST enforcement gates | 2 gates (trace-id, tier-declarations) in CI | Born-under-enforcement |
| Mutation gate | Gating at 82%, nightly | Ratchet-ready |
| Digest-only deploy | Sole deploy path | No tag-resolution fallback |
| Diff-audit + security-review process | Institutionalized | Independent lane for credential-bearing changes |
| Store archetype template | Raw sqlite3 + umask-in-init + metadata-only events | Reused across memory + artifact |
| Fleet separability tests | 9 contract-test suites (S-1 through S-9) | All green, all in CI |
| Event spine | `git.*`, `github.*`, `verification.*`, `memory.*`, `artifact.*` | All registered, cardinality-green |
| Supply-chain confirmation | Zero new third-party deps across all 5 servers | Stdlib-only where possible |

### Gaps Remaining for Phase 4

1. **G-SEC-2 agent-half open** — the claude-agent spawn path still forwards the broad PAT. This is the highest-priority carry-forward.
2. **GitHub write surface is simulated** — `simulate=True` default means no live REST write has been validated. Flip + validate before production.
3. **`run_git` output cap** — unbounded `communicate()` buffers; pathological output could exhaust memory. Land before blob-exposing tools.
4. **Tier-declaration AST gate discovery scope** — scans only `handlers/tools.py`; brittle if a server diverges.
5. **G-FN-2 spawner audit emission disabled** — `OMB_MCP_AUDIT_EMISSION_ENABLED=0` is the workaround; the deep fix is deferred.
6. **G-FN-3 liveness-probe bound** — unbounded `verify_connectivity`; needs `asyncio.wait_for`.
7. **No fleet-level integration test** — each server is tested in isolation + separability, but no end-to-end "orchestrator calls three servers in a workflow" test exists.
8. **Cardinality ratchet not yet automated** — cardinality baselines exist but are not enforced as one-way ratchets in CI.

### Verdict

Phase 3 established the recipe, the enforcement infrastructure, and all five fleet servers under green gates. Phase 4 can build on a stable fleet foundation. The primary risks for Phase 4 are: (a) closing the G-SEC-2 agent-half before any production exposure, (b) validating the github write surface against a live endpoint, and (c) adding fleet-level integration coverage before orchestration workflows depend on multi-server interactions.

---

## Frontmatter
- **Phase:** 3 (Fleet Servers + Enforcement Gates)
- **Epics:** 14–19 (6 epics, 33 stories, ~25 PRs)
- **Timeline:** 2026-06-04 – 2026-06-05
- **Date:** 2026-06-05 (retro synthesis)
- **Author:** R2d2 + Claude
- **Defining outcome:** five fleet servers shipped on a proven recipe under green enforcement; the entry-epic pattern, diff-audit discipline, and store archetype template are the process assets Phase 4 inherits.
