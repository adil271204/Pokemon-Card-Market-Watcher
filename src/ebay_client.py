"""
eBay Browse API client with OAuth 2.0 Client Credentials flow.

Modes:
- Mock (USE_MOCK_EBAY=true):  returns hard-coded test data, no network calls.
- Production (USE_MOCK_EBAY=false): calls the real eBay Browse API.
  Requires EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to be set.
  Raises EbayConfigError immediately if credentials are missing.

No scraping. No HTML parsing. No BeautifulSoup / Selenium / Playwright.
"""

import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EbayConfigError(RuntimeError):
    """Raised when required eBay credentials are missing in production mode."""


class EbayAPIError(RuntimeError):
    """Raised on non-retryable eBay API errors (4xx/5xx)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"eBay API error {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class EbayRateLimitError(EbayAPIError):
    """Raised on HTTP 429 Too Many Requests."""


# ---------------------------------------------------------------------------
# Normalised listing dataclass
# ---------------------------------------------------------------------------


@dataclass
class RawListing:
    """Normalised listing returned by EbayClient."""

    ebay_item_id: str
    title: str
    price: float
    shipping: float
    total_price: float
    currency: str
    url: str                    # itemWebUrl – direct link to eBay article
    item_web_url: str           # same as url, explicit alias
    image_url: str
    condition: str
    item_creation_date: str     # raw string from API
    item_origin_date: str       # raw string from API (preferred)
    listing_date: datetime | None  # parsed datetime: item_origin_date or item_creation_date
    location_country: str | None   # from itemLocation.country (normalised uppercase, UK→GB)
    location_city: str | None
    location_postal_code: str | None
    location_state: str | None
    location_raw: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    # Buying / auction fields
    buying_options: list[str] = field(default_factory=list)
    listing_type: str = "UNKNOWN"          # "FIXED_PRICE" | "AUCTION" | "UNKNOWN"
    best_offer_available: bool = False
    current_bid_price: float | None = None
    bid_count: int | None = None
    item_end_date: datetime | None = None
    display_price: float = 0.0             # bid price for auctions, price for fixed


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

def _days_ago_iso(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


_MOCK_LISTINGS: list[dict[str, Any]] = [
    {
        "itemId": "mock-001",
        "title": "Umbreon VMAX 215/203 Alternate Art PSA 10 Pokémon Card",
        "price": {"value": "1150.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "12.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-001",
        "image": {"imageUrl": "https://i.ebayimg.com/images/mock-001.jpg"},
        "condition": "Used",
        "itemCreationDate": _days_ago_iso(0),
        "itemOriginDate": _days_ago_iso(0),
        "itemLocation": {"country": "DE", "city": "Berlin", "postalCode": "10115"},
    },
    {
        "itemId": "mock-002",
        "title": "Umbreon VMAX 215/203 PSA 9 Graded Pokemon Card",
        "price": {"value": "620.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "10.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-002",
        "image": {"imageUrl": "https://i.ebayimg.com/images/mock-002.jpg"},
        "condition": "Used",
        "itemCreationDate": _days_ago_iso(3),
        "itemOriginDate": _days_ago_iso(3),
        "itemLocation": {"country": "FR", "city": "Paris", "postalCode": "75001"},
    },
    {
        "itemId": "mock-003",
        "title": "Umbreon VMAX 215/203 PSA 10 Top Preis sofort kaufen",
        "price": {"value": "1800.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "15.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-003",
        "image": {"imageUrl": "https://i.ebayimg.com/images/mock-003.jpg"},
        "condition": "Used",
        "itemCreationDate": _days_ago_iso(10),
        "itemOriginDate": _days_ago_iso(10),
        "itemLocation": {"country": "GB", "city": "London", "postalCode": "EC1A"},  # UK – wird gefiltert
    },
    {
        "itemId": "mock-004",
        "title": "Umbreon VMAX 215/203 ALT Art Englisch NM Ungraded",
        "price": {"value": "800.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "8.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-004",
        "image": {"imageUrl": "https://i.ebayimg.com/images/mock-004.jpg"},
        "condition": "Like New",
        "itemCreationDate": _days_ago_iso(20),  # vor 20 Tagen – bei lookback=14 rausgefiltert
        "itemOriginDate": _days_ago_iso(20),
        "itemLocation": {"country": "DE", "city": "München", "postalCode": "80331"},
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse ISO date string from eBay API into UTC datetime."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.debug("Could not parse eBay date string: %r", date_str)
    return None


def _parse_listing(raw: dict[str, Any]) -> RawListing:
    """Normalise a raw eBay API item dict into a RawListing."""
    price = float(raw.get("price", {}).get("value") or 0)
    currency = raw.get("price", {}).get("currency", "EUR")

    shipping_options = raw.get("shippingOptions") or []
    if shipping_options:
        shipping_raw = shipping_options[0].get("shippingCost", {}).get("value")
        shipping = float(shipping_raw) if shipping_raw is not None else 0.0
    else:
        shipping = 0.0

    url = raw.get("itemWebUrl", "")
    image_url = (raw.get("image") or {}).get("imageUrl", "")
    condition = raw.get("condition", "")
    item_creation_date = raw.get("itemCreationDate", "")
    item_origin_date = raw.get("itemOriginDate", "")

    # Prefer itemOriginDate (when listing first went live), fall back to itemCreationDate
    listing_date_str = item_origin_date or item_creation_date
    listing_date = _parse_date(listing_date_str)

    if listing_date_str and listing_date is None:
        logger.warning(
            "listing_date_unknown=true for item %s – could not parse %r",
            raw.get("itemId"),
            listing_date_str,
        )

    # Location from itemLocation (official Browse API field)
    item_location: dict[str, Any] = raw.get("itemLocation") or {}
    raw_country = item_location.get("country") or ""
    norm_country = raw_country.strip().upper()
    if norm_country == "UK":
        norm_country = "GB"
    location_country = norm_country or None

    # Buying options and listing type
    buying_options: list[str] = raw.get("buyingOptions") or []
    if isinstance(buying_options, str):
        buying_options = [buying_options]
    if "AUCTION" in buying_options:
        listing_type = "AUCTION"
    elif "FIXED_PRICE" in buying_options:
        listing_type = "FIXED_PRICE"
    else:
        listing_type = "UNKNOWN"
    best_offer_available = "BEST_OFFER" in buying_options

    current_bid_price: float | None = None
    bid_count: int | None = None
    item_end_date: datetime | None = None
    if listing_type == "AUCTION":
        bid_raw = raw.get("currentBidPrice") or {}
        if bid_raw.get("value"):
            current_bid_price = float(bid_raw["value"])
        if raw.get("bidCount") is not None:
            bid_count = int(raw["bidCount"])
        end_str = raw.get("itemEndDate") or ""
        if end_str:
            item_end_date = _parse_date(end_str)

    display_price = current_bid_price if current_bid_price is not None else price

    return RawListing(
        ebay_item_id=str(raw["itemId"]),
        title=raw.get("title", ""),
        price=price,
        shipping=shipping,
        total_price=round(price + shipping, 2),
        currency=currency,
        url=url,
        item_web_url=url,
        image_url=image_url,
        condition=condition,
        item_creation_date=item_creation_date,
        item_origin_date=item_origin_date,
        listing_date=listing_date,
        location_country=location_country,
        location_city=item_location.get("city") or None,
        location_postal_code=item_location.get("postalCode") or None,
        location_state=item_location.get("stateOrProvince") or None,
        location_raw=item_location,
        raw=raw,
        buying_options=buying_options,
        listing_type=listing_type,
        best_offer_available=best_offer_available,
        current_bid_price=current_bid_price,
        bid_count=bid_count,
        item_end_date=item_end_date,
        display_price=display_price,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class EbayClient:
    """
    Official eBay Browse API client.

    In mock mode (USE_MOCK_EBAY=true) no network calls are made.
    In production mode (USE_MOCK_EBAY=false) the real Browse API is used.
    Raises EbayConfigError at init time if credentials are missing in
    production mode so failures are loud and early.
    """

    _TOKEN_EXPIRY_BUFFER_SEC = 120

    def __init__(self) -> None:
        self._use_mock = config.USE_MOCK_EBAY

        if self._use_mock:
            logger.info("EbayClient: MOCK mode active (USE_MOCK_EBAY=true)")
        else:
            if not config.EBAY_KEYS_SET:
                raise EbayConfigError(
                    "USE_MOCK_EBAY=false but EBAY_CLIENT_ID or EBAY_CLIENT_SECRET "
                    "are not set. Set both environment variables or set "
                    "USE_MOCK_EBAY=true for local development."
                )
            logger.info(
                "EbayClient: production mode, marketplace=%s, env=%s",
                config.EBAY_MARKETPLACE,
                config.EBAY_ENV,
            )

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public: single-page search (existing cron watcher)
    # ------------------------------------------------------------------

    def get_item_by_id(
        self,
        item_id: str,
        marketplace: str = "EBAY_DE",
    ) -> RawListing | None:
        """
        Fetch a single listing by its eBay item ID via the Browse API.
        Uses GET /buy/browse/v1/item/{item_id}.

        Tries bare numeric ID first, then v1|{item_id}|0 format.
        Returns None on 404 or if mock mode is active (item not in mock data).
        """
        if self._use_mock:
            for raw in _MOCK_LISTINGS:
                if raw["itemId"] == item_id:
                    return _parse_listing(raw)
            return None

        for candidate in self._item_id_candidates(item_id):
            result = self._fetch_item(candidate, marketplace)
            if result is not None:
                return result
        return None

    def _item_id_candidates(self, item_id: str) -> list[str]:
        """Return the ID forms to try, in order."""
        if item_id.startswith("v1|"):
            return [item_id]
        return [item_id, f"v1|{item_id}|0"]

    def _fetch_item(self, item_id: str, marketplace: str) -> RawListing | None:
        token = self._ensure_token()
        try:
            resp = requests.get(
                f"{config.EBAY_API_BASE_URL}/buy/browse/v1/item/{item_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": marketplace,
                    "Accept": "application/json",
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise EbayAPIError(0, f"Network error: {exc}") from exc

        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            # Refresh and retry once
            self._access_token = None
            self._token_expires_at = 0.0
            token = self._ensure_token()
            resp = requests.get(
                f"{config.EBAY_API_BASE_URL}/buy/browse/v1/item/{item_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": marketplace,
                    "Accept": "application/json",
                },
                timeout=15,
            )
            if resp.status_code == 404:
                return None
        if resp.status_code >= 400:
            raise EbayAPIError(resp.status_code, resp.text)

        return _parse_listing(resp.json())

    def search_new_listings(
        self,
        query: str,
        marketplace: str = "EBAY_DE",
        max_price: float | None = None,
        limit: int | None = None,
    ) -> list[RawListing]:
        """Fetch one page of the newest listings (used by the regular cron watcher)."""
        effective_limit = limit if limit is not None else config.EBAY_SEARCH_LIMIT

        if self._use_mock:
            return self._fetch_mock(query, max_price, effective_limit, lookback_days=None)
        return self._fetch_real_page(
            query, marketplace, max_price, effective_limit, offset=0
        )

    # ------------------------------------------------------------------
    # Public: paginated backfill (last N days)
    # ------------------------------------------------------------------

    def search_recent_listings(
        self,
        query: str,
        marketplace: str = "EBAY_DE",
        max_price: float | None = None,
        limit: int = 50,
        lookback_days: int | None = None,
        max_pages: int | None = None,
        include_auctions: bool = False,
    ) -> list[RawListing]:
        """
        Fetch listings across multiple pages, filtered to the last *lookback_days* days.

        Stops early when:
        - No more results from API
        - max_pages reached
        - All listings on a page are older than lookback_days (when dates are available)

        Deduplicates by ebay_item_id.
        """
        effective_lookback = lookback_days if lookback_days is not None else config.EBAY_LOOKBACK_DAYS
        effective_max_pages = max_pages if max_pages is not None else config.EBAY_MAX_PAGES
        cutoff = datetime.now(timezone.utc) - timedelta(days=effective_lookback)

        if self._use_mock:
            return self._fetch_mock(query, max_price, limit, lookback_days=effective_lookback)

        all_listings: dict[str, RawListing] = {}

        for page in range(effective_max_pages):
            offset = page * limit
            page_listings = self._fetch_real_page(query, marketplace, max_price, limit, offset, include_auctions=include_auctions)

            if not page_listings:
                logger.info("Backfill: no more results at offset=%d, stopping.", offset)
                break

            page_has_recent = False
            for listing in page_listings:
                if listing.ebay_item_id in all_listings:
                    continue

                if listing.listing_date is not None:
                    if listing.listing_date >= cutoff:
                        page_has_recent = True
                        all_listings[listing.ebay_item_id] = listing
                    # else: too old, skip
                else:
                    # No date info – include it (logged in _parse_listing)
                    page_has_recent = True
                    all_listings[listing.ebay_item_id] = listing

            if not page_has_recent and page > 0:
                logger.info(
                    "Backfill: all listings on page %d are older than %d days, stopping.",
                    page + 1,
                    effective_lookback,
                )
                break

        result = list(all_listings.values())
        logger.info(
            "Backfill search_recent_listings: query=%r → %d listings within %d days",
            query,
            len(result),
            effective_lookback,
        )
        return result

    # ------------------------------------------------------------------
    # OAuth token management
    # ------------------------------------------------------------------

    def _token_is_valid(self) -> bool:
        return (
            self._access_token is not None
            and time.time() < self._token_expires_at - self._TOKEN_EXPIRY_BUFFER_SEC
        )

    def _fetch_token(self) -> None:
        credentials = base64.b64encode(
            f"{config.EBAY_CLIENT_ID}:{config.EBAY_CLIENT_SECRET}".encode()
        ).decode()

        try:
            resp = requests.post(
                config.EBAY_TOKEN_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope": "https://api.ebay.com/oauth/api_scope",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise EbayAPIError(0, f"Token request failed: {exc}") from exc

        if resp.status_code != 200:
            raise EbayAPIError(resp.status_code, resp.text)

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 7200))
        self._token_expires_at = time.time() + expires_in
        logger.debug("eBay OAuth token refreshed, expires in %ds", expires_in)

    def _ensure_token(self) -> str:
        if not self._token_is_valid():
            self._fetch_token()
        assert self._access_token is not None
        return self._access_token

    # ------------------------------------------------------------------
    # Real eBay Browse API
    # ------------------------------------------------------------------

    def _fetch_real_page(
        self,
        query: str,
        marketplace: str,
        max_price: float | None,
        limit: int,
        offset: int,
        retry_on_401: bool = True,
        include_auctions: bool = False,
    ) -> list[RawListing]:
        token = self._ensure_token()

        buying_filter = "" if include_auctions else "buyingOptions:{FIXED_PRICE}"
        params: dict[str, Any] = {
            "q": query,
            "sort": "newlyListed",
            "limit": limit,
            "offset": offset,
        }
        if buying_filter:
            params["filter"] = buying_filter

        if max_price is not None:
            price_filter = f"price:[..{max_price:.2f}],priceCurrency:EUR"
            params["filter"] = f"{params.get('filter', '')},{price_filter}".strip(",")

        try:
            resp = requests.get(
                f"{config.EBAY_API_BASE_URL}/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": marketplace,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                params=params,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise EbayAPIError(0, f"Network error: {exc}") from exc

        if resp.status_code == 401 and retry_on_401:
            logger.warning("eBay returned 401 – refreshing token and retrying once")
            self._access_token = None
            self._token_expires_at = 0.0
            return self._fetch_real_page(query, marketplace, max_price, limit, offset, retry_on_401=False, include_auctions=include_auctions)

        if resp.status_code == 429:
            logger.error(
                "eBay rate limit hit (429). Retry-After: %s",
                resp.headers.get("Retry-After", "unknown"),
            )
            raise EbayRateLimitError(429, resp.text)

        if resp.status_code >= 400:
            raise EbayAPIError(resp.status_code, resp.text)

        items: list[dict[str, Any]] = resp.json().get("itemSummaries", [])
        listings = [_parse_listing(item) for item in items]

        # Client-side price filter fallback
        if max_price is not None:
            listings = [l for l in listings if l.total_price <= max_price]

        logger.info(
            "eBay page: query=%r marketplace=%s offset=%d → %d results",
            query, marketplace, offset, len(listings),
        )
        return listings

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _fetch_mock(
        self,
        query: str,
        max_price: float | None,
        limit: int,
        lookback_days: int | None,
    ) -> list[RawListing]:
        logger.debug("Mock eBay search query=%r max_price=%s lookback_days=%s", query, max_price, lookback_days)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
            if lookback_days is not None
            else None
        )
        results: list[RawListing] = []
        for raw in _MOCK_LISTINGS[:limit]:
            listing = _parse_listing(raw)
            if max_price is not None and listing.total_price > max_price:
                continue
            if cutoff is not None and listing.listing_date is not None:
                if listing.listing_date < cutoff:
                    logger.debug(
                        "Mock: skipping %s – listing_date %s older than cutoff %s",
                        listing.ebay_item_id, listing.listing_date, cutoff,
                    )
                    continue
            results.append(listing)
        return results
