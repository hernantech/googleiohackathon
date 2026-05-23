# Forge orchestrator. Built multi-arch in CI; the target VM is linux/arm64
# (Azure Ampere). python:3.12-slim is multi-arch so this builds natively on
# either platform.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Project metadata + source, then install. (setuptools needs the package
# directory present at build time, so source is copied before `pip install`.)
COPY pyproject.toml ./
COPY orchestrator ./orchestrator
RUN pip install .

# Drop privileges.
RUN useradd --create-home --uid 10001 forge
USER forge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["python", "-m", "uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
