"""
Card-list importer: fetches an external URL and attempts to extract Pokémon card data.

Respects external sites:
- single GET request only
- sensible timeout
- browser-like User-Agent
- SSRF protection (no private IPs, localhost, metadata endpoints)

No eBay scraping – this module is only for external card-list pages.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_HTML_BYTES = 5 * 1024 * 1024  # 5 MB
_TIMEOUT = 15  # seconds
_USER_AGENT = (
    "Mozilla/5.0 (compatible; PokemonCardWatcher/1.0; +https://github.com/pokemon-card-watcher)"
)

# Pokémon card number patterns: 001/165, 166/165 (secret), 1/102
_CARD_NUMBER_RE = re.compile(r"\b(\d{1,3})/(\d{1,3})\b")

# Card number without denominator: isolated 3-digit with leading zero
_NUM_ONLY_RE = re.compile(r"\b(0\d{2})\b")

# Keywords that indicate a Pokémon card entry (raise confidence)
_CARD_KEYWORDS_RE = re.compile(
    r"\b(ex|V|VMAX|VSTAR|GX|EX|Radiant|Trainer|Energy|Supporter|Item|Stadium|"
    r"Illustration|Rare|Common|Uncommon|Full Art|Secret|Hyper|Gold|Rainbow|"
    r"PSA|CGC|graded|Holo|Reverse Holo|Promo)\b",
    re.IGNORECASE,
)

# Rarity normalisierung
_RARITY_MAP = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "holo rare": "Holo Rare",
    "double rare": "Double Rare",
    "ultra rare": "Ultra Rare",
    "illustration rare": "Illustration Rare",
    "special illustration rare": "Special Illustration Rare",
    "hyper rare": "Hyper Rare",
    "secret rare": "Secret Rare",
    "gold": "Gold",
    "promo": "Promo",
    "trainer gallery": "Trainer Gallery",
    "full art": "Full Art",
    "rainbow rare": "Rainbow Rare",
}

# Private/reserved IP ranges (SSRF protection)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "169.254.169.254",
    "instance-data",
}


# ---------------------------------------------------------------------------
# SSRF Protection
# ---------------------------------------------------------------------------

def _validate_url(url: str) -> str:
    """
    Validate URL for SSRF safety.
    Returns the (possibly normalised) URL or raises ValueError.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Nur http/https erlaubt, nicht: {parsed.scheme!r}")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("Kein Hostname in URL gefunden.")

    host_lower = host.lower()
    if host_lower in _BLOCKED_HOSTS:
        raise ValueError(f"Host nicht erlaubt: {host!r}")

    # Resolve hostname to IP and check against private ranges
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Hostname konnte nicht aufgelöst werden: {exc}") from exc

    for family, _, _, _, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _PRIVATE_NETWORKS:
            if ip in net:
                raise ValueError(f"Private/interne IP-Adresse nicht erlaubt: {ip}")

    return url.strip()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_cardlist_url(url: str) -> str:
    """
    Fetch HTML from *url* with SSRF protection and size limit.
    Returns raw HTML string.
    """
    validated_url = _validate_url(url)

    try:
        resp = requests.get(
            validated_url,
            timeout=_TIMEOUT,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de,en;q=0.5",
            },
            allow_redirects=True,
        )
    except requests.exceptions.SSLError as exc:
        raise ValueError(f"SSL-Fehler beim Laden der URL: {exc}") from exc
    except requests.exceptions.Timeout:
        raise ValueError(f"Timeout nach {_TIMEOUT} Sekunden – Seite antwortet nicht.")
    except requests.exceptions.ConnectionError as exc:
        raise ValueError(f"Verbindungsfehler: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise ValueError(f"Fehler beim Abrufen der URL: {exc}") from exc

    if resp.status_code == 403:
        raise ValueError("Zugriff verweigert (HTTP 403). Die Seite erlaubt keine automatischen Anfragen.")
    if resp.status_code == 404:
        raise ValueError("Seite nicht gefunden (HTTP 404).")
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code} – Seite konnte nicht geladen werden.")

    content = resp.content
    if len(content) > _MAX_HTML_BYTES:
        raise ValueError(f"Seite zu groß (>{_MAX_HTML_BYTES // 1024 // 1024} MB). Bitte CSV-Import nutzen.")

    # Try to decode
    encoding = resp.apparent_encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except Exception:
        return content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_cardlist_html(html: str, source_url: str) -> dict[str, Any]:
    """
    Parse HTML and extract Pokémon card data.

    Returns a dict with:
        set_name_detected, set_code_detected, total_cards_detected,
        cards (list of dicts), warnings (list of str)
    """
    warnings: list[str] = []
    soup = BeautifulSoup(html, "lxml")

    # Detect source site for specialised parsing
    parsed_url = urlparse(source_url)
    host = parsed_url.hostname or ""

    set_name_detected = ""
    set_code_detected = ""
    total_cards_detected: int | None = None

    # --- Try JSON-LD and embedded JSON first ---
    json_cards = _try_json_sources(soup, warnings)

    # --- Detect set metadata ---
    set_name_detected, set_code_detected, total_cards_detected = _detect_set_meta(
        soup, source_url, set_name_detected, set_code_detected, total_cards_detected
    )

    # --- Site-specific parsers ---
    cards: list[dict] = []

    if "cardsrfun" in host:
        cards = _parse_cardsrfun(soup, warnings)
    elif "bulbapedia" in host:
        cards = _parse_bulbapedia(soup, warnings)
    elif "serebii" in host:
        cards = _parse_serebii(soup, warnings)
    elif "pkmncards.com" in host:
        cards = _parse_pkmncards(soup, warnings)

    # --- Generic structured parsers ---
    if not cards:
        cards = _parse_tables(soup, warnings)

    if not cards:
        cards = _parse_lists(soup, warnings)

    if not cards:
        cards = _parse_grid_elements(soup, warnings)

    # --- Text-based regex fallback ---
    if not cards:
        cards = _parse_text_fallback(soup, warnings)

    # --- JSON fallback if we found something ---
    if not cards and json_cards:
        cards = json_cards

    # Deduplicate by card_number+card_name
    cards = _deduplicate(cards)

    if not cards:
        warnings.append(
            "Keine Karten erkannt. Die Seitenstruktur ist möglicherweise nicht kompatibel. "
            "Bitte CSV-Import nutzen."
        )
    elif len(cards) < 3:
        warnings.append(
            f"Nur {len(cards)} Karte(n) erkannt – Ergebnis möglicherweise unvollständig. "
            "Bitte Vorschau prüfen und ggf. CSV-Import nutzen."
        )

    if total_cards_detected and len(cards) > 0:
        if len(cards) < total_cards_detected * 0.5:
            warnings.append(
                f"Es wurden {len(cards)} von {total_cards_detected} erwarteten Karten erkannt. "
                "Der Parser kann die Seite nur teilweise lesen."
            )

    return {
        "set_name_detected": set_name_detected,
        "set_code_detected": set_code_detected,
        "total_cards_detected": total_cards_detected,
        "cards": cards,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Set meta detection
# ---------------------------------------------------------------------------

def _detect_set_meta(
    soup: BeautifulSoup,
    source_url: str,
    set_name: str,
    set_code: str,
    total_cards: int | None,
) -> tuple[str, str, int | None]:
    # Page title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # h1
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""

    if not set_name:
        # Use h1 if it looks like a set name
        for candidate in [h1_text, title]:
            if candidate and len(candidate) < 100:
                set_name = candidate.split("|")[0].split("–")[0].strip()
                break

    # Look for total card count patterns
    all_text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d+)\s*(?:Cards|Karten|Karte)\b", all_text, re.IGNORECASE)
    if m and total_cards is None:
        total_cards = int(m.group(1))

    # Set code from URL
    if not set_code:
        # e.g. /sv151/ or /set/sv151 or ?set=sv151
        m_url = re.search(r"[/=]([a-z]{1,4}\d{1,4}[a-z]?)[/\?&\#]?", source_url.lower())
        if m_url:
            set_code = m_url.group(1)

    return set_name, set_code, total_cards


