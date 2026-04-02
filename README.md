# Distributed Systems PA3: Richard Roberson

This project implements a 3-tier architecture for an online marketplace platform.  With separate backend services for buyers and sellers, custom database services using sqlite3 through sqlalchemy ORM, along with client-side command line interfaces (CLIs) for the "frontend".

## Assumptions

- Use HTTP/JSON between client CLIs and backend edge services.
- Use gRPC/protobuf between backend services and DB services.
#### Edge REST/HTTP Contract
- Protected endpoints require `X-Session-Id`.
- Success responses return payload JSON with HTTP 2xx.
- Error responses return HTTP status + JSON body: `{"code":"<ERROR_CODE>","message":"<human message>"}`.
#### Client Frontend CLIs
- "Frontend" refers to the client-side command line interfaces (CLIs) for buyers and sellers.
- CLIs share the same backend client class as the benchmark evaluation scripts.
- Client reconnect/retry logic is basic; no idempotency guarantees.
#### Backend services
- Backend services are FastAPI applications served by Uvicorn.
- Backend services remain stateless; session/cart/auth state is persisted in DB services.
- Backend services communicate to DB services over gRPC.
- Session ID is a server-generated random token (32-byte hex string)

#### Database services
- DB services use gRPC with a thread pool to handle multiple backend calls.
- Both DB services using sqlite3 through sqlalchemy ORM, limited to 1 writer but multiple concurrent readers.
- SQLite uses a locking mechanism to serialize writes, so threads may block waiting for the write lock for up to 10 mins (600 seconds).

##### Customer DB assumptions
- Replicated through a rotating sequencer protocol over UDP for ordered delivery on 3 or more nodes.
- Backend buyers and sellers customer db client uses client-side round-robin endpoint selection with retry failover across the customer sequencer nodes.
- Sequencer protocol state is in-memory and reset on process restart. Not durable.
- Timestamps and session IDs used by replicated operations are generated once in the Customer DB gRPC handler and passed explicitly (`now_iso`, `session_id`, `purchased_at_iso`).
- Request message metadata includes: `sender_id`, `request_sender_id`, `request_local_seq`, `method`, `kwargs`, and `recv_upto`.
- Sequence message metadata includes: `sender_id`, `global_seq`, `request_sender_id`, `request_local_seq`, and `recv_upto`.
- Retransmit message metadata includes: `sender_id`, `target_id`, `mode` (`progress|request|sequence`), plus `request_sender_id`/`request_local_seq` for request-NACKs, `global_seq` for sequence-NACKs, and `recv_upto`.
- Progress propagation is event-driven: nodes send immediate `progress` updates when `recv_upto` advances (no periodic heartbeat loop).
- Session cart and saved cart are separate; Cart.SaveCart overwrites saved cart; Cart.LoadSavedCart overwrites session cart; setting quantity <= 0 removes the cart item.
- Sessions are cleaned up by utilizing a "last touched" timestamp; if a session is inactive for 5+ mins, it is expired and deleted from the DB. Backend services validate on each protected request and update last-activity periodically (every 30s while active) to reduce write amplification.

##### Product DB assumptions
- Replicated through PySyncObj's Raft implementation for high availability on 3 or more nodes.
- Backend buyers and sellers product db client uses client-side round-robin endpoint selection with retry failover across product DB nodes.
- Consistent: write APIs (`CreateItem`, `UpdateItemPrice`, `UpdateItemQuantity`, `DeleteItem`, `AddFeedbackVote`) are committed through Raft and return after commit.
- Raft log index is treated as the global write order identifier for Product DB writes.
- Product SQLite stores `last_applied_raft_index` and per-index apply results; writes are applied idempotently by raft index.
- On startup, each Product DB node replays missing committed Raft regular entries (index `> last_applied_raft_index`) into SQLite before serving traffic.
- Eventually Consistent: read APIs (`GetItem`, `ListItemsBySeller`, `SearchItems`, `GetItemFeedback`, `CheckBuyerVoted`) are served locally on any replica and may be briefly stale during replication lag/failover.
- Item IDs are composite keys of (int item_category, int item_id) where item category is the high-level category chosen by the seller and item_id is a unique identifier within that category (63-bit random integer w/ collision retries).
- **Search semantics:** category match is exact; if keywords are provided, items are ranked by keyword match count; only non-deleted items with quantity > 0 are returned.
- Items are tombstoned (set deleted_at) and hidden from normal Get/Search; cart display will include deleted items that are "no longer availble".
- Item feedback votes update both the item's feedback counters and the seller's feedback counters.
- Up to 5 keywords (limited to 8 characters each)

## Current State

