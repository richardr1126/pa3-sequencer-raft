#!/usr/bin/env python3
"""Seller CLI client for the online marketplace.

Connects to backend-sellers server to perform seller operations.

Usage:
    # Create account
    python clients/sellers/cli.py create-account --name "Alice" --login alice --password secret

    # Login and get session ID
    python clients/sellers/cli.py login --login alice --password secret

    # Protected operations (require --session)
    python clients/sellers/cli.py --session <sid> register-item --name "iPhone 15" --category 1 \
        --keywords phone apple ios --condition new --price 999.99 --quantity 10

    python clients/sellers/cli.py --session <sid> display-items
    python clients/sellers/cli.py --session <sid> change-price --category 1 --item 12345 --price 899.99
    python clients/sellers/cli.py --session <sid> update-units --category 1 --item 12345 --quantity 5
    python clients/sellers/cli.py --session <sid> rating
    python clients/sellers/cli.py --session <sid> logout
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

# Add parent directory to path for imports (allows running from this folder)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from clients.sellers.client import SellerClient


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


def cmd_create_account(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle create-account command."""
    response = client.create_account(
        seller_name=args.name,
        login_name=args.login,
        password=args.password,
    )
    print_response(response, args.verbose)


def cmd_login(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle login command."""
    response = client.login(login_name=args.login, password=args.password)
    print_response(response, args.verbose)


def cmd_logout(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle logout command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)
    response = client.logout(session_id=args.session)
    print_response(response, args.verbose)


def cmd_rating(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle rating command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)
    response = client.get_rating(session_id=args.session)
    print_response(response, args.verbose)


def cmd_register_item(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle register-item command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)

    keywords = args.keywords if args.keywords else []

    response = client.register_item(
        session_id=args.session,
        item_name=args.name,
        item_category=args.category,
        keywords=keywords,
        condition=args.condition,
        sale_price=args.price,
        quantity=args.quantity,
    )
    print_response(response, args.verbose)


def cmd_change_price(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle change-price command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)

    response = client.change_price(
        session_id=args.session,
        item_category=args.category,
        item_id=args.item,
        sale_price=args.price,
    )
    print_response(response, args.verbose)


def cmd_update_units(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle update-units command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)

    if args.quantity is None and args.delta is None:
        print("Error: must specify --quantity or --delta", file=sys.stderr)
        sys.exit(1)

    response = client.update_units(
        session_id=args.session,
        item_category=args.category,
        item_id=args.item,
        quantity=args.quantity,
        delta=args.delta,
    )
    print_response(response, args.verbose)


def cmd_display_items(client: SellerClient, args: argparse.Namespace) -> None:
    """Handle display-items command."""
    if not args.session:
        print("Error: --session is required for this command", file=sys.stderr)
        sys.exit(1)
    response = client.display_items(session_id=args.session)

    if args.verbose:
        print_response(response, args.verbose)
        return

    if response.get("ok"):
        items = response.get("payload", {}).get("items", [])
        if not items:
            print("No items for sale")
        else:
            for item in items:
                print(
                    f"  [{item['item_category']},{item['item_id']}] {item['item_name']}"
                )
                print(
                    f"    Condition: {item['condition']}, Price: ${item['sale_price']:.2f}, Qty: {item['quantity']}"
                )
                print(f"    Keywords: {', '.join(item.get('keywords', []))}")
                print(f"    Feedback: +{item['thumbs_up']}/-{item['thumbs_down']}")
    else:
        print_response(response, args.verbose)


def main():
    parser = argparse.ArgumentParser(
        description="Seller CLI client for online marketplace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new seller account
  %(prog)s create-account --name "Alice Store" --login alice --password secret

  # Login (returns session_id)
  %(prog)s login --login alice --password secret

  # Register an item for sale (requires session)
  %(prog)s --session <sid> register-item --name "iPhone 15" --category 1 \\
      --keywords phone apple --condition new --price 999.99 --quantity 10

  # View your items
  %(prog)s --session <sid> display-items

  # Update price
  %(prog)s --session <sid> change-price --category 1 --item 12345 --price 899.99
        """,
    )

    # Global options
    parser.add_argument(
        "--host",
        default="localhost",
        help="Backend sellers server host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8003,
        help="Backend sellers server port (default: 8003)",
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
        "create-account", help="Create a new seller account"
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

    # rating
    p_rating = subparsers.add_parser("rating", help="Get your seller rating")
    p_rating.set_defaults(func=cmd_rating)

    # register-item
    p_register = subparsers.add_parser(
        "register-item", help="Register a new item for sale"
    )
    p_register.add_argument("--name", required=True, help="Item name (up to 32 chars)")
    p_register.add_argument(
        "--category", type=int, required=True, help="Item category (integer)"
    )
    p_register.add_argument(
        "--keywords", nargs="*", help="Up to 5 keywords (8 chars each)"
    )
    p_register.add_argument(
        "--condition", required=True, choices=["new", "used"], help="Item condition"
    )
    p_register.add_argument("--price", type=float, required=True, help="Sale price")
    p_register.add_argument(
        "--quantity", type=int, required=True, help="Quantity available"
    )
    p_register.set_defaults(func=cmd_register_item)

    # change-price
    p_price = subparsers.add_parser("change-price", help="Change an item's price")
    p_price.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_price.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_price.add_argument("--price", type=float, required=True, help="New sale price")
    p_price.set_defaults(func=cmd_change_price)

    # update-units
    p_units = subparsers.add_parser("update-units", help="Update item quantity")
    p_units.add_argument(
        "--category",
        type=int,
        required=True,
        help="Item category (integer)",
    )
    p_units.add_argument("--item", type=int, required=True, help="Item ID (integer)")
    p_units.add_argument("--quantity", type=int, help="Set absolute quantity")
    p_units.add_argument("--delta", type=int, help="Change quantity by delta (+/-)")
    p_units.set_defaults(func=cmd_update_units)

    # display-items
    p_display = subparsers.add_parser("display-items", help="List your items for sale")
    p_display.set_defaults(func=cmd_display_items)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = SellerClient(args.host, args.port)

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
