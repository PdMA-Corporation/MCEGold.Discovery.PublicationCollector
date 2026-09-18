import sqlite3

from mcegold_discovery_publication_collector import control_settings
from mcegold_discovery_publication_collector.control_settings import (
    CollectorControlSettingsReader,
    DataRetentionSettingsReader,
)


def create_application_setting_table(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE ApplicationSetting (
                SettingKey TEXT PRIMARY KEY,
                SettingValue TEXT NULL,
                ValueType TEXT NOT NULL,
                UpdatedUtc TEXT NOT NULL
            )
            """
        )
        connection.commit()


def set_setting(db_path, key, value, value_type):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO ApplicationSetting(SettingKey, SettingValue, ValueType, UpdatedUtc)
            VALUES (?, ?, ?, '2026-08-20T12:00:00Z')
            ON CONFLICT(SettingKey) DO UPDATE SET
                SettingValue = excluded.SettingValue,
                ValueType = excluded.ValueType,
                UpdatedUtc = excluded.UpdatedUtc
            """,
            (key, value, value_type),
        )
        connection.commit()


def set_control_settings(db_path, enabled="true", polling_interval="15") -> None:
    set_setting(db_path, "Collector.Enabled", enabled, "Boolean")
    set_setting(db_path, "Collector.PollingIntervalSeconds", polling_interval, "Integer")


def set_retention_settings(db_path, enabled="false", days="90") -> None:
    set_setting(db_path, "DataRetention.Enabled", enabled, "Boolean")
    set_setting(db_path, "DataRetention.Days", days, "Integer")


def test_reads_enabled_true_and_polling_interval(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled="true", polling_interval="30")

    result = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=15).read()

    assert result.error_message is None
    assert result.settings.enabled is True
    assert result.settings.polling_interval_seconds == 30


def test_reads_enabled_false(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled="false", polling_interval="15")

    result = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=15).read()

    assert result.error_message is None
    assert result.settings.enabled is False


def test_missing_settings_use_startup_defaults(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)

    result = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=22).read()

    assert result.settings.enabled is True
    assert result.settings.polling_interval_seconds == 22
    assert "Collector.Enabled setting is missing" in result.error_message
    assert "Collector.PollingIntervalSeconds setting is missing" in result.error_message


def test_malformed_boolean_uses_last_known_value(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled="false", polling_interval="15")
    reader = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=22)
    assert reader.read().settings.enabled is False
    set_setting(db_path, "Collector.Enabled", "yes", "Boolean")

    result = reader.read()

    assert result.settings.enabled is False
    assert result.settings.polling_interval_seconds == 15
    assert "Collector.Enabled setting is malformed" in result.error_message


def test_malformed_interval_uses_last_known_value(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled="true", polling_interval="30")
    reader = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=22)
    assert reader.read().settings.polling_interval_seconds == 30
    set_setting(db_path, "Collector.PollingIntervalSeconds", "0", "Integer")

    result = reader.read()

    assert result.settings.enabled is True
    assert result.settings.polling_interval_seconds == 30
    assert "Collector.PollingIntervalSeconds setting is malformed" in result.error_message


def test_database_read_failure_preserves_last_known_control(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled="false", polling_interval="30")
    reader = CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=22)
    assert reader.read().settings.enabled is False

    def failing_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("database locked")

    monkeypatch.setattr(control_settings.sqlite3, "connect", failing_connect)

    result = reader.read()

    assert result.settings.enabled is False
    assert result.settings.polling_interval_seconds == 30
    assert "Could not read collector control settings" in result.error_message


def test_reads_retention_disabled_and_default_days(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="false", days="90")

    result = DataRetentionSettingsReader(str(db_path)).read()

    assert result.error_message is None
    assert result.settings.enabled is False
    assert result.settings.days == 90


def test_reads_retention_enabled_and_minimum_days(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="30")

    result = DataRetentionSettingsReader(str(db_path)).read()

    assert result.error_message is None
    assert result.settings.enabled is True
    assert result.settings.days == 30


def test_reads_retention_maximum_days(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="730")

    result = DataRetentionSettingsReader(str(db_path)).read()

    assert result.error_message is None
    assert result.settings.days == 730


def test_missing_retention_settings_use_safe_defaults(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)

    result = DataRetentionSettingsReader(str(db_path)).read()

    assert result.settings.enabled is False
    assert result.settings.days == 90
    assert "DataRetention.Enabled setting is missing" in result.error_message
    assert "DataRetention.Days setting is missing" in result.error_message


def test_missing_retention_days_uses_last_known_value(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="30")
    reader = DataRetentionSettingsReader(str(db_path))
    assert reader.read().settings.days == 30

    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM ApplicationSetting WHERE SettingKey = 'DataRetention.Days'")
        connection.commit()

    result = reader.read()

    assert result.settings.enabled is True
    assert result.settings.days == 30
    assert "DataRetention.Days setting is missing" in result.error_message


def test_invalid_retention_days_use_last_known_value(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="90")
    reader = DataRetentionSettingsReader(str(db_path))
    assert reader.read().settings.days == 90

    for invalid_value in ("29", "731", "abc"):
        set_setting(db_path, "DataRetention.Days", invalid_value, "Integer")
        result = reader.read()

        assert result.settings.enabled is True
        assert result.settings.days == 90
        assert "DataRetention.Days setting is malformed" in result.error_message


def test_invalid_retention_enabled_uses_last_known_value(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="90")
    reader = DataRetentionSettingsReader(str(db_path))
    assert reader.read().settings.enabled is True
    set_setting(db_path, "DataRetention.Enabled", "yes", "Boolean")

    result = reader.read()

    assert result.settings.enabled is True
    assert result.settings.days == 90
    assert "DataRetention.Enabled setting is malformed" in result.error_message


def test_retention_settings_read_failure_preserves_last_known_values(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_application_setting_table(db_path)
    set_retention_settings(db_path, enabled="true", days="30")
    reader = DataRetentionSettingsReader(str(db_path))
    assert reader.read().settings.days == 30

    def failing_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("database locked")

    monkeypatch.setattr(control_settings.sqlite3, "connect", failing_connect)

    result = reader.read()

    assert result.settings.enabled is True
    assert result.settings.days == 30
    assert "Could not read data retention settings" in result.error_message
