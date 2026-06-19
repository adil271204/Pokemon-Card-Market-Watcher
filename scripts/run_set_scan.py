"""
CLI script: run a set scan for a given set code.

Usage:
    python scripts/run_set_scan.py --set-code sv151
    python scripts/run_set_scan.py --set-code sv151 --language EN --max-cards 50

Can be run as a Render Job for large sets that would time out via the web dashboard.
"""

from __future__ import annotations

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config  # noqa: E402 – must come after sys.path update
from src.database import get_session  # noqa: E402
from src.models import PokemonSet  # noqa: E402
from src.set_scanner import run_set_scan  # noqa: E402

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a set scan via CLI")
    parser.add_argument("--set-code", required=True, help="Set code, e.g. sv151")
    parser.add_argument("--language", default="EN", help="Language filter (default: EN)")
    parser.add_argument("--max-cards", type=int, default=None, help="Max cards to scan")
    parser.add_argument("--include-auctions", action="store_true", help="Include auction listings")
    parser.add_argument("--days", type=int, default=None, help="Lookback days (default: SET_SCAN_DAYS)")
    args = parser.parse_args()

    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(
            code=args.set_code.lower(), language=args.language.upper()
        ).first()

        if not pset:
            logger.error(
                "Set with code=%r language=%r not found in database. "
                "Import cards first via the dashboard or CSV import.",
                args.set_code, args.language,
            )
            sys.exit(1)

        card_count = pset.cards.count() if hasattr(pset.cards, "count") else len(pset.cards)
        logger.info("Starting scan for set %r (%s) – %d cards in DB", pset.name, pset.code, card_count)

        scan = run_set_scan(
            db,
            pset,
            max_cards=args.max_cards,
            include_auctions=args.include_auctions or None,
            lookback_days=args.days,
        )

    logger.info(
        "Scan %d complete: %d cards scanned, %d listings found, %d after filter.",
        scan.id, scan.cards_scanned, scan.listings_found, scan.listings_saved,
    )
    if scan.errors_json:
        import json
        errors = json.loads(scan.errors_json)
        logger.warning("%d card error(s):", len(errors))
        for e in errors:
            logger.warning("  %s: %s", e.get("card_name"), e.get("error"))


if __name__ == "__main__":
    main()
