import logging
import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import UUID

from mcegold_discovery_publication_collector import connection_settings
from mcegold_discovery_publication_collector.connection_settings import (
    ConnectionSettingsUnavailable,
    PortalConnectionSettingsReader,
)
from mcegold_discovery_publication_collector.configuration import CollectorConfig
from mcegold_discovery_publication_collector.control_settings import (
    DataRetentionSettings,
    DataRetentionSettingsReadResult,
    CollectorControlSettingsReader,
)
from mcegold_discovery_publication_collector.measurement_location_lookup import (
    MeasurementLocationLookupError,
    MeasurementLocationLookupResult,
)
from mcegold_discovery_publication_collector.measurement_persistence import MeasurementPersistenceError
from mcegold_discovery_publication_collector.measurement_publication import MeasurementPublicationParser
from mcegold_discovery_publication_collector.publication_client import (
    CliFault,
    ConnectorCliError,
    InvalidSessionError,
    Publication,
    PublicationClient,
)
from mcegold_discovery_publication_collector.publication_processor import PublicationProcessor
from mcegold_discovery_publication_collector.runtime_status import CollectorRuntimeStatusStore, RuntimeStatusError
from mcegold_discovery_publication_collector.worker import PublicationCollectorWorker


def config(discovery_database_path="discovery.db"):
    return CollectorConfig(
        cli_path="cli.exe",
        connector_config_path="connector.json",
        discovery_database_path=discovery_database_path,
        polling_interval_seconds=15,
    )


class FakeClient:
    def __init__(self):
        self.calls = []
        self.read_results = []

    def open_subscription(self):
        self.calls.append(("open",))
        return "session-1"

    def read_publication(self, session_id):
        self.calls.append(("read", session_id))
        if self.read_results:
            result = self.read_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return None

    def remove_publication(self, session_id):
        self.calls.append(("remove", session_id))

    def close_subscription(self, session_id):
        self.calls.append(("close", session_id))


class StopAfterWait:
    def __init__(self):
        self.waits = []
        self.set_called = False

    def is_set(self):
        return self.set_called

    def set(self):
        self.set_called = True

    def wait(self, timeout):
        self.waits.append(timeout)
        self.set_called = True
        return True


class StopAfterWaits:
    def __init__(self, wait_count, after_wait=None):
        self.wait_count = wait_count
        self.after_wait = after_wait
        self.waits = []
        self.set_called = False

    def is_set(self):
        return self.set_called

    def set(self):
        self.set_called = True

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.after_wait:
            self.after_wait(len(self.waits))
        if len(self.waits) >= self.wait_count:
            self.set_called = True
        return self.set_called


class FailingRuntimeStatus:
    def __init__(self):
        self.calls = []

    def heartbeat(self):
        self.calls.append("heartbeat")
        raise RuntimeStatusError("status database unavailable")

    def mark_running(self):
        self.calls.append("mark_running")
        raise RuntimeStatusError("status database unavailable")

    def mark_poll(self):
        self.calls.append("mark_poll")
        raise RuntimeStatusError("status database unavailable")

    def mark_publication(self):
        self.calls.append("mark_publication")
        raise RuntimeStatusError("status database unavailable")

    def mark_error(self, message):
        self.calls.append(("mark_error", message))
        raise RuntimeStatusError("status database unavailable")


class FakeRetentionSettingsReader:
    def __init__(self, *settings):
        self.settings = list(settings) or [DataRetentionSettings(enabled=False, days=90)]
        self.read_count = 0

    def read(self):
        self.read_count += 1
        index = min(self.read_count - 1, len(self.settings) - 1)
        return DataRetentionSettingsReadResult(self.settings[index])


class FakeRetentionService:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def run_cleanup(self, settings, now_utc=None):
        self.calls.append((settings, now_utc))
        if self.error:
            raise self.error


class Clock:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        index = min(self.calls - 1, len(self.values) - 1)
        return self.values[index]


