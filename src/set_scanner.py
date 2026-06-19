"""
Set Scanner – builds eBay queries per card, fetches listings, computes grading opportunity.

No scraping. Only the official eBay Browse API is used.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src import config
from src.ebay_client import EbayClient, RawListing
from src.listing_cleaner import clean_and_classify_listing
from src.location_filter import is_allowed_location
from src.models import PokemonCard, PokemonSet, SetScan, SetScanResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bad keywords (same list as listing_cleaner)
# ---------------------------------------------------------------------------
_BAD_KEYWORDS = {
    "proxy", "reprint", "custom", "orica", "metal",
    "jumbo", "digital", "lot", "bundle", "collection",
}


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def build_card_queries(card: PokemonCard, pokemon_set: PokemonSet) -> dict[str, list[str]]:
    """Return eBay search queries for RAW, PSA9 and PSA10 variants."""
    name = card.search_name or card.name
    num = card.card_number

    raw_queries = [
        f"{name} {num} {pokemon_set.name}",
        f"{name} {num} Pokemon",
        f"{name} {num} {pokemon_set.code}",
    ]
    psa9_queries = [f"{name} {num} PSA 9"]
    psa10_queries = [f"{name} {num} PSA 10"]

    return {
        "raw": raw_queries,
        "psa9": psa9_queries,
        "psa10": psa10_queries,
    }


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def _median(prices: list[float]) -> float | None:
    if not prices:
        return None
    return round(statistics.median(prices), 2)


def _safe_prices(listings: list[RawListing], include_auctions: bool) -> list[float]:
    """Extract total_price from listings, optionally filtering auctions."""
    prices = []
    for l in listings:
        buying_options = l.raw.get("buyingOptions") or []
        if not include_auctions and "AUCTION" in buying_options and "FIXED_PRICE" not in buying_options:
            continue
        if l.total_price and l.total_price > 0:
            prices.append(l.total_price)
    return prices


def _filter_listings(
    listings: list[RawListing],
    include_auctions: bool,
) -> list[RawListing]:
    """Apply location filter, bad-keyword filter, and optional auction filter."""
    kept: list[RawListing] = []
    for l in listings:
        allowed, _ = is_allowed_location(
            l.location_country,
            config.EBAY_ALLOWED_COUNTRIES,
            config.EBAY_EXCLUDED_COUNTRIES,
            config.EBAY_ALLOW_UNKNOWN_LOCATION,
        )
        if not allowed:
            continue

        buying_options = l.raw.get("buyingOptions") or []
        if not include_auctions and "AUCTION" in buying_options and "FIXED_PRICE" not in buying_options:
            continue

        title_lower = l.title.lower()
        if any(kw in title_lower for kw in _BAD_KEYWORDS):
            continue

        kept.append(l)
    return kept


# ---------------------------------------------------------------------------
# Grading opportunity
# ---------------------------------------------------------------------------

def calculate_card_opportunity(
    raw_median: float | None,
    psa9_median: float | None,
    psa10_median: float | None,
) -> dict[str, Any]:
    """
    Calculate expected profit and ROI for grading a raw card.
    Uses config.*GRADING_* values for costs and probabilities.
    """
    reasons: list[str] = []

    if raw_median is None:
        return {
            "expected_profit": None,
            "roi_percent": None,
            "psa10_profit": None,
            "psa9_profit": None,
            "score": 0.0,
            "rating": "Zu wenig Daten",
            "reasons": ["Kein Raw-Preis verfügbar"],
        }

    total_cost = (
        raw_median
        + config.GRADING_COST
        + config.GRADING_SHIPPING_TO_GRADER
        + config.GRADING_RETURN_SHIPPING
    )

    fee_factor = 1 - config.GRADING_MARKETPLACE_FEE_PERCENT / 100
    risk_factor = 1 - config.GRADING_RISK_DISCOUNT_PERCENT / 100

    psa10_sell = (psa10_median or 0) * fee_factor
    psa9_sell = (psa9_median or 0) * fee_factor

    # PSA 8 or lower: assume sell at raw_median * 0.6 (distressed price)
    psa8_sell = raw_median * 0.6 * fee_factor

    psa10_profit = psa10_sell - total_cost if psa10_median else None
    psa9_profit = psa9_sell - total_cost if psa9_median else None

    expected_value = (
        config.GRADING_PSA10_PROBABILITY * psa10_sell
        + config.GRADING_PSA9_PROBABILITY * psa9_sell
        + config.GRADING_PSA8_OR_LOWER_PROBABILITY * psa8_sell
    ) * risk_factor

    expected_profit = round(expected_value - total_cost, 2)
    roi_percent = round((expected_profit / total_cost) * 100, 1) if total_cost > 0 else 0.0

    # --- Score & rating ----
    score = 0.0
    rating = "Nicht attraktiv"

    if psa10_median is None and psa9_median is None:
        return {
            "expected_profit": None,
            "roi_percent": None,
            "psa10_profit": psa10_profit,
            "psa9_profit": psa9_profit,
            "score": 0.0,
            "rating": "Zu wenig Daten",
            "reasons": ["Kein PSA-Preis verfügbar"],
        }

    if psa10_median is not None:
        reasons.append(f"PSA 10 Median: {psa10_median:.2f} €")
        if psa10_profit is not None and psa10_profit > 0:
            reasons.append(f"PSA 10 Gewinn: {psa10_profit:.2f} €")
        else:
            reasons.append("PSA 10 rentiert sich nicht")

    if psa9_median is not None:
        reasons.append(f"PSA 9 Median: {psa9_median:.2f} €")

    reasons.append(f"Gesamtkosten: {total_cost:.2f} € (Raw + Grading + Versand)")
    reasons.append(f"Erwarteter Gewinn: {expected_profit:.2f} €, ROI: {roi_percent:.1f} %")

    # Scoring
    if expected_profit > 50:
        score = 90.0
        if roi_percent > 30:
            rating = "Sehr interessant"
        else:
            rating = "Interessant"
    elif expected_profit > 10:
        score = 65.0
        rating = "Interessant"
    elif expected_profit > 0:
        score = 45.0
        if psa10_profit and psa10_profit > 0:
            rating = "Nur bei PSA 10 interessant"
        else:
            rating = "Riskant"
    elif psa10_profit and psa10_profit > 0:
        score = 30.0
        rating = "Nur bei PSA 10 interessant"
    else:
        score = 10.0
        if roi_percent < -20:
            rating = "Nicht attraktiv"
        else:
            rating = "Riskant"

    return {
        "expected_profit": expected_profit,
        "roi_percent": roi_percent,
        "psa10_profit": round(psa10_profit, 2) if psa10_profit is not None else None,
        "psa9_profit": round(psa9_profit, 2) if psa9_profit is not None else None,
        "score": round(score, 2),
        "rating": rating,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Main scan runner
# ---------------------------------------------------------------------------

def run_set_scan(
    db: Session,
    pokemon_set: PokemonSet,
    *,
    max_cards: int | None = None,
    include_auctions: bool | None = None,
    lookback_days: int | None = None,
    marketplace: str | None = None,
) -> SetScan:
    """
    Scan all cards in *pokemon_set* using the eBay Browse API.

    Creates and returns a SetScan record with all SetScanResult rows.
    Errors per card are collected but do not abort the scan.
    """
    effective_max_cards = max_cards if max_cards is not None else config.SET_SCAN_MAX_CARDS
    effective_auctions = include_auctions if include_auctions is not None else config.SET_SCAN_INCLUDE_AUCTIONS
    effective_days = lookback_days if lookback_days is not None else config.SET_SCAN_DAYS
    effective_marketplace = marketplace or config.EBAY_MARKETPLACE

    scan = SetScan(
        set_id=pokemon_set.id,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(scan)
    db.flush()  # get scan.id

    cards: list[PokemonCard] = (
        db.query(PokemonCard)
        .filter_by(set_id=pokemon_set.id)
        .order_by(PokemonCard.card_number)
        .limit(effective_max_cards)
        .all()
    )

    logger.info("Set scan %d: scanning %d cards for set %r", scan.id, len(cards), pokemon_set.name)

    client = EbayClient()
    errors: list[dict] = []
    total_found = 0
    total_saved = 0

    for card in cards:
        try:
            result = _scan_card(
                db=db,
                client=client,
                scan=scan,
                card=card,
                pokemon_set=pokemon_set,
                include_auctions=effective_auctions,
                lookback_days=effective_days,
                marketplace=effective_marketplace,
            )
            total_found += result["listings_found"]
            total_saved += result["listings_saved"]
            scan.cards_scanned = (scan.cards_scanned or 0) + 1
            db.flush()
        except Exception as exc:
            logger.error("Set scan %d: error on card %d (%s): %s", scan.id, card.id, card.name, exc, exc_info=True)
            errors.append({"card_id": card.id, "card_name": card.name, "error": str(exc)})

    scan.status = "done"
    scan.finished_at = datetime.now(timezone.utc)
    scan.listings_found = total_found
    scan.listings_saved = total_saved
    if errors:
        scan.errors_json = json.dumps(errors, ensure_ascii=False)

    db.commit()
    logger.info(
        "Set scan %d done: %d cards, %d listings found, %d saved, %d errors",
        scan.id, scan.cards_scanned, total_found, total_saved, len(errors),
    )
    return scan


def _scan_card(
    *,
    db: Session,
    client: EbayClient,
    scan: SetScan,
    card: PokemonCard,
    pokemon_set: PokemonSet,
    include_auctions: bool,
    lookback_days: int,
    marketplace: str,
) -> dict[str, int]:
    queries = build_card_queries(card, pokemon_set)

    raw_listings = _fetch_all(client, queries["raw"], marketplace, lookback_days)
    psa9_listings = _fetch_all(client, queries["psa9"], marketplace, lookback_days)
    psa10_listings = _fetch_all(client, queries["psa10"], marketplace, lookback_days)

    raw_filtered = _filter_listings(raw_listings, include_auctions)
    psa9_filtered = _filter_listings(psa9_listings, include_auctions)
    psa10_filtered = _filter_listings(psa10_listings, include_auctions)

    raw_prices = _safe_prices(raw_filtered, include_auctions=True)
    psa9_prices = _safe_prices(psa9_filtered, include_auctions=True)
    psa10_prices = _safe_prices(psa10_filtered, include_auctions=True)

    raw_median = _median(raw_prices)
    raw_min = round(min(raw_prices), 2) if raw_prices else None
    psa9_median = _median(psa9_prices)
    psa10_median = _median(psa10_prices)

    psa10_mult = round(psa10_median / raw_median, 2) if raw_median and psa10_median else None
    psa9_mult = round(psa9_median / raw_median, 2) if raw_median and psa9_median else None

    opp = calculate_card_opportunity(raw_median, psa9_median, psa10_median)

    result_row = SetScanResult(
        set_scan_id=scan.id,
        pokemon_card_id=card.id,
        raw_median_price=raw_median,
        raw_min_price=raw_min,
        raw_listing_count=len(raw_filtered),
        psa9_median_price=psa9_median,
        psa9_listing_count=len(psa9_filtered),
        psa10_median_price=psa10_median,
        psa10_listing_count=len(psa10_filtered),
        psa10_multiplier=psa10_mult,
        psa9_multiplier=psa9_mult,
        expected_profit=opp["expected_profit"],
        roi_percent=opp["roi_percent"],
        score=opp["score"],
        rating=opp["rating"],
        reasons_json=json.dumps(opp["reasons"], ensure_ascii=False),
    )
    db.add(result_row)

    listings_found = len(raw_listings) + len(psa9_listings) + len(psa10_listings)
    listings_saved = len(raw_filtered) + len(psa9_filtered) + len(psa10_filtered)

    logger.info(
        "  Card %s %s: raw=%d psa9=%d psa10=%d → %s (score=%.0f)",
        card.card_number, card.name,
        len(raw_filtered), len(psa9_filtered), len(psa10_filtered),
        opp["rating"], opp["score"],
    )

    return {"listings_found": listings_found, "listings_saved": listings_saved}


def _fetch_all(
    client: EbayClient,
    queries: list[str],
    marketplace: str,
    lookback_days: int,
) -> list[RawListing]:
    """Fetch and deduplicate listings across multiple queries."""
    seen_ids: set[str] = set()
    result: list[RawListing] = []
    for q in queries:
        try:
            listings = client.search_recent_listings(
                query=q,
                marketplace=marketplace,
                lookback_days=lookback_days,
                limit=config.EBAY_SEARCH_LIMIT,
                max_pages=config.EBAY_MAX_PAGES,
            )
            for l in listings:
                if l.ebay_item_id not in seen_ids:
                    seen_ids.add(l.ebay_item_id)
                    result.append(l)
        except Exception as exc:
            logger.warning("Query %r failed: %s", q, exc)
    return result
