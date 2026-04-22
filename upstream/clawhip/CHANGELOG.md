# Changelog

## 0.6.7 - 2026-04-12

### Highlights

- fix native hook repo/worktree metadata so worktree prompt-submitted events route with canonical main-repo names instead of branch/worktree leaf names
- reconcile prompt-submit marker handling between generated native hooks and `clawhip deliver`, storing prompt-submit state at the effective worktree root
- align Codex hook installation with the official OpenAI contract by supporting both `~/.codex/hooks.json` and `<repo>/.codex/hooks.json` while keeping the clawhip bridge in `~/.clawhip`
- keep Claude Code provider-native hook installation global-only, with updated operator docs and regression coverage
- add regression suites for worktree metadata emission, Codex project/global hook detection, prompt-deliver marker reconciliation, and install-scope rejection

### Upgrade notes

- crate version is now `0.6.7`
- rerun `clawhip hooks install --provider codex --scope global --force` (or `--scope project` per-repo) and `clawhip hooks install --provider claude-code --scope global --force` to refresh existing hook files
- existing route/config schema remains compatible; no migration required

## 0.6.6 - 2026-04-10

### Highlights

- remove the residual dispatch bypass-delivery timing flake with a deterministic test path so CI stays boring under load
- fix `clawhip tmux new` false-negative launch failures by handing monitoring back to the daemon after successful session creation
- add `clawhip release preflight` and gate the release workflow on version / Cargo.lock / changelog consistency
- add `clawhip explain` plus route/delivery provenance output for operator debugging

### Upgrade notes

- crate version is now `0.6.5`
- existing config remains compatible; no migration required

### Highlights

