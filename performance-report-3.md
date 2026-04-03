# Performance Report (PA3)

This report summarizes PA3 performance measurements from `benchmark.py` against the Kubernetes deployment of the marketplace distributed system.

## Experiment Setup

- Customer DB StatefulSet: `databases/customer/main.py` (gRPC `8001`, sequencer UDP `12001`)
- Product DB StatefulSet: `databases/product/main.py` (gRPC `11301`, Raft `11001`)
- Backend sellers Deployment: `backends/sellers/main.py` (HTTP `8003`)
- Backend buyers Deployment: `backends/buyers/main.py` (HTTP `8004`)
- Backend financial Deployment: `backends/financial-transaction/main.py` (HTTP `8005`)

### Hardware / Environment

- Cloud platform: GCP GKE, zone `us-central1-f`
- Cluster nodes: 19 total, machine type `c2d-highcpu-2`, w/ 2 CPU, 4 GB RAM per node
- Node boot disk: `pd-standard` (30 GB)
- Benchmark driver runs outside cluster and sends HTTP traffic through Traefik ingress + ExternalDNS domains:
  - `marketplace-sellers.richardr.dev`
  - `marketplace-buyers.richardr.dev`
- Inter-service traffic (backend to DB / backend to backend-financial) stays in-cluster via Kubernetes Services.
- Python version: 3.13.11

## Benchmark Method

### Scenarios and run counts

- Scenario 1: 1 seller + 1 buyer
- Scenario 2: 10 sellers + 10 buyers
- Scenario 3: 100 sellers + 100 buyers
- Failure modes per scenario: `none`, `backend-sellers-buyers`, `product-follower`, `product-leader`
- This report uses 5 runs per scenario/failure-mode pair for cost optimization and to cover the expanded matrix of 8 additional scenario/mode runs.

### Buyer/Seller Run Definitions

- 2k total ops for scenario 1, 20k total ops for scenario 2, 200k total ops for scenario 3.
- Each run performs 1000 API operations per client/user.
- Setup (create account/login/seed items) is excluded from the 1000 ops and latency stats.
- Setup is included in run wall-clock duration used for throughput.
- Before each benchmark scenario (`scenarioX__failure_mode`), the chart is reinstalled and PVCs are deleted.
- Runs within the same scenario execute against the same install.
- `latency`: average latency across all successful operations in the run
- `p50`: median latency across all successful operations in the run
- `p99`: 99th percentile latency across all successful operations in the run
- `throughput`: total successful operations / wall-clock duration

### Failure injection behavior

- Failure is injected once per run, after a fixed delay from run start.
- `backend-sellers-buyers`: deletes one sellers pod and one buyers pod.
- `product-follower`: detects a current non-leader product DB pod and deletes it.
- `product-leader`: detects current product DB leader pod and deletes it.

### API ops distribution

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

### Experiment execution

```bash
# Manual setup (cluster + kubeconfig + base apps)
uv run python k8s/gke-cluster.py create
gcloud container clusters get-credentials pa3-cloud --zone us-central1-f

# Install apps, currently does not install marketplace chart, just Traefik and ExternalDNS
./k8s/helm/install-apps.sh

# Reinstall marketplace chart for a fresh benchmark scenario
./k8s/helm/reinstall-marketplace.sh

# Direct benchmark invocation (manual)
uv run python benchmark.py \
  --scenario 1 \
  --runs 5 \
  --ops-per-client 1000 \
  --failure-mode product-leader \
  --failure-platform k8s \
  --sellers-host marketplace-sellers.richardr.dev \
  --buyers-host marketplace-buyers.richardr.dev \
  --sellers-port 80 \
  --buyers-port 80
```

```bash
# scenario helper wrapper
./k8s/run-benchmark-case.sh <scenario> <failure_mode>
# failure_mode: none | backend-sellers-buyers | product-follower | product-leader

# Full automation (simple one-command flow)
./run-pa3-benchmarks-gke.sh
```

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
- Replication of the 2 DBs, especially the Customer DB, causes real latency spikes. On average, latency increases from the added overhead of replication itself.
- In the Raft-based Product DB, replicating across 5 replicas means write operations need consensus from a majority of nodes (not all nodes), requiring more network calls. Read operations are allowed to be eventually consistent by reading the committed SQLite database on each node directly.
- In the UDP Sequencer-based Customer DB, replicating across 5 replicas means all requests (reads/writes) are sequenced before being applied, which adds network calls for sequencing/progress/retransmit traffic. Delivery is based on majority progress, not full agreement from all 5 replicas.
- As we saw with PA2, a large part of average latency is still coming from real network latency.

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
- This mode stays relatively close to `none`, but with a noticeable latency increase.
- Deleting one buyers pod and one sellers pod causes short retry/reconnect periods while those backend replicas restart.
- Kubernetes recovers these stateless backend pods quickly, so the degradation is temporary compared to DB failure modes.

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
- Killing a Product DB follower increases variance and reduces throughput compared to `none`.
- Even though quorum is still available, follower restart/catch-up adds extra delay, especially for product write-heavy operations.
- The lower p50 with a much higher average shows long-tail behavior, where many requests are still fast but recovery windows create slower spikes.

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
- Killing the Product DB leader causes the expected Raft leader re-election overhead.
- This mode has higher latency and lower throughput than `none` because write traffic waits for leadership failover and recovery.
- After failover, the system stabilizes again, which is why variance is not as extreme as the run-to-run drift seen in Scenario 2.

