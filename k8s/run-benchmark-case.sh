#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 <scenario> <failure_mode>" >&2
  exit 1
fi

SCENARIO="$1"
FAILURE_MODE="$2"

CASE_NAME="scenario${SCENARIO}__${FAILURE_MODE}"

./k8s/helm/reinstall-marketplace.sh

kubectl -n default port-forward svc/marketplace-backend-sellers 8003:8003 >/dev/null 2>&1 &
PF_SELLERS_PID=$!
kubectl -n default port-forward svc/marketplace-backend-buyers 8004:8004 >/dev/null 2>&1 &
PF_BUYERS_PID=$!
cleanup_port_forward() {
  kill "${PF_SELLERS_PID}" "${PF_BUYERS_PID}" >/dev/null 2>&1 || true
  wait "${PF_SELLERS_PID}" "${PF_BUYERS_PID}" 2>/dev/null || true
}
trap cleanup_port_forward EXIT

sleep 8

set +e
uv run python benchmark.py \
  --scenario "${SCENARIO}" \
  --runs 10 \
  --ops-per-client 1000 \
  --failure-mode "${FAILURE_MODE}" \
  --failure-platform k8s \
  --case-name "${CASE_NAME}"
RC=$?
set -e

trap - EXIT
cleanup_port_forward
exit "${RC}"
