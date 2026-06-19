"""Idempotent migration: add cardlist-import fields to pokemon_sets and pokemon_cards."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from src.database import engine

print("Running migrate_cardlist_import_fields.py …")


def _column_exists(conn, table: str, column: str) -> bool:
    dialect = engine.dialect.name
    if dialect == "postgresql":
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
    else:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        row = next((r for r in rows if r[1] == column), None)
    return row is not None


with engine.connect() as conn:
    # pokemon_sets: source_url, source_name
    for col, definition in [
        ("source_url", "TEXT"),
        ("source_name", "VARCHAR(100)"),
    ]:
        if not _column_exists(conn, "pokemon_sets", col):
            conn.execute(text(f"ALTER TABLE pokemon_sets ADD COLUMN {col} {definition}"))
            print(f"  Added pokemon_sets.{col}")
        else:
            print(f"  pokemon_sets.{col} already exists – skip")

    # pokemon_cards: source_raw_text, import_confidence
    for col, definition in [
        ("source_raw_text", "TEXT"),
        ("import_confidence", "FLOAT"),
    ]:
        if not _column_exists(conn, "pokemon_cards", col):
            conn.execute(text(f"ALTER TABLE pokemon_cards ADD COLUMN {col} {definition}"))
            print(f"  Added pokemon_cards.{col}")
        else:
            print(f"  pokemon_cards.{col} already exists – skip")

    conn.commit()

print("Done – cardlist import fields ready.")
