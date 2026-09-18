import sqlite3

import pytest

from mcegold_discovery_publication_collector.bottom_up_discovery import (
    BottomUpDiscoveryError,
    BottomUpDiscoveryService,
)
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
