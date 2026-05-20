# services/metrics-subscriber/Dockerfile — thin override of oh-my-bmad-base:local.
# Pre-req: `just build-base` (or `docker build -f Dockerfile.base
# --target runtime-base -t oh-my-bmad-base:local .`).
#
# Story 10.6 — closes Epic 10. Mirrors the pattern from
# services/registry-api/Dockerfile + services/registry-state/Dockerfile:
# the shared `oh-my-bmad-base:local` image carries the workspace venv
# (built with `--no-editable` so wheels include source); each per-service
# Dockerfile adds a unique non-root user + entrypoint.
#
# UID 10008: next free slot after the existing assignments
# (10001 registry-api, 10002 registry-state, 10003 telegram-gateway,
#  10004 orchestrator-adapter, 10005 worker-wrapper, 10006 clawhip-daemon,
#  10007 console-cli). Story 10.6 spec D5 originally proposed 10003 but
# that UID is occupied; 10008 is the next available slot.
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10008 --gid omb --home /app --shell /usr/sbin/nologin metrics-subscriber
USER metrics-subscriber
ENTRYPOINT ["python", "-m", "metrics_subscriber"]
