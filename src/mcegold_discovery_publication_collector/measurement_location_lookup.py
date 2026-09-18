from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


class MeasurementLocationLookupError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeasurementLocationLookupResult:
    is_known: bool
    measurement_location_key: int | None = None


class SQLiteMeasurementLocationLookup:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def lookup(self, measurement_location_uuid: str) -> MeasurementLocationLookupResult:
        if not self.database_path:
            raise MeasurementLocationLookupError("discoveryDatabasePath is required for measurement-location lookup.")

        path = Path(self.database_path)
        uri_path = path.resolve(strict=False).as_posix()
        try:
            with sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    """
                    SELECT MeasurementLocationKey
                    FROM DimMeasurementLocation
                    WHERE lower(MeasurementLocationUuid) = lower(?)
                    LIMIT 1
                    """,
                    (measurement_location_uuid,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise MeasurementLocationLookupError(
                f"Could not query DimMeasurementLocation in discovery database: {path}"
            ) from exc

        return MeasurementLocationLookupResult(
            is_known=row is not None,
            measurement_location_key=int(row[0]) if row is not None else None,
        )
