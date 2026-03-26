#!/usr/bin/env python3
"""Buyer CLI client for the online marketplace.

Connects to backend-buyers server to perform buyer operations.

Usage:
    # Create account
    python clients/buyers/cli.py create-account --name "Bob" --login bob --password secret

    # Login and get session ID
    python clients/buyers/cli.py login --login bob --password secret

    # Protected operations (require --session)
    python clients/buyers/cli.py --session <sid> search --category 1 --keywords phone apple
    python clients/buyers/cli.py --session <sid> get-item --category 1 --item 12345
    python clients/buyers/cli.py --session <sid> add-to-cart --category 1 --item 12345 --quantity 2
    python clients/buyers/cli.py --session <sid> cart
    python clients/buyers/cli.py --session <sid> save-cart
    python clients/buyers/cli.py --session <sid> clear-cart
    python clients/buyers/cli.py --session <sid> remove-from-cart --category 1 --item 12345 --quantity 1
    python clients/buyers/cli.py --session <sid> feedback --category 1 --item 12345 --vote up
    python clients/buyers/cli.py --session <sid> seller-rating --seller 1
    python clients/buyers/cli.py --session <sid> make-purchase --name "Bob" --card-number 4111111111111111 --expiry 12/30 --security-code 123
    python clients/buyers/cli.py --session <sid> purchases
    python clients/buyers/cli.py --session <sid> logout
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

# Add parent directory to path for imports (allows running from this folder)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clients.buyers.client import BuyerClient


def print_response(response: dict[str, Any], verbose: bool = False) -> None:
    """Print response in a user-friendly format."""
    if verbose:
        print(json.dumps(response, indent=2))
        return

    if response.get("ok"):
        payload = response.get("payload", {})
        if payload:
            print(json.dumps(payload, indent=2))
        else:
            print("Success")
    else:
        error = response.get("error", {})
        code = error.get("code", "ERROR")
        message = error.get("message", "Unknown error")
        print(f"Error [{code}]: {message}", file=sys.stderr)
        sys.exit(1)


def require_session(args: argparse.Namespace) -> str:
    """Ensure session is provided, exit if not."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)
    return args.session


