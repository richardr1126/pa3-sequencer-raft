# Performance Report (PA1)

The benchmark script is at `benchmark.py`. It uses the `clients/common/client.py` library to perform API operations against the deployed services (same as the CLI clients).

## Experiment Setup

- Customer DB service: `databases/customer/main.py` (default port `8001`)
- Product DB service: `databases/product/main.py` (default port `8002`)
- Backend sellers service: `backends/sellers/main.py` (default port `8003`)
- Backend buyers service: `backends/buyers/main.py` (default port `8004`)

### Hardware / Environment

- macOS 26.2 on MacBook Pro (M4) 48 GB RAM
- Separate process for each service
- Python version: 3.13.11

### How the benchmark was run

```bash
# Start services (separate terminals)
uv run python databases/customer/main.py
uv run python databases/product/main.py
uv run python backends/sellers/main.py
uv run python backends/buyers/main.py

# Run benchmark
uv run python benchmark.py --scenario 1
uv run python benchmark.py --scenario 2
uv run python benchmark.py --scenario 3
```

### Buyer/Seller Run Definitions

- Each run performs 1000 API operations per client/user.
- Setup (create account/login/seed items) is excluded from the 1000 ops and latency stats
- But setup **is** included in the run’s wall-clock duration used for throughput calculation.
- SQLite DB is deleted and re-created before each run to ensure a clean state.
- `latency`: average latency across all successful operations in the run
- `p50`: median latency across all successful operations in the run
- `p99`: 99th percentile latency across all successful operations in the run
- `throughput`: total successful operations / wall-clock duration

#### API ops distribution

The runs uses a fixed operation mix (shuffled per run):

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
Run 1: ops=2000/2000, latency=1.08ms (p50=1.00ms, p99=2.41ms), throughput=1550.6 ops/s, duration=1.29s
Run 2: ops=2000/2000, latency=1.05ms (p50=0.98ms, p99=2.19ms), throughput=1595.0 ops/s, duration=1.25s
Run 3: ops=2000/2000, latency=1.09ms (p50=1.02ms, p99=2.34ms), throughput=1531.4 ops/s, duration=1.31s
Run 4: ops=2000/2000, latency=1.06ms (p50=0.96ms, p99=2.31ms), throughput=1591.7 ops/s, duration=1.26s
Run 5: ops=2000/2000, latency=1.05ms (p50=0.97ms, p99=2.38ms), throughput=1593.9 ops/s, duration=1.25s
Run 6: ops=2000/2000, latency=1.12ms (p50=0.98ms, p99=3.38ms), throughput=1507.4 ops/s, duration=1.33s
Run 7: ops=2000/2000, latency=1.05ms (p50=0.95ms, p99=2.66ms), throughput=1606.9 ops/s, duration=1.24s
Run 8: ops=2000/2000, latency=1.06ms (p50=0.96ms, p99=2.88ms), throughput=1583.1 ops/s, duration=1.26s
Run 9: ops=2000/2000, latency=1.08ms (p50=0.98ms, p99=3.15ms), throughput=1553.8 ops/s, duration=1.29s
Run 10: ops=2000/2000, latency=1.10ms (p50=0.98ms, p99=3.29ms), throughput=1506.6 ops/s, duration=1.33s
```

```plaintext
Summary (10 runs):
    Avg Response Time: 1.07 ms
    Avg Throughput: 1562.0 ops/s
    Seller Avg Latency: 0.88 ms
    Buyer Avg Latency: 1.27 ms
