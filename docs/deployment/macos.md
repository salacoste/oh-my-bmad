# macOS Deployment (Local Dev / Self-Hosted)

This guide walks an operator on macOS 14+ from a fresh clone to a running
oh-my-bmad stack that passes `docker compose ps` all-healthy. Covers Docker
Desktop and Colima.

---

## Prerequisites

| Tool | Minimum version | Notes |
|------|-----------------|-------|
| Docker Engine | 24.0 | BuildKit enabled by default |
| Docker Compose | v2.20 | Required for build-contexts |
| uv | 0.5.0 | Matches pyproject.toml required-version |
| just | 1.30 | Required for recipes |
| git | 2.40 | Required for sync-upstream fetch |

### Install

```sh
# Install Homebrew if missing:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv + just in one shot:
brew install uv just

# git (usually pre-installed; upgrade if below 2.40):
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

## Tunnel choice

The Telegram webhook requires HTTPS. oh-my-bmad does not bundle a reverse
proxy. Run exactly one option on the host (not inside compose). On macOS,
ngrok is often most convenient for rapid local iteration.

### Option A — Cloudflare Tunnel (default)

Free, zero-config, works behind NAT. Install via Homebrew:

```sh
brew install cloudflare/cloudflare/cloudflared

# Start a temporary tunnel (replace 8080 with your gateway port):
cloudflared tunnel --url http://localhost:8080
# Prints a public *.trycloudflare.com URL — use that as your webhook.
```

Set `TUNNEL_MODE=cloudflare` in `.env`.

### Option B — ngrok (recommended for macOS dev)

```sh
brew install ngrok/ngrok/ngrok
ngrok config add-authtoken <NGROK_AUTH_TOKEN>
ngrok http 8080
```

Set `TUNNEL_MODE=ngrok` in `.env`.

### Option C — BYO reverse proxy

Use nginx, Caddy, or Traefik with your own TLS certificate. Configure TLS
termination to forward HTTPS → `http://localhost:8080`. Set `TUNNEL_MODE=byo`
in `.env`.

The `.env.example` `TUNNEL_MODE` section (carried from Story 1.4) documents
this variable. Only one tunnel should be active at a time.

---

## Clone + bootstrap

```sh
git clone <this-repo-url> oh-my-bmad && cd oh-my-bmad
uv sync --dev
uv run pre-commit install
just bootstrap-verify
```

Expected output from `just bootstrap-verify`:

```
events 0.1.0
registry_api 0.1.0 | hello from registry_api
registry_state 0.1.0
telegram_gateway 0.1.0
console_cli 0.1.0
orchestrator_adapter 0.1.0
worker_wrapper 0.1.0
clawhip_daemon 0.1.0
task_registry_mcp 0.1.0
session_registry_mcp 0.1.0
clawhip_bridge_mcp 0.1.0
secret_hygiene 0.1.0
idempotency 0.1.0
✓ bootstrap OK (13 workspace-member imports verified)
```

---

## macOS data directory prerequisite

The macOS overlay bind-mounts `${HOME}/.oh-my-bmad` into containers so
persistent data lands on your host filesystem (not inside a Docker-managed
named volume).

`just deploy-macos` creates this directory automatically. If you prefer to
create it in advance:

```sh
mkdir -p "${HOME}/.oh-my-bmad"
```

If the directory is missing when compose starts, the bind-mount fails and
services that write to the registry DB will crash on startup.

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
| `REGISTRY_DB_PATH` | — | Leave at default `/var/lib/oh-my-bmad/registry/state.sqlite3` — the macOS overlay rewrites the host-side path; the container path stays the same |
| `ENV` | — | Use `development` for local macOS hosts (enables Swagger UI at `/v1/docs`) |
| `TUNNEL_MODE` | — | Must match tunnel option chosen above: `cloudflare`, `ngrok`, or `byo` |
| `OMB_IMAGE_REGISTRY` | — | `ghcr.io/<GITHUB_OWNER>` — or keep `ghcr.io/r2d2` for canonical upstream images |
| `OMB_VERSION` | — | Use `dev` on macOS dev hosts to build from local source (skips GHCR round-trip); set to a semver tag when pulling from GHCR |

Example (safe placeholders — replace every angle-bracket value):

```ini
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
ANTHROPIC_API_KEY=sk-ant-<paste-your-key-here>
GITHUB_TOKEN=<GITHUB_PAT>
TG_ALLOWLIST_USER_IDS=<YOUR_TELEGRAM_NUMERIC_ID>
REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3
ENV=development
TUNNEL_MODE=ngrok
OMB_IMAGE_REGISTRY=ghcr.io/<GITHUB_OWNER>
OMB_VERSION=dev
```

