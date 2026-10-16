import json
import subprocess
from pathlib import Path

import pytest

from mcegold_discovery_publication_collector import connection_settings
from mcegold_discovery_publication_collector.connection_settings import (
    ConnectionSettingsUnavailable,
    PortalConnectionSettingsReader,
)
from mcegold_discovery_publication_collector.configuration import CollectorConfig
from mcegold_discovery_publication_collector.discovery_request_client import DiscoveryRequestClient, DiscoveryRequestError
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


def test_borrowed_session_prepares_environment_and_correlates_requests(tmp_path):
    path = tmp_path / "portal.local.json"
    write_connection_settings(path, host="https://original.example.com")
    calls = []
    post_count = 0

    def runner(command, **kwargs):
        nonlocal post_count
        calls.append((command, kwargs))
        verb = command[2]
        assert verb not in {"open-session", "close-session"}
        if verb == "post":
            post_count += 1
            # Changing Portal settings during a workflow must not change its prepared environment.
            write_connection_settings(path, host="https://replacement.example.com")
            return completed(success("request.post", {"requestMessageId": f"request-{post_count}"}))
        return completed(success(f"request.{verb}", {"payload": {"result": "ok"}}))

    client = DiscoveryRequestClient(config(), runner=runner, connection_settings_reader=PortalConnectionSettingsReader(path))
    client.prepare_connection()
    for _ in range(2):
        assert client.execute_request("borrowed-session", {"requestType": "GetSites"}) == {"result": "ok"}
    assert [call[0][2] for call in calls] == ["post", "read-response", "remove-response"] * 2
    for index, (command, kwargs) in enumerate(calls):
        assert command[command.index("--session-id") + 1] == "borrowed-session"
        assert kwargs["env"]["MCEGOLD_HOST"] == "https://original.example.com"
        assert kwargs["env"]["MCEGOLD_PASSWORD"] == "secret"
        if command[2] != "post":
            assert command[command.index("--request-id") + 1] == f"request-{index // 3 + 1}"
        else:
            assert not Path(command[command.index("--input") + 1]).exists()
    client.clear_connection()
    assert client._session_connection_environment is None
    client.prepare_connection()
    assert client._session_connection_environment["MCEGOLD_HOST"] == "https://replacement.example.com"


@pytest.mark.parametrize("failure", ["timeout", "bad-json", "missing-id", "remote-closed"])
def test_post_failure_does_not_retry_or_remove(tmp_path, failure):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "bad-json":
            return subprocess.CompletedProcess(command, 0, stdout="not json", stderr="")
        if failure == "missing-id":
            return completed(success("request.post", {}))
        return completed({"success": False, "fault": {"code": "SessionClosed"}}, returncode=1)

    client = DiscoveryRequestClient(config(), runner=runner)
    with pytest.raises((DiscoveryRequestError, ConnectorCliError)):
        client.execute_request("borrowed", {})
    assert len(calls) == 1
    assert calls[0][2] == "post"


def test_read_failure_is_preserved_when_removal_also_fails(monkeypatch, caplog):
    client = DiscoveryRequestClient(config())
    original = ConnectorCliError("read failed", [])
    calls = []
    monkeypatch.setattr(client, "post_request", lambda *args: "request-A")

    def fail_read(session_id, request_id):
        calls.append(("read", session_id, request_id))
        raise original

    def fail_remove(session_id, request_id):
        calls.append(("remove", session_id, request_id))
        raise DiscoveryRequestError("cleanup failed")

    monkeypatch.setattr(client, "read_response", fail_read)
    monkeypatch.setattr(client, "remove_response", fail_remove)
    with pytest.raises(ConnectorCliError) as error:
        client.execute_request("session-A", {})
    assert error.value is original
    assert calls == [("read", "session-A", "request-A"), ("remove", "session-A", "request-A")]
    assert "Response removal failed" in caplog.text


def test_removal_failure_after_successful_read_is_not_suppressed(monkeypatch):
    client = DiscoveryRequestClient(config())
    monkeypatch.setattr(client, "post_request", lambda *args: "request-A")
    monkeypatch.setattr(client, "read_response", lambda *args: {"ok": True})
    original = DiscoveryRequestError("cleanup failed")

    def fail(*args):
        raise original

    monkeypatch.setattr(client, "remove_response", fail)
    with pytest.raises(DiscoveryRequestError) as error:
        client.execute_request("session-A", {})
    assert error.value is original


def test_failed_preparation_clears_previous_environment(tmp_path):
    path = tmp_path / "portal.local.json"
    write_connection_settings(path)
    client = DiscoveryRequestClient(config(), connection_settings_reader=PortalConnectionSettingsReader(path))
    client.prepare_connection()
    path.unlink()
    with pytest.raises(ConnectionSettingsUnavailable):
        client.prepare_connection()
    assert client._session_connection_environment is None


def test_failed_open_clears_prepared_environment(tmp_path):
    path = tmp_path / "portal.local.json"
    write_connection_settings(path)
    client = DiscoveryRequestClient(
        config(), runner=lambda *args, **kwargs: completed(success("request.open-session", {})),
        connection_settings_reader=PortalConnectionSettingsReader(path),
    )
    with pytest.raises(DiscoveryRequestError):
        client.open_session()
    assert client._session_connection_environment is None
