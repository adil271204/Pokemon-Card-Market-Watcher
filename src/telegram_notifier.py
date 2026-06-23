"""Send deal alerts via Telegram Bot API."""

import html
import logging

import requests

from src import config

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _escape(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return html.escape(str(text), quote=False)


def _format_fixed_price_alert(
    title: str,
    location_country: str | None,
    price: float,
    shipping: float | None,
    total_price: float,
    currency: str,
    watchlist_name: str,
    score: float,
    discount_percent: float,
    url: str,
) -> str:
    sym = "€" if currency == "EUR" else currency
    shipping_str = f"{sym}{shipping:.2f}" if shipping else "kostenlos"

    lines = [
        "🔥 <b>Neues interessantes Listing</b>",
        "",
        f"<b>{_escape(title)}</b>",
        "",
        "Typ: Sofortkauf",
        f"Land: {_escape(location_country or '–')}",
        f"Preis: {sym}{price:.2f}",
        f"Versand: {shipping_str}",
        f"<b>Gesamt: {sym}{total_price:.2f}</b>",
        "",
        f"Watchlist: {_escape(watchlist_name)}",
        f"Score: {score:.0f}",
        f"Rabatt: {discount_percent:.1f} %",
        "",
        f'<a href="{_escape(url)}">Auf eBay öffnen</a>',
    ]
    return "\n".join(lines)


def _format_auction_alert(
    title: str,
    location_country: str | None,
    current_bid_price: float | None,
    bid_count: int | None,
    item_end_date: str | None,
    currency: str,
    url: str,
) -> str:
    sym = "€" if currency == "EUR" else currency
    bid_str = f"{sym}{current_bid_price:.2f}" if current_bid_price else "–"
    bids_str = str(bid_count) if bid_count is not None else "–"

    lines = [
        "⚠️ <b>Neue Auktion gefunden</b>",
        "",
        f"<b>{_escape(title)}</b>",
        "",
        f"Aktuelles Gebot: {bid_str}",
        f"Gebote: {bids_str}",
        f"Endet: {_escape(str(item_end_date) if item_end_date else '–')}",
        f"Land: {_escape(location_country or '–')}",
        "",
        f'<a href="{_escape(url)}">Auf eBay öffnen</a>',
    ]
    return "\n".join(lines)


def send_telegram_message(text: str) -> bool:
    """
    Send a raw text message via Telegram Bot API.
    Returns True on success, False on failure (never raises).
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not config.ENABLE_TELEGRAM_ALERTS:
        logger.debug("Telegram alerts disabled (ENABLE_TELEGRAM_ALERTS=false)")
        return False

    if not token or not chat_id:
        logger.warning(
            "Telegram not configured: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
        )
        return False

    try:
        resp = requests.post(
            _TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Telegram message sent successfully")
        return True
    except requests.RequestException as exc:
        # Never log the token – exc message may contain the URL with the token
        logger.error("Telegram send failed: %s", type(exc).__name__)
        return False


class TelegramNotifier:
    """
    Sends Telegram alerts when a deal is found.

    If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not configured the alert
    is printed to stdout so the pipeline can still be tested without Telegram.
    """

    def __init__(self) -> None:
        self._configured = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
        if not self._configured:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing – "
                "alerts will be logged to stdout only."
            )

    def send_alert(
        self,
        title: str,
        total_price: float,
        target_market_price: float,
        discount_percent: float,
        score: float,
        url: str,
        currency: str = "EUR",
        # Extended fields (optional, for richer messages)
        listing_type: str = "FIXED_PRICE",
        location_country: str | None = None,
        price: float | None = None,
        shipping: float | None = None,
        bid_count: int | None = None,
        current_bid_price: float | None = None,
        item_end_date: str | None = None,
        watchlist_name: str = "",
    ) -> bool:
        """
        Send a deal alert. Returns True on success, False on failure.
        Never raises an exception.
        """
        if not config.ENABLE_TELEGRAM_ALERTS:
            logger.debug("Telegram alerts disabled – skipping alert for: %s", title[:60])
            return False

        if not url:
            logger.warning("Telegram alert skipped – listing has no URL: %s", title[:60])
            return False

        is_auction = listing_type == "AUCTION"

        if is_auction and not config.TELEGRAM_ALERT_INCLUDE_AUCTIONS:
            logger.debug("Auction alerts disabled – skipping: %s", title[:60])
            return False

        if not is_auction and score < config.TELEGRAM_ALERT_MIN_SCORE:
            logger.debug(
                "Score %.1f < min %.1f – skipping Telegram alert for: %s",
                score, config.TELEGRAM_ALERT_MIN_SCORE, title[:60],
            )
            return False

        if is_auction:
            message = _format_auction_alert(
                title=title,
                location_country=location_country,
                current_bid_price=current_bid_price,
                bid_count=bid_count,
                item_end_date=item_end_date,
                currency=currency,
                url=url,
            )
        else:
            message = _format_fixed_price_alert(
                title=title,
                location_country=location_country,
                price=price if price is not None else total_price,
                shipping=shipping,
                total_price=total_price,
                currency=currency,
                watchlist_name=watchlist_name,
                score=score,
                discount_percent=discount_percent,
                url=url,
            )

        if not self._configured:
            print("\n" + "=" * 60)
            print("DEAL ALERT (Telegram not configured):")
            print(message)
            print("=" * 60 + "\n")
            return True

        return send_telegram_message(message)
