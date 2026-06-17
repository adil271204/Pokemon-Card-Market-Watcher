"""eBay client with a mock mode for development and a stub for the real Browse API."""

import logging
from dataclasses import dataclass
from typing import Any

import requests

from src import config

logger = logging.getLogger(__name__)


@dataclass
class RawListing:
    """Normalised listing as returned by EbayClient.search_new_listings."""

    ebay_item_id: str
    title: str
    price: float
    shipping: float
    total_price: float
    currency: str
    url: str
    raw: dict[str, Any]


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_MOCK_LISTINGS: list[dict[str, Any]] = [
    {
        "itemId": "mock-001",
        "title": "Umbreon VMAX 215/203 Alternate Art PSA 10 Pokémon Card",
        "price": {"value": "1150.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "12.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-001",
    },
    {
        "itemId": "mock-002",
        "title": "Umbreon VMAX 215/203 ALT Art PROXY Custom Card Reprint",
        "price": {"value": "5.99", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "2.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-002",
    },
    {
        "itemId": "mock-003",
        "title": "Umbreon VMAX 215/203 PSA 9 Graded Pokemon Card",
        "price": {"value": "620.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "10.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-003",
    },
    {
        "itemId": "mock-004",
        "title": "Umbreon VMAX 215/203 PSA 10 Top Preis sofort kaufen",
        "price": {"value": "1800.00", "currency": "EUR"},
        "shippingOptions": [{"shippingCost": {"value": "15.00", "currency": "EUR"}}],
        "itemWebUrl": "https://www.ebay.de/itm/mock-004",
    },
]


def _parse_listing(raw: dict[str, Any]) -> RawListing:
    price = float(raw.get("price", {}).get("value", 0))
    shipping_options = raw.get("shippingOptions", [])
    shipping = float(
        shipping_options[0].get("shippingCost", {}).get("value", 0)
        if shipping_options
        else 0
    )
    currency = raw.get("price", {}).get("currency", "EUR")
    return RawListing(
        ebay_item_id=str(raw["itemId"]),
        title=raw.get("title", ""),
        price=price,
        shipping=shipping,
        total_price=round(price + shipping, 2),
        currency=currency,
        url=raw.get("itemWebUrl", ""),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class EbayClient:
    """
    Thin wrapper around the eBay Browse API.

    When USE_MOCK_EBAY is true (or API keys are missing) the client returns
    hard-coded mock listings so the full pipeline can be tested without
    eBay credentials.

    Real API integration: fill in _fetch_real() once you have
    EBAY_CLIENT_ID / EBAY_CLIENT_SECRET from the eBay Developer Portal.
    """

    def __init__(self) -> None:
        self._use_mock = config.USE_MOCK_EBAY or not (
            config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET
        )
        if self._use_mock:
            logger.info("EbayClient: running in MOCK mode")
        else:
            logger.info("EbayClient: running against real eBay API")
        self._access_token: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search_new_listings(
        self,
        query: str,
        marketplace: str,
        max_price: float | None = None,
        limit: int = 25,
    ) -> list[RawListing]:
        """
        Search eBay for active listings matching *query*.

        Returns a list of RawListing objects, newest first.
        Falls back to mock data if USE_MOCK_EBAY is true.
        """
        if self._use_mock:
            return self._fetch_mock(query, max_price, limit)
        return self._fetch_real(query, marketplace, max_price, limit)

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    def _fetch_mock(
        self,
        query: str,
        max_price: float | None,
        limit: int,
    ) -> list[RawListing]:
        logger.debug("Mock eBay search for query=%r max_price=%s", query, max_price)
        results: list[RawListing] = []
        for raw in _MOCK_LISTINGS[:limit]:
            listing = _parse_listing(raw)
            if max_price is not None and listing.total_price > max_price:
                continue
            results.append(listing)
        return results

    # ------------------------------------------------------------------
    # Real eBay Browse API stub
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        """
        Fetch an OAuth2 client-credentials token from eBay.

        Docs: https://developer.ebay.com/api-docs/static/oauth-client-credentials-grant.html
        """
        import base64

        credentials = base64.b64encode(
            f"{config.EBAY_CLIENT_ID}:{config.EBAY_CLIENT_SECRET}".encode()
        ).decode()

        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
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
        resp.raise_for_status()
        token: str = resp.json()["access_token"]
        return token

    def _fetch_real(
        self,
        query: str,
        marketplace: str,
        max_price: float | None,
        limit: int,
    ) -> list[RawListing]:
        """
        Call the eBay Browse API – search/item_summary/search.

        Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
        """
        if not self._access_token:
            self._access_token = self._get_access_token()

        params: dict[str, Any] = {
            "q": query,
            "sort": "newlyListed",
            "limit": limit,
        }
        if max_price is not None:
            params["filter"] = f"price:[..{max_price}],priceCurrency:EUR"

        resp = requests.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "X-EBAY-C-ENDUSERCTX": "affiliateCampaignId=<your_campaign_id>",
            },
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        items: list[dict[str, Any]] = resp.json().get("itemSummaries", [])
        return [_parse_listing(item) for item in items]
