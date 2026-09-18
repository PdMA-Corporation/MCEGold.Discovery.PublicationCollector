import sqlite3
from datetime import UTC, datetime

import pytest

from mcegold_discovery_publication_collector import retention
from mcegold_discovery_publication_collector.control_settings import (
    DataRetentionSettings,
    DataRetentionSettingsReader,
)
from mcegold_discovery_publication_collector.retention import (
    MeasurementRetentionService,
    retention_cutoff_utc,
)


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


def create_retention_schema(db_path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE ApplicationSetting (
                SettingKey TEXT PRIMARY KEY,
                SettingValue TEXT NULL,
                ValueType TEXT NOT NULL,
                UpdatedUtc TEXT NOT NULL
            );
            CREATE TABLE CollectorRuntimeStatus (
                CollectorId TEXT PRIMARY KEY,
                InstanceId TEXT NOT NULL,
                StartedUtc TEXT NOT NULL,
                LastHeartbeatUtc TEXT NOT NULL,
                RuntimeState TEXT NOT NULL
            );
            CREATE TABLE DimMeasurementLocation (
                MeasurementLocationKey INTEGER PRIMARY KEY AUTOINCREMENT,
                MeasurementLocationUuid TEXT NOT NULL UNIQUE,
                MeasurementLocationName TEXT NOT NULL
            );
            CREATE TABLE DimUnit (
                UnitKey INTEGER PRIMARY KEY AUTOINCREMENT,
                UnitUuid TEXT NOT NULL UNIQUE
            );
            CREATE TABLE DimInfoSource (
                InfoSourceKey INTEGER PRIMARY KEY AUTOINCREMENT,
                InfoSourceUuid TEXT NOT NULL UNIQUE
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
            CREATE INDEX IX_FactMeasurement_MeasurementTimestampUtc
                ON FactMeasurement(MeasurementTimestampUtc);
            """
        )
        connection.execute(
            "INSERT INTO ApplicationSetting VALUES ('DataRetention.Enabled', 'true', 'Boolean', '2026-08-20T12:00:00Z')"
        )
        connection.execute(
            "INSERT INTO CollectorRuntimeStatus VALUES ('publication-collector', 'instance-1', '2026-08-20T12:00:00Z', '2026-08-20T12:00:00Z', 'Running')"
        )
        connection.execute(
            "INSERT INTO DimMeasurementLocation (MeasurementLocationUuid, MeasurementLocationName) VALUES ('location-1', 'Pump Bearing')"
        )
        connection.execute("INSERT INTO DimUnit (UnitUuid) VALUES ('unit-1')")
        connection.execute("INSERT INTO DimInfoSource (InfoSourceUuid) VALUES ('info-source-1')")
        connection.commit()


def insert_measurement(db_path, timestamp: str, measurement_uuid: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
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
            VALUES (?, 1, 'location-1', 1, 'unit-1', ?, 1.0, 1, 'info-source-1', '2026-08-20T12:00:00Z')
            """,
            (measurement_uuid, timestamp),
        )
        connection.commit()


def fact_timestamps(db_path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT MeasurementTimestampUtc FROM FactMeasurement ORDER BY MeasurementTimestampUtc"
            )
        ]


