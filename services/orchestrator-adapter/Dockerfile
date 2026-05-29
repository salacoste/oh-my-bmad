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
# Ownership: omb group + setgid 2775 inherited from /app, plus an explicit
# ``chgrp -R omb`` so the orchestrator-adapter uid (10004) inside the omb
# group can read every file regardless of upstream's host-side ownership.
FROM oh-my-bmad-base:local
COPY upstream/omc /app/upstream/omc
RUN chgrp -R omb /app/upstream/omc \
 && chmod -R g+rX /app/upstream/omc
RUN useradd --system --uid 10004 --gid omb --home /app --shell /usr/sbin/nologin orchestrator-adapter
USER orchestrator-adapter
ENTRYPOINT ["python", "-m", "orchestrator_adapter"]
