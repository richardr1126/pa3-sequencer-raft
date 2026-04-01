#!/bin/bash

set -euo pipefail

NS="kube-system"

echo "📦 Installing Traefik Helm repo..."
helm repo add traefik https://traefik.github.io/charts >/dev/null 2>&1 || true
helm repo update

echo "🚀 Installing Traefik via Helm (LoadBalancer + Ingress API)..."
helm upgrade --install traefik traefik/traefik \
  --namespace "$NS" \
  --create-namespace \
  --set service.type=LoadBalancer \
  --set providers.kubernetesIngress.enabled=true \
  --set providers.kubernetesCRD.enabled=true \
  --set ingressClass.enabled=true \
  --set ingressClass.isDefaultClass=true \
  --set ingressClass.name=traefik \
  --wait

kubectl rollout status deployment traefik -n "$NS" --timeout=5m

echo "✅ Traefik installed successfully"
