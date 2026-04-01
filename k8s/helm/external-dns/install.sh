#!/bin/bash

set -euo pipefail

NS="kube-system"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "CLOUDFLARE_API_TOKEN must be exported before running this script"
  exit 1
fi

echo "📦 Ensuring namespace exists: $NS"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f -

echo "🔐 Creating Cloudflare DNS secret for ExternalDNS..."
kubectl create secret generic cloudflare-dns \
  --namespace "$NS" \
  --from-literal=cloudflare_api_token="$CLOUDFLARE_API_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "📦 Installing ExternalDNS Helm repo..."
helm repo add external-dns https://kubernetes-sigs.github.io/external-dns/ >/dev/null 2>&1 || true
helm repo update

echo "🚀 Installing ExternalDNS via Helm..."
helm upgrade --install external-dns external-dns/external-dns --version 1.15.2 \
  --namespace "$NS" \
  --set provider=cloudflare \
  --set env[0].name=CF_API_TOKEN \
  --set env[0].valueFrom.secretKeyRef.name=cloudflare-dns \
  --set env[0].valueFrom.secretKeyRef.key=cloudflare_api_token \
  --set sources='{service,ingress,gateway-httproute,gateway-grpcroute,gateway-tlsroute}' \
  --wait

kubectl rollout status deployment external-dns -n "$NS" --timeout=5m

echo "✅ ExternalDNS installed successfully"
