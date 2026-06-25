# oh-my-bmad — operator recipes
#
# Story 1.1 shipped `bootstrap-verify`. Story 1.2 extended it to cover all
# importable workspace members. Story 10.1 (2026-05-19) raised to 14 modules
# verified (capabilities excluded by convention — library-only, no service
# entrypoint; documented in 10.1 Dev Agent Record).
# Story 1.3 adds `sync-upstream` + `migrator-test-additive`.
# Richer recipes (dev, test, lint, scenarios, backup, build, deploy-vps,
# deploy-macos) arrive in Story 1.4 (compose + env + justfile).

default:
    @just --list

# Apply any pending Alembic migrations to the registry-state SQLite store.
# DB path is controlled by REGISTRY_STATE_DB_URL env var (default: local dev path
# sqlite+aiosqlite:////var/lib/oh-my-bmad/registry/state.sqlite3 from env.py).
# Example: REGISTRY_STATE_DB_URL=sqlite+aiosqlite:////tmp/test.sqlite3 just migrate
migrate:
    cd services/registry-state && uv run alembic upgrade head

# Verify the uv workspace resolves and every cross-package import works.
bootstrap-verify:
    uv sync --frozen --no-dev
    uv run --no-dev python -c "from events import __version__; print('events', __version__)"
    uv run --no-dev python -c "from registry_api import __version__, hello; print('registry_api', __version__, '|', hello())"
    uv run --no-dev python -c "from registry_state import __version__; print('registry_state', __version__)"
    uv run --no-dev python -c "from telegram_gateway import __version__; print('telegram_gateway', __version__)"
    uv run --no-dev python -c "from console_cli import __version__; print('console_cli', __version__)"
    uv run --no-dev python -c "from orchestrator_adapter import __version__; print('orchestrator_adapter', __version__)"
    uv run --no-dev python -c "from worker_wrapper import __version__; print('worker_wrapper', __version__)"
    uv run --no-dev python -c "from clawhip_daemon import __version__; print('clawhip_daemon', __version__)"
    uv run --no-dev python -c "from task_registry_mcp import __version__; print('task_registry_mcp', __version__)"
    uv run --no-dev python -c "from session_registry_mcp import __version__; print('session_registry_mcp', __version__)"
    uv run --no-dev python -c "from clawhip_bridge_mcp import __version__; print('clawhip_bridge_mcp', __version__)"
    uv run --no-dev python -c "from git_mcp import __version__; print('git_mcp', __version__)"
    uv run --no-dev python -c "from github_mcp import __version__; print('github_mcp', __version__)"
    uv run --no-dev python -c "from verification_mcp import __version__; print('verification_mcp', __version__)"
    uv run --no-dev python -c "from secret_hygiene import __version__; print('secret_hygiene', __version__)"
    uv run --no-dev python -c "from idempotency import __version__; print('idempotency', __version__)"
    uv run --no-dev python -c "from metrics_subscriber import __version__; print('metrics_subscriber', __version__)"
    uv run --no-dev python -c "from memory_mcp import __version__; print('memory_mcp', __version__)"
    uv run --no-dev python -c "from artifact_mcp import __version__; print('artifact_mcp', __version__)"
    @echo "✓ bootstrap OK (19 workspace-member imports verified)"

# Vendor an upstream fork into upstream/<name>/ and update VENDORED.md with
# the pinned commit SHA. Supported names: omc, clawhip. See Architecture
# §Starter Template Evaluation / Upstream Fork Integration.
sync-upstream name:
    uv run python scripts/sync_upstream.py {{name}}

# Build the migrator Docker image and run its trivial v1.0.0→v1.0.1 additive
# upgrade against a synthetic fixture. Asserts the output is valid JSONL
# with the new `extensions` field on every event.
migrator-test-additive:
    @echo "→ building migrator image…"
    docker build --quiet -t oh-my-bmad-migrator:test scripts/migrator
    @echo "→ preparing fixture sandbox…"
    rm -rf .tmp/migrator-test
    mkdir -p .tmp/migrator-test
    cp scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl .tmp/migrator-test/sample.jsonl
    @echo "→ running migrator…"
    docker run --rm -v "${PWD}/.tmp/migrator-test:/data" -e EVENT_LOG_PATH=/data/sample.jsonl oh-my-bmad-migrator:test v1.0.0-to-v1.0.1
    @echo "→ asserting output…"
    uv run python scripts/migrator/tests/assert_migrated.py .tmp/migrator-test/sample.v1.0.1.jsonl --expected 135
    @test -f .tmp/migrator-test/sample.v1.0.0.archive || (echo "FAIL: archive missing" && exit 1)
    @echo "→ cleanup"
    rm -rf .tmp/migrator-test

# Bring up the stack. On macOS automatically includes the overlay (bind-mount
# to ${HOME}/.oh-my-bmad) and ensures the host directory exists. On Linux uses
# the base compose only (named volume, no host-path prep needed).
# Depends on `build-base` so a fresh clone gets the shared `oh-my-bmad-base:local`
# image before compose's per-service builds run (they all `FROM oh-my-bmad-base:local`).
dev: build-base
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(uname -s)" = "Darwin" ]; then
        mkdir -p "${HOME}/.oh-my-bmad"
        exec docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d
    else
        exec docker compose -f docker-compose.yml up -d
    fi

# PR-gate test suite (excludes @pytest.mark.slow). Runs on `just test`, on
# every commit via .github/workflows/ci.yml, and local developer workflows.
test:
    uv run pytest -m "not slow"

# Full test matrix — includes @pytest.mark.slow. Used by the nightly CI
# workflow (lands in a later story) and operator-manual full-regression runs.
test-slow:
    uv run pytest

# Just the contract-test tree. Required before any `just sync-upstream` per
# Architecture §Post-MVP ops — contract tests pin upstream-fork behavior.
test-contract:
    uv run pytest tests/contract

