"""
Backfill listing_type + buying_options_json for older SeenListing rows
that were saved before the listing_type field existed.

Reads raw_payload_json to extract buyingOptions and derives listing_type.
Safe to run multiple times – skips rows that already have listing_type set.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database import get_session
from src.models import SeenListing


def _derive_type(buying_options: list[str]) -> str:
    if "AUCTION" in buying_options:
        return "AUCTION"
    if "FIXED_PRICE" in buying_options:
        return "FIXED_PRICE"
    return "UNKNOWN"


def run() -> None:
    updated = 0
    skipped = 0
    errors = 0

    with get_session() as db:
        rows = db.query(SeenListing).filter(SeenListing.listing_type.is_(None)).all()
        print(f"Rows without listing_type: {len(rows)}")

        for row in rows:
            try:
                buying_options: list[str] = []
                if row.buying_options_json:
                    buying_options = json.loads(row.buying_options_json)
                elif row.raw_payload_json:
                    raw = json.loads(row.raw_payload_json)
                    buying_options = raw.get("buyingOptions") or []

                if not isinstance(buying_options, list):
                    buying_options = []

                row.listing_type = _derive_type(buying_options)
                if not row.buying_options_json and buying_options:
                    row.buying_options_json = json.dumps(buying_options)
                updated += 1
            except Exception as exc:
                print(f"  error for id={row.id}: {exc}")
                errors += 1

        db.commit()

    print(f"Backfill done: updated={updated} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    run()
