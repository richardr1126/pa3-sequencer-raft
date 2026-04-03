# Performance Report (PA3)

The benchmark script is `benchmark.py`. It uses `clients/common/client.py` to perform API operations against services deployed on GKE Kubernetes.

## Experiment Setup

- Customer DB StatefulSet: `databases/customer/main.py` (gRPC `8001`, sequencer UDP `12001`)
- Product DB StatefulSet: `databases/product/main.py` (gRPC `11301`, Raft `11001`)
- Backend sellers Deployment: `backends/sellers/main.py` (HTTP `8003`)
- Backend buyers Deployment: `backends/buyers/main.py` (HTTP `8004`)
- Backend financial Deployment: `backends/financial-transaction/main.py` (HTTP `8005`)

### Hardware / Environment

- GKE cluster in `us-central1-f` with 19 total nodes.
- Node pools (current script defaults): `pool-1` = `n4d-highcpu-2`, `pool-2` = `c2d-highcpu-2`.
- Benchmark client runs outside the cluster and sends HTTP traffic through Traefik ingress + ExternalDNS domains:
  - `marketplace-sellers.richardr.dev`
  - `marketplace-buyers.richardr.dev`
- Inter-service traffic (backend to DB / backend to backend-financial) stays in-cluster via Kubernetes Services.
- Python version: 3.13.11

### How the benchmark was run

```bash
# Full automation (create cluster -> run all scenarios/failure modes -> delete cluster)
./run-pa3-benchmarks-gke.sh

# Single benchmark case
./k8s/run-benchmark-case.sh <scenario> <failure_mode>
# failure_mode: none | backend-sellers-buyers | product-follower | product-leader
```

### Buyer/Seller Run Definitions

- Each run performs 1000 API operations per client/user.
- Setup (create account/login/seed items) is excluded from the 1000 ops and latency stats.
- Setup is included in the run wall-clock duration used for throughput.
- Marketplace Helm release is reinstalled and PVCs are deleted before each benchmark case (`scenarioX__failure_mode`).
- Within a scenario, the 10 runs are performed on the same release.
- `latency`: average latency across all successful operations in the run
- `p50`: median latency across all successful operations in the run
- `p99`: 99th percentile latency across all successful operations in the run
- `throughput`: total successful operations / wall-clock duration

#### API ops distribution

The runs use a fixed operation mix (shuffled per run):

Seller run:
- 5 setup calls (CreateAccount, Login, RegisterItem x3)
- 400 DisplayItemsForSale calls
- 300 GetSellerRating calls
- 150 ChangeItemPrice calls
- 150 UpdateUnitsForSale calls

Buyer run:
- 2 setup calls (CreateAccount, Login)
- 500 SearchItemsForSale calls
- 200 DisplayCart calls
- 150 AddItemToCart calls
- 100 RemoveItemFromCart calls
- 50 GetBuyerPurchases calls

## Results

### Scenario 1: 1 seller + 1 buyer
#### Failure mode: none
```plaintext
Run 1: ops=2000/2000, latency=67.82ms (p50=47.12ms, p99=257.98ms), throughput=24.5 ops/s, duration=81.61s
Run 2: ops=2000/2000, latency=70.09ms (p50=48.86ms, p99=262.60ms), throughput=24.0 ops/s, duration=83.38s
Run 3: ops=2000/2000, latency=71.41ms (p50=50.63ms, p99=216.66ms), throughput=24.2 ops/s, duration=82.60s
Run 4: ops=2000/2000, latency=73.76ms (p50=52.55ms, p99=255.78ms), throughput=23.3 ops/s, duration=85.73s
Run 5: ops=2000/2000, latency=74.91ms (p50=52.96ms, p99=268.89ms), throughput=22.8 ops/s, duration=87.76s
```

```plaintext
Summary (5 runs):
    Avg Response Time: 71.60 ms (stddev: 2.84 ms)
    Avg Throughput: 23.8 ops/s (stddev: 0.7 ops/s)
    Seller Avg Latency: 83.40 ms
    Buyer Avg Latency: 59.80 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 82.03 ms (samples=714)
      ChangeItemPrice: 170.75 ms (samples=750)
      DisplayCart: 67.30 ms (samples=1038)
      DisplayItemsForSale: 46.89 ms (samples=2000)
      GetBuyerPurchases: 61.21 ms (samples=250)
      GetSellerRating: 44.61 ms (samples=1500)
      RemoveItemFromCart: 80.33 ms (samples=462)
      SearchItemsForSale: 46.59 ms (samples=2536)
      UpdateUnitsForSale: 171.00 ms (samples=750)
```

