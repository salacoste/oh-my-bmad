# services/orchestrator-adapter/Dockerfile — thin override of oh-my-bmad-base:local.
# Pre-req: `just build-base` (or `docker build -f Dockerfile.base
# --target runtime-base -t oh-my-bmad-base:local .`).
#
# Story 11.3.7 / AC1 / H7g — Vendor the OMC source tree (~48MB, 5289 files)
# into this image. ``OMCRunner.__init__`` (services/orchestrator-adapter/
# .../adapters/omc_runner.py:62) validates ``omc_path`` exists at boot and
# raises ``ValueError: omc_path does not exist: upstream/omc`` otherwise.
# Default ``omc_path = "upstream/omc"`` is resolved relative to WORKDIR
# (/app); ``COPY upstream/omc /app/upstream/omc`` makes that path resolve.
#
# Decision D1-A (per-service Dockerfile, not base): only orchestrator-adapter
# needs OMC, so we keep the ~48MB blast radius in this image alone rather
# than multiplying it across all 7 service images via Dockerfile.base.
#
# Ownership: ``--chown=:omb`` + ``--chmod=755`` on the COPY directive so
# the orchestrator-adapter uid (10004) inside the omb group can read
# every file regardless of upstream's host-side ownership. Single COPY
# layer — no separate ``chgrp -R`` / ``chmod -R`` RUN step (which would
# recursively walk 5289 files and create a duplicate ~48MB metadata-only
# layer via overlayfs copy-up). 755 grants read+execute on dirs and read
# on regular files for all triads — the OMC tree is read-only at runtime
# so the broader-than-g+rX permission is harmless.
FROM oh-my-bmad-base:local
COPY --chown=:omb --chmod=755 upstream/omc /app/upstream/omc
RUN useradd --system --uid 10004 --gid omb --home /app --shell /usr/sbin/nologin orchestrator-adapter
USER orchestrator-adapter
ENTRYPOINT ["python", "-m", "orchestrator_adapter"]
