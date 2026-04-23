# services/worker-wrapper/Dockerfile — Node-inclusive runtime.
# Claude Code CLI is Node-based; worker-wrapper supervises it as a subprocess.
# Per Architecture line 243: Python runtime + Node runtime via multi-stage.
# Pre-req: `just build-base`.

# Source of Node binaries + bundled npm/npx — copy only the minimum.
FROM node:lts-bookworm-slim AS node-source

FROM oh-my-bmad-base:local
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-source /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN useradd --system --uid 10005 --gid omb --home /app --shell /usr/sbin/nologin worker-wrapper
USER worker-wrapper
ENTRYPOINT ["python", "-m", "worker_wrapper"]