`OMB_VERSION=dev` means no GHCR pull is attempted — the build step uses your
local source tree directly. This is the right default for active development
on macOS.

---

## Deploy

```sh
just deploy-macos
```

This recipe chains: `build-base` → `mkdir -p ~/.oh-my-bmad` → `compose pull
|| true` → `compose build` → `compose up -d` with the macOS overlay.

The macOS overlay (`docker-compose.macos.yml`) rewrites the registry volume
to `${HOME}/.oh-my-bmad` on your host, so all persistent data lands under
your home directory and survives container recreation.

Expected final lines:

```
 ✔ Container oh-my-bmad-registry-state-1       Started
 ✔ Container oh-my-bmad-registry-api-1         Started
 ✔ Container oh-my-bmad-telegram-gateway-1     Started
 ✔ Container oh-my-bmad-orchestrator-adapter-1 Started
 ✔ Container oh-my-bmad-worker-wrapper-1       Started
 ✔ Container oh-my-bmad-clawhip-daemon-1       Started
```

Alternatively, use `just dev` which auto-detects macOS and applies the same
overlay without requiring you to remember the recipe name:

```sh
just dev   # macOS: applies overlay; Linux: base compose only
```

---

## Verify

```sh
docker compose ps
```

All 6 services should show `Up (healthy)` within 60 seconds. The healthcheck
uses `/tmp/ready` (Story 1.4) — it fires once the service's `__main__.py`
startup completes.

On macOS, Docker Desktop may report healthcheck state slightly differently in
the UI. Both `docker compose ps` and `docker inspect --format '{{.State.Health.Status}}' <container>` are reliable; `compose ps` is simpler.

The `/v1/health` HTTP endpoint arrives in Story 2.9. Once it lands:

```sh
curl http://localhost:<PORT>/v1/health
# Expected: {"status": "ok"}
```

The `/ping` Telegram command arrives in Story 3.5. Once it lands, send `/ping`
to your bot; expect `pong · <latency-ms>ms` within 2 seconds.

Real task execution (the core operator workflow) arrives in Story 5.12.

---

## Troubleshooting

### 1. `mkdir: /Users/<user>/.oh-my-bmad: Permission denied`

This is unusual — your home directory should be writable. More likely cause is
a missing intermediate directory. Verify with:

```sh
ls -la "${HOME}"
```

If the issue persists, create the directory manually:

```sh
mkdir -p "${HOME}/.oh-my-bmad"
chmod 755 "${HOME}/.oh-my-bmad"
```

### 2. Docker Desktop VM out of disk

Docker Desktop stores all images and volumes inside a Linux VM disk image. If
it fills up, builds fail with `no space left on device`.

Fix: Docker Desktop → Settings → Resources → Disk image size → increase and
Apply & Restart. Then relaunch Docker Desktop.

### 3. Colima vs Docker Desktop context conflict

If both Colima and Docker Desktop are installed, the active Docker context
determines which daemon Docker CLI talks to.

```sh
docker context ls                        # list contexts
docker context use colima                # switch to Colima
docker context use desktop-linux         # switch to Docker Desktop
```

When starting Colima, use:

```sh
colima start --runtime docker
```

The `--runtime docker` flag ensures the Docker-compatible socket is exposed.

### 4. BuildKit disabled (Docker Desktop < 4.x)

If you see `ERROR [internal] load build definition` or multi-stage build
errors, BuildKit may be off. Enable it:

```sh
export DOCKER_BUILDKIT=1
just deploy-macos
```

Upgrading to Docker Desktop ≥ 4.x makes this permanent (BuildKit is the
default from 4.0).

### 5. Port conflict when tunneling locally

If `cloudflared` or `ngrok` can't bind because the port is in use:

```sh
lsof -nP -iTCP -sTCP:LISTEN | grep <PORT>
```

Identify the process holding the port, stop it, then restart the tunnel.
Common culprits: a previous `ngrok` process, a local dev server, or another
compose project.

---

## Upgrading

When a new `v*` tag is published to GHCR:

```sh
# 1. Bump the version in .env:
#    OMB_VERSION=<new-tag>   e.g. OMB_VERSION=0.2.0

# 2. Pull new images and restart:
docker compose -f docker-compose.yml -f docker-compose.macos.yml pull
docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d
```

Or simply use `just dev` after bumping `OMB_VERSION` — it will pull and restart
with the overlay applied.

Persistent data under `${HOME}/.oh-my-bmad` survives the upgrade.

See the top-level `README.md` Upgrading section for notes on `:latest` tag
advancement and pre-release tags.
