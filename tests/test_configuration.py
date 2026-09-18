import json

import pytest

from mcegold_discovery_publication_collector.configuration import ConfigurationError, load_config


def test_default_polling_interval_is_15_seconds(tmp_path):
    config_path = tmp_path / "collector.json"
    config_path.write_text(
        json.dumps(
            {
                "cliPath": r"C:\tools\MCEGold.Data.Services.Connector.Cli.exe",
                "connectorConfigPath": "config/connector.config.json",
                "discoveryDatabasePath": "data/discovery_portal.db",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(str(config_path), env={})

    assert config.polling_interval_seconds == 15


def test_environment_overrides_file_values(tmp_path):
    config_path = tmp_path / "collector.json"
    config_path.write_text(
        json.dumps(
            {
                "cliPath": "file-cli.exe",
                "connectorConfigPath": "file-connector.json",
                "discoveryDatabasePath": "file-discovery.db",
                "pollingIntervalSeconds": 15,
                "logLevel": "INFO",
                "cliTimeoutSeconds": 60,
                "includeRaw": False,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(
        str(config_path),
        env={
            "MCEGOLD_CLI_PATH": "env-cli.dll",
            "MCEGOLD_CONNECTOR_CONFIG_PATH": "env-connector.json",
            "MCEGOLD_DISCOVERY_DATABASE_PATH": "env-discovery.db",
            "MCEGOLD_POLL_INTERVAL_SECONDS": "3",
            "MCEGOLD_LOG_LEVEL": "debug",
            "MCEGOLD_CLI_TIMEOUT_SECONDS": "7",
            "MCEGOLD_INCLUDE_RAW": "true",
        },
    )

    assert config.cli_path == "env-cli.dll"
    assert config.connector_config_path == "env-connector.json"
    assert config.discovery_database_path == "env-discovery.db"
    assert config.polling_interval_seconds == 3
    assert config.log_level == "DEBUG"
    assert config.cli_timeout_seconds == 7
    assert config.include_raw is True


def test_missing_required_configuration_fails():
    with pytest.raises(ConfigurationError):
        load_config(env={})
