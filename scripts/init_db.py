"""
Create all database tables.

Run once before the first deployment, or after adding new models:

    python scripts/init_db.py
"""

import logging
import sys

# Make sure we can import from project root
sys.path.insert(0, ".")

from src import config  # noqa: E402 – must happen after sys.path update
from src.database import engine  # noqa: E402
from src.models import Base  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Creating tables against: %s", config.DATABASE_URL or "SQLite fallback")
    Base.metadata.create_all(bind=engine)
    logger.info("Done – all tables created (or already existed).")


if __name__ == "__main__":
    main()