### Scenario 2: 10 sellers + 10 buyers
#### Failure mode: none
```plaintext
Run 1: ops=20000/20000, latency=238.89ms (p50=215.35ms, p99=674.61ms), throughput=76.2 ops/s, duration=262.38s
Run 2: ops=20000/20000, latency=436.27ms (p50=398.04ms, p99=1307.44ms), throughput=39.4 ops/s, duration=507.13s
Run 3: ops=20000/20000, latency=687.91ms (p50=630.34ms, p99=2143.35ms), throughput=24.9 ops/s, duration=803.16s
Run 4: ops=20000/20000, latency=1109.38ms (p50=983.83ms, p99=3600.21ms), throughput=15.0 ops/s, duration=1337.03s
Run 5: ops=20000/20000, latency=1528.44ms (p50=1364.16ms, p99=5042.01ms), throughput=11.3 ops/s, duration=1771.77s
```

```plaintext
Summary (5 runs):
    Avg Response Time: 800.18 ms (stddev: 521.07 ms)
    Avg Throughput: 33.4 ops/s (stddev: 26.3 ops/s)
    Seller Avg Latency: 674.72 ms
    Buyer Avg Latency: 925.63 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 1559.41 ms (samples=7481)
      ChangeItemPrice: 765.86 ms (samples=7500)
      DisplayCart: 1031.16 ms (samples=10083)
      DisplayItemsForSale: 640.39 ms (samples=20000)
      GetBuyerPurchases: 1026.76 ms (samples=2500)
      GetSellerRating: 634.38 ms (samples=15000)
      RemoveItemFromCart: 1548.62 ms (samples=4917)
      SearchItemsForSale: 561.06 ms (samples=25019)
      UpdateUnitsForSale: 755.76 ms (samples=7500)
```

Explanation:
- Scenario 2 gets progressively slower across runs even without injected failures.
- The biggest latency growth is in cart mutation operations (`AddItemToCart`, `RemoveItemFromCart`, `DisplayCart`), which depend on more write/coordination work across services.
- Product read-heavy operations stay relatively lower because they are eventually consistent and can be served from any replica's local committed SQLite state, while write-heavy paths dominate the increase in average latency and p99.
- Based on logs/metrics, the main latency driver in Scenario 2 appears to be the Customer DB sequencer path (gap detection/retransmit/recovery under load), with Product DB Raft failover/replay effects being secondary in this mode.

#### Failure mode: backend-sellers-buyers
```plaintext
Run 1: ops=20000/20000, latency=239.89ms (p50=198.73ms, p99=699.62ms), throughput=73.3 ops/s, duration=272.70s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 2: ops=20000/20000, latency=415.39ms (p50=360.70ms, p99=1275.51ms), throughput=41.1 ops/s, duration=486.48s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 3: ops=20000/20000, latency=664.23ms (p50=592.15ms, p99=2140.78ms), throughput=25.6 ops/s, duration=782.41s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 4: ops=20000/20000, latency=956.51ms (p50=859.09ms, p99=3093.45ms), throughput=17.8 ops/s, duration=1124.01s
    Failure injection: injected=True delay=5.00s exit_code=0
Run 5: ops=20000/20000, latency=1381.70ms (p50=1180.12ms, p99=4283.94ms), throughput=12.4 ops/s, duration=1609.61s
    Failure injection: injected=True delay=5.00s exit_code=0
```

