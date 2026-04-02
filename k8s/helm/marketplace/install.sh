#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

helm upgrade --install marketplace "${SCRIPT_DIR}" \
  --namespace default \
  --values "${SCRIPT_DIR}/custom-values.yaml"

echo "marketplace chart deployed"
