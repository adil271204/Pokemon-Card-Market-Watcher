"""
Idempotent migration: add location columns to seen_listings.
Safe to run multiple times. Never deletes data.

Usage:
    python scripts/migrate_listing_location_fields.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa
from src.database import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("location_country",     "VARCHAR(10)"),
    ("location_city",        "VARCHAR(100)"),
    ("location_postal_code", "VARCHAR(20)"),
    ("location_state",       "VARCHAR(100)"),
    ("location_raw_json",    "TEXT"),
]


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
        return result.fetchone() is not None
    result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result.fetchall())


def migrate() -> None:
    with engine.begin() as conn:
        logger.info("Dialect: %s", conn.dialect.name)
        for col_name, col_type in NEW_COLUMNS:
            if _column_exists(conn, "seen_listings", col_name):
                logger.info("Already exists – skipping: %s", col_name)
                continue
            conn.execute(sa.text(
                f"ALTER TABLE seen_listings ADD COLUMN {col_name} {col_type}"
            ))
            logger.info("Added: %s %s", col_name, col_type)
    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
