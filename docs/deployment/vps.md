# VPS Deployment (Ubuntu 24.04)

This guide walks an operator from a fresh VPS to a running oh-my-bmad stack
that passes `docker compose ps` all-healthy. Audience: Ubuntu 24.04 LTS,
≥ 2 GB RAM, public IPv4.

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
# Docker Engine + Compose v2 — Docker's official apt repo.
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

# Add your user to the docker group (re-login required for the change to take effect).
sudo usermod -aG docker "$USER"

# uv (Python package/workspace manager).
curl -LsSf https://astral.sh/uv/install.sh | sh
# Reload PATH so `uv` is available immediately in this shell session.
source "$HOME/.local/bin/env"

# just (task runner).
# Option A — snap (Ubuntu):
sudo snap install just --classic
# Option B — cargo (any Linux):
# cargo install just

# git (usually pre-installed; upgrade if below 2.40):
sudo apt-get install -y git
```

---

## Tunnel choice

The Telegram webhook requires HTTPS. oh-my-bmad does not bundle a reverse
proxy. Run exactly one option on the host (not inside compose).

### Option A — Cloudflare Tunnel (default, recommended)

Free, zero-config, no port-forwarding required. Works behind NAT/firewalls.

```sh
# Install cloudflared (Debian/Ubuntu):
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
    sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared jammy main' | \
    sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

# Start a temporary tunnel (replace 8080 with your gateway port):
cloudflared tunnel --url http://localhost:8080
# cloudflared prints a public *.trycloudflare.com URL — use that as your webhook.
```

Set `TUNNEL_MODE=cloudflare` in `.env`.

### Option B — ngrok

Adequate for solo-operator use on the free tier.

```sh
# Install ngrok:
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
    sudo tee /etc/apt/trusted.gpg.d/ngrok.asc > /dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
    sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt-get update && sudo apt-get install -y ngrok
ngrok config add-authtoken <NGROK_AUTH_TOKEN>

# Start tunnel:
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
| `REGISTRY_DB_PATH` | — | Leave at default `/var/lib/oh-my-bmad/registry/state.sqlite3` on VPS |
| `ENV` | — | Set to `production` for a real VPS (disables Swagger UI at `/v1/docs`) |
| `TUNNEL_MODE` | — | Must match tunnel option chosen above: `cloudflare`, `ngrok`, or `byo` |
| `OMB_IMAGE_REGISTRY` | — | `ghcr.io/<GITHUB_OWNER>` — use your owner namespace, or keep `ghcr.io/r2d2` for canonical upstream images |
| `OMB_VERSION` | github.com/<owner>/oh-my-bmad/releases | Set to a published semver tag (e.g. `0.1.0`) OR keep `dev` to build locally from source |

Example (safe placeholders — replace every angle-bracket value):

```ini
TELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>
ANTHROPIC_API_KEY=sk-ant-<paste-your-key-here>
GITHUB_TOKEN=<GITHUB_PAT>
TG_ALLOWLIST_USER_IDS=<YOUR_TELEGRAM_NUMERIC_ID>
REGISTRY_DB_PATH=/var/lib/oh-my-bmad/registry/state.sqlite3
ENV=production
TUNNEL_MODE=cloudflare
OMB_IMAGE_REGISTRY=ghcr.io/<GITHUB_OWNER>
OMB_VERSION=0.1.0
```

---

## Deploy

```sh
just deploy-vps
```

This recipe chains: `build-base` → `compose pull || true` → `compose build`
→ `compose up -d`.

- `build-base` builds the shared `oh-my-bmad-base:local` image from
  `Dockerfile.base`.
- `compose pull || true` attempts to pull pre-built service images from GHCR;
  the `|| true` means a `manifest unknown` error (e.g., `OMB_VERSION=dev`) is
  non-fatal — the build step covers it.
- `compose build` builds any service whose image wasn't pulled.
- `compose up -d` starts all 6 compose services in detached mode.

Expected final lines:

```
 ✔ Container oh-my-bmad-registry-state-1       Started
 ✔ Container oh-my-bmad-registry-api-1         Started
 ✔ Container oh-my-bmad-telegram-gateway-1     Started
 ✔ Container oh-my-bmad-orchestrator-adapter-1 Started
 ✔ Container oh-my-bmad-worker-wrapper-1       Started
 ✔ Container oh-my-bmad-clawhip-daemon-1       Started
```

---

## Verify

```sh
docker compose ps
```

All 6 services should show `Up (healthy)` within 60 seconds. The healthcheck
uses `/tmp/ready` (Story 1.4) — it fires once the service's `__main__.py`
startup completes.

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

### 1. `env file .env not found`

```
Error response from daemon: ... env file .env not found
```

Fix:

```sh
cp .env.example .env
$EDITOR .env   # fill in all required fields
```

### 2. Container stuck in `(unhealthy)` or restarting

```sh
docker compose logs <service-name>
```

The Story 1.4 hello-world `__main__.py` logs `<service> ready` on successful
startup. If you see an import error or missing env-var, fix `.env` and re-run
`just deploy-vps`.

### 3. Tunnel can't reach Telegram webhook

Check cloudflared (or ngrok) logs for connection errors. Common causes:

- Wrong port in the tunnel command — must match the gateway port
  (default `8080`).
- `TUNNEL_MODE` in `.env` doesn't match the running tunnel type.
- Firewall or VPS security-group blocks outbound connections on port 7844
  (cloudflared) or 443 (ngrok).

### 4. `manifest unknown` on `compose pull`

```
Error response from daemon: manifest unknown
```

`OMB_VERSION` points at a tag that hasn't been published to GHCR. Either:

- Set `OMB_VERSION=dev` to build entirely from local source (no pull needed).
- Set `OMB_VERSION` to a tag that exists at
  `github.com/<owner>/oh-my-bmad/releases`.

### 5. SELinux blocks bind-mount (RHEL / Fedora hosts)

On SELinux-enforcing systems, the registry bind-mount may fail with
`permission denied`. Fix: append the `:Z` flag to the volume definition in
`docker-compose.yml`, or consider switching to the named-volume path used on
Ubuntu (the default VPS target of this guide).

---

## Upgrading

When a new `v*` tag is published to GHCR:

```sh
# 1. Bump the version in .env:
#    OMB_VERSION=<new-tag>   e.g. OMB_VERSION=0.2.0

# 2. Pull new images and restart:
docker compose pull
docker compose up -d
```

Compose stops each service, pulls the new image tag, and restarts with
preserved volumes. Persistent data (registry DB, event log, artifacts) survives
the upgrade.

See the top-level `README.md` Upgrading section for notes on `:latest` tag
advancement and pre-release tags.
