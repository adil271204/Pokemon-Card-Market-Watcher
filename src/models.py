"""SQLAlchemy ORM models."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Watchlist(Base):
    """A search query the watcher should monitor regularly."""

    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    query = Column(String(500), nullable=False)
    marketplace = Column(String(50), nullable=False, default="EBAY_DE")
    max_price = Column(Float, nullable=True)
    target_market_price = Column(Float, nullable=True)
    min_discount_percent = Column(Float, nullable=False, default=15.0)
    target_grade = Column(String(50), nullable=True)    # e.g. "PSA 10"
    target_language = Column(String(50), nullable=True) # e.g. "English"
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    seen_listings = relationship("SeenListing", back_populates="watchlist")
    alerts = relationship("Alert", back_populates="watchlist")

    def __repr__(self) -> str:
        return f"<Watchlist id={self.id} name={self.name!r}>"


class SeenListing(Base):
    """A listing we have already processed (dedup table)."""

    __tablename__ = "seen_listings"

    id = Column(Integer, primary_key=True)
    ebay_item_id = Column(String(50), nullable=False, unique=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False)
    title = Column(String(500), nullable=True)
    price = Column(Float, nullable=True)
    shipping = Column(Float, nullable=True)
    total_price = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True, default="EUR")
    url = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    condition = Column(String(100), nullable=True)
    listing_date = Column(DateTime(timezone=True), nullable=True)
    item_creation_date = Column(String(50), nullable=True)
    item_origin_date = Column(String(50), nullable=True)
    raw_payload_json = Column(Text, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)

    watchlist = relationship("Watchlist", back_populates="seen_listings")

    def __repr__(self) -> str:
        return f"<SeenListing ebay_item_id={self.ebay_item_id!r}>"


class Alert(Base):
    """A deal alert that was (or should have been) sent via Telegram."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    ebay_item_id = Column(String(50), nullable=False, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id"), nullable=False)
    title = Column(String(500), nullable=True)
    total_price = Column(Float, nullable=True)
    target_market_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)
    score = Column(Float, nullable=True)
    url = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    watchlist = relationship("Watchlist", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert id={self.id} score={self.score} ebay_item_id={self.ebay_item_id!r}>"
