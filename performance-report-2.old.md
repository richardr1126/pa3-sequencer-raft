# Performance Report (PA2)

The benchmark script is at `benchmark.py`. It uses `clients/common/client.py` to perform API operations against the deployed services.

## Experiment Setup

- Customer DB service: `databases/customer/main.py` (port `8001`)
- Product DB service: `databases/product/main.py` (port `8002`)
- Backend sellers service: `backends/sellers/main.py` (port `8003`)
- Backend buyers service: `backends/buyers/main.py` (port `8004`)

### Hardware / Environment

- Each service process runs on a separate VM.
- Client-to-backend communication (benchmark/CLI to ports `8003` and `8004`) uses backend external IPs.
- Inter-service communication uses internal VPC paths for backend-to-DB traffic (`8001`, `8002`).
- Python version: 3.13.11

### How the benchmark was run

```bash
# Run benchmark
uv run python benchmark.py --scenario 1 --sellers-host <external_ip_sellers> --buyers-host <external_ip_buyers> --sellers-port 8003 --buyers-port 8004
uv run python benchmark.py --scenario 2 --sellers-host <external_ip_sellers> --buyers-host <external_ip_buyers> --sellers-port 8003 --buyers-port 8004
uv run python benchmark.py --scenario 3 --sellers-host <external_ip_sellers> --buyers-host <external_ip_buyers> --sellers-port 8003 --buyers-port 8004
```

### Buyer/Seller Run Definitions

- Each run performs 1000 API operations per client/user.
- Setup (create account/login/seed items) is excluded from the 1000 ops and latency stats.
- Setup is included in the run wall-clock duration used for throughput.
- SQLite DB is deleted and re-created before each run to ensure a clean state.
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
```plaintext
Run 1: ops=2000/2000, latency=64.98ms (p50=53.71ms, p99=266.63ms), throughput=29.8 ops/s, duration=67.18s
Run 2: ops=2000/2000, latency=52.93ms (p50=51.37ms, p99=68.31ms), throughput=36.3 ops/s, duration=55.13s
Run 3: ops=2000/2000, latency=52.18ms (p50=51.68ms, p99=66.64ms), throughput=36.7 ops/s, duration=54.52s
Run 4: ops=2000/2000, latency=61.44ms (p50=53.19ms, p99=140.89ms), throughput=30.8 ops/s, duration=64.94s
Run 5: ops=2000/2000, latency=54.75ms (p50=52.70ms, p99=73.16ms), throughput=35.0 ops/s, duration=57.11s
Run 6: ops=2000/2000, latency=54.05ms (p50=52.52ms, p99=68.47ms), throughput=35.7 ops/s, duration=56.01s
Run 7: ops=2000/2000, latency=53.15ms (p50=52.35ms, p99=68.28ms), throughput=36.4 ops/s, duration=55.01s
Run 8: ops=2000/2000, latency=53.06ms (p50=52.16ms, p99=67.64ms), throughput=36.3 ops/s, duration=55.04s
Run 9: ops=2000/2000, latency=53.74ms (p50=52.35ms, p99=71.30ms), throughput=36.1 ops/s, duration=55.37s
Run 10: ops=2000/2000, latency=54.15ms (p50=53.16ms, p99=71.48ms), throughput=35.2 ops/s, duration=56.90s
```

```plaintext
Summary (10 runs):
    Avg Response Time: 55.44 ms (stddev: 4.24 ms)
    Avg Throughput: 34.8 ops/s (stddev: 2.5 ops/s)
    Seller Avg Latency: 53.44 ms
    Buyer Avg Latency: 57.45 ms
```

Explanation:
- With only 1 seller and 1 buyer, there should be minimal contention for resources. Tests performed with REST/gRPC locally showed around 4ms latency, so the higher latency here is likely due to network overhead from communicating between VMs, as well as some possible startup/warmup effects.
- Latency and throughput stay consistent across runs towards the end. I suspect the higher latency and lower throughput in Run 1 is due to a possible startup or warmup effect, which I attribute to possibly starting the benchmark too soon after the VM was ready.

### Scenario 2: 10 sellers + 10 buyers
```plaintext
Run 1: ops=20000/20000, latency=105.31ms (p50=77.33ms, p99=553.60ms), throughput=144.0 ops/s, duration=138.85s
Run 2: ops=20000/20000, latency=105.45ms (p50=80.05ms, p99=508.05ms), throughput=142.0 ops/s, duration=140.82s
Run 3: ops=20000/20000, latency=111.79ms (p50=83.76ms, p99=552.61ms), throughput=133.4 ops/s, duration=149.89s
Run 4: ops=20000/20000, latency=116.38ms (p50=86.28ms, p99=552.32ms), throughput=128.6 ops/s, duration=155.54s
Run 5: ops=20000/20000, latency=126.87ms (p50=91.79ms, p99=568.05ms), throughput=116.2 ops/s, duration=172.12s
Run 6: ops=20000/20000, latency=126.87ms (p50=92.50ms, p99=581.13ms), throughput=113.6 ops/s, duration=176.03s
Run 7: ops=20000/20000, latency=136.46ms (p50=95.81ms, p99=633.22ms), throughput=104.5 ops/s, duration=191.41s
Run 8: ops=20000/20000, latency=140.89ms (p50=97.38ms, p99=624.50ms), throughput=100.4 ops/s, duration=199.28s
Run 9: ops=20000/20000, latency=145.24ms (p50=101.19ms, p99=588.99ms), throughput=97.9 ops/s, duration=204.39s
Run 10: ops=20000/20000, latency=149.26ms (p50=100.67ms, p99=592.94ms), throughput=92.7 ops/s, duration=215.71s
```

