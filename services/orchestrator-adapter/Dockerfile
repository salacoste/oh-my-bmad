# SCAFFOLD VERSION — Story 1.8 replaces with multi-stage Dockerfile.base.
# Build context: services/orchestrator-adapter/
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src
ENTRYPOINT ["python", "-m", "orchestrator_adapter"]
