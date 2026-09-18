import pytest

from mcegold_discovery_publication_collector.measurement_location_lookup import (
    MeasurementLocationLookupError,
    MeasurementLocationLookupResult,
)
from mcegold_discovery_publication_collector.measurement_persistence import MeasurementPersistenceError
from mcegold_discovery_publication_collector.measurement_publication import MeasurementPublicationParser
from mcegold_discovery_publication_collector.publication_client import Publication
from mcegold_discovery_publication_collector.publication_processor import (
    PublicationHandlingError,
    PublicationProcessor,
)


class FakeLookup:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.queries = []

    def lookup(self, measurement_location_uuid):
        self.queries.append(measurement_location_uuid)
        if self.error:
            raise self.error
        return self.result


class FakeTrigger:
    def __init__(self, result=False):
        self.result = result
        self.unknown_locations = []

    def on_unknown_measurement_location(self, identity, publication, segment_uuid_if_known=None):
        self.unknown_locations.append((identity, publication, segment_uuid_if_known))
        return self.result


class FakePersistence:
    def __init__(self, error=None):
        self.error = error
        self.rows = []
        self.calls = []

    def persist_publication(self, rows):
        if self.error:
            raise self.error
        self.calls.append(list(rows))
        self.rows.extend(rows)
        return len(rows)


class DynamicLookup:
    def __init__(self, known_locations=None):
        self.known_locations = set(known_locations or [])
        self.queries = []

    def lookup(self, measurement_location_uuid):
        self.queries.append(measurement_location_uuid)
        return MeasurementLocationLookupResult(is_known=measurement_location_uuid in self.known_locations)


class SegmentDiscoveryTrigger:
    def __init__(self, lookup, location_segments, segment_locations, result=True):
        self.lookup = lookup
        self.location_segments = location_segments
        self.segment_locations = segment_locations
        self.result = result
        self.unknown_locations = []
        self.service_calls = []
        self.request_sessions_opened = 0
        self.exact_location_lookups = []
        self.segment_wide_location_lookups = []

    def on_unknown_measurement_location(self, identity, publication, segment_uuid_if_known=None):
        self.unknown_locations.append((identity, publication, segment_uuid_if_known))
        self.service_calls.append(identity.uuid)
        self.request_sessions_opened += 1
        segment_uuid = segment_uuid_if_known
        if not segment_uuid:
            self.exact_location_lookups.append(identity.uuid)
            segment_uuid = self.location_segments[identity.uuid]
        self.segment_wide_location_lookups.append(segment_uuid)
        if self.result:
            self.lookup.known_locations.update(self.segment_locations[segment_uuid])
        return self.result


def publication(payload):
    return Publication(session_id="session-1", message_id="message-1", payload=payload, envelope={})


def payload(location_uuid="location-1"):
    return {
        "syncMeasurements": {
            "dataArea": {
                "measurements": [
                    {
                        "measurementLocation": {"UUID": location_uuid, "shortName": "Pump Bearing"},
                        "measurement": [
                            {
                                "UUID": "measurement-1",
                                "recorded": {"dateTime": "2018-09-22T03:34:17Z"},
                                "segment": {"UUID": "segment-1"},
                                "data": {"number": {"numeric": "1"}},
                            }
                        ],
                    }
                ]
            }
        }
    }


def multi_location_payload(location_segments, include_segment_uuid=True):
    return {
        "syncMeasurements": {
            "dataArea": {
                "measurements": [
                    {
                        "measurementLocation": {"UUID": location_uuid, "shortName": location_uuid.upper()},
                        "measurement": [
                            {
                                "UUID": f"measurement-{location_uuid}",
                                "recorded": {"dateTime": "2018-09-22T03:34:17Z"},
                                **(
                                    {"segment": {"UUID": segment_uuid}}
                                    if include_segment_uuid
                                    else {}
                                ),
                                "data": {"number": {"numeric": str(index)}},
                            }
                        ],
                    }
                    for index, (location_uuid, segment_uuid) in enumerate(location_segments.items(), start=1)
                ]
            }
        }
    }


def test_known_measurement_location_does_not_trigger_discovery():
    lookup = FakeLookup(result=MeasurementLocationLookupResult(is_known=True))
    trigger = FakeTrigger()
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(payload()))

    assert lookup.queries == ["location-1"]
    assert trigger.unknown_locations == []
    assert len(persistence.rows) == 1


def test_unknown_measurement_location_hits_trigger_boundary():
    lookup = FakeLookup(result=MeasurementLocationLookupResult(is_known=False))
    trigger = FakeTrigger()
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    with pytest.raises(PublicationHandlingError):
        processor(publication(payload()))

    assert trigger.unknown_locations[0][0].uuid == "location-1"
    assert trigger.unknown_locations[0][2] == "segment-1"
    assert persistence.rows == []


def test_unknown_measurement_location_persists_immediately_after_discovery():
    class LookupBecomesKnown(FakeLookup):
        def lookup(self, measurement_location_uuid):
            self.queries.append(measurement_location_uuid)
            return MeasurementLocationLookupResult(is_known=len(self.queries) > 1)

    lookup = LookupBecomesKnown()
    trigger = FakeTrigger(result=True)
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(payload()))

    assert lookup.queries == ["location-1", "location-1"]
    assert len(persistence.rows) == 1