### What Works
- HTTP edge services (backend-sellers, backend-buyers), gRPC DB services (Customer DB, Product DB), and a SOAP financial service with configurable host/ports.
- Client-to-backend communication uses HTTP JSON, while backend-to-DB uses typed gRPC/protobuf RPCs.
- Seller APIs: CreateAccount, Login, Logout, GetSellerRating, RegisterItemForSale, ChangeItemPrice, UpdateUnitsForSale, DisplayItemsForSale.
- Buyer APIs: CreateAccount, Login, Logout, SearchItemsForSale, GetItem, AddItemToCart, RemoveItemFromCart, DisplayCart, SaveCart, ClearCart, ProvideFeedback, GetSellerRating, GetBuyerPurchases, MakePurchase.
- Session timeout: 5 minutes of inactivity (validated in Customer DB; backend periodically “touches” sessions while handling requests).
- Client-side command line interfaces (CLIs) for buyers and sellers with all API commands implemented.
- Benchmark script with 3 scenarios, per-client-function latency output, and optional failure injection controls for PA3-style evaluations.
- Docker Compose deployment path runs replicated DB tiers (3 Customer DB sequencer replicas and 3 Product DB Raft replicas).
- Customer DB replication uses UDP rotating sequencer ordering with retransmit (NACK) recovery.
- Product DB reads are served locally on replicas and are eventually consistent during replication/failover windows.
- Product DB write apply is idempotent by Raft log index and startup performs replay of missing committed entries into SQLite.
- Backend DB clients use client-side round-robin endpoint selection with retry failover.

### What Doesn't Work / Not Implemented

- Retry logic in clients is basic; no idempotency guarantees.
- Backend DB retry/failover is best-effort (round-robin + retry), but there are no end-to-end idempotency keys from client through backend.
- Customer sequencer state is in-memory only; process restart discards protocol state.
- No advanced testing suite (manual CLI flow + benchmark script are the primary validation).

## Docker Compose Dev Setup

Use this path for the fastest full-stack local run (3x Customer DB sequencer replicas + 3x Product DB Raft replicas + both backends + financial service).

Build and start all services from repo root:

```bash
docker compose up --build
```

Run benchmark/CLIs against compose:

```bash
uv run python benchmark.py --scenario 1
uv run python clients/sellers/cli.py --help
uv run python clients/buyers/cli.py --help
```

Stop (and optionally reset volumes):

```bash
docker compose down
# optional full reset:
docker compose down -v
```

## Python Environment Setup

- This assignment targets **Python 3.13** and was not tested on other versions.
- Dependencies are managed with `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`). 

### 1) Install dependencies

From the assignment root:

```bash
uv sync --all-groups
```

After syncing, you can either run commands via `uv run ...` or activate the virtualenv (`source .venv/bin/activate`) and use `python ...`.

## Benchmark Details

- `--failure-mode`: `none` (default), `backend-sellers-buyers`, `product-follower`, `product-leader`
- `--failure-platform`: `compose` (default) or `k8s` (preset commands enabled for marketplace chart names in namespace `default`)
- `--failure-command`: explicit shell command; overrides presets if provided
- `--failure-delay-sec`: delay before running the failure command in each run (default `5.0`)

Examples:

```bash
# Kill one product follower in compose once per run
uv run python benchmark.py --scenario 1 --failure-mode product-follower --failure-platform compose

# Kill one backend-sellers and one backend-buyers pod in k8s once per run
uv run python benchmark.py --scenario 1 --failure-mode backend-sellers-buyers --failure-platform k8s

# Kill product db follower/leader presets in k8s once per run
uv run python benchmark.py --scenario 1 --failure-mode product-follower --failure-platform k8s
uv run python benchmark.py --scenario 1 --failure-mode product-leader --failure-platform k8s
```

## GCP: GKE Cluster Deployment

The detailed GKE setup and operations guide lives in:

- [`k8s/README.md`](/Users/richardroberson/Documents/classes/distributed-systems/pa3-sequencer-raft/k8s/README.md)

Use the root script to automate:

- Cluster create/connect (`k8s/gke-cluster.py`, `gcloud get-credentials`)
- `k8s/helm/install-apps.sh`
- Marketplace reset helper: `k8s/helm/reinstall-marketplace.sh`
- Port-forward sellers/buyers services
- Full benchmark matrix:
  - Scenarios: `1,2,3`
  - Failure modes: `none`, `backend-sellers-buyers`, `product-follower`, `product-leader`
  - Default `10` runs per case, `1000` ops per client

```bash
./run-pa3-benchmarks-gke.sh
```

The script writes per-case logs and a `summary.tsv` under `benchmark-results/<timestamp>/`.
`run-pa3-benchmarks-gke.sh` is intentionally fixed and deterministic:
- Uses `pa3-cloud` in `us-central1-f`
- Runs `10` iterations per case with `1000` ops/client
- Deletes and recreates marketplace state for every case
- Deletes the cluster at the end


## Clients (Frontend CLIs)

The “frontend” for this project is implemented as two Python command-line clients that connect over HTTP to the backend services:

- **Seller CLI:** `clients/sellers/cli.py` _(connects to backend-sellers, default `localhost:8003`)_
- **Buyer CLI:** `clients/buyers/cli.py` _(connects to backend-buyers, default `localhost:8004`)_

You can list commands and see detailed usage with:

- `python clients/sellers/cli.py --help`
- `python clients/buyers/cli.py --help`

### Seller CLI commands

