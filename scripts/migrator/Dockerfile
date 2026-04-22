# Story 1.3: migrator one-shot container.
# Multi-stage build: uv installs dependencies in the build stage; runtime
# stage carries only the venv + source and runs as a non-root user.

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
# Migrator has no runtime deps (stdlib only), so `uv sync` is trivial but
# still creates the .venv layout the runtime stage expects.
RUN uv sync --no-dev

FROM python:3.12-slim-bookworm AS runtime
# Create a dedicated unprivileged user for the migrator process.
# Container security: never run as root even for one-shot operator tools —
# container scanners flag this, and a compromised supply chain shouldn't
# land root execution inside the platform data volume.
RUN groupadd --system migrator && useradd --system --gid migrator --home /app migrator
WORKDIR /app
COPY --from=build --chown=migrator:migrator /app /app
ENV PATH="/app/.venv/bin:$PATH"
USER migrator
ENTRYPOINT ["python", "-m", "migrator"]
