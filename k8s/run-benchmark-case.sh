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
SELLERS_HOST="${SELLERS_HOST:-marketplace-sellers.richardr.dev}"
BUYERS_HOST="${BUYERS_HOST:-marketplace-buyers.richardr.dev}"
SELLERS_PORT="${SELLERS_PORT:-80}"
BUYERS_PORT="${BUYERS_PORT:-80}"

./k8s/helm/reinstall-marketplace.sh

# Give customer sequencer cluster time to converge before benchmark traffic starts.
sleep 20

wait_for_ingress() {
  local name="$1"
  local url="$2"
  local max_attempts=120
  local attempt=1

  while [[ "${attempt}" -le "${max_attempts}" ]]; do
    if curl -fsS --max-time 5 "${url}" >/dev/null; then
      echo "${name} ingress is ready: ${url}"
      return 0
    fi
    sleep 5
    attempt=$((attempt + 1))
  done

  echo "Timed out waiting for ${name} ingress: ${url}" >&2
  return 1
}

wait_for_ingress "sellers" "http://${SELLERS_HOST}:${SELLERS_PORT}/healthz"
wait_for_ingress "buyers" "http://${BUYERS_HOST}:${BUYERS_PORT}/healthz"

set +e
uv run python benchmark.py \
  --scenario "${SCENARIO}" \
  --runs 10 \
  --ops-per-client 1000 \
  --sellers-host "${SELLERS_HOST}" \
  --sellers-port "${SELLERS_PORT}" \
  --buyers-host "${BUYERS_HOST}" \
  --buyers-port "${BUYERS_PORT}" \
  --failure-mode "${FAILURE_MODE}" \
  --failure-platform k8s \
  --case-name "${CASE_NAME}"
RC=$?
set -e

exit "${RC}"
