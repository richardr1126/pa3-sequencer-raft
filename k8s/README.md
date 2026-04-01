# PA3 GKE Cluster

This folder is a simplified GKE setup for this project's services.

## Included

- `gke-cluster.py`: small cluster lifecycle manager (`create`, `list`, `scale`, `delete`)
- `helm/install-apps.sh`: installs only:
  - Cloudflare token secret
  - ExternalDNS
  - Traefik (Ingress controller with LoadBalancer service)
- `helm/marketplace/marketplace.yaml`: Kubernetes resources for:
  - `db-customer` (5 replicas)
  - `db-product` (5 replicas)
  - `backend-sellers` (4 replicas)
  - `backend-buyers` (4 replicas)
  - `backend-financial` (1 replica)

CockroachDB and other task-specific components are intentionally not included.

## Node Pool Strategy

The cluster uses multiple generic node pools with different machine families to reduce the chance of hitting one specific machine-family quota.

Default pools:

- `pool-1`: `e2-standard-2`
- `pool-2`: `t2d-standard-2`
- `pool-3`: `n2d-standard-2`

Default total node count is `19`, using a fixed pool split of `7/6/6`, matching your current target service replicas.

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
python gke-cluster.py create --name pa3-cloud
```

Create uses fixed node pool sizes from the script (`7/6/6`).

## Connect kubectl

```bash
gcloud container clusters get-credentials pa3-cloud --zone us-central1-b --project YOUR_PROJECT_ID
kubectl get nodes
```

## Install Cluster Apps (Helm)

The file `helm/.env` is expected and should contain at least:

```bash
CLOUDFLARE_API_TOKEN=...
```

Install external-dns + secret + traefik:

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

Update image names and ingress hosts in `helm/marketplace/marketplace.yaml`, then deploy:

```bash
cd helm
./marketplace/install.sh
kubectl -n pa3 get pods -o wide
kubectl -n pa3 get svc
kubectl -n pa3 get ingress
```

## Scale Cluster

Scale all pools by total node target:

```bash
python gke-cluster.py scale --name pa3-cloud --nodes 19
```

Scale one pool:

```bash
python gke-cluster.py scale --name pa3-cloud --pool pool-2 --nodes 7
```

## Delete Cluster

```bash
python gke-cluster.py delete --name pa3-cloud
```