# Synthetic crash-injection harness (Story 2.11 / NFR-R1 / NFR-R2 + Story
# 2.12 / FR30). Boots `registry-state` under docker compose, drives a
# synthesized task through each lifecycle phase, kills the container (Linux:
# `stop --timeout 1`, macOS: `kill --signal SIGKILL`), restarts, and asserts
# state-reconstruction with zero duplicate events. Slow (~3-5 min); excluded
# from PR-gate `just test`. Nightly CI invokes this recipe via
# `.github/workflows/nightly.yml`.
#
# Docker dependency split:
#   - Story 2.11 tests (test_restart_recovery.py) require Docker — skipped
#     gracefully via the `skip_if_no_docker` fixture when `docker info` fails.
#   - Story 2.12 tests (test_write_interrupt.py) are filesystem-only (pure
#     subprocess + tmp_path) and run UNCONDITIONALLY — Docker is not needed.
test-crash:
    uv run pytest -m crash --tb=short -v tests/crash-injection/

# Story 2.13 — idempotency 100× concurrent-replay test (FR28 / NFR-R4).
# Exercises ``IdempotencyCacheStore.get_or_run`` wired into POST /v1/tasks:
# 100 concurrent same-key POSTs, byte-identical replies, exactly 1
# ``task.created`` event. The 10× parametrized variant gives the nightly
# its statistical-flakiness signal. Pure in-memory SQLite + httpx
# ASGITransport — no Docker required; fast (~1.3s for all 15 cases).
# Included in the PR-gate ``just test`` lane AND nightly CI.
# Trailing ``*ARGS`` lets nightly forward ``--junitxml=...`` (Mn9).
test-idempotency *ARGS:
    uv run pytest -m idempotency -v tests/idempotency/ {{ARGS}}

# Story 2.14 — migrator integration tests (FR22 / NFR-M3).
# Verifies the v1.0.0 → v1.0.1 additive schema upgrade end-to-end:
# the 100-event fixture runs through the migrator in-process, the
# migrated bytes round-trip through ``EventEnvelope.from_canonical_json``,
# and materializing both the v1.0.0 archive and the v1.0.1 output through
# fresh in-memory SQLite DBs yields identical observable state
# (``tasks`` + ``sessions`` + ``events`` identity columns). Pure stdlib
# + in-memory SQLite — no Docker required; runtime well under 1s.
# Included in nightly CI as the ``migrator-integration`` job.
test-migrator *ARGS="":
    uv run pytest -m migrator -v tests/migrator/ {{ARGS}}

# Story 3.8 — NFR-S5 command-injection Hypothesis fuzz suite. Runs the full
# 13K-example budget (1 combined 10K @slow @fuzz test + 6 per-strategy 500 @fuzz
# tests). ~2 min wall-clock with the full 13K example budget. Excluded from
# PR-gate `just test` via @pytest.mark.slow (combined 10K only; per-strategy
# 500-example tests DO run on PR gate). Trailing `*ARGS` lets nightly forward
# `--junitxml=...` (Story 2.13 Mn9 carry-forward).
test-fuzz *ARGS="":
    uv run pytest tests/integration/test_command_injection_fuzz.py -m fuzz {{ARGS}}

# Story 2.15 — separability tests (FR34 / FR35; S-3 lands here, S-1 + S-2
# in Stories 5.16 + 5.17c). The S-3 e2e test (slow) boots a 3-service
# compose stack with ``ORCHESTRATOR_IMAGE=null-orchestrator:latest`` and
# asserts a task POSTed to registry-api transitions to ``completed`` via
# the null fixture's emitted lifecycle — proving FR35 / NFR-M5. The
# git-diff sentinel (fast) asserts the working tree leaves spine source
# untouched. Trailing ``*ARGS`` lets nightly forward ``--junitxml=...``.
test-separability *ARGS="": build-base
    uv run pytest -m separability -v tests/separability/ {{ARGS}}

# Story 14.2 — mutation-testing harness (NFR-O11), powered by cosmic-ray.
#
# WHY cosmic-ray, not mutmut: mutmut copies sources to mutants/ but this uv
# workspace's *editable* installs resolve imports back to the pristine real
# source, so no mutant is ever exercised (false 0%). cosmic-ray mutates the
# real source IN PLACE under VCS during `exec`, then RESTORES it after each
# mutant — so editable installs pick up the mutation. The session db is a
# gitignored *.sqlite; cosmic-ray leaves the working tree clean on completion.
#
# `mutation-test` — FULL lifecycle over the 3 kernel modules (tiers.py,
# schema_registry.py, canonical.py). SLOW (minutes); nightly / operator-only.
# Non-gating in 14.2 — the score is a baseline signal; Story 14.3 wires the
# `--threshold` enforcement once a defensible floor is established.
# Trailing `*ARGS` lets nightly forward extra flags if needed.
mutation-test *ARGS="":
    rm -f mutation.sqlite
    mkdir -p _bmad-output/test-artifacts
    uv run cosmic-ray init cosmic-ray.toml mutation.sqlite
    uv run cosmic-ray exec cosmic-ray.toml mutation.sqlite
    uv run cosmic-ray dump mutation.sqlite > _bmad-output/test-artifacts/mutation-dump.txt
    uv run python scripts/mutation_score.py --dump-path _bmad-output/test-artifacts/mutation-dump.txt {{ARGS}}

# `mutation-score` — recompute + print the score from the existing session db
# WITHOUT re-running the (slow) mutation exec. Re-dumps mutation.sqlite (cheap)
# and feeds the dump to the pure scorer (which never spawns a process itself).
mutation-score *ARGS="":
    mkdir -p _bmad-output/test-artifacts
    uv run cosmic-ray dump mutation.sqlite > _bmad-output/test-artifacts/mutation-dump.txt
    uv run python scripts/mutation_score.py --dump-path _bmad-output/test-artifacts/mutation-dump.txt {{ARGS}}

