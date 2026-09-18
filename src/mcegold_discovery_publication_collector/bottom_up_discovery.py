from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .discovery_models import MeasurementLocationMetadata, SegmentMetadata, SegmentResolution, SiteMetadata
from .discovery_persistence import DiscoveryPersistenceError, SQLiteDiscoveryPersistence
from .discovery_request_client import DiscoveryRequestClient, DiscoveryRequestError
from .discovery_response_parser import DiscoveryResponseParseError, DiscoveryResponseParser


LOGGER = logging.getLogger(__name__)
SEGMENT_MEASUREMENT_LOCATION_MAX_ITEMS = 32767


class DiscoveryRequestSession(Protocol):
    def execute_request(self, session_id: str, request: dict[str, Any]) -> Any:
        ...


class DiscoverySessionClient(DiscoveryRequestSession, Protocol):
    def open_session(self) -> str:
        ...

    def close_session(self, session_id: str) -> None:
        ...


class DiscoveryPersistence(Protocol):
    def get_segment(self, segment_uuid: str):
        ...

    def site_exists(self, site_uuid: str) -> bool:
        ...

    def persist_targeted_discovery(
        self,
        *,
        triggering_measurement_location_uuid: str,
        sites: list[SiteMetadata],
        segments: list[SegmentMetadata],
        measurement_locations: list[MeasurementLocationMetadata],
    ) -> int:
        ...


class BottomUpDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BottomUpDiscoveryResult:
    measurement_location_uuid: str
    segment_uuid: str
    persisted_measurement_location_count: int


