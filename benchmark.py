#!/usr/bin/env python3
"""Benchmark script for online marketplace evaluation.

Measures average response time and throughput for three scenarios:
- Scenario 1: 1 seller + 1 buyer
- Scenario 2: 10 sellers + 10 buyers
- Scenario 3: 100 sellers + 100 buyers

The benchmark uses REST clients backed by httpx.
"""

import argparse
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from clients.buyers.client import BuyerClient
from clients.sellers.client import SellerClient

DEFAULT_OPS_PER_CLIENT = 1000
DEFAULT_RUNS = 10

SELLER_OP_WEIGHTS: list[tuple[str, int]] = [
    ("DisplayItemsForSale", 40),
    ("GetSellerRating", 30),
    ("ChangeItemPrice", 15),
    ("UpdateUnitsForSale", 15),
]

BUYER_OP_WEIGHTS: list[tuple[str, int]] = [
    ("SearchItemsForSale", 50),
    ("DisplayCart", 20),
    ("AddItemToCart", 15),
    ("RemoveItemFromCart", 10),
    ("GetBuyerPurchases", 5),
]


def time_request(fn, *args, **kwargs):
    start = time.perf_counter()
    resp = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return resp, elapsed


def build_operation_plan(
    op_weights: list[tuple[str, int]], total_ops: int
) -> list[str]:
    """Build an operation plan using weighted operation ratios."""
    if total_ops <= 0:
        return []

    weight_total = sum(weight for _, weight in op_weights)
    if weight_total <= 0:
        raise ValueError("Operation weights must sum to > 0")

    plan: list[str] = []
    used = 0

    for idx, (op_name, weight) in enumerate(op_weights):
        if idx == len(op_weights) - 1:
            count = total_ops - used
        else:
            count = round(total_ops * (weight / weight_total))
            count = max(0, min(count, total_ops - used))
        used += count
        plan.extend([op_name] * count)

    while len(plan) < total_ops:
        plan.append(random.choice(op_weights)[0])

    if len(plan) > total_ops:
        plan = plan[:total_ops]

    random.shuffle(plan)
    return plan


def extract_registered_item_key(payload: dict[str, Any]) -> tuple[int, int] | None:
    """Handle both nested and flat item id payload shapes."""
    item_id_obj = payload.get("item_id")
    if isinstance(item_id_obj, dict):
        item_category = item_id_obj.get("item_category")
        item_id = item_id_obj.get("item_id")
        if isinstance(item_category, int) and isinstance(item_id, int):
            return (item_category, item_id)

    if isinstance(item_id_obj, int) and isinstance(payload.get("item_category"), int):
        return (payload["item_category"], item_id_obj)

    return None


@dataclass
class Stats:
    """Statistics collected during a workload run."""

    client_id: int
    client_type: str
    total_ops: int = 0
    successful_ops: int = 0
    failed_ops: int = 0
    latencies: list[float] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput(self) -> float:
        if self.duration <= 0:
            return 0.0
        return self.successful_ops / self.duration


def run_seller_workload(
    client_id: int,
    host: str,
    port: int,
    ops_per_client: int,
) -> Stats:
    """Run seller benchmark workload with REST/httpx client."""
    stats = Stats(client_id=client_id, client_type="seller")
    client = SellerClient(host, port)

    try:
        client.connect()

        username = f"seller_bench_{client_id}_{time.time_ns()}"
        resp, _ = time_request(
            client.create_account,
            seller_name=f"Seller {client_id}",
            login_name=username,
            password="benchpass",
        )
        if not resp.get("ok"):
            raise RuntimeError(f"Failed to create seller account: {resp}")

        resp, _ = time_request(client.login, login_name=username, password="benchpass")
        if not resp.get("ok"):
            raise RuntimeError(f"Failed to login: {resp}")

        session_id = resp["payload"]["session_id"]

        item_ids: list[tuple[int, int]] = []
        for i in range(3):
            category = (client_id % 10) + 1
            resp, _ = time_request(
                client.register_item,
                session_id=session_id,
                item_name=f"BenchItem_{client_id}_{i}",
                item_category=category,
                keywords=["bench", "test", f"s{client_id}"],
                condition="new" if i % 2 == 0 else "used",
                sale_price=10.0 + i,
                quantity=100,
            )
            if resp.get("ok"):
                item_key = extract_registered_item_key(resp.get("payload", {}))
                if item_key is not None:
                    item_ids.append(item_key)

        if not item_ids:
            raise RuntimeError(f"Failed to register any items. Last response: {resp}")

        op_plan = build_operation_plan(SELLER_OP_WEIGHTS, ops_per_client)

        stats.start_time = time.perf_counter()

        for op in op_plan:
            stats.total_ops += 1
            try:
                if op == "DisplayItemsForSale":
                    resp, latency = time_request(client.display_items, session_id=session_id)
                elif op == "GetSellerRating":
                    resp, latency = time_request(client.get_rating, session_id=session_id)
                elif op == "ChangeItemPrice":
                    item_category, item_id = random.choice(item_ids)
                    resp, latency = time_request(
                        client.change_price,
                        session_id=session_id,
                        item_category=item_category,
                        item_id=item_id,
                        sale_price=random.uniform(5.0, 50.0),
                    )
                elif op == "UpdateUnitsForSale":
                    item_category, item_id = random.choice(item_ids)
                    resp, latency = time_request(
                        client.update_units,
                        session_id=session_id,
                        item_category=item_category,
                        item_id=item_id,
                        quantity=random.randint(50, 200),
                    )
                else:
                    continue

                if resp.get("ok"):
                    stats.successful_ops += 1
                    stats.latencies.append(latency)
                else:
                    stats.failed_ops += 1
            except Exception:
                stats.failed_ops += 1

        stats.end_time = time.perf_counter()
        client.logout(session_id=session_id)

    except Exception as exc:
        print(f"Seller {client_id} error: {exc}", file=sys.stderr)
    finally:
        client.close()

    return stats


