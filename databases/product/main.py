"""Product database gRPC server.

- Item management (create, get, update, delete, list by seller)
- Item search (by category and keywords)
- Item feedback (votes)
"""

import argparse
import concurrent.futures
import logging
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import grpc
from sqlalchemy.exc import IntegrityError

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google.protobuf.timestamp_pb2 import Timestamp
from common.grpc_gen import product_db_pb2, product_db_pb2_grpc
from databases.product.api import ProductDomainApi
from databases.product.db import init_db, make_session_factory
from databases.product.raft import (
    ProductRaftNode,
    ProductReadForwarder,
    RaftError,
    build_raft_config_from_env,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class ProductDbService(product_db_pb2_grpc.ProductDbServiceServicer):
    """Product database gRPC servicer."""

    def __init__(self, api: ProductDomainApi, raft_node: ProductRaftNode):
        self.api = api
        self.raft = raft_node
        self.forwarder = ProductReadForwarder(raft_node)

    def close(self) -> None:
        self.forwarder.close()

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
    def _item_message(item: dict[str, Any]) -> product_db_pb2.Item:
        msg = product_db_pb2.Item(
            item_category=item["item_category"],
            item_id=item["item_id"],
            item_name=item["item_name"],
            condition=item["condition"],
            sale_price=item["sale_price"],
            quantity=item["quantity"],
            seller_id=item["seller_id"],
            thumbs_up=item["thumbs_up"],
            thumbs_down=item["thumbs_down"],
            keywords=item["keywords"],
        )
        ts = ProductDbService._timestamp_from_iso(item.get("deleted_at"))
        if ts is not None:
            msg.deleted_at.CopyFrom(ts)
        return msg

    @staticmethod
    def _is_item_id_collision(exc: RaftError) -> bool:
        return (
            exc.grpc_status == grpc.StatusCode.INVALID_ARGUMENT
            and exc.message.startswith("Item ID collision:")
        )

    @staticmethod
    def _abort_from_exception(context: grpc.ServicerContext, exc: Exception) -> None:
        if isinstance(exc, RaftError):
            context.abort(exc.grpc_status, exc.message)
        if isinstance(exc, ValueError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        if isinstance(exc, IntegrityError):
            message = str(exc.orig) if exc.orig else str(exc)
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, message)
        logger.exception("Unhandled product DB error: %s", exc)
        context.abort(grpc.StatusCode.INTERNAL, "Internal server error")

    def _execute(self, context: grpc.ServicerContext, fn: Callable[[], Any]) -> Any:
        # Centralize exception mapping so handlers only encode/decode request/response data.
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - context.abort raises
            self._abort_from_exception(context, exc)
            raise

    def _apply_write(
        self,
        context: grpc.ServicerContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._execute(context, lambda: self.raft.apply(payload))

    def CreateItem(
        self, request: product_db_pb2.CreateItemRequest, context: grpc.ServicerContext
    ) -> product_db_pb2.CreateItemResponse:
        for _ in range(16):
            item_id = secrets.randbelow((1 << 63) - 1) + 1
            payload = {
                "type": "CreateItem",
                "item_id": item_id,
                "item_name": request.item_name,
                "item_category": request.item_category,
                "keywords": list(request.keywords),
                "condition": request.condition,
                "sale_price": request.sale_price,
                "quantity": request.quantity,
                "seller_id": request.seller_id,
            }

            try:
                result = self.raft.apply(payload)
            except RaftError as exc:
                if self._is_item_id_collision(exc):
                    continue
                self._abort_from_exception(context, exc)
                raise
            except Exception as exc:
                self._abort_from_exception(context, exc)
                raise

            return product_db_pb2.CreateItemResponse(item=self._item_message(result))

        context.abort(
            grpc.StatusCode.UNAVAILABLE,
            "Failed to allocate unique item id after repeated collisions",
        )

    def GetItem(
        self, request: product_db_pb2.GetItemRequest, context: grpc.ServicerContext
    ) -> product_db_pb2.GetItemResponse:
        forwarded = self._execute(
            context,
            lambda: self.forwarder.forward_if_needed(context, "GetItem", request),
        )
        if forwarded is not None:
            return forwarded

        result = self._execute(
            context,
            lambda: self.api.get_item(
                item_category=request.item_category,
                item_id=request.item_id,
                include_deleted=request.include_deleted,
            ),
        )
        response = product_db_pb2.GetItemResponse(found=result.get("item") is not None)
        if result.get("item") is not None:
            response.item.CopyFrom(self._item_message(result["item"]))
        return response

    def UpdateItemPrice(
        self,
        request: product_db_pb2.UpdateItemPriceRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.UpdateItemPriceResponse:
        # `seller_id` is optional: absent means no ownership check at this layer.
        seller_id = request.seller_id if request.HasField("seller_id") else None
        result = self._apply_write(
            context,
            {
                "type": "UpdateItemPrice",
                "item_category": request.item_category,
                "item_id": request.item_id,
                "sale_price": request.sale_price,
                "seller_id": seller_id,
            },
        )
        return product_db_pb2.UpdateItemPriceResponse(sale_price=result["sale_price"])

    def UpdateItemQuantity(
        self,
        request: product_db_pb2.UpdateItemQuantityRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.UpdateItemQuantityResponse:
        quantity: int | None = None
        quantity_delta: int | None = None
        # `quantity_update` is a oneof: request can set absolute quantity or delta, not both.
        quantity_mode = request.WhichOneof("quantity_update")
        if quantity_mode == "quantity":
            quantity = request.quantity
        elif quantity_mode == "quantity_delta":
            quantity_delta = request.quantity_delta

        # `seller_id` is optional: absent means no ownership check at this layer.
        seller_id = request.seller_id if request.HasField("seller_id") else None
        result = self._apply_write(
            context,
            {
                "type": "UpdateItemQuantity",
                "item_category": request.item_category,
                "item_id": request.item_id,
                "quantity": quantity,
                "quantity_delta": quantity_delta,
                "seller_id": seller_id,
            },
        )
        return product_db_pb2.UpdateItemQuantityResponse(quantity=result["quantity"])

    def DeleteItem(
        self, request: product_db_pb2.DeleteItemRequest, context: grpc.ServicerContext
    ) -> product_db_pb2.DeleteItemResponse:
        # `seller_id` is optional: absent means no ownership check at this layer.
        seller_id = request.seller_id if request.HasField("seller_id") else None
        result = self._apply_write(
            context,
            {
                "type": "DeleteItem",
                "item_category": request.item_category,
                "item_id": request.item_id,
                "seller_id": seller_id,
            },
        )
        return product_db_pb2.DeleteItemResponse(deleted=result["deleted"])

    def ListItemsBySeller(
        self,
        request: product_db_pb2.ListItemsBySellerRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.ListItemsBySellerResponse:
        forwarded = self._execute(
            context,
            lambda: self.forwarder.forward_if_needed(context, "ListItemsBySeller", request),
        )
        if forwarded is not None:
            return forwarded

        result = self._execute(
            context,
            lambda: self.api.list_items_by_seller(
                seller_id=request.seller_id,
                include_deleted=request.include_deleted,
            ),
        )
        return product_db_pb2.ListItemsBySellerResponse(
            items=[self._item_message(item) for item in result["items"]]
        )

    def SearchItems(
        self, request: product_db_pb2.SearchItemsRequest, context: grpc.ServicerContext
    ) -> product_db_pb2.SearchItemsResponse:
        forwarded = self._execute(
            context,
            lambda: self.forwarder.forward_if_needed(context, "SearchItems", request),
        )
        if forwarded is not None:
            return forwarded

        result = self._execute(
            context,
            lambda: self.api.search_items(
                item_category=request.item_category,
                keywords=list(request.keywords),
            ),
        )
        return product_db_pb2.SearchItemsResponse(
            items=[
                product_db_pb2.SearchItem(
                    item=self._item_message(item),
                    match_count=item["match_count"],
                )
                for item in result["items"]
            ]
        )

    def AddFeedbackVote(
        self,
        request: product_db_pb2.AddFeedbackVoteRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.AddFeedbackVoteResponse:
        result = self._apply_write(
            context,
            {
                "type": "AddFeedbackVote",
                "buyer_id": request.buyer_id,
                "item_category": request.item_category,
                "item_id": request.item_id,
                "vote": request.vote,
            },
        )
        return product_db_pb2.AddFeedbackVoteResponse(
            thumbs_up=result["thumbs_up"],
            thumbs_down=result["thumbs_down"],
            seller_id=result["seller_id"],
        )

    def GetItemFeedback(
        self,
        request: product_db_pb2.GetItemFeedbackRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.GetItemFeedbackResponse:
        forwarded = self._execute(
            context,
            lambda: self.forwarder.forward_if_needed(context, "GetItemFeedback", request),
        )
        if forwarded is not None:
            return forwarded

        result = self._execute(
            context,
            lambda: self.api.get_item_feedback(
                item_category=request.item_category, item_id=request.item_id
            ),
        )
        response = product_db_pb2.GetItemFeedbackResponse(
            found=result.get("feedback") is not None
        )
        if result.get("feedback") is not None:
            response.feedback.CopyFrom(
                product_db_pb2.ItemFeedback(
                    thumbs_up=result["feedback"]["thumbs_up"],
                    thumbs_down=result["feedback"]["thumbs_down"],
                )
            )
        return response

    def CheckBuyerVoted(
        self,
        request: product_db_pb2.CheckBuyerVotedRequest,
        context: grpc.ServicerContext,
    ) -> product_db_pb2.CheckBuyerVotedResponse:
        forwarded = self._execute(
            context,
            lambda: self.forwarder.forward_if_needed(context, "CheckBuyerVoted", request),
        )
        if forwarded is not None:
            return forwarded

        result = self._execute(
            context,
            lambda: self.api.check_buyer_voted(
                buyer_id=request.buyer_id,
                item_category=request.item_category,
                item_id=request.item_id,
            ),
        )
        response = product_db_pb2.CheckBuyerVotedResponse(voted=result["voted"])
        # `vote` is optional in response and is set only when a prior vote exists.
        if result.get("vote") is not None:
            response.vote = result["vote"]
        return response


def run_server(host: str, port: int):
    """Run the product database gRPC server."""

    raft_config, sqlite_path = build_raft_config_from_env()

    logger.info("Product DB storage path: %s", sqlite_path)
    logger.info("Product DB raft self addr: %s", raft_config.self_addr)
    logger.info("Product DB raft partner addrs: %s", raft_config.partner_addrs)

    # Initialize database
    engine = init_db(db_path=sqlite_path)
    db_session_factory = make_session_factory(engine=engine)
    domain_api = ProductDomainApi(db_session_factory)
    raft = ProductRaftNode(
        config=raft_config,
        apply_fn=domain_api.apply_raft_command,
    )

    service = ProductDbService(domain_api, raft)

    try:
        # Thread pool matches the concurrent RPC model for blocking DB operations.
        server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=64))
        product_db_pb2_grpc.add_ProductDbServiceServicer_to_server(
            service,
            server,
        )
        server.add_insecure_port(f"{host}:{port}")
        server.start()
        logger.info("Product DB gRPC server listening on %s:%s", host, port)
        server.wait_for_termination()
    finally:
        try:
            service.close()
        except Exception:
            pass
        try:
            raft.stop()
        except Exception:
            pass
        try:
            # Ensure pooled DB connections are closed on shutdown.
            engine.dispose()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Product Database gRPC Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8002, help="Port to bind to")
    args = parser.parse_args()

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
