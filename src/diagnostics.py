"""
Listing diagnostics – helps identify why a specific eBay listing is missing
from the dashboard.

No scraping. Uses only the official eBay Browse API.
"""

from __future__ import annotations

import re
from typing import Any

from src import config
from src.deal_scorer import calculate_deal_score
from src.ebay_client import RawListing
from src.listing_cleaner import clean_and_classify_listing
from src.location_filter import is_allowed_location
from src.models import SeenListing, Watchlist


# ---------------------------------------------------------------------------
# Item ID extraction
# ---------------------------------------------------------------------------

def extract_ebay_item_id(raw: str) -> str:
    """
    Extract a plain numeric (or Browse-API v1|…) item ID from any input.

    Handles:
    - bare numeric IDs: "123456789012"
    - eBay URLs:  https://www.ebay.de/itm/123456789012
                  https://www.ebay.de/itm/Some-Title/123456789012
                  https://www.ebay.de/itm/123456789012?...
    - v1 IDs: "v1|123456789012|0" → returned as-is
    """
    raw = raw.strip()

    # Already a v1 ID
    if raw.startswith("v1|"):
        return raw

    # URL: extract last numeric path segment or ?item= param
    if "ebay." in raw:
        # ?item= or &item=
        m = re.search(r"[?&]item=(\d+)", raw)
        if m:
            return m.group(1)
        # /itm/<digits>
        m = re.search(r"/itm/(?:[^/]+/)?(\d{10,})", raw)
        if m:
            return m.group(1)
        # last all-digit segment
        m = re.search(r"/(\d{10,})(?:[/?#]|$)", raw)
        if m:
            return m.group(1)

    # Pure numeric
    if re.fullmatch(r"\d+", raw):
        return raw

    # Best effort: return stripped input and let the API reject it
    return raw


# ---------------------------------------------------------------------------
# Filter diagnosis
# ---------------------------------------------------------------------------

