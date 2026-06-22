"""
Idempotent migration: add listing_type fields to seen_listings.
Safe to run multiple times – checks column existence before ALTER.
Supports both PostgreSQL (production) and SQLite (local dev).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.database import engine

# PostgreSQL type → SQLite type mapping
COLUMNS_PG = [
    ("listing_type",           "VARCHAR(20)"),
    ("buying_options_json",    "TEXT"),
    ("best_offer_available",   "BOOLEAN DEFAULT FALSE"),
    ("current_bid_price",      "DOUBLE PRECISION"),
    ("bid_count",              "INTEGER"),
    ("item_end_date",          "TIMESTAMP WITH TIME ZONE"),
]

COLUMNS_SQLITE = [
    ("listing_type",           "TEXT"),
    ("buying_options_json",    "TEXT"),
    ("best_offer_available",   "INTEGER DEFAULT 0"),
    ("current_bid_price",      "REAL"),
    ("bid_count",              "INTEGER"),
    ("item_end_date",          "TEXT"),
]


def _is_sqlite(conn) -> bool:
    return "sqlite" in str(conn.engine.url).lower() if hasattr(conn, "engine") else False


def _column_exists_pg(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def _column_exists_sqlite(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def run() -> None:
    dialect = engine.dialect.name  # "postgresql" or "sqlite"
    is_sqlite = dialect == "sqlite"

    columns = COLUMNS_SQLITE if is_sqlite else COLUMNS_PG
    print(f"migrate_listing_type_fields: dialect={dialect}")

    with engine.begin() as conn:
        for col, definition in columns:
            if is_sqlite:
                exists = _column_exists_sqlite(conn, "seen_listings", col)
            else:
                exists = _column_exists_pg(conn, "seen_listings", col)

            if not exists:
                conn.execute(text(f"ALTER TABLE seen_listings ADD COLUMN {col} {definition}"))
                print(f"  added column: {col}")
            else:
                print(f"  already exists: {col}")

    print("migrate_listing_type_fields: done")


if __name__ == "__main__":
    run()
