"""
Idempotent migration: add deleted_at column to seen_listings.
Safe to run multiple times. Never deletes data.

Usage:
    python scripts/migrate_seenlisting_deleted_at.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa
from src.database import engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
        if _column_exists(conn, "seen_listings", "deleted_at"):
            logger.info("Column deleted_at already exists – nothing to do.")
            return

        col_type = "TIMESTAMPTZ" if conn.dialect.name == "postgresql" else "TEXT"
        conn.execute(sa.text(f"ALTER TABLE seen_listings ADD COLUMN deleted_at {col_type}"))
        logger.info("Added column deleted_at %s to seen_listings.", col_type)

    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
