from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class PublicationParseError(ValueError):
    pass


@dataclass(frozen=True)
class MeasurementLocationIdentity:
    uuid: str
    name: str | None = None

    @property
    def log_label(self) -> str:
        return f"{self.name} ({self.uuid})" if self.name else self.uuid


@dataclass(frozen=True)
class NormalizedMeasurementRow:
    measurement_location_uuid: str
    measurement_timestamp_utc: str
    numeric_value: float
    value_class: str
    measurement_uuid: str
    segment_uuid: str | None = None
    unit_uuid: str | None = None
    unit_name: str | None = None
    info_source_uuid: str | None = None

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return (self.measurement_location_uuid, self.measurement_timestamp_utc, self.measurement_uuid)

    @property
    def duplicate_signature(self) -> tuple[float, str | None, str, str | None]:
        return (
            self.numeric_value,
            self.unit_uuid,
            self.value_class,
            self.info_source_uuid,
        )


class MeasurementLocationExtractor:
    def extract(self, payload: Any) -> MeasurementLocationIdentity:
        value = _normalize_payload(payload)
        for node in _walk(value):
            if not isinstance(node, dict):
                continue
            location = _first_value(node, "measurementLocation", "MeasurementLocation")
            if isinstance(location, dict):
                uuid = _first_text(location, "UUID", "uuid", "MeasurementLocationUUID", "measurementLocationUuid")
                if uuid:
                    return MeasurementLocationIdentity(uuid=uuid, name=_location_name(location))
        raise PublicationParseError("Publication payload does not contain measurementLocation.UUID.")


class MeasurementPublicationParser:
    def parse(self, payload: Any) -> list[NormalizedMeasurementRow]:
        value = _normalize_payload(payload)
        groups = _measurement_groups(value)
        if not groups:
            raise PublicationParseError("Publication payload does not contain syncMeasurements.dataArea.measurements.")

        rows: list[NormalizedMeasurementRow] = []
        for group in groups:
            location = _first_value(group, "measurementLocation", "MeasurementLocation")
            if not isinstance(location, dict):
                raise PublicationParseError("Measurement group is missing measurementLocation.")
            location_uuid = _first_text(location, "UUID", "uuid", "MeasurementLocationUUID", "measurementLocationUuid")
            if not location_uuid:
                raise PublicationParseError("Measurement group is missing measurementLocation.UUID.")

            for measurement in _as_list(_first_value(group, "measurement", "Measurement")):
                if not isinstance(measurement, dict):
                    raise PublicationParseError("Measurement array contains a non-object item.")
                rows.append(_measurement_row(measurement, location_uuid))

        if not rows:
            raise PublicationParseError("Publication payload does not contain measurement rows.")
        _ensure_no_contradictory_duplicate_identities(rows)
        return _dedupe_rows(rows)


def _normalize_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PublicationParseError("Publication payload string is not valid JSON.") from exc
    return payload


def _measurement_groups(value: Any) -> list[dict[str, Any]]:
    measurements = _get_path(value, ["syncMeasurements", "dataArea", "measurements"])
    return [item for item in _as_list(measurements) if isinstance(item, dict)]


def _measurement_row(measurement: dict[str, Any], location_uuid: str) -> NormalizedMeasurementRow:
    timestamp = _measurement_timestamp(measurement)
    numeric_value, value_class, unit = _measurement_value(measurement)
    measurement_uuid = _first_text(measurement, "UUID", "uuid", "MeasurementUUID", "measurementUuid")
    if not measurement_uuid:
        raise PublicationParseError("Measurement is missing UUID.")
    return NormalizedMeasurementRow(
        measurement_location_uuid=location_uuid,
        measurement_timestamp_utc=timestamp,
        numeric_value=numeric_value,
        value_class=value_class,
        measurement_uuid=measurement_uuid,
        segment_uuid=_segment_uuid(measurement),
        unit_uuid=_first_text(unit, "UUID", "uuid") if isinstance(unit, dict) else None,
        unit_name=_location_name(unit) if isinstance(unit, dict) else None,
        info_source_uuid=_info_source_uuid(measurement),
    )


