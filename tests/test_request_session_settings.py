import sqlite3
from contextlib import closing

import pytest

from mcegold_discovery_publication_collector import request_session_settings
from mcegold_discovery_publication_collector.request_session_settings import (
    PortalRequestSessionReader,
    RequestSessionLookupError,
)


def create_database(path, value):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE ApplicationSetting (SettingKey TEXT PRIMARY KEY, SettingValue TEXT)")
        connection.execute("INSERT INTO ApplicationSetting VALUES (?, ?)", ("Discovery.RequestSessionId", value))
        connection.execute("INSERT INTO ApplicationSetting VALUES (?, ?)", ("Discovery.RequestSessionStartedUtc", "original"))
        connection.commit()


@pytest.mark.parametrize("value, expected", [("session-A", "session-A"), ("  session-A\n", "session-A"),
                                          (None, None), ("", None), (" \t\n", None)])
def test_read_session_without_changing_database(tmp_path, value, expected):
    path = tmp_path / "portal #1.db"
    create_database(path, value)
    before = path.read_bytes()
    assert PortalRequestSessionReader(str(path)).read() == expected
    assert path.read_bytes() == before


def test_missing_row_is_unavailable(tmp_path):
    path = tmp_path / "portal.db"
    create_database(path, "A")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DELETE FROM ApplicationSetting WHERE SettingKey = 'Discovery.RequestSessionId'")
        connection.commit()
    assert PortalRequestSessionReader(str(path)).read() is None


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(RequestSessionLookupError):
        PortalRequestSessionReader(str(path)).read()
    assert not path.exists()


def test_missing_table_is_not_created(tmp_path):
    path = tmp_path / "portal.db"
    with closing(sqlite3.connect(path)):
        pass
    before = path.read_bytes()
    with pytest.raises(RequestSessionLookupError):
        PortalRequestSessionReader(str(path)).read()
    assert path.read_bytes() == before


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_lock_failure_does_not_cache_and_next_read_recovers(tmp_path, journal_mode):
    path = tmp_path / "portal.db"
    create_database(path, "A")
    reader = PortalRequestSessionReader(str(path), busy_timeout_ms=10)
    assert reader.read() == "A"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute(f"PRAGMA journal_mode={journal_mode}")
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE ApplicationSetting SET SettingValue = 'B' WHERE SettingKey = 'Discovery.RequestSessionId'")
        if journal_mode == "DELETE":
            with pytest.raises(RequestSessionLookupError) as error:
                reader.read()
            assert isinstance(error.value.__cause__, sqlite3.OperationalError)
        else:
            # WAL readers see committed data while a writer is active.
            assert reader.read() == "A"
        writer.commit()
    assert reader.read() == "B"


def test_connection_is_read_only_and_explicitly_closed(tmp_path, monkeypatch):
    path = tmp_path / "portal.db"
    create_database(path, "A")
    connect = sqlite3.connect
    connections = []

    def tracked_connect(database_uri, **kwargs):
        assert database_uri.endswith("?mode=ro")
        assert kwargs == {"uri": True, "timeout": 0.025}
        connection = connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE ApplicationSetting SET SettingValue = 'changed'")
        connections.append(connection)
        return connection

    monkeypatch.setattr(request_session_settings.sqlite3, "connect", tracked_connect)
    assert PortalRequestSessionReader(str(path), busy_timeout_ms=25).read() == "A"
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_access_error_is_reported_without_stale_id(tmp_path, monkeypatch):
    path = tmp_path / "portal.db"
    create_database(path, "A")
    reader = PortalRequestSessionReader(str(path))
    assert reader.read() == "A"

    def denied(*args, **kwargs):
        raise sqlite3.OperationalError("permission denied")

    monkeypatch.setattr(request_session_settings.sqlite3, "connect", denied)
    with pytest.raises(RequestSessionLookupError):
        reader.read()


def test_connection_closed_when_query_fails(tmp_path, monkeypatch):
    path = tmp_path / "empty.db"
    connection = sqlite3.connect(path)
    monkeypatch.setattr(request_session_settings.sqlite3, "connect", lambda *args, **kwargs: connection)
    with pytest.raises(RequestSessionLookupError):
        PortalRequestSessionReader(str(path)).read()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_empty_database_path_is_rejected():
    with pytest.raises(RequestSessionLookupError):
        PortalRequestSessionReader("").read()
