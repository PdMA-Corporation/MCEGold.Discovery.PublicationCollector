from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


DEFAULT_COLLECTOR_ENABLED = True
DEFAULT_RETENTION_ENABLED = False
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 730


@dataclass(frozen=True)
class CollectorControlSettings:
    enabled: bool
    polling_interval_seconds: float


@dataclass(frozen=True)
class CollectorControlSettingsReadResult:
    settings: CollectorControlSettings
    error_message: str | None = None


@dataclass(frozen=True)
class DataRetentionSettings:
    enabled: bool
    days: int


@dataclass(frozen=True)
class DataRetentionSettingsReadResult:
    settings: DataRetentionSettings
    error_message: str | None = None


class CollectorControlSettingsReader:
    def __init__(
        self,
        database_path: str,
        fallback_polling_interval_seconds: float,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms
        self.last_valid_settings = CollectorControlSettings(
            enabled=DEFAULT_COLLECTOR_ENABLED,
            polling_interval_seconds=fallback_polling_interval_seconds,
        )

    def read(self) -> CollectorControlSettingsReadResult:
        if not self.database_path:
            return CollectorControlSettingsReadResult(
                self.last_valid_settings,
                "discoveryDatabasePath is required for collector control settings.",
            )

        path = Path(self.database_path)
        try:
            with sqlite3.connect(path) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
                rows = connection.execute(
                    """
                    SELECT SettingKey, SettingValue, ValueType
                    FROM ApplicationSetting
                    WHERE SettingKey IN ('Collector.Enabled', 'Collector.PollingIntervalSeconds')
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            return CollectorControlSettingsReadResult(
                self.last_valid_settings,
                f"Could not read collector control settings from Portal database: {path}",
            )

        settings_by_key = {row["SettingKey"]: row for row in rows}
        errors: list[str] = []
        enabled = self.last_valid_settings.enabled
        polling_interval = self.last_valid_settings.polling_interval_seconds

        enabled_row = settings_by_key.get("Collector.Enabled")
        if enabled_row is None:
            errors.append("Collector.Enabled setting is missing.")
        else:
            parsed_enabled = _parse_enabled(enabled_row)
            if parsed_enabled is None:
                errors.append("Collector.Enabled setting is malformed.")
            else:
                enabled = parsed_enabled

        interval_row = settings_by_key.get("Collector.PollingIntervalSeconds")
        if interval_row is None:
            errors.append("Collector.PollingIntervalSeconds setting is missing.")
        else:
            parsed_interval = _parse_polling_interval(interval_row)
            if parsed_interval is None:
                errors.append("Collector.PollingIntervalSeconds setting is malformed.")
            else:
                polling_interval = parsed_interval

        settings = CollectorControlSettings(
            enabled=enabled,
            polling_interval_seconds=polling_interval,
        )
        if not errors:
            self.last_valid_settings = settings
        elif enabled != self.last_valid_settings.enabled or polling_interval != self.last_valid_settings.polling_interval_seconds:
            self.last_valid_settings = settings

        return CollectorControlSettingsReadResult(settings, " ".join(errors) or None)


class DataRetentionSettingsReader:
    def __init__(
        self,
        database_path: str,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms
        self.last_valid_settings = DataRetentionSettings(
            enabled=DEFAULT_RETENTION_ENABLED,
            days=DEFAULT_RETENTION_DAYS,
        )

    def read(self) -> DataRetentionSettingsReadResult:
        if not self.database_path:
            return DataRetentionSettingsReadResult(
                self.last_valid_settings,
                "discoveryDatabasePath is required for data retention settings.",
            )

        path = Path(self.database_path)
        try:
            with sqlite3.connect(path) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
                rows = connection.execute(
                    """
                    SELECT SettingKey, SettingValue, ValueType
                    FROM ApplicationSetting
                    WHERE SettingKey IN ('DataRetention.Enabled', 'DataRetention.Days')
                    """
                ).fetchall()
        except sqlite3.Error:
            return DataRetentionSettingsReadResult(
                self.last_valid_settings,
                f"Could not read data retention settings from Portal database: {path}",
            )

        settings_by_key = {row["SettingKey"]: row for row in rows}
        errors: list[str] = []
        enabled = self.last_valid_settings.enabled
        days = self.last_valid_settings.days

        enabled_row = settings_by_key.get("DataRetention.Enabled")
        if enabled_row is None:
            errors.append("DataRetention.Enabled setting is missing.")
        else:
            parsed_enabled = _parse_boolean(enabled_row)
            if parsed_enabled is None:
                errors.append("DataRetention.Enabled setting is malformed.")
            else:
                enabled = parsed_enabled

        days_row = settings_by_key.get("DataRetention.Days")
        if days_row is None:
            errors.append("DataRetention.Days setting is missing.")
        else:
            parsed_days = _parse_retention_days(days_row)
            if parsed_days is None:
                errors.append("DataRetention.Days setting is malformed.")
            else:
                days = parsed_days

        settings = DataRetentionSettings(enabled=enabled, days=days)
        if not errors:
            self.last_valid_settings = settings
        elif enabled != self.last_valid_settings.enabled or days != self.last_valid_settings.days:
            self.last_valid_settings = settings

        return DataRetentionSettingsReadResult(settings, " ".join(errors) or None)


def _parse_enabled(row: sqlite3.Row) -> bool | None:
    return _parse_boolean(row)


def _parse_boolean(row: sqlite3.Row) -> bool | None:
    if row["ValueType"] != "Boolean":
        return None
    value = str(row["SettingValue"] or "").strip().casefold()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _parse_polling_interval(row: sqlite3.Row) -> float | None:
    if row["ValueType"] != "Integer":
        return None
    try:
        value = int(str(row["SettingValue"] or "").strip())
    except ValueError:
        return None
    return float(value) if value > 0 else None


def _parse_retention_days(row: sqlite3.Row) -> int | None:
    if row["ValueType"] != "Integer":
        return None
    try:
        value = int(str(row["SettingValue"] or "").strip())
    except ValueError:
        return None
    return value if MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS else None
