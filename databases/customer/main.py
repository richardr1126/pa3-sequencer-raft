"""Customer database gRPC server.

- Buyer/Seller account management (create, authenticate, get)
- Session management (create, validate, delete, cleanup expired)
- Cart management (session cart, saved cart)
- Purchase tracking
"""

import argparse
import concurrent.futures
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import grpc
from sqlalchemy.exc import IntegrityError

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google.protobuf.timestamp_pb2 import Timestamp
from common.grpc_gen import customer_db_pb2, customer_db_pb2_grpc
from databases.customer.api import CustomerDomainApi
from databases.customer.db import init_db, make_session_factory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

class CustomerDbService(customer_db_pb2_grpc.CustomerDbServiceServicer):
    """Customer database gRPC servicer."""

    def __init__(self, db_session_factory):
        self.api = CustomerDomainApi(db_session_factory)

    @staticmethod
    def _timestamp_from_iso(value: str | None) -> Timestamp | None:
        if not value:
            return None
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            # Domain layer emits naive UTC timestamps; normalize before protobuf conversion.
            dt = dt.replace(tzinfo=timezone.utc)
        ts = Timestamp()
        ts.FromDatetime(dt)
        return ts

    @staticmethod
    def _buyer_message(buyer: dict[str, Any]) -> customer_db_pb2.Buyer:
        return customer_db_pb2.Buyer(
            buyer_id=buyer["buyer_id"],
            buyer_name=buyer["buyer_name"],
            items_purchased=buyer["items_purchased"],
        )

    @staticmethod
    def _seller_message(seller: dict[str, Any]) -> customer_db_pb2.Seller:
        return customer_db_pb2.Seller(
            seller_id=seller["seller_id"],
            seller_name=seller["seller_name"],
            thumbs_up=seller["thumbs_up"],
            thumbs_down=seller["thumbs_down"],
            items_sold=seller["items_sold"],
        )

    @staticmethod
    def _cart_item_message(item: dict[str, Any]) -> customer_db_pb2.CartItem:
        return customer_db_pb2.CartItem(
            item_category=item["item_category"],
            item_id=item["item_id"],
            quantity=item["quantity"],
        )

    @staticmethod
    def _purchase_message(purchase: dict[str, Any]) -> customer_db_pb2.Purchase:
        msg = customer_db_pb2.Purchase(
            purchase_id=purchase["purchase_id"],
            item_category=purchase["item_category"],
            item_id=purchase["item_id"],
            quantity=purchase["quantity"],
        )
        ts = CustomerDbService._timestamp_from_iso(purchase.get("purchased_at"))
        if ts is not None:
            msg.purchased_at.CopyFrom(ts)
        return msg

    @staticmethod
    def _abort_from_exception(context: grpc.ServicerContext, exc: Exception) -> None:
        if isinstance(exc, ValueError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        if isinstance(exc, IntegrityError):
            message = str(exc.orig) if exc.orig else str(exc)
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, message)
        logger.exception("Unhandled customer DB error: %s", exc)
        context.abort(grpc.StatusCode.INTERNAL, "Internal server error")

    def _execute(self, context: grpc.ServicerContext, fn: Callable[[], Any]) -> Any:
        # Centralize exception mapping so handlers only encode/decode request/response data.
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - context.abort raises
            self._abort_from_exception(context, exc)
            raise

    def CreateBuyer(
        self, request: customer_db_pb2.CreateBuyerRequest, context: grpc.ServicerContext
    ) -> customer_db_pb2.CreateBuyerResponse:
        result = self._execute(
            context,
            lambda: self.api.create_buyer(
                buyer_name=request.buyer_name,
                login_name=request.login_name,
                password=request.password,
            ),
        )
        return customer_db_pb2.CreateBuyerResponse(buyer=self._buyer_message(result))

    def AuthenticateBuyer(
        self,
        request: customer_db_pb2.AuthenticateBuyerRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.AuthenticateBuyerResponse:
        result = self._execute(
            context,
            lambda: self.api.authenticate_buyer(
                login_name=request.login_name, password=request.password
            ),
        )
        response = customer_db_pb2.AuthenticateBuyerResponse(
            authenticated=result["authenticated"]
        )
        buyer = result.get("buyer")
        if buyer is not None:
            response.buyer.CopyFrom(self._buyer_message(buyer))
        return response

    def GetBuyer(
        self, request: customer_db_pb2.GetBuyerRequest, context: grpc.ServicerContext
    ) -> customer_db_pb2.GetBuyerResponse:
        result = self._execute(context, lambda: self.api.get_buyer(buyer_id=request.buyer_id))
        response = customer_db_pb2.GetBuyerResponse(found=result.get("buyer") is not None)
        if result.get("buyer") is not None:
            response.buyer.CopyFrom(self._buyer_message(result["buyer"]))
        return response

    def IncrementBuyerPurchases(
        self,
        request: customer_db_pb2.IncrementBuyerPurchasesRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.IncrementBuyerPurchasesResponse:
        # `amount` is optional in proto; default to 1 when not set.
        amount = request.amount if request.HasField("amount") else 1
        result = self._execute(
            context,
            lambda: self.api.increment_buyer_purchases(
                buyer_id=request.buyer_id, amount=amount
            ),
        )
        return customer_db_pb2.IncrementBuyerPurchasesResponse(
            items_purchased=result["items_purchased"]
        )

    def CreateSeller(
        self, request: customer_db_pb2.CreateSellerRequest, context: grpc.ServicerContext
    ) -> customer_db_pb2.CreateSellerResponse:
        result = self._execute(
            context,
            lambda: self.api.create_seller(
                seller_name=request.seller_name,
                login_name=request.login_name,
                password=request.password,
            ),
        )
        return customer_db_pb2.CreateSellerResponse(seller=self._seller_message(result))

    def AuthenticateSeller(
        self,
        request: customer_db_pb2.AuthenticateSellerRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.AuthenticateSellerResponse:
        result = self._execute(
            context,
            lambda: self.api.authenticate_seller(
                login_name=request.login_name, password=request.password
            ),
        )
        response = customer_db_pb2.AuthenticateSellerResponse(
            authenticated=result["authenticated"]
        )
        seller = result.get("seller")
        if seller is not None:
            response.seller.CopyFrom(self._seller_message(seller))
        return response

    def GetSeller(
        self, request: customer_db_pb2.GetSellerRequest, context: grpc.ServicerContext
    ) -> customer_db_pb2.GetSellerResponse:
        result = self._execute(
            context, lambda: self.api.get_seller(seller_id=request.seller_id)
        )
        response = customer_db_pb2.GetSellerResponse(found=result.get("seller") is not None)
        if result.get("seller") is not None:
            response.seller.CopyFrom(self._seller_message(result["seller"]))
        return response

    def UpdateSellerFeedback(
        self,
        request: customer_db_pb2.UpdateSellerFeedbackRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.UpdateSellerFeedbackResponse:
        result = self._execute(
            context,
            lambda: self.api.update_seller_feedback(
                seller_id=request.seller_id,
                thumbs_up_delta=request.thumbs_up_delta,
                thumbs_down_delta=request.thumbs_down_delta,
            ),
        )
        return customer_db_pb2.UpdateSellerFeedbackResponse(
            thumbs_up=result["thumbs_up"],
            thumbs_down=result["thumbs_down"],
        )

    def IncrementSellerItemsSold(
        self,
        request: customer_db_pb2.IncrementSellerItemsSoldRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.IncrementSellerItemsSoldResponse:
        # `amount` is optional in proto; default to 1 when not set.
        amount = request.amount if request.HasField("amount") else 1
        result = self._execute(
            context,
            lambda: self.api.increment_seller_items_sold(
                seller_id=request.seller_id, amount=amount
            ),
        )
        return customer_db_pb2.IncrementSellerItemsSoldResponse(
            items_sold=result["items_sold"]
        )

    def CreateBuyerSession(
        self,
        request: customer_db_pb2.CreateBuyerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CreateBuyerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.create_buyer_session(buyer_id=request.buyer_id),
        )
        return customer_db_pb2.CreateBuyerSessionResponse(session_id=result["session_id"])

    def ValidateBuyerSession(
        self,
        request: customer_db_pb2.ValidateBuyerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.ValidateBuyerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.validate_buyer_session(session_id=request.session_id),
        )
        response = customer_db_pb2.ValidateBuyerSessionResponse(
            valid=result["valid"],
            reason=result.get("reason", ""),
        )
        if result.get("buyer") is not None:
            response.buyer.CopyFrom(self._buyer_message(result["buyer"]))
        # Only set optional protobuf timestamp when source value is present.
        ts = self._timestamp_from_iso(result.get("last_activity_at"))
        if ts is not None:
            response.last_activity_at.CopyFrom(ts)
        return response

    def DeleteBuyerSession(
        self,
        request: customer_db_pb2.DeleteBuyerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.DeleteBuyerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.delete_buyer_session(session_id=request.session_id),
        )
        return customer_db_pb2.DeleteBuyerSessionResponse(deleted=result["deleted"])

    def TouchBuyerSession(
        self,
        request: customer_db_pb2.TouchBuyerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.TouchBuyerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.touch_buyer_session(session_id=request.session_id),
        )
        return customer_db_pb2.TouchBuyerSessionResponse(touched=result["touched"])

    def CleanupExpiredBuyerSessions(
        self,
        request: customer_db_pb2.CleanupExpiredBuyerSessionsRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CleanupExpiredBuyerSessionsResponse:
        _ = request
        result = self._execute(context, self.api.cleanup_expired_buyer_sessions)
        return customer_db_pb2.CleanupExpiredBuyerSessionsResponse(
            deleted_count=result["deleted_count"]
        )

    def CleanupBuyerSession(
        self,
        request: customer_db_pb2.CleanupBuyerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CleanupBuyerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.cleanup_buyer_session(session_id=request.session_id),
        )
        return customer_db_pb2.CleanupBuyerSessionResponse(deleted=result["deleted"])

    def CreateSellerSession(
        self,
        request: customer_db_pb2.CreateSellerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CreateSellerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.create_seller_session(seller_id=request.seller_id),
        )
        return customer_db_pb2.CreateSellerSessionResponse(session_id=result["session_id"])

    def ValidateSellerSession(
        self,
        request: customer_db_pb2.ValidateSellerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.ValidateSellerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.validate_seller_session(session_id=request.session_id),
        )
        response = customer_db_pb2.ValidateSellerSessionResponse(
            valid=result["valid"],
            reason=result.get("reason", ""),
        )
        if result.get("seller") is not None:
            response.seller.CopyFrom(self._seller_message(result["seller"]))
        # Only set optional protobuf timestamp when source value is present.
        ts = self._timestamp_from_iso(result.get("last_activity_at"))
        if ts is not None:
            response.last_activity_at.CopyFrom(ts)
        return response

    def DeleteSellerSession(
        self,
        request: customer_db_pb2.DeleteSellerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.DeleteSellerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.delete_seller_session(session_id=request.session_id),
        )
        return customer_db_pb2.DeleteSellerSessionResponse(deleted=result["deleted"])

    def TouchSellerSession(
        self,
        request: customer_db_pb2.TouchSellerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.TouchSellerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.touch_seller_session(session_id=request.session_id),
        )
        return customer_db_pb2.TouchSellerSessionResponse(touched=result["touched"])

    def CleanupExpiredSellerSessions(
        self,
        request: customer_db_pb2.CleanupExpiredSellerSessionsRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CleanupExpiredSellerSessionsResponse:
        _ = request
        result = self._execute(context, self.api.cleanup_expired_seller_sessions)
        return customer_db_pb2.CleanupExpiredSellerSessionsResponse(
            deleted_count=result["deleted_count"]
        )

    def CleanupSellerSession(
        self,
        request: customer_db_pb2.CleanupSellerSessionRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.CleanupSellerSessionResponse:
        result = self._execute(
            context,
            lambda: self.api.cleanup_seller_session(session_id=request.session_id),
        )
        return customer_db_pb2.CleanupSellerSessionResponse(deleted=result["deleted"])

    def GetSessionCart(
        self,
        request: customer_db_pb2.GetSessionCartRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.GetSessionCartResponse:
        result = self._execute(
            context, lambda: self.api.get_session_cart(session_id=request.session_id)
        )
        return customer_db_pb2.GetSessionCartResponse(
            items=[self._cart_item_message(item) for item in result["items"]]
        )

    def SetSessionCartItem(
        self,
        request: customer_db_pb2.SetSessionCartItemRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.SetSessionCartItemResponse:
        result = self._execute(
            context,
            lambda: self.api.set_session_cart_item(
                session_id=request.session_id,
                item_category=request.item_category,
                item_id=request.item_id,
                quantity=request.quantity,
            ),
        )
        return customer_db_pb2.SetSessionCartItemResponse(
            updated=result["updated"],
            quantity=result["quantity"],
        )

    def RemoveSessionCartItem(
        self,
        request: customer_db_pb2.RemoveSessionCartItemRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.RemoveSessionCartItemResponse:
        result = self._execute(
            context,
            lambda: self.api.remove_session_cart_item(
                session_id=request.session_id,
                item_category=request.item_category,
                item_id=request.item_id,
            ),
        )
        return customer_db_pb2.RemoveSessionCartItemResponse(removed=result["removed"])

    def ClearSessionCart(
        self,
        request: customer_db_pb2.ClearSessionCartRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.ClearSessionCartResponse:
        result = self._execute(
            context, lambda: self.api.clear_session_cart(session_id=request.session_id)
        )
        return customer_db_pb2.ClearSessionCartResponse(
            cleared_count=result["cleared_count"]
        )

    def GetSavedCart(
        self,
        request: customer_db_pb2.GetSavedCartRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.GetSavedCartResponse:
        result = self._execute(
            context, lambda: self.api.get_saved_cart(buyer_id=request.buyer_id)
        )
        return customer_db_pb2.GetSavedCartResponse(
            items=[self._cart_item_message(item) for item in result["items"]]
        )

    def SaveCart(
        self, request: customer_db_pb2.SaveCartRequest, context: grpc.ServicerContext
    ) -> customer_db_pb2.SaveCartResponse:
        result = self._execute(
            context,
            lambda: self.api.save_cart(
                session_id=request.session_id,
                buyer_id=request.buyer_id,
            ),
        )
        return customer_db_pb2.SaveCartResponse(saved_count=result["saved_count"])

    def ClearSavedCart(
        self,
        request: customer_db_pb2.ClearSavedCartRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.ClearSavedCartResponse:
        result = self._execute(
            context,
            lambda: self.api.clear_saved_cart(buyer_id=request.buyer_id),
        )
        return customer_db_pb2.ClearSavedCartResponse(
            cleared_count=result["cleared_count"]
        )

    def LoadSavedCart(
        self,
        request: customer_db_pb2.LoadSavedCartRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.LoadSavedCartResponse:
        result = self._execute(
            context,
            lambda: self.api.load_saved_cart(
                session_id=request.session_id,
                buyer_id=request.buyer_id,
            ),
        )
        return customer_db_pb2.LoadSavedCartResponse(loaded_count=result["loaded_count"])

    def AddPurchase(
        self,
        request: customer_db_pb2.AddPurchaseRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.AddPurchaseResponse:
        # `quantity` is optional in proto; default to 1 when not set.
        quantity = request.quantity if request.HasField("quantity") else 1
        result = self._execute(
            context,
            lambda: self.api.add_purchase(
                buyer_id=request.buyer_id,
                item_category=request.item_category,
                item_id=request.item_id,
                quantity=quantity,
            ),
        )
        return customer_db_pb2.AddPurchaseResponse(purchase_id=result["purchase_id"])

    def GetPurchaseHistory(
        self,
        request: customer_db_pb2.GetPurchaseHistoryRequest,
        context: grpc.ServicerContext,
    ) -> customer_db_pb2.GetPurchaseHistoryResponse:
        result = self._execute(
            context, lambda: self.api.get_purchase_history(buyer_id=request.buyer_id)
        )
        return customer_db_pb2.GetPurchaseHistoryResponse(
            purchases=[self._purchase_message(purchase) for purchase in result["purchases"]]
        )

def run_server(host: str, port: int):
    """Run the customer database gRPC server."""
    # Initialize database
    engine = init_db()
    db_session_factory = make_session_factory(engine=engine)

    try:
        # Thread pool matches the concurrent RPC model for blocking DB operations.
        server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=64))
        customer_db_pb2_grpc.add_CustomerDbServiceServicer_to_server(
            CustomerDbService(db_session_factory),
            server,
        )
        server.add_insecure_port(f"{host}:{port}")
        server.start()
        logger.info("Customer DB gRPC server listening on %s:%s", host, port)
        server.wait_for_termination()
    finally:
        try:
            # Ensure pooled DB connections are closed on shutdown.
            engine.dispose()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Customer Database gRPC Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8001, help="Port to bind to")
    args = parser.parse_args()

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