- **create-account:** _Create a new seller account._
- **login:** _Login and receive a session_id._
- **logout:** _End the current seller session._
- **rating:** _Get the feedback totals for the logged-in seller._
- **register-item:** _Register a new item for sale._
- **display-items:** _List items currently for sale by the logged-in seller._
- **change-price:** _Change an item’s sale price._
- **update-units:** _Set quantity or apply a quantity delta to an item._

Example flow:

- `python clients/sellers/cli.py create-account --name "Alice Store" --login alice --password secret`
- `python clients/sellers/cli.py login --login alice --password secret`
- `python clients/sellers/cli.py --session <sid> register-item --name "iPhone 15" --category 1 --keywords phone apple --condition new --price 999.99 --quantity 10`
- `python clients/sellers/cli.py --session <sid> display-items`
- `python clients/sellers/cli.py --session <sid> change-price --category 1 --item <registered_id> --price 899.99`
- `python clients/sellers/cli.py --session <sid> update-units --category 1 --item <registered_id> --delta -1`
- `python clients/sellers/cli.py --session <sid> rating`
- `python clients/sellers/cli.py --session <sid> logout`

### Buyer CLI commands

- **create-account:** _Create a new buyer account._
- **login:** _Login and receive a session_id._
- **logout:** _End the current buyer session._
- **search:** _Search items for sale by category and optional keywords._
- **get-item:** _Get item details by category + item ID._
- **add-to-cart:** _Add an item and quantity to the active session cart._
- **remove-from-cart:** _Remove an item and quantity from the active session cart._
- **cart:** _Display the active session cart._
- **save-cart:** _Persist the current cart across sessions._
- **clear-cart:** _Clear the active session cart._
- **feedback:** _Thumbs up/down vote for an item._
- **seller-rating:** _Fetch seller feedback totals by seller ID._
- **make-purchase:** _Checkout current cart using credit card details._
- **purchases:** _Show purchase history for the logged-in buyer._

Example flow:

- `python clients/buyers/cli.py create-account --name "Bob" --login bob --password secret`
- `python clients/buyers/cli.py login --login bob --password secret`
- `python clients/buyers/cli.py --session <sid> search --category 1 --keywords phone apple`
- `python clients/buyers/cli.py --session <sid> get-item --category 1 --item <any_id_from_search>`
- `python clients/buyers/cli.py --session <sid> add-to-cart --category 1 --item <id_from_search> --quantity 2`
- `python clients/buyers/cli.py --session <sid> cart`
- `python clients/buyers/cli.py --session <sid> save-cart`
- `python clients/buyers/cli.py --session <sid> feedback --category 1 --item <id_from_search> --vote up`
- `python clients/buyers/cli.py --session <sid> seller-rating --seller 1`
- `python clients/buyers/cli.py --session <sid> make-purchase --name "Bob" --card-number 4111111111111111 --expiry 12/30 --security-code 123`
- `python clients/buyers/cli.py --session <sid> purchases`
- `python clients/buyers/cli.py --session <sid> logout`

## APIs

Buyer/seller edge APIs are HTTP JSON endpoints.

### Seller Backend HTTP Endpoints

- `POST /accounts` -> create seller account
- `POST /sessions/login` -> login seller
- `POST /sessions/logout` -> logout seller
- `GET /me/rating` -> get own seller rating
- `POST /items` -> register item for sale
- `PATCH /items/{item_category}/{item_id}/price` -> change item price
- `PATCH /items/{item_category}/{item_id}/quantity` -> update item quantity
- `GET /items` -> display seller items

### Buyer Backend HTTP Endpoints

- `POST /accounts` -> create buyer account
- `POST /sessions/login` -> login buyer
- `POST /sessions/logout` -> logout buyer
- `GET /items/search?item_category=<int>&keywords=<repeatable>` -> search items
- `GET /items/{item_category}/{item_id}` -> get item
- `POST /cart/items` -> add item to cart
- `DELETE /cart/items/{item_category}/{item_id}?quantity=<int>` -> remove item from cart
- `GET /cart` -> display cart
- `POST /cart/save` -> save cart
- `DELETE /cart` -> clear cart
- `POST /feedback` -> provide feedback
- `GET /sellers/{seller_id}/rating` -> get seller rating
- `POST /purchases` -> make purchase for current cart
- `GET /purchases` -> get buyer purchases

### Financial SOAP Endpoint

- WSDL: `GET http://<host>:8005/?wsdl`
- SOAP method: `ProcessTransaction(user_name, credit_card_number, expiration_date, security_code)`
- Result: `"Yes"` (approved) or `"No"` (declined)

### Internal gRPC APIs

Backend-to-DB communication is typed gRPC/protobuf:

- Customer DB contract: `protos/customer_db.proto` (`CustomerDbService`)
- Product DB contract: `protos/product_db.proto` (`ProductDbService`)

Generated Python stubs are stored in `common/grpc_gen/` and are used by:

- DB servers (`databases/customer/main.py`, `databases/product/main.py`) as gRPC servicers
- Backend DB clients (`backends/common/db_client.py`) as typed stubs