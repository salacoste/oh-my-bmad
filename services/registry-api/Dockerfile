# SCAFFOLD VERSION — Story 1.8 replaces with multi-stage Dockerfile.base.
# Build context: services/registry-api/
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "-m", "registry_api"]
