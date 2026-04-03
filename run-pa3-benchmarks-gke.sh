#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

BENCHMARK_RUN_ID="$(date +%Y%m%d-%H%M%S)"
export BENCHMARK_RUN_ID

# uv run python k8s/gke-cluster.py create

# STATUS="$(gcloud container clusters describe pa3-cloud --zone us-central1-f --format='value(status)')"
# if [[ "${STATUS}" != "RUNNING" ]]; then
#   echo "Cluster pa3-cloud is not RUNNING (status=${STATUS}). Aborting." >&2
#   exit 1
# fi

# rm -rf ~/.kube/config
# gcloud container clusters get-credentials pa3-cloud --zone us-central1-f
# ./k8s/helm/install-apps.sh
# ./k8s/run-benchmark-case.sh 1 none
# ./k8s/run-benchmark-case.sh 1 backend-sellers-buyers
# ./k8s/run-benchmark-case.sh 1 product-follower
# ./k8s/run-benchmark-case.sh 1 product-leader
# ./k8s/run-benchmark-case.sh 2 none
# ./k8s/run-benchmark-case.sh 2 backend-sellers-buyers
# ./k8s/run-benchmark-case.sh 2 product-follower
# ./k8s/run-benchmark-case.sh 2 product-leader
./k8s/run-benchmark-case.sh 3 none
./k8s/run-benchmark-case.sh 3 backend-sellers-buyers
./k8s/run-benchmark-case.sh 3 product-follower
./k8s/run-benchmark-case.sh 3 product-leader
uv run python k8s/gke-cluster.py delete

echo "Done. Logs: benchmark-results/${BENCHMARK_RUN_ID}"