def _measurement_timestamp(measurement: dict[str, Any]) -> str:
    recorded = _first_value(measurement, "recorded", "Recorded")
    value = _first_text(recorded, "dateTime", "DateTime") if isinstance(recorded, dict) else None
    if not value:
        raise PublicationParseError("Measurement is missing recorded.dateTime.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicationParseError(f"Measurement recorded.dateTime is not valid: {value}") from exc
    if parsed.tzinfo is None:
        raise PublicationParseError("Measurement recorded.dateTime must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _measurement_value(measurement: dict[str, Any]) -> tuple[float, str, dict[str, Any] | None]:
    data = _first_value(measurement, "data", "Data")
    if not isinstance(data, dict):
        raise PublicationParseError("Measurement is missing data.")

    measure = _first_value(data, "measure", "Measure")
    if isinstance(measure, dict):
        value_node = _first_value(measure, "value", "Value")
        unit = _first_value(measure, "UnitOfMeasure", "unitOfMeasure")
        return _numeric_value(value_node), "Measure", unit if isinstance(unit, dict) else None

    percentage = _first_value(data, "percentage", "Percentage")
    if isinstance(percentage, dict):
        return _numeric_value(percentage), "Percentage", None

    number = _first_value(data, "number", "Number")
    if isinstance(number, dict):
        return _numeric_value(number), "Number", None

    raise PublicationParseError("Measurement data must contain measure, percentage, or number.")


def _numeric_value(value: Any) -> float:
    if isinstance(value, dict):
        value = _first_value(value, "numeric", "Numeric")
    if value in (None, ""):
        raise PublicationParseError("Measurement numeric value is required.")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PublicationParseError(f"Measurement numeric value is not numeric: {value}") from exc


def _info_source_uuid(value: dict[str, Any]) -> str | None:
    info_source = _first_value(value, "infoSource", "InfoSource")
    return _first_text(info_source, "UUID", "uuid") if isinstance(info_source, dict) else None


def _segment_uuid(value: dict[str, Any]) -> str | None:
    segment = _first_value(value, "segment", "Segment")
    return _first_text(segment, "UUID", "uuid", "SegmentUUID", "segmentUuid") if isinstance(segment, dict) else None


def _ensure_no_contradictory_duplicate_identities(rows: list[NormalizedMeasurementRow]) -> None:
    signatures_by_key: dict[tuple[str, str, str], set[tuple[float, str | None, str, str | None]]] = {}
    for row in rows:
        signatures_by_key.setdefault(row.identity_key, set()).add(row.duplicate_signature)
    for row_identity, signatures in signatures_by_key.items():
        if len(signatures) > 1:
            location_uuid, timestamp, measurement_uuid = row_identity
            raise PublicationParseError(
                "Publication contains contradictory duplicate measurements for the same "
                f"measurement identity: {location_uuid} {timestamp} {measurement_uuid}."
            )


def _dedupe_rows(rows: list[NormalizedMeasurementRow]) -> list[NormalizedMeasurementRow]:
    seen: set[tuple[tuple[str, str, str], tuple[float, str | None, str, str | None]]] = set()
    deduped: list[NormalizedMeasurementRow] = []
    for row in rows:
        key = (row.identity_key, row.duplicate_signature)
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _location_name(location: dict[str, Any]) -> str | None:
    direct = _first_text(location, "shortName", "ShortName", "name", "Name", "MeasurementLocationName")
    if direct:
        return direct

    short_names = _first_value(location, "shortNames", "ShortNames")
    if isinstance(short_names, list):
        for item in short_names:
            if isinstance(item, dict):
                text = _first_text(item, "text", "Text", "value", "Value")
                if text:
                    return text
            elif item not in (None, ""):
                return str(item)
    return None


def _first_text(value: dict[str, Any], *keys: str) -> str | None:
    result = _first_value(value, *keys)
    if result in (None, ""):
        return None
    return str(result).strip() or None


def _first_value(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _get_path(value: Any, path: list[str]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