def cmd_create_account(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle create-account command."""
    response = client.create_account(
        buyer_name=args.name,
        login_name=args.login,
        password=args.password,
    )
    print_response(response, args.verbose)


def cmd_login(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle login command."""
    response = client.login(login_name=args.login, password=args.password)
    print_response(response, args.verbose)


def cmd_logout(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle logout command."""
    session = require_session(args)
    response = client.logout(session_id=session)
    print_response(response, args.verbose)


def cmd_search(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle search command."""
    session = require_session(args)
    keywords = args.keywords if args.keywords else []

    response = client.search_items(
        session_id=session,
        item_category=args.category,
        keywords=keywords,
    )

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        items = response.get("payload", {}).get("items", [])
        if not items:
            print("No items found")
        else:
            print(f"Found {len(items)} item(s):\n")
            for item in items:
                match_count = item.get("keyword_match_count", 0)
                print(
                    f"  [{item['item_category']},{item['item_id']}] {item['item_name']} (matches: {match_count})"
                )
                print(
                    f"    Condition: {item['condition']}, Price: ${item['sale_price']:.2f}, Qty: {item['quantity']}"
                )
                print(f"    Keywords: {', '.join(item.get('keywords', []))}")
                print(
                    f"    Seller: {item['seller_id']}, Feedback: +{item['thumbs_up']}/-{item['thumbs_down']}"
                )
                print()
    else:
        print_response(response, args.verbose)


def cmd_get_item(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle get-item command."""
    session = require_session(args)

    response = client.get_item(
        session_id=session, item_category=args.category, item_id=args.item
    )

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        item = response.get("payload", {}).get("item")
        if item:
            print(
                f"Item [{item['item_category']},{item['item_id']}]: {item['item_name']}"
            )
            print(f"  Condition: {item['condition']}")
            print(f"  Price: ${item['sale_price']:.2f}")
            print(f"  Available: {item['quantity']}")
            print(f"  Keywords: {', '.join(item.get('keywords', []))}")
            print(f"  Seller ID: {item['seller_id']}")
            print(f"  Feedback: +{item['thumbs_up']}/-{item['thumbs_down']}")
        else:
            print("Item not found")
    else:
        print_response(response, args.verbose)


def cmd_add_to_cart(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle add-to-cart command."""
    session = require_session(args)

    response = client.add_to_cart(
        session_id=session,
        item_category=args.category,
        item_id=args.item,
        quantity=args.quantity,
    )
    print_response(response, args.verbose)


def cmd_remove_from_cart(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle remove-from-cart command."""
    session = require_session(args)

    response = client.remove_from_cart(
        session_id=session,
        item_category=args.category,
        item_id=args.item,
        quantity=args.quantity,
    )
    print_response(response, args.verbose)


def cmd_cart(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle cart (display-cart) command."""
    session = require_session(args)

    response = client.display_cart(session_id=session)

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        items = response.get("payload", {}).get("items", [])
        if not items:
            print("Cart is empty")
        else:
            total = 0.0
            print("Shopping Cart:\n")
            for item in items:
                subtotal = item["sale_price"] * item["quantity"]
                total += subtotal
                status = " [DELETED]" if item.get("deleted") else ""
                print(
                    f"  [{item['item_category']},{item['item_id']}] {item['item_name']}{status}"
                )
                print(
                    f"    Qty: {item['quantity']} x ${item['sale_price']:.2f} = ${subtotal:.2f}"
                )
                if not item.get("deleted"):
                    print(f"    Available: {item['available']}")
                print()
            print(f"  Total: ${total:.2f}")
    else:
        print_response(response, args.verbose)


def cmd_save_cart(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle save-cart command."""
    session = require_session(args)
    response = client.save_cart(session_id=session)
    print_response(response, args.verbose)


def cmd_clear_cart(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle clear-cart command."""
    session = require_session(args)
    response = client.clear_cart(session_id=session)
    print_response(response, args.verbose)


def cmd_feedback(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle feedback command."""
    session = require_session(args)

    response = client.provide_feedback(
        session_id=session,
        item_category=args.category,
        item_id=args.item,
        vote=args.vote,
    )
    print_response(response, args.verbose)


def cmd_seller_rating(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle seller-rating command."""
    session = require_session(args)

    response = client.get_seller_rating(session_id=session, seller_id=args.seller)

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        payload = response.get("payload", {})
        print(f"Seller {payload['seller_id']}: {payload['seller_name']}")
        print(f"  Rating: +{payload['thumbs_up']}/-{payload['thumbs_down']}")
    else:
        print_response(response, args.verbose)


def cmd_purchases(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle purchases command."""
    session = require_session(args)

    response = client.purchases(session_id=session)

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        purchases = response.get("payload", {}).get("purchases", [])
        if not purchases:
            print("No purchase history")
        else:
            print(f"Purchase History ({len(purchases)} items):\n")
            for p in purchases:
                print(f"  Purchase #{p['purchase_id']}: [{p['item_category']},{p['item_id']}] x{p['quantity']}")
                print(f"    Purchased: {p['purchased_at']}")
                print()
    else:
        print_response(response, args.verbose)


def cmd_make_purchase(client: BuyerClient, args: argparse.Namespace) -> None:
    """Handle make-purchase command."""
    session = require_session(args)
    response = client.make_purchase(
        session_id=session,
        card_holder_name=args.name,
        credit_card_number=args.card_number,
        expiration_date=args.expiry,
        security_code=args.security_code,
    )
    print_response(response, args.verbose)


def main():
    parser = argparse.ArgumentParser(
        description="Buyer CLI client for online marketplace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new buyer account
  %(prog)s create-account --name "Bob Smith" --login bob --password secret

  # Login (returns session_id)
  %(prog)s login --login bob --password secret

  # Search for items (requires session)
  %(prog)s --session <sid> search --category 1 --keywords phone apple

  # Get item details
  %(prog)s --session <sid> get-item --category 1 --item 12345

  # Add item to cart
  %(prog)s --session <sid> add-to-cart --category 1 --item 12345 --quantity 2

  # View cart
  %(prog)s --session <sid> cart

  # Save cart for later sessions
  %(prog)s --session <sid> save-cart

  # Provide feedback on an item
  %(prog)s --session <sid> feedback --category 1 --item 12345 --vote up

  # Complete purchase using payment details
  %(prog)s --session <sid> make-purchase --name "Bob Smith" --card-number 4111111111111111 --expiry 12/30 --security-code 123
        """,
    )

    # Global options
    parser.add_argument(
        "--host",
        default="localhost",
        help="Backend buyers server host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8004,
        help="Backend buyers server port (default: 8004)",
    )
    parser.add_argument(
        "--session",
        "-s",
        help="Session ID for authenticated requests",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print full JSON response",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # create-account
    p_create = subparsers.add_parser(
        "create-account", help="Create a new buyer account"
    )
    p_create.add_argument("--name", required=True, help="Display name (up to 32 chars)")
    p_create.add_argument("--login", required=True, help="Login username")
    p_create.add_argument("--password", required=True, help="Password")
    p_create.set_defaults(func=cmd_create_account)

    # login
    p_login = subparsers.add_parser("login", help="Login and get session ID")
    p_login.add_argument("--login", required=True, help="Login username")
    p_login.add_argument("--password", required=True, help="Password")
    p_login.set_defaults(func=cmd_login)

    # logout
    p_logout = subparsers.add_parser("logout", help="End current session")
    p_logout.set_defaults(func=cmd_logout)

    # search
    p_search = subparsers.add_parser("search", help="Search items for sale")
    p_search.add_argument(
        "--category", type=int, required=True, help="Item category to search"
    )
    p_search.add_argument("--keywords", nargs="*", help="Keywords to match (up to 5)")
    p_search.set_defaults(func=cmd_search)

    # get-item
    p_get = subparsers.add_parser("get-item", help="Get item details")
    p_get.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_get.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_get.set_defaults(func=cmd_get_item)

    # add-to-cart
    p_add = subparsers.add_parser("add-to-cart", help="Add item to cart")
    p_add.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_add.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_add.add_argument(
        "--quantity", type=int, default=1, help="Quantity to add (default: 1)"
    )
    p_add.set_defaults(func=cmd_add_to_cart)

    # remove-from-cart
    p_remove = subparsers.add_parser("remove-from-cart", help="Remove item from cart")
    p_remove.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_remove.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_remove.add_argument(
        "--quantity", type=int, default=1, help="Quantity to remove (default: 1)"
    )
    p_remove.set_defaults(func=cmd_remove_from_cart)

    # cart (display-cart)
    p_cart = subparsers.add_parser("cart", help="Display shopping cart")
    p_cart.set_defaults(func=cmd_cart)

    # save-cart
    p_save = subparsers.add_parser("save-cart", help="Save cart for future sessions")
    p_save.set_defaults(func=cmd_save_cart)

    # clear-cart
    p_clear = subparsers.add_parser("clear-cart", help="Clear shopping cart")
    p_clear.set_defaults(func=cmd_clear_cart)

    # feedback
    p_feedback = subparsers.add_parser("feedback", help="Provide feedback on an item")
    p_feedback.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_feedback.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_feedback.add_argument(
        "--vote", required=True, choices=["up", "down"], help="Thumbs up or down"
    )
    p_feedback.set_defaults(func=cmd_feedback)

    # seller-rating
    p_seller = subparsers.add_parser("seller-rating", help="Get seller rating")
    p_seller.add_argument("--seller", type=int, required=True, help="Seller ID")
    p_seller.set_defaults(func=cmd_seller_rating)

    # purchases
    p_purchases = subparsers.add_parser("purchases", help="Get purchase history")
    p_purchases.set_defaults(func=cmd_purchases)

    # make-purchase
    p_make_purchase = subparsers.add_parser(
        "make-purchase",
        help="Purchase all items currently in cart",
    )
    p_make_purchase.add_argument("--name", required=True, help="Card holder name")
    p_make_purchase.add_argument(
        "--card-number",
        required=True,
        help="Credit card number",
    )
    p_make_purchase.add_argument(
        "--expiry",
        required=True,
        help="Expiration date (MM/YY or MM/YYYY)",
    )
    p_make_purchase.add_argument(
        "--security-code",
        required=True,
        help="Card security code (CVV/CVC)",
    )
    p_make_purchase.set_defaults(func=cmd_make_purchase)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = BuyerClient(args.host, args.port)

    try:
        args.func(client, args)
    except ConnectionRefusedError:
        print(
            f"Error: Cannot connect to server at {args.host}:{args.port}",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    finally:
        client.close()


if __name__ == "__main__":
    main()