def table_count(db_path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def test_retention_cutoff_uses_timezone_aware_utc() -> None:
    assert retention_cutoff_utc(90, now_utc=NOW) == "2026-05-22T12:00:00Z"


def test_retention_cutoff_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        retention_cutoff_utc(90, now_utc=datetime(2026, 8, 20, 12, 0, 0))


def test_cleanup_deletes_expired_rows_and_retains_boundary_and_recent_rows(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    insert_measurement(db_path, "2026-05-22T11:59:59Z", "expired")
    insert_measurement(db_path, "2026-05-22T12:00:00Z", "boundary")
    insert_measurement(db_path, "2026-05-22T12:00:01Z", "recent")

    result = MeasurementRetentionService(str(db_path)).run_cleanup(
        DataRetentionSettings(enabled=True, days=90),
        now_utc=NOW,
    )

    assert result.skipped is False
    assert result.deleted_rows == 1
    assert result.batch_count == 1
    assert result.cutoff_utc == "2026-05-22T12:00:00Z"
    assert fact_timestamps(db_path) == ["2026-05-22T12:00:00Z", "2026-05-22T12:00:01Z"]


def test_cleanup_scope_only_deletes_fact_measurement_rows(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    insert_measurement(db_path, "2026-05-22T11:59:59Z", "expired")

    result = MeasurementRetentionService(str(db_path)).run_cleanup(
        DataRetentionSettings(enabled=True, days=90),
        now_utc=NOW,
    )

    assert result.deleted_rows == 1
    assert table_count(db_path, "FactMeasurement") == 0
    assert table_count(db_path, "ApplicationSetting") == 1
    assert table_count(db_path, "CollectorRuntimeStatus") == 1
    assert table_count(db_path, "DimMeasurementLocation") == 1
    assert table_count(db_path, "DimUnit") == 1
    assert table_count(db_path, "DimInfoSource") == 1


def test_cleanup_deletes_multiple_batches(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    for index in range(7):
        insert_measurement(db_path, f"2026-05-21T12:00:0{index}Z", f"expired-{index}")
    insert_measurement(db_path, "2026-05-22T12:00:00Z", "boundary")

    result = MeasurementRetentionService(str(db_path), batch_size=3).run_cleanup(
        DataRetentionSettings(enabled=True, days=90),
        now_utc=NOW,
    )

    assert result.deleted_rows == 7
    assert result.batch_count == 3
    assert fact_timestamps(db_path) == ["2026-05-22T12:00:00Z"]


def test_disabled_retention_skips_deletion(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    insert_measurement(db_path, "2026-05-22T11:59:59Z", "expired")

    result = MeasurementRetentionService(str(db_path)).run_cleanup(
        DataRetentionSettings(enabled=False, days=90),
        now_utc=NOW,
    )

    assert result.skipped is True
    assert result.deleted_rows == 0
    assert result.batch_count == 0
    assert result.cutoff_utc is None
    assert table_count(db_path, "FactMeasurement") == 1


def test_reader_and_cleanup_service_apply_live_portal_setting_change(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    insert_measurement(db_path, "2026-05-22T11:59:59Z", "expired")
    insert_measurement(db_path, "2026-05-22T12:00:00Z", "boundary")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE ApplicationSetting SET SettingValue = 'false' WHERE SettingKey = 'DataRetention.Enabled'"
        )
        connection.execute(
            "INSERT INTO ApplicationSetting VALUES ('DataRetention.Days', '90', 'Integer', '2026-08-20T12:00:00Z')"
        )
        connection.commit()

    reader = DataRetentionSettingsReader(str(db_path))
    service = MeasurementRetentionService(str(db_path))

    disabled_result = service.run_cleanup(reader.read().settings, now_utc=NOW)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE ApplicationSetting SET SettingValue = 'true' WHERE SettingKey = 'DataRetention.Enabled'"
        )
        connection.commit()
    enabled_result = service.run_cleanup(reader.read().settings, now_utc=NOW)

    assert disabled_result.skipped is True
    assert enabled_result.deleted_rows == 1
    assert fact_timestamps(db_path) == ["2026-05-22T12:00:00Z"]


def test_cleanup_failure_returns_error_result_without_retry_loop(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_retention_schema(db_path)
    insert_measurement(db_path, "2026-05-22T11:59:59Z", "expired")
    calls = 0

    def failing_connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise sqlite3.OperationalError("database locked")

    monkeypatch.setattr(retention.sqlite3, "connect", failing_connect)

    result = MeasurementRetentionService(str(db_path)).run_cleanup(
        DataRetentionSettings(enabled=True, days=90),
        now_utc=NOW,
    )

    assert result.skipped is False
    assert result.deleted_rows == 0
    assert result.batch_count == 0
    assert result.error_message is not None
    assert "Data retention cleanup failed" in result.error_message
    assert calls == 1
