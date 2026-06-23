"""
Idempotent migration: add listing status fields to seen_listings.
Supports PostgreSQL and SQLite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.database import engine

COLUMNS_PG = [
    ("listing_status", "VARCHAR(50) DEFAULT 'new'"),
    ("status_reason", "TEXT"),
    ("user_note", "TEXT"),
    ("reviewed_at", "TIMESTAMP WITH TIME ZONE"),
    ("purchased_at", "TIMESTAMP WITH TIME ZONE"),
    ("updated_at", "TIMESTAMP WITH TIME ZONE"),
]

COLUMNS_SQLITE = [
    ("listing_status", "TEXT DEFAULT 'new'"),
    ("status_reason", "TEXT"),
    ("user_note", "TEXT"),
    ("reviewed_at", "TEXT"),
    ("purchased_at", "TEXT"),
    ("updated_at", "TEXT"),
]


def _is_sqlite(dialect: str) -> bool:
    return dialect == "sqlite"


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
    dialect = engine.dialect.name
    is_sqlite = _is_sqlite(dialect)
    columns = COLUMNS_SQLITE if is_sqlite else COLUMNS_PG

    print(f"migrate_listing_status_fields: dialect={dialect}")

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

    print("migrate_listing_status_fields: done")


if __name__ == "__main__":
    run()
