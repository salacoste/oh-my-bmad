# macOS Deployment (Local Dev / Self-Hosted)

This guide walks an operator on macOS 14+ from a fresh clone to a running
oh-my-bmad stack that passes `docker compose ps` all-healthy. Covers Docker
Desktop and Colima.

---

## Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Docker Engine | 24.0 | BuildKit enabled by default |
| Docker Compose | v2.24 | Required for `env_file: {path, required}` syntax used in `docker-compose.yml` |
| uv | 0.5.0 | Matches `pyproject.toml` `[tool.uv] required-version = ">=0.5"` |
| just | 1.14 | Any recent stable release — no unusual syntax used |
| git | 2.25 | `--depth 1` clone (Ubuntu 20.04+ default) |

### Install

```sh
# Install Homebrew if missing:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv + just in one shot:
brew install uv just

# git (usually pre-installed):
brew install git
```

#### Docker runtime — choose one

**Option A — Docker Desktop (recommended for most operators)**

Download Docker Desktop ≥ 4.x from https://www.docker.com/products/docker-desktop/.
BuildKit is enabled by default from Docker Desktop 4.0+. No extra configuration needed.

**Option B — Colima (lightweight alternative)**

```sh
brew install colima docker docker-compose
colima start --runtime docker --cpu 4 --memory 8
```

Note: if you switch between Docker Desktop and Colima, the active Docker
context changes. Verify with `docker context ls` and set the correct context
with `docker context use <name>`.

---

## Clone + bootstrap

```sh
git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
uv sync --dev
uv run pre-commit install
just bootstrap-verify
```

Expected output (abridged):

```
events 0.1.0
registry_api 0.1.0 | hello from registry_api
...
✓ bootstrap OK (... workspace-member imports verified)
```

---

## Configure `.env`

```sh
cp .env.example .env
$EDITOR .env
```

Field-by-field annotations:

| Field | Where to get it | Notes |
|-------|----------------|-------|
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram → `/newbot` | FAIL-CLOSED: empty means the bot won't start |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys | Starts with `sk-ant-` |
| `GITHUB_TOKEN` | github.com → Settings → Developer settings → Tokens (classic) | Needs `repo` scope for PR-draft creation (Story 5.14) |
| `TG_ALLOWLIST_USER_IDS` | @userinfobot on Telegram — DM it, it replies with your numeric ID | Comma-separated; **WARNING: empty denies ALL users** |
| `REGISTRY_DB_PATH` | — | Leave at default — the macOS overlay rewrites the host-side path; the container path stays the same |
| `ENV` | — | Use `development` for local macOS hosts (enables Swagger UI at `/v1/docs`) |
| `TUNNEL_MODE` | — | Must match tunnel option chosen below: `cloudflare`, `ngrok`, or `byo` |
| `OMB_IMAGE_REGISTRY` | — | `ghcr.io/<GITHUB_OWNER>` — or keep `ghcr.io/r2d2` for canonical upstream images |
| `OMB_VERSION` | — | Use `dev` on macOS dev hosts to build from local source. `just deploy-macos` will still attempt `docker compose pull`, but `\|\| true` tolerates the inevitable `manifest unknown` for the unpublished `:dev` tag and falls through to `docker compose build` — so local builds succeed without a matching GHCR tag. |

> ⚠️ Leave `REGISTRY_DB_PATH` at its default unless you also update
> `docker-compose.yml`'s volume `target:` to match — the service will write
> to a container path the mount doesn't cover, silently losing data on
> container recreate.

Example (safe placeholders — replace every angle-bracket value):

```ini
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
ANTHROPIC_API_KEY=sk-ant-<paste-your-key-here>
GITHUB_TOKEN=<GITHUB_TOKEN>
TG_ALLOWLIST_USER_IDS=<YOUR_TELEGRAM_NUMERIC_ID>
REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3
ENV=development
TUNNEL_MODE=cloudflare
OMB_IMAGE_REGISTRY=ghcr.io/<GITHUB_OWNER>
OMB_VERSION=dev
```

---

## Tunnel choice

The Telegram webhook requires HTTPS. oh-my-bmad does not bundle a reverse
proxy. Run exactly one option on the host (not inside compose). On macOS,
ngrok is often most convenient for rapid local iteration.

> **Note (Phase 1 state):** `telegram-gateway` is currently a hello-world
> container that does NOT listen on any port. The `--url http://localhost:<port>`
> argument below is a placeholder for when **Story 3.1 (aiogram webhook)**
> wires the real webhook receiver. You can still install + start your tunnel
> of choice today as a dry-run — it simply has no backend to forward to
> until Story 3.1 lands.

### Option A — Cloudflare Tunnel (default)

Free, zero-config, works behind NAT. Install via Homebrew:

```sh
brew install cloudflare/cloudflare/cloudflared

# Start a temporary tunnel (port is set by Story 3.1):
cloudflared tunnel --url http://localhost:<port-set-by-Story-3.1>
# Prints a public *.trycloudflare.com URL — use that as your webhook.
```