def test_multiple_unknown_locations_in_same_segment_trigger_one_discovery_with_segment_uuid():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1", "ml-c": "segment-1"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {"segment-1": {"ml-a", "ml-b", "ml-c"}},
    )
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(multi_location_payload(location_segments)))

    assert [call[0].uuid for call in trigger.unknown_locations] == ["ml-a"]
    assert trigger.service_calls == ["ml-a"]
    assert trigger.request_sessions_opened == 1
    assert trigger.exact_location_lookups == []
    assert trigger.segment_wide_location_lookups == ["segment-1"]
    assert lookup.queries == ["ml-a", "ml-a", "ml-b", "ml-c"]
    assert len(persistence.calls) == 1
    assert {row.measurement_location_uuid for row in persistence.rows} == {"ml-a", "ml-b", "ml-c"}


def test_multiple_unknown_locations_in_same_segment_trigger_one_discovery_without_segment_uuid():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1", "ml-c": "segment-1"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {"segment-1": {"ml-a", "ml-b", "ml-c"}},
    )
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(multi_location_payload(location_segments, include_segment_uuid=False)))

    assert [call[0].uuid for call in trigger.unknown_locations] == ["ml-a"]
    assert [call[2] for call in trigger.unknown_locations] == [None]
    assert trigger.service_calls == ["ml-a"]
    assert trigger.request_sessions_opened == 1
    assert trigger.exact_location_lookups == ["ml-a"]
    assert trigger.segment_wide_location_lookups == ["segment-1"]
    assert lookup.queries == ["ml-a", "ml-a", "ml-b", "ml-c"]
    assert len(persistence.calls) == 1
    assert {row.measurement_location_uuid for row in persistence.rows} == {"ml-a", "ml-b", "ml-c"}


def test_unknown_locations_across_two_segments_trigger_one_discovery_per_segment():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1", "ml-c": "segment-2"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {
            "segment-1": {"ml-a", "ml-b"},
            "segment-2": {"ml-c"},
        },
    )
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(multi_location_payload(location_segments)))

    assert [call[0].uuid for call in trigger.unknown_locations] == ["ml-a", "ml-c"]
    assert [call[2] for call in trigger.unknown_locations] == ["segment-1", "segment-2"]
    assert trigger.service_calls == ["ml-a", "ml-c"]
    assert trigger.request_sessions_opened == 2
    assert trigger.segment_wide_location_lookups == ["segment-1", "segment-2"]
    assert lookup.queries == ["ml-a", "ml-a", "ml-b", "ml-c", "ml-c"]
    assert len(persistence.calls) == 1
    assert {row.measurement_location_uuid for row in persistence.rows} == {"ml-a", "ml-b", "ml-c"}


def test_sibling_lookup_is_refreshed_after_discovery():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {"segment-1": {"ml-a", "ml-b"}},
    )
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, FakePersistence())

    processor(publication(multi_location_payload(location_segments)))

    assert trigger.service_calls == ["ml-a"]
    assert lookup.queries == ["ml-a", "ml-a", "ml-b"]
    assert lookup.known_locations == {"ml-a", "ml-b"}


def test_discovery_failure_stops_before_sibling_attempts_or_persistence():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1", "ml-c": "segment-1"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {"segment-1": {"ml-a", "ml-b", "ml-c"}},
        result=False,
    )
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    with pytest.raises(PublicationHandlingError):
        processor(publication(multi_location_payload(location_segments)))

    assert [call[0].uuid for call in trigger.unknown_locations] == ["ml-a"]
    assert trigger.service_calls == ["ml-a"]
    assert lookup.queries == ["ml-a"]
    assert persistence.calls == []
    assert persistence.rows == []


def test_complete_publication_persists_once_after_all_locations_resolve():
    location_segments = {"ml-a": "segment-1", "ml-b": "segment-1", "ml-c": "segment-2"}
    lookup = DynamicLookup()
    trigger = SegmentDiscoveryTrigger(
        lookup,
        location_segments,
        {
            "segment-1": {"ml-a", "ml-b"},
            "segment-2": {"ml-c"},
        },
    )
    persistence = FakePersistence()
    processor = PublicationProcessor(MeasurementPublicationParser(), lookup, trigger, persistence)

    processor(publication(multi_location_payload(location_segments)))

    assert len(persistence.calls) == 1
    assert [row.measurement_location_uuid for row in persistence.calls[0]] == ["ml-a", "ml-b", "ml-c"]


def test_measurement_persistence_failure_after_discovery_retains_publication():
    class LookupBecomesKnown(FakeLookup):
        def lookup(self, measurement_location_uuid):
            self.queries.append(measurement_location_uuid)
            return MeasurementLocationLookupResult(is_known=len(self.queries) > 1)

    processor = PublicationProcessor(
        MeasurementPublicationParser(),
        LookupBecomesKnown(),
        FakeTrigger(result=True),
        FakePersistence(error=MeasurementPersistenceError("database unavailable")),
    )

    with pytest.raises(PublicationHandlingError):
        processor(publication(payload()))


def test_parse_failure_stops_handling():
    processor = PublicationProcessor(
        MeasurementPublicationParser(),
        FakeLookup(result=MeasurementLocationLookupResult(is_known=True)),
        FakeTrigger(),
        FakePersistence(),
    )

    with pytest.raises(PublicationHandlingError):
        processor(publication({"notMeasurement": {}}))


def test_lookup_failure_stops_handling():
    processor = PublicationProcessor(
        MeasurementPublicationParser(),
        FakeLookup(error=MeasurementLocationLookupError("database unavailable")),
        FakeTrigger(),
        FakePersistence(),
    )

    with pytest.raises(PublicationHandlingError):
        processor(publication(payload()))


def test_persistence_failure_stops_handling():
    processor = PublicationProcessor(
        MeasurementPublicationParser(),
        FakeLookup(result=MeasurementLocationLookupResult(is_known=True)),
        FakeTrigger(),
        FakePersistence(error=MeasurementPersistenceError("database unavailable")),
    )

    with pytest.raises(PublicationHandlingError):
        processor(publication(payload()))
