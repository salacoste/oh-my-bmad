# Story 1.3: migrator one-shot container.
# Multi-stage build: uv installs dependencies in the build stage; runtime
# stage carries only the venv + source.

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
# Migrator has no runtime deps (stdlib only), so `uv sync` is trivial but
# still creates the .venv layout the runtime stage expects.
RUN uv sync --no-dev

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["python", "-m", "migrator"]
