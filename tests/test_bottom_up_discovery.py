import sqlite3

import pytest

from mcegold_discovery_publication_collector.bottom_up_discovery import (
    BottomUpDiscoveryError,
    BottomUpDiscoveryService,
    build_bottom_up_discovery_service,
)
from mcegold_discovery_publication_collector.configuration import CollectorConfig
from mcegold_discovery_publication_collector.connection_settings import ConnectionSettingsUnavailable
from mcegold_discovery_publication_collector.publication_client import CliFault, ConnectorCliError
from mcegold_discovery_publication_collector.request_session_settings import PortalRequestSessionReader
from mcegold_discovery_publication_collector.discovery_models import LocalSegment
from mcegold_discovery_publication_collector.discovery_persistence import (
    DiscoveryPersistenceError,
    SQLiteDiscoveryPersistence,
)
from mcegold_discovery_publication_collector.discovery_request_client import DiscoveryRequestError


class FakeRequestClient:
    def __init__(self, responses=None, fail_on_request_type=None):
        self.responses = responses or {}
        self.fail_on_request_type = fail_on_request_type
        self.requests = []
        self.opened = 0
        self.closed = []
        self.prepared = 0
        self.cleared = 0

    def prepare_connection(self):
        self.prepared += 1

    def clear_connection(self):
        self.cleared += 1

    def open_session(self):
        self.opened += 1
        return "request-session-1"

    def execute_request(self, session_id, request):
        self.requests.append((session_id, request))
        if request["requestType"] == self.fail_on_request_type:
            raise DiscoveryRequestError("temporary request failure")
        key = (request["requestType"], tuple(sorted(request.get("filters", {}).items())))
        return self.responses.get(key, empty_response(request["requestType"]))

    def close_session(self, session_id):
        self.closed.append(session_id)


class FakePersistence:
    def __init__(self, segment=None, site_exists=False, error=None):
        self.segment = segment
        self.site_exists_result = site_exists
        self.error = error
        self.persisted = []
        self.site_checks = []

    def get_segment(self, segment_uuid):
        return self.segment

    def site_exists(self, site_uuid):
        self.site_checks.append(site_uuid)
        return self.site_exists_result

    def persist_targeted_discovery(self, **kwargs):
        if self.error:
            raise self.error
        self.persisted.append(kwargs)
        return len(kwargs["measurement_locations"])


class FakeSessionReader:
    def __init__(self, session_id="portal-A"):
        self.session_id = session_id
        self.calls = 0

    def read(self):
        self.calls += 1
        return self.session_id


def shared_service(client=None, persistence=None, reader=None):
    client = client or FakeRequestClient(responses={
        ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)):
            measurement_locations_response("segment-1", ["location-1"]),
    })
    persistence = persistence or FakePersistence(segment=LocalSegment("segment-1", "site-1", 1))
    reader = reader or FakeSessionReader()
    return BottomUpDiscoveryService(
        client, persistence, use_shared_request_session=True, shared_session_reader=reader,
    )


def test_shared_session_replacement_is_read_between_workflows():
    reader = FakeSessionReader()
    service = shared_service(reader=reader)
    for session_id in ["portal-A", "portal-B", "portal-B"]:
        reader.session_id = session_id
        service.discover_measurement_location("location-1", "segment-1")
    assert [session for session, _ in service.request_client.requests] == ["portal-A", "portal-B", "portal-B"]
    assert reader.calls == 3
    assert service.request_client.opened == 0
    assert service.request_client.closed == []
    assert service.request_client.prepared == 3
    assert service.request_client.cleared == 3


def test_shared_workflow_keeps_acquired_id_across_multiple_requests():
    reader = FakeSessionReader()
    client = FakeRequestClient(responses={
        ("GetMeasurementLocations", (("measurementLocationUuid", "location-1"),)):
            measurement_locations_response("segment-1", ["location-1"]),
        ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)):
            measurement_locations_response("segment-1", ["location-1"]),
    })
    execute = client.execute_request

    def replacing_execute(session_id, request):
        reader.session_id = "portal-B"
        return execute(session_id, request)

    client.execute_request = replacing_execute
    shared_service(client=client, reader=reader).discover_measurement_location("location-1")
    assert [session for session, _ in client.requests] == ["portal-A", "portal-A"]
    assert reader.calls == 1
    assert client.closed == []


