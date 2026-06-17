"""Send deal alerts via Telegram Bot API."""

import logging

import requests

from src import config

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _format_message(
    title: str,
    total_price: float,
    target_market_price: float,
    discount_percent: float,
    score: float,
    url: str,
    currency: str = "EUR",
) -> str:
    symbol = "€" if currency == "EUR" else currency
    return (
        "🎴 *Pokemon Card Deal Alert!*\n\n"
        f"*{title}*\n\n"
        f"💰 Price: `{symbol}{total_price:.2f}`\n"
        f"📈 Market value: `{symbol}{target_market_price:.2f}`\n"
        f"🔥 Discount: `{discount_percent:.1f}%`\n"
        f"⭐ Deal score: `{score:.0f}/100`\n\n"
        f"🔗 [View on eBay]({url})"
    )


class TelegramNotifier:
    """
    Sends Telegram messages when a deal is found.

    If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not configured the alert
    is printed to stdout so the pipeline can still be tested end-to-end.
    """

    def __init__(self) -> None:
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._configured = bool(self._token and self._chat_id)
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
    ) -> bool:
        """
        Send a deal alert.

        Returns True on success, False on failure.
        """
        message = _format_message(
            title=title,
            total_price=total_price,
            target_market_price=target_market_price,
            discount_percent=discount_percent,
            score=score,
            url=url,
            currency=currency,
        )

        if not self._configured:
            # Intentional print so the operator sees alerts even without Telegram
            print("\n" + "=" * 60)
            print("DEAL ALERT (Telegram not configured):")
            print(message)
            print("=" * 60 + "\n")
            return True

        try:
            resp = requests.post(
                _TELEGRAM_API.format(token=self._token),
                json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Telegram alert sent for listing: %s", title[:80])
            return True
        except requests.RequestException as exc:
            logger.error("Failed to send Telegram alert: %s", exc)
            return False