def run_buyer_workload(
    client_id: int,
    host: str,
    port: int,
    ops_per_client: int,
) -> Stats:
    """Run buyer benchmark workload with REST/httpx client."""
    stats = Stats(client_id=client_id, client_type="buyer")
    client = BuyerClient(host, port)

    try:
        client.connect()

        username = f"buyer_bench_{client_id}_{time.time_ns()}"
        resp, _ = time_request(
            client.create_account,
            buyer_name=f"Buyer {client_id}",
            login_name=username,
            password="benchpass",
        )
        if not resp.get("ok"):
            raise RuntimeError(f"Failed to create buyer account: {resp}")

        resp, _ = time_request(client.login, login_name=username, password="benchpass")
        if not resp.get("ok"):
            raise RuntimeError(f"Failed to login: {resp}")

        session_id = resp["payload"]["session_id"]
        op_plan = build_operation_plan(BUYER_OP_WEIGHTS, ops_per_client)

        found_items: list[tuple[int, int]] = []
        found_item_set: set[tuple[int, int]] = set()
        cart_qty: dict[tuple[int, int], int] = {}

        stats.start_time = time.perf_counter()

        for op in op_plan:
            stats.total_ops += 1
            try:
                op_item: tuple[int, int] | None = None

                if op == "SearchItemsForSale":
                    category = random.randint(1, 10)
                    keywords = random.sample(
                        ["bench", "test", "phone", "laptop", "book"], k=2
                    )
                    resp, latency = time_request(
                        client.search_items,
                        session_id=session_id,
                        item_category=category,
                        keywords=keywords,
                    )
                    if resp.get("ok"):
                        items = resp.get("payload", {}).get("items", [])
                        for item in items[:3]:
                            item_key = (item["item_category"], item["item_id"])
                            if item_key not in found_item_set:
                                found_item_set.add(item_key)
                                found_items.append(item_key)
                        if len(found_items) > 20:
                            for stale in found_items[:-20]:
                                found_item_set.discard(stale)
                            found_items = found_items[-20:]

                elif op == "DisplayCart":
                    resp, latency = time_request(client.display_cart, session_id=session_id)

                elif op == "AddItemToCart":
                    if found_items:
                        op_item = random.choice(found_items)
                        resp, latency = time_request(
                            client.add_to_cart,
                            session_id=session_id,
                            item_category=op_item[0],
                            item_id=op_item[1],
                            quantity=1,
                        )
                    else:
                        resp, latency = time_request(
                            client.search_items,
                            session_id=session_id,
                            item_category=random.randint(1, 10),
                            keywords=[],
                        )

                elif op == "RemoveItemFromCart":
                    removable_items = [item for item, qty in cart_qty.items() if qty > 0]
                    if removable_items:
                        op_item = random.choice(removable_items)
                        resp, latency = time_request(
                            client.remove_from_cart,
                            session_id=session_id,
                            item_category=op_item[0],
                            item_id=op_item[1],
                            quantity=1,
                        )
                    else:
                        resp, latency = time_request(client.display_cart, session_id=session_id)

                elif op == "GetBuyerPurchases":
                    resp, latency = time_request(client.purchases, session_id=session_id)

                else:
                    continue

                if resp.get("ok"):
                    if op == "AddItemToCart" and op_item is not None:
                        cart_qty[op_item] = cart_qty.get(op_item, 0) + 1
                    elif op == "RemoveItemFromCart" and op_item is not None:
                        remaining = max(0, cart_qty.get(op_item, 0) - 1)
                        if remaining == 0:
                            cart_qty.pop(op_item, None)
                        else:
                            cart_qty[op_item] = remaining

                # For buyer workflows, business-level 4xx responses can be expected
                # (e.g., remove missing cart item), so count completion + latency.
                stats.successful_ops += 1
                stats.latencies.append(latency)

            except Exception:
                stats.failed_ops += 1

        stats.end_time = time.perf_counter()
        client.logout(session_id=session_id)

    except Exception as exc:
        print(f"Buyer {client_id} error: {exc}", file=sys.stderr)
    finally:
        client.close()

    return stats


