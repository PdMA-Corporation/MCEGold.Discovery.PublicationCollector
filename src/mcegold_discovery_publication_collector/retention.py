from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .control_settings import DataRetentionSettings


LOGGER = logging.getLogger(__name__)
DEFAULT_RETENTION_BATCH_SIZE = 5000


@dataclass(frozen=True)
class RetentionCleanupResult:
    skipped: bool
    deleted_rows: int
    batch_count: int
    cutoff_utc: str | None
    error_message: str | None = None


class MeasurementRetentionService:
    def __init__(
        self,
        database_path: str,
        batch_size: int = DEFAULT_RETENTION_BATCH_SIZE,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = database_path
        self.batch_size = batch_size
        self.busy_timeout_ms = busy_timeout_ms

    def run_cleanup(
        self,
        settings: DataRetentionSettings,
        now_utc: datetime | None = None,
    ) -> RetentionCleanupResult:
        if not settings.enabled:
            return RetentionCleanupResult(
                skipped=True,
                deleted_rows=0,
                batch_count=0,
                cutoff_utc=None,
            )

        cutoff = retention_cutoff_utc(settings.days, now_utc=now_utc)
        if not self.database_path:
            return self._failed_result(cutoff, "discoveryDatabasePath is required for data retention cleanup.")
        if self.batch_size <= 0:
            return self._failed_result(cutoff, "Data retention batch size must be greater than zero.")

        path = Path(self.database_path)
        deleted_rows = 0
        batch_count = 0

        while True:
            try:
                batch_deleted_rows = self._delete_batch(path, cutoff)
            except sqlite3.Error as exc:
                error_message = f"Data retention cleanup failed for discovery database: {path}"
                LOGGER.warning("%s", error_message, exc_info=exc)
                return RetentionCleanupResult(
                    skipped=False,
                    deleted_rows=deleted_rows,
                    batch_count=batch_count,
                    cutoff_utc=cutoff,
                    error_message=error_message,
                )

            if batch_deleted_rows == 0:
                break
            deleted_rows += batch_deleted_rows
            batch_count += 1
            if batch_deleted_rows < self.batch_size:
                break

        if deleted_rows:
            LOGGER.info(
                "Data retention cleanup completed: %s measurement rows deleted; cutoff=%s.",
                deleted_rows,
                cutoff,
            )
        else:
            LOGGER.info("Data retention cleanup completed: no expired measurement rows; cutoff=%s.", cutoff)
        return RetentionCleanupResult(
            skipped=False,
            deleted_rows=deleted_rows,
            batch_count=batch_count,
            cutoff_utc=cutoff,
        )

    def _delete_batch(self, path: Path, cutoff_utc: str) -> int:
        with sqlite3.connect(path) as connection:
            connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
            try:
                connection.execute("BEGIN")
                cursor = connection.execute(
                    """
                    DELETE FROM FactMeasurement
                    WHERE MeasurementFactKey IN (
                        SELECT MeasurementFactKey
                        FROM FactMeasurement
                        WHERE MeasurementTimestampUtc < ?
                        ORDER BY MeasurementTimestampUtc
                        LIMIT ?
                    )
                    """,
                    (cutoff_utc, int(self.batch_size)),
                )
                deleted_rows = max(cursor.rowcount, 0)
                connection.commit()
                return deleted_rows
            except Exception:
                connection.rollback()
                raise

    def _failed_result(self, cutoff_utc: str, error_message: str) -> RetentionCleanupResult:
        LOGGER.warning("Data retention cleanup failed: %s", error_message)
        return RetentionCleanupResult(
            skipped=False,
            deleted_rows=0,
            batch_count=0,
            cutoff_utc=cutoff_utc,
            error_message=error_message,
        )


def retention_cutoff_utc(retention_days: int, now_utc: datetime | None = None) -> str:
    effective_now = _as_utc(now_utc or datetime.now(timezone.utc))
    cutoff = effective_now - timedelta(days=retention_days)
    return cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware.")
    return value.astimezone(timezone.utc)
