"""Session-based password authentication for the dashboard."""

import logging

from fastapi import Request
from fastapi.responses import RedirectResponse

from src import config

logger = logging.getLogger(__name__)

_AUTH_ENABLED = bool(config.DASHBOARD_PASSWORD)


def auth_required(request: Request) -> RedirectResponse | None:
    """
    Return a RedirectResponse to /login if the user is not authenticated.
    Returns None if access is granted (auth disabled or session valid).
    """
    if not _AUTH_ENABLED:
        return None
    if request.session.get("logged_in"):
        return None
    return RedirectResponse(url="/login", status_code=303)


def login(request: Request, password: str) -> bool:
    """
    Validate the given password. On success write the session flag.
    Never logs the password value.
    """
    if not _AUTH_ENABLED:
        return True
    if password == config.DASHBOARD_PASSWORD:
        request.session["logged_in"] = True
        logger.info("Dashboard login successful from %s", request.client)
        return True
    logger.warning("Failed dashboard login attempt from %s", request.client)
    return False


def logout(request: Request) -> None:
    request.session.clear()


def is_auth_enabled() -> bool:
    return _AUTH_ENABLED


# Flash helpers -----------------------------------------------------------

def set_flash(request: Request, message: str, level: str = "success") -> None:
    """Store a one-time flash message in the session."""
    request.session["flash"] = {"message": message, "level": level}


def pop_flash(request: Request) -> dict | None:
    """Read and remove the flash message from the session."""
    return request.session.pop("flash", None)
