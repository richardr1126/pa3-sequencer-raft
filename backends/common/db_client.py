"""Typed gRPC clients for customer and product database services."""

import threading
from datetime import datetime, timezone

import grpc

from common.grpc_gen import customer_db_pb2, customer_db_pb2_grpc
from common.grpc_gen import product_db_pb2, product_db_pb2_grpc


def _timestamp_to_iso(value) -> str | None:
    # Protobuf Timestamp defaults to epoch 0 when unset; treat that as missing.
    if value is None or value.seconds == 0 and value.nanos == 0:
        return None
    dt = datetime.fromtimestamp(value.seconds + value.nanos / 1_000_000_000, tz=timezone.utc)
    return dt.replace(tzinfo=None).isoformat()


def _buyer_to_dict(buyer: customer_db_pb2.Buyer) -> dict[str, int | str]:
    return {
        "buyer_id": buyer.buyer_id,
        "buyer_name": buyer.buyer_name,
        "items_purchased": buyer.items_purchased,
    }


def _seller_to_dict(seller: customer_db_pb2.Seller) -> dict[str, int | str]:
    return {
        "seller_id": seller.seller_id,
        "seller_name": seller.seller_name,
        "thumbs_up": seller.thumbs_up,
        "thumbs_down": seller.thumbs_down,
        "items_sold": seller.items_sold,
    }


def _cart_item_to_dict(item: customer_db_pb2.CartItem) -> dict[str, int]:
    return {
        "item_category": item.item_category,
        "item_id": item.item_id,
        "quantity": item.quantity,
    }


def _purchase_to_dict(purchase: customer_db_pb2.Purchase) -> dict[str, int | str | None]:
    return {
        "purchase_id": purchase.purchase_id,
        "item_category": purchase.item_category,
        "item_id": purchase.item_id,
        "quantity": purchase.quantity,
        "purchased_at": _timestamp_to_iso(purchase.purchased_at),
    }


def _item_to_dict(item: product_db_pb2.Item) -> dict[str, int | float | str | list[str] | None]:
    deleted_at = None
    # `deleted_at` is optional in proto; only read it when explicitly present.
    if item.HasField("deleted_at"):
        deleted_at = _timestamp_to_iso(item.deleted_at)

    return {
        "item_category": item.item_category,
        "item_id": item.item_id,
        "item_name": item.item_name,
        "condition": item.condition,
        "sale_price": item.sale_price,
        "quantity": item.quantity,
        "seller_id": item.seller_id,
        "thumbs_up": item.thumbs_up,
        "thumbs_down": item.thumbs_down,
        "keywords": list(item.keywords),
        "deleted_at": deleted_at,
    }