def publication(message_id="message-1", location_uuid="location-1", segment_uuid=None):
    measurement = {
        "UUID": f"measurement-{message_id}",
        "recorded": {"dateTime": "2018-09-22T03:34:17Z"},
        "data": {"number": {"numeric": "1"}},
    }
    if segment_uuid:
        measurement["segment"] = {"UUID": segment_uuid}
    return Publication(
        session_id="session-1",
        message_id=message_id,
        payload={
            "syncMeasurements": {
                "dataArea": {
                    "measurements": [
                        {
                            "measurementLocation": {"UUID": location_uuid, "shortName": "Pump Bearing"},
                            "measurement": [measurement],
                        }
                    ]
                }
            }
        },
        envelope={},
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


def create_application_setting_table(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE ApplicationSetting (
                SettingKey TEXT PRIMARY KEY,
                SettingValue TEXT NULL,
                ValueType TEXT NOT NULL,
                UpdatedUtc TEXT NOT NULL
            )
            """
        )
        connection.commit()


def set_application_setting(db_path, key, value, value_type):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO ApplicationSetting(SettingKey, SettingValue, ValueType, UpdatedUtc)
            VALUES (?, ?, ?, '2026-08-20T12:00:00Z')
            ON CONFLICT(SettingKey) DO UPDATE SET
                SettingValue = excluded.SettingValue,
                ValueType = excluded.ValueType,
                UpdatedUtc = excluded.UpdatedUtc
            """,
            (key, value, value_type),
        )
        connection.commit()


def set_control_settings(db_path, enabled="true", polling_interval="15"):
    set_application_setting(db_path, "Collector.Enabled", enabled, "Boolean")
    set_application_setting(db_path, "Collector.PollingIntervalSeconds", polling_interval, "Integer")


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


def set_retention_settings(db_path, enabled="false", days="90"):
    set_application_setting(db_path, "DataRetention.Enabled", enabled, "Boolean")
    set_application_setting(db_path, "DataRetention.Days", days, "Integer")


def create_controlled_database(db_path, enabled="true", polling_interval="15"):
    create_runtime_status_table(db_path)
    create_application_setting_table(db_path)
    set_control_settings(db_path, enabled=enabled, polling_interval=polling_interval)


def control_reader(db_path):
    return CollectorControlSettingsReader(str(db_path), fallback_polling_interval_seconds=15)


def fetch_runtime_status(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM CollectorRuntimeStatus WHERE CollectorId = 'publication-collector'"
        ).fetchone()


class FakeLookup:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def lookup(self, _measurement_location_uuid):
        if self.error:
            raise self.error
        return self.result


class FakeTrigger:
    def __init__(self):
        self.unknown_locations = []

    def on_unknown_measurement_location(self, identity, publication, segment_uuid_if_known=None):
        self.unknown_locations.append((identity, publication, segment_uuid_if_known))
        return False


class FakePersistence:
    def __init__(self, error=None):
        self.error = error
        self.rows = []

    def persist_publication(self, rows):
        if self.error:
            raise self.error
        self.rows.extend(rows)
        return len(rows)


def processor(is_known=True, lookup_error=None, trigger=None, persistence=None):
    return PublicationProcessor(
        MeasurementPublicationParser(),
        FakeLookup(result=MeasurementLocationLookupResult(is_known=is_known), error=lookup_error),
        trigger or FakeTrigger(),
        persistence or FakePersistence(),
    )


def test_open_session_state_is_retained_between_polls():
    client = FakeClient()
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()
    worker.poll_once()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("read", "session-1"),
    ]


def test_no_publication_keeps_session_and_does_not_remove_or_reopen():
    client = FakeClient()
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()

    assert worker.session_id == "session-1"
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
    ]


def test_no_publication_polling_logs_are_debug_only(caplog):
    client = FakeClient()
    worker = PublicationCollectorWorker(config(), client=client)

    with caplog.at_level(logging.DEBUG, logger="mcegold_discovery_publication_collector.worker"):
        worker.poll_once()

    messages = [record.getMessage() for record in caplog.records]
    assert "Starting publication drain cycle." in messages
    assert "Polling publication session..." in messages
    assert "No publication available; drain cycle complete." in messages
    assert all(record.levelno == logging.DEBUG for record in caplog.records if "publication" in record.getMessage().lower())
    assert ("remove", "session-1") not in client.calls


