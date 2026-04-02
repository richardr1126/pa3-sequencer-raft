#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

helm uninstall marketplace -n default >/dev/null 2>&1 || true
kubectl -n default wait --for=delete pod -l app=marketplace --timeout=300s >/dev/null 2>&1 || true
kubectl -n default delete pvc --all --ignore-not-found=true >/dev/null 2>&1 || true

./marketplace/install.sh

kubectl -n default rollout status statefulset/marketplace-db-customer --timeout=15m
kubectl -n default rollout status statefulset/marketplace-db-product --timeout=15m
kubectl -n default rollout status deployment/marketplace-backend-financial --timeout=15m
kubectl -n default rollout status deployment/marketplace-backend-sellers --timeout=15m
kubectl -n default rollout status deployment/marketplace-backend-buyers --timeout=15m
