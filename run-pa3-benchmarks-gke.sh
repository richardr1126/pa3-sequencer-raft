#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

RESULTS_DIR="benchmark-results/$(date +%Y%m%d-%H%M%S)"
SUMMARY_FILE="${RESULTS_DIR}/summary.tsv"
mkdir -p "${RESULTS_DIR}"
printf "case\tscenario\tfailure_mode\texit_code\tlog_file\n" > "${SUMMARY_FILE}"

uv run python k8s/gke-cluster.py create && \
rm -rf ~/.kube/config && \
gcloud container clusters get-credentials pa3-cloud --zone us-central1-b && \
cd k8s/helm && \
./install-apps.sh && \
cd ../.. && \
./k8s/run-benchmark-case.sh 1 none "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 2 none "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 3 none "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 1 backend-sellers-buyers "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 2 backend-sellers-buyers "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 3 backend-sellers-buyers "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 1 product-follower "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 2 product-follower "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 3 product-follower "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 1 product-leader "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 2 product-leader "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
./k8s/run-benchmark-case.sh 3 product-leader "${RESULTS_DIR}" "${SUMMARY_FILE}" && \
uv run python k8s/gke-cluster.py delete

echo "Done: ${SUMMARY_FILE}"