# ---------------------------------------------------------------------------
# JSON sources
# ---------------------------------------------------------------------------

def _try_json_sources(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    """Try script tags for JSON-LD or Next.js/Shopify JSON."""
    cards: list[dict] = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            found = _extract_from_jsonld(data)
            cards.extend(found)
        except (json.JSONDecodeError, AttributeError):
            pass

    if cards:
        return cards

    # Next.js __NEXT_DATA__
    for script in soup.find_all("script", id="__NEXT_DATA__"):
        try:
            data = json.loads(script.string or "")
            found = _extract_from_next_data(data)
            cards.extend(found)
        except (json.JSONDecodeError, AttributeError):
            pass

    return cards


def _extract_from_jsonld(data: Any) -> list[dict]:
    cards: list[dict] = []
    if isinstance(data, list):
        for item in data:
            cards.extend(_extract_from_jsonld(item))
    elif isinstance(data, dict):
        name = data.get("name", "")
        if name and any(kw in name for kw in ["ex", "V", "GX", "EX", "Trainer", "Energy", "Rare"]):
            number = data.get("sku", "") or data.get("productID", "") or ""
            cards.append(_make_card(name, number, confidence=0.6))
    return cards


def _extract_from_next_data(data: Any, depth: int = 0) -> list[dict]:
    cards: list[dict] = []
    if depth > 8:
        return cards
    if isinstance(data, list):
        for item in data:
            cards.extend(_extract_from_next_data(item, depth + 1))
    elif isinstance(data, dict):
        name = data.get("name") or data.get("title") or ""
        number = data.get("number") or data.get("localId") or ""
        if name and _CARD_NUMBER_RE.search(str(number)):
            cards.append(_make_card(str(name), str(number), confidence=0.7))
        else:
            for v in data.values():
                if isinstance(v, (dict, list)):
                    cards.extend(_extract_from_next_data(v, depth + 1))
    return cards


# ---------------------------------------------------------------------------
# Site-specific parsers
# ---------------------------------------------------------------------------

def _parse_cardsrfun(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    """Parser for cardsrfun.de card list pages."""
    cards: list[dict] = []

    # Try table rows
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any(h in headers for h in ["name", "karte", "card", "nummer", "nr", "#"]):
            continue
        for row in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            card = _try_parse_row_cells(cells, headers)
            if card:
                cards.append(card)

    if cards:
        return cards

    # cardsrfun often uses div.card or article elements
    for elem in soup.find_all(class_=re.compile(r"card|pokemon|karte", re.IGNORECASE)):
        text = elem.get_text(" ", strip=True)
        m = _CARD_NUMBER_RE.search(text)
        if m:
            name = _extract_name_near_number(text, m)
            card = _make_card(name, m.group(0), raw_text=text, confidence=0.65)
            if card:
                cards.append(card)

    return cards


def _parse_bulbapedia(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable|roundy", re.IGNORECASE)):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        for row in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            card = _try_parse_row_cells(cells, headers)
            if card:
                cards.append(card)
    return cards


def _parse_serebii(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if len(cells) >= 2:
                card = _try_parse_row_cells(cells, [])
                if card:
                    cards.append(card)
    return cards


def _parse_pkmncards(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for a in soup.find_all("a", href=re.compile(r"/card/|/cards/")):
        text = a.get_text(" ", strip=True)
        m = _CARD_NUMBER_RE.search(text)
        if m:
            name = _extract_name_near_number(text, m)
            cards.append(_make_card(name, m.group(0), raw_text=text, confidence=0.7))
    return cards


# ---------------------------------------------------------------------------
# Generic parsers
# ---------------------------------------------------------------------------

def _parse_tables(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        rows_found = 0
        for row in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all(["td"])]
            if not cells:
                continue
            card = _try_parse_row_cells(cells, headers)
            if card:
                cards.append(card)
                rows_found += 1
        if rows_found > 2:
            break  # use the first table that has actual cards

    return cards


def _parse_lists(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for ul in soup.find_all(["ul", "ol"]):
        for li in ul.find_all("li"):
            text = li.get_text(" ", strip=True)
            m = _CARD_NUMBER_RE.search(text)
            if not m:
                continue
            name = _extract_name_near_number(text, m)
            if name:
                cards.append(_make_card(name, m.group(0), raw_text=text, confidence=0.6))
    return cards


def _parse_grid_elements(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    cards: list[dict] = []
    for elem in soup.find_all(
        True,
        class_=re.compile(r"card|item|product|pokemon", re.IGNORECASE)
    ):
        text = elem.get_text(" ", strip=True)
        if len(text) > 300:
            continue
        m = _CARD_NUMBER_RE.search(text)
        if not m:
            continue
        name = _extract_name_near_number(text, m)
        if name:
            rarity = _detect_rarity(text)
            card = _make_card(name, m.group(0), rarity=rarity, raw_text=text, confidence=0.65)
            cards.append(card)
    return _deduplicate(cards)


def _parse_text_fallback(soup: BeautifulSoup, warnings: list[str]) -> list[dict]:
    """Last resort: scan all visible text for card-number patterns."""
    cards: list[dict] = []

    # Remove scripts and styles
    for tag in soup(["script", "style", "meta", "link"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    for line in lines:
        m = _CARD_NUMBER_RE.search(line)
        if not m:
            continue
        # Skip lines that look like prices or irrelevant numbers
        if re.search(r"€|\$|£|price|preis|qty|stock", line, re.IGNORECASE):
            continue
        name = _extract_name_near_number(line, m)
        if not name:
            continue
        rarity = _detect_rarity(line)
        confidence = 0.5
        if _CARD_KEYWORDS_RE.search(line):
            confidence = 0.65
        cards.append(_make_card(name, m.group(0), rarity=rarity, raw_text=line, confidence=confidence))

    return _deduplicate(cards)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_parse_row_cells(cells: list[str], headers: list[str]) -> dict | None:
    """Try to extract a card from a table row's cells."""
    row_text = " ".join(cells)
    m = _CARD_NUMBER_RE.search(row_text)
    if not m:
        return None

    number = m.group(0)
    name = ""
    rarity = ""

    # Map by header position
    name_idx = _find_col(headers, ["name", "karte", "card", "pokemon"])
    num_idx = _find_col(headers, ["#", "nr", "nummer", "number", "no"])
    rarity_idx = _find_col(headers, ["rarity", "rarität", "seltenheit", "type"])

    if name_idx is not None and name_idx < len(cells):
        name = cells[name_idx].strip()
    if num_idx is not None and num_idx < len(cells):
        number = cells[num_idx].strip() or number
    if rarity_idx is not None and rarity_idx < len(cells):
        rarity = _normalize_rarity(cells[rarity_idx].strip())

    # Fallback: the cell that doesn't contain the number and is longest
    if not name:
        name = _extract_name_near_number(row_text, m)

    if not name:
        return None

    return _make_card(name, number, rarity=rarity, raw_text=row_text, confidence=0.75)


def _find_col(headers: list[str], candidates: list[str]) -> int | None:
    for idx, h in enumerate(headers):
        if any(c in h for c in candidates):
            return idx
    return None


def _extract_name_near_number(text: str, m: re.Match) -> str:
    """Extract a card name from text near the card-number match."""
    # Text before the number
    before = text[:m.start()].strip()
    # Remove number noise
    before = re.sub(r"\d{3,}/\d{3,}", "", before)
    before = re.sub(r"\s{2,}", " ", before).strip()

    # Take last 1-6 words before the number
    words = before.split()
    if not words:
        # Try text after number
        after = text[m.end():].strip()
        words = after.split()[:5]

    name_words = []
    for w in reversed(words[-6:]):
        # Stop at noise words
        if re.match(r"^[\d€$£/]+$", w):
            break
        name_words.insert(0, w)
    name = " ".join(name_words).strip(" –·•|/\\")

    # Clean up
    name = re.sub(r"[^\w\s\-'.éèêàüöäñ]", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip()

    return name if len(name) > 1 else ""


def _detect_rarity(text: str) -> str:
    text_lower = text.lower()
    for raw, normalised in sorted(_RARITY_MAP.items(), key=lambda x: -len(x[0])):
        if raw in text_lower:
            return normalised
    return ""


def _normalize_rarity(text: str) -> str:
    tl = text.lower().strip()
    return _RARITY_MAP.get(tl, text)


def _make_card(
    name: str,
    number: str,
    *,
    rarity: str = "",
    variant: str = "normal",
    language: str = "EN",
    confidence: float = 0.5,
    raw_text: str = "",
) -> dict:
    return {
        "card_name": name.strip(),
        "card_number": number.strip(),
        "rarity": rarity,
        "variant": variant,
        "language": language,
        "confidence": round(confidence, 2),
        "raw_text": raw_text[:200],
    }


def _deduplicate(cards: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for c in cards:
        key = f"{c['card_number']}|{c['card_name'].lower()}"
        if key not in seen:
            seen.add(key)
            result.append(c)
    # Sort by card number if possible
    def _sort_key(c: dict) -> tuple:
        m = re.match(r"(\d+)", c.get("card_number", ""))
        return (int(m.group(1)) if m else 9999, c.get("card_name", ""))
    result.sort(key=_sort_key)
    return result


# ---------------------------------------------------------------------------
# Public text helper
# ---------------------------------------------------------------------------

def extract_cards_from_text(text: str) -> list[dict]:
    """
    Simple text-based extraction (no HTML).
    Useful for debugging or testing the parser with plain text.
    """
    cards: list[dict] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _CARD_NUMBER_RE.search(line)
        if not m:
            continue
        name = _extract_name_near_number(line, m)
        rarity = _detect_rarity(line)
        if name:
            cards.append(_make_card(name, m.group(0), rarity=rarity, raw_text=line, confidence=0.5))
    return _deduplicate(cards)