def test_invalid_session_recovery_reopens_on_next_poll():
    client = FakeClient()
    client.read_results.append(
        InvalidSessionError(
            "expired",
            [],
            fault=CliFault("remote", "SessionExpired", "Session expired", 410),
        )
    )
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()
    worker.poll_once()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("open",),
        ("read", "session-1"),
    ]


def test_successful_publication_handling_removes_publication():
    client = FakeClient()
    client.read_results.append(publication())
    handled = []
    worker = PublicationCollectorWorker(config(), client=client, handler=handled.append)

    worker.poll_once()

    assert handled[0].message_id == "message-1"
    assert ("remove", "session-1") in client.calls
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
    ]


def test_known_location_processing_removes_publication():
    client = FakeClient()
    client.read_results.append(publication())
    worker = PublicationCollectorWorker(config(), client=client, handler=processor(is_known=True))

    worker.poll_once()

    assert ("remove", "session-1") in client.calls


def test_unknown_location_processing_triggers_boundary_and_does_not_remove_publication():
    client = FakeClient()
    client.read_results.append(publication())
    trigger = FakeTrigger()
    worker = PublicationCollectorWorker(config(), client=client, handler=processor(is_known=False, trigger=trigger))

    worker.poll_once()

    assert trigger.unknown_locations[0][0].uuid == "location-1"
    assert ("remove", "session-1") not in client.calls


def test_malformed_measurement_publication_does_not_remove_publication():
    client = FakeClient()
    client.read_results.append(
        Publication(
            session_id="session-1",
            message_id="message-1",
            payload={"notMeasurement": {}},
            envelope={},
        )
    )
    worker = PublicationCollectorWorker(config(), client=client, handler=processor(is_known=True))

    worker.poll_once()

    assert ("remove", "session-1") not in client.calls


def test_lookup_failure_does_not_remove_publication():
    client = FakeClient()
    client.read_results.append(publication())
    worker = PublicationCollectorWorker(
        config(),
        client=client,
        handler=processor(lookup_error=MeasurementLocationLookupError("database unavailable")),
    )

    worker.poll_once()

    assert ("remove", "session-1") not in client.calls


def test_persistence_failure_does_not_remove_publication():
    client = FakeClient()
    client.read_results.append(publication())
    worker = PublicationCollectorWorker(
        config(),
        client=client,
        handler=processor(persistence=FakePersistence(error=MeasurementPersistenceError("database unavailable"))),
    )

    worker.poll_once()

    assert ("remove", "session-1") not in client.calls


def test_failed_handling_does_not_remove_publication():
    client = FakeClient()
    client.read_results.append(publication())

    def failing_handler(_publication):
        raise RuntimeError("handler failed")

    worker = PublicationCollectorWorker(config(), client=client, handler=failing_handler)

    worker.poll_once()

    assert ("remove", "session-1") not in client.calls
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
    ]


def test_temporary_cli_failure_does_not_discard_session():
    client = FakeClient()
    client.read_results.append(ConnectorCliError("temporary", []))
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()

    assert worker.session_id == "session-1"
    assert ("remove", "session-1") not in client.calls
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
    ]


def test_three_queued_successful_publications_drain_before_waiting():
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2"), publication("message-3")])
    handled = []
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(config(), client=client, handler=handled.append, stop_event=stop_event)

    worker.run()

    assert [item.message_id for item in handled] == ["message-1", "message-2", "message-3"]
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_empty_queue_waits_once_after_one_read():
    client = FakeClient()
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(config(), client=client, stop_event=stop_event)

    worker.run()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_two_successful_publications_then_empty_waits_once_after_drain():
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2")])
    handled = []
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(config(), client=client, handler=handled.append, stop_event=stop_event)

    worker.run()

    assert [item.message_id for item in handled] == ["message-1", "message-2"]
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_processing_failure_stops_drain_and_waits_before_retry():
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2")])
    stop_event = StopAfterWait()

    def failing_handler(_publication):
        raise RuntimeError("handler failed")

    worker = PublicationCollectorWorker(config(), client=client, handler=failing_handler, stop_event=stop_event)

    worker.run()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_persistence_failure_stops_drain_and_waits_before_retry():
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2")])
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(
        config(),
        client=client,
        handler=processor(persistence=FakePersistence(error=MeasurementPersistenceError("database unavailable"))),
        stop_event=stop_event,
    )

    worker.run()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_generic_read_failure_stops_drain_and_waits_without_tight_retry():
    client = FakeClient()
    client.read_results.extend([ConnectorCliError("temporary", []), publication("message-2")])
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(config(), client=client, stop_event=stop_event)

    worker.run()

    assert worker.session_id is None
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]