@pytest.mark.parametrize("failure", ["missing", "database", "prepare", "request", "parse", "persistence"])
def test_shared_failures_never_open_or_close_session(tmp_path, failure):
    service = shared_service()
    if failure == "missing":
        service.shared_session_reader = FakeSessionReader(None)
    elif failure == "database":
        service.shared_session_reader = PortalRequestSessionReader(str(tmp_path / "missing.db"))
    elif failure == "prepare":
        def fail_prepare():
            raise ConnectionSettingsUnavailable("settings incomplete")
        service.request_client.prepare_connection = fail_prepare
    elif failure == "request":
        service.request_client.fail_on_request_type = "GetMeasurementLocations"
    elif failure == "parse":
        service.request_client.responses = {
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): "invalid payload",
        }
    else:
        service.persistence.error = DiscoveryPersistenceError("write failed")
    with pytest.raises(BottomUpDiscoveryError):
        service.discover_measurement_location("location-1", "segment-1")
    assert service.request_client.opened == 0
    assert service.request_client.closed == []
    assert service.request_client.cleared == 1
    if failure in {"missing", "database", "prepare"}:
        assert service.request_client.requests == []


@pytest.mark.parametrize("code", ["SessionClosed", "SessionExpired", "Unauthorized", "InvalidConfiguration"])
def test_shared_remote_failures_do_not_reread_or_retry(code):
    reader = FakeSessionReader()
    service = shared_service(reader=reader)
    calls = []

    def fail(session_id, request):
        calls.append((session_id, request))
        reader.session_id = "portal-B"
        raise ConnectorCliError("remote failure", [], fault=CliFault("remote", code, "failed", 403))

    service.request_client.execute_request = fail
    with pytest.raises(BottomUpDiscoveryError) as error:
        service.discover_measurement_location("location-1", "segment-1")
    assert isinstance(error.value.__cause__, ConnectorCliError)
    assert len(calls) == 1
    assert reader.calls == 1
    assert service.request_client.opened == 0
    assert service.request_client.closed == []


def test_standalone_ignores_shared_reader_and_clears_state():
    reader = FakeSessionReader()
    service = shared_service(reader=reader)
    service.use_shared_request_session = False
    service.discover_measurement_location("location-1", "segment-1")
    assert reader.calls == 0
    assert service.request_client.opened == 1
    assert service.request_client.closed == ["request-session-1"]
    assert service.request_client.cleared == 1


@pytest.mark.parametrize("shared", [True, False])
def test_service_builder_wires_session_configuration(shared):
    service = build_bottom_up_discovery_service(CollectorConfig(
        "cli", "connector.json", "portal.db", use_shared_request_session=shared,
    ))
    assert service.use_shared_request_session is shared
    if shared:
        assert isinstance(service.shared_session_reader, PortalRequestSessionReader)
        assert service.shared_session_reader.database_path == "portal.db"
    else:
        assert service.shared_session_reader is None


def test_segment_uuid_fast_path_with_known_segment_only_fetches_segment_locations():
    client = FakeRequestClient(
        responses={
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1", "location-2"]
            )
        }
    )
    persistence = FakePersistence(segment=LocalSegment("segment-1", "site-1", 1))
    service = BottomUpDiscoveryService(client, persistence)

    result = service.discover_measurement_location("location-1", "segment-1")

    assert result.persisted_measurement_location_count == 2
    assert request_types(client) == ["GetMeasurementLocations"]
    assert client.opened == 1
    assert client.closed == ["request-session-1"]
    assert [location.location_uuid for location in persistence.persisted[0]["measurement_locations"]] == [
        "location-1",
        "location-2",
    ]


def test_segment_uuid_fast_path_with_unknown_segment_fetches_segment_then_locations_without_get_sites():
    client = FakeRequestClient(
        responses={
            ("GetSegments", (("segmentUuid", "segment-1"),)): segments_response("site-1", "segment-1", include_site_name=True),
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            ),
        }
    )
    persistence = FakePersistence()

    BottomUpDiscoveryService(client, persistence).discover_measurement_location("location-1", "segment-1")

    assert request_types(client) == ["GetSegments", "GetMeasurementLocations"]
    assert persistence.site_checks == []


