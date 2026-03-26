"""Customer database schema + SQLite setup.

- Passwords are stored in clear text for PA1 simplicity.
- Session/cart persistence lives here to keep frontends stateless.
- Uses SQLite with the DB file stored in this same directory.
- Defines SQLAlchemy ORM tables for buyers/sellers and customer-owned state.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)


DB_PATH = Path(__file__).resolve().with_name("customer.sqlite3")
DATABASE_URL = f"sqlite:///{DB_PATH}"


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Buyer(Base):
    __tablename__ = "buyers"

    # Basic buyer details
    buyer_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    buyer_name: Mapped[str] = mapped_column(String(32), nullable=False)
    items_purchased: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Needed for CreateAccount/Login
    login_name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    password: Mapped[str] = mapped_column(String(128), nullable=False)

    # Cascade delete relationships to clean up sessions/cart/purchases on buyer deletion.
    sessions: Mapped[list[BuyerSession]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    # Saved cart persists across sessions (only updated on SaveCart).
    saved_cart_items: Mapped[list[BuyerSavedCartItem]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )
    purchases: Mapped[list[BuyerPurchase]] = relationship(
        back_populates="buyer", cascade="all, delete-orphan"
    )


class Seller(Base):
    __tablename__ = "sellers"

    # Basic seller details
    seller_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    seller_name: Mapped[str] = mapped_column(String(32), nullable=False)
    thumbs_up: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Needed for CreateAccount/Login
    login_name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    password: Mapped[str] = mapped_column(String(128), nullable=False)

    # Cascade delete relationships to clean up sessions on seller deletion.
    sessions: Mapped[list[SellerSession]] = relationship(
        back_populates="seller", cascade="all, delete-orphan"
    )


class BuyerSession(Base):
    __tablename__ = "buyer_sessions"

    # Buyer session details
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.buyer_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    # Relationship to buyer and cart items for this session
    buyer: Mapped[Buyer] = relationship(back_populates="sessions")
    # Session cart is keyed by session_id so multiple concurrent sessions are isolated.
    cart_items: Mapped[list[BuyerSessionCartItem]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class SellerSession(Base):
    __tablename__ = "seller_sessions"

    # Seller session details
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seller_id: Mapped[int] = mapped_column(
        ForeignKey("sellers.seller_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, index=True
    )

    # Relationship to seller for this session
    seller: Mapped[Seller] = relationship(back_populates="sessions")


class BuyerSavedCartItem(Base):
    __tablename__ = "buyer_saved_cart_items"

    # Saved cart item details
    saved_cart_item_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.buyer_id"), nullable=False)

    item_category: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Unique constraint to prevent duplicate saved cart items for the same buyer.
    __table_args__ = (
        UniqueConstraint(
            "buyer_id", "item_category", "item_id", name="uq_buyer_saved_cart_item"
        ),
    )

    # Relationship to buyer for this saved cart item
    buyer: Mapped[Buyer] = relationship(back_populates="saved_cart_items")


class BuyerSessionCartItem(Base):
    __tablename__ = "buyer_session_cart_items"

    # Session cart item details
    # (seperate from saved cart to allow multiple concurrent sessions with isolated carts)
    session_cart_item_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("buyer_sessions.session_id", ondelete="CASCADE"), nullable=False
    )

    item_category: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Unique constraint to prevent duplicate cart items for the same session.
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "item_category",
            "item_id",
            name="uq_buyer_session_cart_item",
        ),
    )

    # Relationship to session for this cart item
    session: Mapped[BuyerSession] = relationship(back_populates="cart_items")


class BuyerPurchase(Base):
    __tablename__ = "buyer_purchases"

    # Purchase details
    purchase_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    buyer_id: Mapped[int] = mapped_column(ForeignKey("buyers.buyer_id"), nullable=False)

    item_category: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    purchased_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    # Relationship to buyer for this purchase
    buyer: Mapped[Buyer] = relationship(back_populates="purchases")


def make_engine(*, echo: bool = False) -> Engine:
    engine = create_engine(
        DATABASE_URL,
        echo=echo,
        connect_args={"timeout": 600},
        pool_size=20,  # Base pool size
        max_overflow=100,  # Allow up to 120 total connections
        pool_timeout=None,  # Unlimited timeout for waiting on a connection
    )

    # Ensure FK constraints and WAL are enforced in SQLite.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=600000")
        cursor.close()

    return engine


def make_session_factory(*, engine: Engine | None = None):
    engine = engine or make_engine()
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def init_db(*, engine: Engine | None = None) -> Engine:
    engine = engine or make_engine()
    Base.metadata.create_all(engine)
    return engine