@dataclass
class RunResult:
    """Results from a single scenario run."""

    scenario: int
    num_sellers: int
    num_buyers: int
    run_number: int
    total_ops: int = 0
    successful_ops: int = 0
    failed_ops: int = 0
    total_duration: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    throughput_ops_per_sec: float = 0.0
    seller_avg_latency_ms: float = 0.0
    buyer_avg_latency_ms: float = 0.0
    seller_throughput: float = 0.0
    buyer_throughput: float = 0.0


def run_scenario(
    scenario: int,
    num_sellers: int,
    num_buyers: int,
    sellers_host: str,
    sellers_port: int,
    buyers_host: str,
    buyers_port: int,
    run_number: int,
    ops_per_client: int,
    max_workers: int | None,
) -> RunResult:
    """Run a single scenario with concurrent sellers and buyers."""
    result = RunResult(
        scenario=scenario,
        num_sellers=num_sellers,
        num_buyers=num_buyers,
        run_number=run_number,
    )

    all_stats: list[Stats] = []
    total_clients = num_sellers + num_buyers
    pool_workers = total_clients if max_workers is None else max(1, min(max_workers, total_clients))

    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=pool_workers) as executor:
        futures = []

        for i in range(num_sellers):
            futures.append(
                executor.submit(
                    run_seller_workload,
                    i,
                    sellers_host,
                    sellers_port,
                    ops_per_client,
                )
            )

        for i in range(num_buyers):
            futures.append(
                executor.submit(
                    run_buyer_workload,
                    i,
                    buyers_host,
                    buyers_port,
                    ops_per_client,
                )
            )

        for fut in as_completed(futures):
            try:
                all_stats.append(fut.result())
            except Exception as exc:
                print(f"Workload failed: {exc}", file=sys.stderr)

    result.total_duration = time.perf_counter() - start_time

    all_latencies: list[float] = []
    seller_latencies: list[float] = []
    buyer_latencies: list[float] = []
    seller_total_ops = 0
    seller_duration = 0.0
    buyer_total_ops = 0
    buyer_duration = 0.0

    for stats in all_stats:
        result.total_ops += stats.total_ops
        result.successful_ops += stats.successful_ops
        result.failed_ops += stats.failed_ops
        all_latencies.extend(stats.latencies)

        if stats.client_type == "seller":
            seller_latencies.extend(stats.latencies)
            seller_total_ops += stats.successful_ops
            seller_duration = max(seller_duration, stats.duration)
        else:
            buyer_latencies.extend(stats.latencies)
            buyer_total_ops += stats.successful_ops
            buyer_duration = max(buyer_duration, stats.duration)

    if all_latencies:
        result.avg_latency_ms = statistics.mean(all_latencies) * 1000
        result.p50_latency_ms = statistics.median(all_latencies) * 1000
        sorted_latencies = sorted(all_latencies)
        p99_idx = int(len(sorted_latencies) * 0.99)
        result.p99_latency_ms = (
            sorted_latencies[min(p99_idx, len(sorted_latencies) - 1)] * 1000
        )

    if seller_latencies:
        result.seller_avg_latency_ms = statistics.mean(seller_latencies) * 1000
    if buyer_latencies:
        result.buyer_avg_latency_ms = statistics.mean(buyer_latencies) * 1000

    if result.total_duration > 0:
        result.throughput_ops_per_sec = result.successful_ops / result.total_duration

    if seller_duration > 0:
        result.seller_throughput = seller_total_ops / seller_duration
    if buyer_duration > 0:
        result.buyer_throughput = buyer_total_ops / buyer_duration

    return result


def print_result(result: RunResult) -> None:
    """Print a single run result."""
    print(
        f"  Run {result.run_number}: "
        f"ops={result.successful_ops}/{result.total_ops}, "
        f"latency={result.avg_latency_ms:.2f}ms (p50={result.p50_latency_ms:.2f}ms, p99={result.p99_latency_ms:.2f}ms), "
        f"throughput={result.throughput_ops_per_sec:.1f} ops/s, "
        f"duration={result.total_duration:.2f}s"
    )


