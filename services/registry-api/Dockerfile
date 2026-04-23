# services/registry-api/Dockerfile — thin override of oh-my-bmad-base:local.
# Pre-req: `just build-base` (or `docker build -f Dockerfile.base
# --target runtime-base -t oh-my-bmad-base:local .`).
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10001 --gid omb --home /app --shell /usr/sbin/nologin registry-api
USER registry-api
ENTRYPOINT ["python", "-m", "registry_api"]
