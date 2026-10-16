import json

import pytest

from mcegold_discovery_publication_collector.configuration import CollectorConfig, ConfigurationError, load_config


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
    assert config.use_shared_request_session is False


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


def shared_config(tmp_path, value, env=None):
    path = tmp_path / "collector.json"
    path.write_text(json.dumps({
        "cliPath": "cli", "connectorConfigPath": "connector.json", "discoveryDatabasePath": "db",
        "useSharedRequestSession": value,
    }), encoding="utf-8")
    return load_config(str(path), env=env or {"MCEGOLD_LOG_LEVEL": "INFO"})


@pytest.mark.parametrize("value", [True, False])
def test_shared_session_json_boolean(tmp_path, value):
    assert shared_config(tmp_path, value).use_shared_request_session is value


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None, [], {}])
def test_shared_session_rejects_non_boolean_json(tmp_path, value):
    with pytest.raises(ConfigurationError, match="JSON Boolean"):
        shared_config(tmp_path, value)


@pytest.mark.parametrize("text, expected", [("true", True), ("TrUe", True), (" FALSE ", False)])
def test_shared_session_environment_overrides_json(tmp_path, text, expected):
    assert shared_config(tmp_path, not expected, {
        "MCEGOLD_USE_SHARED_REQUEST_SESSION": text,
    }).use_shared_request_session is expected


@pytest.mark.parametrize("text", ["1", "0", "yes", "on", "null", " ", "invalid"])
def test_shared_session_rejects_invalid_environment(tmp_path, text):
    with pytest.raises(ConfigurationError, match="must be true or false"):
        shared_config(tmp_path, False, {"MCEGOLD_USE_SHARED_REQUEST_SESSION": text})


def test_empty_shared_session_environment_uses_json(tmp_path):
    assert shared_config(tmp_path, True, {"MCEGOLD_USE_SHARED_REQUEST_SESSION": ""}).use_shared_request_session


def test_shared_session_environment_has_precedence_over_invalid_json(tmp_path):
    assert shared_config(tmp_path, "invalid", {"MCEGOLD_USE_SHARED_REQUEST_SESSION": "true"}).use_shared_request_session


@pytest.mark.parametrize("value", [1, "false", None])
def test_direct_config_rejects_non_boolean_shared_session(value):
    with pytest.raises(ConfigurationError, match="must be a Boolean"):
        CollectorConfig("cli", "connector.json", "db", use_shared_request_session=value).validate()