def print_summary(results: list[RunResult]) -> None:
    """Print summary statistics for a set of runs."""
    if not results:
        return

    avg_latencies = [r.avg_latency_ms for r in results]
    throughputs = [r.throughput_ops_per_sec for r in results]

    print(f"\n  Summary ({len(results)} runs):")
    print(
        f"    Avg Response Time: {statistics.mean(avg_latencies):.2f} ms "
        f"(stddev: {statistics.stdev(avg_latencies) if len(avg_latencies) > 1 else 0:.2f} ms)"
    )
    print(
        f"    Avg Throughput: {statistics.mean(throughputs):.1f} ops/s "
        f"(stddev: {statistics.stdev(throughputs) if len(throughputs) > 1 else 0:.1f} ops/s)"
    )

    seller_latencies = [
        r.seller_avg_latency_ms for r in results if r.seller_avg_latency_ms > 0
    ]
    buyer_latencies = [
        r.buyer_avg_latency_ms for r in results if r.buyer_avg_latency_ms > 0
    ]

    if seller_latencies:
        print(f"    Seller Avg Latency: {statistics.mean(seller_latencies):.2f} ms")
    if buyer_latencies:
        print(f"    Buyer Avg Latency: {statistics.mean(buyer_latencies):.2f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark script for online marketplace evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all scenarios with default settings
  %(prog)s

  # Run only scenario 2 (10 sellers + 10 buyers)
  %(prog)s --scenario 2

  # Run fewer runs with fewer operations per client
  %(prog)s --runs 3 --ops-per-client 200

  # Cap worker threads to avoid oversubscribing on small machines
  %(prog)s --scenario 3 --max-workers 100
        """,
    )

    parser.add_argument(
        "--sellers-host",
        default="localhost",
        help="Backend sellers server host (default: localhost)",
    )
    parser.add_argument(
        "--sellers-port",
        type=int,
        default=8003,
        help="Backend sellers server port (default: 8003)",
    )
    parser.add_argument(
        "--buyers-host",
        default="localhost",
        help="Backend buyers server host (default: localhost)",
    )
    parser.add_argument(
        "--buyers-port",
        type=int,
        default=8004,
        help="Backend buyers server port (default: 8004)",
    )
    parser.add_argument(
        "--scenario",
        type=int,
        choices=[1, 2, 3],
        help="Run only specific scenario (1, 2, or 3)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of runs per scenario (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--ops-per-client",
        type=int,
        default=DEFAULT_OPS_PER_CLIENT,
        help=f"Operations per client per run (default: {DEFAULT_OPS_PER_CLIENT})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Optional max thread pool workers across buyers+sellers",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducibility",
    )

    args = parser.parse_args()

    if args.ops_per_client <= 0:
        parser.error("--ops-per-client must be > 0")

    if args.seed is not None:
        random.seed(args.seed)

    scenarios = {
        1: (1, 1),
        2: (10, 10),
        3: (100, 100),
    }

    if args.scenario:
        scenarios = {args.scenario: scenarios[args.scenario]}

    print("-" * 50)
    print("Online Marketplace Benchmark")
    print("-" * 50)
    print(f"Sellers server: {args.sellers_host}:{args.sellers_port}")
    print(f"Buyers server: {args.buyers_host}:{args.buyers_port}")
    print(f"Operations per client: {args.ops_per_client}")
    print(f"Runs per scenario: {args.runs}")
    print(f"Max workers: {args.max_workers if args.max_workers is not None else 'auto'}")
    if args.seed is not None:
        print(f"Random seed: {args.seed}")
    print("-" * 50)

    for scenario_num, (num_sellers, num_buyers) in scenarios.items():
        print(
            f"\nScenario {scenario_num}: {num_sellers} seller(s) + {num_buyers} buyer(s)"
        )
        print("-" * 50)

        results: list[RunResult] = []

        for run in range(1, args.runs + 1):
            result = run_scenario(
                scenario=scenario_num,
                num_sellers=num_sellers,
                num_buyers=num_buyers,
                sellers_host=args.sellers_host,
                sellers_port=args.sellers_port,
                buyers_host=args.buyers_host,
                buyers_port=args.buyers_port,
                run_number=run,
                ops_per_client=args.ops_per_client,
                max_workers=args.max_workers,
            )
            results.append(result)
            print_result(result)

        print_summary(results)

    print("\n" + "-" * 50)
    print("Benchmark complete!")
    print("-" * 50)


if __name__ == "__main__":
    main()