def test_missing_segment_uuid_performs_exact_lookup_then_segment_locations():
    client = FakeRequestClient(
        responses={
            ("GetMeasurementLocations", (("measurementLocationUuid", "location-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            ),
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1", "location-2"]
            ),
        }
    )
    persistence = FakePersistence(segment=LocalSegment("segment-1", "site-1", 1))

    BottomUpDiscoveryService(client, persistence).discover_measurement_location("location-1")

    assert request_types(client) == ["GetMeasurementLocations", "GetMeasurementLocations"]


def test_missing_segment_uuid_and_unknown_segment_fetches_exact_lookup_segment_and_locations():
    client = FakeRequestClient(
        responses={
            ("GetMeasurementLocations", (("measurementLocationUuid", "location-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            ),
            ("GetSegments", (("segmentUuid", "segment-1"),)): segments_response("site-1", "segment-1", include_site_name=True),
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            ),
        }
    )

    BottomUpDiscoveryService(client, FakePersistence()).discover_measurement_location("location-1")

    assert request_types(client) == ["GetMeasurementLocations", "GetSegments", "GetMeasurementLocations"]


def test_missing_site_metadata_invokes_get_sites_fallback():
    client = FakeRequestClient(
        responses={
            ("GetSegments", (("segmentUuid", "segment-1"),)): segments_response("site-1", "segment-1", include_site_name=False),
            ("GetSites", (("siteUuid", "site-1"),)): sites_response("site-1"),
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            ),
        }
    )

    BottomUpDiscoveryService(client, FakePersistence()).discover_measurement_location("location-1", "segment-1")

    assert request_types(client) == ["GetSegments", "GetSites", "GetMeasurementLocations"]


def test_triggering_location_absent_from_segment_result_fails_and_closes_session():
    client = FakeRequestClient(
        responses={
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-2"]
            )
        }
    )

    with pytest.raises(BottomUpDiscoveryError):
        BottomUpDiscoveryService(client, FakePersistence(segment=LocalSegment("segment-1", "site-1", 1))).discover_measurement_location(
            "location-1", "segment-1"
        )

    assert client.closed == ["request-session-1"]


def test_request_failure_retains_publication_path_and_closes_session():
    client = FakeRequestClient(fail_on_request_type="GetMeasurementLocations")

    with pytest.raises(BottomUpDiscoveryError):
        BottomUpDiscoveryService(client, FakePersistence(segment=LocalSegment("segment-1", "site-1", 1))).discover_measurement_location(
            "location-1", "segment-1"
        )

    assert client.closed == ["request-session-1"]


def test_persistence_failure_closes_session():
    client = FakeRequestClient(
        responses={
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1"]
            )
        }
    )
    persistence = FakePersistence(
        segment=LocalSegment("segment-1", "site-1", 1),
        error=DiscoveryPersistenceError("sqlite failure"),
    )

    with pytest.raises(BottomUpDiscoveryError):
        BottomUpDiscoveryService(client, persistence).discover_measurement_location("location-1", "segment-1")

    assert client.closed == ["request-session-1"]


def test_sqlite_discovery_persistence_is_atomic_and_idempotent(tmp_path):
    db_path = tmp_path / "discovery.db"
    create_schema(db_path)
    persistence = SQLiteDiscoveryPersistence(str(db_path))
    client = FakeRequestClient(
        responses={
            ("GetSegments", (("segmentUuid", "segment-1"),)): segments_response("site-1", "segment-1", include_site_name=True),
            ("GetMeasurementLocations", (("segmentUuid", "segment-1"),)): measurement_locations_response(
                "segment-1", ["location-1", "location-2"]
            ),
        }
    )
    service = BottomUpDiscoveryService(client, persistence)

    service.discover_measurement_location("location-1", "segment-1")
    service.discover_measurement_location("location-1", "segment-1")

    with sqlite3.connect(db_path) as connection:
        assert count(connection, "DimSite") == 1
        assert count(connection, "DimSegment") == 1
        assert count(connection, "DimMeasurementLocation") == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM DimMeasurementLocation WHERE MeasurementLocationUuid = 'location-2'"
        ).fetchone()[0] == 1


def request_types(client):
    return [request["requestType"] for _, request in client.requests]


def empty_response(request_type):
    roots = {
        "GetSites": {"showSites": {"dataArea": {"sites": []}}},
        "GetSegments": {"showSegments": {"dataArea": {"segments": []}}},
        "GetMeasurementLocations": {"showMeasurementLocations": {"dataArea": {"measurementLocations": []}}},
    }
    return roots[request_type]


def sites_response(site_uuid):
    return {
        "showSites": {
            "dataArea": {
                "sites": [
                    {
                        "site": [
                            {
                                "UUID": site_uuid,
                                "shortNames": [{"text": "Tampa Plant"}],
                                "type": {"UUID": "site-type-1", "shortNames": [{"text": "Plant"}]},
                            }
                        ]
                    }
                ]
            }
        }
    }


