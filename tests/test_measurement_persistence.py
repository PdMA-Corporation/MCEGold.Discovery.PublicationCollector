import sqlite3

import pytest

from mcegold_discovery_publication_collector.measurement_persistence import (
    MeasurementPersistenceError,
    SQLiteMeasurementPersistence,
)
from mcegold_discovery_publication_collector.measurement_publication import NormalizedMeasurementRow


def create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE DimMeasurementLocation (
            MeasurementLocationKey INTEGER PRIMARY KEY AUTOINCREMENT,
            MeasurementLocationUuid TEXT NOT NULL UNIQUE,
            MeasurementLocationName TEXT NOT NULL,
            ValueClass TEXT NULL,
            DefaultUnitUuid TEXT NULL,
            DefaultUnitKey INTEGER NULL,
            UomQuantityKey INTEGER NULL
        );
        CREATE TABLE DimUnit (
            UnitKey INTEGER PRIMARY KEY AUTOINCREMENT,
            UnitUuid TEXT NOT NULL UNIQUE,
            ShortName TEXT NOT NULL,
            UomQuantityKey INTEGER NULL
        );
        CREATE TABLE DimInfoSource (
            InfoSourceKey INTEGER PRIMARY KEY AUTOINCREMENT,
            InfoSourceUuid TEXT NOT NULL UNIQUE,
            ShortName TEXT NULL
        );
        CREATE TABLE FactMeasurement (
            MeasurementFactKey INTEGER PRIMARY KEY AUTOINCREMENT,
            MeasurementUuid TEXT NULL,
            MeasurementLocationKey INTEGER NOT NULL REFERENCES DimMeasurementLocation(MeasurementLocationKey),
            MeasurementLocationUuid TEXT NOT NULL,
            UnitKey INTEGER NULL REFERENCES DimUnit(UnitKey),
            UnitUuid TEXT NULL,
            MeasurementTimestampUtc TEXT NOT NULL,
            NumericValue REAL NOT NULL,
            InfoSourceKey INTEGER NULL REFERENCES DimInfoSource(InfoSourceKey),
            InfoSourceUuid TEXT NULL,
            InsertedUtc TEXT NOT NULL
        );
        CREATE UNIQUE INDEX UX_FactMeasurement_LocationTimestamp
            ON FactMeasurement(MeasurementLocationUuid, MeasurementTimestampUtc)
            WHERE MeasurementUuid IS NULL;
        CREATE UNIQUE INDEX UX_FactMeasurement_LocationTimestampMeasurementUuid
            ON FactMeasurement(MeasurementLocationUuid, MeasurementTimestampUtc, MeasurementUuid)
            WHERE MeasurementUuid IS NOT NULL;
        """
    )


def seed_known_location(connection, value_class="Measure", unit_uuid="unit-1", uom_quantity_key=10):
    connection.execute(
        "INSERT INTO DimUnit (UnitUuid, ShortName, UomQuantityKey) VALUES (?, ?, ?)",
        ("unit-1", "RPM", uom_quantity_key),
    )
    connection.execute(
        "INSERT INTO DimInfoSource (InfoSourceUuid, ShortName) VALUES (?, ?)",
        ("info-source-1", "MCEGold"),
    )
    connection.execute(
        """
        INSERT INTO DimMeasurementLocation (
            MeasurementLocationUuid,
            MeasurementLocationName,
            ValueClass,
            DefaultUnitUuid,
            DefaultUnitKey,
            UomQuantityKey
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("location-1", "Pump Bearing", value_class, unit_uuid, 1 if unit_uuid else None, uom_quantity_key),
    )


def db_path(tmp_path):
    path = tmp_path / "discovery.db"
    with sqlite3.connect(path) as connection:
        create_schema(connection)
        seed_known_location(connection)
    return path


def row(**overrides):
    values = {
        "measurement_location_uuid": "location-1",
        "measurement_timestamp_utc": "2018-09-22T03:34:17Z",
        "numeric_value": 38.21,
        "value_class": "Measure",
        "measurement_uuid": "measurement-1",
        "unit_uuid": "unit-1",
        "info_source_uuid": "info-source-1",
    }
    values.update(overrides)
    return NormalizedMeasurementRow(**values)


