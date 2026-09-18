from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_POLLING_INTERVAL_SECONDS = 15
DEFAULT_CLI_TIMEOUT_SECONDS = 60
DEFAULT_LOG_LEVEL = "INFO"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CollectorConfig:
    cli_path: str
    connector_config_path: str
    discovery_database_path: str = ""
    polling_interval_seconds: float = DEFAULT_POLLING_INTERVAL_SECONDS
    log_level: str = DEFAULT_LOG_LEVEL
    cli_timeout_seconds: float = DEFAULT_CLI_TIMEOUT_SECONDS
    include_raw: bool = False

    def validate(self) -> None:
        missing = []
        if not self.cli_path:
            missing.append("cliPath or MCEGOLD_CLI_PATH")
        if not self.connector_config_path:
            missing.append("connectorConfigPath or MCEGOLD_CONNECTOR_CONFIG_PATH")
        if not self.discovery_database_path:
            missing.append("discoveryDatabasePath or MCEGOLD_DISCOVERY_DATABASE_PATH")
        if missing:
            raise ConfigurationError("Missing required configuration: " + ", ".join(missing))
        if self.polling_interval_seconds <= 0:
            raise ConfigurationError("pollingIntervalSeconds must be greater than zero.")
        if self.cli_timeout_seconds <= 0:
            raise ConfigurationError("cliTimeoutSeconds must be greater than zero.")


def load_config(path: str | None = None, env: dict[str, str] | None = None) -> CollectorConfig:
    values = _read_json(path or (env or os.environ).get("MCEGOLD_COLLECTOR_CONFIG"))
    effective_env = env or os.environ

    config = CollectorConfig(
        cli_path=str(_override(values, "cliPath", effective_env, "MCEGOLD_CLI_PATH", "")),
        connector_config_path=str(
            _override(values, "connectorConfigPath", effective_env, "MCEGOLD_CONNECTOR_CONFIG_PATH", "")
        ),
        discovery_database_path=str(
            _override(values, "discoveryDatabasePath", effective_env, "MCEGOLD_DISCOVERY_DATABASE_PATH", "")
        ),
        polling_interval_seconds=_float(
            _override(
                values,
                "pollingIntervalSeconds",
                effective_env,
                "MCEGOLD_POLL_INTERVAL_SECONDS",
                DEFAULT_POLLING_INTERVAL_SECONDS,
            ),
            "pollingIntervalSeconds",
        ),
        log_level=str(_override(values, "logLevel", effective_env, "MCEGOLD_LOG_LEVEL", DEFAULT_LOG_LEVEL)).upper(),
        cli_timeout_seconds=_float(
            _override(values, "cliTimeoutSeconds", effective_env, "MCEGOLD_CLI_TIMEOUT_SECONDS", DEFAULT_CLI_TIMEOUT_SECONDS),
            "cliTimeoutSeconds",
        ),
        include_raw=_bool(_override(values, "includeRaw", effective_env, "MCEGOLD_INCLUDE_RAW", False)),
    )
    config.validate()
    return config


def _read_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Configuration file is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("Configuration file root must be a JSON object.")
    return value


def _override(values: dict[str, Any], key: str, env: dict[str, str], env_key: str, default: Any) -> Any:
    env_value = env.get(env_key)
    if env_value not in (None, ""):
        return env_value
    return values.get(key, default)


def _float(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be numeric.") from exc


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
