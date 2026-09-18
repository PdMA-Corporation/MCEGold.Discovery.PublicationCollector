from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .discovery_models import LocalSegment, MeasurementLocationMetadata, SegmentMetadata, SiteMetadata


class DiscoveryPersistenceError(RuntimeError):
    pass


class SQLiteDiscoveryPersistence:
    def __init__(self, database_path: str, busy_timeout_ms: int = 5000) -> None:
        self.database_path = database_path
        self.busy_timeout_ms = busy_timeout_ms

    def get_segment(self, segment_uuid: str) -> LocalSegment | None:
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                """
                SELECT SegmentUuid, SiteUuid, SiteKey
                FROM DimSegment
                WHERE lower(SegmentUuid) = lower(?)
                LIMIT 1
                """,
                (segment_uuid,),
            ).fetchone()
        if row is None:
            return None
        return LocalSegment(segment_uuid=str(row["SegmentUuid"]), site_uuid=row["SiteUuid"], site_key=row["SiteKey"])

    def site_exists(self, site_uuid: str) -> bool:
        with self._connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT SiteKey FROM DimSite WHERE lower(SiteUuid) = lower(?) LIMIT 1",
                (site_uuid,),
            ).fetchone()
        return row is not None

    def persist_targeted_discovery(
        self,
        *,
        triggering_measurement_location_uuid: str,
        sites: Iterable[SiteMetadata],
        segments: Iterable[SegmentMetadata],
        measurement_locations: Iterable[MeasurementLocationMetadata],
    ) -> int:
        site_list = list(sites)
        segment_list = list(segments)
        location_list = list(measurement_locations)
        path = Path(self.database_path)
        try:
            with sqlite3.connect(path) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
                try:
                    connection.execute("BEGIN")
                    self._save_info_sources(connection, _info_sources(site_list, segment_list, location_list))
                    self._save_site_types(connection, site_list)
                    self._save_segment_types(connection, segment_list)
                    self._save_measurement_location_types(connection, location_list)
                    self._save_uom_quantities(connection, location_list)
                    self._save_units(connection, location_list)
                    self._save_uom_quantities(connection, location_list)
                    self._save_sites(connection, site_list)
                    self._save_segments(connection, segment_list)
                    self._save_measurement_locations(connection, location_list)
                    if not self._measurement_location_exists(connection, triggering_measurement_location_uuid):
                        raise DiscoveryPersistenceError(
                            "Triggering measurement location was not persisted by targeted discovery: "
                            f"{triggering_measurement_location_uuid}"
                        )
                    connection.commit()
                    return len(location_list)
                except Exception:
                    connection.rollback()
                    raise
        except sqlite3.Error as exc:
            raise DiscoveryPersistenceError(f"Discovery persistence failed for discovery database: {path}") from exc

    def _connect(self, readonly: bool = False) -> sqlite3.Connection:
        if not self.database_path:
            raise DiscoveryPersistenceError("discoveryDatabasePath is required for bottom-up discovery.")
        path = Path(self.database_path)
        if readonly:
            uri_path = path.resolve(strict=False).as_posix()
            connection = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        else:
            connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        return connection

    def _save_info_sources(self, connection: sqlite3.Connection, values: Iterable[tuple[str | None, str | None]]) -> None:
        now = _utc_now()
        rows = []
        seen: set[str] = set()
        for uuid, short_name in values:
            if not uuid or not short_name or uuid in seen:
                continue
            seen.add(uuid)
            rows.append((uuid, short_name, now, now))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimInfoSource (InfoSourceUuid, ShortName, FirstSeenUtc, LastSeenUtc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(InfoSourceUuid) DO UPDATE SET
                    ShortName = COALESCE(excluded.ShortName, DimInfoSource.ShortName),
                    LastSeenUtc = excluded.LastSeenUtc
                """,
                rows,
            )

    def _save_site_types(self, connection: sqlite3.Connection, sites: list[SiteMetadata]) -> None:
        rows = []
        for site in sites:
            if site.site_type_uuid:
                rows.append((
                    site.site_type_uuid,
                    site.site_type or site.site_type_uuid,
                    site.site_type_info_source_uuid,
                    self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", site.site_type_info_source_uuid),
                ))
        self._upsert_type(connection, "DimSiteType", "SiteTypeUuid", "ShortName", "InfoSourceUuid", "InfoSourceKey", rows)

    def _save_segment_types(self, connection: sqlite3.Connection, segments: list[SegmentMetadata]) -> None:
        rows = []
        for segment in segments:
            if segment.segment_type_uuid:
                rows.append((
                    segment.segment_type_uuid,
                    segment.segment_type or segment.segment_type_uuid,
                    segment.segment_type_info_source_uuid,
                    self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", segment.segment_type_info_source_uuid),
                ))
        self._upsert_type(connection, "DimSegmentType", "SegmentTypeUuid", "ShortName", "InfoSourceUuid", "InfoSourceKey", rows)

    def _save_measurement_location_types(self, connection: sqlite3.Connection, locations: list[MeasurementLocationMetadata]) -> None:
        rows = []
        for location in locations:
            if location.type_uuid:
                rows.append((
                    location.type_uuid,
                    location.location_type or location.type_uuid,
                    location.uom_quantity_uuid,
                    location.measurement_location_type_info_source_uuid,
                    self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", location.measurement_location_type_info_source_uuid),
                ))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimMeasurementLocationType (
                    MeasurementLocationTypeUuid, ShortName, UomQuantityUuid, InfoSourceUuid, InfoSourceKey
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(MeasurementLocationTypeUuid) DO UPDATE SET
                    ShortName = excluded.ShortName,
                    UomQuantityUuid = COALESCE(excluded.UomQuantityUuid, DimMeasurementLocationType.UomQuantityUuid),
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimMeasurementLocationType.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimMeasurementLocationType.InfoSourceKey)
                """,
                rows,
            )

    def _save_uom_quantities(self, connection: sqlite3.Connection, locations: list[MeasurementLocationMetadata]) -> None:
        rows = []
        seen: set[str] = set()
        for location in locations:
            if location.uom_quantity_uuid and location.uom_quantity_uuid not in seen:
                seen.add(location.uom_quantity_uuid)
                rows.append((
                    location.uom_quantity_uuid,
                    location.uom_quantity or location.uom_quantity_uuid,
                    location.reference_unit_uuid,
                    self._key(connection, "DimUnit", "UnitKey", "UnitUuid", location.reference_unit_uuid),
                    location.uom_quantity_info_source_uuid,
                    self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", location.uom_quantity_info_source_uuid),
                ))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimUomQuantity (
                    UomQuantityUuid, ShortName, ReferenceUnitUuid, ReferenceUnitKey, InfoSourceUuid, InfoSourceKey
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(UomQuantityUuid) DO UPDATE SET
                    ShortName = excluded.ShortName,
                    ReferenceUnitUuid = COALESCE(excluded.ReferenceUnitUuid, DimUomQuantity.ReferenceUnitUuid),
                    ReferenceUnitKey = COALESCE(excluded.ReferenceUnitKey, DimUomQuantity.ReferenceUnitKey),
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimUomQuantity.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimUomQuantity.InfoSourceKey)
                """,
                rows,
            )

    def _save_units(self, connection: sqlite3.Connection, locations: list[MeasurementLocationMetadata]) -> None:
        rows = []
        seen: set[str] = set()
        for location in locations:
            if location.unit_uuid and location.unit_uuid not in seen:
                seen.add(location.unit_uuid)
                rows.append((
                    location.unit_uuid,
                    location.unit or location.unit_uuid,
                    location.uom_quantity_uuid,
                    self._key(connection, "DimUomQuantity", "UomQuantityKey", "UomQuantityUuid", location.uom_quantity_uuid),
                    location.conversion_scale,
                    location.conversion_offset,
                    location.unit_info_source_uuid,
                    self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", location.unit_info_source_uuid),
                ))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimUnit (
                    UnitUuid, ShortName, UomQuantityUuid, UomQuantityKey, ConversionScale, ConversionOffset,
                    InfoSourceUuid, InfoSourceKey
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(UnitUuid) DO UPDATE SET
                    ShortName = excluded.ShortName,
                    UomQuantityUuid = COALESCE(excluded.UomQuantityUuid, DimUnit.UomQuantityUuid),
                    UomQuantityKey = COALESCE(excluded.UomQuantityKey, DimUnit.UomQuantityKey),
                    ConversionScale = COALESCE(excluded.ConversionScale, DimUnit.ConversionScale),
                    ConversionOffset = COALESCE(excluded.ConversionOffset, DimUnit.ConversionOffset),
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimUnit.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimUnit.InfoSourceKey)
                """,
                rows,
            )

    def _save_sites(self, connection: sqlite3.Connection, sites: list[SiteMetadata]) -> None:
        now = _utc_now()
        rows = [
            (
                site.site_uuid,
                site.site_name or site.site_uuid,
                site.site_type_uuid,
                self._key(connection, "DimSiteType", "SiteTypeKey", "SiteTypeUuid", site.site_type_uuid),
                site.info_source_uuid,
                self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", site.info_source_uuid),
                now,
                now,
            )
            for site in sites
        ]
        if rows:
            connection.executemany(
                """
                INSERT INTO DimSite (
                    SiteUuid, SiteName, SiteTypeUuid, SiteTypeKey, InfoSourceUuid, InfoSourceKey, FirstSeenUtc, LastSeenUtc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(SiteUuid) DO UPDATE SET
                    SiteName = excluded.SiteName,
                    SiteTypeUuid = excluded.SiteTypeUuid,
                    SiteTypeKey = excluded.SiteTypeKey,
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimSite.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimSite.InfoSourceKey),
                    LastSeenUtc = excluded.LastSeenUtc
                """,
                rows,
            )

    def _save_segments(self, connection: sqlite3.Connection, segments: list[SegmentMetadata]) -> None:
        now = _utc_now()
        rows = []
        for segment in segments:
            site_key = self._key(connection, "DimSite", "SiteKey", "SiteUuid", segment.site_uuid)
            if site_key is None:
                raise DiscoveryPersistenceError(f"Segment parent site is missing: {segment.site_uuid}")
            rows.append((
                segment.segment_uuid,
                segment.segment_name or segment.segment_uuid,
                segment.segment_type_uuid,
                self._key(connection, "DimSegmentType", "SegmentTypeKey", "SegmentTypeUuid", segment.segment_type_uuid),
                segment.site_uuid,
                site_key,
                segment.info_source_uuid,
                self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", segment.info_source_uuid),
                now,
                now,
            ))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimSegment (
                    SegmentUuid, SegmentName, SegmentTypeUuid, SegmentTypeKey, SiteUuid, SiteKey,
                    InfoSourceUuid, InfoSourceKey, FirstSeenUtc, LastSeenUtc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(SegmentUuid) DO UPDATE SET
                    SegmentName = excluded.SegmentName,
                    SegmentTypeUuid = excluded.SegmentTypeUuid,
                    SegmentTypeKey = excluded.SegmentTypeKey,
                    SiteUuid = excluded.SiteUuid,
                    SiteKey = excluded.SiteKey,
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimSegment.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimSegment.InfoSourceKey),
                    LastSeenUtc = excluded.LastSeenUtc
                """,
                rows,
            )

    def _save_measurement_locations(self, connection: sqlite3.Connection, locations: list[MeasurementLocationMetadata]) -> None:
        now = _utc_now()
        rows = []
        for location in locations:
            segment_key = self._key(connection, "DimSegment", "SegmentKey", "SegmentUuid", location.segment_uuid)
            if segment_key is None:
                raise DiscoveryPersistenceError(f"Measurement location parent segment is missing: {location.segment_uuid}")
            value_class = location.value_class or None
            unit_uuid = location.unit_uuid
            unit_key = self._key(connection, "DimUnit", "UnitKey", "UnitUuid", unit_uuid)
            uom_quantity_uuid = location.uom_quantity_uuid
            uom_quantity_key = self._key(connection, "DimUomQuantity", "UomQuantityKey", "UomQuantityUuid", uom_quantity_uuid)
            if value_class in {"Percentage", "Number"}:
                unit_uuid = None
                unit_key = None
                uom_quantity_uuid = None
                uom_quantity_key = None
            rows.append((
                location.location_uuid,
                location.location_name or location.location_uuid,
                location.type_uuid,
                self._key(connection, "DimMeasurementLocationType", "MeasurementLocationTypeKey", "MeasurementLocationTypeUuid", location.type_uuid),
                location.segment_uuid,
                segment_key,
                value_class,
                unit_uuid,
                unit_key,
                uom_quantity_uuid,
                uom_quantity_key,
                location.info_source_uuid,
                self._key(connection, "DimInfoSource", "InfoSourceKey", "InfoSourceUuid", location.info_source_uuid),
                now,
                now,
            ))
        if rows:
            connection.executemany(
                """
                INSERT INTO DimMeasurementLocation (
                    MeasurementLocationUuid, MeasurementLocationName, MeasurementLocationTypeUuid,
                    MeasurementLocationTypeKey, SegmentUuid, SegmentKey, ValueClass, DefaultUnitUuid, DefaultUnitKey,
                    UomQuantityUuid, UomQuantityKey, InfoSourceUuid, InfoSourceKey, FirstSeenUtc, LastSeenUtc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(MeasurementLocationUuid) DO UPDATE SET
                    MeasurementLocationName = excluded.MeasurementLocationName,
                    MeasurementLocationTypeUuid = excluded.MeasurementLocationTypeUuid,
                    MeasurementLocationTypeKey = excluded.MeasurementLocationTypeKey,
                    SegmentUuid = excluded.SegmentUuid,
                    SegmentKey = excluded.SegmentKey,
                    ValueClass = excluded.ValueClass,
                    DefaultUnitUuid = excluded.DefaultUnitUuid,
                    DefaultUnitKey = excluded.DefaultUnitKey,
                    UomQuantityUuid = excluded.UomQuantityUuid,
                    UomQuantityKey = excluded.UomQuantityKey,
                    InfoSourceUuid = COALESCE(excluded.InfoSourceUuid, DimMeasurementLocation.InfoSourceUuid),
                    InfoSourceKey = COALESCE(excluded.InfoSourceKey, DimMeasurementLocation.InfoSourceKey),
                    LastSeenUtc = excluded.LastSeenUtc
                """,
                rows,
            )

    def _upsert_type(
        self,
        connection: sqlite3.Connection,
        table: str,
        uuid_column: str,
        short_name_column: str,
        info_source_uuid_column: str,
        info_source_key_column: str,
        rows: list[tuple[str, str, str | None, int | None]],
    ) -> None:
        if not rows:
            return
        connection.executemany(
            f"""
            INSERT INTO {table} ({uuid_column}, {short_name_column}, {info_source_uuid_column}, {info_source_key_column})
            VALUES (?, ?, ?, ?)
            ON CONFLICT({uuid_column}) DO UPDATE SET
                {short_name_column} = excluded.{short_name_column},
                {info_source_uuid_column} = COALESCE(excluded.{info_source_uuid_column}, {table}.{info_source_uuid_column}),
                {info_source_key_column} = COALESCE(excluded.{info_source_key_column}, {table}.{info_source_key_column})
            """,
            rows,
        )

    def _key(
        self,
        connection: sqlite3.Connection,
        table: str,
        key_column: str,
        uuid_column: str,
        uuid: str | None,
    ) -> int | None:
        if not uuid:
            return None
        row = connection.execute(
            f"SELECT {key_column} FROM {table} WHERE lower({uuid_column}) = lower(?) LIMIT 1",
            (uuid,),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def _measurement_location_exists(self, connection: sqlite3.Connection, location_uuid: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM DimMeasurementLocation WHERE lower(MeasurementLocationUuid) = lower(?) LIMIT 1",
            (location_uuid,),
        ).fetchone() is not None


def _info_sources(
    sites: list[SiteMetadata],
    segments: list[SegmentMetadata],
    locations: list[MeasurementLocationMetadata],
) -> Iterable[tuple[str | None, str | None]]:
    for site in sites:
        yield site.info_source_uuid, site.info_source_short_name
        yield site.site_type_info_source_uuid, site.site_type_info_source_short_name
    for segment in segments:
        yield segment.info_source_uuid, segment.info_source_short_name
        yield segment.segment_type_info_source_uuid, segment.segment_type_info_source_short_name
    for location in locations:
        yield location.info_source_uuid, location.info_source_short_name
        yield location.measurement_location_type_info_source_uuid, location.measurement_location_type_info_source_short_name
        yield location.unit_info_source_uuid, location.unit_info_source_short_name
        yield location.uom_quantity_info_source_uuid, location.uom_quantity_info_source_short_name


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
