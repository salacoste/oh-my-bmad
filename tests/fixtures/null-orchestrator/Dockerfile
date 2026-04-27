# Story 2.15 — null-orchestrator test fixture image.
# Multi-stage build mirroring scripts/migrator/Dockerfile:
#   Stage 1: build the workspace venv (events + registry-state are
#            installed as wheels via the repo-root pyproject + uv.lock).
#   Stage 2: slim runtime carrying only /opt/venv + the null-orchestrator
#            script, running as a non-root user.
#
# Build context: tests/fixtures/null-orchestrator/ on its own does NOT have
# enough to resolve workspace deps — they live at the repo root. The
# tests/separability/_build_null_orchestrator.py helper builds with the
# REPO ROOT as context and -f pointed at this Dockerfile so the relative
# COPY paths reach the workspace files.
#
# Image tag: ``null-orchestrator:latest`` (consumed by the S-3 test via
# ``ORCHESTRATOR_IMAGE=null-orchestrator:latest``).

FROM python:3.12-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.11.*
WORKDIR /build
# Repo-root workspace files — the lockfile drives ``events`` + ``registry-state``
# resolution. ``--no-editable`` builds the workspace members as wheels so the
# runtime stage can drop the source tree entirely.
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
COPY services/ ./services/
COPY mcp-servers/ ./mcp-servers/
COPY src/ ./src/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --frozen --no-dev --all-packages --no-editable

FROM python:3.12-slim-bookworm AS runtime
# Dedicated unprivileged user. uid is OUTSIDE the per-service range used in
# the spine (10001-10006) so the test fixture can never be confused for a
# real platform service in audit logs.
RUN groupadd --system --gid 10500 null-orchestrator \
 && useradd --system --uid 10500 --gid null-orchestrator --home /app \
        --shell /usr/sbin/nologin null-orchestrator
WORKDIR /app
# Copy ONLY the venv (not the workspace source — the wheels built by
# ``--no-editable`` carry the code already) from the builder stage.
COPY --from=build --chown=null-orchestrator:null-orchestrator /opt/venv /opt/venv
# Copy the orchestrator script into /app so ``python /app/null_orchestrator.py``
# (or ``python -m null_orchestrator`` once the parent dir is on PYTHONPATH)
# resolves it. The script is self-contained — no other files in this fixture
# directory are needed at runtime.
COPY --chown=null-orchestrator:null-orchestrator \
     tests/fixtures/null-orchestrator/null_orchestrator.py /app/null_orchestrator.py
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER null-orchestrator
# Direct script invocation — simpler than the ``-m`` form and equivalent
# given the script's ``if __name__ == "__main__"`` guard at module bottom.
ENTRYPOINT ["python", "/app/null_orchestrator.py"]
