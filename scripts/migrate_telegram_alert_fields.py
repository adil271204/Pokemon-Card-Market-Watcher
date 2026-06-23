"""
Idempotent migration: add telegram_sent and telegram_error columns to alerts table.
Supports PostgreSQL and SQLite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.database import engine


COLUMNS_PG = [
    ("telegram_sent", "BOOLEAN"),
    ("telegram_error", "TEXT"),
]

COLUMNS_SQLITE = [
    ("telegram_sent", "INTEGER"),   # SQLite stores booleans as INTEGER
    ("telegram_error", "TEXT"),
]


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
    is_sqlite = dialect == "sqlite"
    columns = COLUMNS_SQLITE if is_sqlite else COLUMNS_PG

    print(f"migrate_telegram_alert_fields: dialect={dialect}")

    with engine.begin() as conn:
        for col, definition in columns:
            if is_sqlite:
                exists = _column_exists_sqlite(conn, "alerts", col)
            else:
                exists = _column_exists_pg(conn, "alerts", col)

            if not exists:
                conn.execute(text(f"ALTER TABLE alerts ADD COLUMN {col} {definition}"))
                print(f"  added column: {col}")
            else:
                print(f"  already exists: {col}")

    print("migrate_telegram_alert_fields: done")


if __name__ == "__main__":
    run()
