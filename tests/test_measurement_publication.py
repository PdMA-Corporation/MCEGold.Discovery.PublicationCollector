import json

import pytest

from mcegold_discovery_publication_collector.measurement_publication import (
    MeasurementLocationExtractor,
    MeasurementPublicationParser,
    PublicationParseError,
)


def test_extracts_measurement_location_from_sync_measurements_payload():
    identity = MeasurementLocationExtractor().extract(
        {
            "syncMeasurements": {
                "dataArea": {
                    "measurements": [
                        {
                            "measurementLocation": {
                                "UUID": "3f2da77e-a7c5-4a8d-536f-f5627ffda289",
                                "shortName": "Flow Meter FM-189-Loc1",
                            },
                            "measurement": [],
                        }
                    ]
                }
            }
        }
    )

    assert identity.uuid == "3f2da77e-a7c5-4a8d-536f-f5627ffda289"
    assert identity.name == "Flow Meter FM-189-Loc1"


def test_extracts_measurement_location_from_json_string_with_short_names():
    payload = json.dumps(
        {
            "measurementLocation": {
                "measurementLocationUuid": "location-1",
                "shortNames": [{"text": "Pump Bearing"}],
            }
        }
    )

    identity = MeasurementLocationExtractor().extract(payload)

    assert identity.uuid == "location-1"
    assert identity.name == "Pump Bearing"


def test_missing_measurement_location_uuid_fails():
    with pytest.raises(PublicationParseError):
        MeasurementLocationExtractor().extract({"measurementLocation": {"shortName": "No UUID"}})


def test_invalid_json_string_fails():
    with pytest.raises(PublicationParseError):
        MeasurementLocationExtractor().extract("{not json")


def sync_payload(data, timestamp="2018-09-22T03:34:17Z"):
    return {
        "syncMeasurements": {
            "dataArea": {
                "measurements": [
                    {
                        "measurementLocation": {"UUID": "location-1", "shortName": "Pump Bearing"},
                        "measurement": [
                            {
                                "@@type": "SingleDataMeasurement",
                                "UUID": "measurement-1",
                                "recorded": {"dateTime": timestamp},
                                "infoSource": {"UUID": "info-source-1"},
                                "data": data,
                            }
                        ],
                    }
                ]
            }
        }
    }


def test_parser_parses_measure_string_value():
    rows = MeasurementPublicationParser().parse(
        sync_payload(
            {
                "measure": {
                    "value": "38.21",
                    "unitOfMeasure": {"UUID": "unit-1", "shortName": "RPM"},
                }
            }
        )
    )

    assert rows[0].measurement_location_uuid == "location-1"
    assert rows[0].measurement_uuid == "measurement-1"
    assert rows[0].measurement_timestamp_utc == "2018-09-22T03:34:17Z"
    assert rows[0].numeric_value == 38.21
    assert rows[0].value_class == "Measure"
    assert rows[0].unit_uuid == "unit-1"
    assert rows[0].info_source_uuid == "info-source-1"


def test_parser_parses_percentage_numeric_value():
    rows = MeasurementPublicationParser().parse(sync_payload({"percentage": {"numeric": "98.5"}}))

    assert rows[0].numeric_value == 98.5
    assert rows[0].value_class == "Percentage"
    assert rows[0].unit_uuid is None


def test_parser_parses_number_numeric_value():
    rows = MeasurementPublicationParser().parse(sync_payload({"number": {"numeric": "7"}}))

    assert rows[0].numeric_value == 7
    assert rows[0].value_class == "Number"


def test_parser_extracts_measurement_segment_uuid_when_present():
    payload = sync_payload({"number": {"numeric": "7"}})
    payload["syncMeasurements"]["dataArea"]["measurements"][0]["measurement"][0]["segment"] = {"UUID": "segment-1"}

    rows = MeasurementPublicationParser().parse(payload)

    assert rows[0].segment_uuid == "segment-1"


def test_parser_parses_measure_value_numeric_form():
    rows = MeasurementPublicationParser().parse(
        sync_payload(
            {
                "measure": {
                    "value": {"numeric": "1800"},
                    "unitOfMeasure": {"UUID": "unit-1"},
                }
            }
        )
    )

    assert rows[0].numeric_value == 1800
    assert rows[0].unit_uuid == "unit-1"


def test_parser_normalizes_timestamp_to_utc_text():
    rows = MeasurementPublicationParser().parse(
        sync_payload({"number": {"numeric": "1"}}, timestamp="2018-09-21T23:34:17-04:00")
    )

    assert rows[0].measurement_timestamp_utc == "2018-09-22T03:34:17Z"


def test_parser_rejects_missing_timestamp():
    payload = sync_payload({"number": {"numeric": "1"}})
    del payload["syncMeasurements"]["dataArea"]["measurements"][0]["measurement"][0]["recorded"]

    with pytest.raises(PublicationParseError):
        MeasurementPublicationParser().parse(payload)


def test_parser_allows_distinct_measurement_uuids_at_same_location_timestamp():
    payload = sync_payload({"number": {"numeric": "1"}})
    payload["syncMeasurements"]["dataArea"]["measurements"][0]["measurement"].append(
        {
            "@@type": "SingleDataMeasurement",
            "UUID": "measurement-2",
            "recorded": {"dateTime": "2018-09-22T03:34:17Z"},
            "data": {"number": {"numeric": "2"}},
        }
    )

    rows = MeasurementPublicationParser().parse(payload)

    assert len(rows) == 2
    assert {row.measurement_uuid for row in rows} == {"measurement-1", "measurement-2"}


def test_parser_rejects_contradictory_duplicate_measurement_identity():
    payload = sync_payload({"number": {"numeric": "1"}})
    payload["syncMeasurements"]["dataArea"]["measurements"][0]["measurement"].append(
        {
            "@@type": "SingleDataMeasurement",
            "UUID": "measurement-1",
            "recorded": {"dateTime": "2018-09-22T03:34:17Z"},
            "data": {"number": {"numeric": "2"}},
        }
    )

    with pytest.raises(PublicationParseError):
        MeasurementPublicationParser().parse(payload)


def test_parser_rejects_missing_measurement_uuid():
    payload = sync_payload({"number": {"numeric": "1"}})
    del payload["syncMeasurements"]["dataArea"]["measurements"][0]["measurement"][0]["UUID"]

    with pytest.raises(PublicationParseError):
        MeasurementPublicationParser().parse(payload)
