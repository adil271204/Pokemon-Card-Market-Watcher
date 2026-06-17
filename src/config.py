"""Application configuration loaded from environment variables."""

import os
import secrets

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

# Dashboard auth
DASHBOARD_PASSWORD: str | None = os.getenv("DASHBOARD_PASSWORD")

_raw_secret = os.getenv("SESSION_SECRET")
if _raw_secret:
    SESSION_SECRET: str = _raw_secret
else:
    # Ephemeral fallback – sessions reset on every restart; fine locally,
    # not acceptable in production.
    SESSION_SECRET = secrets.token_hex(32)

SESSION_SECRET_IS_SET: bool = bool(_raw_secret)
