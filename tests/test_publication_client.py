import json
import subprocess

import pytest

from mcegold_discovery_publication_collector import connection_settings
from mcegold_discovery_publication_collector.connection_settings import (
    ConnectionSettingsUnavailable,
    PortalConnectionSettingsReader,
)
from mcegold_discovery_publication_collector.configuration import CollectorConfig
from mcegold_discovery_publication_collector.publication_client import (
    ConnectorCliError,
    InvalidSessionError,
    PublicationClient,
)


def config(cli_path=r"C:\tools\MCEGold.Data.Services.Connector.Cli.exe", include_raw=False):
    return CollectorConfig(
        cli_path=cli_path,
        connector_config_path="config/connector.config.json",
        include_raw=include_raw,
    )


def write_connection_settings(path, host="https://mcegold.example.com", username="user", password="secret", api_key="key"):
    path.write_text(
        json.dumps(
            {
                "mcegoldDataServicesUrl": host,
                "mcegoldUsername": username,
                "mcegoldPassword": password,
                "mcegoldApiKey": api_key,
            }
        ),
        encoding="utf-8",
    )


def connection_reader(path):
    return PortalConnectionSettingsReader(path)


def completed(envelope, returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=json.dumps(envelope),
        stderr=stderr,
    )


def success(command, data):
    return {
        "schemaVersion": "1.0",
        "success": True,
        "command": command,
        "timestampUtc": "2026-08-06T00:00:00Z",
        "data": data,
        "fault": None,
        "raw": None,
    }


def fault(command, status_code=500, code="CommandFailed", message="Failed"):
    return {
        "schemaVersion": "1.0",
        "success": False,
        "command": command,
        "timestampUtc": "2026-08-06T00:00:00Z",
        "data": None,
        "fault": {
            "category": "remote",
            "code": code,
            "message": message,
            "statusCode": status_code,
            "details": [],
        },
        "raw": None,
    }


def test_cli_command_construction_for_exe():
    client = PublicationClient(config(include_raw=True))

    assert client.build_open_subscription_command() == [
        r"C:\tools\MCEGold.Data.Services.Connector.Cli.exe",
        "publication",
        "open-subscription",
        "--config",
        "config/connector.config.json",
        "--output",
        "json",
        "--include-raw",
    ]
    assert client.build_read_publication_command("session-1") == [
        r"C:\tools\MCEGold.Data.Services.Connector.Cli.exe",
        "publication",
        "read",
        "--config",
        "config/connector.config.json",
        "--session-id",
        "session-1",
        "--output",
        "json",
        "--include-raw",
    ]


def test_cli_command_construction_for_dll_uses_dotnet():
    client = PublicationClient(config(cli_path=r"C:\tools\MCEGold.Data.Services.Connector.Cli.dll"))

    assert client.build_remove_publication_command("session-1")[:2] == [
        "dotnet",
        r"C:\tools\MCEGold.Data.Services.Connector.Cli.dll",
    ]