Explanation:
- With only 1 seller and 1 buyer and no failure triggers, the system average latency has jumped up around 20ms compared to PA2

#### Failure mode: backend-sellers-buyers
```plaintext
Run 1: ops=2000/2000, latency=81.75ms (p50=46.91ms, p99=224.69ms), throughput=23.7 ops/s, duration=84.48s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 2: ops=2000/2000, latency=83.02ms (p50=47.35ms, p99=240.39ms), throughput=23.3 ops/s, duration=85.88s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 3: ops=2000/2000, latency=85.53ms (p50=50.04ms, p99=229.04ms), throughput=22.5 ops/s, duration=89.02s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 4: ops=2000/2000, latency=87.48ms (p50=51.52ms, p99=226.02ms), throughput=21.8 ops/s, duration=91.92s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 5: ops=2000/2000, latency=89.49ms (p50=53.41ms, p99=232.54ms), throughput=21.3 ops/s, duration=93.99s
    Failure injection: injected=True delay=5.00s exit_code=0
```

```plaintext
Summary (5 runs):
    Avg Response Time: 85.45 ms (stddev: 3.16 ms)
    Avg Throughput: 22.5 ops/s (stddev: 1.0 ops/s)
    Seller Avg Latency: 82.14 ms
    Buyer Avg Latency: 88.76 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 202.60 ms (samples=738)
      ChangeItemPrice: 170.14 ms (samples=750)
      DisplayCart: 94.38 ms (samples=1019)
      DisplayItemsForSale: 45.73 ms (samples=2000)
      GetBuyerPurchases: 59.42 ms (samples=250)
      GetSellerRating: 43.51 ms (samples=1500)
      RemoveItemFromCart: 79.59 ms (samples=481)
      SearchItemsForSale: 57.71 ms (samples=2512)
      UpdateUnitsForSale: 168.54 ms (samples=750)
```

Explanation:
- 

#### Failure mode: product-follower
```plaintext
Run 1: ops=2000/2000, latency=91.30ms (p50=47.80ms, p99=255.12ms), throughput=19.0 ops/s, duration=105.00s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 2: ops=2000/2000, latency=148.04ms (p50=48.51ms, p99=253.25ms), throughput=12.5 ops/s, duration=160.28s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 3: ops=2000/2000, latency=132.91ms (p50=51.11ms, p99=319.93ms), throughput=13.5 ops/s, duration=147.93s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 4: ops=2000/2000, latency=153.59ms (p50=52.46ms, p99=257.51ms), throughput=12.1 ops/s, duration=164.72s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 5: ops=2000/2000, latency=134.85ms (p50=53.77ms, p99=266.44ms), throughput=13.6 ops/s, duration=147.05s
    Failure injection: injected=True delay=5.00s exit_code=0
```

```plaintext
Summary (5 runs):
    Avg Response Time: 132.14 ms (stddev: 24.44 ms)
    Avg Throughput: 14.2 ops/s (stddev: 2.8 ops/s)
    Seller Avg Latency: 144.17 ms
    Buyer Avg Latency: 120.10 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 109.36 ms (samples=732)
      ChangeItemPrice: 276.14 ms (samples=750)
      DisplayCart: 221.86 ms (samples=1025)
      DisplayItemsForSale: 119.02 ms (samples=2000)
      GetBuyerPurchases: 60.46 ms (samples=250)
      GetSellerRating: 45.76 ms (samples=1500)
      RemoveItemFromCart: 79.39 ms (samples=475)
      SearchItemsForSale: 95.40 ms (samples=2518)
      UpdateUnitsForSale: 276.11 ms (samples=750)
```

Explanation:
- 

#### Failure mode: product-leader
```plaintext
Run 1: ops=2000/2000, latency=140.07ms (p50=97.22ms, p99=302.84ms), throughput=13.1 ops/s, duration=152.42s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 2: ops=2000/2000, latency=118.86ms (p50=98.33ms, p99=278.87ms), throughput=15.3 ops/s, duration=130.87s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 3: ops=2000/2000, latency=119.45ms (p50=99.77ms, p99=261.50ms), throughput=15.3 ops/s, duration=130.91s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 4: ops=2000/2000, latency=122.83ms (p50=100.86ms, p99=308.22ms), throughput=14.8 ops/s, duration=134.78s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 5: ops=2000/2000, latency=122.48ms (p50=102.22ms, p99=256.22ms), throughput=15.1 ops/s, duration=132.66s
    Failure injection: injected=True delay=5.00s exit_code=0
```