Set `TUNNEL_MODE=cloudflare` in `.env`.

### Alternatives

If you prefer **ngrok**: `brew install --cask ngrok`; run
`ngrok config add-authtoken '<NGROK_AUTH_TOKEN>'` then
`ngrok http '<port-set-by-Story-3.1>'`. Set `TUNNEL_MODE=ngrok` in `.env`.

If you prefer a **BYO reverse proxy** (nginx, Caddy, Traefik): configure TLS
termination to forward HTTPS to the telegram-gateway container. Set
`TUNNEL_MODE=byo` in `.env`.

Only one tunnel should be active at a time.

---

## Deploy

```sh
just deploy-macos
```

This recipe chains: `build-base` → `mkdir -p ~/.oh-my-bmad` → `compose pull || true` →
`compose build` → `compose up -d` with the macOS overlay. The overlay rewrites the
registry volume to `${HOME}/.oh-my-bmad` so all persistent data lands on your host
and survives container recreation. `registry-state` is the only service that writes
to this volume; the other three (`registry-api`, `worker-wrapper`, `clawhip-daemon`)
mount it read-only.

Expected final lines:

```
 ✔ Container oh-my-bmad-registry-state-1       Started
 ✔ Container oh-my-bmad-registry-api-1         Started
 ✔ Container oh-my-bmad-telegram-gateway-1     Started
 ✔ Container oh-my-bmad-orchestrator-adapter-1 Started
 ✔ Container oh-my-bmad-worker-wrapper-1       Started
 ✔ Container oh-my-bmad-clawhip-daemon-1       Started
```

Alternatively, `just dev` auto-detects macOS and applies the same overlay.

---

## Verify

```sh
docker compose ps
```

All 6 services should show `Up (healthy)` within ~60 s after container create
(add several minutes on a cold first-run including `docker compose build`).
The healthcheck uses `/tmp/ready` (Story 1.4) — it fires once the service's
`__main__.py` startup completes.

The `/v1/health` HTTP endpoint arrives in Story 2.9. The `/ping` Telegram
command arrives in Story 3.5. Real task execution arrives in Story 5.12.

---

## Console CLI

oh-my-bmad ships a local CLI (`oh-my-bmad-cli`) with full command-surface
parity to the Telegram bot. It runs inside an ephemeral Docker container
attached to the compose network, so the default `http://registry-api:8080`
config works without overrides.

Run it via the `just cli` recipe:

```sh
just cli task "build the auth module"
just cli status t-0192a1b5-1234-7abc-89de-f0123456789a
just cli logs t-0192a1b5-1234-7abc-89de-f0123456789a
just cli events t-0192a1b5-1234-7abc-89de-f0123456789a --follow
just cli ping
just cli --help
```

Prerequisites: run `just build` (builds the console-cli image) and
`just dev` (starts the stack) before using `just cli`.

### Shell alias

For terse desk-side use, add a shell alias:

```sh
# bash
echo "alias bm='just cli'" >> ~/.bashrc

# zsh (macOS default)
echo "alias bm='just cli'" >> ~/.zshrc
```

Then reload:

```sh
source ~/.zshrc   # or: source ~/.bashrc
```

Now you can use:

```sh
bm task "build auth module"
bm status t-0192a1b5-1234-7abc-89de-f0123456789a
bm ping
```

### Exit codes

The CLI maps HTTP errors to specific exit codes for scripting:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic error (network, server, unexpected) |
| 2 | Validation error (HTTP 422) |
| 4 | Not found (HTTP 404) |
| 5 | Conflict (HTTP 409) |

Example:

```sh
bm status t-nonexistent
case $? in
  0) echo "success" ;;
  4) echo "task not found" ;;
  *) echo "other error" ;;
esac
```

---

## Troubleshooting

### 1. `mkdir: /Users/<user>/.oh-my-bmad: Permission denied`

Your home directory should be writable. If this persists, run:

```sh
mkdir -p "${HOME}/.oh-my-bmad" && chmod 755 "${HOME}/.oh-my-bmad"
```

### 2. Docker Desktop VM out of disk

Builds fail with `no space left on device`. Fix: Docker Desktop → Settings →
Resources → Disk image size → increase → Apply & Restart.

### 3. Colima vs Docker Desktop context conflict

```sh
docker context ls && docker context use colima   # or: docker context use desktop-linux
```

### 4. BuildKit disabled (Docker Desktop < 4.x)

If you see multi-stage build errors, enable BuildKit:

```sh
export DOCKER_BUILDKIT=1
just deploy-macos
```

Upgrading to Docker Desktop ≥ 4.x makes this permanent.

### 5. Port conflict when tunneling locally

```sh
lsof -nP -iTCP -sTCP:LISTEN | grep <PORT>
```

Identify the process holding the port, stop it, then restart the tunnel.

---

## Upgrading

See the [Upgrading](../../README.md#upgrading) section in the root README for
the canonical bump + pull + up-d flow.
