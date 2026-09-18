from __future__ import annotations

from typing import Any

from .discovery_models import MeasurementLocationMetadata, SegmentMetadata, SegmentResolution, SiteMetadata


class DiscoveryResponseParseError(ValueError):
    pass


class DiscoveryResponseParser:
    def parse_sites(self, payload: Any) -> list[SiteMetadata]:
        return [_map_site(record) for record in _candidate_site_records(payload)]

    def parse_segments(self, payload: Any) -> list[SegmentResolution]:
        return [
            SegmentResolution(segment=_map_segment(segment, _site_uuid(site)), site=_map_site(site) if site else None)
            for site, segment in _candidate_segment_records(payload)
        ]

    def parse_measurement_locations(self, payload: Any, fallback_segment_uuid: str = "") -> list[MeasurementLocationMetadata]:
        return [
            _map_measurement_location(location, _segment_uuid(segment) or fallback_segment_uuid)
            for segment, location in _candidate_measurement_location_records(payload)
        ]


def _candidate_site_records(value: Any) -> list[dict[str, Any]]:
    roots = [
        _get_path(value, ["showSites", "dataArea", "sites"]),
        _get_path(value, ["showSites", "dataArea", "sites", "site"]),
        value,
    ]
    records: list[dict[str, Any]] = []
    for root in roots:
        records.extend(_site_records(root))
    return _dedupe(records, ("UUID", "uuid", "SiteUUID", "siteUuid", "site_uuid"))


def _site_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [record for item in value for record in _site_records(item)]
    if isinstance(value, dict):
        if _looks_like_site(value):
            return [value]
        for key in ("showSites", "dataArea", "sites", "site", "payload", "data", "response"):
            nested = _site_records(value.get(key))
            if nested:
                return nested
    return []


def _candidate_segment_records(value: Any) -> list[tuple[dict[str, Any] | None, dict[str, Any]]]:
    roots = [_get_path(value, ["showSegments", "dataArea", "segments"]), value]
    records: list[tuple[dict[str, Any] | None, dict[str, Any]]] = []
    for root in roots:
        records.extend(_segment_records(root))
    seen: set[str] = set()
    deduped: list[tuple[dict[str, Any] | None, dict[str, Any]]] = []
    for site, segment in records:
        key = str(_first(segment, ("UUID", "uuid", "SegmentUUID", "segmentUuid")) or id(segment))
        if key not in seen:
            seen.add(key)
            deduped.append((site, segment))
    return deduped


