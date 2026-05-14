# Source-tree analysis

Annotated map of the oh-my-bmad repository. Critical directories are marked with their purpose, entry point, and the workspace-member project type. Sizes are approximate (Python source LOC, excluding tests).

```
oh-my-bmad/
│
├── services/                              # 7 deployable backend services
│   ├── clawhip-daemon/                    # vendored-clawhip supervisor + outbound sink rendering  (~2.6K LOC; scaffold for Story 7.8)
│   │   └── src/clawhip_daemon/
│   │       ├── __init__.py                # version + purpose
│   │       ├── __main__.py                # entry point
│   │       ├── adapters/                  # upstream-fork integration shim
│   │       └── app/                       # supervision logic
│   │
│   ├── console-cli/                       # local Typer CLI; FR12 parity w/ Telegram  (~1.4K LOC)
│   │   └── src/console_cli/
│   │       ├── __main__.py
│   │       ├── commands/                  # subcommand registry
│   │       ├── adapters/                  # registry-API client
│   │       └── app/                       # bootstrap, settings
│   │
│   ├── orchestrator-adapter/              # OMC subprocess supervisor; FR42 swappable runtime  (~1.5K LOC; scaffold for Story 5.10)
│   │   └── src/orchestrator_adapter/
│   │       └── …                          # OMCRunner public surface
│   │
│   ├── registry-api/                      # HTTP API surface — POST/GET /v1/tasks/*  (~2.6K LOC)
│   │   └── src/registry_api/
│   │       ├── __init__.py
│   │       ├── app.py                     # FastAPI factory (build_app)
│   │       ├── lifecycle.py               # lifespan: DB pool + MCP client startup/shutdown
│   │       ├── routes/                    # v1/<resource>.py — see api-contracts.md
│   │       ├── adapters/                  # registry-state + idempotency wiring
│   │       └── test_*.py                  # co-located unit tests
│   │
│   ├── registry-state/                    # SINGLE WRITER — event log → SQLite materializer  (~4.2K LOC)
│   │   └── src/registry_state/
│   │       ├── __init__.py                # exports Task, SessionRow, Event, IdempotencyCache, Snapshot, EventLogWriter, Materializer, recovery
│   │       ├── app/main.py                # entry: run_subscriber
│   │       ├── models/                    # SQLAlchemy 2.0 typed ORM (Mapped[T] / mapped_column)
│   │       ├── materializer/              # event → SQLite materialization (FR25)
│   │       ├── eventlog/                  # JSONL EventLogWriter / EventLogReader
│   │       ├── recovery/                  # restart-replay (NFR-R2)
│   │       └── migrations/versions/       # Alembic (date-prefixed; N999 suppressed)
│   │
│   ├── telegram-gateway/                  # aiogram v3 webhook bot  (~4.9K LOC)
│   │   └── src/telegram_gateway/
│   │       ├── app/main.py                # build_app — webhook + dispatcher
│   │       ├── handlers/                  # /ping, /tasks, /approve, /reject, /stop, /retry, /digest …
│   │       └── middleware/                # AllowlistMiddleware (single auth gate per ADR-0001)
│   │
│   └── worker-wrapper/                    # Claude Code CLI subprocess supervisor; emits typed events via MCP  (~3.1K LOC)
│       └── src/worker_wrapper/
│           ├── adapters/                  # MCP-bridge client
│           ├── app/                       # subprocess lifecycle
│           └── domain/                    # event emission, atomic-write helpers
│
├── packages/                              # 4 shared libraries (imported by services + MCP servers)
│   ├── capabilities/                      # tier classification + enforcement helpers  (~160 LOC)
│   │   └── src/capabilities/              # Tier, CallerContext, CapabilityOk, CapabilityDenied, check_tier*
│   │
│   ├── events/                            # SHARED EVENT ENVELOPE — schema registry + canonical serializer  (~2.0K LOC)
│   │   └── src/events/                    # EventEnvelope, Actor, FrozenClock, new_uuid7, 32+ Payload types, REGISTRY
│   │
│   ├── idempotency/                       # UUIDv7 dedup cache (TTLCache + SQLite persistence, FR28, 7-day TTL)  (~550 LOC)
│   │   └── src/idempotency/               # IdempotencyCacheStore, CacheHit, IdempotencyConflict
│   │
│   └── secret-hygiene/                    # 3-layer secret enforcement (pre-commit + structlog sanitizer + audit events)  (~2.0K LOC)
│       └── src/secret_hygiene/            # AuditedSecret, AuditedBaseSettings, audited_secret_field, sanitizer processor
│
├── mcp-servers/                           # 3 MCP stdio servers (tool/resource contracts to worker)
│   ├── task-registry/                     # read-only task queries + bounded-write tools  (~470 LOC)
│   │   └── src/task_registry_mcp/         # tools: task_add_note, task_attach_artifact, task_emit_event
│   │
│   ├── session-registry/                  # session lifecycle (register/heartbeat/close)  (~420 LOC)
│   │   └── src/session_registry_mcp/      # tools: session_register, session_heartbeat, session_close
│   │
│   └── clawhip-bridge/                    # APPEND-ONLY event-emission surface; sole mutation path to event log  (~425 LOC)
│       └── src/clawhip_bridge_mcp/        # tools: emit_event, emit_blocker, emit_summary, emit_approval_request, emit_completion
│
├── upstream/                              # vendored upstream forks (pinned SHAs in VENDORED.md)
│   ├── omc/                               # Yeachan-Heo/oh-my-claudecode
│   ├── clawhip/                           # Yeachan-Heo/clawhip
│   └── <each>/adapter.py                  # ONLY import path — direct imports of vendored internals rejected
│
├── tests/                                 # cross-service test trees (co-located units live next to source)
│   ├── conftest.py                        # canonical fixtures: fixed_clock, seeded_uuid7, capture_structlog
│   ├── separability/                      # S-1/S-2/S-3 adapter-swap tests
│   ├── crash-injection/                   # NFR-R2 process-crash + recovery
│   ├── idempotency/                       # UUIDv7-key replay tests
│   ├── integration/                       # cross-service journeys
│   ├── contract/                          # upstream-adapter fixtures
│   │   └── fixtures/<adapter>/            # recorded stdin/stdout per adapter
│   ├── migrator/                          # event-log schema migrator correctness
│   └── fixtures/                          # shared payloads (NOT pytest)
│
├── scripts/                               # CI gates + tooling
│   ├── checks/                            # check_imports.py, secret-hygiene runner, fixtures/
│   ├── migrator/                          # one-shot Docker migrator (NOT a uv workspace member)
│   └── sync_upstream.py                   # invoked by `just sync-upstream <name>`
│
├── docs/                                  # operator + AI-context docs (this directory)
│   ├── adr/                               # architecture decision records
│   ├── deployment/                        # vps.md + macos.md
│   ├── *.md                               # operator-runbook, schema-evolution, exceptions, testing-guide, backup-restore, message-design, RENDERER_CONVENTIONS, development
│   └── (this scan adds)                   # project-overview, source-tree-analysis, architecture, component-inventory, api-contracts, data-models, development-guide, deployment-guide, index
│
├── _bmad-output/
│   ├── planning-artifacts/                # product-brief, prd, architecture, epics, readiness-report
│   ├── implementation-artifacts/          # sprint-status.yaml + per-story artifacts
│   ├── project-context.md                 # AI-agent rule digest (Cats 1-7) — required reading before code
│   └── deferred-work.md                   # epic-boundary deferred items w/ review_by dates
│
├── _bmad/                                 # BMad framework + skill configs
│   ├── _config/                           # manifest.yaml, agent-manifest.csv, skill-manifest.csv, bmad-help.csv
│   ├── bmm/                               # planning module config + skills
│   ├── bmb/                               # builder module
│   ├── tea/                               # test-architecture module
│   └── core/                              # core module
│
├── pyproject.toml                         # uv workspace root (services/* + packages/* + mcp-servers/*)
├── uv.lock                                # locked deps (regenerate via uv lock; never hand-edit)
├── justfile                               # operator recipes (single source of truth)
├── docker-compose.yml                     # base stack (Linux)
├── docker-compose.macos.yml               # macOS overlay (bind-mounts permitted only here)
├── Dockerfile.base                        # multi-stage base image (Story 1.8)
├── .env / .env.example                    # operator-provisioned secrets + documented defaults
├── ruff.toml                              # lint + format SoT (line 100, py312, E/F/I/UP/B/SIM/N)
├── mypy.ini                               # --strict; mypy_path is SINGLE-LINE form (do not "reformat")
├── .pre-commit-config.yaml                # secret-hygiene-precommit + secret-hygiene-commit-msg
├── .roborev.toml                          # PR auto-review config
├── .secret-hygiene-ignore                 # opt-out paths for the scanner
└── README.md                              # human-facing quickstart
```

