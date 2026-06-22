"""Database query helpers used by dashboard routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models import Alert, SeenListing, Watchlist


# ---------------------------------------------------------------------------
# Overview / KPI
# ---------------------------------------------------------------------------


def get_overview_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    total_watchlists = db.query(func.count(Watchlist.id)).scalar() or 0
    active_watchlists = db.query(func.count(Watchlist.id)).filter_by(enabled=True).scalar() or 0
    total_listings = (
        db.query(func.count(SeenListing.id))
        .filter(SeenListing.deleted_at.is_(None))
        .scalar() or 0
    )
    total_alerts = db.query(func.count(Alert.id)).scalar() or 0
    alerts_24h = (
        db.query(func.count(Alert.id))
        .filter(Alert.sent_at >= cutoff_24h)
        .scalar() or 0
    )
    avg_score = db.query(func.avg(Alert.score)).scalar()
    avg_score = round(float(avg_score), 1) if avg_score else 0.0

    best_alert = (
        db.query(Alert)
        .order_by(Alert.score.desc())
        .first()
    )

    # Top 5 watchlists by alert count
    top_watchlists = (
        db.query(Watchlist.name, func.count(Alert.id).label("alert_count"))
        .join(Alert, Alert.watchlist_id == Watchlist.id, isouter=True)
        .group_by(Watchlist.id, Watchlist.name)
        .order_by(func.count(Alert.id).desc())
        .limit(5)
        .all()
    )

    return {
        "total_watchlists": total_watchlists,
        "active_watchlists": active_watchlists,
        "total_listings": total_listings,
        "total_alerts": total_alerts,
        "alerts_24h": alerts_24h,
        "avg_score": avg_score,
        "best_alert": best_alert,
        "top_watchlists": [{"name": r[0], "alert_count": r[1]} for r in top_watchlists],
    }


def get_recent_listings(db: Session, limit: int = 10) -> list[SeenListing]:
    return (
        db.query(SeenListing)
        .filter(SeenListing.deleted_at.is_(None))
        .order_by(SeenListing.first_seen_at.desc())
        .limit(limit)
        .all()
    )


def get_recent_alerts(db: Session, limit: int = 10) -> list[Alert]:
    return (
        db.query(Alert)
        .order_by(Alert.sent_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


def get_all_watchlists(db: Session) -> list[Watchlist]:
    return db.query(Watchlist).order_by(Watchlist.created_at.desc()).all()


def get_watchlist(db: Session, watchlist_id: int) -> Watchlist | None:
    return db.query(Watchlist).filter_by(id=watchlist_id).first()


def create_watchlist(db: Session, data: dict[str, Any]) -> Watchlist:
    wl = Watchlist(**data)
    db.add(wl)
    db.commit()
    db.refresh(wl)
    return wl


def update_watchlist(db: Session, wl: Watchlist, data: dict[str, Any]) -> Watchlist:
    for key, value in data.items():
        setattr(wl, key, value)
    db.commit()
    db.refresh(wl)
    return wl


def toggle_watchlist(db: Session, wl: Watchlist) -> Watchlist:
    wl.enabled = not wl.enabled
    db.commit()
    db.refresh(wl)
    return wl


def delete_watchlist(db: Session, wl: Watchlist) -> None:
    """Hard-delete a watchlist and all associated data."""
    db.query(Alert).filter_by(watchlist_id=wl.id).delete()
    db.query(SeenListing).filter_by(watchlist_id=wl.id).delete()
    db.delete(wl)
    db.commit()


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


def get_listings(
    db: Session,
    watchlist_id: int | None = None,
    title_search: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    listing_type: str | None = None,
    sort: str = "first_seen_at",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[SeenListing], int]:
    q = db.query(SeenListing).filter(SeenListing.deleted_at.is_(None))

    if watchlist_id:
        q = q.filter(SeenListing.watchlist_id == watchlist_id)
    if title_search:
        q = q.filter(SeenListing.title.ilike(f"%{title_search}%"))
    if price_min is not None:
        q = q.filter(SeenListing.total_price >= price_min)
    if price_max is not None:
        q = q.filter(SeenListing.total_price <= price_max)
    if date_from:
        q = q.filter(SeenListing.first_seen_at >= date_from)
    if date_to:
        q = q.filter(SeenListing.first_seen_at <= date_to)
    if listing_type and listing_type in ("FIXED_PRICE", "AUCTION"):
        q = q.filter(SeenListing.listing_type == listing_type)

    total = q.count()

    sort_col = SeenListing.first_seen_at
    if sort == "total_price":
        sort_col = SeenListing.total_price
    q = q.order_by(sort_col.desc())

    items = q.offset((page - 1) * per_page).limit(per_page).all()
    return items, total


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def get_alerts(
    db: Session,
    watchlist_id: int | None = None,
    min_score: float | None = None,
    min_discount: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Alert], int]:
    q = db.query(Alert)

    if watchlist_id:
        q = q.filter(Alert.watchlist_id == watchlist_id)
    if min_score is not None:
        q = q.filter(Alert.score >= min_score)
    if min_discount is not None:
        q = q.filter(Alert.discount_percent >= min_discount)
    if date_from:
        q = q.filter(Alert.sent_at >= date_from)
    if date_to:
        q = q.filter(Alert.sent_at <= date_to)

    total = q.count()
    items = q.order_by(Alert.sent_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return items, total


def get_alert_kpis(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    alerts_7d = db.query(func.count(Alert.id)).filter(Alert.sent_at >= cutoff_7d).scalar() or 0
    best = db.query(Alert).order_by(Alert.score.desc()).first()
    avg_discount = db.query(func.avg(Alert.discount_percent)).scalar()
    avg_score = db.query(func.avg(Alert.score)).scalar()

    return {
        "alerts_7d": alerts_7d,
        "best_alert": best,
        "avg_discount": round(float(avg_discount), 1) if avg_discount else 0.0,
        "avg_score": round(float(avg_score), 1) if avg_score else 0.0,
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def get_analytics(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)

    # Alerts per day last 30 days – grouped in Python to stay DB-agnostic
    # (SQLite lacks date_trunc; PostgreSQL has it but this works on both)
    recent_alerts_raw = (
        db.query(Alert.sent_at, Alert.score)
        .filter(Alert.sent_at >= cutoff_30d)
        .order_by(Alert.sent_at)
        .all()
    )

    _day_buckets: dict[str, list[float]] = {}
    for sent_at, score in recent_alerts_raw:
        day_key = str(sent_at)[:10]
        _day_buckets.setdefault(day_key, []).append(float(score) if score else 0.0)

    alerts_per_day = [
        {
            "day": day,
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 1),
        }
        for day, scores in sorted(_day_buckets.items())
    ]

    # Top watchlists by alert count
    top_wl = (
        db.query(Watchlist.name, func.count(Alert.id).label("cnt"))
        .join(Alert, Alert.watchlist_id == Watchlist.id, isouter=True)
        .group_by(Watchlist.id, Watchlist.name)
        .order_by(func.count(Alert.id).desc())
        .limit(10)
        .all()
    )

    # Avg discount per watchlist
    avg_discount_wl = (
        db.query(Watchlist.name, func.avg(Alert.discount_percent).label("avg_disc"))
        .join(Alert, Alert.watchlist_id == Watchlist.id)
        .group_by(Watchlist.id, Watchlist.name)
        .order_by(func.avg(Alert.discount_percent).desc())
        .all()
    )

    # Listings per watchlist (exclude soft-deleted)
    listings_per_wl = (
        db.query(Watchlist.name, func.count(SeenListing.id).label("cnt"))
        .join(SeenListing, SeenListing.watchlist_id == Watchlist.id, isouter=True)
        .filter(SeenListing.deleted_at.is_(None))
        .group_by(Watchlist.id, Watchlist.name)
        .order_by(func.count(SeenListing.id).desc())
        .all()
    )

    total_listings = (
        db.query(func.count(SeenListing.id))
        .filter(SeenListing.deleted_at.is_(None))
        .scalar() or 0
    )
    total_alerts = db.query(func.count(Alert.id)).scalar() or 0
    alert_ratio = round(total_alerts / total_listings * 100, 1) if total_listings else 0.0

    return {
        "alerts_per_day": alerts_per_day,
        "top_watchlists": [{"name": r[0], "count": r[1]} for r in top_wl],
        "avg_discount_per_wl": [
            {"name": r[0], "avg_discount": round(float(r[1]), 1) if r[1] else 0.0}
            for r in avg_discount_wl
        ],
        "listings_per_wl": [{"name": r[0], "count": r[1]} for r in listings_per_wl],
        "total_listings": total_listings,
        "total_alerts": total_alerts,
        "alert_ratio": alert_ratio,
    }


# ---------------------------------------------------------------------------
# Soft delete / restore
# ---------------------------------------------------------------------------


def soft_delete_listing(db: Session, listing_id: int) -> SeenListing | None:
    """Set deleted_at = now() for one listing. Returns the listing or None."""
    listing = db.query(SeenListing).filter_by(id=listing_id).first()
    if listing is None:
        return None
    listing.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return listing


def soft_delete_listings(db: Session, listing_ids: list[int]) -> int:
    """Soft-delete a batch of listings by ID. Returns count of affected rows."""
    if not listing_ids:
        return 0
    now = datetime.now(timezone.utc)
    updated = (
        db.query(SeenListing)
        .filter(SeenListing.id.in_(listing_ids))
        .filter(SeenListing.deleted_at.is_(None))
        .all()
    )
    for listing in updated:
        listing.deleted_at = now
    db.commit()
    return len(updated)


def restore_listing(db: Session, listing_id: int) -> SeenListing | None:
    """Clear deleted_at so a listing becomes visible again."""
    listing = db.query(SeenListing).filter_by(id=listing_id).first()
    if listing is None:
        return None
    listing.deleted_at = None
    db.commit()
    return listing
