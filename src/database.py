"""Database engine and session factory."""

import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from src import config

logger = logging.getLogger(__name__)

_FALLBACK_SQLITE = "sqlite:///./pokemon_watcher.db"


def _get_db_url() -> str:
    if config.DATABASE_URL:
        return config.DATABASE_URL
    logger.warning(
        "DATABASE_URL not set – falling back to local SQLite (%s). "
        "Do NOT use SQLite in production on Render.",
        _FALLBACK_SQLITE,
    )
    return _FALLBACK_SQLITE


engine = create_engine(
    _get_db_url(),
    # psycopg2 pool keeps connections alive; for cron jobs a small pool is fine
    pool_pre_ping=True,
    echo=False,
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


def get_session() -> Session:
    """Return a new database session. Caller is responsible for closing it."""
    return SessionLocal()
