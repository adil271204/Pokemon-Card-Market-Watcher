"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


def _normalize_db_url(url: str | None) -> str | None:
    """SQLAlchemy requires postgresql:// but Render provides postgres://."""
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL: str | None = _normalize_db_url(os.getenv("DATABASE_URL"))

USE_MOCK_EBAY: bool = os.getenv("USE_MOCK_EBAY", "true").lower() in ("true", "1", "yes")

EBAY_CLIENT_ID: str | None = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET: str | None = os.getenv("EBAY_CLIENT_SECRET")
EBAY_MARKETPLACE: str = os.getenv("EBAY_MARKETPLACE", "EBAY_DE")

TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str | None = os.getenv("TELEGRAM_CHAT_ID")

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
