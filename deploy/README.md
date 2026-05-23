# deploy

CI/CD for the Forge orchestrator → Azure VM `galois-cloud-vm-2`
(`westus2`, aarch64 / Ampere).

## How it works

`.github/workflows/deploy-backend.yml`:

1. **build-and-push** — builds `forge_v2/` as a `linux/arm64` image and pushes
   to `ghcr.io/hernantech/googleiohackathon-orchestrator` (`:latest` + `:<sha>`).
   Runs on every backend-touching branch push (validates PRs).
2. **deploy** — SSHes to the VM, copies `docker-compose.yml`, writes `.env`
   from secrets, `docker compose pull && up -d`, and health-checks
   `/healthz`. Runs only on `main` or manual `workflow_dispatch`.

## One-time setup

### 1. Bootstrap the VM (run locally, once)

```bash
ssh -i ~/.ssh/id_ed25519 galois@20.230.188.247 'bash -s' < deploy/bootstrap-vm.sh
```

Installs Docker, authorizes the CI deploy key, creates `~/forge-deploy`.

### 2. GitHub secrets (already set via `gh secret set`)

| Secret | Purpose |
|---|---|
| `VM_HOST` | VM public IP (`20.230.188.247`) |
| `VM_USER` | SSH user (`galois`) |
| `VM_SSH_PRIVATE_KEY` | private half of the dedicated CI deploy key |
| `GEMINI_API_KEY` | Gemini Live / SME models |
| `MANAGED_AGENTS_API_KEY` | *(optional)* SME sandboxes; unset → stub mode |

### 3. Open the public port (separate, deliberate step)

The orchestrator listens on `8080`. To reach it from outside the VM (e.g. the
phone client), open the NSG port:

```bash
az vm open-port --resource-group GALOIS-CLOUD-RG --name galois-cloud-vm-2 --port 8080 --priority 1010
```

Until then, the deploy still works and health-checks pass *inside* the VM.
Prefer fronting `8080` with TLS before exposing it broadly.

## Manual deploy / rollback

```bash
gh workflow run deploy-backend.yml --ref main          # redeploy latest main
ssh galois@20.230.188.247 'cd forge-deploy && ORCHESTRATOR_IMAGE=...:<sha> docker compose up -d'
```
