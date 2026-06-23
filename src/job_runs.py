"""
Service functions for JobRun tracking.

Usage:
    run_id = start_job_run(session, "watcher")
    try:
        ...
        finish_job_run(session, run_id, "success", stats={...})
    except Exception as e:
        record_job_error(session, run_id, e)
        raise
"""

import json
import logging
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models import JobRun

logger = logging.getLogger(__name__)

VALID_JOB_TYPES = {"watcher", "backfill", "set_scan", "smart_search", "watchlist_test", "manual_test"}
VALID_STATUSES = {"running", "success", "partial_success", "failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_job_run(
    session: Session,
    job_type: str,
    metadata: dict | None = None,
) -> int:
    """Create a new JobRun record and return its id."""
    run = JobRun(
        job_type=job_type,
        status="running",
        started_at=_now(),
        metadata_json=_safe_json(metadata),
    )
    session.add(run)
    session.flush()  # get id without committing
    logger.debug("JobRun started: id=%d job_type=%s", run.id, job_type)
    return run.id


def finish_job_run(
    session: Session,
    job_run_id: int,
    status: str,
    stats: dict | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> JobRun | None:
    """Complete a JobRun with final stats and status."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")

    run = session.get(JobRun, job_run_id)
    if run is None:
        logger.error("JobRun id=%d not found – cannot finish", job_run_id)
        return None

    now = _now()
    run.status = status
    run.finished_at = now
    run.duration_seconds = (now - run.started_at).total_seconds()

    if stats:
        _apply_stats(run, stats)

    if error_message:
        run.error_message = str(error_message)[:4000]

    if metadata:
        run.metadata_json = _safe_json(metadata)

    session.flush()
    logger.info(
        "JobRun finished: id=%d status=%s duration=%.1fs",
        run.id, run.status, run.duration_seconds or 0,
    )
    return run


def record_job_error(
    session: Session,
    job_run_id: int,
    error: Exception | str,
) -> None:
    """Mark a running JobRun as failed and store error details."""
    run = session.get(JobRun, job_run_id)
    if run is None:
        return

    now = _now()
    run.status = "failed"
    run.finished_at = now
    run.duration_seconds = (now - run.started_at).total_seconds()
    run.errors_count = (run.errors_count or 0) + 1

    if isinstance(error, Exception):
        tb = traceback.format_exc()
        run.error_message = f"{type(error).__name__}: {error}\n\n{tb}"[:4000]
    else:
        run.error_message = str(error)[:4000]

    try:
        session.flush()
    except Exception:
        logger.exception("Could not save JobRun error record for id=%d", job_run_id)


def get_recent_job_runs(session: Session, limit: int = 50) -> list[JobRun]:
    return (
        session.query(JobRun)
        .order_by(JobRun.started_at.desc())
        .limit(limit)
        .all()
    )


def get_last_job_run(session: Session, job_type: str) -> JobRun | None:
    return (
        session.query(JobRun)
        .filter(JobRun.job_type == job_type)
        .order_by(JobRun.started_at.desc())
        .first()
    )


def get_last_successful_job_run(session: Session, job_type: str) -> JobRun | None:
    return (
        session.query(JobRun)
        .filter(JobRun.job_type == job_type, JobRun.status.in_(["success", "partial_success"]))
        .order_by(JobRun.started_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STAT_FIELDS = {
    "watchlists_checked",
    "queries_executed",
    "api_results_count",
    "listings_saved",
    "listings_updated",
    "listings_skipped_existing",
    "listings_filtered_country",
    "listings_filtered_keywords",
    "listings_filtered_price",
    "listings_filtered_deleted",
    "alerts_sent",
    "errors_count",
}


def _apply_stats(run: JobRun, stats: dict) -> None:
    for field in _STAT_FIELDS:
        if field in stats:
            setattr(run, field, int(stats[field]))


def _safe_json(data: dict | None) -> str | None:
    """Serialize metadata, stripping any keys that look like secrets."""
    if not data:
        return None
    _secret_keys = {"client_id", "client_secret", "token", "password", "secret", "api_key"}
    cleaned = {
        k: v for k, v in data.items()
        if not any(s in k.lower() for s in _secret_keys)
    }
    try:
        return json.dumps(cleaned, default=str)
    except Exception:
        return None
