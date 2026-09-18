import sqlite3

import pytest

from mcegold_discovery_publication_collector import runtime_status
from mcegold_discovery_publication_collector.runtime_status import (
    CollectorRuntimeStatusStore,
    RuntimeStatusError,
    safe_last_error,
)


def create_runtime_status_table(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE CollectorRuntimeStatus (
                CollectorId TEXT PRIMARY KEY,
                InstanceId TEXT NOT NULL,
                StartedUtc TEXT NOT NULL,
                LastHeartbeatUtc TEXT NOT NULL,
                RuntimeState TEXT NOT NULL
                    CHECK (RuntimeState IN ('Running', 'Idle', 'Error')),
                LastPollUtc TEXT NULL,
                LastPublicationUtc TEXT NULL,
                LastErrorUtc TEXT NULL,
                LastError TEXT NULL
                    CHECK (LastError IS NULL OR length(LastError) <= 2048)
            )
            """
        )
        connection.commit()


def fetch_status(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM CollectorRuntimeStatus").fetchone()


@pytest.fixture
def now_sequence(monkeypatch):
    values = iter(
        [
            "2026-08-20T12:00:00Z",
            "2026-08-20T12:00:01Z",
            "2026-08-20T12:00:02Z",
            "2026-08-20T12:00:03Z",
            "2026-08-20T12:00:04Z",
        ]
    )
    monkeypatch.setattr(runtime_status, "utc_now", lambda: next(values))


def test_first_upsert_creates_running_status_row(tmp_path, now_sequence) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    store.mark_running()

    row = fetch_status(db_path)
    assert row["CollectorId"] == "publication-collector"
    assert row["InstanceId"] == "instance-1"
    assert row["StartedUtc"] == "2026-08-20T12:00:00Z"
    assert row["LastHeartbeatUtc"] == "2026-08-20T12:00:01Z"
    assert row["RuntimeState"] == "Running"


def test_later_heartbeat_updates_same_collector_and_preserves_process_identity(tmp_path, now_sequence) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    store.mark_running()
    store.heartbeat()

    rows = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM CollectorRuntimeStatus").fetchall()

    assert len(rows) == 1
    assert rows[0]["InstanceId"] == "instance-1"
    assert rows[0]["StartedUtc"] == "2026-08-20T12:00:00Z"
    assert rows[0]["LastHeartbeatUtc"] == "2026-08-20T12:00:02Z"


def test_idle_is_accepted_by_writer_api(tmp_path, now_sequence) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    store.mark_idle()

    row = fetch_status(db_path)
    assert row["RuntimeState"] == "Idle"


def test_poll_publication_and_error_fields_update(tmp_path, now_sequence) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    store.mark_poll()
    store.mark_publication()
    store.mark_error("Failed to persist measurement.")

    row = fetch_status(db_path)
    assert row["RuntimeState"] == "Error"
    assert row["LastPollUtc"] == "2026-08-20T12:00:01Z"
    assert row["LastPublicationUtc"] == "2026-08-20T12:00:02Z"
    assert row["LastErrorUtc"] == "2026-08-20T12:00:03Z"
    assert row["LastError"] == "Failed to persist measurement."


def test_long_error_is_bounded_before_write(tmp_path, now_sequence) -> None:
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    store.mark_error("x" * 3000)

    row = fetch_status(db_path)
    assert len(row["LastError"]) == 2048
    assert row["LastError"].endswith("...")


def test_last_error_redacts_secret_like_values() -> None:
    message = "password=secret apiKey:abc Authorization: Bearer token"

    assert safe_last_error(message) == "password=***REDACTED*** apiKey:***REDACTED*** Authorization: ***REDACTED***"


def test_missing_status_table_raises_clear_runtime_status_error(tmp_path) -> None:
    db_path = tmp_path / "discovery_portal.db"
    store = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")

    with pytest.raises(RuntimeStatusError, match="schema v17 or later"):
        store.mark_running()
