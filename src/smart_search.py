"""
Smart Search – detect input type, build queries, run eBay Browse API search.

Supports:
  - Free-text card/set search
  - Cardmarket URL (slug parsing only, no price scraping)
  - Set code / set name lookup against local DB

No scraping of eBay HTML. Only official eBay Browse API.
No Cardmarket price scraping. URL used only to derive search terms.
No Telegram alerts sent.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from src import config
from src.ebay_client import EbayClient, RawListing
from src.listing_cleaner import clean_and_classify_listing
from src.location_filter import is_allowed_location

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; PokemonCardWatcher/1.0; +https://github.com/pokemon-card-watcher)"
)

# ---------------------------------------------------------------------------
# Cardmarket set-slug cleanup map (common cases)
# ---------------------------------------------------------------------------
_SET_SLUG_REPLACEMENTS = [
    (r"\bScarlet-Violet-", "SV "),
    (r"\bSword-Shield-", "SS "),
    (r"\bBlack-White-", "BW "),
    (r"\bXY-", "XY "),
    (r"\bSun-Moon-", "SM "),
    (r"\bDiamond-Pearl-", "DP "),
]

# Cardmarket product code patterns
# Compound: SET_CODE (2-4 letters) + CARD_PREFIX (GG|TG|IR|UT) + DIGITS  e.g. CRZGG69
_CM_COMPOUND_RE = re.compile(r'^([A-Z]{2,4})(GG|TG|IR|UT)(\d{1,3})$')
# Simple: SET_CODE (2-5 letters) + DIGITS  e.g. MEW199, SV3EN123
_CM_SIMPLE_RE = re.compile(r'^([A-Z]{2,5})(\d{1,3})$')
# Bare card number: GG69, TG17, 199
_CM_CARDNUM_RE = re.compile(r'^(GG|TG|IR|UT)?(\d{1,3})$')

# Pokemon card keywords that increase confidence it's a card (not set) search
_CARD_KW_RE = re.compile(
    r"\b(ex|EX|GX|V\b|VMAX|VSTAR|Radiant|Trainer|Energy|PSA|CGC|Holo|Secret|Full Art)\b"
)

# Set codes like sv151, swsh1, xy10 etc.
_SET_CODE_RE = re.compile(r"^[a-z]{2,4}\d{1,3}[a-z]?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1. Input detection
# ---------------------------------------------------------------------------

def detect_smart_search_input(input_value: str) -> dict[str, Any]:
    """
    Classify the user's input and extract as much structured data as possible.

    Returns:
        type: "cardmarket_url" | "card" | "set" | "unknown"
        normalized_input, detected_card_name, detected_set_name,
        detected_card_number, warnings
    """
    warnings: list[str] = []
    raw = input_value.strip()

    if not raw:
        return _result("unknown", raw, warnings=["Bitte einen Suchbegriff eingeben."])

    # --- Cardmarket URL ---
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        if "cardmarket.com" in (parsed.hostname or ""):
            cm = parse_cardmarket_url(raw)
            return _result(
                "cardmarket_url",
                raw,
                card_name=cm.get("card_name", ""),
                set_name=cm.get("set_name", ""),
                card_number=cm.get("card_number_hint", ""),
                warnings=cm.get("warnings", []),
                extra={"cardmarket": cm},
            )
        warnings.append("URL erkannt, aber keine Cardmarket-URL. Wird als Freitext behandelt.")
        return _result("unknown", raw, warnings=warnings)

    # --- Set code? (e.g. sv151, swsh12) ---
    if _SET_CODE_RE.match(raw):
        return _result("set", raw, set_name=raw.lower())

    # --- Set name? Contains mostly words, no card number ---
    if not re.search(r"\d{3,}", raw) and not _CARD_KW_RE.search(raw):
        # Short input with ≤ 3 words and no digits → likely a set name
        words = raw.split()
        if len(words) <= 4:
            return _result("set", raw, set_name=raw)

    # --- Looks like a card search ---
    # Try to extract a card number like 006/165 or 199
    card_number = ""
    m = re.search(r"\b(\d{1,3}/\d{1,3})\b", raw)
    if m:
        card_number = m.group(1)

    return _result("card", raw, card_name=_clean_card_name(raw), card_number=card_number)


def _clean_card_name(text: str) -> str:
    """Remove card-number patterns, leaving just the card name."""
    cleaned = re.sub(r"\b\d{1,3}/\d{1,3}\b", "", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _result(
    type_: str,
    normalized: str,
    *,
    card_name: str = "",
    set_name: str = "",
    card_number: str = "",
    warnings: list[str] | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    r: dict[str, Any] = {
        "type": type_,
        "normalized_input": normalized,
        "detected_card_name": card_name,
        "detected_set_name": set_name,
        "detected_card_number": card_number,
        "warnings": warnings or [],
    }
    if extra:
        r.update(extra)
    return r


# ---------------------------------------------------------------------------
# 2. Cardmarket URL parser
# ---------------------------------------------------------------------------

def parse_cardmarket_url(url: str) -> dict[str, Any]:
    """
    Derive card/set search terms from a Cardmarket URL slug.
    No price scraping. Optionally fetches page title for better data.
    Falls back to slug parsing if fetch fails.
    """
    warnings: list[str] = []
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    # Path: /de/Pokemon/Products/Singles/{set-slug}/{card-slug}
    # Find "Singles" in path
    try:
        singles_idx = next(i for i, p in enumerate(path_parts) if p.lower() == "singles")
        set_slug = path_parts[singles_idx + 1] if singles_idx + 1 < len(path_parts) else ""
        card_slug = path_parts[singles_idx + 2] if singles_idx + 2 < len(path_parts) else ""
    except StopIteration:
        # Fallback: last two path parts
        set_slug = path_parts[-2] if len(path_parts) >= 2 else ""
        card_slug = path_parts[-1] if path_parts else ""

    set_name = _slug_to_name(set_slug)
    card_name_raw, card_number_hint, code_parsed = _parse_card_slug(card_slug)
    card_name = card_name_raw

    # Optional: try a single GET to fetch meta title (title tag / og:title)
    try:
        resp = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "de,en;q=0.5"},
            allow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.content) < 2 * 1024 * 1024:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.content[:500_000], "lxml")
            og_title = soup.find("meta", property="og:title")
            page_title = og_title["content"] if og_title and og_title.get("content") else ""
            if not page_title:
                t = soup.find("title")
                page_title = t.get_text(strip=True) if t else ""
            if page_title:
                # og:title often looks like "Charizard ex (MEW 006) – Cardmarket"
                name_from_title = page_title.split("–")[0].split("|")[0].split("(")[0].strip()
                if name_from_title and len(name_from_title) < 80:
                    card_name = name_from_title
    except Exception as exc:
        logger.debug("Cardmarket fetch failed (slug fallback used): %s", exc)
        warnings.append("Cardmarket-Seite konnte nicht geladen werden – Suche basiert auf URL-Slug.")

    queries = _build_cm_queries(card_name, set_name, card_number_hint, code_parsed)

    return {
        "card_name": card_name,
        "set_name": set_name,
        "set_slug": set_slug,
        "card_slug": card_slug,
        "card_number_hint": card_number_hint,
        "set_code_hint": (code_parsed or {}).get("set_code_hint", ""),
        "code_parsed": code_parsed,
        "queries": queries,
        "warnings": warnings,
    }


def parse_cardmarket_product_code(code: str) -> dict[str, Any]:
    """
    Parse a Cardmarket product code token into set_code_hint + card_number_hint.

    Examples:
      CRZGG69  → set_code_hint=CRZ, card_number_hint=GG69
      MEW199   → set_code_hint=MEW, card_number_hint=199
      SWSHTG17 → set_code_hint=SWSH, card_number_hint=TG17
      GG69     → set_code_hint='',  card_number_hint=GG69
      TG17     → set_code_hint='',  card_number_hint=TG17
      199      → set_code_hint='',  card_number_hint=199
    """
    code = code.strip().upper()
    warnings: list[str] = []

    # Compound first: CRZGG69 → CRZ + GG69
    m = _CM_COMPOUND_RE.match(code)
    if m:
        return {"raw_code": code, "set_code_hint": m.group(1),
                "card_number_hint": m.group(2) + m.group(3), "warnings": warnings}

    # Bare card number prefix (GG69, TG17) – check BEFORE simple set-code match
    # so GG is not treated as a set code
    m = _CM_CARDNUM_RE.match(code)
    if m and m.group(1):  # has known prefix (GG/TG/IR/UT) + digits
        return {"raw_code": code, "set_code_hint": "",
                "card_number_hint": m.group(1) + m.group(2), "warnings": warnings}

    # Simple: MEW199 → MEW + 199
    m = _CM_SIMPLE_RE.match(code)
    if m:
        return {"raw_code": code, "set_code_hint": m.group(1),
                "card_number_hint": m.group(2), "warnings": warnings}

    # Pure number: 199
    m = _CM_CARDNUM_RE.match(code)
    if m:
        return {"raw_code": code, "set_code_hint": "",
                "card_number_hint": m.group(2), "warnings": warnings}

    warnings.append(f"Unbekanntes Cardmarket-Code-Format: {code}")
    return {"raw_code": code, "set_code_hint": "", "card_number_hint": code, "warnings": warnings}


def _is_cm_product_code(segment: str) -> bool:
    s = segment.upper()
    return bool(_CM_COMPOUND_RE.match(s) or _CM_SIMPLE_RE.match(s))


def _slug_to_name(slug: str) -> str:
    """Convert a Cardmarket set slug to a human-readable name."""
    return slug.replace("-", " ").strip()


def _parse_card_slug(slug: str) -> tuple[str, str, dict | None]:
    """
    Extract card name, number hint, and parsed code dict from a card slug.
    E.g. "Giratina-VSTAR-CRZGG69" → ("Giratina VSTAR", "GG69", {set_code_hint: "CRZ", ...})
         "Charizard-ex-V3-MEW199" → ("Charizard ex", "199", {set_code_hint: "MEW", ...})
    """
    segments = slug.split("-")

    code_parsed: dict | None = None
    code_idx: int | None = None

    # Scan right-to-left for the first segment that looks like a product code
    for i in range(len(segments) - 1, -1, -1):
        if _is_cm_product_code(segments[i]):
            code_parsed = parse_cardmarket_product_code(segments[i])
            code_idx = i
            break

    name_segments = segments[:code_idx] if code_idx is not None else segments[:]

    # Remove Cardmarket variant suffixes V2, V3 etc. (not Pokémon types V/VMAX/VSTAR)
    if name_segments and re.match(r'^V\d+$', name_segments[-1], re.IGNORECASE):
        name_segments = name_segments[:-1]

    name = " ".join(s for s in name_segments if s).strip()
    number_hint = code_parsed["card_number_hint"] if code_parsed else ""

    return name, number_hint, code_parsed


def _build_cm_queries(
    card_name: str,
    set_name: str,
    card_number_hint: str,
    code_parsed: dict | None = None,
) -> list[str]:
    """Build eBay-friendly search queries with special cases for known set codes."""
    queries: list[str] = []
    set_code = (code_parsed or {}).get("set_code_hint", "").upper()
    num = card_number_hint.upper() if card_number_hint else ""

    # Special case: Crown Zenith Galarian Gallery (CRZGG…)
    is_crz_gg = set_code == "CRZ" and num.startswith("GG")
    # Special case: Pokémon 151 (MEW set code)
    is_151 = set_code == "MEW"

    if is_crz_gg:
        if card_name and num:
            queries.append(f"{card_name} {num}")                        # Giratina VSTAR GG69
        if card_name:
            queries.append(f"{card_name} Galarian Gallery")             # Giratina VSTAR Galarian Gallery
        if card_name and num:
            queries.append(f"{card_name} Crown Zenith {num}")           # Giratina VSTAR Crown Zenith GG69
    elif is_151:
        if card_name:
            queries.append(f"{card_name} 151 {num}".strip())            # Charizard ex 151 006
            queries.append(f"{card_name} {num}".strip())                # Charizard ex 006
            queries.append(f"{card_name} SV 151")                       # Charizard ex SV 151
    else:
        if card_name and set_name:
            queries.append(f"{card_name} {set_name}")
        if card_name and num:
            queries.append(f"{card_name} {num}")
        if card_name and not queries:
            queries.append(f"{card_name} Pokemon")

    return queries


# ---------------------------------------------------------------------------
# 3. Query builder
# ---------------------------------------------------------------------------

def build_smart_queries(
    parsed_input: dict[str, Any],
    options: dict[str, Any],
) -> list[dict[str, str]]:
    """
    Build list of {query, category, source} dicts for eBay search.
    """
    include_raw = options.get("include_raw", True)
    include_psa9 = options.get("include_psa9", True)
    include_psa10 = options.get("include_psa10", True)

    input_type = parsed_input.get("type", "unknown")
    card_name = parsed_input.get("detected_card_name", "").strip()
    set_name = parsed_input.get("detected_set_name", "").strip()
    card_number = parsed_input.get("detected_card_number", "").strip()

    # For cardmarket URLs, prefer the CM-derived names and code_parsed
    code_parsed: dict | None = None
    if input_type == "cardmarket_url" and "cardmarket" in parsed_input:
        cm = parsed_input["cardmarket"]
        card_name = cm.get("card_name", card_name)
        set_name = cm.get("set_name", set_name)
        card_number = cm.get("card_number_hint", card_number)
        code_parsed = cm.get("code_parsed")

    if not card_name and not set_name:
        card_name = parsed_input.get("normalized_input", "")

    queries: list[dict[str, str]] = []

    def _add(q: str, cat: str) -> None:
        q = q.strip()
        if q:
            queries.append({"query": q, "category": cat, "source": "smart_search"})

    # Use CM-aware query builder for raw queries when we have structured data
    if input_type == "cardmarket_url" and card_name:
        cm_raw_queries = _build_cm_queries(card_name, set_name, card_number, code_parsed)
        if include_raw:
            for q in cm_raw_queries:
                _add(q, "RAW")
        if include_psa9:
            for q in cm_raw_queries[:2]:  # take the best 1-2 queries for graded
                _add(f"{q} PSA 9", "PSA9")
        if include_psa10:
            for q in cm_raw_queries[:2]:
                _add(f"{q} PSA 10", "PSA10")
    else:
        if include_raw:
            if card_name and card_number and set_name:
                _add(f"{card_name} {card_number} {set_name}", "RAW")
            if card_name and set_name:
                _add(f"{card_name} {set_name}", "RAW")
            if card_name and card_number:
                _add(f"{card_name} {card_number}", "RAW")
            if card_name and not card_number and not set_name:
                _add(f"{card_name} Pokemon", "RAW")

        if include_psa9:
            if card_name and card_number:
                _add(f"{card_name} {card_number} PSA 9", "PSA9")
            elif card_name and set_name:
                _add(f"{card_name} {set_name} PSA 9", "PSA9")
            elif card_name:
                _add(f"{card_name} PSA 9", "PSA9")

        if include_psa10:
            if card_name and card_number:
                _add(f"{card_name} {card_number} PSA 10", "PSA10")
            elif card_name and set_name:
                _add(f"{card_name} {set_name} PSA 10", "PSA10")
            elif card_name:
                _add(f"{card_name} PSA 10", "PSA10")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for q in queries:
        if q["query"] not in seen:
            seen.add(q["query"])
            deduped.append(q)

    return deduped


# ---------------------------------------------------------------------------
# 4. Free set search (set not locally imported)
# ---------------------------------------------------------------------------

def build_free_set_queries(set_name: str, options: dict[str, Any]) -> list[dict[str, str]]:
    """
    Build generic eBay queries for a set that hasn't been imported locally.
    Includes special-case query lists for well-known sets.
    Respects include_raw / include_psa9 / include_psa10 options.
    """
    include_raw = options.get("include_raw", True)
    include_psa9 = options.get("include_psa9", True)
    include_psa10 = options.get("include_psa10", True)

    name = set_name.strip()
    nl = name.lower()
    queries: list[dict[str, str]] = []

    def _add(q: str, cat: str) -> None:
        q = q.strip()
        if q:
            queries.append({"query": q, "category": cat, "source": "free_set_search"})

    # --- Special case: Pokémon 151 ---
    if nl == "151" or "151" in nl:
        if include_raw:
            _add("Pokemon 151", "RAW")
            _add("Pokémon 151", "RAW")
            _add("Scarlet Violet 151", "RAW")
            _add("Scarlet & Violet 151", "RAW")
        if include_psa9:
            _add("151 PSA 9", "PSA9")
        if include_psa10:
            _add("151 PSA 10", "PSA10")

    # --- Special case: Crown Zenith ---
    elif "crown zenith" in nl or nl in ("crz",):
        if include_raw:
            _add("Crown Zenith Pokemon", "RAW")
            _add("Crown Zenith Pokémon", "RAW")
            _add("Crown Zenith", "RAW")
            _add("Crown Zenith Galarian Gallery", "RAW")
            _add("Crown Zenith GG", "RAW")
            _add("Crown Zenith Gold", "RAW")
        if include_psa9:
            _add("Crown Zenith PSA 9", "PSA9")
        if include_psa10:
            _add("Crown Zenith PSA 10", "PSA10")

    # --- Generic set ---
    else:
        if include_raw:
            _add(f"{name} Pokemon", "RAW")
            _add(f"{name} Pokémon", "RAW")
            _add(name, "RAW")
        if include_psa9:
            _add(f"{name} PSA 9", "PSA9")
        if include_psa10:
            _add(f"{name} PSA 10", "PSA10")

    return queries


# ---------------------------------------------------------------------------
# 5. eBay search runner
# ---------------------------------------------------------------------------

def run_smart_search(
    queries: list[dict[str, str]],
    options: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute eBay Browse API searches for each query in *queries*.
    Deduplicates, applies location filter, keyword cleaner.
    Returns structured results – no Telegram alerts, no DB writes here.
    """
    lookback_hours = int(options.get("lookback_hours", 24))
    lookback_days = max(1, lookback_hours // 24) or 1
    include_auctions = bool(options.get("include_auctions", False))
    only_eu = bool(options.get("only_eu", True))
    max_results = int(options.get("max_results_per_query", 50))
    marketplace = options.get("marketplace", config.EBAY_MARKETPLACE)

    client = EbayClient()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    seen_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    api_total = 0
    errors: list[str] = []

    for q_info in queries:
        query = q_info["query"]
        category = q_info["category"]
        try:
            listings = client.search_recent_listings(
                query=query,
                marketplace=marketplace,
                limit=min(max_results, config.EBAY_SEARCH_LIMIT),
                lookback_days=lookback_days,
                max_pages=config.EBAY_MAX_PAGES,
                include_auctions=include_auctions,
            )
            api_total += len(listings)

            for l in listings:
                if l.ebay_item_id in seen_ids:
                    continue
                seen_ids.add(l.ebay_item_id)

                # Date filter (more precise than lookback_days)
                if l.listing_date and l.listing_date < cutoff:
                    continue

                # Location filter
                if only_eu:
                    allowed, _ = is_allowed_location(
                        l.location_country,
                        config.EBAY_ALLOWED_COUNTRIES,
                        config.EBAY_EXCLUDED_COUNTRIES,
                        config.EBAY_ALLOW_UNKNOWN_LOCATION,
                    )
                    if not allowed:
                        continue

                # Keyword cleaner (skip proxies, reprints etc.)
                cl = clean_and_classify_listing(l.title)
                if cl.is_bad_match:
                    continue

                is_auction = l.listing_type == "AUCTION"

                results.append({
                    "listing": l,
                    "category": category,
                    "query": query,
                    "is_auction": is_auction,
                    "classification": cl,
                })

        except Exception as exc:
            logger.warning("Smart search query %r failed: %s", query, exc)
            errors.append(f"Query «{query}»: {exc}")

    # Sort by listing_date desc, then total_price asc
    results.sort(
        key=lambda r: (
            -(r["listing"].listing_date.timestamp() if r["listing"].listing_date else 0),
            r["listing"].total_price,
        )
    )

    return {
        "results": results,
        "api_total": api_total,
        "after_filter": len(results),
        "errors": errors,
        "queries_run": [q["query"] for q in queries],
    }


# ---------------------------------------------------------------------------
# 5. Set search helper
# ---------------------------------------------------------------------------

def search_set_cards(
    pokemon_set: Any,
    cards: list[Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """
    Run smart search for every card in *cards* (belonging to *pokemon_set*).
    Returns per-card summary and all raw listings.
    """
    from src.set_scanner import build_card_queries  # avoid circular at module level

    include_raw = options.get("include_raw", True)
    include_psa9 = options.get("include_psa9", True)
    include_psa10 = options.get("include_psa10", True)

    all_results: list[dict] = []
    card_summaries: list[dict] = []
    api_total = 0

    client = EbayClient()
    lookback_hours = int(options.get("lookback_hours", 24))
    lookback_days = max(1, lookback_hours // 24)
    include_auctions = bool(options.get("include_auctions", False))
    only_eu = bool(options.get("only_eu", True))
    marketplace = options.get("marketplace", config.EBAY_MARKETPLACE)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    for card in cards:
        q_map = build_card_queries(card, pokemon_set)
        card_listings: list[dict] = []
        seen_ids: set[str] = set()

        for cat, cat_queries in [("RAW", q_map["raw"]), ("PSA9", q_map["psa9"]), ("PSA10", q_map["psa10"])]:
            if cat == "RAW" and not include_raw:
                continue
            if cat == "PSA9" and not include_psa9:
                continue
            if cat == "PSA10" and not include_psa10:
                continue

            for q in cat_queries:
                try:
                    listings = client.search_recent_listings(
                        query=q, marketplace=marketplace,
                        lookback_days=lookback_days,
                        limit=config.EBAY_SEARCH_LIMIT,
                        max_pages=min(config.EBAY_MAX_PAGES, 2),
                        include_auctions=include_auctions,
                    )
                    api_total += len(listings)
                    for l in listings:
                        if l.ebay_item_id in seen_ids:
                            continue
                        seen_ids.add(l.ebay_item_id)
                        if l.listing_date and l.listing_date < cutoff:
                            continue
                        if only_eu:
                            allowed, _ = is_allowed_location(
                                l.location_country,
                                config.EBAY_ALLOWED_COUNTRIES,
                                config.EBAY_EXCLUDED_COUNTRIES,
                                config.EBAY_ALLOW_UNKNOWN_LOCATION,
                            )
                            if not allowed:
                                continue
                        cl = clean_and_classify_listing(l.title)
                        if cl.is_bad_match:
                            continue
                        row = {"listing": l, "category": cat, "query": q}
                        card_listings.append(row)
                        all_results.append({**row, "card_name": card.name, "card_number": card.card_number})
                except Exception as exc:
                    logger.warning("Set smart search query %r failed: %s", q, exc)

        # Per-card summary
        def _prices(cat: str) -> list[float]:
            return [r["listing"].total_price for r in card_listings if r["category"] == cat]

        raw_prices = _prices("RAW")
        psa9_prices = _prices("PSA9")
        psa10_prices = _prices("PSA10")

        best_raw = min((r for r in card_listings if r["category"] == "RAW"),
                       key=lambda r: r["listing"].total_price, default=None)
        best_psa9 = min((r for r in card_listings if r["category"] == "PSA9"),
                        key=lambda r: r["listing"].total_price, default=None)
        best_psa10 = min((r for r in card_listings if r["category"] == "PSA10"),
                         key=lambda r: r["listing"].total_price, default=None)

        card_summaries.append({
            "card": card,
            "raw_count": len(raw_prices),
            "raw_min": min(raw_prices, default=None),
            "psa9_count": len(psa9_prices),
            "psa9_min": min(psa9_prices, default=None),
            "psa10_count": len(psa10_prices),
            "psa10_min": min(psa10_prices, default=None),
            "best_raw_url": best_raw["listing"].url if best_raw else None,
            "best_psa9_url": best_psa9["listing"].url if best_psa9 else None,
            "best_psa10_url": best_psa10["listing"].url if best_psa10 else None,
            "all_listings": card_listings,
        })

    return {
        "card_summaries": card_summaries,
        "all_results": all_results,
        "api_total": api_total,
    }