## Architecturally load-bearing directories

These have invariants enforced by CI / static checks. **Don't drift them without an ADR.**

- `services/registry-state/` — single writer (FR26). No other service holds an `AsyncSession`.
- `services/registry-state/src/registry_state/eventlog/` — `EventLogWriter` is the only opener-for-write on the JSONL log.
- `packages/events/src/events/` — event envelope + schema registry. Adding a new event type registers `(event_type, schema_version)` here.
- `upstream/<fork>/adapter.py` — sole import path for vendored code. Direct imports of vendored internals are rejected.
- `mcp-servers/*` — stdio transport only. Imports of `mcp.server.sse` / `mcp.server.streamable_http` are rejected.
- `scripts/checks/check_imports.py` — runs the separability / boundary checks on PR.

## Entry-point map

| Process | Entry | Notes |
|---|---|---|
| `registry-api` | `python -m registry_api` → `registry_api.app:build_app` | FastAPI factory; `lifespan` owns DB pool startup |
| `registry-state` | `python -m registry_state` → `registry_state.app.main:run_subscriber` | Single-writer subscriber loop |
| `telegram-gateway` | `python -m telegram_gateway` → `telegram_gateway.app.main:build_app` | aiogram v3 webhook |
| `worker-wrapper` | `python -m worker_wrapper` | Claude Code subprocess supervisor |
| `console-cli` | `python -m console_cli` | Typer CLI; not in Compose by design |
| `orchestrator-adapter` | `python -m orchestrator_adapter` | OMC subprocess supervisor (scaffold) |
| `clawhip-daemon` | `python -m clawhip_daemon` | clawhip supervisor (scaffold) |
| `task_registry_mcp` | stdio MCP server | invoked by orchestrator as subprocess |
| `session_registry_mcp` | stdio MCP server | same |
| `clawhip_bridge_mcp` | stdio MCP server | same; sole event-mutation surface |

Scaffold processes (`signal.pause()` + healthcheck touch) remain until their owning story replaces them — see `docs/exceptions.md` for the replacement-story map.
