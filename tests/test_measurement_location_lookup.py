import sqlite3

import pytest

from mcegold_discovery_publication_collector.measurement_location_lookup import (
    MeasurementLocationLookupError,
    SQLiteMeasurementLocationLookup,
)


def test_sqlite_lookup_returns_known_when_location_exists(tmp_path):
    db_path = tmp_path / "discovery.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE DimMeasurementLocation (
                MeasurementLocationKey INTEGER PRIMARY KEY AUTOINCREMENT,
                MeasurementLocationUuid TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute("INSERT INTO DimMeasurementLocation (MeasurementLocationUuid) VALUES (?)", ("Location-1",))

    result = SQLiteMeasurementLocationLookup(str(db_path)).lookup("location-1")

    assert result.is_known is True


def test_sqlite_lookup_returns_unknown_when_location_is_absent(tmp_path):
    db_path = tmp_path / "discovery.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE DimMeasurementLocation (
                MeasurementLocationKey INTEGER PRIMARY KEY AUTOINCREMENT,
                MeasurementLocationUuid TEXT NOT NULL UNIQUE
            )
            """
        )

    result = SQLiteMeasurementLocationLookup(str(db_path)).lookup("missing-location")

    assert result.is_known is False


def test_sqlite_lookup_failure_is_not_treated_as_unknown(tmp_path):
    missing_db_path = tmp_path / "missing.db"

    with pytest.raises(MeasurementLocationLookupError):
        SQLiteMeasurementLocationLookup(str(missing_db_path)).lookup("location-1")
