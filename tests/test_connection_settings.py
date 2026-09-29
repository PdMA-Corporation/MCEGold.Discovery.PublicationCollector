import json

from mcegold_discovery_publication_collector import connection_settings
from mcegold_discovery_publication_collector.connection_settings import (
    ConnectionSettingsUnavailable,
    PortalConnectionSettingsReader,
    load_optional_connection_environment,
)


def test_missing_portal_local_json_is_incomplete(tmp_path):
    result = PortalConnectionSettingsReader(tmp_path / "portal.local.json").read()

    assert result.settings is None
    assert result.error_message == "Portal local config file is missing."


def test_missing_implicit_portal_local_json_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "portal.local.json"))

    result = load_optional_connection_environment(PortalConnectionSettingsReader())

    assert result is None


def test_missing_explicit_portal_local_json_is_unavailable(tmp_path):
    reader = PortalConnectionSettingsReader(tmp_path / "portal.local.json")

    try:
        load_optional_connection_environment(reader)
    except ConnectionSettingsUnavailable as exc:
        assert str(exc) == "Portal local config file is missing."
    else:
        raise AssertionError("Expected explicit missing Portal config to fail.")


def test_incomplete_connection_settings_are_reported_without_secret_values(tmp_path):
    config_path = tmp_path / "portal.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcegoldDataServicesUrl": "https://mcegold.example.com",
                "mcegoldUsername": "user",
                "mcegoldPassword": "super-secret",
            }
        ),
        encoding="utf-8",
    )

    result = PortalConnectionSettingsReader(config_path).read()

    assert result.settings is None
    assert result.error_message == "MCEGold Data Services connection settings are incomplete."
    assert "super-secret" not in result.error_message


def test_valid_connection_settings_create_cli_environment(tmp_path):
    config_path = tmp_path / "portal.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcegoldDataServicesUrl": "https://mcegold.example.com",
                "mcegoldUsername": "user",
                "mcegoldPassword": "super-secret",
                "mcegoldApiKey": "api-secret",
            }
        ),
        encoding="utf-8",
    )

    result = PortalConnectionSettingsReader(config_path).read()

    assert result.settings is not None
    assert result.settings.as_environment() == {
        "MCEGOLD_HOST": "https://mcegold.example.com",
        "MCEGOLD_USERNAME": "user",
        "MCEGOLD_PASSWORD": "super-secret",
        "MCEGOLD_API_KEY": "api-secret",
    }


def test_invalid_json_does_not_raise(tmp_path):
    config_path = tmp_path / "portal.local.json"
    config_path.write_text("{", encoding="utf-8")

    result = PortalConnectionSettingsReader(config_path).read()

    assert result.settings is None
    assert result.error_message == "Portal local config file could not be read."


def test_malformed_explicit_portal_local_json_is_unavailable(tmp_path):
    config_path = tmp_path / "portal.local.json"
    config_path.write_text("{", encoding="utf-8")

    try:
        load_optional_connection_environment(PortalConnectionSettingsReader(config_path))
    except ConnectionSettingsUnavailable as exc:
        assert str(exc) == "Portal local config file could not be read."
    else:
        raise AssertionError("Expected malformed explicit Portal config to fail.")