```plaintext
Summary (10 runs):
    Avg Response Time: 126.45 ms (stddev: 16.28 ms)
    Avg Throughput: 117.3 ops/s (stddev: 18.7 ops/s)
    Seller Avg Latency: 87.66 ms
    Buyer Avg Latency: 165.24 ms
```

Explanation:
- In this scenario there are 20 concurrent users making requests (20k ops per run), and with only 1 writer in SQLite, write requests start to queue up through the SQLAlchemy connection pool or by waiting for the lock to be released by SQLite, causing increased latency and reduced throughput.
- The increase in throughput is likely due to the fact that with more clients, the services are able to process more operations in parallel, even though each operation takes longer on average. The services are likely able to keep their resources more fully utilized with more concurrent clients and more in-flight requests, which can lead to higher overall throughput despite the increased latency. This contrasts with the PA1 scenario 2 results where the throughput decreased compared to scenario 1, likely due to the fact that all processes were running on the same server.

### Scenario 3: 100 sellers + 100 buyers

```plaintext
Run 1: ops=200000/200000, latency=1475.33ms (p50=1189.65ms, p99=5417.95ms), throughput=91.0 ops/s, duration=2198.65s
Run 2: ops=200000/200000, latency=1867.41ms (p50=1201.76ms, p99=7452.12ms), throughput=62.3 ops/s, duration=3211.16s
Run 3: ops=200000/200000, latency=2387.88ms (p50=1711.78ms, p99=9745.15ms), throughput=48.0 ops/s, duration=4170.64s
```

> Note: After running for over 3 hours, it was infeasible to continue running the remaining runs for scenario 3, so I stopped tests here and am only reporting the results for the 3 completed runs.

```plaintext
Summary (3 completed runs):
    Avg Response Time: 1910.21 ms (stddev: 457.78 ms)
    Avg Throughput: 67.1 ops/s (stddev: 21.9 ops/s)
```

Explanation:
- 200 concurrent users making 200k total simultaneous requests per run causes significant contention for the single SQLite writer lock, leading to very high latencies and very low throughput.
- The Cloud PA2 version of scenario 3 is much worse than the PA1 version of scenario 3. Again, this is mostly due to the fact that in PA1 there was no network latency and the database was not another network round trip.

## Bottlenecks observed
- The main bottleneck now seems to be mostly network latency and possibly somewhat slower GCP e2-standard-2 CPUs.
- With high concurrency required by Scenario 3, SQLite becomes the main bottleneck due to SQLite's 1 writer write lock contention.
- PostgreSQL could improve performance allowing concurrent writes, however since future assignments will require serializing writes through Raft or Paxos I am guessing, concurrent writes won't be used anyway.

## PA1 vs PA2 comparison

It is hard to directly compare PA1 and PA2 results for many reasons, most notably the difference in running on a loopback interface vs on separate VMs across a network. Another smaller note is I am not sure the performance differences in my M4 Pro CPU compared to the GCP VMs I am using for PA2. However, I can make some observations based on the results I have.

The main observation is obviously the addition of network latency in PA2, which is expected to add significant latency compared to the loopback interface used in PA1. The increase in latency from around 1ms in PA1 to around 55ms in PA2 for scenario 1 is mostly due to the network.

After doing just a comparison of scenario 1 locally (results not shown), I observed that with FastAPI and gRPC libraries, the base latency per request increased from around 1ms to 4ms. This is likely due to the overhead of the additional libraries FastAPI, Uvicorn, gRPC, and httpx, compared to much simpler TCP style RPC calls in PA1. This is surprising to me because I expected the optimized libraries to have less overhead, but it seems that the additional features and abstractions they provide come with some cost in terms of request processing time.

Something else I observed when comparing PA1 and PA2 results is that both throughput and latency increased, while with PA1 only latency increased and throughput decreased. I think this can be explained by PA2 now having the network acting as a sort of "throttle" that limits the number of requests that can be processed in parallel, which can help to keep the services more fully utilized and increase throughput, even though each request takes longer on average. Because of this, scenario 2 with 20 users seems to be the sweet spot for maximizing throughput while keeping latency at a reasonable level, while scenario 3 with 200 users causes too much contention and queuing at the SQLite layer, leading to very high latency and reduced throughput.
