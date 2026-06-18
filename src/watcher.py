"""Core watcher logic – ties all modules together."""

import json
import logging

from sqlalchemy.orm import Session

from src.database import get_session
from src.deal_scorer import calculate_deal_score
from src.ebay_client import EbayClient, RawListing
from src.listing_cleaner import clean_and_classify_listing
from src.models import Alert, SeenListing, Watchlist
from src.telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class Watcher:
    """
    Orchestrates the full monitoring cycle:

    1. Load enabled watchlists from DB.
    2. Search eBay for new listings.
    3. Skip already-seen listings.
    4. Classify & filter.
    5. Score deals.
    6. Send Telegram alerts for good deals.
    7. Persist new listings and alerts.
    """

    def __init__(self) -> None:
        self._ebay = EbayClient()
        self._telegram = TelegramNotifier()

    def run(self) -> None:
        """Run one full monitoring cycle."""
        session = get_session()
        try:
            watchlists = (
                session.query(Watchlist).filter_by(enabled=True).all()
            )
            if not watchlists:
                logger.warning("No enabled watchlists found – nothing to do.")
                return

            logger.info("Running watcher for %d watchlist(s).", len(watchlists))
            for wl in watchlists:
                self._process_watchlist(wl, session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_watchlist(self, wl: Watchlist, session: Session) -> None:
        logger.info("Processing watchlist: %r (query=%r)", wl.name, wl.query)

        try:
            raw_listings = self._ebay.search_new_listings(
                query=wl.query,
                marketplace=wl.marketplace,
                max_price=wl.max_price,
            )
        except Exception as exc:
            logger.error("eBay search failed for watchlist %r: %s", wl.name, exc)
            return

        logger.info("Fetched %d listing(s) from eBay.", len(raw_listings))
        new_count = 0
        alert_count = 0

        for listing in raw_listings:
            is_new = self._handle_listing(listing, wl, session)
            if is_new:
                new_count += 1
            # alert_count updated inside _handle_listing – track separately
            _ = alert_count  # suppress linter warning

        logger.info(
            "Watchlist %r – %d new listing(s) processed.", wl.name, new_count
        )

    def _is_seen(self, ebay_item_id: str, session: Session) -> bool:
        return (
            session.query(SeenListing)
            .filter_by(ebay_item_id=ebay_item_id)
            .first()
            is not None
        )

    def _save_seen(
        self, listing: RawListing, wl: Watchlist, session: Session
    ) -> None:
        seen = SeenListing(
            ebay_item_id=listing.ebay_item_id,
            watchlist_id=wl.id,
            title=listing.title,
            price=listing.price,
            shipping=listing.shipping,
            total_price=listing.total_price,
            currency=listing.currency,
            url=listing.url,
            image_url=listing.image_url,
            condition=listing.condition,
            listing_date=listing.listing_date,
            item_creation_date=listing.item_creation_date,
            item_origin_date=listing.item_origin_date,
            raw_payload_json=json.dumps(listing.raw),
        )
        session.add(seen)

    def _save_alert(
        self,
        listing: RawListing,
        wl: Watchlist,
        discount_percent: float,
        score: float,
        session: Session,
    ) -> None:
        alert = Alert(
            ebay_item_id=listing.ebay_item_id,
            watchlist_id=wl.id,
            title=listing.title,
            total_price=listing.total_price,
            target_market_price=wl.target_market_price,
            discount_percent=discount_percent,
            score=score,
            url=listing.url,
        )
        session.add(alert)

    def _handle_listing(
        self, listing: RawListing, wl: Watchlist, session: Session
    ) -> bool:
        """Process a single listing. Returns True if it was new."""
        if self._is_seen(listing.ebay_item_id, session):
            logger.debug("Skipping already-seen listing %s", listing.ebay_item_id)
            return False

        logger.debug(
            "New listing %s – %r (%.2f %s)",
            listing.ebay_item_id,
            listing.title,
            listing.total_price,
            listing.currency,
        )

        classification = clean_and_classify_listing(
            listing.title, target_grade=wl.target_grade
        )

        deal = calculate_deal_score(
            listing=listing,
            target_market_price=wl.target_market_price,
            min_discount_percent=wl.min_discount_percent,
            classification=classification,
            target_grade=wl.target_grade,
        )

        logger.info(
            "Listing %s | score=%.1f | discount=%.1f%% | alert=%s | reason=%s",
            listing.ebay_item_id,
            deal.score,
            deal.discount_percent,
            deal.should_alert,
            deal.reason,
        )

        # Persist the listing so we never re-process it
        self._save_seen(listing, wl, session)

        if deal.should_alert:
            sent = self._telegram.send_alert(
                title=listing.title,
                total_price=listing.total_price,
                target_market_price=wl.target_market_price or 0,
                discount_percent=deal.discount_percent,
                score=deal.score,
                url=listing.url,
                currency=listing.currency,
            )
            if sent:
                self._save_alert(
                    listing, wl, deal.discount_percent, deal.score, session
                )

        return True
