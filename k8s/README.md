# PA3 GKE Cluster

This folder is a simplified GKE setup for this project's services.

## Included

- `gke-cluster.py`: small cluster lifecycle manager (`create`, `list`, `scale`, `delete`)
- `helm/install-apps.sh`: installs Traefik + ExternalDNS + Marketplace chart (`helm/marketplace`)

CockroachDB and other task-specific components are intentionally not included.

## Node Pool Strategy

The cluster uses two fixed generic node pools.

Default pools:

- `pool-1`: `n4d-highcpu-2`
- `pool-2`: `c2d-highcpu-2`

Default total node count is `19`, using a fixed pool split of `10/9`, matching your target service replicas.

Your service target is `19` total replicas, so `19` nodes gives near one-pod-per-node placement.

Private-node mode is enabled by default in `gke-cluster.py`:

- Nodes are created without public IPs.
- Cloud NAT is created automatically for outbound internet access.
- This helps avoid public-IP pressure while still allowing image pulls and egress traffic.

## Prerequisites

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

Dependencies are now managed by the root project `pyproject.toml` (dev group includes GKE client libs):

```bash
uv sync --all-groups
```

## Create Cluster

```bash
# from k8s/
uv run python gke-cluster.py create --name pa3-cloud
```

Create uses fixed node pool sizes from the script (`10/9`).

## Connect kubectl

```bash
gcloud container clusters get-credentials pa3-cloud --zone us-central1-f --project YOUR_PROJECT_ID
kubectl get nodes
```

## Install Cluster Apps (Helm)

Install Traefik, ExternalDNS, and marketplace chart:

```bash
cd helm
./install-apps.sh
```

## Build and Push Images

Set your registry path first:

```bash
export REGISTRY=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/YOUR_REPO
export TAG=latest
```

From repo root:

```bash
docker build -f docker/Dockerfile.db-customer -t $REGISTRY/pa3-db-customer:$TAG .
docker build -f docker/Dockerfile.db-product -t $REGISTRY/pa3-db-product:$TAG .
docker build -f docker/Dockerfile.backend-financial -t $REGISTRY/pa3-backend-financial:$TAG .
docker build -f docker/Dockerfile.backend-sellers -t $REGISTRY/pa3-backend-sellers:$TAG .
docker build -f docker/Dockerfile.backend-buyers -t $REGISTRY/pa3-backend-buyers:$TAG .

docker push $REGISTRY/pa3-db-customer:$TAG
docker push $REGISTRY/pa3-db-product:$TAG
docker push $REGISTRY/pa3-backend-financial:$TAG
docker push $REGISTRY/pa3-backend-sellers:$TAG
docker push $REGISTRY/pa3-backend-buyers:$TAG
```

`install-apps.sh` now also deploys the marketplace Helm chart. If you need to re-deploy
after pushing new images or changing chart values:

```bash
cd helm

./marketplace/install.sh

# or via Helm directly:
helm upgrade --install marketplace ./marketplace \
  --namespace default \
  -f ./marketplace/custom-values.yaml

kubectl -n default get pods -o wide
kubectl -n default get svc
```

## Scale Cluster

Scale all pools by total node target:

```bash
uv run python gke-cluster.py scale --name pa3-cloud --nodes 19
```

Scale one pool:

```bash
uv run python gke-cluster.py scale --name pa3-cloud --pool pool-2 --nodes 7
```

## Delete Cluster

```bash
uv run python gke-cluster.py delete --name pa3-cloud
```

## End-to-End Benchmark Automation

From repo root, run:

```bash
./run-pa3-benchmarks-gke.sh
```

This script creates/connects the cluster, runs `helm/install-apps.sh`,
uses `helm/reinstall-marketplace.sh` between benchmark cases, and executes all
PA3 scenario/failure combinations against ingress hosts
`marketplace-sellers.richardr.dev` and `marketplace-buyers.richardr.dev`
with logs under
`benchmark-results/<timestamp>/`.

This script is intentionally fixed/deterministic and tears down the cluster at completion by default.
