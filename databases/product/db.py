"""Product database schema + SQLite setup.

- Uses SQLite with the DB file stored in this same directory.
- Defines SQLAlchemy ORM tables for items, item keywords, and item feedback.

"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKeyConstraint,
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


DB_PATH = Path(__file__).resolve().with_name("product.sqlite3")
DATABASE_URL = f"sqlite:///{DB_PATH}"


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    # PA1 item identifier is a 2-tuple: (item_category, item_id)
    item_category: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        nullable=False,
        index=True,
    )

    # Random 63-bit integer assigned by the server (unique within category)
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)

    # Basic item details
    item_name: Mapped[str] = mapped_column(String(32), nullable=False)
    condition: Mapped[str] = mapped_column(String(4), nullable=False)  # "New" | "Used"
    sale_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Ownership (seller_id is managed in the customer DB)
    seller_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Feedback summary
    thumbs_up: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thumbs_down: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    # Relationships
    keywords: Mapped[list[ItemKeyword]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    feedback_votes: Mapped[list[ItemFeedbackVote]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ItemKeyword(Base):
    __tablename__ = "item_keywords"

    # Item-keyword association
    item_category: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(8), primary_key=True)

    # Foreign key constraint to items table
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_category", "item_id"],
            ["items.item_category", "items.item_id"],
            name="fk_item_keywords_item",
            ondelete="CASCADE",
        ),
    )

    # Relationship to item for this keyword
    item: Mapped[Item] = relationship(back_populates="keywords")


class ItemFeedbackVote(Base):
    __tablename__ = "item_feedback_votes"

    vote_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Buyer identity is managed in the customer DB
    buyer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Item being voted on
    item_category: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # "up" or "down" (application validates)
    vote: Mapped[str] = mapped_column(String(4), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Foreign key constraint to items table
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_category", "item_id"],
            ["items.item_category", "items.item_id"],
            name="fk_item_feedback_votes_item",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "buyer_id",
            "item_category",
            "item_id",
            name="uq_buyer_item_vote",
        ),
    )

    # Relationship to item for this feedback vote
    item: Mapped[Item] = relationship(back_populates="feedback_votes")


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
