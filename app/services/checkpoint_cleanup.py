"""Background service that deletes expired LangGraph checkpoints."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings
from app.core.logging import logger
from app.services.database import database_service

_CHECKPOINT_TABLES = ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]

_DELETE_EXPIRED_SQL = """\
DELETE FROM {table}
WHERE thread_id IN (
    SELECT thread_id
    FROM checkpoints
    WHERE (checkpoint->>'ts')::timestamptz < :cutoff
)"""


async def run_checkpoint_cleanup() -> None:
    """Periodically delete checkpoints older than CHECKPOINT_TTL_DAYS.

    Runs once at startup then repeats every 24 hours.  Errors are logged
    but never crash the application.
    """
    interval = 24 * 60 * 60  # 24 hours
    while True:
        try:
            await asyncio.sleep(interval)
            _delete_expired_checkpoints()
        except Exception:
            logger.exception("checkpoint_cleanup_failed")


def _delete_expired_checkpoints() -> None:
    """Delete checkpoint rows whose timestamp is older than the TTL."""
    ttl_days = settings.CHECKPOINT_TTL_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

    with Session(database_service.engine) as session:
        total_deleted = 0
        for table in _CHECKPOINT_TABLES:
            result = session.execute(
                text(_DELETE_EXPIRED_SQL.format(table=table)),
                {"cutoff": cutoff},
            )
            total_deleted += result.rowcount  # type: ignore[union-attr]

        session.commit()

    if total_deleted > 0:
        logger.info(
            "checkpoint_cleanup_completed",
            ttl_days=ttl_days,
            total_rows_deleted=total_deleted,
        )
    else:
        logger.debug("checkpoint_cleanup_no_expired_rows", ttl_days=ttl_days)
