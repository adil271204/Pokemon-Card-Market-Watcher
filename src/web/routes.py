"""All dashboard route handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import json

from src import config
from src.database import get_session
from src.deal_scorer import calculate_deal_score
from src.diagnostics import diagnose_listing_against_watchlist, extract_ebay_item_id
from src.ebay_client import EbayClient
from src.listing_cleaner import clean_and_classify_listing
from src.location_filter import is_allowed_location
import csv
import io

from src.models import PokemonCard, PokemonSet, SeenListing, SetScan, SetScanResult, Watchlist
from src.cardlist_importer import fetch_cardlist_url, parse_cardlist_html
from src.set_scanner import build_card_queries, calculate_card_opportunity, run_set_scan
from src.smart_search import build_smart_queries, detect_smart_search_input, run_smart_search, search_set_cards
from src.web import auth, services

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(request: Request, template: str, ctx: dict[str, Any] | None = None) -> HTMLResponse:
    # Starlette 1.x new API: request is the first arg, not in context dict
    context: dict[str, Any] = {
        "flash": auth.pop_flash(request),
        "auth_enabled": auth.is_auth_enabled(),
    }
    if ctx:
        context.update(ctx)
    return templates.TemplateResponse(request=request, name=template, context=context)


def _guard(request: Request) -> RedirectResponse | None:
    return auth.auth_required(request)


def _parse_optional_float(value: str | None) -> float | None:
    if not value or not value.strip():
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def _parse_optional_date(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    if not auth.is_auth_enabled() or request.session.get("logged_in"):
        return RedirectResponse(url="/", status_code=303)
    return _render(request, "login.html")


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    password: Annotated[str, Form()],
) -> RedirectResponse | HTMLResponse:
    if auth.login(request, password):
        return RedirectResponse(url="/", status_code=303)
    auth.set_flash(request, "Falsches Passwort.", "danger")
    return _render(request, "login.html")


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    auth.logout(request)
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, response_model=None)
async def overview(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        stats = services.get_overview_stats(db)
        recent_listings = services.get_recent_listings(db)
        recent_alerts = services.get_recent_alerts(db)
    finally:
        db.close()
    return _render(request, "overview.html", {
        "stats": stats,
        "recent_listings": recent_listings,
        "recent_alerts": recent_alerts,
    })


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


@router.get("/watchlists", response_class=HTMLResponse, response_model=None)
async def watchlists(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        wls = services.get_all_watchlists(db)
    finally:
        db.close()
    return _render(request, "watchlists.html", {
        "watchlists": wls,
        "use_mock_ebay": config.USE_MOCK_EBAY,
    })


@router.get("/watchlists/new", response_class=HTMLResponse, response_model=None)
async def watchlist_new(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    return _render(request, "watchlist_form.html", {"watchlist": None, "errors": []})


@router.post("/watchlists/new", response_model=None)
async def watchlist_create(
    request: Request,
    name: Annotated[str, Form()],
    query: Annotated[str, Form()],
    marketplace: Annotated[str, Form()] = "EBAY_DE",
    max_price: Annotated[str, Form()] = "",
    target_market_price: Annotated[str, Form()] = "",
    min_discount_percent: Annotated[str, Form()] = "15",
    target_grade: Annotated[str, Form()] = "",
    target_language: Annotated[str, Form()] = "",
    enabled: Annotated[str, Form()] = "on",
) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    errors = _validate_watchlist_form(name, query, target_market_price, min_discount_percent)
    if errors:
        return _render(request, "watchlist_form.html", {
            "watchlist": None,
            "errors": errors,
            "form": request,
        })

    db = get_session()
    try:
        services.create_watchlist(db, {
            "name": name.strip(),
            "query": query.strip(),
            "marketplace": marketplace.strip() or "EBAY_DE",
            "max_price": _parse_optional_float(max_price),
            "target_market_price": _parse_optional_float(target_market_price),
            "min_discount_percent": float(min_discount_percent or 15),
            "target_grade": target_grade.strip() or None,
            "target_language": target_language.strip() or None,
            "enabled": enabled == "on",
        })
    finally:
        db.close()

    auth.set_flash(request, f"Watchlist '{name}' erfolgreich erstellt.")
    return RedirectResponse(url="/watchlists", status_code=303)


@router.get("/watchlists/{watchlist_id}/edit", response_class=HTMLResponse, response_model=None)
async def watchlist_edit(request: Request, watchlist_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if not wl:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
            return RedirectResponse(url="/watchlists", status_code=303)
        # Detach from session so template can access attributes after close
        db.expunge(wl)
    finally:
        db.close()
    return _render(request, "watchlist_form.html", {"watchlist": wl, "errors": []})


@router.post("/watchlists/{watchlist_id}/edit", response_model=None)
async def watchlist_update(
    request: Request,
    watchlist_id: int,
    name: Annotated[str, Form()],
    query: Annotated[str, Form()],
    marketplace: Annotated[str, Form()] = "EBAY_DE",
    max_price: Annotated[str, Form()] = "",
    target_market_price: Annotated[str, Form()] = "",
    min_discount_percent: Annotated[str, Form()] = "15",
    target_grade: Annotated[str, Form()] = "",
    target_language: Annotated[str, Form()] = "",
    enabled: Annotated[str, Form()] = "on",
) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    errors = _validate_watchlist_form(name, query, target_market_price, min_discount_percent)
    if errors:
        db = get_session()
        try:
            wl = services.get_watchlist(db, watchlist_id)
            if wl:
                db.expunge(wl)
        finally:
            db.close()
        return _render(request, "watchlist_form.html", {
            "watchlist": wl,
            "errors": errors,
        })

    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if not wl:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
            return RedirectResponse(url="/watchlists", status_code=303)
        services.update_watchlist(db, wl, {
            "name": name.strip(),
            "query": query.strip(),
            "marketplace": marketplace.strip() or "EBAY_DE",
            "max_price": _parse_optional_float(max_price),
            "target_market_price": _parse_optional_float(target_market_price),
            "min_discount_percent": float(min_discount_percent or 15),
            "target_grade": target_grade.strip() or None,
            "target_language": target_language.strip() or None,
            "enabled": enabled == "on",
        })
    finally:
        db.close()

    auth.set_flash(request, f"Watchlist '{name}' gespeichert.")
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/watchlists/{watchlist_id}/toggle", response_model=None)
async def watchlist_toggle(request: Request, watchlist_id: int) -> RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if wl:
            services.toggle_watchlist(db, wl)
            state = "aktiviert" if wl.enabled else "deaktiviert"
            auth.set_flash(request, f"Watchlist '{wl.name}' {state}.")
        else:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
    finally:
        db.close()
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/watchlists/{watchlist_id}/delete", response_model=None)
async def watchlist_delete(request: Request, watchlist_id: int) -> RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if wl:
            name = wl.name
            services.delete_watchlist(db, wl)
            auth.set_flash(request, f"Watchlist '{name}' gelöscht.")
        else:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
    except Exception as exc:
        logger.error("Delete watchlist failed: %s", exc)
        auth.set_flash(request, "Löschen fehlgeschlagen. Bitte prüfe die Logs.", "danger")
    finally:
        db.close()
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/watchlists/{watchlist_id}/test", response_class=HTMLResponse, response_model=None)
async def watchlist_test(request: Request, watchlist_id: int) -> HTMLResponse | RedirectResponse:
    """
    Dry-run a watchlist: fetch listings, classify, score — but do NOT save
    to the database and do NOT send Telegram alerts.
    """
    if redir := _guard(request):
        return redir

    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if not wl:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
            return RedirectResponse(url="/watchlists", status_code=303)
        # Copy values we need after session close
        wl_data = {
            "id": wl.id,
            "name": wl.name,
            "query": wl.query,
            "marketplace": wl.marketplace,
            "max_price": wl.max_price,
            "target_market_price": wl.target_market_price,
            "min_discount_percent": wl.min_discount_percent,
            "target_grade": wl.target_grade,
        }
    finally:
        db.close()

    results: list[dict[str, Any]] = []
    excluded_location: list[dict[str, Any]] = []
    try:
        client = EbayClient()
        raw_listings = client.search_new_listings(
            query=wl_data["query"],
            marketplace=wl_data["marketplace"],
            max_price=wl_data["max_price"],
        )
        for listing in raw_listings:
            loc_allowed, loc_reasons = is_allowed_location(
                listing.location_country,
                config.EBAY_ALLOWED_COUNTRIES,
                config.EBAY_EXCLUDED_COUNTRIES,
                config.EBAY_ALLOW_UNKNOWN_LOCATION,
            )
            if not loc_allowed:
                excluded_location.append({
                    "title": listing.title,
                    "location_country": listing.location_country or "–",
                    "location_city": listing.location_city or "–",
                    "reasons": loc_reasons,
                    "url": listing.url,
                })
                continue

            cl = clean_and_classify_listing(listing.title, target_grade=wl_data["target_grade"])
            deal = calculate_deal_score(
                listing=listing,
                target_market_price=wl_data["target_market_price"],
                min_discount_percent=wl_data["min_discount_percent"],
                classification=cl,
                target_grade=wl_data["target_grade"],
            )
            results.append({
                "title": listing.title,
                "price": listing.price,
                "shipping": listing.shipping,
                "total_price": listing.total_price,
                "currency": listing.currency,
                "url": listing.url,
                "location_country": listing.location_country or "–",
                "location_city": listing.location_city or "–",
                "is_bad_match": cl.is_bad_match,
                "reasons": cl.reasons,
                "grade": f"{cl.grading_company} {cl.grade}" if cl.is_graded else "–",
                "discount_percent": deal.discount_percent,
                "score": deal.score,
                "would_alert": deal.should_alert,
                "deal_reason": deal.reason,
            })
    except Exception as exc:
        logger.error("Test run failed for watchlist %d: %s", watchlist_id, exc)
        auth.set_flash(request, f"Test fehlgeschlagen: {exc}", "danger")
        return RedirectResponse(url="/watchlists", status_code=303)

    return _render(request, "test_results.html", {
        "wl_name": wl_data["name"],
        "watchlist_id": watchlist_id,
        "results": results,
        "excluded_location": excluded_location,
    })


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------


def _run_backfill(
    wl_data: dict[str, Any],
    lookback_days: int,
    db_session: Any,
) -> dict[str, int]:
    """
    Fetch recent listings for one watchlist and persist new ones.
    Returns a stats dict. Never sends Telegram alerts.
    """
    from datetime import datetime, timedelta, timezone

    client = EbayClient()
    raw_listings = client.search_recent_listings(
        query=wl_data["query"],
        marketplace=wl_data["marketplace"],
        max_price=wl_data["max_price"],
        lookback_days=lookback_days,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    stats = {
        "api_total": len(raw_listings),
        "saved": 0,
        "skipped_known": 0,
        "skipped_old": 0,
        "skipped_location": 0,
        "no_url": 0,
        "within_lookback": 0,
    }

    for listing in raw_listings:
        if not listing.url:
            stats["no_url"] += 1

        # Location filter
        allowed, _reasons = is_allowed_location(
            listing.location_country,
            config.EBAY_ALLOWED_COUNTRIES,
            config.EBAY_EXCLUDED_COUNTRIES,
            config.EBAY_ALLOW_UNKNOWN_LOCATION,
        )
        if not allowed:
            stats["skipped_location"] += 1
            continue

        # Date check
        if listing.listing_date is not None and listing.listing_date < cutoff:
            stats["skipped_old"] += 1
            continue
        else:
            stats["within_lookback"] += 1

        # Dedup
        exists = (
            db_session.query(SeenListing)
            .filter_by(ebay_item_id=listing.ebay_item_id)
            .first()
        )
        if exists:
            stats["skipped_known"] += 1
            continue

        seen = SeenListing(
            ebay_item_id=listing.ebay_item_id,
            watchlist_id=wl_data["id"],
            title=listing.title,
            price=listing.price,
            shipping=listing.shipping,
            total_price=listing.total_price,
            currency=listing.currency,
            url=listing.url,
            image_url=listing.image_url,
            condition=listing.condition,
            listing_date=listing.listing_date,
            item_creation_date=listing.item_creation_date,
            item_origin_date=listing.item_origin_date,
            location_country=listing.location_country,
            location_city=listing.location_city,
            location_postal_code=listing.location_postal_code,
            location_state=listing.location_state,
            location_raw_json=json.dumps(listing.location_raw) if listing.location_raw else None,
            raw_payload_json=json.dumps(listing.raw),
        )
        db_session.add(seen)
        stats["saved"] += 1

    db_session.commit()
    return stats


# ---------------------------------------------------------------------------
# Backfill routes
# ---------------------------------------------------------------------------


@router.post("/watchlists/{watchlist_id}/backfill", response_class=HTMLResponse, response_model=None)
async def watchlist_backfill(request: Request, watchlist_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    lookback_days = config.EBAY_LOOKBACK_DAYS

    db = get_session()
    try:
        wl = services.get_watchlist(db, watchlist_id)
        if not wl:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
            return RedirectResponse(url="/watchlists", status_code=303)
        wl_data = {
            "id": wl.id,
            "name": wl.name,
            "query": wl.query,
            "marketplace": wl.marketplace,
            "max_price": wl.max_price,
            "target_market_price": wl.target_market_price,
            "min_discount_percent": wl.min_discount_percent,
            "target_grade": wl.target_grade,
        }
    finally:
        db.close()

    db2 = get_session()
    try:
        stats = _run_backfill(wl_data, lookback_days, db2)
    except Exception as exc:
        logger.error("Backfill failed for watchlist %d: %s", watchlist_id, exc)
        auth.set_flash(request, f"Backfill fehlgeschlagen: {exc}", "danger")
        return RedirectResponse(url="/watchlists", status_code=303)
    finally:
        db2.close()

    return _render(request, "backfill_results.html", {
        "wl_name": wl_data["name"],
        "watchlist_id": watchlist_id,
        "lookback_days": lookback_days,
        "stats": stats,
    })


@router.post("/backfill/recent", response_class=HTMLResponse, response_model=None)
async def backfill_all(request: Request) -> HTMLResponse | RedirectResponse:
    """Global backfill: run for all active watchlists."""
    if redir := _guard(request):
        return redir

    lookback_days = config.EBAY_LOOKBACK_DAYS

    db = get_session()
    try:
        watchlists = db.query(Watchlist).filter_by(enabled=True).all()
        wl_list = [
            {
                "id": wl.id,
                "name": wl.name,
                "query": wl.query,
                "marketplace": wl.marketplace,
                "max_price": wl.max_price,
                "target_market_price": wl.target_market_price,
                "min_discount_percent": wl.min_discount_percent,
                "target_grade": wl.target_grade,
            }
            for wl in watchlists
        ]
    finally:
        db.close()

    results: list[dict[str, Any]] = []
    for wl_data in wl_list:
        db2 = get_session()
        try:
            stats = _run_backfill(wl_data, lookback_days, db2)
            results.append({"name": wl_data["name"], **stats})
        except Exception as exc:
            logger.error("Global backfill failed for watchlist %r: %s", wl_data["name"], exc)
            results.append({"name": wl_data["name"], "error": str(exc)})
        finally:
            db2.close()

    return _render(request, "backfill_results.html", {
        "wl_name": None,
        "lookback_days": lookback_days,
        "results": results,
    })


# ---------------------------------------------------------------------------
# Listings – soft delete
# ---------------------------------------------------------------------------


@router.post("/listings/{listing_id}/delete", response_model=None)
async def listing_soft_delete(request: Request, listing_id: int) -> RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        result = services.soft_delete_listing(db, listing_id)
        if result:
            auth.set_flash(request, "Listing ausgeblendet.")
        else:
            auth.set_flash(request, "Listing nicht gefunden.", "warning")
    except Exception as exc:
        logger.error("Soft delete listing %d failed: %s", listing_id, exc)
        auth.set_flash(request, f"Fehler: {exc}", "danger")
    finally:
        db.close()

    return_url = (await request.form()).get("return_url") or "/listings"
    return RedirectResponse(url=str(return_url), status_code=303)


@router.post("/listings/delete-visible", response_model=None)
async def listings_delete_visible(request: Request) -> RedirectResponse:
    if redir := _guard(request):
        return redir
    form = await request.form()
    raw_ids = form.getlist("listing_ids")
    return_url = form.get("return_url") or "/listings"

    if not raw_ids:
        auth.set_flash(request, "Keine Listings ausgewählt.", "warning")
        return RedirectResponse(url=str(return_url), status_code=303)

    listing_ids = [int(i) for i in raw_ids if str(i).isdigit()]
    db = get_session()
    try:
        count = services.soft_delete_listings(db, listing_ids)
        auth.set_flash(request, f"{count} Listing(s) ausgeblendet.")
    except Exception as exc:
        logger.error("Bulk soft delete failed: %s", exc)
        auth.set_flash(request, f"Fehler: {exc}", "danger")
    finally:
        db.close()

    return RedirectResponse(url=str(return_url), status_code=303)


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


@router.get("/listings", response_class=HTMLResponse, response_model=None)
async def listings(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    params = request.query_params
    watchlist_id = int(params["watchlist_id"]) if params.get("watchlist_id") else None
    title_search = params.get("title") or None
    price_min = _parse_optional_float(params.get("price_min"))
    price_max = _parse_optional_float(params.get("price_max"))
    date_from = _parse_optional_date(params.get("date_from"))
    date_to = _parse_optional_date(params.get("date_to"))
    sort = params.get("sort", "first_seen_at")
    page = max(1, int(params.get("page", 1)))
    per_page = 50

    db = get_session()
    try:
        items, total = services.get_listings(
            db,
            watchlist_id=watchlist_id,
            title_search=title_search,
            price_min=price_min,
            price_max=price_max,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            page=page,
            per_page=per_page,
        )
        watchlists_all = services.get_all_watchlists(db)
        # Build watchlist id→name map
        wl_map = {wl.id: wl.name for wl in watchlists_all}
    finally:
        db.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return _render(request, "listings.html", {
        "items": items,
        "wl_map": wl_map,
        "watchlists": watchlists_all,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "per_page": per_page,
        "filters": {
            "watchlist_id": watchlist_id or "",
            "title": title_search or "",
            "price_min": price_min or "",
            "price_max": price_max or "",
            "date_from": params.get("date_from", ""),
            "date_to": params.get("date_to", ""),
            "sort": sort,
        },
    })


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts", response_class=HTMLResponse, response_model=None)
async def alerts(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    params = request.query_params
    watchlist_id = int(params["watchlist_id"]) if params.get("watchlist_id") else None
    min_score = _parse_optional_float(params.get("min_score"))
    min_discount = _parse_optional_float(params.get("min_discount"))
    date_from = _parse_optional_date(params.get("date_from"))
    date_to = _parse_optional_date(params.get("date_to"))
    page = max(1, int(params.get("page", 1)))
    per_page = 50

    db = get_session()
    try:
        items, total = services.get_alerts(
            db,
            watchlist_id=watchlist_id,
            min_score=min_score,
            min_discount=min_discount,
            date_from=date_from,
            date_to=date_to,
            page=page,
            per_page=per_page,
        )
        kpis = services.get_alert_kpis(db)
        watchlists_all = services.get_all_watchlists(db)
        wl_map = {wl.id: wl.name for wl in watchlists_all}
    finally:
        db.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return _render(request, "alerts.html", {
        "items": items,
        "kpis": kpis,
        "wl_map": wl_map,
        "watchlists": watchlists_all,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "filters": {
            "watchlist_id": watchlist_id or "",
            "min_score": min_score or "",
            "min_discount": min_discount or "",
            "date_from": params.get("date_from", ""),
            "date_to": params.get("date_to", ""),
        },
    })


# ---------------------------------------------------------------------------
# Restore soft-deleted listing
# ---------------------------------------------------------------------------


@router.post("/listings/{listing_id}/restore", response_model=None)
async def listing_restore(request: Request, listing_id: int) -> RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        result = services.restore_listing(db, listing_id)
        if result:
            auth.set_flash(request, "Listing wiederhergestellt.")
        else:
            auth.set_flash(request, "Listing nicht gefunden.", "warning")
    finally:
        db.close()
    form = await request.form()
    return_url = form.get("return_url") or "/listings"
    return RedirectResponse(url=str(return_url), status_code=303)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@router.get("/diagnostics", response_class=HTMLResponse, response_model=None)
async def diagnostics_page(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        watchlists_all = services.get_all_watchlists(db)
    finally:
        db.close()
    return _render(request, "diagnostics.html", {
        "watchlists": watchlists_all,
        "result": None,
    })


@router.post("/diagnostics/listing", response_class=HTMLResponse, response_model=None)
async def diagnostics_listing(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    raw_input = (form.get("item_input") or "").strip()
    watchlist_id = form.get("watchlist_id") or None

    db = get_session()
    try:
        watchlists_all = services.get_all_watchlists(db)
        selected_wl = None
        if watchlist_id:
            selected_wl = services.get_watchlist(db, int(watchlist_id))

        if not raw_input:
            auth.set_flash(request, "Bitte eine eBay-URL oder Item-ID eingeben.", "warning")
            return _render(request, "diagnostics.html", {
                "watchlists": watchlists_all, "result": None,
            })

        item_id = extract_ebay_item_id(raw_input)
        result: dict[str, Any] = {"item_id": item_id, "raw_input": raw_input}

        # 1 – API lookup
        try:
            client = EbayClient()
            listing = client.get_item_by_id(item_id, marketplace=config.EBAY_MARKETPLACE)
        except Exception as exc:
            result["api_error"] = str(exc)
            listing = None

        result["api_found"] = listing is not None
        result["listing"] = listing

        # 2 – DB check (search by ebay_item_id AND by numeric part)
        numeric_id = item_id.split("|")[1] if "|" in item_id else item_id
        db_listing = (
            db.query(SeenListing)
            .filter(
                (SeenListing.ebay_item_id == item_id) |
                (SeenListing.ebay_item_id == numeric_id)
            )
            .first()
        )
        result["db_listing"] = db_listing
        result["db_found"] = db_listing is not None
        result["is_deleted"] = db_listing is not None and db_listing.deleted_at is not None

        # 3 – Filter diagnosis (only if API found and watchlist selected)
        if listing and selected_wl:
            result["diagnosis"] = diagnose_listing_against_watchlist(
                listing, selected_wl, db_listing
            )
        result["selected_watchlist"] = selected_wl

    finally:
        db.close()

    return _render(request, "diagnostics.html", {
        "watchlists": watchlists_all,
        "result": result,
        "last_input": raw_input,
        "last_watchlist_id": watchlist_id or "",
    })


@router.post("/diagnostics/watchlist-search", response_class=HTMLResponse, response_model=None)
async def diagnostics_watchlist_search(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    watchlist_id = form.get("watchlist_id") or None

    db = get_session()
    try:
        watchlists_all = services.get_all_watchlists(db)
        if not watchlist_id:
            auth.set_flash(request, "Bitte eine Watchlist auswählen.", "warning")
            return _render(request, "diagnostics.html", {
                "watchlists": watchlists_all, "result": None,
            })
        wl = services.get_watchlist(db, int(watchlist_id))
        if not wl:
            auth.set_flash(request, "Watchlist nicht gefunden.", "warning")
            return _render(request, "diagnostics.html", {
                "watchlists": watchlists_all, "result": None,
            })
        wl_data = {
            "id": wl.id, "name": wl.name, "query": wl.query,
            "marketplace": wl.marketplace, "max_price": wl.max_price,
            "target_market_price": wl.target_market_price,
            "min_discount_percent": wl.min_discount_percent,
            "target_grade": wl.target_grade,
        }
    finally:
        db.close()

    debug_rows: list[dict[str, Any]] = []
    search_meta: dict[str, Any] = {
        "query": wl_data["query"],
        "marketplace": wl_data["marketplace"],
        "max_price": wl_data["max_price"],
        "limit": config.EBAY_SEARCH_LIMIT,
        "max_pages": config.EBAY_MAX_PAGES,
        "lookback_days": config.EBAY_LOOKBACK_DAYS,
    }
    stats = {"api_total": 0, "location_ok": 0, "location_blocked": 0, "saved": 0, "known": 0}

    try:
        client = EbayClient()
        raw_listings = client.search_recent_listings(
            query=wl_data["query"],
            marketplace=wl_data["marketplace"],
            max_price=wl_data["max_price"],
            lookback_days=config.EBAY_LOOKBACK_DAYS,
        )
        stats["api_total"] = len(raw_listings)

        db2 = get_session()
        try:
            for listing in raw_listings:
                loc_ok, loc_reasons = is_allowed_location(
                    listing.location_country,
                    config.EBAY_ALLOWED_COUNTRIES,
                    config.EBAY_EXCLUDED_COUNTRIES,
                    config.EBAY_ALLOW_UNKNOWN_LOCATION,
                )
                if loc_ok:
                    stats["location_ok"] += 1
                else:
                    stats["location_blocked"] += 1

                exists = db2.query(SeenListing).filter_by(
                    ebay_item_id=listing.ebay_item_id
                ).first()
                if exists:
                    status = "bekannt"
                    stats["known"] += 1
                elif not loc_ok:
                    status = "location_blocked"
                else:
                    status = "neu"
                    stats["saved"] += 1

                debug_rows.append({
                    "title": listing.title,
                    "price": listing.price,
                    "shipping": listing.shipping,
                    "total_price": listing.total_price,
                    "currency": listing.currency,
                    "location_country": listing.location_country or "–",
                    "location_city": listing.location_city or "–",
                    "url": listing.url,
                    "status": status,
                    "loc_reasons": loc_reasons,
                    "buying_options": listing.raw.get("buyingOptions") or [],
                })
        finally:
            db2.close()
    except Exception as exc:
        search_meta["error"] = str(exc)

    return _render(request, "diagnostics.html", {
        "watchlists": watchlists_all,
        "result": None,
        "search_debug": {
            "meta": search_meta,
            "stats": stats,
            "rows": debug_rows[:50],
            "wl_name": wl_data["name"],
        },
        "last_watchlist_id": watchlist_id or "",
    })


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics", response_class=HTMLResponse, response_model=None)
async def analytics(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    db = get_session()
    try:
        data = services.get_analytics(db)
    finally:
        db.close()
    return _render(request, "analytics.html", {"data": data})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse, response_model=None)
async def settings(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    def _mask(value: str | None) -> str:
        if not value:
            return "–"
        if len(value) <= 6:
            return "***"
        return value[:3] + "***" + value[-3:]

    raw_db = config.DATABASE_URL or ""
    masked_db = _mask(raw_db[:30]) if raw_db else "–"

    cfg = {
        "DATABASE_URL": masked_db,
        # eBay
        "USE_MOCK_EBAY": str(config.USE_MOCK_EBAY),
        "EBAY_ENV": config.EBAY_ENV,
        "EBAY_MARKETPLACE": config.EBAY_MARKETPLACE,
        "EBAY_SEARCH_LIMIT": str(config.EBAY_SEARCH_LIMIT),
        "EBAY_LOOKBACK_DAYS": str(config.EBAY_LOOKBACK_DAYS),
        "EBAY_MAX_PAGES": str(config.EBAY_MAX_PAGES),
        "EBAY_ALLOWED_COUNTRIES": ", ".join(sorted(config.EBAY_ALLOWED_COUNTRIES)),
        "EBAY_EXCLUDED_COUNTRIES": ", ".join(sorted(config.EBAY_EXCLUDED_COUNTRIES)),
        "EBAY_ALLOW_UNKNOWN_LOCATION": str(config.EBAY_ALLOW_UNKNOWN_LOCATION),
        "EBAY_CLIENT_ID": "✓ gesetzt" if config.EBAY_CLIENT_ID else "✗ fehlt",
        "EBAY_CLIENT_SECRET": "✓ gesetzt" if config.EBAY_CLIENT_SECRET else "✗ fehlt",
        # Telegram
        "TELEGRAM_BOT_TOKEN": "✓ gesetzt" if config.TELEGRAM_BOT_TOKEN else "✗ fehlt",
        "TELEGRAM_CHAT_ID": "✓ gesetzt" if config.TELEGRAM_CHAT_ID else "✗ fehlt",
        # Auth
        "DASHBOARD_PASSWORD": "✓ gesetzt" if config.DASHBOARD_PASSWORD else "✗ fehlt",
        "SESSION_SECRET": "✓ gesetzt" if config.SESSION_SECRET_IS_SET else "⚠ Nur Fallback – nicht sicher für Production!",
        "LOG_LEVEL": config.LOG_LEVEL,
    }
    warnings: list[str] = []
    if not config.DASHBOARD_PASSWORD:
        warnings.append("DASHBOARD_PASSWORD ist nicht gesetzt – das Dashboard ist ohne Passwort erreichbar!")
    if not config.SESSION_SECRET_IS_SET:
        warnings.append("SESSION_SECRET ist nicht gesetzt – Sessions werden bei jedem Neustart ungültig.")
    if not config.USE_MOCK_EBAY and not config.EBAY_KEYS_SET:
        warnings.append(
            "USE_MOCK_EBAY=false, aber EBAY_CLIENT_ID oder EBAY_CLIENT_SECRET fehlen! "
            "Der Watcher wird beim Start abstürzen. Bitte Zugangsdaten im eBay Developer Portal anlegen."
        )
    if not config.USE_MOCK_EBAY and config.EBAY_ENV == "sandbox":
        warnings.append("EBAY_ENV=sandbox – du nutzt die eBay Sandbox, keine echten Listings!")
    if "GB" not in config.EBAY_EXCLUDED_COUNTRIES:
        warnings.append(
            "GB/UK ist nicht in EBAY_EXCLUDED_COUNTRIES – UK-Listings werden nicht gefiltert. "
            "Empfehlung: GB hinzufügen, um Einfuhrabgaben-Probleme zu vermeiden."
        )

    return _render(request, "settings.html", {
        "cfg": cfg,
        "warnings": warnings,
        "use_mock_ebay": config.USE_MOCK_EBAY,
        "ebay_keys_set": config.EBAY_KEYS_SET,
    })


# ---------------------------------------------------------------------------
# Form validation helper
# ---------------------------------------------------------------------------


def _validate_watchlist_form(
    name: str,
    query: str,
    target_market_price: str,
    min_discount_percent: str,
) -> list[str]:
    errors: list[str] = []
    if not name.strip():
        errors.append("Name darf nicht leer sein.")
    if not query.strip():
        errors.append("Query darf nicht leer sein.")
    if target_market_price.strip():
        try:
            v = float(target_market_price)
            if v <= 0:
                errors.append("Target Market Price muss größer als 0 sein.")
        except ValueError:
            errors.append("Target Market Price muss eine Zahl sein.")
    if min_discount_percent.strip():
        try:
            v = float(min_discount_percent)
            if not (0 <= v <= 100):
                errors.append("Minimum Discount muss zwischen 0 und 100 liegen.")
        except ValueError:
            errors.append("Minimum Discount muss eine Zahl sein.")
    return errors


# ---------------------------------------------------------------------------
# Sets – list & detail
# ---------------------------------------------------------------------------


@router.get("/sets", response_class=HTMLResponse, response_model=None)
async def sets_list(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    with get_session() as db:
        sets = db.query(PokemonSet).order_by(PokemonSet.name).all()
        sets_data = [
            {
                "id": s.id,
                "name": s.name,
                "code": s.code,
                "language": s.language,
                "total_cards": s.total_cards or db.query(PokemonCard).filter_by(set_id=s.id).count(),
                "last_scan": (
                    db.query(SetScan)
                    .filter_by(set_id=s.id)
                    .order_by(SetScan.started_at.desc())
                    .first()
                ),
            }
            for s in sets
        ]
    return _render(request, "sets.html", {"sets": sets_data})


@router.get("/sets/import", response_class=HTMLResponse, response_model=None)
async def sets_import_form(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    return _render(request, "set_import.html", {})


@router.post("/sets/import", response_model=None)
async def sets_import_submit(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    upload = form.get("csv_file")

    if not upload or not hasattr(upload, "read"):
        auth.set_flash(request, "Bitte eine CSV-Datei hochladen.", "danger")
        return RedirectResponse(url="/sets/import", status_code=303)

    content = await upload.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    required_cols = {"set_name", "set_code", "language", "card_name", "card_number"}

    rows = list(reader)
    if not rows:
        auth.set_flash(request, "CSV ist leer.", "danger")
        return RedirectResponse(url="/sets/import", status_code=303)

    missing = required_cols - set(rows[0].keys())
    if missing:
        auth.set_flash(request, f"Fehlende Spalten: {', '.join(missing)}", "danger")
        return RedirectResponse(url="/sets/import", status_code=303)

    sets_created = 0
    cards_created = 0
    cards_skipped = 0

    with get_session() as db:
        for row in rows:
            set_name = row.get("set_name", "").strip()
            set_code = row.get("set_code", "").strip().lower()
            language = row.get("language", "EN").strip().upper()
            card_name = row.get("card_name", "").strip()
            card_number = row.get("card_number", "").strip()
            rarity = row.get("rarity", "").strip()
            variant = row.get("variant", "normal").strip()

            if not set_code or not card_name or not card_number:
                continue

            # Upsert set
            pset = db.query(PokemonSet).filter_by(code=set_code, language=language).first()
            if not pset:
                pset = PokemonSet(name=set_name, code=set_code, language=language)
                db.add(pset)
                db.flush()
                sets_created += 1

            # Deduplicate card by set_id + language + card_number
            existing = db.query(PokemonCard).filter_by(
                set_id=pset.id, card_number=card_number, language=language
            ).first()
            if existing:
                cards_skipped += 1
                continue

            card = PokemonCard(
                set_id=pset.id,
                name=card_name,
                card_number=card_number,
                rarity=rarity or None,
                language=language,
                variant=variant or "normal",
                is_secret=_is_secret_card(card_number),
            )
            db.add(card)
            cards_created += 1

        db.commit()

    auth.set_flash(
        request,
        f"Import abgeschlossen: {sets_created} Set(s) angelegt, {cards_created} Karten importiert, {cards_skipped} bereits vorhanden.",
        "success",
    )
    return RedirectResponse(url="/sets", status_code=303)


def _is_secret_card(card_number: str) -> bool:
    """Heuristic: card number exceeds set total (e.g. 166/165)."""
    parts = card_number.split("/")
    if len(parts) == 2:
        try:
            return int(parts[0]) > int(parts[1])
        except ValueError:
            pass
    return False


@router.get("/sets/import-url", response_class=HTMLResponse, response_model=None)
async def sets_import_url_form(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    return _render(request, "set_import_url.html", {})


@router.post("/sets/import-url/preview", response_model=None)
async def sets_import_url_preview(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    url = (form.get("url") or "").strip()
    set_name_override = (form.get("set_name") or "").strip()
    set_code_override = (form.get("set_code") or "").strip().lower()
    language = (form.get("language") or "EN").strip().upper()
    source_name = (form.get("source_name") or "").strip()

    if not url:
        auth.set_flash(request, "Bitte eine URL eingeben.", "danger")
        return _render(request, "set_import_url.html", {"form_data": dict(form)})

    try:
        html = fetch_cardlist_url(url)
    except ValueError as exc:
        return _render(request, "set_import_url.html", {"error": str(exc), "form_data": dict(form)})

    try:
        result = parse_cardlist_html(html, url)
    except Exception as exc:
        logger.error("parse_cardlist_html failed: %s", exc, exc_info=True)
        return _render(request, "set_import_url.html", {"error": f"Parser-Fehler: {exc}", "form_data": dict(form)})

    if set_name_override:
        result["set_name_detected"] = set_name_override
    if set_code_override:
        result["set_code_detected"] = set_code_override
    for c in result["cards"]:
        c["language"] = language

    return _render(request, "set_import_url_preview.html", {
        "result": result,
        "url": url,
        "set_name": result["set_name_detected"],
        "set_code": result["set_code_detected"],
        "language": language,
        "source_name": source_name,
    })


@router.post("/sets/import-url/confirm", response_model=None)
async def sets_import_url_confirm(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    url = (form.get("url") or "").strip()
    set_name = (form.get("set_name") or "").strip()
    set_code = (form.get("set_code") or "").strip().lower()
    language = (form.get("language") or "EN").strip().upper()
    source_name = (form.get("source_name") or "").strip()

    if not set_name or not set_code:
        auth.set_flash(request, "Set Name und Set Code sind Pflichtfelder.", "danger")
        return RedirectResponse(url="/sets/import-url", status_code=303)

    selected_indices = [k[len("selected_"):] for k in form.keys() if k.startswith("selected_")]
    cards_to_import: list[dict] = []
    for idx in selected_indices:
        card_name = (form.get(f"card_name_{idx}") or "").strip()
        card_number = (form.get(f"card_number_{idx}") or "").strip()
        rarity = (form.get(f"rarity_{idx}") or "").strip()
        variant = (form.get(f"variant_{idx}") or "normal").strip()
        confidence = float(form.get(f"confidence_{idx}") or 0.5)
        raw_text = (form.get(f"raw_text_{idx}") or "").strip()
        if card_name and card_number:
            cards_to_import.append({
                "card_name": card_name, "card_number": card_number,
                "rarity": rarity, "variant": variant, "language": language,
                "confidence": confidence, "raw_text": raw_text,
            })

    if not cards_to_import:
        auth.set_flash(request, "Keine Karten zum Importieren ausgewählt.", "warning")
        return RedirectResponse(url="/sets/import-url", status_code=303)

    cards_created = cards_skipped = cards_updated = 0
    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(code=set_code, language=language).first()
        if not pset:
            pset = PokemonSet(name=set_name, code=set_code, language=language,
                              source_url=url or None, source_name=source_name or None)
            db.add(pset)
            db.flush()
        else:
            if url and not pset.source_url:
                pset.source_url = url
            if source_name and not pset.source_name:
                pset.source_name = source_name

        for c in cards_to_import:
            existing = db.query(PokemonCard).filter_by(
                set_id=pset.id, card_number=c["card_number"], language=language).first()
            if existing:
                if existing.name != c["card_name"]:
                    existing.name = c["card_name"]
                    existing.rarity = c["rarity"] or existing.rarity
                    existing.source_raw_text = c["raw_text"] or existing.source_raw_text
                    existing.import_confidence = c["confidence"]
                    cards_updated += 1
                else:
                    cards_skipped += 1
            else:
                db.add(PokemonCard(
                    set_id=pset.id, name=c["card_name"], card_number=c["card_number"],
                    rarity=c["rarity"] or None, language=language,
                    variant=c["variant"] or "normal", is_secret=_is_secret_card(c["card_number"]),
                    source_raw_text=c["raw_text"] or None, import_confidence=c["confidence"],
                ))
                cards_created += 1

        db.commit()
        set_id = pset.id

    auth.set_flash(request,
        f"Import abgeschlossen: {cards_created} neu, {cards_updated} aktualisiert, {cards_skipped} übersprungen.",
        "success")
    return RedirectResponse(url=f"/sets/{set_id}", status_code=303)


@router.get("/sets/{set_id}", response_class=HTMLResponse, response_model=None)
async def set_detail(request: Request, set_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(id=set_id).first()
        if not pset:
            auth.set_flash(request, "Set nicht gefunden.", "danger")
            return RedirectResponse(url="/sets", status_code=303)
        cards = (
            db.query(PokemonCard)
            .filter_by(set_id=set_id)
            .order_by(PokemonCard.card_number)
            .all()
        )
        last_scan = (
            db.query(SetScan)
            .filter_by(set_id=set_id)
            .order_by(SetScan.started_at.desc())
            .first()
        )
        set_data = {
            "id": pset.id,
            "name": pset.name,
            "code": pset.code,
            "language": pset.language,
            "total_cards": pset.total_cards or len(cards),
            "created_at": pset.created_at,
        }
        cards_data = [
            {
                "id": c.id,
                "name": c.name,
                "card_number": c.card_number,
                "rarity": c.rarity,
                "variant": c.variant,
                "is_secret": c.is_secret,
                "search_name": c.search_name,
            }
            for c in cards
        ]
    return _render(request, "set_detail.html", {
        "set": set_data,
        "cards": cards_data,
        "last_scan": last_scan,
    })


# ---------------------------------------------------------------------------
# Set Scan
# ---------------------------------------------------------------------------


@router.post("/sets/{set_id}/scan", response_model=None)
async def set_scan(request: Request, set_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(id=set_id).first()
        if not pset:
            auth.set_flash(request, "Set nicht gefunden.", "danger")
            return RedirectResponse(url="/sets", status_code=303)

        try:
            scan = run_set_scan(db, pset)
            auth.set_flash(
                request,
                f"Scan abgeschlossen: {scan.cards_scanned} Karten, {scan.listings_found} Listings gefunden, {scan.listings_saved} nach Filter.",
                "success",
            )
        except Exception as exc:
            logger.error("Set scan failed: %s", exc, exc_info=True)
            auth.set_flash(request, f"Scan-Fehler: {exc}", "danger")

    return RedirectResponse(url=f"/sets/{set_id}/scan-results", status_code=303)


# ---------------------------------------------------------------------------
# Scan Results
# ---------------------------------------------------------------------------


@router.get("/sets/{set_id}/scan-results", response_class=HTMLResponse, response_model=None)
async def set_scan_results(request: Request, set_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    # Filter params
    only_raw = request.query_params.get("only_raw", "")
    only_psa10 = request.query_params.get("only_psa10", "")
    only_positive_roi = request.query_params.get("only_positive_roi", "")
    rating_filter = request.query_params.get("rating", "")
    min_listings = _parse_optional_float(request.query_params.get("min_listings"))

    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(id=set_id).first()
        if not pset:
            auth.set_flash(request, "Set nicht gefunden.", "danger")
            return RedirectResponse(url="/sets", status_code=303)

        last_scan = (
            db.query(SetScan)
            .filter_by(set_id=set_id, status="done")
            .order_by(SetScan.finished_at.desc())
            .first()
        )

        results: list[dict] = []
        if last_scan:
            rows = (
                db.query(SetScanResult, PokemonCard)
                .join(PokemonCard, SetScanResult.pokemon_card_id == PokemonCard.id)
                .filter(SetScanResult.set_scan_id == last_scan.id)
                .all()
            )
            for r, c in rows:
                reasons = json.loads(r.reasons_json) if r.reasons_json else []
                row_dict = {
                    "id": r.id,
                    "card_id": c.id,
                    "card_name": c.name,
                    "card_number": c.card_number,
                    "rarity": c.rarity,
                    "raw_median": r.raw_median_price,
                    "raw_min": r.raw_min_price,
                    "raw_count": r.raw_listing_count,
                    "psa9_median": r.psa9_median_price,
                    "psa9_count": r.psa9_listing_count,
                    "psa10_median": r.psa10_median_price,
                    "psa10_count": r.psa10_listing_count,
                    "psa10_mult": r.psa10_multiplier,
                    "psa9_mult": r.psa9_multiplier,
                    "expected_profit": r.expected_profit,
                    "roi_percent": r.roi_percent,
                    "score": r.score,
                    "rating": r.rating,
                    "reasons": reasons,
                }
                # Apply filters
                if only_raw and not r.raw_listing_count:
                    continue
                if only_psa10 and not r.psa10_listing_count:
                    continue
                if only_positive_roi and (r.roi_percent is None or r.roi_percent <= 0):
                    continue
                if rating_filter and r.rating != rating_filter:
                    continue
                if min_listings and (r.raw_listing_count or 0) < min_listings:
                    continue
                results.append(row_dict)

            results.sort(key=lambda x: x["score"], reverse=True)

    all_ratings = ["Sehr interessant", "Interessant", "Riskant", "Nur bei PSA 10 interessant", "Nicht attraktiv", "Zu wenig Daten"]
    return _render(request, "set_scan_results.html", {
        "set": {"id": set_id, "name": pset.name if pset else ""},
        "last_scan": last_scan,
        "results": results,
        "filters": {
            "only_raw": only_raw,
            "only_psa10": only_psa10,
            "only_positive_roi": only_positive_roi,
            "rating": rating_filter,
            "min_listings": min_listings,
        },
        "all_ratings": all_ratings,
    })


# ---------------------------------------------------------------------------
# Card Analysis
# ---------------------------------------------------------------------------


@router.get("/sets/{set_id}/cards/{card_id}/analysis", response_class=HTMLResponse, response_model=None)
async def card_analysis(request: Request, set_id: int, card_id: int) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    with get_session() as db:
        pset = db.query(PokemonSet).filter_by(id=set_id).first()
        card = db.query(PokemonCard).filter_by(id=card_id, set_id=set_id).first()
        if not pset or not card:
            auth.set_flash(request, "Karte oder Set nicht gefunden.", "danger")
            return RedirectResponse(url=f"/sets/{set_id}", status_code=303)

        # Latest scan result for this card
        scan_result = (
            db.query(SetScanResult)
            .join(SetScan, SetScanResult.set_scan_id == SetScan.id)
            .filter(
                SetScanResult.pokemon_card_id == card_id,
                SetScan.set_id == set_id,
                SetScan.status == "done",
            )
            .order_by(SetScan.finished_at.desc())
            .first()
        )

        queries = build_card_queries(card, pset)
        reasons = json.loads(scan_result.reasons_json) if scan_result and scan_result.reasons_json else []

    return _render(request, "set_card_analysis.html", {
        "set": {"id": pset.id, "name": pset.name},
        "card": card,
        "scan_result": scan_result,
        "queries": queries,
        "reasons": reasons,
        "grading_config": {
            "cost": config.GRADING_COST,
            "shipping_to": config.GRADING_SHIPPING_TO_GRADER,
            "return_shipping": config.GRADING_RETURN_SHIPPING,
            "fee_pct": config.GRADING_MARKETPLACE_FEE_PERCENT,
            "psa10_prob": config.GRADING_PSA10_PROBABILITY,
            "psa9_prob": config.GRADING_PSA9_PROBABILITY,
        },
    })


# ---------------------------------------------------------------------------
# Smart Search
# ---------------------------------------------------------------------------



@router.get("/smart-search", response_class=HTMLResponse, response_model=None)
async def smart_search_get(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir
    return _render(request, "smart_search.html", {"result": None})


@router.post("/smart-search", response_model=None)
async def smart_search_post(request: Request) -> HTMLResponse | RedirectResponse:
    if redir := _guard(request):
        return redir

    form = await request.form()
    input_value = (form.get("input_value") or "").strip()
    search_mode = (form.get("search_mode") or "auto").lower()
    lookback_hours = int(form.get("lookback_hours") or 24)
    include_raw = form.get("include_raw") == "on"
    include_psa9 = form.get("include_psa9") == "on"
    include_psa10 = form.get("include_psa10") == "on"
    include_auctions = form.get("include_auctions") == "on"
    only_eu = form.get("only_eu") == "on"
    max_results = int(form.get("max_results_per_query") or 50)

    options = {
        "lookback_hours": lookback_hours,
        "include_raw": include_raw,
        "include_psa9": include_psa9,
        "include_psa10": include_psa10,
        "include_auctions": include_auctions,
        "only_eu": only_eu,
        "max_results_per_query": max_results,
    }

    if not input_value:
        return _render(request, "smart_search.html", {
            "result": None,
            "error": "Bitte einen Suchbegriff eingeben.",
            "form": dict(form),
        })

    parsed = detect_smart_search_input(input_value)

    # Override type if user selected explicit mode
    if search_mode == "set":
        parsed["type"] = "set"
    elif search_mode == "einzelkarte":
        parsed["type"] = "card"

    result_data: dict[str, Any] = {
        "parsed": parsed,
        "options": options,
        "input_value": input_value,
        "set_not_imported": False,
        "set_summaries": None,
        "listings": [],
        "api_total": 0,
        "after_filter": 0,
        "queries_run": [],
        "errors": [],
        "form": dict(form),
    }

    # --- Set search ---
    if parsed["type"] == "set":
        set_name_or_code = parsed.get("detected_set_name") or input_value
        with get_session() as db:
            # Try to find by code first, then by name (case-insensitive)
            pset = (
                db.query(PokemonSet)
                .filter(PokemonSet.code == set_name_or_code.lower())
                .first()
            )
            if not pset:
                pset = (
                    db.query(PokemonSet)
                    .filter(PokemonSet.name.ilike(f"%{set_name_or_code}%"))
                    .first()
                )

            if not pset:
                result_data["set_not_imported"] = True
                result_data["set_search_term"] = set_name_or_code
            else:
                cards = db.query(PokemonCard).filter_by(set_id=pset.id).order_by(PokemonCard.card_number).all()
                if not cards:
                    result_data["set_not_imported"] = True
                    result_data["set_search_term"] = set_name_or_code
                else:
                    set_result = search_set_cards(pset, cards, options)
                    result_data["set_summaries"] = set_result["card_summaries"]
                    result_data["api_total"] = set_result["api_total"]
                    result_data["after_filter"] = sum(
                        s["raw_count"] + s["psa9_count"] + s["psa10_count"]
                        for s in set_result["card_summaries"]
                    )
                    result_data["set_name"] = pset.name
                    result_data["set_id"] = pset.id

    # --- Card / Cardmarket URL search ---
    else:
        queries = build_smart_queries(parsed, options)
        result_data["queries_run"] = [q["query"] for q in queries]

        if not queries:
            result_data["errors"] = ["Keine Queries generiert. Bitte Suchbegriff präzisieren."]
        else:
            search_result = run_smart_search(queries, options)
            result_data["api_total"] = search_result["api_total"]
            result_data["after_filter"] = search_result["after_filter"]
            result_data["errors"] = search_result["errors"]
            result_data["listings"] = search_result["results"]
            result_data["queries_run"] = search_result["queries_run"]

    return _render(request, "smart_search.html", {"result": result_data})


@router.post("/smart-search/save-watchlist", response_model=None)
async def smart_search_save_watchlist(request: Request) -> HTMLResponse | RedirectResponse:
    """Save a Smart Search query as a Watchlist entry."""
    if redir := _guard(request):
        return redir

    form = await request.form()
    query = (form.get("query") or "").strip()
    name = (form.get("name") or query or "Smart Search").strip()

    if not query:
        auth.set_flash(request, "Keine Query angegeben.", "danger")
        return RedirectResponse(url="/smart-search", status_code=303)

    with get_session() as db:
        services.create_watchlist(db, {
            "name": name,
            "query": query,
            "marketplace": config.EBAY_MARKETPLACE,
            "max_price": None,
            "target_market_price": None,
            "min_discount_percent": 15.0,
            "target_grade": None,
            "target_language": None,
            "enabled": True,
        })

    auth.set_flash(request, f"Watchlist «{name}» wurde erstellt.", "success")
    return RedirectResponse(url="/watchlists", status_code=303)