# `mutation-gate` — GATING variant of `mutation-test` (Story 14.3, NFR-O11).
# Runs the SAME slow cosmic-ray init/exec/dump lifecycle over the 3 kernels,
# then feeds the dump to mutation_score.py WITH `--threshold {{THRESHOLD}}` so a
# score below the floor exits NON-ZERO (failing this recipe — and the nightly
# job that calls it). THRESHOLD is version-controlled here: 82 = floor() of the
# 82.4% (145/176) first-nightly baseline. Ratchet UP as surviving mutants are
# killed; never lower it silently (see docs/testing-guide.md). SLOW (minutes);
# nightly / operator-only — do NOT run in the PR-gate lane.
mutation-gate THRESHOLD="82":
    rm -f mutation.sqlite
    mkdir -p _bmad-output/test-artifacts
    uv run cosmic-ray init cosmic-ray.toml mutation.sqlite
    uv run cosmic-ray exec cosmic-ray.toml mutation.sqlite
    uv run cosmic-ray dump mutation.sqlite > _bmad-output/test-artifacts/mutation-dump.txt
    uv run python scripts/mutation_score.py --dump-path _bmad-output/test-artifacts/mutation-dump.txt --threshold {{THRESHOLD}}

# `mutation-expanded-baseline` — NON-GATING exploratory baseline for newer
# kernels (currently task_fsm.py + gemini_runner.py). Do not wire this to
# nightly enforcement until a reviewed threshold is established.
mutation-expanded-baseline *ARGS="":
    rm -f mutation-expanded.sqlite
    mkdir -p _bmad-output/test-artifacts
    uv run cosmic-ray init cosmic-ray.expanded.toml mutation-expanded.sqlite
    uv run cosmic-ray exec cosmic-ray.expanded.toml mutation-expanded.sqlite
    uv run cosmic-ray dump mutation-expanded.sqlite > _bmad-output/test-artifacts/mutation-expanded-dump.txt
    uv run python scripts/mutation_score.py --dump-path _bmad-output/test-artifacts/mutation-expanded-dump.txt {{ARGS}}

# `mutation-smoke` — hard-bounded harness proof: mutates ONLY tiers.py against
# its co-located unit suite (<~3 min). This is the in-place-mutation sanity
# check that proves cosmic-ray reaches the editable-installed source where
# mutmut produced a false 0%. cosmic-ray restores the tree on completion; the
# session db is gitignored. Emits a real `mutation-score: killed/checked` line.
mutation-smoke:
    rm -f mutation-smoke.sqlite
    mkdir -p _bmad-output/test-artifacts
    uv run cosmic-ray init cosmic-ray.smoke.toml mutation-smoke.sqlite
    uv run cosmic-ray exec cosmic-ray.smoke.toml mutation-smoke.sqlite
    uv run cosmic-ray dump mutation-smoke.sqlite > _bmad-output/test-artifacts/mutation-smoke-dump.txt
    uv run python scripts/mutation_score.py --dump-path _bmad-output/test-artifacts/mutation-smoke-dump.txt

# Strict lint + format + type-check + architectural-discipline gates.
# ruff rules cover style (E/F/I/UP/B/SIM/N); ruff format --check enforces
# canonical formatting; mypy --strict gates the platform-owned packages +
# the registry services (upstream adapters relaxed per mypy.ini).
# The three check-gate scripts enforce import-graph, event-registry, and
# single-writer constraints — same checks CI runs; splitting them out would
# create a footgun where `just lint` is green locally but CI fails.
# Story 2.11 (AC-15): crash-injection harness also passes mypy --strict.
# Story 2.13 (AC-13): idempotency replay tests also pass mypy --strict.
# `--explicit-package-bases` avoids a module-name conflict when the harness
# directory (tests/crash-injection/, tests/idempotency/) is not a Python
# package. Registry_state is found via mypy_path (mypy.ini) because
# services/registry-state now has a py.typed marker.
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state services/worker-wrapper
    uv run mypy --strict --explicit-package-bases tests/crash-injection tests/idempotency tests/migrator tests/separability tests/fixtures/null_orchestrator
    uv run python scripts/check_imports.py
    uv run python scripts/check_event_registry.py
    uv run python scripts/check_single_writer.py
    uv run python scripts/check_no_subprocess.py
    uv run python scripts/check_mcp_transport.py
    git ls-files -z | xargs -0 uv run secret-hygiene-precommit

# Run the secret-hygiene scanner across every tracked file. Pre-commit hook
# runs it per-commit; this recipe is the full-tree sweep operators run
# periodically + that `just lint` delegates to.
scan-secrets:
    git ls-files -z | xargs -0 uv run secret-hygiene-precommit

# Architectural-discipline gates: import-graph, event-registry, single-writer,
# mcp-transport (P2-I4 stdio-only), trace-id-required (NFR-O7), task-fsm-only (P6-I3).
# Replicates the CI `Check*` steps locally; run before opening a PR.
check-gates:
    uv run python scripts/check_imports.py
    uv run python scripts/check_event_registry.py
    uv run python scripts/check_single_writer.py
    uv run python scripts/check_mcp_transport.py
    uv run python scripts/check_trace_id_required.py
    uv run python scripts/check_tier_declarations.py
    uv run python scripts/check_task_fsm_only.py

# Run the architectural-gate self-tests — exercises the bundled fixture
# trees under scripts/checks/fixtures/ to verify each check script's own
# detection logic still works.
check-gates-self-test:
    uv run python scripts/check_imports.py --self-test
    uv run python scripts/check_event_registry.py --self-test
    uv run python scripts/check_single_writer.py --self-test
    uv run python scripts/check_mcp_transport.py --self-test
    uv run python scripts/check_trace_id_required.py --self-test
    uv run python scripts/check_tier_declarations.py --self-test
    uv run python scripts/check_sbom_licenses.py --self-test
    uv run python scripts/check_task_fsm_only.py --self-test