class BottomUpDiscoveryService:
    def __init__(
        self,
        request_client: DiscoverySessionClient,
        persistence: DiscoveryPersistence,
        parser: DiscoveryResponseParser | None = None,
    ) -> None:
        self.request_client = request_client
        self.persistence = persistence
        self.parser = parser or DiscoveryResponseParser()

    def discover_measurement_location(
        self,
        measurement_location_uuid: str,
        segment_uuid_if_known: str | None = None,
    ) -> BottomUpDiscoveryResult:
        session_id: str | None = None
        try:
            session_id = self.request_client.open_session()
            return self._discover_in_session(measurement_location_uuid, segment_uuid_if_known, session_id)
        except BottomUpDiscoveryError:
            raise
        except (DiscoveryRequestError, DiscoveryResponseParseError, DiscoveryPersistenceError) as exc:
            raise BottomUpDiscoveryError("Bottom-up discovery failed.") from exc
        except Exception as exc:
            raise BottomUpDiscoveryError("Bottom-up discovery failed.") from exc
        finally:
            if session_id is not None:
                try:
                    self.request_client.close_session(session_id)
                except Exception as exc:
                    LOGGER.warning("Request session close failed during discovery cleanup: %s", exc)

    def _discover_in_session(
        self,
        measurement_location_uuid: str,
        segment_uuid_if_known: str | None,
        session_id: str,
    ) -> BottomUpDiscoveryResult:
        LOGGER.info("Unknown measurement location detected: %s", measurement_location_uuid)
        exact_location: MeasurementLocationMetadata | None = None
        segment_uuid = segment_uuid_if_known

        if segment_uuid:
            LOGGER.info("Parent segment resolved: %s", segment_uuid)
        else:
            exact_locations = self._get_measurement_locations_by_uuid(session_id, measurement_location_uuid)
            exact_location = _first_matching_location(exact_locations, measurement_location_uuid)
            if exact_location is None:
                LOGGER.warning("Remote measurement location not found: %s", measurement_location_uuid)
                raise BottomUpDiscoveryError(f"Remote measurement location not found: {measurement_location_uuid}")
            segment_uuid = exact_location.segment_uuid
            LOGGER.info("Parent segment resolved: %s", segment_uuid)

        local_segment = self.persistence.get_segment(segment_uuid)
        sites: list[SiteMetadata] = []
        segments: list[SegmentMetadata] = []

        if local_segment is not None and local_segment.site_uuid and local_segment.site_key is not None:
            LOGGER.info("Segment already known locally; skipping GetSegments.")
        else:
            LOGGER.info("Parent segment not known locally; resolving metadata.")
            resolution = self._get_segment(session_id, segment_uuid)
            site = resolution.site
            if site is None or not _sufficient_site_metadata(site):
                if resolution.segment.site_uuid and not self.persistence.site_exists(resolution.segment.site_uuid):
                    LOGGER.info("Site metadata incomplete; resolving Site %s.", resolution.segment.site_uuid)
                    site = self._get_site(session_id, resolution.segment.site_uuid)
            if site is None or not _sufficient_site_metadata(site):
                raise BottomUpDiscoveryError(f"Site metadata is insufficient for segment {segment_uuid}.")
            sites.append(site)
            segments.append(resolution.segment)

        segment_locations = self._get_measurement_locations_by_segment(session_id, segment_uuid)
        if exact_location is not None and not _contains_location(segment_locations, exact_location.location_uuid):
            segment_locations.append(exact_location)
        if not _contains_location(segment_locations, measurement_location_uuid):
            raise BottomUpDiscoveryError(
                f"Segment-level discovery did not return triggering measurement location {measurement_location_uuid}."
            )

        count = self.persistence.persist_targeted_discovery(
            triggering_measurement_location_uuid=measurement_location_uuid,
            sites=sites,
            segments=segments,
            measurement_locations=segment_locations,
        )
        LOGGER.info("Discovered %s measurement locations for segment %s.", len(segment_locations), segment_uuid)
        LOGGER.info("Bottom-up discovery completed for measurement location %s.", measurement_location_uuid)
        return BottomUpDiscoveryResult(measurement_location_uuid, segment_uuid, count)

    def _get_measurement_locations_by_uuid(self, session_id: str, measurement_location_uuid: str) -> list[MeasurementLocationMetadata]:
        payload = self.request_client.execute_request(
            session_id,
            {
                "requestType": "GetMeasurementLocations",
                "payloadProfile": "Full",
                "maxItems": 1,
                "filters": {"measurementLocationUuid": measurement_location_uuid},
            },
        )
        return self.parser.parse_measurement_locations(payload)

    def _get_segment(self, session_id: str, segment_uuid: str) -> SegmentResolution:
        payload = self.request_client.execute_request(
            session_id,
            {
                "requestType": "GetSegments",
                "payloadProfile": "Full",
                "maxItems": 1,
                "filters": {"segmentUuid": segment_uuid},
            },
        )
        segments = self.parser.parse_segments(payload)
        for resolution in segments:
            if resolution.segment.segment_uuid.casefold() == segment_uuid.casefold():
                return resolution
        raise BottomUpDiscoveryError(f"Remote segment not found: {segment_uuid}")

    def _get_site(self, session_id: str, site_uuid: str) -> SiteMetadata:
        payload = self.request_client.execute_request(
            session_id,
            {
                "requestType": "GetSites",
                "payloadProfile": "Full",
                "maxItems": 1,
                "filters": {"siteUuid": site_uuid},
            },
        )
        for site in self.parser.parse_sites(payload):
            if site.site_uuid.casefold() == site_uuid.casefold():
                return site
        raise BottomUpDiscoveryError(f"Remote site not found: {site_uuid}")

    def _get_measurement_locations_by_segment(self, session_id: str, segment_uuid: str) -> list[MeasurementLocationMetadata]:
        payload = self.request_client.execute_request(
            session_id,
            {
                "requestType": "GetMeasurementLocations",
                "payloadProfile": "Full",
                "maxItems": SEGMENT_MEASUREMENT_LOCATION_MAX_ITEMS,
                "filters": {"segmentUuid": segment_uuid},
            },
        )
        locations = self.parser.parse_measurement_locations(payload, fallback_segment_uuid=segment_uuid)
        if not locations:
            raise BottomUpDiscoveryError(f"No measurement locations returned for segment {segment_uuid}.")
        return locations


def build_bottom_up_discovery_service(config) -> BottomUpDiscoveryService:
    return BottomUpDiscoveryService(
        request_client=DiscoveryRequestClient(config),
        persistence=SQLiteDiscoveryPersistence(config.discovery_database_path),
    )


def _first_matching_location(
    locations: list[MeasurementLocationMetadata],
    measurement_location_uuid: str,
) -> MeasurementLocationMetadata | None:
    for location in locations:
        if location.location_uuid.casefold() == measurement_location_uuid.casefold():
            return location
    return None


def _contains_location(locations: list[MeasurementLocationMetadata], measurement_location_uuid: str) -> bool:
    return any(location.location_uuid.casefold() == measurement_location_uuid.casefold() for location in locations)


def _sufficient_site_metadata(site: SiteMetadata | None) -> bool:
    return bool(site and site.site_uuid and site.site_name and site.site_name != site.site_uuid)
