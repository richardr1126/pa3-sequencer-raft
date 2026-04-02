"""Typed domain API for product database operations."""

import json
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from databases.product.db import (
    Item,
    ItemFeedbackVote,
    ItemKeyword,
    RaftApplyResult,
    RaftApplyState,
    utcnow,
)


class ProductDomainApi:
    """Product DB business logic independent of gRPC transport."""

    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    @staticmethod
    def _error_response(code: str, message: str, grpc_status: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "grpc_status": grpc_status,
            },
        }

    def get_last_applied_raft_index(self) -> int:
        with self.db_session_factory() as db:
            state = db.get(RaftApplyState, 1)
            return 0 if state is None else int(state.last_applied_raft_index)

    def apply_committed_write(self, payload: dict[str, Any], raft_index: int) -> dict[str, Any]:
        """Apply a raft-committed write command once for a raft index."""
        if raft_index <= 0:
            return self._error_response(
                "RAFT_APPLY_ERROR",
                f"Invalid raft index: {raft_index}",
                "INTERNAL",
            )

        with self.db_session_factory() as db:
            state = db.get(RaftApplyState, 1)
            if state is None:
                state = RaftApplyState(singleton_id=1, last_applied_raft_index=0)
                db.add(state)
                db.flush()

            cached = db.get(RaftApplyResult, raft_index)
            if cached is not None:
                if raft_index > state.last_applied_raft_index:
                    state.last_applied_raft_index = raft_index
                    db.commit()
                return json.loads(cached.response_json)
            if raft_index <= state.last_applied_raft_index:
                return self._error_response(
                    "RAFT_APPLY_ERROR",
                    f"Missing cached result for already-applied raft index {raft_index}",
                    "INTERNAL",
                )

            try:
                value = self.dispatch_committed_write(db, payload)
                response = {"ok": True, "value": value}
            except ValueError as exc:
                response = self._error_response("INVALID_ARGUMENT", str(exc), "INVALID_ARGUMENT")
            except IntegrityError as exc:
                response = self._error_response(
                    "INVALID_ARGUMENT",
                    str(exc.orig) if exc.orig else str(exc),
                    "INVALID_ARGUMENT",
                )
            except Exception as exc:
                response = self._error_response("RAFT_APPLY_ERROR", str(exc), "INTERNAL")

            db.add(
                RaftApplyResult(
                    raft_index=raft_index,
                    response_json=json.dumps(response),
                )
            )
            if raft_index > state.last_applied_raft_index:
                state.last_applied_raft_index = raft_index
            db.commit()
            return response

    def dispatch_committed_write(self, db, payload: dict[str, Any]) -> dict[str, Any]:
        command_type = payload.get("type")
        handlers = {
            "CreateItem": lambda: self.create_item(
                item_id=payload["item_id"],
                item_name=payload["item_name"],
                item_category=payload["item_category"],
                keywords=list(payload.get("keywords") or []),
                condition=payload["condition"],
                sale_price=payload["sale_price"],
                quantity=payload["quantity"],
                seller_id=payload["seller_id"],
                db=db,
            ),
            "UpdateItemPrice": lambda: self.update_item_price(
                item_category=payload["item_category"],
                item_id=payload["item_id"],
                sale_price=payload["sale_price"],
                seller_id=payload.get("seller_id"),
                db=db,
            ),
            "UpdateItemQuantity": lambda: self.update_item_quantity(
                item_category=payload["item_category"],
                item_id=payload["item_id"],
                quantity=payload.get("quantity"),
                quantity_delta=payload.get("quantity_delta"),
                seller_id=payload.get("seller_id"),
                db=db,
            ),
            "DeleteItem": lambda: self.delete_item(
                item_category=payload["item_category"],
                item_id=payload["item_id"],
                seller_id=payload.get("seller_id"),
                db=db,
            ),
            "AddFeedbackVote": lambda: self.add_feedback_vote(
                buyer_id=payload["buyer_id"],
                item_category=payload["item_category"],
                item_id=payload["item_id"],
                vote=payload["vote"],
                db=db,
            ),
        }
        handler = handlers.get(command_type)
        if handler is None:
            raise ValueError(f"Unknown raft write command type: {command_type}")
        return handler()

    # -- Item related ProductDB API --

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
        item_id: int | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """Create a new item for sale."""
        import secrets

        if db is None:
            with self.db_session_factory() as session:
                while True:
                    resolved_item_id = item_id
                    if resolved_item_id is None:
                        resolved_item_id = secrets.randbelow((1 << 63) - 1) + 1
                    try:
                        result = self.create_item(
                            item_name=item_name,
                            item_category=item_category,
                            keywords=keywords,
                            condition=condition,
                            sale_price=sale_price,
                            quantity=quantity,
                            seller_id=seller_id,
                            item_id=resolved_item_id,
                            db=session,
                        )
                        session.commit()
                        return result
                    except ValueError as exc:
                        session.rollback()
                        if item_id is not None or not str(exc).startswith("Item ID collision:"):
                            raise
                        continue

        if item_id is None:
            raise ValueError("item_id is required when using an existing DB session")

        if not all(
            [
                item_name,
                item_category is not None,
                condition,
                sale_price is not None,
                quantity is not None,
                seller_id is not None,
            ]
        ):
            raise ValueError(
                "Missing required fields: item_name, item_category, condition, "
                "sale_price, quantity, seller_id"
            )

        if condition not in ("new", "used", "New", "Used"):
            raise ValueError("Condition must be 'new' or 'used'")

        resolved_condition = condition.title()

        if len(keywords) > 5:
            raise ValueError("Maximum 5 keywords allowed")

        item = Item(
            item_id=item_id,
            item_category=item_category,
            item_name=item_name,
            condition=resolved_condition,
            sale_price=sale_price,
            quantity=quantity,
            seller_id=seller_id,
            thumbs_up=0,
            thumbs_down=0,
        )
        db.add(item)

        for kw in keywords:
            if kw:
                keyword = ItemKeyword(
                    item_category=item_category,
                    item_id=item_id,
                    keyword=kw[:8],
                )
                db.add(keyword)

        try:
            db.flush()
        except IntegrityError:
            raise ValueError(f"Item ID collision: {item_id}") from None

        return {
            "item_id": item.item_id,
            "item_category": item.item_category,
            "item_name": item.item_name,
            "condition": item.condition,
            "sale_price": item.sale_price,
            "quantity": item.quantity,
            "seller_id": item.seller_id,
            "thumbs_up": item.thumbs_up,
            "thumbs_down": item.thumbs_down,
            "keywords": keywords,
        }

    def get_item(
        self,
        *,
        item_category: int,
        item_id: int,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Get an item by its ID."""
        if item_category is None or item_id is None:
            raise ValueError("Missing required fields: item_category, item_id")

        with self.db_session_factory() as db:
            item = db.execute(
                select(Item).where(
                    Item.item_category == item_category,
                    Item.item_id == item_id,
                )
            ).scalar_one_or_none()
            if not item:
                return {"item": None}

            if item.deleted_at is not None and not include_deleted:
                return {"item": None}

            keywords = (
                db.execute(
                    select(ItemKeyword.keyword).where(
                        ItemKeyword.item_category == item_category,
                        ItemKeyword.item_id == item_id,
                    )
                )
                .scalars()
                .all()
            )

            return {
                "item": {
                    "item_id": item.item_id,
                    "item_category": item.item_category,
                    "item_name": item.item_name,
                    "condition": item.condition,
                    "sale_price": item.sale_price,
                    "quantity": item.quantity,
                    "seller_id": item.seller_id,
                    "thumbs_up": item.thumbs_up,
                    "thumbs_down": item.thumbs_down,
                    "keywords": list(keywords),
                    "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
                }
            }

    def update_item_price(
        self,
        *,
        item_category: int,
        item_id: int,
        sale_price: float,
        seller_id: int | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """Update an item's sale price."""
        if db is None:
            with self.db_session_factory() as session:
                result = self.update_item_price(
                    item_category=item_category,
                    item_id=item_id,
                    sale_price=sale_price,
                    seller_id=seller_id,
                    db=session,
                )
                session.commit()
                return result

        if any(v is None for v in [item_category, item_id, sale_price]):
            raise ValueError(
                "Missing required fields: item_category, item_id, sale_price"
            )

        item = db.execute(
            select(Item).where(
                Item.item_category == item_category,
                Item.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not item or item.deleted_at is not None:
            raise ValueError(f"Item {item_id} not found")

        if seller_id is not None and item.seller_id != seller_id:
            raise ValueError("Not authorized to update this item")

        item.sale_price = sale_price
        db.flush()

        return {"sale_price": item.sale_price}

    def update_item_quantity(
        self,
        *,
        item_category: int,
        item_id: int,
        quantity: int | None = None,
        quantity_delta: int | None = None,
        seller_id: int | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """Update an item's quantity."""
        if db is None:
            with self.db_session_factory() as session:
                result = self.update_item_quantity(
                    item_category=item_category,
                    item_id=item_id,
                    quantity=quantity,
                    quantity_delta=quantity_delta,
                    seller_id=seller_id,
                    db=session,
                )
                session.commit()
                return result

        if item_category is None or item_id is None:
            raise ValueError("Missing required fields: item_category, item_id")

        if quantity is None and quantity_delta is None:
            raise ValueError("Must provide either quantity or quantity_delta")

        item = db.execute(
            select(Item).where(
                Item.item_category == item_category,
                Item.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not item or item.deleted_at is not None:
            raise ValueError(f"Item {item_id} not found")

        if seller_id is not None and item.seller_id != seller_id:
            raise ValueError("Not authorized to update this item")

        if quantity is not None:
            item.quantity = quantity
        elif quantity_delta is not None:
            new_quantity = item.quantity + quantity_delta
            if new_quantity < 0:
                raise ValueError("Quantity cannot be negative")
            item.quantity = new_quantity

        db.flush()

        return {"quantity": item.quantity}

    def delete_item(
        self,
        *,
        item_category: int,
        item_id: int,
        seller_id: int | None = None,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """Soft delete an item by setting deleted_at timestamp."""
        if db is None:
            with self.db_session_factory() as session:
                result = self.delete_item(
                    item_category=item_category,
                    item_id=item_id,
                    seller_id=seller_id,
                    db=session,
                )
                session.commit()
                return result

        if item_category is None or item_id is None:
            raise ValueError("Missing required fields: item_category, item_id")

        item = db.execute(
            select(Item).where(
                Item.item_category == item_category,
                Item.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not item or item.deleted_at is not None:
            return {"deleted": False}

        if seller_id is not None and item.seller_id != seller_id:
            raise ValueError("Not authorized to delete this item")

        item.deleted_at = utcnow()
        db.flush()

        return {"deleted": True}

    def list_items_by_seller(
        self, *, seller_id: int, include_deleted: bool = False
    ) -> dict[str, Any]:
        """List all items for a given seller."""
        if seller_id is None:
            raise ValueError("Missing required field: seller_id")

        with self.db_session_factory() as db:
            query = select(Item).where(Item.seller_id == seller_id)
            if not include_deleted:
                query = query.where(Item.deleted_at.is_(None))

            items = db.execute(query).scalars().all()

            result = []
            for item in items:
                keywords = (
                    db.execute(
                        select(ItemKeyword.keyword).where(
                            ItemKeyword.item_category == item.item_category,
                            ItemKeyword.item_id == item.item_id,
                        )
                    )
                    .scalars()
                    .all()
                )

                result.append(
                    {
                        "item_category": item.item_category,
                        "item_id": item.item_id,
                        "item_name": item.item_name,
                        "condition": item.condition,
                        "sale_price": item.sale_price,
                        "quantity": item.quantity,
                        "seller_id": item.seller_id,
                        "thumbs_up": item.thumbs_up,
                        "thumbs_down": item.thumbs_down,
                        "keywords": list(keywords),
                        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
                    }
                )

            return {"items": result}

    # -- Item search ProductDB API action --

    def search_items(self, *, item_category: int, keywords: list[str]) -> dict[str, Any]:
        """Search items by category and keywords.

        Search semantics:
        - Items must match the specified category (exact match)
        - If keywords are provided, items are ranked by keyword match count
        - Items with at least one matching keyword are returned
        - Items are ordered by: (1) number of matching keywords (desc),
          (2) item_id (asc) for consistent ordering
        - Only items with quantity > 0 and not deleted are returned
        """
        if item_category is None:
            raise ValueError("Missing required field: item_category")

        with self.db_session_factory() as db:
            # Base query: items in the specified category with quantity > 0 and not deleted
            if not keywords:
                # No keywords - return all items in category
                items = (
                    db.execute(
                        select(Item)
                        .where(
                            Item.item_category == item_category,
                            Item.quantity > 0,
                            Item.deleted_at.is_(None),
                        )
                        .order_by(Item.item_id)
                    )
                    .scalars()
                    .all()
                )

                result = []
                for item in items:
                    item_keywords = (
                        db.execute(
                            select(ItemKeyword.keyword).where(
                                ItemKeyword.item_category == item.item_category,
                                ItemKeyword.item_id == item.item_id,
                            )
                        )
                        .scalars()
                        .all()
                    )

                    result.append(
                        {
                            "item_category": item.item_category,
                            "item_id": item.item_id,
                            "item_name": item.item_name,
                            "condition": item.condition,
                            "sale_price": item.sale_price,
                            "quantity": item.quantity,
                            "seller_id": item.seller_id,
                            "thumbs_up": item.thumbs_up,
                            "thumbs_down": item.thumbs_down,
                            "keywords": list(item_keywords),
                            "match_count": 0,
                        }
                    )

                return {"items": result}

            # With keywords - find items with matching keywords and rank by match count
            # Subquery to count matching keywords per item
            keyword_match_count = (
                select(
                    ItemKeyword.item_category,
                    ItemKeyword.item_id,
                    func.count().label("match_count"),
                )
                .where(
                    ItemKeyword.item_category == item_category,
                    ItemKeyword.keyword.in_(keywords),
                )
                .group_by(ItemKeyword.item_category, ItemKeyword.item_id)
                .subquery()
            )

            # Join with items and filter by category and availability
            items_with_matches = db.execute(
                select(Item, keyword_match_count.c.match_count)
                .join(
                    keyword_match_count,
                    and_(
                        Item.item_category == keyword_match_count.c.item_category,
                        Item.item_id == keyword_match_count.c.item_id,
                    ),
                )
                .where(
                    Item.item_category == item_category,
                    Item.quantity > 0,
                    Item.deleted_at.is_(None),
                )
                .order_by(
                    keyword_match_count.c.match_count.desc(),
                    Item.item_id,
                )
            ).all()

            result = []
            for item, match_count in items_with_matches:
                item_keywords = (
                    db.execute(
                        select(ItemKeyword.keyword).where(
                            ItemKeyword.item_category == item.item_category,
                            ItemKeyword.item_id == item.item_id,
                        )
                    )
                    .scalars()
                    .all()
                )

                result.append(
                    {
                        "item_category": item.item_category,
                        "item_id": item.item_id,
                        "item_name": item.item_name,
                        "condition": item.condition,
                        "sale_price": item.sale_price,
                        "quantity": item.quantity,
                        "seller_id": item.seller_id,
                        "thumbs_up": item.thumbs_up,
                        "thumbs_down": item.thumbs_down,
                        "keywords": list(item_keywords),
                        "match_count": match_count,
                    }
                )

            return {"items": result}

    # -- Item feedback ProductDB API --

    def add_feedback_vote(
        self,
        *,
        buyer_id: int,
        item_category: int,
        item_id: int,
        vote: str,
        db: Any | None = None,
    ) -> dict[str, Any]:
        """Add a feedback vote for an item.

        A buyer can only vote once per item. The vote updates both the item's
        feedback counters and records the individual vote.
        """
        if db is None:
            with self.db_session_factory() as session:
                result = self.add_feedback_vote(
                    buyer_id=buyer_id,
                    item_category=item_category,
                    item_id=item_id,
                    vote=vote,
                    db=session,
                )
                session.commit()
                return result

        if any(v is None for v in [buyer_id, item_category, item_id, vote]):
            raise ValueError(
                "Missing required fields: buyer_id, item_category, item_id, vote"
            )

        if vote not in ("up", "down"):
            raise ValueError("Vote must be 'up' or 'down'")

        # Check if item exists and is not deleted.
        item = db.execute(
            select(Item).where(
                Item.item_category == item_category,
                Item.item_id == item_id,
            )
        ).scalar_one_or_none()
        if not item or item.deleted_at is not None:
            raise ValueError(f"Item {item_id} not found")

        # Check if buyer already voted.
        existing_vote = db.execute(
            select(ItemFeedbackVote).where(
                ItemFeedbackVote.buyer_id == buyer_id,
                ItemFeedbackVote.item_category == item_category,
                ItemFeedbackVote.item_id == item_id,
            )
        ).scalar_one_or_none()
        if existing_vote:
            raise ValueError("Buyer has already voted on this item")

        # Record the vote and update item counters.
        feedback_vote = ItemFeedbackVote(
            buyer_id=buyer_id,
            item_category=item_category,
            item_id=item_id,
            vote=vote,
        )
        db.add(feedback_vote)
        if vote == "up":
            item.thumbs_up += 1
        else:
            item.thumbs_down += 1
        db.flush()

        return {
            "thumbs_up": item.thumbs_up,
            "thumbs_down": item.thumbs_down,
            "seller_id": item.seller_id,  # Return seller_id so caller can update seller feedback
        }

    def get_item_feedback(self, *, item_category: int, item_id: int) -> dict[str, Any]:
        """Get feedback for an item."""
        if item_category is None or item_id is None:
            raise ValueError("Missing required fields: item_category, item_id")

        with self.db_session_factory() as db:
            item = db.execute(
                select(Item).where(
                    Item.item_category == item_category,
                    Item.item_id == item_id,
                )
            ).scalar_one_or_none()
            if not item:
                return {"feedback": None}

            return {
                "feedback": {
                    "thumbs_up": item.thumbs_up,
                    "thumbs_down": item.thumbs_down,
                }
            }

    def check_buyer_voted(
        self, *, buyer_id: int, item_category: int, item_id: int
    ) -> dict[str, Any]:
        """Check if a buyer has voted on an item."""
        if any(v is None for v in [buyer_id, item_category, item_id]):
            raise ValueError(
                "Missing required fields: buyer_id, item_category, item_id"
            )

        with self.db_session_factory() as db:
            vote = db.execute(
                select(ItemFeedbackVote).where(
                    ItemFeedbackVote.buyer_id == buyer_id,
                    ItemFeedbackVote.item_category == item_category,
                    ItemFeedbackVote.item_id == item_id,
                )
            ).scalar_one_or_none()

            if vote:
                return {"voted": True, "vote": vote.vote}
            return {"voted": False, "vote": None}
