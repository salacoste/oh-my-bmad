# VPS Deployment (Ubuntu 24.04)

This guide walks an operator from a fresh VPS to a running oh-my-bmad stack
that passes `docker compose ps` all-healthy. Audience: Ubuntu 24.04 LTS,
≥ 2 GB RAM, public IPv4.

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

Follow the [official Docker Engine install guide](https://docs.docker.com/engine/install/ubuntu/) for Ubuntu. After install:

```sh
# Add your user to the docker group (re-login required).
sudo usermod -aG docker "$USER"

# uv (Python package/workspace manager):
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

# just (snap is the easiest on Ubuntu):
sudo snap install just --classic

# git (usually pre-installed):
sudo apt-get install -y git
```

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
| `TUNNEL_MODE` | — | Must match tunnel option chosen below: `cloudflare`, `ngrok`, or `byo` |
| `OMB_IMAGE_REGISTRY` | — | `ghcr.io/<GITHUB_OWNER>` — use your owner namespace, or keep `ghcr.io/r2d2` for canonical upstream images |
| `OMB_VERSION` | github.com/<owner>/oh-my-bmad/releases | Set to a published semver tag (e.g. `0.1.0`) OR keep `dev` to build locally from source |

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
ENV=production
TUNNEL_MODE=cloudflare
OMB_IMAGE_REGISTRY=ghcr.io/<GITHUB_OWNER>
OMB_VERSION=0.1.0
```

---

## Tunnel choice

The Telegram webhook requires HTTPS. oh-my-bmad does not bundle a reverse
proxy. Run exactly one option on the host (not inside compose).

> **Note (Phase 1 state):** `telegram-gateway` is currently a hello-world
> container that does NOT listen on any port. The `--url http://localhost:<port>`
> argument below is a placeholder for when **Story 3.1 (aiogram webhook)**
> wires the real webhook receiver. You can still install + start your tunnel
> of choice today as a dry-run — it simply has no backend to forward to
> until Story 3.1 lands.

### Option A — Cloudflare Tunnel (recommended)

Free, zero-config, no port-forwarding required. Works behind NAT/firewalls.

```sh
# Install cloudflared (Debian/Ubuntu):
# Cloudflared publishes a single `jammy` apt suite; it works on Ubuntu 24.04
# (noble) because the binary is statically linked. No noble-specific suite
# exists upstream yet.
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
    sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared jammy main' | \
    sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

# Start a temporary tunnel (port is set by Story 3.1):
cloudflared tunnel --url http://localhost:<port-set-by-Story-3.1>
# cloudflared prints a public *.trycloudflare.com URL — use that as your webhook.
```

Set `TUNNEL_MODE=cloudflare` in `.env`.

### Alternatives

If you prefer **ngrok**: `sudo apt-get install -y ngrok` (via the ngrok apt
repo at https://ngrok.com/docs/agent/install/) or download the binary directly;
run `ngrok http '<port-set-by-Story-3.1>'`. Set `TUNNEL_MODE=ngrok` in `.env`.

If you prefer a **BYO reverse proxy** (nginx, Caddy, Traefik): configure TLS
termination to forward HTTPS to the telegram-gateway container on the compose
network. Set `TUNNEL_MODE=byo` in `.env`.

Only one tunnel should be active at a time.

---

## Deploy

```sh
just deploy-vps
```

This recipe chains: `build-base` → `compose pull || true` → `compose build` → `compose up -d`.
`build-base` builds the shared `oh-my-bmad-base:local` image; `compose pull || true` fetches
any pre-published GHCR images (non-fatal if absent — e.g. `OMB_VERSION=dev`);
`compose build` covers any unpulled services; `compose up -d` starts all 6.

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

All 6 services should show `Up (healthy)` within ~60 s after container create
(add several minutes on a cold first-run including `docker compose build`).
The healthcheck uses `/tmp/ready` (Story 1.4) — it fires once the service's
`__main__.py` startup completes.

The `/v1/health` HTTP endpoint arrives in Story 2.9. The `/ping` Telegram
command arrives in Story 3.5. Real task execution arrives in Story 5.12.

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
  (arrives in Story 3.1).
- `TUNNEL_MODE` in `.env` doesn't match the running tunnel type.
- Firewall or VPS security-group blocks outbound connections on port 7844
  (cloudflared) or 443 (ngrok).

### 4. `manifest unknown` on `compose pull`

`OMB_VERSION` points at a tag that hasn't been published to GHCR. Either set
`OMB_VERSION=dev` to build from local source, or set it to a tag that exists
at `github.com/<owner>/oh-my-bmad/releases`.

### 5. SELinux blocks bind-mount (RHEL / Rocky / AlmaLinux)

The compose stack assumes no SELinux enforcement (Ubuntu 24.04 default). If
deploying on an SELinux-enforcing host, add `:z` (shared-volume relabel) to
volume mounts in a compose override — not `:Z` (exclusive), which breaks the
shared `oh-my-bmad-data` volume when multiple services mount it.

---

## Upgrading

See the [Upgrading](../../README.md#upgrading) section in the root README for
the canonical bump + pull + up-d flow.
