#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Expected CLOUDFLARE_API_TOKEN in k8s/helm/.env" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "CLOUDFLARE_API_TOKEN is not set in k8s/.env" >&2
  exit 1
fi

helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo add external-dns https://kubernetes-sigs.github.io/external-dns/ >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "Installing Traefik..."
helm upgrade --install traefik traefik/traefik \
  --namespace kube-system \
  --set service.type=LoadBalancer \
  --wait

echo "Installing ExternalDNS..."
kubectl create secret generic cloudflare-dns \
  --namespace kube-system \
  --from-literal=cloudflare_api_token="${CLOUDFLARE_API_TOKEN}" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install external-dns external-dns/external-dns --version 1.15.2 \
  --namespace kube-system \
  --set provider=cloudflare \
  --set env[0].name=CF_API_TOKEN \
  --set env[0].valueFrom.secretKeyRef.name=cloudflare-dns \
  --set env[0].valueFrom.secretKeyRef.key=cloudflare_api_token \
  --set sources='{service,ingress}' \
  --wait

echo "Installing Marketplace chart..."
#./marketplace/install.sh

echo "Apps installed successfully (traefik, external-dns, marketplace)"
