"""Core watcher logic – ties all modules together."""

import json
import logging

from sqlalchemy.orm import Session

from src import config
from src.database import get_session
from src.deal_scorer import calculate_deal_score
from src.ebay_client import EbayClient, RawListing
from src import job_runs as jr
from src.listing_cleaner import clean_and_classify_listing
from src.location_filter import is_allowed_location
from src.models import Alert, JobRun, SeenListing, Watchlist
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
    6. Send Telegram alerts for qualifying listings.
    7. Persist new listings and alerts.
    """

    def __init__(self) -> None:
        self._ebay = EbayClient()
        self._telegram = TelegramNotifier()

    def run(self) -> None:
        """Run one full monitoring cycle."""
        session = get_session()
        job_run_id: int | None = None
        stats: dict = {
            "watchlists_checked": 0,
            "queries_executed": 0,
            "api_results_count": 0,
            "listings_saved": 0,
            "listings_skipped_existing": 0,
            "listings_filtered_country": 0,
            "alerts_sent": 0,
            "errors_count": 0,
        }
        watchlist_errors = 0

        try:
            job_run_id = jr.start_job_run(session, "watcher")
            session.commit()

            watchlists = session.query(Watchlist).filter_by(enabled=True).all()
            if not watchlists:
                logger.warning("No enabled watchlists found – nothing to do.")
                jr.finish_job_run(session, job_run_id, "success", stats)
                session.commit()
                return

            logger.info("Running watcher for %d watchlist(s).", len(watchlists))
            stats["watchlists_checked"] = len(watchlists)

            for wl in watchlists:
                wl_stats = self._process_watchlist(wl, session)
                if wl_stats.get("error"):
                    watchlist_errors += 1
                for key in (
                    "queries_executed", "api_results_count", "listings_saved",
                    "listings_skipped_existing", "listings_filtered_country", "alerts_sent",
                ):
                    stats[key] = stats.get(key, 0) + wl_stats.get(key, 0)

            session.commit()

            if watchlist_errors == len(watchlists):
                final_status = "failed"
            elif watchlist_errors > 0:
                final_status = "partial_success"
            else:
                final_status = "success"

            stats["errors_count"] = watchlist_errors
            jr.finish_job_run(session, job_run_id, final_status, stats)
            session.commit()

        except Exception as exc:
            logger.exception("Watcher run failed: %s", exc)
            session.rollback()
            if job_run_id is not None:
                try:
                    jr.record_job_error(session, job_run_id, exc)
                    session.commit()
                except Exception:
                    logger.exception("Could not record watcher job error")
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_watchlist(self, wl: Watchlist, session: Session) -> dict:
        logger.info("Processing watchlist: %r (query=%r)", wl.name, wl.query)
        wl_stats: dict = {
            "queries_executed": 1,
            "api_results_count": 0,
            "listings_saved": 0,
            "listings_skipped_existing": 0,
            "listings_filtered_country": 0,
            "alerts_sent": 0,
            "error": False,
        }

        try:
            raw_listings = self._ebay.search_new_listings(
                query=wl.query,
                marketplace=wl.marketplace,
                max_price=wl.max_price,
            )
        except Exception as exc:
            logger.error("eBay search failed for watchlist %r: %s", wl.name, exc)
            wl_stats["error"] = True
            return wl_stats

        wl_stats["api_results_count"] = len(raw_listings)
        logger.info("Fetched %d listing(s) from eBay.", len(raw_listings))

        for listing in raw_listings:
            result = self._handle_listing(listing, wl, session)
            if result in ("saved", "alert_sent"):
                wl_stats["listings_saved"] += 1
            elif result == "existing":
                wl_stats["listings_skipped_existing"] += 1
            elif result == "filtered_country":
                wl_stats["listings_filtered_country"] += 1
            if result == "alert_sent":
                wl_stats["alerts_sent"] += 1

        logger.info(
            "Watchlist %r – %d new listing(s) saved, %d alert(s) sent.",
            wl.name, wl_stats["listings_saved"], wl_stats["alerts_sent"],
        )
        return wl_stats

    def _is_seen(self, ebay_item_id: str, session: Session) -> bool:
        return (
            session.query(SeenListing)
            .filter_by(ebay_item_id=ebay_item_id)
            .first()
            is not None
        )

    def _alert_exists(self, ebay_item_id: str, watchlist_id: int, session: Session) -> bool:
        return (
            session.query(Alert)
            .filter_by(ebay_item_id=ebay_item_id, watchlist_id=watchlist_id)
            .first()
            is not None
        )

    def _save_seen(self, listing: RawListing, wl: Watchlist, session: Session) -> None:
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
            location_country=listing.location_country,
            location_city=listing.location_city,
            location_postal_code=listing.location_postal_code,
            location_state=listing.location_state,
            location_raw_json=json.dumps(listing.location_raw) if listing.location_raw else None,
            raw_payload_json=json.dumps(listing.raw),
        )
        session.add(seen)

    def _save_alert(
        self,
        listing: RawListing,
        wl: Watchlist,
        discount_percent: float,
        score: float,
        telegram_sent: bool,
        telegram_error: str | None,
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
            telegram_sent=telegram_sent,
            telegram_error=telegram_error,
        )
        session.add(alert)

    def _handle_listing(
        self, listing: RawListing, wl: Watchlist, session: Session
    ) -> str:
        """Process a single listing. Returns a result key string."""
        # Location filter
        allowed, reasons = is_allowed_location(
            listing.location_country,
            config.EBAY_ALLOWED_COUNTRIES,
            config.EBAY_EXCLUDED_COUNTRIES,
            config.EBAY_ALLOW_UNKNOWN_LOCATION,
        )
        if not allowed:
            logger.debug(
                "Skipping listing %s – location rejected: %s (country=%s)",
                listing.ebay_item_id, reasons, listing.location_country,
            )
            return "filtered_country"

        if self._is_seen(listing.ebay_item_id, session):
            logger.debug("Skipping already-seen listing %s", listing.ebay_item_id)
            return "existing"

        logger.debug(
            "New listing %s – %r (%.2f %s)",
            listing.ebay_item_id, listing.title, listing.total_price, listing.currency,
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
            listing.ebay_item_id, deal.score, deal.discount_percent,
            deal.should_alert, deal.reason,
        )

        # Persist the listing so we never re-process it
        self._save_seen(listing, wl, session)

        if not deal.should_alert:
            return "saved"

        # Dedup: never send a second alert for the same ebay_item_id + watchlist
        if self._alert_exists(listing.ebay_item_id, wl.id, session):
            logger.debug("Alert already exists for listing %s – skipping Telegram", listing.ebay_item_id)
            return "saved"

        listing_type = getattr(listing, "listing_type", "FIXED_PRICE") or "FIXED_PRICE"

        telegram_error: str | None = None
        telegram_sent = False
        try:
            telegram_sent = self._telegram.send_alert(
                title=listing.title,
                total_price=listing.total_price,
                target_market_price=wl.target_market_price or 0,
                discount_percent=deal.discount_percent,
                score=deal.score,
                url=listing.url,
                currency=listing.currency,
                listing_type=listing_type,
                location_country=listing.location_country,
                price=listing.price,
                shipping=listing.shipping,
                bid_count=getattr(listing, "bid_count", None),
                current_bid_price=getattr(listing, "current_bid_price", None),
                item_end_date=str(getattr(listing, "item_end_date", None)),
                watchlist_name=wl.name,
            )
        except Exception as exc:
            logger.error("Telegram send raised unexpectedly for %s: %s", listing.ebay_item_id, exc)
            telegram_error = str(exc)[:500]

        self._save_alert(
            listing, wl, deal.discount_percent, deal.score,
            telegram_sent=telegram_sent,
            telegram_error=telegram_error,
            session=session,
        )

        return "alert_sent" if telegram_sent else "saved"
