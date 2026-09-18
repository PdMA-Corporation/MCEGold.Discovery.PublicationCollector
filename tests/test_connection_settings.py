import json

from mcegold_discovery_publication_collector.connection_settings import PortalConnectionSettingsReader


def test_missing_portal_local_json_is_incomplete(tmp_path):
    result = PortalConnectionSettingsReader(tmp_path / "portal.local.json").read()

    assert result.settings is None
    assert result.error_message == "Portal local config file is missing."


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
