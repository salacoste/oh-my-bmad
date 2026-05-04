# Story 2.15 — null-orchestrator test fixture image.
# Multi-stage build mirroring scripts/migrator/Dockerfile:
#   Stage 1: build the workspace venv (events + registry-state are
#            installed as wheels via the repo-root pyproject + uv.lock).
#   Stage 2: slim runtime carrying only /opt/venv + the null-orchestrator
#            package, running as a non-root user.
#
# Build context: tests/fixtures/null_orchestrator/ on its own does NOT have
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
#
# ``COPY src/`` is required because ``uv sync --all-packages`` builds the
# root ``oh-my-bmad`` package (defined in the root pyproject.toml) which
# expects ``src/oh_my_bmad/__init__.py``. Without this copy, the build fails
# with ``Expected a Python module at: src/oh_my_bmad/__init__.py``.
# Story 3.5.4 AC-1: restored after Story 2.15 M3 removed it.
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
#
# Story 2.15 code-review fix M11: the Dockerfile is the SINGLE source of
# truth for the runtime user. The compose overlay's ``user:`` directive
# (which previously overrode this with 10002:10000) was dropped; the
# bind-mount permissions side handles writability via 0o777 mode on the
# pre-created host-side directories.
RUN groupadd --system --gid 10500 null-orchestrator \
 && useradd --system --uid 10500 --gid null-orchestrator --home /app \
        --shell /usr/sbin/nologin null-orchestrator
WORKDIR /app
# Copy ONLY the venv (not the workspace source — the wheels built by
# ``--no-editable`` carry the code already) from the builder stage.
COPY --from=build --chown=null-orchestrator:null-orchestrator /opt/venv /opt/venv
# Copy the null-orchestrator package directory into /app/null_orchestrator/
# so ``python -m null_orchestrator`` resolves correctly (Story 2.15
# code-review fix M1: docstring claim ``python -m null_orchestrator works``
# was previously aspirational; the package layout now matches the claim).
# The package contains __init__.py, __main__.py, and null_orchestrator.py.
COPY --chown=null-orchestrator:null-orchestrator \
     tests/fixtures/null_orchestrator/__init__.py \
     tests/fixtures/null_orchestrator/__main__.py \
     tests/fixtures/null_orchestrator/null_orchestrator.py \
     /app/null_orchestrator/
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
USER null-orchestrator
# Package-form invocation — ``python -m null_orchestrator`` runs
# ``__main__.py`` which calls :func:`null_orchestrator.main`. Equivalent to
# the prior direct-script form but matches the docstring claim and lets
# operators run ``docker run --rm null-orchestrator:latest --help`` if a
# CLI surface is added later.
ENTRYPOINT ["python", "-m", "null_orchestrator"]
