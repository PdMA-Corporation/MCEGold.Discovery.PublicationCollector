import json
import subprocess

import pytest

from mcegold_discovery_publication_collector import connection_settings
from mcegold_discovery_publication_collector.connection_settings import (
    ConnectionSettingsUnavailable,
    PortalConnectionSettingsReader,
)
from mcegold_discovery_publication_collector.configuration import CollectorConfig
from mcegold_discovery_publication_collector.discovery_request_client import DiscoveryRequestClient
from mcegold_discovery_publication_collector.publication_client import ConnectorCliError


def config():
    return CollectorConfig(
        cli_path="cli.exe",
        connector_config_path="connector.json",
        discovery_database_path="discovery.db",
    )


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


def write_connection_settings(path, host="https://mcegold.example.com", password="secret", api_key="key"):
    path.write_text(
        json.dumps(
            {
                "mcegoldDataServicesUrl": host,
                "mcegoldUsername": "user",
                "mcegoldPassword": password,
                "mcegoldApiKey": api_key,
            }
        ),
        encoding="utf-8",
    )


def test_request_open_session_uses_current_connection_settings(tmp_path):
    connection_path = tmp_path / "portal.local.json"
    write_connection_settings(connection_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(success("request.open-session", {"sessionId": "request-session-1"}))

    client = DiscoveryRequestClient(
        config(),
        runner=runner,
        connection_settings_reader=PortalConnectionSettingsReader(connection_path),
    )

    assert client.open_session() == "request-session-1"
    assert calls[0][0][1:3] == ["request", "open-session"]
    assert calls[0][1]["env"]["MCEGOLD_HOST"] == "https://mcegold.example.com"
    assert calls[0][1]["env"]["MCEGOLD_PASSWORD"] == "secret"
    assert calls[0][1]["env"]["MCEGOLD_API_KEY"] == "key"


def test_request_open_session_invokes_cli_when_implicit_portal_config_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(success("request.open-session", {"sessionId": "request-session-1"}))

    client = DiscoveryRequestClient(config(), runner=runner)

    assert client.open_session() == "request-session-1"
    assert calls[0][0] == [
        "cli.exe",
        "request",
        "open-session",
        "--config",
        "connector.json",
        "--output",
        "json",
    ]
    assert calls[0][1]["env"] is None


def test_request_open_session_skips_cli_when_settings_are_incomplete(tmp_path):
    connection_path = tmp_path / "portal.local.json"
    connection_path.write_text(json.dumps({"mcegoldDataServicesUrl": "https://mcegold.example.com"}), encoding="utf-8")
    calls = []
    client = DiscoveryRequestClient(
        config(),
        runner=lambda command, **kwargs: calls.append((command, kwargs)),
        connection_settings_reader=PortalConnectionSettingsReader(connection_path),
    )

    with pytest.raises(ConnectionSettingsUnavailable):
        client.open_session()

    assert calls == []


def test_request_connector_configuration_error_surfaces_as_cli_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(
            {
                "schemaVersion": "1.0",
                "success": False,
                "command": "request.open-session",
                "timestampUtc": "2026-08-06T00:00:00Z",
                "data": None,
                "fault": {
                    "category": "configuration",
                    "code": "InvalidConfiguration",
                    "message": "Configuration validation failed.",
                    "statusCode": 2,
                    "details": [],
                },
                "raw": None,
            },
            2,
        )

    client = DiscoveryRequestClient(config(), runner=runner)

    with pytest.raises(ConnectorCliError):
        client.open_session()

    assert calls