def _segment_records(value: Any) -> list[tuple[dict[str, Any] | None, dict[str, Any]]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [record for item in value for record in _segment_records(item)]
    if isinstance(value, dict):
        if "showSegments" in value:
            return _segment_records(_get_path(value, ["showSegments", "dataArea", "segments"]))
        if "dataArea" in value:
            return _segment_records(_get_path(value, ["dataArea", "segments"]))
        if "segment" in value or "segments" in value:
            site = value.get("site") if isinstance(value.get("site"), dict) else None
            return [(site, segment) for segment in _as_list(value.get("segment") or value.get("segments")) if isinstance(segment, dict)]
        if _looks_like_segment(value):
            return [(None, value)]
        for key in ("segments", "payload", "data", "response"):
            nested = _segment_records(value.get(key))
            if nested:
                return nested
    return []


def _candidate_measurement_location_records(value: Any) -> list[tuple[dict[str, Any] | None, dict[str, Any]]]:
    roots = [_get_path(value, ["showMeasurementLocations", "dataArea", "measurementLocations"]), value]
    records: list[tuple[dict[str, Any] | None, dict[str, Any]]] = []
    for root in roots:
        records.extend(_measurement_location_records(root))
    seen: set[str] = set()
    deduped: list[tuple[dict[str, Any] | None, dict[str, Any]]] = []
    for segment, location in records:
        key = str(_first(location, ("UUID", "uuid", "MeasurementLocationUUID", "measurementLocationUuid")) or id(location))
        if key not in seen:
            seen.add(key)
            deduped.append((segment, location))
    return deduped


def _measurement_location_records(value: Any) -> list[tuple[dict[str, Any] | None, dict[str, Any]]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [record for item in value for record in _measurement_location_records(item)]
    if isinstance(value, dict):
        if "showMeasurementLocations" in value:
            return _measurement_location_records(_get_path(value, ["showMeasurementLocations", "dataArea", "measurementLocations"]))
        if "dataArea" in value:
            return _measurement_location_records(_get_path(value, ["dataArea", "measurementLocations"]))
        if "measurementLocation" in value or "measurementLocations" in value:
            segment = value.get("segment") if isinstance(value.get("segment"), dict) else None
            return [
                (segment, location)
                for location in _as_list(value.get("measurementLocation") or value.get("measurementLocations"))
                if isinstance(location, dict)
            ]
        if _looks_like_measurement_location(value):
            return [(value.get("segment") if isinstance(value.get("segment"), dict) else None, value)]
        for key in ("measurementLocations", "payload", "data", "response"):
            nested = _measurement_location_records(value.get(key))
            if nested:
                return nested
    return []


def _map_site(record: dict[str, Any]) -> SiteMetadata:
    site_uuid = _site_uuid(record)
    if not site_uuid:
        raise DiscoveryResponseParseError("Site response is missing UUID.")
    type_node = _first_node(record, ("type", "Type", "siteType", "SiteType"))
    type_info = _info_source(type_node)
    info = _info_source(record)
    return SiteMetadata(
        site_uuid=site_uuid,
        site_name=_first_short_name(record) or _scalar_first(record, ("name", "Name", "fullName", "FullName")) or site_uuid,
        site_type=_first_short_name(type_node) if isinstance(type_node, dict) else "",
        site_type_uuid=_first_text(type_node, ("UUID", "uuid")) if isinstance(type_node, dict) else None,
        site_type_info_source_uuid=type_info[0],
        site_type_info_source_short_name=type_info[1],
        info_source_uuid=info[0],
        info_source_short_name=info[1],
    )


def _map_segment(record: dict[str, Any], site_uuid: str | None) -> SegmentMetadata:
    segment_uuid = _segment_uuid(record)
    if not segment_uuid:
        raise DiscoveryResponseParseError("Segment response is missing UUID.")
    type_node = _first_node(record, ("type", "Type"))
    type_info = _info_source(type_node)
    info = _info_source(record)
    if not site_uuid:
        site_uuid = _first_text(record.get("site") if isinstance(record.get("site"), dict) else None, ("UUID", "uuid"))
    if not site_uuid:
        raise DiscoveryResponseParseError("Segment response is missing parent Site UUID.")
    return SegmentMetadata(
        segment_uuid=segment_uuid,
        site_uuid=site_uuid,
        segment_name=_first_short_name(record) or segment_uuid,
        segment_type=_first_short_name(type_node) if isinstance(type_node, dict) else "",
        segment_type_uuid=_first_text(type_node, ("UUID", "uuid")) if isinstance(type_node, dict) else None,
        segment_type_info_source_uuid=type_info[0],
        segment_type_info_source_short_name=type_info[1],
        info_source_uuid=info[0],
        info_source_short_name=info[1],
    )


def _map_measurement_location(record: dict[str, Any], segment_uuid: str) -> MeasurementLocationMetadata:
    location_uuid = _first_text(record, ("UUID", "uuid", "MeasurementLocationUUID", "measurementLocationUuid"))
    if not location_uuid:
        raise DiscoveryResponseParseError("MeasurementLocation response is missing UUID.")
    if not segment_uuid:
        segment_uuid = _segment_uuid(record.get("segment") if isinstance(record.get("segment"), dict) else None) or ""
    if not segment_uuid:
        raise DiscoveryResponseParseError("MeasurementLocation response is missing parent Segment UUID.")

    type_node = _first_node(record, ("type", "Type"))
    default_unit = _first_node(record, ("defaultUnitOfMeasure", "DefaultUnitOfMeasure"))
    quantity = _first_node(record, ("UOMQuantity", "uomQuantity", "UomQuantity"))
    reference_unit = _first_node(quantity, ("referenceUnitOfMeasure", "ReferenceUnitOfMeasure")) if isinstance(quantity, dict) else None
    location_type_info = _info_source(type_node)
    unit_info = _info_source(default_unit)
    quantity_info = _info_source(quantity)
    info = _info_source(record)
    value_class = _first_text(record, ("valueClass", "ValueClass"))
    return MeasurementLocationMetadata(
        location_uuid=location_uuid,
        segment_uuid=segment_uuid,
        location_name=_first_short_name(record) or location_uuid,
        location_type=_first_short_name(type_node) if isinstance(type_node, dict) else "",
        type_uuid=_first_text(type_node, ("UUID", "uuid")) if isinstance(type_node, dict) else None,
        value_class=value_class,
        unit=_first_short_name(default_unit) if isinstance(default_unit, dict) else "",
        unit_uuid=_first_text(default_unit, ("UUID", "uuid")) if isinstance(default_unit, dict) else None,
        reference_unit_uuid=_first_text(reference_unit, ("UUID", "uuid")) if isinstance(reference_unit, dict) else None,
        conversion_scale=_optional_float(_first(default_unit, ("conversionScale", "ConversionScale"))) if isinstance(default_unit, dict) else None,
        conversion_offset=_optional_float(_first(default_unit, ("conversionOffset", "ConversionOffset"))) if isinstance(default_unit, dict) else None,
        uom_quantity=_first_short_name(quantity) if isinstance(quantity, dict) else "",
        uom_quantity_uuid=_first_text(quantity, ("UUID", "uuid")) if isinstance(quantity, dict) else None,
        info_source_uuid=info[0],
        info_source_short_name=info[1],
        measurement_location_type_info_source_uuid=location_type_info[0],
        measurement_location_type_info_source_short_name=location_type_info[1],
        unit_info_source_uuid=unit_info[0],
        unit_info_source_short_name=unit_info[1],
        uom_quantity_info_source_uuid=quantity_info[0],
        uom_quantity_info_source_short_name=quantity_info[1],
    )


def _looks_like_site(value: dict[str, Any]) -> bool:
    return bool({key.casefold() for key in value} & {"siteuuid", "uuid", "shortnames", "shortname", "fullname"})


def _looks_like_segment(value: dict[str, Any]) -> bool:
    return bool({key.casefold() for key in value} & {"segmentuuid", "uuid", "shortnames", "type"})


def _looks_like_measurement_location(value: dict[str, Any]) -> bool:
    return bool({key.casefold() for key in value} & {"measurementlocationuuid", "uuid", "shortnames", "valueclass"})


def _dedupe(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = str(_first(record, keys) or id(record))
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _info_source(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    node = value.get("infoSource") or value.get("InfoSource")
    if not isinstance(node, dict):
        return None, None
    return _first_text(node, ("UUID", "uuid")), _first_short_name(node)


def _first_short_name(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        for item in value:
            result = _first_short_name(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("shortNames", "ShortNames", "shortName", "ShortName"):
            result = _first_short_name(value.get(key))
            if result:
                return result
        for key in ("text", "Text", "value", "Value"):
            result = value.get(key)
            if isinstance(result, str) and result:
                return result
    return None


def _site_uuid(value: Any) -> str | None:
    return _first_text(value, ("UUID", "uuid", "SiteUUID", "siteUuid", "site_uuid"))


def _segment_uuid(value: Any) -> str | None:
    return _first_text(value, ("UUID", "uuid", "SegmentUUID", "segmentUuid", "segment_uuid"))


def _first_text(value: Any, names: tuple[str, ...]) -> str | None:
    result = _first(value, names)
    return str(result) if result not in (None, "") else None


def _first(value: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return None
    for name in names:
        if value.get(name) not in (None, ""):
            return value.get(name)
    return None


def _first_node(value: Any, names: tuple[str, ...]) -> Any:
    return _first(value, names) if isinstance(value, dict) else None


def _scalar_first(value: dict[str, Any], names: tuple[str, ...]) -> str | None:
    result = _first(value, names)
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return _first_short_name(result)
    return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_path(value: Any, path: list[str]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
