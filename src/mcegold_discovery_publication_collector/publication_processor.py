from __future__ import annotations

import logging
from typing import Protocol

from .bottom_up_discovery import build_bottom_up_discovery_service
from .configuration import CollectorConfig
from .discovery_trigger import BottomUpDiscoveryTrigger, DiscoveryTrigger
from .measurement_location_lookup import (
    MeasurementLocationLookupError,
    MeasurementLocationLookupResult,
    SQLiteMeasurementLocationLookup,
)
from .measurement_persistence import MeasurementPersistenceError, SQLiteMeasurementPersistence
from .measurement_publication import MeasurementPublicationParser, NormalizedMeasurementRow, PublicationParseError
from .publication_client import Publication, safe_session_id


LOGGER = logging.getLogger(__name__)


class MeasurementLocationLookup(Protocol):
    def lookup(self, measurement_location_uuid: str) -> MeasurementLocationLookupResult:
        ...


class MeasurementPersistence(Protocol):
    def persist_publication(self, rows: list[NormalizedMeasurementRow]) -> int:
        ...


class PublicationHandlingError(RuntimeError):
    pass


class PublicationProcessor:
    def __init__(
        self,
        parser: MeasurementPublicationParser,
        lookup: MeasurementLocationLookup,
        discovery_trigger: DiscoveryTrigger,
        persistence: MeasurementPersistence,
    ) -> None:
        self.parser = parser
        self.lookup = lookup
        self.discovery_trigger = discovery_trigger
        self.persistence = persistence

    def __call__(self, publication: Publication) -> None:
        LOGGER.info(
            "Publication received messageId=%s sessionId=%s",
            publication.message_id,
            safe_session_id(publication.session_id),
        )

        try:
            rows = self.parser.parse(publication.payload)
        except PublicationParseError as exc:
            LOGGER.error(
                "Unsupported/invalid measurement payload; publication retained messageId=%s error=%s",
                publication.message_id,
                exc,
            )
            raise PublicationHandlingError("Measurement publication parsing failed.") from exc

        identities = []
        discovered_during_processing = False
        for location_uuid in sorted({row.measurement_location_uuid for row in rows}):
            try:
                result = self.lookup.lookup(location_uuid)
            except MeasurementLocationLookupError as exc:
                LOGGER.error(
                    "Could not determine measurement-location status measurementLocation=%s messageId=%s",
                    location_uuid,
                    publication.message_id,
                )
                raise PublicationHandlingError("Measurement-location lookup failed.") from exc
            if not result.is_known:
                identity = _identity_from_rows(location_uuid, rows)
                segment_uuid = _segment_uuid_from_rows(location_uuid, rows)
                if not self.discovery_trigger.on_unknown_measurement_location(identity, publication, segment_uuid):
                    LOGGER.error(
                        "Unknown measurement location: %s; publication retained messageId=%s",
                        identity.uuid,
                        publication.message_id,
                    )
                    raise PublicationHandlingError("Measurement location is unknown.")
                try:
                    result = self.lookup.lookup(location_uuid)
                except MeasurementLocationLookupError as exc:
                    LOGGER.error(
                        "Could not re-check discovered measurement location measurementLocation=%s messageId=%s",
                        location_uuid,
                        publication.message_id,
                    )
                    raise PublicationHandlingError("Measurement-location lookup failed after discovery.") from exc
                if not result.is_known:
                    LOGGER.error(
                        "Measurement location is still unknown after discovery: %s; publication retained messageId=%s",
                        identity.uuid,
                        publication.message_id,
                    )
                    raise PublicationHandlingError("Measurement location is unknown after discovery.")
                discovered_during_processing = True
            identities.append(_identity_from_rows(location_uuid, rows))

        for identity in identities:
            LOGGER.info("Known measurement location measurementLocation=%s messageId=%s", identity.log_label, publication.message_id)

        try:
            persisted_count = self.persistence.persist_publication(rows)
        except MeasurementPersistenceError as exc:
            LOGGER.error("Measurement persistence failed; publication retained messageId=%s error=%s", publication.message_id, exc)
            raise PublicationHandlingError("Measurement persistence failed.") from exc

        LOGGER.info("Persisted %s measurement row(s).", persisted_count)
        if discovered_during_processing:
            LOGGER.info("Original publication persisted after discovery.")
        LOGGER.info("Measurement publication persisted successfully messageId=%s", publication.message_id)


def build_publication_handler(config: CollectorConfig) -> PublicationProcessor:
    return PublicationProcessor(
        parser=MeasurementPublicationParser(),
        lookup=SQLiteMeasurementLocationLookup(config.discovery_database_path),
        discovery_trigger=BottomUpDiscoveryTrigger(build_bottom_up_discovery_service(config)),
        persistence=SQLiteMeasurementPersistence(config.discovery_database_path),
    )


def _identity_from_rows(location_uuid: str, rows: list[NormalizedMeasurementRow]):
    from .measurement_publication import MeasurementLocationIdentity

    return MeasurementLocationIdentity(uuid=location_uuid, name=None)


def _segment_uuid_from_rows(location_uuid: str, rows: list[NormalizedMeasurementRow]) -> str | None:
    for row in rows:
        if row.measurement_location_uuid == location_uuid and row.segment_uuid:
            return row.segment_uuid
    return None