- add Discord channel binding verification so misbound repo→channel routes are caught before config writes (#198)
  - new `clawhip config verify-bindings [--json]` command audits every channel ID in the config against live Discord state and exits non-zero on drift
  - new `clawhip setup --bind REPO=CHANNEL_ID [--expect-name REPO=NAME]` resolves the channel via Discord, writes a route with a `channel_name` hint, and refuses stale/mismatched bindings before saving
  - new optional `channel_name` hint field on `[[routes]]`, `[defaults]`, `[[monitors.git.repos]]`, and `[[monitors.tmux.sessions]]` — advisory only, old configs continue to load unchanged
- keep release preflight guarding Cargo.toml / Cargo.lock / CHANGELOG consistency before publish (#189)
- wire the release workflow so inconsistent tags are rejected before `dist plan` and `publish-crates` run

### Upgrade notes

- crate version is now `0.6.6`
- existing config remains compatible; no migration required

### How to use

- drift audit: `clawhip config verify-bindings` (text) or `--json` for CI. Exit code is non-zero on any failed binding.
- bind a repo to a Discord channel safely: `clawhip setup --bind oh-my-codex=1480171106324189335 --expect-name oh-my-codex=omx-dev`. Clawhip resolves the channel live, prints `bind: oh-my-codex -> 1480171106324189335 (#omx-dev)`, and writes `[[routes]] filter = { repo = "oh-my-codex" }, channel = "…", channel_name = "omx-dev"`. Name mismatches and 404s abort before the write.
- run `clawhip release preflight` locally in the repo root before tagging — omit the version to default to the current `Cargo.toml` version, or pass an explicit tag (`clawhip release preflight v0.6.5`, `clawhip release preflight refs/tags/v0.6.5`)
- the same command runs in CI via the new `preflight` job gating the release workflow

## 0.6.4 - 2026-04-10

### Breaking

- replace provider-specific wrapper/launcher docs with the shared provider-native Codex + Claude hook surface
- document `clawhip native hook` as the generic ingress for shared hook payload verification
- move public guidance to provider-native installation, git-derived repo/worktree routing identity, and additive `.clawhip/hooks/` augmentation

### Highlights

- add `clawhip deliver` for prompt-submit-aware prompt recovery into existing hooked tmux-backed provider sessions
- validate repo-local hook setup and active Codex/Claude (including OMC/OMX wrapper) panes before retrying Enter
- record prompt-submit readiness in `.clawhip/state/prompt-submit.json` so delivery can stop once the hook actually fires

### Upgrade notes

- if you were using wrapper-specific launch flows, migrate to provider-owned hook registration plus `clawhip native hook` for local testing
- the shared v1 contract now documents only `SessionStart`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, and `Stop`

## 0.6.0 - 2026-04-09

### Highlights

- replace the old bespoke OMC/OMX wrapper glue with provider-native Codex + Claude hook registration built around `clawhip native hook`
- preserve prompt-submit and stop lifecycle events in the provider-native flow so real interactive sessions emit the same critical lifecycle signals clawhip expects
- fix Codex native-hook installation/detection to use `~/.codex/hooks.json` as the real hook registry surface instead of treating `.codex/config.toml` as installed-state evidence
- add tmux session/pane metadata to native hook payloads, including pane id, pane tty, attached state, and client count
- preserve the tmux metadata through normalization and rendering so local hook logs stay attributable to the originating tmux pane/session

### Documentation and tooling

- update public docs to describe the shared provider-native hook contract instead of the older wrapper/launcher-specific hook assets
- remove the checked-in OMC/OMX wrapper helper scripts and legacy hook assets that were superseded by provider-owned hook registration
- add provider-native docs regression coverage so the new installation guidance remains consistent

### Upgrade notes

- crate version is now `0.6.0`
- rerun `clawhip hooks install --all --scope global --force` to install the latest global Codex/Claude hook wiring into `~/.codex/hooks.json` and `~/.claude/settings.json`
- Codex hook installation now targets `~/.codex/hooks.json`; `.codex/config.toml` remains a general config surface and should not be treated as the hook registry
- if you were relying on the older wrapper-specific OMC/OMX launch helpers or checked-in wrapper hook assets, migrate to provider-owned hook registration plus `clawhip native hook` for local verification

## 0.5.4 - 2026-04-05

### Highlights

- native OMC/OMX lifecycle hooks with one-shot installer (`clawhip hooks install --omc|--omx|--all`)
- `clawhip omc "prompt"` and `clawhip omx launch "prompt"` for guaranteed prompt delivery with TUI detection
- session-init and session-stop hooks emit `session.started` / `session.finished` / `session.failed` to clawhip daemon
- cleaned up accidentally committed embedded worktree and local agent state from repo history

### Upgrade notes

- crate version is now `0.5.4`
- run `clawhip hooks install --omc` to deploy OMC lifecycle hooks to `~/.claude/hooks/`
- run `clawhip hooks install --omx` for OMX lifecycle hooks
- existing config remains compatible; no migration required

## 0.5.3 - 2026-04-04

### Highlights

- fix `clawhip send --channel` being overridden by route or default channel config
- for `custom` events, the explicit event channel now takes highest priority over route and default channels

### Upgrade notes

- crate version is now `0.5.3`
- existing config remains compatible; no migration required
- if you relied on a catch-all `event = "custom"` route to redirect all `clawhip send` traffic to a specific channel, that route channel will now only apply when `--channel` is not specified

## 0.5.2 - 2026-04-04

### Highlights

- reduced routine Discord burst noise with configurable batching for routine notifications
- allow `stale_minutes = 0` to disable tmux stale detection cleanly
- keep cron startup alive when persisted scheduler state is empty or invalid
- surface source failures as degraded alerts before the daemon appears healthy
- make matched route channels override source-provided channel hints consistently
- quiet invalid git monitor paths so they stop drowning out actionable failures

### Upgrade notes

- crate version is now `0.5.2`
- existing config remains compatible; no schema migration is required for this patch release
- `stale_minutes = 0` is now treated as an explicit disable for tmux stale alerts

## 0.3.0 - 2026-03-09

### Highlights

- introduced the typed internal event model used by the dispatcher pipeline
- generalized routing so one event can fan out to multiple deliveries
- extracted git, GitHub, and tmux monitoring into explicit event sources
- split rendering from transport and shipped the Discord sink on top of that boundary
- kept existing CLI and HTTP event ingress compatible while normalizing into the new architecture

### Upgrade notes

- crate version is now `0.3.0`
- `[providers.discord]` is the preferred config surface; legacy `[discord]` remains compatible
- routes may set `sink = "discord"`; omitting it still defaults to Discord in this release
