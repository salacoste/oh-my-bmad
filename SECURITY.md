# Security policy

## Reporting a vulnerability

If you find a security issue in **oh-my-bmad**, please **do not open a public GitHub issue.**

Use one of:

- **GitHub private security advisory** — [open one here](https://github.com/salacoste/oh-my-bmad/security/advisories/new) (preferred).
- Email the maintainer via the GitHub profile contact.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce (proof-of-concept code or screenshots if applicable).
- The version / commit SHA you tested against.
- Your assessment of severity (CVSS optional).

You'll get an acknowledgement within a few days, and a reasonable effort to triage and fix. No bug bounty.

## Scope

oh-my-bmad is a **self-hosted personal platform**. The threat model assumes:

- The operator runs the stack on infrastructure they control (VPS or local macOS host).
- Telegram bot tokens, Anthropic API keys, and GitHub PATs are operator-provisioned via `.env`.
- The Telegram `AllowlistMiddleware` ([ADR-0001](./docs/adr/0001-allowlist-middleware-auth.md)) gates which Telegram users may invoke the bot at all.
- Capability tiers ([deep-dive](./docs/explanations/capability-tiers.md)) gate which actions any caller may perform inside the platform.

**In scope:**

- Auth-bypass paths (anything that defeats `AllowlistMiddleware` or `capabilities.check_tier`).
- Secret leakage via logs, traces, error messages, or generated artifacts (NFR-S1 / FR43).
- Event-log corruption or replay attacks against the single-writer invariant (FR26).
- Capability-tier escalation (Tier-2 → Tier-3 without an `approval.granted` event).
- Schema-migration paths that violate additive-only semantics.
- Supply-chain issues in vendored upstream forks under `upstream/` accessed outside the adapter shims.

**Out of scope (for this Phase 1 baseline):**

- Vulnerabilities requiring host-level root access on the deployment target.
- Vulnerabilities in upstream Claude Code, aiogram, FastAPI, SQLAlchemy, or other pinned dependencies that have a public CVE and an upstream fix.
- DoS attacks against the public Telegram webhook ingress that don't bypass the allowlist (treat as operator-tunable; deploy behind a tunnel / WAF as appropriate).
- Issues that require local console access — that surface is already trusted by design.

## Security controls

The platform's load-bearing security controls are documented:

- **Three-layer secret hygiene** — pre-commit scanner + structlog sanitizer (wired *before* the renderer) + `secret.accessed` audit events. See `packages/secret-hygiene/` and [`_bmad-output/project-context.md`](./_bmad-output/project-context.md) Cat 2 / Cat 5.
- **Capability tiers** — uniform `check_tier` / `check_tier_with_approval` calls at every MCP tool boundary. See [`docs/explanations/capability-tiers.md`](./docs/explanations/capability-tiers.md).
- **Deny-path test contract** — three mandatory `@pytest.mark.security` tests per MCP tool boundary (deny / default-deny / escalation). Security-marked tests are never skipped. PRs reducing security test count require architect sign-off.
- **Service separability** — `services.<A>` cannot import `services.<B>.*`; enforced by `scripts/checks/check_imports.py` as a PR-required CI gate.
- **Bandit `S` rule family** in CI — `eval`, `pickle.loads` across trust boundaries, `yaml.load` without `SafeLoader`, `subprocess(shell=True)`, weak hash functions, `random` for security purposes, etc. are all hard-banned.
- **Internal vs external error boundary** — stack traces, file paths, module names, and DB schema hints never cross to external surfaces (HTTP response, MCP tool return, CLI/Telegram output). See [`_bmad-output/project-context.md`](./_bmad-output/project-context.md) Cat 5.

## Disclosure

After a fix lands, a security advisory will be published on GitHub with credit to the reporter (unless anonymity is requested). The advisory includes the affected versions and the migration path.

Thank you for helping keep oh-my-bmad's operators safe.
