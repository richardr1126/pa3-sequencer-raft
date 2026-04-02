# marketplace Helm chart

Single chart for PA3 services:

- db-customer StatefulSet (5)
- db-product StatefulSet (5)
- backend-sellers Deployment (4)
- backend-buyers Deployment (4)
- backend-financial Deployment (1)

Install:

```bash
cd k8s/helm
./marketplace/install.sh
```
