# SCAFFOLD VERSION — Story 1.8 replaces with multi-stage Dockerfile.base.
# Build context: services/registry-api/
FROM python:3.12-slim-bookworm
# Non-root runtime per Story 1.3 pattern. Service users share a low numeric UID
# range (10001-10006) so bind-mounted data dirs have consistent ownership on
# macOS (Docker Desktop) regardless of which service writes first.
RUN groupadd --system --gid 10000 omb \
 && useradd --system --uid 10001 --gid omb --home /app --shell /usr/sbin/nologin registry-api
WORKDIR /app
COPY src/ /app/src/
RUN chown -R registry-api:omb /app
ENV PYTHONPATH=/app/src
USER registry-api
ENTRYPOINT ["python", "-m", "registry_api"]
