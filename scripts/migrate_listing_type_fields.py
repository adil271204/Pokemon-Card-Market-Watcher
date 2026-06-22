"""
Idempotent migration: add listing_type fields to seen_listings.
Safe to run multiple times – checks column existence before ALTER.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.database import engine

COLUMNS = [
    ("listing_type",           "VARCHAR(20)"),
    ("buying_options_json",    "TEXT"),
    ("best_offer_available",   "BOOLEAN DEFAULT FALSE"),
    ("current_bid_price",      "DOUBLE PRECISION"),
    ("bid_count",              "INTEGER"),
    ("item_end_date",          "TIMESTAMP WITH TIME ZONE"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def run() -> None:
    with engine.begin() as conn:
        for col, definition in COLUMNS:
            if not _column_exists(conn, "seen_listings", col):
                conn.execute(text(f"ALTER TABLE seen_listings ADD COLUMN {col} {definition}"))
                print(f"  added column: {col}")
            else:
                print(f"  already exists: {col}")
    print("migrate_listing_type_fields: done")


if __name__ == "__main__":
    run()
