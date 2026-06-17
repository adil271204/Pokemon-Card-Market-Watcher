"""All dashboard route handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import config
from src.database import get_session
from src.deal_scorer import calculate_deal_score
from src.ebay_client import EbayClient
from src.listing_cleaner import clean_and_classify_listing
from src.models import Watchlist
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
    return _render(request, "watchlists.html", {"watchlists": wls})


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
    try:
        client = EbayClient()
        raw_listings = client.search_new_listings(
            query=wl_data["query"],
            marketplace=wl_data["marketplace"],
            max_price=wl_data["max_price"],
        )
        for listing in raw_listings:
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
    })


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
        "USE_MOCK_EBAY": str(config.USE_MOCK_EBAY),
        "EBAY_MARKETPLACE": config.EBAY_MARKETPLACE,
        "EBAY_CLIENT_ID": "✓ gesetzt" if config.EBAY_CLIENT_ID else "✗ fehlt",
        "EBAY_CLIENT_SECRET": "✓ gesetzt" if config.EBAY_CLIENT_SECRET else "✗ fehlt",
        "TELEGRAM_BOT_TOKEN": "✓ gesetzt" if config.TELEGRAM_BOT_TOKEN else "✗ fehlt",
        "TELEGRAM_CHAT_ID": "✓ gesetzt" if config.TELEGRAM_CHAT_ID else "✗ fehlt",
        "DASHBOARD_PASSWORD": "✓ gesetzt" if config.DASHBOARD_PASSWORD else "✗ fehlt",
        "SESSION_SECRET": "✓ gesetzt" if config.SESSION_SECRET_IS_SET else "⚠ Nur Fallback – nicht sicher für Production!",
        "LOG_LEVEL": config.LOG_LEVEL,
    }
    warnings: list[str] = []
    if not config.DASHBOARD_PASSWORD:
        warnings.append("DASHBOARD_PASSWORD ist nicht gesetzt – das Dashboard ist ohne Passwort erreichbar!")
    if not config.SESSION_SECRET_IS_SET:
        warnings.append("SESSION_SECRET ist nicht gesetzt – Sessions werden bei jedem Neustart ungültig.")

    return _render(request, "settings.html", {"cfg": cfg, "warnings": warnings})


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
