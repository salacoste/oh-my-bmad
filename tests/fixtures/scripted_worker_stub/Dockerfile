# Story 5.16 — scripted-worker-stub test fixture image.
# Multi-stage build mirroring tests/fixtures/null_orchestrator/Dockerfile:
#   Stage 1: build the workspace venv (events + registry-state + MCP SDK
#            are installed as wheels via the repo-root pyproject + uv.lock).
#   Stage 2: slim runtime carrying only /opt/venv + the stub package,
#            running as a non-root user.
#
# Build context: repo root (same as null-orchestrator).
# The _build_scripted_worker.py helper builds with REPO ROOT as context
# and -f pointed at this Dockerfile.

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY mcp-servers/ ./mcp-servers/
COPY src/ ./src/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --frozen --no-dev --all-packages --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10600 scripted-worker \
 && useradd --system --uid 10600 --gid scripted-worker --home /app \
        --shell /usr/sbin/nologin scripted-worker
WORKDIR /app
COPY --from=build --chown=scripted-worker:scripted-worker /opt/venv /opt/venv
COPY --chown=scripted-worker:scripted-worker \
     tests/fixtures/scripted_worker_stub/__init__.py \
     tests/fixtures/scripted_worker_stub/__main__.py \
     tests/fixtures/scripted_worker_stub/scripted_worker_stub.py \
     /app/scripted_worker_stub/
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
USER scripted-worker
ENTRYPOINT ["python", "-m", "scripted_worker_stub"]