```plaintext
Summary (5 runs):
    Avg Response Time: 731.54 ms (stddev: 452.64 ms)
    Avg Throughput: 34.0 ops/s (stddev: 24.5 ops/s)
    Seller Avg Latency: 619.97 ms
    Buyer Avg Latency: 843.12 ms
    Avg Response Time Per Client Function:
      AddItemToCart: 1426.44 ms (samples=7481)
      ChangeItemPrice: 719.48 ms (samples=7500)
      DisplayCart: 954.75 ms (samples=10106)
      DisplayItemsForSale: 584.14 ms (samples=20000)
      GetBuyerPurchases: 933.22 ms (samples=2500)
      GetSellerRating: 558.20 ms (samples=15000)
      RemoveItemFromCart: 1417.34 ms (samples=4894)
      SearchItemsForSale: 502.28 ms (samples=25019)
      UpdateUnitsForSale: 739.55 ms (samples=7500)
```

Explanation:
- Similar to Scenario 2 `none`, this mode also gets progressively slower across runs, which means the main bottleneck is still sustained load and queueing/backlog growth, not just the injected backend failure itself.
- The backend pod kill adds extra disruption early in each run (reconnect + retry), but at this scale that overhead gets dominated by DB coordination and write-heavy contention as the run continues.
- The same pattern still appears in function-level latency: cart mutation operations (`AddItemToCart`, `RemoveItemFromCart`, `DisplayCart`) grow the most, while read-heavy calls are less impacted because they are eventually consistent reads that can be served by any replica.
- Overall, this confirms that deleting one buyers + one sellers replica is recoverable, but it worsens an already overloaded path where Customer DB sequencer overhead is the dominant latency contributor.

> Note: Remaining unreported sections (Scenario 2 `product-follower` / `product-leader`, and all Scenario 3 modes) were not completed due to very expensive runtime requirements and latency of replication. Scenario 3 was especially too slow at this scale, so I stopped it to avoid using up remaining cloud credits.

## Bottlenecks observed
- In Scenario 2, the dominant bottleneck appears to be Customer DB sequencer coordination/retransmit behavior under sustained write-heavy load (observed via frequent sequencer gap/recovery activity), which drives long-tail latency and throughput collapse.

## PA1 vs PA2 comparison

It is hard to directly compare PA1 and PA2 results for many reasons, most notably the difference in running on a loopback interface vs on separate VMs across a network. Another smaller note is I am not sure the performance differences in my M4 Pro CPU compared to the GCP VMs I am using for PA2. However, I can make some observations based on the results I have.

The main observation is obviously the addition of network latency in PA2, which is expected to add significant latency compared to the loopback interface used in PA1. The increase in latency from around 1ms in PA1 to around 55ms in PA2 for scenario 1 is mostly due to the network.

After doing just a comparison of scenario 1 locally (results not shown), I observed that with FastAPI and gRPC libraries, the base latency per request increased from around 1ms to 4ms. This is likely due to the overhead of the additional libraries FastAPI, Uvicorn, gRPC, and httpx, compared to much simpler TCP style RPC calls in PA1. This is surprising to me because I expected the optimized libraries to have less overhead, but it seems that the additional features and abstractions they provide come with some cost in terms of request processing time.

Something else I observed when comparing PA1 and PA2 results is that both throughput and latency increased, while with PA1 only latency increased and throughput decreased. I think this can be explained by PA2 now having the network acting as a sort of "throttle" that limits the number of requests that can be processed in parallel, which can help to keep the services more fully utilized and increase throughput, even though each request takes longer on average. Because of this, scenario 2 with 20 users seems to be the sweet spot for maximizing throughput while keeping latency at a reasonable level, while scenario 3 with 200 users causes too much contention and queuing at the SQLite layer, leading to very high latency and reduced throughput.

## PA2 vs PA3 comparison

It is hard to directly compare PA2 and PA3 for many reasons, mostly because PA3 adds replication protocols on top of the PA2 architecture. In PA2, each DB path was effectively single-node logic with retry/failover behavior at the client side, while PA3 now adds protocol-level coordination for both Customer DB and Product DB.

The main observation is that PA3 introduces a much larger coordination cost than PA2, especially under concurrency. In PA3, even Scenario 1 has higher baseline latency because Product DB writes now require Raft commit and Customer DB operations go through sequencer ordering/retransmit logic.

The bigger difference appears in Scenario 2. In PA2, Scenario 2 was the practical throughput sweet spot, but in PA3 Scenario 2 degrades run-over-run as queueing and protocol overhead build up. The function-level stats also show that write-heavy/cart-heavy paths increase much more sharply than read-heavy paths, since those reads are eventually consistent and can be served by any replica while writes require protocol coordination. From logs and metrics during these runs, most of this Scenario 2 latency increase appears to come from the Customer DB sequencer path rather than Product DB read behavior.

Another practical difference is failure behavior. In PA3, failures include protocol recovery behavior (leader re-election, follower catch-up, replay, sequencer gap recovery), so tail latency becomes much more sensitive to timing and system load.
