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
    location_country = Column(String(10), nullable=True)
    location_city = Column(String(100), nullable=True)
    location_postal_code = Column(String(20), nullable=True)
    location_state = Column(String(100), nullable=True)
    location_raw_json = Column(Text, nullable=True)
    raw_payload_json = Column(Text, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
    # Buying / listing type fields (added in migrate_listing_type_fields.py)
    listing_type = Column(String(20), nullable=True)          # FIXED_PRICE | AUCTION | UNKNOWN
    buying_options_json = Column(Text, nullable=True)         # JSON array of buyingOptions
    best_offer_available = Column(Boolean, nullable=True, default=False)
    current_bid_price = Column(Float, nullable=True)
    bid_count = Column(Integer, nullable=True)
    item_end_date = Column(DateTime(timezone=True), nullable=True)
    # Listing status system (added in migrate_listing_status_fields.py)
    listing_status = Column(String(50), nullable=True, default="new")
    status_reason = Column(Text, nullable=True)
    user_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    purchased_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, default=_now, onupdate=_now)

    watchlist = relationship("Watchlist", back_populates="seen_listings")

    def __repr__(self) -> str:
        return f"<SeenListing ebay_item_id={self.ebay_item_id!r}>"


class PokemonSet(Base):
    """A Pokémon card set (e.g. 'Scarlet & Violet 151')."""

    __tablename__ = "pokemon_sets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=False)
    language = Column(String(10), nullable=False, default="EN")
    total_cards = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    source_url = Column(Text, nullable=True)
    source_name = Column(String(100), nullable=True)

    cards = relationship("PokemonCard", back_populates="pokemon_set", cascade="all, delete-orphan")
    scans = relationship("SetScan", back_populates="pokemon_set", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PokemonSet id={self.id} code={self.code!r}>"


class PokemonCard(Base):
    """A single card within a Pokémon set."""

    __tablename__ = "pokemon_cards"

    id = Column(Integer, primary_key=True)
    set_id = Column(Integer, ForeignKey("pokemon_sets.id"), nullable=False)
    name = Column(String(255), nullable=False)
    card_number = Column(String(20), nullable=False)
    rarity = Column(String(100), nullable=True)
    language = Column(String(10), nullable=False, default="EN")
    variant = Column(String(100), nullable=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    search_name = Column(String(500), nullable=True)
    source_raw_text = Column(Text, nullable=True)
    import_confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    pokemon_set = relationship("PokemonSet", back_populates="cards")
    scan_results = relationship("SetScanResult", back_populates="card", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<PokemonCard id={self.id} name={self.name!r} num={self.card_number!r}>"


class SetScan(Base):
    """A scan run for an entire set."""

    __tablename__ = "set_scans"

    id = Column(Integer, primary_key=True)
    set_id = Column(Integer, ForeignKey("pokemon_sets.id"), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending/running/done/error
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    cards_scanned = Column(Integer, nullable=False, default=0)
    listings_found = Column(Integer, nullable=False, default=0)
    listings_saved = Column(Integer, nullable=False, default=0)
    errors_json = Column(Text, nullable=True)

    pokemon_set = relationship("PokemonSet", back_populates="scans")
    results = relationship("SetScanResult", back_populates="scan", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SetScan id={self.id} set_id={self.set_id} status={self.status!r}>"


class SetScanResult(Base):
    """Per-card result from a set scan."""

    __tablename__ = "set_scan_results"

    id = Column(Integer, primary_key=True)
    set_scan_id = Column(Integer, ForeignKey("set_scans.id"), nullable=False)
    pokemon_card_id = Column(Integer, ForeignKey("pokemon_cards.id"), nullable=False)
    raw_median_price = Column(Float, nullable=True)
    raw_min_price = Column(Float, nullable=True)
    raw_listing_count = Column(Integer, nullable=False, default=0)
    psa9_median_price = Column(Float, nullable=True)
    psa9_listing_count = Column(Integer, nullable=False, default=0)
    psa10_median_price = Column(Float, nullable=True)
    psa10_listing_count = Column(Integer, nullable=False, default=0)
    psa10_multiplier = Column(Float, nullable=True)
    psa9_multiplier = Column(Float, nullable=True)
    expected_profit = Column(Float, nullable=True)
    roi_percent = Column(Float, nullable=True)
    score = Column(Float, nullable=False, default=0.0)
    rating = Column(String(50), nullable=False, default="Zu wenig Daten")
    reasons_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    scan = relationship("SetScan", back_populates="results")
    card = relationship("PokemonCard", back_populates="scan_results")

    def __repr__(self) -> str:
        return f"<SetScanResult id={self.id} card_id={self.pokemon_card_id} rating={self.rating!r}>"


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