```

Explanation:
- With only 1 seller and 1 buyer, there is minimal contention for resources, so latency is very low (1ms) and throughput is very high.
- The services can easily handle the low concurrency without any queuing or delays, because there will be at most 2 writes (1 from seller, 1 from buyer) at any time.
- Latency and trhoughput stay consistent across runs.

### Scenario 2: 10 sellers + 10 buyers

```plaintext
Run 1: ops=20000/20000, latency=18.41ms (p50=9.81ms, p99=130.44ms), throughput=763.3 ops/s, duration=26.20s
Run 2: ops=20000/20000, latency=19.59ms (p50=10.92ms, p99=116.35ms), throughput=697.9 ops/s, duration=28.66s
Run 3: ops=20000/20000, latency=20.48ms (p50=10.29ms, p99=108.42ms), throughput=650.5 ops/s, duration=30.75s
Run 4: ops=20000/20000, latency=21.61ms (p50=10.18ms, p99=102.93ms), throughput=600.0 ops/s, duration=33.33s
Run 5: ops=20000/20000, latency=22.98ms (p50=9.90ms, p99=102.23ms), throughput=550.8 ops/s, duration=36.31s
Run 6: ops=20000/20000, latency=24.05ms (p50=9.89ms, p99=108.76ms), throughput=521.5 ops/s, duration=38.35s
Run 7: ops=20000/20000, latency=25.52ms (p50=10.10ms, p99=122.63ms), throughput=485.8 ops/s, duration=41.17s
Run 8: ops=20000/20000, latency=26.51ms (p50=9.63ms, p99=136.36ms), throughput=459.3 ops/s, duration=43.55s
Run 9: ops=20000/20000, latency=27.90ms (p50=10.05ms, p99=155.91ms), throughput=432.7 ops/s, duration=46.22s
Run 10: ops=20000/20000, latency=30.04ms (p50=10.47ms, p99=172.47ms), throughput=396.6 ops/s, duration=50.43s
```

```plaintext
Summary (10 runs):
    Avg Response Time: 23.71 ms
    Avg Throughput: 555.8 ops/s
    Seller Avg Latency: 10.31 ms
    Buyer Avg Latency: 37.10 ms
```

Explanation:
- In this scenario there are 20 concurrent users making requests (20k ops per run), and with only 1 writer in SQLite, write requests start to queue up through the SQLAlchemy connection pool or by waiting for the lock to be released by SQLite, causing increased latency and reduced throughput.
- Latency increases around 15x compared to Scenario 1's 1ms response time, and throughput drops significantly as well.

### Scenario 3: 100 sellers + 100 buyers

```plaintext
Run 1: ops=200000/200000, latency=326.49ms (p50=132.96ms, p99=2199.10ms), throughput=408.5 ops/s, duration=489.59s
Run 2: ops=200000/200000, latency=437.14ms (p50=125.11ms, p99=2040.08ms), throughput=286.3 ops/s, duration=698.49s
Run 3: ops=200000/200000, latency=497.04ms (p50=159.58ms, p99=2896.79ms), throughput=236.8 ops/s, duration=844.43s
Run 4: ops=200000/200000, latency=602.44ms (p50=169.99ms, p99=3781.54ms), throughput=194.8 ops/s, duration=1026.60s
Run 5: ops=200000/200000, latency=695.44ms (p50=187.75ms, p99=4731.57ms), throughput=165.4 ops/s, duration=1209.41s
Run 6: ops=200000/200000, latency=766.74ms (p50=205.28ms, p99=5600.51ms), throughput=149.4 ops/s, duration=1338.91s
Run 7: ops=200000/200000, latency=837.99ms (p50=213.30ms, p99=6522.62ms), throughput=134.3 ops/s, duration=1489.22s
Run 8: ops=200000/200000, latency=931.15ms (p50=204.62ms, p99=7255.16ms), throughput=121.2 ops/s, duration=1649.98s
Run 9: ops=200000/200000, latency=960.59ms (p50=247.78ms, p99=8230.59ms), throughput=114.7 ops/s, duration=1743.61s
```

```plaintext
Summary (10 runs):
    Avg Response Time: 713.54 ms
    Avg Throughput: 191.4 ops/s
    Seller Avg Latency: 200.14 ms
    Buyer Avg Latency: 1226.93 ms
```

Explanation:
- 200 concurrent users making 200k total simultaneous requests per run causes significant contention for the single SQLite writer lock, leading to very high latencies (up to 900ms average, with p99 up to 8s) and low throughput (as low as 114 ops/s).
- The more concurrent users, the more queuing occurs at the SQLite database layer, causing major increases in latency and decreases in throughput.

### Bottlenecks observed
- With high concurrency required by Scenario 3, SQLite becomes the main bottleneck due to SQLite's 1 writer write lock contention.
- PostgreSQL could improve performance allowing concurrent writes, however since future assignments will require to serialize writes through Raft or Paxos I am guessing, concurrent writes wont be used anyway.

### Discussion

- Performing these evals was very beneficial to evolve the system architecture to better handle and understand concurrency and contention. Especially around SQLite, I learned a lot about its write queing.
- At first, instead of thread-per-connection to the db services, I tested a single connection to db services from backend services with a write queue, but performance was almost the same as using a connection pool with threads waiting for the write lock from SQLite. So I decided to keep the connection pool with SQLite locking becuase it was my better implementation.
- I really went deep into a SQLite vs. PostgreSQL comparison for this assignment, and learned a lot about their differences in handling concurrency. However, since future assignments will require consensus protocols to serialize writes anyway, I decided to stick with SQLite for simplicity.