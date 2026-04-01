#!/bin/bash

# Simple script to install only core ingress apps:
# - Cloudflare API token secret
# - ExternalDNS
# - Traefik (Ingress controller)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
else
  echo ".env file not found in $SCRIPT_DIR"
  echo "Copy/create one with CLOUDFLARE_API_TOKEN"
  exit 1
fi

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "CLOUDFLARE_API_TOKEN is required in helm/.env"
  exit 1
fi

echo "Starting ExternalDNS installation script..."
./external-dns/install.sh

echo "Starting Traefik installation script..."
./traefik/install.sh

echo "✅ Core apps installed successfully (secret + external-dns + traefik)"
