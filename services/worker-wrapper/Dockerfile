# services/worker-wrapper/Dockerfile — Node-inclusive runtime.
# Claude Code CLI is Node-based; worker-wrapper supervises it as a subprocess.
# Per Architecture line 243: Python runtime + Node runtime via multi-stage.
# Pre-req: `just build-base`.
#
# Symlink hack: BuildKit's `COPY --from=` dereferences symlinks, so copying
# `/usr/local/bin/npm` directly would yield a JS stub that can't find its
# siblings. We copy the real modules + recreate the wrapper symlinks ourselves.

FROM node:lts-bookworm-slim AS node-source

FROM oh-my-bmad-base:local
# Node runtime files (real files; no symlink dereferencing issue here).
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
# Recreate the binary-wrapper symlinks that COPY --from= would flatten.
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js  /usr/local/bin/npm \
 && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js  /usr/local/bin/npx \
 && ln -sf /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
 && chmod +x /usr/local/lib/node_modules/npm/bin/npm-cli.js \
              /usr/local/lib/node_modules/npm/bin/npx-cli.js \
              /usr/local/lib/node_modules/corepack/dist/corepack.js
# Non-root runtime user (shared omb group lets it write under /app).
RUN useradd --system --uid 10005 --gid omb --home /app --shell /usr/sbin/nologin worker-wrapper
USER worker-wrapper
ENTRYPOINT ["python", "-m", "worker_wrapper"]