# Scenario harness (journey-level smoke tests) lands across Stories 2.11 /
# 2.12 / 5.18. Story 1.5 only wires the harness; real scenarios land later.
scenarios:
    @echo "scenario harness lands across Stories 2.11 / 2.12 / 5.18"

# Snapshot the `oh-my-bmad-data` named volume to a local .tgz. Works on Linux
# (named volume lives under /var/lib/docker/volumes/) AND macOS (overlay binds
# to ${HOME}/.oh-my-bmad) because we mount the volume into a throwaway alpine
# container and tar it from there — no host-path assumption. Optional `name=`
# suffix labels the archive (e.g., `just backup name=pre-upgrade`). Timestamp
# is UTC second-precision so same-day backups don't collide.
backup name="":
    #!/usr/bin/env bash
    set -euo pipefail
    ts=$(date -u +%FT%H%M%SZ)
    name_arg='{{name}}'
    # Validate name contains only safe chars to prevent shell-expansion surprises
    # when a future operator passes e.g. `name="foo; rm -rf /"`.
    if [ -n "${name_arg}" ] && ! echo "${name_arg}" | grep -Eq '^[A-Za-z0-9._-]+$'; then
        echo "ERROR: backup name must match [A-Za-z0-9._-]+, got: ${name_arg}" >&2
        exit 1
    fi
    suffix=""
    if [ -n "${name_arg}" ]; then suffix="-${name_arg}"; fi
    archive="oh-my-bmad-backup-${ts}${suffix}.tgz"
    # Locate the data volume regardless of compose project prefix.
    volume=$(docker volume ls --format '{{{{.Name}}' | grep -E '_oh-my-bmad-data$' | head -1 || true)
    if [ -z "${volume}" ]; then
        echo "ERROR: no *_oh-my-bmad-data docker volume found. Initialize the stack first (just dev)." >&2
        exit 1
    fi
    compose_files=(-f docker-compose.yml)
    if [ "$(uname -s)" = "Darwin" ]; then
        compose_files+=(-f docker-compose.macos.yml)
    fi
    # Restart the stack on exit even if tar fails — operators should never lose
    # their platform to a failed backup attempt.
    restart() {
        echo "→ restarting stack"
        docker compose "${compose_files[@]}" up -d || true
    }
    trap restart EXIT
    echo "→ stopping stack"
    docker compose "${compose_files[@]}" down
    echo "→ archiving volume ${volume} → ${PWD}/${archive}"
    docker run --rm \
        -v "${volume}:/source:ro" \
        -v "${PWD}:/dest" \
        alpine:3 \
        tar -czf "/dest/${archive}" -C /source .
    echo "✓ backup written to ${PWD}/${archive}"

