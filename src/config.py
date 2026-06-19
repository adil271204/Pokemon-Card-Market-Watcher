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

# eBay
USE_MOCK_EBAY: bool = os.getenv("USE_MOCK_EBAY", "true").lower() in ("true", "1", "yes")
EBAY_CLIENT_ID: str | None = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET: str | None = os.getenv("EBAY_CLIENT_SECRET")
EBAY_MARKETPLACE: str = os.getenv("EBAY_MARKETPLACE", "EBAY_DE")
EBAY_ENV: str = os.getenv("EBAY_ENV", "production").lower()  # "production" or "sandbox"
EBAY_SEARCH_LIMIT: int = int(os.getenv("EBAY_SEARCH_LIMIT", "50"))
EBAY_LOOKBACK_DAYS: int = int(os.getenv("EBAY_LOOKBACK_DAYS", "14"))
EBAY_MAX_PAGES: int = int(os.getenv("EBAY_MAX_PAGES", "5"))

# Location filter
_EU_DEFAULT = "DE,AT,FR,IT,ES,NL,BE,PL,IE,PT,SE,FI,DK,CZ,SK,SI,HU,HR,RO,BG,GR,LT,LV,EE,LU,MT,CY"
_EXCLUDED_DEFAULT = "GB,UK,US,CN,JP,CA,AU"


def _parse_country_set(raw: str | None, default: str) -> set[str]:
    src = raw if raw and raw.strip() else default
    return {c.strip().upper().replace("UK", "GB") for c in src.split(",") if c.strip()}


EBAY_ALLOWED_COUNTRIES: set[str] = _parse_country_set(
    os.getenv("EBAY_ALLOWED_COUNTRIES"), _EU_DEFAULT
)
EBAY_EXCLUDED_COUNTRIES: set[str] = _parse_country_set(
    os.getenv("EBAY_EXCLUDED_COUNTRIES"), _EXCLUDED_DEFAULT
)
EBAY_ALLOW_UNKNOWN_LOCATION: bool = os.getenv(
    "EBAY_ALLOW_UNKNOWN_LOCATION", "false"
).lower() in ("true", "1", "yes")

# Telegram
TELEGRAM_BOT_TOKEN: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str | None = os.getenv("TELEGRAM_CHAT_ID")

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Dashboard auth
DASHBOARD_PASSWORD: str | None = os.getenv("DASHBOARD_PASSWORD")

_raw_secret = os.getenv("SESSION_SECRET")
if _raw_secret:
    SESSION_SECRET: str = _raw_secret
else:
    SESSION_SECRET = secrets.token_hex(32)

SESSION_SECRET_IS_SET: bool = bool(_raw_secret)

# Set Scanner
SET_SCAN_MAX_CARDS: int = int(os.getenv("SET_SCAN_MAX_CARDS", "200"))
SET_SCAN_INCLUDE_AUCTIONS: bool = os.getenv("SET_SCAN_INCLUDE_AUCTIONS", "false").lower() in ("true", "1", "yes")
SET_SCAN_DAYS: int = int(os.getenv("SET_SCAN_DAYS", "14"))

# Grading costs (for opportunity calculation)
GRADING_COST: float = float(os.getenv("GRADING_COST", "18"))
GRADING_SHIPPING_TO_GRADER: float = float(os.getenv("GRADING_SHIPPING_TO_GRADER", "15"))
GRADING_RETURN_SHIPPING: float = float(os.getenv("GRADING_RETURN_SHIPPING", "15"))
GRADING_MARKETPLACE_FEE_PERCENT: float = float(os.getenv("GRADING_MARKETPLACE_FEE_PERCENT", "13"))
GRADING_RISK_DISCOUNT_PERCENT: float = float(os.getenv("GRADING_RISK_DISCOUNT_PERCENT", "10"))
GRADING_PSA10_PROBABILITY: float = float(os.getenv("GRADING_PSA10_PROBABILITY", "0.50"))
GRADING_PSA9_PROBABILITY: float = float(os.getenv("GRADING_PSA9_PROBABILITY", "0.30"))
GRADING_PSA8_OR_LOWER_PROBABILITY: float = float(os.getenv("GRADING_PSA8_OR_LOWER_PROBABILITY", "0.20"))

# Derived helpers
EBAY_KEYS_SET: bool = bool(EBAY_CLIENT_ID and EBAY_CLIENT_SECRET)

# Token endpoints
_EBAY_TOKEN_URLS = {
    "production": "https://api.ebay.com/identity/v1/oauth2/token",
    "sandbox": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
}
_EBAY_API_URLS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}

EBAY_TOKEN_URL: str = _EBAY_TOKEN_URLS.get(EBAY_ENV, _EBAY_TOKEN_URLS["production"])
EBAY_API_BASE_URL: str = _EBAY_API_URLS.get(EBAY_ENV, _EBAY_API_URLS["production"])
