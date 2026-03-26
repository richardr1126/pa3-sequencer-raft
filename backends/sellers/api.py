"""Seller domain service for FastAPI routes."""

from datetime import datetime
from typing import Any, Callable

import grpc

from backends.common.errors import ApiError


class SellerService:
    """Seller operations backed by customer/product DB gRPC services."""

    SESSION_TOUCH_INTERVAL_SECONDS = 5.0

    def __init__(self, customer_db: Any, product_db: Any):
        self.customer_db = customer_db
        self.product_db = product_db

    # -- Shared seller service helpers --

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

    def validate_seller_session(self, session_id: str) -> dict[str, Any]:
        """Validate session and return seller record."""
        if not session_id:
            raise ApiError(
                status_code=401,
                code="AUTH_REQUIRED",
                message="Must be logged in to perform this action",
            )

        session_result = self._customer_call(
            self.customer_db.validate_seller_session,
            session_id=session_id,
        )

        if not session_result.get("valid"):
            reason = session_result.get("reason", "Invalid session")
            if reason == "expired":
                try:
                    self._customer_call(
                        self.customer_db.cleanup_seller_session,
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
                self.customer_db.touch_seller_session,
                session_id=session_id,
            )

        seller = session_result.get("seller")
        if not isinstance(seller, dict):
            raise ApiError(
                status_code=401,
                code="INVALID_SESSION",
                message="Invalid session",
            )
        return seller

    # -- Public API --

    def create_account(self, *, seller_name: str, login_name: str, password: str) -> dict[str, Any]:
        payload = {
            "seller_name": seller_name,
            "login_name": login_name,
            "password": password,
        }
        self._require_fields(payload, ["seller_name", "login_name", "password"])

        result = self._customer_call(
            self.customer_db.create_seller,
            seller_name=seller_name,
            login_name=login_name,
            password=password,
        )
        return {"seller_id": result["seller_id"], "seller_name": result["seller_name"]}

    def login(self, *, login_name: str, password: str) -> dict[str, Any]:
        self._require_fields(
            {"login_name": login_name, "password": password},
            ["login_name", "password"],
        )

        auth_result = self._customer_call(
            self.customer_db.authenticate_seller,
            login_name=login_name,
            password=password,
        )

        if not auth_result.get("authenticated"):
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Invalid login credentials",
            )

        seller = auth_result["seller"]
        session_result = self._customer_call(
            self.customer_db.create_seller_session,
            seller_id=seller["seller_id"],
        )

        return {
            "session_id": session_result["session_id"],
            "seller_id": seller["seller_id"],
            "seller_name": seller["seller_name"],
        }

    # -- Protected API --

    def logout(self, *, session_id: str) -> dict[str, Any]:
        self._customer_call(self.customer_db.delete_seller_session, session_id=session_id)
        return {"logged_out": True}

    def get_rating(self, *, seller: dict[str, Any]) -> dict[str, Any]:
        return {
            "seller_id": seller["seller_id"],
            "thumbs_up": seller["thumbs_up"],
            "thumbs_down": seller["thumbs_down"],
        }

    def register_item(
        self,
        *,
        seller: dict[str, Any],
        item_name: str,
        item_category: int,
        keywords: list[str],
        condition: str,
        sale_price: float,
        quantity: int,
    ) -> dict[str, Any]:
        if not all(
            [
                item_name,
                item_category is not None,
                condition,
                sale_price is not None,
                quantity is not None,
            ]
        ):
            raise ApiError(
                status_code=400,
                code="ERROR",
                message=(
                    "Missing required fields: item_name, item_category, condition, "
                    "sale_price, quantity"
                ),
            )

        result = self._product_call(
            self.product_db.create_item,
            item_name=item_name,
            item_category=item_category,
            keywords=keywords,
            condition=condition,
            sale_price=sale_price,
            quantity=quantity,
            seller_id=seller["seller_id"],
        )

        return {
            "item_id": {
                "item_category": result["item_category"],
                "item_id": result["item_id"],
            },
            "item_name": result["item_name"],
        }

    def change_price(
        self,
        *,
        seller: dict[str, Any],
        item_category: int,
        item_id: int,
        sale_price: float,
    ) -> dict[str, Any]:
        if any(v is None for v in [item_category, item_id, sale_price]):
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id, sale_price",
            )

        result = self._product_call(
            self.product_db.update_item_price,
            item_category=item_category,
            item_id=item_id,
            sale_price=sale_price,
            seller_id=seller["seller_id"],
        )
        return {"sale_price": result["sale_price"]}

    def update_units(
        self,
        *,
        seller: dict[str, Any],
        item_category: int,
        item_id: int,
        quantity: int | None = None,
        quantity_delta: int | None = None,
    ) -> dict[str, Any]:
        if item_category is None or item_id is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Missing required fields: item_category, item_id",
            )
        if quantity is None and quantity_delta is None:
            raise ApiError(
                status_code=400,
                code="ERROR",
                message="Must provide either quantity or quantity_delta",
            )

        result = self._product_call(
            self.product_db.update_item_quantity,
            item_category=item_category,
            item_id=item_id,
            quantity=quantity,
            quantity_delta=quantity_delta,
            seller_id=seller["seller_id"],
        )
        return {"quantity": result["quantity"]}

    def display_items(self, *, seller: dict[str, Any]) -> dict[str, Any]:
        result = self._product_call(
            self.product_db.list_items_by_seller,
            seller_id=seller["seller_id"],
        )
        return {"items": result["items"]}