def test_cli_success_parsing_opens_session(tmp_path):
    calls = []
    connection_path = tmp_path / "portal.local.json"
    write_connection_settings(connection_path)

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(success("publication.open-subscription", {"sessionId": "session-1"}))

    client = PublicationClient(config(), runner=runner, connection_settings_reader=connection_reader(connection_path))

    assert client.open_subscription() == "session-1"
    assert calls[0][0][1:3] == ["publication", "open-subscription"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["env"]["MCEGOLD_HOST"] == "https://mcegold.example.com"
    assert calls[0][1]["env"]["MCEGOLD_USERNAME"] == "user"
    assert calls[0][1]["env"]["MCEGOLD_PASSWORD"] == "secret"
    assert calls[0][1]["env"]["MCEGOLD_API_KEY"] == "key"


def test_missing_implicit_portal_config_still_invokes_cli_with_connector_config(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(success("publication.open-subscription", {"sessionId": "session-1"}))

    client = PublicationClient(config(), runner=runner)

    assert client.open_subscription() == "session-1"
    assert calls[0][0] == [
        r"C:\tools\MCEGold.Data.Services.Connector.Cli.exe",
        "publication",
        "open-subscription",
        "--config",
        "config/connector.config.json",
        "--output",
        "json",
    ]
    assert calls[0][1]["env"] is None


def test_direct_mcegold_environment_is_inherited_without_collector_preflight(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    monkeypatch.setenv("MCEGOLD_HOST", "https://env.example.com")
    monkeypatch.setenv("MCEGOLD_USERNAME", "env-user")
    monkeypatch.setenv("MCEGOLD_PASSWORD", "env-password")
    monkeypatch.setenv("MCEGOLD_API_KEY", "env-key")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(success("publication.open-subscription", {"sessionId": "session-1"}))

    client = PublicationClient(config(), runner=runner)

    assert client.open_subscription() == "session-1"
    assert calls[0][1]["env"] is None


def test_incomplete_connection_settings_skip_open_cli_call(tmp_path):
    calls = []
    connection_path = tmp_path / "portal.local.json"
    connection_path.write_text(json.dumps({"mcegoldDataServicesUrl": "https://mcegold.example.com"}), encoding="utf-8")
    client = PublicationClient(config(), runner=lambda command, **kwargs: calls.append((command, kwargs)), connection_settings_reader=connection_reader(connection_path))

    with pytest.raises(ConnectionSettingsUnavailable):
        client.open_subscription()

    assert calls == []


def test_connector_configuration_error_surfaces_as_cli_failure_not_waiting(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(
            fault("publication.open-subscription", 2, "InvalidConfiguration", "Configuration validation failed."),
            2,
        )

    client = PublicationClient(config(), runner=runner)

    with pytest.raises(ConnectorCliError) as exc_info:
        client.open_subscription()

    assert calls
    assert exc_info.value.fault is not None
    assert exc_info.value.fault.code == "InvalidConfiguration"


def test_existing_session_uses_cached_connection_environment_until_close(tmp_path):
    calls = []
    connection_path = tmp_path / "portal.local.json"
    write_connection_settings(connection_path, host="https://first.example.com", password="first-secret", api_key="first-key")

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["publication", "open-subscription"]:
            return completed(success("publication.open-subscription", {"sessionId": "session-1"}))
        return completed(
            success(
                "publication.read",
                {
                    "sessionId": "session-1",
                    "messageId": "message-1",
                    "payload": {"measurement": {"value": 1}},
                },
            )
        )

    client = PublicationClient(config(), runner=runner, connection_settings_reader=connection_reader(connection_path))
    session_id = client.open_subscription()
    write_connection_settings(connection_path, host="https://second.example.com", password="second-secret", api_key="second-key")

    client.read_publication(session_id)

    assert calls[0][1]["env"]["MCEGOLD_HOST"] == "https://first.example.com"
    assert calls[1][1]["env"]["MCEGOLD_HOST"] == "https://first.example.com"
    assert calls[1][1]["env"]["MCEGOLD_PASSWORD"] == "first-secret"


def test_read_success_parses_publication_payload():
    client = PublicationClient(
        config(),
        runner=lambda *_args, **_kwargs: completed(
            success(
                "publication.read",
                {
                    "sessionId": "session-1",
                    "messageId": "message-1",
                    "payload": {"measurement": {"value": 1}},
                },
            )
        ),
    )

    publication = client.read_publication("session-1")

    assert publication is not None
    assert publication.message_id == "message-1"
    assert publication.payload == {"measurement": {"value": 1}}


def test_cli_fault_parsing_raises_connector_error():
    client = PublicationClient(
        config(),
        runner=lambda *_args, **_kwargs: completed(fault("publication.remove", 500, "CommandFailed", "Server failed"), 1),
    )

    with pytest.raises(ConnectorCliError) as exc_info:
        client.remove_publication("session-1")

    assert exc_info.value.fault is not None
    assert exc_info.value.fault.status_code == 500
    assert exc_info.value.fault.code == "CommandFailed"


def test_read_no_publication_available_is_empty_publication_result():
    client = PublicationClient(
        config(),
        runner=lambda *_args, **_kwargs: completed(
            fault(
                "publication.read",
                404,
                "NoPublicationAvailable",
                "No publication is available for the subscription session.",
            ),
            1,
        ),
    )

    assert client.read_publication("session-1") is None


def test_legacy_read_404_not_found_is_empty_publication_result():
    client = PublicationClient(
        config(),
        runner=lambda *_args, **_kwargs: completed(fault("publication.read", 404, "CommandFailed", "Not Found"), 1),
    )

    assert client.read_publication("session-1") is None


def test_invalid_session_fault_is_distinguished():
    client = PublicationClient(
        config(),
        runner=lambda *_args, **_kwargs: completed(fault("publication.read", 404, "SessionNotFound", "Session not found"), 1),
    )

    with pytest.raises(InvalidSessionError):
        client.read_publication("session-1")


def test_documented_short_json_shapes_match_inspected_cli_names():
    client = PublicationClient(config())

    assert client.documented_short_json_command("open-subscription")["command"] == "OpenSubscription"
    assert client.documented_short_json_command("read", "session-1")["command"] == "ReadPublication"
    assert client.documented_short_json_command("remove", "session-1")["command"] == "RemovePublication"
    assert client.documented_short_json_command("close-subscription", "session-1")["command"] == "CloseSubscription"
