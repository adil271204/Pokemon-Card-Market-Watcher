"""
Idempotent migration: add new columns to seen_listings if they don't exist yet.
Safe to run multiple times – existing data is never deleted.

Usage:
    python scripts/migrate_recent_listing_fields.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEW_COLUMNS = [
    ("image_url",           "TEXT"),
    ("condition",           "VARCHAR(100)"),
    ("listing_date",        "TIMESTAMPTZ"),
    ("item_creation_date",  "VARCHAR(50)"),
    ("item_origin_date",    "VARCHAR(50)"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    dialect = conn.dialect.name
    if dialect == "postgresql":
        result = conn.execute(
            __import__("sqlalchemy").text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
        return result.fetchone() is not None
    else:
        # SQLite: pragma
        result = conn.execute(
            __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
        )
        return any(row[1] == column for row in result.fetchall())


def migrate() -> None:
    import sqlalchemy as sa

    with engine.begin() as conn:
        dialect = conn.dialect.name
        logger.info("Database dialect: %s", dialect)

        for col_name, col_type in NEW_COLUMNS:
            if _column_exists(conn, "seen_listings", col_name):
                logger.info("Column already exists – skipping: %s", col_name)
                continue

            if dialect == "postgresql":
                # TIMESTAMPTZ not valid in SQLite
                actual_type = col_type
                sql = f"ALTER TABLE seen_listings ADD COLUMN {col_name} {actual_type}"
            else:
                # SQLite doesn't support TIMESTAMPTZ, use TEXT
                actual_type = "TEXT" if col_type == "TIMESTAMPTZ" else col_type
                sql = f"ALTER TABLE seen_listings ADD COLUMN {col_name} {actual_type}"

            conn.execute(sa.text(sql))
            logger.info("Added column: %s %s", col_name, actual_type)

    logger.info("Migration complete.")


if __name__ == "__main__":
    migrate()
