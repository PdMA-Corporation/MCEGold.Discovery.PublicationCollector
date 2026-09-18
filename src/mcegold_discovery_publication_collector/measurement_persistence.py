from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .measurement_publication import NormalizedMeasurementRow


LOGGER = logging.getLogger(__name__)


class MeasurementPersistenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistedMeasurementRow:
    measurement_uuid: str
    measurement_location_key: int
    measurement_location_uuid: str
    unit_key: int | None
    unit_uuid: str | None
    measurement_timestamp_utc: str
    numeric_value: float
    info_source_key: int | None
    info_source_uuid: str | None
    inserted_utc: str


class SQLiteMeasurementPersistence:
    def __init__(self, database_path: str, busy_timeout_ms: int = 5000) -> None:
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms

    def persist_publication(self, rows: Sequence[NormalizedMeasurementRow]) -> int:
        if not self.database_path:
            raise MeasurementPersistenceError("discoveryDatabasePath is required for measurement persistence.")
        if not rows:
            raise MeasurementPersistenceError("No measurement rows to persist.")

        path = Path(self.database_path)
        try:
            with sqlite3.connect(path) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
                try:
                    connection.execute("BEGIN")
                    persisted_rows = [self._prepare_row(connection, row) for row in rows]
                    connection.executemany(
                        """
                        INSERT INTO FactMeasurement (
                            MeasurementUuid,
                            MeasurementLocationKey,
                            MeasurementLocationUuid,
                            UnitKey,
                            UnitUuid,
                            MeasurementTimestampUtc,
                            NumericValue,
                            InfoSourceKey,
                            InfoSourceUuid,
                            InsertedUtc
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(MeasurementLocationUuid, MeasurementTimestampUtc, MeasurementUuid)
                            WHERE MeasurementUuid IS NOT NULL
                        DO UPDATE SET
                            MeasurementLocationKey = excluded.MeasurementLocationKey,
                            UnitKey = excluded.UnitKey,
                            UnitUuid = excluded.UnitUuid,
                            NumericValue = excluded.NumericValue,
                            InfoSourceKey = COALESCE(excluded.InfoSourceKey, FactMeasurement.InfoSourceKey),
                            InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, FactMeasurement.InfoSourceUuid),
                            InsertedUtc = excluded.InsertedUtc
                        """,
                        [
                            (
                                row.measurement_uuid,
                                row.measurement_location_key,
                                row.measurement_location_uuid,
                                row.unit_key,
                                row.unit_uuid,
                                row.measurement_timestamp_utc,
                                row.numeric_value,
                                row.info_source_key,
                                row.info_source_uuid,
                                row.inserted_utc,
                            )
                            for row in persisted_rows
                        ],
                    )
                    connection.commit()
                    return len(persisted_rows)
                except Exception:
                    connection.rollback()
                    raise
        except sqlite3.Error as exc:
            raise MeasurementPersistenceError(f"Measurement persistence failed for discovery database: {path}") from exc

    def _prepare_row(
        self,
        connection: sqlite3.Connection,
        row: NormalizedMeasurementRow,
    ) -> PersistedMeasurementRow:
        location = self._measurement_location(connection, row.measurement_location_uuid)
        if location is None:
            raise MeasurementPersistenceError(
                f"Measurement location is not known: {row.measurement_location_uuid}"
            )

        value_class = row.value_class or str(location["ValueClass"] or "Measure")
        unit_uuid = row.unit_uuid
        unit_key = self._unit_key(connection, unit_uuid) if unit_uuid else None
        if value_class == "Measure":
            if unit_uuid is None:
                unit_uuid = location["DefaultUnitUuid"]
                unit_key = location["DefaultUnitKey"]
            if unit_key is None:
                raise MeasurementPersistenceError(
                    f"Measure fact requires a known unit for measurement location {row.measurement_location_uuid}."
                )
            self._validate_unit_quantity(connection, unit_key, location)
        elif value_class in {"Percentage", "Number"}:
            unit_uuid = None
            unit_key = None
        else:
            raise MeasurementPersistenceError(f"Unsupported measurement value class: {value_class}")

        info_source_key = self._info_source_key(connection, row.info_source_uuid) if row.info_source_uuid else None
        return PersistedMeasurementRow(
            measurement_uuid=row.measurement_uuid,
            measurement_location_key=int(location["MeasurementLocationKey"]),
            measurement_location_uuid=row.measurement_location_uuid,
            unit_key=unit_key,
            unit_uuid=unit_uuid,
            measurement_timestamp_utc=row.measurement_timestamp_utc,
            numeric_value=row.numeric_value,
            info_source_key=info_source_key,
            info_source_uuid=row.info_source_uuid,
            inserted_utc=_utc_now(),
        )

    def _measurement_location(self, connection: sqlite3.Connection, location_uuid: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT
                MeasurementLocationKey,
                MeasurementLocationUuid,
                ValueClass,
                DefaultUnitUuid,
                DefaultUnitKey,
                UomQuantityKey
            FROM DimMeasurementLocation
            WHERE lower(MeasurementLocationUuid) = lower(?)
            """,
            (location_uuid,),
        ).fetchone()

    def _unit_key(self, connection: sqlite3.Connection, unit_uuid: str | None) -> int | None:
        if not unit_uuid:
            return None
        row = connection.execute(
            "SELECT UnitKey FROM DimUnit WHERE lower(UnitUuid) = lower(?)",
            (unit_uuid,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def _info_source_key(self, connection: sqlite3.Connection, info_source_uuid: str | None) -> int | None:
        if not info_source_uuid:
            return None
        row = connection.execute(
            "SELECT InfoSourceKey FROM DimInfoSource WHERE lower(InfoSourceUuid) = lower(?)",
            (info_source_uuid,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def _validate_unit_quantity(
        self,
        connection: sqlite3.Connection,
        unit_key: int,
        location: sqlite3.Row,
    ) -> None:
        location_uom_key = location["UomQuantityKey"]
        if location_uom_key is None:
            return
        row = connection.execute(
            "SELECT UomQuantityKey FROM DimUnit WHERE UnitKey = ?",
            (unit_key,),
        ).fetchone()
        unit_uom_key = row[0] if row is not None else None
        if unit_uom_key is not None and int(unit_uom_key) != int(location_uom_key):
            raise MeasurementPersistenceError("Measure fact unit does not belong to the measurement-location UOM quantity.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
