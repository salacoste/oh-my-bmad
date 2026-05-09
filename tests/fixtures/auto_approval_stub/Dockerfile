# Story 5.18 — auto-approval-stub test fixture image.
# Multi-stage build mirroring scripted_worker_stub/Dockerfile.
# Build context: repo root.

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
RUN groupadd --system --gid 10600 auto-approval \
 && useradd --system --uid 10600 --gid auto-approval --home /app \
        --shell /usr/sbin/nologin auto-approval
WORKDIR /app
COPY --from=build --chown=auto-approval:auto-approval /opt/venv /opt/venv
COPY --chown=auto-approval:auto-approval \
     tests/fixtures/auto_approval_stub/__init__.py \
     tests/fixtures/auto_approval_stub/__main__.py \
     tests/fixtures/auto_approval_stub/auto_approval_stub.py \
     /app/auto_approval_stub/
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
USER auto-approval
ENTRYPOINT ["python", "-m", "auto_approval_stub"]
