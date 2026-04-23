# services/console-cli/Dockerfile — thin override of oh-my-bmad-base:local.
# Console-CLI isn't a compose service (invoked via `docker run --rm` or
# `docker compose exec`); real Typer CLI lands in Story 4.1 + host wrapper
# in Story 4.6. Today it's a hello-world parity image so every workspace
# Python service has a valid Dockerfile.
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10007 --gid omb --home /app --shell /usr/sbin/nologin console-cli
USER console-cli
ENTRYPOINT ["python", "-m", "console_cli"]
