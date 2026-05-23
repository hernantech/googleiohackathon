# forge_v2 — orchestrator

FastAPI + LangGraph orchestrator for Forge. See [`../specs`](../specs) for the
full design; this directory currently holds the **connection-layer scaffold**
(health + chat-bus handshake). Graph nodes, SME dispatch, and the chat bus fill
in under `orchestrator/`.

## Run locally

```bash
cd forge_v2
pip install -e .
python -m orchestrator.main          # serves on :8080
curl localhost:8080/healthz
```

Comes up cleanly with zero env vars (stub mode). Copy `.env.example` → `.env`
to configure integrations.

## Container

```bash
docker build -t forge-orchestrator .
docker run --rm -p 8080:8080 forge-orchestrator
```

## Deploy

CI builds a `linux/arm64` image, pushes it to GHCR, and deploys to the Azure VM
over SSH. See [`../deploy`](../deploy) and
[`../.github/workflows/deploy-backend.yml`](../.github/workflows/deploy-backend.yml).