def test_invalid_session_stops_drain_and_reopens_on_next_cycle():
    client = FakeClient()
    client.read_results.extend(
        [
            InvalidSessionError(
                "expired",
                [],
                fault=CliFault("remote", "SessionNotFound", "Session not found", 404),
            ),
            publication("message-1"),
        ]
    )
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()
    worker.poll_once()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
    ]


def test_successful_discovery_then_sibling_publication_reads_immediately():
    class LookupBecomesKnownAfterDiscovery:
        def __init__(self):
            self.known = set()

        def lookup(self, location_uuid):
            return MeasurementLocationLookupResult(is_known=location_uuid in self.known)

    class DiscoverySucceeds:
        def __init__(self, lookup):
            self.lookup = lookup
            self.unknown_locations = []

        def on_unknown_measurement_location(self, identity, _publication, _segment_uuid_if_known=None):
            self.unknown_locations.append(identity.uuid)
            self.lookup.known.update({"location-1", "location-2"})
            return True

    lookup = LookupBecomesKnownAfterDiscovery()
    trigger = DiscoverySucceeds(lookup)
    persistence = FakePersistence()
    handler = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)
    client = FakeClient()
    client.read_results.extend(
        [
            publication("message-1", "location-1", "segment-1"),
            publication("message-2", "location-2", "segment-1"),
        ]
    )
    worker = PublicationCollectorWorker(config(), client=client, handler=handler)

    worker.poll_once()

    assert trigger.unknown_locations == ["location-1"]
    assert [row.measurement_location_uuid for row in persistence.rows] == ["location-1", "location-2"]
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("read", "session-1"),
    ]


def test_graceful_shutdown_during_drain_stops_reading_and_closes_session():
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2")])
    stop_event = StopAfterWait()
    handled = []

    def stopping_handler(item):
        handled.append(item)
        stop_event.set()

    worker = PublicationCollectorWorker(config(), client=client, handler=stopping_handler, stop_event=stop_event)

    worker.run()

    assert [item.message_id for item in handled] == ["message-1"]
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == []


def test_graceful_shutdown_attempts_close_subscription():
    client = FakeClient()
    worker = PublicationCollectorWorker(config(), client=client)
    worker.session_id = "session-1"

    worker.shutdown()

    assert ("close", "session-1") in client.calls
    assert worker.session_id is None


