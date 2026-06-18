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
    """Normalised listing returned by EbayClient.search_new_listings."""

    ebay_item_id: str
    title: str
    price: float
    shipping: float
    total_price: float
    currency: str
    url: str                          # itemWebUrl – direct link to eBay article
    image_url: str
    condition: str
    item_creation_date: str
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mock data (development / CI only)
# ---------------------------------------------------------------------------

_MOCK_LISTINGS: list[dict[str, Any]] = [
    {
        "itemId": "mock-001",
        "title": "Umbreon VMAX 215/203 Alternate Art PSA 10 Pokémon Card",
        "price": {"value": "1150.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "12.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-001",
        "image": {"imageUrl": ""},
        "condition": "Used",
        "itemCreationDate": "",
    },
    {
        "itemId": "mock-002",
        "title": "Umbreon VMAX 215/203 ALT Art PROXY Custom Card Reprint",
        "price": {"value": "5.99", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "2.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-002",
        "image": {"imageUrl": ""},
        "condition": "Used",
        "itemCreationDate": "",
    },
    {
        "itemId": "mock-003",
        "title": "Umbreon VMAX 215/203 PSA 9 Graded Pokemon Card",
        "price": {"value": "620.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "10.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-003",
        "image": {"imageUrl": ""},
        "condition": "Used",
        "itemCreationDate": "",
    },
    {
        "itemId": "mock-004",
        "title": "Umbreon VMAX 215/203 PSA 10 Top Preis sofort kaufen",
        "price": {"value": "1800.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "15.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-004",
        "image": {"imageUrl": ""},
        "condition": "Used",
        "itemCreationDate": "",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_listing(raw: dict[str, Any]) -> RawListing:
    """Normalise a raw eBay API item dict into a RawListing."""
    price = float(raw.get("price", {}).get("value") or 0)
    currency = raw.get("price", {}).get("currency", "EUR")

    shipping_options = raw.get("shippingOptions") or []
    if shipping_options:
        shipping_raw = shipping_options[0].get("shippingCost", {}).get("value")
        shipping = float(shipping_raw) if shipping_raw is not None else 0.0
    else:
        shipping = 0.0  # free shipping or unknown; not a scraping assumption

    url = raw.get("itemWebUrl", "")
    image_url = (raw.get("image") or {}).get("imageUrl", "")
    condition = raw.get("condition", "")
    item_creation_date = raw.get("itemCreationDate", "")

    return RawListing(
        ebay_item_id=str(raw["itemId"]),
        title=raw.get("title", ""),
        price=price,
        shipping=shipping,
        total_price=round(price + shipping, 2),
        currency=currency,
        url=url,
        image_url=image_url,
        condition=condition,
        item_creation_date=item_creation_date,
        raw=raw,
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

    _TOKEN_EXPIRY_BUFFER_SEC = 120  # refresh token 2 minutes before expiry

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
        self._token_expires_at: float = 0.0  # unix timestamp

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def search_new_listings(
        self,
        query: str,
        marketplace: str = "EBAY_DE",
        max_price: float | None = None,
        limit: int | None = None,
    ) -> list[RawListing]:
        """
        Search eBay for active listings matching *query*, sorted by newest first.

        Args:
            query:       Search term (from Watchlist.query).
            marketplace: eBay marketplace ID, e.g. "EBAY_DE".
            max_price:   Optional upper price bound (item + shipping).
            limit:       Max results; defaults to config.EBAY_SEARCH_LIMIT.

        Returns:
            List of RawListing, newest first.
        """
        effective_limit = limit if limit is not None else config.EBAY_SEARCH_LIMIT

        if self._use_mock:
            return self._fetch_mock(query, max_price, effective_limit)
        return self._fetch_real(query, marketplace, max_price, effective_limit)

    # ------------------------------------------------------------------
    # OAuth token management
    # ------------------------------------------------------------------

    def _token_is_valid(self) -> bool:
        return (
            self._access_token is not None
            and time.time() < self._token_expires_at - self._TOKEN_EXPIRY_BUFFER_SEC
        )

    def _fetch_token(self) -> None:
        """Fetch a new OAuth2 client-credentials token and cache it."""
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
            # Never log the credentials – only status and (sanitised) body
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

    def _fetch_real(
        self,
        query: str,
        marketplace: str,
        max_price: float | None,
        limit: int,
    ) -> list[RawListing]:
        """Call eBay Browse API. Retries once on 401."""
        return self._do_search(query, marketplace, max_price, limit, retry_on_401=True)

    def _do_search(
        self,
        query: str,
        marketplace: str,
        max_price: float | None,
        limit: int,
        retry_on_401: bool,
    ) -> list[RawListing]:
        token = self._ensure_token()

        params: dict[str, Any] = {
            "q": query,
            "sort": "newlyListed",
            "limit": limit,
            "filter": "buyingOptions:{FIXED_PRICE}",
        }

        # Add price filter via eBay API; fall back to client-side if it errors
        if max_price is not None:
            params["filter"] += f",price:[..{max_price:.2f}],priceCurrency:EUR"

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
            return self._do_search(query, marketplace, max_price, limit, retry_on_401=False)

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

        # Client-side price filter as fallback (in case API filter was ignored)
        if max_price is not None:
            listings = [l for l in listings if l.total_price <= max_price]

        logger.info(
            "eBay search: query=%r marketplace=%s → %d results",
            query,
            marketplace,
            len(listings),
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
    ) -> list[RawListing]:
        logger.debug("Mock eBay search query=%r max_price=%s", query, max_price)
        results: list[RawListing] = []
        for raw in _MOCK_LISTINGS[:limit]:
            listing = _parse_listing(raw)
            if max_price is not None and listing.total_price > max_price:
                continue
            results.append(listing)
        return results
