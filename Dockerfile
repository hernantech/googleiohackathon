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

# ── Dependency layer ────────────────────────────────────────────────────────
# Install third-party deps ONLY, keyed on pyproject.toml. A throwaway one-line
# package stub lets the setuptools backend build without the real source, so
# this (slow) layer's sole cache input is pyproject.toml: a source-only edit
# reuses it from cache and skips the google-genai/grpcio download+compile.
# [live] pulls google-genai so the real gemini-3.5-flash seams activate when
# GEMINI_API_KEY is set (ROADMAP Phase 3/4). Without a key it still boots stub.
COPY pyproject.toml ./
RUN mkdir -p orchestrator && touch orchestrator/__init__.py \
    && pip install ".[live]"

# ── Source layer ────────────────────────────────────────────────────────────
# Copy the real package over the stub and reinstall ONLY the package itself;
# deps are already satisfied above, so --no-deps makes this near-instant.
# bench_knowledge/ (the default bq79616 demo board profile, loaded at runtime by
# path — not part of the installed package) comes last so a board-profile edit
# re-runs nothing but a file copy.
COPY orchestrator ./orchestrator
RUN pip install --no-deps --force-reinstall .
COPY bench_knowledge ./bench_knowledge

RUN useradd --create-home --uid 10001 forge
USER forge

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["python", "-m", "uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8080"]
