#!/usr/bin/env bash
# One-time VM bootstrap for the Forge orchestrator deploy.
# Run ONCE against galois-cloud-vm-2:
#
#   ssh -i ~/.ssh/id_ed25519 galois@20.230.188.247 'bash -s' < deploy/bootstrap-vm.sh
#
# Idempotent: safe to re-run. Installs Docker, authorizes the CI deploy key,
# and creates the deploy directory. Does NOT open any inbound port — do that
# separately (see deploy/README.md).
set -euo pipefail

# Public half of the dedicated CI deploy key (private half lives in the
# GitHub secret VM_SSH_PRIVATE_KEY). Public keys are safe to commit.
CI_DEPLOY_PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJWkD0MwP8d5LNpSOAK+NmVrAWsQ1VB5+0vkC0pdovFU forge-ci-deploy@github-actions"

echo "==> authorizing CI deploy key"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -qF "$CI_DEPLOY_PUBKEY" ~/.ssh/authorized_keys || echo "$CI_DEPLOY_PUBKEY" >> ~/.ssh/authorized_keys

echo "==> installing Docker (if missing)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

echo "==> creating deploy dir"
mkdir -p ~/forge-deploy

echo "==> versions"
docker --version
sudo docker compose version || docker compose version

echo "==> done. NOTE: log out/in (or 'newgrp docker') for group membership to apply."
