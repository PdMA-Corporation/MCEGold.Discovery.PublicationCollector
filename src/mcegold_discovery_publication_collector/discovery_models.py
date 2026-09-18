from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteMetadata:
    site_uuid: str
    site_name: str
    site_type: str = ""
    site_type_uuid: str | None = None
    site_type_info_source_uuid: str | None = None
    site_type_info_source_short_name: str | None = None
    info_source_uuid: str | None = None
    info_source_short_name: str | None = None


@dataclass(frozen=True)
class SegmentMetadata:
    segment_uuid: str
    site_uuid: str
    segment_name: str
    segment_type: str = ""
    segment_type_uuid: str | None = None
    segment_type_info_source_uuid: str | None = None
    segment_type_info_source_short_name: str | None = None
    info_source_uuid: str | None = None
    info_source_short_name: str | None = None


@dataclass(frozen=True)
class MeasurementLocationMetadata:
    location_uuid: str
    segment_uuid: str
    location_name: str
    location_type: str = ""
    type_uuid: str | None = None
    value_class: str | None = None
    unit: str = ""
    unit_uuid: str | None = None
    reference_unit_uuid: str | None = None
    conversion_scale: float | None = None
    conversion_offset: float | None = None
    uom_quantity: str = ""
    uom_quantity_uuid: str | None = None
    info_source_uuid: str | None = None
    info_source_short_name: str | None = None
    measurement_location_type_info_source_uuid: str | None = None
    measurement_location_type_info_source_short_name: str | None = None
    unit_info_source_uuid: str | None = None
    unit_info_source_short_name: str | None = None
    uom_quantity_info_source_uuid: str | None = None
    uom_quantity_info_source_short_name: str | None = None


@dataclass(frozen=True)
class SegmentResolution:
    segment: SegmentMetadata
    site: SiteMetadata | None = None


@dataclass(frozen=True)
class LocalSegment:
    segment_uuid: str
    site_uuid: str | None
    site_key: int | None
