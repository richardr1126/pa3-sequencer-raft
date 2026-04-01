"""Typed domain API for customer database operations."""

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from databases.customer.db import (
    Buyer,
    BuyerPurchase,
    BuyerSavedCartItem,
    BuyerSession,
    BuyerSessionCartItem,
    Seller,
    SellerSession,
)

SESSION_TIMEOUT_MINUTES = 5


def _resolve_now(now_iso: str) -> datetime:
    if not now_iso:
        raise ValueError("Missing required field: now_iso")
    parsed = datetime.fromisoformat(now_iso)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class CustomerDomainApi:
    """Customer DB business logic independent of gRPC transport."""

    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    # -- Buyer related CustomerDB API --

    def create_buyer(self, *, buyer_name: str, login_name: str, password: str) -> dict[str, Any]:
        """Create a new buyer account."""

        if not all([buyer_name, login_name, password]):
            raise ValueError(
                "Missing required fields: buyer_name, login_name, password"
            )

        with self.db_session_factory() as db:
            # Check if login_name already exists
            existing = db.execute(
                select(Buyer).where(Buyer.login_name == login_name)
            ).scalar_one_or_none()
            if existing:
                raise ValueError(f"Login name '{login_name}' already exists")

            buyer = Buyer(
                buyer_name=buyer_name,
                login_name=login_name,
                password=password,
                items_purchased=0,
            )
            db.add(buyer)
            db.commit()

            return {
                "buyer_id": buyer.buyer_id,
                "buyer_name": buyer.buyer_name,
                "items_purchased": buyer.items_purchased,
            }

    def authenticate_buyer(self, *, login_name: str, password: str) -> dict[str, Any]:
        """Authenticate a buyer by login_name and password."""

        if not all([login_name, password]):
            raise ValueError("Missing required fields: login_name, password")

        with self.db_session_factory() as db:
            buyer = db.execute(
                select(Buyer).where(Buyer.login_name == login_name)
            ).scalar_one_or_none()

            if not buyer or buyer.password != password:
                return {"authenticated": False, "buyer": None}

            return {
                "authenticated": True,
                "buyer": {
                    "buyer_id": buyer.buyer_id,
                    "buyer_name": buyer.buyer_name,
                    "items_purchased": buyer.items_purchased,
                },
            }

    def get_buyer(self, *, buyer_id: int) -> dict[str, Any]:
        """Get buyer by ID."""
        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")

        with self.db_session_factory() as db:
            buyer = db.get(Buyer, buyer_id)
            if not buyer:
                return {"buyer": None}

            return {
                "buyer": {
                    "buyer_id": buyer.buyer_id,
                    "buyer_name": buyer.buyer_name,
                    "items_purchased": buyer.items_purchased,
                }
            }

    def increment_buyer_purchases(self, *, buyer_id: int, amount: int = 1) -> dict[str, Any]:
        """Increment the items_purchased count for a buyer."""

        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")

        with self.db_session_factory() as db:
            buyer = db.get(Buyer, buyer_id)
            if not buyer:
                raise ValueError(f"Buyer {buyer_id} not found")

            buyer.items_purchased += amount
            db.commit()

            return {"items_purchased": buyer.items_purchased}

    # -- Seller related CustomerDB API --

    def create_seller(self, *, seller_name: str, login_name: str, password: str) -> dict[str, Any]:
        """Create a new seller account."""

        if not all([seller_name, login_name, password]):
            raise ValueError(
                "Missing required fields: seller_name, login_name, password"
            )

        with self.db_session_factory() as db:
            # Check if login_name already exists
            existing = db.execute(
                select(Seller).where(Seller.login_name == login_name)
            ).scalar_one_or_none()
            if existing:
                raise ValueError(f"Login name '{login_name}' already exists")

            seller = Seller(
                seller_name=seller_name,
                login_name=login_name,
                password=password,
                thumbs_up=0,
                thumbs_down=0,
                items_sold=0,
            )
            db.add(seller)
            db.commit()

            return {
                "seller_id": seller.seller_id,
                "seller_name": seller.seller_name,
                "thumbs_up": seller.thumbs_up,
                "thumbs_down": seller.thumbs_down,
                "items_sold": seller.items_sold,
            }

    def authenticate_seller(self, *, login_name: str, password: str) -> dict[str, Any]:
        """Authenticate a seller by login_name and password."""

        if not all([login_name, password]):
            raise ValueError("Missing required fields: login_name, password")

        with self.db_session_factory() as db:
            seller = db.execute(
                select(Seller).where(Seller.login_name == login_name)
            ).scalar_one_or_none()

            if not seller or seller.password != password:
                return {"authenticated": False, "seller": None}

            return {
                "authenticated": True,
                "seller": {
                    "seller_id": seller.seller_id,
                    "seller_name": seller.seller_name,
                    "thumbs_up": seller.thumbs_up,
                    "thumbs_down": seller.thumbs_down,
                    "items_sold": seller.items_sold,
                },
            }

    def get_seller(self, *, seller_id: int) -> dict[str, Any]:
        """Get seller by ID."""
        if seller_id is None:
            raise ValueError("Missing required field: seller_id")

        with self.db_session_factory() as db:
            seller = db.get(Seller, seller_id)
            if not seller:
                return {"seller": None}

            return {
                "seller": {
                    "seller_id": seller.seller_id,
                    "seller_name": seller.seller_name,
                    "thumbs_up": seller.thumbs_up,
                    "thumbs_down": seller.thumbs_down,
                    "items_sold": seller.items_sold,
                }
            }

    def update_seller_feedback(self, *, seller_id: int, thumbs_up_delta: int = 0, thumbs_down_delta: int = 0) -> dict[str, Any]:
        """Update seller feedback (thumbs up/down)."""

        if seller_id is None:
            raise ValueError("Missing required field: seller_id")

        with self.db_session_factory() as db:
            seller = db.get(Seller, seller_id)
            if not seller:
                raise ValueError(f"Seller {seller_id} not found")

            seller.thumbs_up += thumbs_up_delta
            seller.thumbs_down += thumbs_down_delta
            db.commit()

            return {
                "thumbs_up": seller.thumbs_up,
                "thumbs_down": seller.thumbs_down,
            }

    def increment_seller_items_sold(self, *, seller_id: int, amount: int = 1) -> dict[str, Any]:
        """Increment the items_sold count for a seller."""

        if seller_id is None:
            raise ValueError("Missing required field: seller_id")

        with self.db_session_factory() as db:
            seller = db.get(Seller, seller_id)
            if not seller:
                raise ValueError(f"Seller {seller_id} not found")

            seller.items_sold += amount
            db.commit()

            return {"items_sold": seller.items_sold}

    # -- Buyer session related CustomerDB API --

    def create_buyer_session(
        self,
        *,
        buyer_id: int,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Create a new buyer session."""
        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            # Verify buyer exists
            buyer = db.get(Buyer, buyer_id)
            if not buyer:
                raise ValueError(f"Buyer {buyer_id} not found")

            now = _resolve_now(now_iso)

            session = BuyerSession(
                session_id=session_id,
                buyer_id=buyer_id,
                created_at=now,
                last_activity_at=now,
            )
            db.add(session)
            db.commit()

            return {"session_id": session_id}

    def validate_buyer_session(
        self,
        *,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Validate a buyer session and return buyer info if valid.

        This is a read-only operation. If the session is expired, it returns
        expired status but does NOT delete the session. The caller should
        invoke BuyerSession.Cleanup to delete the expired session.
        """
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(BuyerSession, session_id)
            if not session:
                return {
                    "valid": False,
                    "buyer": None,
                    "last_activity_at": None,
                    "reason": "not_found",
                }

            # Check for timeout - but don't delete here (read-only)
            timeout_threshold = _resolve_now(now_iso) - timedelta(
                minutes=SESSION_TIMEOUT_MINUTES
            )
            if session.last_activity_at < timeout_threshold:
                return {
                    "valid": False,
                    "buyer": None,
                    "last_activity_at": session.last_activity_at.isoformat(),
                    "reason": "expired",
                }

            buyer = session.buyer
            return {
                "valid": True,
                "buyer": {
                    "buyer_id": buyer.buyer_id,
                    "buyer_name": buyer.buyer_name,
                    "items_purchased": buyer.items_purchased,
                },
                "last_activity_at": session.last_activity_at.isoformat(),
            }

    def delete_buyer_session(self, *, session_id: str) -> dict[str, Any]:
        """Delete a buyer session (logout)."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(BuyerSession, session_id)
            if session:
                db.delete(session)
                db.commit()
                return {"deleted": True}
            return {"deleted": False}

    def touch_buyer_session(
        self,
        *,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Update last_activity_at for a buyer session."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(BuyerSession, session_id)
            if not session:
                return {"touched": False}

            session.last_activity_at = _resolve_now(now_iso)
            db.commit()
            return {"touched": True}

    def cleanup_expired_buyer_sessions(
        self,
        *,
        now_iso: str,
    ) -> dict[str, Any]:
        """Delete all expired buyer sessions."""
        timeout_threshold = _resolve_now(now_iso) - timedelta(
            minutes=SESSION_TIMEOUT_MINUTES
        )

        with self.db_session_factory() as db:
            result = db.execute(
                delete(BuyerSession).where(
                    BuyerSession.last_activity_at < timeout_threshold
                )
            )
            db.commit()
            return {"deleted_count": result.rowcount}

    def cleanup_buyer_session(self, *, session_id: str) -> dict[str, Any]:
        """Delete a single expired buyer session by session_id.

        Called by backend when validate returns 'expired'. This allows
        validate to be a read-only operation while still cleaning up
        expired sessions.
        """
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            result = db.execute(
                delete(BuyerSession).where(BuyerSession.session_id == session_id)
            )
            db.commit()
            return {"deleted": result.rowcount > 0}

    # -- Seller session related CustomerDB API --

    def create_seller_session(
        self,
        *,
        seller_id: int,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Create a new seller session."""
        if seller_id is None:
            raise ValueError("Missing required field: seller_id")
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            # Verify seller exists
            seller = db.get(Seller, seller_id)
            if not seller:
                raise ValueError(f"Seller {seller_id} not found")

            now = _resolve_now(now_iso)

            session = SellerSession(
                session_id=session_id,
                seller_id=seller_id,
                created_at=now,
                last_activity_at=now,
            )
            db.add(session)
            db.commit()

            return {"session_id": session_id}

    def validate_seller_session(
        self,
        *,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Validate a seller session and return seller info if valid.

        This is a read-only operation. If the session is expired, it returns
        expired status but does NOT delete the session. The caller should
        invoke SellerSession.Cleanup to delete the expired session.
        """
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(SellerSession, session_id)
            if not session:
                return {
                    "valid": False,
                    "seller": None,
                    "last_activity_at": None,
                    "reason": "not_found",
                }

            # Check for timeout - but don't delete here (read-only)
            timeout_threshold = _resolve_now(now_iso) - timedelta(
                minutes=SESSION_TIMEOUT_MINUTES
            )
            if session.last_activity_at < timeout_threshold:
                return {
                    "valid": False,
                    "seller": None,
                    "last_activity_at": session.last_activity_at.isoformat(),
                    "reason": "expired",
                }

            seller = session.seller
            return {
                "valid": True,
                "seller": {
                    "seller_id": seller.seller_id,
                    "seller_name": seller.seller_name,
                    "thumbs_up": seller.thumbs_up,
                    "thumbs_down": seller.thumbs_down,
                    "items_sold": seller.items_sold,
                },
                "last_activity_at": session.last_activity_at.isoformat(),
            }

    def delete_seller_session(self, *, session_id: str) -> dict[str, Any]:
        """Delete a seller session (logout)."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(SellerSession, session_id)
            if session:
                db.delete(session)
                db.commit()
                return {"deleted": True}
            return {"deleted": False}

    def touch_seller_session(
        self,
        *,
        session_id: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Update last_activity_at for a seller session."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            session = db.get(SellerSession, session_id)
            if not session:
                return {"touched": False}

            session.last_activity_at = _resolve_now(now_iso)
            db.commit()
            return {"touched": True}

    def cleanup_expired_seller_sessions(
        self,
        *,
        now_iso: str,
    ) -> dict[str, Any]:
        """Delete all expired seller sessions."""
        timeout_threshold = _resolve_now(now_iso) - timedelta(
            minutes=SESSION_TIMEOUT_MINUTES
        )

        with self.db_session_factory() as db:
            result = db.execute(
                delete(SellerSession).where(
                    SellerSession.last_activity_at < timeout_threshold
                )
            )
            db.commit()
            return {"deleted_count": result.rowcount}

    def cleanup_seller_session(self, *, session_id: str) -> dict[str, Any]:
        """Delete a single expired seller session by session_id.

        Called by backend when validate returns 'expired'. This allows
        validate to be a read-only operation while still cleaning up
        expired sessions.
        """
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            result = db.execute(
                delete(SellerSession).where(SellerSession.session_id == session_id)
            )
            db.commit()
            return {"deleted": result.rowcount > 0}

    # -- Cart related CustomerDB API (session cart) --

    def get_session_cart(self, *, session_id: str) -> dict[str, Any]:
        """Get all items in a session's cart."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            items = (
                db.execute(
                    select(BuyerSessionCartItem).where(
                        BuyerSessionCartItem.session_id == session_id
                    )
                )
                .scalars()
                .all()
            )

            return {
                "items": [
                    {
                        "item_category": item.item_category,
                        "item_id": item.item_id,
                        "quantity": item.quantity,
                    }
                    for item in items
                ]
            }

    def set_session_cart_item(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
        quantity: int,
    ) -> dict[str, Any]:
        """Set/update an item in the session cart."""

        if not all(
            [
                session_id,
                item_category is not None,
                item_id is not None,
                quantity is not None,
            ]
        ):
            raise ValueError(
                "Missing required fields: session_id, item_category, item_id, quantity"
            )

        with self.db_session_factory() as db:
            # Verify session exists
            session = db.get(BuyerSession, session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            # Find existing cart item
            cart_item = db.execute(
                select(BuyerSessionCartItem).where(
                    BuyerSessionCartItem.session_id == session_id,
                    BuyerSessionCartItem.item_category == item_category,
                    BuyerSessionCartItem.item_id == item_id,
                )
            ).scalar_one_or_none()

            if quantity is not None and quantity <= 0:
                # Remove item if quantity is 0 or negative
                if cart_item:
                    db.delete(cart_item)
                    db.commit()
                return {"updated": True, "quantity": 0}

            if cart_item:
                cart_item.quantity = quantity
            else:
                cart_item = BuyerSessionCartItem(
                    session_id=session_id,
                    item_category=item_category,
                    item_id=item_id,
                    quantity=quantity,
                )
                db.add(cart_item)

            db.commit()
            return {"updated": True, "quantity": quantity}

    def remove_session_cart_item(
        self,
        *,
        session_id: str,
        item_category: int,
        item_id: int,
    ) -> dict[str, Any]:
        """Remove an item from the session cart."""

        if not all([session_id, item_category is not None, item_id is not None]):
            raise ValueError(
                "Missing required fields: session_id, item_category, item_id"
            )

        with self.db_session_factory() as db:
            result = db.execute(
                delete(BuyerSessionCartItem).where(
                    BuyerSessionCartItem.session_id == session_id,
                    BuyerSessionCartItem.item_category == item_category,
                    BuyerSessionCartItem.item_id == item_id,
                )
            )
            db.commit()
            return {"removed": result.rowcount > 0}

    def clear_session_cart(self, *, session_id: str) -> dict[str, Any]:
        """Clear all items from a session's cart."""
        if not session_id:
            raise ValueError("Missing required field: session_id")

        with self.db_session_factory() as db:
            result = db.execute(
                delete(BuyerSessionCartItem).where(
                    BuyerSessionCartItem.session_id == session_id
                )
            )
            db.commit()
            return {"cleared_count": result.rowcount}

    # -- Cart related CustomerDB API (saved cart) --

    def get_saved_cart(self, *, buyer_id: int) -> dict[str, Any]:
        """Get all items in a buyer's saved cart."""
        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")

        with self.db_session_factory() as db:
            items = (
                db.execute(
                    select(BuyerSavedCartItem).where(
                        BuyerSavedCartItem.buyer_id == buyer_id
                    )
                )
                .scalars()
                .all()
            )

            return {
                "items": [
                    {
                        "item_category": item.item_category,
                        "item_id": item.item_id,
                        "quantity": item.quantity,
                    }
                    for item in items
                ]
            }

    def save_cart(self, *, session_id: str, buyer_id: int) -> dict[str, Any]:
        """Save the session cart to the saved cart (overwrites saved cart)."""

        if not all([session_id, buyer_id is not None]):
            raise ValueError("Missing required fields: session_id, buyer_id")

        with self.db_session_factory() as db:
            # Get session cart items
            session_items = (
                db.execute(
                    select(BuyerSessionCartItem).where(
                        BuyerSessionCartItem.session_id == session_id
                    )
                )
                .scalars()
                .all()
            )

            # Clear existing saved cart
            db.execute(
                delete(BuyerSavedCartItem).where(
                    BuyerSavedCartItem.buyer_id == buyer_id
                )
            )

            # Copy session cart to saved cart
            for item in session_items:
                saved_item = BuyerSavedCartItem(
                    buyer_id=buyer_id,
                    item_category=item.item_category,
                    item_id=item.item_id,
                    quantity=item.quantity,
                )
                db.add(saved_item)

            db.commit()
            return {"saved_count": len(session_items)}

    def clear_saved_cart(self, *, buyer_id: int) -> dict[str, Any]:
        """Clear a buyer's saved cart."""
        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")

        with self.db_session_factory() as db:
            result = db.execute(
                delete(BuyerSavedCartItem).where(
                    BuyerSavedCartItem.buyer_id == buyer_id
                )
            )
            db.commit()
            return {"cleared_count": result.rowcount}

    def load_saved_cart(self, *, session_id: str, buyer_id: int) -> dict[str, Any]:
        """Load the saved cart into the session cart."""

        if not all([session_id, buyer_id is not None]):
            raise ValueError("Missing required fields: session_id, buyer_id")

        with self.db_session_factory() as db:
            # Get saved cart items
            saved_items = (
                db.execute(
                    select(BuyerSavedCartItem).where(
                        BuyerSavedCartItem.buyer_id == buyer_id
                    )
                )
                .scalars()
                .all()
            )

            # Clear existing session cart
            db.execute(
                delete(BuyerSessionCartItem).where(
                    BuyerSessionCartItem.session_id == session_id
                )
            )

            # Copy saved cart to session cart
            for item in saved_items:
                session_item = BuyerSessionCartItem(
                    session_id=session_id,
                    item_category=item.item_category,
                    item_id=item.item_id,
                    quantity=item.quantity,
                )
                db.add(session_item)

            db.commit()
            return {"loaded_count": len(saved_items)}

    # -- Purchase related CustomerDB API --

    def add_purchase(
        self,
        *,
        buyer_id: int,
        item_category: int,
        item_id: int,
        quantity: int = 1,
        purchased_at_iso: str,
    ) -> dict[str, Any]:
        """Record a purchase for a buyer."""

        if not all(
            [buyer_id is not None, item_category is not None, item_id is not None]
        ):
            raise ValueError(
                "Missing required fields: buyer_id, item_category, item_id"
            )
        if not purchased_at_iso:
            raise ValueError("Missing required field: purchased_at_iso")

        with self.db_session_factory() as db:
            purchase = BuyerPurchase(
                buyer_id=buyer_id,
                item_category=item_category,
                item_id=item_id,
                quantity=quantity,
                purchased_at=_resolve_now(purchased_at_iso),
            )
            db.add(purchase)
            db.commit()

            return {"purchase_id": purchase.purchase_id}

    def get_purchase_history(self, *, buyer_id: int) -> dict[str, Any]:
        """Get purchase history for a buyer."""
        if buyer_id is None:
            raise ValueError("Missing required field: buyer_id")

        with self.db_session_factory() as db:
            purchases = (
                db.execute(
                    select(BuyerPurchase)
                    .where(BuyerPurchase.buyer_id == buyer_id)
                    .order_by(BuyerPurchase.purchased_at.desc())
                )
                .scalars()
                .all()
            )

            return {
                "purchases": [
                    {
                        "purchase_id": p.purchase_id,
                        "item_category": p.item_category,
                        "item_id": p.item_id,
                        "quantity": p.quantity,
                        "purchased_at": p.purchased_at.isoformat(),
                    }
                    for p in purchases
                ]
            }
