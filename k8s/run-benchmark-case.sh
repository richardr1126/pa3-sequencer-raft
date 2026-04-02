#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 4 ]]; then
  echo "usage: $0 <scenario> <failure_mode> <results_dir> <summary_file>" >&2
  exit 1
fi

SCENARIO="$1"
FAILURE_MODE="$2"
RESULTS_DIR="$3"
SUMMARY_FILE="$4"

CASE_NAME="scenario${SCENARIO}__${FAILURE_MODE}"
CASE_LOG="${RESULTS_DIR}/${CASE_NAME}.log"

./k8s/helm/reinstall-marketplace.sh

kubectl -n default port-forward svc/marketplace-backend-sellers 8003:8003 >/dev/null 2>&1 &
PF_SELLERS_PID=$!
kubectl -n default port-forward svc/marketplace-backend-buyers 8004:8004 >/dev/null 2>&1 &
PF_BUYERS_PID=$!

sleep 8

set +e
uv run python benchmark.py \
  --scenario "${SCENARIO}" \
  --runs 10 \
  --ops-per-client 1000 \
  --failure-mode "${FAILURE_MODE}" \
  --failure-platform k8s \
  --log-file "${CASE_LOG}"
RC=$?
set -e

kill "${PF_SELLERS_PID}" "${PF_BUYERS_PID}" >/dev/null 2>&1 || true
wait "${PF_SELLERS_PID}" "${PF_BUYERS_PID}" 2>/dev/null || true

printf "%s\t%s\t%s\t%s\t%s\n" "${CASE_NAME}" "${SCENARIO}" "${FAILURE_MODE}" "${RC}" "${CASE_LOG}" >> "${SUMMARY_FILE}"

exit 0