class CustomerDbClient:
    """Typed gRPC client for customer database operations."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._channels: list[grpc.Channel] = []
        self._stubs: list[customer_db_pb2_grpc.CustomerDbServiceStub] = []
        self._round_robin_lock = threading.Lock()
        self._next_stub_index = 0

        raw_targets = [token.strip() for token in host.split(",") if token.strip()]
        if not raw_targets:
            raw_targets = [host.strip()] if host.strip() else ["localhost"]

        for target in raw_targets:
            full_target = target
            # Allow explicit host:port entries, otherwise use the supplied port arg.
            if ":" not in target or not target.rsplit(":", 1)[1].isdigit():
                full_target = f"{target}:{port}"
            channel = grpc.insecure_channel(full_target)
            stub = customer_db_pb2_grpc.CustomerDbServiceStub(channel)
            self._channels.append(channel)
            self._stubs.append(stub)

    def close(self) -> None:
        for channel in self._channels:
            channel.close()

    @staticmethod
    def _is_retryable(exc: grpc.RpcError) -> bool:
        return exc.code() in {
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
        }

    def _rpc(self, method_name: str, request):
        if not self._stubs:
            raise RuntimeError("No customer DB endpoints configured")

        with self._round_robin_lock:
            start_index = self._next_stub_index
            self._next_stub_index = (self._next_stub_index + 1) % len(self._stubs)

        last_retryable_error: grpc.RpcError | None = None
        for offset in range(len(self._stubs)):
            stub = self._stubs[(start_index + offset) % len(self._stubs)]
            method = getattr(stub, method_name)
            try:
                return method(request)
            except grpc.RpcError as exc:
                if self._is_retryable(exc):
                    last_retryable_error = exc
                    continue
                raise

        if last_retryable_error is not None:
            raise last_retryable_error
        raise RuntimeError("All customer DB endpoints failed")

    def create_buyer(self, *, buyer_name: str, login_name: str, password: str) -> dict:
        response = self._rpc(
            "CreateBuyer",
            customer_db_pb2.CreateBuyerRequest(
                buyer_name=buyer_name,
                login_name=login_name,
                password=password,
            )
        )
        return _buyer_to_dict(response.buyer)

    def authenticate_buyer(self, *, login_name: str, password: str) -> dict:
        response = self._rpc(
            "AuthenticateBuyer",
            customer_db_pb2.AuthenticateBuyerRequest(
                login_name=login_name,
                password=password,
            )
        )
        buyer = _buyer_to_dict(response.buyer) if response.HasField("buyer") else None
        return {
            "authenticated": response.authenticated,
            "buyer": buyer,
        }

    def get_buyer(self, *, buyer_id: int) -> dict:
        response = self._rpc("GetBuyer", customer_db_pb2.GetBuyerRequest(buyer_id=buyer_id))
        buyer = _buyer_to_dict(response.buyer) if response.found and response.HasField("buyer") else None
        return {"buyer": buyer}

    def increment_buyer_purchases(self, *, buyer_id: int, amount: int | None = None) -> dict:
        request = customer_db_pb2.IncrementBuyerPurchasesRequest(buyer_id=buyer_id)
        if amount is not None:
            request.amount = amount
        response = self._rpc("IncrementBuyerPurchases", request)
        return {"items_purchased": response.items_purchased}

    def create_seller(self, *, seller_name: str, login_name: str, password: str) -> dict:
        response = self._rpc(
            "CreateSeller",
            customer_db_pb2.CreateSellerRequest(
                seller_name=seller_name,
                login_name=login_name,
                password=password,
            )
        )
        return _seller_to_dict(response.seller)

    def authenticate_seller(self, *, login_name: str, password: str) -> dict:
        response = self._rpc(
            "AuthenticateSeller",
            customer_db_pb2.AuthenticateSellerRequest(
                login_name=login_name,
                password=password,
            )
        )
        seller = _seller_to_dict(response.seller) if response.HasField("seller") else None
        return {
            "authenticated": response.authenticated,
            "seller": seller,
        }

    def get_seller(self, *, seller_id: int) -> dict:
        response = self._rpc("GetSeller", customer_db_pb2.GetSellerRequest(seller_id=seller_id))
        seller = _seller_to_dict(response.seller) if response.found and response.HasField("seller") else None
        return {"seller": seller}

    def update_seller_feedback(
        self,
        *,
        seller_id: int,
        thumbs_up_delta: int = 0,
        thumbs_down_delta: int = 0,
    ) -> dict:
        response = self._rpc(
            "UpdateSellerFeedback",
            customer_db_pb2.UpdateSellerFeedbackRequest(
                seller_id=seller_id,
                thumbs_up_delta=thumbs_up_delta,
                thumbs_down_delta=thumbs_down_delta,
            )
        )
        return {
            "thumbs_up": response.thumbs_up,
            "thumbs_down": response.thumbs_down,
        }

    def increment_seller_items_sold(self, *, seller_id: int, amount: int | None = None) -> dict:
        request = customer_db_pb2.IncrementSellerItemsSoldRequest(seller_id=seller_id)
        if amount is not None:
            request.amount = amount
        response = self._rpc("IncrementSellerItemsSold", request)
        return {"items_sold": response.items_sold}

    def create_buyer_session(self, *, buyer_id: int) -> dict:
        response = self._rpc(
            "CreateBuyerSession",
            customer_db_pb2.CreateBuyerSessionRequest(buyer_id=buyer_id)
        )
        return {"session_id": response.session_id}

    def validate_buyer_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "ValidateBuyerSession",
            customer_db_pb2.ValidateBuyerSessionRequest(session_id=session_id)
        )
        buyer = _buyer_to_dict(response.buyer) if response.HasField("buyer") else None
        last_activity_at = None
        if response.HasField("last_activity_at"):
            last_activity_at = _timestamp_to_iso(response.last_activity_at)
        return {
            "valid": response.valid,
            "buyer": buyer,
            "last_activity_at": last_activity_at,
            "reason": response.reason,
        }

    def delete_buyer_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "DeleteBuyerSession",
            customer_db_pb2.DeleteBuyerSessionRequest(session_id=session_id)
        )
        return {"deleted": response.deleted}

    def touch_buyer_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "TouchBuyerSession",
            customer_db_pb2.TouchBuyerSessionRequest(session_id=session_id)
        )
        return {"touched": response.touched}

    def cleanup_expired_buyer_sessions(self) -> dict:
        response = self._rpc(
            "CleanupExpiredBuyerSessions",
            customer_db_pb2.CleanupExpiredBuyerSessionsRequest()
        )
        return {"deleted_count": response.deleted_count}

    def cleanup_buyer_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "CleanupBuyerSession",
            customer_db_pb2.CleanupBuyerSessionRequest(session_id=session_id)
        )
        return {"deleted": response.deleted}

    def create_seller_session(self, *, seller_id: int) -> dict:
        response = self._rpc(
            "CreateSellerSession",
            customer_db_pb2.CreateSellerSessionRequest(seller_id=seller_id)
        )
        return {"session_id": response.session_id}

    def validate_seller_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "ValidateSellerSession",
            customer_db_pb2.ValidateSellerSessionRequest(session_id=session_id)
        )
        seller = _seller_to_dict(response.seller) if response.HasField("seller") else None
        last_activity_at = None
        if response.HasField("last_activity_at"):
            last_activity_at = _timestamp_to_iso(response.last_activity_at)
        return {
            "valid": response.valid,
            "seller": seller,
            "last_activity_at": last_activity_at,
            "reason": response.reason,
        }

    def delete_seller_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "DeleteSellerSession",
            customer_db_pb2.DeleteSellerSessionRequest(session_id=session_id)
        )
        return {"deleted": response.deleted}

    def touch_seller_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "TouchSellerSession",
            customer_db_pb2.TouchSellerSessionRequest(session_id=session_id)
        )
        return {"touched": response.touched}

    def cleanup_expired_seller_sessions(self) -> dict:
        response = self._rpc(
            "CleanupExpiredSellerSessions",
            customer_db_pb2.CleanupExpiredSellerSessionsRequest()
        )
        return {"deleted_count": response.deleted_count}

    def cleanup_seller_session(self, *, session_id: str) -> dict:
        response = self._rpc(
            "CleanupSellerSession",
            customer_db_pb2.CleanupSellerSessionRequest(session_id=session_id)
        )
        return {"deleted": response.deleted}

    def get_session_cart(self, *, session_id: str) -> dict:
        response = self._rpc(
            "GetSessionCart",
            customer_db_pb2.GetSessionCartRequest(session_id=session_id)
        )
        return {"items": [_cart_item_to_dict(item) for item in response.items]}

    def set_session_cart_item(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
        quantity: int,
    ) -> dict:
        response = self._rpc(
            "SetSessionCartItem",
            customer_db_pb2.SetSessionCartItemRequest(
                session_id=session_id,
                item_category=item_category,
                item_id=item_id,
                quantity=quantity,
            )
        )
        return {
            "updated": response.updated,
            "quantity": response.quantity,
        }

    def remove_session_cart_item(self, *, session_id: str, item_category: int, item_id: int) -> dict:
        response = self._rpc(
            "RemoveSessionCartItem",
            customer_db_pb2.RemoveSessionCartItemRequest(
                session_id=session_id,
                item_category=item_category,
                item_id=item_id,
            )
        )
        return {"removed": response.removed}

    def clear_session_cart(self, *, session_id: str) -> dict:
        response = self._rpc(
            "ClearSessionCart",
            customer_db_pb2.ClearSessionCartRequest(session_id=session_id)
        )
        return {"cleared_count": response.cleared_count}

    def get_saved_cart(self, *, buyer_id: int) -> dict:
        response = self._rpc("GetSavedCart", customer_db_pb2.GetSavedCartRequest(buyer_id=buyer_id))
        return {"items": [_cart_item_to_dict(item) for item in response.items]}

    def save_cart(self, *, session_id: str, buyer_id: int) -> dict:
        response = self._rpc(
            "SaveCart",
            customer_db_pb2.SaveCartRequest(session_id=session_id, buyer_id=buyer_id)
        )
        return {"saved_count": response.saved_count}

    def clear_saved_cart(self, *, buyer_id: int) -> dict:
        response = self._rpc(
            "ClearSavedCart",
            customer_db_pb2.ClearSavedCartRequest(buyer_id=buyer_id)
        )
        return {"cleared_count": response.cleared_count}

    def load_saved_cart(self, *, session_id: str, buyer_id: int) -> dict:
        response = self._rpc(
            "LoadSavedCart",
            customer_db_pb2.LoadSavedCartRequest(session_id=session_id, buyer_id=buyer_id)
        )
        return {"loaded_count": response.loaded_count}

    def add_purchase(
        self,
        *,
        buyer_id: int,
        item_category: int,
        item_id: int,
        quantity: int | None = None,
    ) -> dict:
        request = customer_db_pb2.AddPurchaseRequest(
            buyer_id=buyer_id,
            item_category=item_category,
            item_id=item_id,
        )
        if quantity is not None:
            request.quantity = quantity
        response = self._rpc("AddPurchase", request)
        return {"purchase_id": response.purchase_id}

    def get_purchase_history(self, *, buyer_id: int) -> dict:
        response = self._rpc(
            "GetPurchaseHistory",
            customer_db_pb2.GetPurchaseHistoryRequest(buyer_id=buyer_id)
        )
        return {"purchases": [_purchase_to_dict(purchase) for purchase in response.purchases]}


class ProductDbClient:
    """Typed gRPC client for product database operations."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._channels: list[grpc.Channel] = []
        self._stubs: list[product_db_pb2_grpc.ProductDbServiceStub] = []
        self._round_robin_lock = threading.Lock()
        self._next_stub_index = 0

        raw_targets = [token.strip() for token in host.split(",") if token.strip()]
        if not raw_targets:
            raw_targets = [host.strip()] if host.strip() else ["localhost"]

        for target in raw_targets:
            full_target = target
            # Allow explicit host:port entries, otherwise use the supplied port arg.
            if ":" not in target or not target.rsplit(":", 1)[1].isdigit():
                full_target = f"{target}:{port}"
            channel = grpc.insecure_channel(full_target)
            stub = product_db_pb2_grpc.ProductDbServiceStub(channel)
            self._channels.append(channel)
            self._stubs.append(stub)

    def close(self) -> None:
        for channel in self._channels:
            channel.close()

    @staticmethod
    def _is_retryable(exc: grpc.RpcError) -> bool:
        return exc.code() in {
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
        }

    def _rpc(self, method_name: str, request):
        if not self._stubs:
            raise RuntimeError("No product DB endpoints configured")

        with self._round_robin_lock:
            start_index = self._next_stub_index
            self._next_stub_index = (self._next_stub_index + 1) % len(self._stubs)

        last_retryable_error: grpc.RpcError | None = None
        for offset in range(len(self._stubs)):
            index = (start_index + offset) % len(self._stubs)
            stub = self._stubs[index]
            method = getattr(stub, method_name)
            try:
                return method(request)
            except grpc.RpcError as exc:
                if self._is_retryable(exc):
                    last_retryable_error = exc
                    continue
                raise

        if last_retryable_error is not None:
            raise last_retryable_error
        raise RuntimeError("All product DB endpoints failed")

    def create_item(
        self,
        *,
        item_name: str,
        item_category: int,
        keywords: list[str],
        condition: str,
        sale_price: float,
        quantity: int,
        seller_id: int,
    ) -> dict:
        response = self._rpc(
            "CreateItem",
            product_db_pb2.CreateItemRequest(
                item_name=item_name,
                item_category=item_category,
                keywords=keywords,
                condition=condition,
                sale_price=sale_price,
                quantity=quantity,
                seller_id=seller_id,
            ),
        )
        return _item_to_dict(response.item)

    def get_item(self, *, item_category: int, item_id: int, include_deleted: bool = False) -> dict:
        response = self._rpc(
            "GetItem",
            product_db_pb2.GetItemRequest(
                item_category=item_category,
                item_id=item_id,
                include_deleted=include_deleted,
            ),
        )
        item = _item_to_dict(response.item) if response.found and response.HasField("item") else None
        return {"item": item}

    def update_item_price(
        self,
        *,
        item_category: int,
        item_id: int,
        sale_price: float,
        seller_id: int | None = None,
    ) -> dict:
        request = product_db_pb2.UpdateItemPriceRequest(
            item_category=item_category,
            item_id=item_id,
            sale_price=sale_price,
        )
        if seller_id is not None:
            request.seller_id = seller_id

        response = self._rpc("UpdateItemPrice", request)
        return {"sale_price": response.sale_price}

    def update_item_quantity(
        self,
        *,
        item_category: int,
        item_id: int,
        quantity: int | None = None,
        quantity_delta: int | None = None,
        seller_id: int | None = None,
    ) -> dict:
        request = product_db_pb2.UpdateItemQuantityRequest(
            item_category=item_category,
            item_id=item_id,
        )
        # `quantity_update` is a proto oneof: set either absolute quantity or delta.
        if quantity is not None:
            request.quantity = quantity
        elif quantity_delta is not None:
            request.quantity_delta = quantity_delta

        if seller_id is not None:
            request.seller_id = seller_id

        response = self._rpc("UpdateItemQuantity", request)
        return {"quantity": response.quantity}

    def delete_item(
        self,
        *,
        item_category: int,
        item_id: int,
        seller_id: int | None = None,
    ) -> dict:
        request = product_db_pb2.DeleteItemRequest(
            item_category=item_category,
            item_id=item_id,
        )
        if seller_id is not None:
            request.seller_id = seller_id
        response = self._rpc("DeleteItem", request)
        return {"deleted": response.deleted}

    def list_items_by_seller(self, *, seller_id: int, include_deleted: bool = False) -> dict:
        response = self._rpc(
            "ListItemsBySeller",
            product_db_pb2.ListItemsBySellerRequest(
                seller_id=seller_id,
                include_deleted=include_deleted,
            ),
        )
        return {"items": [_item_to_dict(item) for item in response.items]}

    def search_items(self, *, item_category: int, keywords: list[str]) -> dict:
        response = self._rpc(
            "SearchItems",
            product_db_pb2.SearchItemsRequest(
                item_category=item_category,
                keywords=keywords,
            ),
        )

        items = []
        for match in response.items:
            item_dict = _item_to_dict(match.item)
            item_dict["match_count"] = match.match_count
            items.append(item_dict)

        return {"items": items}

    def add_feedback_vote(
        self,
        *,
        buyer_id: int,
        item_category: int,
        item_id: int,
        vote: str,
    ) -> dict:
        response = self._rpc(
            "AddFeedbackVote",
            product_db_pb2.AddFeedbackVoteRequest(
                buyer_id=buyer_id,
                item_category=item_category,
                item_id=item_id,
                vote=vote,
            ),
        )
        return {
            "thumbs_up": response.thumbs_up,
            "thumbs_down": response.thumbs_down,
            "seller_id": response.seller_id,
        }

    def get_item_feedback(self, *, item_category: int, item_id: int) -> dict:
        response = self._rpc(
            "GetItemFeedback",
            product_db_pb2.GetItemFeedbackRequest(
                item_category=item_category,
                item_id=item_id,
            ),
        )

        feedback = None
        if response.found and response.HasField("feedback"):
            feedback = {
                "thumbs_up": response.feedback.thumbs_up,
                "thumbs_down": response.feedback.thumbs_down,
            }
        return {"feedback": feedback}

    def check_buyer_voted(self, *, buyer_id: int, item_category: int, item_id: int) -> dict:
        response = self._rpc(
            "CheckBuyerVoted",
            product_db_pb2.CheckBuyerVotedRequest(
                buyer_id=buyer_id,
                item_category=item_category,
                item_id=item_id,
            ),
        )

        # `vote` is optional: it is present only when `voted` is true.
        vote = response.vote if response.HasField("vote") else None
        return {"voted": response.voted, "vote": vote}
