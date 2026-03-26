"""Seller client wrapper.

Extends MarketplaceClient with seller-specific API methods.
"""

from typing import Any

from clients.common.client import MarketplaceClient


class SellerClient(MarketplaceClient):
    """Client for the backend-sellers server (default port 8003)."""

    def __init__(self, host: str = "localhost", port: int = 8003):
        super().__init__(host, port)

    # -- API actions wrappers (used by CLI + benchmark) --

    def create_account(
        self, *, seller_name: str, login_name: str, password: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/accounts",
            payload={
                "seller_name": seller_name,
                "login_name": login_name,
                "password": password,
            },
        )

    def login(self, *, login_name: str, password: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/sessions/login",
            payload={"login_name": login_name, "password": password},
        )

    def logout(self, *, session_id: str) -> dict[str, Any]:
        return self.request("POST", "/sessions/logout", payload={}, session_id=session_id)

    def get_rating(self, *, session_id: str) -> dict[str, Any]:
        return self.request("GET", "/me/rating", session_id=session_id)

    def register_item(
        self,
        *,
        session_id: str,
        item_name: str,
        item_category: int,
        keywords: list[str],
        condition: str,
        sale_price: float,
        quantity: int,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/items",
            payload={
                "item_name": item_name,
                "item_category": item_category,
                "keywords": keywords,
                "condition": condition,
                "sale_price": sale_price,
                "quantity": quantity,
            },
            session_id=session_id,
        )

    def change_price(
        self, *, session_id: str, item_category: int, item_id: int, sale_price: float
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/items/{item_category}/{item_id}/price",
            payload={"sale_price": sale_price},
            session_id=session_id,
        )

    def update_units(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
        quantity: int | None = None,
        delta: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if quantity is not None:
            payload["quantity"] = quantity
        if delta is not None:
            payload["quantity_delta"] = delta
        return self.request(
            "PATCH",
            f"/items/{item_category}/{item_id}/quantity",
            payload=payload,
            session_id=session_id,
        )

    def display_items(self, *, session_id: str) -> dict[str, Any]:
        return self.request("GET", "/items", session_id=session_id)
