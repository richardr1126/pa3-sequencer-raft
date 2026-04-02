#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

BENCHMARK_RUN_ID="$(date +%Y%m%d-%H%M%S)"
export BENCHMARK_RUN_ID

uv run python k8s/gke-cluster.py create
rm -rf ~/.kube/config
gcloud container clusters get-credentials pa3-cloud --zone us-central1-b
cd k8s/helm
./install-apps.sh
cd ../..
./k8s/run-benchmark-case.sh 1 none
./k8s/run-benchmark-case.sh 2 none
./k8s/run-benchmark-case.sh 3 none
./k8s/run-benchmark-case.sh 1 backend-sellers-buyers
./k8s/run-benchmark-case.sh 2 backend-sellers-buyers
./k8s/run-benchmark-case.sh 3 backend-sellers-buyers
./k8s/run-benchmark-case.sh 1 product-follower
./k8s/run-benchmark-case.sh 2 product-follower
./k8s/run-benchmark-case.sh 3 product-follower
./k8s/run-benchmark-case.sh 1 product-leader
./k8s/run-benchmark-case.sh 2 product-leader
./k8s/run-benchmark-case.sh 3 product-leader
uv run python k8s/gke-cluster.py delete

echo "Done. Logs: benchmark-results/${BENCHMARK_RUN_ID}"
