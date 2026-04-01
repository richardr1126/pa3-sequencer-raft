"""Buyer domain service for FastAPI routes."""

from datetime import datetime
from typing import Any, Callable

import grpc

from backends.common.errors import ApiError
from backends.common.financial_client import (
    FinancialClient,
    FinancialInvalidCardError,
    FinancialUnavailableError,
)


class BuyerService:
    """Buyer operations backed by customer/product DB gRPC services."""

    SESSION_TOUCH_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        customer_db: Any,
        product_db: Any,
        financial_client: FinancialClient | None = None,
    ):
        self.customer_db = customer_db
        self.product_db = product_db
        self.financial_client = financial_client

    # -- Shared buyer service helpers --

    @staticmethod
    def _raise_rpc_error(exc: grpc.RpcError) -> None:
        if exc.code() == grpc.StatusCode.UNAVAILABLE:
            raise ApiError(status_code=503, code="DB_ERROR", message=str(exc.details() or exc)) from exc
        raise ApiError(status_code=400, code="ERROR", message=str(exc.details() or exc)) from exc

    def _customer_call(self, method: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        try:
            return method(**kwargs)
        except grpc.RpcError as exc:
            self._raise_rpc_error(exc)

    def _product_call(self, method: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        try:
            return method(**kwargs)
        except grpc.RpcError as exc:
            self._raise_rpc_error(exc)

    @staticmethod
    def _parse_iso_datetime(value: str | None) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    @staticmethod
    def _require_fields(payload: dict[str, Any], fields: list[str]) -> None:
        missing = [f for f in fields if payload.get(f) is None]
        if missing:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message=f"Missing required fields: {', '.join(missing)}",
            )

    def validate_buyer_session(self, session_id: str) -> dict[str, Any]:
        """Validate session and return buyer record."""
        if not session_id:
            raise ApiError(
                status_code=401,
                code="AUTH_REQUIRED",
                message="Must be logged in to perform this action",
            )

        session_result = self._customer_call(
            self.customer_db.validate_buyer_session,
            session_id=session_id,
        )

        if not session_result.get("valid"):
            reason = session_result.get("reason", "Invalid session")
            if reason == "expired":
                try:
                    self._customer_call(
                        self.customer_db.cleanup_buyer_session,
                        session_id=session_id,
                    )
                except ApiError:
                    pass
            raise ApiError(status_code=401, code="INVALID_SESSION", message=reason)

        should_touch = True
        last_activity_at = self._parse_iso_datetime(session_result.get("last_activity_at"))
        if last_activity_at is not None:
            # Avoid touching on every request; this reduces DB write amplification.
            should_touch = (
                datetime.utcnow() - last_activity_at
            ).total_seconds() >= self.SESSION_TOUCH_INTERVAL_SECONDS

        if should_touch:
            self._customer_call(
                self.customer_db.touch_buyer_session,
                session_id=session_id,
            )

        buyer = session_result.get("buyer")
        if not isinstance(buyer, dict):
            raise ApiError(
                status_code=401,
                code="INVALID_SESSION",
                message="Invalid session",
            )
        return buyer

    # -- Public API --

    def create_account(self, *, buyer_name: str, login_name: str, password: str) -> dict[str, Any]:
        payload = {
            "buyer_name": buyer_name,
            "login_name": login_name,
            "password": password,
        }
        self._require_fields(payload, ["buyer_name", "login_name", "password"])
        result = self._customer_call(
            self.customer_db.create_buyer,
            buyer_name=buyer_name,
            login_name=login_name,
            password=password,
        )
        return {"buyer_id": result["buyer_id"], "buyer_name": result["buyer_name"]}

    def login(self, *, login_name: str, password: str) -> dict[str, Any]:
        self._require_fields(
            {"login_name": login_name, "password": password},
            ["login_name", "password"],
        )

        auth_result = self._customer_call(
            self.customer_db.authenticate_buyer,
            login_name=login_name,
            password=password,
        )

        if not auth_result.get("authenticated"):
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Invalid login credentials",
            )

        buyer = auth_result["buyer"]
        session_result = self._customer_call(
            self.customer_db.create_buyer_session,
            buyer_id=buyer["buyer_id"],
        )
        session_id = session_result["session_id"]

        self._customer_call(
            self.customer_db.load_saved_cart,
            session_id=session_id,
            buyer_id=buyer["buyer_id"],
        )

        return {
            "session_id": session_id,
            "buyer_id": buyer["buyer_id"],
            "buyer_name": buyer["buyer_name"],
        }

    # -- Protected API --

    def logout(self, *, session_id: str) -> dict[str, Any]:
        self._customer_call(self.customer_db.delete_buyer_session, session_id=session_id)
        return {"logged_out": True}

    def search_items(self, *, item_category: int, keywords: list[str]) -> dict[str, Any]:
        if item_category is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required field: item_category",
            )
        result = self._product_call(
            self.product_db.search_items,
            item_category=item_category,
            keywords=keywords,
        )
        return {"items": result["items"]}

    def get_item(self, *, item_category: int, item_id: int) -> dict[str, Any]:
        if item_category is None or item_id is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id",
            )

        result = self._product_call(
            self.product_db.get_item,
            item_category=item_category,
            item_id=item_id,
        )
        if result["item"] is None:
            raise ApiError(status_code=400, code="ERROR", message=f"Item {item_id} not found")
        return {"item": result["item"]}

    def add_to_cart(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
        quantity: int = 1,
    ) -> dict[str, Any]:
        if item_category is None or item_id is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id",
            )
        if quantity <= 0:
            raise ApiError(status_code=400, code="ERROR", message="Quantity must be positive")

        item_result = self._product_call(
            self.product_db.get_item,
            item_category=item_category,
            item_id=item_id,
        )
        if item_result["item"] is None:
            raise ApiError(status_code=400, code="ERROR", message=f"Item {item_id} not found")

        item = item_result["item"]
        if item["quantity"] < quantity:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message=(
                    "Insufficient quantity available. "
                    f"Requested: {quantity}, Available: {item['quantity']}"
                ),
            )

        cart_result = self._customer_call(
            self.customer_db.get_session_cart,
            session_id=session_id,
        )

        current_qty = 0
        for cart_item in cart_result["items"]:
            if (
                cart_item["item_category"] == item_category
                and cart_item["item_id"] == item_id
            ):
                current_qty = cart_item["quantity"]
                break

        new_qty = current_qty + quantity
        if new_qty > item["quantity"]:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message=(
                    f"Cannot add {quantity} more. Cart has {current_qty}, "
                    f"available: {item['quantity']}"
                ),
            )

        self._customer_call(
            self.customer_db.set_session_cart_item,
            session_id=session_id,
            item_category=item_category,
            item_id=item_id,
            quantity=new_qty,
        )

        return {
            "item_category": item_category,
            "item_id": item_id,
            "quantity_in_cart": new_qty,
        }

    def remove_from_cart(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
        quantity: int = 1,
    ) -> dict[str, Any]:
        if item_category is None or item_id is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id",
            )
        if quantity <= 0:
            raise ApiError(status_code=400, code="ERROR", message="Quantity must be positive")

        cart_result = self._customer_call(
            self.customer_db.get_session_cart,
            session_id=session_id,
        )

        current_qty = 0
        for cart_item in cart_result["items"]:
            if (
                cart_item["item_category"] == item_category
                and cart_item["item_id"] == item_id
            ):
                current_qty = cart_item["quantity"]
                break

        if current_qty == 0:
            raise ApiError(status_code=400, code="ERROR", message=f"Item {item_id} not in cart")

        new_qty = max(0, current_qty - quantity)
        self._customer_call(
            self.customer_db.set_session_cart_item,
            session_id=session_id,
            item_category=item_category,
            item_id=item_id,
            quantity=new_qty,
        )

        return {
            "item_category": item_category,
            "item_id": item_id,
            "quantity_in_cart": new_qty,
            "removed": new_qty == 0,
        }

    def display_cart(self, *, session_id: str) -> dict[str, Any]:
        cart_result = self._customer_call(
            self.customer_db.get_session_cart,
            session_id=session_id,
        )

        items: list[dict[str, Any]] = []
        for cart_item in cart_result["items"]:
            # Include deleted items so cart UI can show stale entries explicitly.
            item_result = self._product_call(
                self.product_db.get_item,
                item_category=cart_item["item_category"],
                item_id=cart_item["item_id"],
                include_deleted=True,
            )

            item_info = item_result.get("item")
            items.append(
                {
                    "item_category": cart_item["item_category"],
                    "item_id": cart_item["item_id"],
                    "quantity": cart_item["quantity"],
                    "item_name": item_info["item_name"] if item_info else "(unknown)",
                    "sale_price": item_info["sale_price"] if item_info else 0,
                    "available": item_info["quantity"] if item_info else 0,
                    "deleted": item_info["deleted_at"] is not None if item_info else True,
                }
            )

        return {"items": items}

    def save_cart(self, *, session_id: str, buyer: dict[str, Any]) -> dict[str, Any]:
        result = self._customer_call(
            self.customer_db.save_cart,
            session_id=session_id,
            buyer_id=buyer["buyer_id"],
        )
        return {"saved_count": result["saved_count"]}

    def clear_cart(self, *, session_id: str) -> dict[str, Any]:
        result = self._customer_call(
            self.customer_db.clear_session_cart,
            session_id=session_id,
        )
        return {"cleared_count": result["cleared_count"]}

    def provide_feedback(
        self,
        *,
        buyer: dict[str, Any],
        item_category: int,
        item_id: int,
        vote: str,
    ) -> dict[str, Any]:
        if any(v is None for v in [item_category, item_id, vote]):
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id, vote",
            )
        if vote not in ("up", "down"):
            raise ApiError(status_code=400, code="ERROR", message="Vote must be 'up' or 'down'")

        result = self._product_call(
            self.product_db.add_feedback_vote,
            buyer_id=buyer["buyer_id"],
            item_category=item_category,
            item_id=item_id,
            vote=vote,
        )

        # Keep seller aggregate feedback in sync with per-item votes.
        seller_id = result["seller_id"]
        if vote == "up":
            self._customer_call(
                self.customer_db.update_seller_feedback,
                seller_id=seller_id,
                thumbs_up_delta=1,
            )
        else:
            self._customer_call(
                self.customer_db.update_seller_feedback,
                seller_id=seller_id,
                thumbs_down_delta=1,
            )

        return {
            "item_thumbs_up": result["thumbs_up"],
            "item_thumbs_down": result["thumbs_down"],
        }

    def get_seller_rating(self, *, seller_id: int) -> dict[str, Any]:
        if seller_id is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required field: seller_id",
            )

        result = self._customer_call(self.customer_db.get_seller, seller_id=seller_id)
        if result["seller"] is None:
            raise ApiError(status_code=400, code="ERROR", message=f"Seller {seller_id} not found")

        seller = result["seller"]
        return {
            "seller_id": seller["seller_id"],
            "seller_name": seller["seller_name"],
            "thumbs_up": seller["thumbs_up"],
            "thumbs_down": seller["thumbs_down"],
        }

    def get_purchases(self, *, buyer: dict[str, Any]) -> dict[str, Any]:
        result = self._customer_call(
            self.customer_db.get_purchase_history,
            buyer_id=buyer["buyer_id"],
        )
        return {"purchases": result["purchases"]}

    def make_purchase(
        self,
        *,
        session_id: str,
        buyer: dict[str, Any],
        card_holder_name: str,
        credit_card_number: str,
        expiration_date: str,
        security_code: str,
    ) -> dict[str, Any]:
        self._require_fields(
            {
                "card_holder_name": card_holder_name,
                "credit_card_number": credit_card_number,
                "expiration_date": expiration_date,
                "security_code": security_code,
            },
            [
                "card_holder_name",
                "credit_card_number",
                "expiration_date",
                "security_code",
            ],
        )

        cart_result = self._customer_call(
            self.customer_db.get_session_cart,
            session_id=session_id,
        )
        cart_items = cart_result["items"]
        if not cart_items:
            raise ApiError(status_code=400, code="CART_EMPTY", message="Cart is empty")

        # Validate the full cart before charging so we fail fast on stale items or stock.
        purchase_items: list[dict[str, Any]] = []
        for cart_item in cart_items:
            item_result = self._product_call(
                self.product_db.get_item,
                item_category=cart_item["item_category"],
                item_id=cart_item["item_id"],
                include_deleted=True,
            )
            item = item_result.get("item")
            if item is None or item.get("deleted_at") is not None:
                raise ApiError(
                    status_code=400,
                    code="ITEM_UNAVAILABLE",
                    message=(
                        f"Item [{cart_item['item_category']},{cart_item['item_id']}] is no longer available"
                    ),
                )
            if item["quantity"] < cart_item["quantity"]:
                raise ApiError(
                    status_code=400,
                    code="ITEM_UNAVAILABLE",
                    message=(
                        f"Insufficient stock for item [{cart_item['item_category']},{cart_item['item_id']}]"
                    ),
                )

            purchase_items.append(
                {
                    "item_category": cart_item["item_category"],
                    "item_id": cart_item["item_id"],
                    "quantity": cart_item["quantity"],
                    "item_name": item["item_name"],
                    "sale_price": item["sale_price"],
                    "seller_id": item["seller_id"],
                }
            )

        if self.financial_client is None:
            raise ApiError(
                status_code=503,
                code="FINANCIAL_ERROR",
                message="Financial transactions service is unavailable",
            )

        try:
            outcome = self.financial_client.process_transaction(
                user_name=card_holder_name,
                credit_card_number=credit_card_number,
                expiration_date=expiration_date,
                security_code=security_code,
            )
        except FinancialInvalidCardError as exc:
            raise ApiError(status_code=400, code="INVALID_CARD", message=str(exc)) from exc
        except FinancialUnavailableError as exc:
            raise ApiError(status_code=503, code="FINANCIAL_ERROR", message=str(exc)) from exc

        if outcome == "declined":
            raise ApiError(
                status_code=400,
                code="TRANSACTION_DECLINED",
                message="Transaction declined by financial service",
            )

        # Payment is approved; apply inventory first so later failures can be compensated.
        decremented_items: list[dict[str, Any]] = []
        try:
            for item in purchase_items:
                self._product_call(
                    self.product_db.update_item_quantity,
                    item_category=item["item_category"],
                    item_id=item["item_id"],
                    quantity_delta=-item["quantity"],
                )
                decremented_items.append(item)

            total_quantity = 0
            for item in purchase_items:
                self._customer_call(
                    self.customer_db.add_purchase,
                    buyer_id=buyer["buyer_id"],
                    item_category=item["item_category"],
                    item_id=item["item_id"],
                    quantity=item["quantity"],
                )
                self._customer_call(
                    self.customer_db.increment_seller_items_sold,
                    seller_id=item["seller_id"],
                    amount=item["quantity"],
                )
                total_quantity += item["quantity"]

            self._customer_call(
                self.customer_db.increment_buyer_purchases,
                buyer_id=buyer["buyer_id"],
                amount=total_quantity,
            )
            self._customer_call(
                self.customer_db.clear_session_cart,
                session_id=session_id,
            )
        except ApiError as exc:
            # Best-effort rollback for any inventory decrements that already succeeded.
            for item in reversed(decremented_items):
                try:
                    self._product_call(
                        self.product_db.update_item_quantity,
                        item_category=item["item_category"],
                        item_id=item["item_id"],
                        quantity_delta=item["quantity"],
                    )
                except ApiError:
                    pass

            raise ApiError(
                status_code=503,
                code="PURCHASE_ABORTED",
                message=f"Purchase failed after approval: {exc.message}",
            ) from exc

        return {
            "approved": True,
            "item_count": len(purchase_items),
            "total_quantity": total_quantity,
            "items": [
                {
                    "item_category": item["item_category"],
                    "item_id": item["item_id"],
                    "quantity": item["quantity"],
                    "item_name": item["item_name"],
                    "sale_price": item["sale_price"],
                }
                for item in purchase_items
            ],
        }