def segments_response(site_uuid, segment_uuid, include_site_name=True):
    site = {"UUID": site_uuid}
    if include_site_name:
        site["shortNames"] = [{"text": "Tampa Plant"}]
    return {
        "showSegments": {
            "dataArea": {
                "segments": [
                    {
                        "site": site,
                        "segment": [
                            {
                                "UUID": segment_uuid,
                                "shortNames": [{"text": "Pump Train"}],
                                "type": {"UUID": "segment-type-1", "shortNames": [{"text": "Pump Train"}]},
                            }
                        ],
                    }
                ]
            }
        }
    }


def measurement_locations_response(segment_uuid, location_uuids):
    return {
        "showMeasurementLocations": {
            "dataArea": {
                "measurementLocations": [
                    {
                        "segment": {"UUID": segment_uuid},
                        "measurementLocation": [
                            {
                                "UUID": uuid,
                                "shortNames": [{"text": f"Location {uuid}"}],
                                "type": {
                                    "UUID": "location-type-1",
                                    "shortNames": [{"text": "Current"}],
                                },
                                "valueClass": "Number",
                            }
                            for uuid in location_uuids
                        ],
                    }
                ]
            }
        }
    }


def create_schema(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE DimInfoSource (
                InfoSourceKey INTEGER PRIMARY KEY AUTOINCREMENT,
                InfoSourceUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT,
                FirstSeenUtc TEXT NOT NULL,
                LastSeenUtc TEXT NOT NULL
            );
            CREATE TABLE DimSiteType (
                SiteTypeKey INTEGER PRIMARY KEY AUTOINCREMENT,
                SiteTypeUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT NOT NULL,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER
            );
            CREATE TABLE DimSegmentType (
                SegmentTypeKey INTEGER PRIMARY KEY AUTOINCREMENT,
                SegmentTypeUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT NOT NULL,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER
            );
            CREATE TABLE DimMeasurementLocationType (
                MeasurementLocationTypeKey INTEGER PRIMARY KEY AUTOINCREMENT,
                MeasurementLocationTypeUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT NOT NULL,
                UomQuantityUuid TEXT,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER
            );
            CREATE TABLE DimUomQuantity (
                UomQuantityKey INTEGER PRIMARY KEY AUTOINCREMENT,
                UomQuantityUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT NOT NULL,
                ReferenceUnitUuid TEXT,
                ReferenceUnitKey INTEGER,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER
            );
            CREATE TABLE DimUnit (
                UnitKey INTEGER PRIMARY KEY AUTOINCREMENT,
                UnitUuid TEXT NOT NULL UNIQUE,
                ShortName TEXT NOT NULL,
                UomQuantityUuid TEXT,
                UomQuantityKey INTEGER,
                ConversionScale REAL,
                ConversionOffset REAL,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER
            );
            CREATE TABLE DimSite (
                SiteKey INTEGER PRIMARY KEY AUTOINCREMENT,
                SiteUuid TEXT NOT NULL UNIQUE,
                SiteName TEXT NOT NULL,
                SiteTypeUuid TEXT,
                SiteTypeKey INTEGER,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER,
                FirstSeenUtc TEXT NOT NULL,
                LastSeenUtc TEXT NOT NULL
            );
            CREATE TABLE DimSegment (
                SegmentKey INTEGER PRIMARY KEY AUTOINCREMENT,
                SegmentUuid TEXT NOT NULL UNIQUE,
                SegmentName TEXT NOT NULL,
                SegmentTypeUuid TEXT,
                SegmentTypeKey INTEGER,
                SiteUuid TEXT,
                SiteKey INTEGER,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER,
                FirstSeenUtc TEXT NOT NULL,
                LastSeenUtc TEXT NOT NULL
            );
            CREATE TABLE DimMeasurementLocation (
                MeasurementLocationKey INTEGER PRIMARY KEY AUTOINCREMENT,
                MeasurementLocationUuid TEXT NOT NULL UNIQUE,
                MeasurementLocationName TEXT NOT NULL,
                MeasurementLocationTypeUuid TEXT,
                MeasurementLocationTypeKey INTEGER,
                SegmentUuid TEXT,
                SegmentKey INTEGER,
                ValueClass TEXT,
                DefaultUnitUuid TEXT,
                DefaultUnitKey INTEGER,
                UomQuantityUuid TEXT,
                UomQuantityKey INTEGER,
                InfoSourceUuid TEXT,
                InfoSourceKey INTEGER,
                FirstSeenUtc TEXT NOT NULL,
                LastSeenUtc TEXT NOT NULL
            );
            """
        )


def count(connection, table_name):
    return connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