# Story 13.3 / FR71 — DISASTER RECOVERY: rebuild the oh-my-bmad-data volume from
# the litestream replica configured in `litestream.yml` (Story 13.2 + ADR-0007).
# Replication ≠ HA: this is an operator-initiated, stack-DOWN procedure.
# Flow: stop stack → empty the volume → litestream restore state.sqlite3 → bring
# the registry dir back to the omb-group perms → start stack → bootstrap-verify.
# Config-based: the bucket/key + creds live in litestream.yml / LITESTREAM_* env
# (the SAME config the sidecar uses) — no positional bucket/key arg.
# DESTRUCTIVE: replaces the live volume. Run only when recovering a lost/corrupt
# host. `config=` overrides the config path (default litestream.yml). Set
# OMB_RESTORE_CONFIRM=yes-restore to skip the interactive prompt (automation).
restore-from-litestream config="litestream.yml":
    #!/usr/bin/env bash
    set -euo pipefail
    cfg='{{config}}'
    if [ ! -f "${cfg}" ]; then
        echo "ERROR: litestream config '${cfg}' not found (cp litestream.yml.example litestream.yml, then fill it)." >&2
        exit 1
    fi
    volume=$(docker volume ls --format '{{{{.Name}}' | grep -E '_oh-my-bmad-data$' | head -1 || true)
    if [ -z "${volume}" ]; then
        echo "ERROR: no *_oh-my-bmad-data docker volume found. Run 'just dev' once to create it." >&2
        exit 1
    fi
    # Review HIGH: DESTRUCTIVE op — require an explicit typed confirmation (or the
    # OMB_RESTORE_CONFIRM env bypass for scripted DR). Empties the live volume.
    if [ "${OMB_RESTORE_CONFIRM:-}" != "yes-restore" ]; then
        echo "WARNING: this DESTROYS all data in volume '${volume}' and replaces it from the litestream replica."
        printf "Type 'yes-restore' to proceed: "
        read -r confirm
        if [ "${confirm}" != "yes-restore" ]; then echo "Aborted." >&2; exit 1; fi
    fi
    compose_files=(-f docker-compose.yml)
    if [ "$(uname -s)" = "Darwin" ]; then compose_files+=(-f docker-compose.macos.yml); fi
    # Review HIGH: recovery trap — if anything fails after the wipe, the volume is
    # empty + stack down. Print clear guidance instead of leaving the operator
    # stranded. Cleared on success before the final messages.
    fail_guidance() {
        echo "" >&2
        echo "FATAL: restore-from-litestream did not complete — volume '${volume}' may be EMPTY and the stack is DOWN." >&2
        echo "  → Fix the cause (config path / LITESTREAM_* creds / replica reachability), then re-run." >&2
        echo "  → Or restore a tarball snapshot instead (see docs/backup-restore.md)." >&2
    }
    trap fail_guidance EXIT
    echo "→ stopping stack"
    docker compose "${compose_files[@]}" down
    echo "→ emptying volume ${volume} (DESTRUCTIVE) + recreating registry/ + registry/events/ (2775, omb gid 10000)"
    docker run --rm -v "${volume}:/v" alpine:3 sh -c '
        set -e
        rm -rf /v/* /v/.[!.]* /v/..?* 2>/dev/null || true
        [ -z "$(ls -A /v)" ] || { echo "ERROR: volume not empty after wipe" >&2; exit 1; }
        mkdir -p /v/registry/events
        chown -R 10002:10000 /v/registry
        chmod 2775 /v /v/registry /v/registry/events'
    echo "→ restoring state.sqlite3 from litestream replica (latest generation)"
    docker run --rm \
        -v "${volume}:/var/lib/oh-my-bmad" \
        -v "${PWD}/${cfg}:/etc/litestream/litestream.yml:ro" \
        -e LITESTREAM_ACCESS_KEY_ID="${LITESTREAM_ACCESS_KEY_ID:-}" \
        -e LITESTREAM_SECRET_ACCESS_KEY="${LITESTREAM_SECRET_ACCESS_KEY:-}" \
        litestream/litestream:${LITESTREAM_VERSION:-0.3.13} \
        restore -config /etc/litestream/litestream.yml /var/lib/oh-my-bmad/registry/state.sqlite3
    # Review CRITICAL: litestream runs as root, so the restored state.sqlite3 is
    # root-owned and registry-state (uid 10002) cannot write it → readonly-DB
    # crash loop (the Epic-11.3 bug). Re-own + 0o660 the restored file (+ -wal/-shm
    # if present) so the omb group can write before the stack comes up.
    echo "→ fixing restored DB ownership/mode (uid 10002:omb, 0o660)"
    docker run --rm -v "${volume}:/v" alpine:3 sh -c '
        for f in /v/registry/state.sqlite3 /v/registry/state.sqlite3-wal /v/registry/state.sqlite3-shm; do
            [ -e "$f" ] && chown 10002:10000 "$f" && chmod 0660 "$f" || true
        done'
    echo "→ restarting stack"
    docker compose "${compose_files[@]}" up -d
    echo "→ verifying workspace resolves"
    just bootstrap-verify
    trap - EXIT
    echo "✓ restore-from-litestream complete — now confirm the stack reaches healthy:"
    echo "    docker compose ${compose_files[*]} ps"

# Story 13.3 / FR71 — hermetic litestream replicate→restore drill (no cloud).
# Proves the restore MECHANISM: seed WAL db → file-replica → wipe → restore →
# assert integrity_check + row count. Locally runnable + run in nightly.yml.
litestream-restore-drill:
    ./scripts/litestream-restore-drill.sh

# Story 13.4 / NFR-R7 — poll litestream /metrics, debounce, emit replication.lagging.
# Run on a ~30s cron/timer. Detects a sustained (>5 min) replication stall
# (litestream_sync_count stalls + litestream_sync_error_count rises) and emits a
# single replication.lagging event per episode via the FR26 flock-guarded append.
# Config is env-driven: OMB_LITESTREAM_METRICS_URL (default
# http://localhost:9090/metrics — requires `addr: ":9090"` in litestream.yml),
# OMB_LITESTREAM_DB, OMB_LITESTREAM_LAG_STATE_PATH, EVENT_LOG_DIR.
# Exit 3 on flock contention (Platform stack is the live writer).
litestream-lag-check:
    uv run python scripts/check_replication_lag.py

# Build the shared base image `oh-my-bmad-base:local`. Every per-service
# Dockerfile extends this. Run before `just build` / `just deploy-*` on a
# fresh checkout or whenever `Dockerfile.base` / `uv.lock` changes.
build-base:
    DOCKER_BUILDKIT=1 docker build -f Dockerfile.base --target runtime-base -t oh-my-bmad-base:local .

# Build all 7 service images locally (single-arch: 6 compose services via
# `docker compose build` + console-cli via `docker build`). Multi-arch buildx
# bake lands with Story 1.9's release workflow. Depends on the shared base
# image — `build-base` ensures it exists before per-service builds run.
build: build-base
    docker compose -f docker-compose.yml build
    docker build -f services/console-cli/Dockerfile -t oh-my-bmad-console-cli:local .

# Console CLI wrapper — runs oh-my-bmad-cli in an ephemeral container
# attached to the compose network. Requires `just build` (for the image)
# and `just dev` (for the stack). Example: just cli task "build auth module"
cli *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker compose ps --quiet 2>/dev/null | head -1 | grep -q .; then
        echo "Error: stack not running. Run \`just dev\` first." >&2
        exit 1
    fi
    # Must match name: in docker-compose.yml
    project="omb"
    network="${project}_oh-my-bmad-net"
    if ! docker image inspect oh-my-bmad-console-cli:local >/dev/null 2>&1; then
        echo "Error: console-cli image not found. Run \`just build\` first." >&2
        exit 1
    fi
    exec docker run --rm --network "${network}" oh-my-bmad-console-cli:local {{ARGS}}

# Print every oh-my-bmad-* docker image and its size. Operator sanity after
# `just build` — each service image must stay ≤ 200 MB per Story 1.8 AC-7
# (worker-wrapper is the documented exception at ~283 MB; see story 1.8 notes).
image-sizes:
    @docker image ls --format '{{{{.Repository}}:{{{{.Tag}} {{{{.Size}}' | grep -E '(^oh-my-bmad-|/oh-my-bmad-)' | sort

# Linux VPS deploy primitive: build-base → pull-if-available → build → up.
# Story 1.9's GHCR images will make pull the primary path; until then build
# is the source of truth. Docs in Story 1.10a.
#
# Epic 14 / Story 14.1 / FR77: this TAG-BASED path is DEPRECATED for
# production — tags are mutable. Use ``just deploy-vps-digest`` (digest-pinned,
# the supported production path). This recipe is retained for local/dev builds.
deploy-vps: build-base
    @echo "::warning::[FR77] tag-based deploy-vps is DEPRECATED for production (mutable tags). Use 'just deploy-vps-digest' (digest-pinned, the supported production path)."
    docker compose -f docker-compose.yml pull || true
    docker compose -f docker-compose.yml build
    docker compose -f docker-compose.yml up -d

# macOS deploy primitive — same flow plus the macOS overlay and a mkdir
# prerequisite so the bind-mount source exists.
#
# Epic 14 / Story 14.1 / FR77: DEPRECATED for production — see deploy-vps above.
# Use ``just deploy-macos-digest`` (digest-pinned, the supported production path).
deploy-macos: build-base
    @echo "::warning::[FR77] tag-based deploy-macos is DEPRECATED for production (mutable tags). Use 'just deploy-macos-digest' (digest-pinned, the supported production path)."
    mkdir -p "${HOME}/.oh-my-bmad"
    docker compose -f docker-compose.yml -f docker-compose.macos.yml pull || true
    docker compose -f docker-compose.yml -f docker-compose.macos.yml build
    docker compose -f docker-compose.yml -f docker-compose.macos.yml up -d

# Epic 14 / Story 14.1 / FR77 — DIGEST-PINNED VPS deploy (supported production
# path). Verifies the supply-chain triumvirate (cosign sig + SLSA + SBOM) for
# every published image, then pulls + runs each CORE service BY CONTENT-DIGEST
# via the docker-compose.digest.yml overlay (immutable, not the mutable tag).
#
# The overlay's ``${OMB_IMAGE_DIGEST_<svc>:?...}`` refs FAIL LOUD if any digest
# is unset, so the ``pull`` deliberately has NO ``|| true`` — a missing/wrong
# digest MUST abort the deploy, never silently fall back to a tag.
# Populate OMB_IMAGE_DIGEST_* in .env from the release notes first (see
# docs/deployment-guide.md §Upgrading).
deploy-vps-digest: verify-images
    docker compose -f docker-compose.yml -f docker-compose.digest.yml pull
    docker compose -f docker-compose.yml -f docker-compose.digest.yml up -d

# Epic 14 / Story 14.1 / FR77 — DIGEST-PINNED macOS deploy. Same flow as
# deploy-vps-digest plus the macOS bind-mount overlay + mkdir prerequisite.
deploy-macos-digest: verify-images
    mkdir -p "${HOME}/.oh-my-bmad"
    docker compose -f docker-compose.yml -f docker-compose.digest.yml -f docker-compose.macos.yml pull
    docker compose -f docker-compose.yml -f docker-compose.digest.yml -f docker-compose.macos.yml up -d

# Story 8.5 (Phase 2 Epic 8): Verify cosign signature + SLSA L2 + CycloneDX SBOM
# attestations for every Platform-published image before deploy. Operator runs
# this BEFORE `docker compose pull` to enforce the supply-chain triumvirate at
# the deploy boundary (FR56a, NFR-S9).
#
# Requirements:
#   - cosign binary installed locally (brew install cosign | apt install cosign |
#     download from sigstore/cosign releases). See docs/deployment-guide.md
#     §"Verifying releases" for installation guidance.
#   - .env populated with OMB_GHCR_OWNER + OMB_IMAGE_DIGEST_<service> entries
#     (one per image). Digests are listed in the GitHub release notes.
#
# Failure modes (mapped to owning story per ADR-0008 §F12 8-state table):
#   - cosign verify fails → Story 8.3 signature gate (re-tag or fix-forward).
#   - cosign verify-attestation --type slsaprovenance fails → Story 8.2 SLSA
#     gate (re-tag, typically Sigstore Fulcio outage).
#   - cosign verify-attestation --type cyclonedx fails → Story 8.4 SBOM-attest
#     gate (cosign attest re-runnable independently from local cosign).
#
# Exit codes: 0 = all 8 images verified all 3 attestations; 1 = any failure.
verify-images:
    #!/usr/bin/env bash
    set -euo pipefail
    # Code-review pass-2 F13: trap cleanup ensures tempfiles created via
    # mktemp are removed on SIGINT/SIGTERM, not just normal exit.
    _omb_tmpfiles=()
    cleanup_tmpfiles() {
        if [ ${#_omb_tmpfiles[@]} -gt 0 ]; then
            rm -f "${_omb_tmpfiles[@]}" 2>/dev/null || true
        fi
    }
    trap cleanup_tmpfiles EXIT INT TERM
    if ! command -v cosign >/dev/null 2>&1; then
        echo "::error::cosign binary not found; install via brew/apt or sigstore releases. See docs/deployment-guide.md §Verifying releases."
        exit 1
    fi
    if [ ! -f .env ]; then
        echo "::error::.env file missing; copy .env.example to .env and populate OMB_IMAGE_DIGEST_<service> entries."
        exit 1
    fi
    # shellcheck disable=SC1091
    set -a
    source .env
    set +a
    : "${OMB_GHCR_OWNER:?OMB_GHCR_OWNER must be set in .env (typically the GitHub repository owner)}"
    # Code-review pass-2 F4: warn when OMB_GHCR_OWNER differs from the canonical
    # upstream owner. The cert-identity regexp interpolates this value, so an
    # operator who sets OMB_GHCR_OWNER to a fork would trust attestations from
    # that fork's workflow. Hard-fail only if the value is malformed; warn (do
    # not block) when it differs from the canonical, since legitimate forks
    # exist. Operator can suppress the warning by setting OMB_ACK_CUSTOM_OWNER=1.
    OMB_CANONICAL_OWNER="salacoste"
    if ! printf '%s' "${OMB_GHCR_OWNER}" | grep -qE '^[a-z0-9](-?[a-z0-9])*$'; then
        echo "::error::OMB_GHCR_OWNER='${OMB_GHCR_OWNER}' has invalid GitHub-username format (lowercase alphanumeric + hyphens, no trailing hyphen)."
        exit 1
    fi
    if [ "${OMB_GHCR_OWNER}" != "${OMB_CANONICAL_OWNER}" ] && [ "${OMB_ACK_CUSTOM_OWNER:-0}" != "1" ]; then
        echo "::warning::OMB_GHCR_OWNER='${OMB_GHCR_OWNER}' is NOT the canonical upstream owner '${OMB_CANONICAL_OWNER}'."
        echo "::warning::All cosign verifies will trust attestations signed by ${OMB_GHCR_OWNER}'s workflow runs."
        echo "::warning::Only proceed if you operate a legitimate fork. Set OMB_ACK_CUSTOM_OWNER=1 in .env to suppress this warning."
    fi
    REGISTRY="ghcr.io"
    SERVICES=("base" "registry-api" "registry-state" "telegram-gateway" "orchestrator-adapter" "worker-wrapper" "clawhip-daemon" "console-cli")
    # Anchored cert-identity-regexp (F1 lesson from Story 8.2 review +
    # F3 end-anchor + semver tightening from pass-2): prevents fork-attestation
    # spoofing AND suffix-injection. Matches only canonical workflow at a
    # well-formed semver tag (including pre-release identifiers).
    CERT_ID="^https://github.com/${OMB_GHCR_OWNER}/oh-my-bmad/\.github/workflows/release\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$"
    CERT_ISSUER="https://token.actions.githubusercontent.com"
    # Code-review pass-2 F6: validate digest format before passing to cosign.
    # `cosign` would reject a malformed digest, but failing early gives the
    # operator a clearer error message naming the .env variable to fix.
    digest_re='^sha256:[a-f0-9]{64}$'
    failures=()
    # G2 (FR56a / NFR-S9): parallel structured track for the 3 cosign-verify
    # failure types, so the final block can best-effort emit one
    # ``deployment.signature_rejected`` event per failed check. We deliberately
    # do NOT record the "digest not set / invalid format" failures here — there
    # is no resolvable image to attest in those cases.
    emit_image=()
    emit_digest=()
    emit_type=()
    emit_errtail=()
    for svc in "${SERVICES[@]}"; do
        digest_var="OMB_IMAGE_DIGEST_${svc//-/_}"
        digest="${!digest_var:-}"
        if [ -z "$digest" ]; then
            failures+=("$svc: $digest_var not set in .env")
            continue
        fi
        if ! printf '%s' "$digest" | grep -qE "$digest_re"; then
            failures+=("$svc: $digest_var='$digest' has invalid format (expected sha256:<64 hex chars>)")
            continue
        fi
        image="${REGISTRY}/${OMB_GHCR_OWNER}/oh-my-bmad-${svc}@${digest}"
        # Digest-less repo ref for the emit helper's --image arg: its payload
        # model (_IMAGE_PATTERN) accepts the canonical repo ref WITHOUT the
        # ``@sha256:...`` suffix (the digest is carried separately in --digest).
        image_ref="${REGISTRY}/${OMB_GHCR_OWNER}/oh-my-bmad-${svc}"
        echo "→ verifying $svc @ $digest"
        # 1. cosign signature (Story 8.3)
        errfile=$(mktemp)
        _omb_tmpfiles+=("$errfile")
        if ! cosign verify \
            --certificate-identity-regexp "$CERT_ID" \
            --certificate-oidc-issuer "$CERT_ISSUER" \
            "$image" >/dev/null 2>"$errfile"; then
            errtail=$(tail -5 "$errfile" 2>/dev/null || true)
            failures+=("$svc: cosign verify (signature) FAILED — owned by Story 8.3"$'\n'"    $errtail")
            emit_image+=("$image_ref"); emit_digest+=("$digest"); emit_type+=("signature"); emit_errtail+=("$errtail")
        fi
        rm -f "$errfile"
        # 2. SLSA L2 provenance attestation (Story 8.2)
        errfile=$(mktemp)
        _omb_tmpfiles+=("$errfile")
        # Story 8.2 pass-3 fix: use explicit predicate URI instead of the
        # ``slsaprovenance`` alias. cosign aliases assume SLSA v0.2 (deprecated);
        # `actions/attest-build-provenance@v4.1.0` publishes SLSA v1.0
        # (`https://slsa.dev/provenance/v1`). Bypassing the alias works on both
        # cosign v2 and v3.
        if ! cosign verify-attestation --type "https://slsa.dev/provenance/v1" \
            --certificate-identity-regexp "$CERT_ID" \
            --certificate-oidc-issuer "$CERT_ISSUER" \
            "$image" >/dev/null 2>"$errfile"; then
            errtail=$(tail -5 "$errfile" 2>/dev/null || true)
            failures+=("$svc: cosign verify-attestation slsaprovenance FAILED — owned by Story 8.2"$'\n'"    $errtail")
            emit_image+=("$image_ref"); emit_digest+=("$digest"); emit_type+=("slsaprovenance"); emit_errtail+=("$errtail")
        fi
        rm -f "$errfile"
        # 3. CycloneDX SBOM attestation (Story 8.4)
        # Story 8.4 pass-3 fix: explicit predicate URI matches both cosign
        # v2 (legacy `.att`) and cosign v3 (OCI 1.1 referrers, the default for
        # cosign attest >=v3.0). Requires CI to run cosign v3 (workflow's
        # cosign-release pin) so the attestation lands in OCI referrers.
        errfile=$(mktemp)
        _omb_tmpfiles+=("$errfile")
        if ! cosign verify-attestation --type "https://cyclonedx.org/bom" \
            --certificate-identity-regexp "$CERT_ID" \
            --certificate-oidc-issuer "$CERT_ISSUER" \
            "$image" >/dev/null 2>"$errfile"; then
            errtail=$(tail -5 "$errfile" 2>/dev/null || true)
            failures+=("$svc: cosign verify-attestation cyclonedx FAILED — owned by Story 8.4"$'\n'"    $errtail")
            emit_image+=("$image_ref"); emit_digest+=("$digest"); emit_type+=("cyclonedx"); emit_errtail+=("$errtail")
        fi
        rm -f "$errfile"
    done
    if [ ${#failures[@]} -ne 0 ]; then
        echo
        echo "::error::Supply-chain verification FAILED for ${#failures[@]} item(s):"
        for f in "${failures[@]}"; do echo "  - $f"; done
        echo
        echo "Triage: see docs/deployment-guide.md §Verifying releases for fix-forward procedures."
        echo "Do NOT run docker compose pull until all verifications pass."
        # G2 traceability close (FR56a / NFR-S9): best-effort record one
        # ``deployment.signature_rejected`` event per FAILED cosign-verify check,
        # routed THROUGH scripts/emit_signature_rejected.py (FR26: that helper
        # is the single flock-defended writer — no second writer path here).
        #
        # Hard conservatism rules:
        #   * Emission is BEST-EFFORT. It NEVER changes the outcome: this recipe
        #     always still `exit 1` below regardless of emit success/failure.
        #   * Guarded so a non-zero emit exit does not trip `set -e`.
        #   * Helper exit 3 (flock contention = stack appears up) → ::warning::
        #     and continue. Any other non-zero → ::warning:: and continue.
        #   * `uv` missing → ::warning:: and skip.
        #   * Opt-out: OMB_SKIP_REJECTION_EVENT=1 skips emission with a notice.
        if [ "${OMB_SKIP_REJECTION_EVENT:-0}" = "1" ]; then
            echo "::notice::OMB_SKIP_REJECTION_EVENT=1 — skipping deployment.signature_rejected emission (G2 opt-out)."
        elif [ ${#emit_type[@]} -eq 0 ]; then
            : # no emittable cosign-verify failures (only digest not-set/format) — nothing to record.
        elif ! command -v uv >/dev/null 2>&1; then
            echo "::warning::uv not on PATH — skipping deployment.signature_rejected emission (FR56a/NFR-S9 gap not recorded for this run)."
        else
            for i in "${!emit_type[@]}"; do
                emit_args=(
                    run python scripts/emit_signature_rejected.py
                    --image "${emit_image[$i]}"
                    --digest "${emit_digest[$i]}"
                    --attestation-type "${emit_type[$i]}"
                    --error-message "${emit_errtail[$i]}"
                    --omb-version "${OMB_VERSION:-unknown}"
                    --ghcr-owner "$OMB_GHCR_OWNER"
                    # operator-id must match the payload model's
                    # ``^op-[a-zA-Z0-9-]+$`` contract; ``op-verify-images``
                    # identifies the gate as emitter AND passes validation so
                    # the rejection event is actually recorded (gap closed).
                    --operator-id "${OMB_OPERATOR_ID:-op-verify-images}"
                )
                # Only redirect the event log when the operator/test sets the
                # override; otherwise the helper uses its own default dir.
                if [ -n "${OMB_EVENT_LOG_DIR:-}" ]; then
                    emit_args+=(--event-log-dir "$OMB_EVENT_LOG_DIR")
                fi
                # Guard the call so a non-zero exit does NOT abort via set -e,
                # while still capturing the code for the exit-3 distinction.
                emit_rc=0
                uv "${emit_args[@]}" || emit_rc=$?
                if [ "$emit_rc" -eq 3 ]; then
                    echo "::warning::rejection event not recorded — registry-state holds the log lock; stack appears up (${emit_type[$i]} / ${emit_image[$i]})."
                elif [ "$emit_rc" -ne 0 ]; then
                    echo "::warning::emit_signature_rejected exited ${emit_rc} — rejection event not recorded (${emit_type[$i]} / ${emit_image[$i]})."
                fi
            done
        fi
        exit 1
    fi
    echo
    echo "✓ All 8 images verified (signature + SLSA L2 + CycloneDX SBOM)."
    echo "  Safe to proceed: docker compose pull && docker compose up -d"

# Build base + one representative service multi-arch locally via buildx
# (no push). Diagnostic hook — exercises the release.yml hand-off
# (`--build-context oh-my-bmad-base:local=...`) to catch shape bugs before
# tagging. Auto-bootstraps a `omb-multiarch` buildx builder if missing.
# Pass `version=<X>` to set tag; defaults to `dev`.
release-local version="dev":
    #!/usr/bin/env bash
    set -euo pipefail
    if ! docker buildx inspect omb-multiarch >/dev/null 2>&1; then
        echo "→ bootstrapping omb-multiarch buildx builder"
        docker buildx create --name omb-multiarch --driver docker-container >/dev/null
    fi
    docker buildx use omb-multiarch
    echo "→ building multi-arch base: oh-my-bmad-base:{{version}}"
    docker buildx build --platform linux/amd64,linux/arm64 \
        -f Dockerfile.base --target runtime-base \
        -t oh-my-bmad-base:{{version}} \
        --output type=oci,dest=/tmp/omb-base-{{version}}.tar \
        .
    echo "→ building registry-api with --build-context override (amd64 only, loaded locally for smoke)"
    # Single-platform + --load so the resulting image is usable via
    # `docker run` — multi-platform --load is not supported.
    docker buildx build --platform linux/amd64 \
        --build-context oh-my-bmad-base:local=docker-image://python:3.12-slim-bookworm \
        -f services/registry-api/Dockerfile \
        -t oh-my-bmad-registry-api:{{version}}-smoke \
        --load \
        . || echo "  ↑ smoke build without real base is expected to warn; it validates build-contexts syntax only"
    echo "✓ release-local {{version}} complete"
    echo "  Base OCI archive: /tmp/omb-base-{{version}}.tar"
    echo "  Real release builds a base-FROM chain; this is a syntax/shape check only."

# Verify the HMAC of a task.approval_signed event from the JSONL log (FR65, Story 11.4).
# Works offline — Platform stack not required. Requires OPERATOR_HMAC_KEY env var
# (or --key-file PATH for archived-key verification).
#
# Usage:
#   just verify-approval EVENT_ID                          # uses default log dir
#   just verify-approval EVENT_ID /path/to/log/dir         # custom log dir
#   just verify-approval EVENT_ID /path/to/log/dir --json  # machine-readable
verify-approval EVENT_ID LOG_DIR='/var/lib/oh-my-bmad/registry/events' *FLAGS='':
    uv run python scripts/verify_approval.py {{EVENT_ID}} --log-dir {{LOG_DIR}} {{FLAGS}}