def test_runtime_status_startup_creates_running_row(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = FakeClient()
    stop_event = StopAfterWait()
    runtime_status = CollectorRuntimeStatusStore(str(db_path))
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=runtime_status,
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert row["CollectorId"] == "publication-collector"
    UUID(row["InstanceId"])
    assert row["StartedUtc"]
    assert row["RuntimeState"] == "Running"
    assert row["LastHeartbeatUtc"]


def test_runtime_status_poll_attempt_updates_last_poll(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = FakeClient()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(config(str(db_path)), client=client, runtime_status=runtime_status)

    worker.poll_once()

    row = fetch_runtime_status(db_path)
    assert row["RuntimeState"] == "Running"
    assert row["LastPollUtc"] is not None


def test_runtime_status_successful_publication_updates_last_publication_after_remove(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = FakeClient()
    client.read_results.append(publication())
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        handler=processor(is_known=True),
        runtime_status=runtime_status,
    )

    worker.poll_once()

    row = fetch_runtime_status(db_path)
    assert ("remove", "session-1") in client.calls
    assert row["RuntimeState"] == "Running"
    assert row["LastPublicationUtc"] is not None


def test_runtime_status_open_failure_marks_error_and_preserves_retry_behavior(tmp_path):
    class OpenFails(FakeClient):
        def open_subscription(self):
            self.calls.append(("open",))
            raise ConnectorCliError("open failed", [])

    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = OpenFails()
    stop_event = StopAfterWait()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=runtime_status,
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [("open",)]
    assert stop_event.waits == [15]
    assert row["RuntimeState"] == "Error"
    assert row["LastErrorUtc"] is not None
    assert row["LastError"] == "Failed to open subscription session."


def test_missing_connection_settings_mark_idle_without_open_cli_call(tmp_path, caplog):
    class UnconfiguredClient(FakeClient):
        def open_subscription(self):
            self.calls.append(("open",))
            raise ConnectionSettingsUnavailable("missing")

    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = UnconfiguredClient()
    stop_event = StopAfterWait()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=runtime_status,
    )

    with caplog.at_level(logging.INFO, logger="mcegold_discovery_publication_collector.worker"):
        worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [("open",)]
    assert row["RuntimeState"] == "Idle"
    assert row["LastError"] is None
    assert "Waiting for MCEGold Data Services connection settings." in caplog.text
    assert "reason=missing" in caplog.text


def test_missing_implicit_portal_config_does_not_block_worker_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("MCEGOLD_PORTAL_LOCAL_CONFIG_PATH", raising=False)
    monkeypatch.setattr(connection_settings, "DEFAULT_PORTAL_LOCAL_CONFIG_PATH", str(tmp_path / "missing-portal.local.json"))
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["publication", "open-subscription"]:
            return completed(success("publication.open-subscription", {"sessionId": "session-1"}))
        if command[1:3] == ["publication", "read"]:
            return completed(
                fault(
                    "publication.read",
                    404,
                    "NoPublicationAvailable",
                    "No publication is available for the subscription session.",
                ),
                1,
            )
        return completed(success("publication.close-subscription", {"sessionId": "session-1"}))

    client = PublicationClient(config(str(db_path)), runner=runner)
    stop_event = StopAfterWait()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=runtime_status,
    )

    worker.run()

    assert [call[0][1:3] for call in calls] == [
        ["publication", "open-subscription"],
        ["publication", "read"],
        ["publication", "close-subscription"],
    ]
    assert calls[0][1]["env"] is None


