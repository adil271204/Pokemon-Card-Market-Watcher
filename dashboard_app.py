"""FastAPI Dashboard entry point.

Start locally:
    uvicorn dashboard_app:app --reload

On Render:
    uvicorn dashboard_app:app --host 0.0.0.0 --port $PORT
"""

import logging
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src import config
from src.web.routes import router

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

if not config.SESSION_SECRET_IS_SET:
    logger.warning(
        "SESSION_SECRET is not set – using an ephemeral random secret. "
        "All sessions will be invalidated on every restart. "
        "Set SESSION_SECRET in production."
    )

app = FastAPI(
    title="Pokémon Card Market Watcher – Dashboard",
    docs_url=None,   # disable Swagger UI in production
    redoc_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="pcmw_session",
    max_age=60 * 60 * 24,  # 24 hours
    https_only=False,       # set True behind Render's TLS proxy in prod if desired
)

app.mount("/static", StaticFiles(directory="src/static"), name="static")

app.include_router(router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Health check endpoint – no auth required, used by Render."""
    return JSONResponse({"status": "ok"})


@app.exception_handler(404)
async def not_found(request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.exception_handler(500)
async def server_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error: %s", exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)
