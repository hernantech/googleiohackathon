# Forge orchestrator (ROADMAP Phase 2). Built linux/arm64 in CI for the Azure
# Ampere VM. Build context is the repo root; package is `orchestrator` with the
# bundled board profile under bench_knowledge/.
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

# Project metadata + source (setuptools needs the package present at build),
# then install. bench_knowledge/ ships the default bq79616 demo board profile.
COPY pyproject.toml ./
COPY orchestrator ./orchestrator
COPY bench_knowledge ./bench_knowledge
# [live] pulls google-genai so the real gemini-3.5-flash seams activate when
# GEMINI_API_KEY is set (ROADMAP Phase 3/4). Without a key it still boots stub.
RUN pip install ".[live]"

RUN useradd --create-home --uid 10001 forge
USER forge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["python", "-m", "uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