def facts(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(item) for item in connection.execute("SELECT * FROM FactMeasurement ORDER BY MeasurementFactKey")]


def test_known_location_persists_one_fact_measurement_row(tmp_path):
    path = db_path(tmp_path)

    count = SQLiteMeasurementPersistence(str(path)).persist_publication([row()])

    stored = facts(path)
    assert count == 1
    assert stored[0]["MeasurementLocationKey"] == 1
    assert stored[0]["MeasurementUuid"] == "measurement-1"
    assert stored[0]["MeasurementLocationUuid"] == "location-1"
    assert stored[0]["UnitKey"] == 1
    assert stored[0]["UnitUuid"] == "unit-1"
    assert stored[0]["InfoSourceKey"] == 1
    assert stored[0]["InfoSourceUuid"] == "info-source-1"
    assert stored[0]["NumericValue"] == 38.21


def test_multiple_measurements_persist_atomically(tmp_path):
    path = db_path(tmp_path)

    SQLiteMeasurementPersistence(str(path)).persist_publication(
        [
            row(measurement_timestamp_utc="2018-09-22T03:34:17Z"),
            row(measurement_uuid="measurement-2", measurement_timestamp_utc="2018-09-22T03:35:17Z", numeric_value=39),
        ]
    )

    assert len(facts(path)) == 2


def test_different_measurement_uuids_at_same_location_timestamp_persist_as_two_rows(tmp_path):
    path = db_path(tmp_path)

    SQLiteMeasurementPersistence(str(path)).persist_publication(
        [
            row(measurement_uuid="measurement-1", numeric_value=38.21),
            row(measurement_uuid="measurement-2", numeric_value=39.5),
        ]
    )

    stored = facts(path)
    assert len(stored) == 2
    assert {item["MeasurementUuid"] for item in stored} == {"measurement-1", "measurement-2"}


def test_replay_same_location_timestamp_is_idempotent(tmp_path):
    path = db_path(tmp_path)
    persistence = SQLiteMeasurementPersistence(str(path))

    persistence.persist_publication([row()])
    persistence.persist_publication([row()])

    assert len(facts(path)) == 1


def test_upsert_updates_existing_fact_values(tmp_path):
    path = db_path(tmp_path)
    persistence = SQLiteMeasurementPersistence(str(path))

    persistence.persist_publication([row(numeric_value=1)])
    persistence.persist_publication([row(numeric_value=2)])

    stored = facts(path)
    assert len(stored) == 1
    assert stored[0]["NumericValue"] == 2


def test_unknown_location_persists_nothing(tmp_path):
    path = db_path(tmp_path)

    with pytest.raises(MeasurementPersistenceError):
        SQLiteMeasurementPersistence(str(path)).persist_publication([row(measurement_location_uuid="unknown")])

    assert facts(path) == []


def test_persistence_failure_rolls_back_every_row(tmp_path):
    path = db_path(tmp_path)

    with pytest.raises(MeasurementPersistenceError):
        SQLiteMeasurementPersistence(str(path)).persist_publication(
            [
                row(measurement_timestamp_utc="2018-09-22T03:34:17Z"),
                row(measurement_location_uuid="unknown", measurement_timestamp_utc="2018-09-22T03:35:17Z"),
            ]
        )

    assert facts(path) == []


def test_percentage_fact_is_unitless(tmp_path):
    path = db_path(tmp_path)

    SQLiteMeasurementPersistence(str(path)).persist_publication([row(value_class="Percentage", unit_uuid="unit-1")])

    stored = facts(path)
    assert stored[0]["UnitKey"] is None
    assert stored[0]["UnitUuid"] is None


def test_measure_fact_requires_known_unit(tmp_path):
    path = db_path(tmp_path)

    with pytest.raises(MeasurementPersistenceError):
        SQLiteMeasurementPersistence(str(path)).persist_publication([row(unit_uuid="missing-unit")])

    assert facts(path) == []


def test_info_source_uuid_is_stored_even_when_key_is_missing(tmp_path):
    path = db_path(tmp_path)

    SQLiteMeasurementPersistence(str(path)).persist_publication([row(info_source_uuid="missing-info-source")])

    stored = facts(path)
    assert stored[0]["InfoSourceKey"] is None
    assert stored[0]["InfoSourceUuid"] == "missing-info-source"
