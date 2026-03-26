"""Buyer client wrapper.

Extends MarketplaceClient with buyer-specific API methods.
"""

from typing import Any

from clients.common.client import MarketplaceClient


class BuyerClient(MarketplaceClient):
    """Client for the backend-buyers server (default port 8004)."""

    def __init__(self, host: str = "localhost", port: int = 8004):
        super().__init__(host, port)

    # -- API actions wrappers (used by CLI + benchmark) --

    def create_account(
        self, *, buyer_name: str, login_name: str, password: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/accounts",
            payload={
                "buyer_name": buyer_name,
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

    def search_items(
        self, *, session_id: str, item_category: int, keywords: list[str]
    ) -> dict[str, Any]:
        params: list[tuple[str, Any]] = [("item_category", item_category)]
        params.extend(("keywords", keyword) for keyword in keywords)
        return self.request(
            "GET",
            "/items/search",
            params=params,
            session_id=session_id,
        )

    def get_item(
        self, *, session_id: str, item_category: int, item_id: int
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/items/{item_category}/{item_id}",
            session_id=session_id,
        )

    def add_to_cart(
        self, *, session_id: str, item_category: int, item_id: int, quantity: int
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/cart/items",
            payload={
                "item_category": item_category,
                "item_id": item_id,
                "quantity": quantity,
            },
            session_id=session_id,
        )

    def remove_from_cart(
        self, *, session_id: str, item_category: int, item_id: int, quantity: int
    ) -> dict[str, Any]:
        return self.request(
            "DELETE",
            f"/cart/items/{item_category}/{item_id}",
            params={"quantity": quantity},
            session_id=session_id,
        )

    def display_cart(self, *, session_id: str) -> dict[str, Any]:
        return self.request("GET", "/cart", session_id=session_id)

    def save_cart(self, *, session_id: str) -> dict[str, Any]:
        return self.request("POST", "/cart/save", payload={}, session_id=session_id)

    def clear_cart(self, *, session_id: str) -> dict[str, Any]:
        return self.request("DELETE", "/cart", session_id=session_id)

    def provide_feedback(
        self, *, session_id: str, item_category: int, item_id: int, vote: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/feedback",
            payload={"item_category": item_category, "item_id": item_id, "vote": vote},
            session_id=session_id,
        )

    def get_seller_rating(self, *, session_id: str, seller_id: int) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/sellers/{seller_id}/rating",
            session_id=session_id,
        )

    def purchases(self, *, session_id: str) -> dict[str, Any]:
        return self.request("GET", "/purchases", session_id=session_id)

    def make_purchase(
        self,
        *,
        session_id: str,
        card_holder_name: str,
        credit_card_number: str,
        expiration_date: str,
        security_code: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/purchases",
            payload={
                "card_holder_name": card_holder_name,
                "credit_card_number": credit_card_number,
                "expiration_date": expiration_date,
                "security_code": security_code,
            },
            session_id=session_id,
        )