def diagnose_listing_against_watchlist(
    listing: RawListing,
    watchlist: Watchlist,
    db_listing: SeenListing | None = None,
) -> dict[str, Any]:
    """
    Run every filter against *listing* for *watchlist* and return a detailed
    report so the user can see exactly which step failed.
    """
    checks: list[dict[str, Any]] = []

    # ---- A) Query match (keyword presence in title) ----
    query_words = [w.lower() for w in watchlist.query.split() if len(w) > 2]
    title_lower = listing.title.lower()
    matched_words = [w for w in query_words if w in title_lower]
    missing_words = [w for w in query_words if w not in title_lower]
    query_passed = len(missing_words) == 0
    checks.append({
        "name": "query_match",
        "label": "Query-Übereinstimmung",
        "passed": query_passed,
        "reason": (
            f"Alle Keywords gefunden: {matched_words}"
            if query_passed
            else f"Fehlende Keywords im Titel: {missing_words} (Watchlist-Query: \"{watchlist.query}\")"
        ),
    })

    # ---- B) Price filter ----
    price_passed = True
    price_reason = f"total_price={listing.total_price} € – kein max_price gesetzt"
    if watchlist.max_price is not None:
        price_passed = listing.total_price <= watchlist.max_price
        price_reason = (
            f"total_price={listing.total_price} € ≤ max_price={watchlist.max_price} €"
            if price_passed
            else f"total_price={listing.total_price} € > max_price={watchlist.max_price} € → ausgeschlossen"
        )
    checks.append({
        "name": "price_filter",
        "label": "Preisfilter",
        "passed": price_passed,
        "reason": price_reason,
    })

    # ---- C) Location / Country filter ----
    loc_passed, loc_reasons = is_allowed_location(
        listing.location_country,
        config.EBAY_ALLOWED_COUNTRIES,
        config.EBAY_EXCLUDED_COUNTRIES,
        config.EBAY_ALLOW_UNKNOWN_LOCATION,
    )
    checks.append({
        "name": "country_filter",
        "label": "Länderfilter",
        "passed": loc_passed,
        "reason": (
            f"Land={listing.location_country or 'unbekannt'} ist erlaubt"
            if loc_passed
            else f"Land={listing.location_country or 'unbekannt'} – {', '.join(loc_reasons)}"
        ),
    })

    # ---- D) Buying option ----
    buying_options = listing.raw.get("buyingOptions") or []
    buying_passed = "FIXED_PRICE" in buying_options or not buying_options
    checks.append({
        "name": "buying_option",
        "label": "Kaufoption",
        "passed": buying_passed,
        "reason": (
            f"buyingOptions={buying_options} – FIXED_PRICE vorhanden"
            if buying_passed
            else f"buyingOptions={buying_options} – kein FIXED_PRICE → wäre ausgeschlossen"
        ),
    })

    # ---- E) Keyword cleaner ----
    cl = clean_and_classify_listing(listing.title, target_grade=watchlist.target_grade)
    cleaner_passed = not cl.is_bad_match
    checks.append({
        "name": "keyword_cleaner",
        "label": "Keyword-Filter (Proxy/Reprint etc.)",
        "passed": cleaner_passed,
        "reason": (
            "Kein schlechtes Keyword gefunden"
            if cleaner_passed
            else f"Schlechtes Keyword erkannt: {', '.join(cl.reasons)}"
        ),
    })

    # ---- F) Grade filter ----
    grade_passed = True
    grade_reason = "Kein Ziel-Grade gesetzt"
    if watchlist.target_grade:
        if cl.is_graded:
            detected = f"{cl.grading_company} {cl.grade}"
            grade_passed = watchlist.target_grade.lower() in detected.lower()
            grade_reason = (
                f"Erkannter Grade: {detected} – passt zu Ziel-Grade \"{watchlist.target_grade}\""
                if grade_passed
                else f"Erkannter Grade: {detected} – passt NICHT zu Ziel-Grade \"{watchlist.target_grade}\""
            )
        else:
            grade_passed = False
            grade_reason = f"Kein Grade im Titel erkannt – Ziel-Grade ist \"{watchlist.target_grade}\""
    checks.append({
        "name": "grade_filter",
        "label": "Grade-Filter",
        "passed": grade_passed,
        "reason": grade_reason,
    })

    # ---- G) Soft delete ----
    deleted = db_listing is not None and db_listing.deleted_at is not None
    checks.append({
        "name": "soft_delete",
        "label": "Soft Delete (ausgeblendet)",
        "passed": not deleted,
        "reason": (
            "Listing ist sichtbar (nicht ausgeblendet)"
            if not deleted
            else f"Listing wurde ausgeblendet am {db_listing.deleted_at.strftime('%d.%m.%Y %H:%M') if db_listing and db_listing.deleted_at else '–'}"
        ),
    })

    # ---- Deal score (informational) ----
    deal = calculate_deal_score(
        listing=listing,
        target_market_price=watchlist.target_market_price,
        min_discount_percent=watchlist.min_discount_percent,
        classification=cl,
        target_grade=watchlist.target_grade,
    )

    all_passed = all(c["passed"] for c in checks)
    first_fail = next((c for c in checks if not c["passed"]), None)

    summary = _build_summary(checks, db_listing)

    return {
        "matches": all_passed,
        "checks": checks,
        "first_failure": first_fail,
        "summary": summary,
        "classification": cl,
        "deal": deal,
    }


def _build_summary(checks: list[dict], db_listing: SeenListing | None) -> str:
    failed = [c for c in checks if not c["passed"]]
    if not failed:
        if db_listing and db_listing.deleted_at is None:
            return "Listing ist gespeichert und sichtbar."
        if db_listing is None:
            return (
                "Listing würde alle Filter bestehen. Es wurde aber noch nicht gespeichert – "
                "möglicherweise war die Paginierung zu kurz oder die Query hat es nicht gefunden."
            )
        return "Alle Filter bestanden."

    reasons = [c["name"] for c in failed]

    if "soft_delete" in reasons:
        return "Listing ist ausgeblendet (soft-deleted). Über die Wiederherstellungs-Funktion reaktivierbar."
    if "country_filter" in reasons:
        return (
            "Listing ist über die eBay API abrufbar, wurde aber wegen des Länderfilters ausgeschlossen. "
            f"Land: {failed[0]['reason'] if failed else '–'}"
        )
    if "price_filter" in reasons:
        return "Listing wurde wegen max_price der Watchlist ausgeschlossen."
    if "keyword_cleaner" in reasons:
        return "Listing wurde wegen Proxy/Reprint/Custom-Keyword ausgeschlossen."
    if "grade_filter" in reasons:
        return "Listing passt nicht zum Ziel-Grade der Watchlist."
    if "buying_option" in reasons:
        return "Listing hat keine FIXED_PRICE Kaufoption."
    if "query_match" in reasons:
        return (
            "Listing ist über Item Lookup abrufbar, erscheint aber möglicherweise nicht "
            "in der Search API für diese Query. "
            "Alternativ: EBAY_MAX_PAGES erhöhen oder Query anpassen."
        )
    return f"Ausgeschlossen durch: {', '.join(reasons)}"
