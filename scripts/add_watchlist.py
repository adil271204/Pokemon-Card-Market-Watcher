"""
Insert the example Umbreon VMAX watchlist into the database.

Run:
    python scripts/add_watchlist.py

You can safely run this multiple times – it checks for an existing entry first.
"""

import logging
import sys

sys.path.insert(0, ".")

from src.database import get_session  # noqa: E402
from src.models import Watchlist  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EXAMPLE_WATCHLIST = dict(
    name="Umbreon VMAX Alt Art PSA 10",
    query="Umbreon VMAX 215/203 PSA 10",
    marketplace="EBAY_DE",
    max_price=None,
    target_market_price=1500.0,
    min_discount_percent=15.0,
    target_grade="PSA 10",
    target_language="English",
    enabled=True,
)


def main() -> None:
    session = get_session()
    try:
        existing = (
            session.query(Watchlist)
            .filter_by(name=EXAMPLE_WATCHLIST["name"])
            .first()
        )
        if existing:
            logger.info("Watchlist %r already exists (id=%d) – skipping.", existing.name, existing.id)
            return

        wl = Watchlist(**EXAMPLE_WATCHLIST)
        session.add(wl)
        session.commit()
        logger.info("Watchlist %r inserted with id=%d.", wl.name, wl.id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
