# services/telegram-gateway/Dockerfile — thin override of oh-my-bmad-base:local.
# Pre-req: `just build-base` (or `docker build -f Dockerfile.base
# --target runtime-base -t oh-my-bmad-base:local .`).
FROM oh-my-bmad-base:local
RUN useradd --system --uid 10003 --gid omb --home /app --shell /usr/sbin/nologin telegram-gateway
USER telegram-gateway
ENTRYPOINT ["python", "-m", "telegram_gateway"]
