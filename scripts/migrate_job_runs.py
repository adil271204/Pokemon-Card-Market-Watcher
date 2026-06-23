"""
Idempotent migration: create job_runs table if it does not exist.
Supports PostgreSQL and SQLite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from src.database import engine


_CREATE_PG = """
CREATE TABLE IF NOT EXISTS job_runs (
    id                        SERIAL PRIMARY KEY,
    job_type                  VARCHAR(50)  NOT NULL,
    status                    VARCHAR(20)  NOT NULL DEFAULT 'running',
    started_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    finished_at               TIMESTAMP WITH TIME ZONE,
    duration_seconds          DOUBLE PRECISION,
    watchlists_checked        INTEGER NOT NULL DEFAULT 0,
    queries_executed          INTEGER NOT NULL DEFAULT 0,
    api_results_count         INTEGER NOT NULL DEFAULT 0,
    listings_saved            INTEGER NOT NULL DEFAULT 0,
    listings_updated          INTEGER NOT NULL DEFAULT 0,
    listings_skipped_existing INTEGER NOT NULL DEFAULT 0,
    listings_filtered_country INTEGER NOT NULL DEFAULT 0,
    listings_filtered_keywords INTEGER NOT NULL DEFAULT 0,
    listings_filtered_price   INTEGER NOT NULL DEFAULT 0,
    listings_filtered_deleted INTEGER NOT NULL DEFAULT 0,
    alerts_sent               INTEGER NOT NULL DEFAULT 0,
    errors_count              INTEGER NOT NULL DEFAULT 0,
    error_message             TEXT,
    metadata_json             TEXT,
    created_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
)
"""

_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS job_runs (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type                  TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'running',
    started_at                TEXT NOT NULL,
    finished_at               TEXT,
    duration_seconds          REAL,
    watchlists_checked        INTEGER NOT NULL DEFAULT 0,
    queries_executed          INTEGER NOT NULL DEFAULT 0,
    api_results_count         INTEGER NOT NULL DEFAULT 0,
    listings_saved            INTEGER NOT NULL DEFAULT 0,
    listings_updated          INTEGER NOT NULL DEFAULT 0,
    listings_skipped_existing INTEGER NOT NULL DEFAULT 0,
    listings_filtered_country INTEGER NOT NULL DEFAULT 0,
    listings_filtered_keywords INTEGER NOT NULL DEFAULT 0,
    listings_filtered_price   INTEGER NOT NULL DEFAULT 0,
    listings_filtered_deleted INTEGER NOT NULL DEFAULT 0,
    alerts_sent               INTEGER NOT NULL DEFAULT 0,
    errors_count              INTEGER NOT NULL DEFAULT 0,
    error_message             TEXT,
    metadata_json             TEXT,
    created_at                TEXT NOT NULL
)
"""


def _table_exists_pg(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": table},
    ).fetchone()
    return row is not None


def _table_exists_sqlite(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return row is not None


def run() -> None:
    dialect = engine.dialect.name
    is_sqlite = dialect == "sqlite"

    print(f"migrate_job_runs: dialect={dialect}")

    with engine.begin() as conn:
        if is_sqlite:
            exists = _table_exists_sqlite(conn, "job_runs")
        else:
            exists = _table_exists_pg(conn, "job_runs")

        if not exists:
            sql = _CREATE_SQLITE if is_sqlite else _CREATE_PG
            conn.execute(text(sql))
            print("  created table: job_runs")
        else:
            print("  already exists: job_runs")

    print("migrate_job_runs: done")


if __name__ == "__main__":
    run()
