# services/orchestrator-adapter/Dockerfile — thin override of oh-my-bmad-base:local.
# Pre-req: `just build-base` (or `docker build -f Dockerfile.base
# --target runtime-base -t oh-my-bmad-base:local .`).
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10004 --gid omb --home /app --shell /usr/sbin/nologin orchestrator-adapter
USER orchestrator-adapter
ENTRYPOINT ["python", "-m", "orchestrator_adapter"]