def test_settings_saved_between_cycles_open_subscription_without_restart(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    connection_path = tmp_path / "portal.local.json"
    connection_path.write_text(json.dumps({"mcegoldDataServicesUrl": "https://mcegold.example.com"}), encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["publication", "open-subscription"]:
            return completed(success("publication.open-subscription", {"sessionId": "session-1"}))
        if command[1:3] == ["publication", "read"]:
            return completed(
                fault(
                    "publication.read",
                    404,
                    "NoPublicationAvailable",
                    "No publication is available for the subscription session.",
                ),
                1,
            )
        return completed(success("publication.close-subscription", {"sessionId": "session-1"}))

    def after_wait(count):
        if count == 1:
            write_connection_settings(connection_path)

    client = PublicationClient(
        config(str(db_path)),
        runner=runner,
        connection_settings_reader=PortalConnectionSettingsReader(connection_path),
    )
    stop_event = StopAfterWaits(2, after_wait=after_wait)
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=runtime_status,
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert [call[0][1:3] for call in calls] == [
        ["publication", "open-subscription"],
        ["publication", "read"],
        ["publication", "close-subscription"],
    ]
    assert calls[0][1]["env"]["MCEGOLD_HOST"] == "https://mcegold.example.com"
    assert stop_event.waits == [15, 15]
    assert row["RuntimeState"] == "Running"
    assert row["LastPollUtc"] is not None


def test_configured_open_cli_failure_remains_error(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    connection_path = tmp_path / "portal.local.json"
    write_connection_settings(connection_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(fault("publication.open-subscription", 401, "Unauthorized", "Unauthorized"), 1)

    client = PublicationClient(
        config(str(db_path)),
        runner=runner,
        connection_settings_reader=PortalConnectionSettingsReader(connection_path),
    )
    stop_event = StopAfterWait()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(config(str(db_path)), client=client, stop_event=stop_event, runtime_status=runtime_status)

    worker.run()

    row = fetch_runtime_status(db_path)
    assert len(calls) == 1
    assert row["RuntimeState"] == "Error"
    assert row["LastError"] == "Failed to open subscription session."


def test_invalid_session_reopen_rereads_connection_settings(tmp_path):
    connection_path = tmp_path / "portal.local.json"
    write_connection_settings(connection_path, host="https://first.example.com", password="first-secret", api_key="first-key")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["publication", "open-subscription"]:
            return completed(success("publication.open-subscription", {"sessionId": f"session-{len(calls)}"}))
        if command[1:3] == ["publication", "read"] and len([call for call in calls if call[0][1:3] == ["publication", "read"]]) == 1:
            write_connection_settings(
                connection_path,
                host="https://second.example.com",
                password="second-secret",
                api_key="second-key",
            )
            return completed(fault("publication.read", 410, "SessionExpired", "Session expired"), 1)
        return completed(
            fault(
                "publication.read",
                404,
                "NoPublicationAvailable",
                "No publication is available for the subscription session.",
            ),
            1,
        )

    client = PublicationClient(config(), runner=runner, connection_settings_reader=PortalConnectionSettingsReader(connection_path))
    worker = PublicationCollectorWorker(config(), client=client)

    worker.poll_once()
    worker.poll_once()

    open_environments = [call[1]["env"] for call in calls if call[0][1:3] == ["publication", "open-subscription"]]
    assert [environment["MCEGOLD_HOST"] for environment in open_environments] == [
        "https://first.example.com",
        "https://second.example.com",
    ]
    assert open_environments[1]["MCEGOLD_PASSWORD"] == "second-secret"


def test_runtime_status_read_failure_recovers_to_running_after_successful_cycle(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = FakeClient()
    client.read_results.append(ConnectorCliError("temporary", []))
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(config(str(db_path)), client=client, runtime_status=runtime_status)

    worker.poll_once()
    error_row = fetch_runtime_status(db_path)
    worker.poll_once()
    recovered_row = fetch_runtime_status(db_path)

    assert error_row["RuntimeState"] == "Error"
    assert error_row["LastError"] == "Connector CLI failed while polling publications."
    assert recovered_row["RuntimeState"] == "Running"
    assert recovered_row["LastError"] == "Connector CLI failed while polling publications."


def test_runtime_status_shutdown_writes_final_heartbeat_and_keeps_close_behavior(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_runtime_status_table(db_path)
    client = FakeClient()
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(config(str(db_path)), client=client, runtime_status=runtime_status)
    worker.session_id = "session-1"

    worker.shutdown()

    row = fetch_runtime_status(db_path)
    assert ("close", "session-1") in client.calls
    assert worker.session_id is None
    assert row["LastHeartbeatUtc"] is not None
    assert row["RuntimeState"] == "Running"


def test_runtime_status_write_failure_does_not_block_polling_or_loop_status_calls(caplog):
    client = FakeClient()
    runtime_status = FailingRuntimeStatus()
    worker = PublicationCollectorWorker(config(), client=client, runtime_status=runtime_status)

    with caplog.at_level(logging.WARNING, logger="mcegold_discovery_publication_collector.worker"):
        worker.poll_once()

    assert client.calls == [
        ("open",),
        ("read", "session-1"),
    ]
    assert runtime_status.calls == ["heartbeat", "heartbeat", "mark_running", "mark_poll", "mark_running"]
    assert "Runtime status telemetry unavailable; will retry" in caplog.text


def test_runtime_status_write_failure_retries_after_schema_becomes_available(tmp_path, caplog):
    db_path = tmp_path / "discovery_portal.db"
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(config(str(db_path)), client=FakeClient(), runtime_status=runtime_status)

    with caplog.at_level(logging.WARNING, logger="mcegold_discovery_publication_collector.worker"):
        worker._heartbeat()

    assert "Runtime status telemetry unavailable; will retry" in caplog.text

    create_runtime_status_table(db_path)
    worker._mark_idle()

    row = fetch_runtime_status(db_path)
    assert row is not None
    assert row["RuntimeState"] == "Idle"


def test_retention_startup_enabled_runs_cleanup_once_before_first_loop() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    settings = DataRetentionSettings(enabled=True, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(settings, settings),
        retention_service=retention_service,
        clock=Clock(now, now),
    )

    worker.run()

    assert retention_service.calls == [(settings, now)]


def test_retention_startup_disabled_does_not_run_cleanup() -> None:
    settings = DataRetentionSettings(enabled=False, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(settings),
        retention_service=retention_service,
        clock=Clock(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)),
    )

    worker.run()

    assert retention_service.calls == []


def test_retention_disabled_to_enabled_runs_cleanup_on_next_loop() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    disabled = DataRetentionSettings(enabled=False, days=90)
    enabled = DataRetentionSettings(enabled=True, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(disabled, enabled),
        retention_service=retention_service,
        clock=Clock(now),
    )

    worker.run()

    assert retention_service.calls == [(enabled, now)]


def test_retention_enabled_to_disabled_stops_cleanup_even_after_interval() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    enabled = DataRetentionSettings(enabled=True, days=90)
    disabled = DataRetentionSettings(enabled=False, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(enabled, disabled),
        retention_service=retention_service,
        clock=Clock(now, now + timedelta(hours=24)),
    )

    worker.run()

    assert retention_service.calls == [(enabled, now)]


def test_retention_days_change_runs_cleanup_with_new_setting() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    ninety_days = DataRetentionSettings(enabled=True, days=90)
    thirty_days = DataRetentionSettings(enabled=True, days=30)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(ninety_days, thirty_days),
        retention_service=retention_service,
        clock=Clock(now, now + timedelta(minutes=1)),
    )

    worker.run()

    assert retention_service.calls == [
        (ninety_days, now),
        (thirty_days, now + timedelta(minutes=1)),
    ]


def test_retention_unchanged_settings_wait_until_24_hour_interval() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    settings = DataRetentionSettings(enabled=True, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWaits(2),
        retention_settings_reader=FakeRetentionSettingsReader(settings, settings, settings),
        retention_service=retention_service,
        clock=Clock(now, now + timedelta(hours=23, minutes=59), now + timedelta(hours=24)),
    )

    worker.run()

    assert retention_service.calls == [
        (settings, now),
        (settings, now + timedelta(hours=24)),
    ]


def test_retention_failed_cleanup_does_not_retry_before_interval() -> None:
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    settings = DataRetentionSettings(enabled=True, days=90)
    retention_service = FakeRetentionService(error=RuntimeError("database locked"))
    worker = PublicationCollectorWorker(
        config(),
        client=FakeClient(),
        stop_event=StopAfterWait(),
        retention_settings_reader=FakeRetentionSettingsReader(settings, settings),
        retention_service=retention_service,
        clock=Clock(now, now + timedelta(minutes=1)),
    )

    worker.run()

    assert retention_service.calls == [(settings, now)]


def test_enabled_startup_opens_subscription_and_polls(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    client = FakeClient()
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15]
    assert row["RuntimeState"] == "Running"
    assert row["LastPollUtc"] is not None


def test_retention_runs_while_collector_processing_is_disabled(tmp_path):
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="false", polling_interval="15")
    client = FakeClient()
    retention_settings = DataRetentionSettings(enabled=True, days=90)
    retention_service = FakeRetentionService()
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=StopAfterWait(),
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
        retention_settings_reader=FakeRetentionSettingsReader(retention_settings, retention_settings),
        retention_service=retention_service,
        clock=Clock(now, now),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert retention_service.calls == [(retention_settings, now)]
    assert client.calls == []
    assert row["RuntimeState"] == "Idle"


def test_disabled_startup_enters_idle_without_opening_or_polling(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="false", polling_interval="15")
    client = FakeClient()
    stop_event = StopAfterWait()
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == []
    assert stop_event.waits == [15]
    assert row["RuntimeState"] == "Idle"
    assert row["LastHeartbeatUtc"] is not None
    assert row["LastPollUtc"] is None


def test_retention_schema_failure_does_not_force_runtime_error_when_publication_is_healthy(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    set_retention_settings(db_path, enabled="true", days="90")
    client = FakeClient()
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=StopAfterWait(),
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
        retention_settings_reader=FakeRetentionSettingsReader(
            DataRetentionSettings(enabled=True, days=90),
            DataRetentionSettings(enabled=True, days=90),
        ),
        retention_service=FakeRetentionService(error=RuntimeError("FactMeasurement missing")),
        clock=Clock(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert row["RuntimeState"] == "Running"


def test_disable_transition_finishes_in_flight_publication_closes_session_and_stops_reading(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    client = FakeClient()
    client.read_results.extend([publication("message-1"), publication("message-2")])
    handled = []
    stop_event = StopAfterWait()

    def disabling_handler(item):
        handled.append(item)
        set_application_setting(db_path, "Collector.Enabled", "false", "Boolean")

    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        handler=disabling_handler,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert [item.message_id for item in handled] == ["message-1"]
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("remove", "session-1"),
        ("close", "session-1"),
    ]
    assert row["RuntimeState"] == "Idle"
    assert row["LastPublicationUtc"] is not None


def test_disable_with_no_open_session_is_idempotent(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="false", polling_interval="15")
    client = FakeClient()
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker._enter_idle()
    worker._enter_idle()

    row = fetch_runtime_status(db_path)
    assert client.calls == []
    assert row["RuntimeState"] == "Idle"


def test_reenable_transition_resumes_polling_without_restart(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="false", polling_interval="15")
    client = FakeClient()

    def after_wait(count):
        if count == 1:
            set_application_setting(db_path, "Collector.Enabled", "true", "Boolean")

    stop_event = StopAfterWaits(2, after_wait=after_wait)
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [
        ("open",),
        ("read", "session-1"),
        ("close", "session-1"),
    ]
    assert stop_event.waits == [15, 15]
    assert row["RuntimeState"] == "Running"
    assert row["LastPollUtc"] is not None


def test_dynamic_polling_interval_change_affects_next_wait(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    client = FakeClient()

    def after_wait(count):
        if count == 1:
            set_application_setting(db_path, "Collector.PollingIntervalSeconds", "30", "Integer")

    stop_event = StopAfterWaits(2, after_wait=after_wait)
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    assert stop_event.waits == [15, 30]


def test_enabled_failure_sets_error_and_disable_clears_to_idle(tmp_path):
    class OpenFailsOnce(FakeClient):
        def __init__(self):
            super().__init__()
            self.failed = False

        def open_subscription(self):
            self.calls.append(("open",))
            if not self.failed:
                self.failed = True
                raise ConnectorCliError("open failed", [])
            return "session-1"

    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    client = OpenFailsOnce()

    def after_wait(count):
        if count == 1:
            set_application_setting(db_path, "Collector.Enabled", "false", "Boolean")

    stop_event = StopAfterWaits(2, after_wait=after_wait)
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        stop_event=stop_event,
        runtime_status=CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1"),
        control_settings_reader=control_reader(db_path),
    )

    worker.run()

    row = fetch_runtime_status(db_path)
    assert client.calls == [("open",)]
    assert row["RuntimeState"] == "Idle"
    assert row["LastError"] == "Failed to open subscription session."


def test_disabled_heartbeat_does_not_update_poll_or_publication_timestamps(tmp_path):
    db_path = tmp_path / "discovery_portal.db"
    create_controlled_database(db_path, enabled="true", polling_interval="15")
    client = FakeClient()
    client.read_results.append(publication("message-1"))
    runtime_status = CollectorRuntimeStatusStore(str(db_path), instance_id="instance-1")
    worker = PublicationCollectorWorker(
        config(str(db_path)),
        client=client,
        handler=processor(is_known=True),
        runtime_status=runtime_status,
        control_settings_reader=control_reader(db_path),
    )
    worker.poll_once()
    active_row = fetch_runtime_status(db_path)
    set_application_setting(db_path, "Collector.Enabled", "false", "Boolean")
    stop_event = StopAfterWait()
    worker.stop_event = stop_event

    worker.run()

    idle_row = fetch_runtime_status(db_path)
    assert idle_row["RuntimeState"] == "Idle"
    assert idle_row["LastHeartbeatUtc"] >= active_row["LastHeartbeatUtc"]
    assert idle_row["LastPollUtc"] == active_row["LastPollUtc"]
    assert idle_row["LastPublicationUtc"] == active_row["LastPublicationUtc"]