```plaintext
Summary (5 runs):
    Avg Response Time: 124.74 ms (stddev: 8.75 ms)
    Avg Throughput: 14.7 ops/s (stddev: 0.9 ops/s)
    Seller Avg Latency: 135.20 ms
    Buyer Avg Latency: 114.27 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 132.08 ms (samples=734)
      ChangeItemPrice: 215.12 ms (samples=750)
      DisplayCart: 117.71 ms (samples=1020)
      DisplayItemsForSale: 106.04 ms (samples=2000)
      GetBuyerPurchases: 110.72 ms (samples=250)
      GetSellerRating: 93.13 ms (samples=1500)
      RemoveItemFromCart: 130.63 ms (samples=480)
      SearchItemsForSale: 104.92 ms (samples=2516)
      UpdateUnitsForSale: 217.18 ms (samples=750)
```

Explanation:
- 

### Scenario 2: 10 sellers + 10 buyers
#### Failure mode: none
```plaintext

```

```plaintext

```

Explanation:
- In this scenario there are 20 concurrent users making requests (20k ops per run), and with only 1 writer in SQLite, write requests start to queue up through the SQLAlchemy connection pool or by waiting for the lock to be released by SQLite, causing increased latency and reduced throughput.
- The increase in throughput is likely due to the fact that with more clients, the services are able to process more operations in parallel, even though each operation takes longer on average. The services are likely able to keep their resources more fully utilized with more concurrent clients and more in-flight requests, which can lead to higher overall throughput despite the increased latency. This contrasts with the PA1 scenario 2 results where the throughput decreased compared to scenario 1, likely due to the fact that all processes were running on the same server.

#### Failure mode: backend-sellers-buyers
```plaintext

```

```plaintext

```

Explanation:
- 

#### Failure mode: product-follower
```plaintext

```

```plaintext

```

Explanation:
- 

#### Failure mode: product-leader
```plaintext

```

```plaintext

```

Explanation:
- 

### Scenario 3: 100 sellers + 100 buyers
#### Failure mode: none
```plaintext

```

```plaintext

```

Explanation:
- 200 concurrent users making 200k total simultaneous requests per run causes significant contention for the single SQLite writer lock, leading to very high latencies and very low throughput.
- The Cloud PA2 version of scenario 3 is much worse than the PA1 version of scenario 3. Again, this is mostly due to the fact that in PA1 there was no network latency and the database was not another network round trip.

#### Failure mode: backend-sellers-buyers
```plaintext

```

```plaintext

```

Explanation:
- 

#### Failure mode: product-follower
```plaintext

```

```plaintext

```

Explanation:
- 

#### Failure mode: product-leader
```plaintext

```

```plaintext

```

Explanation:
- 

## Bottlenecks observed
- The main bottleneck now seems to be mostly network latency and possibly somewhat slower GCP e2-standard-2 CPUs.
- With high concurrency required by Scenario 3, SQLite becomes the main bottleneck due to SQLite's 1 writer write lock contention.
- PostgreSQL could improve performance allowing concurrent writes, however since future assignments will require serializing writes through Raft or Paxos I am guessing, concurrent writes won't be used anyway.

## PA1 vs PA2 comparison

It is hard to directly compare PA1 and PA2 results for many reasons, most notably the difference in running on a loopback interface vs on separate VMs across a network. Another smaller note is I am not sure the performance differences in my M4 Pro CPU compared to the GCP VMs I am using for PA2. However, I can make some observations based on the results I have.

The main observation is obviously the addition of network latency in PA2, which is expected to add significant latency compared to the loopback interface used in PA1. The increase in latency from around 1ms in PA1 to around 55ms in PA2 for scenario 1 is mostly due to the network.

After doing just a comparison of scenario 1 locally (results not shown), I observed that with FastAPI and gRPC libraries, the base latency per request increased from around 1ms to 4ms. This is likely due to the overhead of the additional libraries FastAPI, Uvicorn, gRPC, and httpx, compared to much simpler TCP style RPC calls in PA1. This is surprising to me because I expected the optimized libraries to have less overhead, but it seems that the additional features and abstractions they provide come with some cost in terms of request processing time.

Something else I observed when comparing PA1 and PA2 results is that both throughput and latency increased, while with PA1 only latency increased and throughput decreased. I think this can be explained by PA2 now having the network acting as a sort of "throttle" that limits the number of requests that can be processed in parallel, which can help to keep the services more fully utilized and increase throughput, even though each request takes longer on average. Because of this, scenario 2 with 20 users seems to be the sweet spot for maximizing throughput while keeping latency at a reasonable level, while scenario 3 with 200 users causes too much contention and queuing at the SQLite layer, leading to very high latency and reduced throughput.

## PA2 vs PA3 comparison
