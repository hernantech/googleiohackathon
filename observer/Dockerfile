# Forge Observer — decoupled manager dashboard. Built linux/arm64 in CI for the
# Azure Ampere VM, same as the orchestrator. Build context is THIS directory
# (observer/), NOT the repo root — it ships none of the orchestrator source, so
# the two images build independently.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl backs the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY observer ./observer

# SQLite lives here on a mounted named volume (see docker-compose.yml).
RUN mkdir -p /data && useradd --create-home --uid 10002 observer && chown observer /data
USER observer

ENV OBSERVER_DB_PATH=/data/observer.db
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8090/healthz || exit 1

CMD ["python", "-m", "uvicorn", "observer.main:app", "--host", "0.0.0.0", "--port", "8090"]
