"""
Location-based listing filter.

Uses itemLocation.country from the official eBay Browse API response.
No scraping. No HTML parsing.

By default:
- Only EU countries are allowed (see config.EBAY_ALLOWED_COUNTRIES)
- UK/GB, US, CN, JP, CA, AU are excluded (config.EBAY_EXCLUDED_COUNTRIES)
- Listings without a country are rejected (config.EBAY_ALLOW_UNKNOWN_LOCATION=false)
"""

from __future__ import annotations


def normalize_country(country: str | None) -> str | None:
    """Uppercase + map UK → GB."""
    if not country:
        return None
    c = country.strip().upper()
    return "GB" if c == "UK" else c


def is_allowed_location(
    location_country: str | None,
    allowed_countries: set[str],
    excluded_countries: set[str],
    allow_unknown_location: bool = False,
) -> tuple[bool, list[str]]:
    """
    Returns (allowed, reasons).

    reasons is non-empty only when not allowed.
    """
    country = normalize_country(location_country)

    if not country:
        if allow_unknown_location:
            return True, []
        return False, ["unknown_location"]

    if country in excluded_countries:
        return False, [f"excluded_country:{country}"]

    if allowed_countries and country not in allowed_countries:
        return False, [f"not_allowed_country:{country}"]

    return True, []
